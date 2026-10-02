"""Test, identify and silence: fire-and-forget commands to a device."""

from __future__ import annotations

from homeassistant.components.button import (
    ButtonDeviceClass,
    ButtonEntity,
    ButtonEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import PhareApiError, PhareAuthError, PhareConnectionError
from .coordinator import PhareConfigEntry, PhareCoordinator
from .entity import PhareDeviceEntity

BUTTONS: tuple[ButtonEntityDescription, ...] = (
    ButtonEntityDescription(key="test", translation_key="test", entity_category=EntityCategory.DIAGNOSTIC),
    ButtonEntityDescription(
        key="identify",
        translation_key="identify",
        device_class=ButtonDeviceClass.IDENTIFY,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    # Silencing a live alarm from an automation is a decision each household should make on
    # purpose, so the button exists but starts disabled.
    ButtonEntityDescription(
        key="silence", translation_key="silence", entity_registry_enabled_default=False
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: PhareConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    known: set[str] = set()

    @callback
    def add_new() -> None:
        new = [
            PhareButton(coordinator, device_id, description)
            for device_id in coordinator.data.devices
            if device_id not in known
            for description in BUTTONS
        ]
        known.update(coordinator.data.devices)
        if new:
            async_add_entities(new)

    add_new()
    entry.async_on_unload(coordinator.async_add_listener(add_new))


class PhareButton(PhareDeviceEntity, ButtonEntity):
    def __init__(
        self, coordinator: PhareCoordinator, device_id: str, description: ButtonEntityDescription
    ) -> None:
        super().__init__(coordinator, device_id)
        self.entity_description = description
        self._attr_unique_id = f"{device_id}_{description.key}"

    async def async_press(self) -> None:
        try:
            await self.coordinator.client.command(self._device_id, self.entity_description.key)
        except PhareAuthError as err:
            raise HomeAssistantError(
                "Phare refused the request: the token may lack the control scope."
            ) from err
        except (PhareApiError, PhareConnectionError) as err:
            raise HomeAssistantError(f"Could not {self.entity_description.key} the device: {err}") from err
