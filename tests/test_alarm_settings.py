"""The alarm settings in Home Assistant: alert switches and the levels behind them."""

from __future__ import annotations

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er

from custom_components.phare.const import CONF_BASE_URL, CONF_TOKEN, DEFAULT_BASE_URL, DOMAIN

from .conftest import BASE, FROZEN_NOW, TOKEN, mock_api, payload
from .test_init import BEDROOM, KITCHEN, entity_id

# What a C1 Pro reports (the firmware defaults), plus keys this integration does not offer: they
# must not become entities even if they turned up.
REPORTED = {
    "FIRE_ALERT": 1, "REMOTE_IC": 1, "PWR_ALERT": 1, "BAT_ALERT": 1, "CO_ALERT": 1,
    "ALR_TGS_L": 10, "ALR_TGS_L_L": 5, "ALR_TGS_M": 25, "ALR_TGS_M_L": 20, "ALR_TGS_H": 50,
    "ALR_TGS_H_L": 45, "ALR_TGS_C": 100, "ALR_TGS_C_L": 90,
    "MOV_ALERT": 0, "ALR_MOV_THR": 3, "ALR_MOV_SAND_DUR": 15,
    "HUM_ALERT": 1, "ALR_HUM_THR": 80, "ALR_HUM_L": 75, "ALR_HUM_DUR": 3600,
    "TEMP_ALERT": 1, "ALR_TEMP_THR": 30, "ALR_TEMP_L": 28, "ALR_TEMP_DUR": 3600,
    "ALR_VTEMP_THR": 35, "ALR_VTEMP_L": 33,
    "SOUND_ALERT": 1, "ALR_SOUND_M": 85, "ALR_SOUND_H": 100, "ALR_SOUND_OST": 1440,
    "OTHER_ALERT": 1, "ALR_OTHER_THR": 30,
}
UNIQUE = "auth0|000000000000000000000001"


def _reported(aioclient_mock, device, reported):
    aioclient_mock.get(
        f"{BASE}/v1/devices/{device}/settings",
        json={"data": {"device_id": device, "reported": reported, "desired": {}}},
    )


async def _setup(hass, aioclient_mock, freezer, *, enable=()):
    """Kitchen is a C1 Pro (hw 10); Bedroom is turned into a C1 Core (hw 8). Both report REPORTED.
    `enable` pre-registers those entities enabled, as a user who switched them on would have."""
    devices = payload("devices.json")
    for raw in devices["data"]:
        if raw["device_id"] == BEDROOM:
            raw["hw"] = 8
    _reported(aioclient_mock, KITCHEN, REPORTED)
    _reported(aioclient_mock, BEDROOM, REPORTED)
    freezer.move_to(FROZEN_NOW)
    mock_api(aioclient_mock, devices=devices)
    entry = MockConfigEntry(domain=DOMAIN, unique_id=UNIQUE, title="Test Account",
                            data={CONF_TOKEN: TOKEN, CONF_BASE_URL: DEFAULT_BASE_URL})
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    for platform, unique_id in enable:
        registry.async_get_or_create(platform, DOMAIN, unique_id, config_entry=entry)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _registry(hass, platform, device, key):
    eid = entity_id(hass, platform, f"{device}_{key.lower()}")
    return er.async_get(hass).async_get(eid) if eid else None


async def test_every_customer_setting_is_offered_on_a_c1_pro(hass, aioclient_mock, freezer) -> None:
    await _setup(hass, aioclient_mock, freezer)
    for key in ("FIRE_ALERT", "PWR_ALERT", "BAT_ALERT", "CO_ALERT", "HUM_ALERT", "TEMP_ALERT", "SOUND_ALERT"):
        assert _registry(hass, "switch", KITCHEN, key) is not None, key
    for key in ("ALR_TGS_L", "ALR_TGS_C_L", "ALR_MOV_THR", "ALR_MOV_SAND_DUR", "ALR_HUM_DUR",
                "ALR_VTEMP_L", "ALR_SOUND_OST"):
        assert _registry(hass, "number", KITCHEN, key) is not None, key


