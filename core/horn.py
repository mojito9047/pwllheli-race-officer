"""Horn hardware: serial adapter IO, manual-input sensing and horn firing.

Extracted verbatim from app.py. Talks to the ProLog-style serial relay adapter
(DTR fires the horn, RTS is the sense common, DCD/CTS report idle/active),
reads the hardware settings with env-var defaults, detects rising edges on the
external/manual horn input, and fires the horn with the process-wide IO lock
held so a browser input poll cannot deassert the relay mid-blast.

Manual-horn events schedule an evidence video clip through VIDEO_CLIP_SCHEDULER,
a hook app.py registers (the video subsystem still lives there) -- same pattern
as core.db.SCHEMA_INITIALIZER.
"""
from __future__ import annotations

import os
import re
import urllib.parse
import threading
import time
from datetime import datetime
from typing import Any, Dict, Optional

from core.activitylog import settings_change_summary
from core.db import get_db, init_db
from core.eventlog import log_event
from core.settings import bool_to_text, int_in_range, text_to_bool

# Registered by app.py once the video subsystem is loaded.
VIDEO_CLIP_SCHEDULER = None

# ---------------------------------------------------------------------------
# Runtime configuration defaults. Database settings override these values after
# first save, but environment variables are still useful for first-run setup.
# ---------------------------------------------------------------------------
DEFAULT_HARDWARE_CONFIG = {
    "serial_port": os.environ.get("RO_SERIAL_PORT", "").strip(),
    "horn_line": os.environ.get("RO_HORN_LINE", "DTR").strip().upper() or "DTR",
    "horn_active": os.environ.get("RO_HORN_ACTIVE", "1").strip().lower() not in ("0", "false", "no"),
    "horn_duration_ms": int(os.environ.get("RO_HORN_DURATION_MS", "1200")),
    "horn_input_enabled": os.environ.get("RO_HORN_INPUT_ENABLED", "0").strip().lower() in ("1", "true", "yes", "on"),
    "horn_input_line": os.environ.get("RO_HORN_INPUT_LINE", "CTS").strip().upper() or "CTS",
    "horn_input_active": os.environ.get("RO_HORN_INPUT_ACTIVE", "1").strip().lower() not in ("0", "false", "no"),
    "horn_input_poll_ms": int(os.environ.get("RO_HORN_INPUT_POLL_MS", "250")),
    # Victron VE.Direct power-monitoring ports (read by core.power). Stored in the
    # same hardware_settings table; monitor-only, so no wiring/line config.
    "vedirect_smartshunt_port": os.environ.get("RO_VEDIRECT_SMARTSHUNT_PORT", "").strip(),
    "vedirect_phoenix_port": os.environ.get("RO_VEDIRECT_PHOENIX_PORT", "").strip(),
    "vedirect_smartsolar_port": os.environ.get("RO_VEDIRECT_SMARTSOLAR_PORT", "").strip(),
    "power_sim_enabled": os.environ.get("RO_POWER_SIM", "0").strip().lower() in ("1", "true", "yes", "on"),
    "power_sample_seconds": int(os.environ.get("RO_POWER_SAMPLE_SECONDS", "30")),
    "power_retention_days": int(os.environ.get("RO_POWER_RETENTION_DAYS", "365")),
    # Web server sizing, read once by Waitress at startup — see serve() in app.py.
    # Here rather than in a config file because the race office cannot edit files
    # on the hut PC mid-season, and the day these matter is a race day.
    #
    # threads: how many requests can be served at once. connection_limit: how many
    # sockets may be open at all, which is the one that bit — a browser holds up to
    # six per origin, so the default 100 is about sixteen viewers whether or not
    # anything is slow. channel_timeout: how long an idle socket is kept before it
    # is reaped, which is what stops a phone that walked out of range holding a slot.
    "server_threads": int(os.environ.get("RO_THREADS", "8")),
    "server_connection_limit": int(os.environ.get("RO_CONNECTION_LIMIT", "100")),
    "server_channel_timeout": int(os.environ.get("RO_CHANNEL_TIMEOUT", "120")),
    # The address the outside world reaches this app on. Behind the relay the app
    # sees the tunnel's own Host header and plain http, so every link it builds
    # with url_for(_external=True) came out as http://hut-origin... -- an internal
    # hostname, in competitor share links and in the branding manifest the relay
    # reads. Empty means "use whatever Host the request arrived with", which is
    # right on the hut LAN and wrong through the tunnel.
    "server_public_base_url": os.environ.get("RO_PUBLIC_BASE_URL", "").strip(),
    # GPS yacht-tracking (read by core.track). The hut app pulls positions from a
    # Traccar server on the relay; stored in the same hardware_settings table.
    "track_enabled": os.environ.get("RO_TRACK_ENABLED", "0").strip().lower() in ("1", "true", "yes", "on"),
    # Shared secret for Traccar's position forwarder (see core.track ingest).
    # Empty disables the push endpoint entirely — the poller is then the only source.
    "track_ingest_secret": os.environ.get("RO_TRACK_INGEST_SECRET", "").strip(),
    # The optional model behind the on-the-water command page. With no key the
    # app falls back to its own grammar, which is a working state rather than a
    # failure: that is what runs the racing when the hut can reach nothing.
    "assistant_api_key": os.environ.get("RO_ASSISTANT_API_KEY", "").strip(),
    "assistant_model": os.environ.get("RO_ASSISTANT_MODEL", "").strip(),
    "assistant_base_url": os.environ.get("RO_ASSISTANT_BASE_URL", "").strip(),
    "traccar_base_url": os.environ.get("RO_TRACCAR_BASE_URL", "").strip(),
    "traccar_token": os.environ.get("RO_TRACCAR_TOKEN", "").strip(),
    "track_poll_seconds": int(os.environ.get("RO_TRACK_POLL_SECONDS", "5")),
    "track_retention_days": int(os.environ.get("RO_TRACK_RETENTION_DAYS", "90")),
    "track_sim_enabled": os.environ.get("RO_TRACK_SIM", "0").strip().lower() in ("1", "true", "yes", "on"),
    "gps_finish_horn": os.environ.get("RO_GPS_FINISH_HORN", "0").strip().lower() in ("1", "true", "yes", "on"),
    "track_race_poll_enabled": os.environ.get("RO_TRACK_RACE_POLL", "1").strip().lower() not in ("0", "false", "no", "off"),
    "track_rounding_radius_m": int(os.environ.get("RO_TRACK_ROUNDING_RADIUS_M", "50")),
    "track_gate_reach_m": int(os.environ.get("RO_TRACK_GATE_REACH_M", "750")),
    # The SIMs behind the trackers (read by core.hologram). Answers the one
    # question no other source can: whether a silent tracker's SIM has been
    # stopped by the network, which never recovers on its own.
    "hologram_enabled": os.environ.get("RO_HOLOGRAM_ENABLED", "0").strip().lower() in ("1", "true", "yes", "on"),
    "hologram_api_key": os.environ.get("RO_HOLOGRAM_API_KEY", "").strip(),
    "hologram_org_id": os.environ.get("RO_HOLOGRAM_ORG_ID", "").strip(),
}

