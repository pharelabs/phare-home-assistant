"""Night path light: when the schedule starts and ends, in the home's local time."""

from __future__ import annotations

from datetime import time

from homeassistant.components.time import TimeEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import night_path
from .coordinator import PhareConfigEntry, PhareCoordinator
from .entity import PhareSettingEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: PhareConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    known: set[str] = set()

    @callback
    def add_new() -> None:
        new: list[TimeEntity] = []
        for device in coordinator.data.devices.values():
            if device.device_id in known or not night_path.has_schedule(device):
                continue
            known.add(device.device_id)
            new.append(PhareNightPathTime(coordinator, device.device_id, night_path.START, "night_path_start"))
            new.append(PhareNightPathTime(coordinator, device.device_id, night_path.STOP, "night_path_end"))
        if new:
            async_add_entities(new)

    add_new()
    entry.async_on_unload(coordinator.async_add_listener(add_new))


class PhareNightPathTime(PhareSettingEntity, TimeEntity):
    """One end of the schedule window. The window may run past midnight (21:00 to 06:00); a start
    equal to its end would never open, so that is refused."""

    _what = "night path light"

    def __init__(self, coordinator: PhareCoordinator, device_id: str, setting: str, key: str) -> None:
        super().__init__(coordinator, device_id, key)
        self._setting = setting
        self._other = night_path.STOP if setting == night_path.START else night_path.START
        self._attr_translation_key = key

    @property
    def native_value(self) -> time | None:
        value = self.setting(self._setting)
        return None if value is None else night_path.minutes_to_time(value)

    async def async_set_value(self, value: time) -> None:
        minutes = night_path.time_to_minutes(value)
        if minutes == self.setting(self._other):
            raise HomeAssistantError("The schedule's start and end must be different times.")
        await self.write({self._setting: minutes})
