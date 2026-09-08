"""Tests for the PRESTART display status (core.races.entry_display_status).

A boat is stored as RACING from the moment it is entered, because elapsed time,
results, GPS finish detection and the "still racing" counts all key off RACING.
For display it should read PRESTART until the race's first warning signal.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import app as ro
from core.races import entry_display_status


def _race(minutes_from_now, name="Status Race"):
    """Create a race whose first warning signal is N minutes from now."""
    warning = (datetime.now() + timedelta(minutes=minutes_from_now)).isoformat(timespec="seconds")
    with ro.get_db() as db:
        cur = db.execute(
            "INSERT INTO races (name, class_name, course_no, start_time, rating_rule, notes, created_at) "
            "VALUES (?, 'IRC', ?, ?, 'DUAL', '', ?)",
            (name, ro.appstate.COURSES[0]["course_no"], warning,
             datetime.now().isoformat(timespec="seconds")),
        )
        db.commit()
        return db.execute("SELECT * FROM races WHERE id = ?", (int(cur.lastrowid),)).fetchone()


def _entry(race_id, status="RACING"):
    with ro.get_db() as db:
        cur = db.execute(
            "INSERT INTO entries (race_id, boat_name, sail_no, status) VALUES (?, 'Boat', 'X1', ?)",
            (race_id, status),
        )
        db.commit()
        return db.execute("SELECT * FROM entries WHERE id = ?", (int(cur.lastrowid),)).fetchone()


class TestPrestartStatus:
    def test_prestart_before_the_warning_signal(self, client):
        race = _race(30)                    # warning signal in 30 minutes
        entry = _entry(race["id"])
        assert entry["status"] == "RACING"   # stored status is unchanged
        assert entry_display_status(entry, race) == "PRESTART"

    def test_racing_after_the_warning_signal(self, client):
        race = _race(-10)                   # warning signal 10 minutes ago
        entry = _entry(race["id"])
        assert entry_display_status(entry, race) == "RACING"

    def test_other_statuses_are_untouched(self, client):
        race = _race(30)                    # even before the start
        for status in ("FINISHED", "DNF", "OCS", "RET", "DNS"):
            entry = _entry(race["id"], status=status)
            assert entry_display_status(entry, race) == status

    def test_no_warning_time_is_prestart(self, client):
        race = _race(30)
        with ro.get_db() as db:
            db.execute("UPDATE races SET start_time = '' WHERE id = ?", (race["id"],))
            db.commit()
            race = db.execute("SELECT * FROM races WHERE id = ?", (race["id"],)).fetchone()
        entry = _entry(race["id"])
        assert entry_display_status(entry, race) == "PRESTART"

    def test_shown_on_the_public_race_page(self, client):
        race = _race(30)
        _entry(race["id"])
        html = client.get(f"/public/race/{race['id']}").get_data(as_text=True)
        assert "PRESTART" in html
