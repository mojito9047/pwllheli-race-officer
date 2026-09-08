"""A waypoint must not surface as a mark anywhere a course is printed or drawn.

Reported from a test server: a made-up course reading 1p AAs Yp Fs showed a red
**TCp** chip for the Trwyn Cilan turning point, and the chart drew TC as a course
mark. The stored course was correct — `{"mark": "TC", "rounding": "via"}` — and it
was the read path that was wrong, in two separate places, plus a third in the
browser:

* ``course_from_sequence`` rebuilt every entry as port or starboard, folding
  "via" back to "port" on the way out.
* Six templates rendered the chips by looping ``course.marks`` themselves.
* ``routePoints`` in static/course_map.js is a *third* implementation of the
  course expansion, in JavaScript, and knew nothing about waypoints.

The same shape as the compound-mark fault: one rule, several implementations.
"""
from __future__ import annotations

import json
import pathlib
import re

import pytest

import app as ro
from core import appstate, courses

TEMPLATES = pathlib.Path("templates")
JS = pathlib.Path("static/course_map.js")


@pytest.fixture
def waypoint(monkeypatch):
    patched = dict(appstate.MARKS)
    patched["TCX"] = {"name": "Trwyn Cilan turning point", "display": "TCx",
                      "lat": 52.77645, "lon": -4.528532, "waypoint": True}
    monkeypatch.setattr(appstate, "MARKS", patched)
    monkeypatch.setattr(courses.appstate, "MARKS", patched)
    return "TCX"


def sequence(waypoint_code):
    return [{"mark": "1", "rounding": "port"},
            {"mark": waypoint_code, "rounding": "via"},
            {"mark": "O", "rounding": "port"}]


class TestTheRoundingSurvivesTheReadPath:
    def test_course_from_sequence_keeps_via(self, waypoint):
        c = courses.course_from_sequence(sequence(waypoint))
        assert [m["rounding"] for m in c["marks"]] == ["port", "via", "port"]

    def test_it_is_not_folded_to_port(self, waypoint):
        """The reported symptom: a red TCp chip for something nobody rounds."""
        c = courses.course_from_sequence(sequence(waypoint))
        assert "TCXp" not in [m["token"] for m in c["marks"]]

    def test_a_saved_race_course_round_trips(self, client, waypoint):
        with ro.app.app_context():
            with ro.get_db() as db:
                cur = db.execute(
                    "INSERT INTO races (name, course_no, start_time, created_at,"
                    " custom_course_json) VALUES ('W', 1, '2026-08-01T10:00:00',"
                    " '2026-08-01T10:00:00', ?)",
                    (json.dumps({"marks": sequence(waypoint)}),))
                db.commit()
                race = ro.get_race(int(cur.lastrowid))
            c = courses.course_for_race(race)
        assert [m["rounding"] for m in c["marks"]] == ["port", "via", "port"]


class TestTheBoardList:
    def test_board_marks_leaves_the_waypoint_out(self, waypoint):
        c = courses.course_from_sequence(sequence(waypoint))
        assert [m["mark"] for m in c["board_marks"]] == ["1", "O"]

    def test_the_full_list_keeps_it_for_the_geometry(self, waypoint):
        c = courses.course_from_sequence(sequence(waypoint))
        assert [m["mark"] for m in c["marks"]] == ["1", waypoint, "O"]

    def test_a_fixed_course_also_has_the_list(self):
        """Templates fall back to course.marks, so every course must carry it."""
        c = courses.course_for_display(appstate.COURSE_BY_NO[1])
        assert "board_marks" in c

    def test_the_board_text_agrees_with_the_chips(self, waypoint):
        c = courses.course_from_sequence(sequence(waypoint))
        from_chips = " ".join(f"{courses.mark_display_code(m['mark'])}{m['rounding'][0]}"
                              for m in c["board_marks"])
        assert from_chips == courses.course_sequence_text(c)


class TestNoTemplateRollsItsOwn:
    """Six templates print these chips. One of them getting it right is not enough."""

    CHIP = re.compile(r'for m in course\.marks')

    def test_no_template_loops_the_raw_mark_list_for_chips(self):
        offenders = []
        for path in TEMPLATES.glob("*.html"):
            text = path.read_text(encoding="utf-8")
            if self.CHIP.search(text):
                offenders.append(path.name)
        assert not offenders, (
            f"{offenders} print the course chips from course.marks, which includes "
            "waypoints. Use course.board_marks."
        )

    def test_the_templates_that_print_chips_use_board_marks(self):
        using = [p.name for p in TEMPLATES.glob("*.html")
                 if "course.board_marks" in p.read_text(encoding="utf-8")]
        assert len(using) >= 4, f"only {using} use board_marks — has one been missed?"


class TestTheChartKnowsToo:
    """static/course_map.js expands the course itself, in the browser."""

    def test_the_js_route_builder_handles_waypoints(self):
        src = JS.read_text(encoding="utf-8")
        route = src[src.index("function routePoints"):src.index("function allKnownPoints")]
        assert ".waypoint" in route, (
            "routePoints does not look at the waypoint flag, so the chart will draw "
            "a turning point as a course mark"
        )

    def test_the_marker_loop_skips_them(self):
        src = JS.read_text(encoding="utf-8")
        assert "if (p.waypoint) return;" in src

    def test_the_background_mark_layer_skips_them_too(self):
        """There is no buoy at a turning point. Drawing one faintly among the real
        marks — beside 2, 3, T and E — says there is."""
        src = JS.read_text(encoding="utf-8")
        fn = src[src.index("function allKnownPoints"):src.index("// The **start** line")]
        assert ".waypoint" in fn
