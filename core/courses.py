"""Course geometry: mark expansion, leg building, length and recommendation.

Extracted from app.py. Reads the fixed-course and mark data from core.appstate
(so a backup restore that reloads that data is reflected here) and uses the pure
geometry helpers from core.timeutils. No Flask, request-context or database
access -- functions take race rows / course dicts as arguments.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any, Dict, Iterable, List, Optional, Tuple

from core import appstate
from core.timeutils import angular_diff, bearing_deg, haversine_nm
from core.polars import (  # leg analysis needs the speed/sail model
    leg_side, point_of_sail, sail_for, target_speed_info_for,
)
from core.timeutils import angular_diff


def wind_in_range(twd: float, start: float, end: float) -> bool:
    """Return whether a wind direction falls inside a wrapped compass range."""
    twd = twd % 360
    start = start % 360
    end = end % 360
    if start <= end:
        return start <= twd <= end
    return twd >= start or twd <= end


def course_wind_score(course: Dict[str, Any], twd: float) -> float:
    """Score how well a fixed course's wind band matches the current wind."""
    rng = course["wind_range_deg"]
    if wind_in_range(twd, rng["from"], rng["to"]):
        return 0.0
    return angular_diff(twd, float(course["wind_bearing_deg"]))


def recommend_courses(twd: float, target_minutes: float, average_speed_kn: float) -> List[Dict[str, Any]]:
    """Rank fixed courses from the CHPSC course list for a given wind direction and target length."""
    target_nm = max(0.1, average_speed_kn * target_minutes / 60.0)
    rows = []
    for c in appstate.COURSES:
        wind_score = course_wind_score(c, twd)
        numeric_length = course_numeric_length(c)
        length_score = abs(numeric_length - target_nm)
        # Wind suitability dominates. Within a wind band, favour length match.
        score = wind_score * 10.0 + length_score
        rows.append({**c, "length_nm": round(numeric_length, 2), "wind_score": wind_score, "length_score": length_score, "target_nm": target_nm, "score": score})
    return sorted(rows, key=lambda r: (r["score"], r["length_score"]))


def is_waypoint(mark: str) -> bool:
    """Is this mark a waypoint rather than something boats round?

    A waypoint exists to bend a leg. Pwllheli's long courses run west past the
    Llyn peninsula, and a straight line from mark 2 to the Gwylan Islands crosses
    Mynytho, Llangian and Trwyn Cilan: the chart draws a course over land, the leg
    measures 10.9 nm against the 13.1 nm actually sailed, and a single bearing of
    243T stands in for a 211T reach followed by a 278T broad reach — so the polar
    projection is wrong for both halves of it.

    Boats are not asked to round a waypoint and it is not a mark: it stays off the
    course board, out of the announcement, out of the shortening options and out of
    the mark count. It is in the geometry only, and the course simply changes
    direction there.
    """
    return bool((appstate.MARKS.get(str(mark or "").strip().upper()) or {}).get("waypoint"))


