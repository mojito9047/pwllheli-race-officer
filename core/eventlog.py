"""Race event log: horn/audio/finish events recorded per race.

Extracted verbatim from app.py. Simple SQLite-backed event log used by the
start console, horn hardware and central automation, plus the JSON shaping for
live polling and the rule for which events may be assigned as finish times.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any, Dict, List, Optional

from core.db import get_db, init_db
from core.timeutils import dt_full_display

def log_event(race_id: Optional[int], event_type: str, label: str = "", source: str = "manual", details: Optional[Dict[str, Any]] = None) -> int:
    """Write a race or hardware event to the event log."""
    init_db()
    with get_db() as db:
        cur = db.execute(
            "INSERT INTO race_events (race_id, event_time, event_type, label, source, details) VALUES (?, ?, ?, ?, ?, ?)",
            (race_id, datetime.now().isoformat(timespec="seconds"), event_type, label, source, json.dumps(details or {})),
        )
        db.commit()
        return int(cur.lastrowid)


def get_events(race_id: Optional[int] = None, limit: Optional[int] = 30) -> List[sqlite3.Row]:
    """Load recent race/hardware events for display.

    ``limit=None`` returns them all, which is what the race sheet's own log
    wants: it is the record of one race, and twenty rows silently hid the start
    of the very sequence somebody was looking at. The dashboard's list, which is
    every race at once, still asks for a handful.
    """
    init_db()
    with get_db() as db:
        if race_id is None:
            if limit is None:
                return db.execute("SELECT * FROM race_events ORDER BY event_time DESC, id DESC").fetchall()
            return db.execute("SELECT * FROM race_events ORDER BY event_time DESC, id DESC LIMIT ?", (limit,)).fetchall()
        if limit is None:
            return db.execute("SELECT * FROM race_events WHERE race_id = ? ORDER BY event_time DESC, id DESC", (race_id,)).fetchall()
        return db.execute("SELECT * FROM race_events WHERE race_id = ? ORDER BY event_time DESC, id DESC LIMIT ?", (race_id, limit)).fetchall()


def get_events_after(race_id: int, after_id: int = 0, limit: int = 30) -> List[sqlite3.Row]:
    """Load race events newer than a known event id in chronological order."""
    init_db()
    after_id = max(0, int(after_id or 0))
    limit = max(1, min(100, int(limit or 30)))
    with get_db() as db:
        return db.execute(
            "SELECT * FROM race_events WHERE race_id = ? AND id > ? ORDER BY id ASC LIMIT ?",
            (race_id, after_id, limit),
        ).fetchall()


def event_for_json(ev: sqlite3.Row) -> Dict[str, Any]:
    """Return a race event in the shape used by the live log pollers."""
    return {
        "id": int(ev["id"]),
        "race_id": ev["race_id"],
        "event_time": ev["event_time"],
        "event_time_display": dt_full_display(ev["event_time"]),
        "event_type": ev["event_type"],
        "label": ev["label"] or "",
        "source": ev["source"] or "",
        "assignable_finish": is_finish_assignable_event(ev),
    }


def is_finish_assignable_event(ev: Any) -> bool:
    """Only manual horn activations may be assigned as a boat finish.

    Auto-sequence horns, recalls, postponements and abandonments deliberately
    cannot be used as finishes from the race log.  The manual serial input is
    also treated as a manual horn activation because it represents the RO's
    physical horn button.
    """
    if not ev:
        return False
    try:
        event_type = str(ev["event_type"] or "").strip().lower()
        source = str(ev["source"] or "").strip().lower()
        label = str(ev["label"] or "").strip().lower()
    except Exception:
        event_type = str(getattr(ev, "event_type", "") or "").strip().lower()
        source = str(getattr(ev, "source", "") or "").strip().lower()
        label = str(getattr(ev, "label", "") or "").strip().lower()
    if event_type == "manual-horn-input":
        return True
    if event_type == "horn" and source == "manual" and label.startswith("manual horn"):
        return True
    return False



# ---------------------------------------------------------------------------
# Authentication, public access, template helpers and Flask routes
# ---------------------------------------------------------------------------
