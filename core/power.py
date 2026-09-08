"""Hut off-grid power monitoring via Victron VE.Direct.

Reads three Victron devices over their VE.Direct serial ports (a SmartShunt
battery monitor, a Phoenix IP43 charger, and a SmartSolar MPPT), stores periodic
samples in a *separate* SQLite database (data/power_history.db, independent of
the race database), and reports live status for the dashboard and a history
graph.

Deliberately monitor-only: we parse the read-only VE.Direct text telemetry and
never send commands to the charger. The design mirrors core.weather_store (a
store + a daemon poller + a status dict); serial handling mirrors core.horn
(late `import serial`, graceful degradation when pyserial or the port is
absent). A built-in simulator lets the whole feature run before the cables are
wired -- real configured ports always override the simulator.
"""
from __future__ import annotations

import json
import math
import sqlite3
import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from core import appstate
from core import db as core_db
from core.horn import hardware_config
from core.settings import int_in_range

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
# VE.Direct devices report at 19200 baud, 8N1.
VEDIRECT_BAUD = 19200

# Victron charge-state (CS) codes -> human labels, shared by the MPPT and charger.
CHARGE_STATE_NAMES: Dict[int, str] = {
    0: "Off",
    1: "Low power",
    2: "Fault",
    3: "Bulk",
    4: "Absorption",
    5: "Float",
    6: "Storage",
    7: "Equalize",
    9: "Inverting",
    11: "Power supply",
    245: "Starting-up",
    246: "Repeated absorption",
    247: "Auto equalize",
    248: "BatterySafe",
    252: "External control",
}


def power_config() -> Dict[str, Any]:
    """Return VE.Direct power-monitoring settings.

    Read through core.horn.hardware_config so the env-var defaults and the saved
    hardware_settings overrides (where these keys live) resolve the same way for
    the poller as they do for the Settings page.
    """
    cfg = hardware_config()
    return {
        "smartshunt_port": str(cfg.get("vedirect_smartshunt_port", "") or "").strip(),
        "phoenix_port": str(cfg.get("vedirect_phoenix_port", "") or "").strip(),
        "smartsolar_port": str(cfg.get("vedirect_smartsolar_port", "") or "").strip(),
        "sim_enabled": bool(cfg.get("power_sim_enabled", False)),
        "sample_seconds": int_in_range(cfg.get("power_sample_seconds"), 30, 5, 3600),
        "retention_days": int_in_range(cfg.get("power_retention_days"), 365, 1, 3650),
    }


def any_port_configured(cfg: Optional[Dict[str, Any]] = None) -> bool:
    """True if at least one VE.Direct serial port has been set."""
    cfg = cfg or power_config()
    return bool(cfg["smartshunt_port"] or cfg["phoenix_port"] or cfg["smartsolar_port"])


# ---------------------------------------------------------------------------
# VE.Direct text-protocol parser (pure, hardware-free -> fully unit-testable)
# ---------------------------------------------------------------------------
def parse_vedirect_stream(buffer: bytes) -> Tuple[List[Dict[str, str]], bytes]:
    """Parse complete VE.Direct frames from a byte buffer.

    VE.Direct devices emit ``\\r\\n<label>\\t<value>`` records, grouped into
    frames terminated by a ``Checksum`` record whose one-byte value makes the
    sum of every byte in the frame equal 0 (mod 256). The checksum byte can
    itself be \\r, \\n or \\t, so we scan record-by-record rather than naively
    splitting on newlines, and special-case the single-byte ``Checksum`` value.

    Returns ``(frames, remainder)`` where each frame is a ``{label: value}``
    dict (excluding the Checksum field) that passed checksum validation, and
    remainder is the trailing partial bytes to prepend to the next read.
    """
    frames: List[Dict[str, str]] = []
    fields: Dict[str, str] = {}
    length = len(buffer)
    first = buffer.find(b"\r\n")
    if first == -1:
        return frames, buffer
    pos = first
    frame_start = pos
    while pos < length:
        if buffer[pos:pos + 2] != b"\r\n":
            nxt = buffer.find(b"\r\n", pos)
            if nxt == -1:
                break
            pos = nxt
        tab = buffer.find(b"\t", pos + 2)
        if tab == -1:
            break  # incomplete label
        label = buffer[pos + 2:tab]
        if label == b"Checksum":
            if tab + 1 >= length:
                break  # need the checksum byte itself
            end = tab + 2  # one byte after the tab
            frame_bytes = buffer[frame_start:end]
            if sum(frame_bytes) % 256 == 0:
                frames.append(dict(fields))
            fields = {}
            pos = end
            frame_start = pos
        else:
            nl = buffer.find(b"\r\n", tab + 1)
            if nl == -1:
                break  # incomplete value
            value = buffer[tab + 1:nl]
            try:
                fields[label.decode("ascii")] = value.decode("ascii").strip()
            except Exception:
                pass
            pos = nl
    remainder = buffer[frame_start:]
    return frames, remainder


