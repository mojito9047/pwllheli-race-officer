"""Whole races, sailed on the polars, reported like the club's trackers.

The night race of 2026-08-08 is the only real track we have with a rounding the
walk got wrong, and it is one course of one shape in one wind. These sail all the
club's fixed courses instead, on a real polar, and sample them at the 61 s the
Teltonika trackers actually report at — which is where the interesting failures
live, because at 8 kn that is 250 m of travel between fixes.

Ground truth is known by construction: the boat rounds every mark on the required
side, so anything the walk misses is a miss and anything it refuses is a false
refusal. The simulator checks its own geometry first (TestTheSimulatorSailsProperly)
because a fixture that quietly sails the wrong side proves whatever you like — two
earlier versions of this one did exactly that, and both times the detector was
right and the fixture was lying.

Kept to a handful of courses and three rounding widths so it costs a few seconds.
The full sweep over all 67 courses and eight widths lives in the commit message for
fa7c710; this is the regression net.
"""
from __future__ import annotations

import json
import math

import pytest

from core import appstate, rounding
from core.polar_io import load_polar
from core.polars import target_speed_info_for
from core.track import (MarkApproach, _passed_mark, _mark_distances,
                        mark_neighbourhood_m)

TWS = 20.0
STEP_S = 4.0          # simulation resolution
REPORT_S = 61.0       # what the club's Teltonika trackers do
TACK_CORRIDOR_M = 400.0
RADIUS_M = 50.0

# Different wind directions and course shapes; 3 is the longest in the book.
COURSE_NOS = (1, 3, 29, 39, 52, 63)
# Tight, ordinary, and wider than the old 400 m neighbourhood could ever see.
OFFSETS_M = (20.0, 150.0, 450.0)

M_LAT = 111132.0


def m_lon(lat):
    return 111320.0 * math.cos(math.radians(lat))


@pytest.fixture(scope="module")
def world():
    """Marks, courses and a polar, from the sandboxed copy of the real data."""
    marks = json.loads((appstate.DATA_DIR / "marks.json").read_text(encoding="utf-8"))["marks"]
    courses = json.loads((appstate.DATA_DIR / "courses.json").read_text(encoding="utf-8"))["courses"]
    polar = load_polar(appstate.DATA_DIR / "polars" / "Beneteau 40.7.txt")
    return marks, courses, polar


def to_en(lat, lon, lat0, lon0):
    return ((lon - lon0) * m_lon(lat0), (lat - lat0) * M_LAT)


def to_ll(e, n, lat0, lon0):
    return (lat0 + n / M_LAT, lon0 + e / m_lon(lat0))


def bearing_of(de, dn):
    return (math.degrees(math.atan2(de, dn)) + 360.0) % 360.0


def twa_of(heading, twd):
    return (heading - twd + 540.0) % 360.0 - 180.0


def boat_speed(heading, twd, polar):
    return (target_speed_info_for(twa_of(heading, twd), TWS, polar) or {"bsp": 5.0})["bsp"]


def sail_to(pos, aim, twd, t0, polar):
    """Sail to a point, tacking or gybing when it cannot be laid."""
    lat0, lon0 = pos
    aim_e, aim_n = to_en(aim[0], aim[1], lat0, lon0)
    track, e, n, t, tack = [], 0.0, 0.0, t0, 1
    for _ in range(100000):
        de, dn = aim_e - e, aim_n - n
        dist = math.hypot(de, dn)
        if dist < 30.0:
            break
        want = bearing_of(de, dn)
        info = target_speed_info_for(twa_of(want, twd), TWS, polar)
        if info and info["mode"] in ("upwind", "downwind"):
            polar_twa = info["polar_twa"]
            ux, uy = de / dist, dn / dist
            cross = -uy * e + ux * n
            if abs(cross) > TACK_CORRIDOR_M:
                tack = -1 if cross > 0 else 1
            heading = (twd + tack * polar_twa) % 360.0
            if info["mode"] == "downwind":
                heading = (twd + 180.0 - tack * (180.0 - polar_twa)) % 360.0
            if dist < TACK_CORRIDOR_M * 1.5 and abs(twa_of(want, twd)) >= polar_twa - 1:
                heading = want
        else:
            heading = want
        step = boat_speed(heading, twd, polar) * 1852.0 / 3600.0 * STEP_S
        e += step * math.sin(math.radians(heading))
        n += step * math.cos(math.radians(heading))
        t += STEP_S
        track.append((t, *to_ll(e, n, lat0, lon0)))
    return track, to_ll(e, n, lat0, lon0), t


def circle_point(mark, theta_deg, r_m):
    th = math.radians(theta_deg)
    return to_ll(r_m * math.sin(th), r_m * math.cos(th), mark[0], mark[1])


