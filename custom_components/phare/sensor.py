"""Readings: one sensor per measured quantity per device, created only for what the device reports."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    CONCENTRATION_PARTS_PER_MILLION,
    PERCENTAGE,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import PhareConfigEntry, PhareCoordinator, pick_reading
from .entity import PhareDeviceEntity


@dataclass(frozen=True, kw_only=True)
class PhareSensorDescription(SensorEntityDescription):
    value_fn: Callable[[dict], float | None]


SENSORS: tuple[PhareSensorDescription, ...] = (
    PhareSensorDescription(
        key="temperature",
        translation_key="temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        suggested_display_precision=1,
        value_fn=lambda r: pick_reading(r, "temperature"),
    ),
    PhareSensorDescription(
        key="humidity",
        translation_key="humidity",
        device_class=SensorDeviceClass.HUMIDITY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        suggested_display_precision=0,
        value_fn=lambda r: pick_reading(r, "humidity"),
    ),
    PhareSensorDescription(
        key="carbon_monoxide",
        translation_key="carbon_monoxide",
        device_class=SensorDeviceClass.CO,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=CONCENTRATION_PARTS_PER_MILLION,
        suggested_display_precision=1,
        value_fn=lambda r: pick_reading(r, "carbon_monoxide"),
    ),
    PhareSensorDescription(
        key="carbon_dioxide",
        translation_key="carbon_dioxide",
        device_class=SensorDeviceClass.CO2,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=CONCENTRATION_PARTS_PER_MILLION,
        suggested_display_precision=0,
        value_fn=lambda r: pick_reading(r, "carbon_dioxide"),
    ),
    # The API's `voc` carries no unit (the firmware's index, not ppb), so no device class: a VOC
    # device class would promise a unit Home Assistant then converts.
    PhareSensorDescription(
        key="voc",
        translation_key="voc",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=lambda r: pick_reading(r, "voc"),
    ),
    PhareSensorDescription(
        key="air_quality",
        translation_key="air_quality",
        device_class=SensorDeviceClass.AQI,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda r: pick_reading(r, "air_quality"),
    ),
    PhareSensorDescription(
        key="battery",
        translation_key="battery",
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        suggested_display_precision=0,
        entity_registry_enabled_default=False,
        value_fn=lambda r: pick_reading(r, "battery"),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: PhareConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    known: set[tuple[str, str]] = set()

    @callback
    def add_new() -> None:
        """A sensor exists only for a quantity the device has reported: a C1 Core never gets a
        humidity sensor sitting at unknown forever."""
        new: list[PhareSensor] = []
        for device in coordinator.data.devices.values():
            for description in SENSORS:
                if (device.device_id, description.key) in known:
                    continue
                if description.value_fn(device.readings) is None:
                    continue
                known.add((device.device_id, description.key))
                new.append(PhareSensor(coordinator, device.device_id, description))
        if new:
            async_add_entities(new)

    add_new()
    entry.async_on_unload(coordinator.async_add_listener(add_new))


class PhareSensor(PhareDeviceEntity, SensorEntity):
    entity_description: PhareSensorDescription

    def __init__(
        self, coordinator: PhareCoordinator, device_id: str, description: PhareSensorDescription
    ) -> None:
        super().__init__(coordinator, device_id)
        self.entity_description = description
        self._attr_unique_id = f"{device_id}_{description.key}"

    @property
    def native_value(self) -> float | None:
        device = self.device
        return self.entity_description.value_fn(device.readings) if device else None
