# WY6Y Weather (CyberPWS)

Cyberpunk personal weather dashboard with a **Progressive Web App** shell. Live data comes from **WeatherThief** — an ESP32 + CC1101 gateway running [OpenMQTTGateway](https://github.com/1technophile/OpenMQTTGateway) rtl_433, decoding an AcuRite 5-in-1 over 433 MHz.

No Weather Underground subscription required for normal operation.

## Features

- Live temperature, humidity, wind, rain from AcuRite via MQTT
- NWS severe weather / heat alerts for your coordinates
- Installable PWA with neon cyberpunk UI (Add to Home Screen)
- 24h charts built from on-device history
- Honest **N/A** for barometric pressure (not on 5-in-1)

## Quick start

```bash
cd cyberpws-fresh
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # edit MQTT password

export $(grep -v '^#' .env | xargs)
python3 app.py
```

Open **http://localhost:5001**

Regenerate PWA icons:

```bash
python3 scripts/generate_icons.py
```

## MQTT topics

WeatherThief publishes to:

```
wy6y/weather/WeatherThief/RTL_433toMQTT/#
```

Enable the 433 MHz receiver after wiring CC1101:

```bash
mosquitto_pub -h 127.0.0.1 -u wy6y -P 'YOUR_PASS' \
  -t 'wy6y/weather/WeatherThief/commands/MQTTtoRF/config' \
  -m '{"active":3}'
```

## Reverse proxy (Caddy)

See `Caddyfile.fresh` — point `reverse_proxy` at `127.0.0.1:5001`.

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `PORT` | `5000` | HTTP port |
| `MQTT_HOST` | `127.0.0.1` | Mosquitto host |
| `MQTT_USER` / `MQTT_PASS` | `wy6y` / — | MQTT credentials |
| `MQTT_TOPIC_PREFIX` | `wy6y/weather` | Topic root |
| `USE_WU_FALLBACK` | `0` | Set `1` to fall back to Weather Underground API |

## PWA

- `manifest.json` — standalone app, neon theme
- `static/sw.js` — offline shell + network-first API
- Icons in `static/` (192, 512, maskable, Apple touch)

On iPhone: Safari → Share → **Add to Home Screen**

## Project layout

```
app.py              # Flask API + MQTT ingest
templates/          # Dashboard HTML
static/             # PWA icons + service worker
scripts/            # Icon generator
Caddyfile.fresh     # Example reverse proxy
```

## License

MIT — hack it, fork it, no corporate bullshit.