"""A race keeps the marks it was sailed with, whatever happens to them later.

Marks get re-measured after they drag. Before this, every such correction reached
backwards through the whole season: an old race was redrawn against buoys that
had been somewhere else on the day, its legs re-measured, its course length
changed — and worse, its boats re-walked around the course. Rounding is judged
within a radius of the mark's *recorded* position, so a mark corrected by more
than that radius put every recorded rounding outside the circle. The walk is
sequential, so the first such mark stalls every mark behind it and a race that
scored perfectly at the time reads afterwards as a fleet that never got round.

The positions are already kept — each one stamped with the moment it was set, and
each superseded one kept in `position_history` with its own stamp. So the
position in force at a past moment is the newest whose stamp is not after it, and
`marks_as_of` hands a whole marks dict back with the clock wound back. Every
consumer already takes a dict of that shape, so nothing else had to learn about
history.

The anchor is the *end* of the race, not the start — see the mid-race class at
the bottom of this file for the case that decides which.
"""
from __future__ import annotations

import json
from datetime import datetime

import pytest

import app as ro
import core.marks as marks
import core.track as track
from core.courses import course_legs


LAID = (52.8800, -4.4000)      # where the mark was laid
DRAGGED = (52.8830, -4.4000)   # ~333 m north, well outside a 50 m circle
MOVED_ON = "2026-07-01T10:00:00"

BEFORE = "2026-06-15T13:00:00"
AFTER = "2026-07-15T13:00:00"


def a_mark(lat, lon, *, set_at=None, history=None):
    record = {
        "name": "Mark 4", "lat": lat, "lon": lon,
        "lat_text": marks.format_lat_text(lat),
        "lon_text": marks.format_lon_text(lon),
    }
    if set_at is not None:
        record["position_set_at"] = set_at
        record["position_set_by"] = "rib"
        record["position_source"] = "phone"
    if history is not None:
        record["position_history"] = history
    return record


@pytest.fixture
def moved_mark():
    """Mark 4, laid where it was, dragged and re-measured on 1 July."""
    return a_mark(*DRAGGED, set_at=MOVED_ON,
                  history=[{"lat": LAID[0], "lon": LAID[1], "set_at": None,
                            "set_by": None, "source": None, "accuracy_m": None}])


class TestWhereWasIt:
    def test_after_the_move_it_is_where_it_is_now(self, moved_mark):
        assert marks.position_at(moved_mark, AFTER) == DRAGGED

    def test_at_the_moment_of_the_move_it_is_the_new_position(self, moved_mark):
        assert marks.position_at(moved_mark, MOVED_ON) == DRAGGED

    def test_before_the_move_it_is_where_it_was(self, moved_mark):
        assert marks.position_at(moved_mark, BEFORE) == LAID

    def test_no_time_asked_for_means_now(self, moved_mark):
        assert marks.position_at(moved_mark, None) == DRAGGED

    def test_a_mark_never_moved_reads_the_same_at_any_date(self):
        """Most marks. Without this every race before the first re-measurement
        would be unplottable."""
        plain = a_mark(*LAID)
        assert marks.position_at(plain, BEFORE) == LAID
        assert marks.position_at(plain, AFTER) == LAID

    def test_a_mark_with_no_position_has_none(self):
        assert marks.position_at({"compound": True, "components": ["A"]}, BEFORE) is None

    def test_three_positions_resolve_to_the_middle_one(self):
        """The general case: the newest stamp not after the moment asked for."""
        record = a_mark(3.0, 3.0, set_at="2026-06-20T10:00:00", history=[
            {"lat": 2.0, "lon": 2.0, "set_at": "2026-06-10T10:00:00"},
            {"lat": 1.0, "lon": 1.0, "set_at": None},
        ])
        assert marks.position_at(record, "2026-06-15T00:00:00") == (2.0, 2.0)
        assert marks.position_at(record, "2026-06-05T00:00:00") == (1.0, 1.0)
        assert marks.position_at(record, "2026-06-25T00:00:00") == (3.0, 3.0)

    def test_epoch_seconds_work_as_well_as_a_string(self, moved_mark):
        """The replay works in epoch seconds throughout."""
        ts = datetime.fromisoformat(BEFORE).timestamp()
        assert marks.position_at(moved_mark, ts) == LAID

    def test_an_unreadable_stamp_does_not_explode(self):
        record = a_mark(*DRAGGED, set_at="not a date",
                        history=[{"lat": LAID[0], "lon": LAID[1], "set_at": None}])
        assert marks.position_at(record, BEFORE) == DRAGGED


