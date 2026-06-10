#!/bin/bash
# Quick launcher for the cyberpunk PWS dashboard

cd "$(dirname "$0")"

# Activate virtualenv if it exists
if [ -d "venv" ]; then
    source venv/bin/activate
fi

IP=$(hostname -I | awk '{print $1}' 2>/dev/null || echo "localhost")
echo "╔════════════════════════════════════════════╗"
echo "║  Starting WY6Y WEATHER uplink...           ║"
echo "║  http://$IP:5000                           ║"
echo "║  http://localhost:5000                     ║"
echo "╚════════════════════════════════════════════╝"

python3 app.py
