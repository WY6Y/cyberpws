#!/usr/bin/env python3
"""
cyberpws/app.py
Badass cyberpunk web dashboard — live uplink from WeatherThief (rtl_433 / AcuRite).

Pure WeatherThief MQTT listener (no Wunderground API).
"""

from flask import Flask, jsonify, send_from_directory, request
import json
import math
import requests
import os
import threading
import time
from datetime import datetime, timezone
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
DATA_STALE_SEC = int(os.getenv("DATA_STALE_SEC", "900"))  # stale threshold for local data
HISTORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "weatherthief_history.json")
HISTORY_MAX_AGE = 604800  # keep 7 days of samples for stale bootstrap

# Station location — EM15fo grid (Edmond / Brasswood, OK)
GRIDSQUARE = "EM15fo"
STATION_LAT = float(os.getenv("STATION_LAT", "35.6563"))
STATION_LON = float(os.getenv("STATION_LON", "-97.5438"))

# Cache TTLs (NWS only now; local data is live from MQTT)
ALERTS_TTL = int(os.getenv("NWS_ALERTS_TTL", "300"))   # 5 minutes
NWS_PRESSURE_TTL = int(os.getenv("NWS_PRESSURE_TTL", "300"))  # 5 minutes
NWS_USER_AGENT = "wy6y-weather/1.0 (personal PWS EM15fo)"

_alerts_cache = {"data": [], "ts": 0}
_nws_station_cache = {"id": None, "name": None, "ts": 0}
_pressure_cache = {"current": None, "history": [], "station": None, "ts": 0}

# WeatherThief ESP32 rtl_433 uplink (OpenMQTTGateway)
MQTT_HOST = os.getenv("MQTT_HOST", "127.0.0.1")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
MQTT_TOPIC_PREFIX = os.getenv("MQTT_TOPIC_PREFIX", "wy6y/weather")
MQTT_USER = os.getenv("MQTT_USER", "wy6y")
MQTT_PASS = os.getenv("MQTT_PASS", "IndyBoy24#")
GATEWAY_NAME = os.getenv("GATEWAY_NAME", "WeatherThief")
AUTO_ENABLE_RF = os.getenv("AUTO_ENABLE_RF", "1").strip().lower() in ("1", "true", "yes")
RF_ACTIVE_RTL = int(os.getenv("RF_ACTIVE_RTL", "3"))  # ACTIVE_RTL in OpenMQTTGateway
_raw = os.getenv("ALLOWED_SENSOR_IDS", "").strip()
ALLOWED_SENSOR_IDS: set[int] = {int(x) for x in _raw.split(",") if x.strip().isdigit()} if _raw else set()

PUSHOVER_USER_KEY = os.getenv("PUSHOVER_USER_KEY", "")
PUSHOVER_API_TOKEN = os.getenv("PUSHOVER_API_TOKEN", "")
# RSSI threshold delta applied to WeatherThief after every boot/reconnect.
# Threshold = avgRSSI + delta; lower values pass weaker signals.
WEATHERTHIEF_RSSI_DELTA = int(os.getenv("WEATHERTHIEF_RSSI_DELTA", "5"))

_local_lock = threading.Lock()
_local_state = {
    "online": False,
    "gateway": GATEWAY_NAME,
    "last_message": None,
    "last_ts": 0,
    "readings": {},
    "merged": {},
    "history": [],
    "rain_day_key": None,
    "rain_day_baseline_mm": None,
    "last_rain_mm": None,
}
_mqtt_client = None
_mqtt_lock = threading.Lock()
_last_mqtt_rx_ts = 0.0
_last_rf_enable_ts = 0.0
_last_mqtt_reconnect_ts = 0.0
_lwt_was_online = None
_rf_watchdog_started = False

# ============== FLASK ==============
static_url_path = "/static"
app = Flask(__name__, template_folder="templates", static_folder="static", static_url_path=static_url_path)


def _cache_age(cache):
    if not cache or not cache.get("ts"):
        return None
    return int(time.time() - cache["ts"])


# ============== CORE FETCH LOGIC (WeatherThief local only) ==============

def fetch_current(force=False):
    """Return current obs shaped like legacy, sourced purely from WeatherThief MQTT."""
    data = _build_current_from_local()
    if data:
        return data
    raise RuntimeError("No WeatherThief data available")


def fetch_rapid(force=False):
    """Return rapid history shaped like legacy, sourced purely from WeatherThief MQTT."""
    try:
        return _build_rapid_from_local()
    except Exception:
        _, _, history = _snapshot_merged()
        return [_history_point_to_rapid(p) for p in _normalize_history(history)]


def _filter_rapid(rapid):
    good = [o for o in rapid if o.get("qcStatus") in (0, None)]
    return good if good else rapid


