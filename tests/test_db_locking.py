"""A finish must not be lost to a database lock.

Reported from the hut, mid-race: a manual-horn clip wedged FFmpeg for its full
180-second timeout, and with the disk saturated the race officer's "Finish now"
came back as ``sqlite3.OperationalError: database is locked``. A 500 page, no
finish recorded, and the app had to be restarted.

FFmpeg was the trigger; the defect was that ``sqlite3.connect()`` was called with
no ``timeout``, so every connection used the library default of **five seconds**
and then gave up. Measured on a copy of the hut database: a write that has to wait
eight seconds fails at five, and succeeds if it is simply allowed to wait.

Waiting is always better than failing for this write. Nothing the race office does
is so urgent that erroring out beats arriving late, and a finish that was never
recorded is the one thing this app cannot afford to lose.

WAL would also help — in the default rollback mode a reader blocks a writer, so
the clubhouse display or a backup snapshot can hold up a finish — but it is not
switched on, and ``TestWhyWalIsNotOnYet`` is the measurement of why not.
"""
from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

import pytest

import app as ro
from core import appstate, backup as backup_core, db as core_db, power, track


class TestConnectionsAreOpenedToWaitNotToFail:
    def test_the_race_database_waits_far_longer_than_five_seconds(self, client):
        """Five seconds was the library default nobody chose."""
        assert core_db.DB_BUSY_TIMEOUT_SECONDS >= 20
        conn = core_db.get_db()
        try:
            assert conn.execute("PRAGMA busy_timeout").fetchone()[0] >= 20_000
        finally:
            conn.close()

    def test_the_track_database_does_too(self, client):
        """It holds the evidence behind GPS finishes; losing a fix to a lock is
        the same mistake in a quieter place."""
        conn = track.get_track_db()
        try:
            assert conn.execute("PRAGMA busy_timeout").fetchone()[0] >= 20_000
        finally:
            conn.close()

    def test_and_the_power_database(self, client):
        conn = power.get_power_db()
        try:
            assert conn.execute("PRAGMA busy_timeout").fetchone()[0] >= 20_000
        finally:
            conn.close()

    def test_the_timeout_can_be_raised_on_a_slow_hut_pc(self, monkeypatch):
        """Env-var override, so a genuinely struggling PC does not need a rebuild.

        Read through a function rather than bound at import on purpose: reloading
        core.db to pick up a new constant would drop the schema initialiser app.py
        registers on it, and other modules hold direct references to its functions.
        """
        monkeypatch.setenv(core_db.DB_TIMEOUT_ENV_VAR, "45")
        assert core_db.busy_timeout_seconds() == 45.0

    def test_a_nonsense_override_falls_back_rather_than_crashing_startup(self, monkeypatch):
        monkeypatch.setenv(core_db.DB_TIMEOUT_ENV_VAR, "soon")
        assert core_db.busy_timeout_seconds() == core_db.DB_BUSY_TIMEOUT_SECONDS

    def test_it_cannot_be_set_back_below_the_default_that_caused_the_bug(self, monkeypatch):
        monkeypatch.setenv(core_db.DB_TIMEOUT_ENV_VAR, "1")
        assert core_db.busy_timeout_seconds() >= 5.0


