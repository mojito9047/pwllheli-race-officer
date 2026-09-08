"""Correcting the mark a boat is sailing to.

The walk through the course is **sequential**, so a mark the app cannot see rounded
costs more than a wrong number on a screen:

* the boat shows several marks behind where it actually is, for the rest of the race;
* and because a finish is only looked for once every earlier mark is rounded, its
  GPS finish never arrives however plainly it crosses the line.

Both are fixed by one press of an arrow. The correction is deliberately **not**
retroactive, and the reason is specific: the club's finish-line mark (O) is commonly
a mid-course rounding mark too, so a boat nudged forward to the finish could
otherwise be finished by a line crossing it made on an earlier lap.

**These fixtures had to be made much wider in v0.247.** Rounding is no longer decided
by proximity alone — a boat that came within the mark's neighbourhood and has since
left towards the next mark counts as having rounded it (see test_mark_rounding.py),
which catches the 300 m berth these tests originally used. The arrows are for what is
left: a rounding wider than the neighbourhood, a mark that has dragged out of reach
altogether, or a tracker that missed the whole rounding. So the wide track here now
passes 618 m clear, beyond the 400 m neighbourhood, and the geometry class below
asserts that rather than trusting it.
"""
from __future__ import annotations

import pytest

import app as ro
from core import track


# A tiny course: two rounding marks then the line, at Pwllheli latitudes so the
# projection helpers behave as they do in service. A is deliberately **off** the
# route from B back to the line — on an out-and-back course the first mark sits on
# the way home, so a track routed wide of it on the way out comes back within the
# neighbourhood on the finishing run and rounds it after all. That cost an hour.
#
# Nor are these tracks eyeballed. _passed_mark tests the *leg* between fixes, so a
# plausible-looking "wide" track sweeps inside the radius anyway and the boat rounds
# regardless. Every closest approach was computed with distance_to_segment_m and is
# asserted below.
MARK_A = (52.8900, -4.3800)
MARK_B = (52.8980, -4.3950)          # 1343 m from A
LINE_A = (52.8800, -4.4100)
LINE_B = (52.8800, -4.4000)          # the line runs along 52.8800
START = (52.8802, -4.4050)           # on the line, where a race begins

# 618 m clear of A the whole way past: wider than the mark's neighbourhood, so
# neither the radius nor the closest-approach test counts it.
WIDE_1 = (52.883712, -4.382233)
WIDE_2 = (52.885508, -4.372556)
WIDE_3 = (52.891797, -4.369578)
WIDE_4 = (52.896288, -4.374789)
# Straight over A, for the tests that need a rounding actually detected.
A_APPROACH = (52.887305, -4.381489)
A_DEPART = (52.892695, -4.382978)
# Either side of the line, on the finishing run.
BEFORE_LINE = (52.8806, -4.4050)
OVER_LINE = (52.8794, -4.4050)

ROUNDING_RADIUS_M = 50.0


def seq_of(*points, radius_m=ROUNDING_RADIUS_M):
    return [{"code": code, "lat": lat, "lon": lon, "radius_m": radius_m}
            for code, (lat, lon) in points]


COURSE = seq_of(("A", MARK_A), ("B", MARK_B), ("O", LINE_A))


def fix(t, point):
    return {"t": float(t), "lat": point[0], "lon": point[1],
            "speed_kn": 5.0, "course_deg": 0.0}


def track_of(*pairs):
    return [fix(t, point) for t, point in pairs]


def progress(fixes, override=None, seq=COURSE):
    return track.boat_course_progress(fixes, seq, LINE_A, LINE_B, None, ROUNDING_RADIUS_M,
                                      not_before_ts=0.0, min_elapsed_s=0.0,
                                      override=override)


# The tracks, each named for what it does.
WIDE_OF_A = track_of((30, START), (60, WIDE_1), (90, WIDE_2), (120, WIDE_3),
                     (150, WIDE_4), (180, MARK_B))
