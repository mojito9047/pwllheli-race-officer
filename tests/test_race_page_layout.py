"""Two race-day layout decisions: where trackers are assigned, and chart height.

Both came from running the app rather than reading it.
"""
from __future__ import annotations

import pathlib
import re
from datetime import datetime

import app as ro
import core.track as track


def _race_with_an_entry():
    now = datetime.now().isoformat(timespec="seconds")
    with ro.get_db() as db:
        cur = db.execute("INSERT INTO races (name, course_no, start_time, created_at)"
                         " VALUES ('Layout', 1, ?, ?)", (now, now))
        race_id = int(cur.lastrowid)
        cur = db.execute("INSERT INTO boats (boat_name, sail_no, status, created_at, updated_at)"
                         " VALUES ('Layout', 'GBR9L', 'ACTIVE', ?, ?)", (now, now))
        boat_id = int(cur.lastrowid)
        cur = db.execute("INSERT INTO entries (race_id, boat_id, boat_name, sail_no, status)"
                         " VALUES (?, ?, 'Layout', 'GBR9L', 'RACING')", (race_id, boat_id))
        entry_id = int(cur.lastrowid)
        db.commit()
    return race_id, entry_id


class TestTrackersAreAssignedInOnePlace:
    """Assigning a tracker belonged on the Trackers page, not on every race.

    The race page had a per-entry *loaner override* dropdown beside Save. Two
    places to set the same thing is two places to get it wrong on a race morning,
    and the Trackers page is where the boat/tracker pairing already lives.
    """

    def test_the_entries_tab_no_longer_offers_a_tracker_dropdown(self, logged_in_client, monkeypatch):
        import app as app_mod
        import routes.race as race_routes
        cfg = {**track.track_config(), "enabled": True}
        monkeypatch.setattr(track, "track_config", lambda: cfg)
        monkeypatch.setattr(app_mod, "track_config", lambda: cfg)
        # routes/race.py binds the helper off the app module at import time, so
        # patching `app` alone never reaches the race page.
        monkeypatch.setattr(race_routes, "track_config", lambda: cfg)
        race_id, _entry_id = _race_with_an_entry()
        res = logged_in_client.get(f"/race/{race_id}", follow_redirects=True)
        assert res.status_code == 200        # the page itself, not a redirect stub
        html = res.get_data(as_text=True)
        assert 'name="tracker_unique_id"' not in html
        # ...and still points the officer at the page that does own it.
        assert "Assign trackers" in html

    def test_saving_an_entry_leaves_an_existing_override_alone(self, logged_in_client):
        """The form no longer posts the field, so the update must not write it.

        Without this the first Save on any entry would silently blank a loaner
        tracker set before the upgrade — the dropdown is gone, so `request.form`
        returns "" and the old code stored NULL.
        """
        race_id, entry_id = _race_with_an_entry()
        with ro.get_db() as db:
            db.execute("UPDATE entries SET tracker_unique_id='LOANER-1' WHERE id=?", (entry_id,))
            db.commit()
        with logged_in_client.session_transaction() as sess:
            sess["_csrf_token"] = "test-csrf-token"
        res = logged_in_client.post(
            f"/race/{race_id}/entry/{entry_id}/update",
            data={"_csrf_token": "test-csrf-token", "status": "DNF", "finish_time": ""},
            follow_redirects=True)
        assert res.status_code == 200
        with ro.get_db() as db:
            row = db.execute("SELECT status, tracker_unique_id FROM entries WHERE id=?",
                             (entry_id,)).fetchone()
        assert row["status"] == "DNF"                    # the update really ran
        assert row["tracker_unique_id"] == "LOANER-1"    # and left this alone


class TestTheChartIsCappedByWindowHeight:
    """A square chart at its full 1200px ran to two and a half screens.

    Everything the officer needs *under* the chart — playback controls, the
    leaderboard — was below the fold, so using it meant scrolling the chart off
    the top. The chart is still square by preference; the cap only bites on a
    wide screen, where it goes landscape. A phone stays square, because there the
    width is the smaller bound.
    """

    def test_the_chart_is_capped_at_half_the_window(self):
        css = pathlib.Path("static/style.css").read_text(encoding="utf-8")
        assert "max-height: 55vh;" in css
        assert "aspect-ratio: 1 / 1;" in css              # still square when it fits

    def test_a_window_resize_tells_leaflet(self):
        """Height now depends on the viewport, so a resize changes the container.
        Leaflet keeps drawing at its build size unless invalidateSize is called."""
        js = pathlib.Path("static/course_map.js").read_text(encoding="utf-8")
        assert "addEventListener('resize'" in js
        assert "invalidateSize()" in js


