"""A race keeps its tracks after its trackers are removed.

Reported from the hut: the simulated trackers were deleted on the Trackers page,
and the race they had sailed then showed no tracks at all — no boats on the
chart, no progress, nothing to replay. The expectation was that the track stayed
with the boat, and that expectation was right; `remove_tracker` says so in as
many words, and the fixes were never touched.

What was thrown away was the *thread back to them*. Every stored fix carries the
boat it belonged to, and `positions_for_entry_since` resolves a boat's track by
that. But the chart, the progress walk and the replay all ask a cheaper question
first — "is this entry tracked?" — and that was answered from the trackers table,
which is exactly the row being deleted. So the code refused to go and look for
positions that were sitting right there.

The question is now answered from the boat's own history as a last resort: the
device it was last seen on, whether or not it still has one.
"""
from __future__ import annotations

import time

import pytest

import app as ro
import core.track as track


NOW = time.time()
START = NOW - 3600
STAMP = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(START))


@pytest.fixture
def sailed_race(client):
    """A race sailed an hour ago by one boat, with its tracker still fitted."""
    with ro.app.app_context():
        with ro.get_db() as db:
            cur = db.execute("INSERT INTO boats (boat_name, sail_no, status,"
                             " created_at, updated_at)"
                             " VALUES ('Sim One', 'SIM1', 'ACTIVE', ?, ?)", (STAMP, STAMP))
            boat_id = int(cur.lastrowid)
            cur = db.execute("INSERT INTO races (name, course_no, start_time, created_at)"
                             " VALUES ('Simulated', 1, ?, ?)", (STAMP, STAMP))
            race_id = int(cur.lastrowid)
            db.execute("INSERT INTO entries (race_id, boat_id, boat_name, sail_no, status)"
                       " VALUES (?, ?, 'Sim One', 'SIM1', 'RACING')", (race_id, boat_id))
            db.execute("INSERT INTO trackers (unique_id, boat_id, active)"
                       " VALUES ('SIM-1', ?, 1)", (boat_id,))
            db.commit()
        track.init_track_db()
        with track.get_track_db() as tdb:
            for i in range(20):
                tdb.execute(
                    "INSERT INTO track_positions (unique_id, lat, lon, fix_time, server_time,"
                    " boat_id, speed_kn, course_deg)"
                    " VALUES ('SIM-1', ?, ?, ?, ?, ?, 5.0, 90)",
                    (52.88 + i * 0.0005, -4.40, START + i * 30, START + i * 30, boat_id))
            tdb.commit()
        return race_id, boat_id


def entry_of(race_id):
    with ro.app.app_context():
        return ro.get_entries(race_id)[0]


def remove_the_tracker():
    with ro.app.app_context():
        return track.remove_tracker("SIM-1")


class TestBeforeTheTrackerIsRemoved:
    def test_the_entry_resolves_to_its_tracker(self, sailed_race):
        assert track.effective_tracker_for_entry(entry_of(sailed_race[0])) == "SIM-1"

    def test_and_the_track_is_there(self, sailed_race):
        fixes = track.positions_for_entry_since(entry_of(sailed_race[0]), START - 60)
        assert len(fixes) == 20


class TestAfterTheTrackerIsRemoved:
    def test_the_tracker_row_really_is_gone(self, sailed_race):
        ok, _ = remove_the_tracker()
        assert ok
        with ro.app.app_context():
            with ro.get_db() as db:
                assert db.execute("SELECT COUNT(*) n FROM trackers").fetchone()["n"] == 0

    def test_the_fixes_are_kept(self, sailed_race):
        """This part always worked, and is what made the loss so confusing."""
        remove_the_tracker()
        with track.get_track_db() as tdb:
            assert tdb.execute("SELECT COUNT(*) n FROM track_positions").fetchone()["n"] == 20

    def test_the_entry_still_knows_it_was_tracked(self, sailed_race):
        """The gate every chart and replay checks first. Without this it went
        false and nothing else was even attempted."""
        remove_the_tracker()
        assert track.effective_tracker_for_entry(entry_of(sailed_race[0])) == "SIM-1"

    def test_and_the_race_still_has_its_track(self, sailed_race):
        remove_the_tracker()
        fixes = track.positions_for_entry_since(entry_of(sailed_race[0]), START - 60)
        assert len(fixes) == 20

    def test_the_replay_window_still_covers_the_race(self, sailed_race):
        """race_track_window gates on the same question; with it false the window
        collapsed to nothing and there was no replay to scrub through."""
        race_id, _ = sailed_race
        remove_the_tracker()
        with ro.app.app_context():
            window = track.race_track_window(ro.get_race(race_id))
        assert window is not None
        assert window[1] - window[0] > 300


class TestItDoesNotInventTracks:
    def test_a_boat_that_never_reported_is_still_untracked(self, client):
        """The fallback answers from stored fixes, so a boat with none stays
        untracked — it must not start claiming a track that does not exist."""
        with ro.app.app_context():
            with ro.get_db() as db:
                cur = db.execute("INSERT INTO boats (boat_name, sail_no, status,"
                                 " created_at, updated_at)"
                                 " VALUES ('Never', 'N1', 'ACTIVE', ?, ?)", (STAMP, STAMP))
                boat_id = int(cur.lastrowid)
                cur = db.execute("INSERT INTO races (name, course_no, start_time, created_at)"
                                 " VALUES ('Empty', 1, '2026-06-01T13:00:00', ?)", (STAMP,))
                race_id = int(cur.lastrowid)
                db.execute("INSERT INTO entries (race_id, boat_id, boat_name, sail_no, status)"
                           " VALUES (?, ?, 'Never', 'N1', 'RACING')", (race_id, boat_id))
                db.commit()
        assert track.effective_tracker_for_entry(entry_of(race_id)) is None

    def test_an_entry_with_no_boat_is_untracked(self, client):
        assert track.last_tracker_seen_for_boat(None) is None

    def test_a_loaner_override_still_wins(self, sailed_race):
        """A per-race loaner is the most specific answer there is and must not be
        second-guessed by the boat's history."""
        race_id, _ = sailed_race
        with ro.app.app_context():
            with ro.get_db() as db:
                db.execute("UPDATE entries SET tracker_unique_id = 'LOANER-9'"
                           " WHERE race_id = ?", (race_id,))
                db.commit()
        assert track.effective_tracker_for_entry(entry_of(race_id)) == "LOANER-9"

    def test_a_refitted_tracker_wins_over_the_old_one(self, sailed_race):
        """The current tracker is checked before the history, so a boat given a
        new device does not keep resolving to the one it used to have."""
        _, boat_id = sailed_race
        with ro.app.app_context():
            with ro.get_db() as db:
                db.execute("UPDATE trackers SET unique_id = 'NEW-7' WHERE boat_id = ?",
                           (boat_id,))
                db.commit()
        assert track.effective_tracker_for_entry(entry_of(sailed_race[0])) == "NEW-7"
