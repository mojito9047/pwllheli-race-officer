"""A mark can carry its own rounding radius.

One radius for every mark is a compromise. A buoy on a long scope swings a big
circle and needs a generous radius; one boats are told to give a wide berth needs
a bigger one still; and a radius set large enough for those is large enough, at a
mark passed twice in a course, to swallow roundings that did not happen.

So the Settings value stays as the default and a mark may override it. Blank is
not stored as a number: a mark that inherits should follow the setting when it
changes, rather than being frozen at whatever it was the day somebody last edited
the mark.
"""
from __future__ import annotations

import json

import pytest

import app as ro
import core.marks as marks
import core.track as track


@pytest.fixture
def marks_file(client, monkeypatch, tmp_path):
    data = {"marks": {
        "T1": {"name": "Test one", "lat": 52.88, "lon": -4.40,
               "lat_text": "", "lon_text": "", "buoy": "", "top_mark": ""},
        "T2": {"name": "Wide berth", "lat": 52.89, "lon": -4.41,
               "lat_text": "", "lon_text": "", "rounding_radius_m": 120},
        "O": {"name": "ODM", "lat": 52.87, "lon": -4.42, "lat_text": "", "lon_text": ""},
    }}
    path = tmp_path / "marks.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(marks, "_marks_path", lambda: path)
    monkeypatch.setattr(ro.appstate, "MARKS", data["marks"])
    return path


def stored(path):
    return json.loads(path.read_text(encoding="utf-8"))["marks"]


class TestReadingTheValue:
    @pytest.mark.parametrize("given, expected", [
        ("", None), (None, None), ("   ", None),
        ("60", 60), (60, 60), (60.4, 60), ("60.6", 61),
        ("10", 10), ("500", 500),
    ])
    def test_what_counts_as_a_radius(self, given, expected):
        ok, radius, _ = marks.parse_rounding_radius(given)
        assert ok and radius == expected

    @pytest.mark.parametrize("given", ["9", "501", "-20", "0"])
    def test_out_of_range_is_refused(self, given):
        ok, _, err = marks.parse_rounding_radius(given)
        assert not ok and "between" in err

    def test_nonsense_is_refused(self):
        ok, _, err = marks.parse_rounding_radius("wide")
        assert not ok and "number" in err

    def test_a_mark_without_one_inherits(self, marks_file):
        assert marks.mark_rounding_radius(stored(marks_file)["T1"]) is None

    def test_a_mark_with_one_reports_it(self, marks_file):
        assert marks.mark_rounding_radius(stored(marks_file)["T2"]) == 120

    def test_rubbish_in_the_file_does_not_break_the_walk(self):
        """It falls back to inheriting rather than raising mid-race."""
        assert marks.mark_rounding_radius({"rounding_radius_m": "wide"}) is None


class TestSavingIt:
    def test_it_can_be_set_when_editing(self, marks_file):
        ok, _ = marks.update_mark("T1", "Test one", 52.88, -4.40,
                                  rounding_radius_m="80", by="admin")
        assert ok
        assert stored(marks_file)["T1"]["rounding_radius_m"] == 80

    def test_blank_clears_it_rather_than_storing_a_number(self, marks_file):
        """The whole point of inheriting: it must not be frozen at today's value."""
        ok, _ = marks.update_mark("T2", "Wide berth", 52.89, -4.41,
                                  rounding_radius_m="", by="admin")
        assert ok
        assert "rounding_radius_m" not in stored(marks_file)["T2"]

    def test_a_bad_value_is_refused_and_changes_nothing(self, marks_file):
        ok, err = marks.update_mark("T2", "Renamed", 52.89, -4.41,
                                    rounding_radius_m="9", by="admin")
        assert not ok and "between" in err
        after = stored(marks_file)["T2"]
        assert after["rounding_radius_m"] == 120 and after["name"] == "Wide berth"

    def test_it_can_be_set_when_adding(self, marks_file):
        ok, _ = marks.add_mark("T3", "New", 52.90, -4.43, rounding_radius_m="45")
        assert ok
        assert stored(marks_file)["T3"]["rounding_radius_m"] == 45

    def test_adding_without_one_leaves_the_key_out(self, marks_file):
        ok, _ = marks.add_mark("T4", "Plain", 52.91, -4.44)
        assert ok
        assert "rounding_radius_m" not in stored(marks_file)["T4"]

    def test_setting_a_radius_is_not_a_position_change(self, marks_file):
        """Editing the radius must not claim somebody went out and surveyed it."""
        marks.update_mark("T1", "Test one", 52.88, -4.40,
                          rounding_radius_m="80", by="admin")
        assert "position_set_at" not in stored(marks_file)["T1"]