class TestWindingTheWholeSetBack:
    def test_the_moved_mark_moves_back(self, moved_mark):
        at = marks.marks_as_of(BEFORE, {"4": moved_mark})
        assert (at["4"]["lat"], at["4"]["lon"]) == LAID

    def test_the_printed_position_goes_back_with_it(self, moved_mark):
        """Otherwise the course board would print today's position beside a chart
        drawn at last month's."""
        at = marks.marks_as_of(BEFORE, {"4": moved_mark})
        assert at["4"]["lat_text"] == marks.format_lat_text(LAID[0])
        assert at["4"]["lon_text"] == marks.format_lon_text(LAID[1])

    def test_it_says_it_is_historical(self, moved_mark):
        at = marks.marks_as_of(BEFORE, {"4": moved_mark})
        assert at["4"]["position_is_historical"] is True
        assert "position_is_historical" not in marks.marks_as_of(AFTER, {"4": moved_mark})["4"]

    def test_the_stored_mark_is_not_touched(self, moved_mark):
        """It reads a copy. Winding the clock back must not write to marks.json."""
        marks.marks_as_of(BEFORE, {"4": moved_mark})
        assert (moved_mark["lat"], moved_mark["lon"]) == DRAGGED

    def test_compound_marks_come_through_untouched(self):
        compound = {"compound": True, "components": ["4"]}
        at = marks.marks_as_of(BEFORE, {"TC": compound})
        assert at["TC"] is compound

    def test_marks_that_never_moved_come_through_untouched(self):
        plain = a_mark(*LAID)
        at = marks.marks_as_of(BEFORE, {"4": plain})
        assert at["4"] is plain

    def test_no_time_returns_the_live_set_unchanged(self, moved_mark):
        source = {"4": moved_mark}
        assert marks.marks_as_of(None, source) is source


# ---------------------------------------------------------------------------
# What it is all for: the race
# ---------------------------------------------------------------------------

def make_race(start_time: str) -> int:
    with ro.app.app_context():
        with ro.get_db() as db:
            cur = db.execute(
                "INSERT INTO races (course_set, name, course_no, start_time, created_at)"
                " VALUES (1, 'Historical', 1, ?, ?)",
                (start_time, start_time))
            db.commit()
            return int(cur.lastrowid)


@pytest.fixture
def race_before_the_move(client, monkeypatch, tmp_path, moved_mark):
    """A race sailed in June, with a mark re-measured in July."""
    data = {"marks": {
        "4": moved_mark,
        "O": a_mark(52.8700, -4.4100, set_at=MOVED_ON,
                    history=[{"lat": 52.8690, "lon": -4.4100, "set_at": None}]),
    }}
    path = tmp_path / "marks.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(marks, "_marks_path", lambda: path)
    monkeypatch.setattr(ro.appstate, "MARKS", data["marks"])
    with ro.app.app_context():
        from core.races import get_race
        return get_race(make_race(BEFORE))


