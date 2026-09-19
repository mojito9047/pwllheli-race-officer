#!/usr/bin/env python3
"""Move a day's finishes, horn events and video clips onto the race they belong to.

The race officer finishes boats from a race page. Pick the wrong race off the
list -- an old one with a similar name, at the top of a series from last season
-- and everything that follows is recorded against it: the finish times, the
horn events they came from, and the evidence clips the recorder cuts. The
mistake is invisible on the water, because the page looks right and the horn
still sounds.

That happened on 19 September 2026. Today's Race 1 (id 87) was sailed and scored
against "R1 - 13th Sept" from the 2025 Autumn Series (id 1). Three boats were
finished there, three clips were uploaded to ``racevideos/race1/``, and last
year's race was scribbled over in the process: two boats set to DNC, one
finisher deleted, one entry added, and the race's own rating rule and course
replaced with today's.

So there are two halves to this, and they are separate switches:

**Moving** takes the rows on ``--from`` that are stamped with ``--to``'s race
day and refiles them: clips and horn events change race, each clip's entry is
looked up again by *boat* in the destination race, and the clip's files and
bucket key are renamed to the names the app itself would have given them. It
refuses when the two races are on the same day, because then nothing in the row
says which race it came from.

**Restoring** puts ``--from`` back as a pre-incident backup has it: fields
changed back, a deleted entry recreated, and an entry added during the incident
removed -- that last only when the same boat has an entry in ``--to``, so an
entry somebody added on purpose is never taken away.

    python scripts/refile_race_finishes.py --from 1 --to 87
    python scripts/refile_race_finishes.py --from 1 --to 87 \
        --restore-from pwllheli-race-officer-backup-20260912-145715.zip --apply

Run it on the hut, against the hut's own data directory, after a backup. It does
not touch the bucket: the objects are copied and the old ones deleted
separately, so the public links never point at nothing halfway through.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import sys
import tempfile
import zipfile
from typing import Any, Dict, List, Optional, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _ROOT)


def open_db(path: str, what: str = "race database") -> sqlite3.Connection:
    """Open a database that must already exist and must be one of ours.

    ``sqlite3.connect`` creates an empty file for a path that is not there, so a
    mistyped backup name opens cleanly and then fails several screens later on a
    missing table -- which is exactly how this was first found.
    """
    if not os.path.exists(path):
        raise SystemExit(f"no {what} at {path}")
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    missing = [t for t in ("races", "entries") if not db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (t,)).fetchone()]
    if missing:
        raise SystemExit(f"{path} is not a race database (no {', '.join(missing)} table)")
    return db


def backup_db(path: str, workdir: str) -> str:
    """A backup ZIP or a bare .db, either way a path to a database."""
    if not os.path.exists(path):
        raise SystemExit(f"no backup at {path}")
    if not zipfile.is_zipfile(path):
        return path
    with zipfile.ZipFile(path) as z:
        name = next((n for n in z.namelist() if n.endswith("data/race_officer.db")), None)
        if not name:
            raise SystemExit(f"no data/race_officer.db inside {path}")
        out = os.path.join(workdir, "backup_race_officer.db")
        with z.open(name) as f, open(out, "wb") as o:
            shutil.copyfileobj(f, o)
        return out


def check_predates(was_db: sqlite3.Connection, race_id: int, day: str) -> None:
    """Refuse a backup taken after the mistake, which would restore the mistake.

    The damage is on the race being restored, so a backup holding rows of the
    wrong day against it is one taken too late to be any use.
    """
    late = was_db.execute(
        "SELECT COUNT(*) FROM race_events WHERE race_id = ? AND substr(event_time,1,10) = ?",
        (race_id, day)).fetchone()[0]
    if late:
        raise SystemExit(
            f"that backup already has race {race_id} carrying {late} row(s) dated {day},\n"
            "so it was taken after the mistake. Restoring from it would keep the mistake.\n"
            "Use a backup from before that day.")


def race_day(race: sqlite3.Row) -> str:
    return str(race["start_time"] or "")[:10]


def entries_by_boat(db: sqlite3.Connection, race_id: int) -> Dict[int, sqlite3.Row]:
    return {int(r["boat_id"]): r for r in db.execute(
        "SELECT * FROM entries WHERE race_id = ? AND boat_id IS NOT NULL", (race_id,))}


def misfiled(db: sqlite3.Connection, from_id: int, day: str) -> Tuple[List[sqlite3.Row], List[sqlite3.Row]]:
    """Rows on the wrong race that carry the right race's date."""
    clips = db.execute(
        "SELECT * FROM video_clips WHERE race_id = ? AND substr(event_time,1,10) = ?"
        " ORDER BY id", (from_id, day)).fetchall()
    events = db.execute(
        "SELECT * FROM race_events WHERE race_id = ? AND substr(event_time,1,10) = ?"
        " ORDER BY id", (from_id, day)).fetchall()
    return clips, events


