"""Which slice of a recorded race gets replayed.

Reported from a real run: replaying a 55-minute race announced itself as

    3 boats, 4583 fixes, 293 min of racing at 1x = 293 min to watch

Because "everything since the gun" is not a race. Some entries carry a tracker
that lives on the committee boat and reports all season, and the demo trackers
kept running long after their finish — so most of what would have been replayed
was boats sitting by the line in the dark.

The app already works out this window for the replay viewer, ending a couple of
minutes after the last boat finished. Using the same helper means the clubhouse
display and the replay cannot disagree about where a race ends.
"""
from __future__ import annotations

import importlib.util
import pathlib

_ROOT = pathlib.Path(__file__).resolve().parent.parent


def _script():
    path = _ROOT / "scripts" / "rerun_race.py"
    spec = importlib.util.spec_from_file_location("rerun_race", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


rerun = _script()


class _Track:
    def __init__(self, window):
        self._window = window

    def race_track_window(self, race):
        return self._window


class TestTheWindowComesFromTheApp:
    GUN = 1_000_000.0

    def test_it_uses_the_apps_own_race_window(self):
        window = (self.GUN - 300, self.GUN + 3300)      # warning to just after the finish
        assert rerun.race_window(_Track(window), {"id": 1}, self.GUN) == window

    def test_a_race_the_app_cannot_place_falls_back_to_a_bounded_guess(self):
        got = rerun.race_window(_Track(None), {"id": 1}, self.GUN)
        assert got[0] < self.GUN < got[1]
        assert got[1] - got[0] <= rerun.MAX_RACE_S + 600

    def test_a_helper_that_raises_does_not_stop_the_replay(self):
        class _Broken:
            def race_track_window(self, race):
                raise RuntimeError("no entries")
        got = rerun.race_window(_Broken(), {"id": 1}, self.GUN)
        assert got[0] < self.GUN < got[1]

    def test_the_window_ends_well_before_the_trackers_do(self):
        """The point of the fix: a 55-minute race is 55 minutes to watch, not the
        five hours its trackers happened to keep reporting for."""
        race_end = self.GUN + 55 * 60
        window = rerun.race_window(_Track((self.GUN - 300, race_end)), {"id": 1}, self.GUN)
        assert window[1] - window[0] < 2 * 3600