def _num(value: Optional[str], scale: float = 1.0) -> Optional[float]:
    """Parse a VE.Direct integer/float field, applying a unit scale."""
    if value is None or value == "":
        return None
    try:
        return int(value) * scale
    except (TypeError, ValueError):
        try:
            return float(value) * scale
        except (TypeError, ValueError):
            return None


def _charge_state_name(value: Optional[str]) -> Optional[str]:
    if value is None or value == "":
        return None
    try:
        return CHARGE_STATE_NAMES.get(int(value), f"State {int(value)}")
    except (TypeError, ValueError):
        return str(value)


def normalise_frame(kind: str, fields: Dict[str, str]) -> Dict[str, Any]:
    """Convert raw VE.Direct fields to normalised units for one device kind.

    kind is one of "battery" (SmartShunt), "solar" (SmartSolar MPPT) or
    "charger" (Phoenix). mV/mA are scaled to V/A, SOC per-mille to percent.
    """
    if kind == "battery":
        return {
            "v": _num(fields.get("V"), 0.001),
            "i": _num(fields.get("I"), 0.001),
            "p": _num(fields.get("P")),
            "soc": _num(fields.get("SOC"), 0.1),
            "ttg": _num(fields.get("TTG")),  # minutes; -1 == not discharging
            "ce": _num(fields.get("CE"), 0.001),
        }
    if kind == "solar":
        return {
            "v": _num(fields.get("V"), 0.001),
            "i": _num(fields.get("I"), 0.001),
            "pv_v": _num(fields.get("VPV"), 0.001),
            "pv_w": _num(fields.get("PPV")),
            "state": _charge_state_name(fields.get("CS")),
            "yield_wh": _num(fields.get("H20"), 10.0),  # 0.01 kWh -> Wh
            "err": _num(fields.get("ERR")),
        }
    if kind == "charger":
        return {
            "v": _num(fields.get("V"), 0.001),
            "i": _num(fields.get("I"), 0.001),
            "state": _charge_state_name(fields.get("CS")),
            "err": _num(fields.get("ERR")),
        }
    return {}


# ---------------------------------------------------------------------------
# Serial reading (mirrors core.horn's graceful-degradation idioms)
# ---------------------------------------------------------------------------
def read_vedirect_once(port: str, read_seconds: float = 1.5) -> Optional[Dict[str, str]]:
    """Read a VE.Direct port and return the latest merged raw fields, or None.

    Returns None (never raises) when the port is blank, pyserial is missing, the
    port cannot be opened, or no complete frame arrives -- the caller treats that
    as "not connected". A device may split its data across consecutive frames, so
    fields from every frame read in the window are merged (last value wins).
    """
    if not port:
        return None
    try:
        import serial  # type: ignore
    except ImportError:  # pragma: no cover - pyserial ships in requirements
        return None
    ser = None
    try:
        ser = serial.Serial()  # type: ignore[attr-defined]
        ser.port = port
        ser.baudrate = VEDIRECT_BAUD
        ser.timeout = 0.3
        ser.open()
    except Exception:  # pragma: no cover - depends on local hardware
        if ser is not None:
            try:
                ser.close()
            except Exception:
                pass
        return None

    buffer = b""
    frames: List[Dict[str, str]] = []
    try:  # pragma: no cover - depends on local hardware
        deadline = time.monotonic() + read_seconds
        while time.monotonic() < deadline:
            chunk = ser.read(256)
            if chunk:
                buffer += chunk
                new_frames, buffer = parse_vedirect_stream(buffer)
                frames.extend(new_frames)
                if len(buffer) > 8192:
                    buffer = buffer[-4096:]
    except Exception:  # pragma: no cover - depends on local hardware
        pass
    finally:
        try:
            ser.close()
        except Exception:
            pass

    if not frames:
        return None
    merged: Dict[str, str] = {}
    for frame in frames:
        merged.update(frame)
    return merged


