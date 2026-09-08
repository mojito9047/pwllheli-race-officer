"""Tests for the yacht-tracking + GPS auto-finish subsystem (core/track.py).

Covers the pure crossing geometry, the separate positions DB (insert/dedup/
retention/query), the Traccar client parser, tracker resolution (permanent vs
per-race loaner override), end-to-end finish detection (proposal and auto-
confirm), and the JSON + confirm/dismiss routes. No hardware or network.
"""
from __future__ import annotations

import math
import time
from datetime import datetime, timedelta

import pytest

import app as ro
from core import track


@pytest.fixture(autouse=True)
def _isolate_track_db(tmp_path, monkeypatch):
    """Point the separate track-positions DB at a temp file for every test."""
    monkeypatch.setattr(track, "TRACK_DB_PATH", tmp_path / "track_positions.db")
    monkeypatch.setattr(track, "TRACK_DB_INITIALIZED", False)


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------
def _line_and_normal():
    a, b = track.finish_line_points()
    mid = ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0)
    dlat, dlon = b[0] - a[0], b[1] - a[1]
    nlat, nlon = -dlon, dlat
    norm = math.hypot(nlat, nlon) or 1.0
    return a, b, mid, (nlat / norm, nlon / norm)


class TestGeometry:
    def test_finish_crossing_leaving_the_course(self):
        a, b, mid, (nlat, nlon) = _line_and_normal()
        course_ref = (mid[0] + nlat * 0.01, mid[1] + nlon * 0.01)  # course side (+n)
        fixes = [
            {"t": 1000.0, "lat": mid[0] + nlat * 0.002, "lon": mid[1] + nlon * 0.002},  # course side
            {"t": 1002.0, "lat": mid[0] - nlat * 0.002, "lon": mid[1] - nlon * 0.002},  # other side
        ]
        crossing = track.detect_finish_crossing(fixes, a, b, course_ref, 0.0, 0.0)
        assert crossing is not None
        assert 1000.0 < crossing["t"] < 1002.0

    def test_start_crossing_is_not_a_finish(self):
        a, b, mid, (nlat, nlon) = _line_and_normal()
        course_ref = (mid[0] + nlat * 0.01, mid[1] + nlon * 0.01)
        # Reverse direction: entering the course (a start) must not count.
        fixes = [
            {"t": 1000.0, "lat": mid[0] - nlat * 0.002, "lon": mid[1] - nlon * 0.002},
            {"t": 1002.0, "lat": mid[0] + nlat * 0.002, "lon": mid[1] + nlon * 0.002},
        ]
        assert track.detect_finish_crossing(fixes, a, b, course_ref, 0.0, 0.0) is None

    def test_no_crossing_when_parallel_to_line(self):
        a, b, mid, (nlat, nlon) = _line_and_normal()
        course_ref = (mid[0] + nlat * 0.01, mid[1] + nlon * 0.01)
        # Two fixes both on the course side, never crossing.
        fixes = [
            {"t": 1000.0, "lat": mid[0] + nlat * 0.004, "lon": mid[1] + nlon * 0.004},
            {"t": 1002.0, "lat": mid[0] + nlat * 0.006, "lon": mid[1] + nlon * 0.006},
        ]
        assert track.detect_finish_crossing(fixes, a, b, course_ref, 0.0, 0.0) is None

    def test_crossing_before_guard_is_ignored(self):
        a, b, mid, (nlat, nlon) = _line_and_normal()
        course_ref = (mid[0] + nlat * 0.01, mid[1] + nlon * 0.01)
        fixes = [
            {"t": 1000.0, "lat": mid[0] + nlat * 0.002, "lon": mid[1] + nlon * 0.002},
            {"t": 1002.0, "lat": mid[0] - nlat * 0.002, "lon": mid[1] - nlon * 0.002},
        ]
        # not_before=900, min_elapsed=200 => guard=1100 > 1002 => ignored.
        assert track.detect_finish_crossing(fixes, a, b, course_ref, 900.0, 200.0) is None


