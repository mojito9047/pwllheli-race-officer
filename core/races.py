"""Race-level helpers: entry loading and start-time derivation.

Extracted from app.py. get_entries reads a race's entries via core.db; the
start-time helpers derive the first warning/start times from a race row (the
DB column is still named start_time but holds the first warning-signal time;
the first start is five minutes later under RRS 26).

The start-plan/schedule builders live in core/classconfig.py with the rest of
the class-config model; the series/race result builders live in core/series.py.
"""
from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional

from core.db import get_db, init_db, row_get
from core.racesignals import (
    POSTPONEMENT_KINDS,
    RESUME_WARNING_DELAY_S,
    normalise_postponement_kind,
)
from core.timeutils import parse_dt


def get_entries(race_id: int) -> List[sqlite3.Row]:
    """Load entries for a race in display order."""
    with get_db() as db:
        return db.execute("SELECT * FROM entries WHERE race_id = ? ORDER BY id", (race_id,)).fetchall()


def postponement_kind_spec(kind: Any) -> Dict[str, Any]:
    """The spec for a stored kind, defaulting to plain AP."""
    return POSTPONEMENT_KINDS.get(normalise_postponement_kind(kind),
                                  POSTPONEMENT_KINDS["AP"])


def race_is_postponed(race: Any, now: Optional[datetime] = None) -> bool:
    """Whether AP is flying for this race, right now.

    Lives here rather than with the postponement commands in core.raceadmin
    because the start-sequence scheduler has to ask it, and that module cannot
    import raceadmin without a cycle. It is a question about the state of a
    race, which is what this module is for.

    Once the race officer has said when AP comes down, the flag is still up
    until that moment: the displays must show what is actually on the mast, not
    what has been decided about it.
    """
    if not str(row_get(race, "postponed_at", "") or "").strip():
        return False
    ends = parse_dt(str(row_get(race, "postponement_ends_at", "") or ""))
    return True if not ends else (now or datetime.now()) < ends


def postponement_is_due_to_end(race: Any, now: Optional[datetime] = None) -> bool:
    """A postponement whose chosen moment has come, and not yet been ended."""
    if not str(row_get(race, "postponed_at", "") or "").strip():
        return False
    return not race_is_postponed(race, now)


def postponement_flag(race: Any) -> str:
    """The flag flying for this race, or "" -- for the pages that show flags."""
    if not race_is_postponed(race):
        return ""
    kind = normalise_postponement_kind(row_get(race, "postponement_kind", "") or "AP")
    return POSTPONEMENT_KINDS.get(kind, POSTPONEMENT_KINDS["AP"])["flag"]


def race_first_warning_dt(race: sqlite3.Row) -> Optional[datetime]:
    """Return the race's first warning-signal time.

    The historic database column is still named start_time, but from v0.51 the
    race office enters the time of the first warning signal.  The first start is
    five minutes after this time under RRS 26.
    """
    return parse_dt(row_get(race, "start_time", ""))


def entry_display_status(entry: Any, race: Optional[sqlite3.Row]) -> str:
    """Status to show for an entry: **PRESTART** until the warning signal.

    An entry is stored as RACING from the moment the boat is entered, because
    every calculation keys off that (elapsed/corrected time, results, GPS finish
    detection, "still racing" counts). For display only, a boat that is still
    RACING before the race's first warning signal is shown as PRESTART, which is
    what the race office and competitors expect to see; it becomes RACING once
    the warning signal time is reached. Any other status (FINISHED, DNF, OCS …)
    is shown unchanged.
    """
    status = str(row_get(entry, "status", "") or "").strip().upper() or "RACING"
    if status != "RACING":
        return status
    warning = race_first_warning_dt(race) if race is not None else None
    if warning is None or datetime.now() < warning:
        return "PRESTART"
    return status


def race_first_start_dt(race: sqlite3.Row) -> Optional[datetime]:
    """Return the first actual start time for a race."""
    warning_dt = race_first_warning_dt(race)
    return warning_dt + timedelta(minutes=5) if warning_dt else None


def race_first_start_time(race: sqlite3.Row) -> str:
    """Return the first actual start time as an ISO string for templates/results."""
    dt = race_first_start_dt(race)
    return dt.isoformat(timespec="seconds") if dt else ""


def list_series() -> List[sqlite3.Row]:
    """Return all race series for navigation and race assignment."""
    init_db()
    with get_db() as db:
        return db.execute("SELECT * FROM race_series ORDER BY updated_at DESC, id DESC").fetchall()


