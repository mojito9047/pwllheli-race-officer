"""Backup/restore section metadata and pure ZIP-membership/safety helpers.

Extracted from app.py. This holds the backup-section catalogue and the pure
functions that classify ZIP members by section and validate restore member
names (path-traversal guard). The path/database-coupled archive and restore IO
(create_data_backup_zip, sqlite_backup_to_file, restore_data_backup_zip, ...)
stays in app.py because it reads the monkeypatched data/runtime paths and calls
init_db / reload_course_mark_data there.

A backup can also be written **encrypted**, which is what the nightly off-site
copy uses (core/offsite.py): same archive, same ``data/...`` member names, but
AES-256 contents. That is deliberately the WinZip AES format rather than a
container of the app's own devising, because the whole point of an off-site
backup is the day the app is not available to open it — 7-Zip and WinRAR read
these with nothing but the passphrase.
"""
from __future__ import annotations

import json
import re
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from core import appstate
from core.db import init_db

try:                                    # optional: only encrypted backups need it
    import pyzipper
except Exception:                       # pragma: no cover - exercised by the "missing" test
    pyzipper = None                     # type: ignore[assignment]

# The compression method WinZip AES entries advertise. stdlib zipfile can list
# such a member but raises on reading it, so this is how an encrypted archive is
# recognised before something tries to extract from it and fails obscurely.
WZ_AES_COMPRESS_TYPE = 99

BACKUP_MANIFEST_NAME = "race_officer_backup_manifest.json"
BACKUP_SECTION_DEFINITIONS: List[Dict[str, Any]] = [
    {
        "id": "database",
        "label": "Database (Races & settings)",
        "description": "SQLite database with races, entries, boats, settings, users, weather history and video metadata.",
        "default_backup": True,
        "default_restore": True,
        "warning": "Restoring this replaces the current race-office database.",
    },
    {
        "id": "tracks",
        "label": "GPS tracks",
        "description": "Recorded tracker positions for every boat (data/track_positions.db) — the race map history, replays and the evidence behind GPS finishes.",
        "default_backup": True,
        "default_restore": True,
        "warning": "Restoring this replaces the recorded GPS position history.",
    },
    {
        "id": "power_history",
        "label": "Hut power history",
        "description": "Battery, solar and load readings from the hut power monitor (data/power_history.db).",
        "default_backup": True,
        "default_restore": True,
        "warning": "Restoring this replaces the recorded power history.",
    },
    {
        "id": "marks_courses",
        "label": "Marks, Courses, start/finish line",
        "description": "Fixed course definitions, mark positions and start/finish line files.",
        "default_backup": True,
        "default_restore": True,
        "warning": "Restoring this replaces the current marks/courses/start-finish files.",
    },
    {
        "id": "polars_sailcharts",
        "label": "Polars & Sail charts",
        "description": "Boat polar files, per-polar sail charts and the default sail chart.",
        "default_backup": True,
        "default_restore": True,
        "warning": "Restoring this replaces the current polar and sail-chart folders.",
    },
    {
        "id": "branding",
        "label": "Branding Images",
        "description": "Uploaded club and sponsor images used on public video, live images and published results.",
        "default_backup": True,
        "default_restore": True,
        "warning": "Restoring this replaces the current uploaded branding folder.",
    },
    {
        "id": "videos",
        "label": "Videos",
        "description": "Saved start/finish/manual-horn evidence clips under data/video_clips/.",
        "default_backup": False,
        "default_restore": False,
        "warning": "Video backups can be very large and may take a long time to download, upload or restore.",
    },
]
BACKUP_SECTION_BY_ID = {str(section["id"]): section for section in BACKUP_SECTION_DEFINITIONS}


def backup_sections_for_template() -> List[Dict[str, Any]]:
    """Return configured backup sections for the Backup/Restore page."""
    return [dict(section) for section in BACKUP_SECTION_DEFINITIONS]


