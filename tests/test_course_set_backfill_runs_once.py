"""The migration that marked old races as having a course ran for ever.

``course_set`` arrived to tell a race's **fallback** course number apart from a
course somebody actually chose. Every race that existed when the column was added
predated the distinction, so a back-fill marked them chosen — reasonably, on the
evidence available: *a race with a start time has a course*.

It was written inside ``_init_db_uncached``, which runs on every request, and it
had nothing telling it to stop. So it kept applying that rule to races created
long afterwards, for which it is false. Setting the first warning signal before
choosing the course is an ordinary thing to do on a race morning; the next
restart then marked that race as having a course nobody had picked, and the
course board, the chart and the competitor page all went back to showing the
fallback. The whole point of the column, undone by its own migration.

It only survived this long because ``init_db`` is cached per process — the
back-fill ran at startup, so a race created afterwards kept its state until the
app was next restarted, which on the hut PC might be a week later. That is a
worse failure than an immediate one: it works all afternoon and is wrong the next
morning.

``ensure_column`` now reports whether it was the call that added the column, and
the back-fill runs only then.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

import app as ro
from core.db import ensure_column


def _race(name, start_time, course_set=0):
    from core.db import get_db
    now = datetime.now().isoformat(timespec="seconds")
    with get_db() as db:
        cur = db.execute(
            "INSERT INTO races (name, course_no, course_set, start_time, notes, created_at)"
            " VALUES (?,1,?,?,'',?)", (name, course_set, start_time, now))
        db.commit()
        return int(cur.lastrowid)


def _course_set(race_id):
    from core.db import get_db
    with get_db() as db:
        return int(db.execute("SELECT course_set FROM races WHERE id = ?",
                              (race_id,)).fetchone()[0])


class TestEnsureColumnReportsWhatItDid:
    def test_it_says_true_when_it_adds_one(self, client):
        from core.db import get_db
        with get_db() as db:
            db.execute("CREATE TABLE t_once (id INTEGER PRIMARY KEY)")
            assert ensure_column(db, "t_once", "added", "INTEGER") is True

    def test_and_false_when_it_was_already_there(self, client):
        from core.db import get_db
        with get_db() as db:
            db.execute("CREATE TABLE t_twice (id INTEGER PRIMARY KEY)")
            ensure_column(db, "t_twice", "added", "INTEGER")
            assert ensure_column(db, "t_twice", "added", "INTEGER") is False

    def test_the_column_is_there_either_way(self, client):
        from core.db import get_db
        from core.db import table_columns
        with get_db() as db:
            db.execute("CREATE TABLE t_three (id INTEGER PRIMARY KEY)")
            ensure_column(db, "t_three", "added", "INTEGER")
            ensure_column(db, "t_three", "added", "INTEGER")
            assert "added" in table_columns(db, "t_three")


class TestARaceKeepsItsUnchosenCourse:
    def test_a_start_time_does_not_make_it_chosen(self, client):
        """The reported shape: a first warning signal set before the course."""
        when = (datetime.now() + timedelta(hours=2)).isoformat(timespec="seconds")
        rid = _race("Club Race", when, course_set=0)
        ro._init_db_uncached()
        assert _course_set(rid) == 0

    def test_not_even_after_several_restarts(self, client):
        """init_db is cached per process, so the old back-fill fired at startup:
        the race was fine all afternoon and wrong the next morning."""
        when = (datetime.now() + timedelta(hours=2)).isoformat(timespec="seconds")
        rid = _race("Club Race", when, course_set=0)
        for _ in range(3):
            ro._init_db_uncached()
        assert _course_set(rid) == 0

    def test_a_race_with_no_start_time_is_untouched_too(self, client):
        rid = _race("Club Race", "", course_set=0)
        ro._init_db_uncached()
        assert _course_set(rid) == 0

    def test_and_a_chosen_course_stays_chosen(self, client):
        """It only ever went one way, and still does."""
        when = (datetime.now() + timedelta(hours=2)).isoformat(timespec="seconds")
        rid = _race("Club Race", when, course_set=1)
        ro._init_db_uncached()
        assert _course_set(rid) == 1


class TestTheBackfillStillHappensWhenItShould:
    def test_an_old_database_gets_its_races_marked(self, client):
        """A database from before the column: every race in it predates the
        distinction, so marking the ones with a start time is right. This is the
        case the migration exists for, and dropping the column and re-adding it
        is the closest a test can get to it."""
        from core.db import get_db
        when = (datetime.now() + timedelta(hours=2)).isoformat(timespec="seconds")
        rid = _race("Old Race", when, course_set=0)
        with get_db() as db:
            db.execute("ALTER TABLE races DROP COLUMN course_set")
            db.commit()
        ro._init_db_uncached()
        assert _course_set(rid) == 1

    def test_but_one_with_no_start_time_is_left_alone(self, client):
        """Nothing about it suggests anybody chose a course."""
        from core.db import get_db
        rid = _race("Old Race", "", course_set=0)
        with get_db() as db:
            db.execute("ALTER TABLE races DROP COLUMN course_set")
            db.commit()
        ro._init_db_uncached()
        assert _course_set(rid) == 0