def _nws_headers():
    return {"User-Agent": NWS_USER_AGENT, "Accept": "application/geo+json"}


def _pa_to_inhg(pa):
    if pa is None:
        return None
    return round(float(pa) * 0.00029529983, 2)


def _parse_nws_ts(ts_str):
    if not ts_str:
        return int(time.time())
    ts_str = ts_str.replace("Z", "+00:00")
    return int(datetime.fromisoformat(ts_str).timestamp())


def _nws_get(url, timeout=10):
    r = requests.get(url, timeout=timeout, headers=_nws_headers())
    r.raise_for_status()
    return r.json()


def _nws_nearest_station():
    now = time.time()
    if _nws_station_cache["id"] and now - _nws_station_cache["ts"] < 86400:
        return _nws_station_cache["id"], _nws_station_cache["name"]

    points = _nws_get(f"https://api.weather.gov/points/{STATION_LAT},{STATION_LON}")
    stations_url = points["properties"]["observationStations"]
    stations = _nws_get(stations_url)
    features = stations.get("features") or []
    if not features:
        raise RuntimeError("No NWS observation stations found")
    station_id = features[0]["properties"]["stationIdentifier"]
    station_name = features[0]["properties"].get("name", station_id)
    _nws_station_cache.update({"id": station_id, "name": station_name, "ts": now})
    return station_id, station_name


def fetch_nws_pressure(force=False):
    """Latest barometric pressure from nearest NWS ASOS (free, local)."""
    now = time.time()
    if not force and _pressure_cache["current"] and now - _pressure_cache["ts"] < NWS_PRESSURE_TTL:
        return _pressure_cache["current"]

    station_id, station_name = _nws_nearest_station()
    obs = _nws_get(f"https://api.weather.gov/stations/{station_id}/observations/latest")
    props = obs.get("properties") or {}
    baro = props.get("barometricPressure") or {}
    pa = baro.get("value")
    if pa is None:
        sea = props.get("seaLevelPressure") or {}
        pa = sea.get("value")
    inhg = _pa_to_inhg(pa)
    if inhg is None:
        raise RuntimeError(f"No pressure in NWS observation from {station_id}")

    obs_ts = _parse_nws_ts(props.get("timestamp"))

    current = {
        "pressure_inhg": inhg,
        "station_id": station_id,
        "station_name": station_name,
        "obs_ts": obs_ts,
        "source": "nws",
    }
    _pressure_cache["current"] = current
    _pressure_cache["station"] = station_id
    _pressure_cache["ts"] = now

    hist = _pressure_cache.get("history") or []
    if not hist or abs(hist[-1]["ts"] - obs_ts) > 60:
        hist.append({"ts": obs_ts, "pressure_inhg": inhg})
        cutoff = now - HISTORY_MAX_AGE
        _pressure_cache["history"] = [h for h in hist if h["ts"] >= cutoff][-500:]
    return current


def fetch_nws_pressure_history(force=False):
    """Rolling 24h pressure samples for charts."""
    now = time.time()
    hist = _pressure_cache.get("history") or []
    if not force and hist and now - _pressure_cache.get("hist_ts", 0) < NWS_PRESSURE_TTL:
        return hist

    station_id, _ = _nws_nearest_station()
    start = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - HISTORY_MAX_AGE))
    url = (
        f"https://api.weather.gov/stations/{station_id}/observations"
        f"?start={start}&limit=500"
    )
    try:
        data = _nws_get(url, timeout=18)
        samples = []
        for feat in data.get("features") or []:
            props = feat.get("properties") or {}
            baro = props.get("barometricPressure") or {}
            pa = baro.get("value")
            if pa is None:
                sea = props.get("seaLevelPressure") or {}
                pa = sea.get("value")
            inhg = _pa_to_inhg(pa)
            ts_str = props.get("timestamp")
            if inhg is None or not ts_str:
                continue
            samples.append({"ts": _parse_nws_ts(ts_str), "pressure_inhg": inhg})
        samples.sort(key=lambda x: x["ts"])
        if samples:
            _pressure_cache["history"] = samples
            _pressure_cache["hist_ts"] = now
        return _pressure_cache.get("history") or hist
    except Exception as e:
        print(f"[nws] pressure history failed (non-fatal): {e}")
        return hist


def _pressure_at_ts(target_ts, pressure_hist):
    if not pressure_hist:
        return None
    best = None
    best_delta = None
    for p in pressure_hist:
        delta = abs(p["ts"] - target_ts)
        if best_delta is None or delta < best_delta:
            best_delta = delta
            best = p["pressure_inhg"]
    if best_delta is not None and best_delta <= 3600:
        return best
    return None