# ---------------------------------------------------------------------------
# Positions DB
# ---------------------------------------------------------------------------
class TestPositionsDB:
    # These take `client` for its temp database: insert_positions() looks up the
    # device -> boat map in the main DB, which without the fixture would be the
    # developer's live one.
    def test_insert_dedups_and_queries(self, client):
        now = time.time()
        rows = [
            {"unique_id": "A", "device_id": "1", "name": "A", "lat": 52.9, "lon": -4.4, "speed_kn": 3.0, "course_deg": 100.0, "fix_time": now - 10},
            {"unique_id": "A", "device_id": "1", "name": "A", "lat": 52.91, "lon": -4.41, "speed_kn": 3.5, "course_deg": 110.0, "fix_time": now - 5},
        ]
        assert track.insert_positions(rows, retention_days=90) == 2
        # Re-inserting the same (not newer) fixes stores nothing.
        assert track.insert_positions(rows, retention_days=90) == 0
        got = track.positions_since("A", now - 3600)
        assert len(got) == 2
        assert got[0]["t"] < got[1]["t"]
        latest = track.latest_positions()
        assert latest["A"]["lat"] == 52.91

    def test_retention_purge(self, client):
        """Old fixes are dropped — but by the housekeeping job, not by the insert.

        insert_positions used to purge on every call, and every call is every fix
        the relay pushes. The purge filters on fix_time inside a write
        transaction, and this database is in rollback journal mode where a writer
        excludes every reader, so each pushed fix locked out the clubhouse
        display and every competitor page. It is a flat age cut on data kept for
        months; it belongs on a timer.
        """
        now = time.time()
        old = [{"unique_id": "B", "device_id": "2", "name": "B", "lat": 52.9, "lon": -4.4, "speed_kn": 0, "course_deg": 0, "fix_time": now - 100 * 86400}]
        new = [{"unique_id": "B", "device_id": "2", "name": "B", "lat": 52.9, "lon": -4.4, "speed_kn": 0, "course_deg": 0, "fix_time": now - 5}]
        track.insert_positions(old, retention_days=90)
        track.insert_positions(new, retention_days=90)
        # Still there: the insert stores, it does not tidy up.
        assert len(track.positions_since("B", now - 200 * 86400)) == 2
        track.purge_old_positions(retention_days=90, force=True)
        got = track.positions_since("B", now - 200 * 86400)
        assert len(got) == 1  # the 100-day-old fix was purged
        assert got[0]["t"] > now - 3600


# ---------------------------------------------------------------------------
# Traccar client parser
# ---------------------------------------------------------------------------
class TestTraccarClient:
    def test_parse_iso_epoch(self):
        assert track._parse_iso_epoch("2026-07-27T10:00:00Z") == pytest.approx(
            datetime.fromisoformat("2026-07-27T10:00:00+00:00").timestamp())
        assert track._parse_iso_epoch(None) is None
        assert track._parse_iso_epoch("not-a-date") is None

    def test_fetch_positions_normalises(self, monkeypatch):
        devices = [{"id": 7, "uniqueId": "IMEI-7", "name": "Boat Seven"}]
        positions = [{"deviceId": 7, "latitude": 52.9, "longitude": -4.4, "speed": 5.5,
                      "course": 123.0, "fixTime": "2026-07-27T10:00:00Z"}]

        def fake_get(cfg, path, timeout=4.0):
            return devices if path.endswith("/devices") else positions

        monkeypatch.setattr(track, "_traccar_get", fake_get)
        out, error = track.fetch_positions({"base_url": "http://x", "token": "t"})
        assert error is None
        assert len(out) == 1
        assert out[0]["unique_id"] == "IMEI-7"
        assert out[0]["lat"] == 52.9 and out[0]["speed_kn"] == 5.5
        assert out[0]["fix_time"] == pytest.approx(datetime.fromisoformat("2026-07-27T10:00:00+00:00").timestamp())

    def test_non_ascii_field_gives_clear_error(self):
        # A copy-pasted em-dash in the token/URL must not raise a cryptic latin-1
        # codec error deep in urllib — it should return a clear, named message.
        out, err = track.fetch_positions({"base_url": "https://x/traccar", "token": "abc—def"})
        assert out == [] and err and "API token" in err and "non-ASCII" in err
        out, err = track.fetch_positions({"base_url": "https://x—/traccar", "token": "clean"})
        assert out == [] and err and "Base URL" in err

    def test_fetch_positions_degrades_on_error(self, monkeypatch):
        def boom(cfg, path, timeout=4.0):
            raise OSError("network down")
        monkeypatch.setattr(track, "_traccar_get", boom)
        out, error = track.fetch_positions({"base_url": "http://x", "token": "t"})
        assert out == [] and "network down" in error


