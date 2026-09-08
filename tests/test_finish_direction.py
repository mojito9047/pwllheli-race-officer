"""Which way across the line counts as finishing.

A finish is a crossing that **leaves** the course, so something has to say which way
that is. Until v0.248 it was the centroid of the course marks, and that is the wrong
point: ``_side`` measures against the *infinite* line through the two ends, so a course
with marks on both sides of that extension has a centroid that can land on the far side
from where the boats actually come in.

The direction test is then exactly backwards. The real finish reads as *entering* the
course and is thrown away, and the crossing that gets recorded is the boat coming back
across the line on its way home. Found by a race officer walking a simulated race
through the replay: the recorded finish was 13:29:09 and the boat had plainly crossed
at 13:28:34, from the direction of the last mark.

It was not one odd course. **8 of the club's 12 courses** have marks that straddle the
line that way — course 1 included — and every tracked boat-race in the database had
finished 25 to 46 seconds late because of it. It went unnoticed because those finishes
were *recorded by GPS auto-confirm*, so the stored time and the detected time were the
same number: nothing to disagree with.

The last mark rounded is the point that actually defines the direction, and it is what
the racing rules mean by the "course side" a boat must cross from.
"""
from __future__ import annotations

import pytest

from core import track


# Modelled on the club's real line and on course 53, whose marks straddle it. Every
# side and intersection below is asserted in TestTheGeometryOfTheseFixtures, because a
# fixture that quietly stops straddling would leave these tests passing and testing
# nothing.
LINE_SEAWARD = (52.8791166667, -4.3993333333)        # O, the ODM
LINE_SHORE = (52.8820633333, -4.4010150000)
MARK_W = (52.8733333333, -4.4263333333)              # like mark 2 — west of the line
MARK_E = (52.8758333333, -4.3858333333)              # like mark 6 — east of it
EAST_OF_LINE = (52.8805, -4.3985)
WEST_OF_LINE = (52.8805, -4.4016)
FURTHER_WEST = (52.8807, -4.4030)

RADIUS_M = 50.0
CENTROID = ((MARK_W[0] + MARK_E[0]) / 2.0, (MARK_W[1] + MARK_E[1]) / 2.0)


def mark(code, point):
    return {"code": code, "lat": point[0], "lon": point[1], "radius_m": RADIUS_M}


# W, then E, then the line: the shape of course 53's lap.
STRADDLING = [mark("W", MARK_W), mark("E", MARK_E), mark("O", LINE_SEAWARD)]


def fix(t, point):
    return {"t": float(t), "lat": point[0], "lon": point[1],
            "speed_kn": 5.0, "course_deg": 0.0}


def side_of(point):
    lat0, lon0 = LINE_SEAWARD
    a = track._project(LINE_SEAWARD[0], LINE_SEAWARD[1], lat0, lon0)
    b = track._project(LINE_SHORE[0], LINE_SHORE[1], lat0, lon0)
    p = track._project(point[0], point[1], lat0, lon0)
    return track._side(a[0], a[1], b[0], b[1], p[0], p[1])


def progress(fixes, seq=STRADDLING, course_ref=CENTROID):
    return track.boat_course_progress(fixes, seq, LINE_SEAWARD, LINE_SHORE, course_ref,
                                      RADIUS_M, not_before_ts=0.0, min_elapsed_s=0.0)


# The race: round W, round E, come back and cross the line westward (the finish),
# then turn round and cross back eastward on the way home. The finish is *interpolated*
# between the two fixes either side of the line, so it lands inside this leg rather
# than on a fix — which is the point of interpolating it.
INBOUND_LEG = (240.0, 300.0)
HOME_T = 420.0
SAILED = [
    fix(0, WEST_OF_LINE),
    fix(60, MARK_W),                 # rounds the western mark
    fix(180, MARK_E),                # rounds the eastern mark — the last one
    fix(240, EAST_OF_LINE),          # coming back in from the east
    fix(300, WEST_OF_LINE),          # now west of it: the finish is in between
    fix(360, FURTHER_WEST),
    fix(HOME_T, EAST_OF_LINE),       # crosses back, heading for the marina
]


