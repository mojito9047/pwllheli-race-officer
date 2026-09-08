"""Add, edit and remove racing marks in data/marks.json from the UI.

Marks are stored as a dict keyed by mark code in data/marks.json (loaded into
appstate.MARKS). This module validates, persists and safety-checks changes to
SIMPLE marks. Compound marks are managed by editing the JSON directly and are
protected here; their *component* marks are ordinary marks and can be moved.

**Marks move.** A laid mark drags in a storm, and then boats round a buoy that is
no longer where the app thinks it is — with a 50 m rounding radius, fifty metres
of drift is enough for the course walk to decide a boat never rounded it, which
stalls every mark after it. So a position is not a constant: it is a measurement,
and it needs re-measuring after heavy weather.

Every position change records **who set it, when, how, and how accurate the fix
claimed to be**, and keeps the positions it replaced. That history is what
``marks_as_of`` reads: a race is drawn, replayed and analysed with the marks as
they stood when it was sailed, so correcting a mark today does not silently
redraw last month's races or move the buoys a boat is recorded as having rounded.
Recorded finish times were never affected, being times rather than geometry.

After writing, callers must refresh the in-memory copy (app.reload_course_mark_data).
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from core import appstate
from core import rounding
from core.timeutils import haversine_nm

# How many superseded positions to keep per mark. Enough to see a season's
# re-laying without the file growing without bound.
POSITION_HISTORY_LIMIT = 12

# A fix worse than this is not worth writing over a surveyed position with: the
# rounding radius is 50 m, so a 40 m fix could move a mark most of the way to the
# edge of its own circle.
MAX_ACCURACY_M = 25.0

# Far enough that it is more likely a mistake — the wrong mark selected, or a
# phone still reporting from the clubhouse — than genuine drift.
IMPLAUSIBLE_MOVE_M = 2000.0

# Mark codes appear as JSON keys and inside course tokens like "1p"/"Os", so keep
# them short and free of spaces/punctuation.
MARK_CODE_RE = re.compile(r"^[A-Za-z0-9]{1,4}$")

# A per-mark rounding radius may override the one in Settings. Same bounds as the
# global setting, so neither can be set to something the other would refuse.
MIN_ROUNDING_RADIUS_M = 10
MAX_ROUNDING_RADIUS_M = 500


def parse_rounding_radius(value: Any) -> Tuple[bool, Optional[int], str]:
    """Read a per-mark rounding radius. Returns (ok, radius or None, error).

    Blank means "no override": the mark uses the radius from Settings. That is
    the normal case, and it matters that blank is not stored as a number — a mark
    that inherits should follow the setting when it changes, not be frozen at
    whatever the setting happened to be the day somebody edited the mark.
    """
    if value is None:
        return True, None, ""
    text = str(value).strip()
    if not text:
        return True, None, ""
    try:
        radius = int(round(float(text)))
    except (TypeError, ValueError):
        return False, None, "The rounding radius must be a number of metres."
    if not (MIN_ROUNDING_RADIUS_M <= radius <= MAX_ROUNDING_RADIUS_M):
        return False, None, (f"The rounding radius must be between "
                             f"{MIN_ROUNDING_RADIUS_M} and {MAX_ROUNDING_RADIUS_M} m.")
    return True, radius, ""


def mark_rounding_radius(record: Any) -> Optional[int]:
    """The per-mark rounding radius, or None when the mark inherits Settings."""
    if not isinstance(record, dict):
        return None
    ok, radius, _ = parse_rounding_radius(record.get("rounding_radius_m"))
    return radius if ok else None


def mark_accuracy_m(record: Any) -> float:
    """How well this mark's position is known, in metres.

    Not a tolerance to be tuned: it is the width of the band in which a boat's
    side of the mark cannot be determined at all, because the boat passed nearer
    to the mark than we know where the mark is. A laid buoy swinging on scope in
    a tideway is tens of metres; a surveyed transit is nearly nothing. See
    core.rounding for what the number decides.
    """
    if isinstance(record, dict):
        for key in ("position_accuracy_m", "accuracy_m"):
            try:
                value = record.get(key)
                if value is not None:
                    return max(0.0, float(value))
            except (TypeError, ValueError):
                pass
    return rounding.DEFAULT_MARK_ACCURACY_M


def _marks_path():
    return appstate.DATA_DIR / "marks.json"


def _load() -> dict:
    with _marks_path().open("r", encoding="utf-8") as f:
        return json.load(f)


def _save(data: dict) -> None:
    # Match the existing marks.json style so adding/removing a mark makes a
    # minimal diff: ascii-escaped (degrees as °) and LF newlines (newline="\n"
    # avoids Windows CRLF translation rewriting every line).
    with _marks_path().open("w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=True, indent=2)
        f.write("\n")


def format_lat_text(lat: float) -> str:
    hemi = "N" if lat >= 0 else "S"
    v = abs(lat)
    deg = int(v)
    return f"{deg:02d}° {(v - deg) * 60:06.3f}'{hemi}"


def format_lon_text(lon: float) -> str:
    hemi = "E" if lon >= 0 else "W"
    v = abs(lon)
    deg = int(v)
    return f"{deg:03d}° {(v - deg) * 60:06.3f}'{hemi}"


def validate_new_mark(code: str, name: str, lat, lon) -> Tuple[bool, str]:
    code = (code or "").strip()
    if not MARK_CODE_RE.match(code):
        return False, "Mark code must be 1-4 letters or digits, with no spaces."
    if code in appstate.MARKS:
        return False, f"A mark with code '{code}' already exists."
    if not (name or "").strip():
        return False, "A mark name is required."
    try:
        latf, lonf = float(lat), float(lon)
    except (TypeError, ValueError):
        return False, "Latitude and longitude must be numbers in decimal degrees."
    if not (-90.0 <= latf <= 90.0) or not (-180.0 <= lonf <= 180.0):
        return False, "Latitude must be between -90 and 90, longitude between -180 and 180."
    return True, ""


def add_mark(code: str, name: str, lat, lon, buoy: str = "", top_mark: str = "",
             rounding_radius_m: Any = None, waypoint: bool = False) -> Tuple[bool, str]:
    """Add a simple mark, or a waypoint. Returns (True, code) or (False, error).

    A **waypoint** is not a mark. It exists to bend a leg round a headland so the
    chart, the leg length and the wind analysis follow the water rather than a
    straight line over land. Boats are not asked to round it, it stays off the
    course board and out of the announcement, and it carries no rounding radius:
    it is passed by crossing the perpendicular at it, not by coming near it.
    """
    ok, err = validate_new_mark(code, name, lat, lon)
    if not ok:
        return False, err
    # A waypoint has no rounding radius to parse: nothing rounds it. Accepting one
    # would store a number that reads as meaningful and is never consulted.
    if waypoint:
        radius = None
    else:
        ok, radius, err = parse_rounding_radius(rounding_radius_m)
        if not ok:
            return False, err
    code = code.strip()
    latf, lonf = float(lat), float(lon)
    data = _load()
    data.setdefault("marks", {})[code] = {
        "name": name.strip(),
        "lat": latf,
        "lon": lonf,
        "lat_text": format_lat_text(latf),
        "lon_text": format_lon_text(lonf),
        "buoy": (buoy or "").strip(),
        "top_mark": (top_mark or "").strip(),
    }
    if radius is not None:
        data["marks"][code]["rounding_radius_m"] = radius
    if waypoint:
        data["marks"][code]["waypoint"] = True
    _save(data)
    return True, code


def mark_delete_block(code: str) -> Optional[str]:
    """Return the reason a mark cannot be deleted, or None if it is safe to delete."""
    md = appstate.MARKS.get(code)
    if not md:
        return "Mark not found."
    if md.get("compound") or md.get("components"):
        return "Compound mark - edit marks.json directly to change it."
    if md.get("component_of"):
        return f"Component of compound mark {md['component_of']}."
    for course in appstate.COURSES:
        for item in course.get("marks", []):
            if item.get("mark") == code:
                return f"Used in course {course.get('course_no')}."
    # And the made-up courses, which live on the race rather than in the course
    # list. Waypoints will mostly be used there, and deleting one would silently
    # straighten a leg back across the land on a race already sailed — changing
    # its recorded length and bearings after the event.
    used_by = _races_using_mark(code)
    if used_by:
        first = used_by[0]
        more = f" and {len(used_by) - 1} other(s)" if len(used_by) > 1 else ""
        return f"Used in the made-up course for race #{first}{more}."
    sf = appstate.START_FINISH or {}
    for line_key, label in (("start_line", "start line"), ("finish_line", "finish line")):
        if (sf.get(line_key) or {}).get("seaward_end_mark") == code:
            return f"Used as the {label} seaward mark."
    return None


def _races_using_mark(code: str) -> List[int]:
    """Race ids whose made-up course uses this mark. Best effort; never raises."""
    try:
        from core.db import get_db, init_db
        init_db()
        with get_db() as db:
            rows = db.execute(
                "SELECT id, custom_course_json FROM races"
                " WHERE custom_course_json IS NOT NULL AND custom_course_json != ''"
            ).fetchall()
    except Exception:
        return []
    hits = []
    for row in rows:
        try:
            seq = json.loads(row["custom_course_json"]) or []
        except Exception:
            continue
        marks_in = seq.get("marks", []) if isinstance(seq, dict) else seq
        for item in marks_in or []:
            if isinstance(item, dict) and str(item.get("mark", "")).strip().upper() == code:
                hits.append(int(row["id"]))
                break
    return hits


def delete_mark(code: str) -> Tuple[bool, str]:
    """Delete a mark if it is safe to. Returns (True, code) or (False, reason)."""
    block = mark_delete_block(code)
    if block:
        return False, block
    data = _load()
    if code in data.get("marks", {}):
        del data["marks"][code]
        _save(data)
    return True, code


# ---------------------------------------------------------------------------
# Editing an existing mark, and moving one from a phone on the water
# ---------------------------------------------------------------------------

def validate_position(lat: Any, lon: Any) -> Tuple[bool, str]:
    """Check a latitude/longitude pair on its own."""
    try:
        latf, lonf = float(lat), float(lon)
    except (TypeError, ValueError):
        return False, "Latitude and longitude must be numbers in decimal degrees."
    if not (-90.0 <= latf <= 90.0) or not (-180.0 <= lonf <= 180.0):
        return False, "Latitude must be between -90 and 90, longitude between -180 and 180."
    return True, ""


def mark_has_position(code: str) -> bool:
    """Whether this mark is one with a position of its own.

    A compound mark such as Y or A is a name for two physical corner marks and
    carries no position itself; its components do, and those are ordinary marks.
    """
    md = appstate.MARKS.get(code) or {}
    return md.get("lat") is not None and md.get("lon") is not None


def mark_edit_block(code: str) -> Optional[str]:
    """The reason a mark's details cannot be edited here, or None."""
    md = appstate.MARKS.get(code)
    if not md:
        return "Mark not found."
    if md.get("compound") or md.get("components"):
        return "Compound mark - edit marks.json directly to change it."
    if not mark_has_position(code):
        return "This mark has no position of its own."
    return None


