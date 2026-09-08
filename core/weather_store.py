"""Wind sample store and background weather poller.

Extracted verbatim from app.py. Stores live/manual wind samples in SQLite
(purging ambient samples after 24h while keeping race-window history, via
core.weather's retention windows), serves the latest sample and recent history,
polls the station over HTTP, and runs the daemon polling thread whose status
the Settings page reports. Config comes from core.settings.weather_config; this
is its own module (rather than core.weather) because core.settings imports
core.weather, and this layer needs core.settings.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import urllib.request
from datetime import datetime
from typing import Any, Dict, List, Optional

from core.db import get_db, init_db
from core.settings import int_in_range, weather_config
from core.timeutils import parse_float
from core.weather import (
    parse_weather_livedata,
    race_wind_retention_windows,
    sample_time_in_any_window,
    validate_weather_station_url,
)

# Background weather polling state. The poller keeps collecting samples even when
# nobody is looking at the course-recommendation page, so the RO has history
# available as soon as they open it.
WEATHER_POLLER_STATE: Dict[str, Any] = {
    "started": False,
    "last_status": {"ok": False, "enabled": False, "message": "Weather background poller has not started yet."},
    "last_poll_at": None,
}
WEATHER_POLLER_LOCK = threading.Lock()


def insert_weather_sample(twd: Optional[float], tws_kt: Optional[float], gust_kt: Optional[float], source: str, raw: Any) -> Dict[str, Any]:
    """Store one weather sample in the local history table.

    Samples older than 24 hours are purged unless they fall within a race's
    retention window (one hour either side of that race's start/finish), so
    wind history tied to an actual race is kept indefinitely while ambient
    background samples are trimmed to the last day.
    """
    init_db()
    now = time.time()
    iso = datetime.now().isoformat(timespec="seconds")
    cutoff = now - 24 * 3600
    with get_db() as db:
        db.execute("INSERT INTO weather_samples (sample_time, sample_iso, twd, tws_kt, gust_kt, source, raw_json) VALUES (?, ?, ?, ?, ?, ?, ?)", (now, iso, twd, tws_kt, gust_kt, source, json.dumps(raw, ensure_ascii=False)[:20000]))
        windows = race_wind_retention_windows(db)
        stale_ids = [
            row["id"]
            for row in db.execute("SELECT id, sample_time FROM weather_samples WHERE sample_time < ?", (cutoff,)).fetchall()
            if not sample_time_in_any_window(row["sample_time"], windows)
        ]
        if stale_ids:
            db.executemany("DELETE FROM weather_samples WHERE id = ?", [(i,) for i in stale_ids])
        db.commit()
    return {"t": now, "iso": iso, "twd": twd, "tws": tws_kt, "gust": gust_kt, "source": source}


def manual_weather_sample(cfg: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """Return the configured manual wind input as a current sample."""
    cfg = cfg or weather_config()
    if cfg.get("weather_source") != "manual":
        return None
    twd = parse_float(cfg.get("weather_manual_twd"))
    tws = parse_float(cfg.get("weather_manual_tws"))
    if twd is None and tws is None:
        return None
    now = time.time()
    return {
        "t": now,
        "iso": datetime.now().isoformat(timespec="seconds"),
        "twd": twd,
        "tws": tws,
        "gust": None,
        "source": "manual",
    }


def latest_weather_sample() -> Optional[Dict[str, Any]]:
    """Return the most recent wind sample from the selected source."""
    cfg = weather_config()
    manual = manual_weather_sample(cfg)
    if manual:
        return manual
    init_db()
    try:
        with get_db() as db:
            row = db.execute("SELECT * FROM weather_samples ORDER BY sample_time DESC, id DESC LIMIT 1").fetchone()
    except sqlite3.OperationalError:
        # During tests, startup or a database swap, a background poller may ask
        # for the latest sample before the replacement SQLite file has every
        # table visible on that connection.  Treat that the same as no sample
        # yet; the next poll will use the initialised schema.
        return None
    if not row:
        return None
    return {"t": row["sample_time"], "iso": row["sample_iso"], "twd": row["twd"], "tws": row["tws_kt"], "gust": row["gust_kt"], "source": row["source"]}


def weather_history(minutes: int = 10) -> List[Dict[str, Any]]:
    """Return recent wind samples for charts and live course analysis."""
    cfg = weather_config()
    manual = manual_weather_sample(cfg)
    if manual:
        return [{"t": manual["t"], "iso": manual["iso"], "twd": manual.get("twd"), "tws": manual.get("tws"), "gust": manual.get("gust"), "source": "manual"}]
    init_db()
    minutes = int_in_range(minutes, 10, 1, 360)
    since = time.time() - minutes * 60
    try:
        with get_db() as db:
            rows = db.execute("SELECT * FROM weather_samples WHERE sample_time >= ? ORDER BY sample_time ASC, id ASC", (since,)).fetchall()
    except sqlite3.OperationalError:
        return []
    return [{"t": r["sample_time"], "iso": r["sample_iso"], "twd": r["twd"], "tws": r["tws_kt"], "gust": r["gust_kt"], "source": r["source"]} for r in rows]


def weather_samples_between(start_ts: float, end_ts: float) -> List[Dict[str, Any]]:
    """Return the stored wind samples inside a time window (epoch seconds).

    Used to summarise the conditions during a race once it has finished. Race
    windows are exempt from the 24-hour purge (see insert_weather_sample), so an
    old race's wind is still there.
    """
    init_db()
    if end_ts < start_ts:
        start_ts, end_ts = end_ts, start_ts
    try:
        with get_db() as db:
            rows = db.execute(
                "SELECT * FROM weather_samples WHERE sample_time >= ? AND sample_time <= ?"
                " ORDER BY sample_time ASC, id ASC",
                (float(start_ts), float(end_ts)),
            ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [{"t": r["sample_time"], "iso": r["sample_iso"], "twd": r["twd"], "tws": r["tws_kt"], "gust": r["gust_kt"], "source": r["source"]} for r in rows]


def fetch_weather_station_sample(force: bool = False) -> Dict[str, Any]:
    """Poll the weather station and store one live wind sample."""
    init_db()
    cfg = weather_config()
    if cfg.get("weather_source") == "manual":
        latest = latest_weather_sample()
        return {"ok": bool(latest), "enabled": False, "source": "manual", "message": "Using manual wind input." if latest else "Manual wind input is selected but TWD/TWS have not been entered.", "sample": latest, "latest": latest}
    if not cfg.get("weather_enabled") and not force:
        latest = latest_weather_sample()
        return {"ok": False, "enabled": False, "source": "station", "message": "Weather station polling is disabled.", "latest": latest}
    now = time.time()
    latest = latest_weather_sample()
    poll_seconds = int(cfg.get("weather_poll_seconds", 5))
    if not force and latest and (now - float(latest.get("t", 0))) < poll_seconds:
        return {"ok": True, "enabled": bool(cfg.get("weather_enabled")), "message": "Using recent weather sample.", "sample": latest}
    try:
        url = validate_weather_station_url(str(cfg.get("weather_station_url", "")))
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=4) as response:
            raw = response.read()
        data = json.loads(raw.decode("utf-8-sig", errors="replace"))
        parsed = parse_weather_livedata(data, cfg)
        if not parsed.get("ok"):
            return {"ok": False, "enabled": bool(cfg.get("weather_enabled")), "message": "Weather station replied, but no wind direction/speed was found in common_list.", "raw": data, "latest": latest}
        sample = insert_weather_sample(parsed.get("twd"), parsed.get("tws_kt"), parsed.get("gust_kt"), url, data)
        return {"ok": True, "enabled": bool(cfg.get("weather_enabled")), "message": "Weather station sample recorded.", "sample": sample}
    except Exception as exc:
        return {"ok": False, "enabled": bool(cfg.get("weather_enabled")), "message": f"Could not read weather station: {exc}", "latest": latest}


def weather_runtime_status() -> Dict[str, Any]:
    """Return the current wind source status plus the latest sample."""
    cfg = weather_config()
    latest = latest_weather_sample()
    if cfg.get("weather_source") == "manual":
        return {
            "ok": bool(latest),
            "enabled": False,
            "source": "manual",
            "message": "Manual wind input selected." if latest else "Manual wind input selected; enter TWD/TWS in Settings.",
            "sample": latest,
            "latest": latest,
            "background_started": bool(WEATHER_POLLER_STATE.get("started")),
        }
    with WEATHER_POLLER_LOCK:
        status = dict(WEATHER_POLLER_STATE.get("last_status") or {})
        last_poll_at = WEATHER_POLLER_STATE.get("last_poll_at")
        started = bool(WEATHER_POLLER_STATE.get("started"))
    if not status:
        status = {"ok": False, "enabled": cfg.get("weather_enabled"), "source": "station", "message": "Weather background poller is waiting to start."}
    if latest:
        status.setdefault("sample", latest)
        status.setdefault("latest", latest)
    if last_poll_at:
        status["last_poll_at"] = last_poll_at
    status["background_started"] = started
    status["source"] = "station"
    return status


def weather_background_loop() -> None:
    """Continuously poll the weather station while the app is running."""
    while True:
        sleep_seconds = 5
        try:
            cfg = weather_config()
            if cfg.get("weather_source") == "manual":
                status = weather_runtime_status()
                sleep_seconds = 5
            elif cfg.get("weather_enabled"):
                status = fetch_weather_station_sample(force=True)
                sleep_seconds = int_in_range(cfg.get("weather_poll_seconds"), 5, 1, 120)
            else:
                status = {"ok": False, "enabled": False, "source": "station", "message": "Weather station polling is disabled.", "latest": latest_weather_sample()}
                sleep_seconds = 5
            with WEATHER_POLLER_LOCK:
                WEATHER_POLLER_STATE["last_status"] = status
                WEATHER_POLLER_STATE["last_poll_at"] = time.time()
        except Exception as exc:
            with WEATHER_POLLER_LOCK:
                WEATHER_POLLER_STATE["last_status"] = {"ok": False, "enabled": weather_config().get("weather_enabled"), "message": f"Weather background poller error: {exc}", "latest": latest_weather_sample()}
                WEATHER_POLLER_STATE["last_poll_at"] = time.time()
            sleep_seconds = 5
        time.sleep(max(1, min(120, int(sleep_seconds))))


def start_weather_background_poller() -> None:
    """Start the weather polling thread if it is not already running."""
    with WEATHER_POLLER_LOCK:
        if WEATHER_POLLER_STATE.get("started"):
            return
        WEATHER_POLLER_STATE["started"] = True
    thread = threading.Thread(target=weather_background_loop, name="weather-background-poller", daemon=True)
    thread.start()
