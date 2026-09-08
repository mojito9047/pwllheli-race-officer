"""Pursuit-race helpers.

A pursuit race is a fixed-period race with per-boat staggered starts derived from
a single rating system (IRC or YTC). The slowest-rated boat starts first; every
faster boat is delayed so that, sailing to its handicap, all boats would finish
together at the end of the fixed period. There are no classes and a single finish
signal; the race officer records finishing positions by the boats' on-the-water
order rather than by corrected time.

Start maths (unified across rating systems). Define a speed factor s where a
larger value means a faster boat:

    IRC:  s = TCC              (a higher TCC is a faster boat)
    YTC:  s = 1 / YTC number   (a higher YTC number is a slower boat)

The slowest boat (smallest s) starts at the first start. Each boat's delay is

    offset_i = duration * (1 - s_slowest / s_i)

so equal-handicap boats would finish together at first_start + duration.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from core.db import get_db, row_get
from core.races import race_first_start_dt
from core.series import rating_from_entry_for_result
from core.timeutils import parse_dt

# rating_rule values a pursuit race may use to drive its start times.
PURSUIT_RATING_RULES = ("IRC_TCC", "YTC")


def is_pursuit_race(race: Any) -> bool:
    """Return True when the race row is a pursuit race."""
    return str(row_get(race, "race_type", "") or "").strip().lower() == "pursuit"


def pursuit_rating_type(race: Any) -> str:
    """Return 'IRC' or 'YTC' — the rating system that drives the staggered starts."""
    return "YTC" if str(row_get(race, "rating_rule", "") or "").upper() == "YTC" else "IRC"


def pursuit_duration_seconds(race: Any) -> Optional[float]:
    """Return the fixed race period in seconds, or None if not set."""
    try:
        mins = float(row_get(race, "pursuit_duration_min", None))
    except (TypeError, ValueError):
        return None
    return mins * 60.0 if mins > 0 else None


def _speed_factor(rating: Optional[float], rating_type: str) -> Optional[float]:
    """Speed factor where a larger value means a faster boat.

    This mirrors the results engine's corrected-time multiplier so the pursuit
    starts stay consistent with how the app scores handicaps: corrected time is
    elapsed * TCC for IRC and elapsed * 1000 / YTC for YTC, and a larger
    multiplier means the boat must sail faster (a faster boat). Only the ratio
    between boats matters for the offsets.
    """
    if rating is None or rating <= 0:
        return None
    return rating if rating_type == "IRC" else 1000.0 / rating


def compute_pursuit_start_offsets(
    entries: List[sqlite3.Row],
    rating_type: str,
    duration_seconds: Optional[float],
    boats_by_id: Optional[Dict[int, sqlite3.Row]] = None,
) -> Tuple[Dict[int, float], List[str]]:
    """Compute each entry's start offset (seconds from the first start).

    Returns (offsets_by_entry_id, missing_rating_boat_names). The slowest boat
    gets offset 0. Boats without a usable rating in the chosen system get no
    offset and are listed as warnings.
    """
    factors: Dict[int, float] = {}
    missing: List[str] = []
    boats_by_id = boats_by_id or {}
    for entry in entries:
        boat = boats_by_id.get(row_get(entry, "boat_id"))
        rating, _label = rating_from_entry_for_result(entry, boat, rating_type)
        factor = _speed_factor(rating, rating_type)
        if factor is None:
            missing.append(row_get(entry, "boat_name", "") or f"Entry {row_get(entry, 'id')}")
            continue
        factors[int(entry["id"])] = factor

    offsets: Dict[int, float] = {}
    if factors and duration_seconds and duration_seconds > 0:
        slowest = min(factors.values())
        for entry_id, factor in factors.items():
            offsets[entry_id] = max(0.0, duration_seconds * (1.0 - slowest / factor))
    return offsets, missing


def pursuit_missing_rating_ids(entries: List[sqlite3.Row], rating_type: str, boats_by_id: Optional[Dict[int, sqlite3.Row]] = None) -> set:
    """Return the ids of entries that genuinely lack a rating in the chosen system.

    This is distinct from an entry simply not having a start time yet (which also
    happens before the first warning time / fixed period are set).
    """
    boats_by_id = boats_by_id or {}
    missing = set()
    for entry in entries:
        boat = boats_by_id.get(row_get(entry, "boat_id"))
        rating, _label = rating_from_entry_for_result(entry, boat, rating_type)
        if rating is None or rating <= 0:
            missing.add(int(entry["id"]))
    return missing


def _load_boats_by_id(db: sqlite3.Connection, entries: List[sqlite3.Row]) -> Dict[int, sqlite3.Row]:
    ids = [row_get(e, "boat_id") for e in entries if row_get(e, "boat_id")]
    if not ids:
        return {}
    placeholders = ",".join("?" for _ in ids)
    rows = db.execute(f"SELECT * FROM boats WHERE id IN ({placeholders})", ids).fetchall()
    return {int(b["id"]): b for b in rows}


def assign_pursuit_start_times(db: sqlite3.Connection, race: sqlite3.Row) -> Dict[str, Any]:
    """Recompute and persist every entry's start_time_override for a pursuit race.

    Uses the supplied (already open) DB connection so it can run inside another
    transaction. Does not commit. Returns a summary dict.
    """
    rating_type = pursuit_rating_type(race)
    duration = pursuit_duration_seconds(race)
    first_start = race_first_start_dt(race)
    entries = db.execute("SELECT * FROM entries WHERE race_id = ? ORDER BY id", (race["id"],)).fetchall()
    boats_by_id = _load_boats_by_id(db, entries)
    offsets, missing = compute_pursuit_start_offsets(entries, rating_type, duration, boats_by_id)

    assigned = 0
    for entry in entries:
        entry_id = int(entry["id"])
        if first_start and duration and entry_id in offsets:
            start_dt = first_start + timedelta(seconds=offsets[entry_id])
            db.execute(
                "UPDATE entries SET start_time_override = ? WHERE id = ?",
                (start_dt.isoformat(timespec="seconds"), entry_id),
            )
            assigned += 1
        else:
            db.execute("UPDATE entries SET start_time_override = NULL WHERE id = ?", (entry_id,))

    finish_dt = first_start + timedelta(seconds=duration) if (first_start and duration) else None
    return {
        "assigned": assigned,
        "warnings": missing,
        "rating_type": rating_type,
        "first_start": first_start.isoformat(timespec="seconds") if first_start else None,
        "finish": finish_dt.isoformat(timespec="seconds") if finish_dt else None,
        "duration_seconds": duration,
        "ready": bool(first_start and duration),
    }


def recompute_pursuit_start_times(race_id: int) -> Dict[str, Any]:
    """Recompute pursuit start times for a race id, opening and committing its own DB."""
    with get_db() as db:
        race = db.execute("SELECT * FROM races WHERE id = ?", (race_id,)).fetchone()
        if not race or not is_pursuit_race(race):
            return {"assigned": 0, "warnings": [], "ready": False}
        result = assign_pursuit_start_times(db, race)
        db.commit()
        return result


def pursuit_finish_dt(race: sqlite3.Row) -> Optional[datetime]:
    """Return the single finish-signal time (first start + duration)."""
    first_start = race_first_start_dt(race)
    duration = pursuit_duration_seconds(race)
    return first_start + timedelta(seconds=duration) if (first_start and duration) else None


def pursuit_start_rows(race: sqlite3.Row, entries: List[sqlite3.Row]) -> List[Dict[str, Any]]:
    """Return per-entry start rows sorted by start time for display.

    Each row: {entry, start_dt, start_iso, has_start}. Entries without a start
    time (missing rating, or timing not set yet) sort to the end.
    """
    rows: List[Dict[str, Any]] = []
    for entry in entries:
        start_dt = parse_dt(row_get(entry, "start_time_override"))
        rows.append({
            "entry": entry,
            "start_dt": start_dt,
            "start_iso": start_dt.isoformat(timespec="seconds") if start_dt else None,
            "has_start": start_dt is not None,
        })
    rows.sort(key=lambda r: (r["start_dt"] is None, r["start_dt"] or datetime.max, int(r["entry"]["id"])))
    return rows


def _format_offset(seconds: Optional[float]) -> Optional[str]:
    """Format a start offset from the first boat as '+M:SS' (or 'first start')."""
    if seconds is None:
        return None
    seconds = int(round(seconds))
    if seconds <= 0:
        return "first start"
    return f"+{seconds // 60}:{seconds % 60:02d}"


def pursuit_display_rows(race: sqlite3.Row, entries: List[sqlite3.Row], boats_by_id: Optional[Dict[int, sqlite3.Row]] = None, rating_type: Optional[str] = None) -> List[Dict[str, Any]]:
    """Return per-entry display rows for the admin and competitor pages.

    Each row carries the absolute start time (when the first warning time and
    period are set), the offset from the first boat to start (a preview usable
    before the timing is set), and whether the boat is genuinely unrated. Rows are
    sorted by absolute start time when available, otherwise by offset.
    """
    rating_type = rating_type or pursuit_rating_type(race)
    duration = pursuit_duration_seconds(race)
    offsets, _missing = compute_pursuit_start_offsets(entries, rating_type, duration, boats_by_id)
    missing_ids = pursuit_missing_rating_ids(entries, rating_type, boats_by_id)
    rows: List[Dict[str, Any]] = []
    for entry in entries:
        entry_id = int(entry["id"])
        start_dt = parse_dt(row_get(entry, "start_time_override"))
        offset = offsets.get(entry_id)
        rows.append({
            "entry": entry,
            "start_dt": start_dt,
            "start_iso": start_dt.isoformat(timespec="seconds") if start_dt else None,
            "has_start": start_dt is not None,
            "no_rating": entry_id in missing_ids,
            "offset_seconds": offset,
            "offset_text": _format_offset(offset),
        })
    if race_first_start_dt(race) and duration:
        rows.sort(key=lambda r: (r["start_dt"] is None, r["start_dt"] or datetime.max, int(r["entry"]["id"])))
    else:
        rows.sort(key=lambda r: (r["offset_seconds"] is None, r["offset_seconds"] if r["offset_seconds"] is not None else 1e9, int(r["entry"]["id"])))
    return rows


def pursuit_next_start_announcements(race: sqlite3.Row, entries: List[sqlite3.Row], within_seconds: float = 20.0, lead_seconds: float = 10.0) -> List[Dict[str, Any]]:
    """Return spoken 'next to start' announcements naming upcoming boats.

    Boats whose starts fall within `within_seconds` of each other are announced
    together. Each group (after the first) is announced `lead_seconds` after the
    previous group's last boat started — i.e. a heads-up for the boats due next.
    Returns [{dt, text, label}] where dt is when to speak.
    """
    boats_by_time: Dict[datetime, List[str]] = {}
    for entry in entries:
        start_dt = parse_dt(row_get(entry, "start_time_override"))
        if start_dt:
            boats_by_time.setdefault(start_dt, []).append(entry["boat_name"] or "")
    times = sorted(boats_by_time)
    if len(times) < 2:
        return []
    groups: List[Dict[str, Any]] = []
    for start_dt in times:
        if groups and (start_dt - groups[-1]["last"]).total_seconds() <= within_seconds:
            groups[-1]["last"] = start_dt
            groups[-1]["names"].extend(boats_by_time[start_dt])
        else:
            groups.append({"start": start_dt, "last": start_dt, "names": list(boats_by_time[start_dt])})

    announcements: List[Dict[str, Any]] = []
    for i in range(1, len(groups)):
        speak_dt = groups[i - 1]["last"] + timedelta(seconds=lead_seconds)
        names = [n for n in groups[i]["names"] if n]
        if not names:
            continue
        if len(names) == 1:
            phrase = f"{names[0]} next to start."
        else:
            phrase = ", ".join(names[:-1]) + f" and {names[-1]} next to start."
        announcements.append({
            "dt": speak_dt,
            "text": phrase,
            "label": f"Pursuit next-start: {groups[i]['start'].strftime('%H:%M:%S')}",
        })
    return announcements


def pursuit_start_signal_times(race: sqlite3.Row, entries: List[sqlite3.Row]) -> List[datetime]:
    """Return the distinct boat start times, ascending (one horn/video per time)."""
    seen = {}
    for entry in entries:
        start_dt = parse_dt(row_get(entry, "start_time_override"))
        if start_dt:
            seen[start_dt.isoformat(timespec="seconds")] = start_dt
    return [seen[key] for key in sorted(seen)]
