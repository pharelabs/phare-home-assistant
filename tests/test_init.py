"""The integration set up from recorded payloads: entities, availability, arming, re-auth."""

from __future__ import annotations

from datetime import timedelta

import pytest

from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from homeassistant.components.alarm_control_panel import (
    DOMAIN as ALARM_DOMAIN,
    SERVICE_ALARM_ARM_AWAY,
    AlarmControlPanelState,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import ATTR_ENTITY_ID, STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import area_registry as ar, device_registry as dr, entity_registry as er
from homeassistant.util import dt as dt_util

from custom_components.phare.const import CONF_BASE_URL, CONF_TOKEN, DEFAULT_BASE_URL, DOMAIN
from custom_components.phare.diagnostics import async_get_config_entry_diagnostics
from custom_components.phare.entity import suggested_area
from custom_components.phare.model import PhareDevice, PhareSpace

from .conftest import BASE, FROZEN_NOW, TOKEN, mock_api, payload

KITCHEN = "351103920000001"  # hw 10, online, open CO alert
BEDROOM = "351103920000002"  # hw 9, offline, no humidity reading
HALLWAY = "351103920000003"  # reached through the home only: no devices row, no status row


async def setup_entry(hass: HomeAssistant, aioclient_mock, freezer, **overrides) -> MockConfigEntry:
    freezer.move_to(FROZEN_NOW)
    mock_api(aioclient_mock, **overrides)
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="auth0|000000000000000000000001",
        title="Test Account",
        data={CONF_TOKEN: TOKEN, CONF_BASE_URL: DEFAULT_BASE_URL},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    return entry


def entity_id(hass: HomeAssistant, platform: str, unique_id: str) -> str | None:
    return er.async_get(hass).async_get_entity_id(platform, DOMAIN, unique_id)


async def test_sensors_exist_only_for_what_each_device_reports(hass, aioclient_mock, freezer) -> None:
    await setup_entry(hass, aioclient_mock, freezer)

    temp = hass.states.get(entity_id(hass, "sensor", f"{KITCHEN}_temperature"))
    assert temp.state == "21.5"
    assert temp.attributes["unit_of_measurement"] == "°C"
    assert hass.states.get(entity_id(hass, "sensor", f"{KITCHEN}_carbon_monoxide")).state == "0.4", "averaged CO wins"
    assert hass.states.get(entity_id(hass, "sensor", f"{KITCHEN}_humidity")).state == "45.2"

    # The bedroom device never reported humidity: no sensor, rather than one stuck at unknown.
    assert entity_id(hass, "sensor", f"{BEDROOM}_humidity") is None
    assert entity_id(hass, "sensor", f"{BEDROOM}_carbon_dioxide") is not None


async def test_an_offline_device_is_unavailable_except_its_connectivity(hass, aioclient_mock, freezer) -> None:
    await setup_entry(hass, aioclient_mock, freezer)

    assert hass.states.get(entity_id(hass, "sensor", f"{BEDROOM}_temperature")).state == STATE_UNAVAILABLE
    assert hass.states.get(entity_id(hass, "binary_sensor", f"{BEDROOM}_connectivity")).state == STATE_OFF
    assert hass.states.get(entity_id(hass, "binary_sensor", f"{KITCHEN}_connectivity")).state == STATE_ON


async def test_a_device_without_status_is_judged_by_its_reading_age(hass, aioclient_mock, freezer) -> None:
    await setup_entry(hass, aioclient_mock, freezer)
    temp = entity_id(hass, "sensor", f"{HALLWAY}_temperature")
    assert hass.states.get(temp).state == "20.1", "reading is 3 minutes old: reachable"

    freezer.tick(timedelta(minutes=20))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get(temp).state == STATE_UNAVAILABLE, "same reading, now 23 minutes old"


async def test_alert_sensors_follow_the_open_alerts(hass, aioclient_mock, freezer) -> None:
    await setup_entry(hass, aioclient_mock, freezer)

    co = hass.states.get(entity_id(hass, "binary_sensor", f"{KITCHEN}_alert_co"))
    assert co.state == STATE_ON
    assert co.attributes["device_class"] == "carbon_monoxide"
    assert co.attributes["alert_id"] == "alert_351103920000001_20261002T095000Z"
    assert hass.states.get(entity_id(hass, "binary_sensor", f"{KITCHEN}_alert_fire")).state == STATE_OFF
    # Resolved newest record: off, even though an older open record for the same alert exists.
    assert hass.states.get(entity_id(hass, "binary_sensor", f"{BEDROOM}_alert_fire")).state == STATE_UNAVAILABLE
    # Movement needs the radar: hw 10 has the sensor, hw 9 and unknown hardware do not.
    assert entity_id(hass, "binary_sensor", f"{KITCHEN}_alert_mvmt") is not None
    assert entity_id(hass, "binary_sensor", f"{BEDROOM}_alert_mvmt") is None
    assert entity_id(hass, "binary_sensor", f"{HALLWAY}_alert_mvmt") is None


async def test_arming_posts_to_capable_devices_only_and_the_state_survives_a_poll(
    hass, aioclient_mock, freezer
) -> None:
    await setup_entry(hass, aioclient_mock, freezer)
    panel = entity_id(hass, ALARM_DOMAIN, "sp_home_break_in")
    assert hass.states.get(panel).state == AlarmControlPanelState.DISARMED

    aioclient_mock.post(f"{BASE}/v1/devices/{KITCHEN}/arm", json={"data": {"device_id": KITCHEN, "armed": True}})
    # After the arm, the API reports the home armed.
    armed_spaces = payload("spaces.json")
    armed_spaces["data"][0]["armed"] = True
    aioclient_mock.clear_requests()
    mock_api(aioclient_mock, spaces=armed_spaces)
    aioclient_mock.post(f"{BASE}/v1/devices/{KITCHEN}/arm", json={"data": {"device_id": KITCHEN, "armed": True}})

    await hass.services.async_call(
        ALARM_DOMAIN, SERVICE_ALARM_ARM_AWAY, {ATTR_ENTITY_ID: panel}, blocking=True
    )
    await hass.async_block_till_done()

    posts = [c for c in aioclient_mock.mock_calls if c[0] == "POST"]
    assert [str(c[1]) for c in posts] == [f"{BASE}/v1/devices/{KITCHEN}/arm"], "hw 9 and unknown hw are skipped"
    assert hass.states.get(panel).state == AlarmControlPanelState.ARMED_AWAY

    # The next regular poll must not flip it back.
    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get(panel).state == AlarmControlPanelState.ARMED_AWAY


async def test_a_refused_token_mid_poll_starts_reauth(hass, aioclient_mock, freezer) -> None:
    entry = await setup_entry(hass, aioclient_mock, freezer)

    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{BASE}/v1/devices", json=payload("devices.json"))
    aioclient_mock.get(f"{BASE}/v1/spaces", status=403, json={"message": "Forbidden"})
    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    flows = [f for f in hass.config_entries.flow.async_progress() if f["context"].get("source") == "reauth"]
    assert len(flows) == 1 and flows[0]["context"]["entry_id"] == entry.entry_id


async def test_a_plan_without_api_access_mid_poll_starts_reauth(hass, aioclient_mock, freezer) -> None:
    # A handler's 403 normally means "not your resource" and only fails the poll. With
    # `membership_required` it means the account lost API access: entities wired into smoke
    # and CO automations must not go quietly stale, so Home Assistant asks the user to act.
    entry = await setup_entry(hass, aioclient_mock, freezer)

    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{BASE}/v1/devices", json=payload("devices.json"))
    aioclient_mock.get(
        f"{BASE}/v1/spaces",
        status=403,
        json={"error": "API access is not included in your plan", "code": "membership_required"},
    )
    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    flows = [f for f in hass.config_entries.flow.async_progress() if f["context"].get("source") == "reauth"]
    assert len(flows) == 1 and flows[0]["context"]["entry_id"] == entry.entry_id


async def test_a_plan_refusal_on_a_button_names_the_plan_not_the_scope(hass, aioclient_mock, freezer) -> None:
    await setup_entry(hass, aioclient_mock, freezer)
    aioclient_mock.post(
        f"{BASE}/v1/devices/{KITCHEN}/test",
        status=403,
        json={"error": "API access is not included in your plan", "code": "membership_required"},
    )
    with pytest.raises(HomeAssistantError) as raised:
        await hass.services.async_call(
            "button", "press", {ATTR_ENTITY_ID: entity_id(hass, "button", f"{KITCHEN}_test")}, blocking=True
        )
    assert "not included in your plan" in str(raised.value)
    assert "control scope" not in str(raised.value)


async def test_each_device_is_created_in_its_rooms_area(hass, aioclient_mock, freezer) -> None:
    # Areas are rooms in Home Assistant. One area per home put every device's alarms on one card.
    await setup_entry(hass, aioclient_mock, freezer)
    devices = dr.async_get(hass)
    areas = ar.async_get(hass)
    kitchen = devices.async_get_device(identifiers={(DOMAIN, KITCHEN)})
    assert areas.async_get_area(kitchen.area_id).name == "Kitchen"
    home = devices.async_get_device(identifiers={(DOMAIN, "sp_home")})
    if home is not None:
        assert home.area_id is None, "a home is not a room"


def test_a_room_name_carries_its_home_when_the_account_has_several() -> None:
    space = PhareSpace(space_id="sp_1", address="Flat 1 Maldon")
    device = PhareDevice(device_id="119", room="Living Room")
    assert suggested_area(device, space, home_count=1) == "Living Room"
    assert suggested_area(device, space, home_count=2) == "Living Room (Flat 1 Maldon)"
    # No room reported: the home is the best place there is.
    assert suggested_area(PhareDevice(device_id="120"), space, home_count=2) == "Flat 1 Maldon"
    assert suggested_area(PhareDevice(device_id="121"), None, home_count=1) is None


async def test_test_identify_and_silence_are_controls(hass, aioclient_mock, freezer) -> None:
    await setup_entry(hass, aioclient_mock, freezer)
    registry = er.async_get(hass)
    for key in ("test", "identify", "silence"):
        entry = registry.async_get(entity_id(hass, "button", f"{KITCHEN}_{key}"))
        assert entry.entity_category is None, f"{key} is a control, not a diagnostic"


async def test_the_device_card_shows_hardware_every_firmware_and_the_mac(hass, aioclient_mock, freezer) -> None:
    devices = payload("devices.json")
    devices["data"][0].update(
        hw="7", fw_esp="v0.6.2", fw_efr="v0.6.1", fw_nrf="v0.6.3",
        mac="a1:b2:c3:d4:e5:f6", iccid="8900000000000000000",
    )
    await setup_entry(hass, aioclient_mock, freezer, devices=devices)

    card = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, KITCHEN)})
    assert card.model == card.hw_version == "Phare C1 (v7)"
    assert card.sw_version == "Smoke alarm v0.6.2 · Controller v0.6.1 · Cellular modem v0.6.3"
    assert (dr.CONNECTION_NETWORK_MAC, "a1:b2:c3:d4:e5:f6") in card.connections

    iccid = entity_id(hass, "sensor", f"{KITCHEN}_iccid")
    assert hass.states.get(iccid).state == "8900000000000000000"
    assert er.async_get(hass).async_get(iccid).entity_category == "diagnostic"
    # A board without a SIM gets no ICCID entity sitting at unknown.
    assert entity_id(hass, "sensor", f"{BEDROOM}_iccid") is None