class TestTheGeometryOfTheseFixtures:
    """If these stop holding, every test below passes while testing nothing."""

    def test_the_two_marks_straddle_the_line(self):
        assert (side_of(MARK_W) > 0) != (side_of(MARK_E) > 0)

    def test_the_centroid_lands_on_the_wrong_side(self):
        """The whole bug: the boats come in from E, and the centroid is over with W."""
        assert (side_of(CENTROID) > 0) == (side_of(MARK_W) > 0)
        assert (side_of(CENTROID) > 0) != (side_of(MARK_E) > 0)

    def test_the_crossing_points_are_either_side(self):
        assert (side_of(EAST_OF_LINE) > 0) != (side_of(WEST_OF_LINE) > 0)

    def test_and_the_crossing_lands_within_the_line_itself(self):
        """Not past the ODM: a crossing outside the line's ends is not a finish."""
        lat0, lon0 = LINE_SEAWARD
        a = track._project(LINE_SEAWARD[0], LINE_SEAWARD[1], lat0, lon0)
        b = track._project(LINE_SHORE[0], LINE_SHORE[1], lat0, lon0)
        p1 = track._project(EAST_OF_LINE[0], EAST_OF_LINE[1], lat0, lon0)
        p2 = track._project(WEST_OF_LINE[0], WEST_OF_LINE[1], lat0, lon0)
        f = track.segment_intersection_fraction(p1, p2, a, b)
        assert f is not None and 0.0 < f < 1.0


class TestTheFinishIsTheFirstCrossingFromTheLastMark:
    def test_it_finishes_on_the_way_in_not_on_the_way_home(self):
        out = progress(SAILED)
        assert out["finished"] is True
        assert INBOUND_LEG[0] < out["finish_time"] < INBOUND_LEG[1], (
            "the crossing on the way home was recorded instead of the finish")

    def test_the_boat_is_not_left_unfinished(self):
        assert progress(SAILED)["rounded"] == len(STRADDLING)

    def test_a_boat_that_never_crosses_back_still_finishes(self):
        """The case that hid the bug on the water: the club's own line is crossed
        again on the way in, so a wrong direction merely reads late. A boat that
        anchors off after finishing would never have been finished at all."""
        out = progress(SAILED[:5])
        assert out["finished"] is True
        assert INBOUND_LEG[0] < out["finish_time"] < INBOUND_LEG[1]

    def test_crossing_the_other_way_is_not_a_finish(self):
        """Tested on the crossing itself rather than through the walk, because a
        track cannot reach the west side after rounding E *without* crossing
        outbound on the way — the first draft of this test did exactly that and
        proved the opposite of what it claimed."""
        outbound = [fix(0, EAST_OF_LINE), fix(60, WEST_OF_LINE)]
        inbound = [fix(0, WEST_OF_LINE), fix(60, EAST_OF_LINE)]
        assert track.detect_finish_crossing(outbound, LINE_SEAWARD, LINE_SHORE,
                                            MARK_E, 0.0, 0.0) is not None
        assert track.detect_finish_crossing(inbound, LINE_SEAWARD, LINE_SHORE,
                                            MARK_E, 0.0, 0.0) is None

    def test_and_the_centroid_would_have_got_both_backwards(self):
        """The old reference, on the same two legs: the finish rejected, the run
        home accepted."""
        outbound = [fix(0, EAST_OF_LINE), fix(60, WEST_OF_LINE)]
        inbound = [fix(0, WEST_OF_LINE), fix(60, EAST_OF_LINE)]
        assert track.detect_finish_crossing(outbound, LINE_SEAWARD, LINE_SHORE,
                                            CENTROID, 0.0, 0.0) is None
        assert track.detect_finish_crossing(inbound, LINE_SEAWARD, LINE_SHORE,
                                            CENTROID, 0.0, 0.0) is not None


