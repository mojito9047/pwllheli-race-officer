#!/usr/bin/env python3
"""Run a race that has already been sailed again, as if it were happening now.

Built for testing the clubhouse display (`/bar`) and anything else that only
comes alive during a race — the start camera window, boats moving on the chart,
finishes arriving one at a time — without waiting for a Saturday, or for the
fifty minutes a simulated race takes in real time.

It takes a recorded race, makes a **new** race starting in a moment, and feeds
that race's own recorded fixes back in on the clock. Nothing about the original
race is touched, and the boats and trackers it creates are its own, so a real
tracker reporting at the same time cannot collide with it.

    python scripts/rerun_race.py --source 130                # as it happened
    python scripts/rerun_race.py --source 130 --speed 6      # ten minutes, not an hour
    python scripts/rerun_race.py --list

**`--speed` scales the recorded speed over the ground as well as the clock.** The
bar display decides when to show the finish camera from distance-to-go divided by
speed, so replaying positions six times faster while leaving the speeds alone
would have every boat looking six times further out than it is, and the camera
would come up as the boat crossed rather than two minutes before.

Ctrl-C stops it. The race it made stays in the database unless `--clean-up` is
given; `--purge` removes every race a previous run left behind.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timedelta

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _ROOT)

os.environ.setdefault("RO_INITIAL_ADMIN_PASSWORD", "rerun-only")

NAME_PREFIX = "RERUN"
TRACKER_PREFIX = "RERUN-"
# Fallback only, for a race the app cannot give a window for.
MAX_RACE_S = 6 * 3600.0


def race_window(track, race, gun):
    """The stretch of time worth replaying: warning signal to shortly after the
    last finish.

    Not simply "everything since the gun". Some entries carry a tracker that
    lives on the committee boat and reports all season, and the demo trackers
    kept running long after their race — so a 55-minute race reported itself as
    293 minutes to watch, most of it boats sitting by the line. The app already
    works this window out for the replay viewer; using the same helper means the
    two cannot disagree.
    """
    window = None
    try:
        window = track.race_track_window(race)
    except Exception:
        window = None
    if window:
        return window[0], window[1]
    return gun - 600, gun + MAX_RACE_S


def _load(ro, track, source_id):
    """Every entry of the source race with its recorded fixes, earliest first."""
    from core.races import race_first_start_dt
    race = ro.get_race(source_id)
    if not race:
        sys.exit(f"Race {source_id} not found. Try --list.")
    start_dt = race_first_start_dt(race)
    if not start_dt:
        sys.exit(f"Race {source_id} has no start time, so there is nothing to replay against.")
    gun = start_dt.timestamp()
    win_from, win_to = race_window(track, race, gun)
    out = []
    for entry in ro.get_entries(source_id):
        fixes = track.positions_for_entry_since(entry, win_from, win_to)
        if fixes:
            out.append({"entry": entry, "fixes": sorted(fixes, key=lambda f: f["t"])})
    if not out:
        sys.exit(f"Race {source_id} has no recorded track to replay.")
    return race, gun, out


def _build(ro, track, source_race, boats, gun, name):
    """A new race, with its own boats and trackers, starting at `gun`."""
    now = datetime.now().isoformat(timespec="seconds")
    # races.start_time is the WARNING signal; the gun is five minutes after it.
    warning = (datetime.fromtimestamp(gun) - timedelta(minutes=5)).isoformat(timespec="seconds")
    created = {"race": None, "boats": [], "trackers": []}
    with ro.get_db() as db:
        cur = db.execute(
            "INSERT INTO races (name, course_no, start_time, created_at,"
            " gps_finish_enabled, gps_auto_confirm) VALUES (?, ?, ?, ?, 1, 1)",
            (name, source_race["course_no"], warning, now))
        race_id = int(cur.lastrowid)
        created["race"] = race_id
        for i, b in enumerate(boats, start=1):
            e = b["entry"]
            uid = f"{TRACKER_PREFIX}{race_id}-{i}"
            cur = db.execute(
                "INSERT INTO boats (boat_name, sail_no, status, irc_rating, ytc_rating,"
                " created_at, updated_at) VALUES (?, ?, 'ACTIVE', ?, ?, ?, ?)",
                (e["boat_name"], e["sail_no"], e["manual_irc_rating"], e["manual_ytc_rating"],
                 now, now))
            boat_id = int(cur.lastrowid)
            db.execute(
                "INSERT INTO entries (race_id, boat_id, boat_name, sail_no, status,"
                " manual_irc_rating, manual_ytc_rating) VALUES (?, ?, ?, ?, 'RACING', ?, ?)",
                (race_id, boat_id, e["boat_name"], e["sail_no"],
                 e["manual_irc_rating"], e["manual_ytc_rating"]))
            db.execute("INSERT INTO trackers (unique_id, label, boat_id, active, updated_at)"
                       " VALUES (?, ?, ?, 1, ?)", (uid, e["boat_name"], boat_id, now))
            b["uid"] = uid
            b["device_id"] = 9000 + i
            created["boats"].append(boat_id)
            created["trackers"].append(uid)
        db.commit()
    return race_id, created


def _clean(ro, track, race_ids):
    with ro.get_db() as db:
        for rid in race_ids:
            uids = [r["unique_id"] for r in db.execute(
                "SELECT t.unique_id FROM trackers t JOIN entries e ON e.boat_id = t.boat_id"
                " WHERE e.race_id = ?", (rid,)).fetchall()]
            boat_ids = [r["boat_id"] for r in db.execute(
                "SELECT boat_id FROM entries WHERE race_id = ?", (rid,)).fetchall()]
            db.execute("DELETE FROM finish_proposals WHERE race_id = ?", (rid,))
            db.execute("DELETE FROM entries WHERE race_id = ?", (rid,))
            db.execute("DELETE FROM races WHERE id = ?", (rid,))
            for uid in uids:
                db.execute("DELETE FROM trackers WHERE unique_id = ?", (uid,))
            for bid in boat_ids:
                if bid:
                    db.execute("DELETE FROM boats WHERE id = ?", (bid,))
            with track.get_track_db() as tdb:
                for uid in uids:
                    tdb.execute("DELETE FROM track_positions WHERE unique_id = ?", (uid,))
                tdb.commit()
        db.commit()


def import_app():
    """Import the app, or explain which Python to use.

    On the hut PC the app runs from the ``.venv`` the installer makes, and a bare
    ``python`` is a different interpreter with none of the requirements in it. The
    bare ModuleNotFoundError that produces says nothing about the venv, so this
    catches it and names the interpreter to use instead.
    """
    try:
        import app as ro                   # noqa: E402  (needs the env var above)
        from core import track             # noqa: E402
        from core.races import race_first_start_dt   # noqa: E402
        return ro, track, race_first_start_dt
    except ModuleNotFoundError as exc:
        venv = os.path.join(_ROOT, ".venv", "Scripts", "python.exe")
        if not os.path.exists(venv):
            venv = os.path.join(_ROOT, ".venv", "bin", "python")
        here = os.path.relpath(os.path.abspath(__file__), _ROOT)
        print(f"Cannot start: no module named {exc.name!r}.\n", file=sys.stderr)
        if os.path.exists(venv):
            print("This Python is not the one the app runs in. Use the project's:\n",
                  file=sys.stderr)
            print(f'    "{os.path.relpath(venv, os.getcwd())}" {here} '
                  + " ".join(sys.argv[1:]) + "\n", file=sys.stderr)
        else:
            print("The app's virtual environment is missing. On the race-hut PC run\n"
                  "deploy\\windows\\install_startup_task.cmd, which creates .venv and\n"
                  "installs the requirements into it.\n", file=sys.stderr)
        raise SystemExit(2)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", type=int, help="Race id to replay")
    ap.add_argument("--speed", type=float, default=1.0,
                    help="Playback multiplier; scales reported speed too (default %(default)s)")
    ap.add_argument("--start-in", type=float, default=180.0, metavar="SECONDS",
                    help="How long from now the gun should be (default %(default)s). Leave enough "
                         "to open the display before the start camera window at gun minus two minutes.")
    ap.add_argument("--name", default=None, help="Name for the new race")
    ap.add_argument("--list", action="store_true", help="List races that have a track to replay")
    ap.add_argument("--clean-up", action="store_true", help="Remove the new race when the replay ends")
    ap.add_argument("--purge", action="store_true", help="Remove every race a previous run left behind, and exit")
    args = ap.parse_args()

    ro, track, race_first_start_dt = import_app()

    if args.purge:
        with ro.get_db() as db:
            ids = [int(r["id"]) for r in db.execute(
                "SELECT id FROM races WHERE name LIKE ?", (NAME_PREFIX + "%",)).fetchall()]
        if not ids:
            print("Nothing to purge.")
            return
        _clean(ro, track, ids)
        print(f"Removed {len(ids)} replay race(s): {ids}")
        return

    if args.list or not args.source:
        with ro.get_db() as db:
            races = db.execute("SELECT id, name, course_no, start_time FROM races"
                               " ORDER BY id DESC LIMIT 40").fetchall()
        print("Races with a recorded track:")
        for r in races:
            race = ro.get_race(int(r["id"]))
            start = race_first_start_dt(race)
            if not start:
                continue
            gun = start.timestamp()
            win_from, win_to = race_window(track, race, gun)
            tracked = [len(track.positions_for_entry_since(e, win_from, win_to))
                       for e in ro.get_entries(int(r["id"]))]
            boats = sum(1 for n in tracked if n)
            if boats:
                print(f"  {r['id']:>4}  {str(r['name'])[:36]:38} course {r['course_no']}"
                      f"  {boats:>2} boats  {sum(tracked):>6} fixes")
        if not args.source:
            print("\nPick one with --source ID.")
        return

    source_race, source_gun, boats = _load(ro, track, args.source)
    span = max(f["t"] for b in boats for f in b["fixes"]) - source_gun
    speed = max(0.1, args.speed)
    gun = time.time() + args.start_in
    name = args.name or f"{NAME_PREFIX} — {source_race['name']}"
    race_id, created = _build(ro, track, source_race, boats, gun, name)

    print(f"Replaying race {args.source} ({source_race['name']}) as race {race_id}.")
    print(f"  {len(boats)} boats, {sum(len(b['fixes']) for b in boats)} fixes, "
          f"{span/60:.0f} min of racing at {speed:g}x = {span/speed/60:.0f} min to watch.")
    print(f"  Gun at {datetime.fromtimestamp(gun):%H:%M:%S} "
          f"(in {args.start_in:.0f}s). Camera window opens two minutes before it.")
    print(f"  Open the display now:  /bar/{race_id}    (plain /bar picks it up once it is racing)")
    print("  Ctrl-C to stop.")

    # One merged stream in recorded order, so the boats arrive interleaved exactly
    # as they did on the day rather than boat by boat.
    stream = sorted(((f["t"], b, f) for b in boats for f in b["fixes"]), key=lambda x: x[0])
    t0 = time.time()
    sent = 0
    try:
        for src_t, b, f in stream:
            due = gun + (src_t - source_gun) / speed
            wait = due - time.time()
            if wait > 0:
                time.sleep(wait)
            track.insert_positions([{
                "device_id": b["device_id"], "unique_id": b["uid"], "name": b["uid"],
                "boat_id": None, "lat": f["lat"], "lon": f["lon"],
                # Scaled so distance-to-go over speed still means what it says.
                "speed_kn": (f.get("speed_kn") or 0.0) * speed,
                "course_deg": f.get("course_deg"),
                "fix_time": due, "server_time": due,
            }], retention_days=3650)
            sent += 1
            if sent % 200 == 0:
                print(f"  {sent}/{len(stream)} fixes, {(time.time()-t0)/60:.1f} min elapsed")
        print(f"Replay complete: {sent} fixes over {(time.time()-t0)/60:.1f} minutes.")
    except KeyboardInterrupt:
        print(f"\nStopped after {sent} fixes.")
    finally:
        if args.clean_up:
            _clean(ro, track, [race_id])
            print(f"Removed race {race_id}.")
        else:
            print(f"Race {race_id} left in the database. "
                  f"Remove it with:  python scripts/rerun_race.py --purge")


if __name__ == "__main__":
    main()
