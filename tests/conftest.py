"""Shared pytest fixtures for the Pwllheli Race Officer test suite.

Run from the project root with:  pytest -q
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest

# Allow "import app" to resolve to app.py in the project root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Supply a known admin password so tests can log in without reading
# runtime/initial_admin_password.txt (which may not exist or may differ).
os.environ.setdefault("RO_INITIAL_ADMIN_PASSWORD", "testadminpass")

import app as ro  # noqa: E402 — must follow sys.path and env setup
from core import (  # noqa: E402 — extracted modules to monkeypatch
    appstate, audio, db, loginguard, offsite, power, settings, startsequence, track, weather_store,
)


# ---------------------------------------------------------------------------
# Real-database guard
# ---------------------------------------------------------------------------

_REAL_DB_PATH = Path(appstate.DB_PATH)
# The GPS track lives in its own SQLite file, and it leaked the same way: its
# path is bound at import, so a test that did not redirect it wrote SIM boats
# into the developer's real track_positions.db (found in v0.189 testing).
_REAL_TRACK_DB_PATH = Path(track.TRACK_DB_PATH)


@pytest.fixture(autouse=True)
def never_touch_the_real_database(monkeypatch):
    """Fail a test that opens the developer's live database instead of a temp one.

    Only the tests that ask for `client` (or `reset_module_state`) get
    appstate.DB_PATH redirected. A test that forgets and then calls something
    like save_app_settings() writes the real data/race_officer.db — and because
    that helper rewrites the whole settings block, it silently wipes settings it
    was not given. That is how the live-stream URL disappeared in v0.183; this
    guard turns a silent data loss into a failing test.
    """
    real_connect = db.sqlite3.connect
    protected = {_REAL_DB_PATH.resolve(): "database", _REAL_TRACK_DB_PATH.resolve(): "GPS track database"}

    def guarded_connect(database, *args, **kwargs):
        try:
            what = protected.get(Path(str(database)).resolve())
        except (OSError, ValueError):
            what = None
        if what:
            raise AssertionError(
                f"This test opened the real {what} at {database}. "
                "Add the `client` fixture (or `reset_module_state`) so it runs against a temp copy."
            )
        return real_connect(database, *args, **kwargs)

    monkeypatch.setattr(db.sqlite3, "connect", guarded_connect)
    monkeypatch.setattr(track.sqlite3, "connect", guarded_connect)


# ---------------------------------------------------------------------------
# Data-folder sandbox
# ---------------------------------------------------------------------------

_REAL_DATA_DIR = Path(appstate.DATA_DIR)

# Every appstate path that hangs off DATA_DIR. Redirecting DATA_DIR on its own is
# not enough: these are separate module globals bound at import, so they would go
# on pointing into the real folder — and then code that asks for a file's path
# relative to DATA_DIR (core.backup.safe_backup_archive_name) raises on a
# branding or polar file that is no longer underneath it.
_DERIVED_DATA_PATHS = {
    "POLARS_DIR": ("polars",),
    "SAIL_CHARTS_DIR": ("sailcharts",),
    "VIDEO_CLIPS_DIR": ("video_clips",),
    "LEGACY_VIDEO_CLIPS_DIR": ("video", "clips"),
    "BRANDING_DIR": ("branding",),
    "BRANDING_MANIFEST_PATH": ("branding", "sponsor_logos.json"),
    "SAIL_CHART_PATH": ("DefaultSailChart.txt",),
    "LEGACY_SAIL_CHART_PATH": ("SailChart J122 North.txt",),
}

# Folders left out of the copy. Race video is the club's evidence: hundreds of
# megabytes that no test reads, and it has its own (gitignored) tree.
_SANDBOX_SKIP_DIRS = {"video_clips", "video"}

# Subfolders the real layout always has, created even when the developer's copy
# has yet to grow one, so a test that writes a logo or a polar behaves the same
# on a fresh clone as on a working machine.
_SANDBOX_DIRS = ("polars", "sailcharts", "branding", "video_clips")


def _is_database_file(name: str) -> bool:
    """race_officer.db and friends, including SQLite's -wal/-shm/-journal sidecars."""
    return ".db" in name.lower()


def _copy_real_data_into(sandbox: Path) -> None:
    """Byte-copy the real data folder into ``sandbox`` (databases and video aside).

    Both copy helpers copy bytes, so each file keeps the line endings it has in
    the repo — read_text/write_text would rewrite them and make marks.json diff as
    a whole file the moment a test saved it. The subfolders use copyfile rather
    than copy2 because the bundled polar library is fifty-odd tiny files and
    copying their timestamps as well is a measurable share of this fixture's cost
    on Windows; nothing reads the mtime of a polar or a logo.
    """
    sandbox.mkdir(parents=True, exist_ok=True)
    if _REAL_DATA_DIR.is_dir():
        for source in _REAL_DATA_DIR.iterdir():
            if source.name.startswith(".") or _is_database_file(source.name):
                continue
            if source.is_dir():
                if source.name in _SANDBOX_SKIP_DIRS:
                    continue
                shutil.copytree(source, sandbox / source.name, dirs_exist_ok=True,
                                copy_function=shutil.copyfile,
                                ignore=lambda _dir, names: [n for n in names if _is_database_file(n)])
            elif source.is_file():
                shutil.copy2(source, sandbox / source.name)
    for name in _SANDBOX_DIRS:
        (sandbox / name).mkdir(parents=True, exist_ok=True)