def metres_moved(code: str, lat: Any, lon: Any) -> Optional[float]:
    """How far a proposed position is from where the mark is recorded now."""
    md = appstate.MARKS.get(code) or {}
    if md.get("lat") is None or md.get("lon") is None:
        return None
    try:
        return haversine_nm(float(md["lat"]), float(md["lon"]),
                            float(lat), float(lon)) * 1852.0
    except (TypeError, ValueError):
        return None


def _as_datetime(when: Any) -> Optional[datetime]:
    """Coerce a datetime, ISO string or epoch seconds to a datetime, or None."""
    if when is None or when == "":
        return None
    if isinstance(when, datetime):
        return when
    if isinstance(when, (int, float)):
        try:
            return datetime.fromtimestamp(float(when))
        except (OverflowError, OSError, ValueError):
            return None
    try:
        return datetime.fromisoformat(str(when))
    except ValueError:
        return None


def position_at(record: Dict[str, Any], when: Any) -> Optional[Tuple[float, float]]:
    """Where this mark was at ``when``. None if it has no position at all.

    A mark's positions form a chain: the current one, stamped with the moment it
    was set, then each superseded position in ``position_history``, each stamped
    with the moment *it* was set. So the position in force at some past time is
    the newest one whose stamp is not after that time.

    An unstamped position — a mark that has never been re-measured, or the
    original entry at the end of the history — is treated as having always been
    there. There is nothing better to assume, and assuming otherwise would make
    every race before the first re-measurement unplottable.
    """
    if not isinstance(record, dict):
        return None
    at = _as_datetime(when)

    def point(lat: Any, lon: Any) -> Optional[Tuple[float, float]]:
        if lat is None or lon is None:
            return None
        try:
            return (float(lat), float(lon))
        except (TypeError, ValueError):
            return None

    current = point(record.get("lat"), record.get("lon"))
    if at is None:
        return current
    set_at = _as_datetime(record.get("position_set_at"))
    if set_at is None or at >= set_at:
        return current
    for entry in (record.get("position_history") or []):
        if not isinstance(entry, dict):
            continue
        entry_at = _as_datetime(entry.get("set_at"))
        if entry_at is None or at >= entry_at:
            candidate = point(entry.get("lat"), entry.get("lon"))
            if candidate is not None:
                return candidate
    # Older than every stamp we hold. The oldest recorded position is the best
    # answer available; falling back to the current one would be the one thing
    # this function exists to avoid.
    for entry in reversed(record.get("position_history") or []):
        if isinstance(entry, dict):
            candidate = point(entry.get("lat"), entry.get("lon"))
            if candidate is not None:
                return candidate
    return current


