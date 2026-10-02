"""One poll for the whole account."""

from __future__ import annotations

from datetime import timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    PhareApiClient,
    PhareApiError,
    PhareAuthError,
    PhareConnectionError,
    PhareRateLimitedError,
)
from .const import ALERT_WINDOW, DOMAIN, INVENTORY_REFRESH_EVERY, SCAN_INTERVAL
from .model import (
    PhareData,
    PhareDevice,
    PhareSpace,
    build_data,
    coerce_hw,
    merge_alerts,
    pick_reading,
)

__all__ = [
    "PhareConfigEntry",
    "PhareCoordinator",
    "PhareData",
    "PhareDevice",
    "PhareSpace",
    "coerce_hw",
    "pick_reading",
]

_LOGGER = logging.getLogger(__name__)

type PhareConfigEntry = ConfigEntry[PhareCoordinator]


class PhareCoordinator(DataUpdateCoordinator[PhareData]):
    """Four reads a minute for the whole account: homes (for the armed flag), readings,
    connectivity, alerts. The device inventory (names, rooms, hardware) is read on the first poll
    and then every INVENTORY_REFRESH_EVERY polls, or straight after a write that changes it.
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: PhareApiClient) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=SCAN_INTERVAL,
            config_entry=entry,
        )
        self.client = client
        self._polls = 0
        self._devices: list[dict[str, Any]] = []
        self._active_alerts: dict[tuple[str, str], dict[str, Any]] = {}
        self.refresh_inventory = True

    async def _async_update_data(self) -> PhareData:
        try:
            if self.refresh_inventory or self._polls % INVENTORY_REFRESH_EVERY == 0:
                self._devices = await self.client.devices()
                self.refresh_inventory = False
            spaces = await self.client.spaces()
            spaces_data = await self.client.spaces_data()
            statuses = await self.client.devices_status()
            alerts = await self.client.alerts(window=ALERT_WINDOW)
        except PhareAuthError as err:
            raise ConfigEntryAuthFailed(err.message) from err
        except PhareRateLimitedError as err:
            # Back off for the window the API names, else one extra interval.
            wait = err.retry_after or SCAN_INTERVAL.total_seconds()
            self.update_interval = timedelta(seconds=max(wait, SCAN_INTERVAL.total_seconds()) * 2)
            raise UpdateFailed(f"Rate limited; retrying in {self.update_interval}") from err
        except (PhareApiError, PhareConnectionError) as err:
            raise UpdateFailed(str(err)) from err
        self.update_interval = SCAN_INTERVAL
        self._polls += 1
        self._active_alerts = merge_alerts(self._active_alerts, alerts)
        return build_data(spaces, self._devices, spaces_data, statuses, self._active_alerts)
