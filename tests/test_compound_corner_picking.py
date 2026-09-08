"""A race officer can put one corner of a compound mark on the course.

Y and A each name a *pair* of buoys, and until now only the pair was offered: the
builder listed Y and A, and the validator refused a corner with "add the parent
instead". That is right for the usual case — the sailing instructions name the
pair — but it left no way to send the fleet round only one of them.

The corners are ordinary coordinate-bearing marks, so nothing about the geometry
needed to change. What needed care was everything that *names* a mark: the picker,
the sort order, the course board and the audio announcement.
"""
from __future__ import annotations

import json

import pytest

from core import appstate, courses


class TestThePickerOffersThem:
    def test_every_corner_is_selectable(self):
        names = courses.selectable_mark_names()
        for corner in ("YA", "YB", "AA", "AB"):
            assert corner in names, f"{corner} is not offered"

    def test_the_parents_are_still_offered(self):
        names = courses.selectable_mark_names()
        assert "Y" in names and "A" in names

    def test_corners_sort_directly_under_their_parent(self):
        """Alphabetically they would land at the end, nowhere near the mark they
        are half of."""
        names = courses.selectable_mark_names()
        assert names[names.index("Y"):names.index("Y") + 3] == ["Y", "YA", "YB"]
        assert names[names.index("A"):names.index("A") + 3] == ["A", "AA", "AB"]

    def test_nothing_else_moved(self):
        names = courses.selectable_mark_names()
        assert names[:11] == ["1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "O"]


class TestTheValidatorAcceptsThem:
    def test_a_course_to_one_corner_is_accepted(self):
        seq = courses.validate_course_sequence_json(
            json.dumps([{"mark": "YA", "rounding": "port"}, {"mark": "O", "rounding": "port"}]))
        assert [s["mark"] for s in seq] == ["YA", "O"]

    def test_an_unknown_mark_is_still_refused(self):
        with pytest.raises(ValueError, match="Unknown mark"):
            courses.validate_course_sequence_json(json.dumps([{"mark": "ZZ", "rounding": "port"}]))


class TestWhatItIsCalled:
    """The key is YA because the keys are upper-case throughout. Nobody should
    ever see that."""

    def test_the_board_uses_the_display_label(self):
        c = {"marks": [{"mark": "YA", "rounding": "port"}, {"mark": "O", "rounding": "port"}]}
        assert courses.course_sequence_text(c) == "Yap Op"

    def test_the_board_is_unchanged_for_ordinary_marks(self):
        c = {"marks": [{"mark": "1", "rounding": "port"}, {"mark": "2", "rounding": "starboard"}]}
        assert courses.course_sequence_text(c) == "1p 2s"

    def test_a_corner_is_spoken_by_its_feature_not_its_key(self):
        """"mark YA" over the hut audio is "mark why-ay"."""
        assert courses.spoken_mark("YA") == "Gwylan Islands corner Ya"
        assert courses.spoken_mark("YB") == "Gwylan Islands corner Yb"

    def test_the_parent_is_spoken_as_before(self):
        assert courses.spoken_mark("Y") == "Gwylan Islands"
        assert courses.spoken_mark("1") == "mark one"
        assert courses.spoken_mark("O") == "outer distance mark"


class TestTheGeometryIsUnaffected:
    def test_one_corner_expands_to_itself(self):
        c = {"marks": [{"mark": "YA", "rounding": "port"}, {"mark": "O", "rounding": "port"}]}
        assert [p["mark"] for p in courses.expand_course_points(c)] == ["O", "YA", "O"]

    def test_the_parent_still_expands_to_both(self):
        c = {"marks": [{"mark": "Y", "rounding": "port"}, {"mark": "O", "rounding": "port"}]}
        assert [p["mark"] for p in courses.expand_course_points(c)] == ["O", "YA", "YB", "O"]

    def test_a_course_to_one_corner_has_a_length(self):
        """It would be None if the corner had no position — the thing that made the
        parent unusable in the GPS walk."""
        c = {"marks": [{"mark": "YA", "rounding": "port"}, {"mark": "O", "rounding": "port"}]}
        assert courses.course_length_nm(c) is not None


class TestShorteningAtACorner:
    def test_a_corner_can_be_a_shorten_point_and_reads_properly(self):
        c = {"marks": [{"mark": "1", "rounding": "port"},
                       {"mark": "YA", "rounding": "port"},
                       {"mark": "O", "rounding": "port"}]}
        opts = courses.course_shorten_options(c)
        corner = [o for o in opts if o["code"] == "YA"]
        assert corner, "a corner in the course is not offered as a shortening point"
        assert corner[0]["display"] == "Ya"

    def test_shortening_there_truncates_the_course(self):
        c = {"marks": [{"mark": "1", "rounding": "port"},
                       {"mark": "YA", "rounding": "port"},
                       {"mark": "O", "rounding": "port"}]}
        short = courses.apply_course_shortening(c, 1)
        assert [m["mark"] for m in short["marks"]] == ["1", "YA"]
