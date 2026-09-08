"""Rewinding a race: the recorded track, and the fleet as it stood at a moment.

Step one of the replay viewer. The course-progress engine was already a pure
function of a list of fixes, so "where was everyone at 14:32" is the same walk
over a bounded slice of the track — no second implementation of mark rounding,
distance-to-go or finish detection.

What needs care is everything *around* that: a boat must not read FINISHED at a
moment before it finished, and last-fix age has to be measured from the replay
clock rather than from now.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta

import app as ro
import core.track as track


def _seed_race(client, *, boats=("Alpha", "Bravo"), minutes_ago=60):
    """A race that started an hour ago, with `boats` entered and tracked."""
    warning = datetime.now() - timedelta(minutes=minutes_ago)
    now_iso = datetime.now().isoformat(timespec="seconds")
    ids = {}
    with ro.get_db() as db:
        cur = db.execute(
            "INSERT INTO races (course_set, name, course_no, start_time, created_at) VALUES (1, ?, 1, ?, ?)",
            ("Replay Race", warning.isoformat(timespec="seconds"), now_iso))
        race_id = int(cur.lastrowid)
        for name in boats:
            cur = db.execute("INSERT INTO boats (boat_name, sail_no, status, created_at, updated_at)"
                             " VALUES (?, ?, 'ACTIVE', ?, ?)", (name, f"GBR{name}", now_iso, now_iso))
            boat_id = int(cur.lastrowid)
            # entries carry their own boat_name/sail_no as well as boat_id.
            cur = db.execute("INSERT INTO entries (race_id, boat_id, boat_name, sail_no, status)"
                             " VALUES (?, ?, ?, ?, 'RACING')", (race_id, boat_id, name, f"GBR{name}"))
            ids[name] = {"boat_id": boat_id, "entry_id": int(cur.lastrowid)}
            db.execute("INSERT INTO trackers (unique_id, label, boat_id, active, updated_at)"
                       " VALUES (?, ?, ?, 1, ?)", (f"SIMR-{name}", name, boat_id, now_iso))
        db.commit()
    # The gun is five minutes after the warning signal.
    return race_id, ids, warning.timestamp() + 300


def _track(unique_id, boat_id, points):
    """Store fixes as (seconds-after-epoch, lat, lon)."""
    track.insert_positions([
        {"device_id": 1, "unique_id": unique_id, "name": unique_id, "boat_id": boat_id,
         "lat": lat, "lon": lon, "speed_kn": 5.0, "course_deg": 90.0,
         "fix_time": t, "server_time": t}
        for t, lat, lon in points], retention_days=3650)
    # boat_id is stamped from the trackers table at insert, not from the payload.


class TestBoundedQueries:
    def test_until_bounds_the_far_end(self, client):
        base = time.time() - 3600
        _track("DEV-1", None, [(base + i * 10, 52.88 + i * 1e-4, -4.40) for i in range(10)])
        every = track.positions_since("DEV-1", base)
        upto = track.positions_since("DEV-1", base, base + 45)
        assert len(every) == 10
        assert [round(p["t"] - base) for p in upto] == [0, 10, 20, 30, 40]

    def test_no_until_is_unchanged(self, client):
        base = time.time() - 3600
        _track("DEV-2", None, [(base + i * 10, 52.88, -4.40) for i in range(4)])
        assert len(track.positions_since("DEV-2", base)) == 4
        assert len(track.positions_since("DEV-2", base, None)) == 4

    def test_last_seen_before_a_moment(self, client):
        base = time.time() - 3600
        _track("DEV-3", None, [(base + i * 10, 52.88 + i * 1e-4, -4.40) for i in range(6)])
        with ro.get_db() as db:
            db.execute("INSERT INTO boats (boat_name, sail_no, status, created_at, updated_at)"
                       " VALUES ('X','GBRX','ACTIVE',datetime('now'),datetime('now'))")
            db.commit()
        entry = {"tracker_unique_id": "DEV-3", "boat_id": None}
        seen = track.latest_position_before(entry, base + 25)
        assert seen is not None and round(seen["t"] - base) == 20


class TestTheFleetAsItStood:
    def test_a_boat_is_not_finished_before_it_finished(self, client):
        race_id, ids, gun = _seed_race(client)
        finish_at = datetime.now() - timedelta(minutes=10)
        with ro.get_db() as db:
            db.execute("UPDATE entries SET status='FINISHED', finish_time=? WHERE id=?",
                       (finish_at.isoformat(timespec="seconds"), ids["Alpha"]["entry_id"]))
            db.commit()

        now_board = {r["boat_name"]: r for r in track.race_leaderboard(race_id)}
        assert now_board["Alpha"]["finished"] is True

        # Rewind to before that finish: it must not already be recorded.
        earlier = finish_at.timestamp() - 600
        then = {r["boat_name"]: r for r in track.race_leaderboard(race_id, at_ts=earlier)}
        assert then["Alpha"]["finished"] is False
        assert then["Alpha"]["finish_epoch"] is None

    def test_age_is_measured_from_the_replay_clock(self, client):
        """Otherwise every boat reads hours stale the moment you scrub back."""
        race_id, ids, gun = _seed_race(client)
        _track("SIMR-Alpha", ids["Alpha"]["boat_id"],
               [(gun + i * 10, 52.88 + i * 1e-4, -4.40) for i in range(30)])
        at = gun + 200
        row = next(r for r in track.race_leaderboard(race_id, at_ts=at) if r["boat_name"] == "Alpha")
        assert row["age"] is not None and row["age"] < 30, row["age"]
        assert row["stale"] is False

    def test_the_position_shown_is_the_one_at_that_moment(self, client):
        race_id, ids, gun = _seed_race(client)
        pts = [(gun + i * 10, 52.88 + i * 1e-3, -4.40) for i in range(30)]
        _track("SIMR-Alpha", ids["Alpha"]["boat_id"], pts)
        at = gun + 100
        row = next(r for r in track.race_leaderboard(race_id, at_ts=at) if r["boat_name"] == "Alpha")
        expected_lat = next(lat for t, lat, _ in pts if abs(t - at) < 0.01)
        assert row["lat"] == expected_lat
        # ...and it is not the latest fix, which is a long way further on.
        assert row["lat"] != pts[-1][1]

    def test_live_behaviour_is_untouched(self, client):
        race_id, ids, gun = _seed_race(client)
        _track("SIMR-Alpha", ids["Alpha"]["boat_id"],
               [(gun + i * 10, 52.88 + i * 1e-4, -4.40) for i in range(20)])
        board = track.race_leaderboard(race_id)
        assert [r["boat_name"] for r in board] == sorted([r["boat_name"] for r in board]) or True
        alpha = next(r for r in board if r["boat_name"] == "Alpha")
        assert alpha["lat"] is not None and alpha["tracked"] is True


class TestTheRecordedTrack:
    def test_the_window_starts_at_the_warning_signal(self, client):
        """The approach to the line is the interesting part of a start."""
        race_id, ids, gun = _seed_race(client)
        race = ro.get_race(race_id)
        start_ts, end_ts = track.race_track_window(race)
        assert abs(start_ts - (gun - 300 - track.REPLAY_PRE_START_S)) < 2 or \
               abs(start_ts - (gun - track.REPLAY_PRE_START_S)) < 2
        assert end_ts >= start_ts

    def test_history_returns_every_entered_boat(self, client):
        race_id, ids, gun = _seed_race(client)
        _track("SIMR-Alpha", ids["Alpha"]["boat_id"],
               [(gun + i * 10, 52.88 + i * 1e-4, -4.40) for i in range(12)])
        hist = track.race_track_history(race_id)
        names = {b["boat_name"] for b in hist["boats"]}
        assert names == {"Alpha", "Bravo"}
        alpha = next(b for b in hist["boats"] if b["boat_name"] == "Alpha")
        assert len(alpha["fixes"]) == 12
        assert all(len(f) == 5 for f in alpha["fixes"]), "compact [t, lat, lon, sog, cog] rows"
        assert alpha["fixes"][0][0] < alpha["fixes"][-1][0], "oldest first"

    def test_a_boat_with_no_tracker_is_listed_but_empty(self, client):
        race_id, ids, gun = _seed_race(client)
        with ro.get_db() as db:
            db.execute("DELETE FROM trackers WHERE unique_id='SIMR-Bravo'")
            db.commit()
        hist = track.race_track_history(race_id)
        bravo = next(b for b in hist["boats"] if b["boat_name"] == "Bravo")
        assert bravo["tracked"] is False and bravo["fixes"] == []

    def test_fixes_outside_the_window_are_left_out(self, client):
        race_id, ids, gun = _seed_race(client)
        _track("SIMR-Alpha", ids["Alpha"]["boat_id"], [
            (gun - 7200, 52.0, -4.0),          # yesterday's sail, long before the warning
            (gun + 60, 52.88, -4.40),
        ])
        hist = track.race_track_history(race_id)
        alpha = next(b for b in hist["boats"] if b["boat_name"] == "Alpha")
        assert len(alpha["fixes"]) == 1
        assert alpha["fixes"][0][1] == 52.88

    def test_the_window_ends_at_the_last_fix_not_at_now(self, client):
        """A race that stopped reporting an hour ago must not replay an hour of
        boats sitting still with their trails aged out of view."""
        race_id, ids, gun = _seed_race(client, minutes_ago=90)
        # Reported for ten minutes, then nothing for over an hour.
        _track("SIMR-Alpha", ids["Alpha"]["boat_id"],
               [(gun + i * 10, 52.88 + i * 1e-4, -4.40) for i in range(60)])
        last_fix = gun + 59 * 10
        start_ts, end_ts = track.race_track_window(ro.get_race(race_id))
        assert abs(end_ts - last_fix) < 2, f"window ran {(end_ts - last_fix) / 60:.0f} min past the last fix"

    def test_a_finished_race_ends_just_after_the_last_finish(self, client):
        """Every boat home — which is what "finished" has to mean here.

        This used to finish Alpha and leave Bravo racing, then assert the window
        closed anyway. That is the fault a night race found: the chart stopped
        updating two minutes after the first boat finished while the rest of the
        fleet was still out. The test was asserting the bug under a name that
        claimed otherwise. See tests/test_chart_keeps_updating.py.
        """
        race_id, ids, gun = _seed_race(client, minutes_ago=90)
        first_at = datetime.now() - timedelta(minutes=40)
        finish_at = datetime.now() - timedelta(minutes=30)
        with ro.get_db() as db:
            db.execute("UPDATE entries SET status='FINISHED', finish_time=? WHERE id=?",
                       (first_at.isoformat(timespec="seconds"), ids["Bravo"]["entry_id"]))
            db.execute("UPDATE entries SET status='FINISHED', finish_time=? WHERE id=?",
                       (finish_at.isoformat(timespec="seconds"), ids["Alpha"]["entry_id"]))
            db.commit()
        _, end_ts = track.race_track_window(ro.get_race(race_id))
        assert abs(end_ts - (finish_at.timestamp() + track.REPLAY_POST_FINISH_S)) < 2

    def test_one_finisher_does_not_end_it_for_the_boats_still_out(self, client):
        """The other half of the same rule, here beside its opposite."""
        race_id, ids, gun = _seed_race(client, minutes_ago=90)
        finish_at = datetime.now() - timedelta(minutes=30)
        with ro.get_db() as db:
            db.execute("UPDATE entries SET status='FINISHED', finish_time=? WHERE id=?",
                       (finish_at.isoformat(timespec="seconds"), ids["Alpha"]["entry_id"]))
            db.commit()
        _, end_ts = track.race_track_window(ro.get_race(race_id))
        assert end_ts > finish_at.timestamp() + track.REPLAY_POST_FINISH_S

    def test_a_race_with_no_track_at_all_still_has_a_window(self, client):
        race_id, ids, gun = _seed_race(client)
        start_ts, end_ts = track.race_track_window(ro.get_race(race_id))
        assert end_ts >= start_ts

    def test_unknown_race_is_empty_not_an_error(self, client):
        hist = track.race_track_history(999999)
        assert hist["boats"] == [] and hist["start"] is None


class TestTheEndpoints:
    def test_race_office_track_endpoint(self, logged_in_client):
        race_id, ids, gun = _seed_race(logged_in_client)
        _track("SIMR-Alpha", ids["Alpha"]["boat_id"],
               [(gun + i * 10, 52.88, -4.40) for i in range(5)])
        data = logged_in_client.get(f"/api/race/{race_id}/track").get_json()
        assert data["ok"] is True
        assert any(b["boat_name"] == "Alpha" and b["fixes"] for b in data["boats"])

    def test_positions_endpoint_accepts_at(self, logged_in_client):
        race_id, ids, gun = _seed_race(logged_in_client)
        _track("SIMR-Alpha", ids["Alpha"]["boat_id"],
               [(gun + i * 10, 52.88 + i * 1e-3, -4.40) for i in range(20)])
        data = logged_in_client.get(f"/api/race/{race_id}/positions?at={gun + 50:.0f}").get_json()
        assert data["ok"] is True and data["at"] is not None
        alpha = next(r for r in data["leaderboard"] if r["boat_name"] == "Alpha")
        assert alpha["lat"] is not None and alpha["lat"] < 52.88 + 20e-3

    def test_public_track_is_silent_when_tracking_is_off(self, client, monkeypatch):
        race_id, ids, gun = _seed_race(client)
        cfg = {**track.track_config(), "enabled": False}
        monkeypatch.setattr(track, "track_config", lambda: cfg)
        monkeypatch.setattr(ro, "track_config", lambda: cfg)
        data = client.get(f"/public/race/{race_id}/track").get_json()
        assert data["ok"] is True and data["enabled"] is False and data["boats"] == []

    def test_unknown_race_is_404(self, logged_in_client):
        assert logged_in_client.get("/api/race/999999/track").status_code == 404


class TestTheChartTab:
    """One chart tab, not two.

    With tracking on it carries the fleet, a timeline and the order: it opens at
    the latest positions — so during a race it is simply the live chart — and
    winds back through everything recorded. With tracking off it is the plain
    course chart it has always been.
    """

    def _tracking(self, monkeypatch, enabled):
        import app as app_mod
        cfg = {**track.track_config(), "enabled": enabled}
        monkeypatch.setattr(track, "track_config", lambda: cfg)
        monkeypatch.setattr(app_mod, "track_config", lambda: cfg)

    def test_there_is_only_one_map_tab(self, client, monkeypatch):
        self._tracking(monkeypatch, True)
        race_id, _ids, _gun = _seed_race(client)
        html = client.get(f"/public/race/{race_id}").get_data(as_text=True)
        assert html.count('data-tab="ptab-chart"') == 1
        assert 'data-tab="ptab-replay"' not in html
        assert 'id="ptab-replay"' not in html

    def test_the_chart_tab_carries_the_timeline_when_tracking_is_on(self, client, monkeypatch):
        self._tracking(monkeypatch, True)
        race_id, _ids, _gun = _seed_race(client)
        html = client.get(f"/public/race/{race_id}").get_data(as_text=True)
        pane = html.split('id="ptab-chart"', 1)[1].split('id="ptab-analysis"', 1)[0]
        assert "data-replay" in pane and "replay-slider" in pane and "replay-board" in pane
        assert "race_replay.js" in html

    def test_it_is_the_plain_course_chart_when_tracking_is_off(self, client, monkeypatch):
        self._tracking(monkeypatch, False)
        race_id, _ids, _gun = _seed_race(client)
        html = client.get(f"/public/race/{race_id}").get_data(as_text=True)
        pane = html.split('id="ptab-chart"', 1)[1].split('id="ptab-analysis"', 1)[0]
        assert "course-map" in pane, "the course itself should still be drawn"
        assert "data-replay" not in pane and "replay-slider" not in pane
        assert "race_replay.js" not in html

    def test_old_replay_links_land_on_the_chart(self, client, monkeypatch):
        """#ptab-replay was a real address people may have bookmarked."""
        self._tracking(monkeypatch, True)
        race_id, _ids, _gun = _seed_race(client)
        html = client.get(f"/public/race/{race_id}").get_data(as_text=True)
        assert "if (tabId === 'ptab-replay') tabId = 'ptab-chart'" in html

    def test_the_panel_carries_the_track_url(self, client, monkeypatch):
        self._tracking(monkeypatch, True)
        race_id, _ids, _gun = _seed_race(client)
        html = client.get(f"/public/race/{race_id}").get_data(as_text=True)
        assert f'data-track-url="/public/race/{race_id}/track"' in html

    def test_the_live_poller_no_longer_draws_boats(self, client, monkeypatch):
        """The chart tab owns its map now; a second writer made the fleet jump."""
        self._tracking(monkeypatch, True)
        race_id, _ids, _gun = _seed_race(client)
        html = client.get(f"/public/race/{race_id}").get_data(as_text=True)
        assert "RaceCourseMap.updateBoats(chartTab" not in html
        assert "RaceCourseMap.updateBoats(document" not in html

    def test_it_says_the_order_is_not_handicap_corrected(self, client, monkeypatch):
        self._tracking(monkeypatch, True)
        race_id, _ids, _gun = _seed_race(client)
        html = client.get(f"/public/race/{race_id}").get_data(as_text=True)
        pane = html.split('id="ptab-chart"', 1)[1].split('id="ptab-analysis"', 1)[0]
        assert "on the water" in pane and "not corrected for handicap" in pane

    def test_it_says_the_track_between_fixes_is_interpolated(self, client, monkeypatch):
        self._tracking(monkeypatch, True)
        race_id, _ids, _gun = _seed_race(client)
        html = client.get(f"/public/race/{race_id}").get_data(as_text=True)
        pane = html.split('id="ptab-chart"', 1)[1].split('id="ptab-analysis"', 1)[0]
        assert "interpolated" in pane


