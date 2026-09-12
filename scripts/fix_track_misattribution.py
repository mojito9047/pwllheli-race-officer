#!/usr/bin/env python3
"""Detach one tracker's fixes from a boat they were never that boat's fixes.

Two trackers can be paired to the same boat at the same time: the trackers table
is keyed on the device, so nothing stops a second device being pointed at a boat
that already has one. When that happens the boat's track becomes the two devices
interleaved, and if one of them is aboard a *different* boat the track flips
between the two positions several times a minute.

That is what happened to CRACKAJACK in the club race of 8 August 2026. Device
864032050547569 was aboard MOJITO -- median 10 m from Mojito's own tracker, 89%
of its fixes within 50 m -- while being paired to Crackajack in the app.
Crackajack's own tracker was where Crackajack actually was, a median 1.1 km
away. The replay drew the two alternately.

The repair is to set those rows' ``boat_id`` to NULL: the fixes stay, as a
record of what the device reported, but they stop belonging to a boat. They are
deliberately *not* re-pointed at the other boat -- that boat has its own tracker
and its own fixes, and a pairing that never existed should not be invented after
the fact to tidy a chart.

**Say which days.** A wrong pairing is almost always a day or two, not the life
of the device: these are club loaners that move from boat to boat between races,
so the same device is genuinely that boat's on the weeks either side. Repairing
8 August without a window took all 30,154 fixes the device had ever recorded for
Crackajack, back to 6 August and on to the 17th -- and CRACKAJACK then vanished
from the chart, and from the film, of the race a week later, which it had sailed
with that very device aboard. The dry run below names the days it can see the
device on someone else's boat, and ``--apply`` refuses an unbounded run when
there are other days mixed in with them.

    python scripts/fix_track_misattribution.py --device 8648... --boat 21
    python scripts/fix_track_misattribution.py --device 8648... --boat 21
        --from 2026-08-08 --until 2026-08-09T06:00 --apply

``--reattach`` is the way back if a repair took too much. It gives those same
rows their boat again, and it insists on both bounds, because handing a device's
whole history to a boat is the mistake in the other direction.

    python scripts/fix_track_misattribution.py --device 8648... --boat 21
        --reattach --from 2026-08-09T06:00 --until 2026-08-18 --apply

Finding the two numbers: the IMEI is on the Trackers page, and the boat id is
in the Boats page URL. If you are not sure which device is the wrong one, run
it against either -- the dry run names the boat each device was really sitting
on, day by day, and changes nothing.

Times are this machine's own clock, matching the dates printed below, and a bare
date means midnight at the start of it.

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

# How much of a day's fixes have to land within 50 m of another boat's tracker
# before we will say the device was *on* that boat. Well clear of the 25-60% two
# boats on neighbouring moorings score just by sitting there all day.
ABOARD_SHARE = 0.75


def haversine_m(lat1, lon1, lat2, lon2):
    p = math.pi / 180.0
    return 6371000.0 * 2 * math.asin(math.sqrt(
        math.sin((lat2 - lat1) * p / 2) ** 2
        + math.cos(lat1 * p) * math.cos(lat2 * p) * math.sin((lon2 - lon1) * p / 2) ** 2))


def when(ts):
    return datetime.fromtimestamp(float(ts)).strftime("%Y-%m-%d %H:%M")


def parse_when(text):
    """A bound as the operator typed it: a date, or a date and a time.

    Local time, to agree with every date this script prints. A bare date is the
    midnight that starts it, so ``--from 2026-08-08 --until 2026-08-09`` is the
    whole of the 8th and nothing of the 9th.
    """
    try:
        return datetime.fromisoformat(text.strip()).timestamp()
    except ValueError:
        raise SystemExit(f"not a date or time I can read: {text!r} "
                         "(try 2026-08-08 or 2026-08-08T20:00)")


def target_rows(device, boat_id, *, reattach, t_from, t_until):
    """The WHERE clause selecting exactly the rows this run would change."""
    if reattach:
        where, params = "unique_id = ? AND boat_id IS NULL", [device]
    else:
        where, params = "unique_id = ? AND boat_id = ?", [device, boat_id]
    if t_from is not None:
        where += " AND fix_time >= ?"
        params.append(t_from)
    if t_until is not None:
        where += " AND fix_time <= ?"
        params.append(t_until)
    return where, tuple(params)


def co_location_by_day(db, where, params, boat_id):
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
        f" WHERE {where} AND lat IS NOT NULL ORDER BY fix_time", params).fetchall()
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
    # Required, with no default. This was written for one device on one boat,
    # and leaving those as defaults would have somebody repair a pairing that
    # stopped existing the day it was repaired.
    ap.add_argument("--device", required=True, help="the tracker's unique id (IMEI)")
    ap.add_argument("--boat", type=int, required=True,
                    help="the boat its fixes are wrongly attributed to")
    ap.add_argument("--from", dest="t_from", help="only fixes at or after this date/time")
    ap.add_argument("--until", dest="t_until", help="only fixes at or before this date/time")
    ap.add_argument("--reattach", action="store_true",
                    help="the other way: give these fixes back to the boat (needs both bounds)")
    ap.add_argument("--db", default=None, help="track database (default: this checkout's)")
    ap.add_argument("--apply", action="store_true", help="write the change; without this it only reports")
    args = ap.parse_args(argv)

    t_from = parse_when(args.t_from) if args.t_from else None
    t_until = parse_when(args.t_until) if args.t_until else None
    if t_from is not None and t_until is not None and t_until <= t_from:
        raise SystemExit("--until is not after --from")
    if args.reattach and (t_from is None or t_until is None):
        raise SystemExit(
            "--reattach needs --from and --until. Giving a device's whole history to a\n"
            "boat is the same mistake as taking it all away; name the days you mean.")

    path = args.db
    if not path:
        from core import appstate
        path = str(appstate.DATA_DIR / "track_positions.db")
    if not os.path.exists(path):
        raise SystemExit(f"no track database at {path}")

    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row

    where, params = target_rows(args.device, args.boat,
                                reattach=args.reattach, t_from=t_from, t_until=t_until)
    row = db.execute(
        f"SELECT COUNT(*) n, MIN(fix_time) t0, MAX(fix_time) t1 FROM track_positions WHERE {where}",
        params).fetchone()
    if not row["n"]:
        if args.reattach:
            print(f"Nothing to do: no unattached fixes from {args.device} in that window.")
        else:
            print(f"Nothing to do: no fixes from {args.device} are attributed to boat {args.boat}.")
        return 0

    keep = db.execute(
        "SELECT COUNT(*) FROM track_positions WHERE boat_id = ? AND unique_id != ?",
        (args.boat, args.device)).fetchone()[0]

    print(f"database   {path}")
    print(f"device     {args.device}")
    print(f"boat       {args.boat}")
    if t_from is not None or t_until is not None:
        print(f"window     {when(t_from) if t_from is not None else 'the start'} -> "
              f"{when(t_until) if t_until is not None else 'the end'}")
    else:
        print("window     everything this device ever recorded for this boat")
    print(f"{'attaching ' if args.reattach else 'detaching'}  {row['n']} fixes   "
          f"{when(row['t0'])} -> {when(row['t1'])}")
    print(f"remaining  {keep} fixes from this boat's other tracker(s)")

    print()
    print("where this device actually was, day by day:")
    aboard_days, own_days = [], []
    for day, n, (other, share, compared) in co_location_by_day(db, where, params, args.boat):
        if other is not None and share >= ABOARD_SHARE:
            aboard_days.append(day)
            print(f"  {day}  {n:6d} fixes  ABOARD BOAT {other} "
                  f"({share * 100:.0f}% within 50 m of {compared} compared)")
        elif other is not None and share >= 0.25:
            own_days.append(day)
            print(f"  {day}  {n:6d} fixes  near boat {other} some of the time ({share * 100:.0f}%)")
        else:
            own_days.append(day)
            print(f"  {day}  {n:6d} fixes  not with any tracked boat")

    if args.reattach:
        if aboard_days:
            print()
            print("REFUSING: this window covers " + ", ".join(aboard_days) + ", when the device")
            print("          was on another boat. Those fixes are right where they are.")
            return 1
    elif not aboard_days:
        print()
        print("NOTE: no day shows this device clearly aboard another boat. That does not")
        print("      make the detachment wrong, but read the days above before applying.")
    elif own_days and t_from is None and t_until is None:
        # The guard this script exists to have. Without it every day goes,
        # including the ones the device really did spend on the boat it is
        # paired to, and nothing about the finished run says so.
        print()
        print("REFUSING: only " + ", ".join(aboard_days) + " shows this device on another")
        print(f"          boat. The other {len(own_days)} day(s) above look like this boat's own")
        print("          fixes, and an unbounded repair would take those too. Say which days:")
        print()
        print(f"    --from {aboard_days[0]} --until {aboard_days[-1]}T23:59 --apply")
        print()
        print("          Check the hours at each end first -- an overnight race crosses")
        print("          midnight, and a boat is boarded before the start.")
        return 1

    if not args.reattach and not keep:
        print()
        print("REFUSING: boat " + str(args.boat) + " has no other tracker, so this would")
        print("          leave it with no track at all. Name the right device.")
        return 1

    if not args.apply:
        print()
        print("Dry run. Re-run with --apply to write it. Back up the database first.")
        return 0

    if args.reattach:
        db.execute(f"UPDATE track_positions SET boat_id = ? WHERE {where}",
                   (args.boat,) + params)
    else:
        db.execute(f"UPDATE track_positions SET boat_id = NULL WHERE {where}", params)
    db.commit()
    print()
    if args.reattach:
        print(f"Done. {row['n']} fixes given back to boat {args.boat}.")
    else:
        print(f"Done. {row['n']} fixes detached; they are still in the table with no boat.")
    print("Re-render any film of an affected race -- the old one has the jumps in it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
