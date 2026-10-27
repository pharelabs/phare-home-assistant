"""Shared fixtures: the custom integration enabled, and the API mocked from recorded payloads."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parent / "fixtures"
BASE = "https://api.pharelabs.com/protect"
TOKEN = "phare_pat_testtokentesttokentesttokentesttoken00"
# Every fixture reading is a few minutes before this instant.
FROZEN_NOW = "2026-10-02T10:00:00+00:00"


@pytest.fixture(autouse=True)
def _enable_custom_integrations(enable_custom_integrations):
    yield


def payload(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def mock_api(aioclient_mock, *, spaces=None, spaces_data=None, statuses=None, alerts=None, devices=None):
    """Point every read the integration makes at the fixtures (or the overrides given)."""
    aioclient_mock.get(f"{BASE}/v1/user/settings", json=payload("user_settings.json"))
    aioclient_mock.get(f"{BASE}/v1/spaces", json=spaces or payload("spaces.json"))
    aioclient_mock.get(f"{BASE}/v1/devices", json=devices or payload("devices.json"))
    aioclient_mock.get(f"{BASE}/v1/spaces/data", json=spaces_data or payload("spaces_data.json"))
    aioclient_mock.get(f"{BASE}/v1/devices/status", json=statuses or payload("devices_status.json"))
    aioclient_mock.get(f"{BASE}/v1/alerts", json=alerts or payload("alerts.json"))
    # Every device's settings, empty unless a test registered its own first (the first
    # matching mock answers).
    for device_id in sorted(_device_ids(devices or payload("devices.json"),
                                        spaces_data or payload("spaces_data.json"),
                                        statuses or payload("devices_status.json"))):
        aioclient_mock.get(
            f"{BASE}/v1/devices/{device_id}/settings",
            json={"data": {"device_id": device_id, "reported": {}, "desired": {}}},
        )


def _device_ids(*payloads) -> set[str]:
    found: set[str] = set()

    def walk(node):
        if isinstance(node, dict):
            if isinstance(node.get("device_id"), (str, int)):
                found.add(str(node["device_id"]))
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    for p in payloads:
        walk(p)
    return found
