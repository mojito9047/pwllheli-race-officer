#!/usr/bin/env python3
"""Feed Traccar with simulated boats sailing a real course, to test the whole
GPS-tracking + automated-finish chain end to end (script -> Traccar -> hut app
pull -> live map + finish detection). Unlike the app's built-in simulator, this
drives Traccar itself, so it exercises the real ingestion path.

It pushes positions using Traccar's **OsmAnd** protocol (a simple HTTP GET on
port 5055 by default) — no binary Queclink needed. Each boat starts on the
pre-start (harbour) side of the CHPSC start/finish line, crosses it onto the
course, sails the chosen course's marks in order, then returns and crosses the
finish line — so both the live map and (once a race is armed) GPS finish
detection can be watched with no hardware.

Standard library only. Reads data/marks.json + data/courses.json from the repo.

FIRST, in Traccar, add a device for each simulated boat with a matching unique
id (default SIM-1, SIM-2, ...), or enable unknown-device registration —
Traccar ignores positions from unknown ids. Then assign those trackers to boats
in the app (Settings -> GPS tracking), enter those boats in a race, and (for
finishes) arm GPS auto-finish on the race with a start time a few minutes ago.

Run it on the relay (Traccar on localhost:5055) with the repo's data/ available:
    python3 scripts/simulate_trackers.py --course 1 --devices SIM-1,SIM-2,SIM-3
or from the home LAN pointing at the relay:
    python3 scripts/simulate_trackers.py --url http://RELAY_IP:5055 --course 1

Useful options: --speed (knots, raise it to finish sooner), --interval (seconds
between fixes), --once (send one fix per device and exit — a connectivity test),
--loop (keep sailing the course), --no-finish (don't cross the line at the end).

Two extra modes support testing the push path (v0.189):

  --forward-to URL   Skip Traccar and POST Traccar-shaped forwarder JSON
                     ({"position":…, "device":…}) straight at the app's
                     /api/track/ingest, with --token for the shared secret.
                     Exercises the ingest endpoint, storage, finish detection
                     and the horn without needing a Traccar to reconfigure.
  --fast-near-line M Report every --fast-interval seconds within M metres of the
                     start/finish line and every --interval elsewhere — what a
                     real tracker would do if its rate is raised near the line.
                     This is the case the horn's timing actually depends on.

  --report-latency   Print fix-time -> accepted-at for each fix, and a summary,
                     so "how late is the horn" is a number rather than a feeling.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
import urllib.parse
import urllib.request

_HERE = os.path.dirname(os.path.abspath(__file__))
_DEFAULT_DATA = os.path.join(os.path.dirname(_HERE), "data")

# CHPSC start/finish line endpoints (must match core/appstate.py). The seaward
# end is the ODM mark "O"; the shore end is the surveyed bridge window. These are
# the fallback for the club line when data/start_finish.json cannot be read; the
# lines themselves come from that file — see load_finish_line.
BRIDGE_LAT = 52.0 + (52.9238 / 60.0)
BRIDGE_LON = -(4.0 + (24.0609 / 60.0))
DEFAULT_LINE_SEAWARD_MARK = "O"


def load_finish_line(data_dir, key=None):
    """Resolve a finish line from data/start_finish.json to its two endpoints.

    The club sails to more than one line: the default `psc` line runs from the ODM
    (mark O) to the bridge window, and `isora_plas_heli` runs from the Pwllheli
    Fairway Buoy (mark F) to a point off Plas Heli — 1172 m away from the ODM. The
    boats start and finish across whichever line the *race* is set to, so the
    simulator has to be told which one, or it sails to the club line and a race set
    to the ISORA line never sees a finish at all. On course 1 the simulated path
    crossed the ISORA line exactly zero times.

    Returns (key, label, seaward, shore) with both ends as (lat, lon).
    """
    with open(os.path.join(data_dir, "start_finish.json"), encoding="utf-8") as f:
        lines = json.load(f).get("finish_lines") or []
    with open(os.path.join(data_dir, "marks.json"), encoding="utf-8") as f:
        marks = json.load(f)["marks"]
    if not lines:
        sys.exit("No finish_lines in start_finish.json")
    chosen = None
    if key:
        chosen = next((l for l in lines if str(l.get("key")) == str(key)), None)
        if chosen is None:
            available = ", ".join(str(l.get("key")) for l in lines)
            sys.exit(f"Finish line {key!r} not found. Available: {available}")
    else:
        chosen = next((l for l in lines if l.get("default")), lines[0])
    code = str((chosen.get("seaward") or {}).get("mark") or DEFAULT_LINE_SEAWARD_MARK)
    mark = marks.get(code) or {}
    if mark.get("lat") is None:
        sys.exit(f"Finish line {chosen.get('key')!r} needs mark {code!r}, "
                 f"which has no position in marks.json")
    shore = chosen.get("shore") or {}
    if shore.get("lat") is None:
        sys.exit(f"Finish line {chosen.get('key')!r} has no shore-end position")
    return (str(chosen.get("key")),
            f"{code} to {shore.get('label') or 'shore end'}",
            (float(mark["lat"]), float(mark["lon"])),
            (float(shore["lat"]), float(shore["lon"])))

EARTH_M = 6371000.0


def haversine_m(a, b):
    (la1, lo1), (la2, lo2) = a, b
    p1, p2 = math.radians(la1), math.radians(la2)
    dp = math.radians(la2 - la1)
    dl = math.radians(lo2 - lo1)
    x = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_M * math.atan2(math.sqrt(x), math.sqrt(1 - x))


def bearing_deg(a, b):
    (la1, lo1), (la2, lo2) = a, b
    p1, p2 = math.radians(la1), math.radians(la2)
    dl = math.radians(lo2 - lo1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def lerp(a, b, f):
    return (a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f)


def load_course(data_dir, course_no):
    with open(os.path.join(data_dir, "marks.json"), encoding="utf-8") as f:
        marks = json.load(f)["marks"]
    with open(os.path.join(data_dir, "courses.json"), encoding="utf-8") as f:
        courses = json.load(f)["courses"]
    by_no = {int(c["course_no"]): c for c in courses}
    if course_no is None:
        course = courses[0]
    elif int(course_no) in by_no:
        course = by_no[int(course_no)]
    else:
        sys.exit(f"Course {course_no} not found. Available: {sorted(by_no)}")
    # Resolve the ordered course marks to lat/lon (skip compound parents w/o coords).
    pts = []
    for item in course["marks"]:
        code = item["mark"] if isinstance(item, dict) else item
        m = marks.get(str(code))
        if m and m.get("lat") is not None and m.get("lon") is not None:
            pts.append((float(m["lat"]), float(m["lon"])))
    o = marks.get("O") or {}
    line_o = (float(o["lat"]), float(o["lon"])) if o.get("lat") is not None else None
    return int(course["course_no"]), pts, line_o


def build_path(course_pts, line_o, do_finish, line_shore=None, prestart_metres=None):
    """Waypoints for one boat: pre-start (harbour side) -> across the line ->
    course marks -> back across the finish line -> harbour side.

    ``line_o`` and ``line_shore`` are the two ends of the line being sailed to. The
    shore end defaults to the club line's bridge window, so a caller that does not
    care (verify_leaderboard.py) behaves exactly as before.
    """
    line_a = line_o or (52.8791166667, -4.3993333333)
    line_b = line_shore or (BRIDGE_LAT, BRIDGE_LON)
    mid = ((line_a[0] + line_b[0]) / 2.0, (line_a[1] + line_b[1]) / 2.0)
    course_marks = course_pts or [(mid[0] + 0.01, mid[1])]
    # Leave and return **perpendicular to the line**, through its midpoint, so the
    # boat crosses the segment between the two ends squarely — as a real boat
    # finishing does. Offsetting merely "away from the course centroid" can run
    # nearly parallel to the line, and the boat then crosses its *extension*
    # beyond the ODM/bridge end, which is not a finish (seen in testing: two of
    # three simulated boats sailed the whole course and were never finished).
    # Work in metres so the offsets below mean what they say: a degree of
    # longitude here is only ~0.6 of a degree of latitude.
    m_per_deg_lat = 111_320.0
    m_per_deg_lon = 111_320.0 * math.cos(math.radians(mid[0]))
    dlat, dlon = (line_b[0] - line_a[0]), (line_b[1] - line_a[1])
    # The line's own direction in metres, then a true perpendicular to it: rotating
    # (east, north) by 90 degrees gives (-north, east). This used to be written
    # (-east, north), which is a *reflection* rather than a rotation and is only
    # perpendicular when the line happens to run at 45 degrees. On the club line
    # (113 m east, 328 m north) it came out at 38 degrees to the line, so the boat
    # left and returned at a slant: mark O, which is an END of that line, measured
    # as 137 m off it. The comment above is what was intended all along.
    line_east = dlon * m_per_deg_lon
    line_north = dlat * m_per_deg_lat
    px, py = -line_north, line_east            # perpendicular (east, north), metres
    pn = math.hypot(px, py) or 1.0
    px, py = px / pn, py / pn
    def offset(metres):
        """A point `metres` from the line's midpoint along the perpendicular."""
        return (mid[0] + (py * metres) / m_per_deg_lat,
                mid[1] + (px * metres) / m_per_deg_lon)
    def perp_offset_m(point):
        """How far `point` sits off the line, in metres, signed like offset()."""
        east = (point[1] - mid[1]) * m_per_deg_lon
        north = (point[0] - mid[0]) * m_per_deg_lat
        return east * px + north * py

    # A mark sitting ON the line cannot say which side to approach from, and the
    # last mark of most club courses is the ODM — an end of the line itself. The app
    # has the same problem and solves it the same way, by stepping back to the
    # previous mark (core.track.finish_direction_point). 30 m is comfortably inside
    # the 50 m rounding radius, so any mark that is genuinely off the line qualifies.
    ON_THE_LINE_M = 30.0

    def side_of_course():
        """Which side the course as a whole lies on — so the boats wait on the other.

        The centroid, not the first mark. Aiming the pre-start by the first mark put
        the boats on the *seaward* side for any course starting at a mark north-east
        of the line (courses 4, 15 and 26 among them): they then began on the course
        side, which is neither where a boat waits nor where it can start from.
        """
        lat = sum(p[0] for p in course_marks) / len(course_marks)
        lon = sum(p[1] for p in course_marks) / len(course_marks)
        d = perp_offset_m((lat, lon))
        return 1.0 if d > 0 else -1.0

    def side_of_last():
        for point in reversed(course_marks):
            d = perp_offset_m(point)
            if abs(d) >= ON_THE_LINE_M:
                return 1.0 if d > 0 else -1.0
        return 1.0

    # Which way the boat starts and which way it finishes are two different
    # questions, and using one answer for both was wrong.
    #
    # This used to orient everything by the centroid of the course marks. Since
    # v0.248 the app decides a finish is a crossing "from the side the LAST mark is
    # on" (core.track.finish_direction_point), and on a course whose marks straddle
    # the line's extension the last mark and the centroid are on opposite sides — 8
    # of the club's 12 courses, course 1 among them. The simulated boat therefore
    # made its finishing pass in exactly the direction the app rejects, and sailed
    # the whole course without ever being finished.
    #
    # So: wait on the side away from the course and cross onto it to start; finish by
    # coming from the LAST mark's side and passing through to the other.
    course_side = side_of_course()
    last_side = side_of_last()

    # How far off the line the boats wait. 275 m rather than the old 450 because the
    # old figure was measured along an axis 38 degrees off the perpendicular, so it
    # only ever put them ~277 m from the club line — now that the axis is a true
    # perpendicular, 450 would start them half again as far out and add a minute of
    # motoring before the gun.
    prestart_m = float(prestart_metres or 275.0)
    harbour = offset(-course_side * prestart_m)   # holding position, pre-start side
    # Aim 50 m *through* the line's midpoint rather than at the line itself. A
    # waypoint sitting exactly on the line means the boat touches it and turns
    # away, and whether that registers as a crossing then depends on where a fix
    # happens to land — one demo boat sailed the whole course and was never
    # finished. Passing squarely through the middle is also what a real boat does.
    start_through = offset(course_side * 50.0)   # 50 m onto the course side
    path = [harbour, start_through] + course_marks
    if do_finish:
        # Come back to the last mark's side of the midpoint *first*, then pass
        # through. The last course mark is often an END of the line itself, so
        # heading straight from it starts the leg already on the line and no pair of
        # fixes cleanly straddles it. Approaching from 50 m out and leaving 50 m the
        # other side gives a square 100 m pass, in the direction a finish is judged.
        path += [offset(last_side * 50.0), offset(-last_side * 50.0),
                 offset(-last_side * prestart_m)]
    return path