# Simple per-process state for detecting rising edges on the external/manual horn input.
# A single-process monitor; a busier deployment would want a dedicated I/O daemon.
HARDWARE_RUNTIME_STATE: Dict[str, Any] = {
    "last_input_active": None,
    "last_input_logged_at": 0.0,
}
# Guard access to the horn serial adapter.  The browser can poll the manual input
# while the user presses Test Horn or while an automatic sequence fires.  Without
# a process-wide lock, the input poll can reopen the same serial adapter and
# deassert RTS/DTR in the middle of a horn blast.
HARDWARE_IO_LOCK = threading.RLock()

# ProLog-style horn interface wiring used by the Pwllheli race-office hardware:
# - DTR pin 4 drives the horn relay.
# - RTS pin 7 is held asserted and used as the sense/contact common.
# - The relay feedback contact connects RTS to DCD pin 1 when idle.
# - The relay feedback contact connects RTS to CTS pin 8 when horn/manual input is active.
PROLOG_HORN_OUTPUT_LINE = "DTR"
PROLOG_SENSE_REFERENCE_LINE = "RTS"
PROLOG_IDLE_INPUT_LINE = "DCD"
PROLOG_ACTIVE_INPUT_LINE = "CTS"


def normalise_public_base_url(value: Any) -> str:
    """Clean a public base URL down to ``scheme://host[:port]``, or "" if unusable.

    Anything that is not http or https, or has no host, is dropped rather than
    stored: a malformed value here would corrupt every external link the app
    builds, which is worse than not having the setting at all. A path is
    stripped too -- the app is not served under a sub-path and pretending
    otherwise would produce links that 404.
    """
    text = str(value or "").strip()
    if not text:
        return ""
    parsed = urllib.parse.urlsplit(text)
    if parsed.scheme in ("http", "https") and parsed.netloc:
        scheme, host = parsed.scheme, parsed.netloc
    elif "://" in text:
        return ""                         # a scheme was given, and not one we serve
    else:
        guess = urllib.parse.urlsplit("https://" + text)   # a bare hostname is the common entry
        if not guess.netloc:
            return ""
        scheme, host = "https", guess.netloc
    # Last gate, and the one that matters: host[:port] and nothing else. It is
    # what rejects "javascript:alert(1)", which otherwise survives having
    # "https://" pasted in front of it, and any netloc carrying credentials.
    if not re.match(r"^[A-Za-z0-9.\-]+(:\d{1,5})?$", host):
        return ""
    return f"{scheme}://{host.lower()}"


