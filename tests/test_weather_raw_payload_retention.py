"""The raw weather payload is kept for a couple of days, then dropped.

The same thing the power monitor was doing, in the *race* database. Ambient
samples are purged after 24 hours, but samples inside a race window are kept
indefinitely on purpose -- they are the wind a race was sailed in. Their raw
payloads were kept with them, up to 20 KB a row and read by nothing.

On the club's own database that was **39.5 MB of a 49.8 MB race database, 79%**,
across 47,096 rows. Nulling the payloads and vacuuming takes it to 5.9 MB with
every row and every wind reading still there.

It matters less to a backup than the power database did -- JSON compresses well,
so 49.8 MB was already only 2.8 MB zipped against power's 9.7 MB -- and more to
everything that reads or restores the race database.

The vacuum is the careful part. This is the live race database, and VACUUM takes
an exclusive lock.
"""
from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timedelta

import pytest

import app as ro
from core import weather_store


def _add(db, age_hours, payload='{"wind": {"twd": 177}}', twd=177.0):
    db.execute("INSERT INTO weather_samples (sample_time, sample_iso, twd, tws_kt, gust_kt,"
               " source, raw_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
               (time.time() - age_hours * 3600, "iso", twd, 8.0, 12.0, "test", payload))
    db.commit()


class TestOldPayloadsAreDropped:
    def test_a_payload_older_than_the_window_is_cleared(self, client):
        with ro.get_db() as db:
            _add(db, age_hours=24 * 5)
            weather_store.prune_weather_raw_payloads(db, time.time())
            db.commit()
            assert db.execute("SELECT raw_json FROM weather_samples").fetchone()["raw_json"] is None

    def test_a_recent_one_is_kept(self, client):
        with ro.get_db() as db:
            _add(db, age_hours=6)
            weather_store.prune_weather_raw_payloads(db, time.time())
            db.commit()
            assert db.execute("SELECT raw_json FROM weather_samples").fetchone()["raw_json"] is not None

    def test_the_wind_itself_survives(self, client):
        """This is the point. A race's wind is evidence and is kept forever;
        the payload it arrived in is not."""
        with ro.get_db() as db:
            _add(db, age_hours=24 * 30, twd=212.0)
            weather_store.prune_weather_raw_payloads(db, time.time())
            db.commit()
            row = db.execute("SELECT twd, tws_kt, gust_kt, sample_time FROM weather_samples").fetchone()
            assert row["twd"] == 212.0 and row["tws_kt"] == 8.0 and row["gust_kt"] == 12.0

    def test_running_it_twice_clears_nothing_the_second_time(self, client):
        """It runs on every weather sample."""
        with ro.get_db() as db:
            _add(db, age_hours=24 * 5)
            assert weather_store.prune_weather_raw_payloads(db, time.time()) == 1
            db.commit()
            assert weather_store.prune_weather_raw_payloads(db, time.time()) == 0

    def test_the_window_is_days_not_the_forever_the_rows_get(self):
        assert 1 <= weather_store.RAW_JSON_RETENTION_DAYS <= 7