WIDE_THEN_FINISH = WIDE_OF_A + track_of((210, BEFORE_LINE), (240, OVER_LINE))
LAP_CROSSING_FIRST = track_of((30, BEFORE_LINE), (60, OVER_LINE),
                              (90, WIDE_1), (120, WIDE_2))
ROUNDS_A = track_of((30, START), (60, A_APPROACH), (90, MARK_A), (120, A_DEPART))


class TestTheGeometryOfTheseFixtures:
    """The fixtures are the test. If a "wide" track quietly starts passing inside
    the neighbourhood, every assertion below still passes while testing nothing."""

    def _closest_to(self, mark, fixes):
        return min(track.distance_to_segment_m(mark[0], mark[1],
                                              fixes[i]["lat"], fixes[i]["lon"],
                                              fixes[i + 1]["lat"], fixes[i + 1]["lon"])
                   for i in range(len(fixes) - 1))

    def test_the_wide_track_clears_the_neighbourhood_not_just_the_radius(self):
        """The v0.247 requirement. Clearing 50 m is no longer enough to stall a boat."""
        assert self._closest_to(MARK_A, WIDE_OF_A) > track.MARK_NEIGHBOURHOOD_M

    def test_the_wide_track_really_does_round_b(self):
        assert self._closest_to(MARK_B, WIDE_OF_A) <= ROUNDING_RADIUS_M

    def test_the_finishing_run_also_clears_the_neighbourhood(self):
        """A is off the route home for exactly this reason."""
        assert self._closest_to(MARK_A, WIDE_THEN_FINISH) > track.MARK_NEIGHBOURHOOD_M

    def test_the_lap_first_track_clears_it_too(self):
        assert self._closest_to(MARK_A, LAP_CROSSING_FIRST) > track.MARK_NEIGHBOURHOOD_M

    def test_the_proper_track_rounds_a(self):
        assert self._closest_to(MARK_A, ROUNDS_A) <= ROUNDING_RADIUS_M


class TestTheProblemBeingSolved:
    """The behaviour that makes the arrows necessary, pinned here so it is not
    mistaken for a bug in the walk and "fixed" later."""

    def test_a_wide_rounding_stalls_the_boat_for_the_rest_of_the_race(self):
        out = progress(WIDE_OF_A)
        assert out["rounded"] == 0
        assert out["next_mark"] == "A", "B was rounded but cannot count while A has not"

    def test_and_that_blocks_the_gps_finish_however_plainly_it_crosses(self):
        assert progress(WIDE_THEN_FINISH)["finished"] is False


class TestForwardArrow:
    def test_it_moves_the_boat_on_to_the_next_mark(self):
        """Cut short before the boat reaches B, so what is under test is the one
        step and not the walk carrying on afterwards (which the next test covers)."""
        short = WIDE_OF_A[:4]
        assert progress(short)["next_mark"] == "A"
        assert progress(short, {"idx": 1, "t": 25})["next_mark"] == "B"

    def test_the_walk_carries_on_from_the_corrected_mark(self):
        """Why it is a plain assignment and not a one-way floor: once excused A, the
        boat's own rounding of B still has to count."""
        out = progress(WIDE_OF_A, {"idx": 1, "t": 45})
        assert out["rounded"] == 2, "B should still be picked up by the walk"
        assert out["next_mark"] == "O"

    def test_the_gps_finish_then_arrives(self):
        assert progress(WIDE_THEN_FINISH)["finished"] is False
        assert progress(WIDE_THEN_FINISH, {"idx": 1, "t": 45})["finished"] is True

    def test_it_applies_even_before_the_next_fix_arrives(self):
        """Pressed at 14:31, the next fix may be ten seconds off — and for a boat
        whose tracker has gone quiet it may never come at all. A button that appears
        to do nothing until then reads as broken."""
        out = progress(WIDE_OF_A, {"idx": 1, "t": 9999})
        assert out["next_mark"] == "B"

    def test_it_applies_to_a_boat_with_no_fixes_at_all(self):
        out = progress([], {"idx": 1, "t": 100})
        assert out["next_mark"] == "B"
        assert out["rounded"] == 1