def normalise_backup_section_ids(values: Iterable[Any]) -> List[str]:
    """Keep only known backup-section ids while preserving display order."""
    requested = {str(v).strip() for v in values if str(v).strip()}
    return [str(section["id"]) for section in BACKUP_SECTION_DEFINITIONS if str(section["id"]) in requested]


def backup_section_prefixes(section_id: str) -> List[str]:
    """Return ZIP member prefixes owned by one backup section."""
    return {
        "database": ["data/race_officer.db"],
        "tracks": ["data/track_positions.db"],
        "power_history": ["data/power_history.db"],
        "marks_courses": ["data/marks.json", "data/courses.json", "data/start_finish.json"],
        "polars_sailcharts": ["data/DefaultSailChart.txt", "data/polars/", "data/sailcharts/"],
        "branding": ["data/branding/"],
        "videos": ["data/video_clips/", "data/video/clips/"],
    }.get(section_id, [])


def backup_member_is_for_section(member_name: str, section_id: str) -> bool:
    """Check whether a ZIP member belongs to a backup/restore section."""
    name = member_name.replace("\\", "/").lstrip("/")
    if name.endswith("/"):
        return False
    for prefix in backup_section_prefixes(section_id):
        if prefix.endswith("/"):
            if name.startswith(prefix):
                return True
        elif name == prefix:
            return True
    return False


def backup_zip_has_section(zip_file: zipfile.ZipFile, section_id: str) -> bool:
    """Return whether the uploaded backup contains files for a section."""
    return any(backup_member_is_for_section(info.filename, section_id) for info in zip_file.infolist())


def safe_restore_member_name(raw_name: str) -> Optional[str]:
    """Validate and normalise a backup ZIP member name.

    This blocks absolute paths, parent-directory traversal and Windows drive
    paths before a selected member is copied into the local data folder.
    """
    name = str(raw_name or "").replace("\\", "/").lstrip("/")
    if not name or name.endswith("/") or "\x00" in name:
        return None
    if name == BACKUP_MANIFEST_NAME:
        return None
    parts = [part for part in name.split("/") if part]
    if len(parts) < 2 or parts[0] != "data":
        return None
    if any(part in (".", "..") for part in parts):
        return None
    if re.match(r"^[A-Za-z]:", parts[0]):
        return None
    return "/".join(parts)


# ---------------------------------------------------------------------------
# Archive / restore IO. Reads the data/runtime paths and DB path from
# core.appstate and uses core.db.init_db. The restore orchestrator
# (restore_data_backup_zip) stays in app.py because it also calls
# reload_course_mark_data and invalidate_app_settings_cache there.
# ---------------------------------------------------------------------------

def backup_section_restore_targets(section_id: str) -> List[Path]:
    """Return local files/directories that are replaced by a selected restore section."""
    return {
        "marks_courses": [appstate.DATA_DIR / "marks.json", appstate.DATA_DIR / "courses.json", appstate.DATA_DIR / "start_finish.json"],
        "polars_sailcharts": [appstate.DATA_DIR / "DefaultSailChart.txt", appstate.POLARS_DIR, appstate.SAIL_CHARTS_DIR],
        "branding": [appstate.BRANDING_DIR],
        "videos": [appstate.VIDEO_CLIPS_DIR, appstate.LEGACY_VIDEO_CLIPS_DIR],
    }.get(section_id, [])


def safe_backup_archive_name(path: Path) -> str:
    """Return a data-relative ZIP member name for a backup file."""
    rel = path.resolve().relative_to(appstate.DATA_DIR.resolve())
    return "data/" + rel.as_posix()


def add_file_to_backup(zip_file: zipfile.ZipFile, source: Path, archive_name: str) -> bool:
    """Add one existing file to a backup ZIP and report whether it was included."""
    if not source.exists() or not source.is_file():
        return False
    zip_file.write(source, archive_name)
    return True