class TestARaceKeepsItsOwnMarks:
    def test_race_marks_winds_back_to_the_day_it_was_sailed(self, race_before_the_move):
        at = track.race_marks(race_before_the_move)
        assert (at["4"]["lat"], at["4"]["lon"]) == LAID

    def test_a_race_with_no_start_time_uses_the_marks_as_they_are(self, client,
                                                                  monkeypatch,
                                                                  tmp_path,
                                                                  moved_mark):
        """A race being built will be sailed with the marks as they are now."""
        monkeypatch.setattr(ro.appstate, "MARKS", {"4": moved_mark})
        with ro.app.app_context():
            from core.races import get_race
            race = get_race(make_race(""))
        at = track.race_marks(race)
        assert (at["4"]["lat"], at["4"]["lon"]) == DRAGGED

    def test_no_race_at_all_uses_the_marks_as_they_are(self, client, monkeypatch,
                                                       moved_mark):
        monkeypatch.setattr(ro.appstate, "MARKS", {"4": moved_mark})
        assert track.race_marks(None)["4"]["lat"] == DRAGGED[0]

    def test_the_rounding_walk_looks_where_the_boats_went(self, race_before_the_move,
                                                          monkeypatch):
        """The one that broke races. A 333 m correction is far outside the 50 m
        rounding radius, so the walk would find no rounding at the new position
        and stall every mark behind it."""
        monkeypatch.setattr(track, "_effective_course", lambda race: {
            "marks": [{"mark": "4", "rounding": "port"}, {"mark": "O", "rounding": "port"}]})
        seq = track.course_rounding_sequence(race_before_the_move)
        four = next(s for s in seq if s["code"] == "4")
        assert (four["lat"], four["lon"]) == LAID

    def test_the_finish_line_is_the_one_that_was_laid(self, race_before_the_move):
        """O is a laid mark too, and it is one end of the finish line."""
        line = track.finish_line_points(track.race_marks(race_before_the_move))
        assert line is not None
        assert line[0] == (52.8690, -4.4100)

    def test_and_today_the_line_is_where_it_is_now(self, race_before_the_move):
        assert track.finish_line_points()[0] == (52.8700, -4.4100)

    def test_the_legs_are_measured_against_the_old_positions(self,
                                                             race_before_the_move):
        course = {"marks": [{"mark": "4", "rounding": "port"}]}
        then = course_legs(course, marks=track.race_marks(race_before_the_move))
        now = course_legs(course)
        assert then[0]["distance_nm"] != pytest.approx(now[0]["distance_nm"])


# ---------------------------------------------------------------------------
# The mark that is found to have dragged *during* the race
# ---------------------------------------------------------------------------

def add_entry(race_id: int, sail_no: str, status: str, finish_time: str = "") -> None:
    with ro.app.app_context():
        with ro.get_db() as db:
            db.execute(
                "INSERT INTO entries (race_id, boat_name, sail_no, status, finish_time)"
                " VALUES (?, ?, ?, ?, ?)",
                (race_id, sail_no, sail_no, status, finish_time))
            db.commit()


def gun(start_time: str):
    """The moment race_first_start_dt reports: the warning signal plus five."""
    from core.races import race_first_start_dt
    return race_first_start_dt({"start_time": start_time})


