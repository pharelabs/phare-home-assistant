"""Pushed alerts: registration with the API, signature checks, and the sensor flipping at once."""

from __future__ import annotations

import hashlib
import hmac
import json
import time

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.core_config import async_process_ha_core_config
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from custom_components.phare.const import (
    CONF_BASE_URL,
    CONF_TOKEN,
    CONF_WEBHOOK_ID,
    CONF_WEBHOOK_SECRET,
    DEFAULT_BASE_URL,
    DOMAIN,
    EVENT_ALERT,
)

from .conftest import BASE, FROZEN_NOW, TOKEN, mock_api
from .test_init import KITCHEN, entity_id

SECRET = "0f" * 32
EXTERNAL = "https://ha.example.com"


async def setup_with_webhook(hass: HomeAssistant, aioclient_mock, freezer, *, external=EXTERNAL, register=None):
    freezer.move_to(FROZEN_NOW)
    if external:
        await async_process_ha_core_config(hass, {"external_url": external})
    assert await async_setup_component(hass, "webhook", {})
    mock_api(aioclient_mock)
    if register is None:
        register = {"json": {"data": {"user_id": "auth0|x", "notify_webhook": True, "webhook_secret": SECRET}}}
    aioclient_mock.post(f"{BASE}/v1/user/settings", **register)
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="auth0|6957b01d2529aaa8d853e210",
        title="Test Account",
        data={CONF_TOKEN: TOKEN, CONF_BASE_URL: DEFAULT_BASE_URL},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    return entry


def registration_calls(aioclient_mock):
    return [c for c in aioclient_mock.mock_calls if c[0] == "POST" and str(c[1]).endswith("/v1/user/settings")]


def signed(secret: str, payload: dict, *, ts: int | None = None) -> tuple[bytes, dict]:
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    ts = int(time.time()) if ts is None else ts
    sig = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    return body, {
        "Content-Type": "application/json",
        "X-Phare-Delivery": payload.get("delivery_id", "dlv_x"),
        "X-Phare-Timestamp": str(ts),
        "X-Phare-Signature": f"sha256={sig}",
    }


def alert_payload(device_id: str, code: str, *, stage: str = "new", delivery: str = "dlv_1") -> dict:
    return {
        "event": "alert",
        "delivery_id": delivery,
        "ts": "2026-10-02T09:59:30Z",
        "data": {
            "alert_id": f"alert_{device_id}_x", "type_code": code, "type": "Carbon Monoxide",
            "event": "CO detected", "stage": stage, "ts": "2026-10-02T09:59:30Z", "device_id": device_id,
        },
    }


async def test_setup_registers_https_webhook_and_keeps_the_secret(hass, aioclient_mock, freezer) -> None:
    entry = await setup_with_webhook(hass, aioclient_mock, freezer)
    calls = registration_calls(aioclient_mock)
    assert len(calls) == 1
    body = calls[0][2]
    assert body["notify_webhook"] is True
    assert body["webhook_url"].startswith(f"{EXTERNAL}/api/webhook/")
    assert entry.data[CONF_WEBHOOK_SECRET] == SECRET
    assert body["webhook_url"].endswith(entry.data[CONF_WEBHOOK_ID])


async def test_signed_delivery_flips_the_alert_sensor_before_the_next_poll(hass, aioclient_mock, freezer, hass_client_no_auth) -> None:
    entry = await setup_with_webhook(hass, aioclient_mock, freezer)
    sensor = entity_id(hass, "binary_sensor", f"{KITCHEN}_alert_fire")
    assert hass.states.get(sensor).state == STATE_OFF
    events = []
    hass.bus.async_listen(EVENT_ALERT, lambda e: events.append(e))

    client = await hass_client_no_auth()
    body, headers = signed(SECRET, alert_payload(KITCHEN, "fire"))
    resp = await client.post(f"/api/webhook/{entry.data[CONF_WEBHOOK_ID]}", data=body, headers=headers)
    assert resp.status == 200
    await hass.async_block_till_done()

    assert hass.states.get(sensor).state == STATE_ON
    assert len(events) == 1
    assert events[0].data["device_id"] == KITCHEN and events[0].data["delivery_id"] == "dlv_1"