# Read on every request, so it is cached and invalidated when settings are saved
# rather than costing a database round trip per hit.
_PUBLIC_BASE_URL_CACHE: Dict[str, Any] = {"value": None}


def public_base_url() -> str:
    """The address the outside world reaches this app on, or "" if not configured."""
    cached = _PUBLIC_BASE_URL_CACHE["value"]
    if cached is None:
        try:
            saved = get_hardware_setting_overrides().get("server_public_base_url")
        except Exception:
            saved = None
        cached = normalise_public_base_url(
            saved if saved is not None else DEFAULT_HARDWARE_CONFIG["server_public_base_url"])
        _PUBLIC_BASE_URL_CACHE["value"] = cached
    return cached


def forget_public_base_url() -> None:
    """Drop the cached value so the next request picks up a saved change."""
    _PUBLIC_BASE_URL_CACHE["value"] = None


def get_hardware_setting_overrides() -> Dict[str, str]:
    """Read horn/I/O settings from the database."""
    try:
        init_db()
        with get_db() as db:
            return {row["key"]: row["value"] for row in db.execute("SELECT key, value FROM hardware_settings").fetchall()}
    except Exception:
        return {}


def save_hardware_config(settings: Dict[str, Any]) -> str:
    """Persist horn/I/O settings from the settings form.

    Returns a redacted ``key: old -> new`` summary of what changed, for the activity
    log. This is the table the horn wiring lives in, so it is the one where knowing
    the previous value matters most.
    """
    init_db()
    existing = get_hardware_setting_overrides()
    now = datetime.now().isoformat(timespec="seconds")
    values = {
        "serial_port": str(settings.get("serial_port", "")).strip(),
        "horn_line": str(settings.get("horn_line", PROLOG_HORN_OUTPUT_LINE)).strip().upper() if str(settings.get("horn_line", "")).strip().upper() in ("RTS", "DTR") else PROLOG_HORN_OUTPUT_LINE,
        "horn_active": bool_to_text(text_to_bool(settings.get("horn_active"), True)),
        "horn_duration_ms": str(int_in_range(settings.get("horn_duration_ms"), 1200, 50, 5000)),
        "horn_input_enabled": bool_to_text(text_to_bool(settings.get("horn_input_enabled"), False)),
        "horn_input_line": str(settings.get("horn_input_line", "CTS")).strip().upper() if str(settings.get("horn_input_line", "")).strip().upper() in ("CTS", "DSR", "DCD", "CD", "RI") else "CTS",
        "horn_input_active": bool_to_text(text_to_bool(settings.get("horn_input_active"), True)),
        "horn_input_poll_ms": str(int_in_range(settings.get("horn_input_poll_ms"), 250, 100, 5000)),
        "vedirect_smartshunt_port": str(settings.get("vedirect_smartshunt_port", "")).strip(),
        "vedirect_phoenix_port": str(settings.get("vedirect_phoenix_port", "")).strip(),
        "vedirect_smartsolar_port": str(settings.get("vedirect_smartsolar_port", "")).strip(),
        "power_sim_enabled": bool_to_text(text_to_bool(settings.get("power_sim_enabled"), False)),
        "power_sample_seconds": str(int_in_range(settings.get("power_sample_seconds"), 30, 5, 3600)),
        "power_retention_days": str(int_in_range(settings.get("power_retention_days"), 365, 1, 3650)),
        # Clamped hard: these are the knobs that can make the app unreachable. Four
        # threads still serves a race office; 512 sockets is far past what the hut
        # link carries. A ten-second channel timeout would cut off a slow download.
        "server_threads": str(int_in_range(settings.get("server_threads"), 8, 4, 64)),
        "server_connection_limit": str(int_in_range(settings.get("server_connection_limit"), 100, 50, 512)),
        "server_channel_timeout": str(int_in_range(settings.get("server_channel_timeout"), 120, 20, 600)),
        "server_public_base_url": normalise_public_base_url(settings.get("server_public_base_url")),
        "track_enabled": bool_to_text(text_to_bool(settings.get("track_enabled"), False)),
        "track_ingest_secret": str(settings.get("track_ingest_secret", "") or "").strip(),
        "assistant_api_key": str(settings.get("assistant_api_key", "") or "").strip(),
        "assistant_model": str(settings.get("assistant_model", "") or "").strip(),
        "assistant_base_url": str(settings.get("assistant_base_url", "") or "").strip(),
        "traccar_base_url": str(settings.get("traccar_base_url", "")).strip(),
        "traccar_token": str(settings.get("traccar_token", "")).strip(),
        "track_poll_seconds": str(int_in_range(settings.get("track_poll_seconds"), 5, 2, 60)),
        "track_retention_days": str(int_in_range(settings.get("track_retention_days"), 90, 1, 3650)),
        "track_sim_enabled": bool_to_text(text_to_bool(settings.get("track_sim_enabled"), False)),
        "gps_finish_horn": bool_to_text(text_to_bool(settings.get("gps_finish_horn"), False)),
        "track_race_poll_enabled": bool_to_text(text_to_bool(settings.get("track_race_poll_enabled"), True)),
        "track_rounding_radius_m": str(int_in_range(settings.get("track_rounding_radius_m"), 50, 10, 500)),
        "track_gate_reach_m": str(int_in_range(settings.get("track_gate_reach_m"), 750, 50, 5000)),
        "hologram_enabled": bool_to_text(text_to_bool(settings.get("hologram_enabled"), False)),
        "hologram_api_key": str(settings.get("hologram_api_key", "") or "").strip(),
        "hologram_org_id": str(settings.get("hologram_org_id", "") or "").strip(),
    }
    if values["horn_input_line"] == "CD":
        values["horn_input_line"] = "DCD"
    # In the ProLog-style interface, enabling manual horn input means the app
    # must use the actual wiring: DTR fires the horn relay, RTS is held asserted
    # as the relay-feedback common, CTS means active/horn on, and DCD means idle.
    if values["horn_input_enabled"] == "1":
        values["horn_line"] = PROLOG_HORN_OUTPUT_LINE
        values["horn_input_line"] = PROLOG_ACTIVE_INPUT_LINE
        values["horn_input_active"] = "1"
        # Assert-to-fire as well. Active-low holds DTR asserted at idle, which on
        # this wiring energises the horn relay and makes its feedback contact
        # report the manual button as permanently pressed. Stored, not just applied
        # at read time, so the tickbox shows what the app is actually doing.
        values["horn_active"] = "1"
    with get_db() as db:
        for key, value in values.items():
            db.execute(
                """
                INSERT INTO hardware_settings (key, value, updated_at) VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                """,
                (key, value, now),
            )
        db.commit()
    forget_public_base_url()
    return settings_change_summary(existing, values)


