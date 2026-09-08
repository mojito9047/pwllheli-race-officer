#!/usr/bin/env python3
"""Measure how good the predicted leaderboard actually is.

`simulate_trackers.py --sail` puts boats round the course under a polar in real
time, which is the right way to watch the thing work — but a 50-minute race per
experiment is no way to find out whether the *estimate* is any good. This sails
the same fleet offline in a second or two, writes the track into the database as
if it had happened, and then asks the app's own estimator what it would have said
at every point in the race — against the finish time the boat actually achieved.

Two questions, and they are different:

  * **How wrong is the projected finish time?** Reported as the error against the
    boat's true elapsed, by how far into the race the estimate was made.
  * **Would a competitor watching have seen the right answer?** The projection
    only matters through the order it produces, so the corrected order at each
    moment is compared with the final corrected result.

Every boat sails the same polar at a different percentage of it — which is the
case the estimator has to survive, because the app models the whole fleet with
one polar and cannot know that one boat is having a bad day.

    python scripts/verify_leaderboard.py --twd 20 --tws 12
    python scripts/verify_leaderboard.py --boats 6 --keep     # leave it to look at

--keep leaves the race in the database so it can be opened in the browser and
scrubbed through with the real chart. Otherwise everything it created is removed.
"""
from __future__ import annotations

import argparse
import math
import os
import sys
import time
from datetime import datetime, timedelta

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _ROOT)
sys.path.insert(0, _HERE)

os.environ.setdefault("RO_INITIAL_ADMIN_PASSWORD", "verify-only")

import simulate_trackers as sim            # noqa: E402


def sail_fleet(data_dir, course_no, polar, twd, tws, fleet, gun, fix_s, tack_min):
    """Sail every boat offline. Returns per-boat fixes and the true finish time.

    The finish is taken where the boat's track actually crosses the line, not
    where it reaches a waypoint: that is what the app detects, and it is the
    number every estimate in here is scored against.
    """
    rows = sim.load_polar_rows(os.path.join(data_dir, "polars", polar))
    _no, pts, line_o = sim.load_course(data_dir, course_no)
    path = sim.build_path(pts, line_o, True)
    line_a = line_o or (52.8791166667, -4.3993333333)
    line_b = (sim.BRIDGE_LAT, sim.BRIDGE_LON)

    def side(p):
        """Which side of the (infinite) start/finish line a point falls."""
        return math.copysign(1.0, (line_b[0] - line_a[0]) * (p[1] - line_a[1])
                             - (line_b[1] - line_a[1]) * (p[0] - line_a[0]))

    out = {}
    tack_s = tack_min * 60.0 if tack_min > 0 else 1e9
    for i, (dev, pct, bias) in enumerate(fleet):
        boat = sim.SailingBoat(dev, path, pct, tack_s, rows, 0.0, upwind_bias=bias)
        fixes, t, next_fix = [], 0.0, 0.0
        was, finish_t, marks_done = side(boat.pos), None, False
        while boat.step(t, 1.0, twd, tws) and t < 4 * 3600:
            t += 1.0
            now_side = side(boat.pos)
            if boat.wp >= len(path) - 2:
                marks_done = True
            if marks_done and finish_t is None and now_side != was:
                finish_t = t
            was = now_side
            if t >= next_fix:
                next_fix += fix_s
                fixes.append({"t": gun + t, "lat": boat.pos[0], "lon": boat.pos[1],
                              "sog": boat.speed_kn, "cog": boat.heading})
        out[dev] = {"fixes": fixes, "finish_s": finish_t, "pct": pct, "bias": bias,
                    "distance_nm": boat.distance_m / 1852.0, "tacks": boat.tacks,
                    "arrived": boat.finished}
    return out, path


