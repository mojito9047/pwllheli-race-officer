"""A replay request answers with a replay, not with the whole database.

``/api/race/<id>/track`` had no upper bound on anything. It draws its window
from the race's gun to its last finish, and one afternoon a finish was stamped
352 days after the gun -- a mis-typed manual finish, since corrected -- so the
endpoint was asked, quite correctly by its own logic, for a year-long replay.
One request took gigabytes.

The obvious culprit is the fixes, and the obvious culprit is wrong. Measured, a
retained fix row costs about 43 bytes a boat; a *board snapshot* costs about 470,
and the board is sampled every ``REPLAY_BOARD_STEP_S`` across the whole window
whether or not a single boat reported in it. Roughly 1 MB per hour per three
boats before any fix is counted. So the window is the bound that matters.

It is not the only one. The window bounds time; it cannot see rate. Every club
tracker reports every 5 s today, but that is a device setting -- one reconfigured
to 1 Hz would put five times the fixes through the same bounded window. Hence a
second, independent bound on the fix count.

Neither bound can change a race result. This path is read by the replay viewer
and by nothing else; finish detection walks ``positions_for_entry_since``, which
is deliberately left unbounded, and these tests hold that line too.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

import app as ro
import core.track as track

from tests.test_race_replay import _seed_race, _track


def _finish_everyone(race_id, when):
    """Every boat in, so the window closes on the last finish rather than on now."""
    with ro.get_db() as db:
        db.execute("UPDATE entries SET status='FINISHED', finish_time=? WHERE race_id=?",
                   (when.isoformat(timespec="seconds"), race_id))
        db.commit()


class TestTheWindowIsBounded:
    def test_a_real_race_is_untouched(self, client):
        """The longest race the club has sailed is 2.7 hours. Nothing about a
        normal one may change, or the bound is a behaviour change in disguise."""
        race_id, ids, gun = _seed_race(client, minutes_ago=160)
        _finish_everyone(race_id, datetime.now() - timedelta(minutes=5))
        start_ts, end_ts = track.race_track_window(ro.get_race(race_id))
        assert 0 < (end_ts - start_ts) < 3 * 3600
        assert end_ts - start_ts < track.REPLAY_MAX_WINDOW_S

    def test_a_year_long_window_is_refused(self, client):
        """A race 500 days old, finished 400 days after its gun: entirely in the
        past, so ``min(end, now)`` does not save us and only the clamp does."""
        race_id, ids, gun = _seed_race(client, minutes_ago=500 * 24 * 60)
        _finish_everyone(race_id, datetime.now() - timedelta(days=100))
        start_ts, end_ts = track.race_track_window(ro.get_race(race_id))
        assert end_ts - start_ts <= track.REPLAY_MAX_WINDOW_S + 1

    def test_the_start_of_the_window_is_not_moved(self, client):
        """Clamping takes time off the end. Moving the start would hide the one
        part of the race the window exists to include -- the approach to the line."""
        race_id, ids, gun = _seed_race(client, minutes_ago=500 * 24 * 60)
        before = gun - track.REPLAY_PRE_START_S
        _finish_everyone(race_id, datetime.now() - timedelta(days=100))
        start_ts, _ = track.race_track_window(ro.get_race(race_id))
        assert abs(start_ts - before) < 2

    def test_the_board_series_is_bounded_with_it(self, client):
        """The point of the whole exercise: the board is one snapshot every
        REPLAY_BOARD_STEP_S across the window, so bounding the window is what
        bounds the snapshot count -- and the snapshots are the expensive part."""
        race_id, ids, gun = _seed_race(client, minutes_ago=500 * 24 * 60)
        _finish_everyone(race_id, datetime.now() - timedelta(days=100))
        start_ts, end_ts = track.race_track_window(ro.get_race(race_id))
        snapshots = (end_ts - start_ts) / track.REPLAY_BOARD_STEP_S
        assert snapshots <= track.REPLAY_MAX_WINDOW_S / track.REPLAY_BOARD_STEP_S + 1
        # Unclamped this race would have asked for over six million.
        assert snapshots < 20000, snapshots

    def test_a_race_nobody_ever_closed_is_bounded_too(self, client):
        """The other way in, and the likelier one. A boat left RACING keeps the
        window open to now, so a race nobody tidied up in March is a months-long
        window every time anyone opens it -- no bad timestamp required."""
        race_id, ids, gun = _seed_race(client, minutes_ago=40 * 24 * 60)
        start_ts, end_ts = track.race_track_window(ro.get_race(race_id))
        assert end_ts - start_ts <= track.REPLAY_MAX_WINDOW_S + 1

    def test_the_kept_stretch_is_the_start_of_the_race_not_the_end(self, client):
        """Clamping the end rather than the start is the whole decision. On a
        race stale for weeks, keeping the last 12 hours would show an empty sea
        and cut out the race; keeping the first 12 shows the race."""
        race_id, ids, gun = _seed_race(client, minutes_ago=40 * 24 * 60)
        start_ts, end_ts = track.race_track_window(ro.get_race(race_id))
        assert start_ts < gun
        assert end_ts - gun == pytest.approx(
            track.REPLAY_MAX_WINDOW_S - track.REPLAY_PRE_START_S, abs=2)

    def test_the_clamp_is_longer_than_any_race(self, client):
        """A guard on the constant itself: set below a real race length it would
        silently truncate replays instead of catching bad data."""
        assert track.REPLAY_MAX_WINDOW_S >= 6 * 3600


class TestThinning:
    def test_fewer_than_the_budget_comes_back_whole(self):
        rows = [[float(i), 52.0, -4.0, 5.0, 90.0] for i in range(10)]
        assert track.thin_fixes(rows, 100) == rows
        assert track.thin_fixes(rows, 10) == rows

    def test_both_ends_are_kept(self):
        rows = [[float(i), 52.0, -4.0, 5.0, 90.0] for i in range(1000)]
        out = track.thin_fixes(rows, 50)
        assert out[0] == rows[0]
        assert out[-1] == rows[-1]

    def test_it_fits_the_budget_and_stays_in_order(self):
        rows = [[float(i), 52.0, -4.0, 5.0, 90.0] for i in range(1000)]
        out = track.thin_fixes(rows, 50)
        assert len(out) == 50
        assert [r[0] for r in out] == sorted(r[0] for r in out)
        assert all(r in rows for r in out)

    def test_it_is_spread_evenly_not_truncated(self):
        """A truncated track stops in the middle of the bay and reads as a boat
        that retired."""
        rows = [[float(i), 52.0, -4.0, 5.0, 90.0] for i in range(1000)]
        out = track.thin_fixes(rows, 11)
        gaps = [out[i + 1][0] - out[i][0] for i in range(len(out) - 1)]
        assert max(gaps) - min(gaps) <= 1

    def test_a_budget_of_one_keeps_the_last_fix(self):
        """Where the boat ended up, not where it started."""
        rows = [[float(i), 52.0, -4.0, 5.0, 90.0] for i in range(10)]
        assert track.thin_fixes(rows, 1) == [rows[-1]]

    def test_an_empty_track_is_fine(self):
        assert track.thin_fixes([], 50) == []


class TestTheBudgetInTheReply:
    @pytest.fixture
    def small_budget(self, monkeypatch):
        """Ten fixes a boat, so a test does not have to write thirty thousand."""
        monkeypatch.setattr(track, "REPLAY_MAX_FIXES", 20)
        monkeypatch.setattr(track, "REPLAY_MIN_FIXES_PER_BOAT", 4)
        return 10

    def test_a_normal_race_is_not_thinned(self, client):
        race_id, ids, gun = _seed_race(client, minutes_ago=60)
        _track("SIMR-Alpha", ids["Alpha"]["boat_id"],
               [(gun + i * 5, 52.88 + i * 1e-5, -4.40) for i in range(600)])
        hist = track.race_track_history(race_id)
        alpha = next(b for b in hist["boats"] if b["boat_name"] == "Alpha")
        assert len(alpha["fixes"]) == 600
        assert hist["thinned"] is False
        assert hist["fixes_dropped"] == 0

    def test_a_flood_is_thinned_to_the_share(self, client, small_budget):
        race_id, ids, gun = _seed_race(client, minutes_ago=60)
        for name in ("Alpha", "Bravo"):
            _track("SIMR-" + name, ids[name]["boat_id"],
                   [(gun + i, 52.88 + i * 1e-5, -4.40) for i in range(200)])
        hist = track.race_track_history(race_id)
        for boat in hist["boats"]:
            assert len(boat["fixes"]) <= small_budget, boat["boat_name"]

    def test_the_ends_of_each_track_survive_the_budget(self, client, small_budget):
        race_id, ids, gun = _seed_race(client, minutes_ago=60)
        pts = [(gun + i, 52.88 + i * 1e-4, -4.40) for i in range(200)]
        _track("SIMR-Alpha", ids["Alpha"]["boat_id"], pts)
        hist = track.race_track_history(race_id)
        alpha = next(b for b in hist["boats"] if b["boat_name"] == "Alpha")
        assert alpha["fixes"][0][1] == pytest.approx(pts[0][1])
        assert alpha["fixes"][-1][1] == pytest.approx(pts[-1][1])

    def test_the_reply_says_it_thinned(self, client, small_budget):
        """A cap nobody is told about reads as 'this is the whole track'."""
        race_id, ids, gun = _seed_race(client, minutes_ago=60)
        _track("SIMR-Alpha", ids["Alpha"]["boat_id"],
               [(gun + i, 52.88 + i * 1e-5, -4.40) for i in range(200)])
        hist = track.race_track_history(race_id)
        alpha = next(b for b in hist["boats"] if b["boat_name"] == "Alpha")
        assert hist["thinned"] is True
        assert hist["fixes_dropped"] == 200 - len(alpha["fixes"])
        assert alpha["recorded_fixes"] == 200

    def test_every_boat_is_counted_even_untracked_ones(self, client):
        """recorded_fixes is on every boat, so the viewer never has to guess
        whether a zero means 'no tracker' or 'thinned away'."""
        race_id, ids, gun = _seed_race(client, minutes_ago=60)
        hist = track.race_track_history(race_id)
        assert all("recorded_fixes" in b for b in hist["boats"])

    def test_a_bigger_fleet_never_drops_below_the_floor(self, client, monkeypatch):
        """The share shrinks with the fleet; the floor stops it reaching nothing."""
        monkeypatch.setattr(track, "REPLAY_MAX_FIXES", 10)
        monkeypatch.setattr(track, "REPLAY_MIN_FIXES_PER_BOAT", 25)
        race_id, ids, gun = _seed_race(client, boats=("Alpha", "Bravo", "Charlie"),
                                       minutes_ago=60)
        for name in ("Alpha", "Bravo", "Charlie"):
            _track("SIMR-" + name, ids[name]["boat_id"],
                   [(gun + i, 52.88 + i * 1e-5, -4.40) for i in range(200)])
        hist = track.race_track_history(race_id)
        for boat in hist["boats"]:
            assert len(boat["fixes"]) == 25, boat["boat_name"]


class TestScoringIsNotBounded:
    def test_finish_detection_still_sees_every_fix(self, client, monkeypatch):
        """The bound is on the replay reply, not on the track. Thinning the walk
        that detects finishes would move finish times, which is the one thing
        none of this is allowed to do."""
        monkeypatch.setattr(track, "REPLAY_MAX_FIXES", 20)
        monkeypatch.setattr(track, "REPLAY_MIN_FIXES_PER_BOAT", 4)
        race_id, ids, gun = _seed_race(client, minutes_ago=60)
        _track("SIMR-Alpha", ids["Alpha"]["boat_id"],
               [(gun + i, 52.88 + i * 1e-5, -4.40) for i in range(200)])
        entry = next(e for e in ro.get_entries(race_id) if e["boat_name"] == "Alpha")
        assert len(track.positions_for_entry_since(entry, gun - 600)) == 200