# ---------------------------------------------------------------------------
# Tracker resolution (permanent vs per-race override)
# ---------------------------------------------------------------------------
def _make_boat(name="Test Boat", sail="GBR1"):
    now = datetime.now().isoformat(timespec="seconds")
    with ro.get_db() as db:
        cur = db.execute(
            "INSERT INTO boats (boat_name, sail_no, status, created_at, updated_at) VALUES (?, ?, 'ACTIVE', ?, ?)",
            (name, sail, now, now),
        )
        db.commit()
        return int(cur.lastrowid)


def _make_race(gps_finish=0, gps_auto=0, minutes_ago=10, course_no=None):
    warning = (datetime.now() - timedelta(minutes=minutes_ago)).isoformat(timespec="seconds")
    cno = course_no if course_no is not None else ro.appstate.COURSES[0]["course_no"]
    with ro.get_db() as db:
        cur = db.execute(
            "INSERT INTO races (course_set, name, class_name, course_no, start_time, rating_rule, notes, created_at, gps_finish_enabled, gps_auto_confirm) "
            "VALUES (1, ?, 'IRC', ?, ?, 'DUAL', '', ?, ?, ?)",
            ("GPS Race", cno, warning,
             datetime.now().isoformat(timespec="seconds"), gps_finish, gps_auto),
        )
        db.commit()
        return int(cur.lastrowid)


def _make_entry(race_id, boat_id, boat_name="Test Boat", sail="GBR1", tracker=None):
    with ro.get_db() as db:
        cur = db.execute(
            "INSERT INTO entries (race_id, boat_name, sail_no, boat_id, status, tracker_unique_id) VALUES (?, ?, ?, ?, 'RACING', ?)",
            (race_id, boat_name, sail, boat_id, tracker),
        )
        db.commit()
        return int(cur.lastrowid)


class TestTrackerResolution:
    def test_permanent_assignment(self, client):
        boat_id = _make_boat()
        track.upsert_tracker("IMEI-100", label="Tracker A", boat_id=boat_id)
        race_id = _make_race()
        eid = _make_entry(race_id, boat_id)
        with ro.get_db() as db:
            entry = db.execute("SELECT * FROM entries WHERE id = ?", (eid,)).fetchone()
        assert track.effective_tracker_for_entry(entry) == "IMEI-100"

    def test_loaner_override_wins(self, client):
        boat_id = _make_boat()
        track.upsert_tracker("IMEI-100", label="Permanent", boat_id=boat_id)
        race_id = _make_race()
        eid = _make_entry(race_id, boat_id, tracker="IMEI-LOANER")
        with ro.get_db() as db:
            entry = db.execute("SELECT * FROM entries WHERE id = ?", (eid,)).fetchone()
        assert track.effective_tracker_for_entry(entry) == "IMEI-LOANER"


class TestBoatLinkedTracks:
    def test_track_stays_with_boat_across_tracker_change(self, client):
        boat_id = _make_boat()
        now = time.time()
        track.upsert_tracker("DEV-A", boat_id=boat_id)
        track.insert_positions([_fix("DEV-A", 52.90, -4.40, now - 100)])
        # Change the boat's tracker: DEV-A off, DEV-B on.
        track.upsert_tracker("DEV-A", boat_id=None)
        track.upsert_tracker("DEV-B", boat_id=boat_id)
        track.insert_positions([_fix("DEV-B", 52.91, -4.41, now - 10)])
        got = track.positions_for_boat_since(boat_id, now - 3600)
        # Both the old (DEV-A) and new (DEV-B) fixes belong to the boat.
        assert sorted(g["lat"] for g in got) == [52.90, 52.91]

    def test_assignment_backfills_unattributed_fixes(self, client):
        boat_id = _make_boat()
        now = time.time()
        # Device reports before being assigned to any boat (boat_id stamped NULL).
        track.insert_positions([_fix("DEV-C", 52.9, -4.4, now - 20)])
        assert track.positions_for_boat_since(boat_id, now - 3600) == []
        track.upsert_tracker("DEV-C", boat_id=boat_id)  # assign -> back-fill
        assert len(track.positions_for_boat_since(boat_id, now - 3600)) == 1

    def test_loaner_by_device_default_by_boat(self, client):
        now = time.time()
        boat_id = _make_boat()
        track.upsert_tracker("PERM", boat_id=boat_id)
        race_id = _make_race()
        e_def = _make_entry(race_id, boat_id)
        track.insert_positions([_fix("PERM", 52.9, -4.4, now - 10)])          # -> boat_id
        boat2 = _make_boat(name="B2", sail="B2")
        e_loan = _make_entry(race_id, boat2, tracker="LOAN")                   # per-race loaner
        track.insert_positions([_fix("LOAN", 52.8, -4.5, now - 10)])          # boat_id NULL
        with ro.get_db() as db:
            ed = db.execute("SELECT * FROM entries WHERE id = ?", (e_def,)).fetchone()
            el = db.execute("SELECT * FROM entries WHERE id = ?", (e_loan,)).fetchone()
        assert len(track.positions_for_entry_since(ed, now - 3600)) == 1       # resolved by boat
        loan = track.positions_for_entry_since(el, now - 3600)
        assert len(loan) == 1 and loan[0]["lat"] == 52.8                       # resolved by device