async def test_resolved_delivery_clears_an_open_alert(hass, aioclient_mock, freezer, hass_client_no_auth) -> None:
    entry = await setup_with_webhook(hass, aioclient_mock, freezer)
    sensor = entity_id(hass, "binary_sensor", f"{KITCHEN}_alert_co")
    assert hass.states.get(sensor).state == STATE_ON, "fixture has an open CO alert in the kitchen"

    client = await hass_client_no_auth()
    body, headers = signed(SECRET, alert_payload(KITCHEN, "co", stage="resolved"))
    assert (await client.post(f"/api/webhook/{entry.data[CONF_WEBHOOK_ID]}", data=body, headers=headers)).status == 200
    await hass.async_block_till_done()
    assert hass.states.get(sensor).state == STATE_OFF


@pytest.mark.parametrize("tamper", ["body", "signature", "stale", "missing"])
async def test_bad_deliveries_are_dropped_and_logged(hass, aioclient_mock, freezer, hass_client_no_auth, caplog, tamper) -> None:
    entry = await setup_with_webhook(hass, aioclient_mock, freezer)
    sensor = entity_id(hass, "binary_sensor", f"{KITCHEN}_alert_fire")
    payload = alert_payload(KITCHEN, "fire")
    if tamper == "stale":
        body, headers = signed(SECRET, payload, ts=int(time.time()) - 301)
    else:
        body, headers = signed(SECRET, payload)
        if tamper == "body":
            body = body.replace(b'"new"', b'"old"')
        elif tamper == "signature":
            headers["X-Phare-Signature"] = "sha256=" + "0" * 64
        elif tamper == "missing":
            del headers["X-Phare-Signature"]

    client = await hass_client_no_auth()
    resp = await client.post(f"/api/webhook/{entry.data[CONF_WEBHOOK_ID]}", data=body, headers=headers)
    await hass.async_block_till_done()
    assert resp.status == 401
    assert hass.states.get(sensor).state == STATE_OFF
    assert "Dropped a Phare webhook delivery" in caplog.text


async def test_duplicate_delivery_is_acknowledged_once(hass, aioclient_mock, freezer, hass_client_no_auth) -> None:
    entry = await setup_with_webhook(hass, aioclient_mock, freezer)
    events = []
    hass.bus.async_listen(EVENT_ALERT, lambda e: events.append(e))
    client = await hass_client_no_auth()
    body, headers = signed(SECRET, alert_payload(KITCHEN, "fire", delivery="dlv_same"))
    for _ in range(2):
        assert (await client.post(f"/api/webhook/{entry.data[CONF_WEBHOOK_ID]}", data=body, headers=headers)).status == 200
    await hass.async_block_till_done()
    assert len(events) == 1


async def test_read_only_token_stays_on_polling(hass, aioclient_mock, freezer, caplog) -> None:
    entry = await setup_with_webhook(
        hass, aioclient_mock, freezer, register={"status": 403, "json": {"message": "Forbidden"}}
    )
    assert entry.state is ConfigEntryState.LOADED
    assert CONF_WEBHOOK_SECRET not in entry.data
    assert "control scope" in caplog.text
    assert hass.states.get(entity_id(hass, "sensor", f"{KITCHEN}_temperature")).state == "21.5"


async def test_without_an_https_url_nothing_is_registered(hass, aioclient_mock, freezer, caplog) -> None:
    entry = await setup_with_webhook(hass, aioclient_mock, freezer, external=None)
    assert registration_calls(aioclient_mock) == []
    assert CONF_WEBHOOK_ID not in entry.data
    assert "no https external URL" in caplog.text


async def test_removing_the_entry_deregisters_with_the_api(hass, aioclient_mock, freezer) -> None:
    entry = await setup_with_webhook(hass, aioclient_mock, freezer)
    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    calls = registration_calls(aioclient_mock)
    assert calls[-1][2] == {"notify_webhook": False, "webhook_url": None}
