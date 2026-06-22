#!/bin/bash
# Point live Caddy reverse_proxy at the cyberpws port (no sudo needed — uses admin API).
set -euo pipefail

PORT="${CYBERPWS_PORT:-5001}"
TARGET="127.0.0.1:${PORT}"
CURRENT=$(curl -fsS http://127.0.0.1:2019/config/apps/http/servers/srv0/routes/0/handle/0/routes/0/handle/0/upstreams/0/dial 2>/dev/null || true)

if [[ "$CURRENT" == "\"${TARGET}\"" ]]; then
  echo "Caddy already proxying to ${TARGET}"
  exit 0
fi

echo "Patching Caddy upstream: ${CURRENT:-unknown} -> ${TARGET}"
curl -fsS -X PATCH \
  "http://127.0.0.1:2019/config/apps/http/servers/srv0/routes/0/handle/0/routes/0/handle/0/upstreams/0/dial" \
  -H "Content-Type: application/json" \
  -d "\"${TARGET}\""
echo "OK"