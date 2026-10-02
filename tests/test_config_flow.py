"""Setup form: a good token becomes an entry named after the account; bad ones say why."""

from __future__ import annotations

import aiohttp
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.phare.const import CONF_BASE_URL, CONF_TOKEN, DEFAULT_BASE_URL, DOMAIN

from .conftest import BASE, TOKEN, payload


async def test_a_valid_token_creates_the_entry(hass: HomeAssistant, aioclient_mock) -> None:
    aioclient_mock.get(f"{BASE}/v1/user/settings", json=payload("user_settings.json"))
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {}

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_TOKEN: f" {TOKEN} "})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Test Account"
    assert result["data"] == {CONF_TOKEN: TOKEN, CONF_BASE_URL: DEFAULT_BASE_URL}
    assert result["result"].unique_id == "auth0|6957b01d2529aaa8d853e210"
    assert aioclient_mock.mock_calls[0][3]["Authorization"] == f"Bearer {TOKEN}"


async def test_a_refused_token_is_invalid_auth(hass: HomeAssistant, aioclient_mock) -> None:
    # API Gateway's own answer for an unknown, revoked or expired token.
    aioclient_mock.get(f"{BASE}/v1/user/settings", status=403, json={"message": "Forbidden"})
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_TOKEN: TOKEN})
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


async def test_an_unreachable_api_is_cannot_connect(hass: HomeAssistant, aioclient_mock) -> None:
    aioclient_mock.get(f"{BASE}/v1/user/settings", exc=aiohttp.ClientConnectionError("down"))
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_TOKEN: TOKEN})
    assert result["errors"] == {"base": "cannot_connect"}


async def test_a_server_error_is_unknown(hass: HomeAssistant, aioclient_mock) -> None:
    aioclient_mock.get(f"{BASE}/v1/user/settings", status=500, json={"error": "Internal server error"})
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_TOKEN: TOKEN})
    assert result["errors"] == {"base": "unknown"}


async def test_the_same_account_cannot_be_added_twice(hass: HomeAssistant, aioclient_mock) -> None:
    MockConfigEntry(domain=DOMAIN, unique_id="auth0|6957b01d2529aaa8d853e210", data={CONF_TOKEN: "old"}).add_to_hass(hass)
    aioclient_mock.get(f"{BASE}/v1/user/settings", json=payload("user_settings.json"))
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_TOKEN: TOKEN})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_the_base_url_is_advanced_only_and_must_be_https(hass: HomeAssistant, aioclient_mock) -> None:
    plain = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert CONF_BASE_URL not in plain["data_schema"].schema

    advanced = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER, "show_advanced_options": True}
    )
    assert CONF_BASE_URL in advanced["data_schema"].schema
    result = await hass.config_entries.flow.async_configure(
        advanced["flow_id"], {CONF_TOKEN: TOKEN, CONF_BASE_URL: "http://evil.example/protect"}
    )
    assert result["errors"] == {CONF_BASE_URL: "https_required"}
    assert aioclient_mock.call_count == 0, "the token was not sent anywhere"


async def test_reauth_accepts_a_new_token_for_the_same_account(hass: HomeAssistant, aioclient_mock) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="auth0|6957b01d2529aaa8d853e210",
        data={CONF_TOKEN: "phare_pat_old", CONF_BASE_URL: DEFAULT_BASE_URL},
    )
    entry.add_to_hass(hass)
    aioclient_mock.get(f"{BASE}/v1/user/settings", json=payload("user_settings.json"))

    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_TOKEN: TOKEN})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_TOKEN] == TOKEN


async def test_reauth_refuses_a_token_from_another_account(hass: HomeAssistant, aioclient_mock) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="auth0|someone-else",
        data={CONF_TOKEN: "phare_pat_old", CONF_BASE_URL: DEFAULT_BASE_URL},
    )
    entry.add_to_hass(hass)
    aioclient_mock.get(f"{BASE}/v1/user/settings", json=payload("user_settings.json"))

    result = await entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_TOKEN: TOKEN})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_account"
    assert entry.data[CONF_TOKEN] == "phare_pat_old"