class TestTheWalkUsesIt:
    @pytest.fixture
    def race(self, marks_file, monkeypatch):
        monkeypatch.setattr(track, "_effective_course", lambda race: {
            "marks": [{"mark": "T1", "rounding": "port"},
                      {"mark": "T2", "rounding": "port"},
                      {"mark": "O", "rounding": "port"}]})
        return None      # race_marks(None) uses the current marks

    def test_the_sequence_carries_each_mark_s_own_radius(self, race):
        seq = {s["code"]: s["radius_m"] for s in track.course_rounding_sequence(race)}
        assert seq["T2"] == 120
        assert seq["T1"] is None, "an inheriting mark must not be frozen at a number"

    def test_a_boat_inside_the_wider_radius_rounds_that_mark(self, marks_file):
        """80 m from T2: outside a 50 m default, inside its own 120 m."""
        seq = [{"code": "T2", "lat": 52.89, "lon": -4.41, "radius_m": 120},
               {"code": "END", "lat": 52.87, "lon": -4.42, "radius_m": None}]
        near = _point_north_of(52.89, -4.41, 80.0)
        progress = track.boat_course_progress(
            [{"t": 100.0, "lat": near[0], "lon": near[1]}], seq,
            (52.87, -4.42), (52.87, -4.43), None, 50.0)
        assert progress["rounded"] == 1

    def test_and_would_not_have_on_the_default(self, marks_file):
        seq = [{"code": "T2", "lat": 52.89, "lon": -4.41, "radius_m": None},
               {"code": "END", "lat": 52.87, "lon": -4.42, "radius_m": None}]
        near = _point_north_of(52.89, -4.41, 80.0)
        progress = track.boat_course_progress(
            [{"t": 100.0, "lat": near[0], "lon": near[1]}], seq,
            (52.87, -4.42), (52.87, -4.43), None, 50.0)
        assert progress["rounded"] == 0


def _point_north_of(lat, lon, metres):
    return (lat + metres / 111320.0, lon)


class TestThePage:
    def test_the_column_shows_an_override(self, logged_in_client, marks_file):
        html = logged_in_client.get("/admin/marks").get_data(as_text=True)
        assert "120&nbsp;m" in html

    def test_an_inheriting_mark_shows_what_it_inherits(self, logged_in_client,
                                                       marks_file):
        """Rather than a blank cell the reader has to go and look up."""
        html = logged_in_client.get("/admin/marks").get_data(as_text=True)
        assert "50&nbsp;m" in html

    def test_the_edit_form_offers_the_field(self, logged_in_client, marks_file):
        html = logged_in_client.get("/admin/marks").get_data(as_text=True)
        assert 'name="rounding_radius_m"' in html

    def test_the_edit_panel_is_not_inside_the_actions_cell(self, logged_in_client,
                                                           marks_file):
        """Where it used to be, and where it cannot go back.

        A table cell is sized to its content, so a six-field form in the actions
        cell was granted every pixel it asked for and never wrapped: opening one
        made the page 1216px wider than the window, and reading the rest of the
        table meant scrolling past the form. It now has a row of its own spanning
        the table, which is what gives the fields a bounded width to wrap inside.
        """
        html = logged_in_client.get("/admin/marks").get_data(as_text=True)
        assert 'class="mark-edit-row"' in html
        assert "colspan" in html.split('class="mark-edit-row"')[1].split(">")[1]
        actions = html.split('class="actions-cell"')[1].split("</td>")[0]
        assert "mark-edit" not in actions, "the edit panel is back in the cell"

    def test_saving_it_through_the_form(self, logged_in_client, csrf_post, marks_file):
        csrf_post("/marks/T1/edit", {"name": "Test one", "lat": "52.88",
                                     "lon": "-4.40", "rounding_radius_m": "75"})
        assert stored(marks_file)["T1"]["rounding_radius_m"] == 75
