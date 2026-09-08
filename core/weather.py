"""Weather-station URL validation, live-data parsing and wind-retention windows.

Extracted from app.py. These are the framework-independent weather helpers:
normalising/validating the station URL (with SSRF guards), converting station
speed/direction values, parsing the get_livedata_info payload, and computing the
per-race wind-history retention windows. They take their inputs as arguments
(payloads, a db connection) and read only DEFAULT_WEATHER_STATION_URL from
core.appstate.

The stateful pieces -- sample storage/purge, latest/history reads, the live
poll and the background poller -- live in core/weather_store.py (a separate
module because core.settings imports this one).
"""
from __future__ import annotations

import ipaddress
import math
import os
import re
import socket
import sqlite3
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from core import appstate
from core.timeutils import parse_dt


def normalise_weather_url(url: str) -> str:
    """Turn a weather-station IP or URL into the live-data endpoint URL."""
    url = (url or "").strip()
    if not url:
        url = appstate.DEFAULT_WEATHER_STATION_URL
    if not url.lower().startswith(("http://", "https://")):
        url = "http://" + url
    parsed = urlparse(url)
    if (parsed.path or "") in ("", "/"):
        url = url.rstrip("/") + "/get_livedata_info?"
    return url


def weather_allowed_host_patterns() -> List[str]:
    """Return optional comma-separated host allow-list patterns from env."""
    raw = os.environ.get("RO_WEATHER_ALLOWED_HOSTS", "").strip()
    return [item.strip().lower() for item in raw.split(",") if item.strip()]


def host_matches_pattern(hostname: str, pattern: str) -> bool:
    """Match an exact host or a leading-dot domain suffix pattern."""
    hostname = hostname.strip().lower().rstrip(".")
    pattern = pattern.strip().lower().rstrip(".")
    if not pattern:
        return False
    if pattern.startswith("."):
        return hostname.endswith(pattern)
    return hostname == pattern


def validate_weather_station_url(url: str) -> str:
    """Validate a weather-station URL before the server connects to it.

    The hut normally talks to a local weather station, so private RFC1918
    addresses are allowed.  Dangerous destinations such as localhost, link-local
    metadata addresses, multicast and unspecified addresses are rejected.  Set
    RO_WEATHER_ALLOWED_HOSTS to a comma-separated list to restrict the host even
    further on exposed deployments.
    """
    url = normalise_weather_url(url)
    parsed = urlparse(url)
    if parsed.scheme.lower() not in ("http", "https") or not parsed.hostname:
        raise ValueError("Weather station URL must be http:// or https:// with a host.")
    if parsed.username or parsed.password:
        raise ValueError("Weather station URL must not include embedded credentials.")
    hostname = parsed.hostname.strip().lower()
    allow_patterns = weather_allowed_host_patterns()
    if allow_patterns and not any(host_matches_pattern(hostname, pattern) for pattern in allow_patterns):
        raise ValueError("Weather station host is not in RO_WEATHER_ALLOWED_HOSTS.")
    try:
        port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
    except ValueError as exc:
        raise ValueError("Weather station URL contains an invalid port.") from exc
    try:
        infos = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError(f"Weather station host could not be resolved: {exc}") from exc
    addresses = sorted({info[4][0] for info in infos})
    if not addresses:
        raise ValueError("Weather station host did not resolve to an address.")
    for addr in addresses:
        ip = ipaddress.ip_address(addr)
        if ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified:
            raise ValueError("Weather station URL resolves to a disallowed local/link-local address.")
    return url


def safe_weather_station_url(url: str) -> str:
    """Return a valid weather URL or the built-in weather-station default."""
    try:
        return validate_weather_station_url(url)
    except ValueError:
        return normalise_weather_url(appstate.DEFAULT_WEATHER_STATION_URL)


def value_to_float_and_unit(value: Any, fallback_unit: str = "") -> Tuple[Optional[float], str]:
    """Parse a weather value that may include units."""
    text = str(value or "").strip()
    if not text:
        return None, fallback_unit
    match = re.search(r"[-+]?\d+(?:\.\d+)?", text)
    if not match:
        return None, fallback_unit
    return float(match.group(0)), (text[match.end():].strip() or fallback_unit or "").lower()


def speed_to_knots(value: Any, unit: str = "") -> Optional[float]:
    """Convert a parsed weather speed value to knots."""
    number, unit_text = value_to_float_and_unit(value, unit)
    if number is None:
        return None
    unit_text = unit_text.lower().replace(" ", "")
    if unit_text in ("kt", "kts", "kn", "knot", "knots"):
        return number
    if unit_text in ("mph", "mi/h", "mile/h", "miles/h"):
        return number * 0.868976242
    if unit_text in ("m/s", "ms", "mps", "meter/s", "metre/s", "meters/s", "metres/s"):
        return number * 1.94384449
    if unit_text in ("km/h", "kph", "kmh"):
        return number * 0.539956803
    # The generic API defines wind speed as m/s; live data may include display units.
    return number * 1.94384449 if not unit_text else number


