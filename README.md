# Phare Protect for Home Assistant

Brings your [Phare](https://pharelabs.com) fire, CO and air-quality sensors into Home Assistant:
readings, online status, active alarms, the break-in alarm, and the test / identify / silence
commands — so a Phare alert can drive your lights, sirens and locks.

Cloud polling once a minute (homes, readings, online status, alerts; the device list every tenth
poll), plus pushed alerts when Home Assistant can be reached from outside (see below). Devices publish every 60 s on Wi-Fi and
every 300 s on LTE-M, so polling faster would not see more.

## Install

1. In HACS → Integrations → ⋮ → **Custom repositories**, add
   `https://github.com/pharelabs/phare-home-assistant` as an *Integration*.
2. Install **Phare Protect**, restart Home Assistant.
3. Settings → Devices & services → **Add integration** → *Phare Protect*.

## Set up

1. In the Phare Protect app: Settings → **API tokens** → Create token. Pick **Read and control**
   if you want Home Assistant to arm, test or silence devices; **Read only** for sensors alone.
   The token is shown once — copy it.
2. Paste it into the integration's setup form.

If the token is revoked or expires (tokens live at most a year), Home Assistant shows a
*re-authenticate* prompt; create a new token and paste it there.

## What you get

| Phare | Home Assistant |
| --- | --- |
| Each device | A device in an area named after its home |
| Temperature, humidity, CO, CO₂, VOC, air-quality index, battery | `sensor` (only the quantities the device reports) |
| Online / offline | `binary_sensor` connectivity; every other entity of the device is *unavailable* while offline |
| Active alarms: CO, smoke, movement, temperature, humidity, pipe burst, fault, mains, battery | `binary_sensor` per type, on while the alert is open |
| Break-in alarm, per home | `alarm_control_panel` (arm away / disarm). Homes with only C1 Core / C1 devices have none — those devices cannot arm |
| Test, identify, silence | `button` × 3. *Silence* is disabled by default: enable it per device if you want automations to silence alarms |

## Pushed alerts

Readings are polled once a minute. Alerts can also be **pushed**: when Home Assistant is
reachable from the internet, the integration registers a webhook with the API on setup, and
each alert arrives within seconds instead of on the next poll. The poll keeps running as a
safety net, so a missed delivery still shows up within a minute.

What it needs:

- an **https** address for Home Assistant: a Nabu Casa subscription (the integration creates a
  cloudhook automatically) or an external URL configured under Settings → System → Network;
- a token with the **control** scope, because registering the webhook writes to your account.

Without either, the integration says so once in the log and stays on polling. Every delivery is
signed by Phare and verified before it is used; a bad or replayed delivery is dropped and logged.
Each pushed alert also fires a `phare_alert` event on the Home Assistant bus, with the alert's
fields, for automations that want the raw event. Removing the integration tells the API to stop
posting.

## Notes

- A read-only token makes the arm control and buttons fail with "the token may lack the control scope".
- Readings are the values the device last published, timestamped by the device.
- Devices reached through a shared home are not covered by the API's online status for a token; for
  those, a reading older than 15 minutes marks the device unavailable.
- Diagnostics (Settings → Devices & services → Phare → ⋮ → Download diagnostics) redact the token, the
  address and coordinates, and the account's name, email and phone.

## Development

```
python -m pip install -r requirements_test.txt
python -m pytest -q
```

`tests/test_model.py` runs without Home Assistant installed; the rest use
`pytest-homeassistant-custom-component`. CI runs hassfest, the HACS validator and the tests.