# ---------------------------------------------------------------------------
# Sailing model (--sail)
#
# The default mode walks each boat along the rhumb line at a fixed speed, which
# is fine for exercising ingestion and finish detection but is not a boat: it
# sails dead upwind, never tacks, and covers the course in the straight-line
# distance. That makes it useless for judging the predicted leaderboard, whose
# whole job is to reason about how long the *remaining* legs will take.
#
# In --sail mode a boat obeys its polar. It cannot point closer than the polar's
# minimum TWA, so a windward leg is beaten in tacks; it will not sail dead
# downwind either, so a run is gybed. Speed comes from the polar at the angle
# actually being sailed, scaled by that boat's percentage — so a fleet can be
# given real performance differences and the estimator asked to sort them out.
#
# The polar reading here mirrors core/polars.py deliberately; a test pins the two
# together, because a simulator that read the polar differently from the app
# would make the comparison meaningless.
# ---------------------------------------------------------------------------

def load_polar_rows(path):
    """Read an Expedition-style polar: TWS then (TWA, boat speed) pairs a row."""
    rows = []
    with open(path, encoding="utf-8-sig") as f:
        for line in f:
            parts = [p for p in line.replace(",", "\t").split("\t") if p.strip()]
            try:
                nums = [float(p) for p in parts]
            except ValueError:
                continue                      # a header or comment line
            if len(nums) < 3:
                continue
            pts = []
            for i in range(1, len(nums) - 1, 2):
                twa, bsp = nums[i], nums[i + 1]
                if twa > 0 and bsp > 0:
                    pts.append({"twa": twa, "bsp": bsp})
            if pts:
                rows.append({"tws": nums[0], "points": sorted(pts, key=lambda p: p["twa"])})
    return sorted(rows, key=lambda r: r["tws"])