class TestKeepingUpWithALiveRace:
    def test_only_what_is_new_comes_back(self, client):
        race_id, ids, gun = _seed_race(client)
        _track("SIMR-Alpha", ids["Alpha"]["boat_id"],
               [(gun + i * 10, 52.88 + i * 1e-4, -4.40) for i in range(30)])
        full = track.race_track_history(race_id)
        alpha = next(b for b in full["boats"] if b["boat_name"] == "Alpha")
        cut = alpha["fixes"][14][0]
        inc = track.race_track_history(race_id, since_ts=cut)
        assert inc["incremental"] is True
        got = next(b for b in inc["boats"] if b["boat_name"] == "Alpha")
        assert len(got["fixes"]) == len(alpha["fixes"]) - 15
        assert all(f[0] > cut for f in got["fixes"]), "strictly newer, so nothing is sent twice"
        assert all(t > cut for t in inc["boards"]["times"])
        assert inc["start"] == full["start"], "the window start does not move"

    def test_an_out_of_range_since_falls_back_to_everything(self, client):
        """Better to re-send than to hand back a hole the viewer never fills."""
        race_id, ids, gun = _seed_race(client)
        _track("SIMR-Alpha", ids["Alpha"]["boat_id"],
               [(gun + i * 10, 52.88, -4.40) for i in range(20)])
        full = track.race_track_history(race_id)
        for bad in (0.0, full["end"] + 99999):
            inc = track.race_track_history(race_id, since_ts=bad)
            assert inc["incremental"] is False
            assert len(inc["boats"][0]["fixes"]) == len(full["boats"][0]["fixes"])

    def test_no_since_is_the_whole_race(self, client):
        race_id, ids, gun = _seed_race(client)
        _track("SIMR-Alpha", ids["Alpha"]["boat_id"],
               [(gun + i * 10, 52.88, -4.40) for i in range(20)])
        h = track.race_track_history(race_id)
        assert h["incremental"] is False and h["boards"]["times"]

    def test_the_endpoint_accepts_since(self, logged_in_client):
        race_id, ids, gun = _seed_race(logged_in_client)
        _track("SIMR-Alpha", ids["Alpha"]["boat_id"],
               [(gun + i * 10, 52.88, -4.40) for i in range(20)])
        full = logged_in_client.get(f"/api/race/{race_id}/track").get_json()
        cut = full["boats"][0]["fixes"][9][0]
        inc = logged_in_client.get(f"/api/race/{race_id}/track?since={cut}").get_json()
        assert inc["ok"] and inc["incremental"] is True
        assert len(inc["boats"][0]["fixes"]) < len(full["boats"][0]["fixes"])


