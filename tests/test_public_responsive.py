"""Tests for the single responsive public page.

The separate /public/mobile/... variant was retired: one page now serves phones,
tablets and PCs, with wide tables becoming one card per row on narrow screens
(driven by the stack-table class + per-cell data-label attributes in CSS).
"""
from __future__ import annotations

from datetime import datetime, timedelta

import app as ro


def _race(name="Responsive Race"):
    """An ordinary race with a course chosen.

    `course_set` used to be unnecessary here: the back-fill ran on every request
    and marked any race with a start time as chosen, so this fixture got one for
    free. That back-fill is now one-time (see
    tests/test_course_set_backfill_runs_once.py), and a race without a chosen
    course has no course board, no chart and no leg analysis -- which is correct,
    and not what these tests are about.
    """
    warning = (datetime.now() + timedelta(minutes=15)).isoformat(timespec="seconds")
    with ro.get_db() as db:
        cur = db.execute(
            "INSERT INTO races (name, class_name, course_no, course_set, start_time, rating_rule, notes, created_at) "
            "VALUES (?, 'IRC', ?, 1, ?, 'DUAL', '', ?)",
            (name, ro.appstate.COURSES[0]["course_no"], warning,
             datetime.now().isoformat(timespec="seconds")),
        )
        db.execute("INSERT INTO entries (race_id, boat_name, sail_no, status) VALUES (?, 'Boat', 'GBR1', 'RACING')",
                   (int(cur.lastrowid),))
        db.commit()
        return int(cur.lastrowid)


class TestLegacyMobileLinksRedirect:
    def test_mobile_race_redirects_to_the_responsive_page(self, client):
        race_id = _race()
        resp = client.get(f"/public/mobile/race/{race_id}")
        assert resp.status_code in (301, 302)
        assert resp.headers["Location"].endswith(f"/public/race/{race_id}")

    def test_mobile_current_redirects(self, client):
        resp = client.get("/public/mobile/current")
        assert resp.status_code in (301, 302)
        assert resp.headers["Location"].endswith("/public/current")

    def test_mobile_links_still_reach_a_page(self, client):
        race_id = _race()
        resp = client.get(f"/public/mobile/race/{race_id}", follow_redirects=True)
        assert resp.status_code == 200


class TestStackableTables:
    def test_race_page_tables_are_stackable(self, client):
        race_id = _race()
        html = client.get(f"/public/race/{race_id}").get_data(as_text=True)
        # The entries + leg-analysis tables opt in to the phone card layout...
        assert 'class="entry-list stack-table"' in html
        assert 'stack-table' in html
        # ...and each cell carries the heading used as its label on a phone.
        for label in ('data-label="Boat"', 'data-label="Sail no."', 'data-label="Status"',
                      'data-label="Distance"', 'data-label="Bearing"'):
            assert label in html, label

    def test_home_page_race_table_is_stackable(self, client):
        _race()
        html = client.get("/public/current").get_data(as_text=True)
        assert 'stack-table' in html
        assert 'data-label="First start"' in html

class TestRacePageTabs:
    def test_three_tabs_below_the_header(self, client):
        race_id = _race()
        html = client.get(f"/public/race/{race_id}").get_data(as_text=True)
        for tab in ('data-tab="ptab-entries"', 'data-tab="ptab-chart"', 'data-tab="ptab-analysis"'):
            assert tab in html, tab
        # Entries is the default pane; the chart and analysis panes exist too.
        assert 'class="tab-pane active" id="ptab-entries"' in html
        assert 'id="ptab-chart"' in html and 'id="ptab-analysis"' in html
        # The race header/countdown stays above the tabs.
        assert html.index('class="race-header') < html.index('data-tab="ptab-entries"')

    def test_panels_keep_their_ids_inside_the_tabs(self, client):
        race_id = _race()
        html = client.get(f"/public/race/{race_id}").get_data(as_text=True)
        assert 'id="publicEntriesPanel"' in html          # entries pane
        assert 'id="publicCourseAnalysisPanel"' in html   # analysis pane
        assert 'id="publicLegAnalysisTable"' in html
        assert 'class="course-map"' in html               # chart pane still has the map

    def test_leader_board_tab_is_added_once_the_race_has_finished(self, client):
        race_id = _race()
        html = client.get(f"/public/race/{race_id}").get_data(as_text=True)
        assert 'data-tab="ptab-results"' not in html      # hidden while racing
        with ro.get_db() as db:
            db.execute("UPDATE entries SET status='FINISHED', finish_time=? WHERE race_id=?",
                       (datetime.now().isoformat(timespec="seconds"), race_id))
            db.commit()
        html = client.get(f"/public/race/{race_id}").get_data(as_text=True)
        # The other three tabs survive; the leader board is added and opens first.
        for tab in ('data-tab="ptab-results"', 'data-tab="ptab-entries"',
                    'data-tab="ptab-chart"', 'data-tab="ptab-analysis"'):
            assert tab in html, tab
        assert 'data-default-tab="ptab-results"' in html

    def test_no_desktop_mobile_toggle_link(self, client):
        race_id = _race()
        html = client.get(f"/public/race/{race_id}").get_data(as_text=True)
        assert "/public/mobile/race/" not in html    # no toggle to the old variant
