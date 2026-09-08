"""The progress walk stops when the race did.

It used to stop nowhere. ``race_leaderboard`` read every fix from the gun to
*now*, so opening a race sailed on 21 July walked six weeks of tracking that had
nothing to do with it: 98,362 fixes across nine boats, **81,849 of them for one
boat**, and one more day's worth every day it sat in the database. Half a second
to draw a race sheet for nine boats, getting slower every week, for a reason
nobody looking at the page could see.

A race whose boats have all finished ended when the last of them crossed. An
hour of margin is kept past that so a finish time corrected afterwards is still
inside the window the walk covers.

**A race with boats still RACING must be walked to now**, and this is the part
worth being careful about. The replay reply is capped at twelve hours, and
reusing that here looked tidy -- but that constant caps the size of an HTTP
response, where being mean costs nothing. This one decides whether a boat still
shows progress and whether its GPS finish is ever detected. The club sails ISORA
passage races; Pwllheli to Ireland finishes on the transit at Plas Heli and
takes the better part of a day. A twelve-hour cut-off would freeze that fleet
mid-Irish-Sea and silently stop looking for its finishes. The backstop is three
days, and it exists only for a race somebody left open.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta

import app as ro
import core.track as track

from tests.test_race_replay import _seed_race, _track


def _finish(race_id, boat_name, when):
    from core.db import get_db
    with get_db() as db:
        db.execute("UPDATE entries SET status = 'FINISHED', finish_time = ?"
                   " WHERE race_id = ? AND boat_name = ?",
                   (datetime.fromtimestamp(when).isoformat(timespec="seconds"),
                    race_id, boat_name))
        db.commit()


def _entries(race_id):
    return ro.get_entries(race_id)


class TestAFinishedRaceStopsAtItsLastFinish:
    def test_the_walk_ends_an_hour_after_the_last_boat(self, client):
        race_id, ids, gun = _seed_race(client, minutes_ago=200)
        last = gun + 3600
        _finish(race_id, "Alpha", gun + 1800)
        _finish(race_id, "Bravo", last)
        end = track._progress_walk_end(_entries(race_id), gun, time.time())
        assert end is not None
        assert abs(end - (last + 3600)) < 2, "margin for a corrected finish time"

    def test_so_a_month_of_later_fixes_is_not_read(self, client):
        """The reported case: fixes recorded long after the race, on the same
        boat, walked every time the race sheet was opened."""
        race_id, ids, gun = _seed_race(client, minutes_ago=200)
        _finish(race_id, "Alpha", gun + 1800)
        _finish(race_id, "Bravo", gun + 1900)
        during = [(gun + i * 30, 52.88 + i * 1e-4, -4.40) for i in range(40)]
        after = [(gun + 20 * 86400 + i * 30, 52.9, -4.4) for i in range(400)]
        _track("SIMR-Alpha", ids["Alpha"]["boat_id"], during + after)

        end = track._progress_walk_end(_entries(race_id), gun, time.time())
        entry = next(e for e in _entries(race_id) if e["boat_name"] == "Alpha")
        walked = track.positions_for_entry_since(entry, gun, end)
        assert len(walked) == len(during), f"walked {len(walked)}, race had {len(during)}"

    def test_a_race_where_nobody_finished_still_has_an_end(self, client):
        """Abandoned, or every boat DNF: no finish to reach towards."""
        from core.db import get_db
        race_id, ids, gun = _seed_race(client, minutes_ago=200)
        with get_db() as db:
            db.execute("UPDATE entries SET status = 'DNF' WHERE race_id = ?", (race_id,))
            db.commit()
        end = track._progress_walk_end(_entries(race_id), gun, time.time())
        assert end is not None and end <= time.time()


class TestARaceStillBeingSailedIsWalkedToNow:
    def test_boats_still_racing_are_followed_to_now(self, client):
        race_id, ids, gun = _seed_race(client, minutes_ago=60)
        now = time.time()
        end = track._progress_walk_end(_entries(race_id), gun, now)
        assert end == now or abs(end - now) < 2

    def test_one_boat_still_out_keeps_the_race_live(self, client):
        """Eight home and one still out is not a finished race."""
        race_id, ids, gun = _seed_race(client, minutes_ago=60)
        _finish(race_id, "Alpha", gun + 1800)          # Bravo is still RACING
        now = time.time()
        assert abs(track._progress_walk_end(_entries(race_id), gun, now) - now) < 2


class TestAPassageRaceIsNotCutOff:
    """The reason the backstop is three days and not the replay's twelve hours."""

    def test_the_backstop_is_generous_enough_for_an_offshore_race(self):
        assert track.PROGRESS_MAX_WINDOW_S >= 24 * 3600, (
            "an ISORA passage race to Ireland takes the better part of a day"
        )

    def test_and_is_not_the_replay_reply_cap(self):
        """Reusing REPLAY_MAX_WINDOW_S here would freeze a passage race at 12
        hours. One caps a response size; this one decides whether a boat is
        still being followed."""
        assert track.PROGRESS_MAX_WINDOW_S > track.REPLAY_MAX_WINDOW_S

    def test_a_boat_twenty_hours_into_a_passage_race_is_still_followed(self, client):
        """Started yesterday morning, still racing, still reporting."""
        race_id, ids, gun = _seed_race(client, minutes_ago=20 * 60)
        recent = time.time() - 120
        _track("SIMR-Alpha", ids["Alpha"]["boat_id"],
               [(gun + i * 600, 52.9 + i * 1e-3, -4.5) for i in range(100)] +
               [(recent, 53.2, -5.4)])
        row = next(r for r in track.race_leaderboard(race_id) if r["boat_name"] == "Alpha")
        assert row["lat"] is not None, "the fleet vanished mid-passage"
        assert row["age"] is not None and row["age"] < 600

    def test_and_its_fixes_from_hour_eighteen_are_still_walked(self, client):
        """Not just the position: the whole track has to stay in the window, or
        the marks it rounded overnight are forgotten and its finish never fires."""
        race_id, ids, gun = _seed_race(client, minutes_ago=20 * 60)
        end = track._progress_walk_end(_entries(race_id), gun, time.time())
        assert end - gun > 19 * 3600


class TestTheRaceLeftOpenForever:
    def test_it_is_bounded_by_the_backstop(self, client):
        """Boats still marked RACING a week later. Somebody forgot; the walk
        must not grow without limit because of it."""
        race_id, ids, gun = _seed_race(client, minutes_ago=8 * 24 * 60)
        end = track._progress_walk_end(_entries(race_id), gun, time.time())
        assert end == gun + track.PROGRESS_MAX_WINDOW_S


class TestTheReplayIsUnaffected:
    def test_rewinding_still_stops_at_the_moment_asked_for(self, client):
        race_id, ids, gun = _seed_race(client, minutes_ago=200)
        _finish(race_id, "Alpha", gun + 1800)
        _finish(race_id, "Bravo", gun + 1900)
        _track("SIMR-Alpha", ids["Alpha"]["boat_id"],
               [(gun + i * 30, 52.88 + i * 1e-4, -4.40) for i in range(60)])
        at = gun + 300
        row = next(r for r in track.race_leaderboard(race_id, at_ts=at)
                   if r["boat_name"] == "Alpha")
        assert row["age"] is not None and row["age"] < 60, (
            "the replay clock, not the finished-race bound"
        )
