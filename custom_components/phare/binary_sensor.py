"""Online/offline per device, and one "alarm active" sensor per alert type the device can raise."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import ALERT_TYPE_CODES, HW_WITHOUT_RADAR
from .coordinator import PhareConfigEntry, PhareCoordinator
from .entity import PhareDeviceEntity

# Alert types every revision raises. Movement needs the radar (hw 10+); the rest arrive per
# device as the API reports them, so a type a device never raises is simply never created.
BASE_ALERT_CODES = ("co", "fire", "tmp", "hum", "flt", "pwr", "bat")

ALERT_DESCRIPTIONS = {
    code: BinarySensorEntityDescription(
        key=f"alert_{code}",
        translation_key=f"alert_{code}",
        device_class=BinarySensorDeviceClass(ALERT_TYPE_CODES[code]),
    )
    for code in ALERT_TYPE_CODES
}

CONNECTIVITY = BinarySensorEntityDescription(
    key="connectivity",
    translation_key="connectivity",
    device_class=BinarySensorDeviceClass.CONNECTIVITY,
    entity_category=EntityCategory.DIAGNOSTIC,
)


async def async_setup_entry(
    hass: HomeAssistant, entry: PhareConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    known: set[tuple[str, str]] = set()

    @callback
    def add_new() -> None:
        new: list[BinarySensorEntity] = []
        for device in coordinator.data.devices.values():
            if (device.device_id, "connectivity") not in known:
                known.add((device.device_id, "connectivity"))
                new.append(PhareConnectivitySensor(coordinator, device.device_id))
            codes = set(BASE_ALERT_CODES) | set(device.active_alerts)
            if device.hw is not None and device.hw not in HW_WITHOUT_RADAR:
                codes.add("mvmt")
            for code in sorted(codes):
                if (device.device_id, code) in known:
                    continue
                known.add((device.device_id, code))
                new.append(PhareAlertSensor(coordinator, device.device_id, code))
        if new:
            async_add_entities(new)

    add_new()
    entry.async_on_unload(coordinator.async_add_listener(add_new))


class PhareConnectivitySensor(PhareDeviceEntity, BinarySensorEntity):
    entity_description = CONNECTIVITY

    def __init__(self, coordinator: PhareCoordinator, device_id: str) -> None:
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_connectivity"

    @property
    def available(self) -> bool:
        # The one entity that must stay available while the device is offline: it is how the
        # user learns the device is offline.
        return self.coordinator.last_update_success and self.device is not None

    @property
    def is_on(self) -> bool | None:
        device = self.device
        return device.is_reachable(dt_util.utcnow()) if device else None


class PhareAlertSensor(PhareDeviceEntity, BinarySensorEntity):
    def __init__(self, coordinator: PhareCoordinator, device_id: str, code: str) -> None:
        super().__init__(coordinator, device_id)
        self._code = code
        self.entity_description = ALERT_DESCRIPTIONS[code]
        self._attr_unique_id = f"{device_id}_alert_{code}"

    @property
    def is_on(self) -> bool | None:
        device = self.device
        return self._code in device.active_alerts if device else None

    @property
    def extra_state_attributes(self) -> dict[str, str] | None:
        device = self.device
        alert = device.active_alerts.get(self._code) if device else None
        if not alert:
            return None
        return {
            "alert_id": alert.get("alert_id"),
            "stage": alert.get("stage"),
            "since": alert.get("ts"),
            "event": alert.get("event"),
        }
