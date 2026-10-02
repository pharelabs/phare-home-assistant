"""Setup: paste a personal access token, we check it against the account it belongs to."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import PhareApiClient, PhareApiError, PhareAuthError, PhareConnectionError
from .const import CONF_BASE_URL, CONF_TOKEN, DEFAULT_BASE_URL, DOMAIN

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema({vol.Required(CONF_TOKEN): str})
# Shown only with advanced mode on: pointing at another host sends the token there.
STEP_USER_ADVANCED_SCHEMA = STEP_USER_SCHEMA.extend(
    {vol.Optional(CONF_BASE_URL, default=DEFAULT_BASE_URL): str}
)
STEP_REAUTH_SCHEMA = vol.Schema({vol.Required(CONF_TOKEN): str})


async def validate_token(hass, token: str, base_url: str) -> dict[str, Any]:
    """The account the token belongs to, or an error code the form can show."""
    client = PhareApiClient(async_get_clientsession(hass), token.strip(), base_url)
    return await client.user_settings()


class PhareConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            token = user_input[CONF_TOKEN].strip()
            base_url = (user_input.get(CONF_BASE_URL) or DEFAULT_BASE_URL).strip()
            account: dict[str, Any] = {}
            if not base_url.startswith("https://"):
                errors[CONF_BASE_URL] = "https_required"
            else:
                try:
                    account = await validate_token(self.hass, token, base_url)
                except PhareAuthError:
                    errors["base"] = "invalid_auth"
                except PhareConnectionError:
                    errors["base"] = "cannot_connect"
                except PhareApiError:
                    _LOGGER.exception("Unexpected answer from the Phare API")
                    errors["base"] = "unknown"
            if not errors:
                user_id = account.get("user_id")
                if not user_id:
                    errors["base"] = "unknown"
                else:
                    await self.async_set_unique_id(user_id)
                    self._abort_if_unique_id_configured()
                    title = account.get("name") or account.get("email") or "Phare Protect"
                    return self.async_create_entry(
                        title=title,
                        data={CONF_TOKEN: token, CONF_BASE_URL: base_url},
                    )
        schema = STEP_USER_ADVANCED_SCHEMA if self.show_advanced_options else STEP_USER_SCHEMA
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """The token was revoked or expired: ask for a new one for the same account."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()
        if user_input is not None:
            token = user_input[CONF_TOKEN].strip()
            base_url = entry.data.get(CONF_BASE_URL, DEFAULT_BASE_URL)
            try:
                account = await validate_token(self.hass, token, base_url)
            except PhareAuthError:
                errors["base"] = "invalid_auth"
            except PhareConnectionError:
                errors["base"] = "cannot_connect"
            except PhareApiError:
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(account.get("user_id"))
                self._abort_if_unique_id_mismatch(reason="wrong_account")
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_TOKEN: token}
                )
        return self.async_show_form(
            step_id="reauth_confirm", data_schema=STEP_REAUTH_SCHEMA, errors=errors
        )
