#!/bin/bash
# Simple installer for the cyberpunk PWS dashboard
# Run: bash install.sh

set -e

echo "=== Cyberpunk PWS Dashboard Installer ==="

cd "$(dirname "$0")"

# Create virtual environment
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi

echo "Activating virtual environment..."
source venv/bin/activate

echo "Installing dependencies..."
pip install --upgrade pip
pip install -r requirements.txt

# Create .env if it doesn't exist
if [ ! -f ".env" ]; then
    echo ""
    echo "Creating .env file..."
    read -p "Enter your WU PWS API Key: " WU_API_KEY
    read -p "Enter your Station ID (e.g. KOKEDMON585): " WU_STATION_ID
    read -p "Enter a display name (e.g. WY6Y Weather) [Your Weather]: " DISPLAY_NAME
    DISPLAY_NAME=${DISPLAY_NAME:-Your Weather}
    read -p "Enter neighborhood/location [Your Neighborhood]: " NEIGHBORHOOD
    NEIGHBORHOOD=${NEIGHBORHOOD:-Your Neighborhood}

    cat > .env << EOF
WU_API_KEY=$WU_API_KEY
WU_STATION_ID=$WU_STATION_ID
DISPLAY_NAME=$DISPLAY_NAME
NEIGHBORHOOD=$NEIGHBORHOOD
# HOST=0.0.0.0
# PORT=5000
EOF
    echo ".env file created."
else
    echo ".env already exists, skipping creation."
fi

chmod +x run.sh

echo ""
echo "=== Installation complete ==="
echo ""
echo "To run: ./run.sh"
echo "Then open http://localhost:5000 (or your machine's IP) in your browser."
echo ""

read -p "Set up systemd user service for auto-start on login? (y/N): " SETUP_SERVICE
if [[ "$SETUP_SERVICE" =~ ^[Yy]$ ]]; then
    mkdir -p ~/.config/systemd/user
    cat > ~/.config/systemd/user/cyberpws.service << EOF
[Unit]
Description=Cyberpunk PWS Dashboard
After=network.target

[Service]
Type=simple
WorkingDirectory=$(pwd)
ExecStart=$(pwd)/venv/bin/python $(pwd)/app.py
Restart=always
RestartSec=10
Environment=PATH=$(pwd)/venv/bin

[Install]
WantedBy=default.target
EOF
    systemctl --user daemon-reload
    systemctl --user enable --now cyberpws.service
    echo "Systemd user service installed, enabled, and started."
    echo "Manage with: systemctl --user status cyberpws"
    echo "View logs: journalctl --user -u cyberpws -f"
fi

echo ""
echo "For production (recommended):"
echo "  - Set up Caddy for HTTPS (see Caddyfile.example and README)"
echo "  - Access via https:// for best Chrome PWA experience (avoids cookie warnings)"
echo ""
echo "To update later: git pull && ./install.sh"