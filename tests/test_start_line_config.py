"""The start line's mark and shore point come from data/start_finish.json.

Both used to be written into the code as Pwllheli's: every course was measured
from mark O, and the chart drew the line from O to the CHPSC bridge window. Given
Liverpool Yacht Club's start_finish.json -- a transit from the Brunswick lock
house flagstaff, bounded by the Pluckington buoy (PLK) -- the finish line was
right, because that already came from the file, but every course length came out
blank (there is no O on the Mersey) and the chart drew Pwllheli's line.

The second club here is Liverpool's, cut down to four marks.
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

import app as ro
import core.track as track
from core import appstate, courses, settings, startfinish


# Pwllheli's bridge window, as it was written into the code.
BRIDGE = (52.0 + 52.9238 / 60.0, -(4.0 + 24.0609 / 60.0))

FLAGSTAFF = (53.0 + 23.2225 / 60.0, -(2.0 + 59.0625 / 60.0))

LYC_MARKS = {
    "PLK": {"name": "Pluckington", "lat": 53.3838333333, "lon": -2.9926666667},
    "NEL": {"name": "Nelson", "lat": 53.3725, "lon": -2.9748333333},
    "MID": {"name": "Midway", "lat": 53.3583333333, "lon": -2.9516666667},
    "G11": {"name": "G11", "lat": 53.3531666667, "lon": -2.9231666667},
}

LYC_START_FINISH = {
    "start_line": {
        "seaward_end_mark": "PLK",
        "shore_end": "Brunswick lock house flagstaff (orange triangle) - survey required",
        "shore_end_position": "53° 23.2225'N 002° 59.0625'W",
    },
    "finish_line": {"seaward_end_mark": "PLK"},
    "finish_lines": [{
        "key": "lyc", "name": "LYC line", "default": True,
        "seaward": {"mark": "PLK", "label": "Pluckington (PLK)"},
        "seaward_extension_m": 100,
        "shore": {"lat": 53.3870418, "lon": -2.9843753,
                  "label": "Brunswick lock house flagstaff", "short_label": "Lock house"},
    }],
}

LYC_COURSE = {"course_no": 1, "marks": [{"mark": "G11", "rounding": "port"},
                                        {"mark": "NEL", "rounding": "starboard"}]}


@pytest.fixture()
def liverpool(monkeypatch):
    monkeypatch.setattr(appstate, "MARKS", dict(LYC_MARKS))
    monkeypatch.setattr(appstate, "START_FINISH", dict(LYC_START_FINISH))
    monkeypatch.setattr(appstate, "COURSES", [LYC_COURSE])
    monkeypatch.setattr(appstate, "COURSE_BY_NO", {1: LYC_COURSE})


def close(a, b, tol=1e-6):
    return a is not None and b is not None and all(abs(x - y) < tol for x, y in zip(a, b))


class TestPwllheliIsUnchanged:
    def test_the_start_mark_is_o(self):
        assert startfinish.start_mark() == "O"

    def test_the_shore_point_is_the_bridge_window(self):
        assert close(startfinish.shore_point(), BRIDGE)

    def test_the_chart_is_given_the_same_line(self, client):
        chart = settings.course_chart_config()
        assert chart["start_finish_mark"] == "O"
        assert close((chart["bridge_window_lat"], chart["bridge_window_lon"]), BRIDGE)
        assert chart["start_shore_short_label"] == "Bridge"
        assert chart["start_shore_label"] == "CHPSC Bridge window"

    def test_courses_are_measured_from_o(self):
        legs = courses.course_legs(appstate.COURSES[0])
        assert legs[0]["from_mark"] == "O" and legs[0]["distance_nm"] is not None


class TestAnotherClubsLine:
    def test_the_start_mark_is_theirs(self, liverpool):
        assert startfinish.start_mark() == "PLK"

    def test_the_shore_point_is_theirs(self, liverpool):
        assert close(startfinish.shore_point(), FLAGSTAFF)

    def test_courses_are_measured_from_their_mark(self, liverpool):
        legs = courses.course_legs(LYC_COURSE)
        assert [leg["from_mark"] for leg in legs] == ["PLK", "G11"]
        assert all(leg["distance_nm"] is not None for leg in legs)

    def test_a_course_has_a_length(self, liverpool):
        """Blank before: the first leg ran from an O the river does not have."""
        assert courses.course_length_nm(LYC_COURSE) is not None

    def test_made_up_courses_start_from_their_mark(self, liverpool):
        marks = courses.made_up_course_marks()
        assert marks and "PLK" not in marks and set(marks) <= {"NEL", "MID", "G11"}

    def test_the_chart_is_given_their_line(self, liverpool, client):
        chart = settings.course_chart_config()
        assert chart["start_finish_mark"] == "PLK"
        assert close((chart["bridge_window_lat"], chart["bridge_window_lon"]), FLAGSTAFF)
        assert chart["start_shore_label"] == "Brunswick lock house flagstaff"
        assert chart["start_shore_short_label"] == "Lock house"

    def test_with_no_finish_lines_they_finish_on_their_start_line(self, liverpool, monkeypatch):
        cfg = {k: v for k, v in LYC_START_FINISH.items() if k != "finish_lines"}
        monkeypatch.setattr(appstate, "START_FINISH", cfg)
        seaward, shore = track.finish_line_points()
        assert close(seaward, (LYC_MARKS["PLK"]["lat"], LYC_MARKS["PLK"]["lon"]))
        assert close(shore, FLAGSTAFF)

    def test_the_race_page_draws_their_line(self, liverpool, logged_in_client):
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - 7200))
        with ro.app.app_context():
            with ro.get_db() as db:
                cur = db.execute("INSERT INTO races (course_set, name, course_no, start_time, created_at)"
                                 " VALUES (1, 'Mersey', 1, ?, ?)", (stamp, stamp))
                db.commit()
                race_id = int(cur.lastrowid)
        html = logged_in_client.get(f"/admin/race/{race_id}").get_data(as_text=True)
        assert 'data-start-finish-mark="PLK"' in html
        assert 'data-start-shore-short-label="Lock house"' in html
        assert "53.387" in html


class TestWhereEachEndComesFrom:
    def test_the_start_line_block_comes_first(self, liverpool, monkeypatch):
        cfg = dict(LYC_START_FINISH, start_line={"seaward_end_mark": "NEL",
                                                 "shore_end_position": "53 23.0000'N 002 59.0000'W"})
        monkeypatch.setattr(appstate, "START_FINISH", cfg)
        assert startfinish.start_mark() == "NEL"
        assert close(startfinish.shore_point(), (53.0 + 23.0 / 60.0, -(2.0 + 59.0 / 60.0)))

    def test_then_the_default_finish_line(self, liverpool, monkeypatch):
        cfg = {k: v for k, v in LYC_START_FINISH.items() if k != "start_line"}
        monkeypatch.setattr(appstate, "START_FINISH", cfg)
        assert startfinish.start_mark() == "PLK"
        assert close(startfinish.shore_point(), (53.3870418, -2.9843753))

    def test_then_the_old_bridge_window_block(self, monkeypatch):
        monkeypatch.setattr(appstate, "START_FINISH", {"bridge_window": {"lat": BRIDGE[0], "lon": BRIDGE[1]}})
        assert close(startfinish.shore_point(), BRIDGE)
        assert startfinish.start_mark() == startfinish.LEGACY_START_MARK

    def test_a_file_that_gives_no_shore_point_draws_no_line(self, monkeypatch):
        monkeypatch.setattr(appstate, "START_FINISH", {"start_line": {"seaward_end_mark": "O"}})
        assert startfinish.shore_point() is None
        assert startfinish.start_line_points() is None

    def test_a_position_can_be_written_longitude_first(self):
        assert close(startfinish.position_from_text("002° 59.0625'W 53° 23.2225'N"), FLAGSTAFF)

    def test_southern_and_eastern_positions_are_negative_and_positive(self):
        assert close(startfinish.position_from_text("33 51.5'S 151 12.5'E"),
                     (-(33.0 + 51.5 / 60.0), 151.0 + 12.5 / 60.0))


def test_the_chart_has_no_pwllheli_position_to_fall_back_on():
    """With nothing on the page it used to centre on Pwllheli."""
    js = (Path(__file__).resolve().parents[1] / "static" / "course_map.js").read_text(encoding="utf-8")
    assert "52.87" not in js and "-4.40" not in js
