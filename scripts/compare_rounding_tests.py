#!/usr/bin/env python3
"""Compare ways of deciding that a boat has rounded a mark, over recorded tracks.

Read-only. It opens the app's databases, walks stored fixes and prints tables; it
writes nothing and changes no app code.

This is the harness that chose the v0.247 rule, kept so the choice can be re-examined
once there is a season of real racing behind it rather than the handful of boat-races
available when it was written. **Its verdicts were made on 8 boat-races, 5 of them
simulator tracks, and every recorded finish among them had been recorded by GPS
auto-confirm — so part A had no independent ground truth whatsoever.** That is how the
inverted finishing direction fixed in v0.248 survived it. Re-run this once some finishes
have been timed by hand.

The question it answers is whether "was the boat close to the mark?" should be "has
the boat gone past the mark and set off on the next leg?" — given the app's job is
progress and finish detection, not adjudicating the rounding, which is the fleet's
business. Four detectors, all sharing the sequential walk and the finish rule (every
earlier mark rounded, then a line crossing in the finishing direction):

  radius      proximity alone: core.track._passed_mark, inside the mark's radius,
              measured against the path between fixes, with the 60 s gap gate. What
              the app did before v0.247.
  plane       a turn-gate at each mark: the plane whose normal is the average of the
              incoming and outgoing leg directions, crossed outbound within BOUND_M.
              **Rejected** — kept because the negative result is the useful part; see
              part A, where it finds none of the recorded finishes.
  cpa         closest approach: came within the neighbourhood, has since opened up,
              and the next mark has closed since. What the app now uses as its
              second test.
  radius+cpa  either of those. What the app ships.

Six parts. A is the floor: any candidate that loses a race-officer-recorded finish is
out, whatever else it does. B and C matter most, because the failures being solved — a
wide rounding, and a coarse reporting rate stepping over a mark — are not in the
recorded data, so tracks that currently work are broken on purpose. D and E are the
risk side: a looser test that fired early, or credited marks a boat never sailed, would
be worse than the problem. F guards the harness itself.

  A  agreement with the recorded finish times — but read its warning: a finish
     recorded by GPS auto-confirm is this detector's own output, not evidence
  B  the boat pushed radially clear of a mark: how wide a rounding still counts
  C  every Nth fix kept: what survives an asset tracker's reporting interval
  D  when each mark was counted, against the radius — early would be dangerous
  E  each track walked against courses it did not sail — does it credit marks freely
  F  does this harness still agree with the walk the app actually ships

    python scripts/compare_rounding_tests.py
    python scripts/compare_rounding_tests.py --part B --part C
    python scripts/compare_rounding_tests.py --depart 25 --neighbourhood 600

The tuning sweep is separate and answers "what should the departure margin be?":

    python scripts/compare_rounding_tests.py --sweep

Part F checks the harness still agrees with the app it is meant to be evaluating. The
walk here is a deliberate *copy* of core.track.boat_course_progress, so that the mark
test can be swapped without the script being able to change how the app behaves — and
a copy drifts. F runs the shipped function over the same cases and compares.
"""
from __future__ import annotations

import argparse
import math
import os
import sys
from datetime import datetime

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _ROOT)

os.environ.setdefault("RO_INITIAL_ADMIN_PASSWORD", "compare-only")

import app as ro                                       # noqa: E402
from core import track                                 # noqa: E402


DETECTORS = ("radius", "plane", "cpa", "radius+cpa")

# How far from the mark a turn-gate crossing is still believed. Generous next to the
# 50 m rounding radius, but still local to the mark: an unbounded plane is infinite
# and gets crossed by a boat elsewhere on the course, which is most of why the gate
# failed. Only used by the rejected `plane` detector.
BOUND_M = 250.0
# Below this the incoming and outgoing legs are too nearly opposite for their sum to
# give a usable direction (a windward mark with a leeward one next). Fall back to the
# plane across the incoming leg, which is the right answer there anyway.
DEGENERATE = 0.35


def unit(vx, vy):
    n = math.hypot(vx, vy)
    return (0.0, 0.0) if n == 0 else (vx / n, vy / n)


def dist_m(fix, mark):
    return track.haversine_nm(fix["lat"], fix["lon"], mark["lat"], mark["lon"]) * 1852.0


