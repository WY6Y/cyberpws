#!/usr/bin/env python3
"""
cyberpws/app.py
Badass cyberpunk web dashboard — live uplink from WeatherThief (rtl_433 / AcuRite).

Run:
  cd ~/cyberpws-fresh
  MQTT_USER=wy6y MQTT_PASS='...' python3 app.py

Then open http://YOUR-IP:5001  (or localhost:5001)

Legacy WU fallback: set USE_WU_FALLBACK=1 and WU_API_KEY / .wu_api_key
"""

from flask import Flask, jsonify, send_from_directory
import json
import math
import requests
import os
import threading
import time
from statistics import mean

try:
    import paho.mqtt.client as mqtt
except ImportError:
    mqtt = None  # type: ignore

# ============== YOUR PWS CONFIG ==============
STATION_ID = "KOKEDMON585"
DISPLAY_NAME = "WY6Y Weather"
NEIGHBORHOOD = "Brasswood"
UNITS = "e"
API_BASE = "https://api.weather.com/v2/pws"
USE_WU_FALLBACK = os.getenv("USE_WU_FALLBACK", "0").strip().lower() in ("1", "true", "yes")
DATA_STALE_SEC = int(os.getenv("DATA_STALE_SEC", "900"))  # 15 min before fallback
HISTORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "weatherthief_history.json")
HISTORY_MAX_AGE = 86400  # keep 24h of samples

# Station location (from PWS obs) for NWS severe weather + heat alerts (Edmond, OK)
STATION_LAT = 35.617
STATION_LON = -97.538

# Cache TTLs — keep WU traffic low and polite
CURRENT_TTL = int(os.getenv("WU_CURRENT_TTL", "90"))   # seconds
RAPID_TTL = int(os.getenv("WU_RAPID_TTL", "300"))      # 5 minutes
ALERTS_TTL = int(os.getenv("NWS_ALERTS_TTL", "300"))   # 5 minutes

_current_cache = {"data": None, "ts": 0}
_rapid_cache = {"data": None, "ts": 0}
_alerts_cache = {"data": [], "ts": 0}
_api_key = None

# WeatherThief ESP32 rtl_433 uplink (OpenMQTTGateway)
MQTT_HOST = os.getenv("MQTT_HOST", "127.0.0.1")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
MQTT_TOPIC_PREFIX = os.getenv("MQTT_TOPIC_PREFIX", "wy6y/weather")
MQTT_USER = os.getenv("MQTT_USER", "wy6y")
MQTT_PASS = os.getenv("MQTT_PASS", "IndyBoy24#")
GATEWAY_NAME = os.getenv("GATEWAY_NAME", "WeatherThief")

_local_lock = threading.Lock()
_local_state = {
    "online": False,
    "gateway": GATEWAY_NAME,
    "last_message": None,
    "last_ts": 0,
    "readings": {},
    "merged": {},
    "history": [],
    "rain_mm_baseline": None,
}
_mqtt_client = None

# ============== FLASK ==============
static_url_path = "/static"
app = Flask(__name__, template_folder="templates", static_folder="static", static_url_path=static_url_path)


def _load_api_key():
    global _api_key
    if _api_key:
        return _api_key

    key = os.getenv("WU_API_KEY", "").strip()
    if not key:
        secrets_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".wu_api_key")
        if os.path.isfile(secrets_path):
            with open(secrets_path, "r", encoding="utf-8") as f:
                key = f.read().strip()

    if not key:
        raise RuntimeError("WU API key missing — set WU_API_KEY or create .wu_api_key")

    _api_key = key
    return _api_key


def _wu_params():
    return {
        "stationId": STATION_ID,
        "format": "json",
        "units": UNITS,
        "apiKey": _load_api_key(),
    }


def _cache_age(cache):
    if not cache["data"] or not cache["ts"]:
        return None
    return int(time.time() - cache["ts"])


# ============== CORE FETCH LOGIC ==============