def marks_as_of(when: Any, marks: Optional[Dict[str, Any]] = None) -> Dict[str, Dict[str, Any]]:
    """A marks dict as it stood at ``when``, shaped like appstate.MARKS.

    Every consumer of mark positions — the chart, the leg analysis, the rounding
    walk — takes a dict of this shape, so winding the clock back is a matter of
    handing them a different dict rather than teaching each of them about
    history. Passing None for ``when`` returns the marks as they are now.

    Compound marks carry no position of their own (they expand to components),
    so they come through untouched.
    """
    source = appstate.MARKS if marks is None else marks
    if when is None:
        return source
    out: Dict[str, Dict[str, Any]] = {}
    for code, record in (source or {}).items():
        if not isinstance(record, dict):
            out[code] = record
            continue
        moved = position_at(record, when)
        if moved is None or (record.get("lat") == moved[0] and record.get("lon") == moved[1]):
            out[code] = record
            continue
        historical = dict(record)
        historical["lat"], historical["lon"] = moved
        # The printed position is what the marks list and the course board show;
        # leaving the current text beside a past position would be worse than
        # showing neither.
        historical["lat_text"] = format_lat_text(moved[0])
        historical["lon_text"] = format_lon_text(moved[1])
        historical["position_is_historical"] = True
        out[code] = historical
    return out