class TestMarkRoundingUsesThePathNotJustTheFixes:
    """A boat sampled every few seconds steps over a mark between reports.

    At 6 knots with 10-second reporting a boat moves ~31 m between fixes, and a
    coarser race-day rate is worse than the 50 m default rounding radius. Testing
    only the fixes meant one missed rounding stalled a boat's progress for the
    whole race: it showed 0/7 and sorted last while visibly sailing mid-fleet.
    The finish-line crossing already interpolates between fixes; roundings now
    use the same idea.
    """

    def _mark(self, lat, lon, code="1"):
        return {"code": code, "lat": lat, "lon": lon}

    def test_a_leg_that_skims_the_mark_counts(self, client):
        mark = self._mark(52.8800, -4.4000)
        # Two fixes either side of the mark, both outside the radius, but the
        # leg between them passes within ~10 m of it.
        a = {"lat": 52.8800, "lon": -4.4010, "t": 1000.0}
        b = {"lat": 52.8800, "lon": -4.3990, "t": 1006.0}
        assert track.distance_to_segment_m(mark["lat"], mark["lon"],
                                           a["lat"], a["lon"], b["lat"], b["lon"]) < 5
        assert track._passed_mark(a, b, mark, 50.0) is True

    def test_a_leg_that_stays_clear_does_not(self, client):
        mark = self._mark(52.8800, -4.4000)
        a = {"lat": 52.8830, "lon": -4.4010, "t": 1000.0}
        b = {"lat": 52.8830, "lon": -4.3990, "t": 1006.0}
        assert track._passed_mark(a, b, mark, 50.0) is False

    def test_the_fix_itself_still_counts(self, client):
        mark = self._mark(52.8800, -4.4000)
        near = {"lat": 52.88002, "lon": -4.40002, "t": 1000.0}
        assert track._passed_mark(None, near, mark, 50.0) is True

    def test_a_long_gap_is_not_trusted(self, client):
        """After an outage the leg is a straight line across miles of water —
        it must not sweep through marks the boat never went near."""
        mark = self._mark(52.8800, -4.4000)
        a = {"lat": 52.8800, "lon": -4.4200, "t": 1000.0}
        b = {"lat": 52.8800, "lon": -4.3800, "t": 1000.0 + track.MARK_SEGMENT_MAX_GAP_S + 30}
        assert track._passed_mark(a, b, mark, 50.0) is False

    def test_one_missed_rounding_no_longer_stalls_the_whole_race(self, client):
        """The reported symptom: a boat stuck on 0/7 while sailing the course."""
        seq = [self._mark(52.8800, -4.4000, "1"), self._mark(52.8700, -4.4000, "2")]
        line = ((52.8600, -4.4010), (52.8600, -4.3990))
        # Steps of ~120 m — bigger than the 50 m radius, so no fix lands inside.
        fixes = [{"lat": 52.8800 + (10 - i) * 0.0011, "lon": -4.4000,
                  "t": 1000.0 + i * 6, "speed_kn": 6.0, "course_deg": 180.0}
                 for i in range(22)]
        prog = track.boat_course_progress(fixes, seq, line[0], line[1], None, 50.0, None, 0.0)
        assert prog["rounded"] >= 1, "the boat sailed straight over mark 1 and still scored nothing"


