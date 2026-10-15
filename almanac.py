"""
CyberPunk Farmers Almanac — wit, wisdom, and provisional climate.

Old Farmer tone with a little neon. Station-young baselines are honest;
NWS climate normals fill the gaps until Volume II (and beyond) exists.
"""

from __future__ import annotations

import math
import random
import time
from datetime import date, datetime, timedelta
from typing import Any, Callable, Optional

# ---------------------------------------------------------------------------
# NWS / climate reference for Edmond–OKC area (approx. NOAA 1991–2020 normals)
# Not our roof. Labeled as such everywhere in the UI.
# ---------------------------------------------------------------------------
NWS_MONTHLY_NORMALS = {
    1: {"high_f": 48.0, "low_f": 27.0, "rain_in": 1.28, "name": "January"},
    2: {"high_f": 53.0, "low_f": 31.0, "rain_in": 1.56, "name": "February"},
    3: {"high_f": 62.0, "low_f": 39.0, "rain_in": 2.87, "name": "March"},
    4: {"high_f": 71.0, "low_f": 48.0, "rain_in": 3.15, "name": "April"},
    5: {"high_f": 79.0, "low_f": 58.0, "rain_in": 4.92, "name": "May"},
    6: {"high_f": 87.0, "low_f": 67.0, "rain_in": 4.40, "name": "June"},
    7: {"high_f": 93.0, "low_f": 71.0, "rain_in": 2.94, "name": "July"},
    8: {"high_f": 92.0, "low_f": 70.0, "rain_in": 2.95, "name": "August"},
    9: {"high_f": 84.0, "low_f": 62.0, "rain_in": 3.66, "name": "September"},
    10: {"high_f": 73.0, "low_f": 50.0, "rain_in": 3.41, "name": "October"},
    11: {"high_f": 60.0, "low_f": 38.0, "rain_in": 2.08, "name": "November"},
    12: {"high_f": 50.0, "low_f": 29.0, "rain_in": 1.82, "name": "December"},
}

CREEK_FOOTERS = [
    "The creek does not care about your forecast confidence interval.",
    "The creek does not care. It was here before the mesh network.",
    "The creek does not care about your unit toggle. Water is water.",
    "The creek does not care. It has seen worse Julys.",
    "The creek does not care about Volume I being incomplete.",
    "The creek does not care. Plant anyway, or don't. The creek remains.",
    "The creek does not care about Tailscale latency.",
    "The creek does not care. Neon fades; silt remembers.",
    "The creek does not care about your phone's weather app.",
    "The creek does not care. Chisholm still runs when the MQTT broker hiccups.",
]

STATIC_PROVERBS = [
    "Never trust a calm July afternoon in Oklahoma.",
    "When the wind dies and the sky goes green, stop gardening and get indoors.",
    "A dry August will make a liar of every green lawn advertisement.",
    "The first freeze always arrives on the day you leave the hose outside.",
    "Rain predicted for Tuesday is a suggestion, not a contract.",
    "If the humidity is higher than your IQ claim, stay hydrated and humble.",
    "Plant by the moon if you like; water by the sensor if you want results.",
    "A north wind after a front is nature's way of resetting the patio furniture.",
    "Dust is just soil that lost hope. Give it rain or give it mulch.",
    "The almanac is patient. Your tomatoes are not.",
    "When thrushes hush and pressure falls, the sky is writing a memo.",
    "Measure twice, irrigate once, and never argue with a wet bulb.",
]

ORACLE_LINES = [
    "When the Deco mesh runs cooler than the patio, outdoor dining has been vetoed by thermodynamics.",
    "The old ones say: if the radar is empty and the lawn is brittle, believe the lawn.",
    "Plant root crops on the waning moon — or whenever you remember the seed packet.",
    "A full moon over Brasswood does not water the garden. You still have to turn the faucet.",
    "If the station archive is shorter than a summer squash vine, cite NWS and smile.",
    "Folklore claims bees work harder before rain. Your AcuRite works the same either way.",
    "The stars do not owe you a cool front. Neither does the National Weather Service.",
    "On nights when the grid square hums and the humidity sulks, the air is keeping secrets.",
]