def renamed(clip: sqlite3.Row, to_id: int, keep_file_names: bool = False) -> Dict[str, str]:
    """What the app would have called this clip had it been filed correctly.

    Mirrors core/video.py: ``race<id>_<type>_<clip id>.mp4`` for the evidence
    copy, ``..._public.mp4`` for the published one, and ``<prefix>/race<id>/``
    in front of the bucket key.

    ``keep_file_names`` leaves the two on-disk paths alone and rewrites only the
    bucket key and public link. That is what a repair delivered as a backup to
    restore needs: a backup carries the databases and not the video files, so
    restoring one cannot rename anything on disk, and a row pointing at a file
    that was never renamed is a broken piece of evidence.
    """
    columns = ("public_object_key", "public_url") if keep_file_names else (
        "file_path", "public_file_path", "public_object_key", "public_url")
    out: Dict[str, str] = {}
    for column in columns:
        was = str(clip[column] or "")
        if not was:
            continue
        out[column] = was.replace(f"race{clip['race_id']}_", f"race{to_id}_") \
                         .replace(f"race{clip['race_id']}/", f"race{to_id}/")
    return out


def move(db: sqlite3.Connection, from_id: int, to_id: int, apply: bool,
         keep_file_names: bool = False) -> int:
    src = db.execute("SELECT * FROM races WHERE id = ?", (from_id,)).fetchone()
    dst = db.execute("SELECT * FROM races WHERE id = ?", (to_id,)).fetchone()
    if src is None or dst is None:
        raise SystemExit(f"no race with id {from_id if src is None else to_id}")
    if race_day(src) == race_day(dst):
        raise SystemExit(
            f"races {from_id} and {to_id} are both on {race_day(src)}. Nothing in a row says\n"
            "which of them it came from, so this cannot tell them apart. Move them by hand.")

    day = race_day(dst)
    clips, events = misfiled(db, from_id, day)
    print(f"from  race {from_id} '{src['name']}'  ({race_day(src)})")
    print(f"to    race {to_id} '{dst['name']}'  ({day})")
    print(f"found {len(events)} event(s) and {len(clips)} clip(s) on race {from_id} dated {day}")
    if not clips and not events:
        print("Nothing to move.")
        return 0

    by_boat = entries_by_boat(db, to_id)
    plan: List[Tuple[sqlite3.Row, Optional[int], Dict[str, str]]] = []
    blocked = False
    print()
    for clip in clips:
        target = None
        if clip["entry_id"] is not None:
            was = db.execute("SELECT boat_id, boat_name FROM entries WHERE id = ?",
                             (clip["entry_id"],)).fetchone()
            if was is None or was["boat_id"] is None:
                print(f"  clip {clip['id']}: entry {clip['entry_id']} has no boat; "
                      "it will move with no entry")
            else:
                match = by_boat.get(int(was["boat_id"]))
                if match is None:
                    print(f"  clip {clip['id']}: {was['boat_name']} has no entry in race {to_id}. "
                          "Enter the boat there first.")
                    blocked = True
                    continue
                target = int(match["id"])
                print(f"  clip {clip['id']} {clip['clip_type']:<12} {was['boat_name']:<12} "
                      f"entry {clip['entry_id']} -> {target}")
        else:
            print(f"  clip {clip['id']} {clip['clip_type']:<12} (no entry)")
        names = renamed(clip, to_id, keep_file_names)
        for column, value in names.items():
            if value != (clip[column] or ""):
                print(f"      {column}: {clip[column]} -> {value}")
        plan.append((clip, target, names))
    if blocked:
        print()
        print("REFUSING: a clip has no matching boat in the destination race (above).")
        return 1

    print()
    for event in events:
        print(f"  event {event['id']} {event['event_time'][11:]} {event['event_type']:<18} {event['label']}")

    if not apply:
        print()
        print("Dry run. Re-run with --apply to write it.")
        return 0

    for clip, target, names in plan:
        sets = ["race_id = ?"]
        values: List[Any] = [to_id]
        if target is not None:
            sets.append("entry_id = ?")
            values.append(target)
        for column, value in names.items():
            sets.append(f"{column} = ?")
            values.append(value)
        values.append(int(clip["id"]))
        db.execute(f"UPDATE video_clips SET {', '.join(sets)} WHERE id = ?", values)
        if not keep_file_names:
            rename_on_disk(clip, names)
    for event in events:
        db.execute("UPDATE race_events SET race_id = ? WHERE id = ?", (to_id, int(event["id"])))
    db.commit()
    print()
    print(f"Moved {len(plan)} clip(s) and {len(events)} event(s) to race {to_id}.")
    return 0