class TestNotRetroactive:
    def test_a_lap_crossing_before_the_correction_cannot_finish_the_boat(self):
        """The hazard that decides the design. At the club the finish-line mark O is
        a mid-course rounding mark too, so a boat nudged on to the line must not be
        finished by a crossing it made earlier on a lap."""
        out = progress(LAP_CROSSING_FIRST, {"idx": 2, "t": 130})
        assert out["finished"] is False, "an earlier lap crossing must not finish it"
        assert out["next_mark"] == "O"

    def test_a_crossing_after_the_correction_does_finish_it(self):
        fixes = LAP_CROSSING_FIRST + track_of((150, BEFORE_LINE), (180, OVER_LINE))
        assert progress(fixes, {"idx": 2, "t": 130})["finished"] is True


class TestBackArrow:
    def test_it_puts_the_boat_back_a_mark(self):
        assert progress(ROUNDS_A)["next_mark"] == "B"
        assert progress(ROUNDS_A, {"idx": 0, "t": 130})["next_mark"] == "A"

    def test_a_rounding_already_in_the_track_does_not_immediately_undo_it(self):
        """Going back only holds because the walk does not re-count the mark passed
        before the correction — the same "from now on" rule as the forward arrow."""
        out = progress(ROUNDS_A, {"idx": 0, "t": 130})
        assert out["next_mark"] == "A", "the correction should hold"

    def test_but_a_boat_that_really_does_round_it_again_advances(self):
        fixes = ROUNDS_A + track_of((150, A_APPROACH), (180, MARK_A))
        out = progress(fixes, {"idx": 0, "t": 130})
        assert out["rounded"] == 1


class TestTheIndexIsClamped:
    @pytest.mark.parametrize("idx,expected", [(-5, "A"), (0, "A"), (99, "O")])
    def test_a_nonsense_index_lands_inside_the_course(self, idx, expected):
        assert progress(WIDE_OF_A, {"idx": idx, "t": 5})["next_mark"] == expected

    def test_it_never_reads_past_the_finish(self):
        """seq[-1] is the line, so total-1 is the top of the range: past it is
        *finishing*, which is the finish button's job and must not be a side effect
        of an arrow."""
        out = progress(track_of((30, WIDE_1)), {"idx": 50, "t": 5})
        assert out["finished"] is False
        assert out["rounded"] == len(COURSE) - 1


class TestTheReplayAgrees:
    def test_the_two_walks_give_the_same_answer_with_an_override(self):
        """boat_progress_series (the replay) and boat_course_progress (live) are two
        implementations of one rule, and a test already pins that they agree. They
        have to keep agreeing once a correction is in play, or a replay shows a
        different race from the one the race officer ran."""
        override = {"idx": 1, "t": 45}
        live = progress(WIDE_OF_A, override)
        series = track.boat_progress_series(WIDE_OF_A, COURSE, LINE_A, LINE_B, None,
                                           ROUNDING_RADIUS_M, [200.0], not_before_ts=0.0,
                                           min_elapsed_s=0.0, override=override)
        assert series[-1]["rounded"] == live["rounded"]
        assert series[-1]["next_mark"] == live["next_mark"]


# ---------------------------------------------------------------------------
# Storage and the route
# ---------------------------------------------------------------------------

def make_race_with_entry(status="RACING"):
    from datetime import datetime, timedelta
    now = datetime.now()
    with ro.get_db() as db:
        race_id = int(db.execute(
            "INSERT INTO races (name, course_no, start_time, rating_rule, created_at)"
            " VALUES ('Points 1', 1, ?, 'DUAL', ?)",
            ((now - timedelta(hours=1)).isoformat(timespec="seconds"),
             now.isoformat(timespec="seconds"))).lastrowid)
        entry_id = int(db.execute(
            "INSERT INTO entries (race_id, boat_name, sail_no, status)"
            " VALUES (?, 'Kittiwake', 'GBR 42', ?)", (race_id, status)).lastrowid)
        db.commit()
    return race_id, entry_id


