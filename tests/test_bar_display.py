"""The clubhouse TV: when it cuts from the chart to the camera.

The page itself is a chart with overlays, but the one piece of real logic is
deciding when there is something worth watching. The start is a known time. A
finish is not — a boat arrives when it arrives — so "two minutes before
finishing" is predicted from the distance it still has to sail and the speed it
is making. Crude over a whole race, fine over the last two minutes: the boat is
on its final approach with the line in sight.
"""
from __future__ import annotations

import pathlib

import core.bardisplay as bar


def _boat(name="Kite", dist=None, sog=None, finished=False, finish_epoch=None):
    return {"boat_name": name, "dist_remaining_nm": dist, "sog": sog,
            "finished": finished, "finish_epoch": finish_epoch}


class TestTheEtaToTheLine:
    def test_distance_over_speed(self):
        # 0.5 nm to go at 6 knots is five minutes.
        assert bar.finish_eta_seconds(_boat(dist=0.5, sog=6.0)) == 300.0

    def test_a_boat_barely_moving_has_no_estimate(self):
        """A tenth of a knot would put the arrival half an hour out, or never —
        and a boat that slow is not on a final approach."""
        assert bar.finish_eta_seconds(_boat(dist=0.1, sog=0.1)) is None

    def test_a_finished_boat_has_no_eta(self):
        assert bar.finish_eta_seconds(_boat(finished=True)) is None

    def test_missing_figures_give_no_estimate(self):
        assert bar.finish_eta_seconds(_boat(dist=None, sog=6.0)) is None
        assert bar.finish_eta_seconds(_boat(dist=0.2, sog=None)) is None


class TestTheStartWindow:
    GUN = 1_000_000.0

    def test_it_comes_up_two_minutes_before_the_gun(self):
        w = bar.video_window(self.GUN, [], self.GUN - 119)
        assert w["show"] and w["reason"] == "start"

    def test_it_stays_two_minutes_after(self):
        assert bar.video_window(self.GUN, [], self.GUN + 119)["show"]

    def test_it_is_off_before_and_after_that(self):
        assert not bar.video_window(self.GUN, [], self.GUN - 121)["show"]
        assert not bar.video_window(self.GUN, [], self.GUN + 121)["show"]

    def test_with_no_start_time_there_is_no_start_window(self):
        assert not bar.video_window(None, [], self.GUN)["show"]

    def test_the_caption_counts_down_to_the_gun(self):
        w = bar.video_window(self.GUN, [], self.GUN - 95)
        assert bar.caption_for(w) == "Start in 1:35"


class TestTheFinishWindow:
    NOW = 2_000_000.0

    def test_a_boat_two_minutes_out_brings_the_camera_up(self):
        # 0.2 nm at 6 kn = 120 s.
        rows = [_boat("Kite", dist=0.2, sog=6.0)]
        w = bar.video_window(None, rows, self.NOW)
        assert w["show"] and w["reason"] == "finishing" and w["boat"] == "Kite"

    def test_a_boat_further_out_does_not(self):
        rows = [_boat("Kite", dist=1.0, sog=6.0)]          # ten minutes away
        assert not bar.video_window(None, rows, self.NOW)["show"]

    def test_it_holds_for_a_minute_after_the_boat_crosses(self):
        rows = [_boat("Kite", finished=True, finish_epoch=self.NOW - 59)]
        w = bar.video_window(None, rows, self.NOW)
        assert w["show"] and w["reason"] == "finished" and w["boat"] == "Kite"

    def test_and_drops_after_that(self):
        rows = [_boat("Kite", finished=True, finish_epoch=self.NOW - 61)]
        assert not bar.video_window(None, rows, self.NOW)["show"]

    def test_the_closest_boat_is_the_one_named(self):
        rows = [_boat("Far", dist=0.2, sog=6.0),           # 120 s
                _boat("Near", dist=0.05, sog=6.0)]         # 30 s
        assert bar.video_window(None, rows, self.NOW)["boat"] == "Near"

    def test_a_finish_that_has_happened_beats_one_still_coming(self):
        """People look up at the screen for the boat that just crossed."""
        rows = [_boat("Coming", dist=0.05, sog=6.0),
                _boat("Crossed", finished=True, finish_epoch=self.NOW - 5)]
        w = bar.video_window(None, rows, self.NOW)
        assert w["reason"] == "finished" and w["boat"] == "Crossed"

    def test_the_most_recent_crossing_wins_when_several_are_in(self):
        rows = [_boat("First", finished=True, finish_epoch=self.NOW - 50),
                _boat("Second", finished=True, finish_epoch=self.NOW - 10)]
        assert bar.video_window(None, rows, self.NOW)["boat"] == "Second"

    def test_the_caption_names_the_boat(self):
        rows = [_boat("Kite", dist=0.1, sog=6.0)]
        assert bar.caption_for(bar.video_window(None, rows, self.NOW)) \
            == "Kite approaching the finish"