@pytest.fixture(autouse=True)
def sandbox_data_dir(tmp_path, monkeypatch):
    """Give every test its own copy of the data folder.

    The databases were already redirected; the JSON files next to them were not,
    so any test that exercised a route which saves one edited the developer's
    working copy for real. That is how a test of the *Set marks* permission
    renamed the club's actual Outer Distance Mark in data/marks.json (v0.247):
    core.marks._marks_path() resolves appstate.DATA_DIR when it is called, and
    nothing in this file had redirected it.

    The whole folder is copied rather than a hand-listed set of files, so a new
    JSON file the app starts reading is covered without anyone remembering. It
    costs a few tens of milliseconds a test, which buys back the class of bug
    where a test silently rewrites source data.

    Autouse, deliberately: the tests that remembered to sandbox themselves were
    never the problem.

    The folder is "data_sandbox" rather than "data" because several tests build
    their own empty ``tmp_path / "data"`` and point the same constants at it. On
    the same path they would inherit this copy's contents instead, and a test that
    counts what a backup of an empty branding folder contains would be counting
    the club's real sponsor logos.
    """
    sandbox = tmp_path / "data_sandbox"
    _copy_real_data_into(sandbox)
    monkeypatch.setattr(appstate, "DATA_DIR", sandbox)
    for name, parts in _DERIVED_DATA_PATHS.items():
        monkeypatch.setattr(appstate, name, sandbox.joinpath(*parts))
    return sandbox


# ---------------------------------------------------------------------------
# Background-thread isolation
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def no_r2_uploader_thread(monkeypatch):
    """Stop the Cloudflare R2 public-live uploader daemon from starting in tests.

    ``settings_save`` / ``hardware_save`` call ``start_public_live_r2_uploader()``;
    with an R2 provider configured that launches a daemon whose ``started`` latch
    (``video.PUBLIC_LIVE_R2_STATE``) is never reset between tests. It loops calling
    ``upload_public_live_frame_once`` forever, so when a later test monkeypatches
    ``video.upload_file_to_r2`` / ``public_live_frame_path`` the background call
    runs the test's fakes and clobbers its shared assertion state — this made
    ``test_upload_public_live_frame_once_uses_stable_r2_key`` intermittently fail.
    It is the only *looping* caller of ``upload_file_to_r2`` and no test relies on
    it running, so neutralise just this launcher and clear the latch. (Other
    ``start_*`` daemons are left alone; e.g. the power monitor must start so
    ``/api/power/status`` reports its full shape.)
    """
    # Neutralise the loop body itself (looked up in core.video at thread-start
    # time), so it doesn't matter which module holds the launcher reference — the
    # routes/ split means route modules bind their own copy of
    # start_public_live_r2_uploader, which patching it on the app module would
    # miss. A spawned thread now runs a no-op and exits immediately.
    monkeypatch.setattr(ro.video, "public_live_r2_uploader_loop", lambda *a, **k: None)
    if hasattr(ro, "start_public_live_r2_uploader"):
        monkeypatch.setattr(ro, "start_public_live_r2_uploader", lambda *a, **k: None)
    # Same treatment for the other daemons a request starts
    # (ensure_background_tasks_running). Their launchers still run — some tests
    # assert on the "started" flags — but the loop bodies do nothing, because a
    # daemon outlives the test that started it and would then poll, write
    # weather samples into, and run finish detection against the developer's
    # real database once the temp-path monkeypatch has been undone.
    # (The power monitor's loop is left running: /api/power/status only reports
    # its full shape once the loop has populated it, and that loop writes to its
    # own power_history.db rather than the race database.)
    # The off-site backup scheduler is in this list for a sharper reason than the
    # others: left running, a test that happens to enable it would build a real
    # encrypted archive of the developer's data folder and push it to a real R2
    # bucket. Its loop body does nothing in tests; the tests that exercise the
    # backup call run_offsite_backup_once() directly with a fake uploader.
    for module, loop_name in (
        (weather_store, "weather_background_loop"),
        (track, "track_background_loop"),
        (audio, "central_audio_worker_loop"),
        (startsequence, "start_sequence_scheduler_loop"),
        (offsite, "offsite_backup_loop"),
    ):
        if hasattr(module, loop_name):
            monkeypatch.setattr(module, loop_name, lambda *a, **k: None)
    # ...and clear its start latch, which is a module global that would otherwise
    # persist across the whole session.
    with offsite.OFFSITE_LOCK:
        offsite.OFFSITE_STATE["started"] = False
        offsite.OFFSITE_STATE["running"] = False
        offsite.OFFSITE_STATE["last_status"] = {}
    try:
        with ro.video.PUBLIC_LIVE_R2_LOCK:
            ro.video.PUBLIC_LIVE_R2_STATE["started"] = False
    except Exception:
        pass
    # Reset the login brute-force guard so failed-login tests don't accumulate
    # lockouts across the session (same test-client ip + username).
    loginguard.reset_all()
    yield