def fetch_current(force=False):
    now = time.time()
    if not force and _current_cache["data"] is not None and now - _current_cache["ts"] < CURRENT_TTL:
        return _current_cache["data"]

    url = f"{API_BASE}/observations/current"
    try:
        r = requests.get(url, params=_wu_params(), timeout=12)
        r.raise_for_status()
        data = r.json()
        if not data.get("observations"):
            raise RuntimeError("No current observation")
        obs = data["observations"][0]
        obs["_fetched_at"] = int(now)
        _current_cache.update({"data": obs, "ts": now})
        return obs
    except Exception:
        if _current_cache["data"] is not None:
            stale = dict(_current_cache["data"])
            stale["_stale"] = True
            stale["_stale_age"] = _cache_age(_current_cache)
            return stale
        raise


def fetch_rapid(force=False):
    now = time.time()
    if not force and _rapid_cache["data"] is not None and now - _rapid_cache["ts"] < RAPID_TTL:
        return _rapid_cache["data"]

    url = f"{API_BASE}/observations/all/1day"
    try:
        r = requests.get(url, params=_wu_params(), timeout=18)
        r.raise_for_status()
        data = r.json()
        rapid = data.get("observations", [])
        _rapid_cache.update({"data": rapid, "ts": now})
        return rapid
    except Exception:
        if _rapid_cache["data"] is not None:
            return _rapid_cache["data"]
        raise


def _filter_rapid(rapid):
    good = [o for o in rapid if o.get("qcStatus") in (0, None)]
    return good if good else rapid


def fetch_nws_alerts():
    now = time.time()
    if now - _alerts_cache["ts"] < ALERTS_TTL:
        return _alerts_cache["data"]

    url = f"https://api.weather.gov/alerts/active?point={STATION_LAT},{STATION_LON}"
    try:
        r = requests.get(
            url,
            timeout=8,
            headers={"User-Agent": "cyberpws-ok/1.0 (personal weather uplink for Edmond OK)"},
        )
        r.raise_for_status()
        data = r.json()
        alerts = []
        for f in data.get("features", []):
            props = f.get("properties", {})
            event = (props.get("event") or "").strip()
            event_upper = event.upper()
            if any(k in event_upper for k in [
                "TORNADO", "THUNDERSTORM", "SEVERE", "FLASH FLOOD",
                "HEAT", "EXCESSIVE HEAT", "WIND", "FLOOD", "ADVISORY", "WARNING", "WATCH",
            ]):
                alerts.append({
                    "event": event,
                    "severity": props.get("severity", "Unknown"),
                    "headline": props.get("headline") or event,
                    "expires": props.get("expires"),
                    "description": (props.get("description") or props.get("instruction") or "")[:220].strip(),
                })
        _alerts_cache.update({"data": alerts, "ts": now})
        return alerts
    except Exception as e:
        print(f"[alerts] NWS fetch failed (non-fatal): {e}")
        return _alerts_cache["data"] or []


