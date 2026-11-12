"""One build of a race's board for everyone watching it.

Every open chart asks for what is new every 10 s while a race is on: the public
race page, whichever of its tabs is showing, and the clubhouse display. Each of
those requests used to build the race's whole order-on-the-water series from the
warning signal, send the last few snapshots and throw the rest away -- 0.2 s a
viewer a poll on a twenty-hour race, so the cost grew with the audience. Finding
where the window ends read every fix the race had, a tenth of a second more, and
each reply carried the whole race's wind, which the viewer then ignored; its wind
gauge went blank once the reading it loaded with was ten minutes old.
"""
from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path

import pytest

import app as ro
import core.track as track
from tests.test_race_replay import _seed_race, _track


@pytest.fixture
def builds(monkeypatch):
    """Count the board builds."""
    calls = []
    real = track.race_leaderboard_series

    def counting(*a, **k):
        calls.append(1)
        return real(*a, **k)

    monkeypatch.setattr(track, "race_leaderboard_series", counting)
    return calls


def _race(client, minutes=10):
    """Alpha, every 10 s from the gun for ``minutes``."""
    race_id, ids, gun = _seed_race(client, boats=("Alpha",))
    gun = float(int(gun))
    _track("SIMR-Alpha", ids["Alpha"]["boat_id"],
           [(gun + i * 10, 52.88 + i * 1e-4, -4.40) for i in range(minutes * 6 + 1)])
    return race_id, ids, gun


def _wind(start, end, every_s=30.0):
    with ro.get_db() as db:
        db.execute("DELETE FROM weather_samples")
        t = start
        while t <= end:
            db.execute("INSERT INTO weather_samples (sample_time, sample_iso, twd, tws_kt, gust_kt,"
                       " source, raw_json) VALUES (?,?,?,?,?,?,?)",
                       (t, datetime.fromtimestamp(t).isoformat(timespec="seconds"),
                        220.0, 12.0, 16.0, "test", "{}"))
            t += every_s
        db.commit()


class TestOneBuildForEveryone:
    def test_viewers_polling_together_share_one_build(self, client, builds):
        race_id, _ids, gun = _race(client)
        for since in (gun + 300, gun + 450, gun + 540, gun + 590, gun + 600):   # five viewers
            reply = track.race_track_history(race_id, since_ts=since)
            assert reply["incremental"] is True
        assert len(builds) == 1

    def test_each_still_gets_its_own_part(self, client, builds):
        race_id, _ids, gun = _race(client)
        early = track.race_track_history(race_id, since_ts=gun + 300)["boards"]["times"]
        late = track.race_track_history(race_id, since_ts=gun + 540)["boards"]["times"]
        assert early and late and len(early) > len(late)
        assert min(early) > gun + 300 and min(late) > gun + 540

    def test_it_is_built_again_once_the_share_time_is_up(self, client, builds, monkeypatch):
        monkeypatch.setattr(track, "REPLAY_BOARD_SHARE_S", 0.0)
        race_id, _ids, gun = _race(client)
        track.race_track_history(race_id, since_ts=gun + 300)
        track.race_track_history(race_id, since_ts=gun + 300)
        assert len(builds) == 2

    def test_the_whole_race_is_still_built_fresh(self, client, builds):
        """It is what a viewer starts again from after late fixes; a shared build
        from before they landed would leave them out of the board for good."""
        race_id, _ids, gun = _race(client)
        track.race_track_history(race_id, since_ts=gun + 300)
        track.race_track_history(race_id)
        assert len(builds) == 2


class TestTheBoardHasItsOwnCursor:
    """A shared build can be a few seconds older than a viewer's newest fix. Cut at
    the fixes, the snapshots in between would never reach that viewer."""

    def _viewer_moved_on(self, client, monkeypatch):
        race_id, ids, gun = _race(client)
        first = track.race_track_history(race_id, since_ts=gun + 300)     # builds to gun+600
        board_cursor = first["boards"]["times"][-1]
        # More fixes, which reach the viewer while the board it was sent is older.
        _track("SIMR-Alpha", ids["Alpha"]["boat_id"],
               [(gun + 600 + i * 10, 52.89, -4.40) for i in range(1, 31)])
        monkeypatch.setattr(track, "REPLAY_BOARD_SHARE_S", 0.0)             # the next build is due
        return race_id, gun + 900, board_cursor

    def test_the_snapshots_in_between_are_sent(self, client, monkeypatch):
        race_id, newest_fix, board_cursor = self._viewer_moved_on(client, monkeypatch)
        times = track.race_track_history(race_id, since_ts=newest_fix,
                                         board_since=board_cursor)["boards"]["times"]
        assert times and min(times) > board_cursor
        assert max(times) == pytest.approx(newest_fix)
        assert len(times) >= 50          # five minutes of snapshots at 5 s

    def test_a_page_with_no_board_cursor_is_cut_at_its_fixes_as_before(self, client, monkeypatch):
        race_id, newest_fix, _board_cursor = self._viewer_moved_on(client, monkeypatch)
        times = track.race_track_history(race_id, since_ts=newest_fix)["boards"]["times"]
        assert all(t > newest_fix for t in times)


class TestOnlyNewWindIsSent:
    def test_the_whole_race_carries_all_of_it(self, client):
        race_id, _ids, gun = _race(client)
        _wind(gun - 300, gun + 600)
        assert len(track.race_track_history(race_id)["wind"]) >= 25

    def test_a_poll_carries_only_what_comes_after_the_viewers_newest_reading(self, client):
        race_id, _ids, gun = _race(client)
        _wind(gun - 300, gun + 600)
        wind = track.race_track_history(race_id, since_ts=gun + 590, wind_since=gun + 480)["wind"]
        assert wind and all(w[0] > gun + 480 for w in wind)
        assert len(wind) <= 4

    def test_a_viewer_with_no_wind_yet_gets_what_is_new_since_its_fixes(self, client):
        race_id, _ids, gun = _race(client)
        _wind(gun - 300, gun + 600)
        wind = track.race_track_history(race_id, since_ts=gun + 540)["wind"]
        assert wind and all(w[0] > gun + 540 for w in wind)


class TestTheWindowEndIsFoundCheaply:
    def test_it_does_not_read_every_fix_of_the_race(self, client, monkeypatch):
        race_id, _ids, gun = _race(client)
        monkeypatch.setattr(track, "positions_for_entry_since",
                            lambda *a, **k: pytest.fail("read the whole track to find its end"))
        start_ts, end_ts = track.race_track_window(ro.get_race(race_id))
        assert end_ts == pytest.approx(gun + 600)

    def test_a_boat_last_heard_before_the_window_does_not_move_its_end(self, client):
        race_id, ids, gun = _seed_race(client, boats=("Alpha", "Bravo"))
        _track("SIMR-Alpha", ids["Alpha"]["boat_id"], [(gun + 60, 52.88, -4.40)])
        _track("SIMR-Bravo", ids["Bravo"]["boat_id"], [(gun - 3600, 52.88, -4.40)])   # an hour before
        _start, end_ts = track.race_track_window(ro.get_race(race_id))
        assert end_ts == pytest.approx(gun + 60)


class TestTheViewersSendTheirCursors:
    @pytest.mark.parametrize("name", ["race_replay.js", "bar_display.js"])
    def test_board_and_wind_cursors_go_with_each_poll(self, name):
        src = (Path(__file__).resolve().parent.parent / "static" / name).read_text(encoding="utf-8")
        assert "&board=" in src and "&wind=" in src
        assert "payload.wind" in src, "new wind readings must be appended, not ignored"
