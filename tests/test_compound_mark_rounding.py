"""Compound marks have to reach the GPS walk, not just the chart.

Y and A are names for a *pair* of corner buoys — the Gwylan Islands and St Tudwal's
— and carry no position of their own. The chart, the leg lengths and the TWA
analysis all go through ``expand_course_points``, which turns Y into Ya and Yb.
``course_rounding_sequence`` used to walk ``course["marks"]`` with its own loop,
look up "Y", find no lat/lon and drop it silently.

The consequence was not a cosmetic one. The walk is sequential and the finish is
only looked for once every earlier mark has been rounded, so a course to the
Gwylans asked for nothing there at all: a boat could be recorded as finished
having never sailed west of Abersoch. Both now come from the one function.

Reported from a race sheet whose chart drew a leg straight across the Llyn
peninsula — the compound expansion was what made the chart right and the walk
wrong at the same time.
"""
from __future__ import annotations

import time

import pytest

import app as ro
from core import appstate, track
from core.courses import expand_course_points

STAMP = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - 7200))


def make_race(finish_line_key=None):
    with ro.app.app_context():
        with ro.get_db() as db:
            cur = db.execute("INSERT INTO races (course_set, name, course_no, start_time, created_at,"
                             " finish_line_key) VALUES (1, 'Gwylans', 1, ?, ?, ?)",
                             (STAMP, STAMP, finish_line_key))
            db.commit()
            return int(cur.lastrowid)


def race(race_id):
    with ro.app.app_context():
        return ro.get_race(race_id)


def sequence(race_id, marks, monkeypatch):
    monkeypatch.setattr(track, "_effective_course", lambda r: {
        "marks": [{"mark": m, "rounding": "port"} for m in marks]})
    return [s["code"] for s in track.course_rounding_sequence(race(race_id))]


class TestTheCompoundMarkIsActuallyRounded:
    def test_a_course_to_the_gwylans_asks_for_both_corners(self, client, monkeypatch):
        seq = sequence(make_race(None), ["1", "Y", "O"], monkeypatch)
        assert seq == ["1", "YA", "YB", "O"], (
            "Y carries no position; it has to expand to its corners or the walk "
            "asks for nothing at the Gwylans"
        )

    def test_the_parent_is_kept_for_display(self, client, monkeypatch):
        monkeypatch.setattr(track, "_effective_course", lambda r: {
            "marks": [{"mark": "Y", "rounding": "port"}, {"mark": "O", "rounding": "port"}]})
        seq = track.course_rounding_sequence(race(make_race(None)))
        corners = [s for s in seq if s["code"] in ("YA", "YB")]
        assert corners, "no corners in the sequence"
        assert all(s["parent"] == "Y" for s in corners), (
            "the course board and the announcement call it Y; the sequence has to "
            "remember which mark a corner belongs to"
        )

    def test_a_plain_mark_is_its_own_parent(self, client, monkeypatch):
        monkeypatch.setattr(track, "_effective_course", lambda r: {
            "marks": [{"mark": "1", "rounding": "port"}, {"mark": "O", "rounding": "port"}]})
        seq = track.course_rounding_sequence(race(make_race(None)))
        assert seq[0]["parent"] == "1"

    def test_the_corners_carry_real_positions(self, client, monkeypatch):
        monkeypatch.setattr(track, "_effective_course", lambda r: {
            "marks": [{"mark": "Y", "rounding": "port"}, {"mark": "O", "rounding": "port"}]})
        for s in track.course_rounding_sequence(race(make_race(None))):
            assert s["lat"] is not None and s["lon"] is not None


class TestItAgreesWithTheChart:
    """The chart and the walk disagreeing is what caused this. They are the same
    function now, and this fails if a second one grows back."""

    @pytest.mark.parametrize("marks", [
        ["1", "2", "O"],
        ["1", "Y", "O"],
        ["Y", "A", "O"],
        ["2", "Y", "8", "O"],
    ])
    def test_the_walk_visits_what_the_chart_draws(self, client, monkeypatch, marks):
        course = {"marks": [{"mark": m, "rounding": "port"} for m in marks]}
        monkeypatch.setattr(track, "_effective_course", lambda r: course)
        walk = [s["code"] for s in track.course_rounding_sequence(race(make_race(None)))]
        # the chart's points, minus the leading start, minus anything unpositioned
        drawn = [p["mark"] for p in expand_course_points(course)[1:]
                 if (appstate.MARKS.get(p["mark"]) or {}).get("lat") is not None]
        assert walk == drawn, "the chart and the GPS walk are sailing different courses"

    def test_a_starboard_rounding_takes_the_corners_in_the_other_order(self, client, monkeypatch):
        """Which corner comes first depends on which way round the pair goes."""
        def seq_for(rounding):
            monkeypatch.setattr(track, "_effective_course", lambda r: {
                "marks": [{"mark": "Y", "rounding": rounding}, {"mark": "O", "rounding": "port"}]})
            return [s["code"] for s in track.course_rounding_sequence(race(make_race(None)))]
        port = seq_for("port")
        starboard = seq_for("starboard")
        assert port[:2] == list(reversed(starboard[:2])), (
            f"port gave {port[:2]}, starboard gave {starboard[:2]}"
        )
