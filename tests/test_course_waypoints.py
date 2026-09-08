"""Waypoints: points that bend a leg without being marks.

Pwllheli's long courses run west past the Llyn peninsula. A straight line from
mark 2 to the Gwylan Islands crosses Mynytho, Llangian and Trwyn Cilan, so the
chart drew a course over land — and that was the least of it. The leg measured
10.90 nm against the 13.06 nm actually sailed (20% short), and one bearing of
243T stood in for a 211T reach followed by a 278T broad reach, which with the
wind at 145T is a 66 degree TWA and a 133 degree TWA modelled as a single 98.

A waypoint fixes the geometry without inventing a mark: boats are not asked to
round it, it is not on the course board, not in the announcement, not in the
shortening options, not in the mark count and not drawn on the chart. The course
simply changes direction there.
"""
from __future__ import annotations

import math

import pytest

from core import appstate, courses, track

WP = "WPT_TEST"
MARK_2 = (52.8733333333, -4.4263333333)
CILAN = (52.7760, -4.5230)                 # the corner the straight line cuts off
YA = (52.789985, -4.693318333333333)


@pytest.fixture
def waypoint(monkeypatch):
    """A waypoint in the mark table for the duration of one test.

    Injected rather than written into data/marks.json: where the club wants its
    turning points is a navigational decision, not one to invent in a test.
    """
    marks = dict(appstate.MARKS)
    marks[WP] = {"name": "Trwyn Cilan turning point", "display": "Cilan",
                 "lat": CILAN[0], "lon": CILAN[1], "waypoint": True}
    monkeypatch.setattr(appstate, "MARKS", marks)
    return WP


def course(*marks):
    return {"marks": [{"mark": m, "rounding": "via" if m == WP else "port"} for m in marks]}


class TestItIsInTheGeometry:
    def test_the_chart_route_goes_round_the_corner(self, waypoint):
        pts = [p["mark"] for p in courses.expand_course_points(course("2", WP, "Y", "O"))]
        assert pts == ["O", "2", WP, "YA", "YB", "O"]

    def test_the_course_gets_longer_by_the_corner_it_no_longer_cuts(self, waypoint):
        direct = courses.course_length_nm(course("2", "Y", "O"))
        routed = courses.course_length_nm(course("2", WP, "Y", "O"))
        assert routed > direct
        assert round(routed - direct, 1) == 2.2, "the corner off Cilan is worth about 2.2 nm"

    def test_the_leg_is_split_into_two_bearings(self, waypoint):
        """One leg at 243T was standing in for a reach and a broad reach."""
        legs = courses.course_legs(course("2", WP, "Y", "O"))
        bearings = [round(l["bearing_deg"]) for l in legs if l.get("bearing_deg") is not None]
        assert 211 in bearings and 278 in bearings
        assert 243 not in bearings


class TestItIsNotAMark:
    def test_the_course_board_does_not_show_it(self, waypoint):
        assert courses.course_sequence_text(course("2", WP, "Y", "O")) == "2p Yp Op"

    def test_the_board_is_identical_with_and_without_one(self, waypoint):
        assert (courses.course_sequence_text(course("2", WP, "Y", "O"))
                == courses.course_sequence_text(course("2", "Y", "O")))

    def test_the_announcement_does_not_mention_it(self, waypoint):
        said = courses.course_announcement_text({"course_no": 1}, course("2", WP, "Y", "O"))
        assert "Cilan" not in said and WP not in said
        assert "Gwylan Islands" in said

    def test_it_is_not_offered_as_a_shortening_point(self, waypoint):
        opts = courses.course_shorten_options(course("2", WP, "Y", "O"))
        assert [o["code"] for o in opts] == ["2", "Y", "O"]

    def test_it_carries_no_rounding_direction(self, waypoint):
        import json
        seq = courses.validate_course_sequence_json(
            json.dumps([{"mark": "2", "rounding": "port"},
                        {"mark": WP, "rounding": "starboard"}]))
        assert seq[1]["rounding"] == "via", "a turning point cannot be left on a hand"