def fetch_nws_alerts():
    now = time.time()
    if now - _alerts_cache["ts"] < ALERTS_TTL:
        return _alerts_cache["data"]

    url = f"https://api.weather.gov/alerts/active?point={STATION_LAT},{STATION_LON}"
    try:
        r = requests.get(url, timeout=8, headers=_nws_headers())
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
        summary["precip_daily"] = round(last_precip, 2)
        summary["precip_total"] = summary["precip_daily"]

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


def _local_day_key(ts=None):
    ts = ts or time.time()
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


def _update_daily_rain(merged: dict, now: int, *, lock_held: bool = False):
    """AcuRite rain_mm is a lifetime counter — derive today's accumulation."""
    rain_mm = merged.get("rain_mm")
    if rain_mm is None:
        return

    def work():
        today = _local_day_key(now)
        day_key = _local_state.get("rain_day_key")
        baseline = _local_state.get("rain_day_baseline_mm")

        if day_key != today:
            midnight = datetime.fromtimestamp(now).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            midnight_ts = int(midnight.timestamp())
            new_baseline = None
            for point in reversed(_local_state["history"]):
                if point.get("ts", 0) < midnight_ts:
                    readings = point.get("readings") or {}
                    if readings.get("rain_mm") is not None:
                        new_baseline = float(readings["rain_mm"])
                        break
            if new_baseline is None:
                for point in sorted(_local_state["history"], key=lambda p: p.get("ts", 0)):
                    if _local_day_key(point.get("ts", now)) != today:
                        continue
                    readings = point.get("readings") or {}
                    if readings.get("rain_mm") is not None:
                        new_baseline = float(readings["rain_mm"])
                        break
            if new_baseline is None:
                new_baseline = _local_state.get("last_rain_mm")
            if new_baseline is None:
                new_baseline = float(rain_mm)
            baseline = new_baseline
            _local_state["rain_day_key"] = today
            _local_state["rain_day_baseline_mm"] = baseline
        elif baseline is None:
            baseline = float(rain_mm)
            _local_state["rain_day_baseline_mm"] = baseline

        daily_mm = max(0.0, float(rain_mm) - float(baseline))
        merged["rain_daily_mm"] = round(daily_mm, 3)
        merged["rain_daily_in"] = _mm_to_in(daily_mm)
        _local_state["merged"]["rain_daily_mm"] = merged["rain_daily_mm"]
        _local_state["merged"]["rain_daily_in"] = merged["rain_daily_in"]
        _local_state["last_rain_mm"] = float(rain_mm)

    if lock_held:
        work()
    else:
        with _local_lock:
            work()


def _sync_rain_day_state():
    with _local_lock:
        if not _local_state["merged"].get("rain_mm"):
            return
        today = _local_day_key()
        first_today_mm = None
        for point in sorted(_local_state["history"], key=lambda p: p.get("ts", 0)):
            if _local_day_key(point.get("ts")) != today:
                continue
            readings = point.get("readings") or {}
            if readings.get("rain_mm") is not None:
                first_today_mm = float(readings["rain_mm"])
                break
        baseline = _local_state.get("rain_day_baseline_mm")
        if first_today_mm is not None and (
            baseline is None
            or _local_state.get("rain_day_key") != today
            or baseline > first_today_mm + 0.001
        ):
            _local_state["rain_day_key"] = today
            _local_state["rain_day_baseline_mm"] = first_today_mm
        ts = _local_state.get("last_ts") or int(time.time())
        _update_daily_rain(_local_state["merged"], ts, lock_held=True)


def _enable_weatherthief_rf(reason: str = "online", *, force: bool = False):
    """Re-enable rtl_433 RX after ESP boot (DEFER_RF_AT_BOOT)."""
    global _last_rf_enable_ts
    if not AUTO_ENABLE_RF or _mqtt_client is None:
        return
    now = time.time()
    debounce = 8 if force else 30
    if not force and now - _last_rf_enable_ts < debounce:
        return
    topic = f"{MQTT_TOPIC_PREFIX.strip('/')}/{GATEWAY_NAME}/commands/MQTTtoRF/config"
    try:
        _mqtt_client.publish(topic, json.dumps({"active": RF_ACTIVE_RTL}), qos=1)
        _last_rf_enable_ts = now
        print(f"[mqtt] RF receiver enabled ({reason}) -> {topic}")
    except Exception as e:
        print(f"[mqtt] RF enable failed: {e}")