class TestAFinishOutlastsALockedDatabase:
    """The reported failure, reproduced and then prevented.

    Eight seconds of contention is what an I/O-stalled hut PC looks like — a
    wedged FFmpeg saturating the disk while something else commits. At the old
    five-second default the finish is lost; allowed to wait, it is recorded.
    """

    HOLD_SECONDS = 8.0

    def _hold_the_database(self, path, release_after):
        """Hold a write lock, releasing on a timer of its own."""
        def hold():
            conn = sqlite3.connect(path, timeout=30)
            conn.execute("BEGIN EXCLUSIVE")
            conn.execute("UPDATE app_settings SET value = value "
                         "WHERE key = (SELECT MIN(key) FROM app_settings)")
            time.sleep(release_after)
            conn.commit()
            conn.close()
        thread = threading.Thread(target=hold, daemon=True)
        thread.start()
        return thread

    def test_the_old_five_second_default_loses_the_finish(self, client):
        """Pins the bug itself, so the timeout cannot quietly go back."""
        ro._init_db_uncached()
        holder = self._hold_the_database(appstate.DB_PATH, self.HOLD_SECONDS)
        time.sleep(0.4)
        try:
            impatient = sqlite3.connect(appstate.DB_PATH, timeout=5.0)   # the old behaviour
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                impatient.execute("UPDATE entries SET finish_time = 'x' WHERE id = -1")
                impatient.commit()
            impatient.close()
        finally:
            holder.join(timeout=20)

    def test_allowed_to_wait_the_finish_is_recorded(self, client):
        ro._init_db_uncached()
        with ro.get_db() as db:
            race_id = db.execute(
                "INSERT INTO races (name, course_no, start_time, rating_rule, created_at) "
                "VALUES ('Lock Test', 1, '2026-08-03T13:00:00', 'IRC_TCC', '2026-08-03T12:00:00')").lastrowid
            entry_id = db.execute("INSERT INTO entries (race_id, boat_name, status) VALUES (?, 'Boat', 'RACING')",
                                  (race_id,)).lastrowid
            db.commit()

        holder = self._hold_the_database(appstate.DB_PATH, self.HOLD_SECONDS)
        time.sleep(0.4)
        try:
            started = time.time()
            conn = core_db.get_db()
            conn.execute("UPDATE entries SET finish_time = '13:52:10', status = 'FINISHED' WHERE id = ?",
                         (entry_id,))
            conn.commit()
            conn.close()
            waited = time.time() - started
        finally:
            holder.join(timeout=20)

        assert waited > 5, "the lock did not outlast the old default, so this proves nothing"
        with ro.get_db() as db:
            row = db.execute("SELECT finish_time, status FROM entries WHERE id = ?", (entry_id,)).fetchone()
        assert row["finish_time"] == "13:52:10"
        assert row["status"] == "FINISHED"


class TestWhyWalIsNotOnYet:
    """WAL is the better answer and is deliberately not used. This records why, so
    it is not switched on by someone reading the SQLite documentation.

    A restore overwrites race_officer.db in place. In WAL a live ``-wal`` beside it
    is replayed over the file just written, and the rows that come back are the OLD
    ones — a restore that reports success and restores nothing. Deleting the
    sidecar does not rescue it on Windows: it cannot be removed while any
    connection holds the database open, and this app leaks connections because
    ``with get_db() as db`` is a transaction context manager, not a closer.
    """

    def test_a_stale_wal_overrides_a_restored_database_file(self, tmp_path):
        """The measurement behind the decision. If SQLite ever stops replaying a
        mismatched WAL, this fails and WAL can be reconsidered."""
        live = tmp_path / "live.db"
        conn = sqlite3.connect(live)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE entries (id INTEGER PRIMARY KEY, boat TEXT)")
        conn.execute("INSERT INTO entries VALUES (1, 'OLD')")
        conn.commit()
        conn.close()
        conn = sqlite3.connect(live)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("INSERT INTO entries VALUES (2, 'ALSO OLD')")
        conn.commit()                       # left uncheckpointed, connection still open

        backup = tmp_path / "backup.db"
        b = sqlite3.connect(backup)
        b.execute("CREATE TABLE entries (id INTEGER PRIMARY KEY, boat TEXT)")
        b.execute("INSERT INTO entries VALUES (99, 'RESTORED')")
        b.commit()
        b.close()

        live.write_bytes(backup.read_bytes())        # what restore_data_backup_zip does
        conn.close()

        check = sqlite3.connect(live)
        boats = {r[0] for r in check.execute("SELECT boat FROM entries").fetchall()}
        check.close()
        assert "OLD" in boats, "a stale -wal no longer overrides the restored file; revisit WAL"

    def test_a_sidecar_cannot_be_deleted_while_the_database_is_open(self, tmp_path):
        """Why the obvious mitigation does not work on Windows."""
        db = tmp_path / "held.db"
        conn = sqlite3.connect(db)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE t (a)")
        conn.execute("INSERT INTO t VALUES (1)")
        conn.commit()
        wal = Path(str(db) + "-wal")
        assert wal.exists()
        try:
            with pytest.raises(OSError):
                wal.unlink()
        finally:
            conn.close()

    def test_a_fresh_database_is_not_in_wal(self, client):
        """Until the restore path is reworked, a new database stays in rollback
        mode. This fails the day someone turns WAL on without the rest."""
        ro._init_db_uncached()
        conn = core_db.get_db()
        try:
            assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() != "wal"
        finally:
            conn.close()


