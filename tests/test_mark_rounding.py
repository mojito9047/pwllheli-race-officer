"""Deciding that a boat has rounded a mark.

Until v0.247 the only test was proximity: inside the mark's rounding radius, measured
against the path between fixes. That answers "was the boat close to the mark?" when
the question the app needs answered is "has it got round and set off on the next
leg?" — and the app's job is progress and finish detection, not adjudicating the
rounding, which is the fleet's business.

The two come apart exactly where it hurts. A boat that gives a mark a wide berth
stays outside any sane radius the whole way past, and because the walk is sequential
it then reads as never having rounded *anything* after that — and its GPS finish is
never even looked for, the line only being watched once every mark is rounded.

So a second test runs alongside the radius: the boat came within the mark's
neighbourhood and has since opened up again, with the next mark nearer than it was at
the closest approach. Chosen by measuring candidates over recorded tracks rather than
by argument (scripts/compare_rounding_tests.py): against every recorded finish it agreed
with the radius to within a second, it held a rounding pushed 400 m wide where the
radius lost it at 100 m, it kept finding finishes at 30 s reporting intervals where
the radius found none, and it was never early. A turn-gate plane — the obvious first
idea — was measured too and was *worse*: 0 of 8 finishes, because an infinite plane's
sides are global rather than local to the leg being sailed.
"""
from __future__ import annotations

import pytest

from core import track


# A course with legs long enough that the flat neighbourhood applies rather than the
# short-leg cap, and with the first mark deliberately off the route from the second
# back to the line — see test_next_mark_override for why that matters.
MARK_A = (52.8900, -4.3800)
MARK_B = (52.8980, -4.3950)
LINE_A = (52.8800, -4.4100)
LINE_B = (52.8800, -4.4000)
START = (52.8802, -4.4050)

RADIUS_M = 50.0


def seq_of(*points, radius_m=RADIUS_M, neighbourhood_m=None):
    out = []
    for code, (lat, lon) in points:
        mark = {"code": code, "lat": lat, "lon": lon, "radius_m": radius_m}
        if neighbourhood_m is not None:
            mark["neighbourhood_m"] = neighbourhood_m
        out.append(mark)
    return out


COURSE = seq_of(("A", MARK_A), ("B", MARK_B), ("O", LINE_A))


def fix(t, point):
    return {"t": float(t), "lat": point[0], "lon": point[1],
            "speed_kn": 5.0, "course_deg": 0.0}


def track_of(*pairs):
    return [fix(t, p) for t, p in pairs]


def progress(fixes, seq=COURSE):
    return track.boat_course_progress(fixes, seq, LINE_A, LINE_B, None, RADIUS_M,
                                      not_before_ts=0.0, min_elapsed_s=0.0)


def metres(north, east, of=MARK_A):
    """A point offset from a mark, in metres."""
    import math
    return (of[0] + north / 111320.0,
            of[1] + east / (111320.0 * math.cos(math.radians(of[0]))))


def closest_approach(fixes, mark):
    return min(track.distance_to_segment_m(mark[0], mark[1],
                                           fixes[i]["lat"], fixes[i]["lon"],
                                           fixes[i + 1]["lat"], fixes[i + 1]["lon"])
               for i in range(len(fixes) - 1))


# ---------------------------------------------------------------------------
# The neighbourhood: how near counts as near
# ---------------------------------------------------------------------------

