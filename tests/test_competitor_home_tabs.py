"""The public competitor home page: Races / Wind / Live camera tabs.

The races tab lists the whole current year grouped into series, rolled up except
for the current series, so a season stays scannable. The camera starts when its
tab is opened rather than behind a checkbox.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import app as ro


def _series(name):
    now = datetime.now().isoformat(timespec="seconds")
    with ro.get_db() as db:
        cur = db.execute(
            "INSERT INTO race_series (name, description, created_at, updated_at) VALUES (?, '', ?, ?)",
            (name, now, now),
        )
        db.commit()
        return int(cur.lastrowid)


def _race(name, *, series_id=None, when=None, racing=True, year=None):
    when = when or datetime.now() + timedelta(minutes=20)
    if year:
        when = when.replace(year=year)
    with ro.get_db() as db:
        cur = db.execute(
            "INSERT INTO races (name, class_name, course_no, start_time, series_id, rating_rule, notes, created_at)"
            " VALUES (?, 'IRC', ?, ?, ?, 'IRC_TCC', '', ?)",
            (name, ro.appstate.COURSES[0]["course_no"], when.isoformat(timespec="seconds"),
             series_id, when.isoformat(timespec="seconds")),
        )
        race_id = int(cur.lastrowid)
        db.execute(
            "INSERT INTO entries (race_id, boat_name, sail_no, status, finish_time) VALUES (?, 'Boat', 'GBR1', ?, ?)",
            (race_id, "RACING" if racing else "FINISHED",
             None if racing else when.isoformat(timespec="seconds")),
        )
        db.commit()
        return race_id


class TestHomeTabs:
    def test_three_tabs(self, client):
        html = client.get("/public/current").get_data(as_text=True)
        for tab in ('data-tab="htab-races"', 'data-tab="htab-wind"', 'data-tab="htab-camera"'):
            assert tab in html, tab
        assert 'class="tab-pane active" id="htab-races"' in html
        # The panels keep their ids inside the panes.
        for panel in ('id="competitorRacesPanel"', 'id="competitorWindPanel"', 'id="competitorCameraPanel"'):
            assert panel in html, panel

    def test_camera_needs_no_checkbox_and_is_not_hidden(self, client):
        html = client.get("/public/current").get_data(as_text=True)
        assert 'id="showPublicCamera"' not in html          # the checkbox is gone
        assert 'id="publicCameraWrap"' in html
        assert '<div class="live-video-frame-wrap" id="publicCameraWrap" hidden>' not in html

    def test_wind_plot_is_the_tall_variant(self, client):
        html = client.get("/public/current").get_data(as_text=True)
        assert 'class="wind-history-canvas tall"' in html

    def test_tall_canvas_has_css(self, client):
        css = client.get("/static/style.css").get_data(as_text=True)
        assert ".wind-history-canvas.tall" in css


def _groups(current_series_id):
    """public_year_race_groups builds public race URLs, so it needs a request context."""
    with ro.app.test_request_context("/public/current"):
        return ro.public_year_race_groups(ro.get_current_competitor_race(), current_series_id)


class TestYearRaceGroups:
    def test_groups_this_years_races_by_series(self, client):
        spring = _series("Spring Series")
        summer = _series("Summer Series")
        _race("Spring 1", series_id=spring, when=datetime.now() - timedelta(days=30), racing=False)
        _race("Summer 1", series_id=summer, when=datetime.now() + timedelta(minutes=20))
        html = client.get("/public/current").get_data(as_text=True)
        assert "Spring Series" in html and "Summer Series" in html
        assert f"Races in {datetime.now().year}" in html

    def test_current_series_is_open_and_the_others_are_rolled_up(self, client):
        old = _series("Winter Series")
        current = _series("Autumn Series")
        _race("Winter 1", series_id=old, when=datetime.now() - timedelta(days=60), racing=False)
        _race("Autumn 1", series_id=current, when=datetime.now() + timedelta(minutes=20))
        groups = _groups(current)
        by_title = {g["title"]: g for g in groups}
        assert by_title["Autumn Series"]["open"] is True
        assert by_title["Winter Series"]["open"] is False
        # The open group is listed first.
        assert groups[0]["title"] == "Autumn Series"

    def test_races_without_a_series_get_their_own_group(self, client):
        _race("One-off race", series_id=None)
        groups = _groups(None)
        titles = [g["title"] for g in groups]
        assert "Races not in a series" in titles

    def test_other_years_are_left_out(self, client):
        series = _series("This Year")
        _race("This year race", series_id=series)
        _race("Last year race", series_id=series, when=datetime.now() - timedelta(days=30),
              racing=False, year=datetime.now().year - 1)
        groups = _groups(series)
        names = [card["race"]["name"] for group in groups for card in group["cards"]]
        assert "This year race" in names
        assert "Last year race" not in names

    def test_the_current_race_is_highlighted(self, client):
        series = _series("Live Series")
        race_id = _race("Live race", series_id=series)
        html = client.get("/public/current").get_data(as_text=True)
        assert "highlight-row" in html
        assert "Current race" in html
        assert f"/public/race/{race_id}" in html


class TestHomeHeaderInstruments:
    """The race-information header carries the wind dial and the countdown."""

    def test_wind_gauge_is_the_shared_dial(self, client):
        html = client.get("/public/current").get_data(as_text=True)
        assert 'id="publicWindGauge"' in html
        assert 'class="wind-gauge public-wind-gauge"' in html
        assert 'class="wind-dial"' in html            # same SVG as the dashboard
        assert "wind_gauge.js" in html                # driven by the shared script
        # It sits in the header, above the tabs.
        assert html.index('id="publicWindGauge"') < html.index('data-tab="htab-races"')

    def test_gauge_carries_the_server_rendered_wind(self, client):
        with ro.get_db() as db:
            db.execute(
                "INSERT INTO weather_samples (sample_time, sample_iso, twd, tws_kt, gust_kt, source, raw_json)"
                " VALUES (?, ?, 212.4, 11.4, 15.0, 'test', '{}')",
                (__import__("time").time(), datetime.now().isoformat(timespec="seconds")),
            )
            db.commit()
        html = client.get("/public/current").get_data(as_text=True)
        assert 'data-twd="212.4"' in html
        assert 'data-tws="11.4"' in html

    def test_gauge_does_not_publish_weather_station_messages(self, client):
        """Raw station errors belong on the Wind tab, not under the public dial."""
        html = client.get("/public/current").get_data(as_text=True)
        gauge = html[html.index('id="publicWindGauge"'):html.index('id="publicWindGaugeStatus"')]
        assert "data-status-target" not in gauge
        assert "Wind at the start hut" in html

    def test_countdown_shown_for_the_current_race(self, client):
        _race("Countdown race", when=datetime.now() + timedelta(minutes=12))
        html = client.get("/public/current").get_data(as_text=True)
        assert 'id="publicHomeCountdown"' in html
        assert 'data-race-finished="0"' in html
        assert "countdown.js" in html                 # shared formatter
        assert 'id="publicHomeCountdownTime"' in html

    def test_countdown_marks_a_finished_race(self, client):
        _race("Done race", when=datetime.now() - timedelta(hours=2), racing=False)
        html = client.get("/public/current").get_data(as_text=True)
        assert 'data-race-finished="1"' in html

    def test_no_countdown_without_a_race(self, client):
        html = client.get("/public/current").get_data(as_text=True)
        assert 'id="publicHomeCountdown"' not in html
        assert 'id="publicWindGauge"' in html          # the dial still shows

    def test_dial_filter_id_is_per_gauge(self, client):
        """Two dials on one page must not share the drop-shadow filter id."""
        html = client.get("/public/current").get_data(as_text=True)
        assert 'id="publicWindGaugeShadow"' in html
        assert 'url(#publicWindGaugeShadow)' in html
