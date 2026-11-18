"""One boat can be picked out on the competitor chart, starred and followed.

The app competitors used before let them tap a boat for its details, keep a list
of favourites and choose how fast to play a race back. The club's own chart drew
every boat alike: a row in the board could be highlighted, and that was all. The
footnote under the chart said "click a boat to highlight its track", and clicking
a boat did nothing.

The moving parts are in the browser (static/race_replay.js, public_follow.js),
and what is tested here is everything they stand on:

* the key a favourite is remembered by, which has to come out the same for a boat
  however its sail number was typed, in every race it enters;
* what the card is allowed to say about a boat -- its design, club, class and
  ratings, and **not its owner's name**, which the boats table also holds;
* that the page hands the script all of it: the keys on every row that names a
  boat, the details as data, the card to fill in, and the controls;
* that the lists the page is drawn with and the lists the script uses are the
  same lists (the speeds; the board's columns).
"""
from __future__ import annotations

import json
import pathlib
import re
from datetime import datetime

import app as ro
from core import publicapp
from tests.test_race_replay import _seed_race

STATIC = pathlib.Path(__file__).resolve().parent.parent / "static"
TEMPLATES = pathlib.Path(__file__).resolve().parent.parent / "templates"


class TestTheFavouriteKey:
    def test_it_is_the_sail_number_however_it_was_typed(self):
        assert publicapp.boat_follow_key("GBR4822R") == "GBR4822R"
        assert publicapp.boat_follow_key(" gbr 4822r ") == "GBR4822R"
        assert publicapp.boat_follow_key("GBR-4822/R") == "GBR4822R"

    def test_a_boat_with_no_sail_number_goes_by_its_name(self):
        assert publicapp.boat_follow_key("", "Sgrech Bach") == "NAMESGRECHBACH"
        assert publicapp.boat_follow_key(None, "Sgrech Bach") == "NAMESGRECHBACH"

    def test_a_name_can_never_be_mistaken_for_a_sail_number(self):
        """A boat called "4483" with no sail number is not sail number 4483."""
        assert publicapp.boat_follow_key("", "4483") != publicapp.boat_follow_key("4483", "Tiller Girl")

    def test_nothing_to_go_by_is_no_key(self):
        assert publicapp.boat_follow_key("", "") == ""
        assert publicapp.boat_follow_key(None, None) == ""
        assert publicapp.boat_follow_key("---", "!!!") == ""


ENTRY = {"id": 7, "boat_id": 3, "boat_name": "MOJITO", "sail_no": "GBR4822R", "class_name": "Class 1",
         "manual_irc_rating": 1.084, "manual_ytc_rating": 807.0}
BOAT = {"id": 3, "design": "J/122E", "club": "Pwllheli SC", "owner": "A Private Person"}


class TestWhatTheCardMayKnow:
    def test_design_club_class_and_ratings(self):
        info = publicapp.public_boat_info([ENTRY], {3: BOAT})["7"]
        assert info["name"] == "MOJITO" and info["sail"] == "GBR4822R"
        assert info["key"] == "GBR4822R"
        assert (info["design"], info["club"], info["class"]) == ("J/122E", "Pwllheli SC", "Class 1")
        assert (info["irc"], info["ytc"]) == ("1.084", "807")

    def test_the_owner_is_not_among_them(self):
        """The race office needs the owner's name. A public page showing where
        the boat is does not, and is not given it."""
        info = publicapp.public_boat_info([ENTRY], {3: BOAT})
        assert "owner" not in info["7"]
        assert "A Private Person" not in json.dumps(info)

    def test_a_series_class_replaces_the_boats_own_division(self):
        info = publicapp.public_boat_info([ENTRY], {3: BOAT}, {7: "IRC 0"})["7"]
        assert info["class"] == "IRC 0"

    def test_a_ytc_factor_is_shown_as_a_factor(self):
        """YTC is published as a three-figure number and as a factor near one."""
        entry = {**ENTRY, "manual_ytc_rating": 0.853}
        assert publicapp.public_boat_info([entry])["7"]["ytc"] == "0.853"

    def test_no_rating_is_no_rating_not_a_nought(self):
        entry = {**ENTRY, "manual_irc_rating": None, "manual_ytc_rating": None}
        info = publicapp.public_boat_info([entry])["7"]
        assert (info["irc"], info["ytc"]) == ("", "")

    def test_an_entry_with_no_boat_record_still_has_a_card(self):
        entry = {**ENTRY, "boat_id": None}
        info = publicapp.public_boat_info([entry], {3: BOAT})["7"]
        assert info["name"] == "MOJITO" and info["design"] == "" and info["club"] == ""

    def test_the_finish_video_is_whatever_the_caller_says_can_be_watched(self):
        info = publicapp.public_boat_info([ENTRY], finish_video_href=lambda e: f"/video/{e['id']}")
        assert info["7"]["video"] == "/video/7"
        assert publicapp.public_boat_info([ENTRY], finish_video_href=lambda e: "")["7"]["video"] == ""

    def test_a_clip_that_cannot_be_linked_does_not_cost_the_card(self):
        def broken(entry):
            raise RuntimeError("no such clip")
        assert publicapp.public_boat_info([ENTRY], finish_video_href=broken)["7"]["video"] == ""


