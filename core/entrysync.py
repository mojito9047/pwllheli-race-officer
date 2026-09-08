"""Race/series entry management and synchronisation.

Extracted verbatim from app.py. Adds boat-database or manual entries to a race
(with the rating snapshot rules), and keeps entries synchronised across the
races of a series: a boat entered in one series race is propagated to the
others, with DNC-style status derived from whether each target race has already
started. All functions take an open db connection or use core.db/core.races
accessors; no Flask coupling.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple

from core.boats import boat_rating_for_rule
from core.classconfig import race_series_row
from core.db import get_db, row_get
from core.races import get_series, race_first_start_dt, races_in_order
from core.timeutils import clean_sail_no, normalise_key, parse_dt

def entry_exists_for_boat(db: sqlite3.Connection, race_id: int, boat_id: int) -> bool:
    """Return True when a boat-database entry already exists in a race."""
    return db.execute("SELECT 1 FROM entries WHERE race_id = ? AND boat_id = ? LIMIT 1", (race_id, boat_id)).fetchone() is not None


def entry_exists_for_manual_boat(db: sqlite3.Connection, race_id: int, boat_name: str, sail_no: str) -> bool:
    """Return True when a manual/race-only boat already exists in a race."""
    return db.execute(
        """
        SELECT 1 FROM entries
        WHERE race_id = ?
          AND boat_id IS NULL
          AND UPPER(TRIM(boat_name)) = UPPER(TRIM(?))
          AND COALESCE(UPPER(TRIM(sail_no)), '') = COALESCE(UPPER(TRIM(?)), '')
        LIMIT 1
        """,
        (race_id, boat_name, sail_no or ""),
    ).fetchone() is not None


def legacy_entry_rating_value(irc_rating: Optional[float], ytc_rating: Optional[float]) -> Tuple[Optional[float], str]:
    """Return the backwards-compatible single rating fields stored on entries."""
    if irc_rating is not None:
        return irc_rating, "Race entry IRC TCC"
    if ytc_rating is not None:
        return ytc_rating, "Race entry YTC"
    return None, "Race entry rating"


def add_boat_database_entry_to_race(
    db: sqlite3.Connection,
    race: sqlite3.Row,
    boat: sqlite3.Row,
    class_override: str = "",
    rating_source: str = "AUTO",
    status: str = "RACING",
) -> bool:
    """Add one boat-database record to one race, snapshotting race ratings."""
    race_id = int(race["id"])
    boat_id = int(boat["id"])
    if entry_exists_for_boat(db, race_id, boat_id):
        return False
    # The boat's own free-text class used to sit between the override and the
    # race's label. Nothing types it any more, so a new entry would have taken
    # a value from a field the race officer can no longer see or correct.
    class_name = str(class_override or "").strip() or race["class_name"] or ""
    irc_rating = boat["irc_rating"] if boat["irc_rating"] is not None else None
    ytc_rating = boat["ytc_rating"] if boat["ytc_rating"] is not None else None
    legacy_rating, legacy_source = legacy_entry_rating_value(irc_rating, ytc_rating)
    db.execute(
        """
        INSERT INTO entries (race_id, boat_id, boat_name, sail_no, class_name, rating, rating_source, manual_irc_rating, manual_ytc_rating, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (race_id, boat_id, boat["boat_name"], boat["sail_no"], class_name, legacy_rating, legacy_source, irc_rating, ytc_rating, normalise_entry_status(status)),
    )
    return True


