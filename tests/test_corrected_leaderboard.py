"""Estimating a finish time mid-race, and correcting it for handicap.

A competitor watching the chart wants to know who is *winning*, not who is in
front. That needs a projected finish time for every boat still racing, and there
is no honest way to get one — so the three methods here are offered as a choice
and all are labelled an estimate:

* **polar pace factor** — how the boat's time so far compares with the target
  time for the legs it has sailed, applied to the legs it has not. Sound while
  the remaining course is a mix of points of sail, because it compares like with
  like: a slow beat is measured against a slow beat.
* **recent pace** — how fast the boat has actually been closing on the finish,
  over a window long enough that a tacking duel does not read as a stop.
* **average pace since the start** — how much of the course the boat has covered
  and how long that has taken. The default.

That last one was dismissed here as "the naive alternative", biased by whatever
the boat has just been doing — a fleet that has finished a beat looks slow for
the whole of the run home. The bias is real and it does not matter, which is the
interesting part: measured over the night race of 2026-08-08 its median error was
the LARGER (58 minutes against 28) and it called the finishing order right in 99%
of snapshots against 63-83%. The bias is common-mode - every boat has just sailed
the same beat - and a board ranks differences, so a shared error cancels where a
per-boat one does not.

The other two remain on offer. The polar factor is the only one that knows the
legs ahead are a different point of sail from the legs behind; it just needs a
wind, and the club measures wind on the hut, which is not where the boats are.
"""
from __future__ import annotations

import pathlib

import pytest

import core.track as track


class TestPolarPaceFactor:
    """A three-leg course: 1.0 nm beat (20 min), 1.0 nm reach (10), 1.0 nm run (15)."""

    MODEL = [{"distance_nm": 1.0, "minutes": 20.0},
             {"distance_nm": 1.0, "minutes": 10.0},
             {"distance_nm": 1.0, "minutes": 15.0}]

    def test_a_boat_exactly_on_its_polar_projects_the_polar_time(self):
        # At the windward mark after 20 minutes: 20 of the 45 target minutes done.
        est = track.estimate_elapsed_by_pace(20 * 60, 1, None, self.MODEL)
        assert round(est / 60, 1) == 45.0

    def test_the_beat_does_not_make_the_rest_of_the_course_look_slow(self):
        """The bias this method exists to avoid.

        A boat takes 30 minutes over the 1 nm beat, so its average speed is 2 kn.
        Carrying 2 kn over the 2 nm still to sail projects 30 + 60 = 90 minutes —
        but the 2 kn was a beat, and a reach and a run are still to come.

        Pace compares like with like: 30 minutes against a 20-minute target leg is
        1.5x the polar, so the whole 45-minute course scales to 67.5.
        """
        est = track.estimate_elapsed_by_pace(30 * 60, 1, None, self.MODEL)
        assert round(est / 60, 1) == 67.5
        naive_minutes = 30 + (2.0 / 2.0) * 60      # 2 nm to go at the 2 kn average
        assert round(naive_minutes, 1) == 90.0

    def test_part_way_along_a_leg_counts_the_part_sailed(self):
        # Half way up the beat (0.5 nm to go) after 10 minutes: 10 of 45 done.
        est = track.estimate_elapsed_by_pace(10 * 60, 0, 0.5, self.MODEL)
        assert round(est / 60, 1) == 45.0

    def test_nothing_sailed_yet_gives_no_estimate(self):
        assert track.estimate_elapsed_by_pace(60, 0, 1.0, self.MODEL) is None

    def test_no_polar_model_gives_no_estimate(self):
        assert track.estimate_elapsed_by_pace(20 * 60, 1, None, []) is None


class TestVmc:
    def test_closing_speed_carries_the_distance_left(self):
        # Closed 0.5 nm in the 5-minute window = 6 kn; 3 nm left = 30 minutes more.
        est = track.estimate_elapsed_by_vmc(20 * 60, 3.0, 3.5, 300.0)
        assert round(est / 60, 1) == 50.0

    def test_a_boat_going_the_wrong_way_gets_no_estimate(self):
        """On the wrong tack the distance to the mark grows; projecting that
        would put the boat's finish somewhere in the last century."""
        assert track.estimate_elapsed_by_vmc(20 * 60, 3.5, 3.0, 300.0) is None

    def test_barely_moving_gets_no_estimate(self):
        # 0.01 nm in five minutes is 0.12 kn: an hours-long projection from noise.
        assert track.estimate_elapsed_by_vmc(20 * 60, 3.0, 3.01, 300.0) is None