# ---------------------------------------------------------------------------
# Simulator (so the feature can be demoed before the cables are wired)
# ---------------------------------------------------------------------------
def simulate_reading(now: Optional[float] = None) -> Dict[str, Dict[str, Any]]:
    """Return plausible battery/solar/charger values for demo/preview.

    Solar follows a day/night curve; battery SOC drifts on a slow sine so the
    history graph shows movement. Deterministic given the clock -- no randomness.
    """
    now = now if now is not None else time.time()
    hour = (now % 86400) / 3600.0  # 0..24 local-ish; fine for a demo curve
    # Solar: a bell curve peaking at ~13:00, zero before 06:00 / after 20:00.
    daylight = max(0.0, math.sin((hour - 6.0) / 14.0 * math.pi)) if 6.0 <= hour <= 20.0 else 0.0
    pv_w = round(daylight * 780.0, 1)
    # Battery SOC oscillates slowly so the graph is interesting.
    soc = 60.0 + 30.0 * math.sin(now / 4000.0)
    soc = max(15.0, min(100.0, soc))
    battery_v = round(12.0 + (soc / 100.0) * 1.75, 2)  # ~12.0-13.75 V
    load_a = 2.4  # steady hut load
    solar_a = round(pv_w / battery_v, 2) if battery_v else 0.0
    battery_i = round(solar_a - load_a, 2)  # + = charging, - = discharging
    discharging = battery_i < 0
    ttg = round((soc - 10.0) / 100.0 * 2000.0 / max(0.1, load_a - solar_a)) if discharging else -1
    if pv_w > 400:
        solar_state = "Bulk"
    elif pv_w > 30:
        solar_state = "Absorption" if soc > 90 else "Bulk"
    else:
        solar_state = "Off"
    charger_state = "Off"  # RO plugs shore power in manually; idle by default
    return {
        "battery": {"v": battery_v, "i": battery_i, "soc": round(soc, 1), "ttg": ttg,
                    "p": round(battery_v * battery_i, 1), "ce": None},
        "solar": {"v": battery_v, "i": solar_a, "pv_v": round(battery_v * 2.6, 1),
                  "pv_w": pv_w, "state": solar_state, "yield_wh": round(2500 * daylight, 0), "err": 0},
        "charger": {"v": battery_v, "i": 0.0, "state": charger_state, "err": 0},
    }


# ---------------------------------------------------------------------------
# Reading assembly
# ---------------------------------------------------------------------------
_EMPTY_READING: Dict[str, Any] = {
    "battery_v": None, "battery_i": None, "battery_soc": None, "battery_ttg": None,
    "solar_v": None, "solar_i": None, "solar_pv_v": None, "solar_pv_w": None,
    "solar_state": None, "solar_yield_wh": None,
    "charger_v": None, "charger_i": None, "charger_state": None,
}


