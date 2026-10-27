"""Constants for the Phare Protect integration."""

from __future__ import annotations

from datetime import timedelta

DOMAIN = "phare"
MANUFACTURER = "Phare Labs"

CONF_TOKEN = "token"
CONF_BASE_URL = "base_url"
# Push: the webhook Home Assistant registered with the API and the secret the API signs with.
CONF_WEBHOOK_ID = "webhook_id"
CONF_WEBHOOK_SECRET = "webhook_secret"
CONF_CLOUDHOOK = "cloudhook"
# A delivery whose timestamp is further from now than this is dropped as a replay.
WEBHOOK_MAX_SKEW = timedelta(seconds=300)
# Deliveries are at-least-once; this many recent delivery ids are remembered to drop repeats.
WEBHOOK_DEDUPE_SIZE = 256
EVENT_ALERT = "phare_alert"
DEFAULT_BASE_URL = "https://api.pharelabs.com/protect"

# Devices publish every 60 s on Wi-Fi and every 300 s on LTE-M, so polling faster buys nothing.
SCAN_INTERVAL = timedelta(seconds=60)
# The device inventory (names, rooms, hardware) changes rarely; re-read it every so many polls.
# Homes are read every poll: their armed flag is state, not inventory.
INVENTORY_REFRESH_EVERY = 10
# Device settings reads in flight at once during a poll.
SETTINGS_READS_AT_ONCE = 4
# A setting written to a device is shown as written until the device reports it, for at most this
# long: an offline device takes it when it is back, and must not show it as applied forever.
PENDING_SETTINGS_FOR = timedelta(minutes=10)
# Alerts older than this are only known if they were already active when the integration
# started; see model.merge_alerts. The API default is 30d and the cap 90d.
ALERT_WINDOW = "30d"

# Hardware revisions as the API reports them in `hw` (stored as "9" or 9).
# Names as the Phare app shows them. 4-7 are the earlier C1 revisions.
HW_MODEL = {
    4: "Phare C1 (v4)",
    5: "Phare C1 (v5)",
    6: "Phare C1 (v6)",
    7: "Phare C1 (v7)",
    8: "Phare C1 Core",
    9: "Phare C1",
    10: "Phare C1 Pro",
}
# The three processors, each with its own firmware, named by what they do as the app and the
# firmware notices name them, not by chip family. Order is the order shown.
FIRMWARE_CHIPS = (
    ("fw_esp", "Smoke alarm"),
    ("fw_efr", "Controller"),
    ("fw_nrf", "Cellular modem"),
)
# The smoke-alarm (ESP) firmware that first acts on the interconnect. Older builds accept the
# setting and silently ignore it, so the per-device switch is only offered from this version, as
# the Phare app does.
INTERCONNECT_MIN_ESP = (1, 2, 0)
# Revisions without movement detection: the API answers 400 to arm on these.
HW_WITHOUT_RADAR = {8, 9}

# The alert types Home Assistant is told about, and what each means there. This is an allow-list:
# a type_code not listed here is dropped everywhere, with no entity, no event and no trace in
# diagnostics, so adding a type to the API exposes nothing until it is added here on purpose.
ALERT_TYPE_CODES = {
    "co": "carbon_monoxide",
    "fire": "smoke",
    "mvmt": "motion",
    "tmp": "heat",
    "hum": "moisture",
    "flt": "problem",
    # Not "power": that class means "power present" when on, and this sensor is on when it is lost.
    "pwr": "problem",
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