class TestTheRacePageHandsItOver:
    def _tracking(self, monkeypatch, enabled=True):
        import core.track as track
        cfg = {**track.track_config(), "enabled": enabled}
        monkeypatch.setattr(track, "track_config", lambda: cfg)
        monkeypatch.setattr(ro, "track_config", lambda: cfg)

    def _page(self, client, monkeypatch, tracking=True):
        self._tracking(monkeypatch, tracking)
        race_id, ids, _gun = _seed_race(client)
        now = datetime.now().isoformat(timespec="seconds")
        with ro.get_db() as db:
            db.execute("UPDATE boats SET owner = ?, design = ?, club = ?, updated_at = ? WHERE id = ?",
                       ("A Private Person", "J/122E", "Pwllheli SC", now, ids["Alpha"]["boat_id"]))
            db.commit()
        return race_id, ids, client.get(f"/public/race/{race_id}").get_data(as_text=True)

    def test_every_row_that_names_a_boat_carries_its_key(self, client, monkeypatch):
        _race_id, ids, html = self._page(client, monkeypatch)
        entry_id = ids["Alpha"]["entry_id"]
        assert f'<tr data-entry-id="{entry_id}" data-boat-key="GBRALPHA" data-boat-name="Alpha">' in html

    def test_the_details_are_on_the_page_as_data(self, client, monkeypatch):
        _race_id, ids, html = self._page(client, monkeypatch)
        block = re.search(r'<script type="application/json" id="publicBoatInfo">(.*?)</script>', html, re.S)
        assert block, "the boat details are not on the page"
        info = json.loads(block.group(1))
        alpha = info[str(ids["Alpha"]["entry_id"])]
        assert (alpha["key"], alpha["design"], alpha["club"]) == ("GBRALPHA", "J/122E", "Pwllheli SC")

    def test_the_owners_name_is_nowhere_on_the_page(self, client, monkeypatch):
        _race_id, _ids, html = self._page(client, monkeypatch)
        assert "A Private Person" not in html

    def test_the_chart_has_a_card_to_fill_in_and_knows_its_race(self, client, monkeypatch):
        race_id, _ids, html = self._page(client, monkeypatch)
        pane = html.split('id="ptab-chart"', 1)[1].split('id="ptab-analysis"', 1)[0]
        assert f'data-race-id="{race_id}"' in pane
        # The link that is shared is the pinned page, which stays on this race.
        assert f'data-share-url="/public/race/{race_id}"' in pane
        for part in ("boat-card-name", "boat-card-facts", "boat-card-fav", "boat-card-follow",
                     "boat-card-share", "boat-card-video", "boat-card-close", "boat-card-more"):
            assert part in pane, part

    def test_the_card_starts_hidden(self, client, monkeypatch):
        _race_id, _ids, html = self._page(client, monkeypatch)
        assert '<aside class="boat-card" hidden' in html

    def test_speed_and_view_are_choices_from_a_list(self, client, monkeypatch):
        _race_id, _ids, html = self._page(client, monkeypatch)
        pane = html.split('id="ptab-chart"', 1)[1].split('id="ptab-analysis"', 1)[0]
        assert '<select class="replay-speed"' in pane
        view = re.search(r'<select class="replay-view".*?</select>', pane, re.S).group(0)
        assert re.findall(r'<option value="(\w+)"', view) == ["start", "course", "finish", "fleet"]

    def test_the_favourites_script_loads_before_the_chart_asks_for_it(self, client, monkeypatch):
        _race_id, _ids, html = self._page(client, monkeypatch)
        assert html.index("public_follow.js") < html.index("race_replay.js")

    def test_without_tracking_there_is_no_card_but_the_rows_still_take_a_star(self, client, monkeypatch):
        _race_id, _ids, html = self._page(client, monkeypatch, tracking=False)
        assert "boat-card" not in html
        assert 'data-boat-key="GBRALPHA"' in html

    def test_the_competitor_home_says_who_is_being_followed(self, client):
        html = client.get("/public/current").get_data(as_text=True)
        assert 'id="publicFollowLine" hidden' in html
        assert "follow-clear" in html and "public_follow.js" in html