def _apply_rssi_threshold(reason: str = "", delay: float = 0):
    """Push configured RSSI delta to WeatherThief RF config topic.

    Called after every boot or LWT online transition so the adaptive threshold
    stays at WEATHERTHIEF_RSSI_DELTA even after firmware restarts reset it.
    """
    def _do():
        if delay > 0:
            time.sleep(delay)
        if _mqtt_client is None:
            return
        topic = f"{MQTT_TOPIC_PREFIX.strip('/')}/{GATEWAY_NAME}/commands/MQTTtoRF/config"
        try:
            _mqtt_client.publish(topic, json.dumps({"rssithreshold": WEATHERTHIEF_RSSI_DELTA}), qos=1)
            print(f"[mqtt] rssithreshold={WEATHERTHIEF_RSSI_DELTA} applied ({reason})")
        except Exception as e:
            print(f"[mqtt] rssithreshold apply failed: {e}")
    if delay > 0:
        threading.Thread(target=_do, daemon=True).start()
    else:
        _do()


def _rtl_data_age_sec():
    with _local_lock:
        last_ts = _local_state.get("last_ts") or 0
    if not last_ts:
        return None
    return int(time.time() - last_ts)


def _maybe_enable_rf_after_online(reason: str):
    """LWT can stay retained 'online' across power cycles — also check data freshness."""
    age = _rtl_data_age_sec()
    if age is None or age > 90:
        _enable_weatherthief_rf(f"{reason}-stale-{age or 'none'}s", force=True)
    else:
        _enable_weatherthief_rf(reason)


def _reconnect_mqtt(reason: str):
    """Recover when the Flask app loses its MQTT subscription."""
    global _mqtt_client, _last_mqtt_reconnect_ts
    now = time.time()
    if now - _last_mqtt_reconnect_ts < 60:
        return
    with _mqtt_lock:
        if _mqtt_client is None:
            return
        _last_mqtt_reconnect_ts = now
        try:
            print(f"[mqtt] reconnecting ({reason})")
            try:
                _mqtt_client.loop_stop()
            except Exception:
                pass
            try:
                _mqtt_client.disconnect()
            except Exception:
                pass
            _mqtt_client.reconnect()
            _mqtt_client.loop_start()
            print("[mqtt] reconnect complete")
        except Exception as e:
            print(f"[mqtt] reconnect failed ({e}) — rebuilding client")
            try:
                _mqtt_client.loop_stop()
            except Exception:
                pass
            _mqtt_client = None
            _build_mqtt_client()


def _pushover_notify(title: str, message: str):
    if not PUSHOVER_USER_KEY or not PUSHOVER_API_TOKEN:
        return
    try:
        import urllib.request
        import urllib.parse
        data = urllib.parse.urlencode({
            "token": PUSHOVER_API_TOKEN,
            "user": PUSHOVER_USER_KEY,
            "title": title,
            "message": message,
        }).encode()
        req = urllib.request.Request("https://api.pushover.net/1/messages.json", data=data)
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        print(f"[pushover] notify failed: {e}")


def _restart_weatherthief_fw(reason: str):
    """Full ESP32 firmware restart via MQTT SYS command — last resort after RF reinits fail."""
    if _mqtt_client is None:
        return
    topic = f"{MQTT_TOPIC_PREFIX.strip('/')}/{GATEWAY_NAME}/commands/MQTTtoSYS"
    try:
        _mqtt_client.publish(topic, json.dumps({"cmd": "restart"}), qos=1)
        print(f"[mqtt] WeatherThief firmware restart sent ({reason}) -> {topic}")
        _pushover_notify("WeatherThief restarted", f"Watchdog sent firmware restart: {reason}")
    except Exception as e:
        print(f"[mqtt] firmware restart failed: {e}")