# Light ham / RF asides — flavor only, not a propagation forecast product.
HAM_ASIDES = [
    {
        "when": "humid_hot",
        "text": "Thick air, short skip on VHF gossip — the 2m net still hears you if the repeater does.",
    },
    {
        "when": "dry_cool",
        "text": "Dry air, clean signals: a good night to listen before you transmit.",
    },
    {
        "when": "stormy",
        "text": "Storm cells nearby: unplug the shiny antennas if thunder is bargaining.",
    },
    {
        "when": "quiet",
        "text": "Quiet band weather on the ground often means quiet bands above — or not. HF is a trickster.",
    },
    {
        "when": "default",
        "text": "Propagation is a guest at this almanac, not the landlord. See sdr.wy6y.net for the real work.",
    },
]


def _safe_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        f = float(v)
        if math.isnan(f) or math.isinf(f):
            return None
        return f
    except (TypeError, ValueError):
        return None


def _parse_day(day_str: str) -> Optional[date]:
    try:
        return datetime.strptime(day_str, "%Y-%m-%d").date()
    except Exception:
        return None


def meteorological_season(d: date) -> dict:
    """Meteorological seasons (NH): DJF / MAM / JJA / SON."""
    m = d.month
    if m in (12, 1, 2):
        key, name, color = "winter", "Winter", "cyan"
        start_m, end_m = (12, 2)
    elif m in (3, 4, 5):
        key, name, color = "spring", "Spring", "green"
        start_m, end_m = (3, 5)
    elif m in (6, 7, 8):
        key, name, color = "summer", "Summer", "yellow"
        start_m, end_m = (6, 8)
    else:
        key, name, color = "fall", "Fall", "magenta"
        start_m, end_m = (9, 11)

    year = d.year
    if key == "winter":
        # Winter spans years: Dec of prior → Feb
        if m == 12:
            start = date(year, 12, 1)
            end = date(year + 1, 2, 28 if (year + 1) % 4 else 29)
            # simplify end
            end = date(year + 1, 3, 1) - timedelta(days=1)
        else:
            start = date(year - 1, 12, 1)
            end = date(year, 3, 1) - timedelta(days=1)
    else:
        start = date(year, start_m, 1)
        if end_m == 12:
            end = date(year, 12, 31)
        else:
            end = date(year, end_m + 1, 1) - timedelta(days=1)

    total = (end - start).days + 1
    elapsed = (d - start).days + 1
    elapsed = max(1, min(elapsed, total))
    pct = round(100.0 * elapsed / total, 1)
    return {
        "key": key,
        "name": name,
        "color": color,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "day_in_season": elapsed,
        "days_in_season": total,
        "pct_complete": pct,
        "label": f"Day {elapsed} of meteorological {name.lower()}",
    }


def day_of_year_info(d: date) -> dict:
    doy = d.timetuple().tm_yday
    # leap-aware year length
    year_len = 366 if (d.year % 4 == 0 and (d.year % 100 != 0 or d.year % 400 == 0)) else 365
    return {
        "doy": doy,
        "year_len": year_len,
        "pct_year": round(100.0 * doy / year_len, 1),
        "label": f"Day {doy} of {d.year}",
    }


def moon_phase(d: date) -> dict:
    """Approximate moon phase (known simple algorithm). Folklore optional."""
    # Days since known new moon: 2000-01-06
    known = date(2000, 1, 6)
    synodic = 29.53058867
    days = (d - known).days + 0.5  # noon-ish
    age = days % synodic
    frac = age / synodic
    illum = (1 - math.cos(2 * math.pi * frac)) / 2 * 100.0

    if age < 1.84566:
        name, icon = "New Moon", "●"
        lore = "New moon: some plant leafy starts. Others wait for the weekend like sensible people."
    elif age < 5.53699:
        name, icon = "Waxing Crescent", "☽"
        lore = "Waxing crescent: folklore says above-ground crops stretch toward the light."
    elif age < 9.22831:
        name, icon = "First Quarter", "◐"
        lore = "First quarter: half lit, half dark — like every Oklahoma forecast."
    elif age < 12.91963:
        name, icon = "Waxing Gibbous", "◕"
        lore = "Waxing gibbous: good week to fix the hose before the next dry spell argues."
    elif age < 16.61096:
        name, icon = "Full Moon", "○"
        lore = "Full moon: plant by romance if you must; irrigate by sensor if you want dinner."
    elif age < 20.30228:
        name, icon = "Waning Gibbous", "◔"
        lore = "Waning gibbous: old books say root crops. Modern books say 'check the soil.'"
    elif age < 23.99361:
        name, icon = "Last Quarter", "◑"
        lore = "Last quarter: prune dead wood, not living hope."
    else:
        name, icon = "Waning Crescent", "☾"
        lore = "Waning crescent: rest the garden, rest the operator, trust the archive."

    days_to_full = (14.765 - age) % synodic
    days_to_new = (synodic - age) % synodic
    next_full = d + timedelta(days=int(round(days_to_full)))
    next_new = d + timedelta(days=int(round(days_to_new)))

    return {
        "name": name,
        "icon": icon,
        "age_days": round(age, 2),
        "illumination_pct": round(illum, 1),
        "next_full": next_full.isoformat(),
        "next_new": next_new.isoformat(),
        "lore": lore,
        "disclaimer": "Moon math is astronomy. Planting rules are folklore. Both are labeled.",
    }