def rename_on_disk(clip: sqlite3.Row, names: Dict[str, str]) -> None:
    """Rename the clip's files to match, including the segment list beside it.

    Best effort: the database is the thing that has to be right, and a file that
    has already been tidied away by hand must not stop the repair.
    """
    from core import appstate

    for column in ("file_path", "public_file_path"):
        was, now = str(clip[column] or ""), names.get(column, "")
        if not was or not now or was == now:
            continue
        for a, b in ((was, now), (was[:-4] + ".txt", now[:-4] + ".txt")):
            src = os.path.join(str(appstate.BASE_DIR), a.replace("\\", os.sep))
            dst = os.path.join(str(appstate.BASE_DIR), b.replace("\\", os.sep))
            if os.path.exists(src) and not os.path.exists(dst):
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                os.replace(src, dst)
                print(f"      renamed {a} -> {b}")


def restore(db: sqlite3.Connection, race_id: int, other_id: int,
            was_db: sqlite3.Connection, apply: bool) -> int:
    """Put a scribbled-on race back the way the backup has it."""
    old_race = was_db.execute("SELECT * FROM races WHERE id = ?", (race_id,)).fetchone()
    if old_race is None:
        raise SystemExit(f"the backup has no race {race_id}")
    now_race = db.execute("SELECT * FROM races WHERE id = ?", (race_id,)).fetchone()

    columns = [c[1] for c in db.execute("PRAGMA table_info(races)")]
    race_changes = {c: (now_race[c], old_race[c]) for c in columns
                    if c in old_race.keys() and now_race[c] != old_race[c]}
    old_entries = {int(r["id"]): r for r in
                   was_db.execute("SELECT * FROM entries WHERE race_id = ?", (race_id,))}
    now_entries = {int(r["id"]): r for r in
                   db.execute("SELECT * FROM entries WHERE race_id = ?", (race_id,))}
    keep_boats = {int(r["boat_id"]) for r in
                  db.execute("SELECT boat_id FROM entries WHERE race_id = ? AND boat_id IS NOT NULL",
                             (other_id,))}

    ecols = [c[1] for c in db.execute("PRAGMA table_info(entries)")]
    changed = {eid: {c: (now_entries[eid][c], old_entries[eid][c]) for c in ecols
                     if c in old_entries[eid].keys() and now_entries[eid][c] != old_entries[eid][c]}
               for eid in sorted(set(old_entries) & set(now_entries))}
    changed = {k: v for k, v in changed.items() if v}
    gone = sorted(set(old_entries) - set(now_entries))
    added = [eid for eid in sorted(set(now_entries) - set(old_entries))
             if now_entries[eid]["boat_id"] is not None
             and int(now_entries[eid]["boat_id"]) in keep_boats]
    kept = [eid for eid in sorted(set(now_entries) - set(old_entries)) if eid not in added]

    print()
    print(f"restoring race {race_id} '{old_race['name']}' from the backup")
    for column, (is_now, was) in race_changes.items():
        print(f"  race.{column}: {str(is_now)[:60]!r} -> {str(was)[:60]!r}")
    for eid, diff in changed.items():
        print(f"  entry {eid} {old_entries[eid]['boat_name']}: " +
              ", ".join(f"{c} {str(a)[:24]!r} -> {str(b)[:24]!r}" for c, (a, b) in diff.items()))
    for eid in gone:
        print(f"  entry {eid} {old_entries[eid]['boat_name']}: deleted, will be put back")
    for eid in added:
        print(f"  entry {eid} {now_entries[eid]['boat_name']}: added during the incident, "
              f"and the boat is entered in race {other_id}; will be removed")
    for eid in kept:
        print(f"  entry {eid} {now_entries[eid]['boat_name']}: added since the backup but not "
              f"racing in {other_id}; LEFT ALONE")
    if not (race_changes or changed or gone or added):
        print("  nothing to restore")
        return 0

    if not apply:
        print()
        print("Dry run. Re-run with --apply to write it.")
        return 0

    if race_changes:
        db.execute(f"UPDATE races SET {', '.join(c + ' = ?' for c in race_changes)} WHERE id = ?",
                   [v[1] for v in race_changes.values()] + [race_id])
    for eid, diff in changed.items():
        db.execute(f"UPDATE entries SET {', '.join(c + ' = ?' for c in diff)} WHERE id = ?",
                   [v[1] for v in diff.values()] + [eid])
    for eid in gone:
        row = old_entries[eid]
        shared = [c for c in ecols if c in row.keys()]
        db.execute(f"INSERT INTO entries ({', '.join(shared)}) VALUES ({', '.join('?' * len(shared))})",
                   [row[c] for c in shared])
    for eid in added:
        db.execute("DELETE FROM entries WHERE id = ?", (eid,))
    db.commit()
    print()
    print(f"Restored race {race_id}: {len(changed)} entry change(s), {len(gone)} put back, "
          f"{len(added)} removed.")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--from", dest="from_id", type=int, required=True,
                    help="the race the finishes were recorded against by mistake")
    ap.add_argument("--to", dest="to_id", type=int, required=True,
                    help="the race actually sailed")
    ap.add_argument("--db", default=None, help="race_officer.db (default: this checkout's)")
    ap.add_argument("--restore-from", default=None,
                    help="a backup ZIP or .db from before the mistake, to put --from back")
    ap.add_argument("--keep-file-names", action="store_true",
                    help="leave the on-disk clip names alone and rewrite only the bucket key "
                         "and public link (for a repair delivered as a backup to restore)")
    ap.add_argument("--apply", action="store_true", help="write it; without this it only reports")
    args = ap.parse_args(argv)

    path = args.db
    if not path:
        from core import appstate
        path = str(appstate.DB_PATH)
    if not os.path.exists(path):
        raise SystemExit(f"no race database at {path}")
    if args.from_id == args.to_id:
        raise SystemExit("--from and --to are the same race")

    db = open_db(path)
    with tempfile.TemporaryDirectory() as tmp:
        # Opened and checked before anything is written. The move commits, so a
        # backup that turns out to be missing or unreadable after it would leave
        # the clips refiled and the scribbled-on race still wrong -- which is
        # half a repair, and the half that is hard to notice.
        was = None
        if args.restore_from:
            dst = db.execute("SELECT * FROM races WHERE id = ?", (args.to_id,)).fetchone()
            if dst is None:
                raise SystemExit(f"no race with id {args.to_id}")
            was = open_db(backup_db(args.restore_from, tmp), "backup")
            check_predates(was, args.from_id, race_day(dst))

        rc = move(db, args.from_id, args.to_id, args.apply, args.keep_file_names)
        if rc:
            return rc
        if was is not None:
            rc = restore(db, args.from_id, args.to_id, was, args.apply)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
