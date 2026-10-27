"""Night path light: what lets motion turn the light on."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
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
        new = [
            PhareNightPathTriggerMode(coordinator, device.device_id)
            for device in coordinator.data.devices.values()
            if device.device_id not in known and night_path.has_schedule(device)
        ]
        known.update(e._device_id for e in new)
        if new:
            async_add_entities(new)

    add_new()
    entry.async_on_unload(coordinator.async_add_listener(add_new))


class PhareNightPathTriggerMode(PhareSettingEntity, SelectEntity):
    """Dark (the default), the schedule alone, or both together. The schedule runs on the home's
    local time: the API gives the device its offset with every schedule write."""

    _attr_translation_key = "night_path_mode"
    _attr_options = list(night_path.MODE_OPTIONS)
    _what = "night path light"

    def __init__(self, coordinator: PhareCoordinator, device_id: str) -> None:
        super().__init__(coordinator, device_id, "night_path_mode")

    @property
    def current_option(self) -> str | None:
        value = self.setting(night_path.MODE)
        if value is None:
            return None
        return next((o for o, v in night_path.MODE_OPTIONS.items() if v == value), None)

    async def async_select_option(self, option: str) -> None:
        mode = night_path.MODE_OPTIONS[option]
        if night_path.uses_schedule(mode) and self.setting(night_path.START) == self.setting(night_path.STOP):
            raise HomeAssistantError("Set different start and end times before using the schedule.")
        await self.write({night_path.MODE: mode})
