"""
SQLite archive for CyberPWS.

Keeps full-resolution samples (default 90 days), daily min/max rollups forever,
and all-time station records. Thread-safe via a single connection lock.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from datetime import datetime
from typing import Any, Optional

DEFAULT_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "weather.db")
SAMPLE_RETENTION_DAYS = int(os.getenv("WX_SAMPLE_RETENTION_DAYS", "90"))
PRUNE_INTERVAL_SEC = 3600

_lock = threading.Lock()
_conn: Optional[sqlite3.Connection] = None
_db_path = DEFAULT_DB_PATH
_last_prune_ts = 0.0


def _day_key(ts: float | int | None = None) -> str:
    ts = ts if ts is not None else time.time()
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


def init(db_path: str | None = None) -> None:
    global _conn, _db_path
    if db_path:
        _db_path = db_path
    with _lock:
        if _conn is not None:
            try:
                _conn.close()
            except Exception:
                pass
        _conn = sqlite3.connect(_db_path, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA synchronous=NORMAL")
        _conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS samples (
                ts INTEGER PRIMARY KEY,
                temp_f REAL,
                humidity REAL,
                wind_mph REAL,
                wind_gust_mph REAL,
                wind_dir REAL,
                rain_mm REAL,
                rain_daily_in REAL,
                precip_rate_in REAL,
                pressure_inhg REAL,
                battery_ok INTEGER,
                sensor_id INTEGER,
                rssi REAL,
                model TEXT,
                topic TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_samples_ts ON samples(ts);

            CREATE TABLE IF NOT EXISTS daily (
                day TEXT PRIMARY KEY,
                temp_min REAL,
                temp_max REAL,
                temp_min_ts INTEGER,
                temp_max_ts INTEGER,
                humidity_min REAL,
                humidity_max REAL,
                wind_gust_max REAL,
                wind_gust_max_ts INTEGER,
                rain_daily_in REAL,
                samples INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS records (
                key TEXT PRIMARY KEY,
                value REAL NOT NULL,
                ts INTEGER,
                day TEXT,
                meta TEXT
            );

            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT
            );
            """
        )
        _conn.commit()


def close() -> None:
    global _conn
    with _lock:
        if _conn is not None:
            _conn.close()
            _conn = None


def _require() -> sqlite3.Connection:
    if _conn is None:
        raise RuntimeError("weather_db not initialized — call weather_db.init()")
    return _conn


def insert_sample(point: dict) -> None:
    """Insert one history point (readings dict + ts). Updates daily + records."""
    readings = point.get("readings") or {}
    ts = int(point.get("ts") or time.time())
    row = {
        "ts": ts,
        "temp_f": readings.get("temp_f"),
        "humidity": readings.get("humidity"),
        "wind_mph": readings.get("wind_mph"),
        "wind_gust_mph": readings.get("wind_gust_mph"),
        "wind_dir": readings.get("wind_dir"),
        "rain_mm": readings.get("rain_mm"),
        "rain_daily_in": readings.get("rain_daily_in"),
        "precip_rate_in": readings.get("precip_rate_in"),
        "pressure_inhg": readings.get("pressure_inhg"),
        "battery_ok": readings.get("battery_ok"),
        "sensor_id": readings.get("sensor_id"),
        "rssi": readings.get("rssi"),
        "model": point.get("model"),
        "topic": point.get("topic"),
    }
    with _lock:
        conn = _require()
        conn.execute(
            """
            INSERT INTO samples (
                ts, temp_f, humidity, wind_mph, wind_gust_mph, wind_dir,
                rain_mm, rain_daily_in, precip_rate_in, pressure_inhg,
                battery_ok, sensor_id, rssi, model, topic
            ) VALUES (
                :ts, :temp_f, :humidity, :wind_mph, :wind_gust_mph, :wind_dir,
                :rain_mm, :rain_daily_in, :precip_rate_in, :pressure_inhg,
                :battery_ok, :sensor_id, :rssi, :model, :topic
            )
            ON CONFLICT(ts) DO UPDATE SET
                temp_f=excluded.temp_f,
                humidity=excluded.humidity,
                wind_mph=excluded.wind_mph,
                wind_gust_mph=excluded.wind_gust_mph,
                wind_dir=excluded.wind_dir,
                rain_mm=excluded.rain_mm,
                rain_daily_in=excluded.rain_daily_in,
                precip_rate_in=excluded.precip_rate_in,
                pressure_inhg=excluded.pressure_inhg,
                battery_ok=excluded.battery_ok,
                sensor_id=excluded.sensor_id,
                rssi=excluded.rssi,
                model=excluded.model,
                topic=excluded.topic
            """,
            row,
        )
        _update_daily_locked(conn, row)
        _update_records_locked(conn, row)
        conn.commit()
    maybe_prune()