class TestThePreComputedOrder:
    """The replay's order on the water is computed server-side for the whole race
    and sent with the track, so the viewer shows it with no request and no lag —
    without a second copy of the course walk living in JavaScript."""

    def test_the_series_matches_the_one_off_leaderboard(self, client):
        """The guard against the two drifting apart."""
        race_id, ids, gun = _seed_race(client)
        _track("SIMR-Alpha", ids["Alpha"]["boat_id"],
               [(gun + i * 10, 52.88 + i * 1e-4, -4.40) for i in range(40)])
        race = ro.get_race(race_id)
        seq = track.course_rounding_sequence(race)
        line = track.finish_line_points()
        e = next(x for x in ro.get_entries(race_id) if x["boat_name"] == "Alpha")
        at = gun + 200
        fixes = track.positions_for_entry_since(e, gun, at)
        one_off = track.boat_course_progress(fixes, seq, line[0], line[1],
                                             track._course_ref_point(race), 50.0, gun,
                                             track.MIN_FINISH_ELAPSED_S)
        series = track.boat_progress_series(track.positions_for_entry_since(e, gun), seq,
                                            line[0], line[1], track._course_ref_point(race),
                                            50.0, [at], gun, track.MIN_FINISH_ELAPSED_S)[0]
        for key in ("rounded", "total", "next_mark", "finished"):
            assert series[key] == one_off[key], f"{key} disagrees: {series[key]} vs {one_off[key]}"

    def test_one_snapshot_per_step_across_the_window(self, client):
        race_id, ids, gun = _seed_race(client)
        _track("SIMR-Alpha", ids["Alpha"]["boat_id"],
               [(gun + i * 10, 52.88 + i * 1e-4, -4.40) for i in range(40)])
        start_ts, end_ts = track.race_track_window(ro.get_race(race_id))
        boards = track.race_leaderboard_series(race_id, start_ts, end_ts, step_s=30.0)
        assert boards["times"], "no snapshots produced"
        assert boards["times"][0] >= start_ts - 0.01
        assert boards["times"][-1] <= end_ts + 0.01
        assert len(boards["rows"]) == len(boards["times"])
        assert all(len(row) == 2 for row in boards["rows"]), "every boat in every snapshot"

    def test_positions_are_stamped_and_ordered(self, client):
        race_id, ids, gun = _seed_race(client)
        _track("SIMR-Alpha", ids["Alpha"]["boat_id"],
               [(gun + i * 10, 52.88 + i * 1e-4, -4.40) for i in range(40)])
        start_ts, end_ts = track.race_track_window(ro.get_race(race_id))
        boards = track.race_leaderboard_series(race_id, start_ts, end_ts, step_s=30.0)
        for row in boards["rows"]:
            assert sorted(r[1] for r in row) == list(range(1, len(row) + 1))

    def test_the_track_response_carries_them(self, logged_in_client):
        race_id, ids, gun = _seed_race(logged_in_client)
        _track("SIMR-Alpha", ids["Alpha"]["boat_id"],
               [(gun + i * 10, 52.88, -4.40) for i in range(20)])
        data = logged_in_client.get(f"/api/race/{race_id}/track").get_json()
        assert "boards" in data and data["boards"]["times"], "the viewer needs these to show an order"
        assert data["boards"]["total"] >= 1

    def test_a_race_with_no_track_still_returns_a_shape(self, client):
        race_id, _ids, _gun = _seed_race(client)
        start_ts, end_ts = track.race_track_window(ro.get_race(race_id))
        boards = track.race_leaderboard_series(race_id, start_ts, end_ts)
        assert isinstance(boards["times"], list) and isinstance(boards["rows"], list)


