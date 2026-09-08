"""The rounding gate: reaches far on the hand a boat must pass, not on the other.

Written against the night race of 2026-08-08, where CRACKAJACK sailed round AA
420 m off, on the correct side, and the walk missed it by 20 m against a 400 m
neighbourhood — stalling the boat for six hours and killing its automatic finish.

The fixture builds tracks in **metres** and checks its own geometry before any
assertion runs. An earlier fixture in this project mixed degrees and metres, put a
boat "5000 m offshore" 0.6 m from the mark, and four cases passed while testing
nothing at all.
"""
from __future__ import annotations

import math

import pytest

from core import rounding
from core.track import MarkApproach, rounded_mark

MARK_LAT, MARK_LON = 52.8, -4.5
_M_PER_DEG_LAT = 111132.0
_M_PER_DEG_LON = 111320.0 * math.cos(math.radians(MARK_LAT))

# The leg arrives from due south, so "east of the mark" is to the right of the
# direction of travel — which is where a boat leaving the mark to port sails.
FROM_SOUTH = {"lat": MARK_LAT - 3000.0 / _M_PER_DEG_LAT, "lon": MARK_LON}


def at(east_m: float, north_m: float, t: float = 0.0) -> dict:
    return {"t": t, "lat": MARK_LAT + north_m / _M_PER_DEG_LAT,
            "lon": MARK_LON + east_m / _M_PER_DEG_LON}


def metres_from_mark(fix: dict) -> float:
    e, n = rounding.enu_m(fix["lat"], fix["lon"], MARK_LAT, MARK_LON)
    return math.hypot(e, n)


def mark(side: str = "port", radius_m: float = 50.0, accuracy_m=None) -> dict:
    return {"code": "T", "lat": MARK_LAT, "lon": MARK_LON, "side": side,
            "radius_m": radius_m, "accuracy_m": accuracy_m, "from_point": FROM_SOUTH}


def crossed(east_m: float, side: str = "port", reach_m: float = 500.0,
            radius_m: float = 50.0, accuracy_m=None, northbound: bool = True) -> bool:
    """Sail past the mark at `east_m` to the east of it, and see if the gate counts."""
    m = mark(side, radius_m, accuracy_m)
    a, b = (-400.0, 400.0) if northbound else (400.0, -400.0)
    return rounding.crossed_gate(at(east_m, a, 0.0), at(east_m, b, 60.0),
                                 FROM_SOUTH, m, side, reach_m,
                                 rounding.undetermined_side_m(accuracy_m, radius_m))


class TestTheFixtureIsHonest:
    """If these fail, every other test in the file is meaningless."""

    def test_a_point_200_m_east_really_is_200_m_away(self):
        assert metres_from_mark(at(200.0, 0.0)) == pytest.approx(200.0, abs=1.0)

    def test_east_and_north_are_not_swapped(self):
        e, n = rounding.enu_m(at(300.0, 0.0)["lat"], at(300.0, 0.0)["lon"], MARK_LAT, MARK_LON)
        assert e == pytest.approx(300.0, abs=1.0)
        assert n == pytest.approx(0.0, abs=1.0)

    def test_the_leg_really_does_arrive_from_the_south(self):
        e, n = rounding.enu_m(FROM_SOUTH["lat"], FROM_SOUTH["lon"], MARK_LAT, MARK_LON)
        assert n < -2000.0 and abs(e) < 1.0


class TestTheGateReachesFarOnTheCorrectHand:
    """The wide rounding is the whole reason this detector exists."""

    def test_a_rounding_400_m_off_on_the_correct_hand_counts(self):
        assert crossed(400.0, "port")

    def test_so_does_one_at_the_full_reach(self):
        assert crossed(490.0, "port")

    def test_but_not_one_beyond_it(self):
        assert not crossed(510.0, "port")

    def test_the_reach_is_configurable(self):
        assert not crossed(700.0, "port", reach_m=500.0)
        assert crossed(700.0, "port", reach_m=1000.0)

    def test_starboard_roundings_are_the_mirror_image(self):
        assert crossed(-400.0, "starboard")
        assert not crossed(400.0, "starboard")