def volume_info(archive_start: Optional[date], today: date) -> dict:
    """Volume number = calendar years of archive span (at least I)."""
    if not archive_start:
        years = 1
        incomplete = True
        joke = (
            "Volume I has not yet learned what a year feels like. "
            "It has opinions anyway — borrowed politely from the National Weather Service."
        )
    else:
        years = max(1, today.year - archive_start.year + 1)
        # "complete" only if we span a full prior calendar year with data
        incomplete = years < 2 or (today - archive_start).days < 365
        if incomplete:
            joke = (
                f"Volume {'I' * min(years, 3) if years <= 3 else years} is incomplete, and that is the joke. "
                f"The roof has spoken for {(today - archive_start).days} days — not decades. "
                "Until we grow our own climate memory, we borrow the National Weather Service's "
                "and label the loan in plain sight."
            )
        else:
            joke = (
                "The archive has teeth now. Station normals grow; NWS remains a polite guest column."
            )

    roman = {1: "I", 2: "II", 3: "III", 4: "IV", 5: "V"}.get(years, str(years))
    return {
        "number": years,
        "roman": roman,
        "label": f"VOL. {roman} · {today.year}",
        "incomplete": incomplete,
        "joke": joke,
        "masthead": "THE CYBERPUNK FARMERS ALMANAC",
        "subtitle": "Chisholm Creek Edition · Plant by data, pray by neon",
        "copyright": f"© {today.year} WY6Y Weather Trust · Brasswood / EM15fo",
        "barcode": "EM15fo",
    }


def _sorted_days(daily_rows: list[dict]) -> list[dict]:
    rows = [r for r in daily_rows if r.get("day")]
    rows.sort(key=lambda r: r["day"])
    return rows


def archive_meta(daily_rows: list[dict], db_stats: dict) -> dict:
    rows = _sorted_days(daily_rows)
    first = _parse_day(rows[0]["day"]) if rows else None
    last = _parse_day(rows[-1]["day"]) if rows else None
    n = len(rows)
    span = (last - first).days + 1 if first and last else 0
    return {
        "daily_days": n,
        "first_day": first.isoformat() if first else None,
        "last_day": last.isoformat() if last else None,
        "span_days": span,
        "samples": db_stats.get("samples"),
        "baseline_label": f"BASELINE: STATION-YOUNG · N={n} days" if n < 365 else f"BASELINE: STATION · N={n} days",
        "provisional": n < 365,
        "note": (
            "Station climatology is provisional until a full year of roof-truth exists. "
            "Reference column uses NWS-area monthly normals (OKC / central Oklahoma)."
            if n < 365
            else "Station archive spans a year or more. NWS normals remain available for comparison."
        ),
    }