class TestTheStartTakesPrecedence:
    def test_a_start_window_wins_over_a_finishing_boat(self):
        """Only matters on a course short enough for the first boat to be inside
        two minutes of the line while the last is still starting — but then the
        gun is the thing everyone is waiting for."""
        gun = 3_000_000.0
        rows = [_boat("Kite", dist=0.05, sog=6.0)]
        assert bar.video_window(gun, rows, gun + 10)["reason"] == "start"


class TestThePage:
    """A display opened once and left alone, so it has to look after itself."""

    def _seed(self, client, monkeypatch):
        import app as app_mod
        import core.track as track
        from tests.test_race_replay import _seed_race
        cfg = {**track.track_config(), "enabled": True}
        monkeypatch.setattr(track, "track_config", lambda: cfg)
        monkeypatch.setattr(app_mod, "track_config", lambda: cfg)
        return _seed_race(client)[0]

    def test_it_is_reachable_without_signing_in(self, client, monkeypatch):
        """It lives on a television in the bar; nobody is going to log it in."""
        race_id = self._seed(client, monkeypatch)
        assert client.get(f"/bar/{race_id}").status_code == 200

    def test_it_carries_the_chart_the_header_and_the_order(self, client, monkeypatch):
        race_id = self._seed(client, monkeypatch)
        html = client.get(f"/bar/{race_id}").get_data(as_text=True)
        assert 'class="course-map"' in html          # the chart
        assert 'class="bar-top"' in html             # race information across the top
        assert 'class="bar-board"' in html           # the order down the right
        assert 'class="bar-video"' in html           # and the camera over the chart

    def test_it_has_no_controls_to_press(self, client, monkeypatch):
        """Nothing on this page is meant to be clicked — there is no keyboard or
        mouse near it, and a stray press must not be able to break the display."""
        race_id = self._seed(client, monkeypatch)
        html = client.get(f"/bar/{race_id}").get_data(as_text=True)
        for tag in ("<button", "<select", "<input"):
            assert tag not in html

    def test_with_no_race_it_says_so_rather_than_erroring(self, client):
        res = client.get("/bar")
        assert res.status_code in (200, 404)
        assert "No race running" in res.get_data(as_text=True)

    def test_the_state_endpoint_reports_the_camera_window(self, client, monkeypatch):
        race_id = self._seed(client, monkeypatch)
        body = client.get(f"/bar/state/{race_id}").get_json()
        assert body["ok"] is True
        assert set(body["video"]) >= {"show", "reason", "boat", "caption"}

    def test_the_state_endpoint_says_which_race_is_current(self, client, monkeypatch):
        """Left running for a season, the display follows the club from one race
        to the next rather than showing yesterday's finish for ever."""
        race_id = self._seed(client, monkeypatch)
        body = client.get(f"/bar/state/{race_id}").get_json()
        assert "current_race_id" in body and body["race_id"] == race_id


class TestTheClockStopsWhenTheRaceDoes:
    """It counted up for ever: a race that finished at lunchtime was still
    ticking on the bar screen hours later. The state endpoint knows when the last
    boat crossed, so the display can stop there and show the race's elapsed time.
    """

    def test_the_state_endpoint_reports_the_last_finish(self, client, monkeypatch):
        import app as app_mod
        import core.track as track
        from tests.test_race_replay import _seed_race
        cfg = {**track.track_config(), "enabled": True}
        monkeypatch.setattr(track, "track_config", lambda: cfg)
        monkeypatch.setattr(app_mod, "track_config", lambda: cfg)
        race_id = _seed_race(client)[0]
        body = client.get(f"/bar/state/{race_id}").get_json()
        assert "last_finish" in body and "finished" in body

    def test_the_display_stops_the_clock_rather_than_counting_on(self):
        import pathlib
        js = pathlib.Path("static/bar_display.js").read_text(encoding="utf-8")
        assert "raceFinished" in js and "last_finish" in js
        assert "'Finished'" in js