def add_manual_entry_to_race(
    db: sqlite3.Connection,
    race: sqlite3.Row,
    boat_name: str,
    sail_no: str,
    class_name: str,
    irc_rating: Optional[float],
    ytc_rating: Optional[float],
    status: str = "RACING",
) -> bool:
    """Add one race-only/manual entry to one race, preserving per-race ratings."""
    race_id = int(race["id"])
    if entry_exists_for_manual_boat(db, race_id, boat_name, sail_no):
        return False
    entry_class = str(class_name or "").strip() or race["class_name"] or ""
    legacy_rating, legacy_source = legacy_entry_rating_value(irc_rating, ytc_rating)
    db.execute(
        """
        INSERT INTO entries (race_id, boat_name, sail_no, class_name, rating, rating_source, manual_irc_rating, manual_ytc_rating, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (race_id, boat_name, sail_no, entry_class, legacy_rating, legacy_source, irc_rating, ytc_rating, normalise_entry_status(status)),
    )
    return True


def normalise_entry_status(status: Any) -> str:
    """Return a valid race-entry status, defaulting to RACING."""
    value = str(status or "").strip().upper()
    allowed = {"RACING", "FINISHED", "DNS", "DNC", "OCS", "RET", "DNF", "DSQ", "DNE", "DGM"}
    return value if value in allowed else "RACING"


def series_race_order_key(race: sqlite3.Row) -> Tuple[str, int]:
    """Return the ordering key used for deciding earlier/later series races."""
    return (str(row_get(race, "start_time") or row_get(race, "created_at") or ""), int(row_get(race, "id") or 0))


def series_entry_status_for_target(target_race: sqlite3.Row, source_race: Optional[sqlite3.Row] = None) -> str:
    """Return DNC for races earlier than the race where a boat joined the series.

    When the RO adds a new competitor from Race 3 of a series, earlier races
    should show that boat as DNC rather than RACING.  The joining race and any
    later races remain RACING so they are ready to finish/score normally.
    """
    if source_race is not None and series_race_order_key(target_race) < series_race_order_key(source_race):
        return "DNC"
    return "RACING"


def series_races_for_sync(db: sqlite3.Connection, series_id: Optional[int]) -> List[sqlite3.Row]:
    """Return races in a series for entry synchronisation, in series order.

    Every race in the series is synced, so the order changes nothing here -- but
    "series order" now means one specific thing (core.races.races_in_order), and
    a second definition of it sitting in this file is a trap for whoever reads
    the phrase next.
    """
    if not series_id:
        return []
    rows = db.execute(
        "SELECT * FROM races WHERE series_id = ? ORDER BY id", (series_id,)
    ).fetchall()
    return races_in_order(rows)


def source_race_for_series_sync(db: sqlite3.Connection, source_race_id: Optional[int]) -> Optional[sqlite3.Row]:
    """Load the race where a competitor was added to a series, if known."""
    if not source_race_id:
        return None
    return db.execute("SELECT * FROM races WHERE id = ?", (int(source_race_id),)).fetchone()


def add_boat_database_entry_to_series_races(
    db: sqlite3.Connection,
    series_id: Optional[int],
    boat: sqlite3.Row,
    class_override: str = "",
    rating_source: str = "AUTO",
    source_race_id: Optional[int] = None,
) -> int:
    """Add a boat-database entry to every race in a series.

    If source_race_id is supplied, races earlier than that race receive DNC so
    late-joining competitors do not appear as still racing in races that have
    already happened.
    """
    added = 0
    source_race = source_race_for_series_sync(db, source_race_id)
    for target_race in series_races_for_sync(db, series_id):
        status = series_entry_status_for_target(target_race, source_race)
        if add_boat_database_entry_to_race(db, target_race, boat, class_override, rating_source, status=status):
            added += 1
    return added


def add_manual_entry_to_series_races(
    db: sqlite3.Connection,
    series_id: Optional[int],
    boat_name: str,
    sail_no: str,
    class_name: str,
    irc_rating: Optional[float],
    ytc_rating: Optional[float],
    source_race_id: Optional[int] = None,
) -> int:
    """Add a manual entry to every race in a series.

    If source_race_id is supplied, races earlier than that race receive DNC so
    late-joining competitors do not appear as still racing in races that have
    already happened.
    """
    added = 0
    source_race = source_race_for_series_sync(db, source_race_id)
    for target_race in series_races_for_sync(db, series_id):
        status = series_entry_status_for_target(target_race, source_race)
        if add_manual_entry_to_race(db, target_race, boat_name, sail_no, class_name, irc_rating, ytc_rating, status=status):
            added += 1
    return added


def sync_series_entries_into_race(db: sqlite3.Connection, race_id: int, series_id: Optional[int]) -> int:
    """Populate a race with all competitors already present elsewhere in its series.

    Boat-database entries are snapshotted from the current boat database rating
    when they are inserted into the new race.  Manual/race-only entries copy the
    per-race rating fields from the first existing series entry found.
    """
    if not series_id:
        return 0
    target_race = db.execute("SELECT * FROM races WHERE id = ?", (race_id,)).fetchone()
    if not target_race:
        return 0
    existing_entries = db.execute(
        """
        SELECT e.*
        FROM entries e
        JOIN races r ON r.id = e.race_id
        WHERE r.series_id = ? AND e.race_id <> ?
        ORDER BY e.boat_id IS NULL, e.boat_name COLLATE NOCASE, e.id
        """,
        (series_id, race_id),
    ).fetchall()
    added = 0
    seen_boat_ids: set = set()
    seen_manual_keys: set = set()
    for entry in existing_entries:
        if entry["boat_id"] is not None:
            boat_id = int(entry["boat_id"])
            if boat_id in seen_boat_ids:
                continue
            seen_boat_ids.add(boat_id)
            boat = db.execute("SELECT * FROM boats WHERE id = ?", (boat_id,)).fetchone()
            if boat and add_boat_database_entry_to_race(db, target_race, boat, entry["class_name"] or "", "AUTO"):
                added += 1
        else:
            key = (str(entry["boat_name"] or "").strip().upper(), str(entry["sail_no"] or "").strip().upper())
            if key in seen_manual_keys:
                continue
            seen_manual_keys.add(key)
            if add_manual_entry_to_race(
                db,
                target_race,
                entry["boat_name"],
                entry["sail_no"] or "",
                entry["class_name"] or "",
                entry["manual_irc_rating"] if "manual_irc_rating" in entry.keys() else None,
                entry["manual_ytc_rating"] if "manual_ytc_rating" in entry.keys() else None,
                source_race_id=race_id,
            ):
                added += 1
    return added


def sync_race_entries_to_series(db: sqlite3.Connection, race_id: int, series_id: Optional[int]) -> int:
    """Ensure every entry in one race also exists in all other races in the series."""
    if not series_id:
        return 0
    entries = db.execute("SELECT * FROM entries WHERE race_id = ? ORDER BY id", (race_id,)).fetchall()
    added = 0
    for entry in entries:
        if entry["boat_id"] is not None:
            boat = db.execute("SELECT * FROM boats WHERE id = ?", (int(entry["boat_id"]),)).fetchone()
            if boat:
                added += add_boat_database_entry_to_series_races(db, series_id, boat, entry["class_name"] or "", "AUTO", source_race_id=race_id)
        else:
            added += add_manual_entry_to_series_races(
                db,
                series_id,
                entry["boat_name"],
                entry["sail_no"] or "",
                entry["class_name"] or "",
                entry["manual_irc_rating"] if "manual_irc_rating" in entry.keys() else None,
                entry["manual_ytc_rating"] if "manual_ytc_rating" in entry.keys() else None,
                source_race_id=race_id,
            )
    return added


def add_active_boats_to_race(db: sqlite3.Connection, race_id: int, race_class_name: str, rating_rule: str, rating_source: str = "AUTO", status: str = "RACING") -> int:
    """Add every active boat in the database to a race if not already entered."""
    race = db.execute("SELECT * FROM races WHERE id = ?", (race_id,)).fetchone()
    if not race:
        return 0
    boats = db.execute("SELECT * FROM boats WHERE status = 'ACTIVE' ORDER BY boat_name COLLATE NOCASE").fetchall()
    added = 0
    for boat in boats:
        if add_boat_database_entry_to_race(db, race, boat, race_class_name or "", rating_source, status=status):
            added += 1
    return added


def add_active_boats_to_series_races(
    db: sqlite3.Connection,
    series_id: Optional[int],
    source_race_id: Optional[int],
    race_class_name: str,
    rating_rule: str,
    rating_source: str = "AUTO",
) -> int:
    """Add active boats to each race in a series, DNC in races before source."""
    added = 0
    source_race = source_race_for_series_sync(db, source_race_id)
    for target_race in series_races_for_sync(db, series_id):
        status = series_entry_status_for_target(target_race, source_race)
        added += add_active_boats_to_race(
            db,
            int(target_race["id"]),
            race_class_name or target_race["class_name"] or "",
            rating_rule or target_race["rating_rule"],
            rating_source=rating_source,
            status=status,
        )
    return added



# ---------------------------------------------------------------------------
# Settings parsing and persisted app configuration
# ---------------------------------------------------------------------------