def build_race(ro, track, course_no, gun, sailed, ratings):
    """Put the sailed fleet into the database as a race that has been run."""
    now = datetime.now().isoformat(timespec="seconds")
    # races.start_time is the WARNING signal; the app puts the gun five minutes
    # after it, and course progress ignores every fix before the gun. Storing the
    # gun here instead cost an afternoon: the first five minutes of track were
    # discarded, mark 1 was rounded inside them, and because the walk is
    # sequential every later mark stalled too — the whole fleet read 0/7.
    gun_iso = (datetime.fromtimestamp(gun) - timedelta(minutes=5)).isoformat(timespec="seconds")
    created = {"boats": [], "trackers": [], "race": None}
    with ro.get_db() as db:
        cur = db.execute("INSERT INTO races (name, course_no, start_time, created_at)"
                         " VALUES (?, ?, ?, ?)",
                         (f"VERIFY — estimator check {int(gun)}", course_no, gun_iso, now))
        race_id = int(cur.lastrowid)
        created["race"] = race_id
        for dev, info in sailed.items():
            irc, ytc = ratings[dev]
            cur = db.execute("INSERT INTO boats (boat_name, sail_no, status, irc_rating,"
                             " ytc_rating, created_at, updated_at)"
                             " VALUES (?, ?, 'ACTIVE', ?, ?, ?, ?)",
                             (f"Verify {dev}", dev, irc, ytc, now, now))
            boat_id = int(cur.lastrowid)
            created["boats"].append(boat_id)
            db.execute("INSERT INTO entries (race_id, boat_id, boat_name, sail_no, status,"
                       " manual_irc_rating, manual_ytc_rating)"
                       " VALUES (?, ?, ?, ?, 'RACING', ?, ?)",
                       (race_id, boat_id, f"Verify {dev}", dev, irc, ytc))
            db.execute("INSERT INTO trackers (unique_id, label, boat_id, active, updated_at)"
                       " VALUES (?, ?, ?, 1, ?)", (dev, dev, boat_id, now))
            created["trackers"].append(dev)
        db.commit()
    # Clear anything left under these ids first. A previous run that died before
    # its cleanup leaves a whole race of fixes on the same device ids, and the
    # progress walk then reads two races interleaved — which looks exactly like
    # a broken estimator and is not.
    with track.get_track_db() as db:
        db.executemany("DELETE FROM track_positions WHERE unique_id = ?",
                       [(d,) for d in sailed])
        db.commit()
    for i, (dev, info) in enumerate(sailed.items()):
        track.insert_positions(
            [{"device_id": 900 + i, "unique_id": dev, "name": dev, "boat_id": None,
              "lat": f["lat"], "lon": f["lon"], "speed_kn": f["sog"],
              "course_deg": f["cog"], "fix_time": f["t"], "server_time": f["t"]}
             for f in info["fixes"]], retention_days=3650)
    return race_id, created


def cleanup(ro, track, created):
    with ro.get_db() as db:
        db.execute("DELETE FROM entries WHERE race_id = ?", (created["race"],))
        db.execute("DELETE FROM races WHERE id = ?", (created["race"],))
        for uid in created["trackers"]:
            db.execute("DELETE FROM trackers WHERE unique_id = ?", (uid,))
        for bid in created["boats"]:
            db.execute("DELETE FROM boats WHERE id = ?", (bid,))
        db.commit()
    with track.get_track_db() as db:
        for uid in created["trackers"]:
            db.execute("DELETE FROM track_positions WHERE unique_id = ?", (uid,))
        db.commit()


