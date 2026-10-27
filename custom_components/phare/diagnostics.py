"""The support dump: what the coordinator holds, minus secrets, personal data and firmware internals.

A diagnostics file is meant to be attached to a public GitHub issue, so it carries what helps
support read the integration's behaviour and nothing else: no token, address, names, hardware
identifiers, or the text of an alert (which can name the person who was notified), and none of
the firmware's internal working values.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .const import CONF_TOKEN, CONF_WEBHOOK_ID, CONF_WEBHOOK_SECRET
from .coordinator import PhareConfigEntry

TO_REDACT = {
    CONF_TOKEN, CONF_WEBHOOK_SECRET, CONF_WEBHOOK_ID, "cloudhook_url",
    "address", "address_lat", "address_lng", "email", "name", "phone",
    # Hardware identifiers.
    "iccid", "mac",
    # An alert's text names people: "<name> has been notified".
    "event",
}

# Firmware working values: detector internals, calibration and accuracy flags, raw battery and
# supply measurements. They help nobody reading an integration issue, and describe how the
# detectors work.
HIDDEN_READINGS = frozenset({
    "bat_i", "bat_t_pc", "bat_tdie", "co", "cusum", "dcv", "gas_pc_ac", "iaq_ac",
    "mains_v", "p_fire", "pd_b", "pd_ir", "t_t", "voc_ac",
})
# Shown under the name the reader expects: the averaged CO is the CO value the sensor reports.
RELABELLED_READINGS = {"co_avg": "co"}


def _readings(readings: dict[str, Any]) -> dict[str, Any]:
    hidden = set(HIDDEN_READINGS)
    if "co_avg" not in readings:
        # Without the average the sensor shows the raw value, so the dump does too.
        hidden.discard("co")
    shown = {k: v for k, v in readings.items() if k not in hidden}
    return {RELABELLED_READINGS.get(k, k): v for k, v in sorted(shown.items())}


def _device(device) -> dict[str, Any]:
    return asdict(device) | {"readings": _readings(device.readings)}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: PhareConfigEntry
) -> dict[str, Any]:
    coordinator = entry.runtime_data
    data = coordinator.data
    return async_redact_data(
        {
            "entry": dict(entry.data),
            "spaces": {k: asdict(v) | {"device_ids": sorted(v.device_ids)} for k, v in data.spaces.items()},
            "devices": {k: _device(v) for k, v in data.devices.items()},
            "last_update_success": coordinator.last_update_success,
        },
        TO_REDACT,
    )