class TestTheDirectionPoint:
    def test_it_is_the_last_mark_rounded(self):
        ref = track.finish_direction_point(STRADDLING, LINE_SEAWARD, LINE_SHORE, CENTROID)
        assert ref == (MARK_E[0], MARK_E[1])

    def test_not_the_centroid_it_is_given(self):
        ref = track.finish_direction_point(STRADDLING, LINE_SEAWARD, LINE_SHORE, CENTROID)
        assert ref != CENTROID

    def test_it_falls_back_when_there_is_no_previous_mark(self):
        """A sequence that is only the line: nothing to take a direction from."""
        only_line = [mark("O", LINE_SEAWARD)]
        assert track.finish_direction_point(only_line, LINE_SEAWARD, LINE_SHORE,
                                            CENTROID) == CENTROID

    def test_it_falls_back_when_the_last_mark_sits_on_the_line(self):
        """A mark exactly on the line gives no side, so it cannot say which way."""
        on_line = [mark("X", LINE_SHORE), mark("O", LINE_SEAWARD)]
        assert track.finish_direction_point(on_line, LINE_SEAWARD, LINE_SHORE,
                                            CENTROID) == CENTROID

    def test_it_tolerates_an_empty_sequence(self):
        assert track.finish_direction_point([], LINE_SEAWARD, LINE_SHORE, CENTROID) == CENTROID
        assert track.finish_direction_point([], LINE_SEAWARD, LINE_SHORE) is None

    def test_a_shortened_course_uses_its_shorten_mark(self):
        """course_rounding_sequence truncates the marks and appends the line, so
        seq[-2] is the mark boats were sent round before finishing."""
        shortened = [mark("W", MARK_W), mark("O", LINE_SEAWARD)]
        ref = track.finish_direction_point(shortened, LINE_SEAWARD, LINE_SHORE, CENTROID)
        assert ref == (MARK_W[0], MARK_W[1])


class TestACourseThatDoesNotStraddleIsUnchanged:
    """The four club courses whose marks all sit one side of the line must behave
    exactly as before, or this fix has broken more than it mended."""

    WEST_ONLY = [mark("W", MARK_W), mark("W2", (52.8745, -4.4200)),
                 mark("O", LINE_SEAWARD)]

    def test_the_marks_really_are_all_one_side(self):
        for m in self.WEST_ONLY[:2]:
            assert (side_of((m["lat"], m["lon"])) > 0) == (side_of(MARK_W) > 0)

    def test_the_direction_agrees_with_the_centroid(self):
        centroid = ((MARK_W[0] + 52.8745) / 2.0, (MARK_W[1] + -4.4200) / 2.0)
        ref = track.finish_direction_point(self.WEST_ONLY, LINE_SEAWARD, LINE_SHORE, centroid)
        assert (side_of(ref) > 0) == (side_of(centroid) > 0)

    def test_a_boat_finishing_outbound_still_finishes(self):
        sailed = [fix(0, EAST_OF_LINE), fix(60, MARK_W),
                  fix(120, (52.8745, -4.4200)),
                  fix(180, WEST_OF_LINE), fix(240, EAST_OF_LINE)]
        out = progress(sailed, seq=self.WEST_ONLY,
                       course_ref=((MARK_W[0] + 52.8745) / 2.0, (MARK_W[1] + -4.4200) / 2.0))
        assert out["finished"] is True
        assert out["finish_time"] == pytest.approx(240.0, abs=60.0)


class TestTheReplayAgrees:
    def test_the_series_walk_finds_the_same_finish(self):
        """Two implementations of one rule. A replay that disagreed with the live view
        would show a different race from the one that was sailed."""
        live = progress(SAILED)
        series = track.boat_progress_series(SAILED, STRADDLING, LINE_SEAWARD, LINE_SHORE,
                                            CENTROID, RADIUS_M, [HOME_T + 60],
                                            not_before_ts=0.0, min_elapsed_s=0.0)
        assert series[-1]["finished"] is live["finished"]
        assert series[-1]["finish_time"] == pytest.approx(live["finish_time"], abs=1.0)


class TestTheRealCourses:
    def test_every_bundled_course_can_state_a_finishing_direction(self, client):
        """Either from its last mark or from the centroid — never nothing, which
        would silently accept a crossing in either direction."""
        import app as ro
        from datetime import datetime, timedelta
        now = datetime.now()
        checked = 0
        for course in ro.appstate.COURSES:
            course_no = int(course["course_no"])
            with ro.get_db() as db:
                race_id = int(db.execute(
                    "INSERT INTO races (course_set, name, course_no, start_time, rating_rule, created_at)"
                    " VALUES (1, ?, ?, ?, 'DUAL', ?)",
                    (f"Direction {course_no}", course_no,
                     (now - timedelta(hours=1)).isoformat(timespec="seconds"),
                     now.isoformat(timespec="seconds"))).lastrowid)
                db.commit()
            race = ro.get_race(race_id)
            seq = track.course_rounding_sequence(race)
            line = track.race_finish_line_points(race)
            if len(seq) < 2 or line is None:
                continue
            ref = track.finish_direction_point(seq, line[0], line[1],
                                               track._course_ref_point(race))
            assert ref is not None, f"course {course_no} has no finishing direction"
            checked += 1
        assert checked >= 10, f"only {checked} courses checked"


