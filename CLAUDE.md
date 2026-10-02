# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Home Assistant custom integration (`custom_components/phare`, domain `phare`), distributed through HACS.
It polls the Phare Protect public API with a user's personal access token and exposes devices as sensors,
binary sensors, a break-in `alarm_control_panel` and test / identify / silence buttons. The API itself
lives in `pharelabs-aws-cdk`; this repo only consumes it.

- `api.py` — thin aiohttp client. Every response is `{"data": ..., "meta": {...}}`; statuses map to typed
  errors (`PhareAuthError` triggers re-auth, `PhareRateLimitedError` carries Retry-After).
- `coordinator.py` — one poll per `SCAN_INTERVAL` (60 s) for the whole account; the device inventory is
  re-read every `INVENTORY_REFRESH_EVERY` polls.
- `model.py` — pure functions that turn API payloads into entity state. No Home Assistant import, so its
  tests run without HA installed.
- `const.py` — API field names, hardware revisions (`hw` 8/9/10) and alert `type_code` mappings.

## Build, test

```bash
python3.13 -m venv .venv && .venv/bin/pip install -r requirements_test.txt
.venv/bin/python -m pytest -q
```

`tests/test_model.py` needs only pytest; the rest run under `pytest-homeassistant-custom-component`, with
every API call mocked from `tests/fixtures/` (`conftest.mock_api`). Tests never reach the network.

CI (`.github/workflows/validate.yml`) runs on every pull request: hassfest, the HACS validator and the
tests. The rulesets do not require them, so check the PR is green before merging.

## Fixtures

- `tests/fixtures/*.json` — synthetic payloads, built from the OpenAPI spec and handler field names. They
  hold the alert scenarios.
- `tests/fixtures/recorded/` — real sandbox responses, anonymised. Keep every field name, type and shape as
  the API returned it; replace ids, addresses, coordinates, names, account and SIM ids. **Never commit a
  recorded payload that has not been anonymised**, and never record from production.

## Versioning and release

- `manifest.json` `version` is bumped once, in the release PR (`develop` into `main`), never in a feature PR.
- HACS installs from `main`, so a merge to `main` ships to users.

## Branching model

- `develop` is the integration branch. Feature and fix branches branch from `develop` and PR back into
  `develop`.
- `main` is the production branch. Only release PRs (`develop` into `main`) promote accumulated work, and
  only when explicitly requested.
- Default PR base is **`develop`**. Use `gh pr create --base develop`. Release PRs use
  `gh pr create --base main --head develop`.
- Both branches are protected by the `branch-protection` ruleset: no deletion, no force-push, changes only
  through a pull request. `main` also has `main-merge-commit-only`: release PRs merge with a merge commit,
  never squash, so `develop` and `main` keep a shared history.

## Git commits

Use **`feat:`**, **`fix:`**, **`chore:`** or **`test:`** only (lowercase, no parentheses). Do **not** use
scopes like `feat(sensor):`.

Examples: `feat: add pipe-burst binary sensor`, `fix: re-auth on API Gateway 403`, `chore: bump hacs action`.

## Device & testing safety

**NEVER interact with real deployed devices.** There are 145+ Phare devices in production serving real
customers. The arm control and the test / identify / silence buttons send real commands; pressing one
against a customer's device can sound an alarm in someone's home or silence a real one.

**Designated test device and space — use these for ALL manual testing:**

| Resource | Value |
|---|---|
| Test device ID | `119` |
| Test space ID | `sp_da4e85e8-37df-494a-ae55-d55fa5f9f8d5` |

**Rules:**
- Manual testing against a live API uses the sandbox (point the integration's *API base URL* at it) with a
  token for the test account, which sees only the test space. Never configure the integration with a token
  that can see customer devices.
- Prefer a **read-only** token; use a control token only when testing arm or the buttons, and only on
  device `119`.
- **Never** iterate over all devices or send bulk commands.
- **Never** trigger notifications to real users during testing.
- Automated tests use the fixtures only. Hardcode identifiers in tests; never discover devices dynamically.
- Refer to devices by their canonical device ID, never by IMEI.