def _interp(points, x):
    """Linear interpolation over [(x, y)], flat outside the range."""
    if not points:
        return None
    pts = sorted(points)
    if x <= pts[0][0]:
        return pts[0][1]
    if x >= pts[-1][0]:
        return pts[-1][1]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if x0 <= x <= x1:
            span = x1 - x0
            return y0 if span == 0 else y0 + (x - x0) / span * (y1 - y0)
    return None


def _by_tws(rows, tws, fn):
    """Interpolate a per-row quantity between the two bracketing wind speeds."""
    if not rows:
        return None
    if tws <= rows[0]["tws"]:
        return fn(rows[0])
    if tws >= rows[-1]["tws"]:
        return fn(rows[-1])
    for low, high in zip(rows, rows[1:]):
        if low["tws"] <= tws <= high["tws"]:
            a, b = fn(low), fn(high)
            if a is None or b is None:
                return None
            f = (tws - low["tws"]) / ((high["tws"] - low["tws"]) or 1)
            return a + f * (b - a)
    return None


def polar_speed(rows, twa, tws):
    """Boat speed in knots at a true wind angle — the speed actually sailed."""
    return _by_tws(rows, tws,
                   lambda r: _interp([(p["twa"], p["bsp"]) for p in r["points"]], abs(twa)))