def compute_summary(rapid):
    if not rapid:
        return {}
    rapid = _filter_rapid(rapid)
    temps, hums, press, winds, gusts = [], [], [], [], []
    last_precip = None
    precip_rates = []

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
        pr = imp.get("precipRate")
        if pr is not None:
            precip_rates.append(pr)

    summary = {}
    if temps:
        if len(temps) > 6:
            s = sorted(temps)
            temps = s[2:-2]
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

    if precip_rates:
        summary["precip_max_rate"] = round(max(precip_rates), 2)
        step = max(1, len(precip_rates) // 40)
        history = precip_rates[::step][-36:]
        summary["precip_rate_history"] = [round(x, 2) for x in history]
    else:
        summary["precip_max_rate"] = 0.0
        summary["precip_rate_history"] = []

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


def _cache_meta():
    return {
        "current_age": _cache_age(_current_cache),
        "rapid_age": _cache_age(_rapid_cache),
        "alerts_age": _cache_age(_alerts_cache),
    }


# ============== WEATHERTHIEF MQTT (rtl_433) ==============

def _c_to_f(c):
    if c is None:
        return None
    return round((float(c) * 9 / 5) + 32, 1)


def _kmh_to_mph(kmh):
    if kmh is None:
        return None
    return round(float(kmh) * 0.621371, 1)


def _mm_to_in(mm):
    if mm is None:
        return None
    return round(float(mm) / 25.4, 3)


def _compute_dewpoint_f(temp_f, humidity):
    if temp_f is None or humidity is None or humidity <= 0:
        return None
    t_c = (float(temp_f) - 32) * 5 / 9
    h = max(1, min(100, float(humidity)))
    a = 17.27
    b = 237.7
    alpha = ((a * t_c) / (b + t_c)) + math.log(h / 100.0)
    dew_c = (b * alpha) / (a - alpha)
    return round((dew_c * 9 / 5) + 32, 1)


def _compute_heat_index_f(temp_f, humidity):
    if temp_f is None or humidity is None:
        return None
    t = float(temp_f)
    rh = float(humidity)
    if t < 80:
        return round(t, 1)
    hi = (
        -42.379 + 2.04901523 * t + 10.14333127 * rh
        - 0.22475541 * t * rh - 0.00683783 * t * t
        - 0.05481717 * rh * rh + 0.00122874 * t * t * rh
        + 0.00085282 * t * rh * rh - 0.00000199 * t * t * rh * rh
    )
    return round(hi, 1)


def _local_is_fresh():
    with _local_lock:
        if not _local_state["online"] or not _local_state["last_ts"]:
            return False
        return (time.time() - _local_state["last_ts"]) < DATA_STALE_SEC


def _save_history():
    try:
        with _local_lock:
            payload = {
                "merged": _local_state["merged"],
                "rain_mm_baseline": _local_state["rain_mm_baseline"],
                "history": _local_state["history"][-500:],
            }
        tmp = HISTORY_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        os.replace(tmp, HISTORY_FILE)
    except Exception as e:
        print(f"[history] save failed: {e}")


def _load_history():
    if not os.path.isfile(HISTORY_FILE):
        return
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            payload = json.load(f)
        cutoff = time.time() - HISTORY_MAX_AGE
        history = [p for p in payload.get("history", []) if p.get("ts", 0) >= cutoff]
        with _local_lock:
            _local_state["history"] = history[-500:]
            if payload.get("merged"):
                _local_state["merged"].update(payload["merged"])
            if payload.get("rain_mm_baseline") is not None:
                _local_state["rain_mm_baseline"] = payload["rain_mm_baseline"]
            if history:
                _local_state["last_ts"] = history[-1]["ts"]
                _local_state["online"] = True
        print(f"[history] loaded {len(history)} samples")
    except Exception as e:
        print(f"[history] load failed: {e}")


def _ingest_rtl433(payload: dict, topic: str):
    if not isinstance(payload, dict):
        return
    model = str(payload.get("model") or payload.get("protocol") or "unknown")
    now = int(time.time())

    merged = {}
    if payload.get("temperature_C") is not None:
        merged["temp_f"] = _c_to_f(payload["temperature_C"])
    if payload.get("temperature_F") is not None:
        merged["temp_f"] = round(float(payload["temperature_F"]), 1)
    if payload.get("humidity") is not None and int(payload.get("humidity", 0)) <= 100:
        merged["humidity"] = int(payload["humidity"])
    if payload.get("wind_avg_km_h") is not None:
        merged["wind_mph"] = _kmh_to_mph(payload["wind_avg_km_h"])
    if payload.get("wind_max_km_h") is not None:
        merged["wind_gust_mph"] = _kmh_to_mph(payload["wind_max_km_h"])
    if payload.get("wind_dir_deg") is not None:
        merged["wind_dir"] = round(float(payload["wind_dir_deg"]), 1)
    if payload.get("rain_mm") is not None:
        merged["rain_mm"] = float(payload["rain_mm"])
        merged["rain_in"] = _mm_to_in(payload["rain_mm"])
    if payload.get("rain_in") is not None:
        merged["rain_in"] = round(float(payload["rain_in"]), 3)
    if payload.get("battery_ok") is not None:
        merged["battery_ok"] = int(payload["battery_ok"])
    if payload.get("id") is not None:
        merged["sensor_id"] = payload["id"]
    if payload.get("channel") is not None:
        merged["channel"] = payload["channel"]

    point = {
        "ts": now,
        "topic": topic,
        "model": model,
        "readings": merged,
        "raw": payload,
    }

    with _local_lock:
        _local_state["online"] = True
        _local_state["last_message"] = model
        _local_state["last_ts"] = now
        _local_state["readings"][model] = merged
        for key, val in merged.items():
            _local_state["merged"][key] = val

        # Precip rate from cumulative rain_mm deltas
        if "rain_mm" in merged:
            prev_mm = _local_state["merged"].get("_prev_rain_mm")
            prev_ts = _local_state["merged"].get("_prev_rain_ts")
            if prev_mm is not None and prev_ts and now > prev_ts:
                delta_mm = merged["rain_mm"] - prev_mm
                if delta_mm >= 0:
                    hours = (now - prev_ts) / 3600.0
                    if hours > 0:
                        merged["precip_rate_in"] = round(_mm_to_in(delta_mm) / hours, 3)
                        _local_state["merged"]["precip_rate_in"] = merged["precip_rate_in"]
            _local_state["merged"]["_prev_rain_mm"] = merged["rain_mm"]
            _local_state["merged"]["_prev_rain_ts"] = now

        _local_state["history"].append(point)
        cutoff = now - HISTORY_MAX_AGE
        _local_state["history"] = [p for p in _local_state["history"] if p.get("ts", 0) >= cutoff][-500:]

    _save_history()


def _snapshot_merged():
    with _local_lock:
        return dict(_local_state["merged"]), _local_state["last_ts"], list(_local_state["history"])


def _build_current_from_local():
    merged, last_ts, _ = _snapshot_merged()
    if not merged or not last_ts:
        return None

    temp_f = merged.get("temp_f")
    humidity = merged.get("humidity")
    wind_mph = merged.get("wind_mph")
    gust_mph = merged.get("wind_gust_mph") or wind_mph
    wind_dir = merged.get("wind_dir", 0)
    rain_in = merged.get("rain_in")
    precip_rate = merged.get("precip_rate_in", 0.0) or 0.0
    dewpt = _compute_dewpoint_f(temp_f, humidity)
    heat_idx = _compute_heat_index_f(temp_f, humidity)

    obs_local = time.localtime(last_ts)
    obs_time_local = time.strftime("%Y-%m-%d %H:%M:%S", obs_local)

    return {
        "_source": "weatherthief",
        "_fetched_at": int(time.time()),
        "stationID": STATION_ID,
        "neighborhood": NEIGHBORHOOD,
        "country": "US",
        "lat": STATION_LAT,
        "lon": STATION_LON,
        "obsTimeLocal": obs_time_local,
        "obsTimeUtc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(last_ts)),
        "epoch": last_ts,
        "humidity": humidity,
        "winddir": int(wind_dir) if wind_dir is not None else 0,
        "qcStatus": 0,
        "softwareType": "WeatherThief/rtl_433",
        "imperial": {
            "temp": temp_f,
            "dewpt": dewpt,
            "heatIndex": heat_idx,
            "windChill": temp_f,
            "windSpeed": wind_mph,
            "windGust": gust_mph,
            "pressure": None,
            "precipRate": precip_rate,
            "precipTotal": rain_in,
            "elev": None,
        },
    }


def _history_point_to_rapid(point: dict):
    r = point.get("readings") or {}
    ts = point.get("ts", int(time.time()))
    temp_f = r.get("temp_f")
    humidity = r.get("humidity")
    wind_mph = r.get("wind_mph")
    gust_mph = r.get("wind_gust_mph") or wind_mph
    obs_local = time.localtime(ts)
    obs_time_local = time.strftime("%Y-%m-%d %H:%M:%S", obs_local)
    dewpt = _compute_dewpoint_f(temp_f, humidity)
    heat_idx = _compute_heat_index_f(temp_f, humidity)
    return {
        "epoch": ts,
        "obsTimeLocal": obs_time_local,
        "humidityAvg": humidity,
        "humidityHigh": humidity,
        "humidityLow": humidity,
        "imperial": {
            "tempAvg": temp_f,
            "tempHigh": temp_f,
            "tempLow": temp_f,
            "dewptAvg": dewpt,
            "dewptHigh": dewpt,
            "dewptLow": dewpt,
            "heatindexAvg": heat_idx,
            "heatindexHigh": heat_idx,
            "heatindexLow": heat_idx,
            "windspeedAvg": wind_mph,
            "windspeedHigh": wind_mph,
            "windspeedLow": wind_mph,
            "windgustAvg": gust_mph,
            "windgustHigh": gust_mph,
            "windgustLow": gust_mph,
            "pressureMax": None,
            "pressureMin": None,
            "pressureTrend": 0.0,
            "precipRate": r.get("precip_rate_in", 0.0) or 0.0,
            "precipTotal": r.get("rain_in"),
        },
        "lat": STATION_LAT,
        "lon": STATION_LON,
    }


def _build_rapid_from_local():
    _, _, history = _snapshot_merged()
    if not history:
        current = _build_current_from_local()
        return [_history_point_to_rapid({"ts": current["epoch"], "readings": {
            "temp_f": current["imperial"]["temp"],
            "humidity": current["humidity"],
            "wind_mph": current["imperial"]["windSpeed"],
            "wind_gust_mph": current["imperial"]["windGust"],
            "rain_in": current["imperial"]["precipTotal"],
            "precip_rate_in": current["imperial"]["precipRate"],
        }})] if current else []
    return [_history_point_to_rapid(p) for p in history]


def _dashboard_payload():
    source = "weatherthief"
    current = _build_current_from_local()
    rapid = _build_rapid_from_local()
    summary = compute_summary(rapid) if rapid else {}
    alerts = fetch_nws_alerts()
    local = _local_payload()

    if not current and USE_WU_FALLBACK:
        source = "wunderground"
        try:
            rapid = fetch_rapid()
            current = fetch_current()
            summary = compute_summary(rapid)
        except Exception:
            pass
    elif not current:
        raise RuntimeError("WeatherThief offline — no rtl_433 data yet")

    return {
        "source": source,
        "current": current,
        "rapid": rapid,
        "summary": summary,
        "alerts": alerts,
        "local": local,
    }


def _on_mqtt_connect(client, userdata, flags, rc, properties=None):
    if rc != 0:
        print(f"[mqtt] connect failed rc={rc}")
        return
    prefix = MQTT_TOPIC_PREFIX.strip("/")
    client.subscribe(f"{prefix}/#")
    print(f"[mqtt] subscribed {prefix}/#")


def _on_mqtt_message(client, userdata, msg):
    topic = msg.topic or ""
    payload_raw = msg.payload.decode("utf-8", "replace").strip()
    if topic.endswith("/LWT"):
        with _local_lock:
            _local_state["online"] = payload_raw.lower() in ("online", "connected", "1", "true")
        return
    if "/RTL_433toMQTT" not in topic:
        return
    try:
        payload = json.loads(payload_raw)
    except json.JSONDecodeError:
        return
    if isinstance(payload, dict):
        _ingest_rtl433(payload, topic)


def _start_mqtt():
    global _mqtt_client
    if mqtt is None:
        print("[mqtt] paho-mqtt not installed — local rtl_433 uplink disabled")
        return
    try:
        if hasattr(mqtt, "CallbackAPIVersion"):
            client = mqtt.Client(
                callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
                client_id="cyberpws-weather",
            )
        else:
            client = mqtt.Client(client_id="cyberpws-weather")
        if MQTT_USER:
            client.username_pw_set(MQTT_USER, MQTT_PASS or None)
        client.on_connect = _on_mqtt_connect
        client.on_message = _on_mqtt_message
        client.connect_async(MQTT_HOST, MQTT_PORT, 60)
        client.loop_start()
        _mqtt_client = client
        print(f"[mqtt] WeatherThief listener on {MQTT_HOST}:{MQTT_PORT}")
    except Exception as e:
        print(f"[mqtt] start failed: {e}")


def _local_payload():
    with _local_lock:
        age = int(time.time() - _local_state["last_ts"]) if _local_state["last_ts"] else None
        return {
            "gateway": _local_state["gateway"],
            "online": _local_state["online"],
            "last_message": _local_state["last_message"],
            "last_age_sec": age,
            "readings": dict(_local_state["readings"]),
            "merged": dict(_local_state["merged"]),
            "history": list(_local_state["history"][-20:]),
        }


# ============== API ROUTES ==============

@app.route("/")
def index():
    template_path = os.path.join(app.template_folder, "index.html")
    with open(template_path, "r", encoding="utf-8") as f:
        html = f.read()

    html = html.replace("{{ display_name }}", DISPLAY_NAME)
    html = html.replace("{{ neighborhood | upper }}", NEIGHBORHOOD.upper())

    return html


@app.route("/api/dashboard")
def api_dashboard():
    """Single bundled endpoint — WeatherThief MQTT primary, WU optional fallback."""
    try:
        payload = _dashboard_payload()
        cache = _cache_meta()
        cache["source"] = payload.get("source", "weatherthief")
        cache["local_age"] = _local_payload().get("last_age_sec")
        return jsonify({"success": True, "data": payload, "cache": cache})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 502


@app.route("/api/current")
def api_current():
    try:
        return jsonify({"success": True, "data": fetch_current(), "cache": _cache_meta()})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 502


@app.route("/api/rapid")
def api_rapid():
    try:
        return jsonify({"success": True, "data": fetch_rapid(), "cache": _cache_meta()})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 502


@app.route("/api/summary")
def api_summary():
    try:
        rapid = fetch_rapid()
        return jsonify({"success": True, "data": compute_summary(rapid), "cache": _cache_meta()})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 502


@app.route("/api/alerts")
def api_alerts():
    try:
        return jsonify({"success": True, "data": fetch_nws_alerts(), "cache": _cache_meta()})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 502


@app.route("/api/local")
def api_local():
    """Live AcuRite/rtl_433 decode stream from WeatherThief ESP32."""
    return jsonify({"success": True, "data": _local_payload()})


@app.route("/manifest.json")
def manifest():
    return jsonify({
        "id": "/",
        "name": "WY6Y Weather",
        "short_name": "WY6Y WX",
        "description": "Cyberpunk personal weather dashboard — live WeatherThief rtl_433 uplink",
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "display_override": ["standalone", "minimal-ui"],
        "orientation": "any",
        "background_color": "#050505",
        "theme_color": "#00f0ff",
        "categories": ["weather", "utilities"],
        "icons": [
            {"src": "/static/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
            {"src": "/static/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
            {"src": "/static/icon-maskable-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
            {"src": "/static/apple-touch-icon.png", "sizes": "180x180", "type": "image/png", "purpose": "any"},
        ],
    })


@app.route("/sw.js")
def service_worker():
    return send_from_directory(
        os.path.join(app.root_path, "static"),
        "sw.js",
        mimetype="application/javascript",
        max_age=0,
    )


if __name__ == "__main__":
    _load_history()
    _start_mqtt()
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", 5000))
    print("╔════════════════════════════════════════════════════════════╗")
    print("║  WY6Y WEATHER  •  WEATHERTHIEF UPLINK  |  rtl_433         ║")
    print("║  Neon. Data. No corporate bullshit.                        ║")
    print("╚════════════════════════════════════════════════════════════╝")
    print(f"Station : {STATION_ID} ({DISPLAY_NAME})")
    print(f"Binding : {host}:{port}")
    print(f"Source  : WeatherThief MQTT (WU fallback={'on' if USE_WU_FALLBACK else 'off'})")
    print(f"Cache   : current={CURRENT_TTL}s  rapid={RAPID_TTL}s  alerts={ALERTS_TTL}s")
    print(f"MQTT    : {MQTT_HOST}:{MQTT_PORT}  topic={MQTT_TOPIC_PREFIX}/#  gateway={GATEWAY_NAME}")
    print("Ctrl-C to stop.\n")
    app.run(host=host, port=port, debug=False, threaded=True)