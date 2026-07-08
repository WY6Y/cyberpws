# CyberPWS — WY6Y Weather Dashboard

Cyberpunk personal weather dashboard running on the Pi 5. Live data flows from the **WeatherThief** ESP32+CC1101 SDR via MQTT. No Weather Underground API required.

Served at `https://wx.wy6y.net/` (primary) or `https://wy6ypi5.taile0fc4a.ts.net/` (Tailscale alias).

## Architecture

```
AcuRite 5-in-1 (433 MHz)
    → CC1101 + ESP32 (WeatherThief / OpenMQTTGateway rtl_433)
        → MQTT (mosquitto, 127.0.0.1:1883)
            → app.py (Flask, port 5000)
                → Caddy (wx.wy6y.net)
                    → Browser / CyberHUD
```

The app subscribes to `wy6y/weather/#`. It publishes current conditions to `wy6y/weather/hud/current` and `wy6y/weather/hud/state` for the CyberHUD display to consume.

## Quick start

```bash
cd ~/cyberpws-fresh
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in MQTT_PASS

python3 app.py
```

## Service management

```bash
sudo systemctl restart cyberpws.service
sudo systemctl status cyberpws.service
sudo journalctl -u cyberpws.service -f
```

## Environment variables (`.env`)

| Variable | Default | Description |
|----------|---------|-------------|
| `PORT` | `5000` | HTTP listen port |
| `MQTT_HOST` | `127.0.0.1` | Mosquitto broker |
| `MQTT_USER` / `MQTT_PASS` | `wy6y` / — | MQTT credentials |
| `MQTT_TOPIC_PREFIX` | `wy6y/weather` | Topic namespace root |
| `GATEWAY_NAME` | `WeatherThief` | MQTT client ID / gateway name |
| `ALLOWED_SENSOR_IDS` | `563` | Comma-separated rtl_433 sensor IDs to accept. **Leave blank to accept all** (risks neighbor sensors corrupting data). `563` = WY6Y AcuRite 5-in-1. |
| `AUTO_ENABLE_RF` | `1` | Send `{"active":3}` to WeatherThief when its LWT goes online |
| `RF_ACTIVE_RTL` | `3` | RTL receiver mode number in OpenMQTTGateway |

## MQTT topics

| Topic | Direction | Content |
|-------|-----------|---------|
| `wy6y/weather/WeatherThief/RTL_433toMQTT/#` | IN | Decoded sensor data from WeatherThief |
| `wy6y/weather/WeatherThief/LWT` | IN | WeatherThief online/offline |
| `wy6y/weather/WeatherThief/SYStoMQTT` | IN | Used for auto RF-enable on fresh boot |
| `wy6y/weather/WeatherThief/commands/MQTTtoRF/config` | OUT | `{"active":3}` to re-enable RTL receiver |
| `wy6y/weather/WeatherThief/commands/MQTTtoSYS` | OUT | `{"cmd":"restart"}` to reboot ESP32 (watchdog escalation) |
| `wy6y/weather/hud/current` | OUT (retained) | Current conditions JSON for CyberHUD |
| `wy6y/weather/hud/state` | OUT (retained) | Merged state JSON for CyberHUD |

The app **ignores any message on `wy6y/weather/hud/*`** to prevent self-feedback loops — those are outbound-only topics.

## RF watchdog behavior

When RTL data goes stale the watchdog escalates in two stages:

**Stage 1 — RF reinit (attempts 1–3):** sends `{"active":3}` to re-enable the CC1101 receiver, with exponential backoff (45s → 45s → 45s) to avoid hammering the CC1101 — each command causes a ~2s SPI reinit window during which the receiver misses transmissions.

**Stage 2 — firmware restart (attempt 4+):** if RF reinits haven't recovered the data after ~5 minutes, sends `{"cmd":"restart"}` to `commands/MQTTtoSYS`, rebooting the ESP32. The device reconnects in ~15–20 seconds and the RF receiver is re-enabled automatically via the LWT-online handler. Backoff continues at 2min → 5min → 5min → 10min intervals for subsequent attempts.

Backoff resets to zero as soon as fresh data arrives.

The watchdog only reconnects its own MQTT client if MQTT itself has gone silent (`mqtt_rx_age > 120s`). It does **not** reconnect just because RTL data is stale — that reconnect triggers cascading `lwt-still-online` + `app-connect` RF enables simultaneously, which can put the CC1101 into a constant reinit loop and prevent it from decoding anything.

## Data flow details

- Sensor decodes are merged into `_local_state["merged"]` in memory.
- History is persisted to `weatherthief_history.json` (rolling 500-sample buffer, ~2.5 hours at 18s intervals).
- On startup the last known merged state is loaded from history so the dashboard shows real data immediately, even before the first new decode arrives.
- NWS alerts are polled every 5 minutes for the station coordinates.
- The HUD is fed via `_publish_to_hud()` on every new decode and every 30 seconds via a background thread.

## Sensor ID filtering

The AcuRite 5-in-1 transmits on 433 MHz. So do many neighbors' sensors. Without filtering, the first decode after a WeatherThief reboot might be a neighbor's indoor sensor (often ~20°C / 68°F), corrupting the display.

`ALLOWED_SENSOR_IDS=563` in `.env` restricts the ingest to the WY6Y sensor only. To find your sensor's ID, temporarily clear the variable and watch:

```bash
mosquitto_sub -h 127.0.0.1 -u wy6y -P 'YOUR_PASS' \
  -t 'wy6y/weather/WeatherThief/RTL_433toMQTT/#' -v
```

## Project layout

```
app.py                       # Flask app + MQTT ingest + HUD publisher
templates/index.html         # Cyberpunk dashboard UI
static/sw.js                 # PWA service worker (cache v3)
static/                      # PWA icons, service worker
scripts/generate_icons.py    # Regenerate PWA icons from source
weatherthief_history.json    # Persisted rolling history
.env                         # Runtime config (gitignored)
```

## PWA install

The dashboard is installable as a PWA (works offline for the shell; live data requires network).

**iOS (Safari):** Share → Add to Home Screen
**Android (Chrome):** tap Install App banner or browser menu → Install app

> **Prerequisite:** Your device must trust the Caddy internal CA certificate.
> Download from `https://wx.wy6y.net/caddy-ca.crt` and install it.
> On iOS, after installing the profile go to Settings → General → About → Certificate Trust Settings and enable it.

If the home screen icon shows "Not Found" after install, visit `https://wx.wy6y.net/` once in the browser (not the icon) to trigger a service worker update, then re-add to home screen.

## Reverse proxy

Caddy proxies `wx.wy6y.net` directly to `127.0.0.1:5000` at root — no prefix stripping. Flask sees `/`, `/api/`, `/static/` as normal. See `/etc/caddy/Caddyfile` and `~/README.md` for the network architecture.