class TestTheGateBarelyReachesOnTheWrongHand:
    """This is the only thing in the walk that knows a mark has a required side."""

    def test_a_wide_pass_on_the_wrong_hand_does_not_count(self):
        assert not crossed(-400.0, "port")

    def test_nor_does_one_just_outside_the_rounding_radius(self):
        assert not crossed(-80.0, "port", radius_m=50.0)

    def test_but_inside_the_radius_the_side_is_not_asked_about(self):
        """Closer to the mark than we know where the mark is: no side to be had."""
        assert crossed(-20.0, "port", radius_m=50.0)
        assert crossed(20.0, "port", radius_m=50.0)

    def test_a_mark_known_only_roughly_widens_the_no_side_zone(self):
        """80 m of position doubt means a side cannot be judged at 60 m."""
        assert not crossed(-60.0, "port", radius_m=50.0)
        assert crossed(-60.0, "port", radius_m=50.0, accuracy_m=80.0)


class TestDirectionOfTravel:
    def test_sailing_back_down_the_leg_does_not_re_round_the_mark(self):
        assert crossed(300.0, "port", northbound=True)
        assert not crossed(300.0, "port", northbound=False)


class TestItWorksAtTheClubsReportingRate:
    """61 s between fixes at 8 kn is 250 m of travel: a whole rounding can fall
    between two reports. Crossing is a fact about the chord, so that is survivable
    where accumulating a swept angle was not."""

    def test_a_rounding_entirely_between_two_fixes_still_counts(self):
        m = mark("port")
        before, after = at(120.0, -180.0, 0.0), at(60.0, 140.0, 61.0)
        step = math.hypot(60.0 - 120.0, 140.0 + 180.0)
        assert step > 250.0, "the fixture must actually straddle the mark"
        assert min(metres_from_mark(before), metres_from_mark(after)) > 50.0
        assert rounding.crossed_gate(before, after, FROM_SOUTH, m, "port", 500.0,
                                     rounding.undetermined_side_m(None, 50.0))


class TestTheWidthOfNotKnowing:
    def test_it_is_the_rounding_radius_by_default(self):
        assert rounding.undetermined_side_m(None, 50.0) == 50.0

    def test_a_wide_berth_mark_widens_it(self):
        assert rounding.undetermined_side_m(None, 120.0) == 120.0

    def test_a_badly_known_mark_widens_it_too(self):
        assert rounding.undetermined_side_m(300.0, 50.0) == 300.0

    def test_and_it_is_never_below_the_radius(self):
        assert rounding.undetermined_side_m(1.0, 120.0) == 120.0


class TestTheGateNeverBlocksAWalk:
    """Three detectors, any one sufficient. The gate can only ever add a rounding."""

    def test_a_wrong_side_pass_can_still_round_by_closest_approach(self):
        """A boat 300 m out on the wrong hand fails the gate, and the catch-all
        still has it: this app does not adjudicate wrong-side roundings, the race
        officer does, on a protest, with the video and the track."""
        seq = [mark("port"),
               {"code": "U", "lat": MARK_LAT + 0.06, "lon": MARK_LON, "side": "port",
                "radius_m": 50.0, "accuracy_m": None, "from_point": None}]
        approach, prev, advanced = MarkApproach(), None, False
        # Out on the wrong hand, then away towards the next mark.
        for i, (e, n) in enumerate([(-300.0, -600.0), (-300.0, -200.0), (-300.0, 0.0),
                                    (-300.0, 300.0), (-200.0, 900.0), (-50.0, 2000.0)]):
            fix = at(e, n, i * 60.0)
            if rounded_mark(prev, fix, seq, 0, 50.0, approach):
                advanced = True
            prev = fix
        assert not crossed(-300.0, "port"), "the gate should refuse this pass"
        assert advanced, "but the walk must not stall on it"
