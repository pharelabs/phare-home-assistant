"""Push: the API posts each alert to a webhook this integration registers.

Polling every minute is fine for temperature and humidity and bad for smoke and CO. On setup
the integration asks Home Assistant for a webhook URL (a Nabu Casa cloudhook when the account
has one, else the configured external URL), registers it with the API through the account's
settings, and keeps the one-time signing secret in the config entry. Every delivery is
verified before it touches state: the timestamp must be within WEBHOOK_MAX_SKEW, the body's
HMAC must match, and a delivery id seen before is ignored. The minute poll stays as the
reconciliation path.

Registration is best effort. A token without the `control` scope cannot write settings, and
a Home Assistant without an https URL has nothing to register; both stay on polling and say
so once in the log.
"""

from __future__ import annotations

from collections import deque
import hashlib
import hmac
import json
import logging
import time
from typing import TYPE_CHECKING

from aiohttp import web

from homeassistant.components import webhook
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.network import NoURLAvailableError

from .api import PhareApiError, PhareAuthError, PhareConnectionError, PhareMembershipRequiredError
from .const import (
    ALERT_TYPE_CODES,
    CONF_CLOUDHOOK,
    CONF_WEBHOOK_ID,
    CONF_WEBHOOK_SECRET,
    DOMAIN,
    EVENT_ALERT,
    WEBHOOK_DEDUPE_SIZE,
    WEBHOOK_MAX_SKEW,
)

if TYPE_CHECKING:
    from .coordinator import PhareConfigEntry

_LOGGER = logging.getLogger(__name__)

HEADER_DELIVERY = "X-Phare-Delivery"
HEADER_TIMESTAMP = "X-Phare-Timestamp"
HEADER_SIGNATURE = "X-Phare-Signature"

_WEBHOOK_KEYS = {CONF_WEBHOOK_ID, CONF_WEBHOOK_SECRET, CONF_CLOUDHOOK, "cloudhook_url"}


def signature(secret: str, timestamp: str, body: bytes) -> str:
    digest = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def verify(secret: str, timestamp: str | None, body: bytes, given: str | None, *, now: float) -> str | None:
    """None when the delivery is genuine and fresh, else why it is not."""
    if not timestamp or not given:
        return "missing signature headers"
    try:
        ts = int(timestamp)
    except ValueError:
        return "timestamp is not a number"
    if abs(now - ts) > WEBHOOK_MAX_SKEW.total_seconds():
        return "timestamp outside the accepted window"
    if not hmac.compare_digest(signature(secret, timestamp, body), given):
        return "signature mismatch"
    return None


def _cloud():
    """Nabu Casa's helpers, loaded on demand: importing homeassistant.components.cloud pulls in
    camera support (turbojpeg) that a plain test environment does not carry."""
    try:
        from homeassistant.components import cloud
    except ImportError:
        return None
    return cloud


async def _webhook_url(hass: HomeAssistant, entry: PhareConfigEntry, webhook_id: str) -> tuple[str, bool] | None:
    """(url, is_cloudhook), or None when Home Assistant has no https URL to offer."""
    cloud = _cloud()
    if cloud is not None and cloud.async_active_subscription(hass):
        if entry.data.get(CONF_CLOUDHOOK) and entry.data.get(CONF_WEBHOOK_ID) == webhook_id:
            url = entry.data.get("cloudhook_url")
            if url:
                return url, True
        try:
            if cloud.async_is_connected(hass):
                return await cloud.async_create_cloudhook(hass, webhook_id), True
        except cloud.CloudNotAvailable:
            pass
    try:
        url = webhook.async_generate_url(hass, webhook_id, allow_internal=False, allow_ip=False)
    except NoURLAvailableError:
        return None
    if not url.startswith("https://"):
        return None
    return url, False


