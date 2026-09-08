"""Editing a mark, and re-measuring one from a phone on the water.

Marks drag in storms. The app then looks for boats rounding a buoy that is no
longer there, and with a 50 m rounding radius fifty metres of drift is enough for
the course walk to decide a boat never rounded it — which stalls every mark after
it, because the walk is sequential. So a position is a measurement that needs
re-taking, not a constant.

The phone page exists because the only way to know where a mark actually is, is
to go and stand on it.
"""
from __future__ import annotations

import json

import pytest

import app as ro
import core.marks as marks


@pytest.fixture
def a_mark(client, monkeypatch, tmp_path):
    """A marks.json of our own, so a test never rewrites the real one."""
    path = tmp_path / "marks.json"
    data = {"marks": {
        "T1": {"name": "Test one", "lat": 52.88, "lon": -4.40,
               "lat_text": "52° 52.800'N", "lon_text": "004° 24.000'W",
               "buoy": "yellow", "top_mark": ""},
        "TC": {"name": "Compound", "compound": True, "components": ["T1"]},
    }}
    path.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(marks, "_marks_path", lambda: path)
    monkeypatch.setattr(ro.appstate, "MARKS", data["marks"])
    return path


def _stored(path):
    return json.loads(path.read_text(encoding="utf-8"))["marks"]


class TestEditingAMark:
    def test_a_position_can_be_corrected(self, a_mark):
        ok, _ = marks.update_mark("T1", "Test one", 52.8801, -4.4009, by="admin")
        assert ok
        m = _stored(a_mark)["T1"]
        assert m["lat"] == 52.8801 and m["lon"] == -4.4009

    def test_the_printed_position_is_rewritten_too(self, a_mark):
        """lat_text/lon_text are what the marks page and the charts show; leaving
        them behind would have the app displaying the old position."""
        marks.update_mark("T1", "Test one", 53.0, -5.0, by="admin")
        m = _stored(a_mark)["T1"]
        assert m["lat_text"] == marks.format_lat_text(53.0)
        assert m["lon_text"] == marks.format_lon_text(-5.0)

    def test_renaming_does_not_look_like_a_resurvey(self, a_mark):
        """Only a changed position stamps the provenance. Otherwise fixing a typo
        in a name would claim somebody had been out and measured it."""
        marks.update_mark("T1", "Better name", 52.88, -4.40, by="admin")
        assert "position_set_at" not in _stored(a_mark)["T1"]

    def test_a_compound_mark_cannot_be_edited_here(self, a_mark):
        """It is a name for two corner marks and has no position of its own."""
        ok, why = marks.update_mark("TC", "Compound", 52.9, -4.4, by="admin")
        assert not ok and "ompound" in why

    def test_a_position_off_the_planet_is_refused(self, a_mark):
        ok, _ = marks.update_mark("T1", "Test one", 191.0, -4.40, by="admin")
        assert not ok

    def test_a_name_is_still_required(self, a_mark):
        ok, _ = marks.update_mark("T1", "  ", 52.88, -4.40, by="admin")
        assert not ok


class TestSettingAPositionFromAPhone:
    def test_a_good_fix_moves_the_mark(self, a_mark):
        ok, msg = marks.set_mark_position("T1", 52.8805, -4.4005,
                                          by="rib_dave", accuracy_m=4.0)
        assert ok
        m = _stored(a_mark)["T1"]
        assert m["lat"] == 52.8805
        assert m["position_set_by"] == "rib_dave"
        assert m["position_source"] == "phone"
        assert m["position_accuracy_m"] == 4.0
        assert "m." in msg          # it reports how far the mark moved

    def test_a_vague_fix_is_refused(self, a_mark):
        """The rounding radius is 50 m, so a 40 m fix could move a mark most of
        the way to the edge of its own circle."""
        ok, why = marks.set_mark_position("T1", 52.8805, -4.4005,
                                          by="rib_dave", accuracy_m=40.0)
        assert not ok and "40 m" in why
        assert _stored(a_mark)["T1"]["lat"] == 52.88      # untouched

    def test_a_fix_with_no_accuracy_is_allowed(self, a_mark):
        """Not every source reports one; the other guards still apply."""
        ok, _ = marks.set_mark_position("T1", 52.8805, -4.4005, by="dave")
        assert ok

    def test_a_wild_move_needs_confirming(self, a_mark):
        """At that distance the likely explanations are the wrong mark selected
        or a phone still reporting from the clubhouse, not a dragged anchor."""
        ok, why = marks.set_mark_position("T1", 53.5, -4.40, by="dave", accuracy_m=5.0)
        assert not ok and "km from where" in why

    def test_and_goes_through_once_confirmed(self, a_mark):
        ok, _ = marks.set_mark_position("T1", 53.5, -4.40, by="dave",
                                        accuracy_m=5.0, confirm_large_move=True)
        assert ok

    def test_the_position_it_replaced_is_kept(self, a_mark):
        """Who moved mark 4, and when, is a question a race committee will ask."""
        marks.set_mark_position("T1", 52.8805, -4.4005, by="dave", accuracy_m=5.0)
        marks.set_mark_position("T1", 52.8810, -4.4010, by="jane", accuracy_m=6.0)
        history = _stored(a_mark)["T1"]["position_history"]
        assert history[0]["lat"] == 52.8805 and history[0]["set_by"] == "dave"
        assert history[1]["lat"] == 52.88            # the original

    def test_the_history_does_not_grow_without_bound(self, a_mark):
        for i in range(marks.POSITION_HISTORY_LIMIT + 6):
            marks.set_mark_position("T1", 52.88 + i * 1e-4, -4.40, by="dave", accuracy_m=5.0)
        assert len(_stored(a_mark)["T1"]["position_history"]) == marks.POSITION_HISTORY_LIMIT

    def test_how_far_a_proposed_position_would_move_a_mark(self, a_mark):
        # A ten-thousandth of a degree of latitude is about 11 m.
        moved = marks.metres_moved("T1", 52.8801, -4.40)
        assert 10 < moved < 12


class TestWhoMayDoIt:
    """A per-user permission rather than a role: the person who takes the RIB out
    after a storm is often neither an administrator nor the duty race officer.
    """

    def test_an_administrator_always_may(self):
        assert ro.user_can_set_marks({"role": "admin", "can_set_marks": 0})

    def test_a_race_officer_needs_the_permission(self):
        assert not ro.user_can_set_marks({"role": "race_officer", "can_set_marks": 0})
        assert ro.user_can_set_marks({"role": "race_officer", "can_set_marks": 1})

    def test_an_account_from_before_the_column_existed_does_not_crash(self):
        """ensure_column defaults it to 0, but a row read another way may lack it."""
        assert not ro.user_can_set_marks({"role": "race_officer"})

    def test_nobody_logged_in_may(self):
        assert not ro.user_can_set_marks(None)
