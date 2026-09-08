"""The audit log survives midnight, even when something is holding the file.

Found while verifying the video watchdog, when two app instances overlapped on
this machine and the activity log simply stopped:

    PermissionError: [WinError 32] The process cannot access the file because it
    is being used by another process:
      runtime\\logs\\activity.log -> runtime\\logs\\activity.log.2026-08-13

`TimedRotatingFileHandler` rotates by renaming the live file, and on Windows a
rename fails while any other process holds it open. The failure does not heal:
the handler advances its next-rollover time only after a *successful* rename, so
every subsequent line retries the same doomed rename and is discarded. And
silently -- `logging.raiseExceptions` is off in anything shipped. An audit trail
that can quietly stop is worse than none, because it is still believed.

The fix is to stop renaming: a day's lines go to a file named for that day.
The first test here is the old failure, kept as the reason.
"""
from __future__ import annotations

import logging
import logging.handlers
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import activitylog, logfiles  # noqa: E402


@pytest.fixture
def dir_(tmp_path):
    """A directory holding nothing but log files.

    `tmp_path` itself is not empty -- the autouse sandbox fixture puts its
    database in there -- and these tests count what is in the folder.
    """
    d = tmp_path / "logdir"
    d.mkdir()
    return d


@pytest.fixture
def logs(tmp_path, monkeypatch):
    """A logs directory the activity log reads from, isolated per test."""
    directory = tmp_path / "logs"
    directory.mkdir()
    monkeypatch.setattr(activitylog, "log_dir", lambda: directory)
    return directory


def make_logger(name, handler):
    log = logging.getLogger(name)
    log.handlers.clear()
    log.setLevel(logging.INFO)
    log.propagate = False
    log.addHandler(handler)
    return log


class TestWhyThisExists:
    def test_the_old_handler_lost_every_line_after_a_blocked_rename(self, dir_):
        """Not a regression test -- a record of the fault, so nobody restores
        the old handler thinking the rename is harmless."""
        path = dir_ / "activity.log"
        handler = logging.handlers.TimedRotatingFileHandler(
            str(path), when="midnight", backupCount=400, encoding="utf-8")
        handler.suffix = "%Y-%m-%d"
        log = make_logger("repro.old", handler)
        log.info("before midnight")

        holder = open(path, "r", encoding="utf-8")   # another process reading it
        handler.rolloverAt = time.time() - 1         # midnight has passed
        raising, logging.raiseExceptions = logging.raiseExceptions, False
        try:
            for i in range(3):
                log.info(f"after midnight {i}")
        finally:
            logging.raiseExceptions = raising
            holder.close()
            handler.close()

        text = path.read_text(encoding="utf-8")
        assert "before midnight" in text
        assert "after midnight" not in text, "the old handler kept the lines after all"
        assert not list(dir_.glob("activity.log.*")), "it rotated after all"


class TestTheLinesSurviveMidnight:
    """The day is stubbed rather than the wall clock nudged: these tests are
    about what happens *across* a midnight, and a real one is a poor fixture."""

    def test_a_reader_holding_yesterday_cannot_stop_todays_log(self, dir_, monkeypatch):
        """The exact condition that broke the old handler: something else has
        the previous day's file open at the moment the day turns."""
        clock = {"day": "2026-08-16"}
        monkeypatch.setattr(logfiles, "today", lambda: clock["day"])
        handler = logfiles.DatedFileHandler(dir_, "activity", keep_days=400)
        log = make_logger("repro.new", handler)
        log.info("before midnight")
        yesterday = logfiles.dated_path(dir_, "activity", "2026-08-16")

        holder = open(yesterday, "r", encoding="utf-8")
        clock["day"] = "2026-08-17"
        try:
            for i in range(3):
                log.info(f"after midnight {i}")
        finally:
            holder.close()
            handler.close()

        today = logfiles.dated_path(dir_, "activity", "2026-08-17")
        assert "after midnight 2" in today.read_text(encoding="utf-8")
        assert "before midnight" in yesterday.read_text(encoding="utf-8")

    def test_the_day_change_leaves_yesterday_alone(self, dir_, monkeypatch):
        """Nothing is renamed, so nothing can fail to be renamed."""
        clock = {"day": "2026-08-16"}
        monkeypatch.setattr(logfiles, "today", lambda: clock["day"])
        handler = logfiles.DatedFileHandler(dir_, "activity", keep_days=400)
        log = make_logger("repro.keep", handler)
        log.info("yesterday")
        before = sorted(p.name for p in dir_.iterdir())
        clock["day"] = "2026-08-17"
        log.info("today")
        handler.close()

        assert before == ["activity-2026-08-16.log"]
        assert sorted(p.name for p in dir_.iterdir()) == [
            "activity-2026-08-16.log", "activity-2026-08-17.log"]
        assert "yesterday" in (dir_ / "activity-2026-08-16.log").read_text(encoding="utf-8")
        assert "today" in (dir_ / "activity-2026-08-17.log").read_text(encoding="utf-8")

    def test_a_line_is_filed_by_its_own_date_not_a_timer(self, dir_):
        """No rollover time to drift, or to be missed while the app was stopped
        over a midnight -- which is most midnights in the sailing season."""
        handler = logfiles.DatedFileHandler(dir_, "activity", keep_days=400)
        log = make_logger("repro.date", handler)
        log.info("hello")
        handler.close()
        assert logfiles.dated_path(dir_, "activity", logfiles.today()).exists()

    def test_nothing_is_written_on_a_day_with_no_activity(self, dir_):
        """delay=True: an empty file per quiet day would make the picker a wall."""
        logfiles.DatedFileHandler(dir_, "activity", keep_days=400)
        assert list(dir_.iterdir()) == []