def _mqtt_watchdog_loop():
    # Back off after repeated failures to avoid hammering CC1101 with reinit cycles.
    # Each {"active":3} causes a CC1101 SPI reinit (~2s window of no reception).
    # Cascading reinits can cause the receiver to miss every transmission.
    # After 4 failed RF reinits (~5 min of staleness), escalate to a full firmware restart.
    _backoff_intervals = [45, 45, 45, 120, 120, 300, 300, 600]
    _FW_RESTART_AT_ATTEMPT = 4  # RF reinits 1-3 first; restart on attempt 4
    # If data is already deeply stale at startup (e.g. service restart while device was stuck),
    # skip the RF reinit warmup and go straight to restart-mode so we don't thrash the CC1101.
    time.sleep(10)  # brief settle so MQTT subscriptions are established first
    age0 = _rtl_data_age_sec()
    _attempt = _FW_RESTART_AT_ATTEMPT if (age0 and age0 > 300) else 0
    if _attempt:
        print(f"[mqtt] watchdog: starting at attempt {_attempt+1} (data already stale {age0}s at launch)")
    while True:
        interval = _backoff_intervals[min(_attempt, len(_backoff_intervals) - 1)]
        time.sleep(interval)
        try:
            if os.path.exists("/tmp/watchdog-pause"):
                print("[mqtt] watchdog paused (/tmp/watchdog-pause exists)")
                _attempt = 0
                continue
            age = _rtl_data_age_sec()
            if age is None or age <= 120:
                _attempt = 0  # data is fresh, reset backoff
                continue
            mqtt_rx_age = int(time.time() - _last_mqtt_rx_ts) if _last_mqtt_rx_ts else None
            print(f"[mqtt] watchdog: rtl data stale {age}s, mqtt_rx_age={mqtt_rx_age}, attempt={_attempt+1}")
            if _attempt >= _FW_RESTART_AT_ATTEMPT:
                # RF reinits haven't recovered it — send a full firmware restart and
                # wait a full interval before checking again so the ESP has time to reboot.
                _restart_weatherthief_fw(f"watchdog-stale-{age}s-attempt{_attempt+1}")
            else:
                _enable_weatherthief_rf(f"watchdog-stale-{age}s")
            # Only reconnect MQTT if MQTT itself is silent — not just because RTL data is stale.
            # Reconnecting when MQTT is fine causes a cascade of lwt+app-connect RF enables
            # that hammer the CC1101 with reinits and prevent it from decoding anything.
            if mqtt_rx_age is not None and mqtt_rx_age > 120:
                _reconnect_mqtt(f"stale-mqtt-{mqtt_rx_age}s")
            _attempt += 1
        except Exception as e:
            print(f"[mqtt] watchdog error: {e}")


def _start_rf_watchdog():
    global _rf_watchdog_started
    if _rf_watchdog_started:
        return
    _rf_watchdog_started = True
    threading.Thread(target=_mqtt_watchdog_loop, daemon=True, name="mqtt-watchdog").start()


def _save_history():
    try:
        with _local_lock:
            payload = {
                "merged": _local_state["merged"],
                "rain_day_key": _local_state["rain_day_key"],
                "rain_day_baseline_mm": _local_state["rain_day_baseline_mm"],
                "last_rain_mm": _local_state["last_rain_mm"],
                "history": _local_state["history"][-500:],
            }
        tmp = HISTORY_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        os.replace(tmp, HISTORY_FILE)
    except Exception as e:
        print(f"[history] save failed: {e}")


def _snapshot_readings(merged: dict) -> dict:
    return {k: v for k, v in merged.items() if not str(k).startswith("_")}


def _normalize_history(history):
    """Forward-fill AcuRite partial packets so charts always have temp/humidity."""
    running = {}
    normalized = []
    for point in sorted(history, key=lambda p: p.get("ts", 0)):
        readings = point.get("readings") or {}
        for key, val in readings.items():
            if not str(key).startswith("_") and val is not None:
                running[key] = val
        snap = _snapshot_readings(running)
        if not snap:
            continue
        normalized.append({
            "ts": point.get("ts", int(time.time())),
            "topic": point.get("topic", ""),
            "model": point.get("model", "Acurite-5n1"),
            "readings": snap,
            "raw": point.get("raw"),
        })
    return normalized


def _load_history():
    if not os.path.isfile(HISTORY_FILE):
        return
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            payload = json.load(f)
        cutoff = time.time() - HISTORY_MAX_AGE
        history = [p for p in payload.get("history", []) if p.get("ts", 0) >= cutoff]
        history = _normalize_history(history)
        with _local_lock:
            _local_state["history"] = history[-500:]
            if payload.get("merged"):
                _local_state["merged"].update(payload["merged"])
                print("[load] set merged temp_f from file:", _local_state["merged"].get("temp_f"))
            if payload.get("rain_day_key"):
                _local_state["rain_day_key"] = payload["rain_day_key"]
            if payload.get("rain_day_baseline_mm") is not None:
                _local_state["rain_day_baseline_mm"] = payload["rain_day_baseline_mm"]
            if payload.get("last_rain_mm") is not None:
                _local_state["last_rain_mm"] = payload["last_rain_mm"]
            elif payload.get("rain_mm_baseline") is not None:
                _local_state["rain_day_baseline_mm"] = payload["rain_mm_baseline"]
            if history:
                _local_state["last_ts"] = history[-1]["ts"]
                _local_state["online"] = True
            elif payload.get("merged"):
                # allow old history as initial stale data for dashboard
                _local_state["last_ts"] = int(time.time()) - 3600
                _local_state["online"] = False
        _sync_rain_day_state()
        print(f"[history] loaded {len(history)} samples")
    except Exception as e:
        print(f"[history] load failed: {e}")
    _publish_to_hud()


