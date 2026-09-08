"""What the activity log records, and what it must never record.

The log is the answer to "who changed that, and what was it before?". Two things
were wrong with it:

* **A settings save recorded nothing but the fact of it.** ``audit("settings
  saved")`` with no detail, which is no use for the question people actually ask.
  Knowing the previous value is what lets a change be undone or a fault explained —
  it is how the horn output being switched to active-low would have been traced.
* **35 of 65 POST handlers wrote nothing at all**, including the ones that can
  rewrite a published result and the one that replaces the whole database.

The hard constraint is the redaction: this is a plain-text file on the race-office
PC, so it must never become somewhere to read a password out of.
"""
from __future__ import annotations

import logging
import pathlib

import pytest

import app as ro
from core import activitylog, logfiles


@pytest.fixture()
def activity_log(client, tmp_path, monkeypatch):
    """Point the activity log at a temp file and hand back a reader.

    Two things make this fiddlier than it looks, and both cost a debugging session:

    * ``logging.getLogger("pwllheli.activity")`` is a process-wide singleton, so
      clearing the module's ``_LOGGER`` cache is not enough — a file handler left
      over from an earlier test keeps writing to whichever directory it opened.
    * the activity logger sets ``propagate = False``, and for a non-propagating
      logger pytest attaches its ``LogCaptureHandler`` *directly* to it. Since
      ``_get_logger`` only attaches a handler ``if not logger.handlers``, pytest's
      handler satisfies that check and the file handler is never created: the lines
      show up in pytest's captured log and nowhere on disk.

    So the file handler is built eagerly here, while the logger is known to be bare.
    """
    def reset():
        logger = logging.getLogger("pwllheli.activity")
        for handler in list(logger.handlers):
            handler.close()
            logger.removeHandler(handler)
        activitylog._LOGGER = None

    monkeypatch.setattr(ro.appstate, "RUNTIME_DIR", tmp_path)
    reset()
    activitylog._get_logger()
    # One file per day, named for the day: renaming the live file at midnight
    # is what used to lose the log entirely (see core/logfiles.py).
    log_path = logfiles.dated_path(tmp_path / "logs", "activity", logfiles.today())

    def read():
        if not log_path.exists():
            return []
        return [line for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]

    yield read
    reset()


# ---------------------------------------------------------------------------
# The redaction, which is the part that must not be got wrong
# ---------------------------------------------------------------------------

class TestSecretsAreNeverWritten:
    @pytest.mark.parametrize("name", [
        "ptz_password",
        "video_public_r2_secret_access_key",
        "video_public_r2_access_key_id",
        "offsite_backup_passphrase",
        "track_ingest_secret",
        "traccar_token",
    ])
    def test_every_credential_setting_is_treated_as_secret(self, name):
        assert activitylog.is_secret_setting(name)

    def test_an_unknown_credential_shaped_name_is_secret_by_default(self):
        """Matched as substrings so a key added later is redacted without anyone
        remembering to come back here. The safe direction to fail in."""
        assert activitylog.is_secret_setting("relay_api_token")
        assert activitylog.is_secret_setting("smtp_password")

    def test_an_ordinary_setting_is_not(self):
        for name in ("horn_active", "serial_port", "offsite_backup_bucket", "weather_poll_seconds"):
            assert not activitylog.is_secret_setting(name)

    def test_a_secret_value_never_appears_in_the_summary(self):
        summary = activitylog.settings_change_summary(
            {"ptz_password": "old-camera-pw"}, {"ptz_password": "new-camera-pw"})
        assert "old-camera-pw" not in summary
        assert "new-camera-pw" not in summary
        assert "ptz_password: changed" in summary

    def test_setting_a_secret_for_the_first_time_says_set_not_changed(self):
        summary = activitylog.settings_change_summary(
            {"offsite_backup_passphrase": ""}, {"offsite_backup_passphrase": "a-long-passphrase"})
        assert summary == "offsite_backup_passphrase: set"
        assert "a-long-passphrase" not in summary

    def test_a_real_settings_save_writes_no_passphrase_to_the_file(self, activity_log, logged_in_client):
        """End to end through the route, because that is where it would leak."""
        token = "test-csrf-token"
        with logged_in_client.session_transaction() as sess:
            sess["_csrf_token"] = token
        logged_in_client.post("/admin/settings/save", data={
            "_csrf_token": token,
            "serial_port": "COM7",
            "offsite_backup_passphrase": "unmistakable-secret-value",
            "ptz_password": "unmistakable-camera-value",
        })
        written = "\n".join(activity_log())
        assert "unmistakable-secret-value" not in written
        assert "unmistakable-camera-value" not in written


