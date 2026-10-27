"""Pushed alerts: registration with the API, signature checks, and the sensor flipping at once."""

from __future__ import annotations

import hashlib
import hmac
import json
import time

import aiohttp

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.core_config import async_process_ha_core_config
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from custom_components.phare.diagnostics import async_get_config_entry_diagnostics

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


def alert_payload(device_id: str, code: str, *, stage: str = "new", delivery: str = "dlv_1",
                  ts: str = "2026-10-02T09:59:30Z") -> dict:
    return {
        "event": "alert",
        "delivery_id": delivery,
        "ts": ts,
        "data": {
            "alert_id": f"alert_{device_id}_x", "type_code": code, "type": "Carbon Monoxide",
            "event": "CO detected", "stage": stage, "ts": ts, "device_id": device_id,
        },
    }


async def post(hass, client, entry, payload, secret=SECRET, **kw):
    body, headers = signed(secret, payload, **kw)
    resp = await client.post(f"/api/webhook/{entry.data[CONF_WEBHOOK_ID]}", data=body, headers=headers)
    await hass.async_block_till_done()
    return resp


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


async def test_resolution_with_the_same_delivery_id_is_not_a_duplicate(hass, aioclient_mock, freezer, hass_client_no_auth) -> None:
    # The API's delivery id is per alert and recipient; the new and resolved records share it.
    entry = await setup_with_webhook(hass, aioclient_mock, freezer)
    sensor = entity_id(hass, "binary_sensor", f"{KITCHEN}_alert_fire")
    events = []
    hass.bus.async_listen(EVENT_ALERT, lambda e: events.append(e))
    client = await hass_client_no_auth()
    assert (await post(hass, client, entry, alert_payload(KITCHEN, "fire", delivery="dlv_a"))).status == 200
    assert hass.states.get(sensor).state == STATE_ON
    resolved = alert_payload(KITCHEN, "fire", delivery="dlv_a", stage="resolved", ts="2026-10-02T09:59:50Z")
    assert (await post(hass, client, entry, resolved)).status == 200
    assert hass.states.get(sensor).state == STATE_OFF
    assert [e.data["stage"] for e in events] == ["new", "resolved"]


async def test_reload_keeps_the_webhook_and_rotates_an_unreturned_secret(hass, aioclient_mock, freezer, hass_client_no_auth) -> None:
    entry = await setup_with_webhook(hass, aioclient_mock, freezer)
    webhook_id = entry.data[CONF_WEBHOOK_ID]
    # Second registration of the same URL: the API returns no secret.
    aioclient_mock.clear_requests()
    mock_api(aioclient_mock)
    aioclient_mock.post(f"{BASE}/v1/user/settings", json={"data": {"user_id": "auth0|x", "notify_webhook": True}})
    aioclient_mock.post(f"{BASE}/v1/user/webhook/rotate", json={"data": {"user_id": "auth0|x", "webhook_secret": "ab" * 32}})
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert entry.data[CONF_WEBHOOK_ID] == webhook_id
    assert entry.data[CONF_WEBHOOK_SECRET] == "ab" * 32
    assert registration_calls(aioclient_mock)[0][2]["webhook_url"].endswith(webhook_id)
    # Exactly one live handler, signing with the rotated secret.
    client = await hass_client_no_auth()
    assert (await post(hass, client, entry, alert_payload(KITCHEN, "fire"), secret="ab" * 32)).status == 200
    assert (await post(hass, client, entry, alert_payload(KITCHEN, "fire", delivery="dlv_2"), secret=SECRET)).status == 401


async def test_diagnostics_redact_the_webhook_secret(hass, aioclient_mock, freezer) -> None:
    entry = await setup_with_webhook(hass, aioclient_mock, freezer)
    dump = await async_get_config_entry_diagnostics(hass, entry)
    assert dump["entry"][CONF_WEBHOOK_SECRET] == "**REDACTED**"
    assert dump["entry"][CONF_WEBHOOK_ID] == "**REDACTED**"
    assert dump["entry"][CONF_TOKEN] == "**REDACTED**"


async def test_url_refused_by_the_api_does_not_blame_the_token(hass, aioclient_mock, freezer, caplog) -> None:
    entry = await setup_with_webhook(
        hass, aioclient_mock, freezer,
        register={"status": 400, "json": {"error": "webhook_url hostname does not resolve"}},
    )
    assert entry.state is ConfigEntryState.LOADED
    assert "hostname does not resolve" in caplog.text
    assert "control scope" not in caplog.text