async def test_diagnostics_hide_the_hardware_identifiers(hass, aioclient_mock, freezer) -> None:
    devices = payload("devices.json")
    devices["data"][0].update(mac="a1:b2:c3:d4:e5:f6", iccid="8900000000000000000")
    entry = await setup_entry(hass, aioclient_mock, freezer, devices=devices)
    dump = str(await async_get_config_entry_diagnostics(hass, entry))
    assert "8900000000000000000" not in dump
    assert "a1:b2:c3:d4:e5:f6" not in dump


async def test_unload_cleans_up(hass, aioclient_mock, freezer) -> None:
    entry = await setup_entry(hass, aioclient_mock, freezer)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED
    assert hass.states.get(entity_id(hass, "sensor", f"{KITCHEN}_temperature")).state == STATE_UNAVAILABLE


async def test_diagnostics_show_readings_without_firmware_internals_or_names(hass, aioclient_mock, freezer) -> None:
    from custom_components.phare.diagnostics import HIDDEN_READINGS, _readings

    raw = {key: 1.0 for key in HIDDEN_READINGS} | {"co_avg": 0.1363, "tmp_c": 22.4, "co2": 585.3}
    assert _readings(raw) == {"co": 0.1363, "co2": 585.3, "tmp_c": 22.4}
    # No average reported: the raw CO is what the sensor shows, so it stays.
    assert _readings({"co": 2.0, "tmp_c": 20.0}) == {"co": 2.0, "tmp_c": 20.0}

    alerts = payload("alerts.json")
    for alert in alerts["data"]:
        alert["event"] = "Jane Example has been notified"
    entry = await setup_entry(hass, aioclient_mock, freezer, alerts=alerts)
    dump = str(await async_get_config_entry_diagnostics(hass, entry))
    assert "Jane Example" not in dump
    for key in ("bat_i", "cusum", "p_fire", "pd_ir", "mains_v"):
        assert f"'{key}'" not in dump