def _ingest_rtl433(payload: dict, topic: str):
    if not isinstance(payload, dict):
        return
    if ALLOWED_SENSOR_IDS and payload.get("id") is not None:
        if int(payload["id"]) not in ALLOWED_SENSOR_IDS:
            return
    model = str(payload.get("model") or payload.get("protocol") or "unknown")
    now = int(time.time())

    merged = {}
    if payload.get("temperature_C") is not None:
        new_temp = _c_to_f(payload["temperature_C"])
        with _local_lock:
            cur_temp = _local_state["merged"].get("temp_f")
            cur_ts   = _local_state["last_ts"]
        # Reject implausible single-step jumps (sensor reset, bad decode, neighbor collision)
        # 20°C / 68°F exactly is the AcuRite power-cycle default — always reject it
        if round(new_temp, 1) == 68.0:
            print(f"[filter] rejected temp={new_temp}F (sensor reset default 20C)", flush=True)
            return
        if cur_temp is not None and (now - cur_ts) < 600 and abs(new_temp - cur_temp) > 15:
            print(f"[filter] rejected temp={new_temp}F (jump {round(new_temp-cur_temp,1)}F from {cur_temp}F)", flush=True)
            return
        merged["temp_f"] = round(new_temp, 1)
    if payload.get("temperature_F") is not None:
        new_temp = round(float(payload["temperature_F"]), 1)
        with _local_lock:
            cur_temp = _local_state["merged"].get("temp_f")
            cur_ts   = _local_state["last_ts"]
        if new_temp == 68.0:
            print(f"[filter] rejected temp={new_temp}F (sensor reset default)", flush=True)
            return
        if cur_temp is not None and (now - cur_ts) < 600 and abs(new_temp - cur_temp) > 15:
            print(f"[filter] rejected temp={new_temp}F (jump {round(new_temp-cur_temp,1)}F from {cur_temp}F)", flush=True)
            return
        merged["temp_f"] = new_temp
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
            _update_daily_rain(merged, now, lock_held=True)

        snap = _snapshot_readings(_local_state["merged"])
        point["readings"] = snap
        _local_state["history"].append(point)
        cutoff = now - HISTORY_MAX_AGE
        _local_state["history"] = _normalize_history(
            [p for p in _local_state["history"] if p.get("ts", 0) >= cutoff]
        )[-500:]

    _save_history()
    _publish_to_hud()


def _snapshot_merged():
    with _local_lock:
        return dict(_local_state["merged"]), _local_state["last_ts"], list(_local_state["history"])


def _build_current_from_local():
    merged, last_ts, _ = _snapshot_merged()
    if not merged:
        return None
    temp_f = merged.get("temp_f")
    humidity = merged.get("humidity")
    wind_mph = merged.get("wind_mph")
    gust_mph = merged.get("wind_gust_mph") or wind_mph
    wind_dir = merged.get("wind_dir", 0)
    rain_daily = merged.get("rain_daily_in")
    rain_station = merged.get("rain_in")
    precip_rate = merged.get("precip_rate_in", 0.0) or 0.0
    dewpt = _compute_dewpoint_f(temp_f, humidity)
    heat_idx = _compute_heat_index_f(temp_f, humidity)

    pressure = None
    pressure_meta = {}
    try:
        nws_p = fetch_nws_pressure()
        pressure = nws_p["pressure_inhg"]
        pressure_meta = {
            "pressure_source": "nws",
            "pressure_station": nws_p.get("station_id"),
            "pressure_station_name": nws_p.get("station_name"),
        }
    except Exception as e:
        print(f"[nws] current pressure unavailable: {e}")

    # Dashboard must still work if NWS is briefly unavailable

    obs_local = time.localtime(last_ts)
    obs_time_local = time.strftime("%Y-%m-%d %H:%M:%S", obs_local)

    obs = {
        "_source": "weatherthief",
        "_fetched_at": int(time.time()),
        "stationID": STATION_ID,
        "gridsquare": GRIDSQUARE,
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
            "pressure": pressure,
            "precipRate": precip_rate,
            "precipDaily": rain_daily,
            "precipTotal": rain_daily,
            "precipStationTotal": rain_station,
            "elev": None,
        },
    }
    obs.update(pressure_meta)
    age = int(time.time() - last_ts)
    if age >= DATA_STALE_SEC:
        obs["_stale"] = True
        obs["_stale_age"] = age
    return obs


