"""The support dump: everything the coordinator holds, minus the token and the address."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .const import CONF_TOKEN
from .coordinator import PhareConfigEntry

TO_REDACT = {CONF_TOKEN, "address", "address_lat", "address_lng", "email", "name", "phone"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: PhareConfigEntry
) -> dict[str, Any]:
    coordinator = entry.runtime_data
    data = coordinator.data
    return async_redact_data(
        {
            "entry": dict(entry.data),
            "spaces": {k: asdict(v) | {"device_ids": sorted(v.device_ids)} for k, v in data.spaces.items()},
            "devices": {k: asdict(v) for k, v in data.devices.items()},
            "last_update_success": coordinator.last_update_success,
        },
        TO_REDACT,
    )
