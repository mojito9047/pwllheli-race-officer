"""A finish line tolerates its seaward mark having moved.

An ISORA night race missed a finish. The Fairway Buoy (F) had drifted further
offshore, so the third boat sailed inside the physical mark but outside the
position the app held for it — off the end of the line segment. The direction
test was happy (it measures against the *infinite* line); only the segment bound
refused the crossing, so the finish was never seen.

The two ends are not the same kind of thing. The shore end is a surveyed transit
— a bridge window — and cannot move. The seaward end is a laid buoy that swings,
drags and gets re-laid. So only the seaward end is projected outward, by a margin
set per line in data/start_finish.json.

Worth recording what turned up while fixing it: the stored position for F was
112 m inshore of the position ISORA's own sailing instruction gives — 7% of a
1510 m line, before the buoy had moved at all. Correcting the mark is the first
fix; this stops a stale metre costing a finish.
"""
from __future__ import annotations

import math
import time

import pytest

import app as ro
from core import track
from core.timeutils import haversine_nm

STAMP = "2026-08-08T20:00:00"


def make_race(line_key):
    with ro.app.app_context():
        with ro.get_db() as db:
            cur = db.execute(
                "INSERT INTO races (name, course_no, start_time, created_at,"
                " finish_line_key) VALUES ('ISORA night', 1, ?, ?, ?)",
                (STAMP, STAMP, line_key))
            db.commit()
            return ro.get_race(int(cur.lastrowid))


class TestOnlyTheSeawardEndMoves:
    @pytest.mark.parametrize("key", ["psc", "isora_plas_heli"])
    def test_the_shore_end_is_left_alone(self, client, key):
        """It is a surveyed transit. Extending it would project the line inland."""
        race = make_race(key)
        true_pts = track.race_finish_line_points(race)
        detect = track.race_finish_line_for_detection(race)
        assert detect[1] == true_pts[1]

    @pytest.mark.parametrize("key", ["psc", "isora_plas_heli"])
    def test_the_seaward_end_moves_outward_by_the_configured_amount(self, client, key):
        race = make_race(key)
        true_pts = track.race_finish_line_points(race)
        detect = track.race_finish_line_for_detection(race)
        want = track.race_finish_line_extension_m(race)
        moved = haversine_nm(*true_pts[0], *detect[0]) * 1852
        assert abs(moved - want) < 2

    @pytest.mark.parametrize("key", ["psc", "isora_plas_heli"])
    def test_it_moves_away_from_the_line_not_across_it(self, client, key):
        """Outward. Extending the wrong way would shorten the line."""
        race = make_race(key)
        true_pts = track.race_finish_line_points(race)
        detect = track.race_finish_line_for_detection(race)
        was = haversine_nm(*true_pts[0], *true_pts[1]) * 1852
        now = haversine_nm(*detect[0], *detect[1]) * 1852
        assert now > was

    def test_the_extension_is_collinear(self, client):
        """This is what makes it safe: the infinite line is unchanged, so the
        finishing-direction test cannot be altered by it."""
        race = make_race("isora_plas_heli")
        (sa, so) = track.race_finish_line_points(race)
        (ea, _) = track.race_finish_line_for_detection(race)
        def bearing(p, q):
            dy = (q[0] - p[0]) * 111132.0
            dx = (q[1] - p[1]) * 111320.0 * math.cos(math.radians(p[0]))
            return math.degrees(math.atan2(dx, dy)) % 360
        assert abs(bearing(so, sa) - bearing(so, ea)) < 0.2