class TestTheNeighbourhood:
    def test_it_is_the_flat_allowance_on_ordinary_legs(self):
        """The club's median leg is 1320 m, so most marks get the full allowance."""
        assert track.mark_neighbourhood_m(50.0, 1320.0, 1500.0) == track.MARK_NEIGHBOURHOOD_M

    def test_a_short_leg_caps_it_at_half_the_leg(self):
        """A neighbourhood wider than the legs either side would be nonsense: the
        club's shortest leg is 390 m against that 1320 m median."""
        assert track.mark_neighbourhood_m(50.0, 390.0, 1500.0) == 195.0

    def test_the_shorter_of_the_two_legs_wins(self):
        assert track.mark_neighbourhood_m(50.0, 2000.0, 600.0) == 300.0

    def test_it_is_never_tighter_than_the_rounding_radius(self):
        """Or it would take away roundings the radius alone would have found."""
        assert track.mark_neighbourhood_m(200.0, 100.0, 100.0) == 200.0

    def test_missing_leg_lengths_fall_back_to_the_flat_allowance(self):
        assert track.mark_neighbourhood_m(50.0) == track.MARK_NEIGHBOURHOOD_M
        assert track.mark_neighbourhood_m(50.0, 0.0, 0.0) == track.MARK_NEIGHBOURHOOD_M

    def test_the_course_sequence_carries_one_per_mark(self, client):
        """Worked out once, where the legs are known, so the live walk and the
        replay's cannot disagree about it."""
        import app as ro
        from datetime import datetime, timedelta
        now = datetime.now()
        with ro.get_db() as db:
            race_id = int(db.execute(
                "INSERT INTO races (course_set, name, course_no, start_time, rating_rule, created_at)"
                " VALUES (1, 'Neighbourhood', 1, ?, 'DUAL', ?)",
                ((now - timedelta(hours=1)).isoformat(timespec="seconds"),
                 now.isoformat(timespec="seconds"))).lastrowid)
            db.commit()
        seq = track.course_rounding_sequence(ro.get_race(race_id))
        assert seq, "course 1 should resolve to a sequence"
        for mark in seq:
            assert mark["neighbourhood_m"] > 0
            assert mark["neighbourhood_m"] <= track.MARK_NEIGHBOURHOOD_M


# ---------------------------------------------------------------------------
# The wide rounding, which is the whole point
# ---------------------------------------------------------------------------

class TestAWideRoundingNowCounts:
    def _wide_by(self, offset_m):
        """A track that rounds A at ``offset_m`` and then rounds B properly."""
        return track_of(
            (30, START),
            (60, metres(-offset_m - 100, -offset_m * 0.25)),
            (90, metres(-offset_m * 0.7, offset_m * 0.8)),
            (120, metres(offset_m * 0.3, offset_m * 1.1)),
            (150, metres(offset_m + 100, offset_m * 0.55)),
            (180, MARK_B),
        )

    @pytest.mark.parametrize("offset", [100, 200, 300])
    def test_a_berth_the_radius_cannot_see_is_still_a_rounding(self, offset):
        fixes = self._wide_by(offset)
        assert closest_approach(fixes, MARK_A) > RADIUS_M, "fixture must clear the radius"
        out = progress(fixes)
        assert out["rounded"] >= 1, f"a {offset} m berth should still count"

    def test_beyond_the_neighbourhood_it_does_not(self):
        """The bound is deliberate: at some distance the boat is not rounding the
        mark at all, and the race officer's arrows exist for that."""
        fixes = self._wide_by(700)
        assert closest_approach(fixes, MARK_A) > track.MARK_NEIGHBOURHOOD_M
        assert progress(fixes)["rounded"] == 0

    def test_and_the_gps_finish_then_arrives(self):
        """The real payoff. A stalled boat can never satisfy "every earlier mark
        rounded", so its finish was missed however plainly it crossed the line."""
        fixes = self._wide_by(300) + track_of((210, (52.8806, -4.4050)),
                                             (240, (52.8794, -4.4050)))
        assert progress(fixes)["finished"] is True


class TestATightRoundingIsUnaffected:
    TIGHT = track_of((30, START), (60, metres(-300, 0)), (90, MARK_A),
                     (120, metres(300, -100)), (150, MARK_B))

    def test_it_still_counts(self):
        assert progress(self.TIGHT)["rounded"] >= 1

    def test_it_still_counts_at_the_same_moment(self):
        """The radius is tried first precisely so the common case gains no latency:
        the closest-approach test only fires once the boat has opened up 50 m, which
        measured 30-60 s later, and nobody wants that delay on a rounding the radius
        can already see."""
        seq = seq_of(("A", MARK_A), ("B", MARK_B), ("O", LINE_A))
        approach = track.MarkApproach()
        first_radius_hit = next(
            f["t"] for prev, f in zip([None] + self.TIGHT, self.TIGHT)
            if track._passed_mark(prev, f, seq[0], RADIUS_M))
        first_any_hit = next(
            f["t"] for prev, f in zip([None] + self.TIGHT, self.TIGHT)
            if track.rounded_mark(prev, f, seq, 0, RADIUS_M, approach))
        assert first_any_hit == first_radius_hit

    def test_the_radius_path_short_circuits(self):
        """A fix inside the radius returns without touching the closest-approach
        state at all — so the cheap test really is the one doing the work."""
        seq = seq_of(("A", MARK_A), ("B", MARK_B), ("O", LINE_A))
        approach = track.MarkApproach()
        assert track.rounded_mark(None, fix(90, MARK_A), seq, 0, RADIUS_M, approach) is True
        assert approach.min_d is None


