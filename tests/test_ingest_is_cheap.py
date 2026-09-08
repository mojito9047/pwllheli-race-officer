"""What a pushed position costs the server.

The live system started refusing connections mid-race: Waitress reporting a task
queue climbing past 25 and "total open connections reached the connection limit".
The relay pushes every fix to /api/track/ingest, and that request was doing two
expensive things nobody had priced.

* ``insert_positions`` purged old rows on **every call**. The purge filters on
  ``fix_time``, which no index covered, so each pushed fix scanned the whole
  table — inside a write transaction. This database is deliberately in rollback
  journal mode rather than WAL (see core/db.py), where a writer excludes every
  reader, so each push locked out the clubhouse display, the competitor pages and
  the race sheet, all of which poll.
* ``run_finish_detection`` re-walked every boat in every armed race. A boat
  cannot finish on another boat's fix, so all but one of those walks re-derived a
  result nothing had changed.

Detection stays inline — the horn should fire on the fix that crossed the line,
not on the next poll — but only for the trackers that just reported.
"""
from __future__ import annotations

import inspect
import pathlib
import re
import sqlite3
import time

import pytest

from core import track

TRACK_SRC = pathlib.Path("core/track.py").read_text(encoding="utf-8")


def source_of(name):
    """The function's source as written in the file.

    Not inspect.getsource: conftest stubs the background loop out with a lambda
    for the whole test session, so asking the module object returns the stub and
    the assertion passes or fails on the wrong text entirely.
    """
    m = re.search(rf"^def {name}\(.*?(?=^def |\Z)", TRACK_SRC, re.S | re.M)
    assert m, f"{name} not found in core/track.py"
    return m.group(0)


class TestThePurgeIsOffTheIngestPath:
    def test_insert_positions_does_not_delete(self):
        src = inspect.getsource(track.insert_positions)
        assert "DELETE" not in src.upper(), (
            "insert_positions purges again — that is a table scan and an exclusive "
            "write lock on every pushed fix"
        )

    def test_there_is_a_purge_to_call_instead(self):
        assert callable(track.purge_old_positions)

    def test_the_background_loop_calls_it(self):
        assert "purge_old_positions" in source_of("track_background_loop")

    def test_it_only_runs_once_an_hour(self, client):
        track.purge_old_positions(retention_days=3650, force=True)
        assert track.purge_old_positions(retention_days=3650) == 0, (
            "a second purge inside the interval must be a no-op — it takes the "
            "exclusive lock"
        )

    def test_forcing_it_works_regardless(self, client):
        track.purge_old_positions(retention_days=3650, force=True)
        assert track.purge_old_positions(retention_days=3650, force=True) == 0  # nothing old

    def test_it_actually_removes_old_rows(self, client):
        old_t = time.time() - 400 * 86400
        track.insert_backfilled_positions([{
            "unique_id": "PURGE-1", "name": "P", "lat": 52.88, "lon": -4.40,
            "speed_kn": 0.0, "course_deg": 0.0, "fix_time": old_t}])
        with track.get_track_db() as db:
            before = db.execute("SELECT COUNT(*) c FROM track_positions "
                                "WHERE unique_id='PURGE-1'").fetchone()["c"]
        assert before == 1
        track.purge_old_positions(retention_days=90, force=True)
        with track.get_track_db() as db:
            after = db.execute("SELECT COUNT(*) c FROM track_positions "
                               "WHERE unique_id='PURGE-1'").fetchone()["c"]
        assert after == 0

    def test_a_recent_fix_survives_the_purge(self, client):
        track.insert_backfilled_positions([{
            "unique_id": "PURGE-2", "name": "P", "lat": 52.88, "lon": -4.40,
            "speed_kn": 0.0, "course_deg": 0.0, "fix_time": time.time()}])
        track.purge_old_positions(retention_days=90, force=True)
        with track.get_track_db() as db:
            assert db.execute("SELECT COUNT(*) c FROM track_positions "
                              "WHERE unique_id='PURGE-2'").fetchone()["c"] == 1


class TestThePurgeCanUseAnIndex:
    def test_fix_time_is_indexed(self, client):
        track.init_track_db()
        with track.get_track_db() as db:
            idx = {r["name"] for r in db.execute(
                "SELECT name FROM sqlite_master WHERE type='index'").fetchall()}
        assert "idx_track_positions_time" in idx

    def test_the_delete_no_longer_scans(self, client):
        track.init_track_db()
        with track.get_track_db() as db:
            plan = " ".join(str(r[-1]) for r in db.execute(
                "EXPLAIN QUERY PLAN DELETE FROM track_positions WHERE fix_time < 0"))
        assert "SCAN" not in plan.upper(), f"still scanning: {plan}"


class TestDetectionIsScopedToWhatReported:
    def test_it_takes_a_device_filter(self):
        assert "only_devices" in inspect.signature(track.run_finish_detection).parameters

    def test_the_ingest_path_passes_one(self):
        src = inspect.getsource(track.ingest_forwarded_positions)
        assert "only_devices" in src, (
            "the request thread is re-walking every boat in every armed race on "
            "every pushed fix"
        )

    def test_detection_still_runs_inline(self):
        """Deliberately not moved to the poller: the horn should fire on the fix
        that crossed the line, not up to a poll interval later."""
        assert "run_finish_detection" in inspect.getsource(track.ingest_forwarded_positions)

    def test_an_empty_device_set_does_no_work(self, client):
        assert track.run_finish_detection(only_devices=[]) == 0

    def test_the_poller_still_sweeps_everything(self):
        """The background loop passes no filter, so a boat whose tracker has gone
        quiet is still checked."""
        assert "run_finish_detection()" in source_of("track_background_loop")


class TestTheSlowRequestLog:
    def test_it_can_be_turned_off(self, monkeypatch):
        from core import slowlog
        monkeypatch.setattr(slowlog, "THRESHOLD_MS", 0)
        calls = []

        class FakeApp:
            def before_request(self, f): calls.append(f)
            def after_request(self, f): calls.append(f)

        slowlog.install(FakeApp())
        assert calls == [], "a zero threshold must install nothing at all"

    def test_a_slow_request_is_recorded(self, tmp_path, monkeypatch):
        import logging
        from core import appstate, slowlog
        monkeypatch.setattr(appstate, "RUNTIME_DIR", tmp_path)
        monkeypatch.setattr(slowlog, "_LOGGER", None)
        # The logging module keeps one logger object per name for the whole
        # session, so a handler built by an earlier test still points at that
        # test's tmp_path — and _get_logger, seeing a file handler already
        # attached, adds none. The line then lands in the previous directory and
        # this reads an empty one. Detach first.
        real = logging.getLogger("pwllheli.slow")
        for h in list(real.handlers):
            real.removeHandler(h)
            h.close()

        slowlog.log_slow("GET", "/api/race/1/positions", 200, 812.5)
        for h in slowlog._get_logger().handlers:
            h.flush()
        from core import logfiles
        text = logfiles.dated_path(tmp_path / "logs", "slow",
                                   logfiles.today()).read_text(encoding="utf-8")
        assert "/api/race/1/positions" in text and "812" in text and "GET" in text

    def test_it_never_raises(self, monkeypatch):
        from core import slowlog
        monkeypatch.setattr(slowlog, "_get_logger", lambda: None)
        slowlog.log_slow("GET", "/x", 200, 1.0)      # must not raise