class Gates:
    """The turn-gate for each rounding mark in a course sequence (the `plane` test)."""

    def __init__(self, seq, line_mid):
        self.seq = seq
        self.lat0 = seq[0]["lat"] if seq else 0.0
        self.lon0 = seq[0]["lon"] if seq else 0.0
        self.normals = []
        for i in range(max(0, len(seq) - 1)):        # seq[-1] is the finish line
            before = (seq[i - 1]["lat"], seq[i - 1]["lon"]) if i > 0 else line_mid
            here = (seq[i]["lat"], seq[i]["lon"])
            after = (seq[i + 1]["lat"], seq[i + 1]["lon"])
            self.normals.append(self._normal(before, here, after))

    def _p(self, lat, lon):
        return track._project(lat, lon, self.lat0, self.lon0)

    def _normal(self, before, here, after):
        bx, by = self._p(*before)
        hx, hy = self._p(*here)
        ax, ay = self._p(*after)
        ix, iy = unit(hx - bx, hy - by)          # direction arriving at the mark
        ox, oy = unit(ax - hx, ay - hy)          # direction leaving it
        sx, sy = ix + ox, iy + oy
        if math.hypot(sx, sy) < DEGENERATE:
            return (ix, iy)
        return unit(sx, sy)

    def side(self, idx, fix):
        """Signed distance of a fix from mark idx's gate: <0 before, >0 past."""
        nx, ny = self.normals[idx]
        mx, my = self._p(self.seq[idx]["lat"], self.seq[idx]["lon"])
        px, py = self._p(fix["lat"], fix["lon"])
        return (px - mx) * nx + (py - my) * ny

    def crossed(self, idx, prev, fix):
        """Did the leg prev->fix cross mark idx's gate outbound, near the mark?"""
        if prev is None:
            return False
        s0, s1 = self.side(idx, prev), self.side(idx, fix)
        if not (s0 < 0.0 <= s1):
            return False
        span = s1 - s0
        f = 0.0 if span == 0 else (-s0 / span)
        cx = prev["lat"] + f * (fix["lat"] - prev["lat"])
        cy = prev["lon"] + f * (fix["lon"] - prev["lon"])
        m = self.seq[idx]
        return track.haversine_nm(cx, cy, m["lat"], m["lon"]) * 1852.0 <= BOUND_M


def walk(fixes, seq, line, course_ref, radius, not_before, detector, gates,
         neighbourhood=None, depart=None):
    """The sequential walk, with the mark test swapped out.

    A deliberate copy of core.track.boat_course_progress rather than an import with a
    hook: this script must not be able to change how the app behaves, and a comparison
    is only worth anything if the `radius` baseline really is the old code and
    `radius+cpa` really is the new one. Part F guards the copy against drift.
    """
    depart = track.MARK_DEPART_M if depart is None else depart
    total = len(seq)
    # Which way across the line is finishing: the last mark rounded, as the app does.
    finish_ref = track.finish_direction_point(seq, line[0], line[1], course_ref)
    if total == 0 or not fixes:
        return {"rounded": 0, "total": total, "finish_t": None, "at": []}
    use_radius = detector in ("radius", "radius+cpa")
    use_plane = detector == "plane"
    use_cpa = detector in ("cpa", "radius+cpa")
    idx, prev, finish = 0, None, None
    at = []                             # when each mark was counted
    approach = track.MarkApproach()
    for f in fixes:
        while idx < total - 1:
            mark = seq[idx]
            r = mark.get("radius_m") or radius
            near = neighbourhood
            if near is None:
                near = mark.get("neighbourhood_m") or track.mark_neighbourhood_m(r)
            hit = False
            if use_radius and track._passed_mark(prev, f, mark, r):
                hit = True
            if not hit and use_plane and gates.crossed(idx, prev, f):
                hit = True
            if not hit and use_cpa:
                d, d_next = track._mark_distances(f, seq, idx)
                approach.note(d, d_next)
                hit = approach.departed(d, d_next, near, depart)
            if not hit:
                break
            idx += 1
            at.append(f["t"])
            approach.reset()
        if idx >= total - 1 and prev is not None:
            if not_before is None or f["t"] >= not_before + track.MIN_FINISH_ELAPSED_S:
                cross = track.detect_finish_crossing([prev, f], line[0], line[1],
                                                     finish_ref, None, 0.0)
                if cross:
                    finish = cross
                    break
        prev = f
    return {"rounded": total if finish else idx, "total": total,
            "finish_t": finish["t"] if finish else None, "at": at}