class TestTheGate:
    """A waypoint is passed by crossing the perpendicular at it, not by proximity.

    A radius cannot do this job: boats beating past a headland pass a long way
    offshore, and the radius test fires on proximity alone with no departure to
    hold it back, so a radius wide enough to catch them advances the walk while
    they are still miles short of the corner.
    """

    # Work in a local metre frame and convert back at the end. Doing the arithmetic
    # in degrees is how the first version of these helpers put "5000 m offshore" 0.6 m
    # from the waypoint, which made four of these cases pass without testing anything.
    M_LAT = 111132.0
    M_LON = 111320.0 * math.cos(math.radians(CILAN[0]))

    def _unit_leg(self):
        lx = (CILAN[1] - MARK_2[1]) * self.M_LON      # metres east
        ly = (CILAN[0] - MARK_2[0]) * self.M_LAT      # metres north
        n = math.hypot(lx, ly)
        return lx / n, ly / n

    def _offset(self, east_m, north_m):
        return {"lat": CILAN[0] + north_m / self.M_LAT,
                "lon": CILAN[1] + east_m / self.M_LON}

    def along(self, metres):
        """A point `metres` along the 2 -> Cilan leg beyond Cilan (negative = short)."""
        ux, uy = self._unit_leg()
        return self._offset(ux * metres, uy * metres)

    def at(self, along_m, across_m):
        """`along_m` up the leg past Cilan, `across_m` off to one side of it."""
        ux, uy = self._unit_leg()
        return self._offset(ux * along_m - uy * across_m,
                            uy * along_m + ux * across_m)

    PREV = {"lat": MARK_2[0], "lon": MARK_2[1]}
    WPT = {"lat": CILAN[0], "lon": CILAN[1]}

    @pytest.mark.parametrize("short_by", [8000, 4000, 2000, 500, 100])
    def test_it_does_not_fire_before_the_corner(self, short_by):
        assert not track.passed_waypoint(self.PREV, self.WPT, self.along(-short_by))

    @pytest.mark.parametrize("across", [0, 500, 2000, 5000, -2000, -5000])
    def test_it_fires_however_far_offshore_the_boat_passes(self, across):
        """The case that ruled out a radius: a boat beating past, miles off.

        Taken 200 m past the gate rather than exactly on it. Sitting precisely on
        the perpendicular is a dot product of zero and the sign is then numerical
        noise — it is also a state no boat is ever sampled in.
        """
        assert track.passed_waypoint(self.PREV, self.WPT, self.at(200, across))

    @pytest.mark.parametrize("across", [500, 2000, 5000, -2000, -5000])
    def test_being_far_offshore_does_not_by_itself_count_as_passing(self, across):
        """Wide is not the same as past. A boat still short of the corner has not
        passed it however far out it is — which is the failure a wide radius has."""
        assert not track.passed_waypoint(self.PREV, self.WPT, self.at(-200, across))

    @pytest.mark.parametrize("beyond", [100, 3000, 10000])
    def test_it_stays_passed_once_past(self, beyond):
        assert track.passed_waypoint(self.PREV, self.WPT, self.along(beyond))

    def test_with_nothing_to_take_a_direction_from_it_does_not_block(self):
        assert track.passed_waypoint(None, self.WPT, self.along(-5000))


class TestTheWalk:
    def seq(self):
        return [
            {"code": "2", "lat": MARK_2[0], "lon": MARK_2[1], "radius_m": 50,
             "neighbourhood_m": 400, "via": False},
            {"code": WP, "lat": CILAN[0], "lon": CILAN[1], "radius_m": 50,
             "neighbourhood_m": 400, "via": True},
            {"code": "YA", "lat": YA[0], "lon": YA[1], "radius_m": 50,
             "neighbourhood_m": 400, "via": False},
        ]

    def walk(self, fixes):
        return track.boat_course_progress(fixes, self.seq(), (52.879, -4.399),
                                          (52.882, -4.401), None, 50.0)

    def test_the_waypoint_is_not_counted_as_a_mark(self):
        """Three points in the sequence, two of them marks."""
        out = self.walk([{"lat": MARK_2[0], "lon": MARK_2[1], "t": 100.0}])
        assert out["total"] == 2

    def test_a_boat_at_the_first_mark_is_sailing_to_the_next_mark(self):
        """Not to the waypoint: the race officer counts marks."""
        fixes = [{"lat": MARK_2[0] + 0.0001, "lon": MARK_2[1], "t": 100.0},
                 {"lat": MARK_2[0], "lon": MARK_2[1], "t": 160.0},
                 {"lat": 52.84, "lon": -4.46, "t": 400.0}]
        out = self.walk(fixes)
        assert out["next_mark"] != WP

    def test_distance_to_go_follows_the_corner(self):
        """The whole point: from mark 2 the distance must go round Cilan, not
        through Mynytho."""
        at_two = [{"lat": MARK_2[0], "lon": MARK_2[1], "t": 100.0}]
        routed = self.walk(at_two)["dist_remaining_nm"]
        straight = track.boat_course_progress(
            at_two, [s for s in self.seq() if not s["via"]],
            (52.879, -4.399), (52.882, -4.401), None, 50.0)["dist_remaining_nm"]
        assert routed > straight
        assert round(routed - straight, 1) == 2.2