def hardware_config() -> Dict[str, Any]:
    """Serial horn and manual-horn input configuration stored in the app database.

    Environment variables are still used as one-time/default values if no setting has been saved.
    """
    cfg = dict(DEFAULT_HARDWARE_CONFIG)
    overrides = get_hardware_setting_overrides()
    if overrides:
        cfg["serial_port"] = overrides.get("serial_port", cfg["serial_port"]).strip()
        cfg["horn_line"] = overrides.get("horn_line", cfg["horn_line"]).strip().upper() or PROLOG_HORN_OUTPUT_LINE
        cfg["horn_active"] = text_to_bool(overrides.get("horn_active"), bool(cfg["horn_active"]))
        cfg["horn_duration_ms"] = int_in_range(overrides.get("horn_duration_ms"), int(cfg["horn_duration_ms"]), 50, 5000)
        cfg["horn_input_enabled"] = text_to_bool(overrides.get("horn_input_enabled"), bool(cfg["horn_input_enabled"]))
        cfg["horn_input_line"] = overrides.get("horn_input_line", cfg["horn_input_line"]).strip().upper() or "CTS"
        cfg["horn_input_active"] = text_to_bool(overrides.get("horn_input_active"), bool(cfg["horn_input_active"]))
        cfg["horn_input_poll_ms"] = int_in_range(overrides.get("horn_input_poll_ms"), int(cfg["horn_input_poll_ms"]), 100, 5000)
        cfg["vedirect_smartshunt_port"] = overrides.get("vedirect_smartshunt_port", cfg["vedirect_smartshunt_port"]).strip()
        cfg["vedirect_phoenix_port"] = overrides.get("vedirect_phoenix_port", cfg["vedirect_phoenix_port"]).strip()
        cfg["vedirect_smartsolar_port"] = overrides.get("vedirect_smartsolar_port", cfg["vedirect_smartsolar_port"]).strip()
        cfg["power_sim_enabled"] = text_to_bool(overrides.get("power_sim_enabled"), bool(cfg["power_sim_enabled"]))
        cfg["power_sample_seconds"] = int_in_range(overrides.get("power_sample_seconds"), int(cfg["power_sample_seconds"]), 5, 3600)
        cfg["power_retention_days"] = int_in_range(overrides.get("power_retention_days"), int(cfg["power_retention_days"]), 1, 3650)
        cfg["server_threads"] = int_in_range(overrides.get("server_threads"), int(cfg["server_threads"]), 4, 64)
        cfg["server_connection_limit"] = int_in_range(overrides.get("server_connection_limit"), int(cfg["server_connection_limit"]), 50, 512)
        cfg["server_channel_timeout"] = int_in_range(overrides.get("server_channel_timeout"), int(cfg["server_channel_timeout"]), 20, 600)
        cfg["server_public_base_url"] = normalise_public_base_url(
            overrides.get("server_public_base_url", cfg["server_public_base_url"]))
        cfg["track_enabled"] = text_to_bool(overrides.get("track_enabled"), bool(cfg["track_enabled"]))
        cfg["track_ingest_secret"] = str(overrides.get("track_ingest_secret", cfg["track_ingest_secret"]) or "").strip()
        cfg["traccar_base_url"] = overrides.get("traccar_base_url", cfg["traccar_base_url"]).strip()
        cfg["traccar_token"] = overrides.get("traccar_token", cfg["traccar_token"]).strip()
        cfg["track_poll_seconds"] = int_in_range(overrides.get("track_poll_seconds"), int(cfg["track_poll_seconds"]), 2, 60)
        cfg["track_retention_days"] = int_in_range(overrides.get("track_retention_days"), int(cfg["track_retention_days"]), 1, 3650)
        cfg["track_sim_enabled"] = text_to_bool(overrides.get("track_sim_enabled"), bool(cfg["track_sim_enabled"]))
        cfg["gps_finish_horn"] = text_to_bool(overrides.get("gps_finish_horn"), bool(cfg["gps_finish_horn"]))
        cfg["track_race_poll_enabled"] = text_to_bool(overrides.get("track_race_poll_enabled"), bool(cfg["track_race_poll_enabled"]))
        cfg["track_rounding_radius_m"] = int_in_range(overrides.get("track_rounding_radius_m"), int(cfg["track_rounding_radius_m"]), 10, 500)
        cfg["track_gate_reach_m"] = int_in_range(overrides.get("track_gate_reach_m"), int(cfg["track_gate_reach_m"]), 50, 5000)
        cfg["hologram_enabled"] = text_to_bool(overrides.get("hologram_enabled"), bool(cfg["hologram_enabled"]))
        cfg["hologram_api_key"] = str(overrides.get("hologram_api_key", cfg["hologram_api_key"]) or "").strip()
        cfg["hologram_org_id"] = str(overrides.get("hologram_org_id", cfg["hologram_org_id"]) or "").strip()
        # Saved values have to be read back here as well as written above, or the
        # setting saves, reports itself as changed, and has no effect -- which is
        # what happened: a key typed into Settings left the page still answering
        # with the grammar.
        cfg["assistant_api_key"] = str(overrides.get("assistant_api_key", cfg["assistant_api_key"]) or "").strip()
        cfg["assistant_model"] = str(overrides.get("assistant_model", cfg["assistant_model"]) or "").strip()
        cfg["assistant_base_url"] = str(overrides.get("assistant_base_url", cfg["assistant_base_url"]) or "").strip()
    if cfg["horn_line"] not in ("RTS", "DTR"):
        cfg["horn_line"] = PROLOG_HORN_OUTPUT_LINE
    if cfg.get("horn_input_enabled"):
        # Manual input sensing is wired through the ProLog relay-feedback
        # contact, so RTS cannot be used as the horn output while sensing.
        cfg["horn_line"] = PROLOG_HORN_OUTPUT_LINE
        cfg["horn_input_line"] = PROLOG_ACTIVE_INPUT_LINE
        cfg["horn_input_active"] = True
        # And the horn output has to be assert-to-fire.
        #
        # horn_active=False means active-low: set_serial_output_inactive then holds
        # DTR *asserted* at idle. On ProLog wiring DTR drives the horn relay, so
        # that energises the relay continuously — and its feedback contact then
        # ties RTS to CTS, which is precisely the "manual button pressed" signal.
        # Reported from the hut: clearing this tickbox produced a continuous stream
        # of "Manual horn switch detected" with nobody touching the button, every
        # one of which scheduled an evidence clip; 84 of them then held up the
        # nightly off-site backup. Ticking it again stopped them.
        #
        # The relay being held in is the more serious half of that: it is the horn.
        # So active-low and input sensing are not offered together — sensing wins,
        # because it is the setting that pins down the whole wiring.
        cfg["horn_active"] = True
    if cfg["horn_input_line"] == "CD":
        cfg["horn_input_line"] = "DCD"
    if cfg["horn_input_line"] not in ("CTS", "DSR", "DCD", "RI"):
        cfg["horn_input_line"] = "CTS"
    return cfg