# ---------------------------------------------------------------------------
# End-to-end finish detection
# ---------------------------------------------------------------------------
def _fix(uid, lat, lon, t):
    return {"unique_id": uid, "device_id": uid, "name": uid, "lat": lat, "lon": lon,
            "speed_kn": 5, "course_deg": 0, "fix_time": t}


def _seed_full_course_track(race_id, uid, finish=True):
    """Insert a track that rounds every course mark in order then (finish=True)
    crosses the finish line — the shape now required to record a GPS finish."""
    with ro.get_db() as db:
        race = db.execute("SELECT * FROM races WHERE id = ?", (race_id,)).fetchone()
    seq = track.course_rounding_sequence(race)
    a, b = track.finish_line_points()
    mid = ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0)
    # Approach from the last mark rounded, which is where a finishing boat comes
    # from. This used to build the crossing from _course_ref_point (the centroid of
    # the course marks) and so encoded the old finishing-direction rule rather than
    # a realistic track — on a course whose marks straddle the line, and 8 of the
    # club's 12 do, the centroid is on the opposite side from the last mark. See
    # core.track.finish_direction_point and tests/test_finish_direction.py.
    approach = ((seq[-2]["lat"], seq[-2]["lon"]) if len(seq) >= 2
                else (track._course_ref_point(race) or (mid[0] + 0.01, mid[1])))
    v = (approach[0] - mid[0], approach[1] - mid[1])
    vn = math.hypot(*v) or 1.0
    v = (v[0] / vn * 0.003, v[1] / vn * 0.003)
    t = ro.race_first_start_dt(race).timestamp() + track.MIN_FINISH_ELAPSED_S + 30
    rows = []
    for m in seq[:-1]:  # round every mark except the final finish (the line)
        rows.append(_fix(uid, m["lat"], m["lon"], t)); t += 10
    if finish:
        rows.append(_fix(uid, mid[0] + v[0], mid[1] + v[1], t)); t += 2  # last mark's side
        rows.append(_fix(uid, mid[0] - v[0], mid[1] - v[1], t)); t += 2  # over the line
    track.insert_positions(rows, retention_days=90)


class TestFinishDetection:
    def test_course_ref_point_resolves_from_dict_marks(self, client):
        # Course marks are dicts ({"token","mark","rounding"}); the course-side
        # reference must resolve to a real point so direction disambiguation works.
        race_id = _make_race()
        with ro.get_db() as db:
            race = db.execute("SELECT * FROM races WHERE id = ?", (race_id,)).fetchone()
        ref = track._course_ref_point(race)
        assert ref is not None
        assert isinstance(ref[0], float) and isinstance(ref[1], float)

    def test_proposal_created_when_not_auto(self, client):
        boat_id = _make_boat()
        track.upsert_tracker("IMEI-1", boat_id=boat_id)
        race_id = _make_race(gps_finish=1, gps_auto=0)
        eid = _make_entry(race_id, boat_id)
        _seed_full_course_track(race_id, "IMEI-1")
        assert track.run_finish_detection() >= 1
        pending = track.pending_proposals_for_race(race_id)
        assert len(pending) == 1 and pending[0]["entry_id"] == eid
        # Entry is not yet finished (awaiting confirmation).
        with ro.get_db() as db:
            e = db.execute("SELECT * FROM entries WHERE id = ?", (eid,)).fetchone()
        assert e["status"] == "RACING"
        # Confirming records the finish with the gps-confirmed tag.
        assert track.confirm_proposal(pending[0]["id"]) is True
        with ro.get_db() as db:
            e = db.execute("SELECT * FROM entries WHERE id = ?", (eid,)).fetchone()
        assert e["status"] == "FINISHED" and e["finish_source"] == "gps-confirmed"

    def test_auto_confirm_records_finish(self, client):
        boat_id = _make_boat()
        track.upsert_tracker("IMEI-2", boat_id=boat_id)
        race_id = _make_race(gps_finish=1, gps_auto=1)
        eid = _make_entry(race_id, boat_id)
        _seed_full_course_track(race_id, "IMEI-2")
        assert track.run_finish_detection() >= 1
        with ro.get_db() as db:
            e = db.execute("SELECT * FROM entries WHERE id = ?", (eid,)).fetchone()
        assert e["status"] == "FINISHED" and e["finish_source"] == "gps-auto"
        assert e["finish_time"]

    def test_disarmed_race_detects_nothing(self, client):
        boat_id = _make_boat()
        track.upsert_tracker("IMEI-3", boat_id=boat_id)
        race_id = _make_race(gps_finish=0)
        _make_entry(race_id, boat_id)
        _seed_full_course_track(race_id, "IMEI-3")
        assert track.run_finish_detection() == 0

    def test_dismiss_proposal(self, client):
        boat_id = _make_boat()
        track.upsert_tracker("IMEI-4", boat_id=boat_id)
        race_id = _make_race(gps_finish=1, gps_auto=0)
        eid = _make_entry(race_id, boat_id)
        _seed_full_course_track(race_id, "IMEI-4")
        track.run_finish_detection()
        pending = track.pending_proposals_for_race(race_id)
        assert track.dismiss_proposal(pending[0]["id"]) is True
        assert track.pending_proposals_for_race(race_id) == []
        with ro.get_db() as db:
            e = db.execute("SELECT * FROM entries WHERE id = ?", (eid,)).fetchone()
        assert e["status"] == "RACING"  # dismissed => no finish


