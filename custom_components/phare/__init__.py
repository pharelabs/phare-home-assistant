"""Phare Protect: fire, CO and air-quality sensors from the Phare cloud, polled once a minute."""

from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import PhareApiClient
from .const import CONF_BASE_URL, CONF_TOKEN, DEFAULT_BASE_URL, DOMAIN
from .coordinator import PhareConfigEntry, PhareCoordinator

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.ALARM_CONTROL_PANEL,
    Platform.BUTTON,
]


async def async_setup_entry(hass: HomeAssistant, entry: PhareConfigEntry) -> bool:
    client = PhareApiClient(
        async_get_clientsession(hass),
        entry.data[CONF_TOKEN],
        entry.data.get(CONF_BASE_URL, DEFAULT_BASE_URL),
    )
    coordinator = PhareCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: PhareConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: PhareConfigEntry, device_entry: dr.DeviceEntry
) -> bool:
    """A device or home no longer in the account may be removed from the UI; a live one may not."""
    data = entry.runtime_data.data
    ids = {identifier for domain, identifier in device_entry.identifiers if domain == DOMAIN}
    return not (ids & set(data.devices)) and not (ids & set(data.spaces))