def beat_angle(rows, tws):
    """Closest the boat can point: the polar's lowest usable TWA."""
    return _by_tws(rows, tws, lambda r: r["points"][0]["twa"])


def run_angle(rows, tws):
    """Deepest worth sailing: the TWA giving the best downwind VMG."""
    def best(row):
        cand = [p for p in row["points"] if p["twa"] >= 90] or row["points"][-1:]
        return max(cand, key=lambda p: p["bsp"] * math.cos(math.radians(180 - p["twa"])))["twa"]
    return _by_tws(rows, tws, best)


def signed_diff(a, b):
    """a - b wrapped to -180..180."""
    return (a - b + 540.0) % 360.0 - 180.0


def local_frame(origin):
    """Metre-based x/y around `origin`, so headings and laylines are plain trig."""
    m_lat = 111_320.0
    m_lon = 111_320.0 * math.cos(math.radians(origin[0]))

    def to_xy(p):
        return ((p[1] - origin[1]) * m_lon, (p[0] - origin[0]) * m_lat)

    def to_ll(xy):
        return (origin[0] + xy[1] / m_lat, origin[1] + xy[0] / m_lon)

    return to_xy, to_ll


def sail_leg_heading(brg, twd, tws, rows, tack):
    """The heading a boat can actually steer towards a mark, and how it is sailing.

    Returns (heading, twa_sailed, mode). Upwind of the polar's minimum angle the
    boat beats at that angle on `tack`; deeper than the best downwind VMG angle
    it runs at that angle; in between it simply points at the mark.
    """
    twa_direct = signed_diff(brg, twd)
    beat, run = beat_angle(rows, tws), run_angle(rows, tws)
    if abs(twa_direct) < beat:
        return norm360(twd + tack * beat), beat, "beat"
    if abs(twa_direct) > run:
        return norm360(twd + tack * run), run, "run"
    return brg, abs(twa_direct), "reach"


def norm360(v):
    return v % 360.0


class SailingBoat:
    """One boat working its way round the course under its polar.

    The tacking rule is the one a crew uses: keep going until the *other* tack
    would lay the mark, then flip. That layline moment is where the bearing to
    the mark equals the heading the other tack would steer, so the signed angle
    between the two passes through zero — a sign change is the whole test. A
    working tack every few minutes on top of that gives a beat with several
    boards in it rather than one long leg out and one back.
    """

    ROUND_RADIUS_M = 25.0        # close enough to have rounded the mark
    MANOEUVRE_S = 14.0           # how long a tack or gybe costs speed
    MANOEUVRE_LOSS = 0.45        # speed multiplier at the moment of the turn

    def __init__(self, device, waypoints, pct, tack_seconds, rows, launch_at, upwind_bias=0.0):
        self.device = device
        self.waypoints = waypoints
        self.pct = pct                       # this boat's share of its polar
        # Relative strength upwind against downwind. A flat percentage is the
        # kindest case for any estimate that scales one number: real boats are
        # good at one end and ordinary at the other, and a projection made on a
        # beat then has to survive a run.
        self.upwind_bias = upwind_bias
        self.tack_seconds = tack_seconds
        self.rows = rows
        self.launch_at = launch_at
        self.pos = waypoints[0]
        self.wp = 1                          # index of the mark being sailed to
        self.tack = 1                        # which side of the wind, +1 / -1
        self.lay_sign = None                 # sign of the angle to the other layline
        self.next_tack_at = launch_at + tack_seconds
        self.manoeuvre_at = None
        self.heading = 0.0
        self.speed_kn = 0.0
        self.distance_m = 0.0
        self.tacks = 0
        self.finished = False

    def _manoeuvre_factor(self, now):
        if self.manoeuvre_at is None:
            return 1.0
        elapsed = now - self.manoeuvre_at
        if elapsed >= self.MANOEUVRE_S:
            return 1.0
        return self.MANOEUVRE_LOSS + (1.0 - self.MANOEUVRE_LOSS) * (elapsed / self.MANOEUVRE_S)

    def _flip(self, now):
        self.tack = -self.tack
        self.tacks += 1
        self.lay_sign = None
        self.manoeuvre_at = now
        self.next_tack_at = now + self.tack_seconds

    def step(self, now, dt, twd, tws):
        """Advance `dt` seconds. Returns False once the last waypoint is reached."""
        if self.finished or now < self.launch_at:
            return not self.finished
        target = self.waypoints[self.wp]
        if haversine_m(self.pos, target) <= self.ROUND_RADIUS_M:
            self.wp += 1
            self.lay_sign = None
            if self.wp >= len(self.waypoints):
                self.finished = True
                return False
            target = self.waypoints[self.wp]

        brg = bearing_deg(self.pos, target)
        heading, twa, mode = sail_leg_heading(brg, twd, tws, self.rows, self.tack)

        if mode in ("beat", "run"):
            other = norm360(twd - self.tack * twa)     # what the other tack steers
            to_layline = signed_diff(brg, other)
            sign = 1 if to_layline >= 0 else -1
            if self.lay_sign is None:
                self.lay_sign = sign
            # Reaching the other tack's layline, or a working tack part way up.
            if sign != self.lay_sign or abs(to_layline) < 0.5 or now >= self.next_tack_at:
                self._flip(now)
                heading, twa, mode = sail_leg_heading(brg, twd, tws, self.rows, self.tack)

        pct = self.pct * (1.0 + (self.upwind_bias if twa < 90 else -self.upwind_bias))
        bsp = (polar_speed(self.rows, twa, tws) or 1.0) * pct * self._manoeuvre_factor(now)
        self.heading, self.speed_kn = heading, bsp
        run_m = bsp * 0.514444 * dt
        # Do not sail past the mark inside one step; stopping short keeps the
        # rounding test above honest at coarse time steps.
        run_m = min(run_m, max(0.0, haversine_m(self.pos, target)))
        self.distance_m += run_m
        to_xy, to_ll = local_frame(self.pos)
        th = math.radians(heading)
        self.pos = to_ll((math.sin(th) * run_m, math.cos(th) * run_m))
        return True