class TestStoringTheCorrection:
    def test_the_columns_exist_on_a_fresh_database(self, client):
        with ro.get_db() as db:
            cols = {r["name"] for r in db.execute("PRAGMA table_info(entries)")}
        assert {"next_mark_override_idx", "next_mark_override_at"} <= cols

    def test_it_round_trips(self, client):
        race_id, entry_id = make_race_with_entry()
        track.set_next_mark_override(race_id, entry_id, 2, when_ts=1234.5)
        with ro.get_db() as db:
            row = db.execute("SELECT * FROM entries WHERE id = ?", (entry_id,)).fetchone()
        assert track.entry_next_mark_override(row) == {"idx": 2, "t": 1234.5}

    def test_it_can_be_cleared(self, client):
        race_id, entry_id = make_race_with_entry()
        track.set_next_mark_override(race_id, entry_id, 2)
        track.set_next_mark_override(race_id, entry_id, None)
        with ro.get_db() as db:
            row = db.execute("SELECT * FROM entries WHERE id = ?", (entry_id,)).fetchone()
        assert track.entry_next_mark_override(row) is None

    def test_a_row_without_the_columns_is_tolerated(self, client):
        """The leaderboard runs against rows from more than one query, and an older
        database has the columns added on the next start rather than now."""
        assert track.entry_next_mark_override({"id": 1}) is None

    def test_it_defaults_to_absent(self, client):
        race_id, entry_id = make_race_with_entry()
        with ro.get_db() as db:
            row = db.execute("SELECT * FROM entries WHERE id = ?", (entry_id,)).fetchone()
        assert track.entry_next_mark_override(row) is None


class TestTheRoute:
    def _post(self, client, race_id, entry_id, direction):
        token = "test-csrf-token"
        with client.session_transaction() as sess:
            sess["_csrf_token"] = token
        return client.post(f"/admin/race/{race_id}/entry/{entry_id}/next_mark",
                           json={"direction": direction},
                           headers={"X-CSRFToken": token, "Accept": "application/json"})

    def test_a_finished_boat_is_refused_and_told_where_to_go_instead(self, logged_in_client):
        race_id, entry_id = make_race_with_entry(status="FINISHED")
        res = self._post(logged_in_client, race_id, entry_id, "forward")
        assert res.status_code == 400
        assert "Edit the entry" in res.get_json()["message"]

    def test_a_bad_direction_is_refused(self, logged_in_client):
        race_id, entry_id = make_race_with_entry()
        res = self._post(logged_in_client, race_id, entry_id, "sideways")
        assert res.status_code == 400

    def test_a_missing_entry_is_a_404_not_a_crash(self, logged_in_client):
        race_id, _ = make_race_with_entry()
        assert self._post(logged_in_client, race_id, 999999, "forward").status_code == 404

    def test_it_requires_a_login(self, client):
        race_id, entry_id = make_race_with_entry()
        res = client.post(f"/admin/race/{race_id}/entry/{entry_id}/next_mark",
                          json={"direction": "forward"},
                          headers={"Accept": "application/json"})
        assert res.status_code in (401, 302, 400)

    def test_back_from_the_first_mark_is_refused_rather_than_silently_ignored(self, logged_in_client):
        race_id, entry_id = make_race_with_entry()
        res = self._post(logged_in_client, race_id, entry_id, "back")
        # Either there is no course to step through, or the boat is at the first
        # mark; both are a plain refusal with a message, not a 500 and not a
        # pretend success.
        assert res.status_code == 400
        assert res.get_json()["message"]