async def async_register(hass: HomeAssistant, entry: PhareConfigEntry) -> bool:
    """Register the receiver locally and with the API. True when push is on."""
    coordinator = entry.runtime_data
    webhook_id = entry.data.get(CONF_WEBHOOK_ID) or webhook.async_generate_id()
    located = await _webhook_url(hass, entry, webhook_id)
    if located is None:
        _LOGGER.info(
            "Phare alerts arrive by polling: Home Assistant has no https external URL (or Nabu Casa) "
            "to receive pushed alerts on"
        )
        if entry.data.get(CONF_WEBHOOK_ID):
            # Registered on an earlier setup: tell the API, or it keeps posting into the void.
            await async_deregister_remote(hass, entry, coordinator.client)
            hass.config_entries.async_update_entry(
                entry, data={k: v for k, v in entry.data.items() if k not in _WEBHOOK_KEYS}
            )
        return False
    url, is_cloudhook = located

    try:
        result = await coordinator.client.update_settings({"notify_webhook": True, "webhook_url": url})
    except PhareMembershipRequiredError as err:
        _LOGGER.info("Phare alerts arrive by polling: the API refused the webhook registration (%s)", err.message)
        return False
    except PhareAuthError as err:
        _LOGGER.info(
            "Phare alerts arrive by polling: the API refused the webhook registration (%s). "
            "A token needs the control scope to register one",
            err.message,
        )
        return False
    except PhareApiError as err:
        _LOGGER.info("Phare alerts arrive by polling: the API refused the webhook URL (%s)", err.message)
        return False
    except PhareConnectionError as err:
        if _register_stored(hass, entry):
            # The API still holds the URL and secret from the last setup; keep receiving on them.
            _LOGGER.warning("Phare webhook registration could not be refreshed, keeping the last one: %s", err)
            return True
        _LOGGER.warning("Phare webhook registration failed, staying on polling: %s", err)
        return False

    data = {**entry.data, CONF_WEBHOOK_ID: webhook_id, CONF_CLOUDHOOK: is_cloudhook}
    if is_cloudhook:
        data["cloudhook_url"] = url
    secret = result.get("webhook_secret") if isinstance(result, dict) else None
    if not secret:
        # The API already had this URL and so returned no secret. Rotate rather than trust the
        # one stored here: an entry restored from a backup, or a secret rotated elsewhere, would
        # otherwise fail every signature until the API switched the channel off, and the
        # recovery that email suggests is exactly this reload.
        try:
            secret = await coordinator.client.rotate_webhook_secret()
        except PhareConnectionError as err:
            # The rotation may never have reached the API; the stored secret is then still live.
            if _register_stored(hass, entry):
                _LOGGER.warning("Phare webhook secret could not be rotated, keeping the last one: %s", err)
                return True
            _LOGGER.warning("Phare webhook registered but no secret could be obtained: %s", err)
            return False
        except PhareApiError as err:
            _LOGGER.warning("Phare webhook registered but no secret could be obtained: %s", err)
            return False
        if not secret:
            return False
    data[CONF_WEBHOOK_SECRET] = secret
    hass.config_entries.async_update_entry(entry, data=data)

    _register_handler(hass, entry, webhook_id)
    _LOGGER.debug("Phare alert webhook registered at %s", url)
    return True


def _register_handler(hass: HomeAssistant, entry: PhareConfigEntry, webhook_id: str) -> None:
    webhook.async_register(hass, DOMAIN, entry.title, webhook_id, _make_handler(entry), allowed_methods=["POST"])


def _register_stored(hass: HomeAssistant, entry: PhareConfigEntry) -> bool:
    """Receive on the webhook and secret kept from an earlier setup, when there are both."""
    webhook_id = entry.data.get(CONF_WEBHOOK_ID)
    if not webhook_id or not entry.data.get(CONF_WEBHOOK_SECRET):
        return False
    _register_handler(hass, entry, webhook_id)
    return True


@callback
def async_unregister(hass: HomeAssistant, entry: PhareConfigEntry) -> None:
    webhook_id = entry.data.get(CONF_WEBHOOK_ID)
    if webhook_id:
        webhook.async_unregister(hass, webhook_id)


async def async_deregister_remote(hass: HomeAssistant, entry: PhareConfigEntry, client) -> None:
    """On removal: tell the API to stop posting and give the cloudhook back. Best effort."""
    webhook_id = entry.data.get(CONF_WEBHOOK_ID)
    if not webhook_id:
        return
    try:
        await client.update_settings({"notify_webhook": False, "webhook_url": None})
    except (PhareApiError, PhareConnectionError) as err:
        _LOGGER.debug("Could not deregister the Phare webhook: %s", err)
    cloud = _cloud()
    if entry.data.get(CONF_CLOUDHOOK) and cloud is not None and cloud.async_active_subscription(hass):
        try:
            await cloud.async_delete_cloudhook(hass, webhook_id)
        except cloud.CloudNotAvailable:
            pass


def _make_handler(entry: PhareConfigEntry):
    seen: deque[tuple[str, str, str]] = deque(maxlen=WEBHOOK_DEDUPE_SIZE)

    async def handle(hass: HomeAssistant, webhook_id: str, request: web.Request) -> web.Response:
        body = await request.read()
        secret = entry.data.get(CONF_WEBHOOK_SECRET) or ""
        reason = verify(
            secret,
            request.headers.get(HEADER_TIMESTAMP),
            body,
            request.headers.get(HEADER_SIGNATURE),
            now=time.time(),
        )
        if reason:
            _LOGGER.warning("Dropped a Phare webhook delivery: %s", reason)
            return web.Response(status=401, text=reason)
        try:
            payload = json.loads(body)
        except ValueError:
            return web.Response(status=400, text="body is not JSON")
        if not isinstance(payload, dict) or payload.get("event") != "alert":
            return web.Response(status=400, text="unexpected event")
        delivery = str(payload.get("delivery_id") or request.headers.get(HEADER_DELIVERY) or "")
        alert = payload.get("data") or {}
        if not isinstance(alert, dict):
            return web.Response(status=400, text="alert is not an object")
        if alert.get("type_code") not in ALERT_TYPE_CODES:
            # Acknowledged, so the API does not retry it, and dropped: no state, no bus event.
            return web.Response(status=200, text="ignored")
        # One alert carries several records (new, then resolved); a repeat is the same record.
        record = (delivery, str(alert.get("stage")), str(alert.get("ts")))
        if delivery and record in seen:
            return web.Response(status=200, text="duplicate")
        if delivery:
            seen.append(record)
        if entry.runtime_data.apply_pushed_alert(alert):
            hass.bus.async_fire(EVENT_ALERT, {"entry_id": entry.entry_id, "delivery_id": delivery, **alert})
        return web.Response(status=200, text="ok")

    return handle
