"""A made-up course, found from the marks and timed the way the race sheet times one.

The suggester searches sequences of the club's marks for the wind and a target
time. What is worth holding here is that it finds the course a race officer would
see on a chart -- the mark that is upwind is the windward mark -- and that the
time it gives is exactly the time the leg analysis gives the same course, so a
suggestion and the race sheet can never disagree.
"""
from __future__ import annotations

import math

import pytest

from core import appstate
from core.courses import (course_from_sequence, course_leg_analysis, made_up_course_marks,
                          suggest_made_up_courses)

# A flat, simple polar: 6 knots at every angle from 40 to 180 in every wind. It
# makes the geometry, not the boat, decide what the search finds.
POLAR = [{"tws": tws, "points": [{"twa": twa, "bsp": 6.0} for twa in (40, 60, 90, 120, 150, 180)]}
         for tws in (6, 10, 14)]
NO_SAILS: dict = {}


def at(bearing_deg: float, distance_nm: float, origin=(52.88, -4.40)):
    """A position this far and on this bearing from the origin (flat-earth is plenty)."""
    lat0, lon0 = origin
    north = distance_nm * math.cos(math.radians(bearing_deg)) / 60.0
    east = distance_nm * math.sin(math.radians(bearing_deg)) / (60.0 * math.cos(math.radians(lat0)))
    return {"lat": lat0 + north, "lon": lon0 + east}


@pytest.fixture()
def marks(monkeypatch):
    """O at the line; U a mile upwind in a northerly; R and L off to either side;
    a waypoint, a compound mark and a passage mark that must never be used."""
    layout = {
        "O": {"name": "Line", **at(0, 0)},
        "U": {"name": "Upwind", **at(0, 1.0)},
        "R": {"name": "Right", **at(60, 1.0)},
        "L": {"name": "Left", **at(300, 1.0)},
        "W": {"name": "Waypoint", "waypoint": True, **at(0, 0.5)},
        "C": {"name": "Compound", "compound": True, "lat": None, "lon": None},
        "CA": {"name": "Corner", "component_of": "C", **at(180, 1.0)},
        "P": {"name": "Passage", **at(180, 12.0)},
    }
    monkeypatch.setattr(appstate, "MARKS", layout)
    monkeypatch.setattr(appstate, "COURSES", [])
    return layout


def test_only_marks_a_fleet_can_round_within_reach_are_used(marks):
    assert made_up_course_marks("O") == ["L", "R", "U"]


def test_a_windward_leeward_goes_to_the_mark_that_is_upwind(marks):
    found = suggest_made_up_courses(0, 10, 45, POLAR, NO_SAILS, shape="windward_leeward")
    assert found, "a mile upwind of the line and nothing found"
    best = found[0]
    assert best["lap"][0] == "U"
    assert best["first_twa"] < 5
    assert best["sequence"][-1]["mark"] == "O", "a made-up course finishes at the line"


def test_its_time_is_the_race_sheets_time(marks):
    """The suggester adds up legs it timed one pair at a time; the race sheet
    times the built course. They must agree, or a suggestion is a promise the
    race sheet then breaks."""
    for item in suggest_made_up_courses(0, 10, 60, POLAR, NO_SAILS, shape="any"):
        course = course_from_sequence(item["sequence"])
        legs = course_leg_analysis(course, 0, 10, POLAR, NO_SAILS)
        assert sum(leg["leg_minutes"] for leg in legs) == pytest.approx(item["minutes"])


def test_it_comes_near_the_target(marks):
    for target in (30, 60, 90):
        best = suggest_made_up_courses(0, 10, target, POLAR, NO_SAILS)[0]
        assert abs(best["minutes"] - target) / target < 0.25, (target, best["minutes"])


def test_a_triangle_has_a_reach_in_it(marks):
    best = suggest_made_up_courses(0, 10, 60, POLAR, NO_SAILS, shape="triangle")[0]
    assert len(best["lap"]) == 3
    assert any(55 <= leg["twa"] <= 135 for leg in best["legs"])


def test_asked_for_reaching_every_suggestion_has_a_reach(marks):
    found = suggest_made_up_courses(0, 10, 60, POLAR, NO_SAILS, shape="reaching")
    assert found
    for item in found:
        assert any(55 <= leg["twa"] <= 135 for leg in item["legs"]), item["lap"]


def test_no_wind_angle_is_a_windward_leeward_when_nothing_is_upwind(marks):
    """In a southerly the only marks upwind are the passage mark and a corner of
    a compound, and neither may be used."""
    found = suggest_made_up_courses(180, 10, 45, POLAR, NO_SAILS, shape="windward_leeward")
    assert all("P" not in item["lap"] and "CA" not in item["lap"] for item in found)


def test_nothing_to_suggest_is_an_empty_list_not_an_error(monkeypatch):
    monkeypatch.setattr(appstate, "MARKS", {"O": {"name": "Line", "lat": 52.88, "lon": -4.40}})
    monkeypatch.setattr(appstate, "COURSES", [])
    assert suggest_made_up_courses(0, 10, 60, POLAR, NO_SAILS) == []