def load_cases():
    """Boat-races with both stored fixes and a race-officer finish time.

    Returns (cases, skipped). A finish time with no fixes inside the race window is
    not a test case — the club's tracker history is far shorter than its race history,
    and feeding a 2025 race five days of 2026 sailing measures nothing.
    """
    with ro.get_db() as db:
        races = db.execute("SELECT * FROM races WHERE start_time IS NOT NULL "
                           "AND start_time <> ''").fetchall()
        by_race = {r["id"]: db.execute("SELECT * FROM entries WHERE race_id = ?",
                                       (r["id"],)).fetchall() for r in races}
    cases, skipped = [], []
    for race in races:
        seq = track.course_rounding_sequence(race)
        line = track.race_finish_line_points(race)
        if not seq or line is None:
            continue
        start = ro.race_first_start_dt(race)
        if not start:
            continue
        not_before = start.timestamp()
        line_mid = ((line[0][0] + line[1][0]) / 2.0, (line[0][1] + line[1][1]) / 2.0)
        for e in by_race[race["id"]]:
            if not e["finish_time"]:
                continue
            try:
                recorded = datetime.fromisoformat(str(e["finish_time"])).timestamp()
            except ValueError:
                continue
            fixes = track.positions_for_entry_since(e, not_before, recorded + 1800.0)
            if len(fixes) < 20:
                skipped.append((int(race["id"]), str(e["boat_name"])[:14], len(fixes)))
                continue
            cases.append({
                "race_id": int(race["id"]), "race": str(race["name"])[:22],
                "course_no": race["course_no"],
                "boat": str(e["boat_name"])[:14], "fixes": fixes, "seq": seq, "line": line,
                "course_ref": track._course_ref_point(race), "not_before": not_before,
                "recorded": recorded,
                "source": str((e["finish_source"] if "finish_source" in e.keys()
                               else "") or "unknown"),
                "gates": Gates(seq, line_mid),
                "radius": track.track_config()["rounding_radius_m"],
            })
    return cases, skipped


def run(case, detector, fixes=None, **kw):
    return walk(fixes if fixes is not None else case["fixes"], case["seq"], case["line"],
                case["course_ref"], case["radius"], case["not_before"], detector,
                case["gates"], **kw)


def push_clear_of(fixes, mark, min_m):
    """Move the boat radially outward so it passes no closer than min_m to the mark.

    A wide rounding manufactured from a track that really did round tight. Radial, so
    the shape survives and the arc simply gets wider. Note it flatters the radius test
    at large offsets: pushing two neighbouring fixes apart can leave a straight-line
    interpolation that cuts the corner nearer the mark than the boat ever went.
    """
    out = []
    for f in fixes:
        d = dist_m(f, mark)
        g = dict(f)
        if 0 < d < min_m:
            scale = min_m / d
            g["lat"] = mark["lat"] + (f["lat"] - mark["lat"]) * scale
            g["lon"] = mark["lon"] + (f["lon"] - mark["lon"]) * scale
        out.append(g)
    return out


def closest_approach(fixes, mark):
    return min(dist_m(f, mark) for f in fixes)


