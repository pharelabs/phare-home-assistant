"""The fold from API payloads into the model, against recorded responses. Plain pytest, no HA."""

import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "custom_components" / "phare"
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _load(name: str):
    """Load a module from the component by path, so the package's HA imports never run."""
    pkg = types.ModuleType("phare_under_test")
    pkg.__path__ = [str(PKG)]
    sys.modules.setdefault("phare_under_test", pkg)
    spec = importlib.util.spec_from_file_location(f"phare_under_test.{name}", PKG / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[f"phare_under_test.{name}"] = mod
    spec.loader.exec_module(mod)
    return mod


const = _load("const")
model = _load("model")


def fixture(name: str):
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture
def active_alerts():
    return model.merge_alerts({}, fixture("alerts.json")["data"])


@pytest.fixture
def data(active_alerts):
    return model.build_data(
        fixture("spaces.json")["data"],
        fixture("devices.json")["data"],
        fixture("spaces_data.json")["data"],
        fixture("devices_status.json")["data"],
        active_alerts,
    )


def test_every_device_in_the_readings_is_known_even_without_a_devices_row(data):
    # A token sees only directly assigned devices in /v1/devices; the readings carry the rest.
    assert set(data.devices) >= {"351103920000001", "351103920000002", "351103920000003"}
    orphan = data.devices["351103920000003"]
    assert orphan.space_id == "sp_home" and orphan.room == "Hallway"


def test_readings_are_read_under_the_devices_own_names():
    readings = {"tmp_c": 21.5, "hum_c": 45, "co_avg": 0.4, "co": 0.9, "co2": 410, "voc": 0.12, "iaq": 37, "bat_t_pc": 88}
    assert model.pick_reading(readings, "temperature") == 21.5
    assert model.pick_reading(readings, "humidity") == 45.0
    assert model.pick_reading(readings, "carbon_monoxide") == 0.4, "the averaged value wins when present"
    assert model.pick_reading(readings, "carbon_dioxide") == 410.0
    assert model.pick_reading(readings, "voc") == 0.12
    assert model.pick_reading(readings, "air_quality") == 37.0
    assert model.pick_reading(readings, "battery") == 88.0


def test_readings_also_accept_the_published_spec_names():
    readings = {"temperature": 19.0, "humidity": 50, "co_ppm": 0.5, "co2_ppm": 600, "voc_ppm": 0.2}
    assert model.pick_reading(readings, "temperature") == 19.0
    assert model.pick_reading(readings, "carbon_monoxide") == 0.5
    assert model.pick_reading(readings, "carbon_dioxide") == 600.0


def test_a_missing_or_non_numeric_reading_is_none():
    assert model.pick_reading({}, "temperature") is None
    assert model.pick_reading({"tmp_c": "21.5"}, "temperature") is None
    assert model.pick_reading({"tmp_c": True}, "temperature") is None


def test_hw_is_read_as_an_integer_however_it_arrives():
    assert model.coerce_hw("9") == 9 and model.coerce_hw(9) == 9 and model.coerce_hw(10) == 10
    assert model.coerce_hw(None) is None and model.coerce_hw("1.0") is None


def test_online_comes_from_status_and_is_unknown_without_it(data):
    assert data.devices["351103920000001"].online is True
    assert data.devices["351103920000002"].online is False
    assert data.devices["351103920000003"].online is None


def test_only_the_newest_unresolved_alert_per_type_is_active(data):
    kitchen = data.devices["351103920000001"]
    assert set(kitchen.active_alerts) == {"co"}, "the older resolved fire alert does not count"
    assert kitchen.active_alerts["co"]["stage"] == "new"
    assert data.devices["351103920000002"].active_alerts == {}, "resolved newest record wins over an older open one"


def test_alert_types_the_integration_does_not_model_are_ignored(data):
    for device in data.devices.values():
        assert set(device.active_alerts) <= set(const.ALERT_TYPE_CODES)


def test_spaces_carry_their_devices_and_armed_state(data):
    home = data.spaces["sp_home"]
    assert home.address == "10 Example Street, London, EC1A 1BB"
    assert home.armed is False
    assert home.device_ids == {"351103920000001", "351103920000002", "351103920000003"}


def test_device_names_read_naturally():
    assert model.PhareDevice("1", room="Kitchen", name="Sensor").display_name == "Kitchen Sensor"
    assert model.PhareDevice("1", room="Kitchen", name="Kitchen sensor").display_name == "Kitchen sensor"
    assert model.PhareDevice("351103920000001").display_name == "Phare 000001"


def test_empty_payloads_build_an_empty_model():
    empty = model.build_data([], [], {}, [], {})
    assert empty.spaces == {} and empty.devices == {}


def test_a_home_known_only_from_readings_has_no_armed_flag():
    spaces_data = {"sp_other": {"space_id": "sp_other", "rooms": {"Hall": [{"device_id": "9", "tmp_c": 20}]}}}
    data = model.build_data([], [], spaces_data, [], {})
    assert data.spaces["sp_other"].armed is None, "no panel can be built for it"
    assert data.devices["9"].hw is None and data.devices["9"].online is None


# ── alerts survive the window ───────────────────────────────────────────────

def test_an_open_alert_older_than_the_window_stays_active_until_resolved():
    old = {"device_id": "1", "type_code": "flt", "stage": "in progress", "ts": "2026-08-01T00:00:00Z"}
    active = model.merge_alerts({}, [old])
    assert ("1", "flt") in active
    # Next poll: the window no longer contains it. It must not silently clear.
    active = model.merge_alerts(active, [])
    assert ("1", "flt") in active
    # A resolved record for that device and type clears it.
    resolved = {"device_id": "1", "type_code": "flt", "stage": "resolved", "ts": "2026-10-02T00:00:00Z"}
    active = model.merge_alerts(active, [resolved])
    assert ("1", "flt") not in active


def test_a_newer_open_record_replaces_the_previous_one():
    first = {"device_id": "1", "type_code": "co", "stage": "new", "ts": "2026-10-02T09:00:00Z"}
    second = {"device_id": "1", "type_code": "co", "stage": "in progress", "ts": "2026-10-02T09:30:00Z"}
    active = model.merge_alerts(model.merge_alerts({}, [first]), [second, first])
    assert active[("1", "co")]["stage"] == "in progress"


# ── reachability without a status row ──────────────────────────────────────

def test_a_device_with_a_status_row_is_as_online_as_the_status_says():
    from datetime import datetime, timezone
    now = datetime(2026, 10, 2, 10, 0, tzinfo=timezone.utc)
    assert model.PhareDevice("1", online=True, reading_ts="2020-01-01T00:00:00Z").is_reachable(now) is True
    assert model.PhareDevice("1", online=False, reading_ts="2026-10-02T09:59:00Z").is_reachable(now) is False


def test_a_device_without_a_status_row_is_reachable_while_its_reading_is_fresh():
    from datetime import datetime, timezone
    now = datetime(2026, 10, 2, 10, 0, tzinfo=timezone.utc)
    assert model.PhareDevice("1", reading_ts="2026-10-02T09:50:00Z").is_reachable(now) is True
    assert model.PhareDevice("1", reading_ts="2026-10-02T09:40:00Z").is_reachable(now) is False
    assert model.PhareDevice("1", reading_ts=None).is_reachable(now) is False
    assert model.PhareDevice("1", reading_ts="garbage").is_reachable(now) is False


def test_timestamps_parse_in_the_forms_the_api_writes():
    assert model.parse_ts("2026-10-02T09:50:00Z") is not None
    assert model.parse_ts("2026-10-02T09:50:00.123Z") is not None
    assert model.parse_ts("2026-10-02T09:50:00+00:00") is not None
    assert model.parse_ts("") is None and model.parse_ts(None) is None and model.parse_ts(12) is None


# ── the recorded sandbox payloads (anonymised; see fixtures/recorded/README.md) ────────

def recorded(name: str):
    return json.loads((FIXTURES / "recorded" / name).read_text())


@pytest.fixture
def recorded_data():
    return model.build_data(
        recorded("spaces.json")["data"],
        recorded("devices.json")["data"],
        recorded("spaces_data.json")["data"],
        recorded("devices_status.json")["data"],
        model.merge_alerts({}, recorded("alerts.json")["data"]),
    )


def test_recorded_readings_use_the_devices_field_names(recorded_data):
    with_readings = [d for d in recorded_data.devices.values() if d.readings]
    assert len(with_readings) == 2
    for device in with_readings:
        assert {"tmp_c", "hum_c", "co", "co2", "voc", "iaq", "bat_t_pc", "mains_on"} <= set(device.readings)
        assert "temperature" not in device.readings, "the published spec's names are not what the API sends"
        for kind in ("temperature", "humidity", "carbon_monoxide", "carbon_dioxide", "voc", "air_quality", "battery"):
            assert model.pick_reading(device.readings, kind) is not None, kind


def test_recorded_token_sees_one_device_directly_and_one_through_its_home(recorded_data):
    """/v1/devices and /v1/devices/status cover only the directly assigned device; the second
    device exists only in the readings, so it has a room but no hardware revision or status."""
    direct = [d for d in recorded_data.devices.values() if d.hw is not None]
    via_home = [d for d in recorded_data.devices.values() if d.hw is None]
    assert len(direct) == 1 and len(via_home) == 1
    assert direct[0].hw == 7 and direct[0].online is False and direct[0].firmware["fw_esp"].startswith("v")
    assert via_home[0].online is None and via_home[0].room and via_home[0].space_id
    assert via_home[0].space_id == direct[0].space_id, "both sit in the same home"


def test_recorded_homes_carry_their_armed_flag_and_optional_name(recorded_data):
    assert len(recorded_data.spaces) == 4
    assert all(space.armed is not None for space in recorded_data.spaces.values()), "/v1/spaces listed every home"
    assert sum(1 for s in recorded_data.spaces.values() if s.name) == 2, "name is optional on a home"
    home = next(s for s in recorded_data.spaces.values() if s.device_ids)
    assert len(home.device_ids) == 2


def test_recorded_timestamps_parse(recorded_data):
    for device in recorded_data.devices.values():
        if device.reading_ts:
            assert model.parse_ts(device.reading_ts) is not None, device.reading_ts


# ── device card: hardware, firmware ──────────────────────────────────────────

@pytest.mark.parametrize(
    ("raw", "expected"),
    [("10", 10), (9, 9), ("PH01_07", 7), ("ph01_04", 4), ("", None), (None, None), ("x", None)],
)
def test_hw_reads_every_spelling_the_api_uses(raw, expected):
    assert model.coerce_hw(raw) == expected


@pytest.mark.parametrize(
    ("hw", "name"),
    [(4, "Phare C1 (v4)"), (7, "Phare C1 (v7)"), (8, "Phare C1 Core"), (9, "Phare C1"),
     (10, "Phare C1 Pro"), (11, "Phare (hw 11)"), (None, "Phare")],
)
def test_hardware_is_named_as_the_app_names_it(hw, name):
    assert model.hardware_name(hw, const.HW_MODEL) == name


def test_every_chip_reported_shows_in_order_and_missing_ones_are_left_out():
    chips = const.FIRMWARE_CHIPS
    full = {"fw_nrf": "v1.0.0", "fw_esp": "v2.1.0", "fw_efr": "v1.4.0"}
    assert model.firmware_summary(full, chips) == "Smoke alarm v2.1.0 · Controller v1.4.0 · Cellular modem v1.0.0"
    assert model.firmware_summary({"fw_esp": "v2.1.0"}, chips) == "Smoke alarm v2.1.0"
    assert model.firmware_summary({}, chips) is None


def test_the_device_list_keeps_every_firmware_the_mac_and_the_iccid():
    data = model.build_data(
        [], [{"device_id": "D100", "hw": "PH01_07", "fw_esp": "v0.6.2", "fw_efr": "v0.6.1", "fw_nrf": "v0.6.2",
              "mac": "a1:b2:c3:d4:e5:f6", "iccid": "8900000000000000000"}], {}, [], {},
    )
    device = data.devices["D100"]
    assert device.hw == 7
    assert device.firmware == {"fw_esp": "v0.6.2", "fw_efr": "v0.6.1", "fw_nrf": "v0.6.2"}
    assert (device.mac, device.iccid) == ("a1:b2:c3:d4:e5:f6", "8900000000000000000")


@pytest.mark.parametrize(
    ("raw", "parsed"),
    [("v1.2.0", (1, 2, 0)), ("1.2", (1, 2, 0)), ("v0.6.1-7-gf7e18ba", (0, 6, 1)), ("V2", (2, 0, 0)),
     ("", None), (None, None), ("dev", None)],
)
def test_firmware_versions_are_read_as_the_firmware_writes_them(raw, parsed):
    assert model.parse_version(raw) == parsed


# ── night path rules ─────────────────────────────────────────────────────────

night_path = _load("night_path")


def _device(hw=10, esp="v1.1.0", efr="v1.1.0"):
    firmware = {k: v for k, v in (("fw_esp", esp), ("fw_efr", efr)) if v}
    return model.PhareDevice(device_id="D100", hw=hw, firmware=firmware)


@pytest.mark.parametrize(
    ("device", "light", "schedule"),
    [
        (_device(), True, True),
        (_device(hw=9), True, True),
        (_device(hw=8), False, False),  # the C1 Core has no light
        (_device(esp="v1.0.4", efr="v1.0.4"), True, False),  # schedule needs 1.1.0 on both chips
        (_device(esp="v1.1.0", efr="v1.0.9"), True, False),
        (_device(esp="v0.9.0"), False, False),
        (_device(efr=None), False, False),  # an unknown version is too old: fails closed
        (_device(hw=None), True, True),  # an unknown board is not assumed to be a Core
    ],
)
def test_which_devices_get_the_light_and_its_schedule(device, light, schedule):
    assert night_path.has_light(device) is light
    assert night_path.has_schedule(device) is schedule


@pytest.mark.parametrize(
    ("confirm_ms", "option"),
    [(0, "very_high"), (299, "very_high"), (300, "high"), (499, "high"), (500, "medium"), (999, "medium"),
     (1000, "low"), (5000, "low")],
)
def test_sensitivity_reads_as_the_apps_bands(confirm_ms, option):
    assert night_path.sensitivity_option(confirm_ms) == option


def test_minutes_after_midnight_round_trip():
    assert night_path.minutes_to_time(1260).isoformat() == "21:00:00"
    assert night_path.time_to_minutes(night_path.minutes_to_time(365)) == 365


# ── alarm settings ───────────────────────────────────────────────────────────

settings_catalog = _load("settings_catalog")


@pytest.mark.parametrize(
    ("hw", "has", "lacks"),
    [(8, {"smoke", "co", "power"}, {"humidity", "temperature", "movement", "sound"}),
     (9, {"humidity", "temperature"}, {"movement", "sound"}),
     (10, {"movement", "sound", "humidity"}, set()),
     (None, {"movement", "sound"}, set())],
)
def test_each_board_offers_the_settings_of_the_sensors_it_has(hw, has, lacks):
    features = settings_catalog.features(hw)
    assert has <= features and not (lacks & features)


DEFAULT_LEVELS = {"ALR_TGS_L": 10, "ALR_TGS_L_L": 5, "ALR_TGS_M": 25, "ALR_TGS_M_L": 20, "ALR_TGS_H": 50,
                  "ALR_TGS_H_L": 45, "ALR_TGS_C": 100, "ALR_TGS_C_L": 90, "ALR_HUM_THR": 80, "ALR_HUM_L": 75,
                  "ALR_TEMP_THR": 30, "ALR_TEMP_L": 28, "ALR_VTEMP_THR": 35, "ALR_VTEMP_L": 33,
                  "ALR_SOUND_M": 85, "ALR_SOUND_H": 100}


@pytest.mark.parametrize(
    ("key", "value", "problem"),
    [("ALR_TGS_M", 30, None),
     ("ALR_TGS_M_L", 26, "must not be above the CO moderate level"),
     ("ALR_TGS_M", 19, "must not be above the CO moderate level"),
     ("ALR_TGS_M", 50, "must be below the CO high level"),
     ("ALR_TGS_L", 25, "must be below the CO moderate level"),
     ("ALR_HUM_L", 80, None),  # equal is allowed: it still clears
     ("ALR_HUM_L", 81, "must not be above the humidity level"),
     ("ALR_TEMP_THR", 36, "must be below the very high temperature level"),
     ("ALR_VTEMP_THR", 30, "must not be above the very high temperature level"),
     ("ALR_SOUND_H", 85, "must be below the very loud sound level"),
     ("ALR_HUM_DUR", 10, None)],
)
def test_levels_that_would_leave_an_alert_inconsistent_are_refused(key, value, problem):
    result = settings_catalog.check(key, value, DEFAULT_LEVELS)
    if problem is None:
        assert result is None
    else:
        assert result and problem in result


def test_every_setting_key_is_unique_and_every_label_is_a_known_key():
    keys = [s.key for s in settings_catalog.SETTINGS]
    assert len(keys) == len(set(keys))
    assert set(settings_catalog.LABELS) <= set(keys)
