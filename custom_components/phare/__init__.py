"""Phare Protect: fire, CO and air-quality sensors from the Phare cloud. Polled once a minute,
with alerts pushed the moment they happen when Home Assistant can be reached from outside."""

from __future__ import annotations

import logging

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import PhareApiClient
from .const import CONF_BASE_URL, CONF_TOKEN, DEFAULT_BASE_URL, DOMAIN
from . import webhook as phare_webhook
from .coordinator import PhareConfigEntry, PhareCoordinator

_LOGGER = logging.getLogger(__name__)

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
    try:
        await phare_webhook.async_register(hass, entry)
    except Exception:  # noqa: BLE001 - push is optional; polling must not depend on it
        _LOGGER.exception("Phare webhook registration failed; alerts arrive by polling")
    return True


async def async_unload_entry(hass: HomeAssistant, entry: PhareConfigEntry) -> bool:
    phare_webhook.async_unregister(hass, entry)
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: PhareConfigEntry) -> None:
    """The account is being removed from Home Assistant: stop the API posting to it."""
    client = PhareApiClient(
        async_get_clientsession(hass),
        entry.data[CONF_TOKEN],
        entry.data.get(CONF_BASE_URL, DEFAULT_BASE_URL),
    )
    await phare_webhook.async_deregister_remote(hass, entry, client)


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: PhareConfigEntry, device_entry: dr.DeviceEntry
) -> bool:
    """A device or home no longer in the account may be removed from the UI; a live one may not."""
    data = entry.runtime_data.data
    ids = {identifier for domain, identifier in device_entry.identifiers if domain == DOMAIN}
    return not (ids & set(data.devices)) and not (ids & set(data.spaces))

