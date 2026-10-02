"""The device registry entry every Phare entity hangs off."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, HW_MODEL, MANUFACTURER
from .coordinator import PhareCoordinator, PhareDevice


class PhareDeviceEntity(CoordinatorEntity[PhareCoordinator]):
    """One Home Assistant device per Phare device, named after its room, placed in its home.

    Unique ids derive from the canonical `device_id` only: a renamed room or device keeps its
    history and its automations.
    """

    _attr_has_entity_name = True

    def __init__(self, coordinator: PhareCoordinator, device_id: str) -> None:
        super().__init__(coordinator)
        self._device_id = device_id

    @property
    def device(self) -> PhareDevice | None:
        return self.coordinator.data.devices.get(self._device_id)

    @property
    def device_info(self) -> DeviceInfo:
        device = self.device or PhareDevice(device_id=self._device_id)
        space = self.coordinator.data.spaces.get(device.space_id or "")
        return DeviceInfo(
            identifiers={(DOMAIN, self._device_id)},
            manufacturer=MANUFACTURER,
            model=HW_MODEL.get(device.hw or -1, f"Phare (hw {device.hw})" if device.hw else "Phare"),
            name=device.display_name,
            sw_version=device.firmware,
            serial_number=self._device_id,
            suggested_area=space.display_name if space else None,
        )

    @property
    def available(self) -> bool:
        # An offline device keeps its last reading on screen only as "unavailable", never as a
        # live value: for fire and CO that distinction is the whole point.
        device = self.device
        return super().available and device is not None and device.online is not False
