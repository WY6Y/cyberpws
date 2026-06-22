#!/bin/bash
set -euo pipefail

SRC="/home/wy6y/cyberpws-fresh"
DEST="/home/stephenhouser/cyberpws"

echo "Stopping old cyberpws..."
sudo fuser -k 5000/tcp 2>/dev/null || true
sudo pkill -f '/home/stephenhouser/cyberpws/app.py' 2>/dev/null || true
sleep 1

echo "Deploying to ${DEST}..."
sudo mkdir -p "$DEST"
sudo cp -a "$SRC/app.py" "$SRC/templates" "$SRC/static" "$DEST/"
sudo cp -a "$SRC/.wu_api_key" "$DEST/"
sudo chown -R stephenhouser:stephenhouser "$DEST"
sudo chmod 600 "$DEST/.wu_api_key"

echo "Starting cyberpws..."
cd "$DEST"
sudo -u stephenhouser nohup python3 app.py > /tmp/cyberpws.log 2>&1 &
sleep 2

echo "Smoke test..."
curl -fsS http://127.0.0.1:5000/api/dashboard | python3 -c "import sys,json; d=json.load(sys.stdin); assert d['success']; print('OK — temp', d['data']['current']['imperial']['temp'], 'cache', d.get('cache'))"