def median_interval(fixes):
    gaps = sorted(b["t"] - a["t"] for a, b in zip(fixes, fixes[1:]))
    return gaps[len(gaps) // 2] if gaps else 0.0


def hms(t):
    return datetime.fromtimestamp(t).strftime("%H:%M:%S") if t else "none"


def rule(title):
    print()
    print("=" * 100)
    print(title)
    print("=" * 100)


# ---------------------------------------------------------------------------
# The parts
# ---------------------------------------------------------------------------

def part_a(cases, opts):
    rule("A. AGREEMENT WITH RECORDED FINISHES  (error = detected finish - the recorded time)")
    independent = [c for c in cases if not str(c["source"]).startswith("gps")]
    print("A recorded finish is only evidence if a person timed it. Where it was recorded")
    print("by GPS auto-confirm the stored time *is* this detector's own output, so agreeing")
    print("with it to the second means nothing at all.")
    print()
    print(f"  timed by a person, independent: {len(independent)} of {len(cases)}")
    if not independent:
        print("  NOTHING HERE IS INDEPENDENT. Part A is a self-consistency check only —")
        print("  it cannot tell a correct finish from a systematically wrong one. This is")
        print("  how a 25-46 s error in the finishing *direction* survived unnoticed until")
        print("  a race officer walked a race through the replay by eye (fixed in v0.248).")
        print("  Time some finishes by hand and re-run.")
    print()
    print(f"{'race':<6}{'boat':<15}{'src':<9}{'fixes':>6}{'rate':>7}   "
          + "".join(f"{d:>18}" for d in DETECTORS))
    totals = {d: {"found": 0, "err": []} for d in DETECTORS}
    for c in cases:
        cells = []
        for d in DETECTORS:
            r = run(c, d, **opts)
            if r["finish_t"] is None:
                cells.append(f"{r['rounded']}/{r['total']} no finish".rjust(18))
            else:
                err = r["finish_t"] - c["recorded"]
                totals[d]["found"] += 1
                totals[d]["err"].append(err)
                cells.append(f"{r['rounded']}/{r['total']} {err:+.0f}s".rjust(18))
        print(f"{c['race_id']:<6}{c['boat']:<15}{c['source'][:8]:<9}{len(c['fixes']):>6}"
              f"{median_interval(c['fixes']):>6.0f}s   " + "".join(cells))
    print()
    for d in DETECTORS:
        errs = sorted(abs(e) for e in totals[d]["err"])
        med = f"{errs[len(errs) // 2]:.0f}s" if errs else "-"
        print(f"  {d:<12} finishes found {totals[d]['found']}/{len(cases)}"
              f"   median |error| {med}")


def part_b(cases, opts):
    rule("B. A WIDE ROUNDING  (boat pushed clear of one mark; does that mark still count?)")
    offsets = [50, 100, 200, 400, 800]
    key = "/".join(d[:3] for d in DETECTORS)
    print(f"{'race':<6}{'boat':<15}{'mark':<6}{'was':>6}   "
          + "".join(f"{str(o) + 'm':>17}" for o in offsets))
    print(f"{'':<27}{'':>6}   " + "".join(f"{key:>17}" for _ in offsets))
    for c in cases:
        if run(c, "radius", **opts)["rounded"] < 1:
            continue
        mark = c["seq"][0]
        print(f"{c['race_id']:<6}{c['boat']:<15}{mark['code']:<6}"
              f"{closest_approach(c['fixes'], mark):>5.0f}m   ", end="")
        for o in offsets:
            spoiled = push_clear_of(c["fixes"], mark, float(o))
            got = ["Y" if run(c, d, fixes=spoiled, **opts)["rounded"] > 0 else "n"
                   for d in DETECTORS]
            print(f"{'/'.join(got):>17}", end="")
        print()
    print()
    print("  Y = the mark counted.  n = the boat stalled there, which in service also")
    print("  means its GPS finish never arrives.")


def part_c(cases, opts):
    rule("C. COARSE REPORTING  (keep every Nth fix; marks rounded, F when the finish was found)")
    keeps = [1, 3, 6, 12]
    print(f"{'race':<6}{'boat':<15}" + "".join(f"{str(k) + 'x':>26}" for k in keeps))
    print(f"{'':<21}" + "".join(f"{' / '.join(d[:3] for d in DETECTORS):>26}" for _ in keeps))
    for c in cases:
        print(f"{c['race_id']:<6}{c['boat']:<15}", end="")
        for k in keeps:
            thinned = c["fixes"][::k]
            cell = []
            for d in DETECTORS:
                r = run(c, d, fixes=thinned, **opts)
                cell.append(f"{r['rounded']}{'F' if r['finish_t'] else ''}")
            print(f"{' / '.join(cell):>26}", end="")
        print()
    print()
    print(f"  Multiply each boat's own rate (part A) by the column. Note the "
          f"{track.MARK_SEGMENT_MAX_GAP_S:.0f}s gate on")
    print("  the radius test's path measurement — past that it can only use the fixes themselves.")


def part_d(cases, opts):
    rule("D. IS IT THE SAME ROUNDING?  (when each mark was counted, seconds after the start)")
    print("Firing on the approach rather than the rounding would read early, and a boat")
    print("would gain marks it had not sailed. Late is the safe direction.")
    print()
    print(f"{'race':<6}{'boat':<15}{'mark':>6}{'radius':>10}{'cpa':>10}{'diff':>9}")
    diffs = []
    for c in cases:
        a = run(c, "radius", **opts)["at"]
        b = run(c, "cpa", **opts)["at"]
        for i in range(min(len(a), len(b))):
            d = b[i] - a[i]
            diffs.append(d)
            flag = "  <-- EARLY" if d < -20 else ""
            print(f"{c['race_id']:<6}{c['boat']:<15}{c['seq'][i]['code']:>6}"
                  f"{a[i] - c['not_before']:>9.0f}s{b[i] - c['not_before']:>9.0f}s"
                  f"{d:>+8.0f}s{flag}")
    if diffs:
        print()
        print(f"  {len(diffs)} marks compared. worst early {min(diffs):+.0f}s, "
              f"worst late {max(diffs):+.0f}s, median {sorted(diffs)[len(diffs) // 2]:+.0f}s")


def part_e(cases, opts):
    rule("E. SPECIFICITY  (each boat's track walked against courses it did NOT sail)")
    print("A looser test buys tolerance with credulity. A track run against the wrong")
    print("course should score badly under every detector.")
    print()
    # By course number, not race id: several races share a course, so excluding the
    # races these boats sailed still lets their own course back in through another
    # race that used it — and it then scores 7/7, which reads as a catastrophic
    # specificity result when it is really the boats sailing the course correctly.
    sailed = sorted({int(c["course_no"]) for c in cases if c["course_no"] is not None})
    with ro.get_db() as db:
        if sailed:
            others = db.execute(
                "SELECT * FROM races WHERE course_no IS NOT NULL AND course_no NOT IN (%s)"
                " GROUP BY course_no ORDER BY course_no" % ",".join("?" * len(sailed)),
                tuple(sailed)).fetchall()
        else:
            others = db.execute("SELECT * FROM races WHERE course_no IS NOT NULL"
                                " GROUP BY course_no ORDER BY course_no").fetchall()
    print(f"  (excluding course{'s' if len(sailed) != 1 else ''} "
          f"{', '.join(str(s) for s in sailed) or 'none'}, which these boats did sail)")
    print()
    foreign = []
    for r in others:
        fseq = track.course_rounding_sequence(r)
        fline = track.race_finish_line_points(r)
        if len(fseq) >= 3 and fline is not None:
            mid = ((fline[0][0] + fline[1][0]) / 2.0, (fline[0][1] + fline[1][1]) / 2.0)
            foreign.append((int(r["course_no"]), fseq, fline,
                            track._course_ref_point(r), Gates(fseq, mid)))
    if not foreign:
        print("  No other course in the database to walk these tracks against.")
        return
    print(f"{'course':<8}{'legs':>5}   " + "".join(f"{d:>14}" for d in DETECTORS))
    for course_no, fseq, fline, fref, fgates in foreign[:8]:
        worst = {}
        for d in DETECTORS:
            worst[d] = max(
                walk(c["fixes"], fseq, fline, fref, c["radius"], c["not_before"],
                     d, fgates, **opts)["rounded"] for c in cases)
        print(f"{course_no:<8}{len(fseq):>5}   "
              + "".join(f"{str(worst[d]) + '/' + str(len(fseq)):>14}" for d in DETECTORS))
    print()
    print("  Worst case across the fleet: the most marks any boat was credited with on a")
    print("  course it never sailed. Lower is better; the last leg is the finish line.")


def part_f(cases, opts):
    rule("F. DOES THIS HARNESS STILL MATCH THE APP?")
    print("The walk above is a copy, so it can drift from core.track. This runs the")
    print("shipped boat_course_progress over the same cases and compares.")
    print()
    if opts:
        print("  Skipped: --neighbourhood/--depart override the shipped values, so a")
        print("  difference here would be the override rather than drift.")
        return
    bad = 0
    for c in cases:
        mine = run(c, "radius+cpa")
        theirs = track.boat_course_progress(
            c["fixes"], c["seq"], c["line"][0], c["line"][1], c["course_ref"],
            c["radius"], c["not_before"], track.MIN_FINISH_ELAPSED_S)
        # The finish *time* as well as whether there is one. Comparing only the
        # existence let v0.248's finishing-direction fix through unnoticed: both
        # walks found a finish, 34 s apart.
        gap = None
        if mine["finish_t"] is not None and theirs["finish_time"] is not None:
            gap = abs(mine["finish_t"] - theirs["finish_time"])
        same = (mine["rounded"] == theirs["rounded"]
                and (mine["finish_t"] is None) == (theirs["finish_time"] is None)
                and (gap is None or gap <= 1.0))
        if not same:
            bad += 1
            print(f"  DRIFT  race {c['race_id']} {c['boat']}: harness "
                  f"{mine['rounded']}/{mine['total']} finish={hms(mine['finish_t'])}, "
                  f"app {theirs['rounded']}/{theirs['total']} "
                  f"finish={hms(theirs['finish_time'])}"
                  + (f"  ({gap:.0f}s apart)" if gap else ""))
    if bad:
        print(f"\n  {bad} of {len(cases)} disagree — reconcile walk() with "
              f"core.track.boat_course_progress before trusting anything above.")
    else:
        print(f"  OK: all {len(cases)} agree with the shipped walk.")


def sweep(cases):
    rule("TUNING THE DEPARTURE MARGIN  (latency against how wide a rounding still counts)")
    print("Smaller margins look free here, but the case that would punish them — a boat")
    print("tacking up to a windward mark, distance oscillating — is barely present in")
    print("simulator tracks. Real beating data is what should settle this.")
    print()
    print(f"{'depart':>8}{'neighbourhood':>15}   {'median lag vs radius':>21}"
          f"{'widest rounding held':>22}")
    for depart in (25.0, 50.0, 100.0, 200.0):
        lags = []
        for c in cases:
            a = run(c, "radius")["at"]
            b = run(c, "cpa", depart=depart)["at"]
            lags += [b[i] - a[i] for i in range(min(len(a), len(b)))]
        widest = 0
        for off in (100, 200, 400, 600, 800):
            if all(run(c, "cpa", fixes=push_clear_of(c["fixes"], c["seq"][0], float(off)),
                       depart=depart)["rounded"] > 0 for c in cases):
                widest = off
        med = f"{sorted(lags)[len(lags) // 2]:+.0f}s" if lags else "-"
        print(f"{depart:>7.0f}m{track.MARK_NEIGHBOURHOOD_M:>14.0f}m   {med:>21}"
              f"{str(widest) + ' m':>22}")


PARTS = {"A": part_a, "B": part_b, "C": part_c, "D": part_d, "E": part_e, "F": part_f}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--part", action="append", choices=sorted(PARTS),
                    help="Run only these parts (repeatable). Default: all of them.")
    ap.add_argument("--neighbourhood", type=float, metavar="METRES",
                    help="Override the per-mark neighbourhood (default: the course's own).")
    ap.add_argument("--depart", type=float, metavar="METRES",
                    help=f"Override the departure margin (default {track.MARK_DEPART_M:.0f}).")
    ap.add_argument("--sweep", action="store_true",
                    help="Sweep the departure margin instead of running the parts.")
    args = ap.parse_args()

    opts = {}
    if args.neighbourhood is not None:
        opts["neighbourhood"] = args.neighbourhood
    if args.depart is not None:
        opts["depart"] = args.depart

    with ro.app.app_context():
        cases, skipped = load_cases()
        if not cases:
            print("No boat-race has both stored fixes and a race-officer finish time.")
            print("Nothing to compare: sail some races, or feed the database with")
            print("scripts/simulate_trackers.py --sail.")
            return 1
        if skipped:
            print(f"Excluded: {len(skipped)} boat-races have a recorded finish time but too")
            print("few stored fixes inside the race window — the tracker history is shorter")
            print("than the race history. None of them produced a phantom finish either.")
            print()
        print(f"{len(cases)} boat-races with a race-officer finish time. "
              f"Rounding radius {cases[0]['radius']} m; "
              f"neighbourhood {opts.get('neighbourhood', track.MARK_NEIGHBOURHOOD_M):.0f} m; "
              f"departure {opts.get('depart', track.MARK_DEPART_M):.0f} m.")
        if len(cases) < 20:
            print(f"NOTE: {len(cases)} cases is thin. Treat parts B and C (controlled")
            print("      perturbations) as the stronger evidence, and part A as a floor.")

        if args.sweep:
            sweep(cases)
            return 0
        for name in (args.part or sorted(PARTS)):
            PARTS[name](cases, opts)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
