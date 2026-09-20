"""After a boat finishes, the film goes back to the fleet.

It used to cut to the finish line as the leader came in and stay there to the
end. A club fleet finishes over twenty minutes or more, so once the leader was
across there was nothing on screen but an empty line while the boats still
racing were somewhere else entirely -- measured on the club's own races, twenty
seconds of it in race 87 and forty-three in race 83.

Now each boat gets the line for its run-in and crossing, and the fleet has the
screen in between. Two rules decide the cuts, and both are here:

* the wait is measured in **film** seconds, not race seconds, because the film
  runs at 30x between finishes and at real time around them; and
* the last boat holds the line to the end, because that is where the film stops.

Tested against a real ``TimeWarp`` rather than a stand-in clock: the whole point
of the first rule is that the warp is not linear, and a fake ``frame_of`` that
was would make the test agree with anything.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "replay3d"))

from replay_time import (  # noqa: E402
    FINISH_CUT_IN,
    FINISH_CUT_OUT,
    MIN_AWAY_FILM_S,
    TimeWarp,
    film_seconds,
    finish_shot_times,
    slow_windows_for,
)

DURATION = 5305.0           # race 87, 19 September 2026
FIRST_START = 300.0


def _warp(finishes):
    """The film clock a race with these finishes would really be cut to."""
    data = {"time": {"duration_s": DURATION, "first_start_rel": FIRST_START},
            "boats": [{"finish_t": t} for t in finishes]}
    return TimeWarp(DURATION, slow_windows_for(data), speed=30.0)


def _plan(finishes):
    return finish_shot_times(finishes, _warp(finishes).frame_of)


class TestTheLineIsNotHeldOnAnEmptyFinish:
    """Race 87's real finishes: 4156, 4870, 5185."""

    REAL = [4156.0, 4870.0, 5185.0]

    def test_the_fleet_gets_the_screen_between_the_first_two(self):
        names = [name for name, _t in _plan(self.REAL)]
        assert names[:3] == ["finish", "overview", "finish"], names

    def test_it_goes_back_to_the_line_before_the_next_boat_arrives(self):
        plan = _plan(self.REAL)
        back = [t for name, t in plan if name == "finish"][1]
        assert back == self.REAL[1] - FINISH_CUT_IN
        assert back < self.REAL[1], "the cut back happens after the boat has crossed"

    def test_it_leaves_the_line_once_the_boat_is_across(self):
        away = [t for name, t in _plan(self.REAL) if name == "overview"][0]
        assert away == self.REAL[0] + FINISH_CUT_OUT
        assert away > self.REAL[0]

    def test_the_last_boat_holds_the_line_to_the_end(self):
        """The film stops there, and the results card is drawn over it."""
        assert _plan(self.REAL)[-1][0] == "finish"

    def test_every_boat_gets_a_run_in(self):
        starts = [t for name, t in _plan(self.REAL) if name == "finish"]
        assert starts == [t - FINISH_CUT_IN for t in self.REAL]


class TestWhenItIsNotWorthLeaving:
    def test_two_boats_seconds_apart_do_not_get_a_cut_away(self):
        """Away and back again for a second of film is worse than holding."""
        close = [4156.0, 4174.0]
        assert [name for name, _t in _plan(close)] == ["finish", "finish"]

    def test_the_gap_is_judged_on_screen_not_on_the_water(self):
        """The rule this exists for.

        Both pairs are the same number of race seconds apart. One straddles a
        stretch the film runs at 30x, the other sits inside the real-time window
        around a finish, so on screen one is a moment and the other is not.
        """
        warp = _warp([1000.0, 4156.0])
        span = 60.0
        inside = film_seconds(warp.frame_of, 1000.0 - span / 2, 1000.0 + span / 2)
        outside = film_seconds(warp.frame_of, 2000.0, 2000.0 + span)
        assert inside > outside * 10, "the clock is linear here; this test proves nothing"
        assert outside < MIN_AWAY_FILM_S <= inside, (
            f"{span:.0f} race seconds is {inside:.1f} s of film at a finish and "
            f"{outside:.1f} s on a beat; the threshold has to be read in film seconds")

    def test_a_single_finisher_is_just_the_line(self):
        assert _plan([4156.0]) == [("finish", 4156.0 - FINISH_CUT_IN)]

    def test_finishes_out_of_order_are_still_planned_in_order(self):
        """The scene lists boats in export order, not finishing order."""
        times = [t for _name, t in _plan([5185.0, 4156.0, 4870.0])]
        assert times == sorted(times)


class TestFilmSeconds:
    def test_a_stretch_at_thirty_times_is_a_thirtieth_as_long(self):
        warp = _warp([4156.0])
        # Well clear of the start and the finish, so it is flat 30x.
        assert film_seconds(warp.frame_of, 1500.0, 2100.0) == pytest.approx(600.0 / 30.0, abs=0.5)

    def test_real_time_around_a_finish_is_real_time(self):
        warp = _warp([4156.0])
        assert film_seconds(warp.frame_of, 4146.0, 4166.0) == pytest.approx(20.0, abs=1.0)

    def test_backwards_is_nothing_rather_than_negative(self):
        """Two finishes close enough that the windows overlap can invert these."""
        warp = _warp([4156.0])
        assert film_seconds(warp.frame_of, 2100.0, 1500.0) == 0.0
