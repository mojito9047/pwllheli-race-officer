"""The course API says what the race officer has decided, not only what the course is.

Asked for by a chart-plotter app that draws the current race on a boat's
plotter from /api/current_race_course. It could see a course, but not whether
anybody had chosen it, whether AP was flying, or whether the fleet had been
sent home early:

* **A course nobody set.** A new race is created with a course number, so the
  API always had a course to give. `course_set` is what the race sheet, the
  dashboard and the competitor page already ask before showing one.
* **A course that had been shortened.** `course_for_race` never truncates, so a
  fleet shortened at mark 4 was still being served all thirteen marks and
  10.3 nm. `course.sailed` is the route as it is now being sailed, with the leg
  to the finish that the chart draws dashed.
* **A postponement.** Nothing said to stop counting down.

Everything is additive: the existing fields keep their meaning, because an
importer is already reading them.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

import app as ro
from core import courses, track

# Real marks from the club's list, so the geometry is the club's geometry.
SEQUENCE = [{"mark": "1", "rounding": "port"},
            {"mark": "4", "rounding": "port"},
            {"mark": "7", "rounding": "port"}]


def _race(course_set=0, laps=1, custom=True, start="2099-07-25T10:00:00"):
    """A race far enough ahead to be the current one."""
    custom_json = json.dumps({"marks": SEQUENCE, "laps": laps}) if custom else None
    with ro.get_db() as db:
        cur = db.execute(
            "INSERT INTO races (name, class_name, course_no, start_time, rating_rule, notes,"
            " created_at, custom_course_json, course_set) VALUES (?, '', 1, ?, 'DUAL', '', ?, ?, ?)",
            ("API Test", start, datetime.now().isoformat(timespec="seconds"), custom_json, course_set))
        db.commit()
        return int(cur.lastrowid)


def _update(race_id, **cols):
    with ro.get_db() as db:
        sets = ", ".join(f"{k} = ?" for k in cols)
        db.execute(f"UPDATE races SET {sets} WHERE id = ?", (*cols.values(), race_id))
        db.commit()


def _api(client):
    resp = client.get("/api/current_race_course")
    assert resp.status_code == 200, resp.get_data(as_text=True)[:300]
    return resp.get_json()


def _state(client):
    resp = client.get("/public/race/state")
    assert resp.status_code == 200
    return resp.get_json()


class TestCourseSet:
    def test_a_new_race_has_a_course_nobody_chose(self, client):
        _race(course_set=0, custom=False)
        body = _api(client)
        assert body["race"]["course_set"] is False
        # The course is still there -- it always was, which is the problem.
        assert body["course"]["marks"]

    def test_it_is_true_once_a_course_is_chosen(self, client):
        _race(course_set=1)
        assert _api(client)["race"]["course_set"] is True

    def test_choosing_the_course_the_race_was_created_with(self, logged_in_client, csrf_post):
        """The select route writes the same course_no and a custom course that
        was already NULL, so course_set is the only thing that changes."""
        rid = _race(course_set=0, custom=False)
        before = _state(logged_in_client)
        csrf_post(f"/admin/race/{rid}/course/select", {"course_no": "1"})
        after = _state(logged_in_client)
        assert before["course_set"] is False and after["course_set"] is True
        assert before["signature"] != after["signature"], \
            "the competitor page polls this signature and would go on saying 'not set yet'"


class TestPostponement:
    def test_no_flag_by_default(self, client):
        _race(course_set=1)
        race = _api(client)["race"]
        assert race["postponed"] is False
        assert race["postponement_flag"] == ""
        assert race["postponement_ends_at"] == ""

    def test_ap_flying(self, client):
        rid = _race(course_set=1)
        _update(rid, postponed_at=datetime.now().isoformat(timespec="seconds"), postponement_kind="AP")
        race = _api(client)["race"]
        assert race["postponed"] is True
        assert race["postponement_flag"]

    def test_the_moment_it_comes_down(self, client):
        rid = _race(course_set=1)
        ends = (datetime.now() + timedelta(minutes=5)).isoformat(timespec="seconds")
        _update(rid, postponed_at=datetime.now().isoformat(timespec="seconds"),
                postponement_kind="AP", postponement_ends_at=ends)
        race = _api(client)["race"]
        assert race["postponed"] is True and race["postponement_ends_at"] == ends

    def test_a_postponement_that_has_ended_is_not_flying(self, client):
        """The display shows what is on the mast, not what was once decided."""
        rid = _race(course_set=1)
        _update(rid, postponed_at="2020-01-01T10:00:00", postponement_kind="AP",
                postponement_ends_at="2020-01-01T10:30:00")
        race = _api(client)["race"]
        assert race["postponed"] is False and race["postponement_ends_at"] == ""

    def test_the_state_poll_carries_it_too(self, client):
        rid = _race(course_set=1)
        before = _state(client)
        _update(rid, postponed_at=datetime.now().isoformat(timespec="seconds"), postponement_kind="AP")
        after = _state(client)
        assert before["postponed_flag"] == "" and after["postponed_flag"]
        assert before["signature"] != after["signature"]


class TestWarningTime:
    def test_the_warning_signal_is_named_for_what_it_is(self, client):
        _race(course_set=1, start="2099-07-25T10:00:00")
        race = _api(client)["race"]
        assert race["first_warning_time"] == "2099-07-25T10:00:00"
        assert race["start_time"] == race["first_warning_time"], "kept for the existing importer"
        assert race["first_start_time"].startswith("2099-07-25T10:05")


class TestNotShortened:
    def test_the_sailed_course_is_the_course(self, client):
        _race(course_set=1)
        course = _api(client)["course"]
        assert course["shortened"] is False and course["shortened_at"] is None
        assert course["sailed"]["legs"] == course["legs"]
        assert course["sailed"]["marks"] == course["marks"]
        assert course["sailed"]["finish"] is None
        assert not any(leg.get("finish") for leg in course["sailed"]["legs"])


class TestShortened:
    def _shortened(self, client, index=1, laps=1):
        rid = _race(course_set=1, laps=laps)
        _update(rid, shortened_at_index=index, shortened_at_mark="4",
                shortened_at_time="2099-07-25T11:10:00")
        return rid, _api(client)["course"]

    def test_it_says_so_and_where(self, client):
        _, course = self._shortened(client)
        assert course["shortened"] is True
        at = course["shortened_at"]
        assert at["index"] == 1 and at["mark"] == "4" and at["display"] == "4"
        assert at["time"] == "2099-07-25T11:10:00"
        # The index points into the course as set.
        assert course["marks"][at["index"]]["code"] == "4"

    def test_the_course_as_set_is_left_alone(self, client):
        """course.marks is what was set; an importer may already rely on that."""
        _, course = self._shortened(client)
        assert [m["code"] for m in course["marks"]] == ["1", "4", "7"]

    def test_the_sailed_route_stops_at_the_shorten_mark(self, client):
        _, course = self._shortened(client)
        sailed = course["sailed"]
        assert [m["code"] for m in sailed["marks"]] == ["1", "4"]
        assert sailed["sequence_text"] == "1p 4p"
        rounded = [leg for leg in sailed["legs"] if not leg.get("finish")]
        assert rounded[-1]["to_mark"] == "4"

    def test_then_goes_to_the_middle_of_the_finish_line(self, client):
        """Where the chart draws its dashed leg."""
        rid, course = self._shortened(client)
        sailed = course["sailed"]
        last = sailed["legs"][-1]
        assert last["finish"] is True and last["to"] == "Finish" and last["from_mark"] == "4"
        assert last["distance_nm"] > 0 and 0 <= last["bearing_deg"] < 360
        (s_lat, s_lon), (h_lat, h_lon) = track.race_finish_line_points(ro.get_race(rid))
        assert sailed["finish"]["lat"] == pytest.approx((s_lat + h_lat) / 2, abs=1e-6)
        assert sailed["finish"]["lon"] == pytest.approx((s_lon + h_lon) / 2, abs=1e-6)
        assert len([leg for leg in sailed["legs"] if leg.get("finish")]) == 1

    def test_the_sailed_length_includes_the_run_home(self, client):
        _, course = self._shortened(client)
        sailed = course["sailed"]
        assert sailed["length_nm"] == pytest.approx(sum(l["distance_nm"] for l in sailed["legs"]), abs=0.01)

    def test_a_lapped_course_names_the_lap(self, client):
        """Shortened at mark 4 on the second time round."""
        _, course = self._shortened(client, index=4, laps=2)
        assert course["laps"] == 2
        assert "lap 2" in course["shortened_at"]["label"]
        assert [m["code"] for m in course["sailed"]["marks"]] == ["1", "4", "7", "1", "4"]
        assert "×" not in course["sailed"]["sequence_text"]

    def test_an_index_the_course_no_longer_reaches_is_not_a_shortening(self, client):
        """A course changed after it was shortened can leave the index behind."""
        _, course = self._shortened(client, index=12)
        assert course["shortened"] is False and course["shortened_at"] is None
        assert course["sailed"]["legs"] == course["legs"]


class TestAShortenedCourseIsNotLapped:
    """Found while doing this: the lap count survived the truncation, so every
    board put a x2 after marks that were already the expansion."""

    def _lapped(self):
        return courses.course_from_sequence(SEQUENCE, laps=2)

    def test_the_lap_fields_go(self):
        s = courses.apply_course_shortening(self._lapped(), 4)
        assert s["laps"] == 1 and "lap_marks" not in s
        assert courses.course_sequence_text(s) == "1p 4p 7p 1p 4p"

    def test_the_course_it_came_from_is_untouched(self):
        lapped = self._lapped()
        courses.apply_course_shortening(lapped, 4)
        assert lapped["laps"] == 2 and lapped["lap_marks"]

    def test_a_fixed_courses_text_is_not_carried_over(self):
        fixed = {"course_no": 1, "marks": SEQUENCE, "sequence_text": "1p 4p 7p"}
        s = courses.apply_course_shortening(fixed, 0)
        assert "sequence_text" not in s
        assert courses.course_sequence_text(s) == "1p"

    def test_the_dashboard_board_has_no_lap_count(self, client):
        rid = _race(course_set=1, laps=2)
        _update(rid, shortened_at_index=4, shortened_at_mark="4")
        with ro.app.test_request_context():
            status = ro.dashboard_current_race_status()
        assert status["course_shortened"] is True
        assert status["board_laps"] == 1
        assert [m["mark"] for m in status["board_marks"]] == ["1", "4", "7", "1", "4"]


class TestAWaypointIsNotRounded:
    """course.marks folded a waypoint's "via" into port, so an importer reading
    it was told to leave the turning point off Cilan to port."""

    def _course(self, client):
        rid = _race(course_set=1)
        seq = [{"mark": "1", "rounding": "port"}, {"mark": "TC", "rounding": "via"},
               {"mark": "Y", "rounding": "starboard"}]
        _update(rid, custom_course_json=json.dumps({"marks": seq, "laps": 1}))
        return _api(client)["course"]

    def test_the_course_marks_say_via(self, client):
        tc = next(m for m in self._course(client)["marks"] if m["code"] == "TC")
        assert tc["rounding"] == "via" and tc["waypoint"] is True
        assert tc["token"] == "TC"
        assert [c["rounding"] for c in tc["expanded_components"]] == ["via"]

    def test_and_agree_with_the_expanded_marks(self, client):
        course = self._course(client)
        expanded = next(m for m in course["expanded_marks"] if m["code"] == "TC")
        board = next(m for m in course["marks"] if m["code"] == "TC")
        assert expanded["rounding"] == board["rounding"] == "via"

    def test_real_marks_are_untouched(self, client):
        marks = {m["code"]: m for m in self._course(client)["marks"]}
        assert marks["1"]["rounding"] == "port" and "waypoint" not in marks["1"]
        assert marks["Y"]["rounding"] == "starboard"
        assert [c["code"] for c in marks["Y"]["expanded_components"]] == ["YB", "YA"]


class TestNothingExistingChanged:
    def test_the_fields_an_importer_reads_are_all_still_there(self, client):
        _race(course_set=1)
        body = _api(client)
        for key in ("id", "name", "course_no", "custom_course", "start_time",
                    "first_start_time", "status", "entry_count", "race_finished"):
            assert key in body["race"], key
        for key in ("source", "sequence_text", "length_nm", "marks", "expanded_marks", "legs"):
            assert key in body["course"], key
        assert body["marks"]
