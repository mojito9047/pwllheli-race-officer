"""When the bar TV should be showing the camera instead of the chart.

The clubhouse display spends most of a race on the course chart, and cuts to the
start-hut camera for the moments where there is something to watch: the start,
each boat coming in to finish, and each boat rounding the ODM on the way round.
Deciding *when* is the only real logic in the page, so it lives here rather than
in the browser — it can be tested, and it uses the same course-progress figures
the leaderboard does.

The start is easy: it is a known time. A finish is not — a boat arrives when it
arrives — so "two minutes before finishing" has to be predicted. The distance a
boat still has to sail divided by the speed it is making is crude over a whole
race, but over the last two minutes it is about as good as anything: the boat is
on its final approach, usually on one heading, with the line in sight.

A rounding is different again, and simpler: the camera is pointed at the ODM, so
"is a boat near it" is the whole question. No prediction, no history — just how
far the boat is from the mark against how far it travels in a minute.

Deliberately *not* used here: the leaderboard's polar-pace and VMC projections.
They are built to be right about a finish half an hour away and are withheld for
the first ten minutes of a race; this needs a number that is always available and
only has to be right about the next 120 seconds.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

# How long the camera stays up around each moment.
BEFORE_START_S = 120.0
AFTER_START_S = 120.0
BEFORE_FINISH_S = 120.0
AFTER_FINISH_S = 60.0

# Below this there is no usable estimate: a boat drifting at a tenth of a knot
# would otherwise be "arriving" from half an hour away, or never.
MIN_SOG_KN = 0.5

# Rounding the ODM. Most courses pass mark O more than once — courses 11 and 14
# go round it three times — and O is the seaward end of the start/finish line,
# which is exactly what the hut camera is pointed at. So a boat rounding it
# mid-race is worth cutting to, the same as a start or a finish.
#
# "About a minute either side" is done with a radius rather than a stopwatch: how
# far the boat travels in a minute at the speed it is making, with a floor for a
# boat that is barely moving. One test, no history to keep, and it covers the
# approach and the exit symmetrically without having to know which one it is
# watching.
ROUNDING_LEAD_S = 60.0
ROUNDING_MIN_RADIUS_M = 150.0


STILL_RACING_STATUS = "RACING"


def boat_still_racing(row: Dict[str, Any]) -> bool:
    """Is this boat still out there and expected to finish?

    Not simply "has it finished". A boat can leave a race without finishing it:
    retired, did not start, did not compete, disqualified. Those are settled
    results, not boats still on the water.
    """
    if row.get("finished"):
        return False
    status = str(row.get("status") or STILL_RACING_STATUS).strip().upper()
    return status == STILL_RACING_STATUS


def race_is_over(rows) -> bool:
    """True once no boat is still racing.

    The clubhouse display used to ask whether *every* boat had finished, which a
    race with a retirement or a non-starter never satisfies: those boats never
    get a finish time, so the display counted up all afternoon and kept showing a
    race that was long over. A race ends when nobody is still sailing it.
    """
    rows = list(rows or [])
    return bool(rows) and not any(boat_still_racing(r) for r in rows)


def finish_eta_seconds(row: Dict[str, Any]) -> Optional[float]:
    """Seconds until this boat crosses the line, or None if it cannot be said."""
    if row.get("finished"):
        return None
    dist = row.get("dist_remaining_nm")
    sog = row.get("sog")
    if dist is None or sog is None:
        return None
    try:
        dist, sog = float(dist), float(sog)
    except (TypeError, ValueError):
        return None
    if sog < MIN_SOG_KN or dist < 0:
        return None
    return dist / sog * 3600.0


def _metres_between(a_lat: float, a_lon: float, b_lat: float, b_lon: float) -> float:
    """Straight-line distance in metres. Flat-earth is ample over a few hundred."""
    m_lat = 111_320.0
    m_lon = 111_320.0 * math.cos(math.radians((a_lat + b_lat) / 2.0))
    return math.hypot((a_lat - b_lat) * m_lat, (a_lon - b_lon) * m_lon)


def rounding_window(rows: List[Dict[str, Any]],
                    mark: Optional[Tuple[float, float]],
                    mark_name: str = "O") -> Optional[Dict[str, Any]]:
    """The nearest boat rounding ``mark``, if one is close enough to be worth it.

    A boat that has not rounded anything yet is ignored: it has only just
    started, it is sitting near the ODM because that is where the line is, and
    the start window has that covered. Without this the camera would simply stay
    up after every start on a course whose first leg begins at the line.
    """
    if not mark:
        return None
    best = None
    for row in rows or []:
        if row.get("finished") or not (row.get("rounded") or 0):
            continue
        lat, lon = row.get("lat"), row.get("lon")
        if lat is None or lon is None:
            continue
        try:
            away = _metres_between(float(lat), float(lon), mark[0], mark[1])
        except (TypeError, ValueError):
            continue
        sog = row.get("sog")
        try:
            reach = float(sog) * 0.514444 * ROUNDING_LEAD_S if sog else 0.0
        except (TypeError, ValueError):
            reach = 0.0
        if away > max(ROUNDING_MIN_RADIUS_M, reach):
            continue
        if best is None or away < best[0]:
            best = (away, row)
    if best is None:
        return None
    away, row = best
    return {"show": True, "reason": "rounding", "boat": row.get("boat_name"),
            "eta": round(away, 0), "mark": mark_name}


def video_window(start_ts: Optional[float], rows: List[Dict[str, Any]],
                 now: float, mark: Optional[Tuple[float, float]] = None,
                 mark_name: str = "O") -> Dict[str, Any]:
    """Should the camera be on screen, and why?

    Returns ``{"show", "reason", "boat", "eta"}``. ``reason`` is ``"start"``,
    ``"finishing"`` (a boat is on its way in) or ``"finished"`` (one just crossed),
    which the page uses for the caption — a bar wants to know *whose* finish it is
    being shown.
    """
    off = {"show": False, "reason": None, "boat": None, "eta": None}
    if start_ts is not None:
        if start_ts - BEFORE_START_S <= now <= start_ts + AFTER_START_S:
            return {"show": True, "reason": "start", "boat": None,
                    "eta": round(start_ts - now, 1)}

    # A boat that has just crossed wins over one still coming: the finish that
    # has happened is the one people are looking up at the screen for.
    just_finished = []
    approaching = []
    for row in rows or []:
        if row.get("finished"):
            at = row.get("finish_epoch")
            if at is not None and 0 <= now - float(at) <= AFTER_FINISH_S:
                just_finished.append((float(at), row))
            continue
        eta = finish_eta_seconds(row)
        if eta is not None and eta <= BEFORE_FINISH_S:
            approaching.append((eta, row))

    if just_finished:
        at, row = max(just_finished, key=lambda p: p[0])       # the most recent
        return {"show": True, "reason": "finished", "boat": row.get("boat_name"),
                "eta": round(now - at, 1)}
    if approaching:
        eta, row = min(approaching, key=lambda p: p[0])        # the closest in
        return {"show": True, "reason": "finishing", "boat": row.get("boat_name"),
                "eta": round(eta, 1)}
    # Last, because a finish is the bigger moment — though in practice the ODM is
    # the seaward end of the line, so the camera is looking at both anyway.
    rounding = rounding_window(rows, mark, mark_name)
    if rounding:
        return rounding
    return off


def caption_for(window: Dict[str, Any]) -> str:
    """A line of text for the bar to read from across the room."""
    reason = window.get("reason")
    boat = window.get("boat") or ""
    if reason == "start":
        eta = window.get("eta")
        if eta is not None and eta > 0:
            return "Start in " + _mmss(eta)
        return "Start"
    if reason == "finishing":
        return f"{boat} approaching the finish".strip()
    if reason == "finished":
        return f"{boat} finishing".strip()
    if reason == "rounding":
        return f"{boat} rounding {window.get('mark') or 'the mark'}".strip()
    return ""


def _mmss(seconds: float) -> str:
    s = max(0, int(round(seconds)))
    return f"{s // 60}:{s % 60:02d}"
