"""The device registry entry every Phare entity hangs off."""

from __future__ import annotations

from typing import NoReturn

from homeassistant.const import EntityCategory
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC, DeviceInfo
from homeassistant.util import dt as dt_util
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, FIRMWARE_CHIPS, HW_MODEL, MANUFACTURER
from .coordinator import PhareCoordinator, PhareDevice, PhareSpace
from .api import (
    PhareApiError,
    PhareAuthError,
    PhareConnectionError,
    PhareForbiddenError,
    PhareMembershipRequiredError,
)
from .model import firmware_summary, hardware_name


def suggested_area(device: PhareDevice, space: PhareSpace | None, home_count: int) -> str | None:
    """The Home Assistant area a device is created in: its room.

    Home Assistant areas are rooms. Placing every device in one area named after its home put
    every device's alarm sensors on the same card, each named only "Smoke alarm". A room name is
    shared across homes ("Living Room" in two flats), so an account with more than one home
    gets the home in brackets. Home Assistant reads this only when it first creates the device;
    a user who has moved a device keeps their choice.
    """
    if not device.room:
        return space.display_name if space else None
    if home_count > 1 and space is not None:
        return f"{device.room} ({space.display_name})"
    return device.room


class PhareHomeEntity(CoordinatorEntity[PhareCoordinator]):
    """An entity of a whole home: the break-in alarm, the wireless interconnect.

    Every home the account can reach is a device, so its home-wide controls have somewhere to
    live. It gets no area: a home is not a room, and its alarms already sit in their rooms.
    """

    _attr_has_entity_name = True

    def __init__(self, coordinator: PhareCoordinator, space_id: str) -> None:
        super().__init__(coordinator)
        self.space_id = space_id

    @property
    def space(self) -> PhareSpace | None:
        return self.coordinator.data.spaces.get(self.space_id)

    @property
    def device_info(self) -> DeviceInfo:
        space = self.space
        return DeviceInfo(
            identifiers={(DOMAIN, self.space_id)},
            manufacturer=MANUFACTURER,
            model="Home",
            name=space.display_name if space else self.space_id,
        )


class PhareDeviceEntity(CoordinatorEntity[PhareCoordinator]):
    """One Home Assistant device per Phare device, named after its room, placed in that room.

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
        hardware = hardware_name(device.hw, HW_MODEL)
        return DeviceInfo(
            identifiers={(DOMAIN, self._device_id)},
            # The MAC shows on the device card, and lets a router integration that sees the same
            # address recognise the same device.
            connections={(CONNECTION_NETWORK_MAC, device.mac)} if device.mac else set(),
            manufacturer=MANUFACTURER,
            model=hardware,
            hw_version=hardware,
            name=device.display_name,
            # Home Assistant has one firmware field and the device three processors: all on one line.
            sw_version=firmware_summary(device.firmware, FIRMWARE_CHIPS),
            serial_number=self._device_id,
            suggested_area=suggested_area(device, space, len(self.coordinator.data.spaces)),
        )

    @property
    def available(self) -> bool:
        # An offline device keeps its last reading on screen only as "unavailable", never as a
        # live value: for fire and CO that distinction is the whole point. For devices the status
        # endpoint does not cover, a reading older than STALE_AFTER counts as offline.
        device = self.device
        return super().available and device is not None and device.is_reachable(dt_util.utcnow())


def refused(err: Exception, what: str) -> NoReturn:
    """Why Phare refused a write, in words a user can act on."""
    if isinstance(err, PhareMembershipRequiredError):
        raise HomeAssistantError(f"Phare refused the request: {err.message}") from err
    if isinstance(err, PhareAuthError):
        raise HomeAssistantError("Phare refused the request: the token may lack the control scope.") from err
    if isinstance(err, PhareForbiddenError):
        raise HomeAssistantError(f"Your account cannot change the {what} here: {err.message}") from err
    if isinstance(err, PhareApiError):
        # 400: not possible on this home or device. 502: a device could not take the setting and
        # the home's flag was left as it was. The API's own words say which.
        raise HomeAssistantError(f"Could not change the {what}: {err.message}") from err
    raise HomeAssistantError(f"Could not reach Phare: {err}") from err


class PhareSettingEntity(PhareDeviceEntity):
    """A setting of one device (night path light, alarm settings), in its Configuration section.
    Unavailable until the device's settings were read."""

    _attr_entity_category = EntityCategory.CONFIG
    # What a refusal names: "Could not change the <what>".
    _what = "setting"

    def __init__(self, coordinator: PhareCoordinator, device_id: str, key: str) -> None:
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_{key}"

    @property
    def available(self) -> bool:
        return super().available and self._device_id in self.coordinator.settings

    def setting(self, key: str) -> int | None:
        return self.coordinator.setting(self._device_id, key)

    async def write(self, values: dict[str, int]) -> None:
        try:
            await self.coordinator.async_write_settings(self._device_id, values)
        except (PhareApiError, PhareConnectionError) as err:
            refused(err, self._what)
