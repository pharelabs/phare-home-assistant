"""The night path light: its firmware settings, which devices have it, and how the app presents it.

Plain Python, no Home Assistant import, so it is tested without Home Assistant. The values,
ranges, defaults and gates mirror the Phare app and the API's settings validator; the firmware is
the source of truth for all of them.

The settings are each device's own, under the API's raw setting keys. What is shown is
what the device reported, not what was asked: a write is shown at once, then confirmed when the
device reports it. The schedule runs on the device's own clock offset, which the API sets from the
home's time zone with every schedule write; this integration never sends or shows it.
"""

from __future__ import annotations

from datetime import time

from .model import PhareDevice, parse_version

EN = "NPATH_EN"
BRIGHTNESS = "NPATH_BRIGHT"
DURATION = "NPATH_DUR_S"
LIGHT_THRESHOLD = "NPATH_LIGHT_PCT"
CONFIRM_MS = "NPATH_ON_MS"
MODE = "NPATH_MODE"
START = "NPATH_START"
STOP = "NPATH_STOP"

# The firmware's own defaults, for a device that has not reported a key yet.
DEFAULTS = {
    EN: 0, BRIGHTNESS: 60, DURATION: 15, LIGHT_THRESHOLD: 20, CONFIRM_MS: 300,
    MODE: 0, START: 1260, STOP: 360,
}

# Every revision has the light except the C1 Core.
HW_WITHOUT_NIGHT_PATH = {8}
# Smoke-alarm (ESP) and controller (EFR) firmware: the light from 1.0.0, the schedule from 1.1.0.
# An older unit accepts the schedule keys but never reports them.
LIGHT_MIN_FIRMWARE = (1, 0, 0)
SCHEDULE_MIN_FIRMWARE = (1, 1, 0)

# Trigger modes: what lets motion turn the light on. Display order as in the app.
MODE_OPTIONS = {"dark": 0, "schedule": 1, "dark_and_schedule": 3, "dark_or_schedule": 2}

# Movement sensitivity is the time motion must persist before the light turns on: shorter is MORE
# sensitive. The app's slider runs from 0.1 s (most sensitive) to 2 s in 0.05 s steps and names
# four bands; the value itself is what is set, the band only describes it.
SENSITIVITY_MIN_S = 0.1
SENSITIVITY_MAX_S = 2.0
SENSITIVITY_STEP_S = 0.05
_SENSITIVITY_BANDS = ((300, "very_high"), (500, "high"), (1000, "medium"))

# Ambient light threshold in the firmware's unit, hundredths of a percent. 0 is never offered:
# the gate is "light below the threshold", which 0 can never satisfy.
LIGHT_THRESHOLD_MIN = 1
LIGHT_THRESHOLD_MAX = 10000



def _firmware_at_least(device: PhareDevice, minimum: tuple[int, int, int]) -> bool:
    """Both chips that run the light at `minimum` or newer. An unknown version is too old."""
    for key in ("fw_esp", "fw_efr"):
        version = parse_version(device.firmware.get(key))
        if version is None or version < minimum:
            return False
    return True


def has_light(device: PhareDevice) -> bool:
    if device.hw in HW_WITHOUT_NIGHT_PATH:
        return False
    return _firmware_at_least(device, LIGHT_MIN_FIRMWARE)


def has_schedule(device: PhareDevice) -> bool:
    return has_light(device) and _firmware_at_least(device, SCHEDULE_MIN_FIRMWARE)


def sensitivity_option(confirm_ms: int) -> str:
    for below, option in _SENSITIVITY_BANDS:
        if confirm_ms < below:
            return option
    return "low"


def uses_schedule(mode: int) -> bool:
    return mode != MODE_OPTIONS["dark"]


def minutes_to_time(minutes: int) -> time:
    minutes %= 1440
    return time(minutes // 60, minutes % 60)


def time_to_minutes(value: time) -> int:
    return value.hour * 60 + value.minute
