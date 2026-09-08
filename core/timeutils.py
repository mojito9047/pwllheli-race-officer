"""Pure date/time, number, string and geometry helpers.

Extracted verbatim from app.py. No Flask, database or module-global
dependencies -- everything here is a pure function of its arguments.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, Optional


def parse_dt(value: Optional[str]) -> Optional[datetime]:
    """Parse an ISO-style date/time string into a datetime, returning None when empty or invalid."""
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    value = str(value).strip()
    if not value:
        return None
    # datetime-local often has no seconds. datetime.fromisoformat accepts both.
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        # A common pasted format from race offices.
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
            try:
                return datetime.strptime(value, fmt)
            except ValueError:
                pass
    return None


def parse_float(value: Any) -> Optional[float]:
    """Parse a numeric form or CSV value, returning None when it cannot be used."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def normalise_key(value: str) -> str:
    """Normalise a CSV/header key for tolerant source-column matching."""
    return " ".join(str(value or "").strip().lower().replace("_", " ").split())


def clean_sail_no(value: str) -> str:
    """Normalise sail numbers so lookups are insensitive to case, spaces and punctuation."""
    return "".join(str(value or "").upper().split())


def first_present(row: Dict[str, str], aliases: Iterable[str]) -> str:
    """Return the first matching value from a CSV row using tolerant header aliases."""
    norm_map = {normalise_key(k): v for k, v in row.items()}
    for alias in aliases:
        key = normalise_key(alias)
        if key in norm_map and str(norm_map[key]).strip():
            return str(norm_map[key]).strip()
    # Also allow partial header matches because club spreadsheets often have explanatory headings.
    for alias in aliases:
        alias_key = normalise_key(alias)
        for key, value in norm_map.items():
            if alias_key and alias_key in key and str(value).strip():
                return str(value).strip()
    return ""


def dt_for_input(value: str) -> str:
    """Format a datetime string for HTML datetime-local inputs."""
    dt = parse_dt(value)
    return dt.strftime("%Y-%m-%dT%H:%M:%S") if dt else value


def dt_for_minute_input(value: str) -> str:
    """Format a datetime string for minute-resolution HTML datetime-local inputs."""
    dt = parse_dt(value)
    return dt.strftime("%Y-%m-%dT%H:%M") if dt else value


def next_whole_minute(after: Optional[datetime] = None, at_least_seconds: int = 5) -> str:
    """The next whole minute, at least a few seconds away.

    Signal times are whole minutes throughout this app, because a fleet counts
    down to them. "Lower AP now" pressed at 14:15:41 put the warning signal at
    14:16:41 and the gun at 14:21:41, which is not a countdown anybody can
    follow -- so the moment is rounded up rather than taken from the click.
    """
    base = (after or datetime.now()) + timedelta(seconds=at_least_seconds)
    rounded = base.replace(second=0, microsecond=0)
    if rounded < base:
        rounded += timedelta(minutes=1)
    return rounded.isoformat(timespec="seconds")


def normalise_start_time_value(value: str) -> str:
    """Force start-time form values to whole-minute ISO strings."""
    dt = parse_dt(value)
    if not dt:
        return ""
    dt = dt.replace(second=0, microsecond=0)
    return dt.isoformat(timespec="seconds")


def dt_display(value: Optional[str]) -> str:
    """Format a date/time for compact race-page display."""
    dt = parse_dt(value)
    return dt.strftime("%H:%M:%S") if dt else ""


# Written out rather than left to ``%b``, which follows the machine's locale.
# The club is in Gwynedd and a Welsh-locale host would render these in Welsh on
# one machine and English on another, for the same race.
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def date_display(value: Optional[str]) -> str:
    """A date on its own, UK order: ``19 Sep 2026``.

    Day first, and the month named rather than numbered, so nothing here can be
    read in US order. ISO is kept for anything a machine reads — log filenames
    and the Sailwave export — where sorting matters more than reading.
    """
    dt = parse_dt(value)
    return f"{dt.day:02d} {_MONTHS[dt.month - 1]} {dt.year}" if dt else ""


def dt_full_display(value: Optional[str]) -> str:
    """Format a date/time including seconds for audit/log display."""
    dt = parse_dt(value)
    return f"{date_display(value)} {dt.strftime('%H:%M:%S')}" if dt else ""


def seconds_display(seconds: Optional[float]) -> str:
    """Format a duration in seconds as h:mm:ss or m:ss text."""
    if seconds is None:
        return ""
    seconds = int(round(seconds))
    sign = "-" if seconds < 0 else ""
    seconds = abs(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{sign}{h:d}:{m:02d}:{s:02d}"


def decimal_minutes_to_text(lat: Optional[float], lon: Optional[float]) -> str:
    """Convert decimal minutes to race-officer friendly text."""
    if lat is None or lon is None:
        return ""
    def fmt(value: float, pos: str, neg: str, width: int) -> str:
        """Format a value for CSV output."""
        hemi = pos if value >= 0 else neg
        value = abs(value)
        deg = int(value)
        mins = (value - deg) * 60
        return f"{deg:0{width}d}° {mins:06.3f}'{hemi}"
    return f"{fmt(lat, 'N', 'S', 2)} {fmt(lon, 'E', 'W', 3)}"


def angular_diff(a: float, b: float) -> float:
    """Return the smallest angular difference between two compass bearings."""
    return abs((a - b + 180.0) % 360.0 - 180.0)


def haversine_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate the great-circle distance between two coordinates in nautical miles."""
    radius_nm = 3440.065
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * radius_nm * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate the true bearing from one coordinate to another."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dlambda = math.radians(lon2 - lon1)
    x = math.sin(dlambda) * math.cos(phi2)
    y = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlambda)
    return (math.degrees(math.atan2(x, y)) + 360) % 360
