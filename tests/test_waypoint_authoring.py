"""Creating a waypoint, and putting one on a course.

The engine landed first; this is the half a race officer touches. A waypoint is
made on the Marks page like any other mark but ticked as a waypoint, and added to
a course from the builder with a single button — there is no port or starboard to
choose, because boats pass a turning point rather than leaving it on a hand.
"""
from __future__ import annotations

import json

import pytest

import app as ro
from core import appstate, courses, marks


@pytest.fixture
def race_id(client):
    """A bare race to open the course builder against."""
    stamp = "2026-08-01T10:00:00"
    with ro.app.app_context():
        with ro.get_db() as db:
            cur = db.execute("INSERT INTO races (name, course_no, start_time, created_at)"
                             " VALUES ('Waypoint test', 1, ?, ?)", (stamp, stamp))
            db.commit()
            return int(cur.lastrowid)


@pytest.fixture
def clean_marks():
    """The sandboxed marks.json this test may safely write to.

    conftest's autouse sandbox_data_dir redirects appstate.DATA_DIR to a per-test
    copy, and core.marks._marks_path() resolves that when it is called — so
    add_mark already writes to the copy. This only says where to read it back
    from. The club's real marks are not a fixture; a test rewrote them once
    (v0.247) and that sandbox is why it cannot happen again.
    """
    return marks._marks_path()


class TestMakingOne:
    def test_a_waypoint_is_flagged_as_one(self, client, clean_marks, monkeypatch):
        ok, code = marks.add_mark("WC", "Trwyn Cilan turning point", 52.776, -4.523,
                                  waypoint=True)
        assert ok, code
        saved = json.loads(clean_marks.read_text(encoding="utf-8"))["marks"]["WC"]
        assert saved["waypoint"] is True
        assert saved["lat"] == 52.776 and saved["lon"] == -4.523

    def test_an_ordinary_mark_is_not_flagged(self, client, clean_marks):
        ok, code = marks.add_mark("Q1", "Ordinary mark", 52.88, -4.40)
        assert ok, code
        saved = json.loads(clean_marks.read_text(encoding="utf-8"))["marks"]["Q1"]
        assert "waypoint" not in saved

    def test_a_waypoint_stores_no_rounding_radius(self, client, clean_marks):
        """Nothing rounds it, so a radius would be a number that reads as
        meaningful and is never consulted."""
        ok, code = marks.add_mark("WC", "Cilan", 52.776, -4.523,
                                  rounding_radius_m="250", waypoint=True)
        assert ok, code
        saved = json.loads(clean_marks.read_text(encoding="utf-8"))["marks"]["WC"]
        assert "rounding_radius_m" not in saved

    def test_a_bad_radius_still_stops_an_ordinary_mark(self, client, clean_marks):
        ok, err = marks.add_mark("Q2", "Ordinary", 52.88, -4.40, rounding_radius_m="9999")
        assert not ok


class TestTheMarksPage:
    def test_the_form_offers_the_waypoint_tick(self, logged_in_client):
        html = logged_in_client.get("/admin/marks").get_data(as_text=True)
        assert 'name="waypoint"' in html

    def test_a_waypoint_row_says_what_it_is(self, logged_in_client, monkeypatch):
        patched = dict(appstate.MARKS)
        patched["WC"] = {"name": "Cilan", "display": "Cilan", "lat": 52.776,
                         "lon": -4.523, "waypoint": True}
        monkeypatch.setattr(appstate, "MARKS", patched)
        html = logged_in_client.get("/admin/marks").get_data(as_text=True)
        assert "bends a leg, not rounded" in html


class TestTheCourseBuilder:
    def _with_waypoint(self, monkeypatch):
        patched = dict(appstate.MARKS)
        patched["WC"] = {"name": "Trwyn Cilan turning point", "display": "Cilan",
                         "lat": 52.776, "lon": -4.523, "waypoint": True}
        monkeypatch.setattr(appstate, "MARKS", patched)
        monkeypatch.setattr(courses.appstate, "MARKS", patched)

    def test_a_waypoint_is_offered_with_one_button_not_two(self, logged_in_client,
                                                           monkeypatch, race_id):
        self._with_waypoint(monkeypatch)
        html = logged_in_client.get(f"/admin/race/{race_id}/course/builder").get_data(as_text=True)
        assert 'data-mark="WC" data-rounding="via"' in html
        assert 'data-mark="WC" data-rounding="port"' not in html
        assert 'data-mark="WC" data-rounding="starboard"' not in html

    def test_ordinary_marks_still_get_both(self, logged_in_client, monkeypatch, race_id):
        self._with_waypoint(monkeypatch)
        html = logged_in_client.get(f"/admin/race/{race_id}/course/builder").get_data(as_text=True)
        assert 'data-mark="1" data-rounding="port"' in html
        assert 'data-mark="1" data-rounding="starboard"' in html

    def test_via_survives_a_save_and_reopen(self, monkeypatch):
        """Reopening the builder must not turn every waypoint into a port rounding."""
        self._with_waypoint(monkeypatch)
        seq = courses.validate_course_sequence_json(json.dumps(
            [{"mark": "2", "rounding": "port"},
             {"mark": "WC", "rounding": "via"},
             {"mark": "O", "rounding": "port"}]))
        assert seq[1] == {"mark": "WC", "rounding": "via"}
        assert courses.course_sequence_text({"marks": seq}) == "2p Op"


class TestDeletingOne:
    def test_a_waypoint_used_by_a_made_up_course_cannot_be_deleted(self, client, monkeypatch):
        """Custom courses live on the race, not in the fixed course list, and that is
        where waypoints will mostly be used. Deleting one would silently straighten a
        leg back across the land on a race already sailed."""
        patched = dict(appstate.MARKS)
        patched["WC"] = {"name": "Cilan", "lat": 52.776, "lon": -4.523, "waypoint": True}
        monkeypatch.setattr(appstate, "MARKS", patched)
        with ro.app.app_context():
            with ro.get_db() as db:
                cur = db.execute(
                    "INSERT INTO races (name, course_no, start_time, created_at,"
                    " custom_course_json) VALUES ('W', 1, '2026-08-01T10:00:00',"
                    " '2026-08-01T10:00:00', ?)",
                    (json.dumps([{"mark": "2", "rounding": "port"},
                                 {"mark": "WC", "rounding": "via"}]),))
                db.commit()
                rid = int(cur.lastrowid)
        block = marks.mark_delete_block("WC")
        assert block and str(rid) in block, f"delete was allowed or unexplained: {block!r}"

    def test_an_unused_waypoint_can_be_deleted(self, client, monkeypatch):
        patched = dict(appstate.MARKS)
        patched["WZ"] = {"name": "Unused", "lat": 52.7, "lon": -4.5, "waypoint": True}
        monkeypatch.setattr(appstate, "MARKS", patched)
        assert marks.mark_delete_block("WZ") is None