class TestTheVacuumWaitsForAQuietDay:
    """VACUUM takes an exclusive lock on the database the start sequence writes
    to. A tenth of a second is nothing until it lands between the preparatory
    signal and the gun."""

    def _race_at(self, db, when):
        db.execute("INSERT INTO races (name, course_no, start_time, created_at)"
                   " VALUES ('Vac', 1, ?, ?)",
                   (when.isoformat(timespec="seconds"), datetime.now().isoformat(timespec="seconds")))
        db.commit()

    def test_it_runs_when_nothing_is_happening(self, client, monkeypatch):
        monkeypatch.setattr(weather_store, "_LAST_VACUUM_AT", 0.0)
        with ro.get_db() as db:
            assert weather_store.vacuum_race_db_daily(db, time.time()) is True

    def test_it_waits_while_a_race_is_near(self, client, monkeypatch):
        monkeypatch.setattr(weather_store, "_LAST_VACUUM_AT", 0.0)
        with ro.get_db() as db:
            self._race_at(db, datetime.now() + timedelta(minutes=20))
            assert weather_store.vacuum_race_db_daily(db, time.time()) is False

    def test_and_while_a_boat_is_still_out(self, client, monkeypatch):
        monkeypatch.setattr(weather_store, "_LAST_VACUUM_AT", 0.0)
        with ro.get_db() as db:
            self._race_at(db, datetime.now() - timedelta(days=3))
            db.execute("INSERT INTO entries (race_id, boat_name, status)"
                       " VALUES ((SELECT MAX(id) FROM races), 'Late', 'RACING')")
            db.commit()
            assert weather_store.vacuum_race_db_daily(db, time.time()) is False

    def test_a_race_long_finished_does_not_block_it(self, client, monkeypatch):
        monkeypatch.setattr(weather_store, "_LAST_VACUUM_AT", 0.0)
        with ro.get_db() as db:
            self._race_at(db, datetime.now() - timedelta(days=3))
            assert weather_store.vacuum_race_db_daily(db, time.time()) is True

    def test_waiting_for_racing_does_not_burn_the_daily_slot(self, client, monkeypatch):
        """If it skipped *and* stamped the clock, a Saturday of racing would cost
        the whole day and the database would never be reclaimed in season."""
        monkeypatch.setattr(weather_store, "_LAST_VACUUM_AT", 0.0)
        with ro.get_db() as db:
            self._race_at(db, datetime.now() + timedelta(minutes=20))
            weather_store.vacuum_race_db_daily(db, time.time())
        assert weather_store._LAST_VACUUM_AT == 0.0

    def test_it_will_not_run_again_the_same_day(self, client, monkeypatch):
        now = time.time()
        monkeypatch.setattr(weather_store, "_LAST_VACUUM_AT", now - 60)
        with ro.get_db() as db:
            assert weather_store.vacuum_race_db_daily(db, now) is False

    def test_it_will_not_take_a_lock_it_cannot_reason_about(self, monkeypatch):
        """If the racing check itself fails, the answer is no."""
        class Broken:
            def execute(self, *a, **k):
                raise sqlite3.OperationalError("no such table: entries")
        assert weather_store.racing_now(Broken()) is True

    def test_a_failed_vacuum_does_not_stop_the_weather_poller(self, client, monkeypatch):
        monkeypatch.setattr(weather_store, "_LAST_VACUUM_AT", 0.0)
        monkeypatch.setattr(weather_store, "racing_now", lambda db: False)

        class Broken:
            def execute(self, *a, **k):
                raise sqlite3.OperationalError("database is locked")
            def commit(self):
                pass

        assert weather_store.vacuum_race_db_daily(Broken(), time.time()) is False
        assert weather_store._LAST_VACUUM_AT > 0


class TestStoringASamplePrunesAsItGoes:
    def test_an_old_payload_is_gone_after_the_next_sample(self, client, monkeypatch):
        """The race-window rows are the ones that live forever, so they are the
        ones that were carrying the payloads."""
        with ro.get_db() as db:
            _add(db, age_hours=24 * 10)
            kept_id = db.execute("SELECT MAX(id) FROM weather_samples").fetchone()[0]
        # Keep that old row alive by pretending it is inside a race window.
        monkeypatch.setattr(weather_store, "sample_time_in_any_window", lambda t, w: True)
        weather_store.insert_weather_sample(180.0, 9.0, 13.0, "test", {"wind": {}})
        with ro.get_db() as db:
            row = db.execute("SELECT raw_json, twd FROM weather_samples WHERE id = ?", (kept_id,)).fetchone()
            assert row is not None, "the race-window sample was deleted, not just cleared"
            assert row["raw_json"] is None
            assert row["twd"] == 177.0
