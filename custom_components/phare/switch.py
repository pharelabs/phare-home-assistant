"""Switches: the wireless interconnect for a whole home, and per device the break-in alarm and the
device's own part in the interconnect.

The interconnect, on, makes one alarm detecting smoke sound every alarm in the home. A home's
switch flips every compatible device; a device's switch flips that device only, and the API then
reconciles the home's flag ("every compatible device has it on"). The same goes for arming. Each
switch shows what the API reconciled, read back after the write, not what was asked.
"""

from __future__ import annotations

from collections.abc import Awaitable
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import night_path, settings_catalog
from .api import PhareApiError, PhareConnectionError
from .const import HW_WITHOUT_RADAR, INTERCONNECT_MIN_ESP
from .coordinator import PhareConfigEntry, PhareCoordinator
from .entity import PhareDeviceEntity, PhareHomeEntity, PhareSettingEntity, refused
from .model import PhareDevice, parse_version


ALARM_SWITCHES = tuple(s for s in settings_catalog.SETTINGS if s.kind == "switch")


def can_arm(device: PhareDevice) -> bool:
    """The API answers 400 to arming a board without a radar (hw 8 and 9), and an unknown revision
    is left out: for a token that is every device reached through a shared home."""
    return device.hw is not None and device.hw not in HW_WITHOUT_RADAR


def can_interconnect(device: PhareDevice) -> bool:
    """Smoke-alarm firmware from INTERCONNECT_MIN_ESP; older builds would accept and ignore it."""
    version = parse_version(device.firmware.get("fw_esp"))
    return version is not None and version >= INTERCONNECT_MIN_ESP


async def async_setup_entry(
    hass: HomeAssistant, entry: PhareConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    known: set[tuple[str, str]] = set()

    @callback
    def add_new() -> None:
        new: list[SwitchEntity] = []
        # Every home carries the flag, so every home gets the switch: one with fewer than two
        # compatible devices still shows "off", and turning it on says why it cannot be.
        for space_id, space in coordinator.data.spaces.items():
            if (space_id, "interconnect") not in known and space.remote_ic is not None:
                known.add((space_id, "interconnect"))
                new.append(PhareHomeInterconnectSwitch(coordinator, space_id))
        for device in coordinator.data.devices.values():
            if can_arm(device) and (device.device_id, "break_in") not in known:
                known.add((device.device_id, "break_in"))
                new.append(PhareDeviceBreakInSwitch(coordinator, device.device_id))
            if can_interconnect(device) and (device.device_id, "interconnect") not in known:
                known.add((device.device_id, "interconnect"))
                new.append(PhareDeviceInterconnectSwitch(coordinator, device.device_id))
            if night_path.has_light(device) and (device.device_id, "night_path") not in known:
                known.add((device.device_id, "night_path"))
                new.append(PhareNightPathSwitch(coordinator, device.device_id))
            reported = coordinator.settings.get(device.device_id, {})
            has = settings_catalog.features(device.hw)
            for setting in ALARM_SWITCHES:
                if (device.device_id, setting.key) in known:
                    continue
                if setting.feature in has and setting.key in reported:
                    known.add((device.device_id, setting.key))
                    new.append(PhareAlertSwitch(coordinator, device.device_id, setting))
        if new:
            async_add_entities(new)

    add_new()
    entry.async_on_unload(coordinator.async_add_listener(add_new))


async def _write(coordinator: PhareCoordinator, call: Awaitable[Any], what: str) -> None:
    try:
        await call
    except (PhareApiError, PhareConnectionError) as err:
        refused(err, what)
    # The per-device flags come from the device list, which a poll reads only every few minutes:
    # read it now so every switch, the home's and its devices', shows what the API reconciled.
    coordinator.refresh_inventory = True
    await coordinator.async_request_refresh()


class PhareHomeInterconnectSwitch(PhareHomeEntity, SwitchEntity):
    _attr_translation_key = "interconnect"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: PhareCoordinator, space_id: str) -> None:
        super().__init__(coordinator, space_id)
        self._attr_unique_id = f"{space_id}_interconnect"

    @property
    def is_on(self) -> bool | None:
        space = self.space
        return space.remote_ic if space else None

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set(False)

    async def _set(self, on: bool) -> None:
        call = self.coordinator.client.update_space(self.space_id, {"remote_ic": on})
        await _write(self.coordinator, call, "wireless interconnect")


class PhareDeviceBreakInSwitch(PhareDeviceEntity, SwitchEntity):
    """This device's break-in (movement) detection, beside the home's break-in alarm panel."""

    _attr_translation_key = "break_in"

    def __init__(self, coordinator: PhareCoordinator, device_id: str) -> None:
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_break_in"

    @property
    def is_on(self) -> bool | None:
        device = self.device
        return device.armed if device else None

    async def async_turn_on(self, **kwargs: Any) -> None:
        await _write(self.coordinator, self.coordinator.client.arm(self._device_id, True), "break-in alarm")

    async def async_turn_off(self, **kwargs: Any) -> None:
        await _write(self.coordinator, self.coordinator.client.arm(self._device_id, False), "break-in alarm")


class PhareDeviceInterconnectSwitch(PhareDeviceEntity, SwitchEntity):
    """Whether this device takes part in its home's wireless interconnect."""

    _attr_translation_key = "interconnect"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: PhareCoordinator, device_id: str) -> None:
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_interconnect"

    @property
    def is_on(self) -> bool | None:
        return self.coordinator.data.device_interconnect(self._device_id)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set(False)

    async def _set(self, on: bool) -> None:
        # The firmware key takes the integer 1 or 0, unlike the home endpoint's boolean.
        call = self.coordinator.client.update_device_settings(self._device_id, {"REMOTE_IC": int(on)})
        await _write(self.coordinator, call, "wireless interconnect")


class PhareNightPathSwitch(PhareSettingEntity, SwitchEntity):
    """The night path light on or off: when on, motion in the dark (or in the scheduled hours)
    lights the ring."""

    _attr_translation_key = "night_path"
    _what = "night path light"

    def __init__(self, coordinator: PhareCoordinator, device_id: str) -> None:
        super().__init__(coordinator, device_id, "night_path")

    @property
    def is_on(self) -> bool | None:
        value = self.setting(night_path.EN)
        return None if value is None else value == 1

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.write({night_path.EN: 1})

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.write({night_path.EN: 0})


class PhareAlertSwitch(PhareSettingEntity, SwitchEntity):
    """Whether this device raises one type of alert. Off, nobody is told about that kind of event;
    the alarm itself still sounds."""

    _what = "alert setting"

    def __init__(self, coordinator: PhareCoordinator, device_id: str, setting: settings_catalog.Setting) -> None:
        super().__init__(coordinator, device_id, setting.key.lower())
        self._key = setting.key
        self._attr_translation_key = setting.translation_key
        self._attr_entity_registry_enabled_default = setting.enabled_default

    @property
    def is_on(self) -> bool | None:
        value = self.setting(self._key)
        return None if value is None else value == 1

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.write({self._key: 1})

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.write({self._key: 0})