# ---------------------------------------------------------------------------
# Course progress + mark rounding (the ODM-reused-mark fix) + leaderboard
# ---------------------------------------------------------------------------
class TestCourseProgress:
    def test_no_early_finish_on_odm_reused_course(self, client):
        # Course 11 = 7 F O 1 3 7 O 2 3 O — O is a mid-course mark. Passing O
        # early must NOT finish the boat; only the full course + final crossing.
        boat_id = _make_boat()
        track.upsert_tracker("IMEI-11", boat_id=boat_id)
        race_id = _make_race(gps_finish=1, gps_auto=1, course_no=11)
        eid = _make_entry(race_id, boat_id)
        with ro.get_db() as db:
            race = db.execute("SELECT * FROM races WHERE id = ?", (race_id,)).fetchone()
        seq = track.course_rounding_sequence(race)
        assert seq[-1]["code"] == "O" and any(m["code"] == "O" for m in seq[:-1])  # O reused mid-course
        # Only round the marks up to and including the FIRST O, no final crossing.
        first_o = next(i for i, m in enumerate(seq) if m["code"] == "O")
        t = ro.race_first_start_dt(race).timestamp() + track.MIN_FINISH_ELAPSED_S + 30
        rows = []
        for m in seq[:first_o + 1]:
            rows.append(_fix("IMEI-11", m["lat"], m["lon"], t)); t += 10
        track.insert_positions(rows)
        assert track.run_finish_detection() == 0  # not finished at the first O
        with ro.get_db() as db:
            assert db.execute("SELECT status FROM entries WHERE id=?", (eid,)).fetchone()["status"] == "RACING"
        # Now sail the whole course and cross — it finishes.
        _seed_full_course_track(race_id, "IMEI-11")
        assert track.run_finish_detection() >= 1
        with ro.get_db() as db:
            assert db.execute("SELECT status FROM entries WHERE id=?", (eid,)).fetchone()["status"] == "FINISHED"

    def test_shortened_course_finishes_at_shorten_mark(self, client):
        # Shorten course 11 at index 3 (mark '1'): a boat that rounds 7,F,O,1 and
        # crosses the line must finish — without rounding the dropped later marks.
        boat_id = _make_boat()
        track.upsert_tracker("IMEI-S", boat_id=boat_id)
        race_id = _make_race(gps_finish=1, gps_auto=1, course_no=11)
        with ro.get_db() as db:
            db.execute("UPDATE races SET shortened_at_index = 3, shortened_at_mark = '1' WHERE id = ?", (race_id,))
            db.commit()
        eid = _make_entry(race_id, boat_id)
        with ro.get_db() as db:
            race = db.execute("SELECT * FROM races WHERE id = ?", (race_id,)).fetchone()
        seq = track.course_rounding_sequence(race)
        assert [m["code"] for m in seq] == ["7", "F", "O", "1", "O"]  # truncated + finish O
        _seed_full_course_track(race_id, "IMEI-S")  # seeds the shortened seq's marks + crossing
        assert track.run_finish_detection() >= 1
        with ro.get_db() as db:
            assert db.execute("SELECT status FROM entries WHERE id=?", (eid,)).fetchone()["status"] == "FINISHED"

    def test_progress_and_distance_remaining(self, client):
        boat_id = _make_boat()
        track.upsert_tracker("IMEI-P", boat_id=boat_id)
        race_id = _make_race(gps_finish=1, course_no=11)
        _make_entry(race_id, boat_id)
        with ro.get_db() as db:
            race = db.execute("SELECT * FROM races WHERE id = ?", (race_id,)).fetchone()
        seq = track.course_rounding_sequence(race)
        t = ro.race_first_start_dt(race).timestamp() + track.MIN_FINISH_ELAPSED_S + 30
        # Round the first three marks only.
        rows = [_fix("IMEI-P", m["lat"], m["lon"], t + i * 10) for i, m in enumerate(seq[:3])]
        track.insert_positions(rows)
        board = track.race_leaderboard(race_id)
        assert len(board) == 1
        row = board[0]
        assert row["rounded"] == 3 and row["total"] == len(seq)
        assert row["finished"] is False and row["dist_remaining_nm"] > 0

    def test_position_shown_before_the_start(self, client):
        # Regression (v0.170–v0.174): the map was fed only fixes recorded *after*
        # the race start, so a boat vanished from the chart before the gun and in
        # the first minutes after it. The last known position must always show.
        boat_id = _make_boat()
        track.upsert_tracker("PRESTART", boat_id=boat_id)
        race_id = _make_race(minutes_ago=-30)   # warning signal 30 min in the FUTURE
        _make_entry(race_id, boat_id)
        track.insert_positions([_fix("PRESTART", 52.9, -4.4, time.time() - 30)])
        row = track.race_leaderboard(race_id)[0]
        assert row["lat"] == 52.9 and row["lon"] == -4.4        # on the map
        assert row["age"] is not None and row["age"] < 300      # fix age reported
        assert row["dist_remaining_nm"] > 0                     # useful pre-start "to go"
        assert row["rounded"] == 0                              # progress still start-anchored

    def test_untracked_boats_are_listed(self, client):
        # Every boat entered appears; one without a tracker has no figures so the
        # UI can dash them out and say "No tracker", and sorts after tracked boats.
        race_id = _make_race(course_no=11)
        tracked_boat = _make_boat(name="Tracked", sail="T1")
        plain_boat = _make_boat(name="NoTracker", sail="N1")
        track.upsert_tracker("HAS-TRK", boat_id=tracked_boat)
        _make_entry(race_id, plain_boat, boat_name="NoTracker", sail="N1")
        _make_entry(race_id, tracked_boat, boat_name="Tracked", sail="T1")
        track.insert_positions([_fix("HAS-TRK", 52.9, -4.4, time.time() - 10)])
        board = track.race_leaderboard(race_id)
        assert len(board) == 2                                  # both entries listed
        by_name = {r["boat_name"]: r for r in board}
        assert by_name["Tracked"]["tracked"] is True
        u = by_name["NoTracker"]
        assert u["tracked"] is False
        assert u["rounded"] is None and u["dist_remaining_nm"] is None
        assert u["lat"] is None and u["age"] is None
        assert board[0]["boat_name"] == "Tracked"               # untracked sorts last

    def test_leaderboard_orders_by_progress(self, client):
        race_id = _make_race(gps_finish=1, course_no=11)
        with ro.get_db() as db:
            race = db.execute("SELECT * FROM races WHERE id = ?", (race_id,)).fetchone()
        seq = track.course_rounding_sequence(race)
        t = ro.race_first_start_dt(race).timestamp() + track.MIN_FINISH_ELAPSED_S + 30
        # Boat A rounds 5 marks; boat B rounds 2 — A should lead.
        for uid, name, n in (("IMEI-A", "Ahead", 5), ("IMEI-B", "Behind", 2)):
            bid = _make_boat(name=name, sail=name)
            track.upsert_tracker(uid, boat_id=bid)
            _make_entry(race_id, bid, boat_name=name, sail=name)
            track.insert_positions([_fix(uid, m["lat"], m["lon"], t + i * 10) for i, m in enumerate(seq[:n])])
        board = track.race_leaderboard(race_id)
        assert [r["boat_name"] for r in board] == ["Ahead", "Behind"]
        assert board[0]["position"] == 1 and board[0]["rounded"] > board[1]["rounded"]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
