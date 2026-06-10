#!/bin/bash
# Quick launcher for WY6Y Weather cyberpunk PWS dashboard

cd "$(dirname "$0")"
IP=$(hostname -I | awk '{print $1}')
echo "╔════════════════════════════════════════════╗"
echo "║  Starting WY6Y WEATHER uplink...           ║"
echo "║  http://$IP:5000                           ║"
echo "║  http://localhost:5000                     ║"
echo "╚════════════════════════════════════════════╝"
python3 app.py
