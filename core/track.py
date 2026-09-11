"""Yacht tracking and GPS-based automated finishes.

Boats carry Queclink GL521MG LTE trackers that report to a Traccar server on the
relay (Traccar speaks the Queclink protocol natively). Because the hut app is
outbound-only behind a Cloudflare Tunnel, it *pulls* positions from Traccar's
REST API rather than receiving raw device connections. This module:

  * reads Traccar settings from the hardware config (core.horn),
  * pulls the fleet's latest positions (or synthesises them with a simulator so
    the feature can be demoed without hardware or 4G coverage),
  * stores them in a *separate* SQLite database (data/track_positions.db,
    independent of the race database) mirroring core.power's store pattern,
  * maps trackers to boats (a permanent per-boat assignment plus an optional
    per-race loaner override on the entry),
  * detects finish-line crossings and either proposes a finish for the race
    officer to confirm, or auto-confirms it when the race is armed for unmanned
    finishing.

The design mirrors core.power / core.weather_store (a store + a daemon poller +
a status dict). Nothing here is trusted blind: GPS finishes are interpolated
between coarse fixes and are always reviewable against the finish video and
adjustable by the RO. See core/power.py for the sibling patterns reused here.
"""
from __future__ import annotations

import json
import math
import re
import sqlite3
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from core.activitylog import log_activity
from core import appstate, horn
from core import db as core_db
from core.classconfig import entry_class_labels_map, race_class_config
from core.db import get_db, init_db, row_get
from core import rounding
from core.eventlog import log_event
from core import marks
from core.races import get_boats_by_id, race_first_start_dt
from core.scoring import corrected_seconds_for_result
from core.series import rating_from_entry_for_result
from core.settings import int_in_range
from core.timeutils import haversine_nm

# A finish crossing is ignored until this many seconds after the race's first
# start — this is what stops the *start* crossing (over the same line) from being
# mis-read as a finish; the finish direction check is the other guard.
MIN_FINISH_ELAPSED_S = 120

# A fix dated further ahead than this is treated as a bad clock on the tracker.
# It matters because the live path only accepts fixes *newer* than the newest
# stored for that device: one fix dated next week would otherwise silently block
# every real fix from that tracker from then on (seen in testing with a stale
# simulated fix 48 minutes in the future).
FIX_FUTURE_TOLERANCE_S = 300.0

# Freshness thresholds for a tracker's "last reported" RAG indicator: a fix
# within GREEN seconds is live (green), within AMBER is recent (amber), older is
# stale (red). A tracker counts as "reporting" if its last fix is within AMBER.
TRACK_RAG_GREEN_S = 300      # 5 minutes
TRACK_RAG_AMBER_S = 3600     # 1 hour


# Battery, as a percentage. The GL521MG reports it (@Track protocol V3.05, the
# <Battery Percentage> field) and Traccar puts it in a position's attributes; the app
# used to drop those attributes on the floor in both ingest paths.
#
# The thresholds are about race days rather than about the battery: these are asset
# trackers set to a reporting rate far above their standby design, so what matters is
# "will this last a race" and "does it need charging before Saturday". Below 25% is a
# unit to charge; below 10% is one that may not finish the race.
BATTERY_LOW_PCT = 25.0
BATTERY_CRITICAL_PCT = 10.0
# Traccar names the field differently depending on protocol and firmware. batteryLevel
# is the percentage Queclink sends; Teltonika sends its AVL IO 113 instead, which Traccar
# surfaces as io113. `battery` is volts and is deliberately NOT treated as a percentage,
# because guessing a cell chemistry to convert it would put a made-up number on a page
# people use to decide what to charge.
#
# io113 was confirmed against the live feed rather than a protocol table: over 3.5 hours
# an ATC700 reported it falling 95 -> 65 in 5-point steps while its `battery` voltage fell
# 4.055 -> 3.710, the two moving together the whole way. That is worth the paragraph,
# because the alternative reading -- some unrelated IO port that happens to look like a
# percentage -- would put a fictitious battery on the dashboard.
#
# The device this fixes is the one that needed it. Reporting every 10 s, the ATC700 drains
# about 8.6%/h against the GL521MG's 0.9%/h at 60 s: flat in half a day rather than half a
# week. Reading only batteryLevel meant the warning covered the trackers that barely need
# it and stayed silent for the one that does.
BATTERY_PERCENT_KEYS = ("batteryLevel", "batterylevel", "battery_level", "batteryPercent",
                        "io113")


def battery_from_attributes(attrs: Any) -> Optional[float]:
    """The battery percentage in a Traccar position's attributes, or None.

    None when the device does not report one — shown as a dash rather than a zero,
    which would read as "flat" instead of "not known".
    """
    if not isinstance(attrs, dict):
        return None
    for key in BATTERY_PERCENT_KEYS:
        if key not in attrs:
            continue
        try:
            pct = float(attrs[key])
        except (TypeError, ValueError):
            continue
        if 0.0 <= pct <= 100.0:
            return pct
    return None


# Fix-quality fields kept alongside each position. Stored raw and interpreted
# nowhere here: a gate wants tuning, and a value thrown away at ingest cannot be
# looked at again.
#
# Altitude is the useful one for a boat, because it is the only field whose true
# value is known in advance — a boat is at sea level, so a departure measures the
# error directly. Two GL521MG failures during the races of 16 August were diagnosed
# from it afterwards: one receiver's solution diverged to -1060 m altitude with 627 m
# of horizontal error before snapping back, the other held a +30 m bias for a whole
# race and passed its marks 20-50 m to the south. `hdop` sat at 1-3 through both and
# flagged neither. None of it was in this database; it came from Traccar's own
# retention, which is ten days, so the next such fault would be undiagnosable.
QUALITY_FIELDS = ("altitude", "accuracy", "hdop", "pdop", "sat", "rssi", "valid", "protocol")

# First eight digits of an IMEI are its Type Allocation Code. It separates devices
# Traccar's `protocol` lumps together — the ATC700 and the RUTX50 are both "teltonika"
# and could hardly be less alike — and two units of the same model share it exactly:
# the club's two GL521MGs both read 86486407.
#
# But a TAC is allocated to whoever certified the radio, and a tracker built around an
# off-the-shelf cellular module often ships with the *module vendor's* IMEIs rather than
# its own. The ATC700's own datasheet names its module as a Quectel EG915U-EU, so
# another manufacturer's tracker built on the same part could carry the same TAC and be
# labelled an ATC700 by a TAC-only lookup.
#
# So an entry may name the protocol it applies to, and a device whose protocol
# contradicts the entry does not match it. TAC narrows, protocol confirms; neither is
# sufficient alone. A protocol of None means "any", which is right for a TAC the club
# has confirmed belongs to one product.
TRACKER_TAC_MODELS = {
    "86486407": ("Queclink GL521MG", "gl200"),
    "86212908": ("Teltonika ATC700", "teltonika"),
    "86403205": ("Jimi LL301", "gt06"),
    "86030205": ("Teltonika RUTX50", "teltonika"),
}
TRACKER_PROTOCOL_FAMILIES = {
    "gl200": "Queclink device",
    "teltonika": "Teltonika device",
    "gt06": "Jimi/Concox device",
    "osmand": "phone or app",
}


def tracker_tac(unique_id: Any) -> str:
    """The Type Allocation Code of an IMEI, or "" for anything that is not one."""
    uid = str(unique_id or "").strip()
    return uid[:8] if len(uid) == 15 and uid.isdigit() else ""


def tracker_type_map() -> Dict[str, Tuple[str, Optional[str]]]:
    """TAC -> (model, protocol it applies to): built-ins overlaid with the table.

    The table wins, so a club that buys a unit this code has never heard of can name it
    without a release — and can correct one this code gets wrong.
    """
    models: Dict[str, Tuple[str, Optional[str]]] = dict(TRACKER_TAC_MODELS)
    try:
        with get_db() as db:
            rows = db.execute("SELECT tac, model, protocol FROM tracker_types").fetchall()
        for r in rows:
            if r["tac"] and r["model"]:
                proto = (str(r["protocol"]).strip().lower() or None) if r["protocol"] else None
                models[str(r["tac"])] = (str(r["model"]), proto)
    except sqlite3.OperationalError:
        pass  # table not built yet; the built-ins still answer
    return models


def list_tracker_types() -> List[Dict[str, Any]]:
    """Every catalogued type, built-ins included, for the management UI."""
    try:
        with get_db() as db:
            rows = {str(r["tac"]): dict(r) for r in
                    db.execute("SELECT tac, model, notes, protocol FROM tracker_types").fetchall()}
    except sqlite3.OperationalError:
        rows = {}
    merged = tracker_type_map()
    out = []
    for tac in sorted(merged):
        model, proto = merged[tac]
        out.append({"tac": tac, "model": model, "protocol": proto or "",
                    "notes": (rows.get(tac) or {}).get("notes") or "",
                    "built_in": tac in TRACKER_TAC_MODELS,
                    "edited": tac in rows})
    return out


def upsert_tracker_type(tac: str, model: str, notes: str = "",
                        protocol: str = "") -> Tuple[bool, str]:
    """Catalogue a TAC as a model. Returns (ok, message).

    ``protocol`` is optional and narrows the match: leave it empty for a TAC known to
    belong to one product, set it when the code may be shared with another maker's
    device built on the same cellular module.
    """
    tac = str(tac or "").strip()
    model = str(model or "").strip()
    protocol = str(protocol or "").strip().lower()
    if not (len(tac) == 8 and tac.isdigit()):
        return False, "A type code is the first 8 digits of the IMEI, so 8 digits exactly."
    if not model:
        return False, "Give the model a name."
    init_db()
    with get_db() as db:
        db.execute(
            "INSERT INTO tracker_types (tac, model, notes, protocol, updated_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(tac) DO UPDATE SET model = excluded.model, notes = excluded.notes, "
            "protocol = excluded.protocol, updated_at = excluded.updated_at",
            (tac, model, str(notes or "").strip(), protocol or None,
             datetime.now().isoformat(timespec="seconds")),
        )
        db.commit()
    return True, (f"{tac} is now “{model}”"
                  + (f" when it speaks {protocol}." if protocol else " for any protocol."))


def delete_tracker_type(tac: str) -> Tuple[bool, str]:
    """Forget a catalogued type. Returns (ok, message).

    A built-in cannot be deleted, only overridden — so removing an override reverts to
    the built-in rather than leaving a hole, and the message says which happened.
    """
    tac = str(tac or "").strip()
    if not tac:
        return False, "No type code given."
    init_db()
    with get_db() as db:
        cur = db.execute("DELETE FROM tracker_types WHERE tac = ?", (tac,))
        db.commit()
    if not cur.rowcount:
        return False, (f"{tac} is built in and cannot be removed — save a different model "
                       "against it to override it.")
    built_in = TRACKER_TAC_MODELS.get(tac)
    if built_in:
        return True, f"{tac} is back to the built-in “{built_in[0]}”."
    return True, f"{tac} is no longer catalogued."


def tracker_model(unique_id: Any, protocol: Any = None,
                  models: Optional[Dict[str, Tuple[str, Optional[str]]]] = None) -> str:
    """Best available description of what kind of tracker this is.

    Derived rather than stored: the IMEI is already on every fix, so a stored copy would
    only be one more thing to keep in step.

    TAC narrows and protocol confirms. A TAC belongs to whoever certified the radio, and
    a tracker built on an off-the-shelf module often carries the module vendor's code —
    so a TAC match whose declared protocol contradicts what the device actually speaks
    is *not* a match, and we fall back to the protocol family rather than assert a model
    we have reason to doubt. A device that has never reported has no protocol to
    contradict anything, so the TAC still answers: it is the best guess available.

    Pass ``models`` when resolving a list, to read the table once.
    """
    proto = str(protocol or "").strip().lower()
    tac = tracker_tac(unique_id)
    if tac:
        entry = (tracker_type_map() if models is None else models).get(tac)
        if entry:
            model, wants = entry
            if not wants or not proto or wants == proto:
                return model
    return TRACKER_PROTOCOL_FAMILIES.get(proto) or proto or "unknown"


def tracker_protocols() -> Dict[str, str]:
    """The protocol last seen for each device, from its newest stored fix."""
    try:
        with get_track_db() as db:
            rows = db.execute(
                "SELECT unique_id, protocol FROM track_positions WHERE protocol IS NOT NULL "
                "AND id IN (SELECT MAX(id) FROM track_positions GROUP BY unique_id)"
            ).fetchall()
        return {str(r["unique_id"]): str(r["protocol"]) for r in rows}
    except sqlite3.OperationalError:
        return {}


def tracker_models_by_unique_id(unique_ids: Optional[Iterable[str]] = None) -> Dict[str, str]:
    """Resolve ``unique_id -> model`` for a list of trackers, reading the table once."""
    models = tracker_type_map()
    protocols = tracker_protocols()
    ids = list(unique_ids) if unique_ids is not None else list(protocols)
    return {str(u): tracker_model(u, protocols.get(str(u)), models) for u in ids}


def quality_from_position(pos: Any) -> Dict[str, Any]:
    """Pull the fix-quality and provenance fields out of a Traccar position.

    They come from two levels: ``altitude``, ``accuracy`` and ``valid`` are position
    fields, while ``hdop``, ``pdop``, ``sat`` and ``rssi`` are protocol attributes that
    only some devices send — the GL521MG reports hdop and no satellite count, the
    ATC700 reports all four.

    Missing stays None rather than defaulting to zero. "Not reported" and "reported as
    zero" are different things, and a check that confused them would condemn every fix
    from a device that simply does not send the field.
    """
    attrs = pos.get("attributes") if isinstance(pos, dict) and isinstance(pos.get("attributes"), dict) else {}
    if not isinstance(pos, dict):
        return {k: None for k in QUALITY_FIELDS}

    def num(value: Any) -> Optional[float]:
        # bool is an int subclass, and True would silently become 1.0.
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return float(value)

    valid = pos.get("valid")
    sat = attrs.get("sat")
    protocol = pos.get("protocol")
    return {
        "altitude": num(pos.get("altitude")),
        "accuracy": num(pos.get("accuracy")),
        "hdop": num(attrs.get("hdop")),
        "pdop": num(attrs.get("pdop")),
        "sat": int(sat) if isinstance(sat, (int, float)) and not isinstance(sat, bool) else None,
        "rssi": num(attrs.get("rssi")),
        "valid": None if valid is None else (1 if valid else 0),
        # Kept per fix rather than per tracker: it is the only record of what spoke to
        # Traccar at the time, and it survives the device being removed from the app.
        "protocol": str(protocol) if isinstance(protocol, str) and protocol else None,
    }


def battery_reading(pct: Optional[float], age_s: Optional[float]) -> Dict[str, Any]:
    """How to present a battery level, given the age of the fix that carried it.

    A level is only ever as current as the fix it arrived on. Sat next to
    "1 d ago" with nothing to qualify it, a day-old reading is read as the level
    *now* — and deciding which units to put on charge is the only reason this
    column exists. So past the point where a tracker still counts as reporting,
    the number is kept but marked: it is the last thing the tracker said, not
    what it has left.

    Deliberately not dashed out. A stale *low* reading is the most useful line on
    the page — a tracker that said 8% and then went quiet has very likely gone
    flat — and it is the same value the dashboard warning fires on, so hiding it
    here would leave the two screens contradicting each other.
    """
    stale = pct is not None and age_s is not None and age_s > TRACK_RAG_AMBER_S
    if pct is None:
        title = "This tracker has not reported a battery level"
    elif stale:
        title = f"{pct:.0f}% when it last reported, {_fmt_age(age_s)} — nothing since"
    else:
        title = "Reported with the last fix"
    return {"battery": pct,
            "battery_rag": battery_rag(pct),
            "battery_stale": stale,
            "battery_title": title,
            # A dash, not 0%: a tracker that does not report a battery is not a
            # flat one, and this page is read to decide what to charge.
            "battery_text": "—" if pct is None else f"{pct:.0f}%"}


def battery_rag(pct: Optional[float]) -> str:
    """RAG bucket for a battery percentage: green / amber / red / none."""
    if pct is None:
        return "none"
    if pct <= BATTERY_CRITICAL_PCT:
        return "red"
    if pct <= BATTERY_LOW_PCT:
        return "amber"
    return "green"


def _rag(age_s: Optional[float]) -> str:
    """RAG bucket for a last-fix age in seconds: green / amber / red / none."""
    if age_s is None:
        return "none"
    if age_s <= TRACK_RAG_GREEN_S:
        return "green"
    if age_s <= TRACK_RAG_AMBER_S:
        return "amber"
    return "red"


def _fmt_age(age_s: Optional[float]) -> str:
    """Human 'last reported' text for a fix age in seconds."""
    if age_s is None:
        return "never"
    age = int(age_s)
    if age < 10:
        return "just now"
    if age < 60:
        return f"{age}s ago"
    if age < 3600:
        return f"{age // 60} min ago"
    if age < 86400:
        return f"{age // 3600} hr ago"
    return f"{age // 86400} d ago"

# Simulated device ids (shown in the Trackers settings once the simulator is on).
SIM_DEVICE_IDS = ["SIM-1", "SIM-2", "SIM-3"]


# ---------------------------------------------------------------------------
# Configuration (stored in the hardware_settings table via core.horn)
# ---------------------------------------------------------------------------
def track_config() -> Dict[str, Any]:
    """Return GPS-tracking settings, resolved like the other hardware config."""
    cfg = horn.hardware_config()
    return {
        "enabled": bool(cfg.get("track_enabled", False)),
        "base_url": str(cfg.get("traccar_base_url", "") or "").strip().rstrip("/"),
        "token": str(cfg.get("traccar_token", "") or "").strip(),
        "poll_seconds": int_in_range(cfg.get("track_poll_seconds"), 5, 2, 60),
        "retention_days": int_in_range(cfg.get("track_retention_days"), 90, 1, 3650),
        "sim_enabled": bool(cfg.get("track_sim_enabled", False)),
        # Ask the boats where they are, rather than waiting for their own schedule.
        "race_poll_enabled": bool(cfg.get("track_race_poll_enabled", True)),
        "finish_horn": bool(cfg.get("gps_finish_horn", False)),
        "rounding_radius_m": int_in_range(cfg.get("track_rounding_radius_m"), 50, 10, 500),
        # How far the rounding gate reaches on the hand a boat must pass. Wide
        # by design: it exists to catch the wide rounding the radius misses.
        "gate_reach_m": int_in_range(cfg.get("track_gate_reach_m"), 750, 50, 5000),
        "ingest_secret": str(cfg.get("track_ingest_secret", "") or "").strip(),
    }


def track_active(cfg: Optional[Dict[str, Any]] = None) -> bool:
    """True when the poller should be doing work (live source or simulator)."""
    cfg = cfg or track_config()
    return bool(cfg["enabled"] and (cfg["sim_enabled"] or (cfg["base_url"] and cfg["token"])))


# ---------------------------------------------------------------------------
# Geometry — local planar projection + segment-crossing (none existed before)
# ---------------------------------------------------------------------------
def _project(lat: float, lon: float, lat0: float, lon0: float) -> Tuple[float, float]:
    """Equirectangular projection to metres east/north about (lat0, lon0).

    Accurate enough over the ~sub-km scale of a start/finish line for crossing
    geometry. Reuses the same idea as course_map.js projectPoint.
    """
    m_per_deg_lat = 111_320.0
    m_per_deg_lon = 111_320.0 * math.cos(math.radians(lat0))
    return ((lon - lon0) * m_per_deg_lon, (lat - lat0) * m_per_deg_lat)


def _side(ax: float, ay: float, bx: float, by: float, px: float, py: float) -> float:
    """Signed area (cross product) of (B-A)×(P-A): >0 left of A→B, <0 right."""
    return (bx - ax) * (py - ay) - (by - ay) * (px - ax)


# A boat is only sampled every few seconds, so it can sail past a mark without
# any single fix landing inside the rounding radius: at 6 kn with 10 s reporting
# the gap between fixes is ~31 m, and a coarser race-day rate makes it worse.
# Measuring the *path* between fixes rather than the fixes themselves is the same
# reasoning that already interpolates the finish-line crossing.
MARK_SEGMENT_MAX_GAP_S = 60.0    # don't let a back-filled gap sweep through marks

# A radius answers "was the boat close to the mark?" when the question the app
# actually needs answered is "has it got round and set off on the next leg?". Those
# come apart exactly where it hurts: a boat that gives a mark a wide berth stays
# outside any sane radius the whole way past, and because the walk is sequential it
# then reads as never having rounded *anything* after that — and its GPS finish is
# never looked for, the line only being watched once every mark is rounded.
#
# So a second test runs alongside the radius: the boat came as close to the mark as
# it was going to, and has since opened up again with the next mark now nearer than
# it was at that moment. Chosen by measuring candidates over recorded tracks rather
# than by argument -- scripts/compare_rounding_tests.py, which is kept so the choice
# can be re-examined once there is a season of real racing behind it:
# it agreed with the radius on every race-officer-recorded finish to within a second,
# held a rounding pushed 400 m wide where the radius lost it at 100 m, kept finding
# finishes at 30 s reporting intervals where the radius found none, and was never
# early — 30-60 s later than the radius on a tight rounding, which is the safe
# direction. It was also *better* at rejecting tracks walked against a course they
# never sailed, which is the confirming condition earning its keep.
MARK_NEIGHBOURHOOD_M = 400.0     # how near it must have come at all
MARK_DEPART_M = 50.0             # and how far it must have opened up since


def mark_neighbourhood_m(radius_m: float, *legs: float) -> float:
    """How near a boat must pass a mark for a departure from it to count as rounding.

    Capped at half the shortest leg touching the mark, so a course with legs shorter
    than the flat allowance cannot have a neighbourhood wider than the legs
    themselves — the club's shortest is 390 m against a median of 1320 m. Never
    tighter than the rounding radius it supplements, or it would take away roundings
    the radius alone would have found.
    """
    lengths = [l for l in legs if l and l > 0]
    allowed = MARK_NEIGHBOURHOOD_M
    if lengths:
        allowed = min(allowed, 0.5 * min(lengths))
    return max(allowed, float(radius_m or 0.0))


def distance_to_segment_m(p_lat: float, p_lon: float,
                          a_lat: float, a_lon: float,
                          b_lat: float, b_lon: float) -> float:
    """Closest approach, in metres, of the leg A→B to the point P."""
    px, py = _project(p_lat, p_lon, a_lat, a_lon)
    bx, by = _project(b_lat, b_lon, a_lat, a_lon)
    seg_len_sq = bx * bx + by * by
    if seg_len_sq <= 0:                       # A and B are the same place
        return math.hypot(px, py)
    t = max(0.0, min(1.0, (px * bx + py * by) / seg_len_sq))
    return math.hypot(px - bx * t, py - by * t)