def pctile(values, p):
    if not values:
        return float("nan")
    s = sorted(values)
    return s[min(len(s) - 1, int(round((len(s) - 1) * p)))]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--course", type=int, default=1)
    ap.add_argument("--polar", default="J122.txt")
    ap.add_argument("--twd", type=float, default=20.0, help="Wind direction (default %(default)s: gives this course a mix of beats and reaches)")
    ap.add_argument("--tws", type=float, default=12.0)
    ap.add_argument("--boats", type=int, default=5)
    ap.add_argument("--fix-interval", type=float, default=10.0, help="Seconds between fixes (default %(default)s)")
    ap.add_argument("--tack-minutes", type=float, default=3.0)
    ap.add_argument("--upwind-bias", type=float, default=0.0, metavar="FRACTION",
                    help="Spread the fleet across points of sail as well as speed: 0.08 makes the "
                         "first boat 8%% quicker upwind and 8%% slower down, the last the reverse. "
                         "This is the case a single pace factor cannot see coming (default off)")
    ap.add_argument("--keep", action="store_true", help="Leave the race in the database to look at in the browser")
    args = ap.parse_args()

    import app as ro                       # noqa: E402  (needs the env var above)
    from core import track                 # noqa: E402

    span = 18.0
    # Spread the fleet across speed, and (optionally) across shape: boat 1 strong
    # upwind, the last as strong downwind, the middle even.
    gun = time.time() - 4 * 3600           # a race that finished a while ago

    def bias_for(i):
        if args.upwind_bias <= 0 or args.boats < 2:
            return 0.0
        return args.upwind_bias * (1.0 - 2.0 * i / (args.boats - 1))
    # Device ids unique to this run, the way the race name already is.
    #
    # They used to be a plain VER-1..n, and the second run then died on
    # "UNIQUE constraint failed: trackers.unique_id" — because a run left with --keep,
    # or one that died before its cleanup, still has those ids registered. Clearing
    # them instead would be worse: a kept race resolves its boats' trackers through
    # exactly those rows, so tidying up for the new run would quietly strip the tracks
    # off the old race somebody had asked to keep.
    run_tag = int(gun)
    fleet = [(f"VER-{run_tag}-{i+1}", 1.0 - (span / 100.0) * i / max(1, args.boats - 1),
              bias_for(i)) for i in range(args.boats)]
    # A spread of ratings, deliberately not in the same order as boat speed, so a
    # corrected board that merely echoed the order on the water would show up.
    ratings = {dev: (round(0.94 + 0.035 * ((i * 3) % args.boats), 3),
                     int(1050 - 25 * ((i * 2) % args.boats)))
               for i, (dev, _p, _b) in enumerate(fleet)}

    print(f"Sailing {args.boats} boats, {args.polar} at {args.tws:.0f} kn from {args.twd:.0f}°T, "
          f"course {args.course}.")
    t0 = time.time()
    sailed, path = sail_fleet(_ROOT + "/data", args.course, args.polar, args.twd, args.tws,
                              fleet, gun, args.fix_interval, args.tack_minutes)
    if any(v["finish_s"] is None for v in sailed.values()):
        sys.exit("Some boats never crossed the finish line — the sim is wrong, not the estimator.")
    print(f"  sailed offline in {time.time()-t0:.1f}s")
    for dev, v in sailed.items():
        print(f"    {dev}  {v['pct']*100:5.1f}% polar  {v['bias']*100:+5.1f}% up/down   "
              f"{v['finish_s']/60:5.1f} min   "
              f"{v['distance_nm']:.2f} nm   {v['tacks']:2d} manoeuvres   {len(v['fixes'])} fixes")

    race_id, created = build_race(ro, track, args.course, gun, sailed, ratings)
    try:
        report(track, race_id, gun, sailed, ratings, args)
    finally:
        if args.keep:
            print(f"\nLeft race #{race_id} in the database: /public/race/{race_id}")
        else:
            cleanup(ro, track, created)
            print("\nCleaned up (use --keep to leave it for the browser).")


