#!/usr/bin/env python3
"""Detach one tracker's fixes from a boat they were never that boat's fixes.

Two trackers can be paired to the same boat at the same time: the trackers table
is keyed on the device, so nothing stops a second device being pointed at a boat
that already has one. When that happens the boat's track becomes the two devices
interleaved, and if one of them is aboard a *different* boat the track flips
between the two positions several times a minute.

That is what happened to CRACKAJACK in the club race of 8 August 2026. Device
864032050547569 was aboard MOJITO for the whole race -- median 10 m from
Mojito's own tracker, 89% of its fixes within 50 m over seventeen hours -- while
being paired to Crackajack in the app. Crackajack's own tracker
(864864070498856) was where Crackajack actually was, a median 1.1 km away. The
replay drew the two alternately.

The repair is to set those rows' ``boat_id`` to NULL: the fixes stay, as a
record of what the device reported, but they stop belonging to a boat. They are
deliberately *not* re-pointed at Mojito -- Mojito has its own tracker and 24,000
fixes of its own that day, and a pairing that never existed should not be
invented after the fact to tidy a chart.

    python scripts/fix_track_misattribution.py                      # dry run
    python scripts/fix_track_misattribution.py --apply              # do it
    python scripts/fix_track_misattribution.py --device X --boat N  # another pair

Run it against the hut's own data directory. Take a backup first: Backup /
restore in the app, or copy data/track_positions.db aside.
"""
from __future__ import annotations

import argparse
import math
import os
import sqlite3
import sys
from datetime import datetime

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _ROOT)

# The one this was written for. Both are overridable.
DEFAULT_DEVICE = "864032050547569"
DEFAULT_BOAT = 21


def haversine_m(lat1, lon1, lat2, lon2):
    p = math.pi / 180.0
    return 6371000.0 * 2 * math.asin(math.sqrt(
        math.sin((lat2 - lat1) * p / 2) ** 2
        + math.cos(lat1 * p) * math.cos(lat2 * p) * math.sin((lon2 - lon1) * p / 2) ** 2))


def when(ts):
    return datetime.fromtimestamp(float(ts)).strftime("%Y-%m-%d %H:%M")


def co_location_by_day(db, device, boat_id):
    """Which boat this device was sitting on, day by day.

    Per day rather than one figure for the whole span, because a single number
    lies here. These trackers travel together in a bag between the office and
    the club, so over their whole life the device that shares most of its
    positions with this one is simply whichever tracker shared the bag -- which
    says nothing about what happened on the water. The day of the race is the
    day that matters, and on that day the answer is unambiguous.
    """
    rows = db.execute(
        "SELECT fix_time, lat, lon, date(fix_time,'unixepoch') d FROM track_positions"
        " WHERE unique_id = ? AND boat_id = ? AND lat IS NOT NULL"
        " ORDER BY fix_time", (device, boat_id)).fetchall()
    if not rows:
        return []
    others = [r[0] for r in db.execute(
        "SELECT DISTINCT boat_id FROM track_positions"
        " WHERE boat_id IS NOT NULL AND boat_id != ?", (boat_id,))]

    by_day = {}
    for r in rows:
        by_day.setdefault(r["d"], []).append(r)

    out = []
    for day, day_rows in sorted(by_day.items()):
        sample = day_rows[::max(1, len(day_rows) // 200)]
        best = (None, 0.0, 0)
        for other in others:
            theirs = db.execute(
                "SELECT fix_time, lat, lon FROM track_positions"
                " WHERE boat_id = ? AND lat IS NOT NULL AND fix_time BETWEEN ? AND ?"
                " ORDER BY fix_time",
                (other, day_rows[0]["fix_time"], day_rows[-1]["fix_time"])).fetchall()
            if len(theirs) < 20:
                continue
            times = [t["fix_time"] for t in theirs]
            near = compared = 0
            for r in sample:
                i = min(range(len(times)), key=lambda k: abs(times[k] - r["fix_time"]))
                if abs(times[i] - r["fix_time"]) > 120:
                    continue
                compared += 1
                if haversine_m(r["lat"], r["lon"], theirs[i]["lat"], theirs[i]["lon"]) < 50:
                    near += 1
            if compared >= 20 and near / compared > best[1]:
                best = (other, near / compared, compared)
        out.append((day, len(day_rows), best))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--device", default=DEFAULT_DEVICE, help="the tracker's unique id (IMEI)")
    ap.add_argument("--boat", type=int, default=DEFAULT_BOAT, help="the boat it is wrongly attributed to")
    ap.add_argument("--db", default=None, help="track database (default: this checkout's)")
    ap.add_argument("--apply", action="store_true", help="write the change; without this it only reports")
    args = ap.parse_args(argv)

    path = args.db
    if not path:
        from core import appstate
        path = str(appstate.DATA_DIR / "track_positions.db")
    if not os.path.exists(path):
        raise SystemExit(f"no track database at {path}")

    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row

    row = db.execute(
        "SELECT COUNT(*) n, MIN(fix_time) t0, MAX(fix_time) t1 FROM track_positions"
        " WHERE unique_id = ? AND boat_id = ?", (args.device, args.boat)).fetchone()
    if not row["n"]:
        print(f"Nothing to do: no fixes from {args.device} are attributed to boat {args.boat}.")
        return 0

    keep = db.execute(
        "SELECT COUNT(*) FROM track_positions WHERE boat_id = ? AND unique_id != ?",
        (args.boat, args.device)).fetchone()[0]

    print(f"database   {path}")
    print(f"device     {args.device}")
    print(f"boat       {args.boat}")
    print(f"detaching  {row['n']} fixes   {when(row['t0'])} -> {when(row['t1'])}")
    print(f"remaining  {keep} fixes from this boat's other tracker(s)")

    print()
    print("where this device actually was, day by day:")
    aboard = False
    for day, n, (other, share, compared) in co_location_by_day(db, args.device, args.boat):
        if other is not None and share >= 0.75:
            aboard = True
            print(f"  {day}  {n:6d} fixes  ABOARD BOAT {other} "
                  f"({share * 100:.0f}% within 50 m of {compared} compared)")
        elif other is not None and share >= 0.25:
            print(f"  {day}  {n:6d} fixes  near boat {other} some of the time ({share * 100:.0f}%)")
        else:
            print(f"  {day}  {n:6d} fixes  not with any tracked boat")
    if not aboard:
        print()
        print("NOTE: no day shows this device clearly aboard another boat. That does not")
        print("      make the detachment wrong, but read the days above before applying.")

    if not keep:
        print()
        print("REFUSING: boat " + str(args.boat) + " has no other tracker, so this would")
        print("          leave it with no track at all. Name the right device.")
        return 1

    if not args.apply:
        print()
        print("Dry run. Re-run with --apply to write it. Back up the database first.")
        return 0

    db.execute("UPDATE track_positions SET boat_id = NULL"
               " WHERE unique_id = ? AND boat_id = ?", (args.device, args.boat))
    db.commit()
    print()
    print(f"Done. {row['n']} fixes detached; they are still in the table with no boat.")
    print("Re-render any film of an affected race -- the old one has the jumps in it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