class TestAMarkPingedWhileTheRaceIsStillBeingSailed:
    """The case that decides start-anchored versus end-anchored.

    Nobody notices a mark has dragged until the race is on and the first boats
    round it without registering. The third boat has a mark layer aboard, who
    pings it as they round. Anchored on the *start* signal that correction would
    be filed after the race and ignored by it — the boats already round would
    stay unrecognised, and so would every boat behind them, because the walk is
    sequential. It has to count for the race it was made during.
    """

    @pytest.fixture
    def live_race(self, client, monkeypatch, tmp_path):
        """A race under way, with the mark still recorded where it was laid."""
        data = {"marks": {"4": a_mark(*LAID), "O": a_mark(52.8700, -4.4100)}}
        path = tmp_path / "marks.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        monkeypatch.setattr(marks, "_marks_path", lambda: path)
        monkeypatch.setattr(ro.appstate, "MARKS", data["marks"])
        race_id = make_race(BEFORE)
        add_entry(race_id, "GBR1", "RACING")
        add_entry(race_id, "GBR2", "RACING")
        with ro.app.app_context():
            from core.races import get_race
            return get_race(race_id), path

    def test_the_correction_counts_immediately(self, live_race, monkeypatch):
        race, path = live_race
        # The mark layer pings it from the RIB, mid-race.
        ok, _ = marks.set_mark_position("4", *DRAGGED, by="rib", accuracy_m=5.0)
        assert ok
        reloaded = json.loads(path.read_text(encoding="utf-8"))["marks"]
        monkeypatch.setattr(ro.appstate, "MARKS", reloaded)

        at = track.race_marks(race)
        assert (at["4"]["lat"], at["4"]["lon"]) == DRAGGED, \
            "a race still being sailed must use the corrected position"

    def test_and_the_walk_looks_where_the_boats_actually_went(self, live_race,
                                                              monkeypatch):
        """Which is what recovers the boats that had already rounded: the walk
        recomputes from every stored fix, so it re-reads the whole race."""
        race, path = live_race
        marks.set_mark_position("4", *DRAGGED, by="rib", accuracy_m=5.0)
        monkeypatch.setattr(ro.appstate, "MARKS",
                            json.loads(path.read_text(encoding="utf-8"))["marks"])
        monkeypatch.setattr(track, "_effective_course", lambda race: {
            "marks": [{"mark": "4", "rounding": "port"}, {"mark": "O", "rounding": "port"}]})
        four = next(s for s in track.course_rounding_sequence(race) if s["code"] == "4")
        assert (four["lat"], four["lon"]) == DRAGGED

    def test_it_still_counts_after_the_race_has_finished(self, live_race,
                                                         monkeypatch):
        """Durability. The correction was made during the race, so it belongs to
        that race for good — not only while the race happens to be live."""
        race, path = live_race
        marks.set_mark_position("4", *DRAGGED, by="rib", accuracy_m=5.0)
        monkeypatch.setattr(ro.appstate, "MARKS",
                            json.loads(path.read_text(encoding="utf-8"))["marks"])
        with ro.app.app_context():
            with ro.get_db() as db:
                db.execute("UPDATE entries SET status = 'FINISHED', finish_time = ?"
                           " WHERE race_id = ?",
                           (datetime.now().isoformat(timespec="seconds"), int(race["id"])))
                db.commit()
            from core.races import get_race
            race = get_race(int(race["id"]))
        at = track.race_marks(race)
        assert (at["4"]["lat"], at["4"]["lon"]) == DRAGGED

    def test_a_race_that_finished_before_the_ping_is_left_alone(self, live_race,
                                                               monkeypatch):
        """The other half of the bargain, and the thing the whole feature is for:
        a mark corrected after a race ended does not reach back into it."""
        race, path = live_race
        with ro.app.app_context():
            with ro.get_db() as db:
                db.execute("UPDATE entries SET status = 'FINISHED', finish_time = ?"
                           " WHERE race_id = ?", (BEFORE, int(race["id"])))
                db.commit()
            from core.races import get_race
            race = get_race(int(race["id"]))
        marks.set_mark_position("4", *DRAGGED, by="rib", accuracy_m=5.0)
        monkeypatch.setattr(ro.appstate, "MARKS",
                            json.loads(path.read_text(encoding="utf-8"))["marks"])
        at = track.race_marks(race)
        assert (at["4"]["lat"], at["4"]["lon"]) == LAID


class TestWhenARaceIsPinned:
    def test_a_race_with_boats_still_out_is_not_pinned_at_all(self, client,
                                                             monkeypatch, tmp_path):
        monkeypatch.setattr(ro.appstate, "MARKS", {"4": a_mark(*LAID)})
        race_id = make_race(BEFORE)
        add_entry(race_id, "GBR1", "RACING")
        with ro.app.app_context():
            from core.races import get_race
            assert track.race_ended_dt(get_race(race_id)) is None

    def test_a_finished_race_is_pinned_to_the_last_boat_home(self, client,
                                                             monkeypatch):
        monkeypatch.setattr(ro.appstate, "MARKS", {"4": a_mark(*LAID)})
        race_id = make_race(BEFORE)
        add_entry(race_id, "GBR1", "FINISHED", "2026-06-15T15:10:00")
        add_entry(race_id, "GBR2", "FINISHED", "2026-06-15T15:40:00")
        with ro.app.app_context():
            from core.races import get_race
            ended = track.race_ended_dt(get_race(race_id))
        assert ended == datetime.fromisoformat("2026-06-15T15:40:00")

    def test_a_race_nobody_sailed_falls_back_to_its_own_day(self, client,
                                                            monkeypatch):
        """All DNS/DNC: over, with nothing finished to anchor on."""
        monkeypatch.setattr(ro.appstate, "MARKS", {"4": a_mark(*LAID)})
        race_id = make_race(BEFORE)
        add_entry(race_id, "GBR1", "DNS")
        with ro.app.app_context():
            from core.races import get_race
            assert track.race_ended_dt(get_race(race_id)) == gun(BEFORE)

    def test_a_race_with_no_entries_falls_back_to_its_own_day(self,
                                                              race_before_the_move):
        assert track.race_ended_dt(race_before_the_move) == gun(BEFORE)