def course_marks_only(course: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The course as the race officer and the competitors see it: no waypoints."""
    return [m for m in (course.get("marks") or [])
            if not is_waypoint(str(m.get("mark", "")))]


def mark_display_code(mark: str) -> str:
    """Return the label to show for a mark or compound-mark corner."""
    code = str(mark or "").strip().upper()
    return str(appstate.MARKS.get(code, {}).get("display") or code)


def compound_mark_components(mark: str, rounding: str) -> List[str]:
    """Return the geometry marks used for a course mark and rounding direction.

    Normal marks expand to themselves. Compound marks, such as Y and A,
    expand to their component corner marks in the order boats must round them.
    The parent mark is still retained in the course sequence for course-board
    display and audio announcements.
    """
    code = str(mark or "").strip().upper()
    md = appstate.MARKS.get(code) or {}
    rounding_key = "starboard" if str(rounding or "").lower().startswith("s") else "port"
    order = md.get("rounding_order") if isinstance(md.get("rounding_order"), dict) else {}
    selected = order.get(rounding_key)
    if selected:
        return [str(m).strip().upper() for m in selected if str(m).strip().upper() in appstate.MARKS]
    components = md.get("components") if isinstance(md.get("components"), list) else []
    components = [str(m).strip().upper() for m in components if str(m).strip().upper() in appstate.MARKS]
    if components:
        return list(reversed(components)) if rounding_key == "starboard" else components
    return [code]


def expand_course_points(course: Dict[str, Any], start_at_mark: str = "O") -> List[Dict[str, Any]]:
    """Expand a course sequence into physical points for geometry/charting.

    Compound parent marks remain in `course["marks"]` for course-board display,
    but this expanded list contains the actual corner coordinates used for leg
    lengths, TWA analysis and chart plotting.
    """
    points: List[Dict[str, Any]] = []
    start_code = str(start_at_mark or "O").strip().upper()
    points.append({
        "mark": start_code,
        "display_mark": mark_display_code(start_code),
        "rounding": "start",
        "token": start_code,
        "parent_mark": start_code,
        "is_compound_component": False,
    })
    for item in course.get("marks", []):
        parent = str(item.get("mark", "")).strip().upper()
        if is_waypoint(parent):
            # In the geometry, and nowhere else. No rounding, so no port/starboard
            # colouring on the chart and nothing for the board to print.
            points.append({
                "mark": parent, "display_mark": mark_display_code(parent),
                "rounding": "via", "token": parent, "parent_mark": parent,
                "parent_display": mark_display_code(parent),
                "is_compound_component": False, "waypoint": True,
            })
            continue
        rounding = "starboard" if str(item.get("rounding", "port")).lower().startswith("s") else "port"
        token = str(item.get("token") or f"{parent}{rounding[0]}")
        components = compound_mark_components(parent, rounding)
        for comp in components:
            points.append({
                "mark": comp,
                "display_mark": mark_display_code(comp),
                "rounding": rounding,
                "token": token,
                "parent_mark": parent,
                "parent_display": mark_display_code(parent),
                "is_compound_component": comp != parent,
            })
    return points


def selectable_mark_names() -> List[str]:
    """Return mark names that should appear on course-board/manual-course pickers."""
    return sorted([m for m, md in appstate.MARKS.items() if not md.get("hidden_from_picker")], key=mark_sort_key)


def course_length_nm(course: Dict[str, Any],
                    marks: Optional[Dict[str, Any]] = None) -> Optional[float]:
    """Estimate a course length from its sequence of positioned marks."""
    legs = course_legs(course, marks=marks)
    distances = [leg.get("distance_nm") for leg in legs if leg.get("distance_nm") is not None]
    if not distances or len(distances) != len(legs):
        return None
    return sum(float(d) for d in distances)


def course_uses_compound_marks(course: Dict[str, Any]) -> bool:
    """Return whether a course contains parent compound marks."""
    return any((appstate.MARKS.get(str(m.get("mark", "")).strip().upper()) or {}).get("compound") for m in course.get("marks", []))


def course_numeric_length(course: Dict[str, Any]) -> float:
    """Return a numeric course length, recalculating compound-mark courses."""
    if course_uses_compound_marks(course):
        computed = course_length_nm(course)
        if computed is not None:
            return float(computed)
    try:
        return float(course.get("length_nm"))
    except Exception:
        computed = course_length_nm(course)
        return float(computed) if computed is not None else 999.0


def course_for_display(course: Dict[str, Any]) -> Dict[str, Any]:
    """Return a course copy with computed length when compound marks are present."""
    c = dict(course)
    if course_uses_compound_marks(c):
        length = course_length_nm(c)
        if length is not None:
            c["length_nm"] = round(length, 2)
    # What the course board, the competitor page and the clubhouse display print.
    # Computed here once: six templates render these chips, and asking each of them
    # to remember to leave the waypoints out is how five of them would not.
    c["board_marks"] = course_marks_only(c)
    return c


def course_sequence_text(course: Dict[str, Any]) -> str:
    """Return a compact text version of a course mark sequence.

    Uses the display label rather than the JSON key, so a course to one corner of
    a compound mark reads ``Yap`` and not ``YAp``. The corners are keyed YA/YB
    because the keys are upper-case throughout, but Ya is what the sailing
    instructions call it and what belongs on the board.
    """
    return " ".join(f"{mark_display_code(m.get('mark',''))}{str(m.get('rounding',''))[:1].lower()}"
                    for m in course_marks_only(course))


def course_shorten_options(course: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Marks in the course sequence a shortened course could be called at.

    One option per rounding, in order: {index, code, display, label}. A mark that
    is rounded more than once is numbered so each rounding is a distinct choice.
    """
    marks = course.get("marks", []) or []
    opts = []
    for i, item in enumerate(marks):
        code = str(item.get("mark", "")).strip().upper()
        if not code or is_waypoint(code):
            # "Shorten the course at the turning point off Cilan" is not a thing a
            # race officer can say on the water, so it is not offered here. The index
            # is still the index into course["marks"], so shortening at a real mark
            # further on truncates the waypoints before it along with everything else.
            continue
        opts.append({"index": i, "code": code, "display": mark_display_code(code)})
    counts: Dict[str, int] = {}
    for o in opts:
        counts[o["code"]] = counts.get(o["code"], 0) + 1
    running: Dict[str, int] = {}
    for o in opts:
        if counts[o["code"]] > 1:
            running[o["code"]] = running.get(o["code"], 0) + 1
            o["label"] = f"{o['display']} (rounding {running[o['code']]})"
        else:
            o["label"] = o["display"]
    return opts


def apply_course_shortening(course: Dict[str, Any], index: Any) -> Dict[str, Any]:
    """Return the course truncated so the final rounding is at `index` (0-based,
    inclusive); boats then proceed straight to the finish. An out-of-range or
    missing index returns the course unchanged."""
    marks = course.get("marks", []) or []
    try:
        i = int(index)
    except (TypeError, ValueError):
        return course
    if i < 0 or i >= len(marks):
        return course
    shortened = dict(course)
    shortened["marks"] = marks[: i + 1]
    shortened["shortened_at_index"] = i
    # The course board and the length are of the course, so they have to be of
    # the *shortened* course: a copied dict kept the full thirteen-mark board and
    # 10.3 nm beside the words "shortened at 4", which is the one place somebody
    # reads the course out from.
    shortened["board_marks"] = course_marks_only(shortened)
    length = course_length_nm(shortened)
    shortened["length_nm"] = round(length, 2) if length is not None else "—"
    return shortened


def course_from_sequence(sequence: Iterable[Dict[str, Any]], course_no: Any = "Made up course", wind_label: str = "Made up course", source: str = "manual") -> Dict[str, Any]:
    """Build a temporary course object from a hand-entered mark sequence."""
    marks: List[Dict[str, str]] = []
    for item in sequence:
        mark = str(item.get("mark", "")).strip().upper()
        rounding_raw = str(item.get("rounding", "port")).strip().lower()
        if mark not in appstate.MARKS:
            continue
        # A waypoint keeps "via". Folding it to port here is what put a red TCp
        # chip on the course board for a turning point nobody rounds: the stored
        # course was right, and this rebuilt it wrong on the way back out.
        if is_waypoint(mark):
            marks.append({"mark": mark, "rounding": "via", "token": f"via {mark}"})
            continue
        rounding = "starboard" if rounding_raw.startswith("s") else "port"
        marks.append({"mark": mark, "rounding": rounding, "token": f"{mark}{rounding[0]}"})
    course = {
        "course_no": course_no,
        "wind_label": wind_label,
        "wind_range_deg": {"from": 0, "to": 359},
        "marks": marks,
        "sequence_text": "",
        "length_nm": "—",
        "source": source,
    }
    length = course_length_nm(course) if marks else None
    course["length_nm"] = round(length, 2) if length is not None else "—"
    course["sequence_text"] = course_sequence_text(course)
    course["board_marks"] = course_marks_only(course)
    return course


def custom_course_from_race(race: sqlite3.Row) -> Optional[Dict[str, Any]]:
    """Return the made-up course stored on a race, if one is active."""
    if race is None or "custom_course_json" not in race.keys():
        return None
    raw = (race["custom_course_json"] or "").strip()
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except Exception:
        return None
    sequence = data.get("marks") if isinstance(data, dict) else data
    if not isinstance(sequence, list):
        return None
    course = course_from_sequence(sequence, course_no="Made up course", wind_label="Made up course", source="manual")
    return course if course.get("marks") else None


def course_for_race(race: sqlite3.Row) -> Dict[str, Any]:
    """Resolve the course currently active for a race."""
    custom = custom_course_from_race(race)
    if custom:
        return custom
    return course_for_display(appstate.COURSE_BY_NO[int(race["course_no"])])


def mark_sort_key(mark: str) -> Tuple[int, Any]:
    """Sort mark labels in a race-office friendly order.

    A compound mark's corners sort immediately under it — Y, Ya, Yb — rather than
    alphabetically at the end of the list, where Aa and Ab would land nowhere near
    the A they belong to. The corners are pickable in their own right, so on the
    picker they need to read as what they are: the two halves of one mark.
    """
    text = str(mark)
    if text.isdigit():
        return (0, int(text))
    order = {"O": 100, "F": 101, "C": 102, "E": 103, "T": 104, "M": 105, "Y": 106, "A": 107}
    if text in order:
        return (1, order[text], 0, text)
    parent = str((appstate.MARKS.get(text) or {}).get("component_of") or "")
    if parent in order:
        return (1, order[parent], 1, text)      # 1 sorts the corners after the parent
    return (1, 999, 0, text)


def validate_course_sequence_json(raw: str) -> List[Dict[str, str]]:
    """Validate a manual-course JSON payload from the browser."""
    try:
        data = json.loads(raw or "[]")
    except Exception as exc:
        raise ValueError("Could not read the manual course sequence.") from exc
    if not isinstance(data, list):
        raise ValueError("Manual course sequence must be a list of marks.")
    sequence = []
    for item in data:
        if not isinstance(item, dict):
            continue
        mark = str(item.get("mark", "")).strip().upper()
        rounding = str(item.get("rounding", "port")).strip().lower()
        if mark not in appstate.MARKS:
            raise ValueError(f"Unknown mark: {mark}")
        if appstate.MARKS[mark].get("hidden_from_picker"):
            parent = appstate.MARKS[mark].get("component_of") or "its parent compound mark"
            raise ValueError(f"{mark_display_code(mark)} is a compound-mark corner. Add {parent} to the course instead.")
        if is_waypoint(mark):
            # Not port or starboard: boats pass it, and being told to leave a turning
            # point on a particular hand would be an instruction nobody could follow.
            sequence.append({"mark": mark, "rounding": "via"})
            continue
        sequence.append({"mark": mark, "rounding": "starboard" if rounding.startswith("s") else "port"})
    if not sequence:
        raise ValueError("Add at least one mark to the manual course.")
    return sequence


def course_legs(course: Dict[str, Any], start_at_mark: str = "O",
                marks: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    # Approximation: use ODM O as the line endpoint for start/finish geometry.
    # Final official camera line needs bridge/window calibration.
    """Build leg geometry for a fixed or made-up course.

    Compound marks are expanded here. For example Yp becomes Ya then Yb,
    while Ys becomes Yb then Ya. The original parent mark remains in
    `course["marks"]` for course-board display.

    ``marks`` overrides the current positions — pass ``track.race_marks(race)``
    to measure a past race's legs against the buoys it was actually sailed to.
    Correcting a dragged mark would otherwise rewrite the recorded length and
    bearings of every race that used it.
    """
    at = appstate.MARKS if marks is None else marks
    points = expand_course_points(course, start_at_mark=start_at_mark)
    legs = []
    for i in range(len(points) - 1):
        a = points[i]
        b = points[i + 1]
        ma = at.get(a["mark"])
        mb = at.get(b["mark"])
        leg = {
            "from": a["display_mark"],
            "to": b["display_mark"],
            "from_mark": a["mark"],
            "to_mark": b["mark"],
            "from_parent_mark": a.get("parent_mark"),
            "to_parent_mark": b.get("parent_mark"),
            "to_rounding": b.get("rounding", ""),
            "to_token": b.get("token", ""),
            "to_is_compound_component": b.get("is_compound_component", False),
            "distance_nm": None,
            "bearing_deg": None,
        }
        if ma and mb and ma.get("lat") is not None and mb.get("lat") is not None:
            leg["distance_nm"] = haversine_nm(ma["lat"], ma["lon"], mb["lat"], mb["lon"])
            leg["bearing_deg"] = bearing_deg(ma["lat"], ma["lon"], mb["lat"], mb["lon"])
        legs.append(leg)
    return legs


def number_words(n: int) -> str:
    """Convert small integer mark/course numbers to spoken words for audio announcements."""
    words = {
        0: "zero", 1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven", 8: "eight", 9: "nine",
        10: "ten", 11: "eleven", 12: "twelve", 13: "thirteen", 14: "fourteen", 15: "fifteen", 16: "sixteen", 17: "seventeen", 18: "eighteen", 19: "nineteen",
        20: "twenty", 30: "thirty", 40: "forty", 50: "fifty", 60: "sixty", 70: "seventy", 80: "eighty", 90: "ninety",
    }
    if n in words:
        return words[n]
    if 20 < n < 100:
        return words[n // 10 * 10] + " " + words[n % 10]
    return str(n)


def spoken_mark(mark: str) -> str:
    """Convert a mark rounding token into a clear spoken VHF/audio phrase."""
    text = str(mark or "").strip().upper()
    special = {"O": "outer distance mark", "F": "fairway", "C": "causeway", "E": "Eurwyn", "T": "Tom buoy", "M": "Madog fairway", "Y": "Gwylan Islands", "A": "mark A"}
    if text in special:
        return special[text]
    if text.isdigit():
        return "mark " + number_words(int(text))
    # A single corner of a compound mark. The board is happy with "Ya", but the
    # bare key read aloud is "mark why-ay", so name the feature and then the
    # corner: "Gwylan Islands corner Ya". Nobody on the water should have to work
    # out which rock that meant.
    parent = str((appstate.MARKS.get(text) or {}).get("component_of") or "")
    if parent:
        return f"{spoken_mark(parent)} corner {mark_display_code(text)}"
    return "mark " + text


def course_announcement_text(race: Dict[str, Any], course: Dict[str, Any]) -> str:
    """Build a clear spoken course announcement for central hut-PC audio.

    Keep each mark as its own short sentence.  Local speech engines tend to be
    more reliable if numeric marks are spoken as words, for example "mark two
    to port", rather than starting an utterance with a bare digit.
    """
    # A course nobody has chosen must not be announced over the VHF: the stored
    # course number is a fallback so the geometry has something to work with, and
    # reading it out would send the fleet round a course the race officer never
    # picked.
    try:
        if not int(race["course_set"] if "course_set" in race.keys() else 1):
            return ""
    except (KeyError, IndexError, TypeError, ValueError):
        pass
    parts = []
    # course_marks_only: a waypoint bends the leg, it is not something to announce.
    for m in course_marks_only(course):
        mark = str(m.get("mark", "")).strip()
        rounding = str(m.get("rounding", "")).strip().lower()
        if not mark:
            continue
        name = spoken_mark(mark)
        if rounding.startswith("p"):
            parts.append(f"{name} to port")
        elif rounding.startswith("s"):
            parts.append(f"{name} to starboard")
        else:
            parts.append(name)
    course_no = race["course_no"] if hasattr(race, "keys") and "course_no" in race.keys() else course.get("course_no", "")
    if str(course.get("source", "")).lower() == "manual" or str(course.get("course_no", "")).lower() in ("manual", "made up course"):
        intro = "The course for the next race will be a made up course."
    else:
        try:
            intro = f"The course for the next race will be course {number_words(int(course_no))}."
        except Exception:
            intro = f"The course for the next race will be course {course_no}."
    if parts:
        return intro + " " + ". ".join(parts) + "."
    return intro


def course_leg_analysis(course: Dict[str, Any], twd: float, tws: float, polar_rows: List[Dict[str, Any]], sail_chart: Dict[str, Any], marks: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Analyse every leg for TWA, sail choice, target speed and predicted time."""
    analysed = []
    for leg in course_legs(course, marks=marks):
        if leg["distance_nm"] is None or leg["bearing_deg"] is None:
            analysed.append({**leg, "twa": None, "side": "—", "point_of_sail": "—", "sail": "—", "sail_twa": None, "target": None, "leg_minutes": None})
            continue
        twa = angular_diff(float(leg["bearing_deg"]), twd)
        target = target_speed_info_for(twa, tws, polar_rows)
        leg_minutes = None
        if target and target.get("cmg", 0) > 0:
            leg_minutes = float(leg["distance_nm"]) / float(target["cmg"]) * 60.0
        # For VMG legs the boat is not actually sailing the leg TWA.
        # Upwind it sails at the polar minimum TWA and tacks; deep downwind it
        # sails at the best downwind VMG angle and gybes.  The sail chart must
        # therefore be looked up using the polar/sailed TWA, not the course-made
        # good leg TWA.  Otherwise a leg at, for example, 166° may show no sail
        # even though the target says to sail 139° where the chart has a valid
        # kite selection.
        sail_lookup_twa = float(target.get("polar_twa", twa)) if target else twa
        analysed.append({
            **leg,
            "twa": twa,
            "side": leg_side(float(leg["bearing_deg"]), twd),
            "point_of_sail": point_of_sail(twa),
            "sail": sail_for(sail_lookup_twa, tws, sail_chart),
            "sail_twa": sail_lookup_twa,
            "target": target,
            "leg_minutes": leg_minutes,
        })
    return analysed
