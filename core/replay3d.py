"""One sailed race as a scene file for the 3D replay, and the render job for it.

This is the half of the replay that has to run on the hut, because it is the
half that needs the database: the marks as they stood on the day, the course
actually sailed after any shortening, the finish line that race used, the wind,
the results from the scorer, and every tracked boat's fixes resampled onto a
regular clock. It writes all of that in local metres, so the renderer needs to
know nothing about latitude, longitude or the app.

Nothing here imports Blender, PIL, tifffile or fontTools, and nothing here
renders. The hut cannot render a film in a useful time and should not try; it
produces this file, puts it where a render machine can find it, and waits.

What it deliberately leaves out are the things that are the same for every race
at one club: the terrain grid and the satellite imagery draped on it. Those are
built once by ``scripts/replay3d`` and uploaded as shared assets, and a scene
refers to them by name. Carrying a 54,000-point height grid in every job would
be silly, and it would put GeoTIFF decoding on the hut for no reason.

Coordinates: x is metres east and y metres north of the origin, the centroid of
the course marks. The projection is the same equirectangular one core.track
uses for crossing geometry, so a boat this file says crossed the line is one
the app says crossed the line.
"""
from __future__ import annotations

import json
import math
import os
import re
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from core import appstate
from core.courses import apply_course_shortening, course_for_race, expand_course_points
from core.db import get_db, row_get
from core.races import get_entries, get_race, race_first_start_dt
from core.track import (
    _project,
    default_finish_line_key,
    finish_line_points,
    positions_for_entry_since,
    race_ended_dt,
    race_finish_line_points,
    race_marks,
)
# The half of this the render machine also needs, kept where it can import
# it without importing the app. Re-exported so every caller here is unchanged.
from core.replay3d_protocol import (  # noqa: E402,F401
    FILMS_PREFIX,
    HEARTBEAT_KEY,
    JOB_STATES,
    JOBS_PREFIX,
    SCENE_FORMAT,
    STATUS_PREFIX,
    blank_status,
    film_key,
    job_key,
    status_is_stale,
    status_key,
)


# Seconds of track kept before the first start and after the last finish.
DEFAULT_LEAD_S = 300.0
DEFAULT_TAIL_S = 120.0
# Regular clock the tracks are resampled onto. Trackers report every 5-30 s;
# 5 s keeps every real fix's influence without inventing detail.
DEFAULT_STEP_S = 5.0
# A boat with no fix for this long is out of sight: the track is broken rather
# than drawn as a straight line across the bay.
GAP_BREAK_S = 180.0
# Boats slower than this are treated as stopped, so the heading is held rather
# than derived from GPS jitter.
STOPPED_KN = 0.6
# If nobody finished and no boat is still racing, the race window ends this
# long after the first start.
FALLBACK_DURATION_S = 4 * 3600.0
# Clock the wind series is binned onto. Coarser than the boat tracks on
# purpose: the station reports every few seconds and its direction jitters, and
# half a minute is short enough to keep a real shift and long enough to lose
# the noise that would make every sail twitch.
WIND_SERIES_STEP_S = 30.0

# One colour per boat, in the order the export lists them, sRGB 0-255. Chosen to
# stay apart from each other and from the sea at a distance, and to survive
# being shrunk to a hull a few pixels wide. This is the authoritative copy: it
# is written into the scene file per boat, so the renderer colours a hull and
# its leaderboard row from the file rather than recomputing and risking drift.
BOAT_COLOURS: List[Tuple[int, int, int]] = [
    (217, 26, 26),      # red
    (26, 89, 217),      # blue
    (242, 191, 13),     # yellow
    (26, 166, 64),      # green
    (153, 38, 179),     # purple
    (242, 115, 13),     # orange
    (13, 179, 191),     # cyan
    (140, 77, 26),      # tan
    (230, 102, 166),    # pink
    (120, 120, 120),    # grey
]


def boat_colour(index: int) -> Tuple[int, int, int]:
    return BOAT_COLOURS[index % len(BOAT_COLOURS)]




# ---------------------------------------------------------------------------
# Small maths helpers.

def _wrap360(deg: float) -> float:
    return deg % 360.0


def _bearing_deg(x0: float, y0: float, x1: float, y1: float) -> float:
    """Compass bearing (0 = north, clockwise) of the move from p0 to p1."""
    return _wrap360(math.degrees(math.atan2(x1 - x0, y1 - y0)))


def _circular_mean_deg(values: Sequence[float]) -> Optional[float]:
    if not values:
        return None
    s = sum(math.sin(math.radians(v)) for v in values)
    c = sum(math.cos(math.radians(v)) for v in values)
    if abs(s) < 1e-9 and abs(c) < 1e-9:
        return None
    return _wrap360(math.degrees(math.atan2(s, c)))


def _interp(t: float, t0: float, t1: float, v0: float, v1: float) -> float:
    if t1 <= t0:
        return v0
    f = (t - t0) / (t1 - t0)
    return v0 + (v1 - v0) * f


def _interp_angle(t: float, t0: float, t1: float, a0: float, a1: float) -> float:
    """Interpolate a compass angle the short way round."""
    d = ((a1 - a0 + 180.0) % 360.0) - 180.0
    return _wrap360(_interp(t, t0, t1, a0, a0 + d))


