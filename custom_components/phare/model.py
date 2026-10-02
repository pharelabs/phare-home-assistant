"""The account as the entities read it, and the pure fold from API payloads into it.

Nothing here imports Home Assistant, so the fold is tested against recorded API responses with
plain pytest, and the coordinator stays a thin scheduler around it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from .const import ALERT_RESOLVED_STAGE, ALERT_TYPE_CODES, READING_KEYS

# A device publishes every 60 s on Wi-Fi and every 300 s on LTE-M. A reading older than this is
# treated as the device being away, for the devices whose connectivity the API does not report.
STALE_AFTER = timedelta(minutes=15)


def coerce_hw(value: Any) -> int | None:
    """`hw` arrives as "9" or 9; anything unreadable is None (treated as unknown, not capable)."""
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def pick_reading(readings: dict[str, Any], kind: str) -> float | None:
    """The value for a sensor kind, under whichever name the API used."""
    for key in READING_KEYS[kind]:
        value = readings.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
    return None


def parse_ts(value: Any) -> datetime | None:
    """An ISO 8601 timestamp as the API writes it (Z or offset), or None."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


@dataclass
class PhareSpace:
    space_id: str
    address: str | None = None
    name: str | None = None
    # None until /v1/spaces has reported it: a home known only from readings has no armed flag,
    # and a panel that cannot read the flag must not exist.
    armed: bool | None = None
    device_ids: set[str] = field(default_factory=set)

    @property
    def display_name(self) -> str:
        return self.name or self.address or self.space_id


@dataclass
class PhareDevice:
    device_id: str
    space_id: str | None = None
    name: str | None = None
    room: str | None = None
    hw: int | None = None
    firmware: str | None = None
    armed: bool = False
    # From /v1/devices/status, which a token only gets for directly assigned devices; None for
    # the rest, whose reachability is judged from the age of their last reading instead.
    online: bool | None = None
    readings: dict[str, Any] = field(default_factory=dict)
    reading_ts: str | None = None
    # type_code -> the newest unresolved alert record of that type
    active_alerts: dict[str, dict[str, Any]] = field(default_factory=dict)

    @property
    def display_name(self) -> str:
        if self.room and self.name:
            if self.room.lower() in self.name.lower():
                return self.name
            return f"{self.room} {self.name}"
        return self.name or self.room or f"Phare {self.device_id[-6:]}"

    def is_reachable(self, now: datetime) -> bool:
        """Online when the API says so; otherwise, a reading fresher than STALE_AFTER."""
        if self.online is not None:
            return self.online
        last = parse_ts(self.reading_ts)
        return last is not None and now - last <= STALE_AFTER


@dataclass
class PhareData:
    spaces: dict[str, PhareSpace] = field(default_factory=dict)
    devices: dict[str, PhareDevice] = field(default_factory=dict)


def merge_alerts(
    previous: dict[tuple[str, str], dict[str, Any]], alerts: list[dict[str, Any]]
) -> dict[tuple[str, str], dict[str, Any]]:
    """Active alerts after a poll: (device_id, type_code) -> newest unresolved record.

    The API answers a time window, newest first and deduplicated per alert. An alert that is
    still open but older than the window would vanish from the answer without ever being
    resolved, so what was active before stays active until a `resolved` record arrives for that
    device and type, or a newer open record replaces it.
    """
    active = dict(previous)
    seen: set[tuple[str, str]] = set()
    for alert in alerts or []:
        device_id = alert.get("device_id")
        code = alert.get("type_code")
        if not device_id or code not in ALERT_TYPE_CODES:
            continue
        key = (str(device_id), code)
        if key in seen:
            continue
        seen.add(key)
        if alert.get("stage") == ALERT_RESOLVED_STAGE:
            active.pop(key, None)
        else:
            active[key] = alert
    return active


def build_data(
    spaces: list[dict[str, Any]],
    devices: list[dict[str, Any]],
    spaces_data: dict[str, Any],
    statuses: list[dict[str, Any]],
    active_alerts: dict[tuple[str, str], dict[str, Any]],
) -> PhareData:
    """The payloads folded into one model."""
    data = PhareData()

    for raw in spaces:
        space_id = raw.get("space_id")
        if not space_id:
            continue
        data.spaces[space_id] = PhareSpace(
            space_id=space_id,
            address=raw.get("address"),
            name=raw.get("name"),
            armed=bool(raw.get("armed", False)),
        )

    for raw in devices:
        device_id = raw.get("device_id")
        if not device_id:
            continue
        data.devices[str(device_id)] = PhareDevice(
            device_id=str(device_id),
            space_id=raw.get("space_id"),
            name=raw.get("name"),
            room=raw.get("room"),
            hw=coerce_hw(raw.get("hw")),
            firmware=raw.get("fw_esp"),
            armed=bool(raw.get("armed", False)),
        )

    # Readings carry the authoritative space and room for a device, and are the only inventory a
    # token sees for devices reached through space membership rather than direct assignment.
    for space_id, space_raw in (spaces_data or {}).items():
        if not isinstance(space_raw, dict):
            continue
        space = data.spaces.setdefault(space_id, PhareSpace(space_id=space_id))
        space.address = space.address or space_raw.get("address")
        space.name = space.name or space_raw.get("name")
        for room, points in (space_raw.get("rooms") or {}).items():
            for point in points or []:
                device_id = point.get("device_id")
                if not device_id:
                    continue
                device = data.devices.setdefault(
                    str(device_id), PhareDevice(device_id=str(device_id))
                )
                device.space_id = device.space_id or space_id
                device.room = device.room or (room if room != "unknown" else None)
                device.readings = {k: v for k, v in point.items() if k not in ("device_id", "ts")}
                device.reading_ts = point.get("ts")
                space.device_ids.add(str(device_id))

    for device in data.devices.values():
        if device.space_id and device.space_id in data.spaces:
            data.spaces[device.space_id].device_ids.add(device.device_id)

    for status in statuses or []:
        device_id = status.get("device_id")
        if not device_id:
            continue
        device = data.devices.setdefault(str(device_id), PhareDevice(device_id=str(device_id)))
        device.online = bool(status.get("is_online", False))
        device.name = device.name or status.get("name")
        device.room = device.room or status.get("room")
        device.space_id = device.space_id or status.get("space_id")

    for (device_id, code), alert in active_alerts.items():
        device = data.devices.setdefault(device_id, PhareDevice(device_id=device_id))
        device.active_alerts[code] = alert

    return data