class TestTheDiagnostic:
    """The bug was not that the direction test existed — it was that it failed
    *silently*, on every race, for as long as nobody walked a replay by eye.

    So the walk now reports a crossing that happened with every mark rounded and was
    refused by the direction test alone. That is the exact signature: the real finish
    discarded, a later one recorded. Measured on the club's data the direction test
    changes no answer at all, so anything it does reject is worth a second look.
    """

    def test_a_wrong_direction_is_reported(self):
        """Driven with the old centroid reference: the finish is refused, the run
        home is taken, and the earlier crossing is handed back as the warning."""
        out = progress(SAILED, course_ref=CENTROID, seq=[
            mark("W", MARK_W), mark("E", MARK_E), mark("O", LINE_SEAWARD)])
        # Sanity: with the shipped rule this track finishes on the way in.
        assert out["finished"] is True
        assert out["direction_skipped_time"] is None, (
            "nothing should be skipped when the direction is right")

    def test_it_fires_when_the_direction_really_is_backwards(self):
        """The straddling course with the reference forced to the *wrong* side, which
        is exactly what the centroid did before v0.248."""
        wrong_way = [mark("W", MARK_W), mark("O", LINE_SEAWARD)]   # last mark = W, west
        out = track.boat_course_progress(SAILED, wrong_way, LINE_SEAWARD, LINE_SHORE,
                                         None, RADIUS_M, not_before_ts=0.0, min_elapsed_s=0.0)
        assert out["finished"] is True
        # W is west, so "leaving" is west->east: the inbound crossing is refused and
        # the run home is taken instead — the original bug, reproduced.
        assert out["finish_time"] > INBOUND_LEG[1]
        assert out["direction_skipped_time"] is not None, (
            "the refused crossing should have been reported")
        assert INBOUND_LEG[0] < out["direction_skipped_time"] < INBOUND_LEG[1]

    def test_the_gap_it_reports_is_the_error_it_would_have_flagged(self):
        wrong_way = [mark("W", MARK_W), mark("O", LINE_SEAWARD)]
        out = track.boat_course_progress(SAILED, wrong_way, LINE_SEAWARD, LINE_SHORE,
                                         None, RADIUS_M, not_before_ts=0.0, min_elapsed_s=0.0)
        gap = out["finish_time"] - out["direction_skipped_time"]
        assert gap > 60.0, "the whole point is that the recorded finish is late"

    def test_the_key_is_always_present(self):
        """Callers read it unconditionally, including for a boat with no fixes."""
        for out in (progress(SAILED), progress([]), progress(SAILED[:2])):
            assert "direction_skipped_time" in out

    def test_nothing_is_reported_when_the_boat_never_finishes(self):
        """No crossing at all is a different problem, and not this warning's job."""
        no_crossing = [fix(0, WEST_OF_LINE), fix(60, MARK_W), fix(180, MARK_E)]
        out = progress(no_crossing)
        assert out["finished"] is False
        assert out["direction_skipped_time"] is None


class TestTheDiagnosticReachesTheLog:
    def test_recording_a_finish_writes_the_warning(self, client, monkeypatch):
        """Logged where the finish is recorded, not in the walk: the walk runs on
        every poll for every boat and would flood the log."""
        written = []
        monkeypatch.setattr(track, "log_activity",
                            lambda action, details="", user="system": written.append((action, details)))
        monkeypatch.setattr(track, "run_finish_detection", track.run_finish_detection)

        # Stand in a progress result that carries the diagnostic.
        real = track.boat_course_progress

        def fake(*a, **kw):
            out = dict(real(*a, **kw))
            if out.get("finish_time"):
                out["direction_skipped_time"] = out["finish_time"] - 34.0
            return out

        monkeypatch.setattr(track, "boat_course_progress", fake)
        import tests.test_track as tt
        boat_id = tt._make_boat()
        track.upsert_tracker("IMEI-DIAG", boat_id=boat_id)
        race_id = tt._make_race(gps_finish=1, gps_auto=1)
        tt._make_entry(race_id, boat_id)
        tt._seed_full_course_track(race_id, "IMEI-DIAG")
        assert track.run_finish_detection() >= 1

        warnings = [d for a, d in written if "direction skipped" in a]
        assert warnings, f"no warning logged; got {[a for a, _ in written]}"
        assert "34s later" in warnings[0]
        assert "video" in warnings[0]