def _update_daily_locked(conn: sqlite3.Connection, row: dict) -> None:
    day = _day_key(row["ts"])
    cur = conn.execute("SELECT * FROM daily WHERE day = ?", (day,)).fetchone()
    if cur is None:
        conn.execute(
            """
            INSERT INTO daily (
                day, temp_min, temp_max, temp_min_ts, temp_max_ts,
                humidity_min, humidity_max, wind_gust_max, wind_gust_max_ts,
                rain_daily_in, samples
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
            """,
            (
                day,
                row["temp_f"],
                row["temp_f"],
                row["ts"] if row["temp_f"] is not None else None,
                row["ts"] if row["temp_f"] is not None else None,
                row["humidity"],
                row["humidity"],
                row["wind_gust_mph"] if row["wind_gust_mph"] is not None else row["wind_mph"],
                row["ts"]
                if (row["wind_gust_mph"] is not None or row["wind_mph"] is not None)
                else None,
                row["rain_daily_in"],
            ),
        )
        return

    updates: dict[str, Any] = {"samples": (cur["samples"] or 0) + 1}
    tf = row["temp_f"]
    if tf is not None:
        if cur["temp_min"] is None or tf < cur["temp_min"]:
            updates["temp_min"] = tf
            updates["temp_min_ts"] = row["ts"]
        if cur["temp_max"] is None or tf > cur["temp_max"]:
            updates["temp_max"] = tf
            updates["temp_max_ts"] = row["ts"]
    hum = row["humidity"]
    if hum is not None:
        if cur["humidity_min"] is None or hum < cur["humidity_min"]:
            updates["humidity_min"] = hum
        if cur["humidity_max"] is None or hum > cur["humidity_max"]:
            updates["humidity_max"] = hum
    gust = row["wind_gust_mph"] if row["wind_gust_mph"] is not None else row["wind_mph"]
    if gust is not None:
        if cur["wind_gust_max"] is None or gust > cur["wind_gust_max"]:
            updates["wind_gust_max"] = gust
            updates["wind_gust_max_ts"] = row["ts"]
    if row["rain_daily_in"] is not None:
        prev = cur["rain_daily_in"]
        if prev is None or row["rain_daily_in"] > prev:
            updates["rain_daily_in"] = row["rain_daily_in"]

    if len(updates) == 1 and "samples" in updates:
        conn.execute("UPDATE daily SET samples = ? WHERE day = ?", (updates["samples"], day))
        return
    sets = ", ".join(f"{k} = ?" for k in updates)
    conn.execute(f"UPDATE daily SET {sets} WHERE day = ?", (*updates.values(), day))


def _record_set_locked(
    conn: sqlite3.Connection,
    key: str,
    value: float,
    ts: int,
    day: str,
    *,
    prefer_max: bool,
    meta: str | None = None,
) -> None:
    cur = conn.execute("SELECT value FROM records WHERE key = ?", (key,)).fetchone()
    if cur is None:
        conn.execute(
            "INSERT INTO records (key, value, ts, day, meta) VALUES (?, ?, ?, ?, ?)",
            (key, value, ts, day, meta),
        )
        return
    old = cur["value"]
    if prefer_max and value > old:
        conn.execute(
            "UPDATE records SET value=?, ts=?, day=?, meta=? WHERE key=?",
            (value, ts, day, meta, key),
        )
    elif not prefer_max and value < old:
        conn.execute(
            "UPDATE records SET value=?, ts=?, day=?, meta=? WHERE key=?",
            (value, ts, day, meta, key),
        )


def _update_records_locked(conn: sqlite3.Connection, row: dict) -> None:
    day = _day_key(row["ts"])
    ts = row["ts"]
    if row["temp_f"] is not None:
        _record_set_locked(conn, "temp_max_all", float(row["temp_f"]), ts, day, prefer_max=True)
        _record_set_locked(conn, "temp_min_all", float(row["temp_f"]), ts, day, prefer_max=False)
    if row["humidity"] is not None:
        _record_set_locked(conn, "humidity_max_all", float(row["humidity"]), ts, day, prefer_max=True)
        _record_set_locked(conn, "humidity_min_all", float(row["humidity"]), ts, day, prefer_max=False)
    gust = row["wind_gust_mph"] if row["wind_gust_mph"] is not None else row["wind_mph"]
    if gust is not None:
        _record_set_locked(conn, "gust_max_all", float(gust), ts, day, prefer_max=True)
    if row["rain_daily_in"] is not None:
        _record_set_locked(
            conn, "rain_day_max_all", float(row["rain_daily_in"]), ts, day, prefer_max=True
        )
    if row["precip_rate_in"] is not None:
        _record_set_locked(
            conn, "precip_rate_max_all", float(row["precip_rate_in"]), ts, day, prefer_max=True
        )


def get_daily(day: str | None = None) -> Optional[dict]:
    day = day or _day_key()
    with _lock:
        conn = _require()
        row = conn.execute("SELECT * FROM daily WHERE day = ?", (day,)).fetchone()
        if not row:
            return None
        return dict(row)


