"""SQLite access layer and one-time data-layout migration.

Extracted from app.py. Reads its filesystem paths from core.appstate via
attribute access (appstate.DB_PATH, ...) so runtime reassignment and test
monkeypatching of those names are always seen here. The higher-level schema
builder (init_db / _init_db_uncached) stays in app.py for now because it is
tightly coupled to the schema, admin-password and settings logic there; it
calls the primitives defined here.

DATA_LAYOUT_MIGRATED / DATA_LAYOUT_LOCK are this module's own once-per-process
guard for the legacy-layout migration. Tests reset DATA_LAYOUT_MIGRATED here.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import threading
from typing import Any, List, Optional

from core import appstate

# How long a statement waits for another connection to finish before giving up.
#
# This was left at sqlite3's default of five seconds, and on the hut that cost a
# race officer a finish. A manual-horn clip had wedged FFmpeg for its full
# 180-second timeout; with the disk saturated, a write took longer than five
# seconds to get its lock and "Finish now" came back as
# ``sqlite3.OperationalError: database is locked`` — a 500 page, and no finish
# recorded, in the middle of a race.
#
# Thirty seconds is not a guess at how slow the disk can get; it is the
# recognition that waiting is always better than failing for this particular
# write. Nothing the race office does is so urgent that erroring out beats
# arriving late, and a finish that is not recorded is the one thing this app
# cannot afford to lose.
DB_BUSY_TIMEOUT_SECONDS = 30.0
DB_TIMEOUT_ENV_VAR = "RO_DB_TIMEOUT_S"


def busy_timeout_seconds() -> float:
    """Return how long a statement should wait for a lock.

    Read per connection rather than bound at import, so a hut PC that turns out
    to need longer can be given it by setting the environment variable and
    restarting, with no new build.
    """
    try:
        value = float(os.environ.get(DB_TIMEOUT_ENV_VAR, "") or DB_BUSY_TIMEOUT_SECONDS)
    except ValueError:
        return DB_BUSY_TIMEOUT_SECONDS
    # Never below the old five-second default — that is the bug being fixed.
    return max(5.0, value)

DATA_LAYOUT_LOCK = threading.Lock()
DATA_LAYOUT_MIGRATED = False

# NOT WAL, for now, and the reason is worth recording so it is not "fixed" by
# someone reading the SQLite documentation.
#
# WAL would be better here: in the default rollback mode a reader blocks a writer,
# so the clubhouse display polling or a backup snapshotting 60 MB can hold up a
# finish. Measured: a six-second read blocks a finish in rollback mode and does
# not in WAL.
#
# It cannot be switched on until the restore path changes. Restoring overwrites
# race_officer.db in place, and in WAL a live "-wal" beside it is replayed over
# the file just written -- measured: the restore reports success and hands back
# the OLD rows. The obvious mitigation, deleting the sidecar, does not work on
# Windows: "-wal" cannot be deleted while any connection holds the database open
# (WinError 32), and this app leaks connections because "with get_db() as db" is
# a transaction context manager, not a closer.
#
# So WAL needs connection lifecycle sorted out first, and a restore that goes
# through SQLite (backup() into the live database) rather than copying bytes over
# it. The busy timeout above is what actually fixes the reported failure, and it
# carries none of that risk.

DB_INIT_LOCK = threading.Lock()
DB_INITIALIZED = False
# The schema builder lives in app.py (_init_db_uncached) because it is tightly
# coupled to the schema/admin/settings logic there. app.py registers it here at
# import so init_db() can call it without a circular import.
SCHEMA_INITIALIZER = None


def init_db() -> None:
    """Create or gently upgrade the local SQLite schema once per process."""
    global DB_INITIALIZED
    if DB_INITIALIZED and appstate.DB_PATH.exists():
        return
    with DB_INIT_LOCK:
        if DB_INITIALIZED and appstate.DB_PATH.exists():
            return
        if SCHEMA_INITIALIZER is None:
            raise RuntimeError("core.db.SCHEMA_INITIALIZER is not set; app.py must register the schema builder.")
        SCHEMA_INITIALIZER()
        DB_INITIALIZED = True


def migrate_legacy_data_layout() -> None:
    """Move older single-folder files into the v0.68 data/runtime layout.

    The files that should be backed up live under data/.  Temporary caches,
    recorder logs, live preview frames and the rolling video buffer live under
    runtime/ and can be deleted without losing race data.  This migration only
    needs to be checked once per process; doing it for every SQLite connection
    makes public pages noticeably slower on the hut PC.
    """
    global DATA_LAYOUT_MIGRATED
    if DATA_LAYOUT_MIGRATED:
        return
    with DATA_LAYOUT_LOCK:
        if DATA_LAYOUT_MIGRATED:
            return
        appstate.DATA_DIR.mkdir(parents=True, exist_ok=True)
        appstate.RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        if appstate.LEGACY_DB_PATH.exists() and not appstate.DB_PATH.exists():
            try:
                shutil.copy2(appstate.LEGACY_DB_PATH, appstate.DB_PATH)
            except Exception:
                pass
        if appstate.LEGACY_VIDEO_CLIPS_DIR.exists():
            appstate.VIDEO_CLIPS_DIR.mkdir(parents=True, exist_ok=True)
            for src in appstate.LEGACY_VIDEO_CLIPS_DIR.glob("*"):
                if src.is_file():
                    dst = appstate.VIDEO_CLIPS_DIR / src.name
                    if not dst.exists():
                        try:
                            shutil.copy2(src, dst)
                        except Exception:
                            pass
        if appstate.LEGACY_SAIL_CHART_PATH.exists() and not appstate.SAIL_CHART_PATH.exists():
            try:
                appstate.LEGACY_SAIL_CHART_PATH.rename(appstate.SAIL_CHART_PATH)
            except Exception:
                try:
                    shutil.copy2(appstate.LEGACY_SAIL_CHART_PATH, appstate.SAIL_CHART_PATH)
                except Exception:
                    pass
        DATA_LAYOUT_MIGRATED = True


def get_db() -> sqlite3.Connection:
    """Open the data/race_officer.db SQLite database with row-style access."""
    migrate_legacy_data_layout()
    conn = sqlite3.connect(appstate.DB_PATH, timeout=busy_timeout_seconds())
    conn.row_factory = sqlite3.Row
    return conn


def table_columns(db: sqlite3.Connection, table_name: str) -> List[str]:
    """Return the column names currently present in a SQLite table."""
    return [row["name"] for row in db.execute(f"PRAGMA table_info({table_name})").fetchall()]


def ensure_column(db: sqlite3.Connection, table_name: str, column_name: str, definition: str) -> bool:
    """Add a SQLite column only if it is not already present.

    A few tests and deployment startup paths can open a brand-new database while
    another request/background service is also initialising.  If two initialisers
    both observe an old schema, SQLite may report a duplicate-column race; treat
    that as harmless because the desired column is now present.

    Returns whether this call is the one that added it, which is what a
    **one-time back-fill** needs: `init_db` runs on every request, so a back-fill
    written beside a column runs for ever unless something tells it not to.
    """
    if column_name not in table_columns(db, table_name):
        try:
            db.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}")
            return True
        except sqlite3.OperationalError as exc:
            if "duplicate column" not in str(exc).lower():
                raise
    return False


def row_get(row: Optional[sqlite3.Row], key: str, default: Any = None) -> Any:
    """Read a sqlite Row key without failing on older databases/None."""
    try:
        if row is not None and key in row.keys():
            return row[key]
    except Exception:
        pass
    return default


def safe_json_loads(value: Any, default: Any) -> Any:
    """Parse JSON stored in SQLite, returning default for blank/invalid values."""
    if value is None:
        return default
    try:
        text = str(value).strip()
        if not text:
            return default
        return json.loads(text)
    except Exception:
        return default