# ---------------------------------------------------------------------------
# What stops it counting a mark the boat did not round
# ---------------------------------------------------------------------------

class TestItDoesNotInvent:
    def test_a_boat_that_never_comes_near_is_not_advanced(self):
        fixes = track_of((30, START), (60, metres(-2000, -2000)), (90, metres(0, -3000)),
                         (120, metres(2000, -2000)))
        assert progress(fixes)["rounded"] == 0

    def test_closest_approach_alone_is_not_enough(self):
        """It must have *left*. At the moment of closest approach the boat may still
        be sailing at the mark, and counting it there would read early."""
        approach = track.MarkApproach()
        approach.note(120.0, 900.0)
        assert approach.departed(120.0, 900.0, 400.0) is False

    def test_it_needs_the_next_mark_to_have_closed(self):
        """The confirming half: a boat merely passing a mark it is not rounding does
        not then set off towards the one after it. This is what made the measured
        specificity better than the radius test's, not worse."""
        approach = track.MarkApproach()
        approach.note(120.0, 900.0)          # closest approach, next mark 900 m off
        assert approach.departed(300.0, 950.0, 400.0) is False   # next mark further away
        assert approach.departed(300.0, 850.0, 400.0) is True    # next mark closer

    def test_a_tack_on_the_beat_is_not_a_departure(self):
        """Beating to a windward mark the distance oscillates. The departure margin
        is what stops a tack being read as leaving the mark."""
        approach = track.MarkApproach()
        approach.note(300.0, 900.0)
        opened = 300.0 + track.MARK_DEPART_M - 5.0
        assert approach.departed(opened, 880.0, 400.0) is False

    def test_the_running_minimum_only_ever_falls(self):
        approach = track.MarkApproach()
        approach.note(500.0, 900.0)
        approach.note(200.0, 800.0)
        approach.note(600.0, 700.0)
        assert approach.min_d == 200.0
        assert approach.next_at_min == 800.0

    def test_a_reset_forgets_the_previous_mark(self):
        """State is per-mark; carrying it across would compare a new mark's distances
        against the last one's closest approach."""
        approach = track.MarkApproach()
        approach.note(10.0, 900.0)
        approach.reset()
        assert approach.min_d is None
        assert approach.departed(500.0, 100.0, 400.0) is False


# ---------------------------------------------------------------------------
# The two walks must keep agreeing
# ---------------------------------------------------------------------------

class TestTheLiveWalkAndTheReplayAgree:
    """Two implementations of one rule, and the closest-approach state is exactly the
    kind of thing that makes them drift. Both drive the same MarkApproach."""

    @pytest.mark.parametrize("offset", [0, 150, 300, 700])
    def test_they_report_the_same_progress(self, offset):
        if offset == 0:
            fixes = track_of((30, START), (60, metres(-300, 0)), (90, MARK_A),
                             (120, metres(300, -100)), (150, MARK_B))
        else:
            fixes = TestAWideRoundingNowCounts()._wide_by(offset)
        live = progress(fixes)
        series = track.boat_progress_series(fixes, COURSE, LINE_A, LINE_B, None, RADIUS_M,
                                            [fixes[-1]["t"] + 60.0], not_before_ts=0.0,
                                            min_elapsed_s=0.0)
        assert series[-1]["rounded"] == live["rounded"]
        assert series[-1]["next_mark"] == live["next_mark"]