class TestTheBoardCyclesThroughTheLeaderboards:
    """Nobody is going to walk up to a television and change the leaderboard, so
    it shows each one in turn: the order on the water, then every rating system
    the fleet is actually rated in. Overall only — a bar screen is not the place
    to work through the classes one at a time.
    """

    def _js(self):
        import pathlib
        return pathlib.Path("static/bar_display.js").read_text(encoding="utf-8")

    def test_the_boards_offered_come_from_the_fleets_own_ratings(self):
        js = self._js()
        assert "b.irc_factor" in js and "b.ytc_factor" in js
        assert "Order on the water" in js and "IRC corrected" in js and "YTC corrected" in js

    def test_it_moves_on_by_itself(self):
        js = self._js()
        assert "BOARD_CYCLE_MS" in js
        assert "setInterval(cycleBoard" in js

    def test_a_finished_boat_shows_its_real_time_not_a_projection(self):
        """Same rule as the competitor board: the estimate columns already carry
        the real elapsed once a boat is in."""
        js = self._js()
        assert "r[7] || r[8]" in js          # pace, falling back to VMC

    def test_the_corrected_boards_say_they_are_estimates(self):
        js = self._js()
        assert "not a result" in js


class TestTheMapFollowsTheBoatsStillRacing:
    """A whole-course view spends most of a television on empty sea once the
    fleet has strung out down one leg. The display follows the boats that are
    still racing — each of them and the mark each is sailing to, so the room can
    see the leg rather than just the boats on it.
    """

    def _js(self, name):
        import pathlib
        return pathlib.Path(f"static/{name}").read_text(encoding="utf-8")

    def test_finished_boats_are_left_out_of_the_view(self):
        """They are parked by the line and would hold the view open across the
        whole course for the sake of somebody already in the bar."""
        js = self._js("bar_display.js")
        assert "if (finished) return;" in js

    def test_the_mark_each_boat_is_heading_for_is_included(self):
        js = self._js("bar_display.js")
        assert "markLatLon(nextMark)" in js

    def test_with_nobody_racing_it_falls_back_to_the_whole_course(self):
        """Before the start, and once everyone is in."""
        js = self._js("bar_display.js")
        assert "courseMarks" in js and "if (!pts.length)" in js

    def test_the_view_does_not_chase_every_small_movement(self):
        """Called every few seconds for an hour on a screen nobody is watching
        closely; refitting whenever a boat moves a length would leave the map
        permanently in motion."""
        js = self._js("course_map.js")
        assert "const holds = visible.contains(target)" in js
        assert "if (holds && !roomy) return;" in js

    def test_it_zooms_back_out_when_it_is_holding_far_more_than_it_needs(self):
        js = self._js("course_map.js")
        assert "roomy" in js and "slack" in js

    def test_it_fits_to_the_part_of_the_map_that_can_be_seen(self):
        """The header covers the top and the leaderboard the right-hand quarter;
        fitting to the whole element centres the fleet under the board."""
        js = self._js("course_map.js")
        assert "paddingTopLeft" in js and "paddingBottomRight" in js
        assert "containerPointToLatLng" in js
        assert "overlayInsets" in self._js("bar_display.js")

    def test_there_is_a_floor_on_how_far_in_it_will_go(self):
        """A fleet in close company would otherwise fill the screen with one boat
        length of sea."""
        assert "minSpanM" in self._js("course_map.js")
        assert "FIT_MIN_SPAN_M" in self._js("bar_display.js")


class TestAnEmptyBoardIsSkipped:
    def test_a_corrected_board_with_no_times_is_passed_over(self):
        """The estimate is withheld for the first ten minutes of racing, and a
        column of dashes on a television tells the room nothing."""
        import pathlib
        js = pathlib.Path("static/bar_display.js").read_text(encoding="utf-8")
        assert "function boardHasContent" in js
        assert "if (boardHasContent(boardModes[next]))" in js


