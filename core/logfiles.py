"""Day-stamped log files, because renaming one can fail and lose the lot.

``logging.handlers.TimedRotatingFileHandler`` rotates by *renaming* the live
file at midnight: ``activity.log`` becomes ``activity.log.2026-08-13``. On
Windows that rename fails outright while any other process holds the file open
— a second app instance during a restart, a backup reading it, an editor left
open on it — and the failure does not heal. The handler advances its
next-rollover time only *after* a successful rename, so every line after that
attempts the same doomed rename and is thrown away:

    PermissionError: [WinError 32] The process cannot access the file because
    it is being used by another process: 'activity.log' -> 'activity.log.2026-08-13'

Measured rather than assumed: with one reader holding the file, three lines
emitted after midnight left no rotated file and none of the three lines. And
silently — ``logging.raiseExceptions`` is off in anything shipped, so the audit
trail, the one record of who did what, simply stops with nothing anywhere
saying so. An audit log that can quietly stop is worse than none, because it is
still believed.

So a day's log lives in a file named for that day and is never renamed:
``activity-2026-08-17.log``. Changing day means opening a different file, which
needs no lock on the old one and cannot be blocked by whoever is reading
yesterday's.

Both naming schemes are read back, so no history is lost at the upgrade: the
dated files here, the ``activity.log.YYYY-MM-DD`` the old handler left behind,
and the bare ``activity.log`` it was writing when the app was last stopped. That
last one is filed under the day it was last written to; if a rotation had been
failing it may hold more than one day's lines, which is visible enough, since
every line carries its own timestamp.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List

DAY_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def today() -> str:
    """The current day as the log files name it. Local time: so is the racing."""
    return datetime.now().strftime("%Y-%m-%d")


def dated_path(directory: Path, stem: str, day: str) -> Path:
    """The file a given day's lines are written to."""
    return Path(directory) / f"{stem}-{day}.log"


def _day_of(path: Path, stem: str) -> str:
    """Which day a log file holds, by its name where the name says so.

    Three shapes are recognised, because two of them predate this module and
    the club's hut has years of them: ``activity-2026-08-17.log`` (written
    here), ``activity.log.2026-08-17`` (the old handler's rotated files), and a
    bare ``activity.log`` (what the old handler was writing when it stopped),
    which is filed under the day it was last written to.
    """
    name = path.name
    if name.startswith(stem + "-") and name.endswith(".log"):
        candidate = name[len(stem) + 1:-len(".log")]
        return candidate if DAY_PATTERN.match(candidate) else ""
    if name.startswith(stem + ".log"):
        suffix = name[len(stem) + len(".log"):].lstrip(".")
        if not suffix:
            try:
                return datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d")
            except OSError:
                return ""
        return suffix if DAY_PATTERN.match(suffix) else ""
    return ""


def files_by_day(directory: Path, stem: str) -> Dict[str, List[Path]]:
    """Every log file for ``stem``, grouped by the day it holds, oldest name first."""
    out: Dict[str, List[Path]] = {}
    try:
        entries = sorted(Path(directory).iterdir())
    except OSError:
        return out
    for path in entries:
        try:
            if not path.is_file():
                continue
        except OSError:
            continue
        day = _day_of(path, stem)
        if day:
            out.setdefault(day, []).append(path)
    return out


def prune_days(directory: Path, stem: str, keep_days: int) -> None:
    """Keep the newest ``keep_days`` days and delete the rest. Never raises.

    Replaces ``backupCount``, which counted rotated files. Counting days is the
    same thing when one day is one file, and stays right when a day has both a
    dated file and a legacy one left over from the upgrade.
    """
    if keep_days <= 0:
        return
    by_day = files_by_day(directory, stem)
    for day in sorted(by_day)[:max(0, len(by_day) - keep_days)]:
        for path in by_day[day]:
            try:
                path.unlink()
            except OSError:
                # Held open by something, or gone already. Either way it is not
                # worth failing a log write over; the next day will try again.
                pass


class DatedFileHandler(logging.FileHandler):
    """Write each day's lines to a file named for that day, and never rename.

    The day is checked as each line is written, which costs one ``strftime`` and
    a string compare on a path that already touches the disk. There is no timer
    and no rollover time to get wrong: a line written at 00:00:01 goes to the
    new day's file because that is the file its own date names.
    """

    def __init__(self, directory: Path, stem: str, keep_days: int, encoding: str = "utf-8") -> None:
        self.directory = Path(directory)
        self.stem = str(stem)
        self.keep_days = int(keep_days)
        self.day = today()
        # delay=True: an app that logs nothing today leaves no empty file behind.
        super().__init__(str(dated_path(self.directory, self.stem, self.day)),
                         encoding=encoding, delay=True)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            now = today()
            if now != self.day:
                self.day = now
                self.baseFilename = os.path.abspath(
                    str(dated_path(self.directory, self.stem, now)))
                stream, self.stream = self.stream, None
                if stream is not None:
                    try:
                        stream.close()
                    except Exception:
                        pass
                prune_days(self.directory, self.stem, self.keep_days)
        except Exception:
            # Whatever went wrong deciding the day, still write the line: losing
            # it silently is the fault this module exists to remove.
            pass
        super().emit(record)
