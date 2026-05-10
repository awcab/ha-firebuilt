# Fireboard for Home Assistant

A custom integration that pulls live data from the [Fireboard Cloud API](https://docs.fireboard.io/app/app-api/) and a matching Lovelace card for your dashboard.

- Discovers every Fireboard device on your account
- One temperature `sensor` per probe channel, in the device's native units
- Diagnostic **Battery** and **Last seen** sensors per device
- For FireBoard Drive units: **Setpoint**, **Drive output**, and **Drive battery** sensors
- **Cook started** / **Cook session** sensors that track the active session via `/sessions.json`
- Per-channel **Alert** binary sensors that fire when temps fall outside the configured window
- A `fireboard.end_session` service to stop a cook from a button or automation
- An experimental, opt-in **Setpoint target** number entity for adjusting the Drive setpoint
- Custom card (`type: custom:fireboard-card`) with color-graded probes, alert badges, fan animation, and live cook timer
- One devices request + one sessions request per minute = 10 calls per 5 minutes, comfortably under Fireboard's 17/5min ceiling

## Installation

### HACS (recommended)

This repo isn't in the default HACS registry yet, so it needs to be added once as a custom repository. After that, HACS handles updates like any other integration.

1. **Make sure HACS is installed.** If not, follow the [HACS install guide](https://www.hacs.xyz/docs/use/download/download/) first.
2. Open **HACS** from the Home Assistant sidebar.
3. Click the **⋮ menu (top-right)** → **Custom repositories**.
4. Paste the repo URL and pick the right type:
   - **Repository:** `https://github.com/awcab/ha-firebuilt`
   - **Type:** `Integration`
5. Click **Add**, close the dialog. The repo will now appear in the HACS list.
6. Search HACS for **Fireboard** → click it → click **Download** (top right) → confirm.
7. **Restart Home Assistant** when prompted.
8. Go to **Settings → Devices & Services → Add Integration** → search **Fireboard**.
9. Sign in with your fireboard.io credentials. Your password is exchanged for an API token and is not stored.

> The bundled Lovelace card auto-registers as a frontend resource — no extra `resources:` step needed.

To update later: HACS → Fireboard → **Update** when a new release shows up.

### Manual

If you don't use HACS:

1. Download or clone this repo.
2. Copy the `custom_components/fireboard` directory into your Home Assistant `config/custom_components/` directory. The end result should be `config/custom_components/fireboard/manifest.json` etc.
3. Restart Home Assistant.
4. **Settings → Devices & Services → Add Integration** → search **Fireboard**.
5. Sign in with your fireboard.io credentials.

## The card

The card auto-registers as a Lovelace resource the first time the integration loads — no manual resource step required.

```yaml
# Auto-discover everything
type: custom:fireboard-card

# One specific device
type: custom:fireboard-card
device: 0123456789abcdef0123456789abcdef
title: Smoker

# Hand-pick entities (channels only)
type: custom:fireboard-card
title: Brisket cook
entities:
  - sensor.smokey_pit
  - sensor.smokey_food

# Show 6 hours of trend instead of the default 2
type: custom:fireboard-card
hours: 6

# Hide the trend chart entirely
type: custom:fireboard-card
hours: 0

# Start in compact mode (just titles + probe tiles, no chart, no drive panel)
type: custom:fireboard-card
compact: true

# Keep idle devices visible even when no probes are reading
type: custom:fireboard-card
show_offline: true
```

By default the card hides any device whose probe channels are all unavailable — so if you've got a Pulse sitting idle on the shelf, it won't clutter the dashboard. When every device is idle, the card shows a single "No Fireboard devices are reporting probe data right now" message with a hint about `show_offline`.

A small toggle button at the top-right of the card flips between the full and compact views. Compact mode hides the trend chart, the drive setpoint/output panel, and shrinks the probe tiles. The choice is persisted per-card in `localStorage`, so reloading the dashboard keeps your preferred layout.

The trend chart pulls history through HA's recorder via the `history/history_during_period` WebSocket call. It refreshes once a minute; between fetches the live current value is appended to the in-memory series. If your recorder is disabled or excludes Fireboard sensors, the chart falls back to "Collecting trend data…".

Click any tile to open the standard Home Assistant **More info** dialog.

## Entities

| Entity                                   | Type            | Notes |
| ---------------------------------------- | --------------- | ----- |
| `sensor.<device>_<channel>`              | temperature     | One per enabled probe channel. Goes unavailable when no probe is reading. |
| `sensor.<device>_battery`                | battery %       | Reads `last_battery_reading` (top-level), falls back to `device_log.vBattPer`. |
| `sensor.<device>_last_seen`              | timestamp       | `last_templog`. Diagnostic. |
| `sensor.<device>_setpoint`               | temperature     | Drive devices only. |
| `sensor.<device>_drive_output`           | %               | Drive devices only. Card animates when > 0%. |
| `sensor.<device>_drive_battery`          | volts           | Drive controller battery. Diagnostic. |
| `sensor.<device>_cook_started`           | timestamp       | Active session start. Unavailable when no cook is running. |
| `sensor.<device>_cook_session`           | text            | Active session title. |
| `binary_sensor.<device>_<channel>_alert` | problem         | One per channel that has alerts configured. `on` when temp is outside any enabled `[temp_min, temp_max]`. |
| `number.<device>_setpoint_target`        | temperature     | **Experimental, disabled by default.** Enable in the entity registry. |

## Services

### `fireboard.end_session`

Ends the active cook session. Pass `device_id` (preferred) or `session_id`.

```yaml
service: fireboard.end_session
data:
  device_id: 0123456789abcdef0123456789abcdef
```

## Dashboard quickstart

A full example dashboard is in [`examples/dashboard.yaml`](examples/dashboard.yaml). It includes the custom card, gauge + entities + history-graph cards as alternatives, and an end-cook button.

## Notes

- **Rate limit**: integration polls every 60 s (devices + sessions = 2 calls/cycle = 10/5min). Stays under the documented 17/5min ceiling.
- **Battery**: two paths because Pulse-class hardware reports `device_log: null` but does populate `last_battery_reading`.
- **Setpoint write**: the Fireboard write API isn't publicly documented. The number entity's request body is `{"setpoint": <value>}` — a best-effort guess from the read-side `last_drivelog.setpoint` field name. If that turns out to be wrong, please open an issue with the response body from `home-assistant.log` and I'll adjust.
- **Re-auth**: on token rejection (rare — Fireboard tokens are long-lived) HA will prompt for the password.

## Troubleshooting

### "Custom element doesn't exist: fireboard-card"

The integration auto-serves the card at `/fireboard_static/fireboard-card.js?v=<version>`. If the dashboard says the element doesn't exist:

1. **Hard-refresh the browser** (`Ctrl+Shift+R` / `Cmd+Shift+R`). Browsers aggressively cache failed JS loads.
2. Check Home Assistant's logs (`Settings → System → Logs`) for a line like
   `INFO ... Fireboard Lovelace card registered at /fireboard_static/...`
   If you don't see that line, the static path registration failed — see below.
3. In the browser DevTools **Network** tab, look for `fireboard-card.js`. It should return **200**. If it's 404, the static path didn't register; if it's 200 but the element still doesn't exist, check the **Console** tab for JS errors.
4. As a last resort, register it manually under **Settings → Dashboards → Resources**:
   - URL: `/fireboard_static/fireboard-card.js`
   - Type: `JavaScript Module`

### Fresh-install checklist

- HACS users: after the **Download** step, you **must restart Home Assistant** before the integration appears under Add Integration.
- After adding the integration, the static path is only registered on the *next* dashboard load — so refresh the dashboard once after setup.
- If you upgrade the integration, hard-refresh the browser; the version-suffixed URL (`?v=0.1.0`) busts the cache automatically on integration version bumps.

## License

MIT