def collect_reading(cfg: Optional[Dict[str, Any]] = None) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any], Dict[str, Any]]:
    """Gather one combined reading from the three devices (real or simulated).

    Returns ``(reading, devices, raw)``. ``reading`` maps to the DB columns and
    is None only when nothing at all could be read (no ports, simulator off).
    ``devices`` describes each device's connected/source state for the UI.
    """
    cfg = cfg or power_config()
    sim = cfg["sim_enabled"]
    sim_reading = simulate_reading() if sim else {}
    reading = dict(_EMPTY_READING)
    devices: Dict[str, Any] = {}
    raw: Dict[str, Any] = {}
    any_data = False

    # SmartShunt (battery)
    port = cfg["smartshunt_port"]
    if port:
        fields = read_vedirect_once(port)
        if fields:
            b = normalise_frame("battery", fields)
            reading.update({"battery_v": b["v"], "battery_i": b["i"], "battery_soc": b["soc"], "battery_ttg": b["ttg"]})
            devices["smartshunt"] = {"connected": True, "source": "serial", "port": port, **b}
            raw["smartshunt"] = fields
            any_data = True
        else:
            devices["smartshunt"] = {"connected": False, "source": "serial", "port": port}
    elif sim:
        b = sim_reading["battery"]
        reading.update({"battery_v": b["v"], "battery_i": b["i"], "battery_soc": b["soc"], "battery_ttg": b["ttg"]})
        devices["smartshunt"] = {"connected": True, "source": "sim", **b}
        any_data = True
    else:
        devices["smartshunt"] = {"connected": False, "source": "none"}

    # SmartSolar MPPT
    port = cfg["smartsolar_port"]
    if port:
        fields = read_vedirect_once(port)
        if fields:
            s = normalise_frame("solar", fields)
            reading.update({"solar_v": s["v"], "solar_i": s["i"], "solar_pv_v": s["pv_v"],
                            "solar_pv_w": s["pv_w"], "solar_state": s["state"], "solar_yield_wh": s["yield_wh"]})
            devices["smartsolar"] = {"connected": True, "source": "serial", "port": port, **s}
            raw["smartsolar"] = fields
            any_data = True
        else:
            devices["smartsolar"] = {"connected": False, "source": "serial", "port": port}
    elif sim:
        s = sim_reading["solar"]
        reading.update({"solar_v": s["v"], "solar_i": s["i"], "solar_pv_v": s["pv_v"],
                        "solar_pv_w": s["pv_w"], "solar_state": s["state"], "solar_yield_wh": s["yield_wh"]})
        devices["smartsolar"] = {"connected": True, "source": "sim", **s}
        any_data = True
    else:
        devices["smartsolar"] = {"connected": False, "source": "none"}

    # Phoenix charger
    port = cfg["phoenix_port"]
    if port:
        fields = read_vedirect_once(port)
        if fields:
            c = normalise_frame("charger", fields)
            reading.update({"charger_v": c["v"], "charger_i": c["i"], "charger_state": c["state"]})
            devices["phoenix"] = {"connected": True, "source": "serial", "port": port, **c}
            raw["phoenix"] = fields
            any_data = True
        else:
            devices["phoenix"] = {"connected": False, "source": "serial", "port": port}
    elif sim:
        c = sim_reading["charger"]
        reading.update({"charger_v": c["v"], "charger_i": c["i"], "charger_state": c["state"]})
        devices["phoenix"] = {"connected": True, "source": "sim", **c}
        any_data = True
    else:
        devices["phoenix"] = {"connected": False, "source": "none"}

    return (reading if any_data else None), devices, raw


def hut_consumption_w(battery_v: Optional[float], battery_i: Optional[float], solar_i: Optional[float], charger_i: Optional[float]) -> Optional[float]:
    """Estimate the hut's DC load in watts from the three devices.

    Everything hangs on the battery bus, and the SmartShunt measures only the
    battery's net current (+ charging), so by Kirchhoff's current law the load is
    what the sources deliver minus what goes into the battery:

        load_A = solar_current + charger_current − battery_current
        load_W = bus_voltage × load_A

    Sources that are off/absent contribute 0. Returns None when the battery
    voltage or current is unknown (can't compute), and never a small negative
    value from measurement noise (clamped at 0).
    """
    if battery_v is None or battery_i is None:
        return None
    load_a = (solar_i or 0.0) + (charger_i or 0.0) - battery_i
    return round(max(0.0, battery_v * load_a), 1)


