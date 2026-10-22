"""The activity log lives in the data folder, and keeps sixty days.

It used to be written to runtime/logs. An upgrade unzips the new version into a
folder of its own and copies the data folder across, so the log stayed behind in
the old version's runtime/ every time and had to be carried over by hand -- the
one record of who did what, left in a folder the club deletes once the new
version is settled. In data/ it moves with everything else.

The upgrade that moves it has to bring the old days along: copy the old
runtime/logs across as before, and the first start files them under data/logs.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

import pytest

from core import activitylog, appstate, logfiles


@pytest.fixture()
def folders(tmp_path, monkeypatch):
    """A data folder and a runtime folder of their own, and a logger not yet started."""
    data, runtime = tmp_path / "data", tmp_path / "runtime"
    (runtime / "logs").mkdir(parents=True)
    data.mkdir()
    monkeypatch.setattr(appstate, "DATA_DIR", data)
    monkeypatch.setattr(appstate, "RUNTIME_DIR", runtime)

    def reset():
        logger = logging.getLogger("pwllheli.activity")
        for handler in list(logger.handlers):
            handler.close()
            logger.removeHandler(handler)
        activitylog._LOGGER = None

    reset()
    yield data / "logs", runtime / "logs"
    reset()


class TestItIsWrittenWithTheData:
    def test_a_line_lands_in_data_logs(self, folders):
        data_logs, runtime_logs = folders
        activitylog.log_activity("race created", "#1 'Club Race'", user="admin")
        for handler in logging.getLogger("pwllheli.activity").handlers:
            handler.flush()
        assert logfiles.dated_path(data_logs, "activity", logfiles.today()).exists()
        assert not list(runtime_logs.iterdir())

    def test_the_release_never_carries_one(self):
        """data/ ships in the release ZIP; whoever builds it has a log in there."""
        from pathlib import Path
        root = Path(__file__).resolve().parent.parent
        source = (root / "scripts" / "build_release_zip.py").read_text(encoding="utf-8")
        assert 'os.path.join("data", "logs")' in source
        assert "data/logs/" in (root / ".gitignore").read_text(encoding="utf-8")


class TestTheOldLocationIsCarriedAcross:
    def test_every_naming_moves_and_the_slow_log_stays(self, folders):
        data_logs, runtime_logs = folders
        for name in ("activity-2026-10-01.log", "activity.log.2026-09-30", "activity.log",
                     "slow-2026-10-01.log"):
            (runtime_logs / name).write_text(f"{name}\n", encoding="utf-8")

        activitylog._get_logger()

        assert sorted(p.name for p in data_logs.iterdir()) == \
            ["activity-2026-10-01.log", "activity.log", "activity.log.2026-09-30"]
        assert [p.name for p in runtime_logs.iterdir()] == ["slow-2026-10-01.log"], \
            "the slow-request log is a diagnostic and belongs in runtime/"

    def test_a_day_in_both_places_keeps_both(self, folders):
        """Today's file, if the new place was written to before the old one was copied in."""
        data_logs, runtime_logs = folders
        data_logs.mkdir()
        name = "activity-2026-10-02.log"
        (data_logs / name).write_text("10:05 newer\n", encoding="utf-8")
        (runtime_logs / name).write_text("09:00 older\n", encoding="utf-8")

        activitylog.move_legacy_logs()

        assert (data_logs / name).read_text(encoding="utf-8") == "09:00 older\n10:05 newer\n"
        assert not (runtime_logs / name).exists()

    def test_the_browser_page_sees_the_old_days_at_once(self, folders):
        """Before anything has been logged since the start."""
        _data_logs, runtime_logs = folders
        (runtime_logs / "activity-2026-10-01.log").write_text(
            "2026-10-01 10:00:00 | user=admin | login\n", encoding="utf-8")
        assert [d["day"] for d in activitylog.log_days()] == ["2026-10-01"]


class TestSixtyDaysAreKept:
    def test_a_backlog_is_trimmed_when_the_app_starts(self, folders):
        """The old rule was 400 days; the hut has a season of them."""
        data_logs, runtime_logs = folders
        start = date.today() - timedelta(days=100)
        for n in range(70):
            day = (start + timedelta(days=n)).isoformat()
            logfiles.dated_path(runtime_logs, "activity", day).write_text("x\n", encoding="utf-8")

        activitylog._get_logger()

        kept = sorted(p.name for p in data_logs.iterdir())
        assert len(kept) == activitylog.KEEP_DAYS == 60
        assert kept[-1] == f"activity-{(start + timedelta(days=69)).isoformat()}.log", "the newest were not kept"


