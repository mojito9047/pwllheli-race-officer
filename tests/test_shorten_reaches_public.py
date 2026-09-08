"""Shortening a course has to reach the screens that are already open.

Both public views are told about the course once, when the server renders them: the
sequence across the top, the chart, the shorten-to-finish leg, and Code flag S. So when
the race officer calls a shortened course they must be told to re-render — and they were
not. The competitor page and the clubhouse display went on showing the full course until
somebody reloaded by hand, which for a television mounted in a bar means until somebody
finds a keyboard.

The competitor page already polled a signature and reloaded when it changed. The bug was
that shortening changed nothing in it: ``course_sequence`` is built from
``course_for_race``, which returns the **unshortened** course (the truncation is applied
separately for display), and the ``shortened_at_*`` columns were absent altogether.

The clubhouse display needs its own, narrower signature. It cannot use the full one:
that changes on every recorded finish, and reloading the display mid-finish would restart
the map, the camera and the board cycle at the worst possible moment.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import app as ro


def make_race(course_no: int = 1) -> int:
    now = datetime.now()
    with ro.get_db() as db:
        race_id = int(db.execute(
            "INSERT INTO races (name, course_no, start_time, rating_rule, created_at)"
            " VALUES ('Shortening', ?, ?, 'DUAL', ?)",
            (course_no, (now - timedelta(minutes=30)).isoformat(timespec="seconds"),
             now.isoformat(timespec="seconds"))).lastrowid)
        db.commit()
    return race_id


def shorten(race_id: int, index: int = 2, mark: str = "4") -> None:
    with ro.get_db() as db:
        db.execute("UPDATE races SET shortened_at_mark = ?, shortened_at_index = ?,"
                   " shortened_at_time = ? WHERE id = ?",
                   (mark, index, datetime.now().isoformat(timespec="seconds"), race_id))
        db.commit()


def clear_shorten(race_id: int) -> None:
    with ro.get_db() as db:
        db.execute("UPDATE races SET shortened_at_mark = NULL, shortened_at_index = NULL,"
                   " shortened_at_time = NULL WHERE id = ?", (race_id,))
        db.commit()


class TestTheCompetitorPageSignature:
    def test_shortening_changes_it(self, client):
        """The whole bug in one assertion."""
        race_id = make_race()
        before, _ = ro.competitor_race_state_signature(ro.get_race(race_id))
        shorten(race_id)
        after, _ = ro.competitor_race_state_signature(ro.get_race(race_id))
        assert before != after, "shortening the course must make the page reload"

    def test_clearing_it_changes_it_back(self, client):
        race_id = make_race()
        plain, _ = ro.competitor_race_state_signature(ro.get_race(race_id))
        shorten(race_id)
        clear_shorten(race_id)
        restored, _ = ro.competitor_race_state_signature(ro.get_race(race_id))
        assert restored == plain

    def test_shortening_at_a_different_mark_is_a_different_signature(self, client):
        race_id = make_race()
        shorten(race_id, index=2, mark="4")
        first, _ = ro.competitor_race_state_signature(ro.get_race(race_id))
        shorten(race_id, index=3, mark="O")
        second, _ = ro.competitor_race_state_signature(ro.get_race(race_id))
        assert first != second

    def test_the_state_it_reports_names_the_mark(self, client):
        """So the value is inspectable rather than only hashed."""
        race_id = make_race()
        shorten(race_id, mark="4")
        _, state = ro.competitor_race_state_signature(ro.get_race(race_id))
        assert state["shortened_at_mark"] == "4"

    def test_the_endpoint_serves_it(self, client):
        race_id = make_race()
        before = client.get(f"/public/race/{race_id}/state").get_json()["signature"]
        shorten(race_id)
        after = client.get(f"/public/race/{race_id}/state").get_json()["signature"]
        assert before != after


class TestTheClubhouseRenderSignature:
    def test_shortening_changes_it(self, client):
        race_id = make_race()
        before = ro.public_render_signature(ro.get_race(race_id))
        shorten(race_id)
        assert ro.public_render_signature(ro.get_race(race_id)) != before

    def test_changing_the_course_changes_it(self, client):
        race_id = make_race(course_no=1)
        before = ro.public_render_signature(ro.get_race(race_id))
        with ro.get_db() as db:
            db.execute("UPDATE races SET course_no = 8 WHERE id = ?", (race_id,))
            db.commit()
        assert ro.public_render_signature(ro.get_race(race_id)) != before

    def test_a_mark_moved_from_the_water_changes_it(self, client, monkeypatch):
        """A re-measured mark moves where the chart draws it, and that is a race-day
        event the phone page exists for."""
        race_id = make_race()
        before = ro.public_render_signature(ro.get_race(race_id))
        moved = {code: dict(md) for code, md in ro.appstate.MARKS.items()}
        first = sorted(moved)[0]
        moved[first]["lat"] = float(moved[first].get("lat") or 52.0) + 0.01
        monkeypatch.setattr(ro.appstate, "MARKS", moved)
        assert ro.public_render_signature(ro.get_race(race_id)) != before

    def test_a_recorded_finish_does_not_change_it(self, client):
        """The reason it is separate from the full signature. Reloading a television
        every time a boat finishes would restart the map, the camera and the board
        cycle at exactly the wrong moment."""
        race_id = make_race()
        with ro.get_db() as db:
            entry_id = int(db.execute(
                "INSERT INTO entries (race_id, boat_name, sail_no, status)"
                " VALUES (?, 'Kite', 'GBR 1', 'RACING')", (race_id,)).lastrowid)
            db.commit()
        before = ro.public_render_signature(ro.get_race(race_id))
        with ro.get_db() as db:
            db.execute("UPDATE entries SET status = 'FINISHED', finish_time = ? WHERE id = ?",
                       (datetime.now().isoformat(timespec="seconds"), entry_id))
            db.commit()
        assert ro.public_render_signature(ro.get_race(race_id)) == before

    def test_but_the_full_signature_does(self, client):
        """Confirming the two are genuinely different instruments."""
        race_id = make_race()
        with ro.get_db() as db:
            entry_id = int(db.execute(
                "INSERT INTO entries (race_id, boat_name, sail_no, status)"
                " VALUES (?, 'Kite', 'GBR 1', 'RACING')", (race_id,)).lastrowid)
            db.commit()
        before, _ = ro.competitor_race_state_signature(ro.get_race(race_id))
        with ro.get_db() as db:
            db.execute("UPDATE entries SET status = 'FINISHED' WHERE id = ?", (entry_id,))
            db.commit()
        after, _ = ro.competitor_race_state_signature(ro.get_race(race_id))
        assert before != after

    def test_setting_the_start_time_changes_it(self, client):
        """Reported from the hut the day after the shortening fault: the display sat on
        "Waiting" and the flags stayed down, because its clock and its flag schedule are
        both written into the page from the start time."""
        race_id = make_race()
        with ro.get_db() as db:
            db.execute("UPDATE races SET start_time = '' WHERE id = ?", (race_id,))
            db.commit()
        before = ro.public_render_signature(ro.get_race(race_id))
        with ro.get_db() as db:
            db.execute("UPDATE races SET start_time = ? WHERE id = ?",
                       (datetime.now().isoformat(timespec="seconds"), race_id))
            db.commit()
        assert ro.public_render_signature(ro.get_race(race_id)) != before

    def test_changing_the_start_plan_changes_it(self, client):
        """The flags are driven by the start schedule, which is server-rendered into
        the page as data-schedule."""
        race_id = make_race()
        before = ro.public_render_signature(ro.get_race(race_id))
        with ro.get_db() as db:
            db.execute("UPDATE races SET start_plan_json = ? WHERE id = ?",
                       ('{"starts": [{"name": "Start 1", "offset_min": 0.0,'
                        ' "classes": ["Class 1"]}]}', race_id))
            db.commit()
        assert ro.public_render_signature(ro.get_race(race_id)) != before

    def test_renaming_the_race_changes_it(self, client):
        race_id = make_race()
        before = ro.public_render_signature(ro.get_race(race_id))
        with ro.get_db() as db:
            db.execute("UPDATE races SET name = 'Renamed' WHERE id = ?", (race_id,))
            db.commit()
        assert ro.public_render_signature(ro.get_race(race_id)) != before

    def test_the_bar_state_endpoint_serves_it(self, client):
        race_id = make_race()
        before = client.get(f"/bar/state/{race_id}").get_json()["render_signature"]
        shorten(race_id)
        after = client.get(f"/bar/state/{race_id}").get_json()["render_signature"]
        assert before != after

    def test_the_page_carries_what_it_was_rendered_with(self, client):
        race_id = make_race()
        html = client.get(f"/bar/{race_id}").get_data(as_text=True)
        assert "data-render-signature=" in html
        rendered = html.split('data-render-signature="')[1].split('"')[0]
        assert rendered == ro.public_render_signature(ro.get_race(race_id))

    def test_the_display_reloads_when_it_differs(self):
        source = (ro.appstate.BASE_DIR / "static" / "bar_display.js").read_text(encoding="utf-8")
        assert "s.render_signature !== renderSignature" in source


class TestTheCompetitorHomePage:
    """It had no state poll at all — it ticked its countdown and refreshed the wind, and
    never asked whether anything else had changed. So the race officer setting the first
    warning signal left every open copy reading "Not set" with a dead clock, at the one
    moment everybody is looking at it."""

    def test_the_page_carries_a_signature_and_polls_for_it(self, client):
        make_race()
        html = client.get("/public/current").get_data(as_text=True)
        assert "/public/current/state" in html, "the landing page never asks"
        assert "const initial = " in html

    def test_the_endpoint_answers_without_a_login(self, client):
        """Competitors are not signed in; a new public endpoint has to be allowed
        explicitly, and the allow-list is deliberately narrow."""
        res = client.get("/public/current/state")
        assert res.status_code == 200
        assert res.get_json()["ok"] is True

    def test_it_gives_away_nothing_but_the_hash(self, client):
        assert set(client.get("/public/current/state").get_json()) == {"ok", "signature"}

    def test_setting_a_start_time_changes_it(self, client):
        race_id = make_race()
        with ro.get_db() as db:
            db.execute("UPDATE races SET start_time = '' WHERE id = ?", (race_id,))
            db.commit()
        before = client.get("/public/current/state").get_json()["signature"]
        with ro.get_db() as db:
            db.execute("UPDATE races SET start_time = ? WHERE id = ?",
                       (datetime.now().isoformat(timespec="seconds"), race_id))
            db.commit()
        assert client.get("/public/current/state").get_json()["signature"] != before

    def test_a_new_race_changes_it(self, client):
        make_race()
        before = client.get("/public/current/state").get_json()["signature"]
        make_race()
        assert client.get("/public/current/state").get_json()["signature"] != before


class TestTheChartTabNoLongerBlocksUpdatesForEver:
    """The guard that hid all of this from the people most likely to care.

    A reload throws away where somebody had scrubbed the replay to, so the competitor
    page holds one back while the Chart tab is open. It used to hold it for as long as
    the tab was open at all, reasoning that a replay cannot go stale — which is wrong
    for a race still being sailed, and the chart is exactly where you watch one.
    """

    def _guard(self):
        return (ro.appstate.BASE_DIR / "templates" / "competitor_race.html").read_text(
            encoding="utf-8").split("function replayInUse()")[1].split("}")[0]

    def test_it_asks_whether_the_viewer_has_wound_back(self):
        assert "data-at-live" in self._guard()

    def test_at_the_live_edge_a_reload_is_allowed(self):
        guard = self._guard()
        assert "atLive !== '1'" in guard

    def test_with_no_replay_loaded_there_is_nothing_to_protect(self):
        """The reported case: a race with no start time has no track, so the viewer
        never reports, and holding the reload is what left it on "Start not set"."""
        assert "if (!viewer) return false;" in self._guard()

    def test_the_replay_publishes_whether_it_is_at_the_live_edge(self):
        source = (ro.appstate.BASE_DIR / "static" / "race_replay.js").read_text(encoding="utf-8")
        assert "panel.dataset.atLive" in source


class TestWhatTheShortenedPagesActuallyShow:
    def test_the_bar_shows_the_shorten_leg(self, client):
        race_id = make_race()
        shorten(race_id)
        html = client.get(f"/bar/{race_id}").get_data(as_text=True)
        assert 'data-shortened="1"' in html
        assert "Finish" in html

    def test_and_does_not_when_the_course_is_whole(self, client):
        race_id = make_race()
        assert 'data-shortened="0"' in client.get(f"/bar/{race_id}").get_data(as_text=True)


class TestTheDisplayFollowsTheCurrentRace:
    """What happens to a television showing a race that is then deleted.

    Its state poll asked ``/bar/state/<id>`` for a race that no longer existed, got a
    404, and treated it as a transient error — so it kept 404ing every five seconds and
    went on showing a deleted race until somebody found a keyboard.

    Which race is *current* is deliberately not changed here: it is the latest race that
    still has boats RACING, otherwise the latest race. So a new sheet does not take the
    screen away from a race still being sailed — finishing or retiring the last boat is
    what moves it on. That rule surprised the race officer, so it is written down in
    docs/BAR_DISPLAY.md rather than altered.
    """

    def test_the_state_endpoint_404s_for_a_deleted_race(self, client):
        race_id = make_race()
        assert client.get(f"/bar/state/{race_id}").status_code == 200
        with ro.get_db() as db:
            db.execute("DELETE FROM races WHERE id = ?", (race_id,))
            db.commit()
        assert client.get(f"/bar/state/{race_id}").status_code == 404

    def test_the_display_reloads_once_on_that_404(self):
        source = (ro.appstate.BASE_DIR / "static" / "bar_display.js").read_text(encoding="utf-8")
        assert "res.status === 404" in source
        # Once only: if the reload lands on the same 404 there is nothing to be gained
        # by asking again every five seconds for the rest of the afternoon.
        assert "recovering" in source

    def test_a_transient_error_is_still_shrugged_off(self):
        """A 500 or a dropped connection must not reload the television."""
        source = (ro.appstate.BASE_DIR / "static" / "bar_display.js").read_text(encoding="utf-8")
        after = source.split("async function pollState()")[1][:900]
        assert "if (!res.ok) return;" in after

    def test_the_unpinned_page_picks_up_whatever_is_current_now(self, client):
        """What the reload is for."""
        first = make_race()
        with ro.get_db() as db:
            db.execute("INSERT INTO entries (race_id, boat_name, sail_no, status)"
                       " VALUES (?, 'Kite', 'GBR 1', 'RACING')", (first,))
            db.commit()
        assert str(first) in client.get("/bar").get_data(as_text=True)
        with ro.get_db() as db:
            db.execute("DELETE FROM entries WHERE race_id = ?", (first,))
            db.execute("DELETE FROM races WHERE id = ?", (first,))
            db.commit()
        second = make_race()
        html = client.get("/bar").get_data(as_text=True)
        assert str(second) in html
        assert f"/bar/state/{first}" not in html

    def test_a_pinned_page_for_a_deleted_race_says_there_is_no_race(self, client):
        """Rather than looping: the reload lands here and stops."""
        race_id = make_race()
        with ro.get_db() as db:
            db.execute("DELETE FROM races WHERE id = ?", (race_id,))
            db.commit()
        res = client.get(f"/bar/{race_id}")
        assert res.status_code == 404
        assert "No race running" in res.get_data(as_text=True)

    def test_with_no_races_at_all_it_says_so(self, client):
        with ro.get_db() as db:
            db.execute("DELETE FROM entries")
            db.execute("DELETE FROM races")
            db.commit()
        assert "No race running" in client.get("/bar").get_data(as_text=True)