async def test_keys_not_in_the_catalog_never_become_entities(hass, aioclient_mock, freezer) -> None:
    await _setup(hass, aioclient_mock, freezer)
    for platform in ("switch", "number"):
        for key in ("OTHER_ALERT", "ALR_OTHER_THR", "MOV_ALERT", "REMOTE_IC"):
            assert _registry(hass, platform, KITCHEN, key) is None, (platform, key)


async def test_a_c1_core_offers_only_the_settings_of_its_sensors(hass, aioclient_mock, freezer) -> None:
    await _setup(hass, aioclient_mock, freezer)
    assert _registry(hass, "switch", BEDROOM, "CO_ALERT") is not None
    assert _registry(hass, "number", BEDROOM, "ALR_TGS_M") is not None
    for platform, key in (("switch", "HUM_ALERT"), ("switch", "SOUND_ALERT"), ("number", "ALR_TEMP_THR"),
                          ("number", "ALR_MOV_THR")):
        assert _registry(hass, platform, BEDROOM, key) is None, key


async def test_a_key_the_device_does_not_report_is_not_offered(hass, aioclient_mock, freezer) -> None:
    devices = payload("devices.json")
    older = {k: v for k, v in REPORTED.items() if k not in ("ALR_TGS_L", "ALR_TGS_L_L")}
    _reported(aioclient_mock, KITCHEN, older)
    freezer.move_to(FROZEN_NOW)
    mock_api(aioclient_mock, devices=devices)
    entry = MockConfigEntry(domain=DOMAIN, unique_id=UNIQUE, data={CONF_TOKEN: TOKEN, CONF_BASE_URL: DEFAULT_BASE_URL})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert _registry(hass, "number", KITCHEN, "ALR_TGS_L") is None
    assert _registry(hass, "number", KITCHEN, "ALR_TGS_M") is not None


async def test_fire_and_co_switches_and_every_level_start_disabled(hass, aioclient_mock, freezer) -> None:
    await _setup(hass, aioclient_mock, freezer)
    assert _registry(hass, "switch", KITCHEN, "FIRE_ALERT").disabled_by is er.RegistryEntryDisabler.INTEGRATION
    assert _registry(hass, "switch", KITCHEN, "CO_ALERT").disabled_by is er.RegistryEntryDisabler.INTEGRATION
    assert _registry(hass, "number", KITCHEN, "ALR_HUM_THR").disabled_by is er.RegistryEntryDisabler.INTEGRATION
    assert _registry(hass, "switch", KITCHEN, "HUM_ALERT").disabled_by is None


async def test_switches_and_levels_write_their_key(hass, aioclient_mock, freezer) -> None:
    await _setup(hass, aioclient_mock, freezer, enable=(("number", f"{KITCHEN}_alr_hum_thr"),))
    aioclient_mock.post(f"{BASE}/v1/devices/{KITCHEN}/settings", json={"data": {}})

    switch = entity_id(hass, "switch", f"{KITCHEN}_sound_alert")
    await hass.services.async_call("switch", "turn_off", {ATTR_ENTITY_ID: switch}, blocking=True)
    number = entity_id(hass, "number", f"{KITCHEN}_alr_hum_thr")
    assert float(hass.states.get(number).state) == 80
    await hass.services.async_call("number", "set_value", {ATTR_ENTITY_ID: number, "value": 85}, blocking=True)

    posts = [c[2] for c in aioclient_mock.mock_calls if c[0] == "POST"]
    assert posts == [{"SOUND_ALERT": 0}, {"ALR_HUM_THR": 85}]
    assert hass.states.get(switch).state == "off"


async def test_a_clear_level_above_its_level_is_refused_before_it_is_sent(hass, aioclient_mock, freezer) -> None:
    await _setup(hass, aioclient_mock, freezer, enable=(("number", f"{KITCHEN}_alr_hum_l"),))
    number = entity_id(hass, "number", f"{KITCHEN}_alr_hum_l")
    with pytest.raises(HomeAssistantError, match="must not be above the humidity level"):
        await hass.services.async_call("number", "set_value", {ATTR_ENTITY_ID: number, "value": 90}, blocking=True)
    assert [c for c in aioclient_mock.mock_calls if c[0] == "POST"] == []