def arc_round(mark, theta0, theta1, r_m, side, twd, t0, polar):
    """Sail the arc round the mark, keeping it on the required hand.

    Leaving a mark to port means going anticlockwise in the plane, which is a
    *decreasing* compass bearing from the mark. The two conventions run opposite
    ways, and getting it backwards sails every mark on the wrong hand — which is
    how the first version of this fixture "proved" the detector was broken.
    """
    turn = -1.0 if side == "port" else 1.0
    sweep = ((theta1 - theta0) * turn) % 360.0
    track, t = [], t0
    steps = max(2, int(sweep / 4.0))
    for i in range(1, steps + 1):
        th = theta0 + turn * sweep * i / steps
        la, lo = circle_point(mark, th, r_m)
        heading = (th + turn * 90.0) % 360.0
        seg = 2.0 * math.pi * r_m * (sweep / steps) / 360.0
        t += seg / max(1.0, boat_speed(heading, twd, polar) * 1852.0 / 3600.0)
        track.append((t, la, lo))
    return track, ((track[-1][1], track[-1][2]) if track else None), t


def simulate(course, offset_m, marks, polar):
    """Sail a whole course. Returns (track, arcs) — arcs are the ground truth."""
    twd = float(course["wind_bearing_deg"])
    start = marks["O"]
    pts = []
    for item in course["marks"]:
        mk = marks[str(item["mark"]).upper()]
        side = ("starboard" if str(item.get("rounding", "port")).lower().startswith("s")
                else "port")
        pts.append(((mk["lat"], mk["lon"]), side))
    pos, t, track, arcs = (start["lat"], start["lon"]), 0.0, [], []
    prev = (start["lat"], start["lon"])
    for i, (mark, side) in enumerate(pts):
        nxt = pts[i + 1][0] if i + 1 < len(pts) else (start["lat"], start["lon"])
        in_brg = bearing_of(*to_en(mark[0], mark[1], prev[0], prev[1]))
        out_brg = bearing_of(*to_en(nxt[0], nxt[1], mark[0], mark[1]))
        sign = 1.0 if side == "port" else -1.0
        theta_in = (in_brg + sign * 90.0) % 360.0
        theta_out = (out_brg + sign * 90.0) % 360.0
        leg, pos, t = sail_to(pos, circle_point(mark, theta_in, offset_m), twd, t, polar)
        track += leg
        t0 = t
        arc, end, t = arc_round(mark, theta_in, theta_out, offset_m, side, twd, t, polar)
        track += arc
        arcs.append({"mark": mark, "side": side, "t0": t0, "t1": t})
        if end:
            pos = end
        prev = mark
    return track, arcs


def sample(track, every_s=REPORT_S):
    out, due = [], None
    for t, la, lo in track:
        if due is None or t >= due:
            out.append({"t": t, "lat": la, "lon": lo})
            due = t + every_s
    return out


def build_seq(course, marks):
    seq = []
    for item in course["marks"]:
        code = str(item["mark"]).upper()
        mk = marks[code]
        side = ("starboard" if str(item.get("rounding", "port")).lower().startswith("s")
                else "port")
        seq.append({"code": code, "lat": mk["lat"], "lon": mk["lon"], "via": False,
                    "radius_m": None, "side": side, "accuracy_m": None,
                    "from_point": None})
    for i, entry in enumerate(seq):
        prev_mk = marks[str(course["marks"][i - 1]["mark"]).upper()] if i else marks["O"]
        entry["from_point"] = {"lat": prev_mk["lat"], "lon": prev_mk["lon"]}
    return seq


def detect(prev, fix, seq, idx, approach, use_radius=True, use_gate=True, use_closest=True):
    """The three detectors, any of which can be switched off to prove it is load-bearing."""
    mark = seq[idx]
    if use_radius and _passed_mark(prev, fix, mark, RADIUS_M):
        return "radius"
    if use_gate:
        wrong = rounding.undetermined_side_m(mark.get("accuracy_m"), RADIUS_M)
        if rounding.crossed_gate(prev, fix, mark.get("from_point"), mark, mark.get("side"),
                                 rounding.DEFAULT_GATE_REACH_M, wrong):
            return "gate"
    d, d_next = _mark_distances(fix, seq, idx)
    approach.note(d, d_next)
    if use_closest and approach.departed(d, d_next, mark_neighbourhood_m(RADIUS_M)):
        return "closest"
    return None


def walk(fixes, seq, **which):
    """How many rounding points the walk reached, and what found each one."""
    idx, approach, prev, found = 0, MarkApproach(), None, []
    for fix in fixes:
        moved = True
        while moved and idx < len(seq):
            moved = False
            hit = detect(prev, fix, seq, idx, approach, **which)
            if hit:
                found.append(hit)
                idx += 1
                approach.reset()
                moved = True
        prev = fix
    return idx, found