def _history_point_to_rapid(point: dict, pressure_hist=None):
    r = point.get("readings") or {}
    ts = point.get("ts", int(time.time()))
    temp_f = r.get("temp_f")
    humidity = r.get("humidity")
    wind_mph = r.get("wind_mph")
    gust_mph = r.get("wind_gust_mph") or wind_mph
    pressure = r.get("pressure_inhg")
    if pressure is None and pressure_hist:
        pressure = _pressure_at_ts(ts, pressure_hist)
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
            "pressureMax": pressure,
            "pressureMin": pressure,
            "pressure": pressure,
            "pressureTrend": 0.0,
            "precipRate": r.get("precip_rate_in", 0.0) or 0.0,
            "precipDaily": r.get("rain_daily_in"),
            "precipTotal": r.get("rain_daily_in"),
            "precipStationTotal": r.get("rain_in"),
        },
        "lat": STATION_LAT,
        "lon": STATION_LON,
    }


def _build_rapid_from_local():
    _, _, history = _snapshot_merged()
    history = _normalize_history(history)
    pressure_hist = fetch_nws_pressure_history()

    if not history:
        current = _build_current_from_local()
        if not current:
            return []
        return [_history_point_to_rapid({"ts": current["epoch"], "readings": {
            "temp_f": current["imperial"]["temp"],
            "humidity": current["humidity"],
            "wind_mph": current["imperial"]["windSpeed"],
            "wind_gust_mph": current["imperial"]["windGust"],
            "rain_in": current["imperial"].get("precipStationTotal"),
            "rain_daily_in": current["imperial"].get("precipDaily"),
            "precip_rate_in": current["imperial"]["precipRate"],
            "pressure_inhg": current["imperial"]["pressure"],
        }}, pressure_hist)]

    rapid = [_history_point_to_rapid(p, pressure_hist) for p in history]
    # Charts need rows with at least temperature or humidity
    chartable = [
        r for r in rapid
        if r["imperial"].get("tempAvg") is not None or r.get("humidityAvg") is not None
    ]
    return chartable if chartable else rapid


def _dashboard_payload():
    """Always WeatherThief local listener only (no WU)."""
    source = "weatherthief"
    current = _build_current_from_local()
    try:
        rapid = _build_rapid_from_local()
    except Exception as e:
        print(f"[rapid] build failed, using local history only: {e}")
        _, _, history = _snapshot_merged()
        rapid = [_history_point_to_rapid(p) for p in _normalize_history(history)]
    summary = compute_summary(rapid) if rapid else {}
    alerts = fetch_nws_alerts()
    local = _local_payload()

    if not current:
        merged, last_ts, hist = _snapshot_merged()
        if merged:
            current = _build_current_from_local() or {"_stale": True, "imperial": {}}
            if not rapid:
                rapid = [_history_point_to_rapid(p) for p in _normalize_history(hist)] if hist else []
            source = "stale-weatherthief"
        else:
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
    # Retained LWT may already say "online" — poke RF after subscribe anyway.
    threading.Timer(3.0, lambda: _maybe_enable_rf_after_online("app-connect")).start()


def _on_mqtt_disconnect(client, userdata, *args):
    reason_code = args[-1] if args else 0
    rc = getattr(reason_code, "value", reason_code)
    print(f"[mqtt] disconnected rc={rc}")
    # Do NOT manually reconnect here. paho's built-in auto-reconnect (reconnect_delay_set)
    # already handles recovery. Calling _reconnect_mqtt from on_disconnect competes with
    # paho's internal reconnect — both use the same client ID, so each new connection kicks
    # the previous one, causing another on_disconnect, creating an infinite storm that hammers
    # the CC1101 with RF enable commands every 2 seconds. Let paho handle it; the watchdog
    # will call _reconnect_mqtt only if MQTT is truly silent for >2 minutes.


def _touch_mqtt_rx():
    global _last_mqtt_rx_ts
    _last_mqtt_rx_ts = time.time()


def _on_mqtt_message(client, userdata, msg):
    global _lwt_was_online
    _touch_mqtt_rx()
    topic = msg.topic or ""
    payload_raw = msg.payload.decode("utf-8", "replace").strip()
    gateway_base = f"{MQTT_TOPIC_PREFIX.strip('/')}/{GATEWAY_NAME}"

    # Never process topics this app publishes — would create feedback loops
    if "/hud/" in topic:
        return

    if topic.endswith("/LWT"):
        online = payload_raw.lower() in ("online", "connected", "1", "true")
        with _local_lock:
            _local_state["online"] = online
        if online:
            if _lwt_was_online is not True:
                _maybe_enable_rf_after_online("lwt-transition-online")
                _apply_rssi_threshold("lwt-online", delay=15.0)
            else:
                _maybe_enable_rf_after_online("lwt-still-online")
        _lwt_was_online = online
        return

    if topic == f"{gateway_base}/SYStoMQTT":
        try:
            payload = json.loads(payload_raw)
        except json.JSONDecodeError:
            return
        if isinstance(payload, dict):
            uptime = int(payload.get("uptime") or 9999)
            if uptime < 180:
                with _local_lock:
                    _local_state["online"] = True
                _enable_weatherthief_rf(f"boot-uptime-{uptime}s", force=True)
                _apply_rssi_threshold(f"boot-uptime-{uptime}s", delay=5.0)
        return

    if "/RTL_433toMQTT" not in topic and "/rtl433" not in topic:
        # Loosen for native rtl_433 or other sensor JSON under wy6y/weather (has model/temp/rain)
        pr = payload_raw or ""
        if not any(k in pr.lower() for k in ("temperature", "model", "rain_mm", "wind")):
            return
    try:
        payload = json.loads(payload_raw)
    except json.JSONDecodeError:
        return
    if isinstance(payload, dict):
        _ingest_rtl433(payload, topic)