class TestRoundingTheOdm:
    """Most courses pass mark O more than once — courses 11 and 14 go round it
    three times — and O is the seaward end of the start/finish line, which is
    exactly what the hut camera is pointed at. A boat rounding it mid-race is
    worth cutting to.

    "About a minute either side" is a radius rather than a stopwatch: how far the
    boat travels in a minute at the speed it is making. One test, no history to
    keep, and it covers the approach and the exit symmetrically without having to
    know which one it is looking at.
    """

    MARK = (52.87912, -4.39933)          # the ODM

    def _at(self, metres_north, sog=6.0, rounded=3, finished=False, name="Kite"):
        return {"boat_name": name, "lat": self.MARK[0] + metres_north / 111320.0,
                "lon": self.MARK[1], "sog": sog, "rounded": rounded,
                "finished": finished, "dist_remaining_nm": 3.0}

    def test_a_boat_close_to_the_mark_brings_the_camera_up(self):
        w = bar.video_window(None, [self._at(100)], 0, mark=self.MARK)
        assert w["show"] and w["reason"] == "rounding" and w["boat"] == "Kite"

    def test_a_boat_well_away_from_it_does_not(self):
        assert not bar.video_window(None, [self._at(900)], 0, mark=self.MARK)["show"]

    def test_the_radius_follows_the_boats_speed(self):
        """A minute at 10 knots is over 300 m; a minute at 3 is under 100."""
        fast = bar.video_window(None, [self._at(280, sog=10.0)], 0, mark=self.MARK)
        slow = bar.video_window(None, [self._at(280, sog=3.0)], 0, mark=self.MARK)
        assert fast["show"] and not slow["show"]

    def test_a_drifting_boat_still_gets_a_floor(self):
        """Otherwise a boat parked on the mark in no wind would never show."""
        w = bar.video_window(None, [self._at(120, sog=0.1)], 0, mark=self.MARK)
        assert w["show"]

    def test_it_covers_the_exit_as_well_as_the_approach(self):
        """The same radius test, so a minute after counts like a minute before —
        which is the point of using distance rather than a predicted arrival."""
        assert bar.video_window(None, [self._at(-120)], 0, mark=self.MARK)["show"]

    def test_a_boat_that_has_just_started_is_ignored(self):
        """It is sitting by the ODM because that is where the line is. Without
        this the camera would simply stay up after every start on a course whose
        first leg begins at the line."""
        assert not bar.video_window(None, [self._at(80, rounded=0)], 0, mark=self.MARK)["show"]

    def test_a_finished_boat_parked_by_the_line_does_not_hold_it_up(self):
        assert not bar.video_window(None, [self._at(80, finished=True)], 0,
                                    mark=self.MARK)["show"]

    def test_the_nearest_boat_is_the_one_named(self):
        rows = [self._at(140, name="Far"), self._at(30, name="Near")]
        assert bar.video_window(None, rows, 0, mark=self.MARK)["boat"] == "Near"

    def test_a_finish_takes_precedence_over_a_rounding(self):
        """A rounding is happening now, but a finish is the bigger moment — and
        since the ODM is the end of the line, the camera shows both anyway."""
        rows = [self._at(80, name="Rounding"),
                {"boat_name": "Crossed", "finished": True, "finish_epoch": -5.0}]
        assert bar.video_window(None, rows, 0.0, mark=self.MARK)["reason"] == "finished"

    def test_the_caption_says_which_boat_and_which_mark(self):
        w = bar.video_window(None, [self._at(60)], 0, mark=self.MARK, mark_name="O")
        assert bar.caption_for(w) == "Kite rounding O"

    def test_with_no_mark_position_nothing_changes(self):
        assert not bar.video_window(None, [self._at(20)], 0, mark=None)["show"]