def add_directory_to_backup(zip_file: zipfile.ZipFile, directory: Path) -> int:
    """Add all files below a data-directory child to a backup ZIP."""
    if not directory.exists() or not directory.is_dir():
        return 0
    count = 0
    for source in sorted(directory.rglob("*")):
        if source.is_file():
            zip_file.write(source, safe_backup_archive_name(source))
            count += 1
    return count


def extra_database_files() -> Dict[str, Tuple[Path, str]]:
    """Return the SQLite databases that live outside ``race_officer.db``.

    Keyed by backup-section id, each value is ``(live path, ZIP member name)``.
    The paths are read at call time — they are module globals in core.track /
    core.power that tests redirect to a temp file, and binding them at import
    would make a test write to the developer's real history.
    """
    from core import power, track          # local: avoids an import cycle
    return {
        "tracks": (Path(track.TRACK_DB_PATH), "data/track_positions.db"),
        "power_history": (Path(power.POWER_DB_PATH), "data/power_history.db"),
    }


def sqlite_backup_to_file(destination: Path, source: Optional[Path] = None) -> bool:
    """Copy a live SQLite database through SQLite's backup API.

    Used for every database in a backup: the race database, and (v0.192) the
    GPS track and hut power histories.  A plain file copy of a database the app
    is still writing to can be torn mid-transaction; the online backup API
    cannot, which is why the tracker poller does not have to be stopped to take
    a backup.  Returns whether anything was copied — a hut that has never used
    tracking has no track database, and that is not an error.

    sqlite3 connection objects are context managers for transactions only;
    exiting a ``with sqlite3.connect(...)`` block does not close the file
    handle.  On Windows that can leave the temporary backup database locked
    when the backup route removes its temporary directory, so close both
    connections explicitly after the online backup completes.
    """
    if source is None:
        init_db()
        source = Path(appstate.DB_PATH)
    source = Path(source)
    if not source.exists():
        return False
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_db = None
    backup_db = None
    try:
        source_db = sqlite3.connect(source)
        backup_db = sqlite3.connect(destination)
        source_db.backup(backup_db)
        backup_db.commit()
    finally:
        if backup_db is not None:
            backup_db.close()
        if source_db is not None:
            source_db.close()
    return True


# ---------------------------------------------------------------------------
# Encryption. An encrypted backup is an ordinary Race Officer backup ZIP whose
# member *contents* are AES-256; the member names stay readable, which is how
# section detection and the restore form still work on one.
# ---------------------------------------------------------------------------

def encryption_available() -> bool:
    """Return whether AES-encrypted backups can be written and read here."""
    return pyzipper is not None


def zip_is_encrypted(path: Path) -> bool:
    """Return whether any member of a ZIP has encrypted contents.

    Read with the standard library on purpose: this has to answer the question
    for an archive it cannot decrypt, and the encrypted bit in the general-purpose
    flags is readable without the passphrase.
    """
    try:
        with zipfile.ZipFile(path, "r") as zf:
            return any(bool(info.flag_bits & 0x1) or info.compress_type == WZ_AES_COMPRESS_TYPE
                       for info in zf.infolist())
    except (OSError, zipfile.BadZipFile):
        return False


def open_backup_zip(path: Path, passphrase: str = ""):
    """Open a backup ZIP for reading, decrypting if it needs a passphrase.

    Returns something that behaves like ``zipfile.ZipFile`` in both cases, so
    the restore code reads plain and encrypted backups through one path.
    """
    if not zip_is_encrypted(path):
        return zipfile.ZipFile(path, "r")
    if not passphrase:
        raise ValueError("This backup is encrypted. Enter the backup passphrase to restore it.")
    if pyzipper is None:
        raise ValueError(
            "This backup is encrypted and the pyzipper library is not installed, so the app cannot open it. "
            "Install it (pip install -r requirements.txt), or decrypt the ZIP with 7-Zip first."
        )
    archive = pyzipper.AESZipFile(path, "r")
    archive.setpassword(passphrase.encode("utf-8"))
    return archive


