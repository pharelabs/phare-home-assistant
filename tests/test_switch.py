"""The wireless interconnect switch: one per home, driven through the home, never per device."""

from __future__ import annotations

import pytest

from homeassistant.const import ATTR_ENTITY_ID, STATE_OFF, STATE_ON
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr

from custom_components.phare.const import DOMAIN

from .conftest import BASE, mock_api, payload
from .test_init import entity_id, setup_entry

HOME = "sp_home"


def _spaces(remote_ic):
    spaces = payload("spaces.json")
    spaces["data"][0]["remote_ic"] = remote_ic
    return spaces


def _switch(hass):
    return entity_id(hass, "switch", f"{HOME}_interconnect")


async def test_every_home_gets_the_switch_showing_the_homes_flag(hass, aioclient_mock, freezer) -> None:
    await setup_entry(hass, aioclient_mock, freezer, spaces=_spaces(True))
    assert hass.states.get(_switch(hass)).state == STATE_ON
    home = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, HOME)})
    assert home.model == "Home"
    assert home.area_id is None, "a home is not a room"


async def test_a_home_with_no_stored_value_reads_off(hass, aioclient_mock, freezer) -> None:
    await setup_entry(hass, aioclient_mock, freezer)  # the synthetic fixture carries no remote_ic
    assert hass.states.get(_switch(hass)).state == STATE_OFF


async def test_turning_it_on_switches_the_home_not_a_device(hass, aioclient_mock, freezer) -> None:
    await setup_entry(hass, aioclient_mock, freezer, spaces=_spaces(False))
    aioclient_mock.post(f"{BASE}/v1/spaces/{HOME}", json={"data": {"space_id": HOME, "remote_ic": True}})

    await hass.services.async_call("switch", "turn_on", {ATTR_ENTITY_ID: _switch(hass)}, blocking=True)

    posts = [c for c in aioclient_mock.mock_calls if c[0] == "POST"]
    assert [(str(c[1]), c[2]) for c in posts] == [(f"{BASE}/v1/spaces/{HOME}", {"remote_ic": True})]


@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        (400, {"error": "A home needs at least two compatible devices for the interconnect"}, "at least two"),
        (403, {"error": "Only an owner of this home can do that"}, "Only an owner"),
        (403, {"message": "Forbidden"}, "control scope"),
        (502, {"error": "The setting could not be applied to every device"}, "every device"),
    ],
)
async def test_a_refusal_says_why(hass, aioclient_mock, freezer, status, body, expected) -> None:
    await setup_entry(hass, aioclient_mock, freezer, spaces=_spaces(False))
    aioclient_mock.post(f"{BASE}/v1/spaces/{HOME}", status=status, json=body)

    with pytest.raises(HomeAssistantError) as raised:
        await hass.services.async_call("switch", "turn_on", {ATTR_ENTITY_ID: _switch(hass)}, blocking=True)
    assert expected in str(raised.value)


# ── per device ───────────────────────────────────────────────────────────────

from .test_init import BEDROOM, KITCHEN  # noqa: E402


def _devices(**per_device):
    devices = payload("devices.json")
    for raw in devices["data"]:
        raw.update(per_device.get(raw["device_id"], {}))
    return devices


async def test_switches_exist_only_where_the_device_can_take_them(hass, aioclient_mock, freezer) -> None:
    # Kitchen: hw 10 (radar), smoke-alarm firmware 2.1.0. Bedroom: hw 9 (no radar), 1.1.0 (before
    # the interconnect).
    devices = _devices(**{BEDROOM: {"fw_esp": "v1.1.0"}})
    await setup_entry(hass, aioclient_mock, freezer, devices=devices)
    assert entity_id(hass, "switch", f"{KITCHEN}_break_in") is not None
    assert entity_id(hass, "switch", f"{KITCHEN}_interconnect") is not None
    assert entity_id(hass, "switch", f"{BEDROOM}_break_in") is None, "hw 9 has no radar"
    assert entity_id(hass, "switch", f"{BEDROOM}_interconnect") is None, "firmware too old to act on it"


@pytest.mark.parametrize(
    ("device_value", "home_value", "expected"),
    [(None, True, STATE_ON), (None, False, STATE_OFF), (False, True, STATE_OFF), (True, False, STATE_ON)],
)
async def test_a_device_never_switched_follows_its_home(hass, aioclient_mock, freezer, device_value, home_value, expected) -> None:
    kitchen = {} if device_value is None else {"remote_ic": device_value}
    await setup_entry(hass, aioclient_mock, freezer, spaces=_spaces(home_value), devices=_devices(**{KITCHEN: kitchen}))
    assert hass.states.get(entity_id(hass, "switch", f"{KITCHEN}_interconnect")).state == expected


async def test_the_device_switches_write_that_device_and_read_the_list_back(hass, aioclient_mock, freezer) -> None:
    devices = _devices(**{KITCHEN: {"armed": False}})
    await setup_entry(hass, aioclient_mock, freezer, devices=devices)
    aioclient_mock.clear_requests()  # clears the responses too: register them again
    mock_api(aioclient_mock, devices=devices)
    aioclient_mock.post(f"{BASE}/v1/devices/{KITCHEN}/arm", json={"data": {"device_id": KITCHEN, "armed": True}})
    aioclient_mock.post(f"{BASE}/v1/devices/{KITCHEN}/settings", json={"data": {"device_id": KITCHEN}})

    await hass.services.async_call(
        "switch", "turn_on", {ATTR_ENTITY_ID: entity_id(hass, "switch", f"{KITCHEN}_break_in")}, blocking=True
    )
    await hass.services.async_call(
        "switch", "turn_off", {ATTR_ENTITY_ID: entity_id(hass, "switch", f"{KITCHEN}_interconnect")}, blocking=True
    )
    await hass.async_block_till_done()

    posts = [(str(c[1]), c[2]) for c in aioclient_mock.mock_calls if c[0] == "POST"]
    assert posts == [
        (f"{BASE}/v1/devices/{KITCHEN}/arm", {"armed": True}),
        (f"{BASE}/v1/devices/{KITCHEN}/settings", {"REMOTE_IC": 0}),
    ]
    gets = [str(c[1]) for c in aioclient_mock.mock_calls if c[0] == "GET"]
    assert f"{BASE}/v1/devices" in gets, "the device list is read back, not left for the next inventory poll"


async def test_a_device_switch_refusal_says_why(hass, aioclient_mock, freezer) -> None:
    await setup_entry(hass, aioclient_mock, freezer)
    aioclient_mock.post(
        f"{BASE}/v1/devices/{KITCHEN}/settings", status=400,
        json={"error": "This device does not support the wireless interconnect"},
    )
    with pytest.raises(HomeAssistantError) as raised:
        await hass.services.async_call(
            "switch", "turn_on", {ATTR_ENTITY_ID: entity_id(hass, "switch", f"{KITCHEN}_interconnect")}, blocking=True
        )
    assert "does not support the wireless interconnect" in str(raised.value)