class TestTheChartCanFillTheScreen:
    """The chart owns its panel and the leaderboard floats over it.

    It used to be a capped square with the board stacked underneath, which on a
    phone meant a 300px chart and most of the display spent on everything else.
    Now the panel is a flex column that owns a height: the chart takes what is
    left after the control bar, and the board is a translucent roll-up over it,
    rolled away until asked for.

    Full screen is the *same* layout pinned to the viewport, which is why almost
    everything here is scoped to `[data-replay]` rather than to the full-screen
    class. It is driven by a class rather than the Fullscreen API because iOS
    Safari refuses requestFullscreen() on anything but a <video> — and a phone is
    exactly where this is worth having. The real API is asked for as well where it
    exists, to drop the browser chrome too, but nothing depends on getting it.
    """

    def _js(self):
        return pathlib.Path("static/race_replay.js").read_text(encoding="utf-8")

    def _css(self):
        return pathlib.Path("static/style.css").read_text(encoding="utf-8")

    def test_the_page_offers_it(self, client, monkeypatch):
        import app as app_mod
        from tests.test_race_replay import _seed_race
        cfg = {**track.track_config(), "enabled": True}
        monkeypatch.setattr(track, "track_config", lambda: cfg)
        monkeypatch.setattr(app_mod, "track_config", lambda: cfg)
        race_id, _ids, _gun = _seed_race(client)
        html = client.get(f"/public/race/{race_id}").get_data(as_text=True)
        assert 'class="replay-full' in html          # the button
        assert 'class="replay-exit"' in html         # and a way back out
        assert 'class="replay-sheet"' in html        # the roll-up board

    def test_it_does_not_depend_on_the_fullscreen_api(self):
        """iOS Safari has no requestFullscreen on a div, so the class does the work."""
        js = self._js()
        assert "classList.toggle('replay-fullscreen'" in js
        css = self._css()
        assert ".replay-fullscreen {" in css and "position: fixed" in css

    def test_leaving_by_the_browsers_own_control_puts_the_page_back(self):
        """Esc or the system UI exits the real fullscreen without telling us, and
        the panel would otherwise stay pinned over the whole page."""
        js = self._js()
        assert "fullscreenchange" in js and "'Escape'" in js

    def test_leaflet_is_told_the_map_changed_size(self):
        """Going full screen moves the map with CSS, which fires no resize event."""
        assert "RaceCourseMap.invalidate" in self._js()
        assert "invalidate" in pathlib.Path("static/course_map.js").read_text(encoding="utf-8")

    def test_the_board_floats_above_the_controls_rather_than_over_them(self):
        """A board covering Play and the timeline makes the chart unusable the
        moment it is opened; the bar's height is measured, not guessed."""
        assert "--replay-controls-h" in self._js()
        assert "bottom: var(--replay-controls-h" in self._css()
        # Measured with a ResizeObserver: the Chart tab starts hidden, so a
        # load-time measurement is zero and the sheet lands over the controls.
        assert "ResizeObserver" in self._js()

    def test_the_way_out_cannot_be_covered_by_the_board(self):
        """The control bar's own button sits under the sheet when it is rolled up,
        so leaving full screen gets a control of its own at a higher layer."""
        css = self._css()
        exit_rule = re.search(r'\.replay-exit \{([^}]*)\}', css).group(1)
        sheet_rule = re.search(r'\[data-replay\] \.replay-sheet \{([^}]*)\}', css).group(1)
        z = lambda s: int(re.search(r'z-index:\s*(\d+)', s).group(1))
        assert z(exit_rule) > z(sheet_rule)

    def test_the_board_is_actually_translucent(self):
        """Blurring the panel counts for nothing while the cards on it are solid."""
        css = self._css()
        body = re.search(r'\[data-replay\] \.replay-sheet-body \{([^}]*)\}', css).group(1)
        assert "backdrop-filter" in body
        alpha = float(re.search(r'background: rgba\([^)]*,\s*([\d.]+)\)', body).group(1))
        assert alpha < 0.7
        assert "[data-replay] .replay-sheet-body .stack-table tr" in css   # ...and the cards

    def test_nothing_inside_the_sheet_is_left_opaque(self):
        """Translucency has to hold all the way down the stack.

        Twice now a layer above the blurred panel has been solid and undone it:
        first the row cards, then the table those rows sit in — which only showed
        on a PC, because on a phone the rows become cards and the table itself has
        no visible background at all.
        """
        css = self._css()
        m = re.search(r'\[data-replay\] \.replay-sheet-body \.replay-board,\s*'
                      r'\[data-replay\] \.replay-sheet-body \.replay-board thead th \{([^}]*)\}', css)
        assert m and "background: transparent" in m.group(1)
        assert "[data-replay] .replay-sheet-body .replay-board tbody tr" in css

    def test_the_panel_does_not_go_dark_on_a_dark_preferring_machine(self):
        """The app has no dark theme — the chart, the controls and everything
        around this panel stay light whatever the system prefers. Tinting only the
        sheet made it the one dark thing on a light page, and nearly opaque with
        it. Reported from Chrome on a PC set to prefer dark."""
        css = self._css()
        dark_blocks = re.findall(r'@media \(prefers-color-scheme: dark\) \{(.*?)\n\}', css, re.S)
        for block in dark_blocks:
            assert "replay-sheet" not in block

    def test_the_board_starts_rolled_away(self, client, monkeypatch):
        """Opening the Chart tab is a request to see the chart.

        It is rolled away in the markup as well as by the script, so the board
        never flashes open on first paint before the JavaScript runs.
        """
        import app as app_mod
        from tests.test_race_replay import _seed_race
        cfg = {**track.track_config(), "enabled": True}
        monkeypatch.setattr(track, "track_config", lambda: cfg)
        monkeypatch.setattr(app_mod, "track_config", lambda: cfg)
        race_id, _ids, _gun = _seed_race(client)
        html = client.get(f"/public/race/{race_id}").get_data(as_text=True)
        assert 'class="replay-sheet" data-rolled="1"' in html
        assert 'class="replay-sheet-tab" aria-expanded="false"' in html
        # ...and the script agrees, on every screen size rather than only phones.
        m = re.search(r'function defaultRolled\(\) \{(.*?)\}', self._js(), re.S)
        assert m and "return '1';" in m.group(1)