def new_backup_zip(path: Path, passphrase: str = ""):
    """Create a backup ZIP for writing, encrypted when a passphrase is given."""
    if not passphrase:
        return zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6)
    if pyzipper is None:
        raise ValueError(
            "Encrypted backups need the pyzipper library, which is not installed. "
            "Install it (pip install -r requirements.txt) or clear the backup passphrase."
        )
    archive = pyzipper.AESZipFile(path, "w", compression=pyzipper.ZIP_DEFLATED, compresslevel=6)
    archive.setpassword(passphrase.encode("utf-8"))
    archive.setencryption(pyzipper.WZ_AES, nbits=256)
    return archive


def backup_summary_template(selected_sections: List[str]) -> Dict[str, Any]:
    """Create the manifest/summary skeleton used by the backup download route."""
    return {
        "app": "Pwllheli Race Officer",
        "app_version": appstate.APP_VERSION,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "sections": selected_sections,
        "files": {},
    }


BACKUP_ARCHIVE_GLOB = "race_officer_backup_*.zip"
BACKUP_ARCHIVE_MAX_AGE_SECONDS = 6 * 3600.0


def sweep_stale_backup_archives(max_age_seconds: float = BACKUP_ARCHIVE_MAX_AGE_SECONDS) -> int:
    """Delete leftover backup archives from runtime/ and report how many went.

    A backup is built as a temporary ZIP in runtime/ and streamed to the
    browser; the download route unlinks it from the response's close callback.
    That callback does not always run — an abandoned or interrupted download
    leaves the file behind, and on this hut 218 of them (316 MB, most of a
    month) had piled up unnoticed.  So every new backup first clears out
    archives old enough that nothing can still be downloading them.
    """
    removed = 0
    cutoff = datetime.now().timestamp() - max(0.0, float(max_age_seconds))
    try:
        stale = sorted(appstate.RUNTIME_DIR.glob(BACKUP_ARCHIVE_GLOB))
    except OSError:
        return 0
    for path in stale:
        try:
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
        except OSError:
            continue        # in use, or gone already — try again next backup
    return removed


def create_data_backup_zip(selected_sections: Iterable[Any], passphrase: str = "") -> Tuple[Path, Dict[str, Any]]:
    """Create a selectable ZIP backup of the app's data directory.

    The ZIP uses stable `data/...` member names so it can be moved between hut
    PCs even if the app folder is different.  The SQLite database is copied via
    SQLite's online backup API before being written to the archive.

    With a ``passphrase`` the member contents are AES-256 encrypted — used by the
    nightly off-site copy, since that archive leaves the club's control.
    """
    section_ids = normalise_backup_section_ids(selected_sections)
    if not section_ids:
        raise ValueError("Select at least one backup section.")
    appstate.RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    sweep_stale_backup_archives()
    # Create the ZIP in runtime/ so large video backups do not sit in the app
    # root.  Close the named temporary file immediately; Windows otherwise
    # keeps an open handle that prevents ZipFile from replacing its contents.
    tmp = tempfile.NamedTemporaryFile(prefix="race_officer_backup_", suffix=".zip", dir=appstate.RUNTIME_DIR, delete=False)
    tmp_path = Path(tmp.name)
    tmp.close()
    summary = backup_summary_template(section_ids)
    extra_databases = extra_database_files()
    try:
        # The live database is first copied to a temporary SQLite file.  That
        # temp directory must be removable on Windows, so sqlite_backup_to_file()
        # explicitly closes both database handles before control returns here.
        with tempfile.TemporaryDirectory(prefix="race_officer_backup_db_") as tmpdir:
            tmp_db = Path(tmpdir) / "race_officer.db"
            with new_backup_zip(tmp_path, passphrase) as zf:
                for section_id in section_ids:
                    files_added = 0
                    if section_id == "database":
                        sqlite_backup_to_file(tmp_db)
                        if add_file_to_backup(zf, tmp_db, "data/race_officer.db"):
                            files_added += 1
                    elif section_id in extra_databases:
                        live_path, member_name = extra_databases[section_id]
                        staged = Path(tmpdir) / live_path.name
                        if sqlite_backup_to_file(staged, live_path) and add_file_to_backup(zf, staged, member_name):
                            files_added += 1
                    elif section_id == "marks_courses":
                        for name in ("marks.json", "courses.json", "start_finish.json"):
                            if add_file_to_backup(zf, appstate.DATA_DIR / name, f"data/{name}"):
                                files_added += 1
                    elif section_id == "polars_sailcharts":
                        if add_file_to_backup(zf, appstate.DATA_DIR / "DefaultSailChart.txt", "data/DefaultSailChart.txt"):
                            files_added += 1
                        files_added += add_directory_to_backup(zf, appstate.POLARS_DIR)
                        files_added += add_directory_to_backup(zf, appstate.SAIL_CHARTS_DIR)
                    elif section_id == "branding":
                        files_added += add_directory_to_backup(zf, appstate.BRANDING_DIR)
                    elif section_id == "videos":
                        files_added += add_directory_to_backup(zf, appstate.VIDEO_CLIPS_DIR)
                    summary["files"][section_id] = files_added
                summary["encrypted"] = bool(passphrase)
                zf.writestr(BACKUP_MANIFEST_NAME, json.dumps(summary, indent=2) + "\n")
        summary["zip_path"] = str(tmp_path)
        summary["zip_size_bytes"] = tmp_path.stat().st_size
        return tmp_path, summary
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass
        raise