class TestItIsBackedUpWithTheDatabase:
    """The record of who changed what in the database goes where the database goes:
    into every backup that includes it, the nightly off-site copy among them."""

    @pytest.fixture()
    def logged_days(self, folders, client):
        data_logs, _runtime_logs = folders
        data_logs.mkdir(exist_ok=True)
        for day in ("2026-10-01", "2026-10-02"):
            logfiles.dated_path(data_logs, "activity", day).write_text(
                f"{day} 10:00:00 | user=admin | login\n", encoding="utf-8")
        (data_logs / "notes.txt").write_text("not the log\n", encoding="utf-8")
        return data_logs

    @staticmethod
    def members(zip_path):
        import zipfile
        with zipfile.ZipFile(zip_path) as zf:
            return sorted(zf.namelist())

    def test_the_database_section_carries_each_day_and_nothing_else(self, logged_days):
        from core import backup
        zip_path, summary = backup.create_data_backup_zip(["database"])
        try:
            names = self.members(zip_path)
        finally:
            zip_path.unlink()
        assert "data/logs/activity-2026-10-01.log" in names
        assert "data/logs/activity-2026-10-02.log" in names
        assert "data/logs/notes.txt" not in names

    def test_another_section_does_not(self, logged_days):
        from core import backup
        zip_path, _ = backup.create_data_backup_zip(["marks_courses"])
        try:
            assert not [n for n in self.members(zip_path) if n.startswith("data/logs/")]
        finally:
            zip_path.unlink()

    def test_the_off_site_copy_includes_it_by_default(self):
        from core import offsite
        assert "database" in offsite.DEFAULT_OFFSITE_SECTIONS


class TestARestoreOnlyAddsMissingDays:
    """A history: the days since the backup was made -- today's, and the record of
    the restore itself -- are the newest there is, and must survive it."""

    class _Upload:
        def __init__(self, path):
            self.filename = path.name
            self._path = path

        def save(self, destination):
            from pathlib import Path
            Path(destination).write_bytes(self._path.read_bytes())

    def test_a_missing_day_comes_back_and_a_present_one_is_left_alone(self, folders, client):
        import app as ro
        from core import backup
        data_logs, _runtime_logs = folders
        data_logs.mkdir(exist_ok=True)
        for day in ("2026-10-01", "2026-10-02"):
            logfiles.dated_path(data_logs, "activity", day).write_text(f"{day} as backed up\n", encoding="utf-8")
        zip_path, _ = backup.create_data_backup_zip(["database"])
        try:
            logfiles.dated_path(data_logs, "activity", "2026-10-01").unlink()            # lost
            logfiles.dated_path(data_logs, "activity", "2026-10-02").write_text(
                "2026-10-02 as backed up\n2026-10-02 and since\n", encoding="utf-8")   # grown

            result = ro.restore_data_backup_zip(self._Upload(zip_path), ["database"])
        finally:
            zip_path.unlink()

        assert logfiles.dated_path(data_logs, "activity", "2026-10-01").read_text(encoding="utf-8") == \
            "2026-10-01 as backed up\n"
        assert logfiles.dated_path(data_logs, "activity", "2026-10-02").read_text(encoding="utf-8") == \
            "2026-10-02 as backed up\n2026-10-02 and since\n", "a day already here was rewritten"
        assert result["activity_log_days_restored"] == 1
        assert result["activity_log_days_kept"] == 1
        assert result["restored_counts"]["database"] == 1, "the log days are not database files"
        _ok, message, _details = ro.describe_restore_result(result)
        assert "activity log (1 missing day)" in message
        assert "1 day of activity log already here was kept" in message

    def test_a_member_that_is_not_the_log_is_not_written_into_its_folder(self, folders, client, tmp_path):
        import zipfile
        import app as ro
        data_logs, _runtime_logs = folders
        crafted = tmp_path / "crafted.zip"
        with zipfile.ZipFile(crafted, "w") as zf:
            zf.writestr("data/logs/evil.bat", "echo nope\n")
            zf.writestr("data/logs/activity-2026-10-03.log", "2026-10-03 fine\n")
        ro.restore_data_backup_zip(self._Upload(crafted), ["database"])
        assert not (data_logs / "evil.bat").exists()
        assert logfiles.dated_path(data_logs, "activity", "2026-10-03").exists()
