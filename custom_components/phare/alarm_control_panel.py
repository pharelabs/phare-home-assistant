"""The break-in alarm: one panel per home, armed through the devices that can detect movement."""

from __future__ import annotations

from homeassistant.components.alarm_control_panel import (
    AlarmControlPanelEntity,
    AlarmControlPanelEntityFeature,
    AlarmControlPanelState,
    CodeFormat,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import PhareApiError, PhareAuthError, PhareConnectionError, PhareMembershipRequiredError
from .const import HW_WITHOUT_RADAR
from .coordinator import PhareConfigEntry, PhareCoordinator
from .entity import PhareHomeEntity

def armable_device_ids(coordinator: PhareCoordinator, space_id: str) -> list[str]:
    """Devices in the home the API will accept an arm call for (hw 8 and 9 answer 400).

    A device whose hardware revision is unknown is left out: for a token that is every device
    reached through a shared home, and arming it blind would fail the whole home on a 400.
    """
    space = coordinator.data.spaces.get(space_id)
    if not space:
        return []
    return sorted(
        d.device_id
        for d in (coordinator.data.devices.get(i) for i in space.device_ids)
        if d is not None and d.hw is not None and d.hw not in HW_WITHOUT_RADAR
    )


async def async_setup_entry(
    hass: HomeAssistant, entry: PhareConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    known: set[str] = set()

    @callback
    def add_new() -> None:
        # A home whose every device is a C1 Core or C1 has nothing to arm, and a home whose armed
        # flag the API has not reported cannot show a state: no panel rather than a wrong one.
        new = [
            PhareBreakInAlarm(coordinator, space_id)
            for space_id, space in coordinator.data.spaces.items()
            if space_id not in known
            and space.armed is not None
            and armable_device_ids(coordinator, space_id)
        ]
        known.update(e.space_id for e in new)
        if new:
            async_add_entities(new)

    add_new()
    entry.async_on_unload(coordinator.async_add_listener(add_new))


class PhareBreakInAlarm(PhareHomeEntity, AlarmControlPanelEntity):
    _attr_translation_key = "break_in"
    _attr_supported_features = AlarmControlPanelEntityFeature.ARM_AWAY
    _attr_code_format: CodeFormat | None = None
    _attr_code_arm_required = False

    def __init__(self, coordinator: PhareCoordinator, space_id: str) -> None:
        super().__init__(coordinator, space_id)
        self._attr_unique_id = f"{space_id}_break_in"

    @property
    def alarm_state(self) -> AlarmControlPanelState | None:
        space = self.coordinator.data.spaces.get(self.space_id)
        if space is None:
            return None
        return AlarmControlPanelState.ARMED_AWAY if space.armed else AlarmControlPanelState.DISARMED

    async def async_alarm_arm_away(self, code: str | None = None) -> None:
        await self._set_armed(True)

    async def async_alarm_disarm(self, code: str | None = None) -> None:
        await self._set_armed(False)

    async def _set_armed(self, armed: bool) -> None:
        """Arm every capable device in the home; the API reconciles the home's flag from them."""
        device_ids = armable_device_ids(self.coordinator, self.space_id)
        if not device_ids:
            raise HomeAssistantError("No device in this home can detect movement.")
        failures: list[str] = []
        for device_id in device_ids:
            try:
                await self.coordinator.client.arm(device_id, armed)
            except PhareMembershipRequiredError as err:
                raise HomeAssistantError(f"Phare refused the request: {err.message}") from err
            except PhareAuthError as err:
                raise HomeAssistantError(
                    "Phare refused the request: the token may lack the control scope."
                ) from err
            except (PhareApiError, PhareConnectionError) as err:
                failures.append(f"{device_id}: {err}")
        if failures:
            raise HomeAssistantError("Could not change the alarm on: " + "; ".join(failures))
        # The next poll re-reads every home's armed flag, so the panel shows what the API
        # reconciled rather than what was asked; the device list is re-read with it.
        self.coordinator.refresh_inventory = True
        await self.coordinator.async_request_refresh()
