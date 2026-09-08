"""Run the dev box against a copy of the hut's data, without becoming a second hut.

Copying `race_officer.db` across is the only way to try things on real races
with real tracks and real video. But the settings table comes with it, and that
table is how the app knows the club's Cloudflare keys, the camera password and
the off-site backup passphrase. Started with those in place, a dev box is not a
dev box: the backup scheduler writes to the club's real bucket, the publisher
uploads to it, and the PTZ code can move the camera on the hut roof.

So this swaps the databases in and immediately blanks the credentials and
switches off everything that reaches out. What is left is a full copy of the
racing -- boats, tracks, finishes, clips, results -- and an app that cannot
touch anything belonging to the club.

    python scripts/use_hut_data.py --from C:\\path\\to\\hut-copy
    python scripts/use_hut_data.py --restore

The dev database is kept, not overwritten: it is the source of truth for every
screenshot in the guides (race 1, "R1 - 13th Sept", is what the capture scripts
photograph), and losing it would quietly change every picture in the manuals
next time they are rebuilt.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "data"
DATABASES = ("race_officer.db", "track_positions.db", "power_history.db")
KEPT_SUFFIX = ".devcopy"

# Blanked on the copy. Not only for privacy: with these present the dev box can
# act on the club's own services.
SECRETS = (
    "video_public_r2_access_key_id",
    "video_public_r2_secret_access_key",
    "offsite_backup_passphrase",
    "ptz_password",
    "assistant_api_key",
    "track_ingest_secret",
    "traccar_api_token",
)

# Switched off on the copy. Each of these makes the app do something outside
# itself -- sound a horn, drive a camera, upload a file, pull a stream.
SWITCHED_OFF = {
    "video_enabled": "0",
    "start_automation_horn_enabled": "0",
    "start_automation_audio_enabled": "0",
    "offsite_backup_enabled": "0",
    "video_public_provider": "off",
    "video_public_live_provider": "local",
    "track_ingest_enabled": "0",
}


def kept_path(name: str) -> Path:
    return DATA / (name + KEPT_SUFFIX)


def sanitise(db_path: Path) -> None:
    """Blank the credentials and switch off everything outbound."""
    if not db_path.exists():
        return
    db = sqlite3.connect(db_path)
    try:
        tables = {r[0] for r in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if "app_settings" not in tables:
            return
        for key in SECRETS:
            db.execute("UPDATE app_settings SET value = '' WHERE key = ?", (key,))
        now = datetime.now().isoformat(timespec="seconds")
        for key, value in SWITCHED_OFF.items():
            # updated_at is NOT NULL, so an insert that omits it fails -- and a
            # key the hut never set is exactly the case that needs inserting.
            db.execute(
                "INSERT INTO app_settings (key, value, updated_at) VALUES (?, ?, ?)"
                " ON CONFLICT(key) DO UPDATE SET value = excluded.value,"
                " updated_at = excluded.updated_at", (key, value, now))
        db.commit()
        left = [k for k in SECRETS
                if (db.execute("SELECT value FROM app_settings WHERE key = ?",
                               (k,)).fetchone() or [""])[0]]
        if left:
            raise SystemExit("Refusing to continue: these are still set: " + ", ".join(left))
    finally:
        db.close()


def ensure_nothing_is_holding_them(names) -> None:
    """Refuse before touching anything if the app still has a database open.

    Windows will not rename a file another process holds, so a swap attempted
    with the app running fails partway -- and partway through swapping the
    databases is the worst place to stop. Renaming each file to itself is the
    honest test: it is exactly the operation that would fail later.
    """
    for name in names:
        live = DATA / name
        if not live.exists():
            continue
        probe = live.with_suffix(live.suffix + ".probe")
        try:
            os.replace(live, probe)
            os.replace(probe, live)
        except OSError:
            raise SystemExit(
                f"{name} is open by something else. Stop the app -- and any preview "
                "server or database browser -- then run this again. "
                "Nothing has been changed.")


def install(source: Path) -> None:
    source = Path(source)
    if not (source / "race_officer.db").exists():
        raise SystemExit(f"No race_officer.db in {source}")
    DATA.mkdir(parents=True, exist_ok=True)
    ensure_nothing_is_holding_them(DATABASES)
    for name in DATABASES:
        live, kept = DATA / name, kept_path(name)
        if live.exists() and not kept.exists():
            shutil.move(str(live), str(kept))
            print(f"kept your own {name} as {kept.name}")
        incoming = source / name
        if incoming.exists():
            shutil.copy2(incoming, live)
            print(f"copied in {name} ({live.stat().st_size // 1024} KB)")
    sanitise(DATA / "race_officer.db")
    print("\ncredentials blanked; recording, horns, audio, uploads and ingest switched off")
    print("run:  python scripts/use_hut_data.py --restore   to put your own data back")


def restore() -> None:
    ensure_nothing_is_holding_them(DATABASES)
    found = False
    for name in DATABASES:
        live, kept = DATA / name, kept_path(name)
        if kept.exists():
            if live.exists():
                live.unlink()
            shutil.move(str(kept), str(live))
            print(f"restored your own {name}")
            found = True
    if not found:
        print("nothing to restore: no .devcopy files in data/")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--from", dest="source", help="folder holding the hut's .db files")
    ap.add_argument("--restore", action="store_true", help="put the dev databases back")
    args = ap.parse_args()
    if args.restore:
        restore()
    elif args.source:
        install(args.source)
    else:
        ap.print_help()
        sys.exit(2)


if __name__ == "__main__":
    main()