def rain_book(daily_rows: list[dict], today: date) -> dict:
    rows = _sorted_days(daily_rows)
    ytd = [r for r in rows if (d := _parse_day(r["day"])) and d.year == today.year and d <= today]
    ytd_rain = sum((_safe_float(r.get("rain_daily_in")) or 0.0) for r in ytd)
    wet_days = sum(1 for r in ytd if (_safe_float(r.get("rain_daily_in")) or 0.0) >= 0.01)

    # Monthly station rain this year
    by_month: dict[int, float] = {}
    for r in ytd:
        d = _parse_day(r["day"])
        if not d:
            continue
        by_month[d.month] = by_month.get(d.month, 0.0) + (_safe_float(r.get("rain_daily_in")) or 0.0)

    # Dry streak ending today (or last data day)
    dry_streak = 0
    for r in reversed(rows):
        rain = _safe_float(r.get("rain_daily_in")) or 0.0
        if rain < 0.01:
            dry_streak += 1
        else:
            break

    # Longest dry / wet streaks in archive
    longest_dry = longest_wet = cur_dry = cur_wet = 0
    for r in rows:
        rain = _safe_float(r.get("rain_daily_in")) or 0.0
        if rain < 0.01:
            cur_dry += 1
            cur_wet = 0
            longest_dry = max(longest_dry, cur_dry)
        else:
            cur_wet += 1
            cur_dry = 0
            longest_wet = max(longest_wet, cur_wet)

    # Drought comic scale
    if dry_streak >= 30:
        severity, severity_label = "PRAYER_CIRCUIT", "Prayer circuit"
    elif dry_streak >= 21:
        severity, severity_label = "CRACKED", "Cracked"
    elif dry_streak >= 14:
        severity, severity_label = "BRITTLE", "Brittle"
    elif dry_streak >= 7:
        severity, severity_label = "DUST", "Dust"
    elif dry_streak >= 3:
        severity, severity_label = "THIRSTY", "Thirsty"
    else:
        severity, severity_label = "DAMP_ENOUGH", "Damp enough (for now)"

    # NWS YTD normal (sum of monthly * fraction of month elapsed) — simple monthly sum for months complete + pro-rate current
    nws_ytd = 0.0
    for m in range(1, today.month):
        nws_ytd += NWS_MONTHLY_NORMALS[m]["rain_in"]
    # pro-rate current month
    days_in_month = (date(today.year + (1 if today.month == 12 else 0),
                          1 if today.month == 12 else today.month + 1, 1) - timedelta(days=1)).day
    nws_ytd += NWS_MONTHLY_NORMALS[today.month]["rain_in"] * (today.day / days_in_month)

    month_normal = NWS_MONTHLY_NORMALS[today.month]["rain_in"]
    month_station = by_month.get(today.month, 0.0)

    wettest = max(ytd, key=lambda r: _safe_float(r.get("rain_daily_in")) or 0.0) if ytd else None
    driest_month = None
    wettest_month = None
    if by_month:
        driest_month = min(by_month.items(), key=lambda kv: kv[1])
        wettest_month = max(by_month.items(), key=lambda kv: kv[1])

    return {
        "ytd_in": round(ytd_rain, 2),
        "nws_ytd_normal_in": round(nws_ytd, 2),
        "ytd_vs_nws_delta_in": round(ytd_rain - nws_ytd, 2),
        "wet_days": wet_days,
        "dry_streak_days": dry_streak,
        "longest_dry_streak": longest_dry,
        "longest_wet_streak": longest_wet,
        "drought_severity": severity,
        "drought_label": severity_label,
        "month_station_in": round(month_station, 2),
        "month_nws_normal_in": month_normal,
        "by_month": {str(k): round(v, 2) for k, v in sorted(by_month.items())},
        "wettest_day": {
            "day": wettest.get("day") if wettest else None,
            "rain_in": _safe_float(wettest.get("rain_daily_in")) if wettest else None,
        },
        "wettest_month": (
            {"month": wettest_month[0], "rain_in": round(wettest_month[1], 2)} if wettest_month else None
        ),
        "driest_month": (
            {"month": driest_month[0], "rain_in": round(driest_month[1], 2)} if driest_month else None
        ),
    }