class TestTheConfiguredValues:
    def test_each_line_carries_its_own(self, client):
        assert track.finish_line_extension_m("psc") == 150.0
        assert track.finish_line_extension_m("isora_plas_heli") == 250.0

    def test_an_unknown_line_falls_back(self, client):
        assert track.finish_line_extension_m("nonsense") == track.DEFAULT_SEAWARD_EXTENSION_M

    def test_it_is_never_negative(self, client, monkeypatch):
        monkeypatch.setattr(track, "finish_line_by_key",
                            lambda k: {"seaward_extension_m": -500})
        assert track.finish_line_extension_m("psc") == 0.0

    def test_rubbish_falls_back_rather_than_raising(self, client, monkeypatch):
        monkeypatch.setattr(track, "finish_line_by_key",
                            lambda k: {"seaward_extension_m": "wide"})
        assert track.finish_line_extension_m("psc") == track.DEFAULT_SEAWARD_EXTENSION_M

    def test_zero_means_the_true_line(self, client, monkeypatch):
        race = make_race("psc")
        monkeypatch.setattr(track, "race_finish_line_extension_m", lambda r: 0.0)
        assert track.race_finish_line_for_detection(race) == track.race_finish_line_points(race)


class TestTheMissedFinish:
    """The reported case, end to end."""

    def crossing(self, race, past_the_end_m):
        """A boat crossing the line `past_the_end_m` beyond the seaward mark."""
        (sea, shore) = track.race_finish_line_points(race)
        m_lat, m_lon = 111132.0, 111320.0 * math.cos(math.radians(sea[0]))
        # Unit vector along the line, shore -> seaward, then out past the end.
        dx = (sea[1] - shore[1]) * m_lon
        dy = (sea[0] - shore[0]) * m_lat
        n = math.hypot(dx, dy)
        ux, uy = dx / n, dy / n
        pt = (sea[0] + uy * past_the_end_m / m_lat,
              sea[1] + ux * past_the_end_m / m_lon)
        # Cross it perpendicularly, a fix either side.
        px, py = -uy, ux
        before = (pt[0] - py * 60 / m_lat, pt[1] - px * 60 / m_lon)
        after = (pt[0] + py * 60 / m_lat, pt[1] + px * 60 / m_lon)
        now = time.time()
        return [{"t": now, "lat": before[0], "lon": before[1]},
                {"t": now + 10, "lat": after[0], "lon": after[1]}]

    def detect(self, race, fixes):
        line = track.race_finish_line_for_detection(race)
        # No course_ref: the direction test is exercised elsewhere, and either
        # side counts here so the test is about the segment bound alone.
        return track.detect_finish_crossing(fixes, line[0], line[1], None)

    def test_a_boat_just_past_the_mark_now_finishes(self, client):
        """The third boat: inside the buoy, outside where we thought it was."""
        race = make_race("isora_plas_heli")
        assert self.detect(race, self.crossing(race, 120)) is not None

    def test_it_would_not_have_before(self, client):
        """Against the true line, that same crossing is off the end."""
        race = make_race("isora_plas_heli")
        line = track.race_finish_line_points(race)
        fixes = self.crossing(race, 120)
        assert track.detect_finish_crossing(fixes, line[0], line[1], None) is None

    def test_well_past_the_extension_still_does_not_count(self, client):
        """A tolerance, not an open end."""
        race = make_race("isora_plas_heli")
        assert self.detect(race, self.crossing(race, 400)) is None

    def test_a_crossing_inside_the_line_is_unaffected(self, client):
        race = make_race("isora_plas_heli")
        assert self.detect(race, self.crossing(race, -200)) is not None


class TestOnlyDetectionUsesTheExtendedLine:
    """The chart must draw the line where it is, and distance-to-go must measure
    to the real mark. Two copies of one rule is this codebase's recurring bug, so
    the split between the true line and the detection line is pinned here."""

    def test_the_chart_line_is_the_true_one(self, client):
        import inspect
        src = inspect.getsource(track.race_chart_line)
        assert "race_finish_line_for_detection" not in src

    def test_the_rounding_sequence_ends_at_the_real_mark(self, client):
        import inspect
        src = inspect.getsource(track.course_rounding_sequence)
        assert "race_finish_line_for_detection" not in src, (
            "distance-to-go would measure to a point beyond the buoy"
        )

    def test_the_detection_paths_use_the_extended_line(self, client):
        import inspect
        for fn in (track.run_finish_detection, track.race_leaderboard,
                   track.race_leaderboard_series):
            assert "race_finish_line_for_detection" in inspect.getsource(fn), (
                f"{fn.__name__} still tests crossings against the un-extended line"
            )
