#!/bin/bash
# One-time GitHub publish (after: gh auth login)
set -euo pipefail
cd "$(dirname "$0")/.."

REPO_NAME="${1:-wy6y-weather}"

if ! gh auth status >/dev/null 2>&1; then
  echo "Run first:  gh auth login"
  echo "Then rerun:  $0 $REPO_NAME"
  exit 1
fi

gh repo create "$REPO_NAME" --public \
  --source=. --remote=origin \
  --description "Cyberpunk PWA weather dashboard — WeatherThief rtl_433 MQTT uplink" \
  --push

echo "Done: https://github.com/$(gh api user -q .login)/$REPO_NAME"