def wind_item_id(item: Dict[str, Any]) -> str:
    """Normalise weather-station item ids to hex-like strings."""
    raw = str(item.get("id", "")).strip().lower()
    if raw.startswith("0x"):
        try:
            return f"0x{int(raw, 16):02x}"
        except ValueError:
            return raw
    try:
        return f"0x{int(raw):02x}"
    except ValueError:
        return raw


def parse_weather_livedata(data: Dict[str, Any], cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Extract TWD/TWS/gust from the station get_livedata_info payload.

    cfg supplies the optional weather_wind_dir_offset; callers pass the live
    weather_config(). When omitted a zero offset is used.
    """
    cfg = cfg or {}
    twd = None
    tws_kt = None
    gust_kt = None
    battery = None
    common = data.get("common_list") or []
    if isinstance(common, list):
        for item in common:
            if not isinstance(item, dict):
                continue
            iid = wind_item_id(item)
            val = item.get("val")
            unit = str(item.get("unit", ""))
            if iid == "0x0a":
                number, _ = value_to_float_and_unit(val, unit)
                if number is not None:
                    twd = (number + float(cfg.get("weather_wind_dir_offset", 0.0))) % 360
                    battery = item.get("battery", battery)
            elif iid == "0x0b":
                tws_kt = speed_to_knots(val, unit)
            elif iid == "0x0c":
                gust_kt = speed_to_knots(val, unit)
    return {
        "ok": twd is not None or tws_kt is not None,
        "twd": twd,
        "tws_kt": None if tws_kt is None else max(0.0, tws_kt),
        "gust_kt": None if gust_kt is None else max(0.0, gust_kt),
        "battery": battery,
    }


def circular_mean_deg(values: Any) -> Optional[float]:
    """Vector-average a set of compass bearings.

    An arithmetic mean is wrong for directions: 350 and 10 average to 180
    instead of 0. Average the unit vectors and take the resulting angle.
    Returns None when there is nothing to average, or when the directions
    cancel out exactly (e.g. one sample each at 0 and 180), where no mean
    direction is meaningful.
    """
    xs = ys = 0.0
    count = 0
    for value in values or []:
        if value is None:
            continue
        try:
            radians = math.radians(float(value) % 360.0)
        except (TypeError, ValueError):
            continue
        xs += math.cos(radians)
        ys += math.sin(radians)
        count += 1
    if not count or (abs(xs) < 1e-9 and abs(ys) < 1e-9):
        return None
    mean = math.degrees(math.atan2(ys, xs)) % 360.0
    # A hair below zero (e.g. averaging 350 and 10) rounds up to exactly 360 in
    # the modulo; report it as 0 so callers never display "360°T".
    return 0.0 if mean >= 360.0 else mean


def summarise_wind_samples(samples: Any) -> Dict[str, Any]:
    """Average a set of wind samples as a record of the conditions.

    TWD is vector-averaged (see circular_mean_deg); TWS is a plain mean with
    its range kept for context, and the gust reported is the highest seen.
    """
    rows = list(samples or [])
    tws_values = [float(row["tws"]) for row in rows if row.get("tws") is not None]
    gusts = [float(row["gust"]) for row in rows if row.get("gust") is not None]
    return {
        "count": len(rows),
        "twd": circular_mean_deg(row.get("twd") for row in rows),
        "tws": (sum(tws_values) / len(tws_values)) if tws_values else None,
        "tws_min": min(tws_values) if tws_values else None,
        "tws_max": max(tws_values) if tws_values else None,
        "gust": max(gusts) if gusts else None,
    }


def race_wind_retention_windows(db: sqlite3.Connection) -> List[Tuple[float, Optional[float]]]:
    """Return (start_epoch, end_epoch_or_None) windows of wind data to keep for races.

    Each race keeps its wind data from one hour before its first warning signal
    to one hour after its last recorded finish.  A race with no finishes yet is
    treated as still open, so its window has no upper bound until a finish is
    recorded.
    """
    windows: List[Tuple[float, Optional[float]]] = []
    races = db.execute("SELECT id, start_time FROM races").fetchall()
    for race in races:
        warning_dt = parse_dt(race["start_time"])
        if not warning_dt:
            continue
        start_ts = warning_dt.timestamp() - 3600
        finish_rows = db.execute(
            "SELECT finish_time FROM entries WHERE race_id = ? AND finish_time IS NOT NULL AND finish_time != ''",
            (race["id"],),
        ).fetchall()
        finish_dts = [d for d in (parse_dt(r["finish_time"]) for r in finish_rows) if d]
        end_ts = max(d.timestamp() for d in finish_dts) + 3600 if finish_dts else None
        windows.append((start_ts, end_ts))
    return windows


def sample_time_in_any_window(sample_time: float, windows: List[Tuple[float, Optional[float]]]) -> bool:
    """Return True when a wind-sample timestamp falls inside any race retention window."""
    return any(start <= sample_time and (end is None or sample_time <= end) for start, end in windows)
