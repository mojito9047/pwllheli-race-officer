"""Backup and restore of the whole data folder.

The app keeps race data in three SQLite files, not one: the race database, the
GPS track history (v0.166) and the hut power history (v0.137). Only the first
was ever in a backup, so a hut PC rebuilt from a backup came back with every
boat's recorded track silently gone. These tests pin down what a backup must
contain and that a restore puts each database back where its module looks for
it.
"""
from __future__ import annotations

import time
import zipfile
from pathlib import Path

import pytest

import app as ro
from core import appstate, backup as backup_core, power, track


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@pytest.fixture()
def data_dir(client, tmp_path, monkeypatch):
    """Point the data-folder constants at a throwaway directory.

    ``client`` already redirects the databases; the file/folder sections need
    the same treatment or a restore test writes into the real data folder.
    """
    root = tmp_path / "data"
    for name, value in (
        ("DATA_DIR", root),
        ("POLARS_DIR", root / "polars"),
        ("SAIL_CHARTS_DIR", root / "sailcharts"),
        ("BRANDING_DIR", root / "branding"),
        ("VIDEO_CLIPS_DIR", root / "video_clips"),
        ("LEGACY_VIDEO_CLIPS_DIR", root / "video" / "clips"),
    ):
        Path(value).mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(appstate, name, Path(value))
    return root


def _record_track_fix(unique_id="BACKUP-1", lat=52.88, lon=-4.42, ago=5.0):
    track.insert_positions([{
        "device_id": 1, "unique_id": unique_id, "name": "Backup Boat",
        "lat": lat, "lon": lon, "speed_kn": 5.0, "course_deg": 90.0,
        "fix_time": time.time() - ago, "server_time": time.time() - ago,
    }], retention_days=30)


def _record_power_sample(soc=87.5):
    power.insert_power_sample({"battery_v": 12.8, "battery_soc": soc}, retention_days=30)


def _zip_members(path):
    with zipfile.ZipFile(path, "r") as zf:
        return set(zf.namelist())


def _row_count(db_path, table):
    import sqlite3
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        conn.close()


def _wipe(db_path, table):
    """Empty a database the way losing it would look, without deleting the file.

    The app's helpers open a connection per call and leave it to the garbage
    collector, so on Windows the file cannot be unlinked while the process is
    running — which is also why a restore has to overwrite in place.
    """
    import sqlite3
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(f"DELETE FROM {table}")
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# What a backup contains
# ---------------------------------------------------------------------------

class TestEveryDatabaseIsBackedUp:
    def test_gps_track_history_is_included(self, data_dir):
        _record_track_fix()
        zip_path, summary = ro.create_data_backup_zip(["database", "tracks"])
        assert "data/track_positions.db" in _zip_members(zip_path)
        assert summary["files"]["tracks"] == 1

    def test_hut_power_history_is_included(self, data_dir):
        _record_power_sample()
        zip_path, summary = ro.create_data_backup_zip(["power_history"])
        assert "data/power_history.db" in _zip_members(zip_path)
        assert summary["files"]["power_history"] == 1

    def test_the_track_copy_is_a_readable_database_not_a_torn_file(self, data_dir):
        """Copied through SQLite's backup API, so the poller need not be stopped."""
        for i in range(5):
            _record_track_fix(unique_id=f"HOT-{i}", ago=100 - i)
        zip_path, _ = ro.create_data_backup_zip(["tracks"])
        extracted = data_dir / "extracted_track.db"
        with zipfile.ZipFile(zip_path, "r") as zf:
            extracted.write_bytes(zf.read("data/track_positions.db"))
        assert _row_count(extracted, "track_positions") == 5

    def test_a_hut_that_never_used_tracking_backs_up_cleanly(self, data_dir):
        """No track database on disk is normal, not an error."""
        assert not Path(track.TRACK_DB_PATH).exists()
        zip_path, summary = ro.create_data_backup_zip(["database", "tracks"])
        assert summary["files"]["tracks"] == 0
        assert "data/race_officer.db" in _zip_members(zip_path)

    def test_the_sections_are_offered_on_the_page(self, logged_in_client):
        html = logged_in_client.get("/admin/backup").get_data(as_text=True)
        assert 'value="tracks"' in html
        assert 'value="power_history"' in html

    def test_the_what_each_option_contains_table_lists_every_section(self, logged_in_client):
        """The tick-boxes are generated from the catalogue but the table is hand-written.

        v0.192 added two sections and only the tick-boxes grew, so the page said
        it backed up five things while actually backing up seven.
        """
        html = logged_in_client.get("/admin/backup").get_data(as_text=True)
        table = html.split("What each option contains", 1)[1]
        for section in backup_core.BACKUP_SECTION_DEFINITIONS:
            section_id = str(section["id"])
            prefixes = backup_core.backup_section_prefixes(section_id)
            assert any(p in table for p in prefixes), \
                f"{section_id} is backed up but the table names none of its files ({prefixes})"

    def test_every_section_ticked_by_default_is_actually_producible(self, data_dir):
        """A section in the catalogue with no branch in the writer backs up nothing."""
        _record_track_fix()
        _record_power_sample()
        defaults = [s["id"] for s in backup_core.BACKUP_SECTION_DEFINITIONS if s["default_backup"]]
        _, summary = ro.create_data_backup_zip(defaults)
        for section_id in ("database", "tracks", "power_history"):
            assert summary["files"][section_id] >= 1, f"{section_id} produced no files"


