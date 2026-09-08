"""A course nobody set is not a course to sail round.

Reported from a race whose own header read *Course: not set yet*, with MOJITO
listed at **0/7 marks, next 1, 5.05 nm to go**. Every one of those numbers is
real arithmetic — against course 1, which nobody picked.

``course_no`` defaults to 1 and ``course_for_race`` hands that back as a
fallback "so the geometry has something to work with". v0.276 drew the line for
the race sheet: *no board, no chart line, no predicted time and nothing selected
in the picker until somebody has chosen a course*, and the spoken announcement
refuses on the same ground, because reading out a guessed course would send the
fleet the wrong way. The rounding sequence was the piece nobody asked, and three
things read it: the fleet list, the replay, and **GPS finish detection** — which
was walking a boat towards marks of its own invention and watching for it to
cross the line.

The guard belongs in ``course_rounding_sequence`` rather than in each of the
three, because two copies of one rule is this codebase's recurring bug.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta

import app as ro
import core.track as track

from tests.test_race_replay import _seed_race, _track


def _set_course(race_id, on):
    from core.db import get_db
    with get_db() as db:
        db.execute("UPDATE races SET course_set = ? WHERE id = ?", (1 if on else 0, race_id))
        db.commit()


def _race(client, *, course_set, minutes_ago=60):
    race_id, ids, gun = _seed_race(client, minutes_ago=minutes_ago)
    _set_course(race_id, course_set)
    return race_id, ids, gun


def _row(race_id, boat_name):
    return next(r for r in track.race_leaderboard(race_id) if r["boat_name"] == boat_name)


class TestTheSequenceIsEmptyUntilACourseIsChosen:
    def test_no_marks_to_round(self, client):
        from core.db import get_db
        race_id, ids, gun = _race(client, course_set=False)
        with get_db() as db:
            race = db.execute("SELECT * FROM races WHERE id = ?", (race_id,)).fetchone()
        assert track.course_rounding_sequence(race) == []

    def test_and_the_fallback_course_still_has_one(self, client):
        """The guard must be the flag, not a broken course lookup."""
        from core.db import get_db
        race_id, ids, gun = _race(client, course_set=True)
        with get_db() as db:
            race = db.execute("SELECT * FROM races WHERE id = ?", (race_id,)).fetchone()
        assert len(track.course_rounding_sequence(race)) > 1


class TestTheFleetListSaysNothingItCannotKnow:
    def test_no_marks_count(self, client):
        """The reported row: 0/7 against a course nobody picked."""
        race_id, ids, gun = _race(client, course_set=False)
        _track("SIMR-Alpha", ids["Alpha"]["boat_id"], [(time.time() - 30, 52.88, -4.40)])
        row = _row(race_id, "Alpha")
        assert row["total"] == 0
        assert row["rounded"] in (0, None)

    def test_no_next_mark(self, client):
        race_id, ids, gun = _race(client, course_set=False)
        _track("SIMR-Alpha", ids["Alpha"]["boat_id"], [(time.time() - 30, 52.88, -4.40)])
        assert _row(race_id, "Alpha")["next_mark"] is None

    def test_no_distance_to_go(self, client):
        """5.05 nm to a mark the race officer never chose."""
        race_id, ids, gun = _race(client, course_set=False)
        _track("SIMR-Alpha", ids["Alpha"]["boat_id"], [(time.time() - 30, 52.88, -4.40)])
        assert _row(race_id, "Alpha")["dist_remaining_nm"] is None

    def test_but_the_boat_is_still_where_it_is(self, client):
        """Position, speed and fix age are the tracker's own report and owe
        nothing to a course. Only what is *derived* from a course goes."""
        race_id, ids, gun = _race(client, course_set=False)
        _track("SIMR-Alpha", ids["Alpha"]["boat_id"], [(time.time() - 30, 52.88, -4.40)])
        row = _row(race_id, "Alpha")
        assert row["lat"] is not None and row["lon"] is not None
        assert row["age"] is not None
        assert row["tracked"] is True and row["tracker_assigned"] is True

    def test_and_a_set_course_is_untouched(self, client):
        race_id, ids, gun = _race(client, course_set=True)
        _track("SIMR-Alpha", ids["Alpha"]["boat_id"], [(time.time() - 30, 52.88, -4.40)])
        row = _row(race_id, "Alpha")
        assert row["total"] > 0
        assert row["next_mark"] is not None


class TestThePagesDoNotPrintZeroOfZero:
    import pathlib as _pathlib
    _ROOT = _pathlib.Path(__file__).resolve().parent.parent
    COMP = (_ROOT / "templates" / "competitor_race.html").read_text(encoding="utf-8")
    RACE = (_ROOT / "templates" / "race.html").read_text(encoding="utf-8")

    def test_the_competitor_page_dashes_it(self):
        assert "b.total ? (b.rounded + '/' + b.total) : '—'" in self.COMP

    def test_the_race_sheet_dashes_it(self):
        assert "b.total ? `${b.rounded}/${b.total}` : '—'" in self.RACE