def _parse_iso(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Track resampling.

def _clean_fixes(fixes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Sorted, de-duplicated fixes with usable coordinates."""
    out: List[Dict[str, Any]] = []
    last_t: Optional[float] = None
    for f in sorted(fixes, key=lambda r: float(r["t"])):
        try:
            t = float(f["t"])
            lat = float(f["lat"])
            lon = float(f["lon"])
        except (TypeError, ValueError, KeyError):
            continue
        if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0) or (lat == 0.0 and lon == 0.0):
            continue
        if last_t is not None and t <= last_t:
            continue
        out.append({"t": t, "lat": lat, "lon": lon,
                    "speed_kn": f.get("speed_kn"), "course_deg": f.get("course_deg")})
        last_t = t
    return out


def resample_track(fixes: List[Dict[str, Any]], t_start: float, t_end: float, step: float,
                   lat0: float, lon0: float) -> List[List[Optional[float]]]:
    """Resample a boat's fixes onto a regular clock, in local metres.

    Each sample is ``[t_rel, x, y, heading_deg, speed_kn]`` with ``t_rel`` in
    seconds from ``t_start``. Samples inside a gap longer than GAP_BREAK_S, or
    before the first fix / after the last, are written as ``[t_rel, None, ...]``
    so the Blender side can hide the boat rather than guess.

    The heading is the tracker's course over the ground where it reported one,
    otherwise the direction of travel between fixes, and is held while the boat
    is effectively stopped so it does not twitch on the mooring.
    """
    pts = _clean_fixes(fixes)
    samples: List[List[Optional[float]]] = []
    if not pts:
        return samples
    xy = [_project(p["lat"], p["lon"], lat0, lon0) for p in pts]
    # Per-fix heading: reported course, else bearing to the next fix.
    headings: List[Optional[float]] = []
    for i, p in enumerate(pts):
        course = p.get("course_deg")
        h: Optional[float] = None
        try:
            if course is not None:
                h = _wrap360(float(course))
        except (TypeError, ValueError):
            h = None
        if h is None:
            j = i + 1 if i + 1 < len(pts) else i - 1
            if 0 <= j < len(pts) and j != i:
                a, b = (xy[i], xy[j]) if j > i else (xy[j], xy[i])
                if math.hypot(b[0] - a[0], b[1] - a[1]) > 1.0:
                    h = _bearing_deg(a[0], a[1], b[0], b[1])
        headings.append(h)
    # Fill headings that are still unknown from the nearest known one.
    last_known: Optional[float] = next((h for h in headings if h is not None), 0.0)
    for i, h in enumerate(headings):
        if h is None:
            headings[i] = last_known
        else:
            last_known = h

    n = int(math.floor((t_end - t_start) / step)) + 1
    j = 0
    held_heading: Optional[float] = None
    for k in range(n):
        t = t_start + k * step
        t_rel = round(t - t_start, 3)
        if t < pts[0]["t"] or t > pts[-1]["t"]:
            samples.append([t_rel, None, None, None, None])
            continue
        while j + 1 < len(pts) and pts[j + 1]["t"] <= t:
            j += 1
        p0 = pts[j]
        p1 = pts[min(j + 1, len(pts) - 1)]
        if p1["t"] - p0["t"] > GAP_BREAK_S and p0["t"] < t < p1["t"]:
            samples.append([t_rel, None, None, None, None])
            continue
        x = _interp(t, p0["t"], p1["t"], xy[j][0], xy[min(j + 1, len(pts) - 1)][0])
        y = _interp(t, p0["t"], p1["t"], xy[j][1], xy[min(j + 1, len(pts) - 1)][1])
        s0 = p0.get("speed_kn")
        s1 = p1.get("speed_kn")
        speed: Optional[float]
        try:
            speed = _interp(t, p0["t"], p1["t"], float(s0), float(s1)) if s0 is not None and s1 is not None else None
        except (TypeError, ValueError):
            speed = None
        if speed is None:
            dist = math.hypot(xy[min(j + 1, len(pts) - 1)][0] - xy[j][0], xy[min(j + 1, len(pts) - 1)][1] - xy[j][1])
            dt = max(p1["t"] - p0["t"], 1e-6)
            speed = dist / dt / 0.514444 if p1 is not p0 else 0.0
        heading = _interp_angle(t, p0["t"], p1["t"], float(headings[j]), float(headings[min(j + 1, len(pts) - 1)]))
        if speed < STOPPED_KN and held_heading is not None:
            heading = held_heading
        held_heading = heading
        samples.append([t_rel, round(x, 2), round(y, 2), round(heading, 1), round(speed, 2)])
    return samples


# ---------------------------------------------------------------------------
# Wind.

def race_wind(t_start: float, t_end: float,
              series_step_s: float = WIND_SERIES_STEP_S) -> Dict[str, Any]:
    """True wind over the race window: the mean, and how it moved.

    The mean alone was what the scene used to carry, and for an eighty-minute
    race that is a fiction: the fleet is trimmed to the wind of the moment, and
    a thirty-degree shift halfway round is the reason somebody won. The series
    lets the renderer follow it -- sail trim, heel, and whether a kite is up all
    hang off the true wind angle.

    Binned onto a regular clock rather than shipped raw. The station reports
    every few seconds and its direction jitters; averaging into half-minute
    bins takes that out without losing a real shift, and it means the renderer
    can index the series by time instead of searching it. Direction is a
    circular mean, because the average of 350 and 10 is north, not south.
    A bin with no samples is ``None``: the renderer holds the last known wind,
    which is what the wind itself does.
    """
    try:
        with get_db() as db:
            rows = db.execute(
                "SELECT sample_time, twd, tws_kt, gust_kt FROM weather_samples "
                "WHERE sample_time BETWEEN ? AND ? ORDER BY sample_time",
                (t_start, t_end),
            ).fetchall()
    except Exception:
        rows = []

    twds = [float(r["twd"]) for r in rows if r["twd"] is not None]
    twss = [float(r["tws_kt"]) for r in rows if r["tws_kt"] is not None]

    step = max(1.0, float(series_step_s))
    count = max(1, int(math.ceil((t_end - t_start) / step)))
    bins: List[List[List[float]]] = [[[], [], []] for _ in range(count)]
    for r in rows:
        when = float(r["sample_time"] or 0.0)
        i = int((when - t_start) // step)
        if not (0 <= i < count):
            continue
        if r["twd"] is not None:
            bins[i][0].append(float(r["twd"]))
        if r["tws_kt"] is not None:
            bins[i][1].append(float(r["tws_kt"]))
        if r["gust_kt"] is not None:
            bins[i][2].append(float(r["gust_kt"]))

    series_twd: List[Optional[float]] = []
    series_tws: List[Optional[float]] = []
    series_gust: List[Optional[float]] = []
    for twd_bin, tws_bin, gust_bin in bins:
        mean_twd = _circular_mean_deg(twd_bin)
        series_twd.append(round(mean_twd, 1) if mean_twd is not None else None)
        series_tws.append(round(sum(tws_bin) / len(tws_bin), 2) if tws_bin else None)
        # The gust of a bin is its peak, not its average: an average gust is not a gust.
        series_gust.append(round(max(gust_bin), 2) if gust_bin else None)

    return {
        "twd_deg": _circular_mean_deg(twds),
        "tws_kn": round(sum(twss) / len(twss), 1) if twss else None,
        "samples": len(rows),
        "series": {
            "step_s": step,
            "count": count,
            "twd": series_twd,
            "tws": series_tws,
            "gust": series_gust,
        },
    }


def wind_at(wind: Dict[str, Any], t_rel: float) -> Tuple[Optional[float], Optional[float]]:
    """The true wind at ``t_rel`` seconds into the scene, holding over gaps.

    Here rather than in the renderer so the film and anything else reading a
    scene agree about what the wind was doing at a given moment.
    """
    series = (wind or {}).get("series") or {}
    twd_list, tws_list = series.get("twd") or [], series.get("tws") or []
    if not twd_list:
        return (wind or {}).get("twd_deg"), (wind or {}).get("tws_kn")
    i = int(max(0.0, float(t_rel)) // max(1.0, float(series.get("step_s") or 1.0)))
    i = min(i, len(twd_list) - 1)
    twd = next((twd_list[j] for j in range(i, -1, -1) if twd_list[j] is not None), None)
    tws = next((tws_list[j] for j in range(min(i, len(tws_list) - 1), -1, -1)
                if tws_list[j] is not None), None)
    return (twd if twd is not None else wind.get("twd_deg"),
            tws if tws is not None else wind.get("tws_kn"))


# ---------------------------------------------------------------------------
# The export.

def _course_as_sailed(race: Any) -> Dict[str, Any]:
    course = course_for_race(race)
    if row_get(race, "shortened_at_mark", None):
        idx = row_get(race, "shortened_at_index", None)
        if idx is not None:
            try:
                course = apply_course_shortening(course, idx)
            except Exception:
                pass
    return course


def _dms(text: str) -> Optional[float]:
    """Parse one coordinate written the way the sailing instructions write it.

    ``52 52.9238'N`` and ``004 24.0609'W`` -- degrees, decimal minutes, hemisphere.
    Returns signed decimal degrees, or None if it is not in that form.
    """
    m = re.search(r"(\d+)\s*[^\d]?\s*(\d+(?:\.\d+)?)\s*'?\s*([NSEW])", str(text or "").strip(), re.I)
    if not m:
        return None
    deg, minutes, hemi = float(m.group(1)), float(m.group(2)), m.group(3).upper()
    value = deg + minutes / 60.0
    return -value if hemi in ("S", "W") else value


def start_line_points(race: Any) -> Optional[Dict[str, Any]]:
    """Where the race started, which is not always where it finished.

    Pwllheli starts every race on the club line -- the ODM to the transit on
    the bridge -- but an ISORA passage race finishes on a different line
    nearly a kilometre away. Taking the start from the finish would put the
    start camera, and the drawn line, in the wrong place for those races, so
    the two are exported separately.

    The configured ``start_line`` is the authority when both of its ends
    resolve: a mark code for the seaward end, and the surveyed position for
    the shore end, which is written in degrees and decimal minutes. Failing
    that, the club's default finish line is the same geometry and is already
    machine-readable.
    """
    marks_at = race_marks(race)
    cfg = (appstate.START_FINISH or {}).get("start_line") or {}
    code = str(cfg.get("seaward_end_mark") or "").strip()
    seaward = marks_at.get(code) or {}
    lat = _dms(cfg.get("shore_end_position"))
    lon = _dms(str(cfg.get("shore_end_position") or "").split("N", 1)[-1]
               .split("S", 1)[-1]) if lat is not None else None
    if seaward.get("lat") is not None and lat is not None and lon is not None:
        return {"points": ((float(seaward["lat"]), float(seaward["lon"])), (lat, lon)),
                "key": "start", "source": "start_line"}
    points = finish_line_points(marks_at, default_finish_line_key())
    if points:
        return {"points": points, "key": default_finish_line_key() or "club",
                "source": "default_finish_line"}
    return None


def _race_window(race: Any, entries: List[Any], lead_s: float, tail_s: float) -> Tuple[float, float, float]:
    """Return (t_first_start, t_window_start, t_window_end) as epoch seconds."""
    start_dt = race_first_start_dt(race)
    if start_dt is None:
        raise SystemExit("race has no start time; nothing to replay")
    t_start = start_dt.timestamp()
    ended = race_ended_dt(race)
    if ended is None or ended <= start_dt:
        # Still racing (or nothing recorded): take the latest fix any entry has.
        latest = t_start
        for e in entries:
            fixes = positions_for_entry_since(e, t_start, t_start + FALLBACK_DURATION_S)
            if fixes:
                latest = max(latest, float(fixes[-1]["t"]))
        t_end = latest if latest > t_start else t_start + FALLBACK_DURATION_S
    else:
        t_end = ended.timestamp()
    return t_start, t_start - lead_s, t_end + tail_s

def race_results(race: Any, colour_by_boat: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """The race's corrected-time result, as the app itself scores it.

    Straight from ``core.series``, so the card at the end of the film says the
    same thing as the results page and the printed sheet. A DUAL race is scored
    twice, once per rating, and each rating may be split into classes.
    """
    from core.series import compute_dual_results, compute_rating_result_group

    colour_by_boat = colour_by_boat or {}
    entries = get_entries(int(race["id"]))
    rule = str(row_get(race, "rating_rule", "") or "").upper()
    groups: List[Dict[str, Any]] = []
    try:
        if rule == "DUAL":
            dual = compute_dual_results(race, entries)
            groups = [dual["irc"], dual["ytc"]]
        else:
            groups = [compute_rating_result_group(race, entries, rule or "IRC")]
    except Exception as ex:
        print(f"  results: the scorer refused this race ({ex!r}); using finishing order")

    tables: List[Dict[str, Any]] = []
    for group in groups:
        # One table per rating where the classes share a start, otherwise one per
        # class: the same choice the results page makes, and it keeps the closing
        # card to a readable length instead of listing every boat three times.
        candidates = [t for t in (group.get("tables") or []) if t.get("is_overall")] or \
                     list(group.get("tables") or [])
        for table in candidates:
            rows, absent = [], []
            for row in table.get("rows") or []:
                entry = row.get("entry")
                name = str(row_get(entry, "boat_name", "") or "")
                status = str(row_get(entry, "status", "") or "")
                if not row.get("corrected_text"):
                    # Not a result, just a fact about the day: a footnote, not a row.
                    absent.append(f"{status} {name}".strip())
                    continue
                rows.append({
                    "pos": row.get("rank"),
                    "boat": name,
                    "sail_no": str(row_get(entry, "sail_no", "") or ""),
                    "status": status,
                    "elapsed": str(row.get("elapsed_text") or ""),
                    "corrected": str(row.get("corrected_text") or ""),
                    # Seconds as well as text, so the card can show the gap to the winner.
                    "corrected_s": row.get("corrected_seconds"),
                    "elapsed_s": row.get("elapsed_seconds"),
                    "colour": colour_by_boat.get(name.strip().lower()),
                })
            if rows:
                tables.append({"title": str(table.get("title") or "Results"),
                               "rating_label": str(table.get("rating_label") or ""),
                               "rows": rows, "note": "   ".join(absent)})

    if not tables:
        # No scorer, no ratings, or a race it cannot handle: the order they crossed.
        finished = []
        for entry in entries:
            when = _parse_iso(row_get(entry, "finish_time", ""))
            if when is not None:
                finished.append((when, entry))
        finished.sort(key=lambda item: item[0])
        rows = [{"pos": n, "boat": str(row_get(e, "boat_name", "") or ""),
                 "sail_no": str(row_get(e, "sail_no", "") or ""),
                 "status": str(row_get(e, "status", "") or ""),
                 "elapsed": "", "corrected": when.strftime("%H:%M:%S")}
                for n, (when, e) in enumerate(finished, start=1)]
        if rows:
            tables.append({"title": "Finishing order", "rating_label": "", "rows": rows})
    return tables

def race_videos(race_id: int, t_win0: float, t_win1: float) -> List[Dict[str, Any]]:
    """The race's start and finish clips as windows on the replay clock, plus a URL.

    A clip covers ``pre_seconds`` before its event to ``post_seconds`` after,
    but the *file* does not begin there: clips are concatenated from whole
    rolling-buffer segments (twenty seconds each on the hut), so it opens at the
    segment boundary at or before that. ``core.barreplay.footage_start_ts``
    already works this out for the clubhouse replay, from the recorded footage
    start where there is one and the honest middle-of-the-segment guess where
    there is not, so this uses that rather than a second opinion.

    Only clips already published to the club's bucket are listed. The renderer
    cannot reach the hut, so a clip that exists solely as evidence video on the
    hut PC is one it can never fetch; leaving it out here is the difference
    between a film with no picture-in-picture and a render that fails halfway.
    Frame rate and duration are deliberately not probed: that needs the file,
    and the renderer has it once it downloads. The one thing that must not be
    decided twice is which windows are shown, so that stays here.
    """
    from core.barreplay import footage_start_ts

    try:
        with get_db() as db:
            rows = db.execute(
                "SELECT id, clip_type, event_time, pre_seconds, post_seconds, status, label, "
                "public_status, public_url, footage_started_at FROM video_clips "
                "WHERE race_id = ? AND clip_type IN ('start', 'finish') ORDER BY event_time",
                (race_id,),
            ).fetchall()
    except Exception:
        return []

    out: List[Dict[str, Any]] = []
    for row in rows:
        event = _parse_iso(row_get(row, "event_time", ""))
        if event is None:
            continue
        pre = float(row_get(row, "pre_seconds", 60) or 60)
        post = float(row_get(row, "post_seconds", 60) or 60)
        start_ts = event.timestamp() - pre
        end_ts = event.timestamp() + post
        if end_ts < t_win0 or start_ts > t_win1:
            continue                                    # outside the replay window

        url = str(row_get(row, "public_url", "") or "").strip()
        if not url:
            continue                                    # not published: unreachable from a renderer

        footage_start = footage_start_ts(row, event, int(pre))
        out.append({
            "id": int(row_get(row, "id", 0) or 0),
            "kind": str(row_get(row, "clip_type", "") or ""),
            "label": str(row_get(row, "label", "") or ""),
            "url": url,
            "t_start": round(start_ts - t_win0, 2),
            "t_end": round(end_ts - t_win0, 2),
            "t_event": round(event.timestamp() - t_win0, 2),
            # Seconds into the file at which the event happens.
            "footage_offset_s": round(event.timestamp() - footage_start, 2),
        })

    return trim_video_windows(out)


def trim_video_windows(clips: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """One clip on screen at a time.

    The hut cuts a clip per finishing boat, so two boats crossing seconds apart
    leave two clips of the same moment, and a fleet finishing inside two minutes
    leaves clips that overlap. Settled here so the frame extractor and the scene
    builder cannot disagree about the windows: this list is the only version.
    """
    final: List[Dict[str, Any]] = []
    for clip in sorted(clips, key=lambda c: float(c["t_start"])):
        if final and clip["kind"] == final[-1]["kind"] and abs(clip["t_event"] - final[-1]["t_event"]) < 5.0:
            continue
        if final:
            clip["t_start"] = max(float(clip["t_start"]), float(final[-1]["t_end"]))
        if float(clip["t_end"]) - float(clip["t_start"]) < 5.0:
            continue
        final.append(clip)
    return final


# ---------------------------------------------------------------------------
# The scene.

def build_scene(race_id: int, *, step_s: float = DEFAULT_STEP_S, lead_s: float = DEFAULT_LEAD_S,
                tail_s: float = DEFAULT_TAIL_S, videos: bool = True) -> Dict[str, Any]:
    """One race as a scene file: marks, course, lines, wind, tracks, results.

    Terrain and imagery are not here; a scene names the shared assets it wants
    and the renderer fetches those separately.
    """
    race = get_race(race_id)
    if race is None:
        raise ValueError(f"no race with id {race_id}")
    entries = get_entries(race_id)
    marks_at = race_marks(race)
    course = _course_as_sailed(race)
    points = expand_course_points(course)

    # Origin: centroid of the course marks that have a position.
    course_codes = [p["mark"] for p in points]
    positioned = [(float(marks_at[c]["lat"]), float(marks_at[c]["lon"]))
                  for c in dict.fromkeys(course_codes)
                  if c in marks_at and marks_at[c].get("lat") is not None]
    if not positioned:
        positioned = [(float(m["lat"]), float(m["lon"])) for m in marks_at.values()
                      if isinstance(m, dict) and m.get("lat") is not None]
    if not positioned:
        raise ValueError(f"race {race_id} has no positioned marks to place a scene around")
    lat0 = sum(p[0] for p in positioned) / len(positioned)
    lon0 = sum(p[1] for p in positioned) / len(positioned)

    def xy(lat: float, lon: float) -> List[float]:
        x, y = _project(lat, lon, lat0, lon0)
        return [round(x, 2), round(y, 2)]

    marks_out = []
    for code, rec in marks_at.items():
        if not isinstance(rec, dict) or rec.get("lat") is None or rec.get("lon") is None:
            continue
        marks_out.append({
            "code": code,
            "name": rec.get("name", ""),
            "xy": xy(float(rec["lat"]), float(rec["lon"])),
            "buoy": rec.get("buoy", ""),
            "top_mark": rec.get("top_mark", ""),
            "in_course": code in course_codes,
            "historical_position": bool(rec.get("position_is_historical")),
        })

    course_out = []
    for p in points:
        rec = marks_at.get(p["mark"]) or {}
        if rec.get("lat") is None:
            continue
        course_out.append({
            "mark": p["mark"], "display": p.get("display_mark", p["mark"]),
            "rounding": p.get("rounding", ""), "xy": xy(float(rec["lat"]), float(rec["lon"])),
        })

    start_out = start_line_points(race)
    line = race_finish_line_points(race)
    line_out = None
    if line:
        line_out = {"seaward_xy": xy(*line[0]), "shore_xy": xy(*line[1]),
                    "key": row_get(race, "finish_line_key", "") or "club"}
        # The last leg is to the line, not to a mark: give the drawn path somewhere to end.
        mid = [round((line_out["seaward_xy"][0] + line_out["shore_xy"][0]) / 2.0, 2),
               round((line_out["seaward_xy"][1] + line_out["shore_xy"][1]) / 2.0, 2)]
        course_out.append({"mark": "FINISH", "display": "Finish", "rounding": "finish", "xy": mid})
    if start_out:
        start_out = {"seaward_xy": xy(*start_out["points"][0]),
                     "shore_xy": xy(*start_out["points"][1]),
                     "key": start_out["key"], "source": start_out["source"]}

    t_first_start, t_win0, t_win1 = _race_window(race, entries, lead_s, tail_s)

    boats_out = []
    for e in entries:
        fixes = positions_for_entry_since(e, t_win0, t_win1)
        samples = resample_track(fixes, t_win0, t_win1, step_s, lat0, lon0)
        if not any(s[1] is not None for s in samples):
            continue
        finish_dt = _parse_iso(row_get(e, "finish_time", ""))
        boats_out.append({
            "entry_id": int(e["id"]),
            "colour": list(boat_colour(len(boats_out))),
            "name": row_get(e, "boat_name", "") or "",
            "sail_no": row_get(e, "sail_no", "") or "",
            "class_name": row_get(e, "class_name", "") or "",
            "status": row_get(e, "status", "") or "",
            "finish_t": round(finish_dt.timestamp() - t_win0, 1) if finish_dt else None,
            "fix_count": len(fixes),
            "samples": samples,
        })

    wind = race_wind(t_win0, t_win1)
    if wind["twd_deg"] is None:
        # No hut samples for that window: the course's design wind is the next best guess.
        wind["twd_deg"] = course.get("wind_bearing_deg")
        wind["from"] = "course design wind"
    else:
        wind["twd_deg"] = round(wind["twd_deg"], 1)
        wind["from"] = "hut weather samples"

    # How far the action reaches from the origin. The course, the line and the
    # tracks decide this, not every mark the club owns: the passage marks off
    # the Gwylan Islands are 20 km out and would shrink a club race to a dot.
    extent = 500.0
    for p in course_out:
        extent = max(extent, abs(p["xy"][0]), abs(p["xy"][1]))
    if line_out:
        for key in ("seaward_xy", "shore_xy"):
            extent = max(extent, abs(line_out[key][0]), abs(line_out[key][1]))
    for b in boats_out:
        for s in b["samples"]:
            if s[1] is not None:
                extent = max(extent, abs(s[1]), abs(s[2]))

    return {
        "format": SCENE_FORMAT,
        "race": {
            "id": int(race["id"]),
            "name": row_get(race, "name", ""),
            "class_name": row_get(race, "class_name", "") or "",
            "course_no": row_get(race, "course_no", None),
            "course_text": course.get("sequence_text", ""),
            "shortened_at_mark": row_get(race, "shortened_at_mark", "") or "",
            "race_type": row_get(race, "race_type", "standard"),
            "first_start_iso": datetime.fromtimestamp(t_first_start).isoformat(timespec="seconds"),
        },
        "origin": {"lat": lat0, "lon": lon0,
                   "projection": "equirectangular metres east/north (core.track._project)"},
        "time": {
            "t0_epoch": t_win0,
            "t0_iso": datetime.fromtimestamp(t_win0).isoformat(timespec="seconds"),
            "first_start_rel": round(t_first_start - t_win0, 1),
            "duration_s": round(t_win1 - t_win0, 1),
            "step_s": step_s,
        },
        "extent_m": round(extent, 0),
        "wind": wind,
        "marks": marks_out,
        "course": course_out,
        "start_line": start_out,
        "finish_line": line_out,
        "boats": boats_out,
        # The leaderboard names each boat in the colour its hull wore in the film.
        "results": race_results(race, {b["name"].strip().lower(): b["colour"] for b in boats_out}),
        "terrain": None,
        "videos": race_videos(race_id, t_win0, t_win1) if videos else [],
    }



# ---------------------------------------------------------------------------
# The render job, and what comes back.

def branding_manifest_url() -> str:
    """The public branding endpoint, or "" when this app has no public address.

    Without a configured public base URL there is nothing useful to send: a
    link to ``localhost`` would only have the render machine fetch its own
    logos, or more likely nothing at all.
    """
    from core.horn import public_base_url

    base = public_base_url()
    return f"{base}/api/branding/live" if base else ""


def build_job(scene: Dict[str, Any], *, requested_by: str = "",
              speed: float = 30.0, slow_step: int = 5) -> Dict[str, Any]:
    """Wrap a scene in the instructions a renderer needs to act on it.

    The scene and the job travel as one object rather than two. They are only a
    few hundred kilobytes together, and two files means a renderer can pick up
    a job whose scene has not landed yet.

    It names no terrain. The coastline is the same for every race at a club,
    so the render machine works out which map tiles the scene covers and keeps
    them in a cache of its own -- nothing to build, nothing to name here, and
    nothing anybody has to remember when a longer course is set.
    """
    race = scene.get("race") or {}
    job = {
        "format": SCENE_FORMAT,
        "job": {
            "version": 1,
            "race_id": int(race.get("id") or 0),
            "race_name": race.get("name") or "",
            "created_at": time.time(),
            "created_by": requested_by or "",
            # Where the render machine can read the club's current logos. It
            # is the public manifest the live-stream relay already uses, so
            # the film agrees with the start and finish videos and there is
            # nothing to copy to the render machine when a sponsor changes.
            # Sent as a URL rather than as images because it is read at render
            # time, which may be hours after the job was queued.
            "branding_manifest": branding_manifest_url(),
            "out_key": film_key(int(race.get("id") or 0)),
            "render": {"speed": float(speed), "slow_step": int(slow_step)},
        },
    }
    job.update(scene)
    return job


# ---------------------------------------------------------------------------
# Putting a job where a renderer will find it, and reading what came back.
#
# All of this rides on the bucket the race videos already use. That is not
# laziness: the film belongs beside the clips it is made from, the credentials
# and the public base URL are already configured and already tested by every
# published video, and a second bucket would be a second thing to get wrong at
# the one moment nobody has time. Keys are namespaced under replay3d/ so the
# two never collide.

def bucket_config() -> Dict[str, Any]:
    """The R2 settings the replay borrows from public video, or {} if not set up."""
    from core.video import video_config, video_public_r2_credentials_ready

    cfg = video_config()
    if not video_public_r2_credentials_ready(cfg):
        return {}
    return {
        "account_id": cfg.get("video_public_r2_account_id"),
        "bucket": cfg.get("video_public_r2_bucket"),
        "access_key": cfg.get("video_public_r2_access_key_id"),
        "secret_key": cfg.get("video_public_r2_secret_access_key"),
        "public_base_url": cfg.get("video_public_r2_public_base_url"),
    }


def public_url_for(key: str) -> str:
    """Where a bucket key can be read from on the open internet."""
    from core.video import public_url_for_r2_key, video_config

    return public_url_for_r2_key(video_config(), key)


def _put_json(cfg: Dict[str, Any], key: str, value: Dict[str, Any]) -> None:
    from core import r2

    r2.put_object(
        cfg["account_id"], cfg["bucket"], key,
        json.dumps(value, separators=(",", ":")).encode("utf-8"),
        cfg["access_key"], cfg["secret_key"],
        content_type="application/json",
    )


def _get_json(cfg: Dict[str, Any], key: str) -> Optional[Dict[str, Any]]:
    from core import r2

    try:
        return r2.get_json(cfg["account_id"], cfg["bucket"], key,
                           cfg["access_key"], cfg["secret_key"])
    except Exception:
        return None


def submit_render(race_id: int, *, requested_by: str = "",
                  speed: float = 30.0, slow_step: int = 5, **scene_kwargs: Any) -> Dict[str, Any]:
    """Export the race and queue it for whichever machine is rendering.

    The status is written first and the job second. A renderer polls the job
    prefix, so a job that appears before its status could be claimed and
    finished before the app had anything to show, and the race page would sit
    there saying nothing was happening while a film was being made.
    """
    cfg = bucket_config()
    if not cfg:
        raise RuntimeError("Public video R2 is not configured, so there is nowhere to put a render job.")

    scene = build_scene(race_id, **scene_kwargs)
    job = build_job(scene, requested_by=requested_by, speed=speed, slow_step=slow_step)

    status = blank_status(race_id, "queued", "waiting for a renderer")
    _put_json(cfg, status_key(race_id), status)
    _put_json(cfg, job_key(race_id), job)
    return {
        "race_id": int(race_id),
        "job_key": job_key(race_id),
        "status_key": status_key(race_id),
        "boats": len(scene.get("boats") or []),
        "clips": len(scene.get("videos") or []),
        "duration_s": (scene.get("time") or {}).get("duration_s"),
    }


# ---------------------------------------------------------------------------
# Which races have a film, for pages that list a whole season.

FILM_INDEX_TTL_S = 600.0
_FILM_INDEX: Dict[str, Any] = {"at": 0.0, "films": {}}


def _public_url(cfg: Dict[str, Any], key: str) -> str:
    """Where a bucket key is served from, using this config's own base URL."""
    from urllib.parse import quote

    base = str(cfg.get("public_base_url") or "").rstrip("/")
    return base + "/" + "/".join(quote(part, safe="") for part in str(key).split("/"))


def _film_version(last_modified: str) -> str:
    """The object's own modification time, as a cache-busting token.

    The film's key is only the race id, and it is served with a day of cache,
    so a re-render otherwise sits behind the old copy. Taking the token from
    the object itself means nothing has to be recorded anywhere: the URL for a
    given film is the same for everyone until that film is replaced.
    """
    try:
        stamp = datetime.strptime(last_modified.strip(), "%Y-%m-%dT%H:%M:%S.%fZ")
    except (AttributeError, ValueError):
        try:
            stamp = datetime.strptime(last_modified.strip(), "%Y-%m-%dT%H:%M:%SZ")
        except (AttributeError, ValueError):
            return ""
    return str(int(stamp.replace(tzinfo=timezone.utc).timestamp()))


def published_films(max_age_s: float = FILM_INDEX_TTL_S) -> Dict[int, str]:
    """``{race_id: public film URL}`` for every film in the bucket.

    One listing answers for a whole season, which is the point: the competitor
    landing page draws every race of the year and the published results
    document draws every race of a series, and a status read apiece would be a
    network call apiece on pages the public hits.

    The existence check is the bucket's own listing rather than anything the
    hut wrote down. A link to a film is only worth showing while the film is
    actually there, and a record of a publish can outlive the object it
    describes. Memoised, because a film appears a few times a season.

    Empty on any failure: no links is a correct page, and a page of links to
    films that cannot be fetched is not.
    """
    now = time.time()
    if now - float(_FILM_INDEX["at"]) < max_age_s:
        return dict(_FILM_INDEX["films"])

    films: Dict[int, str] = {}
    cfg = bucket_config()
    if cfg:
        from core import r2

        try:
            objects = r2.list_objects(cfg["account_id"], cfg["bucket"], FILMS_PREFIX + "/",
                                      cfg["access_key"], cfg["secret_key"])
        except Exception:
            objects = []
        for obj in objects:
            key = str(obj.get("key") or "")
            match = re.fullmatch(re.escape(FILMS_PREFIX) + r"/race_(\d+)\.mp4", key)
            if not match or not int(obj.get("size_bytes") or 0):
                continue
            # Built from the same config that just supplied the credentials,
            # rather than going back to video_config for the address. One
            # bucket, one answer: a listing from one account and a link to
            # another is the kind of mismatch nobody sees until a competitor
            # clicks it.
            url = _public_url(cfg, key)
            version = _film_version(str(obj.get("last_modified") or ""))
            films[int(match.group(1))] = f"{url}?v={version}" if version else url

    _FILM_INDEX["films"], _FILM_INDEX["at"] = films, now
    return dict(films)


def forget_published_films() -> None:
    """Drop the memo, so a fresh render shows up without waiting it out."""
    _FILM_INDEX["films"], _FILM_INDEX["at"] = {}, 0.0


def film_url_for(race_id: int) -> str:
    """The public film for one race, or "" if there is not one."""
    return published_films().get(int(race_id), "")


def read_status(race_id: int) -> Optional[Dict[str, Any]]:
    """What the renderer last said about this race, or None if it has said nothing."""
    cfg = bucket_config()
    return _get_json(cfg, status_key(race_id)) if cfg else None


def read_heartbeat() -> Optional[Dict[str, Any]]:
    """When a renderer last checked in at all, whether or not it had work."""
    cfg = bucket_config()
    return _get_json(cfg, HEARTBEAT_KEY) if cfg else None


def renderer_is_up(heartbeat: Optional[Dict[str, Any]], now: Optional[float] = None,
                   after_s: float = 300.0) -> bool:
    """True when a renderer has checked in recently enough to be believed."""
    if not heartbeat:
        return False
    seen = float(heartbeat.get("updated_at") or 0.0)
    return ((now if now is not None else time.time()) - seen) <= after_s


def dashboard_status(race_id: Optional[int] = None, now: Optional[float] = None) -> Dict[str, Any]:
    """One object for the dashboard card and the race page's button.

    **This reads R2 and must not be called per request.** Two GETs a call, and
    the dashboard card polls every few seconds per viewer: on a race day that
    is a bill and a rate limit for no gain, because the thing being watched
    changes once every half minute at most. A background worker on the hut
    calls this on a timer and the API serves what it cached, the same shape the
    weather poller, the power monitor and the offsite worker all use.

    Deliberately says something useful when nothing is configured or nothing
    has ever been rendered, because that is what the card will show for most
    of its life and "no news" should not look like a fault.
    """
    cfg = bucket_config()
    if not cfg:
        return {"configured": False, "ok": True, "renderer_up": False, "state": "",
                "message": "Not set up: the 3D replay uses the public video bucket."}

    beat = read_heartbeat()
    up = renderer_is_up(beat, now)
    # The dashboard asks about no race in particular, so follow whatever the
    # renderer says it is working on. Without this the card cheerfully reports
    # "no render has been asked for" while one is half way through.
    if race_id is None and beat and beat.get("busy_with"):
        race_id = int(beat["busy_with"])
    status = read_status(race_id) if race_id else None
    stale = status_is_stale(status, now)

    if status and status.get("state") == "failed":
        message = status.get("error") or status.get("message") or "The last render failed."
        return {"configured": True, "ok": False, "renderer_up": up, "state": "failed",
                "message": message, "status": status, "heartbeat": beat}
    if stale:
        return {"configured": True, "ok": False, "renderer_up": up, "state": status.get("state"),
                "message": "The renderer stopped reporting part way through.",
                "status": status, "heartbeat": beat}
    if status and status.get("state") == "done":
        # A re-render replaces an object whose key never changes, and the index
        # of which races have a film is a ten-minute memo over a bucket
        # listing. If this render finished after that listing, every film link
        # the app is handing out for this race still carries the previous cut's
        # version token -- and those links are served with a day of cache, so a
        # competitor who clicks inside those ten minutes is pinned to the old
        # film until tomorrow. Cheap to notice here: the page that says "the
        # film is ready" is the one about to offer the link.
        if float(_FILM_INDEX["at"]) < float(status.get("updated_at") or 0.0):
            forget_published_films()
        return {"configured": True, "ok": True, "renderer_up": up, "state": "done",
                "message": "The film is ready.", "status": status, "heartbeat": beat}
    if status:
        pct = float(status.get("progress") or 0.0) * 100.0
        return {"configured": True, "ok": True, "renderer_up": up, "state": status.get("state"),
                "message": (f"{status.get('state', 'working')} {pct:.0f}%" if pct
                            else str(status.get("state") or "working")),
                "status": status, "heartbeat": beat}
    if not up:
        return {"configured": True, "ok": False, "renderer_up": False, "state": "",
                "message": "No render machine has checked in.", "heartbeat": beat}
    return {"configured": True, "ok": True, "renderer_up": True, "state": "",
            "message": "Ready. No render has been asked for.", "heartbeat": beat}


# ---------------------------------------------------------------------------
# The cached answer everything in the app actually reads.

# Two R2 reads a call, and both the race page and the dashboard card want the
# answer -- the card every few seconds, per viewer. A short memo rather than a
# background thread: there is no lifecycle to get wrong, the first caller after
# it expires pays the round trip, and everyone else is free. What is being
# watched moves once every half minute at most, so twenty seconds is already
# finer-grained than the thing itself.
_SNAPSHOT_TTL_S = 20.0
_SNAPSHOTS: Dict[Any, Dict[str, Any]] = {}


def status_snapshot(race_id: Optional[int] = None, max_age_s: float = _SNAPSHOT_TTL_S,
                    now: Optional[float] = None) -> Dict[str, Any]:
    """``dashboard_status`` for this race, cached for a few seconds.

    This is the one the app should call. ``dashboard_status`` goes to the
    network every time and is the thing being cached.
    """
    when = now if now is not None else time.time()
    key = int(race_id) if race_id else None
    hit = _SNAPSHOTS.get(key)
    if hit and (when - hit["at"]) <= max(0.0, max_age_s):
        return hit["value"]
    try:
        value = dashboard_status(race_id, now=when)
    except Exception as exc:                     # a bucket wobble must not take out a page
        value = {"configured": True, "ok": False, "renderer_up": False, "state": "",
                 "message": f"Could not read the render status ({type(exc).__name__})."}
    _SNAPSHOTS[key] = {"at": when, "value": value}
    return value


def forget_status_snapshot(race_id: Optional[int] = None) -> None:
    """Drop the memo, so the next read is fresh.

    Called after queueing a render: the page that comes back from pressing the
    button has to say something happened, not repeat a twenty-second-old
    "no render has been asked for".
    """
    if race_id is None:
        _SNAPSHOTS.clear()
    else:
        _SNAPSHOTS.pop(int(race_id), None)
        _SNAPSHOTS.pop(None, None)


def clips_pending(race_id: int) -> Dict[str, int]:
    """How many of this race's start/finish clips have reached the bucket.

    The renderer fetches clips from there, so one still sitting on the hut is
    one the film cannot show. Worth saying before a two-hour render rather
    than after it, which is why the button reports it.
    """
    try:
        with get_db() as db:
            rows = db.execute(
                "SELECT public_status, public_url FROM video_clips "
                "WHERE race_id = ? AND clip_type IN ('start', 'finish')",
                (race_id,),
            ).fetchall()
    except Exception:
        return {"total": 0, "published": 0, "pending": 0}
    total = len(rows)
    published = sum(1 for r in rows if str(row_get(r, "public_url", "") or "").strip())
    return {"total": total, "published": published, "pending": total - published}
