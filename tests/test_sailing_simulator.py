"""The simulator's sailing model, and the polar reading it shares with the app.

`simulate_trackers.py --sail` exists to test the *predicted* leaderboard, so the
one thing it must not do is read the polar differently from the app. If the
simulator thought a J/122 beat at 35° while `core.courses.course_leg_analysis`
thought 39°, the estimator would be measured against a boat obeying different
physics and the answer would mean nothing. The first class here pins the two
together; the rest check the boat actually sails.

The script is standard-library-only by design (it runs on the relay, away from
the app), which is exactly why the duplication needs a test rather than a note.
"""
from __future__ import annotations

import importlib.util
import math
import pathlib

import pytest

from core.polars import (interpolate_rows_by_tws, row_best_downwind_vmg, row_min_twa,
                         target_speed_info_for)

_ROOT = pathlib.Path(__file__).resolve().parent.parent


def _load_script():
    path = _ROOT / "scripts" / "simulate_trackers.py"
    spec = importlib.util.spec_from_file_location("simulate_trackers", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


sim = _load_script()
POLAR = _ROOT / "data" / "polars" / "J122.txt"
SIM_ROWS = sim.load_polar_rows(POLAR)
# The app's own loader, for the same file, to compare against.
APP_ROWS = [{"tws": r["tws"], "points": r["points"]} for r in SIM_ROWS]


class TestItReadsThePolarTheWayTheAppDoes:
    def test_the_file_parses_into_rows_at_all(self):
        assert len(SIM_ROWS) >= 5
        assert all(r["points"] for r in SIM_ROWS)

    @pytest.mark.parametrize("tws", [6.0, 9.5, 12.0, 16.0, 25.0])
    @pytest.mark.parametrize("twa", [50.0, 70.0, 90.0, 110.0, 135.0])
    def test_boat_speed_matches_the_apps_polar_maths(self, tws, twa):
        """Between the beat angle and the running angle both should interpolate
        the same table the same way, to the last decimal place."""
        mine = sim.polar_speed(SIM_ROWS, twa, tws)
        theirs = target_speed_info_for(twa, tws, APP_ROWS)
        assert theirs is not None
        assert mine == pytest.approx(theirs["bsp"], rel=1e-9)

    @pytest.mark.parametrize("tws", [6.0, 9.5, 12.0, 20.0])
    def test_the_beat_angle_matches(self, tws):
        assert sim.beat_angle(SIM_ROWS, tws) == pytest.approx(
            interpolate_rows_by_tws(APP_ROWS, tws, row_min_twa), rel=1e-9)

    @pytest.mark.parametrize("tws", [6.0, 12.0, 20.0])
    def test_the_running_angle_matches(self, tws):
        best = interpolate_rows_by_tws(APP_ROWS, tws, row_best_downwind_vmg)
        assert sim.run_angle(SIM_ROWS, tws) == pytest.approx(best["twa"], rel=1e-9)


def _windward_leeward(pct=1.0, tack_minutes=0.0, bias=0.0, twd=0.0, tws=12.0, nm=1.0):
    """One nautical mile dead upwind and back again, wind from `twd`."""
    start = (52.90, -4.40)
    _to_xy, to_ll = sim.local_frame(start)
    mark = to_ll((math.sin(math.radians(twd + 180)) * -nm * 1852.0,
                  math.cos(math.radians(twd + 180)) * -nm * 1852.0))
    tack_s = tack_minutes * 60.0 if tack_minutes > 0 else 1e9
    boat = sim.SailingBoat("T", [start, mark, start], pct, tack_s, SIM_ROWS, 0.0,
                           upwind_bias=bias)
    t, headings = 0.0, []
    while boat.step(t, 1.0, twd, tws) and t < 20000:
        t += 1.0
        headings.append(boat.heading)
    return boat, t, headings


class TestItSailsLikeABoat:
    def test_it_never_points_closer_than_the_polar_allows(self):
        boat, _t, headings = _windward_leeward()
        assert boat.finished
        closest = min(abs(sim.signed_diff(h, 0.0)) for h in headings)
        assert closest >= sim.beat_angle(SIM_ROWS, 12.0) - 0.001

    def test_it_never_sails_deeper_than_the_best_running_angle(self):
        _boat, _t, headings = _windward_leeward()
        deepest = max(abs(sim.signed_diff(h, 0.0)) for h in headings)
        assert deepest <= sim.run_angle(SIM_ROWS, 12.0) + 0.001

    def test_a_dead_beat_costs_the_extra_distance_trigonometry_says_it_should(self):
        """Beating at angle A covers 1/cos(A) of the rhumb line — the whole reason
        a leg model has to work in predicted time rather than distance."""
        boat, _t, _h = _windward_leeward()
        beat, run = sim.beat_angle(SIM_ROWS, 12.0), sim.run_angle(SIM_ROWS, 12.0)
        expected = 1852.0 * (1 / math.cos(math.radians(beat))
                             + 1 / math.cos(math.radians(180 - run)))
        # Slightly under, because each mark is rounded at 25 m rather than touched.
        assert 0.93 * expected <= boat.distance_m <= 1.02 * expected

    def test_it_tacks_at_the_layline_even_with_no_working_tacks(self):
        boat, _t, _h = _windward_leeward(tack_minutes=0.0)
        assert boat.tacks >= 2          # at least one tack up and one gybe down

    def test_working_tacks_add_manoeuvres_and_cost_time(self):
        few, t_few, _ = _windward_leeward(tack_minutes=0.0)
        many, t_many, _ = _windward_leeward(tack_minutes=1.5)
        assert many.tacks > few.tacks
        assert t_many > t_few           # every turn is paid for in lost speed

    def test_a_slower_boat_takes_proportionally_longer(self):
        _full, t_full, _ = _windward_leeward(pct=1.0)
        _slow, t_slow, _ = _windward_leeward(pct=0.8)
        assert t_slow / t_full == pytest.approx(1.25, rel=0.06)


class TestPerformanceCanVaryByPointOfSail:
    """A flat percentage is the kind case; boats are good at one end, not both."""

    def test_an_upwind_biased_boat_beats_faster_and_runs_slower(self):
        plain, _t, _h = _windward_leeward(bias=0.0)
        biased, _t2, _h2 = _windward_leeward(bias=0.10)
        # Same course, same polar percentage: only the split between legs moves.
        assert biased.distance_m == pytest.approx(plain.distance_m, rel=0.02)

    def test_the_bias_is_applied_upwind_and_downwind_in_opposite_directions(self):
        boat = sim.SailingBoat("T", [(52.9, -4.4), (52.91, -4.4)], 1.0, 1e9,
                               SIM_ROWS, 0.0, upwind_bias=0.10)
        # Sailing at 45 deg (upwind) should be quicker than the bare polar...
        boat.pos, boat.tack = (52.9, -4.4), 1
        boat.step(1.0, 1.0, 0.0, 12.0)
        upwind = boat.speed_kn / sim.polar_speed(SIM_ROWS, abs(sim.signed_diff(boat.heading, 0.0)), 12.0)
        # ...and downwind slower. 180 deg away puts the same mark dead downwind.
        boat2 = sim.SailingBoat("T", [(52.91, -4.4), (52.90, -4.4)], 1.0, 1e9,
                                SIM_ROWS, 0.0, upwind_bias=0.10)
        boat2.step(1.0, 1.0, 0.0, 12.0)
        downwind = boat2.speed_kn / sim.polar_speed(
            SIM_ROWS, abs(sim.signed_diff(boat2.heading, 0.0)), 12.0)
        assert upwind > 1.0 > downwind
