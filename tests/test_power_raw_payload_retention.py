"""The raw VE.Direct payload is kept for a couple of days, then dropped.

Reported as a manual backup failing with a 504 at the front door, which worked
with the power history unselected. The backup was not the fault: the archive is
built whole before a byte is sent, and it was carrying a database that was 83%
dead weight.

`power_history.db` held 122,546 samples in 104 MB after fifty days — one every
35 seconds, ~700 bytes of `raw_json` on each. **Nothing reads that column.** The
only reader selects the numeric columns and says so in its docstring. At the
365-day row retention the file was heading for about 761 MB, some 629 MB of it
JSON nobody would ever look at.

So the rows stay for the year and the payload goes after two days: long enough
to be there when the Victron link is misbehaving, short enough that it never
becomes the database.
"""
from __future__ import annotations

import json
import sqlite3
import time

import pytest

from core import power


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "power_history.db"
    monkeypatch.setattr(power, "POWER_DB_PATH", path)
    monkeypatch.setattr(power, "POWER_DB_INITIALIZED", False)
    power.init_power_db()
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    yield conn
    conn.close()


def _add(conn, age_days, payload='{"smartshunt": {"H1": "-30024"}}'):
    conn.execute("INSERT INTO power_samples (sample_time, sample_iso, battery_v, raw_json)"
                 " VALUES (?, ?, ?, ?)",
                 (time.time() - age_days * 86400, "iso", 13.7, payload))
    conn.commit()


class TestOldPayloadsAreDropped:
    def test_a_payload_older_than_the_window_is_cleared(self, db):
        _add(db, age_days=5)
        power.prune_raw_payloads(db, time.time())
        db.commit()
        assert db.execute("SELECT raw_json FROM power_samples").fetchone()["raw_json"] is None

    def test_a_recent_one_is_kept(self, db):
        """Two days is the window because that is when somebody is actually
        looking at why the Victron numbers are wrong."""
        _add(db, age_days=0.5)
        power.prune_raw_payloads(db, time.time())
        db.commit()
        assert db.execute("SELECT raw_json FROM power_samples").fetchone()["raw_json"] is not None

    def test_the_reading_itself_survives(self, db):
        """Only the payload goes. The battery voltage is the point of the row."""
        _add(db, age_days=5)
        power.prune_raw_payloads(db, time.time())
        db.commit()
        row = db.execute("SELECT battery_v, sample_time FROM power_samples").fetchone()
        assert row["battery_v"] == 13.7 and row["sample_time"]

    def test_it_reports_how_many_it_cleared(self, db):
        for _ in range(3):
            _add(db, age_days=9)
        _add(db, age_days=0.1)
        assert power.prune_raw_payloads(db, time.time()) == 3

    def test_running_it_twice_clears_nothing_the_second_time(self, db):
        """It runs on every sample, so it must not rewrite rows it has done."""
        _add(db, age_days=9)
        assert power.prune_raw_payloads(db, time.time()) == 1
        db.commit()
        assert power.prune_raw_payloads(db, time.time()) == 0

    def test_the_window_is_days_not_the_year_the_rows_get(self, db):
        assert 1 <= power.RAW_JSON_RETENTION_DAYS <= 7


class TestStoringASamplePrunesAsItGoes:
    def test_an_old_payload_is_gone_after_the_next_sample_arrives(self, db, monkeypatch):
        _add(db, age_days=30)
        power.insert_power_sample({"battery_v": 13.8}, raw={"smartshunt": {"H1": "1"}},
                                  retention_days=365)
        rows = db.execute("SELECT raw_json FROM power_samples ORDER BY sample_time").fetchall()
        assert rows[0]["raw_json"] is None, "the old payload survived a new sample"
        assert rows[-1]["raw_json"] is not None, "the new sample lost its payload"

    def test_the_row_retention_is_untouched(self, db):
        """Rows still last a year; only the payload has a shorter life."""
        _add(db, age_days=400)
        power.insert_power_sample({"battery_v": 13.8}, raw={}, retention_days=365)
        assert db.execute("SELECT COUNT(*) FROM power_samples").fetchone()[0] == 1


