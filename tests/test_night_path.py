"""The night path light's settings in Home Assistant, against a mocked settings shadow."""

from __future__ import annotations

import pytest

from homeassistant.const import ATTR_ENTITY_ID, STATE_OFF, STATE_UNAVAILABLE
from homeassistant.exceptions import HomeAssistantError

from .conftest import BASE, mock_api, payload
from .test_init import BEDROOM, KITCHEN, entity_id, setup_entry

SHADOW = {"NPATH_EN": 1, "NPATH_BRIGHT": 40, "NPATH_DUR_S": 30, "NPATH_LIGHT_PCT": 20,
          "NPATH_ON_MS": 600, "NPATH_MODE": 3, "NPATH_START": 1260, "NPATH_STOP": 360, "TZ_OFFSET_MIN": 0}


def _devices(kitchen_fw=("v1.1.0", "v1.1.0"), bedroom_hw=8):
    devices = payload("devices.json")
    for raw in devices["data"]:
        if raw["device_id"] == KITCHEN:
            raw.update(fw_esp=kitchen_fw[0], fw_efr=kitchen_fw[1])
        else:
            raw.update(hw=bedroom_hw, fw_esp="v1.1.0", fw_efr="v1.1.0")
    return devices


def _spaces(tz="Europe/Madrid"):
    spaces = payload("spaces.json")
    spaces["data"][0]["address_tz"] = tz
    return spaces


def _settings(aioclient_mock, reported=None, device=KITCHEN, status=200):
    body = {"data": {"device_id": device, "reported": SHADOW if reported is None else reported, "desired": {}}}
    aioclient_mock.get(f"{BASE}/v1/devices/{device}/settings", status=status, json=body if status == 200 else {"error": "x"})


async def _setup(hass, aioclient_mock, freezer, *, reported=None, devices=None, spaces=None):
    _settings(aioclient_mock, reported)
    return await setup_entry(hass, aioclient_mock, freezer, devices=devices or _devices(), spaces=spaces or _spaces())


def _state(hass, platform, key):
    return hass.states.get(entity_id(hass, platform, f"{KITCHEN}_{key}"))


async def _posts(aioclient_mock):
    return [c[2] for c in aioclient_mock.mock_calls if c[0] == "POST" and str(c[1]).endswith("/settings")]


async def test_the_light_and_its_settings_show_what_the_device_reported(hass, aioclient_mock, freezer) -> None:
    await _setup(hass, aioclient_mock, freezer)
    assert _state(hass, "switch", "night_path").state == "on"
    assert float(_state(hass, "number", "night_path_brightness").state) == 40
    assert float(_state(hass, "number", "night_path_duration").state) == 30
    assert float(_state(hass, "number", "night_path_light_threshold").state) == 0.2, "hundredths of a percent"
    sensitivity = _state(hass, "number", "night_path_sensitivity")
    assert float(sensitivity.state) == 0.6, "seconds, as the app's slider"
    assert sensitivity.attributes["level"] == "medium"
    assert _state(hass, "select", "night_path_mode").state == "dark_and_schedule"
    assert _state(hass, "time", "night_path_start").state == "21:00:00"
    assert _state(hass, "time", "night_path_end").state == "06:00:00"


async def test_a_missing_key_reads_as_the_firmware_default(hass, aioclient_mock, freezer) -> None:
    await _setup(hass, aioclient_mock, freezer, reported={})
    assert _state(hass, "switch", "night_path").state == STATE_OFF
    assert float(_state(hass, "number", "night_path_brightness").state) == 60
    assert _state(hass, "select", "night_path_mode").state == "dark"


async def test_only_devices_that_have_the_light_get_it(hass, aioclient_mock, freezer) -> None:
    # Bedroom is a C1 Core here: no light at all. Kitchen on 1.0.x: the light, but no schedule.
    await _setup(hass, aioclient_mock, freezer, devices=_devices(kitchen_fw=("v1.0.4", "v1.0.4")))
    assert entity_id(hass, "switch", f"{BEDROOM}_night_path") is None
    assert entity_id(hass, "number", f"{BEDROOM}_night_path_brightness") is None
    assert _state(hass, "switch", "night_path") is not None
    assert entity_id(hass, "select", f"{KITCHEN}_night_path_mode") is None
    assert entity_id(hass, "time", f"{KITCHEN}_night_path_start") is None


