"""The clubhouse display has to know when a race is over.

It asked whether *every* boat had finished. A race with a retirement or a
non-starter never satisfies that: those boats never get a finish time, so the
clock counted up all afternoon and the bar screen kept showing a race that had
finished at lunchtime.

A race ends when nobody is still sailing it — which is not the same thing.
"""
from __future__ import annotations

import pytest

from core import bardisplay


def row(status, finished=False):
    return {"status": status, "finished": finished}


class TestWhoIsStillRacing:
    def test_a_boat_out_on_the_course_is(self):
        assert bardisplay.boat_still_racing(row("RACING")) is True

    def test_one_that_has_finished_is_not(self):
        assert bardisplay.boat_still_racing(row("FINISHED", finished=True)) is False

    @pytest.mark.parametrize("status", ["RET", "DNF", "DNS", "DNC", "OCS", "DSQ", "DNE", "DGM"])
    def test_a_boat_that_left_the_race_is_not(self, status):
        """Settled results, not boats on the water. This is the whole bug."""
        assert bardisplay.boat_still_racing(row(status)) is False

    def test_a_missing_status_is_treated_as_racing(self):
        """Safer to keep the display running than to end a race early."""
        assert bardisplay.boat_still_racing({"finished": False}) is True

    def test_case_and_spacing_do_not_matter(self):
        assert bardisplay.boat_still_racing(row(" ret ")) is False
        assert bardisplay.boat_still_racing(row("racing")) is True


class TestWhenTheRaceIsOver:
    def test_not_while_one_boat_is_still_out(self):
        assert bardisplay.race_is_over([row("FINISHED", True), row("RACING")]) is False

    def test_yes_once_they_are_all_in(self):
        assert bardisplay.race_is_over([row("FINISHED", True), row("FINISHED", True)]) is True

    def test_a_retirement_does_not_hold_the_race_open(self):
        """The reported case: everyone else is in, one boat retired, and the bar
        display carried on as though the race were still being sailed."""
        assert bardisplay.race_is_over([row("FINISHED", True), row("RET")]) is True

    def test_nor_a_boat_that_never_started(self):
        assert bardisplay.race_is_over([row("FINISHED", True), row("DNC")]) is True

    def test_a_race_nobody_sailed_is_over(self):
        assert bardisplay.race_is_over([row("DNC"), row("DNS")]) is True

    def test_a_race_with_no_entries_is_not_called_finished(self):
        """Nothing has happened yet; the display should not show it as over."""
        assert bardisplay.race_is_over([]) is False
        assert bardisplay.race_is_over(None) is False