class TestAViewOfBoatsStaysWithThem:
    """View -> Start line is a place: the chart goes there. View -> Favourites
    (or All boats) is boats, and boats move: asked for in November 2026, the
    chart now stays with them as the race plays."""

    def _js(self):
        return (STATIC / "race_replay.js").read_text(encoding="utf-8")

    def test_the_two_groups_are_held_and_the_three_places_are_not(self):
        body = self._js().split("function showView(which)")[1].split("\n    }\n")[0]
        assert "if (which === 'fleet' || which === 'favourites') { setGroupView(which); return; }" in body
        assert body.index("setGroupView(null);") < body.index("fitTo(")

    def test_it_is_looked_at_on_every_frame_but_moves_only_when_it_must(self):
        js = self._js()
        frame = js.split("function drawFrame()")[1].split("\n    }\n")[0]
        assert "if (groupView) holdGroupView(false);" in frame
        hold = js.split("function holdGroupView(force)")[1].split("\n    }\n")[0]
        assert "if (!force && !strayed && !bunched) return;" in hold
        assert "const bunched = nearest >= map.getZoom() + 1;" in hold, "whole zoom levels stop it hunting"
        assert "if (!force && nowMs < groupHoldUntil) return;" in hold

    def test_played_fast_it_keeps_more_sea_round_them(self):
        """At 60x a boat doing six knots covers 180 m of chart a second. Held
        at the minimum span, one favourite was off the chart in 2 samples of 12."""
        js = self._js()
        span = js.split("function groupSpanM(which)")[1].split("\n    }\n")[0]
        assert "if (!playing) return VIEW_MIN_SPAN_M;" in span
        assert "fastest * KNOTS_TO_MPS * SPEEDS[speedIx] * VIEW_AHEAD_S" in span
        assert "groupSpanM(groupView) / 2 / 111320" in js

    def test_the_viewer_can_always_take_the_chart_back(self):
        js = self._js()
        bind = js.split("function bindMap()")[1].split("\n    }\n")[0]
        assert "map.on('dragstart', () => { if (following) setFollowing(false); letGo(); });" in bind
        # In the capture phase: Leaflet's zoom buttons stop their clicks bubbling.
        assert "if (ev.target.closest('.leaflet-control-zoom')) letGo(); }, true);" in bind
        assert "box.addEventListener('dblclick', letGo, true);" in bind
        assert "ev.touches.length > 1" in bind, "a pinch"

    def test_the_list_shows_what_is_held_and_its_first_line_lets_go(self):
        js = self._js()
        assert "if (viewSel) viewSel.value = groupView || '';" in js
        assert "if (!which) { setGroupView(null); return; }" in js

    def test_one_boat_or_a_group_never_both(self):
        js = self._js()
        follow = js.split("function setFollowing(on)")[1].split("\n    }\n")[0]
        assert "if (following && groupView) { groupView = null;" in follow
        assert js.split("function showView(which)")[1].lstrip().startswith("{\n      if (following) setFollowing(false);")

    def test_it_comes_back_after_the_page_reloads_itself(self):
        js = self._js()
        assert "view: groupView" in js
        assert "(!following && (saved.view === 'fleet' || saved.view === 'favourites')) ? saved.view : null" in js
        shown = js.split("addEventListener('coursemap:shown'")[1][:260]
        assert "holdGroupView(true)" in shown

    def test_unstarring_the_last_favourite_lets_go(self):
        picker = self._js().split("function fitViewPicker()")[1].split("\n    }\n")[0]
        assert "if (groupView === 'favourites') groupView = null;" in picker