def races(world, offsets=OFFSETS_M):
    """Every (course, offset) pair as sampled fixes, its sequence and its ground truth."""
    marks, courses, polar = world
    for no in COURSE_NOS:
        course = next(c for c in courses if c["course_no"] == no)
        codes = [str(m["mark"]).upper() for m in course["marks"]]
        if any(c not in marks or marks[c].get("lat") is None for c in codes):
            continue
        for offset in offsets:
            track, arcs = simulate(course, offset, marks, polar)
            yield no, offset, sample(track), build_seq(course, marks), track, arcs


class TestTheSimulatorSailsProperly:
    """If these fail, nothing else in this file means anything."""

    def test_the_boat_sails_at_polar_speeds(self, world):
        _, _, polar = world
        no, offset, fixes, seq, track, arcs = next(iter(races(world, (150.0,))))
        speeds = []
        for (t1, a1, o1), (t2, a2, o2) in zip(track, track[1:]):
            if t2 > t1:
                d = math.hypot((a2 - a1) * M_LAT, (o2 - o1) * m_lon(a1))
                speeds.append(d / (t2 - t1) * 3600 / 1852)
        assert 6.0 < min(speeds) < 10.0, f"slowest {min(speeds):.1f} kn"
        assert 6.0 < sum(speeds) / len(speeds) < 11.0

    def test_every_mark_is_rounded_on_the_required_side(self, world):
        wrong = total = 0
        for no, offset, fixes, seq, track, arcs in races(world):
            for arc in arcs:
                mk = arc["mark"]
                seg = [p for p in track if arc["t0"] <= p[0] <= arc["t1"]]
                if len(seg) < 3:
                    continue
                (_, la1, lo1), (_, la2, lo2) = seg[len(seg) // 2], seg[len(seg) // 2 + 1]
                ve, vn = (lo2 - lo1) * m_lon(la1), (la2 - la1) * M_LAT
                re, rn = (mk[1] - lo1) * m_lon(la1), (mk[0] - la1) * M_LAT
                actual = "port" if ve * rn - vn * re > 0 else "starboard"
                total += 1
                wrong += actual != arc["side"]
        assert total > 100, "the fixture should be exercising a lot of roundings"
        assert wrong == 0, f"{wrong} of {total} roundings sailed the wrong side"

    def test_the_sampling_really_is_coarse(self, world):
        """250 m between fixes is the whole reason these tests find anything."""
        for no, offset, fixes, seq, track, arcs in races(world, (150.0,)):
            steps = [math.hypot((b["lat"] - a["lat"]) * M_LAT,
                                (b["lon"] - a["lon"]) * m_lon(a["lat"]))
                     for a, b in zip(fixes, fixes[1:])]
            assert 150.0 < sum(steps) / len(steps) < 400.0
            break


class TestTheWalkFindsEveryMark:
    """The last course point is the finish line, reached by crossing it, not rounded."""

    def test_no_rounding_is_missed_at_any_width(self, world):
        missed = []
        for no, offset, fixes, seq, track, arcs in races(world):
            reached, _ = walk(fixes, seq)
            if reached < len(seq) - 1:
                missed.append(f"course {no} at {offset:.0f} m: {reached}/{len(seq) - 1}")
        assert not missed, "; ".join(missed)

    def test_a_wide_rounding_is_found_where_the_old_neighbourhood_could_not_reach(self, world):
        """450 m off is outside MARK_NEIGHBOURHOOD_M, which is what stalled
        CRACKAJACK at AA for six hours."""
        for no, offset, fixes, seq, track, arcs in races(world, (450.0,)):
            reached, found = walk(fixes, seq)
            assert reached >= len(seq) - 1, f"course {no}: {reached}/{len(seq) - 1}"
            assert "gate" in found, "the gate should be doing this work"


class TestEachDetectorEarnsItsPlace:
    """Three detectors is two more than it looks like it needs. It does not."""

    def test_without_the_gate_wide_roundings_are_lost(self, world):
        lost = 0
        for no, offset, fixes, seq, track, arcs in races(world, (450.0,)):
            reached, _ = walk(fixes, seq, use_gate=False)
            lost += reached < len(seq) - 1
        assert lost, "a 450 m rounding should be beyond radius and neighbourhood alike"

    def test_without_closest_approach_tight_roundings_are_lost(self, world):
        """The one that surprised us. At 61 s the boat steps 250 m, so a 20 m
        rounding can put no fix at all beyond the mark: the chord never crosses
        the gate's line and no reach would help. Proximity misses it too, because
        no fix lands inside the radius either."""
        lost = 0
        for no, offset, fixes, seq, track, arcs in races(world, (20.0,)):
            reached, _ = walk(fixes, seq, use_closest=False)
            lost += reached < len(seq) - 1
        assert lost, "a tight rounding at 61 s reporting needs the catch-all"

    def test_all_three_together_lose_nothing(self, world):
        for no, offset, fixes, seq, track, arcs in races(world):
            reached, _ = walk(fixes, seq)
            assert reached >= len(seq) - 1