def status_message(reading: Optional[Dict[str, Any]], cfg: Dict[str, Any]) -> str:
    """One-line human summary for the dashboard card / poller status."""
    if reading is None:
        if not any_port_configured(cfg) and not cfg["sim_enabled"]:
            return "No VE.Direct ports configured. Set them in Settings, or enable the simulator."
        return "No data from the VE.Direct devices yet."
    parts = []
    if reading.get("battery_soc") is not None:
        parts.append(f"Battery {reading['battery_soc']:.0f}%")
    if reading.get("battery_v") is not None:
        parts.append(f"{reading['battery_v']:.2f} V")
    if reading.get("battery_i") is not None:
        parts.append(f"{reading['battery_i']:+.1f} A")
    if reading.get("solar_pv_w") is not None:
        state = reading.get("solar_state") or ""
        parts.append(f"Solar {reading['solar_pv_w']:.0f} W{f' ({state})' if state else ''}")
    load_w = hut_consumption_w(reading.get("battery_v"), reading.get("battery_i"), reading.get("solar_i"), reading.get("charger_i"))
    if load_w is not None:
        parts.append(f"Load {load_w:.0f} W")
    if reading.get("charger_state"):
        parts.append(f"Charger {reading['charger_state']}")
    return " · ".join(parts) if parts else "Power data available."


# ---------------------------------------------------------------------------
# Separate history database (data/power_history.db)
# ---------------------------------------------------------------------------
POWER_DB_PATH = appstate.DATA_DIR / "power_history.db"
POWER_DB_INIT_LOCK = threading.Lock()
POWER_DB_INITIALIZED = False

_SAMPLE_COLUMNS = [
    "battery_v", "battery_i", "battery_soc", "battery_ttg",
    "solar_v", "solar_i", "solar_pv_v", "solar_pv_w", "solar_state", "solar_yield_wh",
    "charger_v", "charger_i", "charger_state",
]


def get_power_db() -> sqlite3.Connection:
    """Open the separate power-history SQLite database with row access.

    Busy timeout as for the race database (see core.db). The power monitor
    writes a sample every 30 seconds for ever, so this is the database most likely
    to be mid-write when something else wants it.
    """
    conn = sqlite3.connect(POWER_DB_PATH, timeout=core_db.busy_timeout_seconds())
    conn.row_factory = sqlite3.Row
    return conn


