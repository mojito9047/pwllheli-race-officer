"""The hut answering while trackers report and a fleet of pages reload (v1.015).

18 October 2026: at each fleet's finishes the hut's task queue reached 54, and
tracker pushes failed with "database is locked" after waiting their full 30 s.
Three things added up. Every pushed fix read the whole (unique_id, fix_time)
index to find each device's newest fix -- 36 ms on a desktop, several times
that on the hut, holding the database each time. Every waiting push held one
of sixteen request threads for up to 30 s. And every page open on the race
reloaded in the same second at each finish, each one re-walking every boat's
track from the gun.
"""
from __future__ import annotations

import sqlite3
import threading
import time
from datetime import datetime

import pytest

import app as ro
import core.track as track
from core.horn import hardware_config, save_hardware_config

TOKEN = "test-ingest-token"


def _fix(unique_id="SIM-1", when=None, device_id=7):
    when = when or datetime.now()
    return {
        "position": {"deviceId": device_id, "protocol": "osmand",
                     "fixTime": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(when.timestamp())),
                     "latitude": 52.88, "longitude": -4.40, "speed": 5.0, "course": 270.0},
        "device": {"id": device_id, "uniqueId": unique_id, "name": f"Boat {unique_id}"},
    }


def _store(uid, fix_time):
    track.init_track_db()
    with track.get_track_db() as db:
        db.execute("INSERT INTO track_positions (unique_id, lat, lon, fix_time, server_time) VALUES (?, 52.88, -4.40, ?, ?)",
                   (uid, fix_time, time.time()))
        db.commit()


class TestTheNewestFix:
    def test_the_index_answer_is_the_group_by_answer(self, client):
        now = time.time()
        for uid, times in {"A": [now - 30, now - 20], "B": [now - 50], "C": [now - 40, now - 10, now - 5]}.items():
            for t in times:
                _store(uid, t)
        with track.get_track_db() as db:
            expected = {r[0]: r[1] for r in db.execute(
                "SELECT unique_id, MAX(fix_time) FROM track_positions GROUP BY unique_id")}
        assert track._latest_fix_times() == expected
        assert track._latest_fix_times({"C", "nobody"}) == {"C": expected["C"]}

    def test_it_reads_the_index_rather_than_scanning_it(self, client):
        track.init_track_db()
        with track.get_track_db() as db:
            plan = " ".join(str(r[-1]) for r in db.execute(
                "EXPLAIN QUERY PLAN SELECT MAX(fix_time) FROM track_positions WHERE unique_id = ?", ("A",)))
        assert "SEARCH" in plan and "idx_track_positions_dev_time" in plan, plan

    def test_a_push_still_drops_a_fix_it_already_has(self, client):
        now = time.time()
        _store("A", now - 10)
        assert track.insert_positions([{"unique_id": "A", "fix_time": now - 20, "lat": 1.0, "lon": 1.0}]) == 0
        assert track.insert_positions([{"unique_id": "A", "fix_time": now - 5, "lat": 1.0, "lon": 1.0}]) == 1


class TestAPushThatCannotWait:
    def test_a_busy_database_is_answered_at_once_and_resent(self, client, monkeypatch):
        save_hardware_config({**hardware_config(), "track_enabled": True, "track_ingest_secret": TOKEN})
        monkeypatch.setattr(track, "INGEST_BUSY_TIMEOUT_S", 0.3)
        track.init_track_db()
        holder = sqlite3.connect(track.TRACK_DB_PATH, timeout=1)
        holder.execute("BEGIN EXCLUSIVE")
        try:
            began = time.monotonic()
            resp = client.post("/api/track/ingest", json=_fix("SIM-9"), headers={"X-RO-Track-Token": TOKEN})
            took = time.monotonic() - began
        finally:
            holder.rollback()
            holder.close()
        assert resp.status_code == 503 and resp.headers.get("Retry-After") == "1"
        assert took < 3, f"the push held its thread for {took:.1f} s"
        # And when the relay sends it again, it goes in.
        again = client.post("/api/track/ingest", json=_fix("SIM-9"), headers={"X-RO-Track-Token": TOKEN})
        assert again.status_code == 200 and again.get_json()["stored"] == 1