class TestTheEstimateIsWithheldEarly:
    def test_ten_minutes_is_the_threshold(self):
        """The YB Tracking convention, and the reason for it: before the first
        mark a boat's pace is mostly which end of the line it started."""
        assert track.ESTIMATE_AFTER_S == 600.0

    def test_the_recent_window_is_long_enough_not_to_divide_by_nothing(self):
        """Five minutes was the original and it is too short.

        Measured over the night race of 2026-08-08, the worst finish estimate a
        window produced was 32218 minutes at 5 minutes, 994 at 10, and 425 at 20 —
        a boat momentarily not closing on the course divides by nearly nothing.
        Longer ranks better too: the finishing order was right in 63%, 70% and 83%
        of snapshots respectively.
        """
        assert track.VMC_WINDOW_S >= 1200.0


class TestRatingFactors:
    """The factor is worked out server-side so the browser only multiplies."""

    def _factors(self, client, irc=None, ytc=None):
        import app as ro
        from datetime import datetime
        now = datetime.now().isoformat(timespec="seconds")
        with ro.get_db() as db:
            cur = db.execute("INSERT INTO races (name, course_no, start_time, created_at)"
                             " VALUES ('Rated', 1, ?, ?)", (now, now))
            race_id = int(cur.lastrowid)
            cur = db.execute("INSERT INTO boats (boat_name, sail_no, status, created_at, updated_at)"
                             " VALUES ('Rated', 'GBR1R', 'ACTIVE', ?, ?)", (now, now))
            boat_id = int(cur.lastrowid)
            db.execute("INSERT INTO entries (race_id, boat_id, boat_name, sail_no, status,"
                       " manual_irc_rating, manual_ytc_rating) VALUES (?, ?, 'Rated', 'GBR1R', 'RACING', ?, ?)",
                       (race_id, boat_id, irc, ytc))
            db.commit()
            race = db.execute("SELECT * FROM races WHERE id=?", (race_id,)).fetchone()
        entries = ro.get_entries(race_id)
        return next(iter(track.entry_rating_factors(race, entries).values()))

    def test_irc_corrects_by_multiplying_by_tcc(self, client):
        assert self._factors(client, irc=1.021)["irc_factor"] == 1.021

    def test_ytc_corrects_by_a_thousand_over_the_rating(self, client):
        # 1000 / 1015, the same arithmetic the published results use.
        assert self._factors(client, ytc=1015)["ytc_factor"] == round(1000 / 1015, 6)

    def test_an_unrated_boat_has_no_factor(self, client):
        info = self._factors(client)
        assert info["irc_factor"] is None and info["ytc_factor"] is None


class TestThePickerIsOnThePage:
    def test_the_chart_tab_offers_the_three_boards(self, client, monkeypatch):
        import app as app_mod
        from tests.test_race_replay import _seed_race
        cfg = {**track.track_config(), "enabled": True}
        monkeypatch.setattr(track, "track_config", lambda: cfg)
        monkeypatch.setattr(app_mod, "track_config", lambda: cfg)
        race_id, _ids, _gun = _seed_race(client)
        html = client.get(f"/public/race/{race_id}").get_data(as_text=True)
        assert 'class="replay-board-mode"' in html
        assert '<option value="irc">' in html and '<option value="ytc">' in html
        # And the choice of estimating method. Average-pace-since-the-start is the
        # default: it needs no wind, and its errors are common-mode across the
        # fleet, so a shared bias cancels when the board ranks the differences.
        assert 'value="start" selected' in html
        assert 'value="recent"' in html and 'value="pace"' in html


class TestThePhoneCardHasRoomForTheBoatName:
    """On a phone the board becomes one card per boat, two label/value pairs to a
    line. The boat cell holds a name *and* a sail number, and it is not the first
    cell here — the position is the card's heading — so without the full width it
    lapped over the cell beside it and the two overlapped on screen.
    """

    def test_the_boat_cell_asks_for_the_whole_card_width(self):
        js = (pathlib.Path("static") / "race_replay.js").read_text(encoding="utf-8")
        assert 'class="stack-wide" data-label="Boat"' in js

    def test_the_stylesheet_grants_it(self):
        css = (pathlib.Path("static") / "style.css").read_text(encoding="utf-8")
        assert ".stack-table td.stack-wide { grid-column: 1 / -1; }" in css