def segment_intersection_fraction(
    p1: Tuple[float, float], p2: Tuple[float, float],
    a: Tuple[float, float], b: Tuple[float, float],
) -> Optional[float]:
    """Return the fraction t along p1→p2 where it crosses segment a→b, else None.

    All points are planar (metres). Returns t in [0, 1] only when the crossing
    lies on *both* segments (so a boat passing the line's extension doesn't
    count). None when parallel or no crossing within the segments.
    """
    r = (p2[0] - p1[0], p2[1] - p1[1])
    s = (b[0] - a[0], b[1] - a[1])
    denom = r[0] * s[1] - r[1] * s[0]
    if abs(denom) < 1e-9:
        return None  # parallel / degenerate
    qp = (a[0] - p1[0], a[1] - p1[1])
    t = (qp[0] * s[1] - qp[1] * s[0]) / denom
    u = (qp[0] * r[1] - qp[1] * r[0]) / denom
    if 0.0 <= t <= 1.0 and 0.0 <= u <= 1.0:
        return t
    return None


def race_ended_dt(race: Any) -> Optional[datetime]:
    """When this race stopped being sailed, or None while it still is.

    This is the moment a race's mark positions are pinned to, and it is the *end*
    rather than the start for a reason worth spelling out.

    A mark that has dragged is usually discovered mid-race, by the boats failing
    to register as rounding it. Somebody then goes out and pings it — during the
    race. Pinning to the start would file that correction *after* the race and
    ignore it: the boats that had already rounded would stay unrecognised, and so
    would every boat behind them, because the walk is sequential. Pinning to the
    end means a correction made while the race was still being sailed counts for
    that race, permanently, and the walk picks up the earlier roundings too since
    it recomputes from every stored fix.

    None — meaning "use the marks as they are now" — while any boat is still
    racing, so a correction takes effect the moment it is made.
    """
    if race is None:
        return None
    try:
        start_dt = race_first_start_dt(race)
    except Exception:
        return None
    try:
        from core.races import get_entries
        entries = get_entries(int(race["id"]))
    except Exception:
        return start_dt
    if not entries:
        return start_dt
    last_finish: Optional[datetime] = None
    for entry in entries:
        status = str(row_get(entry, "status", "") or "").strip().upper()
        if status == "RACING":
            return None            # still out there; the marks are today's
        finish = row_get(entry, "finish_time", "")
        if not finish:
            continue
        try:
            ts = datetime.fromisoformat(str(finish))
        except ValueError:
            continue
        last_finish = ts if last_finish is None else max(last_finish, ts)
    # Nothing finished and nobody still racing (all DNS/DNC/RET): the race is
    # over and its own day is the best anchor there is.
    return last_finish or start_dt


def race_marks(race: Any) -> Dict[str, Any]:
    """The marks as they stood when this race was sailed.

    Marks get re-measured after they drag, and without this every such correction
    would reach backwards: an old race would be redrawn against buoys that were
    somewhere else on the day, its legs re-measured, and boats recorded as having
    rounded a mark could fall outside the rounding radius of its new position and
    read as never having rounded it at all.

    See race_ended_dt for which moment it pins to, and why that is the end of the
    race rather than the start.
    """
    if race is None:
        return appstate.MARKS
    return marks.marks_as_of(race_ended_dt(race))


def finish_lines() -> List[Dict[str, Any]]:
    """Every selectable finish line, from data/start_finish.json."""
    lines = (appstate.START_FINISH or {}).get("finish_lines")
    return list(lines) if isinstance(lines, list) and lines else []


def default_finish_line_key() -> str:
    """The line a race uses when nothing has been chosen."""
    for line in finish_lines():
        if line.get("default"):
            return str(line.get("key") or "")
    lines = finish_lines()
    return str(lines[0].get("key") or "") if lines else ""


def finish_line_by_key(key: str) -> Optional[Dict[str, Any]]:
    """Look a line up by key, falling back to the default for an unknown one."""
    key = str(key or "").strip()
    lines = finish_lines()
    if key:
        for line in lines:
            if str(line.get("key") or "") == key:
                return line
    for line in lines:
        if str(line.get("key") or "") == default_finish_line_key():
            return line
    return lines[0] if lines else None


def race_finish_line_key(race: Any) -> str:
    """The finish line this race is sailed to."""
    try:
        key = race["finish_line_key"] if race is not None else ""
    except (KeyError, IndexError, TypeError):
        key = ""
    return str(key or "").strip() or default_finish_line_key()


def _line_end_point(end: Any, marks_at: Dict[str, Any]) -> Optional[Tuple[float, float]]:
    """One end of a line: a mark code, or an explicit position.

    A mark keeps the end tied to the buoy, so re-measuring it moves the line with
    it. An explicit position is for an end that is not a mark at all — a point on
    a building — or one the sailing instructions state outright, which is then the
    position the race is sailed to whatever the mark list says.
    """
    if not isinstance(end, dict):
        return None
    code = str(end.get("mark") or "").strip()
    if code:
        md = marks_at.get(code) or {}
        lat, lon = md.get("lat"), md.get("lon")
    else:
        lat, lon = end.get("lat"), end.get("lon")
    if lat is None or lon is None:
        return None
    try:
        return (float(lat), float(lon))
    except (TypeError, ValueError):
        return None


def finish_line_points(marks_at: Optional[Dict[str, Any]] = None, line_key: str = ""
                       ) -> Optional[Tuple[Tuple[float, float], Tuple[float, float]]]:
    """Return the finish line as ((lat,lon) seaward end, (lat,lon) shore end).

    The usual Pwllheli line is the ODM (mark ``O``) to the surveyed bridge window,
    and start and finish are geographically the same. Some races are not sailed to
    it: an ISORA passage race finishes on the transit between the Pwllheli Fairway
    Buoy and the bridge at Plas Heli, a different line 1.6 km long lying nearly a
    kilometre from the usual one. ``line_key`` picks which.

    ``marks_at`` is a marks dict from ``race_marks`` when the line wanted is the
    one a past race was sailed to. An end given as a mark is a laid buoy like any
    other, so a corrected mark would otherwise move the line under races already
    scored.
    """
    at = appstate.MARKS if marks_at is None else marks_at
    line = finish_line_by_key(line_key)
    if line is None:
        # No configured lines at all: the historical behaviour, O to the bridge.
        o = at.get("O") or {}
        if o.get("lat") is None or o.get("lon") is None:
            return None
        return ((float(o["lat"]), float(o["lon"])),
                (appstate.BRIDGE_WINDOW_LAT, appstate.BRIDGE_WINDOW_LON))
    seaward = _line_end_point(line.get("seaward"), at)
    shore = _line_end_point(line.get("shore"), at)
    if seaward is None or shore is None:
        return None
    return (seaward, shore)


def finish_line_seaward_mark(race: Any) -> str:
    """The mark code at the seaward end of this race's finish line, if it is one.

    Empty when that end is a bare position rather than a laid mark.
    """
    line = finish_line_by_key(race_finish_line_key(race)) or {}
    return str((line.get("seaward") or {}).get("mark") or "").strip()


def race_chart_line(race: Any) -> Dict[str, Any]:
    """The race's finish line as chart data attributes.

    Empty strings when the race uses the usual club line, so the chart falls back
    to its own bridge-window-to-ODM construction and a mark re-measured since
    still moves the line it is drawn on.
    """
    key = race_finish_line_key(race)
    line = finish_line_by_key(key)
    if not line or key == default_finish_line_key():
        return {"line_seaward_lat": "", "line_seaward_lon": "",
                "line_shore_lat": "", "line_shore_lon": "",
                "line_seaward_label": "", "line_shore_label": ""}
    points = race_finish_line_points(race)
    if points is None:
        return {"line_seaward_lat": "", "line_seaward_lon": "",
                "line_shore_lat": "", "line_shore_lon": "",
                "line_seaward_label": "", "line_shore_label": ""}
    (s_lat, s_lon), (h_lat, h_lon) = points
    return {
        "line_seaward_lat": s_lat, "line_seaward_lon": s_lon,
        "line_shore_lat": h_lat, "line_shore_lon": h_lon,
        "line_seaward_label": str((line.get("seaward") or {}).get("label") or "Finish"),
        "line_shore_label": str((line.get("shore") or {}).get("label") or "Finish"),
    }


# How far past the seaward end of a finish line a crossing still counts, in metres.
# Per line in data/start_finish.json as "seaward_extension_m"; this is the fallback.
#
# The two ends of a line are not the same kind of thing. The shore end is a surveyed
# transit — a bridge window — and does not move. The seaward end is a laid buoy on a
# mooring, and it swings, drags and gets re-laid. A boat that passes inside the buoy
# but outside where we think the buoy is falls off the end of the segment, and its
# finish is never seen: the direction test uses the *infinite* line and is happy, and
# only the segment bound refuses it.
#
# Found when an ISORA night race missed a finish. The stored position for the Fairway
# Buoy was 112 m inshore of the position ISORA's own sailing instruction gives — 7% of
# a 1510 m line, before the buoy had moved at all. So this is not only for drift; it
# also covers the gap between where a mark is recorded and where the fleet is told it
# is. Correcting the mark is still the first fix; this stops one stale metre costing a
# finish.
#
# Extending is safe in a way widening the rounding radius is not: the extension is
# collinear, so the infinite line — and therefore the finishing-direction test — is
# unchanged. And a finish still requires every mark rounded first, which is what stops
# a mid-course pass of the ODM counting.
DEFAULT_SEAWARD_EXTENSION_M = 150.0


def finish_line_extension_m(line_key: str = "") -> float:
    """How far to project the seaward end of this line, in metres."""
    line = finish_line_by_key(line_key) or {}
    try:
        value = float(line.get("seaward_extension_m", DEFAULT_SEAWARD_EXTENSION_M))
    except (TypeError, ValueError):
        return DEFAULT_SEAWARD_EXTENSION_M
    return max(0.0, value)


def race_finish_line_extension_m(race: Any) -> float:
    """The seaward extension for the line this race is sailed to."""
    return finish_line_extension_m(race_finish_line_key(race))


def _extend_away_from(keep: Tuple[float, float], move: Tuple[float, float],
                      metres: float) -> Tuple[float, float]:
    """``move`` pushed ``metres`` further from ``keep``, along the same line."""
    if metres <= 0:
        return move
    lat0 = move[0]
    m_per_deg_lat = 111132.0
    m_per_deg_lon = 111320.0 * math.cos(math.radians(lat0))
    dx = (move[1] - keep[1]) * m_per_deg_lon
    dy = (move[0] - keep[0]) * m_per_deg_lat
    length = math.hypot(dx, dy)
    if length <= 0:
        return move
    return (move[0] + dy / length * metres / m_per_deg_lat,
            move[1] + dx / length * metres / m_per_deg_lon)


def race_finish_line_for_detection(race: Any
                                   ) -> Optional[Tuple[Tuple[float, float], Tuple[float, float]]]:
    """The finish line as the crossing test should see it.

    The true line, with the seaward end projected outward — see
    DEFAULT_SEAWARD_EXTENSION_M. Deliberately separate from
    race_finish_line_points, which stays true: the chart must draw the line where
    it is, and distance-to-go must measure to the real mark rather than to a point
    a couple of hundred metres beyond it.
    """
    points = race_finish_line_points(race)
    if points is None:
        return None
    seaward, shore = points
    return (_extend_away_from(shore, seaward, race_finish_line_extension_m(race)), shore)


def race_finish_line_points(race: Any
                            ) -> Optional[Tuple[Tuple[float, float], Tuple[float, float]]]:
    """The finish line for one race: its chosen line, at its own mark positions."""
    return finish_line_points(race_marks(race), race_finish_line_key(race))


def detect_finish_crossing(
    fixes: List[Dict[str, Any]],
    line_a: Tuple[float, float],
    line_b: Tuple[float, float],
    course_ref: Optional[Tuple[float, float]],
    not_before_ts: Optional[float] = None,
    min_elapsed_s: float = 0.0,
) -> Optional[Dict[str, Any]]:
    """Find the first finish crossing in a boat's ordered GPS track.

    ``fixes`` is a list of ``{"t": epoch, "lat": .., "lon": ..}`` sorted by time.
    A finish is a segment of the track that crosses the finish line **leaving the
    course** (from the course side to the other side). ``course_ref`` is any point
    known to be on the course side (e.g. the centroid of the course marks); when
    None the direction check is skipped (any crossing counts). Crossings before
    ``not_before_ts + min_elapsed_s`` are ignored (filters the start crossing).
    Returns ``{"t", "lat", "lon"}`` interpolated at the crossing, or None.
    """
    if len(fixes) < 2:
        return None
    lat0, lon0 = line_a
    a = _project(line_a[0], line_a[1], lat0, lon0)
    b = _project(line_b[0], line_b[1], lat0, lon0)
    course_sign = None
    if course_ref is not None:
        cr = _project(course_ref[0], course_ref[1], lat0, lon0)
        s = _side(a[0], a[1], b[0], b[1], cr[0], cr[1])
        if s != 0:
            course_sign = 1.0 if s > 0 else -1.0
    guard = None
    if not_before_ts is not None:
        guard = not_before_ts + max(0.0, min_elapsed_s)

    for i in range(1, len(fixes)):
        f1, f2 = fixes[i - 1], fixes[i]
        if guard is not None and f2["t"] < guard:
            continue
        p1 = _project(f1["lat"], f1["lon"], lat0, lon0)
        p2 = _project(f2["lat"], f2["lon"], lat0, lon0)
        # Direction: must leave the course (course side -> other side).
        if course_sign is not None:
            s1 = _side(a[0], a[1], b[0], b[1], p1[0], p1[1])
            s2 = _side(a[0], a[1], b[0], b[1], p2[0], p2[1])
            leaving = (s1 * course_sign > 0) and (s2 * course_sign < 0)
            if not leaving:
                continue
        t = segment_intersection_fraction(p1, p2, a, b)
        if t is None:
            continue
        cross_t = f1["t"] + t * (f2["t"] - f1["t"])
        cross_lat = f1["lat"] + t * (f2["lat"] - f1["lat"])
        cross_lon = f1["lon"] + t * (f2["lon"] - f1["lon"])
        return {"t": cross_t, "lat": cross_lat, "lon": cross_lon}
    return None


# ---------------------------------------------------------------------------
# Traccar REST client (outbound pull; graceful degradation like weather_store)
# ---------------------------------------------------------------------------
def _parse_iso_epoch(value: Optional[str]) -> Optional[float]:
    """Parse a Traccar ISO-8601 timestamp (UTC) to an epoch float."""
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        return None


def _non_latin1_field(cfg: Dict[str, Any]) -> Optional[Tuple[str, str]]:
    """Return (field-name, offending-char) if a request field isn't latin-1.

    HTTP request lines and headers are latin-1; a non-latin-1 char in the base
    URL or token would raise a confusing UnicodeEncodeError inside urllib.
    """
    for field, value in (("Base URL", cfg.get("base_url", "")), ("API token", cfg.get("token", ""))):
        for ch in str(value):
            if ord(ch) > 255:
                return field, ch
    return None


def _traccar_request(cfg: Dict[str, Any], method: str, path: str,
                     body: Optional[Dict[str, Any]] = None, timeout: float = 6.0) -> Any:
    """Call a Traccar API path and return decoded JSON (or None). Raises on error.

    A normal User-Agent is sent because Cloudflare's bot/WAF 403s the default
    python-urllib UA (same gotcha as the branding manifest fetch).
    """
    url = f"{cfg['base_url']}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {cfg['token']}",
        "User-Agent": "Mozilla/5.0 (PwllheliRaceOfficer)",
    }
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        raw = response.read()
        if not raw:
            return None
        return json.loads(raw.decode("utf-8", errors="replace"))


def _traccar_get(cfg: Dict[str, Any], path: str, timeout: float = 4.0) -> Any:
    """GET a Traccar API path and return decoded JSON (raises on error)."""
    return _traccar_request(cfg, "GET", path, timeout=timeout)


def traccar_list_devices(cfg: Dict[str, Any], include_unowned: bool = False) -> List[Dict[str, Any]]:
    """Return devices registered in Traccar.

    Plain ``/api/devices`` returns only the devices linked to the token's own
    user. A device Traccar created by itself (``database.registerUnknown``, when
    a tracker first connects) belongs to nobody, so it is invisible there — the
    same devices Traccar's own web UI hides until you switch on "All Devices".
    ``include_unowned`` asks for those too, which needs an admin token; if the
    server refuses, fall back to the owned list rather than failing.
    """
    if include_unowned:
        try:
            out = _traccar_request(cfg, "GET", "/api/devices?all=true")
            if isinstance(out, list):
                return out
        except Exception:
            pass
    out = _traccar_request(cfg, "GET", "/api/devices")
    return out if isinstance(out, list) else []


def traccar_user_id(cfg: Dict[str, Any]) -> Optional[int]:
    """Best-effort id of the user the API token belongs to.

    Traccar has no "who am I" for token auth (/api/session is session-only), so
    infer it: a single user is unambiguous, and otherwise a single administrator
    is the account an admin token almost certainly belongs to. Returns None when
    it cannot be told, and callers then skip linking rather than guess wrong.
    """
    try:
        users = _traccar_request(cfg, "GET", "/api/users")
    except Exception:
        return None
    if not isinstance(users, list) or not users:
        return None
    if len(users) == 1:
        return users[0].get("id")
    admins = [u for u in users if u.get("administrator")]
    return admins[0].get("id") if len(admins) == 1 else None


def traccar_link_device(cfg: Dict[str, Any], device_id: Any, user_id: Any) -> None:
    """Give the token's user access to a device (raises on failure).

    Devices this app creates are linked to its user automatically; ones Traccar
    auto-registered are not, and an unlinked device's positions are invisible to
    /api/positions — so the poller and the back-fill would never see that boat.
    """
    _traccar_request(cfg, "POST", "/api/permissions",
                     {"userId": int(user_id), "deviceId": int(device_id)})


def traccar_create_device(cfg: Dict[str, Any], unique_id: str, name: str = "") -> Dict[str, Any]:
    """Create a device in Traccar; return the created device dict."""
    return _traccar_request(cfg, "POST", "/api/devices", {"name": name or unique_id, "uniqueId": unique_id})


def traccar_delete_device(cfg: Dict[str, Any], device_id: Any) -> None:
    """Delete a device from Traccar by its numeric Traccar id."""
    _traccar_request(cfg, "DELETE", f"/api/devices/{device_id}")


def fetch_positions(cfg: Optional[Dict[str, Any]] = None) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """Return ``(positions, error)`` — the fleet's latest fixes from Traccar.

    Each position is normalised to ``{device_id, unique_id, name, lat, lon,
    speed_kn, course_deg, fix_time}``. On any failure returns ``([], message)``;
    never raises. The simulator branch is handled by the caller (collect_positions).
    """
    cfg = cfg or track_config()
    # The base URL and token go into the request line and the Authorization
    # header, which HTTP encodes as latin-1. A stray non-ASCII character (a
    # copy-pasted "smart" dash/quote, e.g. an em-dash) otherwise raises a
    # cryptic UnicodeEncodeError deep in urllib; catch it here with a clear,
    # actionable message naming the offending field.
    bad = _non_latin1_field(cfg)
    if bad:
        field, ch = bad
        return [], (f"Traccar {field} contains a non-ASCII character ({ch!r}); "
                    "re-enter it in Settings (copy-paste can insert smart dashes/quotes).")
    try:
        devices = _traccar_get(cfg, "/api/devices")
        positions = _traccar_get(cfg, "/api/positions")
    except Exception as exc:  # pragma: no cover - network dependent
        return [], f"Could not read Traccar: {exc}"
    by_id = {d.get("id"): d for d in devices if isinstance(d, dict)}
    out: List[Dict[str, Any]] = []
    for p in positions:
        if not isinstance(p, dict):
            continue
        dev = by_id.get(p.get("deviceId"), {})
        lat, lon = p.get("latitude"), p.get("longitude")
        if lat is None or lon is None:
            continue
        out.append({
            "device_id": str(p.get("deviceId", "")),
            "unique_id": str(dev.get("uniqueId", "") or p.get("deviceId", "")),
            "name": str(dev.get("name", "") or ""),
            "lat": float(lat),
            "lon": float(lon),
            "speed_kn": float(p.get("speed") or 0.0),
            "course_deg": float(p.get("course") or 0.0),
            "fix_time": _parse_iso_epoch(p.get("fixTime") or p.get("deviceTime")) or time.time(),
            "battery_pct": battery_from_attributes(p.get("attributes")),
            **quality_from_position(p),
        })
    return out, None


# ---------------------------------------------------------------------------
# Simulator (moving boats near the finish line so the feature demos with no kit)
# ---------------------------------------------------------------------------
def simulate_positions(now: Optional[float] = None) -> List[Dict[str, Any]]:
    """Synthesise SIM boats near the course; SIM-1 slowly crosses the finish line.

    Deterministic given the clock (no randomness). SIM-2/3 loiter on the course
    side so they show on the map; SIM-1 drifts across the line on a slow cycle so
    a finish crossing is detectable end-to-end.
    """
    now = now if now is not None else time.time()
    line = finish_line_points()
    if line is None:
        return []
    (o_lat, o_lon), (br_lat, br_lon) = line
    mid_lat, mid_lon = (o_lat + br_lat) / 2.0, (o_lon + br_lon) / 2.0
    # A rough "course side" normal: perpendicular offset from the line midpoint.
    dlat, dlon = (br_lat - o_lat), (br_lon - o_lon)
    nlat, nlon = -dlon, dlat  # perpendicular in lat/lon space (good enough locally)
    norm = math.hypot(nlat, nlon) or 1.0
    nlat, nlon = nlat / norm, nlon / norm

    out: List[Dict[str, Any]] = []
    # SIM-1: position along the normal swings from +course side to -harbour side
    # on a 600s cycle, so it crosses the line once per cycle (a finish).
    swing = math.cos((now % 600.0) / 600.0 * 2 * math.pi)  # +1 .. -1
    off = swing * 0.010  # ~ up to ~1 km each side of the line
    out.append({
        "device_id": "SIM-1", "unique_id": "SIM-1", "name": "Sim boat 1",
        "lat": mid_lat + nlat * off, "lon": mid_lon + nlon * off,
        "speed_kn": 5.0, "course_deg": 180.0 if swing < 0 else 0.0, "fix_time": now,
    })
    # SIM-2/3: small circles parked on the course side.
    for idx, dev in enumerate(SIM_DEVICE_IDS[1:], start=1):
        ang = (now / 120.0 + idx) % (2 * math.pi)
        base = 0.006 + idx * 0.002
        out.append({
            "device_id": dev, "unique_id": dev, "name": f"Sim boat {idx + 1}",
            "lat": mid_lat + nlat * base + math.sin(ang) * 0.0015,
            "lon": mid_lon + nlon * base + math.cos(ang) * 0.0015,
            "speed_kn": 4.0, "course_deg": (math.degrees(ang)) % 360.0, "fix_time": now,
        })
    return out