class TestRestoreClearsStaleJournals:
    def test_a_journal_left_by_a_crash_is_removed_with_the_restore(self, tmp_path):
        """It belongs to the old file, so SQLite would roll it back over the one
        just restored. This PC loses power regularly, so it is not theoretical."""
        db = tmp_path / "race_officer.db"
        db.write_bytes(b"restored database bytes")
        for suffix in ("-journal", "-wal", "-shm"):
            (tmp_path / f"race_officer.db{suffix}").write_bytes(b"stale")

        removed = backup_core.clear_sqlite_sidecars(db)

        for suffix in ("-journal", "-wal", "-shm"):
            assert not (tmp_path / f"race_officer.db{suffix}").exists()
        assert db.read_bytes() == b"restored database bytes", "the database itself must not be touched"
        assert len(removed) == 3

    def test_every_database_section_is_covered(self, client):
        """Derived from the catalogue, so a fourth database is covered without
        anyone remembering to update a hand-written list."""
        assert backup_core.database_backup_section_ids() == {"database", "tracks", "power_history"}

    def test_a_restore_still_replaces_the_database(self, client, tmp_path, monkeypatch):
        """End to end: the rows after a restore are the backup's, not the live ones."""
        root = tmp_path / "data"
        for name, value in (("DATA_DIR", root), ("POLARS_DIR", root / "polars"),
                            ("SAIL_CHARTS_DIR", root / "sailcharts"), ("BRANDING_DIR", root / "branding"),
                            ("VIDEO_CLIPS_DIR", root / "video_clips"),
                            ("LEGACY_VIDEO_CLIPS_DIR", root / "video" / "clips")):
            Path(value).mkdir(parents=True, exist_ok=True)
            monkeypatch.setattr(appstate, name, Path(value))

        ro._init_db_uncached()
        with ro.get_db() as db:
            db.execute("INSERT INTO boats (boat_name, created_at, updated_at) "
                       "VALUES ('IN THE BACKUP', '2026-08-03T12:00:00', '2026-08-03T12:00:00')")
            db.commit()
        zip_path, _ = ro.create_data_backup_zip(["database"])

        with ro.get_db() as db:
            db.execute("DELETE FROM boats WHERE boat_name = 'IN THE BACKUP'")
            db.execute("INSERT INTO boats (boat_name, created_at, updated_at) "
                       "VALUES ('AFTER THE BACKUP', '2026-08-03T13:00:00', '2026-08-03T13:00:00')")
            db.commit()

        class _Upload:
            filename = "b.zip"

            def save(self, dest):
                Path(dest).write_bytes(zip_path.read_bytes())

        ro.restore_data_backup_zip(_Upload(), ["database"])

        with ro.get_db() as db:
            names = {r["boat_name"] for r in db.execute("SELECT boat_name FROM boats").fetchall()}
        assert "IN THE BACKUP" in names, "the restore did not bring the backup's rows back"
        assert "AFTER THE BACKUP" not in names, "post-backup rows survived the restore"


class TestDurabilityIsNotTradedAway:
    def test_synchronous_stays_full_because_the_hut_runs_on_a_battery(self, client):
        """Relaxing this is the usual companion to a concurrency fix, and it risks
        losing the last commits on power loss. This PC is off-grid on a battery, so
        power loss is a normal event here — and the thing at risk is race results."""
        conn = core_db.get_db()
        try:
            assert int(conn.execute("PRAGMA synchronous").fetchone()[0]) == 2
        finally:
            conn.close()