def _stamp_position(record: Dict[str, Any], latf: float, lonf: float, *,
                    by: str, source: str, accuracy_m: Optional[float]) -> None:
    """Write a new position onto a mark record, keeping the one it replaces."""
    previous = None
    if record.get("lat") is not None and record.get("lon") is not None:
        previous = {
            "lat": record["lat"], "lon": record["lon"],
            "set_at": record.get("position_set_at"),
            "set_by": record.get("position_set_by"),
            "source": record.get("position_source"),
            "accuracy_m": record.get("position_accuracy_m"),
        }
    record["lat"] = latf
    record["lon"] = lonf
    record["lat_text"] = format_lat_text(latf)
    record["lon_text"] = format_lon_text(lonf)
    record["position_set_at"] = datetime.now().isoformat(timespec="seconds")
    record["position_set_by"] = (by or "").strip() or "unknown"
    record["position_source"] = source
    if accuracy_m is None:
        record.pop("position_accuracy_m", None)
    else:
        record["position_accuracy_m"] = round(float(accuracy_m), 1)
    if previous is not None:
        history: List[Dict[str, Any]] = list(record.get("position_history") or [])
        history.insert(0, previous)
        record["position_history"] = history[:POSITION_HISTORY_LIMIT]


def update_mark(code: str, name: str, lat: Any, lon: Any,
                buoy: str = "", top_mark: str = "", *,
                by: str = "", source: str = "manual",
                rounding_radius_m: Any = None) -> Tuple[bool, str]:
    """Change an existing mark's details. Returns (True, code) or (False, why).

    The position is only stamped as changed when it actually changes, so editing
    a mark's name does not make it look as though somebody re-surveyed it.
    """
    code = (code or "").strip()
    block = mark_edit_block(code)
    if block:
        return False, block
    if not (name or "").strip():
        return False, "A mark name is required."
    ok, err = validate_position(lat, lon)
    if not ok:
        return False, err
    ok, radius, err = parse_rounding_radius(rounding_radius_m)
    if not ok:
        return False, err
    latf, lonf = float(lat), float(lon)

    data = _load()
    record = data.get("marks", {}).get(code)
    if record is None:
        return False, "Mark not found."
    record["name"] = name.strip()
    record["buoy"] = (buoy or "").strip()
    record["top_mark"] = (top_mark or "").strip()
    # Blank clears the override rather than storing a number, so the mark goes
    # back to following the Settings value.
    if radius is None:
        record.pop("rounding_radius_m", None)
    else:
        record["rounding_radius_m"] = radius
    if record.get("lat") != latf or record.get("lon") != lonf:
        _stamp_position(record, latf, lonf, by=by, source=source, accuracy_m=None)
    _save(data)
    return True, code