class TestAverageSinceTheStart:
    """The default estimator: how much of the course, in how long.

    Chosen over a windowed VMC on evidence from the night race of 2026-08-08, and
    not on accuracy — its median error was the *larger* of the two (58 minutes
    against 28) and it still called the finishing order right in 99% of snapshots
    against 63-83%. Its errors are common-mode: at 30 minutes the three boats were
    out by +153, +162 and +166 minutes, all having sailed the same slow first leg.
    A board ranks differences, so a shared bias cancels and a per-boat one does not.
    """

    def test_a_boat_half_way_round_is_projected_to_take_twice_as_long(self):
        assert track.estimate_elapsed_by_vmc_start(3600.0, 10.0, 20.0) == pytest.approx(7200.0)

    def test_a_boat_a_quarter_round_is_projected_to_take_four_times(self):
        assert track.estimate_elapsed_by_vmc_start(1800.0, 15.0, 20.0) == pytest.approx(7200.0)

    def test_it_needs_no_wind_and_no_polar(self):
        """The whole reason it is the default: the club's only wind sensor is on
        the hut, and a boat twelve miles out round a headland is not in that wind.
        The pace factor is a ratio, so a wind SPEED error largely cancels — but a
        DIRECTION error does not, because it changes which legs are beats. Measured
        on this course: 14% spread over 8-26 kn, 44% over all directions."""
        import inspect
        params = list(inspect.signature(track.estimate_elapsed_by_vmc_start).parameters)
        assert params == ["elapsed_s", "dist_remaining_nm", "course_nm"], params

    def test_nothing_is_claimed_before_the_boat_has_covered_anything(self):
        assert track.estimate_elapsed_by_vmc_start(600.0, 20.0, 20.0) is None
        assert track.estimate_elapsed_by_vmc_start(600.0, None, 20.0) is None
        assert track.estimate_elapsed_by_vmc_start(600.0, 10.0, None) is None


class TestOneMethodDrivesTheOrderAndTheTimes:
    """A competitor reads down the board. If the order came from one estimator and
    the finish times beside it from another, the page contradicts itself."""

    def test_the_board_takes_its_estimate_from_a_single_column(self):
        js = (pathlib.Path(__file__).resolve().parent.parent
              / "static" / "race_replay.js").read_text(encoding="utf-8")
        assert "const est = r[estIdx];" in js, "the estimate must come from the selected method"
        assert "r[8] : r[7]" not in js, "no two-way fallback between estimators"

    def test_the_clubhouse_board_does_not_mix_methods_between_boats(self):
        """It used to read `r[7] || r[8]`, so a boat with no polar estimate that
        snapshot was ranked by VMC against boats ranked by the polar."""
        js = (pathlib.Path(__file__).resolve().parent.parent
              / "static" / "bar_display.js").read_text(encoding="utf-8")
        code = " ".join(line for line in js.splitlines()
                        if not line.strip().startswith("//"))
        assert "r[7] || r[8]" not in code, "the fallback is back in the code"
        assert "r[EST_START]" in code


class TestBoatsAreComparedAtTheSameMoment:
    """Trackers do not report together, so the latest fixes are not contemporaries.

    Race 65's fleet ran at 2 s, 10 s and 61 s, and with dropouts the worst gap
    between two boats' last-known positions averaged 136 seconds and reached 306.
    At 6 kn that is 420 m of pure staleness — enough to invert an order, and the
    board was seen swapping boats round while nothing on the water changed.
    """

    def test_a_quiet_boat_is_carried_forward_on_its_last_course_and_speed(self):
        fix = {"t": 1000.0, "lat": 52.8, "lon": -4.5, "sog": 6.0, "cog": 0.0}
        moved = track.dead_reckoned(fix, 60.0)
        north_m = (moved["lat"] - fix["lat"]) * 111132.0
        assert north_m == pytest.approx(6.0 * 1852.0 / 60.0, rel=0.02)
        assert moved["lon"] == pytest.approx(fix["lon"], abs=1e-9)

    def test_it_reads_either_spelling_of_speed_and_course(self):
        """The walk's fixes carry speed_kn/course_deg; board rows carry sog/cog."""
        a = track.dead_reckoned({"t": 0.0, "lat": 52.8, "lon": -4.5,
                                 "sog": 6.0, "cog": 90.0}, 60.0)
        b = track.dead_reckoned({"t": 0.0, "lat": 52.8, "lon": -4.5,
                                 "speed_kn": 6.0, "course_deg": 90.0}, 60.0)
        assert a["lon"] == pytest.approx(b["lon"])
        assert a["lon"] > -4.5

    def test_a_boat_long_unheard_is_left_where_it_last_actually_was(self):
        """Past the cap it is missing, not merely quiet, and the viewer is told."""
        fix = {"t": 0.0, "lat": 52.8, "lon": -4.5, "sog": 6.0, "cog": 0.0}
        assert track.dead_reckoned(fix, track.PROJECT_MAX_AGE_S + 1.0) is fix

    def test_the_cap_matches_the_staleness_the_rest_of_the_app_uses(self):
        assert track.PROJECT_MAX_AGE_S == track.ESTIMATE_STALE_S

    def test_a_stopped_boat_is_not_sailed_on_by_the_projection(self):
        fix = {"t": 0.0, "lat": 52.8, "lon": -4.5, "sog": 0.0, "cog": 180.0}
        assert track.dead_reckoned(fix, 60.0) is fix