def phenology(daily_rows: list[dict], today: date) -> dict:
    rows = _sorted_days(daily_rows)
    year_rows = [r for r in rows if (d := _parse_day(r["day"])) and d.year == today.year]

    def first_where(pred) -> Optional[dict]:
        for r in year_rows:
            if pred(r):
                return r
        return None

    first_freeze = first_where(lambda r: (_safe_float(r.get("temp_min")) or 99) <= 32)
    last_freeze = None
    for r in year_rows:
        if (_safe_float(r.get("temp_min")) or 99) <= 32:
            last_freeze = r
    first_90 = first_where(lambda r: (_safe_float(r.get("temp_max")) or 0) >= 90)
    first_100 = first_where(lambda r: (_safe_float(r.get("temp_max")) or 0) >= 100)
    first_rain = first_where(lambda r: (_safe_float(r.get("rain_daily_in")) or 0) >= 0.01)

    # count of 100° days this year
    days_100 = sum(1 for r in year_rows if (_safe_float(r.get("temp_max")) or 0) >= 100)
    days_90 = sum(1 for r in year_rows if (_safe_float(r.get("temp_max")) or 0) >= 90)
    freeze_days = sum(1 for r in year_rows if (_safe_float(r.get("temp_min")) or 99) <= 32)

    items = [
        {
            "id": "first_90",
            "label": "First 90°F day",
            "done": first_90 is not None,
            "day": first_90.get("day") if first_90 else None,
            "detail": f"{_safe_float(first_90.get('temp_max')):.0f}°F" if first_90 else "Not yet this year",
        },
        {
            "id": "first_100",
            "label": "First 100°F day",
            "done": first_100 is not None,
            "day": first_100.get("day") if first_100 else None,
            "detail": f"{_safe_float(first_100.get('temp_max')):.0f}°F" if first_100 else "Not yet this year",
        },
        {
            "id": "days_100",
            "label": "100°F days this year",
            "done": days_100 > 0,
            "day": None,
            "detail": f"{days_100} day(s) · 90°F days: {days_90}",
        },
        {
            "id": "first_freeze",
            "label": "First freeze (≤32°F)",
            "done": first_freeze is not None,
            "day": first_freeze.get("day") if first_freeze else None,
            "detail": (
                f"{_safe_float(first_freeze.get('temp_min')):.0f}°F"
                if first_freeze
                else "Garden still negotiates with summer"
            ),
        },
        {
            "id": "last_freeze",
            "label": "Last freeze (season)",
            "done": last_freeze is not None,
            "day": last_freeze.get("day") if last_freeze else None,
            "detail": (
                f"{_safe_float(last_freeze.get('temp_min')):.0f}°F"
                if last_freeze
                else "No freeze logged this calendar year"
            ),
        },
        {
            "id": "first_rain",
            "label": "First measurable rain (year)",
            "done": first_rain is not None,
            "day": first_rain.get("day") if first_rain else None,
            "detail": (
                f"{_safe_float(first_rain.get('rain_daily_in')):.2f}\""
                if first_rain
                else "Still waiting on the sky's first receipt"
            ),
        },
        {
            "id": "freeze_days",
            "label": "Freeze days this year",
            "done": freeze_days > 0,
            "day": None,
            "detail": f"{freeze_days} day(s) with low ≤32°F",
        },
    ]

    return {
        "items": items,
        "counts": {
            "days_90": days_90,
            "days_100": days_100,
            "freeze_days": freeze_days,
        },
    }


def this_day_history(daily_rows: list[dict], today: date) -> dict:
    """Same calendar date across archive years + recent window."""
    rows = _sorted_days(daily_rows)
    same_md = []
    for r in rows:
        d = _parse_day(r["day"])
        if d and d.month == today.month and d.day == today.day:
            same_md.append(r)

    # last 7 / 30 days averages for "context"
    def window(n: int) -> dict:
        cutoff = today - timedelta(days=n - 1)
        w = [r for r in rows if (d := _parse_day(r["day"])) and cutoff <= d <= today]
        highs = [_safe_float(r.get("temp_max")) for r in w if _safe_float(r.get("temp_max")) is not None]
        lows = [_safe_float(r.get("temp_min")) for r in w if _safe_float(r.get("temp_min")) is not None]
        rains = [_safe_float(r.get("rain_daily_in")) or 0.0 for r in w]
        return {
            "n": len(w),
            "avg_high_f": round(sum(highs) / len(highs), 1) if highs else None,
            "avg_low_f": round(sum(lows) / len(lows), 1) if lows else None,
            "rain_in": round(sum(rains), 2),
        }

    today_row = next((r for r in rows if r.get("day") == today.isoformat()), None)
    nws = NWS_MONTHLY_NORMALS[today.month]

    return {
        "date": today.isoformat(),
        "today": {
            "temp_max": _safe_float(today_row.get("temp_max")) if today_row else None,
            "temp_min": _safe_float(today_row.get("temp_min")) if today_row else None,
            "rain_daily_in": _safe_float(today_row.get("rain_daily_in")) if today_row else None,
            "wind_gust_max": _safe_float(today_row.get("wind_gust_max")) if today_row else None,
            "humidity_min": _safe_float(today_row.get("humidity_min")) if today_row else None,
            "humidity_max": _safe_float(today_row.get("humidity_max")) if today_row else None,
        } if today_row else None,
        "on_this_date": [
            {
                "day": r.get("day"),
                "temp_max": _safe_float(r.get("temp_max")),
                "temp_min": _safe_float(r.get("temp_min")),
                "rain_daily_in": _safe_float(r.get("rain_daily_in")),
            }
            for r in same_md
        ],
        "on_this_date_note": (
            "The almanac has no memory of this calendar date before the archive began — "
            "and it will not pretend otherwise. Patience is a station virtue."
            if len(same_md) <= 1
            else f"{len(same_md)} station observations on this calendar date."
        ),
        "last_7": window(7),
        "last_30": window(30),
        "nws_month_normal": {
            "month": nws["name"],
            "avg_high_f": nws["high_f"],
            "avg_low_f": nws["low_f"],
            "rain_in": nws["rain_in"],
            "source": "NWS-area monthly climate normals (OKC / central OK) — not station roof data",
        },
    }