class TestTheFileIsActuallyReclaimed:
    """Nulling a column frees the space inside its pages; it does not shrink the
    file, and `freelist_count` cannot see that it happened.

    Measured on the club's own database: after clearing all 122,546 payloads the
    freelist was still **zero** and the file still **104 MB**. A first attempt at
    this vacuumed only when the freelist passed a threshold, so it would never
    have run at all. VACUUM took 0.2 seconds and gave back 104 MB -> 14.6 MB
    with every row intact, so there is nothing to detect and nothing to tune:
    do it once a day.
    """

    def _fill(self, conn, rows, payload_bytes=700):
        blob = json.dumps({"x": "y" * payload_bytes})
        for i in range(rows):
            conn.execute("INSERT INTO power_samples (sample_time, sample_iso, battery_v, raw_json)"
                         " VALUES (?, ?, ?, ?)", (time.time() - 30 * 86400 + i, "iso", 13.7, blob))
        conn.commit()

    def test_clearing_payloads_does_not_shrink_the_file_on_its_own(self, db):
        """The finding that made the first attempt useless."""
        self._fill(db, 400)
        before = db.execute("PRAGMA page_count").fetchone()[0]
        power.prune_raw_payloads(db, time.time())
        db.commit()
        assert db.execute("PRAGMA freelist_count").fetchone()[0] == 0
        assert db.execute("PRAGMA page_count").fetchone()[0] == before

    def test_the_vacuum_is_what_shrinks_it(self, db, monkeypatch):
        monkeypatch.setattr(power, "_LAST_VACUUM_AT", 0.0)
        self._fill(db, 400)
        power.prune_raw_payloads(db, time.time())
        db.commit()
        before = db.execute("PRAGMA page_count").fetchone()[0]
        assert power.vacuum_power_db_daily(db, time.time()) is True
        assert db.execute("PRAGMA page_count").fetchone()[0] < before

    def test_every_row_survives_it(self, db, monkeypatch):
        monkeypatch.setattr(power, "_LAST_VACUUM_AT", 0.0)
        self._fill(db, 200)
        power.prune_raw_payloads(db, time.time())
        db.commit()
        power.vacuum_power_db_daily(db, time.time())
        assert db.execute("SELECT COUNT(*) FROM power_samples").fetchone()[0] == 200

    def test_it_will_not_run_again_the_same_day(self, db, monkeypatch):
        """It is called from the sampling loop, every thirty-five seconds."""
        now = time.time()
        monkeypatch.setattr(power, "_LAST_VACUUM_AT", now - 60)
        assert power.vacuum_power_db_daily(db, now) is False

    def test_and_runs_again_the_next_day(self, db, monkeypatch):
        now = time.time()
        monkeypatch.setattr(power, "_LAST_VACUUM_AT", now - power.VACUUM_MIN_INTERVAL_S - 1)
        assert power.vacuum_power_db_daily(db, now) is True

    def test_a_failure_does_not_stop_the_sampling(self, db, monkeypatch):
        """This runs on the daemon that keeps the hut's power readings."""
        monkeypatch.setattr(power, "_LAST_VACUUM_AT", 0.0)

        class Broken:
            def execute(self, *a, **k):
                raise sqlite3.OperationalError("database is locked")
            def commit(self):
                pass

        assert power.vacuum_power_db_daily(Broken(), time.time()) is False

    def test_and_is_not_retried_every_sample_after_one(self, db, monkeypatch):
        monkeypatch.setattr(power, "_LAST_VACUUM_AT", 0.0)

        class Broken:
            def execute(self, *a, **k):
                raise sqlite3.OperationalError("database is locked")
            def commit(self):
                pass

        power.vacuum_power_db_daily(Broken(), time.time())
        assert power._LAST_VACUUM_AT > 0, "a failing vacuum would be attempted on every sample"
