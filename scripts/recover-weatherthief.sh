#!/usr/bin/env bash
# Recover WeatherThief MQTT uplink: restart commands + listener refresh.
set -euo pipefail

MQTT_HOST="${MQTT_HOST:-127.0.0.1}"
MQTT_PORT="${MQTT_PORT:-1883}"
MQTT_USER="${MQTT_USER:-wy6y}"
MQTT_PASS="${MQTT_PASS:-IndyBoy24#}"
PREFIX="${MQTT_TOPIC_PREFIX:-wy6y/weather}"
GATEWAY="${GATEWAY_NAME:-WeatherThief}"

pub() {
  mosquitto_pub -h "$MQTT_HOST" -p "$MQTT_PORT" -u "$MQTT_USER" -P "$MQTT_PASS" "$@"
}

echo "WeatherThief recovery @ $(date -Is)"
echo "LWT before:"
timeout 3 mosquitto_sub -h "$MQTT_HOST" -p "$MQTT_PORT" -u "$MQTT_USER" -P "$MQTT_PASS" \
  -t "${PREFIX}/${GATEWAY}/LWT" -C 1 2>/dev/null || echo "(no LWT)"

pub -t "${PREFIX}/${GATEWAY}/commands/MQTTtoSYS/config" -m '{"cmd":"restart"}' -q 1
pub -t "${PREFIX}/${GATEWAY}/commands/MQTTtoRF/config" -m '{"active":3}' -q 1

systemctl --user restart cyberpws.service
sleep 8

echo "LWT after:"
timeout 10 mosquitto_sub -h "$MQTT_HOST" -p "$MQTT_PORT" -u "$MQTT_USER" -P "$MQTT_PASS" \
  -t "${PREFIX}/${GATEWAY}/LWT" -C 1 2>/dev/null || echo "(no LWT)"

curl -s "http://127.0.0.1:5001/api/local" | python3 -c "
import sys, json
d = json.load(sys.stdin).get('data', {})
print(f\"listener online={d.get('online')} age={d.get('last_age_sec')}s gateway={d.get('gateway')}\")
"