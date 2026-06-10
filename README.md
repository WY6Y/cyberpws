<img width="1417" height="719" alt="Screenshot 2026-06-10 at 4 30 52 PM" src="https://github.com/user-attachments/assets/c9772cd4-9d2e-4cf6-a3dc-eb7957d04061" />
# WY6Y Weather — Cyberpunk PWS Dashboard

A self-hosted, neon-drenched dashboard for your Weather Underground Personal Weather Station (PWS). Built as a replacement for the official WU app with a proper cyberpunk / HUD aesthetic.

Replaces the corporate UI with something that actually looks cool on a monitor (or as a saved Chrome web app / PWA).

## Features

- Live current conditions with giant readable temp + feels-like
- Wind vector with rotating compass arrow
- Two live updating Chart.js graphs (Temp/Humidity + Pressure/Wind) from the high-res rapid feed
- 24h stats + "vibe" assessment (MUGGY INFERNO etc.)
- Scrolling data log of recent rapid frames (terminal aesthetic)
- Auto refresh every 45 seconds
- Keyboard shortcuts: Press **R** to force sync, **G** to glitch
- Export current + recent data as JSON
- Heat index critical warnings when it's disgusting out
- Pure neon cyberpunk / HUD / scanline / glitch aesthetic
- No tracking, no ads — your API key never leaves the machine

## Prerequisites

- A Weather Underground API key (PWS contributor key)
- Your PWS Station ID (e.g. `KXXXXXXX`)
- Tailscale (strongly recommended for remote access + easy HTTPS)
- Python 3 + Flask + requests (the app is lightweight)
- Caddy (for proper HTTPS reverse proxy — required for a good PWA experience and to avoid cookie warnings)

## Quick Start (Development)

```bash
cd ~/cyberpws
python3 app.py
```

The app binds only to `127.0.0.1:5000` by default.

Open http://localhost:5000 locally.

**Note:** For anything serious (especially a saved Chrome web app), you should run it behind HTTPS. See the Caddy section below.

## Configuration

Edit `app.py` near the top:

```python
API_KEY = "your-wu-pws-key-here"
STATION_ID = "YOUR_STATION_ID"
DISPLAY_NAME = "WY6Y Weather"
```

(You can also override via environment variables if you prefer.)

## HTTPS + Chrome PWA (Strongly Recommended)

Chrome (and installed "web apps") shows cookie / "not secure" warnings on plain `http://` origins. Tailscale encrypts the wire, but the browser still sees HTTP.

**Best solution:** Put Caddy in front with certs from `tailscale cert`.

### 1. Generate certs (full MagicDNS name)

```bash
tailscale cert your-hostname.ts.net
sudo mkdir -p /etc/caddy/certs
sudo mv your-hostname.ts.net.crt your-hostname.ts.net.key /etc/caddy/certs/
sudo chown -R caddy:caddy /etc/caddy/certs 2>/dev/null || true
sudo chmod 600 /etc/caddy/certs/*
```

### 2. Install Caddy (Debian/Ubuntu)

```bash
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update
sudo apt install caddy
```

### 3. Deploy the config

```bash
sudo cp ~/cyberpws/Caddyfile.example /etc/caddy/Caddyfile
sudo systemctl restart caddy
```

### 4. Access

- https://your-hostname.ts.net (recommended)
- or https://YOUR-SHORT-NAME (short name — redirects)

### 5. Saved Web App / PWA

Delete any old HTTP version. Create a fresh one from the **https** URL:

Chrome menu → Install / "Create shortcut" → check "Open as window".

This is what finally eliminates the cookie warning.

See `Caddyfile.example` for the exact configuration (it includes `auto_https off` to keep things quiet).

## Running Persistently

### cyberpws (the dashboard)

It is set up as a **user systemd service**.

```bash
# Status
systemctl --user status cyberpws

# Restart after changes
systemctl --user restart cyberpws

# Logs
journalctl --user -u cyberpws -f
```

The service file lives at `~/.config/systemd/user/cyberpws.service`.

### Caddy (HTTPS reverse proxy)

Managed as a **system service** by the caddy package.

```bash
sudo systemctl status caddy
sudo systemctl restart caddy
sudo journalctl -u caddy -f
```

Caddy is required to bind to 80/443 and is enabled on boot.

## Tech Stack

- Flask (tiny backend that proxies the WU PWS v2 API)
- Tailwind (via CDN) + Chart.js (via CDN) — no build step
- All frontend in a single beautiful `templates/index.html`
- Caddy for HTTPS + Tailscale certs
- systemd (user service for the app, system service for Caddy)

The original CLI tool (`wu-pws.py`) lives outside this folder and reuses some of the same fetch/stats logic.

## Project Structure

```
~/cyberpws/
├── app.py                 # Flask backend + WU proxy
├── Caddyfile.example      # Example Caddy config for Tailscale HTTPS
├── run.sh                 # Simple launcher
├── templates/
│   └── index.html         # The entire cyberpunk frontend
└── README.md
```

## Notes

- The dashboard is designed to be accessed over Tailscale. All external access is protected by Tailscale + HTTPS.
- The WU API key and station ID are hardcoded in `app.py` for convenience on this machine only. Do not commit them if you ever publish the code.
- Local AcuRite bridge fusion and the WU radar embed have been removed (they broke the aesthetic or weren't reliable).
- The "Glitch" button and keyboard shortcuts (R = refresh, G = glitch) are pure frontend fun.

## Screenshots in Your Head

Black background. Neon cyan, green, and magenta. Giant temperature. Rotating wind vector. Terminal-style data log. Charts that look like they came from a 1980s military terminal that got a 2026 upgrade. Pure "I built this instead of using their garbage app" energy.

Enjoy the data, choom. 73 de WY6Y.