def send_sailing_fix(args, send, dev, pos, now, brg, kn, last_sent, line_a, line_b):
    """Report a sailing boat's fix if it is due. Returns whether one was sent.

    Same dynamic-rate rule as the rhumb-line mode: a real tracker turned up near
    the line and left slow out on the course.
    """
    if args.fast_near_line:
        near = dist_to_line_m(pos, line_a, line_b) <= args.fast_near_line
        due = args.fast_interval if near else args.interval
    else:
        due = args.interval
    if now - last_sent.get(dev, 0.0) < due - 0.05:
        return False
    last_sent[dev] = now
    send(dev, pos, now, brg, kn)
    return True


def path_lengths(path):
    segs = [haversine_m(path[i], path[i + 1]) for i in range(len(path) - 1)]
    return segs, sum(segs)


def position_at(path, segs, dist):
    """Position + bearing at `dist` metres along the path (clamped to the end)."""
    if dist <= 0:
        return path[0], bearing_deg(path[0], path[1]) if len(path) > 1 else 0.0
    acc = 0.0
    for i, seg in enumerate(segs):
        if seg <= 0:
            continue
        if acc + seg >= dist:
            f = (dist - acc) / seg
            return lerp(path[i], path[i + 1], f), bearing_deg(path[i], path[i + 1])
        acc += seg
    return path[-1], bearing_deg(path[-2], path[-1]) if len(path) > 1 else 0.0


def send_osmand(url, device_id, pos, ts, speed_kn, bearing):
    q = urllib.parse.urlencode({
        "id": device_id, "lat": f"{pos[0]:.6f}", "lon": f"{pos[1]:.6f}",
        "timestamp": int(ts), "speed": f"{speed_kn:.1f}", "bearing": f"{bearing:.0f}",
    })
    req = urllib.request.Request(f"{url}/?{q}", method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception as e:  # network/DNS
        return f"ERR {e}"


# The healthiest boat carries --battery and the rest step down by these fractions
# of it. Arithmetic steps do not work here: an even spread from 95 skips straight
# over the amber band (25% to 10%), so the one case worth looking at never appears.
BATTERY_LADDER = (1.0, 0.63, 0.32, 0.19, 0.06)


def battery_for(device_id, devices, start_pct):
    """A plausible battery for each simulated tracker.

    Real units report one with every fix, so a simulator that never does cannot
    exercise the Battery column or the dashboard's low-battery warning at all.
    Five or more devices land in all three bands, rather than giving a column of
    identical numbers that proves only that a number arrives.
    """
    try:
        i = devices.index(device_id)
    except ValueError:
        i = 0
    return max(3.0, min(100.0, start_pct * BATTERY_LADDER[i % len(BATTERY_LADDER)]))


def forward_body(device_id, pos, ts, speed_kn, bearing, battery_pct=None):
    """One fix shaped exactly as Traccar's JSON forwarder sends it.

    Kept separate from the POST so a test can check the app parses what this
    script produces (tests/test_track_ingest.py).
    """
    return {
        "position": {
            "deviceId": abs(hash(device_id)) % 100000,
            "protocol": "osmand",
            "fixTime": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts)),
            "deviceTime": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts)),
            "valid": True,
            "latitude": round(pos[0], 6),
            "longitude": round(pos[1], 6),
            "speed": round(speed_kn, 1),        # Traccar carries speed in knots
            "course": round(bearing, 1),
            **({"attributes": {"batteryLevel": round(battery_pct)}}
               if battery_pct is not None else {}),
        },
        "device": {
            "id": abs(hash(device_id)) % 100000,
            "uniqueId": device_id,
            "name": device_id,
        },
    }