def get_series(series_id: Optional[int]) -> Optional[sqlite3.Row]:
    """Load one race series by id."""
    if not series_id:
        return None
    init_db()
    with get_db() as db:
        return db.execute("SELECT * FROM race_series WHERE id = ?", (series_id,)).fetchone()


def natural_name_key(name: Any) -> tuple:
    """Sort "Race 2" before "Race 10", which plain alphabetical does not."""
    parts = re.split(r"(\d+)", str(name or "").strip().lower())
    return tuple((1, int(p)) if p.isdigit() else (0, p) for p in parts if p != "")


def race_sort_key(race: Any) -> tuple:
    """The order races are sailed in, as far as anything can know it.

    A start time when the race has one; otherwise the name, read naturally, so
    a series set up in advance lists Race 1 to Race 6 rather than in whatever
    order somebody happened to create them.

    The two are never compared against each other, which is the mistake this
    replaces: every list did `COALESCE(start_time, created_at)`, sorting one
    race by when it starts and the next by when it was typed in. Those are
    different clocks. Give Race 1 of a September series a start time and its key
    jumps from "created on the 2nd" to "starts on the 4th", so it moves to the
    end of a list it used to sit in the middle of -- and the three pages that
    each did this slightly differently disagreed about where.

    A fully scheduled series is unaffected: every race has a start time, so this
    is start-time order, exactly as before. That matters because the series race
    NUMBER is a race's position in this list, and a scored season must not
    renumber itself.
    """
    start = str(row_get(race, "start_time", "") or "").strip()
    if start:
        return (0, start, ())
    return (1, "", natural_name_key(row_get(race, "name", "")))


def races_in_order(races: Iterable[Any]) -> List[Any]:
    """Races in sailing order. One rule, so every page agrees."""
    return sorted(races, key=race_sort_key)


def race_status_label(race: Any, entries: Iterable[Any]) -> str:
    """What to call a race's state in a list.

    A new race's entries are added RACING, which is right -- it is how a boat
    that has not finished is recorded -- and it is not the same as the race
    being under way. A series set up in advance showed six races all reading
    **Racing** before a course or a start time had been set for any of them, and
    then, when the first got a start time, that one alone changed to *Start
    sequence pending* while the other five went on claiming to be racing.

    So "no start time" is asked before "any boat still out", and a race nobody
    has scheduled says so.
    """
    entries = list(entries)
    racing_count = sum(1 for e in entries if row_get(e, "status") == "RACING")
    finished = bool(entries) and racing_count == 0
    first_start_dt = parse_dt(race_first_start_time(race))
    if finished:
        return "Finished"
    if postponement_flag(race):
        # Before "start sequence pending": there is no sequence pending while AP
        # is up.
        return "Postponed"
    if not first_start_dt:
        return "No start time set"
    if first_start_dt > datetime.now():
        return "Start sequence pending"
    if racing_count > 0:
        return "Racing"
    return "Started"


def get_series_races(series_id: int) -> List[sqlite3.Row]:
    """Load all races assigned to one series."""
    init_db()
    with get_db() as db:
        rows = db.execute(
            "SELECT * FROM races WHERE series_id = ? ORDER BY id", (series_id,)
        ).fetchall()
    # Ordered here rather than in SQL: the rule needs a natural read of the race
    # name, which SQLite cannot do, and it has to be the same rule everywhere.
    return races_in_order(rows)


def get_race(race_id: int) -> Optional[sqlite3.Row]:
    """Load one race by id."""
    with get_db() as db:
        return db.execute("SELECT * FROM races WHERE id = ?", (race_id,)).fetchone()


def get_boats_by_id(boat_ids: Iterable[Any]) -> Dict[int, sqlite3.Row]:
    """Return a lookup dictionary of boat database rows keyed by id."""
    ids = sorted({int(bid) for bid in boat_ids if bid is not None})
    if not ids:
        return {}
    placeholders = ",".join("?" for _ in ids)
    with get_db() as db:
        rows = db.execute(f"SELECT * FROM boats WHERE id IN ({placeholders})", ids).fetchall()
    return {int(row["id"]): row for row in rows}


def race_has_finished_for_sequence(race_id: int) -> bool:
    """Return True when a race has entries and none are still racing."""
    entries = get_entries(race_id)
    return bool(entries) and all(str(e["status"] or "").upper() != "RACING" for e in entries)