class TestTrackRoutes:
    def test_status_api_shape(self, logged_in_client):
        data = logged_in_client.get("/api/track/status").get_json()
        assert "devices" in data and "server_now" in data

    def test_positions_api(self, logged_in_client):
        boat_id = _make_boat()
        track.upsert_tracker("IMEI-9", boat_id=boat_id)
        race_id = _make_race()
        _make_entry(race_id, boat_id)
        now = time.time()
        track.insert_positions([{"unique_id": "IMEI-9", "device_id": "9", "name": "n", "lat": 52.9, "lon": -4.4, "speed_kn": 4.0, "course_deg": 90.0, "fix_time": now}], retention_days=90)
        data = logged_in_client.get(f"/api/race/{race_id}/positions").get_json()
        assert data["ok"] is True
        assert len(data["boats"]) == 1
        assert data["boats"][0]["lat"] == 52.9 and data["boats"][0]["entry_id"]
        # The response also carries the ordered leaderboard with progress fields.
        assert "leaderboard" in data and len(data["leaderboard"]) == 1
        lb = data["leaderboard"][0]
        assert lb["entry_id"] and "rounded" in lb and "total" in lb and "position" in lb

    def test_positions_api_requires_login(self, client):
        resp = client.get("/api/race/1/positions")
        assert resp.status_code in (401, 404)

    def test_public_positions_needs_no_login(self, client):
        # The public race page shows boat positions, so this endpoint is public.
        from core.horn import hardware_config, save_hardware_config
        save_hardware_config({**hardware_config(), "track_enabled": True})
        boat_id = _make_boat()
        track.upsert_tracker("PUB-1", boat_id=boat_id)
        race_id = _make_race()
        _make_entry(race_id, boat_id)
        track.insert_positions([_fix("PUB-1", 52.9, -4.4, time.time() - 15)])
        data = client.get(f"/public/race/{race_id}/positions").get_json()   # no login
        assert data["ok"] is True and data["enabled"] is True
        assert len(data["boats"]) == 1 and data["boats"][0]["lat"] == 52.9
        assert data["leaderboard"][0]["tracked"] is True

    def test_public_positions_empty_when_tracking_off(self, client):
        from core.horn import hardware_config, save_hardware_config
        save_hardware_config({**hardware_config(), "track_enabled": False})
        race_id = _make_race()
        data = client.get(f"/public/race/{race_id}/positions").get_json()
        assert data["ok"] is True and data["enabled"] is False and data["boats"] == []

    def test_public_positions_unknown_race(self, client):
        assert client.get("/public/race/999999/positions").status_code == 404

    def test_trackers_status_api(self, logged_in_client):
        boat_id = _make_boat()
        track.upsert_tracker("LIVE-1", boat_id=boat_id)
        track.insert_positions([_fix("LIVE-1", 52.9, -4.4, time.time() - 20)])   # green
        track.upsert_tracker("OLD-1", boat_id=_make_boat())
        track.insert_positions([_fix("OLD-1", 52.9, -4.4, time.time() - 7200)])  # red
        data = logged_in_client.get("/api/trackers/status").get_json()
        assert data["ok"] is True and data["total"] == 2
        assert data["report"]["LIVE-1"]["rag"] == "green"
        assert data["report"]["OLD-1"]["rag"] == "red"
        assert data["reporting"] == 1  # only the fresh one counts
        # markers drive the Trackers-page map: both devices have a stored fix
        marks = {m["unique_id"]: m for m in data["markers"]}
        assert marks["LIVE-1"]["lat"] == 52.9 and marks["OLD-1"]["stale"] is True