class TestTheHistoryIsStillReadable:
    """Upgrading must not hide what the old handler wrote. The hut has months."""

    def test_the_old_rotated_files_are_still_listed_and_read(self, logs):
        (logs / "activity.log.2026-08-04").write_text("2026-08-04 10:00:00 | old line\n",
                                                      encoding="utf-8")
        assert "2026-08-04" in [d["day"] for d in activitylog.log_days()]
        assert "old line" in activitylog.read_log("2026-08-04")["lines"][0]

    def test_the_bare_live_file_is_filed_under_the_day_it_was_written(self, logs):
        """It is whatever the old handler had open when the app was stopped."""
        stale = logs / "activity.log"
        stale.write_text("2026-08-10 09:00:00 | interrupted line\n", encoding="utf-8")
        when = datetime.now() - timedelta(days=3)
        import os
        os.utime(stale, (when.timestamp(), when.timestamp()))
        day = when.strftime("%Y-%m-%d")
        assert day in [d["day"] for d in activitylog.log_days()]
        assert "interrupted line" in activitylog.read_log(day)["lines"][0]

    def test_both_namings_for_one_day_read_as_one_day(self, logs):
        """The upgrade can leave a day with a file in each scheme."""
        today = logfiles.today()
        (logs / f"activity-{today}.log").write_text("new scheme\n", encoding="utf-8")
        (logs / f"activity.log.{today}").write_text("old scheme\n", encoding="utf-8")
        out = activitylog.read_log()
        assert out["total"] == 2, out
        assert {"new scheme", "old scheme"} == set(out["lines"])

    def test_today_is_still_the_first_tab_and_carries_no_query_string(self, logs):
        """The page links today as no ?day= and highlights it; unchanged."""
        (logs / f"activity-{logfiles.today()}.log").write_text("x\n", encoding="utf-8")
        (logs / "activity.log.2026-01-01").write_text("y\n", encoding="utf-8")
        days = activitylog.log_days()
        assert days[0]["today"] is True and days[0]["day"] == "" and days[0]["label"] == "Today"

    def test_a_day_with_nothing_reports_missing(self, logs):
        assert activitylog.read_log("1999-12-31")["missing"] is True


class TestItDoesNotFillTheDisk:
    def test_only_the_newest_days_are_kept(self, dir_):
        for day in ("2026-08-01", "2026-08-02", "2026-08-03", "2026-08-04"):
            logfiles.dated_path(dir_, "activity", day).write_text("x\n", encoding="utf-8")
        logfiles.prune_days(dir_, "activity", keep_days=2)
        left = sorted(p.name for p in dir_.iterdir())
        assert left == ["activity-2026-08-03.log", "activity-2026-08-04.log"]

    def test_a_file_that_will_not_delete_does_not_stop_the_others(self, dir_, monkeypatch):
        """Exactly the condition that broke rotation: something holds a file."""
        for day in ("2026-08-01", "2026-08-02", "2026-08-03"):
            logfiles.dated_path(dir_, "activity", day).write_text("x\n", encoding="utf-8")
        real_unlink = Path.unlink

        def stubborn(self, *a, **k):
            if self.name.endswith("2026-08-01.log"):
                raise OSError(32, "used by another process")
            return real_unlink(self, *a, **k)

        monkeypatch.setattr(Path, "unlink", stubborn)
        logfiles.prune_days(dir_, "activity", keep_days=1)
        left = sorted(p.name for p in dir_.iterdir())
        assert "activity-2026-08-02.log" not in left, "one locked file stopped the prune"
        assert "activity-2026-08-01.log" in left

    def test_keeping_nothing_is_not_a_licence_to_delete_everything(self, dir_):
        logfiles.dated_path(dir_, "activity", "2026-08-01").write_text("x\n", encoding="utf-8")
        logfiles.prune_days(dir_, "activity", keep_days=0)
        assert list(dir_.iterdir()), "keep_days=0 wiped the log"


class TestTheSlowLogGotTheSameFix:
    def test_it_uses_the_dated_handler(self, dir_, monkeypatch):
        from core import appstate, slowlog
        monkeypatch.setattr(appstate, "RUNTIME_DIR", dir_)
        monkeypatch.setattr(slowlog, "_LOGGER", None)
        logger = slowlog._get_logger()
        assert any(isinstance(h, logfiles.DatedFileHandler) for h in logger.handlers)
        for h in logger.handlers:
            h.close()
        logger.handlers.clear()