def restore_member_destination(member_name: str) -> Optional[Path]:
    """Map a safe `data/...` ZIP member to the current app's data path."""
    if member_name == "data/race_officer.db":
        return appstate.DB_PATH
    for live_path, archive_name in extra_database_files().values():
        # Same reason as race_officer.db: the live file is wherever its module
        # says it is, which is not necessarily DATA_DIR/<name>.
        if member_name == archive_name:
            return live_path
    if not member_name.startswith("data/"):
        return None
    relative = Path(*member_name.split("/")[1:])
    return appstate.DATA_DIR / relative


SQLITE_SIDECAR_SUFFIXES = ("-wal", "-shm", "-journal")


def database_backup_section_ids() -> set:
    """Return the backup sections that restore a SQLite database file.

    Derived from the catalogue rather than listed by hand, so a fourth database
    added later is covered by the sidecar cleanup without anyone remembering to
    come back here.
    """
    return {"database", *extra_database_files().keys()}


def clear_sqlite_sidecars(path: Path) -> List[str]:
    """Delete any -journal/-wal/-shm file left beside a restored database.

    A restore overwrites the database file in place, so a journal left behind by a
    crash mid-write belongs to the *old* file and SQLite would roll it back over
    the one just restored. Cheap insurance on a PC that loses power regularly.

    This is also the groundwork for WAL (see the note in core.db on why that is
    not switched on yet): there, a live ``-wal`` is replayed over the restored file
    and hands back the old rows. Note the limit found while measuring it — on
    Windows a sidecar cannot be deleted while any connection still holds the
    database open, so this alone is not enough to make WAL safe.

    Returns the sidecars removed, so the restore can say so.
    """
    removed = []
    for suffix in SQLITE_SIDECAR_SUFFIXES:
        sidecar = Path(str(path) + suffix)
        try:
            if sidecar.exists():
                sidecar.unlink()
                removed.append(sidecar.name)
        except OSError:
            # Still held open by another connection. The restore already asks that
            # nobody be using the app; report it rather than pretending it is gone.
            removed.append(f"{sidecar.name} (could not remove)")
    return removed


def clear_restore_target(path: Path) -> None:
    """Remove an existing file or directory before restoring a selected section."""
    try:
        if path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()
    except FileNotFoundError:
        pass
