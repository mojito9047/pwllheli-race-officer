"""App-settings store: the SQLite-backed key/value settings, its short-lived
in-process cache, and the numeric/boolean value validators.

Extracted from app.py. Reads/writes the app_settings table via core.db and
caches values briefly (the public competitor page polls several APIs, so this
avoids repeated init_db()/SQLite reads). Most typed config views live
here too; video_config and hardware_config live with their subsystems
(core/video.py, core/horn.py), and the settings writer (save_app_settings)
stays in app.py with the settings form.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Dict, Optional

from core import appstate
from core.boats import safe_rating_listing_url
from core.db import get_db, init_db
from core.timeutils import parse_float
from core.weather import safe_weather_station_url

APP_SETTINGS_CACHE_LOCK = threading.Lock()
APP_SETTINGS_CACHE_VALUES: Optional[Dict[str, str]] = None
APP_SETTINGS_CACHE_TIME = 0.0
APP_SETTINGS_CACHE_TTL_SECONDS = 1.0


def float_in_range(value: Any, default: float, min_value: float, max_value: float) -> float:
    """Parse and clamp a floating-point settings value."""
    parsed = parse_float(value)
    if parsed is None:
        parsed = default
    return max(min_value, min(max_value, float(parsed)))


def bool_to_text(value: bool) -> str:
    """Convert a boolean to the app's stored settings text format."""
    return "1" if value else "0"


def text_to_bool(value: Any, default: bool = False) -> bool:
    """Parse the app's stored settings text into a boolean."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on", "assert", "high")


def int_in_range(value: Any, default: int, minimum: int, maximum: int) -> int:
    """Parse and clamp an integer settings value."""
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(maximum, number))


def invalidate_app_settings_cache() -> None:
    """Clear the short-lived app-settings cache after settings are saved."""
    global APP_SETTINGS_CACHE_VALUES, APP_SETTINGS_CACHE_TIME
    with APP_SETTINGS_CACHE_LOCK:
        APP_SETTINGS_CACHE_VALUES = None
        APP_SETTINGS_CACHE_TIME = 0.0


def get_app_setting_overrides() -> Dict[str, str]:
    """Read app-level settings from SQLite, using a short in-process cache.

    The public competitor page polls several APIs.  Without this cache each poll
    repeatedly opened SQLite and ran init_db(), which became very slow after the
    v0.85 first-run credential hardening.  Settings changes still appear
    immediately because save_app_settings() invalidates the cache.
    """
    global APP_SETTINGS_CACHE_VALUES, APP_SETTINGS_CACHE_TIME
    try:
        init_db()
        now = time.monotonic()
        with APP_SETTINGS_CACHE_LOCK:
            cached = APP_SETTINGS_CACHE_VALUES
            cached_at = APP_SETTINGS_CACHE_TIME
            if cached is not None and now - cached_at <= APP_SETTINGS_CACHE_TTL_SECONDS:
                return dict(cached)
        with get_db() as db:
            values = {row["key"]: row["value"] for row in db.execute("SELECT key, value FROM app_settings").fetchall()}
        with APP_SETTINGS_CACHE_LOCK:
            APP_SETTINGS_CACHE_VALUES = dict(values)
            APP_SETTINGS_CACHE_TIME = now
        return values
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Typed config views: read raw settings and merge with appstate defaults.
# ---------------------------------------------------------------------------

def listing_config() -> Dict[str, str]:
    """Return configured IRC and YTC rating-list source URLs."""
    overrides = get_app_setting_overrides()
    return {
        "irc_listing_url": safe_rating_listing_url(overrides.get("irc_listing_url") or appstate.IRC_LISTING_URL, appstate.IRC_LISTING_URL),
        "ytc_listing_url": safe_rating_listing_url(overrides.get("ytc_listing_url") or appstate.YTC_LISTING_URL, appstate.YTC_LISTING_URL),
    }


def weather_config() -> Dict[str, Any]:
    """Return normalised weather source settings."""
    overrides = get_app_setting_overrides()
    legacy_enabled = text_to_bool(overrides.get("weather_enabled"), False)
    source = (overrides.get("weather_source") or ("station" if legacy_enabled else "manual")).strip().lower()
    if source not in ("station", "manual"):
        source = "station"
    return {
        "weather_source": source,
        "weather_enabled": source == "station",
        "weather_station_url": safe_weather_station_url(overrides.get("weather_station_url") or appstate.DEFAULT_WEATHER_STATION_URL),
        "weather_poll_seconds": int_in_range(overrides.get("weather_poll_seconds"), 5, 1, 120),
        "weather_wind_dir_offset": float_in_range(overrides.get("weather_wind_dir_offset"), 0.0, -180.0, 180.0),
        "weather_manual_twd": parse_float(overrides.get("weather_manual_twd")),
        "weather_manual_tws": parse_float(overrides.get("weather_manual_tws")),
    }


def course_chart_config() -> Dict[str, Any]:
    """Return configured tile URLs for course charts."""
    overrides = get_app_setting_overrides()
    return {
        "course_chart_tile_url": (overrides.get("course_chart_tile_url") or appstate.DEFAULT_COURSE_CHART_TILE_URL).strip(),
        "course_chart_overlay_url": (overrides.get("course_chart_overlay_url") or appstate.DEFAULT_COURSE_CHART_OVERLAY_URL).strip(),
        "bridge_window_lat": appstate.BRIDGE_WINDOW_LAT,
        "bridge_window_lon": appstate.BRIDGE_WINDOW_LON,
        "start_finish_mark": "O",
    }


def race_console_config() -> Dict[str, Any]:
    """Return central start-sequence automation settings."""
    overrides = get_app_setting_overrides()
    return {
        "start_automation_horn_enabled": text_to_bool(overrides.get("start_automation_horn_enabled"), True),
        "start_automation_audio_enabled": text_to_bool(overrides.get("start_automation_audio_enabled"), True),
        "central_audio_rate": int_in_range(overrides.get("central_audio_rate"), 185, 80, 320),
        # 185 rather than 285: this rate now governs the *timing* of the ten
        # count, not just how briskly the word "Start" is said, and 185 is the
        # rate the count was tuned at and is known to land "One" on the gun.
        # Slowing the normal rate for clarity no longer drags the count with it.
        "central_audio_fast_rate": int_in_range(overrides.get("central_audio_fast_rate"), 185, 120, 420),
        # A short tone played this many seconds before each spoken announcement,
        # to key up a VHF's VOX so the start of the announcement is not clipped.
        "central_audio_vox_tone_enabled": text_to_bool(overrides.get("central_audio_vox_tone_enabled"), False),
        "central_audio_vox_lead_seconds": int_in_range(overrides.get("central_audio_vox_lead_seconds"), 2, 1, 10),
    }
