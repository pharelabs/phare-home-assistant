"""A thin client for the Phare Protect API.

Every response is `{"data": ..., "meta": {...}}`. The statuses that matter to a Home Assistant
integration become typed errors:

* 401, and the API Gateway 403 `{"message": "Forbidden"}` that a refused personal access token
  produces (unknown, revoked, expired, or a write without the `control` scope) -> PhareAuthError.
  A handler's own 403 (`{"error": "Access denied to device"}`) is PhareForbiddenError: the
  credential is fine, the resource is not the caller's. Nothing handles it specially today; the
  distinct type keeps the two apart in logs and lets a caller tell them apart later.
* 403 with `code: "membership_required"` -> PhareMembershipRequiredError, an auth error: the
  token is fine but the account's plan does not include API access. The API does not send it
  today; if Phare ever limits API access by plan, this is how it says so, and Home Assistant
  must ask the user to act rather than let the entities go quietly stale.
* 429 -> PhareRateLimitedError, carrying Retry-After when the API sends one.
* anything else non-2xx -> PhareApiError with the status and the API's message.
"""

from __future__ import annotations

import asyncio
from typing import Any

import aiohttp

from .const import DEFAULT_BASE_URL

TIMEOUT = aiohttp.ClientTimeout(total=30)


class PhareApiError(Exception):
    """The API answered, but not with what was asked for."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"{status}: {message}")
        self.status = status
        self.message = message


class PhareAuthError(PhareApiError):
    """The credential was refused: the user must re-authenticate."""


class PhareMembershipRequiredError(PhareAuthError):
    """The account's plan does not include API access."""


class PhareForbiddenError(PhareApiError):
    """The credential is valid but may not touch this resource."""


class PhareRateLimitedError(PhareApiError):
    """Too many requests; back off."""

    def __init__(self, status: int, message: str, retry_after: float | None) -> None:
        super().__init__(status, message)
        self.retry_after = retry_after


class PhareConnectionError(Exception):
    """The API could not be reached."""


def _message(body: Any, fallback: str) -> str:
    if isinstance(body, dict):
        for key in ("error", "message"):
            if isinstance(body.get(key), str):
                return body[key]
    return fallback


def _retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None  # an HTTP-date; the caller falls back to its own interval


class PhareApiClient:
    """Calls the API on behalf of one personal access token."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        token: str,
        base_url: str = DEFAULT_BASE_URL,
    ) -> None:
        self._session = session
        self._token = token
        self._base_url = base_url.rstrip("/")

    async def _envelope(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
    ) -> dict[str, Any]:
        """The whole response body; the cursor the paged endpoints need lives in `meta`."""
        url = f"{self._base_url}{path}"
        headers = {"Authorization": f"Bearer {self._token}", "Accept": "application/json"}
        try:
            async with self._session.request(
                method, url, headers=headers, params=params, json=json, timeout=TIMEOUT
            ) as resp:
                try:
                    body = await resp.json(content_type=None)
                except (aiohttp.ContentTypeError, ValueError):
                    body = None
                if resp.status == 401:
                    raise PhareAuthError(resp.status, _message(body, "Unauthorized"))
                if resp.status == 403:
                    message = _message(body, "Forbidden")
                    if isinstance(body, dict) and body.get("code") == "membership_required":
                        raise PhareMembershipRequiredError(resp.status, message)
                    # API Gateway's refusal carries `message`; a handler's carries `error`.
                    if isinstance(body, dict) and "error" in body:
                        raise PhareForbiddenError(resp.status, message)
                    raise PhareAuthError(resp.status, message)
                if resp.status == 429:
                    raise PhareRateLimitedError(
                        resp.status,
                        _message(body, "Too Many Requests"),
                        _retry_after(resp.headers.get("Retry-After")),
                    )
                if resp.status >= 400:
                    raise PhareApiError(resp.status, _message(body, getattr(resp, "reason", None) or "Error"))
                # A 2xx with no body (a 204, an empty 200) is a success with nothing to say.
                return body if isinstance(body, dict) else {}
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise PhareConnectionError(str(err)) from err

    async def _data(self, method: str, path: str, **kwargs: Any) -> Any:
        return (await self._envelope(method, path, **kwargs)).get("data")

    async def _paged(self, path: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        params = dict(params or {})
        while True:
            body = await self._envelope("GET", path, params=params)
            items.extend(body.get("data") or [])
            cursor = (body.get("meta") or {}).get("cursor")
            if not cursor:
                return items
            params = {"cursor": cursor}

    # ── reads ──────────────────────────────────────────────────────────────

    async def user_settings(self) -> dict[str, Any]:
        """The account behind the token; `user_id` is its stable identity."""
        return await self._data("GET", "/v1/user/settings") or {}

    async def spaces(self) -> list[dict[str, Any]]:
        return await self._paged("/v1/spaces")

    async def devices(self) -> list[dict[str, Any]]:
        return await self._paged("/v1/devices")

    async def spaces_data(self) -> dict[str, Any]:
        """Latest reading per device, keyed space -> room -> readings. One call for the account."""
        return await self._data("GET", "/v1/spaces/data") or {}

    async def devices_status(self) -> list[dict[str, Any]]:
        return await self._data("GET", "/v1/devices/status") or []

    async def alerts(self, window: str) -> list[dict[str, Any]]:
        """Latest record per alert in the window, newest first; the API pages with a cursor."""
        return await self._paged("/v1/alerts", {"window": window})

    # ── writes (need the `control` scope) ──────────────────────────────────

    async def update_settings(self, patch: dict[str, Any]) -> dict[str, Any]:
        """Partial update of the account's settings. Registering a new webhook URL returns
        `webhook_secret` in this response only."""
        return await self._data("POST", "/v1/user/settings", json=patch) or {}

    async def rotate_webhook_secret(self) -> str | None:
        """A fresh signing secret; the old one stops verifying at once."""
        return ((await self._data("POST", "/v1/user/webhook/rotate")) or {}).get("webhook_secret")

    async def update_space(self, space_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        """Change a home. Only an owner may send anything but `armed` (a member gets 403)."""
        return await self._data("POST", f"/v1/spaces/{space_id}", json=patch) or {}

    async def device_settings(self, device_id: str) -> dict[str, Any]:
        """The device's settings shadow: `reported` is what the device has applied."""
        return await self._data("GET", f"/v1/devices/{device_id}/settings") or {}

    async def update_device_settings(self, device_id: str, settings: dict[str, int]) -> dict[str, Any]:
        """Write firmware settings (raw keys, integer values) to one device."""
        return await self._data("POST", f"/v1/devices/{device_id}/settings", json=settings) or {}

    async def arm(self, device_id: str, armed: bool) -> dict[str, Any]:
        return await self._data("POST", f"/v1/devices/{device_id}/arm", json={"armed": armed}) or {}

    async def command(self, device_id: str, action: str) -> dict[str, Any]:
        """test, silence or identify. Accepted on dispatch: 200 means queued, not done."""
        # identify reads an optional body; the others ignore it. Always send JSON: arm's handler
        # 500s on a non-JSON body and the others may follow it.
        return await self._data("POST", f"/v1/devices/{device_id}/{action}", json={}) or {}