class TestOneBoardForEveryone:
    @pytest.fixture()
    def race_id(self, client, monkeypatch):
        monkeypatch.setattr(track, "_SHARED_RESULTS", {})
        monkeypatch.setattr(track, "_SHARED_LOCKS", {})
        with ro.get_db() as db:
            cur = db.execute("INSERT INTO races (name, class_name, course_no, start_time, rating_rule, notes, created_at)"
                             " VALUES ('Shared', 'IRC', 1, '2026-10-18T10:30:00', 'DUAL', '', '2026-10-18T09:00:00')")
            db.commit()
            rid = int(cur.lastrowid)
        track.init_track_db()
        self.calls = []

        def computed(race_id, at_ts=None):
            self.calls.append(at_ts)
            time.sleep(0.05)
            return [{"entry_id": 1, "boat_name": "X", "lat": 52.0, "lon": -4.0}]
        monkeypatch.setattr(track, "_race_leaderboard_computed", computed)
        return rid

    def test_many_askers_one_walk(self, race_id):
        for _ in range(5):
            assert track.race_leaderboard(race_id)[0]["boat_name"] == "X"
        assert len(self.calls) == 1

    def test_at_a_finish_they_ask_at_once_and_still_one_walk(self, race_id):
        threads = [threading.Thread(target=track.race_leaderboard, args=(race_id,)) for _ in range(12)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(self.calls) == 1

    def test_a_new_fix_is_a_new_board(self, race_id):
        track.race_leaderboard(race_id)
        _store("A", time.time())
        track.race_leaderboard(race_id)
        assert len(self.calls) == 2

    def test_a_finish_recorded_is_a_new_board(self, race_id):
        with ro.get_db() as db:
            db.execute("INSERT INTO entries (race_id, boat_name, sail_no, class_name, status) VALUES (?, 'X', '1', 'IRC', 'RACING')",
                       (race_id,))
            db.commit()
        track.race_leaderboard(race_id)
        with ro.get_db() as db:
            db.execute("UPDATE entries SET status = 'FINISHED' WHERE race_id = ?", (race_id,))
            db.commit()
        track.race_leaderboard(race_id)
        assert len(self.calls) == 2

    def test_it_does_not_outlive_the_clock(self, race_id, monkeypatch):
        track.race_leaderboard(race_id)
        monkeypatch.setattr(track, "SHARED_RESULT_TTL_S", 0.0)
        track.race_leaderboard(race_id)
        assert len(self.calls) == 2

    def test_a_replay_moment_is_the_askers_own(self, race_id):
        track.race_leaderboard(race_id, at_ts=1000.0)
        track.race_leaderboard(race_id, at_ts=1000.0)
        assert self.calls == [1000.0, 1000.0]

    def test_one_askers_changes_do_not_reach_the_next(self, race_id):
        board = track.race_leaderboard(race_id)
        board[0]["boat_name"] = "changed"
        assert track.race_leaderboard(race_id)[0]["boat_name"] == "X"

    def test_the_whole_track_is_shared_and_a_poll_is_not(self, race_id, monkeypatch):
        seen = []
        monkeypatch.setattr(track, "_race_track_history_computed",
                            lambda race_id, since_ts=None: seen.append(since_ts) or {"boats": [], "since": since_ts})
        track.race_track_history(race_id)
        track.race_track_history(race_id)
        track.race_track_history(race_id, since_ts=5.0)
        track.race_track_history(race_id, since_ts=5.0)
        assert seen == [None, 5.0, 5.0]


class TestPagesDoNotAllReloadTogether:
    def test_the_reload_is_spread_out(self):
        page = open("templates/competitor_race.html", encoding="utf-8").read()
        assert "setTimeout(function () { window.location.replace(next.toString()); }, Math.random() * 3000)" in page