# ---------------------------------------------------------------------------
# What a settings save now records
# ---------------------------------------------------------------------------

class TestASettingsSaveSaysWhatChanged:
    def test_it_reports_old_and_new(self):
        summary = activitylog.settings_change_summary({"horn_active": "1"}, {"horn_active": "0"})
        assert summary == "horn_active: 1 -> 0"

    def test_an_empty_previous_value_reads_as_blank_rather_than_nothing(self):
        summary = activitylog.settings_change_summary({"offsite_backup_bucket": ""},
                                                      {"offsite_backup_bucket": "psc-backups"})
        assert summary == "offsite_backup_bucket: (blank) -> psc-backups"

    def test_unchanged_settings_are_not_listed(self):
        """A save writes every key; only the differences are worth a line."""
        before = {f"key{i}": str(i) for i in range(50)}
        after = dict(before, key7="changed")
        summary = activitylog.settings_change_summary(before, after)
        assert summary == "key7: 7 -> changed"

    def test_a_long_value_is_truncated_so_one_change_cannot_swamp_a_line(self):
        summary = activitylog.settings_change_summary({"url": ""}, {"url": "https://" + "x" * 300})
        assert len(summary) < 120
        assert summary.endswith("…")

    def test_a_newline_in_a_value_cannot_forge_a_second_log_line(self):
        summary = activitylog.settings_change_summary({"notes": ""}, {"notes": "a\nb"})
        assert "\n" not in summary

    def test_the_route_records_the_change_it_made(self, activity_log, logged_in_client):
        token = "test-csrf-token"
        with logged_in_client.session_transaction() as sess:
            sess["_csrf_token"] = token
        logged_in_client.post("/admin/settings/save",
                              data={"_csrf_token": token, "serial_port": "COM9"})
        lines = [l for l in activity_log() if "settings saved" in l]
        assert lines, "the save was not recorded"
        assert "serial_port" in lines[-1] and "COM9" in lines[-1]

    def test_saving_with_nothing_altered_still_records_the_visit(self, activity_log, logged_in_client):
        """So "who was in Settings" stays answerable."""
        token = "test-csrf-token"
        with logged_in_client.session_transaction() as sess:
            sess["_csrf_token"] = token
        logged_in_client.post("/admin/settings/save", data={"_csrf_token": token, "serial_port": "COM9"})
        activity_log()
        logged_in_client.post("/admin/settings/save", data={"_csrf_token": token, "serial_port": "COM9"})
        assert any("no changes" in l for l in activity_log())

    def test_the_save_functions_return_the_summary(self, client):
        """Both, because the horn wiring lives in the other table."""
        ro.save_app_settings({"weather_poll_seconds": "5"})
        assert "weather_poll_seconds" in ro.save_app_settings({"weather_poll_seconds": "9"})
        ro.save_hardware_config({"serial_port": "COM1"})
        assert "serial_port" in ro.save_hardware_config({"serial_port": "COM2"})


# ---------------------------------------------------------------------------
# The actions that used to leave no trace
# ---------------------------------------------------------------------------

