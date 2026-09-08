"""The dashboard map: the marks, and whoever is actually on the water.

The dashboard could say a race was on and what the wind was doing, but not
whether anything was out there. This is that glance — no course, no leaderboard,
those belong to a race's own chart.

Two of these guard mistakes made building it, both of which looked like the map
was broken rather than the page:

* Leaflet's own stylesheet has to be on the page. Without it the map panes are
  `position: static`, so the tiles flow down the page as a broken mosaic and
  every marker lands about 3000px below the map. Nothing errors.
* The zoom has to ignore marks laid across the Irish Sea for a passage race.
  Fitting to a mark 115 km out put the whole peninsula on screen and shrank the
  racing area to a smudge.
"""
from __future__ import annotations

import time

import pytest

import app as ro
import core.track as track


@pytest.fixture
def two_trackers(client, monkeypatch):
    """One tracker seen a minute ago, one seen yesterday."""
    now = time.time()
    monkeypatch.setattr(track, "list_trackers",
                        lambda: [{"unique_id": "FRESH"}, {"unique_id": "STALE"}])
    monkeypatch.setattr(track, "latest_positions", lambda: {
        "FRESH": {"lat": 52.88, "lon": -4.40, "fix_time": now - 60, "course_deg": 90},
        "STALE": {"lat": 53.40, "lon": -3.00, "fix_time": now - 86400, "course_deg": 0},
    })
    monkeypatch.setattr(track, "_boat_labels_by_unique_id",
                        lambda: {"FRESH": "GBR1", "STALE": "GBR2"})
    return now


class TestWhoCountsAsOnTheWater:
    def test_without_a_limit_every_tracker_is_returned(self, two_trackers):
        """What the Trackers page wants: it is the page you go to when a tracker
        is *not* reporting, so a silent one must still be listed."""
        uids = {m["unique_id"] for m in track.tracker_markers()}
        assert uids == {"FRESH", "STALE"}

    def test_an_hour_drops_the_one_that_stopped_reporting(self, two_trackers):
        markers = track.tracker_markers(max_age_s=3600)
        assert [m["unique_id"] for m in markers] == ["FRESH"]

    def test_the_one_that_is_out_keeps_its_position_and_heading(self, two_trackers):
        m = track.tracker_markers(max_age_s=3600)[0]
        assert (m["lat"], m["lon"]) == (52.88, -4.40)
        assert m["cog"] == 90
        assert m["sail_no"] == "GBR1"

    def test_a_tracker_with_no_fix_time_is_not_counted_as_out(self, client,
                                                              monkeypatch):
        """No timestamp is not evidence of being out there; without this it would
        drag the zoom to wherever it last was, forever."""
        monkeypatch.setattr(track, "list_trackers", lambda: [{"unique_id": "NOFIX"}])
        monkeypatch.setattr(track, "latest_positions", lambda: {
            "NOFIX": {"lat": 52.88, "lon": -4.40, "fix_time": None}})
        monkeypatch.setattr(track, "_boat_labels_by_unique_id", lambda: {})
        assert track.tracker_markers(max_age_s=3600) == []
        assert len(track.tracker_markers()) == 1


class TestTheEndpoint:
    def test_it_answers_with_the_last_hour(self, logged_in_client, two_trackers):
        resp = logged_in_client.get("/api/dashboard/trackers")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True
        assert [m["unique_id"] for m in body["markers"]] == ["FRESH"]

    def test_it_is_never_cached(self, logged_in_client, two_trackers):
        """It is polled for a live picture; a cached one is worse than none."""
        resp = logged_in_client.get("/api/dashboard/trackers")
        assert "no-store" in resp.headers.get("Cache-Control", "")

    def test_it_needs_a_login(self, client):
        resp = client.get("/api/dashboard/trackers",
                          headers={"Accept": "application/json"})
        assert resp.status_code == 401


class TestThePage:
    def test_the_map_is_on_the_dashboard(self, logged_in_client):
        html = logged_in_client.get("/admin").get_data(as_text=True)
        assert 'id="dashboardMap"' in html
        assert "dashboard_map.js" in html

    def test_leaflet_s_stylesheet_is_loaded(self, logged_in_client):
        """The bug that made the map look broken rather than absent: without it
        the panes are position:static and everything scatters down the page."""
        html = logged_in_client.get("/admin").get_data(as_text=True)
        assert "leaflet.css" in html
        assert "leaflet@1.9.4/dist/leaflet.js" in html

    def test_it_sits_below_the_race_and_wind_cards(self, logged_in_client):
        html = logged_in_client.get("/admin").get_data(as_text=True)
        assert html.index("dashboard-race-wind-layout") < html.index('id="dashboard-map"')

    def test_it_carries_the_marks_and_the_line_to_anchor_the_zoom(self,
                                                                 logged_in_client):
        """data-bridge-lat/lon is what "near the racing area" is measured from."""
        html = logged_in_client.get("/admin").get_data(as_text=True)
        panel = html.split('id="dashboardMap"')[1].split(">")[0]
        for attr in ("data-all-marks", "data-bridge-lat", "data-bridge-lon",
                     "data-trackers-url"):
            assert attr in panel, attr
