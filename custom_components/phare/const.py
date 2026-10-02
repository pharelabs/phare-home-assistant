"""Constants for the Phare Protect integration."""

from __future__ import annotations

from datetime import timedelta

DOMAIN = "phare"
MANUFACTURER = "Phare Labs"

CONF_TOKEN = "token"
CONF_BASE_URL = "base_url"
DEFAULT_BASE_URL = "https://api.pharelabs.com/protect"

# Devices publish every 60 s on Wi-Fi and every 300 s on LTE-M, so polling faster buys nothing.
SCAN_INTERVAL = timedelta(seconds=60)
# The device inventory (names, rooms, hardware) changes rarely; re-read it every so many polls.
# Homes are read every poll: their armed flag is state, not inventory.
INVENTORY_REFRESH_EVERY = 10
# Alerts older than this are only known if they were already active when the integration
# started; see model.merge_alerts. The API default is 30d and the cap 90d.
ALERT_WINDOW = "30d"

# Hardware revisions as the API reports them in `hw` (stored as "9" or 9).
HW_MODEL = {
    8: "Phare C1 Core",
    9: "Phare C1",
    10: "Phare C1 Pro",
}
# Revisions without movement detection: the API answers 400 to arm on these.
HW_WITHOUT_RADAR = {8, 9}

# Alert `type_code` values the API emits, and what each means for Home Assistant.
ALERT_TYPE_CODES = {
    "co": "carbon_monoxide",
    "fire": "smoke",
    "mvmt": "motion",
    "tmp": "heat",
    "hum": "moisture",
    "bp": "moisture",
    "flt": "problem",
    "pwr": "power",
    "bat": "battery",
}
ALERT_RESOLVED_STAGE = "resolved"

# Reading fields as the API returns them (the device's own names, flattened), with the names the
# published spec uses as fallbacks. First match wins.
READING_KEYS = {
    "temperature": ("tmp_c", "temperature"),
    "humidity": ("hum_c", "humidity"),
    "carbon_monoxide": ("co_avg", "co", "co_ppm"),
    "carbon_dioxide": ("co2", "co2_ppm"),
    "voc": ("voc", "voc_ppm"),
    "air_quality": ("iaq",),
    "battery": ("bat_t_pc",),
}
