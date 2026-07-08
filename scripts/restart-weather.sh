#!/usr/bin/env bash
# Restart ONLY the cyberpws-fresh weather app (port 5001) — never touches cyberpvs/solar.
set -euo pipefail
PORT="${PORT:-5001}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PID="$(ss -tlnp 2>/dev/null | awk -v p=":${PORT}" '$4 ~ p { if (match($0, /pid=([0-9]+)/, m)) print m[1] }' | head -1)"
if [[ -n "${PID:-}" ]]; then
  echo "Stopping weather app pid=${PID} on port ${PORT}"
  kill "$PID" 2>/dev/null || true
  sleep 2
fi
cd "$ROOT"
export MQTT_USER="${MQTT_USER:-wy6y}"
export MQTT_PASS="${MQTT_PASS:-IndyBoy24#}"
export PORT
export AUTO_ENABLE_RF="${AUTO_ENABLE_RF:-1}"
exec python3 app.py