async def test_losing_the_external_url_tells_the_api(hass, aioclient_mock, freezer) -> None:
    entry = await setup_with_webhook(hass, aioclient_mock, freezer)
    aioclient_mock.clear_requests()
    mock_api(aioclient_mock)
    aioclient_mock.post(f"{BASE}/v1/user/settings", json={"data": {}})
    await async_process_ha_core_config(hass, {"external_url": "http://ha.local:8123"})
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert registration_calls(aioclient_mock)[-1][2] == {"notify_webhook": False, "webhook_url": None}


async def test_an_older_poll_record_does_not_undo_a_pushed_alert(hass, aioclient_mock, freezer, hass_client_no_auth) -> None:
    # The fixture's alert window holds an older `resolved` fire record for the kitchen: a poll
    # fetched before the push carries it and must not clear the newer pushed alert.
    entry = await setup_with_webhook(hass, aioclient_mock, freezer)
    sensor = entity_id(hass, "binary_sensor", f"{KITCHEN}_alert_fire")
    client = await hass_client_no_auth()
    assert (await post(hass, client, entry, alert_payload(KITCHEN, "fire"))).status == 200
    assert hass.states.get(sensor).state == STATE_ON
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(sensor).state == STATE_ON


async def test_an_out_of_order_retry_does_not_reopen_a_resolved_alert(hass, aioclient_mock, freezer, hass_client_no_auth) -> None:
    entry = await setup_with_webhook(hass, aioclient_mock, freezer)
    sensor = entity_id(hass, "binary_sensor", f"{KITCHEN}_alert_fire")
    client = await hass_client_no_auth()
    resolved = alert_payload(KITCHEN, "fire", delivery="dlv_b", stage="resolved", ts="2026-10-02T09:59:50Z")
    assert (await post(hass, client, entry, resolved)).status == 200
    assert (await post(hass, client, entry, alert_payload(KITCHEN, "fire", delivery="dlv_b"))).status == 200
    assert hass.states.get(sensor).state == STATE_OFF


async def test_api_unreachable_on_reload_keeps_the_stored_webhook(hass, aioclient_mock, freezer, hass_client_no_auth) -> None:
    entry = await setup_with_webhook(hass, aioclient_mock, freezer)
    aioclient_mock.clear_requests()
    mock_api(aioclient_mock)
    aioclient_mock.post(f"{BASE}/v1/user/settings", exc=aiohttp.ClientError("down"))
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.data[CONF_WEBHOOK_SECRET] == SECRET
    client = await hass_client_no_auth()
    assert (await post(hass, client, entry, alert_payload(KITCHEN, "fire"))).status == 200
    assert hass.states.get(entity_id(hass, "binary_sensor", f"{KITCHEN}_alert_fire")).state == STATE_ON


async def test_a_non_object_alert_is_rejected(hass, aioclient_mock, freezer, hass_client_no_auth) -> None:
    entry = await setup_with_webhook(hass, aioclient_mock, freezer)
    client = await hass_client_no_auth()
    payload = {"event": "alert", "delivery_id": "dlv_l", "data": ["not", "an", "object"]}
    assert (await post(hass, client, entry, payload)).status == 400


async def test_losing_the_external_url_forgets_the_webhook(hass, aioclient_mock, freezer) -> None:
    entry = await setup_with_webhook(hass, aioclient_mock, freezer)
    aioclient_mock.clear_requests()
    mock_api(aioclient_mock)
    aioclient_mock.post(f"{BASE}/v1/user/settings", json={"data": {}})
    await async_process_ha_core_config(hass, {"external_url": "http://ha.local:8123"})
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert CONF_WEBHOOK_ID not in entry.data and CONF_WEBHOOK_SECRET not in entry.data
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert len(registration_calls(aioclient_mock)) == 1


@pytest.mark.parametrize("code", ["bp", "hs", "not_a_type"])
async def test_a_pushed_alert_of_a_type_not_exposed_leaves_no_trace(hass, aioclient_mock, freezer, hass_client_no_auth, code) -> None:
    entry = await setup_with_webhook(hass, aioclient_mock, freezer)
    events = []
    hass.bus.async_listen(EVENT_ALERT, lambda e: events.append(e))
    before = {s.entity_id: s.state for s in hass.states.async_all()}

    resp = await post(hass, await hass_client_no_auth(), entry, alert_payload(KITCHEN, code))

    assert resp.status == 200, "acknowledged, so the API does not retry it"
    assert events == []
    assert {s.entity_id: s.state for s in hass.states.async_all()} == before
    assert (KITCHEN, code) not in entry.runtime_data._active_alerts