class TestThePageAndTheScriptUseTheSameLists:
    def _js(self):
        return (STATIC / "race_replay.js").read_text(encoding="utf-8")

    def _template(self):
        return (TEMPLATES / "competitor_race.html").read_text(encoding="utf-8")

    def test_the_speeds(self):
        """The page carries the options so the control is right on first paint,
        and the script rebuilds it from its own list. They must be one list."""
        speeds = re.search(r"const SPEEDS = \[([^\]]+)\]", self._js()).group(1)
        speeds = [s.strip() for s in speeds.split(",")]
        select = re.search(r'<select class="replay-speed".*?</select>', self._template(), re.S).group(0)
        assert re.findall(r'<option value="(\d+)"', select) == speeds
        default = re.search(r"const DEFAULT_SPEED = (\d+);", self._js()).group(1)
        assert re.search(rf'<option value="{default}" selected>', select), "the page opens on another speed"
        assert default in speeds

    def test_a_passage_race_can_still_be_played_through_in_minutes(self):
        speeds = re.search(r"const SPEEDS = \[([^\]]+)\]", self._js()).group(1)
        assert max(int(s) for s in speeds.split(",")) >= 60

    def test_the_boards_columns(self):
        head = re.search(r"const LINE_HEAD = \[([^\]]+)\]", self._js()).group(1)
        head = [h.strip().strip("'") for h in head.split(",")]
        assert head[-2:] == ["VMC", "Sailed"]
        thead = re.search(r'<table class="replay-board stack-table">.*?</thead>', self._template(), re.S).group(0)
        assert re.findall(r"<th>([^<]+)</th>", thead) == head

    def test_every_new_cell_is_labelled_for_the_phone_card(self):
        js = self._js()
        assert 'data-label="VMC"' in js and 'data-label="Sailed"' in js


class TestTheChartScript:
    def _js(self):
        return (STATIC / "race_replay.js").read_text(encoding="utf-8")

    def _map_js(self):
        return (STATIC / "course_map.js").read_text(encoding="utf-8")

    def test_distance_sailed_ignores_gps_jitter(self):
        """A tracker reporting every two seconds is a few metres out each time.
        Adding every step up counted miles the boat never sailed."""
        js = self._js()
        step = int(re.search(r"const SAILED_MIN_STEP_M = (\d+);", js).group(1))
        assert 5 <= step <= 25, "smaller than a fix's error counts the error; larger cuts the corners off a tack"
        assert "if (step >= SAILED_MIN_STEP_M)" in js

    def test_distance_sailed_stops_at_the_finish(self):
        """The tracker keeps reporting all the way back to the mooring."""
        js = self._js()
        body = js.split("function sailedNm(", 1)[1].split("\n    }\n", 1)[0]
        assert "finishTime(boat.entry_id)" in body and "data.first_start" in body

    def test_a_tap_on_a_boat_reaches_the_page(self):
        """The footnote under the chart promised it for months before it was true."""
        assert "onBoatClick: id => selectBoat(id)" in self._js()
        map_js = self._map_js()
        assert "marker.on('click'" in map_js
        # And the tap that lands while the markers are being rebuilt.
        assert "state.map.on('click'" in map_js and "BOAT_TAP_PX" in map_js

    def test_the_other_charts_are_untouched_by_it(self):
        """The race office, trackers and clubhouse charts call updateBoats with
        no callback, and must go on behaving as they did."""
        assert "const pick = typeof options.onBoatClick === 'function';" in self._map_js()

    def test_a_compound_mark_drawn_as_its_corners_is_not_reported_missing(self):
        """Y and A have no position of their own: "no coordinates are available
        for Y" was printed under a chart with both of Y's buoys on it."""
        map_js = self._map_js()
        assert "!validLatLon(allMarks[m.mark])).map(m => m.mark)" not in map_js
        assert "compoundComponents(allMarks, m.mark, m.rounding)" in map_js.split("const located", 1)[1][:400]

    def test_the_viewers_choices_survive_the_page_reloading_itself(self):
        js = self._js()
        assert "'roChart:'" in js and "sessionStorage" in js
        assert "{sel: selected, card: cardOpen, follow: following, view: groupView, speed: SPEEDS[speedIx]}" in js

    def test_a_finished_race_does_not_open_saying_live(self):
        assert "panel.dataset.raceFinished === '1'" in self._js().split("function describe()", 1)[1][:900]


class TestFavouritesStayOnThePhone:
    def test_they_are_kept_in_the_browser_and_sent_nowhere(self):
        js = (STATIC / "public_follow.js").read_text(encoding="utf-8")
        assert "localStorage" in js
        for network in ("fetch(", "XMLHttpRequest", "sendBeacon"):
            assert network not in js, f"public_follow.js must not talk to the server ({network})"

    def test_the_script_never_works_a_key_out_for_itself(self):
        """The server hands the key out with each boat. A second version of the
        rule in the browser is a second answer waiting to happen."""
        js = (STATIC / "public_follow.js").read_text(encoding="utf-8")
        assert "toUpperCase" not in js