# ---------------------------------------------------------------------------
# Traccar device management (mocked) + the Trackers page
# ---------------------------------------------------------------------------
_TRACCAR_CFG = {"base_url": "http://x/traccar", "token": "t", "enabled": True, "sim_enabled": False,
                "finish_horn": False, "poll_seconds": 5, "retention_days": 90, "rounding_radius_m": 50}


class TestTraccarManagement:
    """Trackers are only ever adopted from the list Traccar has already seen. The
    form for typing an IMEI by hand went in v0.270: a tracker that has never reached
    Traccar cannot be tracked whatever the app records, and picking one off a list is
    both easier and impossible to mistype."""

    def test_remove_tracker_deletes_device_but_keeps_track(self, client, monkeypatch):
        boat_id = _make_boat()
        now = time.time()
        track.upsert_tracker("IMEI-DEL", boat_id=boat_id, traccar_device_id="99")
        track.insert_positions([_fix("IMEI-DEL", 52.9, -4.4, now - 10)])
        monkeypatch.setattr(track, "track_config", lambda: dict(_TRACCAR_CFG))
        deleted = {}
        monkeypatch.setattr(track, "traccar_delete_device", lambda c, did: deleted.update(id=did))
        ok, _msg = track.remove_tracker("IMEI-DEL")
        assert ok and deleted["id"] == "99"
        assert "IMEI-DEL" not in [t["unique_id"] for t in track.list_trackers()]
        # The boat keeps its past track even though the tracker record is gone.
        assert len(track.positions_for_boat_since(boat_id, now - 3600)) == 1