def set_mark_position(code: str, lat: Any, lon: Any, *, by: str = "",
                      accuracy_m: Any = None,
                      source: str = "phone",
                      confirm_large_move: bool = False) -> Tuple[bool, str]:
    """Move a mark to a measured position. Returns (True, message) or (False, why).

    Two guards, because this is done one-handed in a small boat:

    * a fix the phone itself calls worse than ``MAX_ACCURACY_M`` is refused —
      the rounding radius is only 50 m, so a vague fix could move a mark most of
      the way to the edge of its own circle;
    * a move of more than ``IMPLAUSIBLE_MOVE_M`` needs confirming, because at
      that distance the likeliest explanations are the wrong mark selected or a
      phone still reporting from the clubhouse, not a dragged anchor.
    """
    code = (code or "").strip()
    block = mark_edit_block(code)
    if block:
        return False, block
    ok, err = validate_position(lat, lon)
    if not ok:
        return False, err
    latf, lonf = float(lat), float(lon)

    acc = None
    if accuracy_m is not None and str(accuracy_m).strip() != "":
        try:
            acc = float(accuracy_m)
        except (TypeError, ValueError):
            return False, "The accuracy reading was not a number."
        if acc > MAX_ACCURACY_M:
            return False, (f"That fix is only accurate to {acc:.0f} m. Wait for a better "
                           f"one - anything worse than {MAX_ACCURACY_M:.0f} m is too vague "
                           f"to move a mark by.")

    moved = metres_moved(code, latf, lonf)
    if moved is not None and moved > IMPLAUSIBLE_MOVE_M and not confirm_large_move:
        return False, (f"That is {moved/1000.0:.1f} km from where {code} is recorded. "
                       f"Check you picked the right mark, then confirm to move it.")

    data = _load()
    record = data.get("marks", {}).get(code)
    if record is None:
        return False, "Mark not found."
    _stamp_position(record, latf, lonf, by=by, source=source, accuracy_m=acc)
    _save(data)
    if moved is None:
        return True, f"{code} position set."
    return True, f"{code} moved {moved:.0f} m."