def set_serial_output_inactive(ser: Any, cfg: Dict[str, Any]) -> None:
    """Set serial horn/sense lines to the safe idle state.

    ProLog wiring uses DTR to fire the horn relay.  RTS is not the horn output
    when manual input sensing is enabled; it is the asserted reference/common
    fed through the relay auxiliary contact to either DCD (idle) or CTS
    (horn/manual active).

    Therefore, the safe state is:
    - DTR inactive, so the PC does not fire the horn.
    - RTS asserted when manual input sensing is enabled, so CTS/DCD can be read.
    - RTS inactive otherwise, unless it is deliberately configured as the horn
      output in a non-ProLog setup.
    """
    inactive = not bool(cfg["horn_active"])
    try:
        ser.setDTR(inactive)
    except Exception:
        pass
    try:
        if cfg.get("horn_input_enabled"):
            ser.setRTS(True)
        else:
            ser.setRTS(inactive)
    except Exception:
        pass


def open_serial_for_horn_io(cfg: Dict[str, Any], timeout: float = 0.2) -> Any:
    """Open the configured serial port with horn output lines inactive first.

    Creating Serial(port=...) opens immediately, before the app can deassert
    RTS/DTR.  Build the object closed, set the initial RTS/DTR levels, then open
    it so the adapter applies the safe state on open.
    """
    import serial  # type: ignore

    ser = serial.Serial()  # type: ignore[attr-defined]
    ser.port = cfg["serial_port"]
    ser.baudrate = 9600
    ser.timeout = timeout
    try:
        ser.write_timeout = timeout
    except Exception:
        pass
    try:
        ser.rtscts = False
    except Exception:
        pass
    try:
        ser.dsrdtr = False
    except Exception:
        pass

    inactive = not bool(cfg["horn_active"])
    # Set desired initial line states before open to avoid USB serial startup pulses.
    # DTR must be idle before opening; RTS is asserted only as the ProLog sense
    # reference/common when manual input sensing is enabled.
    initial_states = {"dtr": inactive, "rts": True if cfg.get("horn_input_enabled") else inactive}
    for attr, value in initial_states.items():
        try:
            setattr(ser, attr, value)
        except Exception:
            pass
    ser.open()
    set_serial_output_inactive(ser, cfg)
    return ser