def month_heatmap(daily_rows: list[dict], year: int, month: int) -> dict:
    rows = {
        r["day"]: r
        for r in daily_rows
        if r.get("day") and r["day"].startswith(f"{year:04d}-{month:02d}-")
    }
    if month == 12:
        next_month = date(year + 1, 1, 1)
    else:
        next_month = date(year, month + 1, 1)
    first = date(year, month, 1)
    n_days = (next_month - first).days
    cells = []
    highs = []
    for day_n in range(1, n_days + 1):
        d = date(year, month, day_n)
        key = d.isoformat()
        r = rows.get(key)
        high = _safe_float(r.get("temp_max")) if r else None
        low = _safe_float(r.get("temp_min")) if r else None
        rain = _safe_float(r.get("rain_daily_in")) if r else None
        if high is not None:
            highs.append(high)
        cells.append({
            "day": key,
            "dom": day_n,
            "temp_max": high,
            "temp_min": low,
            "rain_daily_in": rain,
            "has_data": r is not None,
        })
    # color scale anchors
    hmin = min(highs) if highs else None
    hmax = max(highs) if highs else None
    return {
        "year": year,
        "month": month,
        "month_name": NWS_MONTHLY_NORMALS[month]["name"],
        "cells": cells,
        "high_min": hmin,
        "high_max": hmax,
        "nws_normal": NWS_MONTHLY_NORMALS[month],
    }


def wind_ledger(daily_rows: list[dict], today: date) -> dict:
    rows = _sorted_days(daily_rows)
    year_rows = [r for r in rows if (d := _parse_day(r["day"])) and d.year == today.year]
    gusts = [
        (r, _safe_float(r.get("wind_gust_max")))
        for r in year_rows
        if _safe_float(r.get("wind_gust_max")) is not None
    ]
    peak = max(gusts, key=lambda x: x[1]) if gusts else None
    days_20 = sum(1 for _, g in gusts if g >= 20)
    days_30 = sum(1 for _, g in gusts if g >= 30)
    return {
        "peak_gust_mph": peak[1] if peak else None,
        "peak_gust_day": peak[0].get("day") if peak else None,
        "days_gust_ge_20": days_20,
        "days_gust_ge_30": days_30,
    }