def _build_mqtt_client():
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
        client.reconnect_delay_set(min_delay=1, max_delay=30)
        client.on_connect = _on_mqtt_connect
        client.on_disconnect = _on_mqtt_disconnect
        client.on_message = _on_mqtt_message
        client.connect_async(MQTT_HOST, MQTT_PORT, 60)
        client.loop_start()
        _mqtt_client = client
        print(f"[mqtt] WeatherThief listener on {MQTT_HOST}:{MQTT_PORT}")
    except Exception as e:
        print(f"[mqtt] start failed: {e}")


def _start_mqtt():
    _build_mqtt_client()
    _start_rf_watchdog()


def _local_payload():
    with _local_lock:
        age = int(time.time() - _local_state["last_ts"]) if _local_state["last_ts"] else None
        m = dict(_local_state["merged"])
        return {
            "gateway": _local_state["gateway"],
            "online": _local_state["online"],
            "last_message": _local_state["last_message"],
            "last_age_sec": age,
            "readings": dict(_local_state["readings"]),
            "merged": m,
            "history": list(_local_state["history"][-20:]),
        }


def _publish_to_hud():
    """Push processed current/merged to hud topics so CyberHud (and others) get fresh data from the listener."""
    try:
        if _mqtt_client is None:
            return
        cur = _build_current_from_local() or {}
        mrg, _, _ = _snapshot_merged()
        if cur:
            _mqtt_client.publish("wy6y/weather/hud/current", json.dumps(cur), retain=True)
        if mrg:
            _mqtt_client.publish("wy6y/weather/hud/state", json.dumps(mrg), retain=True)
    except Exception as e:
        print(f"[hud pub] failed: {e}")


# ============== API ROUTES ==============

@app.route("/")
def index():
    prefix = request.headers.get("X-Forwarded-Prefix", "").rstrip("/")
    template_path = os.path.join(app.template_folder, "index.html")
    with open(template_path, "r", encoding="utf-8") as f:
        html = f.read()

    html = html.replace("{{ display_name }}", DISPLAY_NAME)
    html = html.replace("{{ neighborhood | upper }}", NEIGHBORHOOD.upper())
    html = html.replace("{{ url_prefix }}", prefix)

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
    prefix = request.headers.get("X-Forwarded-Prefix", "").rstrip("/")
    return jsonify({
        "id": "wy6y-weather",
        "name": "WY6Y Weather",
        "short_name": "WY6Y WX",
        "description": "Cyberpunk personal weather dashboard — live WeatherThief rtl_433 uplink",
        "start_url": f"{prefix}/?source=pwa" if prefix else "/?source=pwa",
        "scope": f"{prefix}/" if prefix else "/",
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
    _publish_to_hud()
    _start_mqtt()

    # Periodic push from listener itself so HUD stays fed with current good data (no external loop needed)
    def _periodic_push():
        while True:
            try:
                _publish_to_hud()
            except Exception:
                pass
            time.sleep(30)
    threading.Thread(target=_periodic_push, daemon=True).start()
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", 5000))
    print("╔════════════════════════════════════════════════════════════╗")
    print("║  WY6Y WEATHER  •  WEATHERTHIEF UPLINK  |  rtl_433         ║")
    print("║  Neon. Data. No corporate bullshit.                        ║")
    print("╚════════════════════════════════════════════════════════════╝")
    print(f"Station : {STATION_ID} ({DISPLAY_NAME})")
    print(f"Binding : {host}:{port}")
    print("Source  : WeatherThief MQTT (rtl_433 only, no WU)")
    print(f"Cache   : alerts={ALERTS_TTL}s  (local data live via MQTT)")
    print(f"MQTT    : {MQTT_HOST}:{MQTT_PORT}  topic={MQTT_TOPIC_PREFIX}/#  gateway={GATEWAY_NAME}")
    print("Ctrl-C to stop.\n")
    app.run(host=host, port=port, debug=False, threaded=True)