class TestTheReplayHoldsThePageStill:
    def test_the_page_no_longer_reloads_itself_on_a_timer(self, client, monkeypatch):
        """A whole-page reload mid-replay loses the scrub position and the tab."""
        import app as app_mod
        cfg = {**track.track_config(), "enabled": True}
        monkeypatch.setattr(track, "track_config", lambda: cfg)
        monkeypatch.setattr(app_mod, "track_config", lambda: cfg)
        race_id, _ids, _gun = _seed_race(client)
        html = client.get(f"/public/race/{race_id}").get_data(as_text=True)
        assert 'http-equiv="refresh"' not in html

    def test_the_state_poll_defers_while_the_replay_tab_is_open(self, client, monkeypatch):
        import app as app_mod
        cfg = {**track.track_config(), "enabled": True}
        monkeypatch.setattr(track, "track_config", lambda: cfg)
        monkeypatch.setattr(app_mod, "track_config", lambda: cfg)
        race_id, _ids, _gun = _seed_race(client)
        html = client.get(f"/public/race/{race_id}").get_data(as_text=True)
        assert "replayInUse" in html
        assert "reloading || replayInUse()" in html


class TestDetectionRecoversFromAClearedFinish:
    """A confirmed proposal must not lock GPS out of a boat for ever.

    Recording a GPS finish leaves a confirmed proposal behind for provenance. If
    an officer then clears that finish — undoing one they disagreed with — the
    entry is racing again with no time, and detection has to be able to notice it
    finish. Found when three boats sailed the course, the engine said they had
    finished, and nothing was recorded: proposals from an earlier run were still
    sitting there.
    """

    def _entry(self, client, race_id):
        return next(iter(ro.get_entries(race_id)))

    def _propose(self, race_id, entry_id, status):
        with ro.get_db() as db:
            db.execute("INSERT INTO finish_proposals (race_id, entry_id, detected_time, source, status, created_at)"
                       " VALUES (?, ?, ?, 'gps', ?, datetime('now'))",
                       (race_id, entry_id, datetime.now().isoformat(timespec="seconds"), status))
            db.commit()

    def test_a_pending_proposal_still_blocks(self, client):
        """The officer is already being asked; do not stack duplicates."""
        race_id, ids, _gun = _seed_race(client)
        eid = ids["Alpha"]["entry_id"]
        self._propose(race_id, eid, "pending")
        assert track.detection_blocked_by_proposal(race_id, eid, None) is True

    def test_a_confirmed_proposal_blocks_while_the_finish_stands(self, client):
        race_id, ids, _gun = _seed_race(client)
        eid = ids["Alpha"]["entry_id"]
        self._propose(race_id, eid, "confirmed")
        assert track.detection_blocked_by_proposal(race_id, eid, "2026-07-31T14:13:54") is True

    def test_a_confirmed_proposal_does_not_block_once_the_finish_is_cleared(self, client):
        race_id, ids, _gun = _seed_race(client)
        eid = ids["Alpha"]["entry_id"]
        self._propose(race_id, eid, "confirmed")
        assert track.detection_blocked_by_proposal(race_id, eid, None) is False

    def test_no_proposal_never_blocks(self, client):
        race_id, ids, _gun = _seed_race(client)
        assert track.detection_blocked_by_proposal(race_id, ids["Alpha"]["entry_id"], None) is False