class TestAnEarlyEstimateIsNotThrownAway:
    """Competitors want the board ten minutes in, and it can answer then.

    The guard against a nonsense estimate was first written as a ceiling of six
    times the elapsed time. That is the wrong unit: early in a long race the true
    answer *is* many times elapsed — ten minutes into a seven-hour race the ratio
    is 42 — so it suppressed every estimate until the race was most of the way
    through. On the club's night race the board stayed blank until 77 minutes when
    it could have answered from 10, and the estimate it was throwing away at 10
    minutes was 6.0 hours against an actual 6.9.
    """

    def test_ten_minutes_into_a_seven_hour_race_still_gets_an_estimate(self):
        elapsed, remaining, est = 600.0, 28.7, 6.0 * 3600
        assert est / elapsed > 30, "the fixture must be the awkward case: est >> elapsed"
        assert track._credible_estimate(est, elapsed, remaining) == est

    def test_the_twenty_two_day_estimate_is_still_refused(self):
        """What the guard is actually for: 32218 minutes for 3 nm is about 0.01 kn."""
        assert track._credible_estimate(32218 * 60.0, 1200.0, 3.0) is None

    def test_it_is_judged_on_implied_speed_not_on_the_ratio_to_elapsed(self):
        """Same huge ratio, opposite verdicts, decided by how far there is to go."""
        elapsed, est = 600.0, 20.0 * 3600      # 120x elapsed either way
        assert track._credible_estimate(est, elapsed, 60.0) == est      # 3.1 kn: sailing
        assert track._credible_estimate(est, elapsed, 0.5) is None      # 0.03 kn: stopped

    def test_an_estimate_already_behind_the_clock_is_left_alone(self):
        """Nothing left to sail: there is no implied speed to judge."""
        assert track._credible_estimate(500.0, 600.0, 0.0) == 500.0


class TestTheReplayClockCountsFromTheGun:
    """Elapsed time in a race is time since your start, and nothing else.

    The replay window opens at the warning signal, five minutes before the first
    gun, and the clock used to count from there. It therefore read 15:00 when the
    race was ten minutes old — which is the exact moment the board starts
    estimating, so the estimates looked five minutes late when they were not.
    """

    def test_the_track_payload_carries_the_first_start(self, client, monkeypatch):
        import app as app_mod
        from tests.test_race_replay import _seed_race
        import routes.competitor as competitor_routes
        cfg = {**track.track_config(), "enabled": True}
        monkeypatch.setattr(track, "track_config", lambda: cfg)
        monkeypatch.setattr(app_mod, "track_config", lambda: cfg)
        # The route module binds its own name at import, so patching app.py alone
        # leaves the endpoint on the "tracking is off" path and the assertions
        # below then test nothing.
        monkeypatch.setattr(competitor_routes, "track_config", lambda: cfg)
        race_id, _ids, gun = _seed_race(client)
        payload = client.get(f"/public/race/{race_id}/track").get_json()
        assert payload.get("enabled") is not False, "the fixture must have tracking on"
        assert payload["first_start"] is not None
        assert payload["first_start"] >= payload["start"], \
            "the gun cannot be before the window that opens ahead of it"

    def test_the_window_opens_before_the_gun(self, client, monkeypatch):
        """Which is the whole reason the two clocks differ."""
        import app as app_mod
        from tests.test_race_replay import _seed_race
        import routes.competitor as competitor_routes
        cfg = {**track.track_config(), "enabled": True}
        monkeypatch.setattr(track, "track_config", lambda: cfg)
        monkeypatch.setattr(app_mod, "track_config", lambda: cfg)
        monkeypatch.setattr(competitor_routes, "track_config", lambda: cfg)
        race_id, _ids, _gun = _seed_race(client)
        payload = client.get(f"/public/race/{race_id}/track").get_json()
        assert payload.get("enabled") is not False
        assert payload["first_start"] - payload["start"] > 0

    def test_the_replay_clock_is_measured_from_the_gun_not_the_window(self):
        js = (pathlib.Path(__file__).resolve().parent.parent
              / "static" / "race_replay.js").read_text(encoding="utf-8")
        code = " ".join(l for l in js.splitlines() if not l.strip().startswith("//"))
        assert "raceElapsedText(clock)" in code
        assert "elapsedText(clock - data.start)" not in code.split("function raceElapsedText")[0], \
            "the displayed clock must not count from the window start"


