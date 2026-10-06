"""The integration set up from recorded payloads: entities, availability, arming, re-auth."""

from __future__ import annotations

from datetime import timedelta

from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from homeassistant.components.alarm_control_panel import (
    DOMAIN as ALARM_DOMAIN,
    SERVICE_ALARM_ARM_AWAY,
    AlarmControlPanelState,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import ATTR_ENTITY_ID, STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from custom_components.phare.const import CONF_BASE_URL, CONF_TOKEN, DEFAULT_BASE_URL, DOMAIN

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


async def test_unload_cleans_up(hass, aioclient_mock, freezer) -> None:
    entry = await setup_entry(hass, aioclient_mock, freezer)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED
    assert hass.states.get(entity_id(hass, "sensor", f"{KITCHEN}_temperature")).state == STATE_UNAVAILABLE