async def test_a_polled_alert_of_a_type_not_exposed_creates_nothing(hass, aioclient_mock, freezer) -> None:
    alerts = payload("alerts.json")
    template = dict(alerts["data"][0])
    for code in ("bp", "hs"):
        alerts["data"].append(template | {"type_code": code, "alert_id": f"alert_{KITCHEN}_{code}", "stage": "new"})
    await setup_entry(hass, aioclient_mock, freezer, alerts=alerts)
    for code in ("bp", "hs"):
        assert entity_id(hass, "binary_sensor", f"{KITCHEN}_alert_{code}") is None


async def test_setup_removes_entities_of_alert_types_no_longer_exposed(hass, aioclient_mock, freezer) -> None:
    # An install from before the type was dropped still has its entity in the registry.
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id="auth0|000000000000000000000001", title="Test Account",
        data={CONF_TOKEN: TOKEN, CONF_BASE_URL: DEFAULT_BASE_URL},
    )
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    stale = registry.async_get_or_create("binary_sensor", DOMAIN, f"{KITCHEN}_alert_bp", config_entry=entry)
    kept = registry.async_get_or_create("binary_sensor", DOMAIN, f"{KITCHEN}_alert_fire", config_entry=entry)

    freezer.move_to(FROZEN_NOW)
    mock_api(aioclient_mock)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert registry.async_get(stale.entity_id) is None
    assert registry.async_get(kept.entity_id) is not None