class TestTheWindSeriesOnTheChart:
    """The hut wind through a race, shipped with the track so the chart's gauge can
    show the wind at whatever moment the viewer has scrubbed to."""

    def _seed(self, start, end, every_s):
        from datetime import datetime
        import app as ro
        with ro.get_db() as db:
            db.execute("DELETE FROM weather_samples")
            t = start
            k = 0
            while t <= end:
                db.execute("INSERT INTO weather_samples (sample_time, sample_iso, twd,"
                           " tws_kt, gust_kt, source, raw_json) VALUES (?,?,?,?,?,?,?)",
                           (t, datetime.fromtimestamp(t).isoformat(timespec="seconds"),
                            200.0 + k, 10.0, 14.0, "test", "{}"))
                t += every_s
                k += 1
            db.commit()
        return k

    def test_it_thins_a_long_race_to_something_a_dial_can_use(self, client):
        """Every few seconds for seven hours would be thousands of points riding
        along with the track for a needle a viewer is watching."""
        start, end = 1_700_000_000.0, 1_700_003_600.0
        raw = self._seed(start, end, 5.0)
        series = track.race_wind_series(start, end)
        assert raw > 500, "the fixture must be densely sampled"
        assert len(series) < raw / 4
        gaps = [b[0] - a[0] for a, b in zip(series, series[1:])]
        assert min(gaps) >= track.WIND_SERIES_STEP_S

    def test_each_row_carries_direction_speed_and_gust(self, client):
        start, end = 1_700_000_000.0, 1_700_000_600.0
        self._seed(start, end, 30.0)
        row = track.race_wind_series(start, end)[0]
        assert len(row) == 4
        t, twd, tws, gust = row
        assert start <= t <= end
        assert twd == 200.0 and tws == 10.0 and gust == 14.0

    def test_a_race_with_no_stored_wind_gets_an_empty_series(self, client):
        import app as ro
        with ro.get_db() as db:
            db.execute("DELETE FROM weather_samples")
            db.commit()
        assert track.race_wind_series(1_700_000_000.0, 1_700_003_600.0) == []


class TestTheReplayDrawsEveryFrameToTheEnd:
    """A regression that shipped in v0.261 and was found while building the gauge.

    ``raceElapsedText`` was declared at module scope but reads ``data``, which is a
    closure variable of ``init()``. It threw on every frame, and it is called two
    lines into ``drawFrame`` — so the clock updated and then the slider, the
    leaderboard and everything drawn after it did not. The board was empty and the
    elapsed readout stuck at 0:00 while the chart itself looked fine.
    """

    def _js(self):
        return (pathlib.Path(__file__).resolve().parent.parent
                / "static" / "race_replay.js").read_text(encoding="utf-8")

    def test_the_elapsed_helper_can_see_the_data_it_reads(self):
        js = self._js()
        init_at = js.index("function init(")
        assert js.index("function raceElapsedText(") > init_at, \
            "raceElapsedText reads `data`, so it must be declared inside init()"

    def test_every_helper_that_reads_data_is_inside_init(self):
        """The general form of the same mistake, for the next one added."""
        import re
        js = self._js()
        init_at = js.index("function init(")
        for m in re.finditer(r"^  function (\w+)\(([^)]*)\) \{", js[:init_at], re.M):
            body = js[m.end():js.index("\n  }", m.end())]
            assert not re.search(r"\bdata\b", body), \
                f"{m.group(1)}() is at module scope but reads `data`"