def report(track, race_id, gun, sailed, ratings, args):
    with track_wind(track, args.twd, args.tws):
        hist = track.race_track_history(race_id)
    boards = hist["boards"]
    by_entry = {b["entry_id"]: b for b in hist["boats"]}
    dev_of = {b["entry_id"]: b["sail_no"] for b in hist["boats"]}
    if not boards["times"]:
        sys.exit("No board snapshots — the race produced no progress.")

    truth = {dev: v["finish_s"] for dev, v in sailed.items()}
    print(f"\nBoard snapshots: {len(boards['times'])} every {boards['step']:.0f}s. "
          f"Estimates start after {boards['estimate_after_s']/60:.0f} min; "
          f"VMC window {boards['vmc_window_s']/60:.0f} min; "
          f"polar model {'available' if boards['polar_available'] else 'MISSING (pace will be blank)'}.")

    # --- how wrong is the projected finish time? ---------------------------
    buckets = [(0, 15), (15, 30), (30, 45), (45, 999)]
    errs = {("pace", b): [] for b in buckets}
    errs.update({("vmc", b): [] for b in buckets})
    for ix, t in enumerate(boards["times"]):
        elapsed_min = (t - gun) / 60.0
        for row in boards["rows"][ix]:
            eid, _pos, _rd, _nx, _tg, fin, _sog, est_p, est_v = row
            if fin:
                continue
            dev = dev_of.get(eid)
            true_s = truth.get(dev)
            if true_s is None:
                continue
            for name, est in (("pace", est_p), ("vmc", est_v)):
                if not est:
                    continue
                for b in buckets:
                    if b[0] <= elapsed_min < b[1]:
                        errs[(name, b)].append(abs(est - true_s) / true_s * 100.0)
    print("\nProjected finish time — absolute error against what the boat actually did:")
    print(f"  {'minutes into the race':<24}{'polar pace':>26}{'VMC':>26}")
    print(f"  {'':<24}{'median   90th      n':>26}{'median   90th      n':>26}")
    for b in buckets:
        label = f"{b[0]}-{b[1] if b[1] < 999 else '  '} min"
        cells = ""
        for name in ("pace", "vmc"):
            e = errs[(name, b)]
            cells += (f"{pctile(e,0.5):7.1f}% {pctile(e,0.9):7.1f}% {len(e):6d}"
                      if e else f"{'—':>7}  {'—':>7}  {'—':>6}")
            cells = cells.ljust(len(cells) + 4)
        print(f"  {label:<24}{cells}")

    # --- would a competitor have seen the right answer? --------------------
    for system, key in (("IRC", 0), ("YTC", 1)):
        final_all = sorted(truth, key=lambda d: corrected(truth[d], ratings[d], key))
        final = final_all
        print(f"\n{system} corrected order — final result: {' '.join(final)}")
        for name in ("pace", "vmc"):
            first_right, checked, right = None, 0, 0
            for ix, t in enumerate(boards["times"]):
                order = predicted_order(boards["rows"][ix], dev_of, ratings, key, name, truth)
                if order is None:
                    continue
                checked += 1
                want = [d for d in final_all if d in set(order)]
                if order == want:
                    right += 1
                    if first_right is None:
                        first_right = (t - gun) / 60.0
                else:
                    first_right = None          # must stay right from here on
            share = (100.0 * right / checked) if checked else float("nan")
            when = f"settled at {first_right:.0f} min" if first_right is not None else "never settled"
            print(f"  by {name:5}: correct at {share:5.1f}% of snapshots, {when}")


def predicted_order(rows, dev_of, ratings, key, method, truth):
    """The corrected order this snapshot would have shown.

    Only ranks boats the board could actually place; a snapshot where some boat
    has no estimate yet is still a real thing a competitor sees, so it is scored
    against the final order restricted to the same boats rather than thrown away.
    """
    out = []
    for row in rows:
        eid, _pos, _rd, _nx, _tg, fin, _sog, est_p, est_v = row
        dev = dev_of.get(eid)
        if dev is None:
            continue
        est = truth[dev] if fin else (est_p if method == "pace" else est_v)
        if not est:
            continue
        out.append((corrected(est, ratings[dev], key), dev))
    if len(out) < 2:
        return None                       # nothing you could call an order
    return [d for _c, d in sorted(out)]


def corrected(elapsed_s, rating, key):
    irc, ytc = rating
    return elapsed_s * irc if key == 0 else elapsed_s * 1000.0 / ytc


class track_wind:
    """Pin the wind the estimator models the course with, for a repeatable run.

    The app takes it from the hut weather station; here the boats were sailed in
    a wind chosen on the command line, and the point is to test the estimator,
    not the weather.
    """

    def __init__(self, track, twd, tws):
        self.track, self.twd, self.tws = track, twd, tws

    def __enter__(self):
        import core.weather_store as ws
        self._real = ws.latest_weather_sample
        ws.latest_weather_sample = lambda: {"twd": self.twd, "tws": self.tws}
        return self

    def __exit__(self, *exc):
        import core.weather_store as ws
        ws.latest_weather_sample = self._real
        return False


if __name__ == "__main__":
    main()