def get_serial_input_line_value(ser: Any, line: str) -> bool:
    """Read the configured serial modem-status input line."""
    line = line.upper()
    if line == "DSR":
        return bool(ser.dsr)
    if line in ("DCD", "CD"):
        return bool(ser.cd)
    if line == "RI":
        return bool(ser.ri)
    return bool(ser.cts)


def read_prolog_horn_feedback(ser: Any) -> Dict[str, bool]:
    """Read the ProLog relay-feedback wiring: RTS common to DCD idle or CTS active."""
    return {
        "cts": bool(ser.cts),
        "dcd": bool(ser.cd),
    }


def read_manual_horn_input(race_id: Optional[int] = None, log_edges: bool = False) -> Dict[str, Any]:
    """Poll the ProLog-style horn relay feedback and optionally log active edges."""
    cfg = hardware_config()
    now = time.time()
    if not cfg.get("horn_input_enabled"):
        HARDWARE_RUNTIME_STATE["last_input_active"] = None
        return {"ok": True, "enabled": False, "configured": bool(cfg["serial_port"]), "message": "Manual horn input sensing is disabled."}
    if not cfg["serial_port"]:
        return {"ok": False, "enabled": True, "configured": False, "message": "No serial port configured for manual horn input sensing."}
    try:
        import serial  # type: ignore
    except ImportError:
        return {"ok": False, "enabled": True, "configured": True, "message": "pyserial is not installed. Run: pip install -r requirements.txt"}

    # fire_horn() holds HARDWARE_IO_LOCK for the whole blast (~1s+) so a poll
    # cannot drop the relay mid-signal.  This status poll runs several times a
    # second from the start console, so it must never *wait* on that lock: if it
    # did, every poll would queue behind the blast and the UI (clock, live log)
    # would appear frozen for the horn's duration.  Take the lock without
    # blocking; if the horn is firing, report the line active straight away and
    # return.  The line genuinely is active while the horn sounds, and recording
    # it as active also prevents the app's own horn being misread as a manual
    # switch edge once the blast ends.
    if not HARDWARE_IO_LOCK.acquire(blocking=False):
        HARDWARE_RUNTIME_STATE["last_input_active"] = True
        return {
            "ok": True,
            "enabled": True,
            "configured": True,
            "serial_port": cfg["serial_port"],
            "wiring": "prolog-relay-feedback",
            "sense_reference": PROLOG_SENSE_REFERENCE_LINE,
            "input_line": f"{PROLOG_ACTIVE_INPUT_LINE}/{PROLOG_IDLE_INPUT_LINE}",
            "active_line": PROLOG_ACTIVE_INPUT_LINE,
            "idle_line": PROLOG_IDLE_INPUT_LINE,
            "cts_active": True,
            "dcd_idle": False,
            "raw_state": True,
            "active": True,
            "horn_active": True,
            "logged": False,
            "event_time": None,
            "event_id": None,
            "message": "Horn firing; manual input read skipped to keep the console responsive.",
        }
    try:
        with open_serial_for_horn_io(cfg, timeout=0.05) as ser:
            feedback = read_prolog_horn_feedback(ser)
    except Exception as exc:  # pragma: no cover - depends on local hardware
        return {"ok": False, "enabled": True, "configured": True, "message": f"Could not read ProLog horn feedback on {cfg['serial_port']}: {exc}"}
    finally:
        HARDWARE_IO_LOCK.release()

    cts_active = bool(feedback["cts"])
    dcd_idle = bool(feedback["dcd"])
    # With this wiring, RTS is the common.  Idle is RTS->DCD.  Active/horn on is
    # RTS->CTS.  Treat CTS as authoritative so a press is not missed during the
    # relay changeover, but report DCD as a useful wiring diagnostic.
    active = cts_active
    last = HARDWARE_RUNTIME_STATE.get("last_input_active")
    logged = False
    event_time = None
    event_id = None
    if log_edges and active and last is False and (now - float(HARDWARE_RUNTIME_STATE.get("last_input_logged_at") or 0.0)) > 0.75:
        event_time = datetime.now().isoformat(timespec="seconds")
        event_id = log_event(race_id, "manual-horn-input", "Manual horn switch detected", "serial-input", {
            "serial_port": cfg["serial_port"],
            "wiring": "prolog-relay-feedback",
            "sense_reference": PROLOG_SENSE_REFERENCE_LINE,
            "active_line": PROLOG_ACTIVE_INPUT_LINE,
            "idle_line": PROLOG_IDLE_INPUT_LINE,
            "cts_active": cts_active,
            "dcd_idle": dcd_idle,
            "active": active,
        })
        if race_id:
            VIDEO_CLIP_SCHEDULER(int(race_id), "manual_horn", event_time, event_id=event_id, label="Manual horn switch detected")
        HARDWARE_RUNTIME_STATE["last_input_logged_at"] = now
        logged = True
    HARDWARE_RUNTIME_STATE["last_input_active"] = active

    if active:
        state_text = "active"
    elif dcd_idle:
        state_text = "idle"
    else:
        state_text = "not detected"
    return {
        "ok": True,
        "enabled": True,
        "configured": True,
        "serial_port": cfg["serial_port"],
        "wiring": "prolog-relay-feedback",
        "sense_reference": PROLOG_SENSE_REFERENCE_LINE,
        "input_line": f"{PROLOG_ACTIVE_INPUT_LINE}/{PROLOG_IDLE_INPUT_LINE}",
        "active_line": PROLOG_ACTIVE_INPUT_LINE,
        "idle_line": PROLOG_IDLE_INPUT_LINE,
        "cts_active": cts_active,
        "dcd_idle": dcd_idle,
        "raw_state": cts_active,
        "active": active,
        "logged": logged,
        "event_time": event_time,
        "event_id": event_id,
        "message": f"Manual horn input: {state_text}",
    }