class TestTrackerReportStatus:
    def test_rag_buckets(self):
        assert track._rag(10) == "green"        # <= 5 min
        assert track._rag(600) == "amber"       # 5 min .. 1 hr
        assert track._rag(4000) == "red"        # > 1 hr
        assert track._rag(None) == "none"

    def test_markers_only_include_configured_trackers(self, client):
        # Position history is kept when a tracker is removed (the boat keeps its
        # track), so the map must filter to trackers that still exist.
        boat_id = _make_boat()
        now = time.time()
        track.upsert_tracker("MAP-1", boat_id=boat_id)
        track.insert_positions([_fix("MAP-1", 52.9, -4.4, now - 20)])
        assert [m["unique_id"] for m in track.tracker_markers()] == ["MAP-1"]
        track.remove_tracker("MAP-1")
        assert track.tracker_markers() == []                       # gone from the map
        assert len(track.positions_for_boat_since(boat_id, now - 3600)) == 1  # history kept

    def test_report_status_ages_and_rag(self, client):
        now = time.time()
        track.insert_positions([
            _fix("FRESH", 52.9, -4.4, now - 30),      # green
            _fix("RECENT", 52.9, -4.4, now - 1200),   # amber (20 min)
            _fix("STALE", 52.9, -4.4, now - 7200),    # red (2 hr)
        ])
        rs = track.tracker_report_status()
        assert rs["FRESH"]["rag"] == "green"
        assert rs["RECENT"]["rag"] == "amber"
        assert rs["STALE"]["rag"] == "red"
        assert "ago" in rs["FRESH"]["text"] or rs["FRESH"]["text"] == "just now"


class TestTrackersPage:
    def _post(self, lic, url, data):
        token = "test-csrf-token"
        with lic.session_transaction() as sess:
            sess["_csrf_token"] = token
        payload = dict(data); payload["_csrf_token"] = token
        return lic.post(url, data=payload)

    def test_page_renders(self, logged_in_client):
        resp = logged_in_client.get("/trackers")
        assert resp.status_code == 200 and b"Trackers" in resp.data

    def test_page_shows_rag_and_age(self, logged_in_client):
        from core.horn import hardware_config, save_hardware_config
        save_hardware_config({**hardware_config(), "track_enabled": True})  # so the summary line shows
        boat_id = _make_boat()
        track.upsert_tracker("RAG-1", boat_id=boat_id)
        track.insert_positions([_fix("RAG-1", 52.9, -4.4, time.time() - 30)])  # fresh -> green
        html = logged_in_client.get("/trackers").get_data(as_text=True)
        assert "rag-green" in html                                    # per-tracker RAG dot
        assert "reporting (a fix within the last hour)" in html       # freshness-aware summary

    def test_adopt_and_remove_via_routes(self, logged_in_client, monkeypatch):
        """Adopting is the only way in since v0.270 — /trackers/add is gone."""
        cfg = dict(_TRACCAR_CFG); cfg["base_url"] = ""; cfg["token"] = ""  # local-only, no network
        monkeypatch.setattr(track, "track_config", lambda: cfg)
        boat_id = _make_boat()
        resp = self._post(logged_in_client, "/trackers/adopt",
                          {"unique_id": "ROUTE-1", "label": "R1", "boat_id": str(boat_id)})
        assert resp.status_code in (200, 302)
        assert "ROUTE-1" in [t["unique_id"] for t in track.list_trackers()]
        resp = self._post(logged_in_client, "/trackers/remove", {"unique_id": "ROUTE-1"})
        assert resp.status_code in (200, 302)
        assert "ROUTE-1" not in [t["unique_id"] for t in track.list_trackers()]

    def test_the_typed_in_add_route_is_gone(self, logged_in_client):
        """Two ways to register a tracker was one too many, and the typed one needed a
        15-digit IMEI read off a label."""
        assert self._post(logged_in_client, "/trackers/add",
                          {"unique_id": "NOPE-1"}).status_code == 404
