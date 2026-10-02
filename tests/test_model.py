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
