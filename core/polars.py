"""Pure polar / VMG / sail-selection maths.

Extracted verbatim from app.py. Every function is a pure function of its
arguments (polar rows, sail charts, angles) with no Flask, database or
module-global dependencies, so it can be unit-tested in isolation.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional


def nearest_number(values: List[float], target: float) -> Optional[float]:
    """Return the numeric value nearest to a requested target value."""
    if not values:
        return None
    return min(values, key=lambda v: abs(float(v) - target))


def interpolate_points(points: List[Dict[str, float]], x: float) -> Optional[float]:
    """Linearly interpolate between two polar-table points."""
    if not points:
        return None
    pts = sorted(points, key=lambda p: p["x"])
    if x <= pts[0]["x"]:
        return pts[0]["y"]
    if x >= pts[-1]["x"]:
        return pts[-1]["y"]
    for a, b in zip(pts, pts[1:]):
        if a["x"] <= x <= b["x"]:
            span = b["x"] - a["x"]
            f = 0.0 if span == 0 else (x - a["x"]) / span
            return a["y"] + f * (b["y"] - a["y"])
    return None


def polar_usable_points(row: Dict[str, Any]) -> List[Dict[str, float]]:
    """Return usable TWA/speed points from a polar row, excluding blanks and zero-speed placeholders."""
    pts = []
    for p in row.get("points", []):
        twa = float(p.get("twa", 0))
        bsp = float(p.get("bsp", 0))
        if twa > 0 and bsp > 0:
            pts.append({"twa": twa, "bsp": bsp})
    return sorted(pts, key=lambda p: p["twa"])


def row_speed_at_twa(row: Dict[str, Any], twa: float) -> Optional[float]:
    """Interpolate target boat speed for one polar row at a requested TWA."""
    pts = [{"x": p["twa"], "y": p["bsp"]} for p in polar_usable_points(row)]
    return interpolate_points(pts, abs(twa))


def row_min_twa(row: Dict[str, Any]) -> Optional[float]:
    """Return the lowest usable TWA in a polar row."""
    pts = polar_usable_points(row)
    return pts[0]["twa"] if pts else None


def row_max_twa(row: Dict[str, Any]) -> Optional[float]:
    """Return the deepest usable TWA in a polar row."""
    pts = polar_usable_points(row)
    return pts[-1]["twa"] if pts else None


def row_best_downwind_vmg(row: Dict[str, Any]) -> Optional[Dict[str, float]]:
    """Find the downwind angle that gives the best VMG for one polar row."""
    pts = polar_usable_points(row)
    if not pts:
        return None
    best = None
    for p in pts:
        if p["twa"] < 90:
            continue
        vmg = p["bsp"] * math.cos(math.radians(180 - p["twa"]))
        if best is None or vmg > best["vmg"]:
            best = {"twa": p["twa"], "bsp": p["bsp"], "vmg": vmg}
    if best is None:
        p = pts[-1]
        best = {"twa": p["twa"], "bsp": p["bsp"], "vmg": p["bsp"] * math.cos(math.radians(180 - p["twa"]))}
    return best


def interpolate_rows_by_tws(polar_rows: List[Dict[str, Any]], tws: float, fn) -> Any:
    """Interpolate a polar between wind-speed rows."""
    rows = sorted(polar_rows, key=lambda r: float(r["tws"]))
    if not rows:
        return None
    if tws <= rows[0]["tws"]:
        return fn(rows[0])
    if tws >= rows[-1]["tws"]:
        return fn(rows[-1])
    for low, high in zip(rows, rows[1:]):
        if low["tws"] <= tws <= high["tws"]:
            a = fn(low)
            b = fn(high)
            if a is None or b is None:
                return None
            f = (tws - low["tws"]) / (high["tws"] - low["tws"] or 1)
            if isinstance(a, (int, float)) and isinstance(b, (int, float)):
                return float(a) + f * (float(b) - float(a))
            return {
                "twa": a["twa"] + f * (b["twa"] - a["twa"]),
                "bsp": a["bsp"] + f * (b["bsp"] - a["bsp"]),
                "vmg": a["vmg"] + f * (b["vmg"] - a["vmg"]),
            }
    return None


def target_speed_info_for(twa: float, tws: float, polar_rows: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Return target boat speed and course-made-good speed for a leg.

    This mirrors the old RTC calculator behaviour: if the leg is above the minimum polar
    TWA, use upwind VMG. If the leg is deeper than the best downwind VMG angle, use gybe
    VMG. Otherwise use interpolated polar boat speed directly.
    """
    if not polar_rows or not math.isfinite(tws):
        return None
    abs_twa = abs(twa)
    min_twa = interpolate_rows_by_tws(polar_rows, tws, row_min_twa)
    max_twa = interpolate_rows_by_tws(polar_rows, tws, row_max_twa)
    if min_twa is None or max_twa is None:
        return None

    if abs_twa < min_twa:
        bsp = interpolate_rows_by_tws(polar_rows, tws, lambda row: row_speed_at_twa(row, min_twa))
        if not bsp:
            return None
        cmg = float(bsp) * math.cos(math.radians(min_twa - abs_twa))
        return {"bsp": float(bsp), "cmg": max(0.0, cmg), "polar_twa": float(min_twa), "mode": "upwind"}

    best_dw = interpolate_rows_by_tws(polar_rows, tws, row_best_downwind_vmg)
    if best_dw and best_dw["twa"] >= 90 and abs_twa > best_dw["twa"]:
        cmg = best_dw["bsp"] * math.cos(math.radians(abs_twa - best_dw["twa"]))
        return {"bsp": best_dw["bsp"], "cmg": max(0.0, cmg), "polar_twa": best_dw["twa"], "mode": "downwind"}

    polar_twa = min(abs_twa, max_twa)
    bsp = interpolate_rows_by_tws(polar_rows, tws, lambda row: row_speed_at_twa(row, polar_twa))
    if not bsp:
        return None
    return {"bsp": float(bsp), "cmg": float(bsp), "polar_twa": float(polar_twa), "mode": "limited" if polar_twa != abs_twa else "direct"}