# ---------------------------------------------------------------------------
# Module-state reset
# ---------------------------------------------------------------------------

@pytest.fixture()
def reset_module_state(sandbox_data_dir, tmp_path, monkeypatch):
    """Redirect the SQLite database to a temp file and clear cached state.

    Takes ``sandbox_data_dir`` as an argument rather than relying on autouse
    ordering: the data folder has to be a copy before _init_db_uncached() runs the
    data-layout migration over it.

    Also snapshots the in-memory course/mark/start-finish globals and restores
    them afterwards. The backup-restore route calls reload_course_mark_data(),
    which overwrites these module globals in place; without this guard a restore
    test leaves them polluted for every later test in the session (e.g. the
    race page render then fails on the missing start_line key).
    """
    # DB_PATH now lives in core.appstate (single source shared with core.db);
    # the data-layout guard lives in core.db. The rest stay on the app module.
    monkeypatch.setattr(appstate, "DB_PATH", tmp_path / "test_race_officer.db")
    # Positions live in their own file; redirect it for every test rather than
    # relying on each test module to remember (tests/test_track.py did, the
    # newer ingest tests did not, and wrote SIM boats into the real one).
    monkeypatch.setattr(track, "TRACK_DB_PATH", tmp_path / "test_track_positions.db")
    monkeypatch.setattr(track, "TRACK_DB_INITIALIZED", False)
    # Same again for the hut power history: its background loop is deliberately
    # left running (see no_r2_uploader_thread), so an un-redirected path means
    # tests append samples to the developer's real power_history.db.
    monkeypatch.setattr(power, "POWER_DB_PATH", tmp_path / "test_power_history.db")
    monkeypatch.setattr(power, "POWER_DB_INITIALIZED", False)
    # Backups build their temporary ZIP in runtime/. Left pointing at the real
    # folder, a backup test drops archives (video backups are hundreds of MB)
    # into the developer's runtime directory and nothing ever clears them.
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(appstate, "RUNTIME_DIR", runtime_dir)
    monkeypatch.setattr(db, "DB_INITIALIZED", False)
    monkeypatch.setattr(settings, "APP_SETTINGS_CACHE_VALUES", None)
    monkeypatch.setattr(db, "DATA_LAYOUT_MIGRATED", False)
    # Turn the one-second app-settings cache off for tests. Several tests write
    # settings with direct SQL (which cannot invalidate the cache the way
    # save_app_settings does) and then immediately assert on behaviour that reads
    # them back — with the cache live that passes or fails depending on how fast
    # the preceding test ran. A negative TTL means every read goes to SQLite.
    monkeypatch.setattr(settings, "APP_SETTINGS_CACHE_TTL_SECONDS", -1.0)
    monkeypatch.setattr(settings, "APP_SETTINGS_CACHE_TIME", 0.0)

    reloadable_globals = ("COURSES_DATA", "COURSES", "COURSE_BY_NO", "MARKS_DATA", "MARKS", "START_FINISH")
    snapshot = {name: getattr(appstate, name) for name in reloadable_globals}
    try:
        yield
    finally:
        for name, value in snapshot.items():
            setattr(appstate, name, value)


# ---------------------------------------------------------------------------
# Flask test client
# ---------------------------------------------------------------------------

@pytest.fixture()
def client(reset_module_state):
    """Flask test client with an isolated, freshly-initialised SQLite database."""
    ro.app.config["TESTING"] = True
    # The test client speaks http, so Secure cookies would never be stored.
    ro.app.config["SESSION_COOKIE_SECURE"] = False
    with ro.app.test_client() as c:
        with ro.app.app_context():
            ro._init_db_uncached()
        yield c


# ---------------------------------------------------------------------------
# CSRF helper
# ---------------------------------------------------------------------------

@pytest.fixture()
def csrf_post(client):
    """Return a callable that POSTs with a pre-seeded CSRF token.

    Usage::
        def test_something(csrf_post):
            resp = csrf_post("/login", {"username": "admin", "password": "x"})
    """
    def _post(url: str, data: dict | None = None, **kwargs):
        token = "test-csrf-token"
        with client.session_transaction() as sess:
            sess["_csrf_token"] = token
        form_data = dict(data or {})
        form_data["_csrf_token"] = token
        return client.post(url, data=form_data, **kwargs)

    return _post


# ---------------------------------------------------------------------------
# Authenticated client
# ---------------------------------------------------------------------------

@pytest.fixture()
def logged_in_client(client):
    """Test client already authenticated as the auto-created admin user."""
    token = "test-csrf-token"
    with client.session_transaction() as sess:
        sess["_csrf_token"] = token
    resp = client.post(
        "/login",
        data={
            "_csrf_token": token,
            "username": "admin",
            "password": "testadminpass",
        },
    )
    assert resp.status_code in (200, 302), (
        f"Login step failed with HTTP {resp.status_code}. "
        "Check RO_INITIAL_ADMIN_PASSWORD matches the password used by _init_db_uncached()."
    )
    return client
