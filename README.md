# Phare Protect for Home Assistant

> **Beta.** This is an early release. Please share any feedback, issues or bugs: open an
> [issue](https://github.com/pharelabs/phare-home-assistant/issues), or email
> [support@pharelabs.com](mailto:support@pharelabs.com).

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

1. In the Phare Protect app (version 1.2.0 or later): Settings → **API tokens** → Create token.
   Pick **Read and control** if you want Home Assistant to arm, test or silence devices;
   **Read only** for sensors alone. The token is shown once — copy it.
2. Paste it into the integration's setup form.

A token keeps working for as long as Home Assistant uses it: one left unused for 90 days stops
working, and it has no other expiry unless you chose one when creating it (a token issued
before October 2026 keeps the fixed one-year expiry it was issued with). If the token is
revoked or expires, Home Assistant shows a *re-authenticate* prompt; create a new token and
paste it there.

## What you get

| Phare | Home Assistant |
| --- | --- |
| Each device | A device in its room's area ("Kitchen"; "Kitchen (Flat 1)" when the account has several homes). Applies when the device is first added; moving it to another area sticks |
| Device card | Hardware ("Phare C1 Pro"), the firmware of all three processors on one line (smoke alarm, controller, cellular modem), the MAC address when known, and the SIM's ICCID as a diagnostic entity on cellular boards |
| Temperature, humidity, CO, CO₂, VOC, air-quality index, battery level | `sensor` (only the quantities the device reports; battery level is disabled by default) |
| Online / offline | `binary_sensor` connectivity; every other entity of the device is *unavailable* while offline |
| Active alarms: smoke, carbon monoxide, movement, temperature, humidity | `binary_sensor` per type, on while the alert is open (temperature and humidity read *Normal* / *Out of range*) |
| Device health, mains power, battery | `binary_sensor`: *OK* / *Fault*, *Connected* / *Lost*, *Normal* / *Low* |
| Each home | A device of its own (no area: its alarms sit in their rooms), holding the home-wide controls below |
| Break-in alarm, per home | `alarm_control_panel` (arm away / disarm). Homes with only C1 Core / C1 devices have none — those devices cannot arm |
| Wireless interconnect, per home | `switch` (in the home's settings): on, one alarm detecting smoke sounds every alarm in the home. Owners only; turning it on needs at least two compatible devices |
| Per device | A *Break-in alarm* switch (C1 Pro and other boards with a radar) to arm or disarm that one device, and a *Wireless interconnect* switch (smoke-alarm firmware 1.2.0 or later) to take that one device in or out of the home's interconnect. The home's flags follow: on only while every compatible device is on |
| Night path light (all boards but the C1 Core; smoke-alarm and controller firmware 1.0.0 or later) | In the device's settings: the light on or off, brightness, how long it stays on, the ambient light threshold, movement sensitivity (0.1 s to 2 s, as in the app), and, from firmware 1.1.0, the trigger mode (dark, schedule, or both) with the schedule's start and end. The schedule runs on the home's local time, which Phare sets for the device. Values show what the device reported; a change shows at once and is confirmed when the device reports it |
| Alert settings | In the device's settings, for the sensors the board has: switches for smoke, carbon monoxide, mains power, battery, humidity, temperature and sound alerts, and the levels behind them (the CO alert ladder, humidity and temperature levels with their clear levels and delays, the movement threshold and hold time, sound levels and repeat interval). These decide what you are told, not whether the alarm sounds. The smoke and CO switches and all the levels start disabled: enable the ones you want to change. A level that would leave an alert unable to clear, or a ladder out of order, is refused |
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

- The integration's texts are in English, Catalan, Spanish and French, following Home Assistant's language.
- A read-only token makes the arm control and buttons fail with "the token may lack the control scope".
- Readings are the values the device last published, timestamped by the device.
- Devices reached through a shared home are not covered by the API's online status for a token; for
  those, a reading older than 15 minutes marks the device unavailable.
- Diagnostics (Settings → Devices & services → Phare → ⋮ → Download diagnostics) redact the token, the
  address and coordinates, and the account's name, email and phone.

## Support

This integration is in beta. Report bugs and ask for features in the
[issue tracker](https://github.com/pharelabs/phare-home-assistant/issues), or email
[support@pharelabs.com](mailto:support@pharelabs.com). For anything about your account, your
devices or a real alarm, contact Phare support rather than opening a public issue, and never
paste your token into an issue. Security reports: see [SECURITY.md](SECURITY.md).

## Development

```
python -m pip install -r requirements_test.txt
python -m pytest -q
```

`tests/test_model.py` runs without Home Assistant installed; the rest use
`pytest-homeassistant-custom-component`. CI runs hassfest, the HACS validator and the tests.