def sail_for(twa: float, tws: float, sail_chart: Dict[str, Any]) -> str:
    """Look up the recommended sail for a TWS/TWA from the sail chart."""
    twas = sail_chart.get("twas", [])
    rows = sail_chart.get("rows", [])
    if not twas or not rows:
        return "—"
    nearest_tws = nearest_number([float(r["tws"]) for r in rows], tws)
    if nearest_tws is None:
        return "—"
    row = next((r for r in rows if float(r["tws"]) == float(nearest_tws)), None)
    nearest_twa = nearest_number([float(x) for x in twas], round(twa))
    if row is None or nearest_twa is None:
        return "—"
    try:
        col = [float(x) for x in twas].index(float(nearest_twa))
        sail = row["sails"][col]
    except (ValueError, IndexError):
        sail = ""
    return sail.strip() if isinstance(sail, str) and sail.strip() else "—"


def point_of_sail(twa: float) -> str:
    """Convert a true wind angle into a readable point-of-sail label."""
    if twa < 35:
        return "Beat/VMG"
    if twa < 60:
        return "Close hauled"
    if twa < 90:
        return "Close reach"
    if twa < 120:
        return "Beam reach"
    if twa < 150:
        return "Broad reach"
    return "Run/gybe VMG"


def leg_side(course_bearing: float, twd: float) -> str:
    """Return whether the apparent course leg is on port or starboard tack/gybe."""
    signed = ((twd - course_bearing + 540) % 360) - 180
    if abs(signed) < 0.5 or abs(abs(signed) - 180) < 0.5:
        return "—"
    return "Starboard" if signed > 0 else "Port"


def format_minutes(value: Optional[float]) -> str:
    """Format minutes as a short race-duration string."""
    if value is None:
        return "—"
    if value < 60:
        return f"{value:.0f} min"
    h = int(value // 60)
    m = int(round(value % 60))
    if m == 60:
        h += 1
        m = 0
    return f"{h}h {m:02d}m"


def format_target(target: Optional[Dict[str, Any]]) -> str:
    """Format target speed and VMG-mode information for display."""
    if not target:
        return "—"
    if target.get("mode") in ("upwind", "downwind"):
        return f"{target['bsp']:.2f} kt @ {target['polar_twa']:.0f}°"
    return f"{target['bsp']:.2f} kt"
