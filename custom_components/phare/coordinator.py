"""One poll for the whole account."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    PhareApiClient,
    PhareApiError,
    PhareAuthError,
    PhareConnectionError,
    PhareRateLimitedError,
)
from . import night_path
from .const import (
    ALERT_WINDOW,
    DOMAIN,
    INVENTORY_REFRESH_EVERY,
    PENDING_SETTINGS_FOR,
    SCAN_INTERVAL,
    SETTINGS_READS_AT_ONCE,
)
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
        # Device settings (night path light, alarm settings): device_id -> what the device reported. A device is absent until its
        # settings have been read once; its settings entities are unavailable until then.
        self.settings: dict[str, dict[str, int]] = {}
        # device_id -> key -> (value written, until when it is shown before the device reports it)
        self._pending: dict[str, dict[str, tuple[int, datetime]]] = {}
        # Devices whose settings could not be read: tried again with the next inventory read, not
        # on every poll.
        self._settings_failed: set[str] = set()

    async def _async_update_data(self) -> PhareData:
        inventory_read = False
        try:
            if self.refresh_inventory or self._polls % INVENTORY_REFRESH_EVERY == 0:
                self._devices = await self.client.devices()
                self.refresh_inventory = False
                inventory_read = True
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
        data = build_data(spaces, self._devices, spaces_data, statuses, self._active_alerts)
        await self._refresh_settings(data, all_devices=inventory_read)
        return data

    # ── device settings ──────────────────────────────────────────────────

    async def _refresh_settings(self, data: PhareData, *, all_devices: bool) -> None:
        """Settings change rarely, so every device's are read with the inventory; between those,
        only a device with a write still waiting for its report is read again.

        A failed read keeps what was known: the settings entities are a convenience, and must not
        fail the poll that carries the alarms.
        """
        now = dt_util.utcnow()
        for device_id, pending in list(self._pending.items()):
            for key, (_, until) in list(pending.items()):
                if until <= now:
                    del pending[key]
            if not pending:
                del self._pending[device_id]
        wanted = [
            device.device_id for device in data.devices.values()
            if all_devices
            or device.device_id in self._pending
            or (device.device_id not in self.settings and device.device_id not in self._settings_failed)
        ]
        # A few at a time: the poll that carries the alarms should not wait on one read per device
        # in a row, nor send them all at once.
        limit = asyncio.Semaphore(SETTINGS_READS_AT_ONCE)

        async def read(device_id: str) -> tuple[str, dict[str, Any] | None]:
            async with limit:
                try:
                    return device_id, await self.client.device_settings(device_id)
                except (PhareApiError, PhareConnectionError) as err:
                    _LOGGER.debug("Could not read settings of %s: %s", device_id, err)
                    return device_id, None

        for device_id, response in await asyncio.gather(*(read(d) for d in wanted)):
            if response is None:
                self._settings_failed.add(device_id)
                continue
            self._settings_failed.discard(device_id)
            reported = {k: v for k, v in (response.get("reported") or {}).items() if isinstance(v, int)}
            self.settings[device_id] = reported
            pending = self._pending.get(device_id, {})
            for key in [k for k, (value, _) in pending.items() if reported.get(k) == value]:
                del pending[key]
            if device_id in self._pending and not pending:
                del self._pending[device_id]

    def setting(self, device_id: str, key: str) -> int | None:
        """A setting as shown: a write the device has not reported yet, else what it reported, else
        the firmware default for a night path key. None until the device's settings were read
        once, or for an alarm setting the device does not report."""
        reported = self.settings.get(device_id)
        if reported is None:
            return None
        pending = self._pending.get(device_id, {}).get(key)
        if pending is not None and pending[1] > dt_util.utcnow():
            return pending[0]
        return reported.get(key, night_path.DEFAULTS.get(key))

    async def async_write_settings(self, device_id: str, values: dict[str, int]) -> None:
        """Write settings to a device and show them at once. The device confirms on a later poll;
        until then, and at most PENDING_SETTINGS_FOR, the written value is what is shown."""
        await self.client.update_device_settings(device_id, values)
        until = dt_util.utcnow() + PENDING_SETTINGS_FOR
        self._pending.setdefault(device_id, {}).update({k: (v, until) for k, v in values.items()})
        self.async_update_listeners()

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