def get_daily_history(limit: int = 30) -> list[dict]:
    with _lock:
        conn = _require()
        rows = conn.execute(
            "SELECT * FROM daily ORDER BY day DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def get_records() -> dict[str, dict]:
    with _lock:
        conn = _require()
        rows = conn.execute("SELECT * FROM records").fetchall()
        return {r["key"]: dict(r) for r in rows}


def get_samples_since(since_ts: int, limit: int = 5000) -> list[dict]:
    with _lock:
        conn = _require()
        rows = conn.execute(
            """
            SELECT * FROM samples
            WHERE ts >= ?
            ORDER BY ts ASC
            LIMIT ?
            """,
            (since_ts, limit),
        ).fetchall()
        return [dict(r) for r in rows]


def sample_count() -> int:
    with _lock:
        conn = _require()
        row = conn.execute("SELECT COUNT(*) AS n FROM samples").fetchone()
        return int(row["n"] if row else 0)


def stats() -> dict:
    with _lock:
        conn = _require()
        n = conn.execute("SELECT COUNT(*) AS n FROM samples").fetchone()["n"]
        first = conn.execute("SELECT MIN(ts) AS t FROM samples").fetchone()["t"]
        last = conn.execute("SELECT MAX(ts) AS t FROM samples").fetchone()["t"]
        days = conn.execute("SELECT COUNT(*) AS n FROM daily").fetchone()["n"]
        return {
            "path": _db_path,
            "samples": n,
            "first_ts": first,
            "last_ts": last,
            "daily_days": days,
            "retention_days": SAMPLE_RETENTION_DAYS,
        }


def maybe_prune(force: bool = False) -> int:
    global _last_prune_ts
    now = time.time()
    if not force and now - _last_prune_ts < PRUNE_INTERVAL_SEC:
        return 0
    cutoff = int(now - SAMPLE_RETENTION_DAYS * 86400)
    with _lock:
        conn = _require()
        cur = conn.execute("DELETE FROM samples WHERE ts < ?", (cutoff,))
        conn.commit()
        deleted = cur.rowcount if cur.rowcount is not None else 0
    _last_prune_ts = now
    if deleted:
        print(f"[weather_db] pruned {deleted} samples older than {SAMPLE_RETENTION_DAYS}d")
    return deleted


def migrate_from_json_history(history: list[dict]) -> int:
    """One-shot import of weatherthief_history.json points. Skips existing ts."""
    if not history:
        return 0
    inserted = 0
    with _lock:
        conn = _require()
        for point in history:
            readings = point.get("readings") or {}
            ts = int(point.get("ts") or 0)
            if not ts:
                continue
            exists = conn.execute("SELECT 1 FROM samples WHERE ts = ?", (ts,)).fetchone()
            if exists:
                continue
            row = {
                "ts": ts,
                "temp_f": readings.get("temp_f"),
                "humidity": readings.get("humidity"),
                "wind_mph": readings.get("wind_mph"),
                "wind_gust_mph": readings.get("wind_gust_mph"),
                "wind_dir": readings.get("wind_dir"),
                "rain_mm": readings.get("rain_mm"),
                "rain_daily_in": readings.get("rain_daily_in"),
                "precip_rate_in": readings.get("precip_rate_in"),
                "pressure_inhg": readings.get("pressure_inhg"),
                "battery_ok": readings.get("battery_ok"),
                "sensor_id": readings.get("sensor_id"),
                "rssi": readings.get("rssi"),
                "model": point.get("model"),
                "topic": point.get("topic"),
            }
            conn.execute(
                """
                INSERT INTO samples (
                    ts, temp_f, humidity, wind_mph, wind_gust_mph, wind_dir,
                    rain_mm, rain_daily_in, precip_rate_in, pressure_inhg,
                    battery_ok, sensor_id, rssi, model, topic
                ) VALUES (
                    :ts, :temp_f, :humidity, :wind_mph, :wind_gust_mph, :wind_dir,
                    :rain_mm, :rain_daily_in, :precip_rate_in, :pressure_inhg,
                    :battery_ok, :sensor_id, :rssi, :model, :topic
                )
                """,
                row,
            )
            _update_daily_locked(conn, row)
            _update_records_locked(conn, row)
            inserted += 1
        conn.commit()
    if inserted:
        print(f"[weather_db] migrated {inserted} samples from JSON history")
    return inserted


def set_meta(key: str, value: str) -> None:
    with _lock:
        conn = _require()
        conn.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        conn.commit()


def get_meta(key: str, default: str | None = None) -> str | None:
    with _lock:
        conn = _require()
        row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default


def sample_to_history_point(row: dict) -> dict:
    """Convert a samples row into the in-memory history point shape."""
    readings = {
        k: row.get(k)
        for k in (
            "temp_f",
            "humidity",
            "wind_mph",
            "wind_gust_mph",
            "wind_dir",
            "rain_mm",
            "rain_daily_in",
            "precip_rate_in",
            "pressure_inhg",
            "battery_ok",
            "sensor_id",
            "rssi",
        )
        if row.get(k) is not None
    }
    return {
        "ts": row["ts"],
        "topic": row.get("topic") or "",
        "model": row.get("model") or "Acurite-5n1",
        "readings": readings,
    }
