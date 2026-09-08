"""The chart must keep updating while anyone is still racing.

Reported after a night race: the chart stopped updating not long after the first
boat finished. The second boat still finished — the race officer saw it recorded
— but never appeared on the chart, and the next morning every track was there in
full.

All three of those are the same fault. ``race_track_window`` took the *maximum
finish time among boats that had finished*, which with one boat home is simply
that boat's finish, and ended the window a couple of minutes later. Everything the
chart asks for is bounded by that window, so it froze. Finish detection is not:
it walks ``positions_for_entry_since(entry, not_before)`` with no upper bound,
which is why the second boat still finished. And by the next morning the whole
fleet was in, so the window ran to the genuine last finish and showed everything.

There was a second effect on top. Once the viewer's ``since`` passed the frozen
end, ``incremental`` went false and every poll re-sent the entire race — to every
phone watching, from a hut PC that was already short of connections.
"""
from __future__ import annotations

import time

import pytest

import app as ro
from core import track

# The gun, two hours back. It was a literal 2026-08-08 once, which quietly aged:
# by the time REPLAY_MAX_WINDOW_S arrived every race here had been "in progress"
# for weeks, and the tests below -- one boat home half an hour ago, one still out
# -- were describing a race a month long. The sibling track tests already stamp
# relative to now for the same reason.
STAMP = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - 7200))


def make_race(client):
    with ro.app.app_context():
        with ro.get_db() as db:
            cur = db.execute(
                "INSERT INTO races (name, course_no, start_time, created_at)"
                " VALUES ('Night race', 1, ?, ?)", (STAMP, STAMP))
            db.commit()
            return int(cur.lastrowid)


def add_entry(race_id, name, uid, *, status="RACING", finish_time=None):
    with ro.app.app_context():
        with ro.get_db() as db:
            cur = db.execute(
                "INSERT INTO entries (race_id, boat_name, sail_no, status, finish_time,"
                " tracker_unique_id) VALUES (?,?,?,?,?,?)",
                (race_id, name, name, status, finish_time, uid))
            db.commit()
            return int(cur.lastrowid)


def race_of(race_id):
    with ro.app.app_context():
        return ro.get_race(race_id)


class TestTheWindowStaysOpen:
    def test_one_finisher_does_not_close_it_on_the_fleet(self, client):
        """The reported case: first boat home, second still sailing."""
        rid = make_race(client)
        finished_at = time.strftime("%Y-%m-%dT%H:%M:%S",
                                    time.localtime(time.time() - 1800))
        add_entry(rid, "First", "NR-1", status="FINISHED", finish_time=finished_at)
        add_entry(rid, "Second", "NR-2", status="RACING")

        window = track.race_track_window(race_of(rid))
        assert window is not None
        start_ts, end_ts = window
        finish_ts = time.mktime(time.strptime(finished_at, "%Y-%m-%dT%H:%M:%S"))
        assert end_ts > finish_ts + track.REPLAY_POST_FINISH_S, (
            "the window closed a couple of minutes after the first finish while a "
            "boat was still racing — that is the chart freezing"
        )

    def test_it_reaches_about_now_while_a_boat_races(self, client):
        rid = make_race(client)
        finished_at = time.strftime("%Y-%m-%dT%H:%M:%S",
                                    time.localtime(time.time() - 1800))
        add_entry(rid, "First", "NR-1", status="FINISHED", finish_time=finished_at)
        add_entry(rid, "Second", "NR-2", status="RACING")
        _, end_ts = track.race_track_window(race_of(rid))
        assert abs(end_ts - time.time()) < 120

    def test_it_closes_once_the_fleet_is_in(self, client):
        """The behaviour the window is for: no dead tail after the last boat."""
        rid = make_race(client)
        first = time.time() - 3600
        last = time.time() - 1800
        add_entry(rid, "First", "NR-1", status="FINISHED",
                  finish_time=time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(first)))
        add_entry(rid, "Second", "NR-2", status="FINISHED",
                  finish_time=time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(last)))
        _, end_ts = track.race_track_window(race_of(rid))
        assert abs(end_ts - (last + track.REPLAY_POST_FINISH_S)) < 5, (
            "with everyone home it should end just after the last finish"
        )

    def test_a_retired_boat_does_not_hold_it_open(self, client):
        """RET/DNF are not still racing; only RACING is."""
        rid = make_race(client)
        last = time.time() - 1800
        add_entry(rid, "First", "NR-1", status="FINISHED",
                  finish_time=time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(last)))
        add_entry(rid, "Retired", "NR-2", status="RET")
        _, end_ts = track.race_track_window(race_of(rid))
        assert abs(end_ts - (last + track.REPLAY_POST_FINISH_S)) < 5

    def test_no_finishes_yet_still_runs_to_the_present(self, client):
        rid = make_race(client)
        add_entry(rid, "First", "NR-1", status="RACING")
        _, end_ts = track.race_track_window(race_of(rid))
        assert abs(end_ts - time.time()) < 120


class TestTheChartActuallyGetsTheNewFixes:
    def test_a_fix_after_the_first_finish_is_returned(self, client):
        """End to end: the thing the race officer was looking at."""
        rid = make_race(client)
        finished_at = time.strftime("%Y-%m-%dT%H:%M:%S",
                                    time.localtime(time.time() - 1800))
        add_entry(rid, "First", "NR-1", status="FINISHED", finish_time=finished_at)
        add_entry(rid, "Second", "NR-2", status="RACING")
        track.upsert_tracker("NR-2", label="Second")
        now = time.time()
        track.insert_backfilled_positions([{
            "unique_id": "NR-2", "name": "Second", "lat": 52.88, "lon": -4.40,
            "speed_kn": 5.0, "course_deg": 90.0, "fix_time": now - 60}])

        history = track.race_track_history(rid)
        second = next(b for b in history["boats"] if b["boat_name"] == "Second")
        assert second["fixes"], (
            "a fix taken after the first boat finished never reached the chart"
        )
