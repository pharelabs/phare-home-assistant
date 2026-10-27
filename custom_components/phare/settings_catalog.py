"""The alarm settings a customer may change, as Home Assistant shows them.

Plain Python, no Home Assistant import. Each entry is one key of the device's settings shadow,
exactly the keys the API lets a customer read and write. The `*_ALERT` flags and the thresholds
decide when the device raises an alert to the cloud, and so what the customer is told: they do not
change whether the alarm itself sounds.

An entity exists for a key only when the device's hardware has the sensor behind it and its
settings shadow reports the key: a key the firmware does not know is never offered.
"""

from __future__ import annotations

from dataclasses import dataclass

# What each board has, as the Phare app's capability table puts it. A revision not listed is
# treated as having everything, as the app does.
_FEATURES_BY_HW = {
    8: frozenset({"smoke", "co", "power"}),
    9: frozenset({"smoke", "co", "power", "temperature", "humidity"}),
    10: frozenset({"smoke", "co", "power", "temperature", "humidity", "movement", "sound"}),
    7: frozenset({"smoke", "co", "power", "temperature", "humidity", "movement", "sound"}),
}
_ALL_FEATURES = frozenset({"smoke", "co", "power", "temperature", "humidity", "movement", "sound"})


def features(hw: int | None) -> frozenset[str]:
    return _FEATURES_BY_HW.get(hw, _ALL_FEATURES) if hw is not None else _ALL_FEATURES


@dataclass(frozen=True)
class Setting:
    key: str
    translation_key: str
    feature: str
    kind: str  # "switch" or "number"
    unit: str | None = None
    minimum: float = 0
    maximum: float = 1
    step: float = 1
    device_class: str | None = None
    # Switching a fire or CO alert off means nobody is told about a real one, so those switches
    # are opt-in, like the silence button. The tuning numbers are many and advanced: hidden until
    # enabled, so the device page stays readable.
    enabled_default: bool = True


def _switch(key, translation_key, feature, *, enabled_default=True):
    return Setting(key, translation_key, feature, "switch", enabled_default=enabled_default)


def _number(key, translation_key, feature, unit, minimum, maximum, device_class=None):
    return Setting(key, translation_key, feature, "number", unit, minimum, maximum, 1, device_class,
                   enabled_default=False)


SETTINGS: tuple[Setting, ...] = (
    # Fire
    _switch("FIRE_ALERT", "fire_alerts", "smoke", enabled_default=False),
    # Power
    _switch("PWR_ALERT", "power_alerts", "power"),
    _switch("BAT_ALERT", "battery_alerts", "power"),
    # Carbon monoxide: four rungs, each raised above its level and cleared below its clear level
    _switch("CO_ALERT", "co_alerts", "co", enabled_default=False),
    _number("ALR_TGS_L", "co_low", "co", "ppm", 0, 500, "carbon_monoxide"),
    _number("ALR_TGS_L_L", "co_low_clear", "co", "ppm", 0, 500, "carbon_monoxide"),
    _number("ALR_TGS_M", "co_moderate", "co", "ppm", 0, 500, "carbon_monoxide"),
    _number("ALR_TGS_M_L", "co_moderate_clear", "co", "ppm", 0, 500, "carbon_monoxide"),
    _number("ALR_TGS_H", "co_high", "co", "ppm", 0, 500, "carbon_monoxide"),
    _number("ALR_TGS_H_L", "co_high_clear", "co", "ppm", 0, 500, "carbon_monoxide"),
    _number("ALR_TGS_C", "co_critical", "co", "ppm", 0, 500, "carbon_monoxide"),
    _number("ALR_TGS_C_L", "co_critical_clear", "co", "ppm", 0, 500, "carbon_monoxide"),
    # Movement: the alert is raised after this many detections, and held until there has been no
    # movement for the hold time. Arming itself is the break-in alarm switch.
    _number("ALR_MOV_THR", "movement_threshold", "movement", "detections", 0, 100),
    _number("ALR_MOV_SAND_DUR", "movement_hold", "movement", "min", 1, 10080, "duration"),
    # Humidity: raised once above the level for the delay, cleared below the clear level
    _switch("HUM_ALERT", "humidity_alerts", "humidity"),
    _number("ALR_HUM_THR", "humidity_level", "humidity", "%", 0, 100, "humidity"),
    _number("ALR_HUM_L", "humidity_clear", "humidity", "%", 0, 100, "humidity"),
    _number("ALR_HUM_DUR", "humidity_delay", "humidity", "s", 0, 20864, "duration"),
    # Temperature: a high and a very high level, each with its clear level, and a delay
    _switch("TEMP_ALERT", "temperature_alerts", "temperature"),
    _number("ALR_TEMP_THR", "temperature_high", "temperature", "°C", -40, 85, "temperature"),
    _number("ALR_TEMP_L", "temperature_high_clear", "temperature", "°C", -40, 85, "temperature"),
    _number("ALR_VTEMP_THR", "temperature_very_high", "temperature", "°C", -40, 85, "temperature"),
    _number("ALR_VTEMP_L", "temperature_very_high_clear", "temperature", "°C", -40, 85, "temperature"),
    _number("ALR_TEMP_DUR", "temperature_delay", "temperature", "s", 0, 20864, "duration"),
    # Sound: a loud and a very loud level, and how long before the same alert is raised again
    _switch("SOUND_ALERT", "sound_alerts", "sound"),
    _number("ALR_SOUND_M", "sound_loud", "sound", "dB", 0, 150, "sound_pressure"),
    _number("ALR_SOUND_H", "sound_very_loud", "sound", "dB", 0, 150, "sound_pressure"),
    _number("ALR_SOUND_OST", "sound_repeat", "sound", "min", 1, 10080, "duration"),
)