async def test_settings_that_cannot_be_read_leave_the_entities_unavailable_not_the_poll(hass, aioclient_mock, freezer) -> None:
    _settings(aioclient_mock, status=403)
    await setup_entry(hass, aioclient_mock, freezer, devices=_devices(), spaces=_spaces())
    assert _state(hass, "switch", "night_path").state == STATE_UNAVAILABLE
    assert hass.states.get(entity_id(hass, "sensor", f"{KITCHEN}_temperature")).state != STATE_UNAVAILABLE


async def test_writes_use_the_firmware_keys_and_units(hass, aioclient_mock, freezer) -> None:
    await _setup(hass, aioclient_mock, freezer)
    aioclient_mock.post(f"{BASE}/v1/devices/{KITCHEN}/settings", json={"data": {}})

    await hass.services.async_call("switch", "turn_off", {ATTR_ENTITY_ID: _state(hass, "switch", "night_path").entity_id}, blocking=True)
    await hass.services.async_call("number", "set_value", {ATTR_ENTITY_ID: _state(hass, "number", "night_path_light_threshold").entity_id, "value": 0.05}, blocking=True)
    await hass.services.async_call("number", "set_value", {ATTR_ENTITY_ID: _state(hass, "number", "night_path_sensitivity").entity_id, "value": 0.15}, blocking=True)

    assert await _posts(aioclient_mock) == [{"NPATH_EN": 0}, {"NPATH_LIGHT_PCT": 5}, {"NPATH_ON_MS": 150}]
    # Shown at once, before the device reports it.
    assert _state(hass, "switch", "night_path").state == STATE_OFF
    assert float(_state(hass, "number", "night_path_sensitivity").state) == 0.15


async def test_a_schedule_write_sends_no_clock(hass, aioclient_mock, freezer) -> None:
    # The API sets the device's offset from the home's time zone; the integration never sends it.
    await _setup(hass, aioclient_mock, freezer)
    aioclient_mock.post(f"{BASE}/v1/devices/{KITCHEN}/settings", json={"data": {}})

    await hass.services.async_call("time", "set_value", {ATTR_ENTITY_ID: _state(hass, "time", "night_path_start").entity_id, "time": "22:30:00"}, blocking=True)

    assert await _posts(aioclient_mock) == [{"NPATH_START": 1350}]


async def test_choosing_dark_alone_writes_no_clock(hass, aioclient_mock, freezer) -> None:
    await _setup(hass, aioclient_mock, freezer)
    aioclient_mock.post(f"{BASE}/v1/devices/{KITCHEN}/settings", json={"data": {}})
    await hass.services.async_call("select", "select_option", {ATTR_ENTITY_ID: _state(hass, "select", "night_path_mode").entity_id, "option": "dark"}, blocking=True)
    assert await _posts(aioclient_mock) == [{"NPATH_MODE": 0}]


async def test_a_window_that_would_never_open_is_refused(hass, aioclient_mock, freezer) -> None:
    await _setup(hass, aioclient_mock, freezer)
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call("time", "set_value", {ATTR_ENTITY_ID: _state(hass, "time", "night_path_start").entity_id, "time": "06:00:00"}, blocking=True)
    assert await _posts(aioclient_mock) == []


async def test_a_refused_write_says_why(hass, aioclient_mock, freezer) -> None:
    await _setup(hass, aioclient_mock, freezer)
    aioclient_mock.post(f"{BASE}/v1/devices/{KITCHEN}/settings", status=403, json={"message": "Forbidden"})
    with pytest.raises(HomeAssistantError, match="control scope"):
        await hass.services.async_call("number", "set_value", {ATTR_ENTITY_ID: _state(hass, "number", "night_path_brightness").entity_id, "value": 80}, blocking=True)