def init_power_db() -> None:
    """Create the power-history schema once per process (own guard, own DB)."""
    global POWER_DB_INITIALIZED
    if POWER_DB_INITIALIZED and POWER_DB_PATH.exists():
        return
    with POWER_DB_INIT_LOCK:
        if POWER_DB_INITIALIZED and POWER_DB_PATH.exists():
            return
        POWER_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        with get_power_db() as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS power_samples (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    sample_time REAL NOT NULL,
                    sample_iso TEXT NOT NULL,
                    battery_v REAL, battery_i REAL, battery_soc REAL, battery_ttg REAL,
                    solar_v REAL, solar_i REAL, solar_pv_v REAL, solar_pv_w REAL,
                    solar_state TEXT, solar_yield_wh REAL,
                    charger_v REAL, charger_i REAL, charger_state TEXT,
                    raw_json TEXT
                )
                """
            )
            db.execute("CREATE INDEX IF NOT EXISTS idx_power_samples_time ON power_samples(sample_time)")
            db.commit()
        POWER_DB_INITIALIZED = True


def insert_power_sample(reading: Dict[str, Any], raw: Optional[Dict[str, Any]] = None, retention_days: Optional[int] = None) -> Dict[str, Any]:
    """Store one combined power sample and purge rows past the retention window.

    Off-grid telemetry is not race-tied, so retention is a simple flat age cut
    (default from power_config; overridable for tests).
    """
    init_power_db()
    now = time.time()
    iso = datetime.now().isoformat(timespec="seconds")
    if retention_days is None:
        retention_days = power_config()["retention_days"]
    values = [now, iso] + [reading.get(col) for col in _SAMPLE_COLUMNS] + [json.dumps(raw or {}, ensure_ascii=False)[:20000]]
    placeholders = ", ".join(["?"] * len(values))
    columns = "sample_time, sample_iso, " + ", ".join(_SAMPLE_COLUMNS) + ", raw_json"
    with get_power_db() as db:
        db.execute(f"INSERT INTO power_samples ({columns}) VALUES ({placeholders})", values)
        cutoff = now - int(retention_days) * 86400
        db.execute("DELETE FROM power_samples WHERE sample_time < ?", (cutoff,))
        db.commit()
    return {"t": now, "iso": iso, **{col: reading.get(col) for col in _SAMPLE_COLUMNS}}


def _row_to_sample(row: sqlite3.Row) -> Dict[str, Any]:
    sample = {"t": row["sample_time"], "iso": row["sample_iso"]}
    for col in _SAMPLE_COLUMNS:
        sample[col] = row[col]
    sample["load_w"] = hut_consumption_w(row["battery_v"], row["battery_i"], row["solar_i"], row["charger_i"])
    return sample


def power_history(minutes: int = 60) -> List[Dict[str, Any]]:
    """Return recent power samples (compact, no raw_json) for charts."""
    init_power_db()
    minutes = int_in_range(minutes, 60, 1, 60 * 24 * 30)  # up to 30 days
    since = time.time() - minutes * 60
    try:
        with get_power_db() as db:
            rows = db.execute(
                "SELECT * FROM power_samples WHERE sample_time >= ? ORDER BY sample_time ASC, id ASC",
                (since,),
            ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [_row_to_sample(r) for r in rows]


def latest_power_reading() -> Optional[Dict[str, Any]]:
    """Return the most recent stored power sample, or None."""
    init_power_db()
    try:
        with get_power_db() as db:
            row = db.execute("SELECT * FROM power_samples ORDER BY sample_time DESC, id DESC LIMIT 1").fetchone()
    except sqlite3.OperationalError:
        return None
    return _row_to_sample(row) if row else None


# ---------------------------------------------------------------------------
# Background poller + status (mirrors core.weather_store)
# ---------------------------------------------------------------------------
POWER_MONITOR_STATE: Dict[str, Any] = {
    "started": False,
    "last_status": {"ok": False, "message": "Power monitor has not started yet.", "devices": {}},
    "last_poll_at": None,
}
POWER_MONITOR_LOCK = threading.Lock()


def _build_status(reading: Optional[Dict[str, Any]], devices: Dict[str, Any], cfg: Dict[str, Any]) -> Dict[str, Any]:
    reading = reading or {}
    return {
        "ok": bool(devices) and any(d.get("connected") for d in devices.values()),
        "message": status_message(reading if reading else None, cfg),
        "devices": devices,
        "sim": cfg["sim_enabled"],
        "configured": any_port_configured(cfg),
        "battery": {"soc": reading.get("battery_soc"), "v": reading.get("battery_v"),
                    "i": reading.get("battery_i"), "ttg": reading.get("battery_ttg")},
        "solar": {"pv_w": reading.get("solar_pv_w"), "state": reading.get("solar_state"),
                  "yield_wh": reading.get("solar_yield_wh")},
        "charger": {"state": reading.get("charger_state"), "i": reading.get("charger_i")},
        "load": {"w": hut_consumption_w(reading.get("battery_v"), reading.get("battery_i"),
                                        reading.get("solar_i"), reading.get("charger_i"))},
    }


def power_background_loop() -> None:
    """Continuously read the VE.Direct devices and store samples."""
    while True:
        sleep_seconds = 30
        try:
            cfg = power_config()
            sleep_seconds = cfg["sample_seconds"]
            reading, devices, raw = collect_reading(cfg)
            if reading is not None:
                insert_power_sample(reading, raw, retention_days=cfg["retention_days"])
            status = _build_status(reading, devices, cfg)
            with POWER_MONITOR_LOCK:
                POWER_MONITOR_STATE["last_status"] = status
                POWER_MONITOR_STATE["last_poll_at"] = time.time()
        except Exception as exc:
            with POWER_MONITOR_LOCK:
                POWER_MONITOR_STATE["last_status"] = {"ok": False, "message": f"Power monitor error: {exc}", "devices": {}}
                POWER_MONITOR_STATE["last_poll_at"] = time.time()
            sleep_seconds = 30
        time.sleep(max(5, min(3600, int(sleep_seconds))))


def power_runtime_status() -> Dict[str, Any]:
    """Return the latest poller status for the dashboard card and the API."""
    with POWER_MONITOR_LOCK:
        status = dict(POWER_MONITOR_STATE.get("last_status") or {})
        started = bool(POWER_MONITOR_STATE.get("started"))
        last_poll_at = POWER_MONITOR_STATE.get("last_poll_at")
    cfg = power_config()
    if not status or "devices" not in status:
        status = {"ok": False, "message": "Power monitor is starting…", "devices": {}}
    status["background_started"] = started
    status["sim"] = cfg["sim_enabled"]
    status["configured"] = any_port_configured(cfg)
    if last_poll_at:
        status["last_poll_at"] = last_poll_at
    return status


def start_power_monitor_worker() -> None:
    """Start the power-monitor polling thread if it is not already running."""
    with POWER_MONITOR_LOCK:
        if POWER_MONITOR_STATE.get("started"):
            return
        POWER_MONITOR_STATE["started"] = True
    thread = threading.Thread(target=power_background_loop, name="power-monitor", daemon=True)
    thread.start()

# --------------------------------------------------------------------------
# "The battery is not getting back to full" — the winter warning
# --------------------------------------------------------------------------
# A solar hut fails slowly and quietly. Nothing looks wrong on any one day: the
# battery is high, the sun comes up, the charger works. What changes is that the
# bank stops quite reaching full, and then does so a little less each day, and by
# the time the voltage is visibly low there are days rather than weeks in hand.
#
# The club's array is flat on the roof, which at this latitude is close to the
# worst case in December: the sun peaks at 13.7 degrees and a flat panel is
# nearly edge-on to it. Measured against a 24 W standing load, a midwinter day —
# even a clear one — does not replace what the night took. So the interesting
# question is not "is the battery low" but "has it stopped recovering", and that
# is answerable weeks before it matters.
FULL_SOC_PCT = 99.0          # they float at 99.8-100; anything at 99 has finished
RECHARGE_WATCH_DAYS = 3      # missing one dull day is ordinary; three is a trend
RECHARGE_URGENT_DAYS = 7
RECHARGE_URGENT_SOC = 60.0   # or simply low, however long it has been falling


def days_since_full(days_back: int = 21) -> Optional[Dict[str, Any]]:
    """How long since the bank last reached full, and where it stands now.

    ``None`` when there is nothing to judge on — monitoring off, or not enough
    history yet. Silence beats a warning built on two days of data.
    """
    init_power_db()
    since = time.time() - days_back * 86400
    try:
        with get_power_db() as db:
            rows = db.execute(
                "SELECT sample_time, sample_iso, battery_soc FROM power_samples"
                " WHERE sample_time >= ? AND battery_soc IS NOT NULL"
                " ORDER BY sample_time ASC", (since,)).fetchall()
    except sqlite3.OperationalError:
        return None
    if len(rows) < 200:                      # roughly two days at the usual rate
        return None
    by_day: Dict[str, float] = {}
    for row in rows:
        day = str(row["sample_iso"])[:10]
        soc = float(row["battery_soc"])
        by_day[day] = max(by_day.get(day, soc), soc)
    if not by_day:
        return None
    days = sorted(by_day)
    # Count back from the most recent day; today is excluded from the streak
    # because it may simply not have got there yet.
    missed = 0
    for day in reversed(days[:-1]):
        if by_day[day] >= FULL_SOC_PCT:
            break
        missed += 1
    latest = float(rows[-1]["battery_soc"])
    return {"days": missed, "soc": latest, "peak_today": by_day[days[-1]],
            "last_full": next((d for d in reversed(days) if by_day[d] >= FULL_SOC_PCT), ""),
            "history_days": len(days)}


def recharge_warning() -> Optional[Dict[str, Any]]:
    """A dashboard warning when the bank has stopped reaching full. Never raises.

    Quiet while the sun is keeping up, which through the summer measured here is
    every single day.
    """
    try:
        cfg = power_config()
        if not cfg.get("enabled"):
            return None
        state = days_since_full()
        if not state:
            return None
        days, soc = state["days"], state["soc"]
        if soc <= RECHARGE_URGENT_SOC or days >= RECHARGE_URGENT_DAYS:
            level = "urgent"
        elif days >= RECHARGE_WATCH_DAYS:
            level = "watch"
        else:
            return None
        return {"level": level, "days": days, "soc": soc,
                "peak_today": state["peak_today"], "last_full": state["last_full"]}
    except Exception:
        return None