# (raised above, cleared below): the clear level may not be above its level, or the alert could
# never clear.
_CLEARS = (
    ("ALR_TGS_L", "ALR_TGS_L_L"), ("ALR_TGS_M", "ALR_TGS_M_L"),
    ("ALR_TGS_H", "ALR_TGS_H_L"), ("ALR_TGS_C", "ALR_TGS_C_L"),
    ("ALR_HUM_THR", "ALR_HUM_L"), ("ALR_TEMP_THR", "ALR_TEMP_L"), ("ALR_VTEMP_THR", "ALR_VTEMP_L"),
)
# Ladders, lowest first: each rung strictly above the one before.
_LADDERS = (
    ("ALR_TGS_L", "ALR_TGS_M", "ALR_TGS_H", "ALR_TGS_C"),
    ("ALR_TEMP_THR", "ALR_VTEMP_THR"),
    ("ALR_SOUND_M", "ALR_SOUND_H"),
)

LABELS = {
    "ALR_TGS_L": "the CO low level", "ALR_TGS_M": "the CO moderate level",
    "ALR_TGS_H": "the CO high level", "ALR_TGS_C": "the CO critical level",
    "ALR_TGS_L_L": "the CO low clear level", "ALR_TGS_M_L": "the CO moderate clear level",
    "ALR_TGS_H_L": "the CO high clear level", "ALR_TGS_C_L": "the CO critical clear level",
    "ALR_HUM_THR": "the humidity level", "ALR_HUM_L": "the humidity clear level",
    "ALR_TEMP_THR": "the high temperature level", "ALR_TEMP_L": "the high temperature clear level",
    "ALR_VTEMP_THR": "the very high temperature level",
    "ALR_VTEMP_L": "the very high temperature clear level",
    "ALR_SOUND_M": "the loud sound level", "ALR_SOUND_H": "the very loud sound level",
}


def check(key: str, value: int, current: dict[str, int]) -> str | None:
    """Why writing `value` to `key` would leave the device's levels inconsistent, or None."""
    after = {**current, key: value}
    for level, clear in _CLEARS:
        if key in (level, clear) and level in after and clear in after and after[clear] > after[level]:
            return f"{LABELS[clear].capitalize()} ({after[clear]}) must not be above {LABELS[level]} ({after[level]})."
    for ladder in _LADDERS:
        if key not in ladder:
            continue
        present = [k for k in ladder if k in after]
        for lower, upper in zip(present, present[1:]):
            if after[lower] >= after[upper]:
                return f"{LABELS[lower].capitalize()} ({after[lower]}) must be below {LABELS[upper]} ({after[upper]})."
    return None