def fire_horn(duration_ms: Optional[int] = None) -> Dict[str, Any]:
    """Fire the horn using a serial-control line, or simulate if no port is configured."""
    cfg = hardware_config()
    duration_ms = int(duration_ms or cfg["horn_duration_ms"])
    if duration_ms < 50:
        duration_ms = 50
    if duration_ms > 5000:
        duration_ms = 5000

    port = cfg["serial_port"]
    if not port:
        return {"ok": True, "mode": "simulated", "message": f"Simulated {duration_ms} ms horn blast. Configure a serial port on the Settings page to enable real horn output."}

    try:
        import serial  # type: ignore
    except ImportError:
        return {"ok": False, "mode": "error", "message": "pyserial is not installed. Run: pip install -r requirements.txt"}

    line = PROLOG_HORN_OUTPUT_LINE if cfg.get("horn_input_enabled") else cfg["horn_line"]
    active = bool(cfg["horn_active"])
    try:
        with HARDWARE_IO_LOCK:
            with open_serial_for_horn_io(cfg, timeout=0.2) as ser:
                if line == "DTR":
                    ser.setDTR(active)
                    time.sleep(duration_ms / 1000.0)
                    ser.setDTR(not active)
                else:
                    ser.setRTS(active)
                    time.sleep(duration_ms / 1000.0)
                    ser.setRTS(not active)
                set_serial_output_inactive(ser, cfg)
        return {"ok": True, "mode": "serial", "message": f"Horn fired on {port} {line} for {duration_ms} ms."}
    except Exception as exc:  # pragma: no cover - depends on local hardware
        return {"ok": False, "mode": "error", "message": f"Could not fire horn on {port}: {exc}"}