def counsel_and_oracle(
    live: dict,
    rain: dict,
    today_hist: dict,
    season: dict,
    moon: dict,
    archive: dict,
) -> dict:
    """Three tiers: FACT / COUNSEL / ORACLE — never mixed in one sentence."""
    temp = _safe_float(live.get("temp_f"))
    hum = _safe_float(live.get("humidity"))
    gust = _safe_float(live.get("wind_gust_mph")) or _safe_float(live.get("wind_mph"))
    rain_today = _safe_float((today_hist.get("today") or {}).get("rain_daily_in"))
    dry = rain.get("dry_streak_days") or 0

    facts = []
    if temp is not None:
        facts.append(f"Live air: {temp:.0f}°F.")
    if hum is not None:
        facts.append(f"Humidity: {hum:.0f}%.")
    if gust is not None:
        facts.append(f"Wind (gust or speed): {gust:.0f} mph.")
    facts.append(f"Dry streak: {dry} day(s) without 0.01\".")
    facts.append(f"YTD rain at the roof: {rain.get('ytd_in', 0):.2f}\" (NWS-area YTD normal ≈ {rain.get('nws_ytd_normal_in', 0):.2f}\").")
    if archive.get("provisional"):
        facts.append(archive.get("baseline_label") or "Station baseline provisional.")

    # Counsel — operational, mild humor
    counsel_bits = []
    if temp is not None and temp >= 100:
        counsel_bits.append("Triple digits: shade the living, water the rooted, spare the operator.")
    elif temp is not None and temp >= 95:
        counsel_bits.append("Heat is not a suggestion. Morning chores only; afternoon belongs to the cicadas.")
    elif temp is not None and temp <= 32:
        counsel_bits.append("Freeze on the sensor: cover the tender or write their gentle goodbye.")
    elif temp is not None and temp <= 38:
        counsel_bits.append("Near freeze: bring in the basil diplomacy before midnight.")

    if hum is not None and hum <= 25:
        counsel_bits.append("Air is arid. Mulch and irrigation beat wishful thinking.")
    elif hum is not None and hum >= 85 and temp is not None and temp >= 80:
        counsel_bits.append("Soup air. Pace yourself; metal tools stay hot longer than pride.")

    if dry >= 14:
        counsel_bits.append("Two weeks dry: run the hose with intent, not guilt.")
    elif dry >= 7:
        counsel_bits.append("A week without measurable rain — the lawn is cosplaying dust.")
    elif rain_today is not None and rain_today >= 0.5:
        counsel_bits.append("Good rain on the ledger. Skip the sprinkler; let the soil finish its work.")

    if gust is not None and gust >= 30:
        counsel_bits.append("Gusty: stake the tall plants, secure the light furniture, mind the antennas.")
    elif gust is not None and gust >= 20:
        counsel_bits.append("Breezy enough to scatter empty pots. Clip what you care about.")

    if not counsel_bits:
        if season["key"] == "summer":
            counsel_bits.append("Ordinary summer duties: water deep, less often; watch the western sky after lunch.")
        elif season["key"] == "winter":
            counsel_bits.append("Winter counsel: protect pipes, spare the tropical fantasies.")
        elif season["key"] == "spring":
            counsel_bits.append("Spring: plant with one eye on the freeze calendar and one on the seed packet.")
        else:
            counsel_bits.append("Fall: harvest what you can; frost keeps its own appointment book.")

    # Oracle — folklore, clearly tagged
    seed = f"{today_hist.get('date')}-{moon.get('name')}-{season.get('key')}"
    rng = random.Random(seed)
    oracle = rng.choice(ORACLE_LINES)
    proverb = rng.choice(STATIC_PROVERBS)
    # lightly template one proverb with data
    if temp is not None:
        templated = f"Today's live reading is {temp:.0f}°F. The archive calls that a data point — not a destiny."
    else:
        templated = proverb

    # Ham aside
    if temp is not None and hum is not None and temp >= 88 and hum >= 60:
        ham_key = "humid_hot"
    elif temp is not None and hum is not None and temp <= 55 and hum <= 40:
        ham_key = "dry_cool"
    elif gust is not None and gust >= 25:
        ham_key = "stormy"
    elif dry >= 10:
        ham_key = "quiet"
    else:
        ham_key = "default"
    ham = next(h for h in HAM_ASIDES if h["when"] == ham_key)

    creek = rng.choice(CREEK_FOOTERS)

    return {
        "fact": " ".join(facts),
        "counsel": " ".join(counsel_bits),
        "oracle": oracle,
        "proverb": templated,
        "static_proverb": proverb,
        "ham_aside": ham["text"],
        "creek_footer": creek,
        "tiers_note": "FACT is measurement. COUNSEL is practical. ORACLE is folklore. Do not mix them.",
    }


def records_teaser(records: dict) -> list[dict]:
    labels = {
        "temp_max_all": "Hottest on record",
        "temp_min_all": "Coldest on record",
        "gust_max_all": "Strongest gust",
        "rain_day_max_all": "Wettest day",
    }
    out = []
    for key, label in labels.items():
        r = records.get(key)
        if not r:
            continue
        out.append({
            "key": key,
            "label": label,
            "value": r.get("value"),
            "day": r.get("day"),
            "ts": r.get("ts"),
        })
    return out


