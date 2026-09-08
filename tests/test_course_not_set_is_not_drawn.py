"""A course nobody chose is not shown as this race's course.

A new race stores the first fixed course as a **fallback**, so the chart, the leg
analysis and the shortening options have geometry to work with. ``course_set``
exists to tell that fallback apart from a decision, and until v0.276 only some of
the page asked it. The result was a race sheet whose header said *Course: 1* and
whose course board said *Course not set*, sitting one above the other, with the
fallback's marks drawn on the board and the chart underneath — and a competitor
page that said *Course: not set yet* directly above the same seven marks.

The rule is now one flag, asked everywhere: no board, no chart line, no predicted
time and nothing selected in the picker until somebody has chosen a course.

Saving must still work without choosing one. Setting a first warning signal
before the course is known is an ordinary thing to do on a race morning, so an
empty picker keeps the fallback number and leaves ``course_set`` alone — the same
"only ever upwards" rule ``_course_set_after`` already had.
"""
from __future__ import annotations

from datetime import datetime

import pytest

import app as ro


def _race(name="Club Race", course_set=0, course_no=1):
    from core.db import get_db
    now = datetime.now().isoformat(timespec="seconds")
    with get_db() as db:
        cur = db.execute(
            "INSERT INTO races (name, course_no, course_set, start_time, notes, created_at)"
            " VALUES (?,?,?,?,?,?)", (name, course_no, course_set, "", "", now))
        db.commit()
        return int(cur.lastrowid)


def _row(race_id):
    from core.db import get_db
    import sqlite3
    with get_db() as db:
        db.row_factory = sqlite3.Row
        return db.execute("SELECT * FROM races WHERE id = ?", (race_id,)).fetchone()


def _post(client, url, data):
    token = "test-csrf-token"
    with client.session_transaction() as sess:
        sess["_csrf_token"] = token
    payload = dict(data)
    payload["_csrf_token"] = token
    return client.post(url, data=payload, follow_redirects=True)


class TestTheRaceSheet:
    def test_the_header_does_not_name_a_course(self, logged_in_client):
        rid = _race()
        html = logged_in_client.get(f"/admin/race/{rid}").get_data(as_text=True)
        assert "not set yet" in html

    def test_the_picker_arrives_on_course_not_set(self, logged_in_client):
        rid = _race()
        html = logged_in_client.get(f"/admin/race/{rid}").get_data(as_text=True)
        assert '<option value="" selected>Course not set</option>' in html

    def test_no_course_is_preselected(self, logged_in_client):
        """The fallback showing as the chosen course is the whole complaint."""
        import re
        rid = _race()
        html = logged_in_client.get(f"/admin/race/{rid}").get_data(as_text=True)
        picker = re.search(r'<select name="course_no">.*?</select>', html, re.S).group(0)
        assert picker.count("selected") == 1, picker[:400]

    def test_nothing_is_drawn_on_the_chart(self, logged_in_client):
        rid = _race()
        html = logged_in_client.get(f"/admin/race/{rid}").get_data(as_text=True)
        assert "data-course-marks='[]'" in html

    def test_it_says_why_the_board_is_empty(self, logged_in_client):
        rid = _race()
        html = logged_in_client.get(f"/admin/race/{rid}").get_data(as_text=True)
        assert "Nobody has chosen a course for this race yet" in html

    def test_a_chosen_course_is_drawn_as_before(self, logged_in_client):
        """The guard must not swallow a real course."""
        rid = _race(course_set=1)
        html = logged_in_client.get(f"/admin/race/{rid}").get_data(as_text=True)
        assert "data-course-marks='[]'" not in html
        assert "Nobody has chosen a course" not in html

    def test_a_chosen_course_is_selected_in_the_picker(self, logged_in_client):
        rid = _race(course_set=1)
        html = logged_in_client.get(f"/admin/race/{rid}").get_data(as_text=True)
        assert "Course not set</option>" not in html


class TestSavingWithoutChoosingOne:
    def test_the_save_is_accepted(self, logged_in_client):
        rid = _race()
        _post(logged_in_client, f"/admin/race/{rid}/update",
              {"name": "Renamed", "start_time": "", "series_id": "", "notes": "",
               "course_no": "", "start_plan_mode": "grid"})
        assert _row(rid)["name"] == "Renamed"

    def test_and_the_course_still_counts_as_unchosen(self, logged_in_client):
        rid = _race()
        _post(logged_in_client, f"/admin/race/{rid}/update",
              {"name": "Renamed", "start_time": "", "series_id": "", "notes": "",
               "course_no": "", "start_plan_mode": "grid"})
        assert int(_row(rid)["course_set"]) == 0

    def test_the_fallback_number_is_kept(self, logged_in_client):
        """Something has to be stored: the shortening options and the chart both
        read the number even when nothing is drawn from it."""
        rid = _race(course_no=1)
        _post(logged_in_client, f"/admin/race/{rid}/update",
              {"name": "Renamed", "start_time": "", "series_id": "", "notes": "",
               "course_no": "", "start_plan_mode": "grid"})
        assert int(_row(rid)["course_no"]) == 1

    def test_choosing_one_marks_it_chosen(self, logged_in_client):
        rid = _race()
        courses = ro.appstate.COURSES
        chosen = int(courses[0]["course_no"])
        _post(logged_in_client, f"/admin/race/{rid}/update",
              {"name": "Club Race", "start_time": "", "series_id": "", "notes": "",
               "course_no": str(chosen), "start_plan_mode": "grid"})
        row = _row(rid)
        assert int(row["course_set"]) == 1
        assert int(row["course_no"]) == chosen

    def test_a_chosen_course_is_never_unchosen_by_a_later_save(self, logged_in_client):
        """`course_set` only ever goes upwards, and an empty picker is not
        offered once a course exists — but nothing should depend on that."""
        rid = _race(course_set=1)
        _post(logged_in_client, f"/admin/race/{rid}/update",
              {"name": "Club Race", "start_time": "", "series_id": "", "notes": "",
               "course_no": "", "start_plan_mode": "grid"})
        assert int(_row(rid)["course_set"]) == 1


class TestTheCompetitorPage:
    """It was the loudest version of the contradiction: the header already said
    *not set yet*, and the seven marks of the fallback were on the next line."""

    def test_no_course_board(self, client):
        rid = _race()
        html = client.get(f"/public/race/{rid}").get_data(as_text=True)
        assert "The race officer has not set the course yet." in html

    def test_nothing_drawn_on_the_chart(self, client):
        rid = _race()
        html = client.get(f"/public/race/{rid}").get_data(as_text=True)
        assert "data-course-marks='[]'" in html

    def test_no_predicted_time_from_a_course_nobody_picked(self, client):
        rid = _race()
        html = client.get(f"/public/race/{rid}").get_data(as_text=True)
        assert '<strong id="publicPredictedTime">&mdash;</strong>' in html

    def test_a_chosen_course_is_published_as_before(self, client):
        rid = _race(course_set=1)
        html = client.get(f"/public/race/{rid}").get_data(as_text=True)
        assert "has not set the course yet" not in html
        assert "data-course-marks='[]'" not in html


class TestTheListsAgree:
    def test_the_races_page_says_not_set(self, logged_in_client):
        _race()
        html = logged_in_client.get("/admin/races").get_data(as_text=True)
        assert "not set" in html

    def test_the_dead_class_column_is_gone(self, logged_in_client):
        """races.class_name is the fleet label whose form retired in v0.276: a
        dash on every race made since, and unchangeable on the older ones."""
        _race()
        html = logged_in_client.get("/admin/races").get_data(as_text=True)
        assert "<th>Class</th>" not in html