# ---------------------------------------------------------------------------
# Restore
# ---------------------------------------------------------------------------

class _Upload:
    """Minimal stand-in for a Werkzeug FileStorage."""

    def __init__(self, path):
        self.filename = Path(path).name
        self._path = Path(path)

    def save(self, destination):
        Path(destination).write_bytes(self._path.read_bytes())


class TestRestore:
    def test_restoring_tracks_brings_the_positions_back(self, data_dir):
        _record_track_fix(unique_id="GONE-1")
        _record_track_fix(unique_id="GONE-2")
        zip_path, _ = ro.create_data_backup_zip(["tracks"])
        _wipe(track.TRACK_DB_PATH, "track_positions")
        assert track.latest_positions() == {}

        result = ro.restore_data_backup_zip(_Upload(zip_path), ["tracks"])

        assert result["restored_counts"]["tracks"] == 1
        assert set(track.latest_positions()) == {"GONE-1", "GONE-2"}

    def test_restoring_power_history_brings_the_samples_back(self, data_dir):
        _record_power_sample(soc=42.0)
        zip_path, _ = ro.create_data_backup_zip(["power_history"])
        _wipe(power.POWER_DB_PATH, "power_samples")
        assert _row_count(power.POWER_DB_PATH, "power_samples") == 0

        result = ro.restore_data_backup_zip(_Upload(zip_path), ["power_history"])

        assert result["restored_counts"]["power_history"] == 1
        assert _row_count(power.POWER_DB_PATH, "power_samples") == 1

    def test_a_restored_database_goes_where_its_module_looks_for_it(self, data_dir):
        """Not DATA_DIR/<name> — the module global is the authority (as for race_officer.db)."""
        assert backup_core.restore_member_destination("data/track_positions.db") == Path(track.TRACK_DB_PATH)
        assert backup_core.restore_member_destination("data/power_history.db") == Path(power.POWER_DB_PATH)

    def test_restoring_a_backup_without_tracks_is_skipped_not_failed(self, data_dir):
        zip_path, _ = ro.create_data_backup_zip(["database"])
        result = ro.restore_data_backup_zip(_Upload(zip_path), ["database", "tracks"])
        assert "tracks" in result["skipped_sections"]
        assert result["restored_counts"]["database"] == 1

    def test_a_restored_track_database_is_reopened_at_the_current_schema(self, data_dir, monkeypatch):
        """The lazy "already initialised" latch must drop.

        Restore an older hut's file and the app would otherwise go on using it
        untouched — no schema migration, no CREATE INDEX — until the process
        restarts.
        """
        _record_track_fix()
        zip_path, _ = ro.create_data_backup_zip(["tracks"])
        reopened = []
        real_init = track.init_track_db
        monkeypatch.setattr(track, "init_track_db",
                            lambda: (reopened.append(track.TRACK_DB_INITIALIZED), real_init()))

        ro.restore_data_backup_zip(_Upload(zip_path), ["tracks"])

        assert reopened and reopened[0] is False, "restore left the init latch set"


# ---------------------------------------------------------------------------
# Temporary archives
# ---------------------------------------------------------------------------

class TestTemporaryArchivesDoNotPileUp:
    def test_an_abandoned_archive_is_swept_by_the_next_backup(self, data_dir):
        stale = appstate.RUNTIME_DIR / "race_officer_backup_oldone.zip"
        stale.write_bytes(b"not really a zip")
        import os
        old = time.time() - 24 * 3600
        os.utime(stale, (old, old))

        ro.create_data_backup_zip(["database"])

        assert not stale.exists()

    def test_a_download_in_progress_is_left_alone(self, data_dir):
        fresh = appstate.RUNTIME_DIR / "race_officer_backup_inflight.zip"
        fresh.write_bytes(b"still downloading")
        ro.create_data_backup_zip(["database"])
        assert fresh.exists()

    def test_the_download_route_removes_its_own_archive(self, logged_in_client, data_dir):
        """send_file bypasses call_on_close — the cleanup must ride the WSGI iterable.

        Run unbuffered and close the response as a WSGI server does; buffered
        (the test-client default) never closes the iterable, so this would pass
        either way and prove nothing.
        """
        with logged_in_client.session_transaction() as sess:
            sess["_csrf_token"] = "test-csrf-token"
        resp = logged_in_client.post(
            "/admin/backup/download",
            data={"_csrf_token": "test-csrf-token", "sections": ["database"]},
            buffered=False,
        )
        assert resp.status_code == 200
        assert b"".join(resp.iter_encoded())            # the download itself still works
        resp.close()
        leftovers = list(appstate.RUNTIME_DIR.glob(backup_core.BACKUP_ARCHIVE_GLOB))
        assert leftovers == [], f"backup archive left behind: {leftovers}"