# ---------------------------------------------------------------------------
# Push ingest — Traccar's position forwarder posts each fix as it arrives
# ---------------------------------------------------------------------------
# Polling costs up to one poll interval of delay before the app sees a fix. That
# does not affect a recorded finish *time* (crossings are interpolated between
# fix timestamps), but it does delay everything the delay is visible in: the
# automatic horn, and the position shown to competitors. With Traccar's
# forwarder configured, fixes arrive within a moment of Traccar decoding them
# and finish detection runs there and then.
#
# Traccar's JSON forwarder posts {"position": {...}, "device": {...}} per fix
# (org.traccar.forward.PositionData). Speeds are in knots, times ISO-8601 UTC.
def parse_forwarded_positions(payload: Any) -> List[Dict[str, Any]]:
    """Normalise Traccar forwarder JSON into our position dicts.

    Accepts one {"position":…, "device":…} object or a list of them (the
    simulator batches; Traccar sends one per request). Anything unusable is
    skipped rather than raising — this parses data from the network.
    """
    items = payload if isinstance(payload, list) else [payload]
    out: List[Dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        pos = item.get("position") if isinstance(item.get("position"), dict) else None
        dev = item.get("device") if isinstance(item.get("device"), dict) else {}
        if pos is None:
            continue
        lat, lon = pos.get("latitude"), pos.get("longitude")
        if lat is None or lon is None:
            continue
        # The unique id is what we map to a boat; without it a fix is useless.
        unique_id = str(dev.get("uniqueId", "") or "").strip()
        if not unique_id:
            continue
        try:
            out.append({
                "device_id": str(pos.get("deviceId", "") or dev.get("id", "") or ""),
                "unique_id": unique_id,
                "name": str(dev.get("name", "") or ""),
                "lat": float(lat),
                "lon": float(lon),
                "speed_kn": float(pos.get("speed") or 0.0),
                "course_deg": float(pos.get("course") or 0.0),
                "fix_time": _parse_iso_epoch(pos.get("fixTime") or pos.get("deviceTime")) or time.time(),
                "battery_pct": battery_from_attributes(pos.get("attributes")),
                **quality_from_position(pos),
            })
        except (TypeError, ValueError):
            continue
    return out


def ingest_forwarded_positions(payload: Any) -> Dict[str, Any]:
    """Store pushed fixes and run finish detection immediately.

    Returns ``{"received", "stored", "finishes"}``. Detection runs inline so the
    horn fires on the fix that crossed the line rather than on the next poll;
    it is best-effort and never raises into the request.
    """
    positions = parse_forwarded_positions(payload)
    if not positions:
        return {"received": 0, "stored": 0, "finishes": 0}
    cfg = track_config()
    stored = insert_positions(positions, retention_days=cfg["retention_days"])
    finishes = 0
    if stored:
        try:
            # Only the trackers in this push. A boat cannot finish on another
            # boat's fix, and this runs in the request thread.
            finishes = run_finish_detection(
                only_devices={str(p.get("unique_id")) for p in positions if p.get("unique_id")})
        except Exception:
            finishes = 0
    with TRACK_MONITOR_LOCK:
        TRACK_MONITOR_STATE["last_forward_at"] = time.time()
        TRACK_MONITOR_STATE["forward_count"] = int(TRACK_MONITOR_STATE.get("forward_count") or 0) + len(positions)
    return {"received": len(positions), "stored": stored, "finishes": finishes}


# ---------------------------------------------------------------------------
# Back-fill — recover fixes missed while the app was away
# ---------------------------------------------------------------------------
# The live poll asks Traccar for /api/positions, which returns only each
# device's *latest* fix, so an app outage leaves a permanent hole in the track.
# Traccar keeps the history, so ask for the gap explicitly:
#   GET /api/positions?deviceId=<id>&from=<iso>&to=<iso>   (from/to required)
BACKFILL_MAX_HOURS = 6          # never ask for more than this in one go
BACKFILL_MIN_GAP_S = 30.0       # a gap smaller than this is just normal spacing


def _iso_utc(epoch: float) -> str:
    """Format an epoch as the ISO-8601 UTC string Traccar's API expects."""
    return datetime.fromtimestamp(float(epoch), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def insert_backfilled_positions(positions: List[Dict[str, Any]]) -> int:
    """Insert historical fixes, skipping ones already stored.

    insert_positions() only accepts fixes newer than the newest stored for that
    device — right for the live feed, but it would discard every back-filled fix,
    which is by definition older than the latest. Dedupe on (device, fix_time)
    instead.
    """
    init_track_db()
    if not positions:
        return 0
    now = time.time()
    boat_of = _device_boat_map()
    inserted = 0
    with get_track_db() as db:
        for p in positions:
            uid, ft = p.get("unique_id"), p.get("fix_time")
            if not uid or ft is None:
                continue
            exists = db.execute(
                "SELECT 1 FROM track_positions WHERE unique_id = ? AND fix_time = ? LIMIT 1", (uid, ft)
            ).fetchone()
            if exists:
                continue
            db.execute(
                "INSERT INTO track_positions (device_id, unique_id, boat_id, name, lat, lon, speed_kn, course_deg, fix_time, server_time, battery_pct, "
                "altitude, accuracy, hdop, pdop, sat, rssi, valid, protocol) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (p.get("device_id"), uid, boat_of.get(uid), p.get("name"), p.get("lat"), p.get("lon"),
                 p.get("speed_kn"), p.get("course_deg"), ft, now, p.get("battery_pct"),
                 *(p.get(k) for k in QUALITY_FIELDS)),
            )
            inserted += 1
        db.commit()
    return inserted


def backfill_positions(cfg: Optional[Dict[str, Any]] = None, now: Optional[float] = None,
                       since: Optional[Dict[str, float]] = None) -> Tuple[int, Optional[str]]:
    """Fill gaps in the stored track from Traccar's history. Returns (inserted, error).

    Only trackers assigned to a boat are back-filled (an unassigned device's
    history belongs to nobody), and only where the gap is big enough to be a real
    outage. Never raises.

    ``since`` is the newest stored fix per device **as it was before this poll
    stored anything**. It matters: the live poll fetches each device's latest
    fix, so by the time this runs the newest stored fix is seconds old and the
    gap looks trivial — an outage would then never be back-filled at all. Seen
    for real: a 100-second outage recovered exactly one fix per boat until the
    caller started passing the earlier snapshot.
    """
    cfg = cfg or track_config()
    if not (cfg["base_url"] and cfg["token"]):
        return 0, None
    now = now if now is not None else time.time()
    latest = _latest_fix_times() if since is None else dict(since)
    boat_of = _device_boat_map()
    wanted = [uid for uid, boat in boat_of.items() if boat and uid in latest
              and (now - latest[uid]) > BACKFILL_MIN_GAP_S]
    if not wanted:
        return 0, None
    try:
        devices = traccar_list_devices(cfg)
    except Exception as exc:
        return 0, f"Could not list Traccar devices: {exc}"
    id_of = {str(d.get("uniqueId", "")): d.get("id") for d in devices if isinstance(d, dict)}
    inserted = 0
    for uid in wanted:
        device_id = id_of.get(uid)
        if device_id is None:
            continue
        start = max(latest[uid] + 0.001, now - BACKFILL_MAX_HOURS * 3600)
        try:
            rows = _traccar_get(
                cfg, f"/api/positions?deviceId={int(device_id)}&from={_iso_utc(start)}&to={_iso_utc(now)}",
                timeout=8.0,
            )
        except Exception as exc:
            return inserted, f"Could not back-fill from Traccar: {exc}"
        if not isinstance(rows, list):
            continue
        gap: List[Dict[str, Any]] = []
        for p in rows:
            if not isinstance(p, dict):
                continue
            lat, lon = p.get("latitude"), p.get("longitude")
            ft = _parse_iso_epoch(p.get("fixTime") or p.get("deviceTime"))
            if lat is None or lon is None or ft is None:
                continue
            gap.append({
                "device_id": str(p.get("deviceId", "") or device_id),
                "unique_id": uid,
                "name": "",
                "lat": float(lat), "lon": float(lon),
                "speed_kn": float(p.get("speed") or 0.0),
                "course_deg": float(p.get("course") or 0.0),
                "fix_time": ft,
                # Recovered fixes carry their battery and quality too. They did not
                # before: a fix recovered after an outage came back stripped of both,
                # so exactly the period most worth examining held the least data.
                "battery_pct": battery_from_attributes(p.get("attributes")),
                **quality_from_position(p),
            })
        inserted += insert_backfilled_positions(gap)
    return inserted, None


def collect_positions(cfg: Optional[Dict[str, Any]] = None) -> Tuple[List[Dict[str, Any]], Optional[str], str]:
    """Return ``(positions, error, source)`` from the live source or simulator."""
    cfg = cfg or track_config()
    if cfg["sim_enabled"] and not (cfg["base_url"] and cfg["token"]):
        return simulate_positions(), None, "sim"
    if cfg["sim_enabled"]:
        # Both configured: prefer live, fall back to sim on error.
        positions, error = fetch_positions(cfg)
        if error:
            return simulate_positions(), None, "sim"
        return positions, None, "traccar"
    positions, error = fetch_positions(cfg)
    return positions, error, "traccar"


# ---------------------------------------------------------------------------
# Separate positions database (data/track_positions.db)
# ---------------------------------------------------------------------------
TRACK_DB_PATH = appstate.DATA_DIR / "track_positions.db"
TRACK_DB_INIT_LOCK = threading.Lock()
TRACK_DB_INITIALIZED = False


def get_track_db() -> sqlite3.Connection:
    """Open the separate track-positions SQLite database with row access.

    Same busy timeout as the race database (see core.db): the
    tracker poller writes every few seconds while the chart, the replay and the
    clubhouse display read, and the five-second default cost a finish on the hut.
    This is a different file, so it cannot block a finish — but it holds the
    evidence behind GPS finishes, and losing a fix to a lock is the same mistake.
    """
    conn = sqlite3.connect(TRACK_DB_PATH, timeout=core_db.busy_timeout_seconds())
    conn.row_factory = sqlite3.Row
    return conn


def init_track_db() -> None:
    """Create the track-positions schema once per process (own guard, own DB)."""
    global TRACK_DB_INITIALIZED
    if TRACK_DB_INITIALIZED and TRACK_DB_PATH.exists():
        return
    with TRACK_DB_INIT_LOCK:
        if TRACK_DB_INITIALIZED and TRACK_DB_PATH.exists():
            return
        TRACK_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        with get_track_db() as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS track_positions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    device_id TEXT,
                    unique_id TEXT NOT NULL,
                    boat_id INTEGER,
                    name TEXT,
                    lat REAL NOT NULL,
                    lon REAL NOT NULL,
                    speed_kn REAL,
                    course_deg REAL,
                    fix_time REAL NOT NULL,
                    server_time REAL NOT NULL,
                    battery_pct REAL,
                    altitude REAL,
                    accuracy REAL,
                    hdop REAL,
                    pdop REAL,
                    sat INTEGER,
                    rssi REAL,
                    valid INTEGER,
                    protocol TEXT
                )
                """
            )
            # Migrate older DBs that predate boat-linked tracks.
            cols = {r["name"] for r in db.execute("PRAGMA table_info(track_positions)").fetchall()}
            if "boat_id" not in cols:
                db.execute("ALTER TABLE track_positions ADD COLUMN boat_id INTEGER")
            # Battery lives on the fix rather than on the tracker, so the Trackers page
            # reads it exactly as it already reads "last reported" — the newest stored
            # fix per device — and a race's drain is in the history for nothing extra.
            if "battery_pct" not in cols:
                db.execute("ALTER TABLE track_positions ADD COLUMN battery_pct REAL")
            # Fix quality, added after two GPS failures went undiagnosable — see
            # quality_from_position. Existing rows keep NULL, which every reader must
            # treat as "not known" rather than "bad": the whole archive predates this.
            for column, sql_type in (("altitude", "REAL"), ("accuracy", "REAL"), ("hdop", "REAL"),
                                     ("pdop", "REAL"), ("sat", "INTEGER"), ("rssi", "REAL"),
                                     ("valid", "INTEGER"), ("protocol", "TEXT")):
                if column not in cols:
                    db.execute(f"ALTER TABLE track_positions ADD COLUMN {column} {sql_type}")
            db.execute("CREATE INDEX IF NOT EXISTS idx_track_positions_dev_time ON track_positions(unique_id, fix_time)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_track_positions_boat_time ON track_positions(boat_id, fix_time)")
            # The retention purge filters on fix_time alone, which neither index
            # above can serve — it was scanning the whole table.
            db.execute("CREATE INDEX IF NOT EXISTS idx_track_positions_time ON track_positions(fix_time)")
            db.commit()
        TRACK_DB_INITIALIZED = True


def _device_boat_map() -> Dict[str, Optional[int]]:
    """Map each tracker unique_id to its permanently-assigned boat_id (or None).

    Used at insert time to stamp each fix with the boat it belongs to, so a boat's
    track stays with the boat even after its tracker is reassigned.
    """
    try:
        with get_db() as db:
            rows = db.execute("SELECT unique_id, boat_id FROM trackers WHERE active = 1").fetchall()
    except sqlite3.OperationalError:
        return {}
    return {r["unique_id"]: r["boat_id"] for r in rows}


def _latest_fix_times() -> Dict[str, float]:
    """Return the newest stored fix_time per unique_id (for de-duplication)."""
    try:
        with get_track_db() as db:
            rows = db.execute("SELECT unique_id, MAX(fix_time) AS mx FROM track_positions GROUP BY unique_id").fetchall()
    except sqlite3.OperationalError:
        return {}
    return {r["unique_id"]: r["mx"] for r in rows}


def insert_positions(positions: List[Dict[str, Any]], retention_days: Optional[int] = None) -> int:
    """Store new fixes (skipping ones not newer than the last stored per device).

    Returns the number of rows inserted.

    It does **not** purge old rows. It used to, on every call — and every call is
    every position pushed by the relay, several a minute per tracker. The purge
    filters on fix_time, which no index covered, so each pushed fix scanned the
    whole table inside a write transaction. This database is deliberately in
    rollback journal mode rather than WAL (see core/db.py), where a writer
    excludes every reader: so each push locked out the clubhouse display, the
    competitor pages and the race sheet, all of which poll. Retention is a
    housekeeping job on a flat age cut and belongs on a timer, not on the path a
    tracker report takes. See purge_old_positions.
    """
    init_track_db()
    if not positions:
        return 0
    now = time.time()
    # retention_days is accepted and ignored: every caller still passes it, and
    # purge_old_positions is where it is now honoured. Resolving it here meant a
    # settings read from the main database on every pushed fix, for nothing.
    _ = retention_days
    latest = _latest_fix_times()
    # Ignore a stored "latest" that is itself implausibly far ahead, or that one
    # bad fix would keep the device permanently shut out (see the constant).
    horizon = now + FIX_FUTURE_TOLERANCE_S
    latest = {uid: ts for uid, ts in latest.items() if ts <= horizon}
    boat_of = _device_boat_map()  # device -> boat, stamped so the track stays with the boat
    inserted = 0
    with get_track_db() as db:
        for p in positions:
            uid = p.get("unique_id")
            ft = p.get("fix_time")
            if not uid or ft is None:
                continue
            if ft > horizon:      # bad clock on the tracker — don't poison the track
                continue
            if uid in latest and ft <= latest[uid]:
                continue
            db.execute(
                "INSERT INTO track_positions (device_id, unique_id, boat_id, name, lat, lon, speed_kn, course_deg, fix_time, server_time, battery_pct, "
                "altitude, accuracy, hdop, pdop, sat, rssi, valid, protocol) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (p.get("device_id"), uid, boat_of.get(uid), p.get("name"), p.get("lat"), p.get("lon"),
                 p.get("speed_kn"), p.get("course_deg"), ft, now, p.get("battery_pct"),
                 *(p.get(k) for k in QUALITY_FIELDS)),
            )
            latest[uid] = ft
            inserted += 1
        db.commit()
    return inserted


# Retention is checked this often by the background worker. Rows are kept for
# hundreds of days, so the exact moment one is dropped does not matter; keeping
# the write lock off the ingest path does.
PURGE_INTERVAL_S = 3600.0
_LAST_PURGE_AT = 0.0


def purge_old_positions(retention_days: Optional[int] = None,
                        force: bool = False) -> int:
    """Drop fixes past the retention window. Returns rows removed.

    Called from the background track loop rather than from insert_positions, and
    at most once an hour: this takes the exclusive write lock, and in rollback
    journal mode that stops every reader for its duration.
    """
    global _LAST_PURGE_AT
    now = time.time()
    if not force and (now - _LAST_PURGE_AT) < PURGE_INTERVAL_S:
        return 0
    _LAST_PURGE_AT = now
    if retention_days is None:
        retention_days = track_config()["retention_days"]
    init_track_db()
    cutoff = now - int(retention_days) * 86400
    try:
        with get_track_db() as db:
            removed = db.execute("DELETE FROM track_positions WHERE fix_time < ?",
                                 (cutoff,)).rowcount
            db.commit()
    except sqlite3.OperationalError:
        return 0
    return max(0, int(removed or 0))


def positions_since(unique_id: str, since_ts: float,
                    until_ts: Optional[float] = None) -> List[Dict[str, Any]]:
    """Return a device's fixes at/after ``since_ts``, oldest first.

    ``until_ts`` bounds the other end, which is what makes replay possible: the
    progress engine is a pure function of a fix list, so asking for the fixes up
    to a moment gives the fleet exactly as it stood then.
    """
    init_track_db()
    clause = "" if until_ts is None else " AND fix_time <= ?"
    params: Tuple[Any, ...] = (unique_id, since_ts) if until_ts is None else (unique_id, since_ts, until_ts)
    try:
        with get_track_db() as db:
            rows = db.execute(
                "SELECT lat, lon, fix_time, speed_kn, course_deg FROM track_positions "
                f"WHERE unique_id = ? AND fix_time >= ?{clause} ORDER BY fix_time ASC, id ASC",
                params,
            ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [{"lat": r["lat"], "lon": r["lon"], "t": r["fix_time"],
             "speed_kn": r["speed_kn"], "course_deg": r["course_deg"]} for r in rows]


def positions_for_boat_since(boat_id: int, since_ts: float,
                             until_ts: Optional[float] = None) -> List[Dict[str, Any]]:
    """Return a boat's fixes at/after ``since_ts``, oldest first.

    Keyed by boat_id (stamped at insert), so the track stays with the boat across
    tracker changes — fixes recorded while any device was assigned to this boat.
    ``until_ts`` bounds the other end (see positions_since).
    """
    init_track_db()
    clause = "" if until_ts is None else " AND fix_time <= ?"
    try:
        boat_key = int(boat_id)
    except (TypeError, ValueError):
        return []
    params: Tuple[Any, ...] = (boat_key, since_ts) if until_ts is None else (boat_key, since_ts, until_ts)
    try:
        with get_track_db() as db:
            rows = db.execute(
                "SELECT lat, lon, fix_time, speed_kn, course_deg FROM track_positions "
                f"WHERE boat_id = ? AND fix_time >= ?{clause} ORDER BY fix_time ASC, id ASC",
                params,
            ).fetchall()
    except (sqlite3.OperationalError, TypeError, ValueError):
        return []
    return [{"lat": r["lat"], "lon": r["lon"], "t": r["fix_time"],
             "speed_kn": r["speed_kn"], "course_deg": r["course_deg"]} for r in rows]


def _entry_loaner(entry: Any) -> Optional[str]:
    try:
        v = entry["tracker_unique_id"]
    except (KeyError, IndexError, TypeError):
        return None
    return str(v).strip() or None if v else None


def positions_for_entry_since(entry: Any, since_ts: float,
                              until_ts: Optional[float] = None) -> List[Dict[str, Any]]:
    """Return the track for a race entry since ``since_ts``.

    A per-race **loaner** override resolves by that specific device (unique_id);
    otherwise the boat's own track is used (by boat_id), so it survives permanent
    tracker changes. ``until_ts`` bounds the far end for replay.
    """
    loaner = _entry_loaner(entry)
    if loaner:
        return positions_since(loaner, since_ts, until_ts)
    try:
        boat_id = entry["boat_id"]
    except (KeyError, IndexError, TypeError):
        boat_id = None
    if boat_id:
        return positions_for_boat_since(int(boat_id), since_ts, until_ts)
    return []


def _latest_fix_for(column: str, value: Any,
                    before_ts: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """Newest stored fix matching a device (unique_id) or boat (boat_id).

    ``before_ts`` caps it, so a replay can ask where a boat was last seen as of a
    moment rather than where it is now.
    """
    if column not in ("unique_id", "boat_id"):  # guard: only these two are ever used
        return None
    init_track_db()
    clause = "" if before_ts is None else " AND fix_time <= ?"
    params: Tuple[Any, ...] = (value,) if before_ts is None else (value, before_ts)
    try:
        with get_track_db() as db:
            row = db.execute(
                f"SELECT lat, lon, fix_time, speed_kn, course_deg FROM track_positions "
                f"WHERE {column} = ?{clause} ORDER BY fix_time DESC, id DESC LIMIT 1",
                params,
            ).fetchone()
    except (sqlite3.OperationalError, TypeError, ValueError):
        return None
    if not row:
        return None
    return {"lat": row["lat"], "lon": row["lon"], "t": row["fix_time"],
            "speed_kn": row["speed_kn"], "course_deg": row["course_deg"]}


def latest_position_for_entry(entry: Any) -> Optional[Dict[str, Any]]:
    """The entry's most recent fix, **ignoring the race clock**.

    Course progress is measured only from fixes after the start, but the map and
    the "last fix" column should always show where a boat actually is — including
    before the race starts, or in the first minutes after it when no post-start
    fix has arrived yet.
    """
    loaner = _entry_loaner(entry)
    if loaner:
        return _latest_fix_for("unique_id", loaner)
    try:
        boat_id = entry["boat_id"]
    except (KeyError, IndexError, TypeError):
        boat_id = None
    return _latest_fix_for("boat_id", int(boat_id)) if boat_id else None


def latest_position_before(entry: Any, before_ts: float) -> Optional[Dict[str, Any]]:
    """Where the entry was last seen as of ``before_ts`` — the replay's equivalent
    of latest_position_for_entry, so a boat still shows on the map before the gun."""
    loaner = _entry_loaner(entry)
    if loaner:
        return _latest_fix_for("unique_id", loaner, before_ts)
    try:
        boat_id = entry["boat_id"]
    except (KeyError, IndexError, TypeError):
        boat_id = None
    return _latest_fix_for("boat_id", int(boat_id), before_ts) if boat_id else None


def latest_positions() -> Dict[str, Dict[str, Any]]:
    """Return the most recent stored fix per device, keyed by unique_id."""
    init_track_db()
    try:
        with get_track_db() as db:
            rows = db.execute(
                "SELECT t.unique_id, t.name, t.lat, t.lon, t.speed_kn, t.course_deg, t.fix_time, "
                "t.battery_pct "
                "FROM track_positions t "
                "JOIN (SELECT unique_id, MAX(fix_time) AS mx FROM track_positions GROUP BY unique_id) m "
                "ON t.unique_id = m.unique_id AND t.fix_time = m.mx"
            ).fetchall()
    except sqlite3.OperationalError:
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        out[r["unique_id"]] = {"unique_id": r["unique_id"], "name": r["name"], "lat": r["lat"],
                               "lon": r["lon"], "speed_kn": r["speed_kn"], "course_deg": r["course_deg"],
                               "fix_time": r["fix_time"], "battery_pct": r["battery_pct"]}
    return out


# ---------------------------------------------------------------------------
# Tracker <-> boat mapping (permanent) + per-entry loaner override
# ---------------------------------------------------------------------------
def list_trackers() -> List[Dict[str, Any]]:
    """Return all known trackers (device catalogue + permanent boat assignment)."""
    init_db()
    with get_db() as db:
        rows = db.execute("SELECT * FROM trackers ORDER BY label, unique_id").fetchall()
    return [dict(r) for r in rows]


def upsert_tracker(unique_id: str, label: str = "", boat_id: Optional[int] = None,
                   traccar_device_id: str = "", active: bool = True) -> List[str]:
    """Create or update a tracker's label / permanent boat assignment.

    Returns the unique ids of any **other** trackers this took the boat away
    from, so the caller can say so. Usually none.

    A boat has one tracker aboard it, and this enforces that. Without it the
    table happily held two devices pointed at one boat -- it is keyed on the
    device, so the second assignment simply inserted -- and every fix from both
    was stamped with that boat. The boat's track then became the two devices
    interleaved, which is fine while they are in the same place and disastrous
    when they are not: in the club race of 8 August 2026 one of Crackajack's two
    trackers was aboard Mojito, so the replay drew Crackajack flipping between
    the two boats several times a minute, and the repair was 30,000 rows
    (scripts/fix_track_misattribution.py).

    Displacing rather than refusing, because refusing would break the ordinary
    case. The Trackers page posts every row at once, so swapping a boat from one
    device to another arrives as one submit -- clear A, set B -- and a refusal
    would reject B whenever the form happened to reach it before A. Saying "this
    device is on Crackajack" means the old one no longer is, which is what
    somebody doing a swap means anyway.

    The displaced tracker is only unassigned, never deleted, and the fixes it
    already recorded keep the boat they were stamped with: that track happened
    and belongs to that boat.
    """
    unique_id = str(unique_id or "").strip()
    if not unique_id:
        return []
    init_db()
    now = datetime.now().isoformat(timespec="seconds")
    displaced: List[str] = []
    with get_db() as db:
        if boat_id:
            displaced = [r["unique_id"] for r in db.execute(
                "SELECT unique_id FROM trackers WHERE boat_id = ? AND unique_id != ?",
                (int(boat_id), unique_id)).fetchall()]
            if displaced:
                db.execute(
                    "UPDATE trackers SET boat_id = NULL, updated_at = ?"
                    " WHERE boat_id = ? AND unique_id != ?",
                    (now, int(boat_id), unique_id))
        db.execute(
            """
            INSERT INTO trackers (unique_id, traccar_device_id, label, boat_id, active, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(unique_id) DO UPDATE SET
                traccar_device_id = excluded.traccar_device_id,
                label = excluded.label,
                boat_id = excluded.boat_id,
                active = excluded.active,
                updated_at = excluded.updated_at
            """,
            (unique_id, str(traccar_device_id or ""), str(label or ""),
             boat_id if boat_id else None, 1 if active else 0, now),
        )
        db.commit()
    # Attribute this device's not-yet-attributed fixes to the boat, so a fix
    # recorded just before the assignment (or mid-race) still counts for the boat.
    # Fixes already tagged to another boat stay put (that track belongs to them).
    if boat_id:
        try:
            init_track_db()
            with get_track_db() as tdb:
                tdb.execute("UPDATE track_positions SET boat_id = ? WHERE unique_id = ? AND boat_id IS NULL",
                            (int(boat_id), unique_id))
                tdb.commit()
        except sqlite3.OperationalError:
            pass
    return displaced


# ---------------------------------------------------------------------------
# Sending a command to a tracker, and reading its reply
# ---------------------------------------------------------------------------
# Traccar's "custom" command type carries raw protocol text to the device: Codec 12
# for Teltonika, an @Track AT string for Queclink. It is queued while the device is
# asleep and delivered on its next connection, and the device's answer comes back as a
# commandResult event carrying `attributes.result`. Measured round trips: ~6 s on a
# GL521MG holding a TCP long-connection, 95 s to 5 min on an ATC700 that wakes on its
# own reporting schedule.

# Commands that would undo the club's configuration. Refused outright rather than
# confirmed: a factory reset would wipe the server address, the APN and the reporting
# intervals, and a unit that forgets its server address stops being findable at all.
# Recovering one needs the configurator over USB, which means getting the tracker off
# the boat. Queclink GTRTO sub-command 4 is RESET (3 is REBOOT, which is fine).
DESTRUCTIVE_COMMAND_PATTERNS = (
    (re.compile(r"AT\+GTRTO\s*=\s*[^,]*,\s*0*4\s*,", re.I), "a Queclink factory reset (GTRTO sub-command 4)"),
    (re.compile(r"AT\+GTFRI\s*=\s*[^,]*,\s*0*4\s*,", re.I), "a Queclink factory reset"),
    (re.compile(r"\bsetparam\s+1\s*:", re.I), "a Teltonika factory-default write"),
    (re.compile(r"\bfactoryreset\b", re.I), "a factory reset"),
    (re.compile(r"\bdeleterecords\b", re.I), "erasing the device's stored records"),
)


def command_refusal(text: str) -> Optional[str]:
    """Why this command must not be sent, or None if it may be.

    Deliberately a blocklist rather than an allowlist: nearly everything useful we
    learned about these trackers came from sending something nobody had anticipated,
    and an allowlist would have prevented all of it.
    """
    body = str(text or "").strip()
    if not body:
        return "Nothing to send."
    if len(body) > 160:
        return f"Too long: {len(body)} characters, and the limit is 160."
    for pattern, what in DESTRUCTIVE_COMMAND_PATTERNS:
        if pattern.search(body):
            return (f"That looks like {what}. The app will not send it — it would wipe the "
                    "server address and the reporting configuration, and the tracker would "
                    "have to come off the boat to be set up again over USB.")
    return None


def send_tracker_command(unique_id: str, text: str,
                         cfg: Optional[Dict[str, Any]] = None) -> Tuple[bool, str]:
    """Send a raw protocol command to a tracker through Traccar. Returns (ok, message)."""
    unique_id = str(unique_id or "").strip()
    body = str(text or "").strip()
    refusal = command_refusal(body)
    if refusal:
        return False, refusal
    cfg = cfg or track_config()
    if not (cfg["base_url"] and cfg["token"]):
        return False, "Traccar is not configured, so there is nowhere to send it."
    try:
        devices = traccar_list_devices(cfg, include_unowned=True)
    except Exception as exc:
        return False, f"Could not reach Traccar: {str(exc)[:160]}"
    device_id = next((d.get("id") for d in devices
                      if isinstance(d, dict) and str(d.get("uniqueId")) == unique_id), None)
    if device_id is None:
        return False, f"Traccar does not know a device with id '{unique_id}'."
    try:
        _traccar_request(cfg, "POST", "/api/commands/send",
                         body={"deviceId": int(device_id), "type": "custom",
                               "attributes": {"data": body}}, timeout=10.0)
    except Exception as exc:
        return False, f"Traccar would not take the command: {str(exc)[:160]}"
    return True, ("Sent. A tracker that is awake answers in seconds; one that sleeps "
                  "between reports answers on its next connection.")


def tracker_command_results(unique_id: str, since_ts: Optional[float] = None,
                            cfg: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Replies a tracker has sent back, newest last. Never raises."""
    unique_id = str(unique_id or "").strip()
    cfg = cfg or track_config()
    if not (cfg["base_url"] and cfg["token"]):
        return []
    since_ts = since_ts or (time.time() - 1800)
    try:
        devices = traccar_list_devices(cfg, include_unowned=True)
        device_id = next((d.get("id") for d in devices
                          if isinstance(d, dict) and str(d.get("uniqueId")) == unique_id), None)
        if device_id is None:
            return []
        query = urllib.parse.urlencode({
            "deviceId": int(device_id),
            "from": _iso_utc(since_ts),
            "to": _iso_utc(time.time() + 60),
        })
        events = _traccar_get(cfg, f"/api/reports/events?{query}", timeout=10.0)
    except Exception:
        return []
    out: List[Dict[str, Any]] = []
    for e in events if isinstance(events, list) else []:
        if not isinstance(e, dict):
            continue
        result = (e.get("attributes") or {}).get("result")
        if not result:
            continue
        out.append({"at": _parse_iso_epoch(e.get("eventTime")) or time.time(),
                    "type": str(e.get("type") or ""), "result": str(result)})
    out.sort(key=lambda r: r["at"])
    return out


# Prebuilt commands, per protocol family, because the two dialects have nothing in
# common. Every Teltonika interval template writes BOTH halves of the home/roaming pair:
# the club's SIM roams in the UK, so the roaming half is the live one, and a device
# accepts a write to the dead half and reads it back correctly while ignoring it.
#
# ``reply`` says what the app will actually be able to show, which is not the same as
# what the device sends:
#
#   "data"     — the answer itself comes back and appears in the console. Teltonika
#                replies over Codec 12, and the reply *is* the data.
#   "position" — no text, but the device reports a fix, so the answer lands on the map
#                and in the track.
#   "ack"      — the app sees "+ACK:GTRTO" and nothing more. Queclink's @Track protocol
#                answers a query with a separate +RESP message, and Traccar's decoder
#                surfaces the acknowledgement while discarding the payload. Verified
#                against the club's own units on 20 Aug for CSQ, INF and a config read:
#                every one produced an ACK alone. The command still runs on the tracker;
#                its answer is simply not visible from here, and would need raw logging
#                turned on at the relay to read.
COMMAND_TEMPLATES: Dict[str, List[Dict[str, str]]] = {
    "gl200": [
        {"label": "Ask for a position now", "command": "AT+GTRTO=gl521m,1,,,,,,FFFF$",
         "reply": "position",
         "note": "Answers in about a second, and the fix appears on the map and in the "
                 "boat's track. Does not disturb the reporting schedule."},
        {"label": "Reboot (about 6 minutes off the air)",
         "command": "AT+GTRTO=gl521m,3,,,,,,FFFF$", "reply": "ack",
         "note": "Measured: 5 min 45 s with no fixes, then a few minutes at reduced "
                 "accuracy. Configuration survives. Never do this to a boat approaching "
                 "the finish — being blind is worse than being slightly displaced."},
        {"label": "Read the main configuration (answer not visible here)",
         "command": "AT+GTRTO=gl521m,2,CFG,,,,,FFFF$", "reply": "ack",
         "note": "The tracker does answer, with its AT+GTCFG line, but Traccar's Queclink "
                 "decoder discards it. Reading it needs raw logging at the relay."},
        {"label": "Read the server settings (answer not visible here)",
         "command": "AT+GTRTO=gl521m,2,SRI,,,,,FFFF$", "reply": "ack",
         "note": "Server address, port and report mode — discarded by the decoder as above."},
        {"label": "Signal strength (answer not visible here)",
         "command": "AT+GTRTO=gl521m,7,,,,,,FFFF$", "reply": "ack",
         "note": "Would tell you whether a tracker reporting late has a weak signal."},
        {"label": "Firmware and hardware version (answer not visible here)",
         "command": "AT+GTRTO=gl521m,8,,,,,,FFFF$", "reply": "ack",
         "note": "Firmware and hardware revision."},
    ],
    "teltonika": [
        {"label": "Read the reporting configuration",
         "command": "getparam 10000;10100;10050;10150;1011;1000", "reply": "data",
         "note": "On-stop and moving intervals, home and roaming, plus record priority "
                 "and the open-link timeout."},
        {"label": "Read power and sleep settings",
         "command": "getparam 102;116;1012;12150;19000", "reply": "data",
         "note": "Sleep mode, power-bank support, movement mode and accelerometer "
                 "sensitivity."},
        {"label": "Send everything buffered now", "command": "getrecord", "reply": "data",
         "note": "Forces a high-priority record, flushing anything held on the device."},
        {"label": "Device status", "command": "getstatus", "reply": "data", "note": ""},
        {"label": "Battery", "command": "battery", "reply": "data",
         "note": "Voltage and current."},
        {"label": "Report every 10 s while moving (race)",
         "command": "setparam 10050:10;10150:10", "reply": "data",
         "note": "Both halves: the club's SIM roams, so the roaming value is the one "
                 "that takes effect. Costs about 10%/h."},
        {"label": "Report every 60 s while moving (cruise)",
         "command": "setparam 10050:60;10150:60", "reply": "data",
         "note": "Both halves, as above. Measured at roughly 9%/h — barely cheaper than "
                 "10 s, so this saves far less than it looks."},
        {"label": "Report every 5 min when parked",
         "command": "setparam 10000:300;10100:300", "reply": "data",
         "note": "Both halves, as above. About 1.1-1.7%/h."},
        {"label": "Reboot", "command": "cpureset", "reply": "ack",
         "note": "Restarts the device. Configuration survives."},
    ],
}


def command_templates_for(unique_id: str, protocol: Optional[str] = None) -> List[Dict[str, str]]:
    """The prebuilt commands that make sense for this tracker."""
    proto = str(protocol or tracker_protocols().get(str(unique_id), "") or "").strip().lower()
    return COMMAND_TEMPLATES.get(proto, [])


def unregistered_traccar_devices(cfg: Optional[Dict[str, Any]] = None,
                                 limit: int = 50) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """Devices Traccar knows about that this app has not adopted yet.

    Switch a tracker on and — with automatic registration enabled on the relay —
    Traccar creates the device the moment it connects. Listing those here lets a
    race officer pick it off a list and assign a boat, instead of typing a
    15-digit IMEI. Most recently heard from first; ones that have never reported
    sort last. Returns ``(devices, error)`` and never raises.
    """
    cfg = cfg or track_config()
    if not (cfg["base_url"] and cfg["token"]):
        return [], None
    try:
        devices = traccar_list_devices(cfg, include_unowned=True)
    except Exception as exc:
        return [], f"Could not list Traccar devices: {exc}"
    known = {str(t.get("unique_id")) for t in list_trackers()}
    now = time.time()
    # A Traccar *device* carries no battery — that lives on its positions. We store
    # every fix the poll returns, registered here or not, so the level comes from the
    # same place as the registered table's: the newest stored fix.
    latest = latest_positions()
    out: List[Dict[str, Any]] = []
    for d in devices:
        if not isinstance(d, dict):
            continue
        unique_id = str(d.get("uniqueId", "") or "").strip()
        if not unique_id or unique_id in known:
            continue
        last = _parse_iso_epoch(d.get("lastUpdate"))
        age = (now - last) if last else None
        fix = latest.get(unique_id) or {}
        out.append({
            "unique_id": unique_id,
            "traccar_device_id": str(d.get("id", "") or ""),
            "name": str(d.get("name", "") or ""),
            "status": str(d.get("status", "") or ""),
            "last_update": last,
            "age": age,
            "age_text": _fmt_age(age) if age is not None else "never reported",
            "rag": _rag(age) if age is not None else "red",
            # The battery's age is the age of the stored fix that carried it, NOT the
            # device's lastUpdate in Traccar. They are two different clocks: Traccar can
            # have heard from a device minutes ago while the newest fix we hold for it is
            # a day old, and using the wrong one would call a day-old level current.
            **battery_reading(fix.get("battery_pct"), (now - fix["fix_time"]) if fix.get("fix_time") else None),
        })
    # Newest first; never-reported devices last.
    out.sort(key=lambda d: (d["age"] is None, d["age"] if d["age"] is not None else 0))
    return out[:limit], None


def adopt_tracker(unique_id: str, name: str = "", boat_id: Optional[int] = None) -> Tuple[bool, str]:
    """Adopt a device Traccar already knows about, linking it to a boat.

    Takes over a device Traccar already knows about, typically auto-registered when
    the tracker first connected. This is the only way trackers are added: a form for
    typing an IMEI by hand existed until v0.270 and was removed, because a tracker
    that has never reached Traccar cannot be tracked anyway, and the list next to it
    is both easier and impossible to mistype. Returns (ok, message).
    """
    unique_id = str(unique_id or "").strip()
    if not unique_id:
        return False, "No tracker specified."
    cfg = track_config()
    traccar_id = ""
    label = name
    note = ""
    if cfg["base_url"] and cfg["token"]:
        try:
            devices = traccar_list_devices(cfg, include_unowned=True)
            owned = {str(d.get("uniqueId")) for d in traccar_list_devices(cfg)}
        except Exception as exc:
            return False, f"Could not read the device from Traccar: {str(exc)[:200]}"
        for d in devices:
            if str(d.get("uniqueId")) == unique_id:
                traccar_id = str(d.get("id", "") or "")
                label = label or str(d.get("name", "") or "")
                break
        if not traccar_id:
            return False, f"Traccar no longer lists a device with id '{unique_id}'."
        # An auto-registered device belongs to nobody, and its positions are then
        # invisible to /api/positions — the poller and back-fill would never see
        # that boat. Claim it for the token's user.
        if unique_id not in owned:
            user_id = traccar_user_id(cfg)
            if user_id is None:
                note = (" Traccar has more than one account, so it could not be claimed automatically —"
                        " link it to your Traccar user or positions will only arrive by push.")
            else:
                try:
                    traccar_link_device(cfg, traccar_id, user_id)
                except Exception as exc:
                    note = (f" It could not be claimed in Traccar ({str(exc)[:120]}); positions will only"
                            " arrive by push until it is linked to your Traccar user.")
    displaced = upsert_tracker(unique_id, label=label, boat_id=boat_id,
                               traccar_device_id=traccar_id)
    if displaced:
        # Adopting a spare onto a boat that already has one is precisely how a
        # boat came to have two, so this is the message that is worth having.
        note += (" A boat carries one tracker, so " + ", ".join(displaced)
                 + (" was" if len(displaced) == 1 else " were")
                 + " unassigned; past fixes keep the boat they were recorded against.")
    return True, f"Tracker '{label or unique_id}' added from Traccar." + note


def remove_tracker(unique_id: str) -> Tuple[bool, str]:
    """Remove a tracker: delete it from Traccar (if linked) and drop the app record.
    Position history is kept — a boat's past track stays with the boat."""
    unique_id = str(unique_id or "").strip()
    if not unique_id:
        return False, "No tracker specified."
    init_db()
    with get_db() as db:
        row = db.execute("SELECT traccar_device_id FROM trackers WHERE unique_id = ?", (unique_id,)).fetchone()
    if not row:
        return False, "Tracker not found."
    note = ""
    cfg = track_config()
    if row["traccar_device_id"] and cfg["base_url"] and cfg["token"]:
        try:
            traccar_delete_device(cfg, row["traccar_device_id"])
        except Exception as exc:
            note = f" (could not delete it in Traccar: {str(exc)[:150]})"
    with get_db() as db:
        db.execute("DELETE FROM trackers WHERE unique_id = ?", (unique_id,))
        db.commit()
    return True, "Tracker removed." + note


def tracker_for_boat(boat_id: Optional[int]) -> Optional[str]:
    """Return the unique_id permanently assigned to a boat, or None."""
    if not boat_id:
        return None
    init_db()
    with get_db() as db:
        row = db.execute("SELECT unique_id FROM trackers WHERE boat_id = ? AND active = 1 LIMIT 1", (boat_id,)).fetchone()
    return row["unique_id"] if row else None


def last_tracker_seen_for_boat(boat_id: Optional[int]) -> Optional[str]:
    """The device a boat last reported from, whether or not it still has one.

    Stored fixes carry the boat they belonged to, so a boat's track survives its
    tracker being removed — but only the *fixes* survive. The trackers table is
    what says which device a boat has, and deleting the tracker deletes that, so
    there was nothing left to say the boat had ever been tracked at all.
    """
    if not boat_id:
        return None
    init_track_db()
    try:
        with get_track_db() as db:
            row = db.execute(
                "SELECT unique_id FROM track_positions WHERE boat_id = ?"
                " ORDER BY fix_time DESC, id DESC LIMIT 1", (int(boat_id),)).fetchone()
    except (sqlite3.OperationalError, TypeError, ValueError):
        return None
    return str(row["unique_id"]) if row and row["unique_id"] else None


def effective_tracker_for_entry(entry: Any) -> Optional[str]:
    """Resolve an entry's tracker: per-race loaner override, else the boat's, else
    the last device the boat was seen on.

    That last fallback is what keeps an old race readable after its trackers are
    thrown away. The race's chart, progress walk and replay all ask this first —
    as "is this entry tracked at all" — before going near the positions, and a
    simulated fleet deleted from the Trackers page took every race it had ever
    sailed blank with it, while its fixes sat untouched in the database. The
    positions themselves are found by *boat*, so nothing here narrows a boat's
    track to one device; this only answers whether there is a track to look for.
    """
    override = None
    try:
        override = entry["tracker_unique_id"]
    except (KeyError, IndexError, TypeError):
        override = None
    if override:
        return str(override).strip() or None
    boat_id = None
    try:
        boat_id = entry["boat_id"]
    except (KeyError, IndexError, TypeError):
        boat_id = None
    return tracker_for_boat(boat_id) or last_tracker_seen_for_boat(boat_id)


def tracker_assigned_for_entry(entry: Any) -> Optional[str]:
    """The device this entry has **now**: the boat's tracker, or the retired
    per-entry override where an old row still carries one.

    The same resolution as ``effective_tracker_for_entry`` minus the historical
    fallback, so it answers a different question: not "is there a track to look
    for" but "should this boat be reporting". A boat whose tracker has been taken
    off it is not a boat that has gone quiet, and the fleet list has to tell those
    apart -- "No tracker" against "Not reporting".
    """
    try:
        override = entry["tracker_unique_id"]
    except (KeyError, IndexError, TypeError):
        override = None
    if override:
        return str(override).strip() or None
    try:
        boat_id = entry["boat_id"]
    except (KeyError, IndexError, TypeError):
        boat_id = None
    return tracker_for_boat(boat_id)


# ---------------------------------------------------------------------------
# Finish proposals (RO-confirmed or auto-confirmed GPS finishes)
# ---------------------------------------------------------------------------
def open_proposal_exists(race_id: int, entry_id: int) -> bool:
    """True if there is already a pending or confirmed proposal for this entry."""
    init_db()
    with get_db() as db:
        row = db.execute(
            "SELECT 1 FROM finish_proposals WHERE race_id = ? AND entry_id = ? AND status IN ('pending','confirmed') LIMIT 1",
            (race_id, entry_id),
        ).fetchone()
    return bool(row)


def detection_blocked_by_proposal(race_id: int, entry_id: int, finish_time: Any) -> bool:
    """Should GPS finish detection leave this entry alone?

    A **pending** proposal always blocks: the race officer is being asked about
    that crossing and stacking duplicates on top helps nobody.

    A **confirmed** one blocks only while the entry actually holds the finish it
    confirmed. If the finish time has since been cleared — an officer undoing a
    GPS finish they disagreed with — the entry is racing again with no time, and
    a stale proposal must not lock GPS out of ever noticing it finish.
    """
    init_db()
    with get_db() as db:
        row = db.execute(
            "SELECT status FROM finish_proposals WHERE race_id = ? AND entry_id = ?"
            " AND status IN ('pending','confirmed') ORDER BY id DESC LIMIT 1",
            (race_id, entry_id),
        ).fetchone()
    if not row:
        return False
    if row["status"] == "pending":
        return True
    return bool(finish_time)


def create_finish_proposal(race_id: int, entry_id: int, detected_iso: str,
                           lat: Optional[float], lon: Optional[float]) -> Optional[int]:
    """Insert a pending finish proposal (idempotent per open entry)."""
    if open_proposal_exists(race_id, entry_id):
        return None
    init_db()
    now = datetime.now().isoformat(timespec="seconds")
    with get_db() as db:
        cur = db.execute(
            "INSERT INTO finish_proposals (race_id, entry_id, detected_time, lat, lon, source, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, 'gps', 'pending', ?)",
            (race_id, entry_id, detected_iso, lat, lon, now),
        )
        db.commit()
        return int(cur.lastrowid)


def pending_proposals_for_race(race_id: int) -> List[Dict[str, Any]]:
    """Return pending GPS finish proposals for a race (for the RO review UI)."""
    init_db()
    with get_db() as db:
        rows = db.execute(
            "SELECT * FROM finish_proposals WHERE race_id = ? AND status = 'pending' ORDER BY detected_time ASC",
            (race_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def record_gps_finish(race_id: int, entry_id: int, iso_time: str, source: str,
                      fire_horn: bool = False, label: str = "") -> None:
    """Write a finish for an entry, mirroring the manual finish_now side-effects.

    Sets finish_time / finish_source / status=FINISHED, logs the event, schedules
    a finish video clip around the moment for later review, and (only if asked)
    fires the horn. Shared by auto-confirm and the RO confirm route.
    """
    init_db()
    with get_db() as db:
        row = db.execute("SELECT boat_name, sail_no FROM entries WHERE id = ? AND race_id = ?", (entry_id, race_id)).fetchone()
        db.execute(
            "UPDATE entries SET finish_time = ?, finish_source = ?, status = 'FINISHED' WHERE id = ? AND race_id = ?",
            (iso_time, source, entry_id, race_id),
        )
        db.commit()
    boat_label = label or (f"{row['boat_name']} ({row['sail_no']})" if row and row["sail_no"] else (row["boat_name"] if row else f"entry {entry_id}"))
    try:
        log_event(race_id, "finish", boat_label, source, {"entry_id": entry_id, "finish_time": iso_time})
    except Exception:
        pass
    if fire_horn:
        try:
            horn.fire_horn(horn.hardware_config().get("horn_duration_ms", 1200))
        except Exception:
            pass
    scheduler = getattr(horn, "VIDEO_CLIP_SCHEDULER", None)
    if scheduler:
        try:
            scheduler(race_id, "finish", iso_time, entry_id=entry_id, label=f"GPS finish — {boat_label}")
        except Exception:
            pass


def confirm_proposal(proposal_id: int) -> bool:
    """RO confirms a pending proposal → record the finish (gps-confirmed)."""
    init_db()
    with get_db() as db:
        row = db.execute("SELECT * FROM finish_proposals WHERE id = ? AND status = 'pending'", (proposal_id,)).fetchone()
        if not row:
            return False
        db.execute("UPDATE finish_proposals SET status = 'confirmed' WHERE id = ?", (proposal_id,))
        db.commit()
    record_gps_finish(int(row["race_id"]), int(row["entry_id"]), str(row["detected_time"]), "gps-confirmed")
    return True


def dismiss_proposal(proposal_id: int) -> bool:
    """RO dismisses a pending proposal (no finish recorded)."""
    init_db()
    with get_db() as db:
        cur = db.execute("UPDATE finish_proposals SET status = 'dismissed' WHERE id = ? AND status = 'pending'", (proposal_id,))
        db.commit()
    return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Finish detection orchestration (ties positions to races/entries)
# ---------------------------------------------------------------------------
def _effective_course(race: Any) -> Optional[Dict[str, Any]]:
    """The active course for a race, with course-shortening applied if called.

    When the RO has shortened the course, boats round only up to the shorten mark
    and then sail to the finish, so GPS progress/finish must follow the shortened
    course — not the original full one.
    """
    try:
        from core.courses import course_for_race, apply_course_shortening
        course = course_for_race(race)
    except Exception:
        return None
    try:
        idx = race["shortened_at_index"] if "shortened_at_index" in race.keys() else None
    except Exception:
        idx = None
    if idx is not None and str(idx) != "":
        course = apply_course_shortening(course, idx)
    return course


def _course_ref_point(race: Any) -> Optional[Tuple[float, float]]:
    """Centroid of the active course's marks — a point on the course side."""
    course = _effective_course(race)
    if not course:
        return None
    lats, lons = [], []
    at = race_marks(race)
    for item in (course.get("marks") or []):
        # Course marks are dicts like {"token","mark","rounding"}; tolerate plain
        # code strings too. Skip the ODM "O" (it is the finish-line endpoint).
        code = item.get("mark") if isinstance(item, dict) else item
        if not code or str(code) == "O":
            continue
        mark = at.get(str(code))
        if mark and mark.get("lat") is not None and mark.get("lon") is not None:
            lats.append(float(mark["lat"]))
            lons.append(float(mark["lon"]))
    if not lats:
        return None
    return (sum(lats) / len(lats), sum(lons) / len(lons))


def finish_direction_point(seq: List[Dict[str, Any]],
                           line_a: Tuple[float, float], line_b: Tuple[float, float],
                           course_ref: Optional[Tuple[float, float]] = None
                           ) -> Optional[Tuple[float, float]]:
    """The side of the line a finishing boat crosses *from*: the last mark rounded.

    A finish is a crossing that leaves the course, so something has to say which way
    that is. The centroid of the course marks (_course_ref_point) was standing in for
    it, and it is the wrong point: ``_side`` measures against the *infinite* line
    through the two ends, so a course with marks on both sides of that extension has a
    centroid that can land on the far side from where the boats actually come in. The
    direction test is then exactly backwards — the real finish is read as entering the
    course and thrown away, and the next crossing is taken instead. Measured on the
    club's own courses, 8 of 12 have marks that straddle the line that way, and every
    tracked boat-race in the database finished 25-46 s late because of it: the time
    recorded was the boat crossing the line again on its way back to the marina.

    The last mark rounded is the point that actually defines it, and it is what the
    racing rules mean by the "course side" a boat must finish from. Falls
    back to ``course_ref`` when there is no previous mark, or when it sits exactly on
    the line and so gives no direction.
    """
    if len(seq) >= 2:
        prev = seq[-2]
        lat, lon = prev.get("lat"), prev.get("lon")
        if lat is not None and lon is not None:
            lat0, lon0 = line_a
            a = _project(line_a[0], line_a[1], lat0, lon0)
            b = _project(line_b[0], line_b[1], lat0, lon0)
            p = _project(float(lat), float(lon), lat0, lon0)
            if _side(a[0], a[1], b[0], b[1], p[0], p[1]) != 0:
                return (float(lat), float(lon))
    return course_ref


def course_rounding_sequence(race: Any) -> List[Dict[str, Any]]:
    """Return the ordered marks a boat must round, resolved to lat/lon.

    The active course's ``marks`` list IS the rounding sequence, and its last
    element is the finish (the ODM/line). O commonly appears mid-sequence as a
    rounding mark too, so a finish must not be inferred from an early pass of the
    line — see boat_course_progress. When the course has been **shortened**, the
    sequence is the truncated marks with the finish line (O) appended, since boats
    round only to the shorten mark and then sail to the line.

    Built from ``expand_course_points``, which is what the chart, the leg lengths
    and the TWA analysis are drawn from. It used to walk ``course["marks"]`` with
    its own loop, and the two disagreed about **compound marks**: Y and A name a
    pair of corner buoys and carry no position of their own, so the chart expanded
    Y into Ya and Yb while this looked up "Y", found no lat/lon, and silently
    dropped it. A course to the Gwylan Islands therefore asked boats to round
    nothing there — and since the finish is only looked for once every earlier mark
    is rounded, a boat could be finished having never sailed west of Abersoch.
    Both are now the same function; there is no second answer to keep in step.

    **A course nobody has set has no rounding sequence.** ``course_no`` defaults
    to 1 and ``course_for_race`` hands that back so the geometry has something to
    work with, which is right for the chart and wrong for everything downstream
    of here: a race whose header reads *Course: not set yet* listed a boat as
    "0/7, next 1, 5.05 nm to go" round a course the race officer never picked,
    and finish detection was walking the same guess. The spoken announcement
    already refuses on this ground -- reading out a guessed course would send the
    fleet the wrong way -- and so, now, does the sequence itself.
    """
    try:
        if not int(race["course_set"] if "course_set" in race.keys() else 1):
            return []
    except (AttributeError, KeyError, IndexError, TypeError, ValueError):
        # No race row, or one without the column: fall through to the geometry,
        # which is what every caller did before this guard existed.
        pass
    course = _effective_course(race)
    if not course:
        return []
    seq: List[Dict[str, Any]] = []
    # The positions this race was sailed to, not wherever the marks are today —
    # a mark corrected since could otherwise sit further than the rounding radius
    # from where the boats actually went, and the walk is sequential, so one such
    # mark stalls every mark behind it.
    at = race_marks(race)
    # Imported here rather than at the top for the same reason _effective_course
    # does it: core.courses is part of the same cycle.
    from core.courses import expand_course_points
    # [1:] drops the leading start point: this is what has to be *rounded*, and the
    # start is somewhere boats come from, not a mark they go round.
    expanded = expand_course_points(course)
    for position, point in enumerate(expanded[1:], start=1):
        prev_point = expanded[position - 1]
        prev_mark = at.get(str(prev_point.get("mark") or ""))
        from_point = ({"lat": float(prev_mark["lat"]), "lon": float(prev_mark["lon"])}
                      if prev_mark and prev_mark.get("lat") is not None else None)
        code = str(point.get("mark") or "")
        mark = at.get(code) if code else None
        if mark and mark.get("lat") is not None and mark.get("lon") is not None:
            seq.append({"code": code, "lat": float(mark["lat"]), "lon": float(mark["lon"]),
                        # The compound mark this corner belongs to, where there is
                        # one — "Ya" is what a boat rounds, "Y" is what the course
                        # board and the announcement call it.
                        "parent": str(point.get("parent_mark") or code),
                        # A turning point rather than a mark: walked through so the
                        # distance still to sail goes round the corner, but passed
                        # by a gate rather than rounded, and counted nowhere.
                        "via": bool(point.get("waypoint")),
                        # None means "use the radius from Settings". A mark laid
                        # on a long scope, or one boats are told to give a wide
                        # berth, needs its own.
                        "radius_m": marks.mark_rounding_radius(mark),
                        # Which hand the mark is to be left on. Until now nothing
                        # in the walk knew this at all, so a boat rounding the
                        # wrong side counted exactly like one rounding correctly.
                        "side": (point.get("rounding")
                                 if point.get("rounding") in ("port", "starboard") else None),
                        # How well we know where this mark is — the width of the
                        # band in which no test can determine a side.
                        "accuracy_m": marks.mark_accuracy_m(mark),
                        # The course point before this one, which is what gives the
                        # gate its direction. The *previous point*, not the previous
                        # entry in seq: the first mark's leg comes from the start,
                        # which [1:] above has already dropped.
                        "from_point": from_point})
    # The last element of the sequence is the *finish*, and the walk never asks
    # for it to be rounded — it is reached by crossing the line. So when a course
    # already ends at the finish line's own mark, that mark is the finish and is
    # left as it is: boats sail to the line and cross it rather than going round
    # the buoy first. The club line's mark is O and an ISORA one's is F, and
    # either way requiring a rounding there would leave every boat unfinished.
    #
    # Otherwise the line is appended: a full course ending elsewhere, or a
    # shortened course ending at the shorten mark, still has to reach the line.
    line_mark = finish_line_seaward_mark(race)
    if seq and (not line_mark or seq[-1]["code"] != line_mark):
        points = race_finish_line_points(race)
        if points is not None:
            end = at.get(line_mark) or {}
            seq.append({"code": line_mark or "FINISH",
                        "lat": points[0][0], "lon": points[0][1],
                        "radius_m": marks.mark_rounding_radius(end)})
    # How near a boat has to pass each mark for leaving it again to count as having
    # rounded it (see mark_neighbourhood_m). Worked out here, once, because it
    # depends on the legs either side and the sequence is what knows them — and so
    # the live walk and the replay's cannot disagree about it.
    legs = [haversine_nm(a["lat"], a["lon"], b["lat"], b["lon"]) * 1852.0
            for a, b in zip(seq, seq[1:])]
    for i, mark in enumerate(seq):
        mark["neighbourhood_m"] = mark_neighbourhood_m(
            mark.get("radius_m") or 0.0,
            legs[i - 1] if i > 0 else 0.0,          # the leg arriving here
            legs[i] if i < len(legs) else 0.0,      # and the one leaving
        )
    return seq


# Boats do not report together. In race 65 the fleet's trackers ran at 2 s, 10 s
# and 61 s, and with dropouts the worst gap between two boats' last-known positions
# averaged 136 seconds and reached 306. Ranking the latest fixes against each other
# is therefore comparing boats at different moments: at 6 kn, 136 s is 420 m, which
# is more than enough to invert an order and had the board visibly swapping boats
# round while nothing on the water changed.
#
# Two ways to compare like with like. Rewinding everyone to the last moment they had
# all reported is exact, but that moment is 136 s ago and the board is then just
# late: measured on race 65's first leg it got the order right 29% of the time,
# worse than doing nothing. Carrying each boat forward from its last fix on its last
# known course and speed is a guess, but a short and cheap one - median error 7 m,
# 90th percentile 101 m - and it got the order right 86% of the time against 51%
# for the latest-fix comparison that shipped before.
# Past this the boat is missing rather than merely quiet, and is left where it last
# actually was. Tied to ESTIMATE_STALE_S because that is already this app's judgement
# of when a boat's data has stopped meaning anything, and the viewer is told.
#
# It is a genuine trade-off, not a free choice. Measured over race 65's first leg,
# a longer cap ranks better and jumps more: 180 s gave 71% of moments in exactly the
# right order with 3 visible changes, 300 s gave 85% with 8. Projecting further is
# more often right and corrects itself more violently when a real fix lands. The
# reported complaint was the jumping, so the shorter one wins.
PROJECT_MAX_AGE_S = 180.0        # kept equal to ESTIMATE_STALE_S, defined below


def dead_reckoned(fix: Dict[str, Any], age_s: float) -> Dict[str, Any]:
    """Where a boat has probably got to, ``age_s`` after it last reported.

    Straight on at its last course and speed. Wrong through a tack, but a tack
    costs less than the 420 m of staleness it is correcting for, and the error is
    bounded by how long the boat has been quiet.
    """
    speed = fix.get("sog", fix.get("speed_kn"))
    heading = fix.get("cog", fix.get("course_deg"))
    try:
        sog = float(speed or 0.0)
        cog = float(heading)
    except (TypeError, ValueError):
        return fix
    if age_s <= 0.0 or sog <= 0.0 or age_s > PROJECT_MAX_AGE_S:
        return fix          # too long unheard: show it where it last actually was
    run_m = sog * 1852.0 * age_s / 3600.0
    lat = float(fix["lat"]) + run_m * math.cos(math.radians(cog)) / 111132.0
    lon = float(fix["lon"]) + (run_m * math.sin(math.radians(cog))
                               / (111320.0 * math.cos(math.radians(float(fix["lat"])))))
    return {**fix, "lat": lat, "lon": lon}


def _distance_remaining_nm(pos: Dict[str, Any], idx: int, seq: List[Dict[str, Any]]) -> float:
    """Great-circle distance (nm) from pos to the next mark, then the rest of the legs."""
    if idx >= len(seq):
        return 0.0
    total = haversine_nm(pos["lat"], pos["lon"], seq[idx]["lat"], seq[idx]["lon"])
    for i in range(idx, len(seq) - 1):
        total += haversine_nm(seq[i]["lat"], seq[i]["lon"], seq[i + 1]["lat"], seq[i + 1]["lon"])
    return total


def _passed_mark(prev: Optional[Dict[str, Any]], fix: Dict[str, Any],
                 mark: Dict[str, Any], radius_m: float) -> bool:
    """Did the boat pass within ``radius_m`` of ``mark`` on its way to ``fix``?

    The fix itself counts, and so does the leg from the previous fix — otherwise
    a boat that stepped over the mark between two reports never rounds it. The
    leg is only trusted across a normal reporting gap: after a long outage (or a
    back-fill), a single enormous leg could otherwise sweep through marks the
    boat never went near.
    """
    if haversine_nm(fix["lat"], fix["lon"], mark["lat"], mark["lon"]) * 1852.0 <= radius_m:
        return True
    if prev is None:
        return False
    gap = float(fix.get("t") or 0.0) - float(prev.get("t") or 0.0)
    if gap <= 0 or gap > MARK_SEGMENT_MAX_GAP_S:
        return False
    return distance_to_segment_m(mark["lat"], mark["lon"],
                                 prev["lat"], prev["lon"],
                                 fix["lat"], fix["lon"]) <= radius_m


class MarkApproach:
    """Closest approach to the mark a boat is currently sailing to.

    One object per boat, reset each time the walk moves on to the next mark. Both
    walks — the live one and the replay's single-pass one — drive it through the same
    two methods, so the two cannot drift apart on how a rounding is decided (a test
    pins that they agree, and this is the state that would have made them diverge).
    """

    __slots__ = ("min_d", "next_at_min", "why")

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.min_d = None
        self.next_at_min = None
        self.why = ""

    def note(self, d: float, d_next: Optional[float]) -> None:
        """Record this fix's distance to the mark, and to the one after it."""
        if self.min_d is None or d < self.min_d:
            self.min_d = d
            self.next_at_min = d_next

    def departed(self, d: float, d_next: Optional[float],
                 neighbourhood_m: float, depart_m: float = MARK_DEPART_M) -> bool:
        """Has the boat rounded: came close enough, and has since left towards the next?

        Not counted at the moment of closest approach but once the boat has opened up
        ``depart_m`` from it, which is what stops a tack on the beat reading as a
        departure — and is why this fires a little *after* the radius rather than
        before it. The next mark having closed since is the confirming half: a boat
        merely passing a mark it is not rounding does not then set off towards the
        one after it.
        """
        if self.min_d is None or self.min_d > neighbourhood_m:
            return False
        if d < self.min_d + depart_m:
            return False
        if self.next_at_min is None or d_next is None:
            return True                 # nothing to confirm against; the departure stands
        return d_next < self.next_at_min


def _mark_distances(fix: Dict[str, Any], seq: List[Dict[str, Any]],
                    idx: int) -> Tuple[float, Optional[float]]:
    """Distance from a fix to seq[idx], and to seq[idx + 1] when there is one."""
    here = haversine_nm(fix["lat"], fix["lon"], seq[idx]["lat"], seq[idx]["lon"]) * 1852.0
    after = None
    if idx + 1 < len(seq):
        after = haversine_nm(fix["lat"], fix["lon"],
                             seq[idx + 1]["lat"], seq[idx + 1]["lon"]) * 1852.0
    return here, after


def passed_waypoint(prev_pt: Optional[Dict[str, Any]], wp: Dict[str, Any],
                    fix: Dict[str, Any]) -> bool:
    """Has the boat got past a waypoint — a turning point, not a mark to round?

    A gate, not a circle: the boat has passed once it is on the far side of the
    line drawn through the waypoint at right angles to the leg arriving there.

    A radius cannot do this job. Boats beating past a headland pass a long way
    offshore, so the radius would have to be a mile or more — and the radius test
    fires on proximity alone, with no departure to hold it back, so a 2 km radius
    on a waypoint 12.6 km down the leg advances the walk when the boat is still
    84% short of it and nowhere near round the corner. Widening the
    closest-approach neighbourhood instead fails differently: its departure
    threshold is a fixed 50 m, so a single tack at 1900 m counts as leaving a
    2 km neighbourhood.

    The gate has neither problem. It cannot fire early, because the boat has to
    physically draw level with the turning point; and it does not care how far
    offshore that happens, which is the whole reason waypoints exist.
    """
    if prev_pt is None:
        return True                    # nothing to take a direction from
    lat0 = float(wp["lat"])
    m_per_deg_lat = 111132.0
    m_per_deg_lon = 111320.0 * math.cos(math.radians(lat0))
    # The leg arriving at the waypoint, as a vector in metres (east, north).
    lx = (float(wp["lon"]) - float(prev_pt["lon"])) * m_per_deg_lon
    ly = (float(wp["lat"]) - float(prev_pt["lat"])) * m_per_deg_lat
    if lx == 0.0 and ly == 0.0:
        return True
    # The boat relative to the waypoint, same units.
    bx = (float(fix["lon"]) - float(wp["lon"])) * m_per_deg_lon
    by = (float(fix["lat"]) - float(wp["lat"])) * m_per_deg_lat
    # Positive once the boat is beyond the waypoint in the direction of travel,
    # however far to one side of the leg it is.
    return (bx * lx + by * ly) >= 0.0


def rounded_mark(prev: Optional[Dict[str, Any]], fix: Dict[str, Any],
                 seq: List[Dict[str, Any]], idx: int, radius_m: float,
                 approach: MarkApproach,
                 gate_reach_m: float = rounding.DEFAULT_GATE_REACH_M) -> bool:
    """Has the boat rounded seq[idx] by the time of ``fix``?

    The radius first, because it is cheap and it fires the instant a boat rounds
    tight, which is the common case and the one where nobody wants extra latency.
    The closest-approach test then catches what the radius cannot see: the wide
    rounding, and the coarse reporting rate that steps clean over a mark.
    """
    mark = seq[idx]
    radius = mark.get("radius_m") or radius_m
    # 1. Inside the rounding radius. Cheapest, and it asks nothing about sides.
    if _passed_mark(prev, fix, mark, radius):
        approach.why = f"passed within {radius:.0f} m"
        return True
    # 2. The gate: a line through the mark square to the leg arriving at it, reaching
    #    far on the hand the boat should pass and only the rounding radius on the
    #    other. This is what catches the wide rounding — the case that stalled
    #    CRACKAJACK at AA 420 m off for six hours — and the asymmetry is the only
    #    thing in the walk that knows a mark has a required side at all.
    wrong_side = rounding.undetermined_side_m(mark.get("accuracy_m"), radius)
    if rounding.crossed_gate(prev, fix, mark.get("from_point"), mark, mark.get("side"),
                             float(mark.get("gate_reach_m") or gate_reach_m), wrong_side):
        approach.why = (f"crossed the gate on the {mark.get('side')} hand "
                        f"(reaching {float(mark.get('gate_reach_m') or gate_reach_m):.0f} m "
                        f"that side, {wrong_side:.0f} m the other)")
        return True
    # 3. Came close and has since left towards the next mark. The catch-all.
    d, d_next = _mark_distances(fix, seq, idx)
    approach.note(d, d_next)
    return approach.departed(d, d_next,
                             mark.get("neighbourhood_m")
                             or mark_neighbourhood_m(radius))


def boat_course_progress(fixes: List[Dict[str, Any]], seq: List[Dict[str, Any]],
                         line_a: Tuple[float, float], line_b: Tuple[float, float],
                         course_ref: Optional[Tuple[float, float]], radius_m: float,
                         not_before_ts: Optional[float] = None, min_elapsed_s: float = 0.0,
                         override: Optional[Dict[str, Any]] = None,
                         gate_reach_m: float = rounding.DEFAULT_GATE_REACH_M,
                         as_of_ts: Optional[float] = None) -> Dict[str, Any]:
    """Walk a boat's track through the course marks and report progress.

    A mark counts as rounded, in order, either because the boat passed within its
    rounding radius or because it came within the mark's neighbourhood and has since
    left again towards the next mark — see ``rounded_mark``. ``radius_m`` is the
    fallback from Settings; a mark carrying its own ``radius_m`` uses that instead.
    The last element of ``seq`` is the finish: it is recorded only once
    every earlier mark has been rounded AND the boat then crosses the finish line
    in the finishing direction (reusing detect_finish_crossing for the precise,
    interpolated crossing time). This prevents the mid-course passes of the ODM
    from finishing a boat early.

    ``override`` is the race officer's correction: ``{"idx": n, "t": epoch}``,
    meaning "as of ``t``, this boat is sailing to ``seq[n]``". A wide rounding that
    stays outside the radius otherwise stalls the walk at that mark for the rest of
    the race — the boat shows three marks behind where it is, and because the finish
    is only looked for once every earlier mark is rounded, its GPS finish never
    arrives either. One press of the arrow and both come right.

    It is deliberately **not** retroactive. Applying it from the first fix would
    reach back over the whole track, and the club's finish line mark (O) is commonly
    a mid-course rounding mark too: a boat nudged forward to the finish could then
    be "finished" by a line crossing it made half an hour earlier, on a lap. Taking
    effect from the moment the arrow was pressed also makes the backwards arrow
    work, since the walk resumes from the corrected mark rather than immediately
    re-counting the roundings that are already in the track behind it.
    """
    total = len(seq)
    # A waypoint is walked through like anything else — it has to be, or the distance
    # still to sail would cut the corner it exists to go round — but it is not a mark.
    # The race officer counts marks, so the counts and the "next" reported here skip
    # them, and the boat simply appears to be sailing to the mark beyond.
    mark_idx = [i for i, s in enumerate(seq) if not s.get("via")]
    # Which way across the line counts as finishing: the last mark rounded, not the
    # centroid of the course. See finish_direction_point for why the centroid was
    # wrong and what it cost.
    finish_ref = finish_direction_point(seq, line_a, line_b, course_ref)
    empty = {"rounded": 0, "total": len(mark_idx),
             "next_mark": (seq[mark_idx[0]]["code"] if mark_idx else None),
             "dist_remaining_nm": None, "finished": False, "finish_time": None,
             "finish_lat": None, "finish_lon": None, "direction_skipped_time": None}
    if total == 0:
        return empty
    forced_idx = forced_from = None
    if override and override.get("idx") is not None:
        forced_idx = max(0, min(int(override["idx"]), total - 1))
        forced_from = float(override.get("t") or 0.0)
    if not fixes:
        # No fixes yet, but an override still says where the boat is: report it,
        # rather than a stale "next mark" the race officer has already corrected.
        if forced_idx is None:
            return empty
        return {**empty, "rounded": sum(1 for i in mark_idx if i < forced_idx),
                "next_mark": next((seq[i]["code"] for i in mark_idx if i >= forced_idx), None)}
    idx = 0  # index of the next mark to round; seq[-1] is the finish (line)
    forced = forced_idx is None
    finish = None
    prev = None
    # A line crossing that happened with every mark rounded and was refused *only* by
    # the direction test. That is the exact signature of a wrong finishing direction:
    # the real finish thrown away, and a later crossing — the boat coming back in —
    # recorded instead. It went unnoticed for every race in the database until a race
    # officer walked a replay by eye, so the walk now reports it and run_finish_detection
    # writes it to the activity log. Nothing is decided by it; it is a diagnostic.
    skipped = None
    approach = MarkApproach()
    for f in fixes:
        if not forced and float(f.get("t") or 0.0) >= forced_from:
            # A plain assignment, not a max(): going *back* a mark is as much a
            # correction as going forward, and this is the point the walk resumes
            # from either way. It happens once — held to the override for every
            # later fix would pin the boat there for the rest of the race.
            idx = forced_idx
            forced = True
            approach.reset()
        # Advance through the rounding marks (everything except the final finish).
        # Two tests, either of which counts the mark — see rounded_mark. The radius
        # is measured against the *leg* from the previous fix rather than just this
        # fix, and the closest-approach test carries state across fixes, which is
        # what lets a wide rounding count at all.
        while idx < total - 1 and (
                passed_waypoint(seq[idx - 1] if idx else None, seq[idx], f)
                if seq[idx].get("via")
                else rounded_mark(prev, f, seq, idx, radius_m, approach,
                                  gate_reach_m)):
            idx += 1
            approach.reset()
        # With every earlier mark rounded, the final line crossing is the finish.
        if idx >= total - 1 and prev is not None:
            if not_before_ts is None or f["t"] >= not_before_ts + min_elapsed_s:
                cross = detect_finish_crossing([prev, f], line_a, line_b, finish_ref, None, 0.0)
                if cross:
                    finish = cross
                    break
                if skipped is None and finish_ref is not None:
                    # The same leg with the direction test switched off. Only reached
                    # when the direction test refused, and only until the first one is
                    # found, so it costs nothing in the ordinary case.
                    any_way = detect_finish_crossing([prev, f], line_a, line_b, None, None, 0.0)
                    if any_way:
                        skipped = any_way["t"]
        prev = f
    if not forced and finish is None:
        # Every fix predates the correction, which is the normal case for the first
        # few seconds after the arrow is pressed — and stays the case indefinitely
        # for a boat whose tracker has gone quiet. Honour it anyway: a button that
        # appears to do nothing until the next fix arrives reads as broken. Only the
        # displayed progress moves; a finish is never conjured out of a fix that
        # predates the correction, because that test lives inside the loop.
        idx = forced_idx
    last = fixes[-1]
    # What is ranked is where the boat is *now*, not where it last said it was.
    at = float(as_of_ts) if as_of_ts is not None else time.time()
    ranked_from = dead_reckoned(last, at - float(last.get("t") or at))
    return {
        "rounded": len(mark_idx) if finish else sum(1 for i in mark_idx if i < idx),
        "total": len(mark_idx),
        "next_mark": None if finish else next((seq[i]["code"] for i in mark_idx if i >= idx), None),
        "dist_remaining_nm": 0.0 if finish else round(_distance_remaining_nm(ranked_from, idx, seq), 2),
        "finished": finish is not None,
        "finish_time": finish["t"] if finish else None,
        "finish_lat": finish["lat"] if finish else None,
        "finish_lon": finish["lon"] if finish else None,
        "direction_skipped_time": skipped,
    }


def run_finish_detection(only_devices: Optional[Iterable[str]] = None) -> int:
    """Scan armed, in-progress races for GPS finish crossings.

    For each RACING entry that has an effective tracker and no open proposal,
    look at its fixes since the race start and test for a finish crossing. On a
    hit, either create a pending proposal (RO confirms) or, when the race is
    armed for unmanned finishing, record the finish immediately (gps-auto).
    Returns the number of finishes proposed/recorded. Best-effort; never raises.

    ``only_devices`` narrows it to the trackers that have just reported. The
    relay pushes each fix to /api/track/ingest and detection runs inline on
    receipt, so the horn fires on the fix that crossed the line rather than on
    the next poll — that part is worth keeping. Re-walking *every* boat in every
    armed race on every push was not: a boat cannot finish on a fix belonging to
    another boat, so the other walks re-derived a result nothing had changed,
    while holding a request thread and reading a database that pushes are trying
    to write. The background poller still passes None and sweeps everything.
    """
    wanted = {str(d) for d in only_devices} if only_devices is not None else None
    try:
        from core.races import get_entries, race_first_start_dt
    except Exception:
        return 0
    init_db()
    with get_db() as db:
        races = db.execute(
            "SELECT * FROM races WHERE COALESCE(gps_finish_enabled, 0) = 1"
        ).fetchall()
    if wanted is not None and not wanted:
        return 0
    cfg = track_config()
    hits = 0
    for race in races:
        try:
            entries = get_entries(int(race["id"]))
        except Exception:
            continue
        racing = [e for e in entries if e["status"] == "RACING"]
        if not racing:
            continue
        start_dt = race_first_start_dt(race)
        not_before = start_dt.timestamp() if start_dt else None
        if not_before is None:
            continue
        # Per race, not once for all of them: a race can be sailed to a
        # different finish line. An ISORA passage race finishes on the
        # transit between the Fairway Buoy and the bridge at Plas Heli,
        # which is nowhere near the club line — finishing boats on the
        # wrong one would miss every finish, or record it at the wrong spot.
        # The detection line, not the true one: its seaward end is projected out
        # so a buoy that has swung since it was measured does not cost a finish.
        line = race_finish_line_for_detection(race)
        if line is None:
            continue
        course_ref = _course_ref_point(race)
        seq = course_rounding_sequence(race)
        radius = cfg["rounding_radius_m"]
        gate_reach = cfg["gate_reach_m"]
        auto = bool(race["gps_auto_confirm"]) if "gps_auto_confirm" in race.keys() else False
        for entry in racing:
            uid = effective_tracker_for_entry(entry)
            if not uid:
                continue
            if wanted is not None and uid not in wanted:
                continue
            if detection_blocked_by_proposal(int(race["id"]), int(entry["id"]), entry["finish_time"]):
                continue
            # Same bound as the leaderboard's walk. Every entry here is still
            # RACING, so this is always the backstop -- but a race left open for
            # a fortnight would otherwise have this sweep, which runs every few
            # seconds, reading a fortnight of fixes each time round.
            fixes = positions_for_entry_since(
                entry, not_before, _progress_walk_end(racing, not_before, time.time()))
            # Finish only after the whole course has been sailed (every mark
            # rounded in order) and the final line crossing — not on an early
            # mid-course pass of the ODM.
            #
            # The race officer's next-mark correction has to reach this walk, not
            # just the displayed leaderboard: a boat stalled on a mark the app never
            # saw it round can never satisfy "every earlier mark rounded", so its
            # finish would go on being missed however plainly it crosses the line.
            # That is most of the reason the arrows exist.
            progress = boat_course_progress(fixes, seq, line[0], line[1], course_ref, radius,
                                            not_before, MIN_FINISH_ELAPSED_S,
                                            override=entry_next_mark_override(entry),
                                            gate_reach_m=gate_reach)
            if not progress["finished"]:
                continue
            crossing = {"t": progress["finish_time"], "lat": progress["finish_lat"], "lon": progress["finish_lon"]}
            iso = datetime.fromtimestamp(crossing["t"]).isoformat(timespec="seconds")
            # The finish being recorded is not the first time this boat crossed the
            # line with every mark rounded: an earlier crossing was refused by the
            # direction test alone. Either the boat really did cross the wrong way
            # first, or the finishing direction is wrong — and the second is what
            # silently cost every race in the database 25-46 s until v0.248. Logged
            # here rather than in the walk because the walk runs on every poll for
            # every boat, while this runs once, where the finish is recorded.
            skipped = progress.get("direction_skipped_time")
            if skipped:
                log_activity(
                    "finish direction skipped an earlier crossing",
                    f"race #{int(race['id'])} · {entry['boat_name']} · crossed at "
                    f"{datetime.fromtimestamp(skipped).strftime('%H:%M:%S')} but that was "
                    f"judged as entering the course; finish taken at "
                    f"{datetime.fromtimestamp(crossing['t']).strftime('%H:%M:%S')} "
                    f"({crossing['t'] - skipped:.0f}s later). Check it against the video.")
            if auto:
                record_gps_finish(int(race["id"]), int(entry["id"]), iso, "gps-auto",
                                  fire_horn=cfg["finish_horn"])
                # Record a confirmed proposal too, so the review UI has provenance.
                with get_db() as db:
                    db.execute(
                        "INSERT INTO finish_proposals (race_id, entry_id, detected_time, lat, lon, source, status, created_at) "
                        "VALUES (?, ?, ?, ?, ?, 'gps', 'confirmed', ?)",
                        (int(race["id"]), int(entry["id"]), iso, crossing["lat"], crossing["lon"],
                         datetime.now().isoformat(timespec="seconds")),
                    )
                    db.commit()
            else:
                create_finish_proposal(int(race["id"]), int(entry["id"]), iso, crossing["lat"], crossing["lon"])
            hits += 1
    return hits


# --- estimating where a boat will finish -----------------------------------
#
# A corrected-time board is a *ranking*, so what matters is not the absolute
# accuracy of an estimate but whether the error varies with where a boat happens
# to be on the course. Projecting on distance does exactly that: on a course with
# a 2 nm beat and a 2 nm run, a boat at the windward mark has used most of its
# time for half the distance, and projecting pro-rata puts it ~33% slow — while a
# boat halfway down the run is wrong by a different amount. That differential is
# what scrambles the order.
ESTIMATE_AFTER_S = 600.0     # nothing for the first ten minutes (the YB convention)
VMC_WINDOW_S = 1200.0        # the "recent" window: twenty minutes
# Five was the original, and it is too short. Over the night race a five-minute
# window produced a worst-case finish estimate of 32218 minutes - twenty-two days -
# because a boat momentarily not closing on the course divides by nearly nothing.
# Measured on the same race, worst case by window: 5 min 32218, 10 min 994,
# 20 min 425. Longer also ranks better (order right 63%, 70%, 83%).
MIN_COVERED_NM = 0.25        # below this, "how far have you got" is noise
# An estimate is not credible if it has the boat averaging less than this for the
# rest of the course. A board that occasionally says "three days" is not believed
# the rest of the time either.
#
# This was first written as a multiple of the elapsed time, and that was the wrong
# unit. Early in a long race the true answer IS many times elapsed - ten minutes
# into a seven-hour race the ratio is 42 - so a ceiling of six times elapsed threw
# away every estimate until the race was most of the way through. On the club's
# night race the board stayed blank until 77 minutes when it could have been
# answering from 10, and the estimate it was suppressing at 10 minutes was 6.0
# hours against an actual 6.9. Implied speed is the quantity that actually
# separates a sound early guess from nonsense: the 32218-minute estimate that
# prompted the guard implied about 0.01 kn.
MIN_PLAUSIBLE_SPEED_KN = 0.4
ESTIMATE_STALE_S = 180.0     # a boat not heard from for this long gets no estimate


def race_leg_model(race: Any, twd: Optional[float], tws: Optional[float]) -> List[Dict[str, Any]]:
    """Predicted minutes and distance for each leg of a race's course.

    One polar for the whole fleet is enough here: it only has to get the
    *relative* difficulty of the legs right — that a run is quicker than a beat —
    because each boat's own elapsed time supplies its absolute pace. Returns []
    when there is no wind or no usable polar, and the caller falls back.
    """
    try:
        from core.courses import course_leg_analysis
        from core.polar_io import load_polar, load_sail_chart, resolve_sail_chart_path_for_polar
    except Exception:
        return []
    if twd is None or tws is None:
        return []
    course = appstate.COURSE_BY_NO.get(int(race["course_no"])) if race["course_no"] is not None else None
    if not course:
        return []
    polar_path = None
    try:
        name = race["polar_file"] if "polar_file" in race.keys() else None
        if name:
            candidate = appstate.POLARS_DIR / Path(str(name)).name   # basename only: no traversal
            if candidate.is_file():
                polar_path = candidate
    except Exception:
        polar_path = None
    try:
        rows = load_polar(polar_path) if polar_path else load_polar()
        chart = load_sail_chart(resolve_sail_chart_path_for_polar(polar_path))
        legs = course_leg_analysis(course, float(twd), float(tws), rows, chart)
    except Exception:
        return []
    model = [{"distance_nm": float(l.get("distance_nm") or 0.0),
              "minutes": l.get("leg_minutes")} for l in legs]
    if any(m["minutes"] is None for m in model) or not model:
        return []
    return model


def estimate_elapsed_by_pace(elapsed_s: float, idx: int, dist_next_nm: Optional[float],
                             model: List[Dict[str, Any]]) -> Optional[float]:
    """Project the finish from how the boat is going against the leg model.

    ``elapsed x (predicted total / predicted so far)`` — the pace factor. Because
    the yardstick is predicted *time* rather than distance, it already knows the
    remaining legs may be a different point of sail from the ones sailed.
    """
    if not model or elapsed_s <= 0 or idx < 0:
        return None
    total = sum(m["minutes"] for m in model)
    if total <= 0:
        return None
    done = sum(m["minutes"] for m in model[:idx])
    if idx < len(model):
        leg = model[idx]
        if leg["distance_nm"] > 0 and dist_next_nm is not None:
            frac = 1.0 - (float(dist_next_nm) / leg["distance_nm"])
            done += max(0.0, min(1.0, frac)) * leg["minutes"]
    if done <= 0:
        return None
    return elapsed_s * (total / done)


def _credible_estimate(est_s: Optional[float], elapsed_s: float,
                       remaining_nm: Optional[float]) -> Optional[float]:
    """Drop a projected elapsed that implies the boat has all but stopped."""
    if est_s is None or remaining_nm is None:
        return est_s
    hours_left = (float(est_s) - float(elapsed_s)) / 3600.0
    if hours_left <= 0.0:
        return est_s
    return est_s if float(remaining_nm) / hours_left >= MIN_PLAUSIBLE_SPEED_KN else None


def estimate_elapsed_by_vmc_start(elapsed_s: float, dist_remaining_nm: Optional[float],
                                  course_nm: Optional[float]) -> Optional[float]:
    """Project the finish from the boat's average pace over the whole race so far.

    ``elapsed x (course / covered)``. No wind, no window, no polar: it asks only
    how much of the course this boat has got through and how long that took.

    This is the one to rank a board by, and the reason is not accuracy. Measured
    over the night race of 2026-08-08 its median error was *larger* than a windowed
    VMC (58 minutes against 28) and it still called the finishing order right in 99%
    of snapshots against 63-83%, because its errors are **common-mode**: at 30
    minutes the three boats were out by +153, +162 and +166 minutes, having all
    sailed the same slow first leg. A leaderboard ranks differences, so a shared
    bias cancels and a per-boat one does not.

    What it cannot do is notice a change. A boat that parks, or turns onto a beat
    for the run home, keeps being credited with the pace it has averaged - which is
    what the recent-window estimate is for.
    """
    if (dist_remaining_nm is None or course_nm is None
            or elapsed_s <= 0 or course_nm <= 0):
        return None
    covered = float(course_nm) - float(dist_remaining_nm)
    if covered <= MIN_COVERED_NM:
        return None                       # too little of the course to divide by
    return elapsed_s * float(course_nm) / covered


def estimate_elapsed_by_vmc(elapsed_s: float, dist_remaining_nm: Optional[float],
                            earlier_remaining_nm: Optional[float],
                            window_s: float) -> Optional[float]:
    """Project the finish from how fast the boat is closing on it.

    VMC here is the rate the *course remaining* is shrinking — not instantaneous
    speed towards the next mark, which at ten-second reporting is far too noisy
    and reads differently for a boat on a lay line than one at the mark. Over a
    five-minute window it smooths through tacks and gybes on its own.
    """
    if (dist_remaining_nm is None or earlier_remaining_nm is None
            or window_s <= 0 or elapsed_s <= 0):
        return None
    closed_nm = float(earlier_remaining_nm) - float(dist_remaining_nm)
    if closed_nm <= 0:
        return None                       # stopped, or going the wrong way
    vmc_kn = closed_nm / (window_s / 3600.0)
    if vmc_kn < 0.2:
        return None
    return elapsed_s + (float(dist_remaining_nm) / vmc_kn) * 3600.0


def boat_progress_series(fixes: List[Dict[str, Any]], seq: List[Dict[str, Any]],
                         line_a: Tuple[float, float], line_b: Tuple[float, float],
                         course_ref: Optional[Tuple[float, float]], radius_m: float,
                         sample_times: List[float],
                         not_before_ts: Optional[float] = None,
                         min_elapsed_s: float = 0.0,
                         override: Optional[Dict[str, Any]] = None,
                         gate_reach_m: float = rounding.DEFAULT_GATE_REACH_M) -> List[Dict[str, Any]]:
    """Course progress at each of ``sample_times``, from a single pass of the track.

    The replay needs the fleet's order at every moment of the race. Asking the
    server per frame lags visibly, and recomputing progress per sample would be
    O(samples x fixes). Walking the fixes once and snapshotting as the clock
    passes each sample is the same cost as one leaderboard, whatever the sample
    count — and keeps the mark-rounding and finish logic here rather than
    reimplemented in the browser where the two could disagree.

    ``boat_progress_series(...)[-1]`` for a sample at or after the last fix is
    the same answer as ``boat_course_progress``; a test pins that. ``override`` is
    the race officer's next-mark correction and behaves the same way here, so a
    replay shows the race as the race officer saw it rather than with the boat
    stalled on a mark it was corrected past.
    """
    total = len(seq)
    finish_ref = finish_direction_point(seq, line_a, line_b, course_ref)
    out: List[Dict[str, Any]] = []
    idx, prev, finish, last_fix = 0, None, None, None
    approach = MarkApproach()
    si = 0
    forced_idx = None
    if override and override.get("idx") is not None and total:
        forced_idx = max(0, min(int(override["idx"]), total - 1))
    forced = forced_idx is None
    forced_from = float((override or {}).get("t") or 0.0)

    def snapshot(at_ts: Optional[float] = None) -> Dict[str, Any]:
        remaining = None
        next_leg = None
        if finish:
            remaining = 0.0
            next_leg = 0.0
        elif last_fix is not None and seq:
            # Carried forward to the moment being shown, so boats reporting at
            # 2 s and at 61 s are compared at the same instant rather than 136 s
            # apart. See dead_reckoned.
            at = at_ts if at_ts is not None else last_fix["t"]
            ranked_from = dead_reckoned(last_fix, float(at) - float(last_fix["t"]))
            remaining = round(_distance_remaining_nm(ranked_from, idx, seq), 2)
            if idx < len(seq):
                next_leg = haversine_nm(last_fix["lat"], last_fix["lon"],
                                        seq[idx]["lat"], seq[idx]["lon"])
        return {
            "dist_next_nm": next_leg,
            "rounded": total if finish else idx,
            "total": total,
            "next_mark": None if finish else (seq[idx]["code"] if idx < total else None),
            "dist_remaining_nm": remaining,
            "finished": finish is not None,
            "finish_time": finish["t"] if finish else None,
            "lat": last_fix["lat"] if last_fix else None,
            "lon": last_fix["lon"] if last_fix else None,
            "sog": last_fix.get("speed_kn") if last_fix else None,
            "cog": last_fix.get("course_deg") if last_fix else None,
            "t": last_fix["t"] if last_fix else None,
        }

    for f in fixes:
        # Emit every sample the clock has passed before taking this fix in.
        while si < len(sample_times) and sample_times[si] < f["t"]:
            out.append(snapshot(sample_times[si]))
            si += 1
        if finish is None:
            if not forced and float(f.get("t") or 0.0) >= forced_from:
                idx = forced_idx           # see boat_course_progress: once, not a max()
                forced = True
                approach.reset()
            while idx < total - 1 and rounded_mark(prev, f, seq, idx, radius_m,
                                                  approach, gate_reach_m):
                idx += 1
                approach.reset()
            if idx >= total - 1 and prev is not None:
                if not_before_ts is None or f["t"] >= not_before_ts + min_elapsed_s:
                    cross = detect_finish_crossing([prev, f], line_a, line_b, finish_ref, None, 0.0)
                    if cross:
                        finish = cross
            prev = f
        last_fix = f
    while si < len(sample_times):        # samples after the last fix
        out.append(snapshot(sample_times[si]))
        si += 1
    return out


def leaderboard_sort_key(r: Dict[str, Any]):
    """Order on the water: finishers by finish time, then by progress round the course."""
    return (
        0 if r["finished"] else 1,
        r["finish_epoch"] if (r["finished"] and r["finish_epoch"] is not None) else 0.0,
        0 if r.get("tracked") else 1,   # untracked boats after the tracked fleet
        -(r["rounded"] or 0),
        r["dist_remaining_nm"] if r["dist_remaining_nm"] is not None else 9e9,
    )


def order_on_the_water(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Sort a set of leaderboard rows and stamp each with its position."""
    rows.sort(key=leaderboard_sort_key)
    for i, r in enumerate(rows, start=1):
        r["position"] = i
    return rows


def entry_next_mark_override(entry: Any) -> Optional[Dict[str, Any]]:
    """The race officer's next-mark correction for this entry, if there is one.

    Tolerant of a row that has neither column: the leaderboard runs against rows
    from several queries, and an older database gets the columns added on the next
    start rather than being required to have them now.
    """
    try:
        idx = entry["next_mark_override_idx"]
        at = entry["next_mark_override_at"]
    except (KeyError, IndexError, TypeError):
        return None
    if idx is None:
        return None
    return {"idx": int(idx), "t": float(at or 0.0)}


def set_next_mark_override(race_id: int, entry_id: int, idx: Optional[int],
                           when_ts: Optional[float] = None) -> None:
    """Record (or with ``idx=None`` clear) the mark an entry is sailing to."""
    from core.db import get_db
    with get_db() as db:
        db.execute(
            "UPDATE entries SET next_mark_override_idx = ?, next_mark_override_at = ?"
            " WHERE id = ? AND race_id = ?",
            (None if idx is None else int(idx),
             None if idx is None else float(when_ts if when_ts is not None else time.time()),
             int(entry_id), int(race_id)),
        )
        db.commit()


# How long after the gun a race can still be accumulating progress. Only reached
# by a race nobody closed: a race whose boats have all finished stops at its own
# last finish (see _progress_walk_end), which is the normal case and much
# tighter. This is the backstop for one left open with boats still marked RACING.
#
# Three days, not the replay reply's twelve hours. That constant caps the SIZE of
# one HTTP response and can be as mean as it likes; this one decides whether a
# boat still shows progress and whether its GPS finish is ever detected, and the
# club sails ISORA passage races -- Pwllheli to Ireland finishes on the transit
# at Plas Heli, and takes the best part of a day or more. Cutting a passage race
# off at twelve hours would freeze the fleet mid-Irish-Sea and quietly stop
# detecting finishes, which is far worse than walking some extra fixes.
PROGRESS_MAX_WINDOW_S = 72 * 3600.0


def _progress_walk_end(entries: Any, not_before: Optional[float],
                       now: float) -> Optional[float]:
    """When the progress walk should stop reading fixes.

    It used to stop nowhere. The fixes ran from the gun to *now*, so a race
    sailed in July was still walking every fix its boats had recorded since --
    one finished race was reading 98,362 of them, 81,849 for a single boat, and
    gaining another day's worth every day it sat in the database. A race sheet
    that gets slower every week, for a reason nobody can see.

    A race whose boats have all finished ended when the last of them crossed:
    everything after that is the next race, or the sail home. An hour of margin
    is kept past it so a late correction to a finish time is still inside the
    window it is walked in.

    A race with boats still marked RACING has to be walked to now -- they are
    out there and their finishes have not happened yet -- bounded only by the
    backstop, for one left open and forgotten.
    """
    if not_before is None:
        return None
    latest_finish = None
    for e in entries:
        try:
            status = str(e["status"] or "").upper()
        except (KeyError, IndexError, TypeError):
            status = ""
        if status == "RACING":
            return min(now, not_before + PROGRESS_MAX_WINDOW_S)
        raw_finish = None
        try:
            raw_finish = e["finish_time"]
        except (KeyError, IndexError, TypeError):
            raw_finish = None
        if raw_finish:
            try:
                ts = datetime.fromisoformat(str(raw_finish)).timestamp()
            except ValueError:
                continue
            if latest_finish is None or ts > latest_finish:
                latest_finish = ts
    if latest_finish is None:
        # Nobody racing and nobody finished -- abandoned, or all DNF/DNC. There
        # is no finish to reach towards, so the backstop is the whole answer.
        return min(now, not_before + PROGRESS_MAX_WINDOW_S)
    return min(now, max(latest_finish + 3600.0, not_before), not_before + PROGRESS_MAX_WINDOW_S)


def race_leaderboard(race_id: int, at_ts: Optional[float] = None) -> List[Dict[str, Any]]:
    """Return every entry's position-on-the-water for a race, leader first.

    **All** boats entered in the race are listed; a boat with no tracker to look
    for has ``tracked: False`` and no position/progress values, and sorts after
    the tracked boats.

    Every row also carries ``tracker_assigned``: whether the boat has a tracker
    on it **now**, as against ``tracked``, which stays true for one it merely
    used to have so that an old race keeps its track. The pages tell their two
    empty rows apart on that -- "No tracker" against "Not reporting".

    Ordered: finished boats (by finish time) first, then tracked boats by marks
    rounded (more = ahead) and then distance remaining (less = ahead). Progress is
    recomputed from each boat's stored track. Note this is order **on the water**,
    not corrected for handicap.

    ``at_ts`` rewinds the clock: the fleet exactly as it stood at that moment,
    for replaying a race. Progress is a pure function of the fixes up to then, so
    no part of the walk-the-course logic changes — but a boat that finished later
    must not already read FINISHED, and "last fix age" has to be measured from
    ``at_ts`` rather than from now, or every boat looks hours stale.
    """
    try:
        from core.races import get_race, get_entries, race_first_start_dt
    except Exception:
        return []
    race = get_race(race_id)
    # After the race, so the line is the one this race was sailed to: O is a laid
    # mark and gets re-measured like any other. Extended at the seaward end, because
    # this drives the same walk that decides a finish.
    line = race_finish_line_for_detection(race)
    if not race or line is None:
        return []
    entries = get_entries(race_id)
    start_dt = race_first_start_dt(race)
    not_before = start_dt.timestamp() if start_dt else None
    course_ref = _course_ref_point(race)
    seq = course_rounding_sequence(race)
    _cfg = track_config()
    radius, gate_reach = _cfg["rounding_radius_m"], _cfg["gate_reach_m"]
    now = time.time()
    walk_until = at_ts if at_ts is not None else _progress_walk_end(entries, not_before, now)
    rows: List[Dict[str, Any]] = []
    for e in entries:
        uid = effective_tracker_for_entry(e)
        if not uid:
            # Entered but not tracked: list it so the fleet is complete, with no
            # position or progress figures for the UI to dash out.
            finish_epoch = None
            if e["finish_time"]:
                try:
                    finish_epoch = datetime.fromisoformat(str(e["finish_time"])).timestamp()
                except ValueError:
                    finish_epoch = None
            untracked_finished = e["status"] == "FINISHED"
            if at_ts is not None:      # rewound: only finished if it had finished by then
                untracked_finished = finish_epoch is not None and finish_epoch <= at_ts
                if not untracked_finished:
                    finish_epoch = None
            rows.append({
                "entry_id": int(e["id"]), "boat_name": e["boat_name"], "sail_no": e["sail_no"],
                "status": e["status"], "tracked": False, "tracker_assigned": False,
                "finished": untracked_finished, "finish_epoch": finish_epoch,
                "rounded": None, "total": len(seq), "next_mark": None,
                "dist_remaining_nm": None, "lat": None, "lon": None,
                "cog": None, "sog": None, "age": None, "stale": False,
            })
            continue
        fixes = positions_for_entry_since(e, not_before, walk_until) if not_before is not None else []
        override = entry_next_mark_override(e)
        # Replaying a race, a correction made after the moment being replayed has
        # not happened yet, so the fleet is shown as it actually stood.
        if override and at_ts is not None and override["t"] > at_ts:
            override = None
        prog = boat_course_progress(fixes, seq, line[0], line[1], course_ref, radius,
                                    not_before, MIN_FINISH_ELAPSED_S, override=override,
                                    gate_reach_m=gate_reach,
                                    # Rewound or live, every boat is ranked as at the
                                    # same instant rather than whenever each last spoke.
                                    as_of_ts=at_ts if at_ts is not None else now)
        # Display position: the latest known fix, even if it predates the race
        # start — otherwise a boat vanishes from the map before/just after the gun.
        # Replaying, the last fix at/before at_ts is the whole point, so the
        # "ignore the race clock" lookup (which reads *now*) must not be used.
        #
        # But only while that fix still means something. Reaching outside the
        # race window is for a boat that reported a few minutes before the gun,
        # and it had no bound: a boat whose tracker was taken off it weeks ago
        # still resolves through last_tracker_seen_for_boat, so a race with no
        # trackers assigned at all listed a boat at a month-old position, with a
        # distance-to-go worked out from it, ranked first on the water. An hour
        # is the app's own definition of a tracker that is not reporting — the
        # red dot on the Trackers page — so it is the line here too. Past that,
        # fall back to the last fix inside the race itself, which is what an old
        # race wants and is empty for a race that has not started.
        if at_ts is None:
            recent = latest_position_for_entry(e)
            if recent and (now - float(recent.get("t") or 0)) > TRACK_RAG_AMBER_S:
                recent = None
            lp = recent or (fixes[-1] if fixes else None)
        else:
            lp = fixes[-1] if fixes else latest_position_before(e, at_ts)
        # Distance-to-go is still useful before any post-start fix arrives.
        dist_remaining = prog["dist_remaining_nm"]
        if dist_remaining is None and lp and seq and not prog["finished"]:
            dist_remaining = round(_distance_remaining_nm(lp, prog["rounded"], seq), 2)
        # Finish epoch for ordering: the GPS crossing, else the recorded finish time.
        finish_epoch = prog["finish_time"]
        if finish_epoch is None and e["finish_time"]:
            try:
                finish_epoch = datetime.fromisoformat(str(e["finish_time"])).timestamp()
            except ValueError:
                finish_epoch = None
        finished = prog["finished"] or e["status"] == "FINISHED"
        if at_ts is not None:
            # Rewound: a boat is only finished if it had actually finished by then.
            finished = finish_epoch is not None and finish_epoch <= at_ts
            if not finished:
                finish_epoch = None
        clock = now if at_ts is None else at_ts
        age = round(max(0.0, clock - lp["t"]), 1) if lp and lp.get("t") else None
        rows.append({
            "entry_id": int(e["id"]), "boat_name": e["boat_name"], "sail_no": e["sail_no"],
            "status": e["status"], "tracked": True,
            "tracker_assigned": bool(tracker_assigned_for_entry(e)),
            "finished": finished, "finish_epoch": finish_epoch,
            "rounded": prog["rounded"], "total": prog["total"], "next_mark": prog["next_mark"],
            # So the race officer's arrows know which ends they are at, and can show
            # that this boat's progress has been corrected by hand.
            "next_mark_adjusted": override is not None,
            "dist_remaining_nm": dist_remaining,
            "lat": lp["lat"] if lp else None, "lon": lp["lon"] if lp else None,
            "cog": lp["course_deg"] if lp else None,
            "sog": lp["speed_kn"] if lp else None,
            "age": age, "stale": (age is not None and age > 60),
        })

    return order_on_the_water(rows)


# ---------------------------------------------------------------------------
# Recorded tracks for replay
# ---------------------------------------------------------------------------
REPLAY_PRE_START_S = 300.0      # from the warning signal, so the start is watchable
REPLAY_POST_FINISH_S = 120.0    # a little after the last boat, so nothing cuts off dead

# The two bounds on a replay. Neither is a tuning knob: they are the line between
# "a long race" and "the data is wrong", and a request past that line is answered
# with a sane replay rather than with the whole of whatever the database holds.
#
# The window bounds TIME. A finish once landed 352 days after its race's gun (a
# mis-stamped manual finish), and the window is drawn from the gun to the last
# finish, so a single bad timestamp asked for a year-long replay. The cost is not
# in the fixes, which is the obvious guess: the order-on-the-water board is
# snapshotted every REPLAY_BOARD_STEP_S across the window whether any boat
# reported or not, at roughly 300 bytes per boat per snapshot for the progress
# series and another 170 for the row that goes out. That is ~1 MB per hour per
# three boats before a single fix is counted, and it is what turned one request
# into gigabytes. The longest race the club has sailed is 2.7 hours.
REPLAY_MAX_WINDOW_S = 12 * 3600.0

# The budget bounds RATE, which the window cannot see. Trackers report every 5 s
# today (the busiest observed real unit: 1,768 fixes in its busiest hour), but
# that is a device setting, not a law -- one reconfigured to 1 Hz would put five
# times as much through the same window. Fixes are thinned evenly to fit, each
# boat always keeping its first and last, so a track still starts and ends where
# it should. At the rates the club actually runs this never bites: a 2.7-hour
# race at 5 s is ~1,900 fixes a boat. When it does, the replay runs at 6x life,
# so 5 s becoming 6 s is not visible.
REPLAY_MAX_FIXES = 60000            # across the whole fleet, per response
REPLAY_MIN_FIXES_PER_BOAT = 1000    # however large the fleet gets


def race_track_window(race: Any) -> Optional[Tuple[float, float]]:
    """The (start, end) epoch window a race's replay should cover.

    Starts at the **warning signal** rather than the gun — the approach to the
    line is the interesting part — and ends a couple of minutes after the last
    boat finished, or now for a race still going.

    Bounded at ``REPLAY_MAX_WINDOW_S``. The end is taken from a finish time, and
    a finish time can be wrong; the window is what every other part of the reply
    is sized from, so it is the one place worth refusing an absurd answer.
    """
    try:
        from core.races import get_entries, race_first_start_dt
    except Exception:
        return None
    start_dt = race_first_start_dt(race)
    if not start_dt:
        return None
    start_ts = start_dt.timestamp() - REPLAY_PRE_START_S
    now = time.time()
    entries = get_entries(int(race["id"]))
    last_finish = None
    for e in entries:
        if not e["finish_time"]:
            continue
        try:
            ts = datetime.fromisoformat(str(e["finish_time"])).timestamp()
        except ValueError:
            continue
        last_finish = ts if last_finish is None else max(last_finish, ts)
    # "The last boat" means every boat has stopped racing — not "the latest of the
    # ones that have finished so far". Taking the max over finishers alone closed
    # the window two minutes after the FIRST finish, while the rest of the fleet
    # was still out there: the chart froze, and because the viewer's `since` then
    # ran past the end, every poll fell back to re-sending the whole race. Finishes
    # kept being detected the while, since that walk has no upper bound — which is
    # why this showed up as "the chart stopped but the second boat still finished",
    # and why every track was there the next morning, once the fleet was in.
    still_racing = any(e["status"] == "RACING" for e in entries)
    if last_finish is not None and not still_racing:
        end_ts = last_finish + REPLAY_POST_FINISH_S
    else:
        # No finishes recorded: end at the last fix anyone reported rather than
        # at "now". For a race in progress those are the same thing; for one that
        # stopped reporting hours ago, running to now leaves a long dead tail
        # where the boats sit still and their trails age out of view.
        last_fix = None
        for e in entries:
            fixes = positions_for_entry_since(e, start_ts) if effective_tracker_for_entry(e) else []
            if fixes:
                last_fix = fixes[-1]["t"] if last_fix is None else max(last_fix, fixes[-1]["t"])
        end_ts = last_fix if last_fix is not None else now
    # Never past now, and never longer than a race can be. Clamping here rather
    # than at the caller means the fixes, the board series and the wind series
    # are all drawn from the same bounded stretch and cannot disagree about how
    # long the race was.
    end_ts = min(end_ts, now, start_ts + REPLAY_MAX_WINDOW_S)
    return (start_ts, max(end_ts, start_ts))


REPLAY_BOARD_STEP_S = 5.0        # how often the replay's order is snapshotted.
# The viewer shows the latest snapshot at or before the clock, so this is also the
# worst the order can trail the chart. At 5 s a 20-knot boat has moved ~50 m — about
# a boat length on screen — and the whole series still builds in a fraction of a second.


# One wind reading every half minute is plenty for a dial a viewer is watching,
# and a seven-hour race sampled every few seconds would otherwise be thousands of
# points riding along with the track.
WIND_SERIES_STEP_S = 30.0


def race_wind_series(start_ts: float, end_ts: float) -> List[List[Any]]:
    """The hut wind through a race, as ``[t, twd, tws, gust]`` rows.

    Sent with the track so the chart's wind gauge can show the wind at whatever
    moment the viewer has scrubbed to, with no request per frame — the same
    reasoning as the order-on-the-water series beside it.

    This is the wind **at the hut**, which is not the wind where the boats are
    once a course rounds a headland. It is what the club measures, and the gauge
    says whose wind it is rather than implying otherwise.
    """
    try:
        from core.weather_store import weather_samples_between
        samples = weather_samples_between(float(start_ts), float(end_ts))
    except Exception:
        return []
    out: List[List[Any]] = []
    last_t = None
    for sample in samples:
        t = sample.get("t")
        if t is None:
            continue
        if last_t is not None and float(t) - last_t < WIND_SERIES_STEP_S:
            continue
        last_t = float(t)
        out.append([round(float(t), 1),
                    None if sample.get("twd") is None else round(float(sample["twd"]), 1),
                    None if sample.get("tws") is None else round(float(sample["tws"]), 1),
                    None if sample.get("gust") is None else round(float(sample["gust"]), 1)])
    return out


def race_leaderboard_series(race_id: int, start_ts: float, end_ts: float,
                            step_s: float = REPLAY_BOARD_STEP_S) -> Dict[str, Any]:
    """The order on the water at every step across a race, for the replay.

    Sent with the track so the viewer has the whole race in hand and can show the
    order for whatever moment is on screen with no request and no lag. The walk
    and the ordering are the same code the live view uses.
    """
    try:
        from core.races import get_race, get_entries, race_first_start_dt
    except Exception:
        return {"step": step_s, "total": 0, "times": [], "rows": []}
    race = get_race(race_id)
    line = race_finish_line_for_detection(race)
    if not race or line is None or end_ts <= start_ts:
        return {"step": step_s, "total": 0, "times": [], "rows": []}
    times = [start_ts + i * step_s for i in range(int((end_ts - start_ts) / step_s) + 1)]
    if times and times[-1] < end_ts:
        times.append(end_ts)
    start_dt = race_first_start_dt(race)
    not_before = start_dt.timestamp() if start_dt else None
    seq = course_rounding_sequence(race)
    _cfg = track_config()
    radius, gate_reach = _cfg["rounding_radius_m"], _cfg["gate_reach_m"]
    course_ref = _course_ref_point(race)
    # The wind the estimates are made in. The average over the race is the fairer
    # yardstick once it is over; while it is on, the wind now is all there is.
    twd = tws = None
    try:
        from core.weather_store import latest_weather_sample
        sample = latest_weather_sample()
        if sample:
            twd, tws = sample.get("twd"), sample.get("tws")
    except Exception:
        pass
    model = race_leg_model(race, twd, tws)
    per_entry: Dict[int, List[Dict[str, Any]]] = {}
    meta: Dict[int, Dict[str, Any]] = {}
    for e in get_entries(race_id):
        eid = int(e["id"])
        uid = effective_tracker_for_entry(e)
        recorded_finish = None
        if e["finish_time"]:
            try:
                recorded_finish = datetime.fromisoformat(str(e["finish_time"])).timestamp()
            except ValueError:
                recorded_finish = None
        meta[eid] = {"tracked": bool(uid), "recorded_finish": recorded_finish}
        fixes = (positions_for_entry_since(e, not_before, end_ts)
                 if (uid and not_before is not None) else [])
        per_entry[eid] = boat_progress_series(fixes, seq, line[0], line[1], course_ref, radius,
                                              times, not_before, MIN_FINISH_ELAPSED_S,
                                              override=entry_next_mark_override(e),
                                              gate_reach_m=gate_reach)
    # How many snapshots back the VMC window reaches.
    back = max(1, int(round(VMC_WINDOW_S / step_s))) if step_s > 0 else 1
    # The course each boat has to sail, taken as what it had still to go at the
    # first snapshot it was seen at. Per boat, because a boat whose tracker wakes
    # up late must not be credited with covering the part it was never seen on.
    course_nm: Dict[int, float] = {}
    for eid, series in per_entry.items():
        for snap in series:
            if snap and snap.get("dist_remaining_nm") is not None:
                course_nm[eid] = float(snap["dist_remaining_nm"])
                break
    rows: List[List[List[Any]]] = []
    for i, t in enumerate(times):
        board = []
        for eid, series in per_entry.items():
            m = meta[eid]
            s = series[i] if i < len(series) else None
            rec = m["recorded_finish"]
            finished = bool(s and s["finished"]) or (rec is not None and rec <= t)
            est_pace = est_start = est_recent = None
            if m["tracked"] and s and not_before is not None:
                elapsed = t - not_before
                finish_at = s["finish_time"] if s["finished"] else (rec if finished else None)
                if finished and finish_at is not None:
                    # Actually finished: use the real time, not a projection, so
                    # the board turns real boat by boat rather than all at once.
                    est_pace = est_start = est_recent = finish_at - not_before
                elif elapsed >= ESTIMATE_AFTER_S and s["t"] is not None and (t - s["t"]) <= ESTIMATE_STALE_S:
                    est_pace = estimate_elapsed_by_pace(elapsed, s["rounded"], s["dist_next_nm"], model)
                    est_start = estimate_elapsed_by_vmc_start(
                        elapsed, s["dist_remaining_nm"], course_nm.get(eid))
                    earlier = series[i - back] if i - back >= 0 else None
                    est_recent = estimate_elapsed_by_vmc(
                        elapsed, s["dist_remaining_nm"],
                        earlier["dist_remaining_nm"] if earlier else None,
                        t - times[i - back] if i - back >= 0 else 0.0)
                    # Drop an estimate that has the boat crawling the rest of the
                    # course; keep one that is merely a long way off, because early
                    # in a long race that is the right answer.
                    left_nm = s["dist_remaining_nm"]
                    est_pace = _credible_estimate(est_pace, elapsed, left_nm)
                    est_start = _credible_estimate(est_start, elapsed, left_nm)
                    est_recent = _credible_estimate(est_recent, elapsed, left_nm)
            board.append({
                "entry_id": eid, "tracked": m["tracked"], "finished": finished,
                "finish_epoch": (s["finish_time"] if (s and s["finished"]) else (rec if finished else None)),
                "rounded": (s["rounded"] if (s and m["tracked"]) else None),
                "total": (s["total"] if s else 0),
                "next_mark": (s["next_mark"] if (s and m["tracked"]) else None),
                "dist_remaining_nm": (s["dist_remaining_nm"] if (s and m["tracked"]) else None),
                "sog": (s["sog"] if (s and m["tracked"]) else None),
                "est_pace": est_pace, "est_start": est_start, "est_recent": est_recent,
            })
        order_on_the_water(board)
        rows.append([[b["entry_id"], b["position"], b["rounded"], b["next_mark"],
                      b["dist_remaining_nm"], 1 if b["finished"] else 0, b["sog"],
                      None if b["est_pace"] is None else round(b["est_pace"], 1),
                      None if b["est_start"] is None else round(b["est_start"], 1),
                      None if b["est_recent"] is None else round(b["est_recent"], 1)]
                     for b in board])
    # Not rounded: rounding the last one to a tenth can push it past end_ts, and a
    # snapshot claiming to be beyond the window it covers is a small lie the
    # viewer would then have to reason about.
    return {"step": step_s, "total": len(seq), "times": times, "rows": rows,
            "estimate_after_s": ESTIMATE_AFTER_S, "vmc_window_s": VMC_WINDOW_S,
            "polar_available": bool(model)}


def entry_rating_factors(race: Any, entries: List[Any]) -> Dict[int, Dict[str, Any]]:
    """Per-entry multipliers turning an elapsed time into a corrected one.

    The *factor* is computed here rather than the formula being repeated in the
    browser: corrected time is ``elapsed x TCC`` for IRC and ``elapsed x 1000 /
    YTC`` for YTC, and a second copy of that in JavaScript could drift from the
    one the published results use. The viewer only ever multiplies.

    Ratings come from the race entry's own snapshot, via the same resolver the
    results tables use, so a live board and the final result cannot disagree.
    """
    out: Dict[int, Dict[str, Any]] = {}
    try:
        boats_by_id = get_boats_by_id(e["boat_id"] for e in entries)
    except Exception:
        boats_by_id = {}
    try:
        labels = entry_class_labels_map(race_class_config(race), entries)
    except Exception:
        labels = {}
    for e in entries:
        eid = int(e["id"])
        boat = boats_by_id.get(e["boat_id"]) if e["boat_id"] is not None else None
        info: Dict[str, Any] = {"irc_factor": None, "ytc_factor": None,
                                "class_label": labels.get(eid) or ""}
        for kind, key in (("IRC", "irc_factor"), ("YTC", "ytc_factor")):
            try:
                rating, _src = rating_from_entry_for_result(e, boat, kind)
            except Exception:
                rating = None
            if rating:
                # The factor is simply what one second of elapsed corrects to.
                factor = corrected_seconds_for_result(1.0, rating, kind)
                info[key] = round(factor, 6) if factor else None
        out[eid] = info
    return out


def thin_fixes(rows: List[Any], keep: int) -> List[Any]:
    """At most ``keep`` fixes, spread evenly, with both ends kept.

    Evenly rather than "the first N": a truncated track stops in the middle of
    the bay and reads as a boat that retired. Both ends rather than a plain
    stride, because the last fix is where the boat finished and dropping it
    moves the end of the track.
    """
    total = len(rows)
    if keep <= 0 or total <= keep:
        return rows
    if keep == 1:
        return [rows[-1]]
    span = (total - 1) / float(keep - 1)
    return [rows[int(round(i * span))] for i in range(keep)]


def race_track_history(race_id: int, since_ts: Optional[float] = None) -> Dict[str, Any]:
    """Every tracked boat's recorded fixes for a race, for the replay viewer.

    Fixes go out as compact ``[t, lat, lon, sog, cog]`` arrays rather than objects
    — a two-hour race for a dozen boats is a few thousand fixes, and the key names
    would be most of the payload.

    ``since_ts`` returns only what is new. The viewer keeps itself up to date on a
    race still being sailed, and re-sending an entire afternoon's tracks every ten
    seconds would be wasteful — especially to competitors watching on mobile data.
    The reply is marked ``incremental`` so the viewer knows to append rather than
    replace. Progress is cumulative, so the order series is still walked from the
    start of the race; only the new snapshots are sent.

    Two bounds keep the reply a reply and not a database dump — the window
    (``REPLAY_MAX_WINDOW_S``, applied in ``race_track_window``) and the fix budget
    (``REPLAY_MAX_FIXES``, applied here). Neither can change a race result: this
    function is read by the replay viewer and nothing else. Finish detection walks
    ``positions_for_entry_since`` directly and is not bounded by either.
    """
    try:
        from core.races import get_race, get_entries
    except Exception:
        return {"start": None, "end": None, "first_start": None, "boats": [], "incremental": False}
    race = get_race(race_id)
    window = race_track_window(race) if race else None
    if not race or window is None:
        return {"start": None, "end": None, "first_start": None, "boats": [], "incremental": False}
    start_ts, end_ts = window
    # An old or nonsensical `since` falls back to the whole race rather than
    # silently handing back a hole the viewer would never fill.
    incremental = since_ts is not None and start_ts <= since_ts <= end_ts
    from_ts = float(since_ts) if incremental else start_ts
    ratings = entry_rating_factors(race, get_entries(race_id))
    entries = get_entries(race_id)
    tracked = sum(1 for e in entries if effective_tracker_for_entry(e))
    # The budget is shared out over the boats that actually have a track, so a
    # fleet of two is not held to a nine-boat share.
    per_boat = max(REPLAY_MIN_FIXES_PER_BOAT, REPLAY_MAX_FIXES // max(1, tracked))
    boats: List[Dict[str, Any]] = []
    dropped = 0
    for e in entries:
        uid = effective_tracker_for_entry(e)
        fixes = positions_for_entry_since(e, from_ts, end_ts) if uid else []
        rows = [[round(f["t"], 1), f["lat"], f["lon"],
                 f.get("speed_kn"), f.get("course_deg")] for f in fixes]
        if incremental:
            # Filter on the *published* (rounded) time, which is what the caller
            # sent back. Comparing against the unrounded fix time re-sends the
            # boundary fix on every poll, and those duplicates accumulate.
            rows = [r for r in rows if r[0] > from_ts]
        recorded = len(rows)
        rows = thin_fixes(rows, per_boat)
        dropped += recorded - len(rows)
        boats.append({
            "entry_id": int(e["id"]),
            "boat_name": e["boat_name"],
            "sail_no": e["sail_no"],
            "tracked": bool(uid),
            "fixes": rows,
            "recorded_fixes": recorded,
            **ratings.get(int(e["id"]), {}),
        })
    # The order on the water at every step, so the viewer never has to ask again.
    boards = race_leaderboard_series(race_id, start_ts, end_ts)
    if incremental:
        keep = [i for i, bt in enumerate(boards["times"]) if bt > from_ts]
        boards = {**boards,
                  "times": [boards["times"][i] for i in keep],
                  "rows": [boards["rows"][i] for i in keep]}
    # The gun, so the replay can count elapsed time the way a sailor does. The
    # window opens at the warning signal, five minutes earlier, and counting from
    # there made the clock read 15:00 when the race was ten minutes old - which is
    # exactly when the board says it starts estimating, so it looked broken.
    try:
        from core.races import race_first_start_dt
        gun = race_first_start_dt(race)
        first_start = gun.timestamp() if gun else None
    except Exception:
        first_start = None
    # A cap nobody is told about reads as "this is the whole track". Each boat
    # carries what was recorded beside what was sent, and the reply says plainly
    # whether anything was left out.
    return {"start": start_ts, "end": end_ts, "first_start": first_start,
            "boats": boats, "boards": boards, "wind": race_wind_series(start_ts, end_ts),
            "incremental": incremental,
            "thinned": dropped > 0, "fixes_dropped": dropped,
            "max_window_s": REPLAY_MAX_WINDOW_S}


# ---------------------------------------------------------------------------
# Background poller + status (mirrors core.power / core.weather_store)
# ---------------------------------------------------------------------------
TRACK_MONITOR_STATE: Dict[str, Any] = {
    "started": False,
    "last_status": {"ok": False, "message": "GPS tracking has not started yet.", "devices": []},
    "last_poll_at": None,
}
TRACK_MONITOR_LOCK = threading.Lock()


def _boat_labels_by_unique_id() -> Dict[str, str]:
    """Map each assigned tracker's unique_id to a boat label for the status UI."""
    try:
        with get_db() as db:
            rows = db.execute(
                "SELECT t.unique_id AS uid, b.boat_name AS boat_name, t.label AS label "
                "FROM trackers t LEFT JOIN boats b ON b.id = t.boat_id"
            ).fetchall()
    except sqlite3.OperationalError:
        return {}
    out: Dict[str, str] = {}
    for r in rows:
        out[r["uid"]] = r["boat_name"] or r["label"] or r["uid"]
    return out


def _build_status(positions: List[Dict[str, Any]], error: Optional[str], source: str, cfg: Dict[str, Any]) -> Dict[str, Any]:
    now = time.time()
    labels = _boat_labels_by_unique_id()
    devices = []
    for p in sorted(positions, key=lambda x: x.get("unique_id", "")):
        devices.append({
            "unique_id": p["unique_id"],
            "name": p.get("name") or "",
            "boat": labels.get(p["unique_id"], ""),
            "lat": p["lat"], "lon": p["lon"],
            "sog": p.get("speed_kn"),
            "last_fix_age": round(max(0.0, now - (p.get("fix_time") or now)), 1),
        })
    # "Reporting" = a fix within the last hour (the amber window); older devices
    # are still listed but don't count as live.
    for d in devices:
        d["rag"] = _rag(d["last_fix_age"])
    reporting = sum(1 for d in devices if d["last_fix_age"] is not None and d["last_fix_age"] <= TRACK_RAG_AMBER_S)
    if error:
        message = error
    elif not cfg["enabled"]:
        message = "GPS tracking is disabled. Enable it in Settings."
    elif devices:
        message = (f"{reporting} of {len(devices)} tracker(s) reporting (fix within the last hour)"
                   + (" (simulated)" if source == "sim" else ""))
    else:
        message = "No trackers reporting yet."
    return {
        "ok": reporting > 0 and not error,
        "message": message,
        "devices": devices,
        "reporting": reporting,
        "source": source,
        "sim": cfg["sim_enabled"],
        "enabled": cfg["enabled"],
    }


def tracker_markers(max_age_s: Optional[float] = None) -> List[Dict[str, Any]]:
    """Latest position of each **configured** tracker, for the Trackers map.

    Only trackers currently in the trackers table are shown: position history is
    deliberately kept when a tracker is removed (a boat keeps its past track), so
    the stored fixes alone would otherwise resurrect deleted devices on the map.
    Shaped for RaceCourseMap.updateBoats (sail_no/boat_name carry a short label).

    ``max_age_s`` drops trackers whose last fix is older than that. The Trackers
    page wants them all, marked stale — it is the page you go to when a tracker
    is *not* reporting. The dashboard wants only what is out there now, so an
    ashore tracker that last reported on Saturday does not drag the map's zoom
    across the county.
    """
    configured = {t["unique_id"] for t in list_trackers()}
    latest = latest_positions()
    labels = _boat_labels_by_unique_id()
    now = time.time()
    out: List[Dict[str, Any]] = []
    for uid, p in latest.items():
        if uid not in configured:
            continue
        ft = p.get("fix_time")
        age = max(0.0, now - ft) if ft else None
        if max_age_s is not None and (age is None or age > max_age_s):
            continue
        label = labels.get(uid) or uid
        out.append({
            "unique_id": uid,
            "boat_name": label, "sail_no": label,
            "lat": p["lat"], "lon": p["lon"],
            "cog": p.get("course_deg"),
            "stale": age is not None and age > TRACK_RAG_AMBER_S,
        })
    return out


def tracker_report_status() -> Dict[str, Dict[str, Any]]:
    """Per-tracker last-fix age + RAG bucket, keyed by unique_id.

    Uses the stored track (latest fix per device), so it covers every tracker we
    have ever recorded a fix for — independent of the current poll.
    """
    latest = latest_positions()
    now = time.time()
    out: Dict[str, Dict[str, Any]] = {}
    for uid, p in latest.items():
        ft = p.get("fix_time")
        age = max(0.0, now - ft) if ft else None
        pct = p.get("battery_pct")
        out[uid] = {"age": age, "rag": _rag(age), "text": _fmt_age(age),
                    **battery_reading(pct, age)}
    return out


def low_battery_trackers(threshold_pct: Optional[float] = None) -> List[Dict[str, Any]]:
    """Trackers assigned to a boat whose last fix reported a low battery.

    Assigned ones only. A spare in the drawer being flat is a job for another day; a
    tracker on a boat that is about to race is the one worth a warning on the
    dashboard. Sorted flattest first, so the worst is the one you read.
    """
    limit = BATTERY_LOW_PCT if threshold_pct is None else float(threshold_pct)
    status = tracker_report_status()
    trackers = [t for t in list_trackers() if t.get("boat_id")]
    # list_trackers is SELECT * FROM trackers, which carries the boat *id* and not its
    # name — a warning that named no boat would be no use on a dashboard.
    boats = get_boats_by_id([t.get("boat_id") for t in trackers])
    out: List[Dict[str, Any]] = []
    for tk in trackers:
        uid = str(tk.get("unique_id") or "")
        pct = (status.get(uid) or {}).get("battery")
        if pct is None or pct > limit:
            continue
        boat = boats.get(tk.get("boat_id")) or {}
        out.append({"unique_id": uid, "label": tk.get("label") or uid,
                    "boat_name": row_get(boat, "boat_name", "") or "", "battery": pct,
                    "battery_rag": battery_rag(pct),
                    "critical": pct <= BATTERY_CRITICAL_PCT})
    out.sort(key=lambda r: r["battery"])
    return out


# ---------------------------------------------------------------------------
# Asking racing boats where they are, rather than waiting for their own schedule
# ---------------------------------------------------------------------------
# A GL521MG reports once a minute and holds each fix until the top of the minute, so
# its position reaches the app about 45 s old and 185 m behind the boat at 6 kn. That
# is the resolution a finish is interpolated across. An on-demand request answers in
# about a second, and can be sustained at 20 s — measured; faster returns duplicates of
# a cached fix and below 10 s the device backs up. It costs about +1.1 %/h, which is a
# fifth of a percent for the last ten minutes of a race and about 19% for a whole nine
# hours: affordable either way on a unit that idles at 1 %/h.
#
# Only while it is worth having. From the warning signal, because that is when the
# boats matter, until a minute after each stops racing, because a finish detected from
# the fix before the line still wants the fix after it.
RACE_POLL_INTERVAL_S = 20.0
RACE_POLL_GRACE_S = 60.0          # keep asking this long after a boat stops racing
RACE_POLL_MAX_RACE_HOURS = 30.0   # a backstop against a race left open for ever
# Which trackers get asked, keyed by IMEI type code rather than by protocol. "gl200" is
# Queclink's whole @Track family and the models in it do not answer alike -- the command
# below carries "gl521m", which is the GL521M's *password*, so another Queclink would
# reject it outright. Keyed by type code it is also immune to somebody renaming the model
# in the catalogue. The protocol is still checked, as everywhere else: type code narrows,
# protocol confirms.
#
# Teltonika is absent deliberately: an ATC700 already reports every 10 s while moving and
# answers in a second, so there is nothing to ask for and its battery cannot afford the
# asking. Adding a model here means knowing its command and its password, not guessing
# from the family it belongs to.
RACE_POLL_COMMANDS_BY_TAC = {
    # Queclink GL521MG. Sub-command 1 is RTL, "report current position".
    "86486407": ("gl200", "AT+GTRTO=gl521m,1,,,,,,FFFF$"),
}


def race_poll_command_for(unique_id: Any, protocol: Any) -> Optional[str]:
    """The position request this tracker will understand, or None if we do not know one."""
    entry = RACE_POLL_COMMANDS_BY_TAC.get(tracker_tac(unique_id))
    if not entry:
        return None
    wants, command = entry
    return command if str(protocol or "").strip().lower() == wants else None

_RACE_POLL_LAST_SENT: Dict[str, float] = {}     # unique_id -> when we last asked
_RACE_POLL_LAST_RACING: Dict[str, float] = {}   # unique_id -> when it was last racing


def race_poll_targets(now: Optional[float] = None) -> List[str]:
    """Trackers that should be asked for a position right now.

    A boat qualifies from its race's warning signal until ``RACE_POLL_GRACE_S`` after it
    stops being RACING. The grace matters: a finish is interpolated between the fixes
    either side of the line, so the one *after* the crossing is the one that pins it.
    """
    # Imported here rather than at module scope, as the other callers of get_entries in
    # this file do: core.races reaches back into tracking for the leaderboard.
    from core.races import get_entries, race_first_warning_dt

    now = now if now is not None else time.time()
    protocols = tracker_protocols()
    wanted: List[str] = []
    try:
        with get_db() as db:
            races = db.execute("SELECT * FROM races").fetchall()
    except sqlite3.OperationalError:
        return []
    for race in races:
        try:
            warning = race_first_warning_dt(race)
        except Exception:
            continue
        if warning is None:
            continue
        started = warning.timestamp()
        if now < started or now - started > RACE_POLL_MAX_RACE_HOURS * 3600:
            continue
        try:
            entries = get_entries(int(race["id"]))
        except Exception:
            continue
        for entry in entries:
            uid = effective_tracker_for_entry(entry)
            if not uid or not race_poll_command_for(uid, protocols.get(str(uid))):
                continue
            if row_get(entry, "status", "") == "RACING":
                _RACE_POLL_LAST_RACING[str(uid)] = now
                wanted.append(str(uid))
            elif now - _RACE_POLL_LAST_RACING.get(str(uid), 0.0) <= RACE_POLL_GRACE_S:
                wanted.append(str(uid))
    return sorted(set(wanted))


def run_race_position_polling(cfg: Optional[Dict[str, Any]] = None,
                              now: Optional[float] = None) -> int:
    """Ask each racing boat's tracker for a position. Returns how many were asked.

    Never raises: this runs inside the background poller, during a race.
    """
    cfg = cfg or track_config()
    if not cfg.get("race_poll_enabled", True):
        return 0
    if not (cfg["base_url"] and cfg["token"]):
        return 0
    now = now if now is not None else time.time()
    try:
        targets = race_poll_targets(now)
    except Exception:
        return 0
    if not targets:
        return 0
    protocols = tracker_protocols()
    asked = 0
    for uid in targets:
        if now - _RACE_POLL_LAST_SENT.get(uid, 0.0) < RACE_POLL_INTERVAL_S:
            continue
        command = race_poll_command_for(uid, protocols.get(uid, ""))
        if not command:
            continue
        # Mark it sent before the attempt: a Traccar that is refusing must not turn into
        # a tight retry loop around the whole fleet.
        _RACE_POLL_LAST_SENT[uid] = now
        try:
            send_tracker_command(uid, command, cfg)
            asked += 1
        except Exception:
            pass
    return asked


def track_background_loop() -> None:
    """Continuously pull positions, store them, and run finish detection."""
    while True:
        sleep_seconds = 5
        try:
            cfg = track_config()
            sleep_seconds = cfg["poll_seconds"]
            if track_active(cfg):
                # Snapshot how far each track had got BEFORE this poll stores the
                # latest fixes, so a gap is still visible to the back-fill below.
                since = _latest_fix_times()
                positions, error, source = collect_positions(cfg)
                if positions:
                    insert_positions(positions, retention_days=cfg["retention_days"])
                # Recover anything missed while the app was down or offline: the
                # live poll only ever returns each device's latest fix, so a gap
                # would otherwise stay a hole in the track for good.
                try:
                    filled, fill_error = backfill_positions(cfg, since=since)
                    if filled:
                        log_event(None, "gps", f"Back-filled {filled} missed position(s) from Traccar")
                    error = error or fill_error
                except Exception:
                    pass
                try:
                    run_finish_detection()
                except Exception:
                    pass
                # Ask racing boats where they are, rather than waiting up to a minute
                # for a GL521MG's own schedule. Rate-limited per tracker inside.
                try:
                    run_race_position_polling(cfg)
                except Exception:
                    pass
                status = _build_status(positions, error, source, cfg)
            else:
                status = _build_status([], None, "none", cfg)
            try:
                # Housekeeping, hourly, off the ingest path. Runs whether or not
                # the poller is active: a stopped tracking source is no reason to
                # let the table grow for ever.
                #
                # It sits *after* the if/else, not between them. It used to sit
                # between, which quietly re-bound that else to this try: the purge
                # returns normally every cycle, so every poll's status was thrown
                # away and replaced with an empty one. The settings box then read
                # "No trackers reporting yet" while fixes were arriving.
                purge_old_positions(cfg["retention_days"])
            except Exception:
                pass
            with TRACK_MONITOR_LOCK:
                TRACK_MONITOR_STATE["last_status"] = status
                TRACK_MONITOR_STATE["last_poll_at"] = time.time()
        except Exception as exc:
            with TRACK_MONITOR_LOCK:
                TRACK_MONITOR_STATE["last_status"] = {"ok": False, "message": f"GPS tracking error: {exc}", "devices": []}
                TRACK_MONITOR_STATE["last_poll_at"] = time.time()
            sleep_seconds = 10
        time.sleep(max(2, min(60, int(sleep_seconds))))


def track_runtime_status() -> Dict[str, Any]:
    """Return the latest poller status for the settings block and the API."""
    with TRACK_MONITOR_LOCK:
        status = dict(TRACK_MONITOR_STATE.get("last_status") or {})
        started = bool(TRACK_MONITOR_STATE.get("started"))
        last_poll_at = TRACK_MONITOR_STATE.get("last_poll_at")
        last_forward_at = TRACK_MONITOR_STATE.get("last_forward_at")
        forward_count = int(TRACK_MONITOR_STATE.get("forward_count") or 0)
    cfg = track_config()
    if not status or "devices" not in status:
        status = {"ok": False, "message": "GPS tracking is starting…", "devices": []}
    status["background_started"] = started
    status["sim"] = cfg["sim_enabled"]
    status["enabled"] = cfg["enabled"]
    if last_poll_at:
        status["last_poll_at"] = last_poll_at
    # Push status: whether Traccar's forwarder is configured here, and whether
    # anything has actually arrived that way (the poller stays as the safety net).
    status["ingest_configured"] = bool(cfg["ingest_secret"])
    status["forward_count"] = forward_count
    if last_forward_at:
        status["last_forward_at"] = last_forward_at
        status["last_forward_age"] = round(max(0.0, time.time() - last_forward_at), 1)
    status["push_text"] = push_status_text(bool(cfg["ingest_secret"]), forward_count, last_forward_at)
    return status


def push_status_text(configured: bool, forward_count: int, last_forward_at: Optional[float] = None) -> str:
    """One line describing whether Traccar is pushing fixes to this app.

    A token mismatch between Settings and the relay's ``forward.header`` fails
    silently — the app keeps polling and everything looks healthy, only the
    automatic horn is a poll interval late. So say plainly when push is armed
    but nothing has come through, rather than leaving it to be inferred from
    fix ages.
    """
    if not configured:
        return "Push: not configured — positions arrive on the next poll."
    if not forward_count:
        return ("Push: configured, but no fix has arrived this way yet — check the relay's "
                "forward.header matches this token.")
    age = None if last_forward_at is None else max(0.0, time.time() - last_forward_at)
    fixes = "1 fix" if forward_count == 1 else f"{forward_count:,} fixes"
    return f"Push: working — {fixes} received, last {_fmt_age(age)}."


def start_track_monitor_worker() -> None:
    """Start the GPS-tracking polling thread if it is not already running."""
    with TRACK_MONITOR_LOCK:
        if TRACK_MONITOR_STATE.get("started"):
            return
        TRACK_MONITOR_STATE["started"] = True
    thread = threading.Thread(target=track_background_loop, name="track-monitor", daemon=True)
    thread.start()