def build_payload(
    *,
    daily_rows: list[dict],
    records: dict,
    db_stats: dict,
    live: Optional[dict] = None,
    today: Optional[date] = None,
    gridsquare: str = "EM15fo",
    neighborhood: str = "Brasswood",
    station_id: str = "KOKEDMON585",
    display_name: str = "WY6Y Weather",
) -> dict:
    today = today or date.today()
    live = live or {}
    archive = archive_meta(daily_rows, db_stats)
    start = _parse_day(archive["first_day"]) if archive.get("first_day") else None
    vol = volume_info(start, today)
    vol["barcode"] = gridsquare
    season = meteorological_season(today)
    doy = day_of_year_info(today)
    moon = moon_phase(today)
    rain = rain_book(daily_rows, today)
    pheno = phenology(daily_rows, today)
    hist = this_day_history(daily_rows, today)
    heat = month_heatmap(daily_rows, today.year, today.month)
    # previous month heatmap too if useful
    prev = today.replace(day=1) - timedelta(days=1)
    heat_prev = month_heatmap(daily_rows, prev.year, prev.month)
    wind = wind_ledger(daily_rows, today)
    words = counsel_and_oracle(live, rain, hist, season, moon, archive)
    teaser = records_teaser(records)

    page_num = doy["doy"]  # faux page number = day of year
    return {
        "generated_ts": int(time.time()),
        "station": {
            "display_name": display_name,
            "neighborhood": neighborhood,
            "gridsquare": gridsquare,
            "station_id": station_id,
        },
        "volume": vol,
        "archive": archive,
        "season": season,
        "doy": doy,
        "moon": moon,
        "rain": rain,
        "phenology": pheno,
        "this_day": hist,
        "heatmap": heat,
        "heatmap_prev": heat_prev,
        "wind": wind,
        "words": words,
        "records_teaser": teaser,
        "print": {
            "page": page_num,
            "page_label": f"p. {page_num}",
            "edition": vol["label"],
            "printed_for": f"{neighborhood} · {gridsquare}",
            "fine_print": (
            "This edition is a living document. Daily rollups are forever; full samples keep ~90 days. "
            "NWS normals are area reference, not our thermometer. "
            "Space weather is NOAA SWPC (guest column from the sky), not the AcuRite. "
            "Folklore is labeled folklore."
        ),
        },
        "nws_normals_monthly": [
            {"month": m, **NWS_MONTHLY_NORMALS[m]} for m in range(1, 13)
        ],
    }


# ---------------------------------------------------------------------------
# Rare Almanac Pushover notices (once per event key)
# ---------------------------------------------------------------------------

def milestone_events(daily_rows: list[dict], today: Optional[date] = None) -> list[dict]:
    """Return milestone events. `fresh` means the trigger day is today (notify-worthy)."""
    today = today or date.today()
    today_s = today.isoformat()
    rows = _sorted_days(daily_rows)
    year_rows = [r for r in rows if (d := _parse_day(r["day"])) and d.year == today.year]
    events = []

    first_100 = next(
        (r for r in year_rows if (_safe_float(r.get("temp_max")) or 0) >= 100),
        None,
    )
    if first_100:
        events.append({
            "key": f"almanac_first_100_{today.year}",
            "title": "Almanac: first 100°F",
            "message": (
                f"First 100°F day of {today.year} on {first_100.get('day')} "
                f"({_safe_float(first_100.get('temp_max')):.0f}°F). The garden has been advised."
            ),
            "fresh": first_100.get("day") == today_s,
        })

    first_freeze = next(
        (r for r in year_rows if (_safe_float(r.get("temp_min")) or 99) <= 32),
        None,
    )
    if first_freeze:
        events.append({
            "key": f"almanac_first_freeze_{today.year}",
            "title": "Almanac: first freeze",
            "message": (
                f"First freeze of {today.year} on {first_freeze.get('day')} "
                f"(low {_safe_float(first_freeze.get('temp_min')):.0f}°F). Cover what you love."
            ),
            "fresh": first_freeze.get("day") == today_s,
        })

    # 30-day drought: notify only on the day we first cross 30
    dry = 0
    for r in reversed(rows):
        if (_safe_float(r.get("rain_daily_in")) or 0) < 0.01:
            dry += 1
        else:
            break
    if dry >= 30 and rows:
        last_day = rows[-1].get("day")
        events.append({
            "key": f"almanac_drought30_{today.year}_{dry // 30}",  # once per 30-day band / year
            "title": "Almanac: 30-day dry streak",
            "message": (
                f"{dry} days without 0.01\" rain (through {last_day}). "
                "Severity: prayer circuit. The creek still does not care — but your hose might."
            ),
            "fresh": last_day == today_s and dry == 30,
        })

    return events


def maybe_notify_milestones(
    daily_rows: list[dict],
    *,
    get_meta: Callable[[str, Optional[str]], Optional[str]],
    set_meta: Callable[[str, str], None],
    notify: Callable[[str, str], None],
    today: Optional[date] = None,
) -> list[str]:
    """Fire rare Almanac Pushovers once per meta key.

    Historical milestones (already in the archive before deploy) are stamped
    in meta without notifying so we don't spam on first boot.
    """
    sent = []
    for ev in milestone_events(daily_rows, today=today):
        if get_meta(ev["key"]):
            continue
        try:
            if ev.get("fresh"):
                notify(ev["title"], ev["message"])
                set_meta(ev["key"], str(int(time.time())))
                sent.append(ev["key"])
            else:
                # Acknowledge past events without a push
                set_meta(ev["key"], f"ack_historical:{int(time.time())}")
        except Exception:
            pass
    return sent
