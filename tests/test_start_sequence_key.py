"""One version of a start sequence has one key, and a different version does not.

`start_sequence_key` is what lets the scheduler tell "the warning signal I
already sounded" from "the warning signal for the time the race officer has
just moved it to". Its body was lost in the refactor that moved the scheduler
out of app.py -- the extraction kept the first line and dropped the rest -- so
it returned None for every race and every version of every race.

It failed quietly, which is why it lasted: `reset_start_sequence_state_for_race`
clears the in-memory keys on every edit, and the event-log guard falls back to
matching the scheduled time within five seconds. Between them the horns kept
working. What was gone was the guarantee the docstring makes, and the margin: a
race edited so that a new signal lands within five seconds of an old one had
nothing left to tell them apart.

Found while adding AP, because postponing is the case the key exists for.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.startsequence import start_sequence_key  # noqa: E402


def race_row(**over):
    """A race as a sqlite3.Row, which is what the scheduler is handed."""
    fields = {"id": 1, "start_time": "2026-08-16T13:55:00", "course_no": 4,
              "custom_course_json": None, "start_plan_json": None}
    fields.update(over)
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    cols = ", ".join(fields)
    db.execute(f"CREATE TABLE races ({cols})")
    db.execute(f"INSERT INTO races VALUES ({', '.join('?' * len(fields))})",
               tuple(fields.values()))
    return db.execute("SELECT * FROM races").fetchone()


class TestItIsActuallyAKey:
    def test_it_returns_one(self):
        """It returned None for two hundred releases."""
        key = start_sequence_key(race_row())
        assert isinstance(key, str) and key, "start_sequence_key returned nothing"

    def test_the_same_race_twice_is_the_same_key(self):
        assert start_sequence_key(race_row()) == start_sequence_key(race_row())


class TestADifferentVersionIsADifferentKey:
    """Each of these is a reason a signal already sounded must not count as
    having sounded for what the race is now."""

    def test_moving_the_warning_signal_changes_it(self):
        """The postponement case, and the one the docstring promises."""
        before = start_sequence_key(race_row(start_time="2026-08-16T13:55:00"))
        after = start_sequence_key(race_row(start_time="2026-08-16T14:25:00"))
        assert before != after

    def test_a_different_course_changes_it(self):
        assert start_sequence_key(race_row(course_no=4)) != start_sequence_key(race_row(course_no=7))

    def test_a_made_up_course_changes_it(self):
        plain = start_sequence_key(race_row())
        custom = start_sequence_key(race_row(custom_course_json='{"marks": [{"mark": "O"}]}'))
        assert plain != custom

    def test_editing_the_made_up_course_changes_it(self):
        one = start_sequence_key(race_row(custom_course_json='{"marks": [{"mark": "O"}]}'))
        two = start_sequence_key(race_row(custom_course_json='{"marks": [{"mark": "4"}]}'))
        assert one != two

    def test_a_different_start_plan_changes_it(self):
        """Two classes starting ten minutes apart is a different signal plan
        from both starting together, on the same course at the same time."""
        one = start_sequence_key(race_row(start_plan_json='{"starts": [{"name": "Start 1"}]}'))
        two = start_sequence_key(race_row(
            start_plan_json='{"starts": [{"name": "Start 1"}, {"name": "Start 2"}]}'))
        assert one != two

    def test_a_different_race_changes_it(self):
        assert start_sequence_key(race_row(id=1)) != start_sequence_key(race_row(id=2))


class TestItSurvivesAnOlderDatabase:
    def test_a_race_row_without_the_newer_columns_still_keys(self):
        """`row_get` exists because older databases lack columns added later;
        a key that raised here would stop the horn entirely."""
        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
        db.execute("CREATE TABLE races (id, start_time, course_no)")
        db.execute("INSERT INTO races VALUES (1, '2026-08-16T13:55:00', 4)")
        row = db.execute("SELECT * FROM races").fetchone()
        assert start_sequence_key(row)
