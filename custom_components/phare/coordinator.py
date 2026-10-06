"""One poll for the whole account."""

from __future__ import annotations

from datetime import datetime, timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
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
    parse_ts,
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
        self._last_poll: tuple[list, dict, list] | None = None
        # (device_id, type_code) -> timestamp of the newest pushed record. A poll fetched before a
        # push, or a delivery retried out of order, carries older records that must not undo it.
        self._pushed_ts: dict[tuple[str, str], datetime] = {}
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
        alerts = [alert for alert in alerts if not self._older_than_pushed(alert)]
        self._active_alerts = merge_alerts(self._active_alerts, alerts)
        self._last_poll = (spaces, spaces_data, statuses)
        return build_data(spaces, self._devices, spaces_data, statuses, self._active_alerts)

    @callback
    def apply_pushed_alert(self, alert: dict[str, Any]) -> bool:
        """A delivery from the alert webhook: fold it into the active alerts now rather than on
        the next poll. The poll stays the reconciliation path, so a missed delivery self-heals.
        Returns False when the record is not one this integration tracks."""
        if self._older_than_pushed(alert):
            return str(alert.get("device_id")) in self.data.devices if self.data else False
        ts = parse_ts(alert.get("ts"))
        if ts is not None and alert.get("device_id") and alert.get("type_code"):
            self._pushed_ts[(str(alert["device_id"]), alert["type_code"])] = ts
        before = dict(self._active_alerts)
        self._active_alerts = merge_alerts(self._active_alerts, [alert])
        if self._active_alerts == before:
            return str(alert.get("device_id")) in self.data.devices if self.data else False
        if self._last_poll is None:
            return True
        spaces, spaces_data, statuses = self._last_poll
        self.async_set_updated_data(
            build_data(spaces, self._devices, spaces_data, statuses, self._active_alerts)
        )
        return True

    def _older_than_pushed(self, alert: dict[str, Any]) -> bool:
        pushed = self._pushed_ts.get((str(alert.get("device_id")), alert.get("type_code")))
        ts = parse_ts(alert.get("ts"))
        return pushed is not None and ts is not None and ts < pushed
