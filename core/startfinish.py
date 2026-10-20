"""The club's start line: its seaward mark and its shore point, from data/start_finish.json.

Every course is measured from the start mark, the course chart draws the line from
the start mark to the shore point, and the forecast is read at the start mark. Both
used to be written into the code as Pwllheli's -- mark ``O`` and the CHPSC bridge
window -- so another club's start_finish.json drew its finish line correctly and
then measured every course from an ``O`` it does not have: course lengths came out
blank and the chart drew Pwllheli's line.

Where each comes from, first that answers:

* the start mark: ``start_line.seaward_end_mark``, then the seaward mark of the
  default finish line;
* the shore point: ``start_line.shore_end_position`` (written the way the sailing
  instructions write it, degrees and decimal minutes), then the shore end of the
  default finish line, then the old ``bridge_window`` block.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Optional, Tuple

from core import appstate

# Only reached when start_finish.json names no start mark at all, which no file
# written since the start line had its own block does. It is what every course was
# measured from before then, so an old file keeps measuring the same way.
LEGACY_START_MARK = "O"

_DMS = re.compile(r"(\d+)\s*[^\d\s]?\s*(\d+(?:\.\d+)?)\s*'?\s*([NSEW])", re.I)


def _config() -> Dict[str, Any]:
    sf = appstate.START_FINISH
    return sf if isinstance(sf, dict) else {}


def _start_line() -> Dict[str, Any]:
    line = _config().get("start_line")
    return line if isinstance(line, dict) else {}


def default_line() -> Dict[str, Any]:
    """The finish line marked ``default`` (or the first), which is the club line."""
    lines = _config().get("finish_lines")
    if not isinstance(lines, list):
        return {}
    lines = [line for line in lines if isinstance(line, dict)]
    for line in lines:
        if line.get("default"):
            return line
    return lines[0] if lines else {}


def dms(text: Any) -> Optional[float]:
    """Parse one coordinate written the way the sailing instructions write it.

    ``52 52.9238'N`` and ``004 24.0609'W`` -- degrees, decimal minutes, hemisphere.
    Returns signed decimal degrees, or None if it is not in that form.
    """
    m = _DMS.search(str(text or "").strip())
    if not m:
        return None
    deg, minutes, hemi = float(m.group(1)), float(m.group(2)), m.group(3).upper()
    value = deg + minutes / 60.0
    return -value if hemi in ("S", "W") else value


def position_from_text(text: Any) -> Optional[Tuple[float, float]]:
    """``52° 52.9238'N 004° 24.0609'W`` as (lat, lon), whichever order it is written in."""
    lat = lon = None
    for deg, minutes, hemi in _DMS.findall(str(text or "")):
        value = float(deg) + float(minutes) / 60.0
        hemi = hemi.upper()
        if hemi in ("N", "S") and lat is None:
            lat = -value if hemi == "S" else value
        elif hemi in ("E", "W") and lon is None:
            lon = -value if hemi == "W" else value
    if lat is None or lon is None:
        return None
    return (lat, lon)


def start_mark() -> str:
    """The code of the mark at the seaward end of the start line."""
    code = str(_start_line().get("seaward_end_mark") or "").strip()
    if not code:
        code = str((default_line().get("seaward") or {}).get("mark") or "").strip()
    return code or LEGACY_START_MARK


def _point(lat: Any, lon: Any) -> Optional[Tuple[float, float]]:
    try:
        return (float(lat), float(lon)) if lat is not None and lon is not None else None
    except (TypeError, ValueError):
        return None


def shore_point(marks: Optional[Dict[str, Any]] = None) -> Optional[Tuple[float, float]]:
    """(lat, lon) of the shore end of the start line, or None if the file gives none.

    ``marks`` is only consulted when the default line's shore end is a mark rather
    than a position; pass ``track.race_marks(race)`` for a past race.
    """
    point = position_from_text(_start_line().get("shore_end_position"))
    if point:
        return point
    shore = default_line().get("shore") or {}
    if isinstance(shore, dict):
        code = str(shore.get("mark") or "").strip()
        if code:
            row = (appstate.MARKS if marks is None else marks).get(code) or {}
            point = _point(row.get("lat"), row.get("lon"))
        else:
            point = _point(shore.get("lat"), shore.get("lon"))
        if point:
            return point
    legacy = _config().get("bridge_window") or {}
    return _point(legacy.get("lat"), legacy.get("lon")) if isinstance(legacy, dict) else None


def shore_label() -> str:
    """What the shore end is called: "CHPSC Bridge window", "Brunswick lock house flagstaff"."""
    shore = default_line().get("shore") or {}
    label = str(shore.get("label") or "").strip() if isinstance(shore, dict) else ""
    return label or "Shore end"


def shore_short_label() -> str:
    """A label short enough for the chip on the chart: ``short_label``, or the label."""
    shore = default_line().get("shore") or {}
    short = str(shore.get("short_label") or "").strip() if isinstance(shore, dict) else ""
    return short or shore_label()


def start_line_points(marks: Optional[Dict[str, Any]] = None
                      ) -> Optional[Tuple[Tuple[float, float], Tuple[float, float]]]:
    """((lat, lon) of the start mark, (lat, lon) of the shore point), or None."""
    row = (appstate.MARKS if marks is None else marks).get(start_mark()) or {}
    seaward = _point(row.get("lat"), row.get("lon"))
    shore = shore_point(marks)
    if seaward is None or shore is None:
        return None
    return (seaward, shore)