class TestActionsThatChangeAResult:
    def _race_with_entry(self):
        from datetime import datetime, timedelta
        now = datetime.now()
        with ro.get_db() as db:
            race_id = int(db.execute(
                "INSERT INTO races (name, course_no, start_time, rating_rule, created_at)"
                " VALUES ('Points', 1, ?, 'DUAL', ?)",
                ((now - timedelta(hours=2)).isoformat(timespec="seconds"),
                 now.isoformat(timespec="seconds"))).lastrowid)
            entry_id = int(db.execute(
                "INSERT INTO entries (race_id, boat_name, sail_no, status) VALUES (?, 'Kite', 'GBR 1', 'RACING')",
                (race_id,)).lastrowid)
            db.commit()
        return race_id, entry_id

    def test_editing_an_entry_records_the_old_and_new_values(self, activity_log, logged_in_client):
        """The only action that can silently rewrite a published result. `finish
        recorded` beside it was already logged; this was not."""
        race_id, entry_id = self._race_with_entry()
        token = "test-csrf-token"
        with logged_in_client.session_transaction() as sess:
            sess["_csrf_token"] = token
        logged_in_client.post(f"/admin/race/{race_id}/entry/{entry_id}/update", data={
            "_csrf_token": token, "finish_time": "2026-08-04T14:30:00", "status": "FINISHED"})

        lines = [l for l in activity_log() if "entry edited" in l]
        assert lines, "an entry edit left no trace"
        assert "Kite" in lines[-1]
        assert "RACING -> FINISHED" in lines[-1]
        assert "14:30:00" in lines[-1]

    def test_an_edit_that_changes_nothing_is_not_logged(self, activity_log, logged_in_client):
        race_id, entry_id = self._race_with_entry()
        token = "test-csrf-token"
        with logged_in_client.session_transaction() as sess:
            sess["_csrf_token"] = token
        logged_in_client.post(f"/admin/race/{race_id}/entry/{entry_id}/update",
                              data={"_csrf_token": token, "status": "RACING"})
        assert not [l for l in activity_log() if "entry edited" in l]

    def test_restoring_a_backup_is_recorded(self, activity_log, logged_in_client, tmp_path, monkeypatch):
        """The most consequential button in the app: it replaces the data the club
        runs on, and it recorded nothing."""
        for name, value in (("DATA_DIR", tmp_path / "data"), ("BRANDING_DIR", tmp_path / "data" / "branding")):
            pathlib.Path(value).mkdir(parents=True, exist_ok=True)
            monkeypatch.setattr(ro.appstate, name, pathlib.Path(value))
        (ro.appstate.BRANDING_DIR / "club.png").write_bytes(b"logo")
        zip_path, _ = ro.create_data_backup_zip(["branding"])

        token = "test-csrf-token"
        with logged_in_client.session_transaction() as sess:
            sess["_csrf_token"] = token
        with zip_path.open("rb") as handle:
            logged_in_client.post("/admin/backup/restore", data={
                "_csrf_token": token, "sections": "branding",
                "backup_file": (handle, "backup.zip")},
                content_type="multipart/form-data")

        assert [l for l in activity_log() if "backup restored" in l], "a restore left no trace"

    def test_downloading_a_backup_is_recorded(self, activity_log, logged_in_client):
        """It sends every user account and password hash out as a file."""
        token = "test-csrf-token"
        with logged_in_client.session_transaction() as sess:
            sess["_csrf_token"] = token
        logged_in_client.post("/admin/backup/download",
                              data={"_csrf_token": token, "sections": "database"})
        lines = [l for l in activity_log() if "backup downloaded" in l]
        assert lines and "database" in lines[-1]

    def test_a_series_edit_records_the_discard_profile(self, activity_log, logged_in_client):
        """It re-scores every race in the series at once."""
        from datetime import datetime
        now = datetime.now().isoformat(timespec="seconds")
        with ro.get_db() as db:
            series_id = int(db.execute(
                "INSERT INTO race_series (name, created_at, updated_at) VALUES ('Summer', ?, ?)",
                (now, now)).lastrowid)
            db.commit()
        token = "test-csrf-token"
        with logged_in_client.session_transaction() as sess:
            sess["_csrf_token"] = token
        logged_in_client.post(f"/admin/series/{series_id}",
                              data={"_csrf_token": token, "name": "Summer", "discard_profile": "4:1"})
        lines = [l for l in activity_log() if "series updated" in l]
        assert lines and "discards=" in lines[-1]


class TestTheLineFormat:
    def test_it_carries_who_did_it(self, activity_log, logged_in_client):
        assert any("user=admin" in line for line in activity_log()) or True
        token = "test-csrf-token"
        with logged_in_client.session_transaction() as sess:
            sess["_csrf_token"] = token
        logged_in_client.post("/admin/settings/save", data={"_csrf_token": token, "serial_port": "COM3"})
        assert any("user=admin" in line for line in activity_log())

    def test_background_work_is_attributed_to_the_system_not_a_person(self, activity_log):
        activitylog.log_activity("off-site backup uploaded", "key (123 bytes)")
        assert any("user=system" in line for line in activity_log())

    def test_a_log_that_cannot_be_opened_never_reaches_the_caller(self, tmp_path, monkeypatch):
        """A log that cannot be written must not break a race-day request.

        Driven by making the directory genuinely un-creatable — RUNTIME_DIR pointing
        at a *file* — rather than by patching the logger, so it is the real
        best-effort path in _get_logger that is under test.
        """
        blocker = tmp_path / "not-a-directory"
        blocker.write_text("", encoding="utf-8")
        logger = logging.getLogger("pwllheli.activity")
        for handler in list(logger.handlers):
            handler.close()
            logger.removeHandler(handler)
        monkeypatch.setattr(activitylog, "_LOGGER", None)
        monkeypatch.setattr(ro.appstate, "RUNTIME_DIR", blocker)

        activitylog.log_activity("something", "details")      # must not raise
        assert activitylog._get_logger() is None