class TestTheWindIsOnScreen:
    """The bar display carried no wind reading at all, which is the first thing
    anyone watching a race asks about. It is now the shared dial from the
    competitor page, floated over the top-left of the chart.
    """

    def _seed(self, client, monkeypatch):
        import app as app_mod
        import core.track as track
        from tests.test_race_replay import _seed_race
        cfg = {**track.track_config(), "enabled": True}
        monkeypatch.setattr(track, "track_config", lambda: cfg)
        monkeypatch.setattr(app_mod, "track_config", lambda: cfg)
        return _seed_race(client)[0]

    def _css(self):
        return (pathlib.Path(bar.__file__).resolve().parent.parent
                / "static" / "style.css").read_text(encoding="utf-8")

    def test_the_dial_is_on_the_page(self, client, monkeypatch):
        race_id = self._seed(client, monkeypatch)
        html = client.get(f"/bar/{race_id}").get_data(as_text=True)
        assert 'class="wind-gauge bar-wind-gauge"' in html
        assert 'class="bar-wind"' in html

    def test_it_uses_the_shared_gauge_rather_than_a_second_one(self, client, monkeypatch):
        """A copy of the dial would be a second thing to keep working. The partial
        and static/wind_gauge.js already serve the dashboard and competitor page."""
        race_id = self._seed(client, monkeypatch)
        html = client.get(f"/bar/{race_id}").get_data(as_text=True)
        assert "wind_gauge.js" in html

    def test_the_needles_start_from_server_rendered_wind(self, client, monkeypatch):
        """So the dial is never blank while the first poll is in flight."""
        race_id = self._seed(client, monkeypatch)
        html = client.get(f"/bar/{race_id}").get_data(as_text=True)
        gauge = html.split('class="wind-gauge bar-wind-gauge"', 1)[1][:400]
        assert "data-twd=" in gauge and "data-tws=" in gauge

    def test_the_gauge_id_is_unique_to_this_page(self, client, monkeypatch):
        """The dial's drop-shadow filter is addressed by id, so two gauges sharing
        one would both take the first one's filter."""
        race_id = self._seed(client, monkeypatch)
        html = client.get(f"/bar/{race_id}").get_data(as_text=True)
        assert html.count('id="barWindGauge"') == 1
        assert 'id="publicWindGauge"' not in html

    def test_it_adds_no_controls_to_a_screen_nobody_touches(self, client, monkeypatch):
        """The existing rule for this page, re-checked now something was added."""
        race_id = self._seed(client, monkeypatch)
        html = client.get(f"/bar/{race_id}").get_data(as_text=True)
        for tag in ("<button", "<select", "<input"):
            assert tag not in html

    def test_it_stays_on_screen_when_the_camera_takes_over(self):
        """A start and a finish are exactly when the room asks what the wind is
        doing, so the dial sits above the camera panel rather than being hidden by
        it. It stays below the header and the leaderboard."""
        css = self._css()

        def z(selector):
            return int(css.split(selector + " {", 1)[1].split("z-index:", 1)[1].split(";", 1)[0])

        assert z(".bar-wind") > z(".bar-video")
        assert z(".bar-wind") < z(".bar-top")
        assert z(".bar-wind") < z(".bar-board")

    def test_it_starts_below_the_header_rather_than_under_it(self):
        """The header is a solid bar across the top; a dial at top 0 would be
        half-hidden behind it."""
        css = self._css()
        block = css.split(".bar-wind {", 1)[1].split("}", 1)[0]
        top = int(block.split("top:", 1)[1].split("px", 1)[0].strip())
        assert top >= 96, "the dial would collide with .bar-top / .bar-board"

    def test_it_never_swallows_a_map_drag(self):
        """Nobody touches this screen, but a transparent box over the chart that
        ate pointer events would be a nuisance on the one occasion somebody does."""
        block = self._css().split(".bar-wind {", 1)[1].split("}", 1)[0]
        assert "pointer-events: none" in block

    def test_it_reads_over_whatever_the_camera_is_pointing_at(self):
        """The dial stays up while the camera is on screen, so it has to survive
        pale map tiles, bright sky (the likely top-left of a start-hut view) and
        dark water. A translucent white card disappears against white sky, so the
        border and shadow are load-bearing, not decoration. Checked by eye against
        blown-out white and dark water."""
        block = self._css().split(".bar-wind {", 1)[1].split("}", 1)[0]
        assert "rgba(255, 255, 255, 0.82)" in block, "matches .bar-top on the same screen"
        assert "box-shadow:" in block, "nothing separates it from bright sky without this"
        assert "border:" in block