def send_forward(url, token, device_id, pos, ts, speed_kn, bearing, battery_pct=None):
    """POST one fix to the app's ingest endpoint as Traccar's forwarder would."""
    body = json.dumps(forward_body(device_id, pos, ts, speed_kn, bearing,
                                   battery_pct)).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Content-Type": "application/json",
                                          "X-RO-Track-Token": token})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception as e:
        return f"ERR {e}"


def dist_to_line_m(pos, line_a, line_b):
    """Rough distance in metres from pos to the start/finish line segment."""
    # Local planar approximation is plenty over a line a few hundred metres long.
    lat0 = line_a[0]
    def xy(p):
        return (math.radians(p[1] - line_a[1]) * EARTH_M * math.cos(math.radians(lat0)),
                math.radians(p[0] - line_a[0]) * EARTH_M)
    px, py = xy(pos)
    ax, ay = 0.0, 0.0
    bx, by = xy(line_b)
    vx, vy = bx - ax, by - ay
    seg = vx * vx + vy * vy
    t = 0.0 if seg <= 0 else max(0.0, min(1.0, ((px - ax) * vx + (py - ay) * vy) / seg))
    cx, cy = ax + vx * t, ay + vy * t
    return math.hypot(px - cx, py - cy)


def main():
    ap = argparse.ArgumentParser(description="Feed Traccar simulated boats sailing a course.")
    ap.add_argument("--url", default="http://localhost:5055", help="Traccar OsmAnd endpoint (default %(default)s)")
    ap.add_argument("--data-dir", default=_DEFAULT_DATA, help="Repo data/ dir (marks.json, courses.json)")
    ap.add_argument("--course", type=int, default=None, help="Course number (default: first course)")
    ap.add_argument("--prestart", type=float, default=275.0, metavar="METRES",
                    help="How far off the line the boats wait before the start "
                         "(default %(default)s m, about a minute at 8 kn). Raise it to "
                         "give yourself longer between starting the simulator and the gun.")
    ap.add_argument("--finish-line", default=None, metavar="KEY",
                    help="Which line to start and finish across, by key from "
                         "data/start_finish.json (e.g. psc, isora_plas_heli). "
                         "Default: the line marked default there. Set this to match "
                         "the race's own finish line, or the boats sail to the wrong "
                         "one and no finish is ever detected.")
    ap.add_argument("--devices", default="SIM-1,SIM-2,SIM-3", help="Comma list of Traccar device unique ids")
    ap.add_argument("--speed", type=float, default=8.0, help="Boat speed in knots (default %(default)s)")
    ap.add_argument("--interval", type=float, default=5.0, help="Seconds between fixes (default %(default)s)")
    ap.add_argument("--stagger", type=float, default=20.0, help="Seconds between each boat's start (default %(default)s)")
    ap.add_argument("--loop", action="store_true", help="Keep sailing the course in a loop")
    ap.add_argument("--no-finish", dest="finish", action="store_false", help="Do not cross the finish line at the end")
    ap.add_argument("--once", action="store_true", help="Send one fix per device and exit (connectivity test)")
    ap.add_argument("--forward-to", default=None,
                    help="POST Traccar-shaped forwarder JSON to this app URL instead of OsmAnd to Traccar, "
                         "e.g. http://localhost:5050/api/track/ingest")
    ap.add_argument("--token", default="", help="Shared secret for --forward-to (X-RO-Track-Token)")
    ap.add_argument("--fast-near-line", type=float, default=0.0, metavar="METRES",
                    help="Report every --fast-interval seconds within this distance of the line (0 = off)")
    ap.add_argument("--fast-interval", type=float, default=1.0,
                    help="Seconds between fixes when near the line (default %(default)s)")
    ap.add_argument("--battery", type=float, default=None, metavar="PCT",
                    help="Report a battery level with each fix, the fleet spread down from "
                         "PCT (so --battery 95 over five devices gives green, amber and red). "
                         "Needs --forward-to; the OsmAnd endpoint carries no attributes.")
    ap.add_argument("--report-latency", action="store_true",
                    help="Print fix-time -> accepted-at latency for each fix, and a summary")
    sail = ap.add_argument_group(
        "sailing model (--sail)",
        "Sail the course under a polar — beating up, gybing down — instead of motoring "
        "the rhumb line at a fixed speed. Use this to exercise the predicted leaderboard.")
    sail.add_argument("--sail", action="store_true", help="Enable the sailing model")
    sail.add_argument("--polar", default="J122.txt", help="Polar file in data/polars (default %(default)s)")
    sail.add_argument("--twd", type=float, default=298.0,
                      help="True wind direction the boats sail in (default %(default)s). "
                           "Match the app's hut wind or the predicted leaderboard will disagree.")
    sail.add_argument("--tws", type=float, default=12.0, help="True wind speed in knots (default %(default)s)")
    sail.add_argument("--polar-pct", default=None, metavar="LIST",
                      help="Comma list of per-boat polar percentages, e.g. 100,94,88 "
                           "(default: 100 down to 85 across the fleet)")
    sail.add_argument("--tack-minutes", type=float, default=3.0,
                      help="Put in a working tack this often as well as at the laylines "
                           "(0 = sail the laylines only; default %(default)s)")
    args = ap.parse_args()

    devices = [d.strip() for d in args.devices.split(",") if d.strip()]
    course_no, course_pts, _course_line_o = load_course(args.data_dir, args.course)
    line_key, line_label, line_a, line_b = load_finish_line(args.data_dir, args.finish_line)
    latencies = []

    def send(dev, pos, ts, brg, kn=None):
        """Send one fix by whichever transport was chosen, timing the round trip."""
        t0 = time.time()
        kn = args.speed if kn is None else kn
        if args.forward_to:
            batt = battery_for(dev, devices, args.battery) if args.battery is not None else None
            code = send_forward(args.forward_to, args.token, dev, pos, ts, kn, brg, batt)
        else:
            code = send_osmand(args.url, dev, pos, ts, kn, brg)
        if args.report_latency:
            delta = time.time() - ts          # fix time -> accepted by the app
            latencies.append(delta)
            print(f"    {dev} {pos[0]:.5f},{pos[1]:.5f} HTTP {code} "
                  f"fix->accepted {delta:.2f}s (round trip {time.time()-t0:.2f}s)")
        return code
    path = build_path(course_pts, line_a, args.finish, line_b, args.prestart)
    segs, total_m = path_lengths(path)
    speed_ms = max(0.1, args.speed * 0.514444)
    eta_min = (total_m / speed_ms) / 60.0
    target = args.forward_to or args.url

    polar_rows, boat_pct = [], {}
    if args.sail:
        polar_path = os.path.join(args.data_dir, "polars", os.path.basename(args.polar))
        if not os.path.isfile(polar_path):
            sys.exit(f"Polar not found: {polar_path}")
        polar_rows = load_polar_rows(polar_path)
        if not polar_rows:
            sys.exit(f"No usable rows in {polar_path}")
        if args.polar_pct:
            pcts = [float(p) / 100.0 for p in args.polar_pct.split(",") if p.strip()]
        else:
            span = 15.0 if len(devices) > 1 else 0.0
            pcts = [1.0 - (span / 100.0) * i / max(1, len(devices) - 1) for i in range(len(devices))]
        boat_pct = {d: pcts[i % len(pcts)] for i, d in enumerate(devices)}
        beat, run = beat_angle(polar_rows, args.tws), run_angle(polar_rows, args.tws)
        print(f"Course {course_no}: {len(course_pts)} marks, {total_m/1852:.2f} nm round the rhumb line. "
              f"Feeding {len(devices)} boat(s) to {target}")
        print(f"Start/finish line: {line_key} ({line_label}). The race must be set to "
              f"the same line or no finish will be detected.")
        print(f"Sailing {os.path.basename(args.polar)} in {args.tws:.0f} kn from {args.twd:.0f}°T: "
              f"beats at {beat:.0f}° TWA, runs at {run:.0f}°. "
              + ("Laylines only." if args.tack_minutes <= 0
                 else f"Working tack every {args.tack_minutes:.0f} min."))
        print("  " + ", ".join(f"{d} at {boat_pct[d]*100:.0f}%" for d in devices))
        # Which legs are beats matters: a course that happens to be all reaching
        # never tests the tacking, and the wind must match the app's hut wind or
        # the app's own leg model is working from different numbers.
        kinds = []
        for i in range(len(path) - 1):
            twa = abs(signed_diff(bearing_deg(path[i], path[i + 1]), args.twd))
            kinds.append("beat" if twa < beat else ("run" if twa > run else "reach"))
        print(f"  legs: {kinds.count('beat')} beat, {kinds.count('reach')} reaching, "
              f"{kinds.count('run')} running"
              + ("   (no beats at this wind — try --twd near a leg's bearing to make one)"
                 if not kinds.count("beat") else ""))
        print(f"  NOTE: the app estimates from its *hut* wind, so set --twd/--tws to match "
              f"or the predicted leaderboard is being asked a different question.")
    else:
        print(f"Course {course_no}: {len(course_pts)} marks, path {total_m/1852:.2f} nm, "
              f"~{eta_min:.1f} min per lap at {args.speed:.0f} kn. Feeding {len(devices)} boat(s) to {target}")
        print(f"Start/finish line: {line_key} ({line_label}). The race must be set to "
              f"the same line or no finish will be detected.")
    if args.forward_to:
        print("Mode: push — posting Traccar-shaped forwarder JSON straight to the app "
              "(no Traccar involved; needs a matching Push ingest token in Settings).")
    else:
        print("Mode: OsmAnd -> Traccar. Devices must exist in Traccar with these unique ids "
              "(or database.registerUnknown=true):", ", ".join(devices))
    if args.fast_near_line:
        print(f"Reporting every {args.fast_interval:.0f}s within {args.fast_near_line:.0f} m of the line, "
              f"every {args.interval:.0f}s elsewhere.")
    if args.battery is not None:
        if not args.forward_to:
            sys.exit("--battery needs --forward-to: the OsmAnd endpoint has nowhere to put "
                     "attributes, so the level would be silently dropped.")
        print("Batteries: " + ", ".join(f"{d} {battery_for(d, devices, args.battery):.0f}%"
                                        for d in devices))

    if args.once:
        for i, dev in enumerate(devices):
            pos, brg = position_at(path, segs, i * 60.0)
            code = send(dev, pos, time.time(), brg)
            print(f"  {dev}: HTTP {code} @ {pos[0]:.5f},{pos[1]:.5f}")
        print("Done (--once). HTTP 200 = accepted; 400 usually means the device id is unknown to Traccar.")
        return

    # Per-boat state: launch time (staggered) + slight speed variation.
    start = time.time()
    launch = {dev: start + i * args.stagger for i, dev in enumerate(devices)}
    boatspeed = {dev: speed_ms * (1.0 - 0.06 * i) for i, dev in enumerate(devices)}
    done = set()
    first_report = set()
    last_sent = {}
    fleet, last_step = {}, start
    if args.sail:
        tack_s = args.tack_minutes * 60.0 if args.tack_minutes > 0 else 1e9
        fleet = {d: SailingBoat(d, path, boat_pct[d], tack_s, polar_rows, launch[d])
                 for d in devices}
    try:
        while True:
            now = time.time()
            if args.sail:
                # Integrate in one-second sub-steps whatever the reporting rate:
                # a tack decision taken only every --interval seconds overshoots
                # the layline by a boat length per knot.
                remaining = now - last_step
                while remaining > 0:
                    dt = min(1.0, remaining)
                    for b in fleet.values():
                        b.step(last_step + dt, dt, args.twd, args.tws)
                    remaining -= dt
                    last_step += dt
            for dev in devices:
                if now < launch[dev]:
                    continue
                if args.sail:
                    boat = fleet[dev]
                    if boat.finished and dev not in done:
                        done.add(dev)
                        print(f"  {dev}: finished — {(now - launch[dev])/60:.1f} min, "
                              f"{boat.distance_m/1852:.2f} nm sailed, {boat.tacks} manoeuvres.")
                    pos, brg, kn = boat.pos, boat.heading, boat.speed_kn
                    if not send_sailing_fix(args, send, dev, pos, now, brg, kn,
                                            last_sent, line_a, line_b):
                        continue
                    if dev not in first_report:
                        first_report.add(dev)
                        print(f"  {dev}: first fix away.")
                    continue
                travelled = boatspeed[dev] * (now - launch[dev])
                if travelled >= total_m and not args.loop:
                    if dev not in done:
                        done.add(dev)
                        print(f"  {dev}: finished the course (crossed the line).")
                    dist = total_m  # park at the end (past the finish)
                elif args.loop:
                    dist = travelled % total_m
                else:
                    dist = travelled
                pos, brg = position_at(path, segs, dist)
                # A real tracker on a dynamic rate reports faster near the line;
                # skip this fix if the boat is far out and not yet due.
                if args.fast_near_line:
                    near = dist_to_line_m(pos, line_a, line_b) <= args.fast_near_line
                    due = args.fast_interval if near else args.interval
                    if now - last_sent.get(dev, 0.0) < due - 0.05:
                        continue
                last_sent[dev] = now
                code = send(dev, pos, now, brg)
                if dev not in first_report:
                    first_report.add(dev)
                    print(f"  {dev}: first fix HTTP {code}"
                          + ("  (400 = unknown device id in Traccar)" if str(code) == "400" else ""))
            if done and len(done) == len(devices) and not args.loop:
                print("All boats finished. Exiting.")
                break
            # Tick at the finer of the two rates when --fast-near-line is on, or
            # the loop itself caps how often a boat near the line can report.
            time.sleep(min(args.interval, args.fast_interval) if args.fast_near_line else args.interval)
    except KeyboardInterrupt:
        print("\nStopped.")
    if args.report_latency and latencies:
        ordered = sorted(latencies)
        print(f"\nfix -> accepted over {len(ordered)} fixes: "
              f"min {ordered[0]:.2f}s  median {ordered[len(ordered)//2]:.2f}s  max {ordered[-1]:.2f}s")


if __name__ == "__main__":
    main()
