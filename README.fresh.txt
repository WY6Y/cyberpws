FRESH CLEAN RESTART - WY6Y Weather (cyberpws)

This is a complete reset using the clean pre-subpath source logic.

Structure:
- app.py (the full clean version with proper fetch_current, fetch_rapid /all/1day, summary, NWS alerts, clean index() that serves templates/index.html + minimal replaces for display name)
- templates/index.html (the full cyberpunk dashboard HTML/JS/CSS, root paths only, placeholders for name/neighborhood where needed)
- static/ (put your icon-192.png and icon-512.png here for PWA + apple touch icon)

IMPORTANT - DATA WILL NOT LOAD UNTIL YOU DO THIS:
The old API key (23580ae7b33f4b5a980ae7b33f6b5a89) is returning 401 Unauthorized from Weather Underground / api.weather.com.

1. Go to weather.com (or wunderground.com), log in, manage your PWS station KOKEDMON585.
2. Generate a new Personal Weather Station API key.
3. Edit app.py, replace the API_KEY = "..." line with the new key.
4. Save, then restart the app.

Caddy:
A clean Caddyfile.fresh is included. It does ONLY root reverse_proxy + the short-name redirs to bare long name. No /WX subpath remnants at all.

Deployment (run these as your normal user with sudo when needed):

# 1. STOP old app
sudo fuser -k 5000/tcp || true
pkill -f 'python.*app.py' || true

# 2. BACKUP the broken live dir (never hurts)
sudo mv /home/stephenhouser/cyberpws /home/stephenhouser/cyberpws.broken.$(date +%Y%m%d-%H%M%S) 2>/dev/null || true

# 3. Install fresh (you may need to sudo chown or run parts as stephenhouser)
sudo mkdir -p /home/stephenhouser/cyberpws
sudo cp -a /home/wy6y/cyberpws-fresh/* /home/stephenhouser/cyberpws/
sudo chown -R stephenhouser:stephenhouser /home/stephenhouser/cyberpws

# 4. (Optional but recommended) copy old icons if you have them somewhere
# sudo cp /path/to/old/icon-*.png /home/stephenhouser/cyberpws/static/ || true

# 5. Replace Caddy config (backup first)
sudo cp /etc/caddy/Caddyfile /etc/caddy/Caddyfile.broken.$(date +%Y%m%d-%H%M%S) || true
sudo cp /home/wy6y/cyberpws-fresh/Caddyfile.fresh /etc/caddy/Caddyfile
sudo caddy fmt --overwrite /etc/caddy/Caddyfile
sudo caddy reload

# 6. EDIT THE API KEY (critical)
# sudo -u stephenhouser nano /home/stephenhouser/cyberpws/app.py
# Change the API_KEY line to your new fresh key from WU/weather.com

# 7. Start the app fresh
sudo fuser -k 5000/tcp || true
cd /home/stephenhouser/cyberpws
sudo -u stephenhouser nohup python3 app.py > /tmp/cyberpws.log 2>&1 &
sleep 2
tail -20 /tmp/cyberpws.log

# 8. Quick test (from this machine)
curl -s http://127.0.0.1:5000/api/current | head -c 300
curl -s -H "Host: wy6ypi5.taile0fc4a.ts.net" -k https://127.0.0.1/ | grep -o 'WY6Y WEATHER' | head -1

# 9. On your browsers/devices:
# - Use the long bare URL: https://wy6ypi5.taile0fc4a.ts.net/
# - Hard refresh (Ctrl/Cmd-Shift-R)
# - Clear site data / remove old home screen icon and re-add
# - Delete any old WY6Y-WX bookmarks that had the subpath

After the new key is in and the app restarted, current conditions + the 24h charts should populate again.

Tailscale was not touched at all.
