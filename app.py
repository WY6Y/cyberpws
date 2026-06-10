#!/usr/bin/env python3
"""
cyberpws/app.py
Badass cyberpunk web dashboard for a Weather Underground PWS.

Run:
  cd ~/cyberpws
  python3 app.py

Then open http://YOUR-IP:5000  (or localhost:5000)

The WU API key and station are hardcoded for this machine.
Your data stays local — this is a pure proxy + pretty frontend.
"""

from flask import Flask, jsonify, render_template
import requests
import subprocess
import datetime
import json
import os
import time
from statistics import mean
from dotenv import load_dotenv

load_dotenv()  # Load .env file if present

# ============== YOUR PWS CONFIG (from .env or environment) ==============
API_KEY = os.getenv("WU_API_KEY", "YOUR_WU_PWS_API_KEY")
STATION_ID = os.getenv("WU_STATION_ID", "YOUR_STATION_ID")
DISPLAY_NAME = os.getenv("DISPLAY_NAME", "Your Weather")
NEIGHBORHOOD = os.getenv("NEIGHBORHOOD", "Your Neighborhood")
UNITS = os.getenv("UNITS", "e")
API_BASE = "https://api.weather.com/v2/pws"

if API_KEY == "YOUR_WU_PWS_API_KEY" or STATION_ID == "YOUR_STATION_ID":
    print("WARNING: Using placeholder API key or station ID. Set WU_API_KEY and WU_STATION_ID in .env or environment variables.")

# ============== FLASK ==============
app = Flask(__name__, template_folder="templates")

# ============== CORE FETCH LOGIC (adapted from wu-pws.py) ==============

def wind_dir(degrees):
    if degrees is None:
        return "?"
    dirs = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
            "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
    ix = int((degrees + 11.25) / 22.5) % 16
    return dirs[ix]

def fetch_current():
    url = f"{API_BASE}/observations/current"
    params = {"stationId": STATION_ID, "format": "json", "units": UNITS, "apiKey": API_KEY}
    r = requests.get(url, params=params, timeout=12)
    r.raise_for_status()
    data = r.json()
    if not data.get("observations"):
        raise RuntimeError("No current observation")
    return data["observations"][0]

def fetch_rapid():
    url = f"{API_BASE}/observations/all/1day"
    params = {"stationId": STATION_ID, "format": "json", "units": UNITS, "apiKey": API_KEY}
    r = requests.get(url, params=params, timeout=18)
    r.raise_for_status()
    data = r.json()
    return data.get("observations", [])

def _filter_rapid(rapid):
    good = [o for o in rapid if o.get("qcStatus") in (0, None)]
    return good if good else rapid

def compute_summary(rapid):
    if not rapid:
        return {}
    rapid = _filter_rapid(rapid)
    temps, hums, press, winds, gusts = [], [], [], [], []
    last_precip = None

    for o in rapid:
        imp = o.get("imperial", {})
        t = imp.get("tempAvg") or imp.get("tempHigh")
        if t is not None:
            temps.append(t)
        h = o.get("humidityAvg") or o.get("humidityHigh")
        if h is not None:
            hums.append(h)
        p = imp.get("pressureMax") or imp.get("pressure")
        if p is not None:
            press.append(p)
        w = imp.get("windspeedAvg") or imp.get("windspeedHigh")
        if w is not None:
            winds.append(w)
        g = imp.get("windgustHigh") or imp.get("windgustAvg")
        if g is not None:
            gusts.append(g)
        pt = imp.get("precipTotal")
        if pt is not None:
            last_precip = pt

    summary = {}
    if temps:
        if len(temps) > 6:
            s = sorted(temps)
            temps = s[2:-2]  # trim more outliers
        summary.update({
            "temp_min": round(min(temps), 1),
            "temp_max": round(max(temps), 1),
            "temp_avg": round(mean(temps), 1),
            "temp_range": round(max(temps) - min(temps), 1),
        })
    if hums:
        summary.update({
            "hum_min": round(min(hums)),
            "hum_max": round(max(hums)),
            "hum_avg": round(mean(hums), 1),
        })
    if press:
        summary.update({
            "press_min": round(min(press), 2),
            "press_max": round(max(press), 2),
            "press_trend": "RISING" if press[-1] > press[0] else ("FALLING" if press[-1] < press[0] else "STEADY"),
        })
    if winds:
        summary["wind_avg"] = round(mean(winds), 1)
    if gusts:
        summary["gust_max"] = round(max(gusts), 1)
    if last_precip is not None:
        summary["precip_total"] = round(last_precip, 2)

    # Vibe
    tmax = summary.get("temp_max", 70)
    hmax = summary.get("hum_max", 50)
    if tmax >= 95 and hmax >= 55:
        summary["vibe"] = "MUGGY INFERNO"
    elif tmax >= 90:
        summary["vibe"] = "HOT AS FUCK"
    elif tmax >= 80 and hmax >= 60:
        summary["vibe"] = "MUGGY"
    elif tmax < 50:
        summary["vibe"] = "CHILLY"
    else:
        summary["vibe"] = "NOMINAL"

    return summary

# ============== API ROUTES ==============

@app.route("/")
def index():
    return render_template("index.html", 
                           station=STATION_ID, 
                           display_name=DISPLAY_NAME,
                           neighborhood=NEIGHBORHOOD)

@app.route("/api/current")
def api_current():
    try:
        obs = fetch_current()
        obs["_fetched_at"] = int(time.time())
        return jsonify({"success": True, "data": obs})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 502

@app.route("/api/rapid")
def api_rapid():
    try:
        rapid = fetch_rapid()
        return jsonify({"success": True, "data": rapid})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 502

@app.route("/api/summary")
def api_summary():
    try:
        rapid = fetch_rapid()
        summary = compute_summary(rapid)
        return jsonify({"success": True, "data": summary})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 502

# ============== MAIN ==============
if __name__ == "__main__":
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", 5000))
    print("╔════════════════════════════════════════════════════════════╗")
    print(f"║  {DISPLAY_NAME}  •  CYBERPWS UPLINK  |  {STATION_ID}          ║")
    print("║  Neon. Data. No corporate bullshit.                        ║")
    print("╚════════════════════════════════════════════════════════════╝")
    print(f"Station : {STATION_ID} ({DISPLAY_NAME})")
    print(f"Binding : {host}:{port}")
    print("IMPORTANT: For Chrome PWAs and to avoid cookie warnings,")
    print("           run behind HTTPS (Caddy + Tailscale recommended).")
    print("Ctrl-C to stop.\n")
    app.run(host=host, port=port, debug=False, threaded=True)
