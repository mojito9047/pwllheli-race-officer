"""The rounding gate: a line through the mark that only reaches the correct side.

Three detectors decide a rounding, any one of them sufficient:

1. **The rounding radius.** A fix inside it — rounded. Cheapest, fires instantly on
   a tight rounding, and asks no questions about sides.
2. **The gate**, here. A line through the mark at right angles to the leg arriving
   at it, reaching ``reach_m`` (750 m by default) on the side the boat should pass
   and only the rounding radius on the side it should not. Crossing it is rounding.
3. **Closest approach and departure** — see MarkApproach: came within 400 m, has
   since opened up 50 m, and the next mark has closed since. The catch-all.

The gate is what the first and third cannot do. A boat rounding wide stays outside
the radius and may stay outside the neighbourhood too: in the night race of
2026-08-08 CRACKAJACK sailed round AA 420 m off, plainly round it and on the
correct side, and the walk missed it by 20 m against a 400 m neighbourhood. Since
the walk is sequential, that one miss stalled the boat for six hours — distance to
go rose instead of falling, the projected finish reached 165 hours, and because a
finish is only looked for once every earlier mark is rounded, its automatic finish
could never arrive either.

**Why this shape and not a swept angle.** The asymmetry does two jobs with one
number. The long side encodes *which hand* the mark must be left on, so a boat
passing 400 m out on the correct side crosses the gate and one passing 400 m out on
the wrong side does not. The short side is the width in which no side can be
determined at all — a boat closer to the mark than we know where the mark is has no
determinable side, and drift is exactly a reason not to discriminate — so inside the
radius the gate accepts either hand and the question is not asked.

And crossing is a *binary fact about the chord between two fixes*, which is what
makes this work at the reporting rate the club actually has. The Teltonika trackers
report every 61 s, which at 8 kn is 250 m of travel: a tight rounding can happen
entirely between two fixes. A test that accumulates a swept angle reads the
sampling rate rather than the boat there — measured, a correct 20 m rounding gave 87
degrees of confident *wrong-side* evidence, because the straight line between the
fixes either side of it ran 81 m the wrong side of the mark. The gate has no such
failure: the chord either crosses the segment or it does not.
"""
from __future__ import annotations

import math
from typing import Any, Dict, Optional, Tuple

# How far the gate reaches on the side the boat is supposed to pass. Wide, because
# its whole purpose is to catch the wide rounding that the radius and the
# neighbourhood both miss. Overridable per mark, and from Settings.
#
# 750 m, from the night race rather than from taste. Drawing level with AA on a
# 9.2 km leg, the three boats were 369 m, 468 m and 512 m off, all on the correct
# hand - so a 500 m reach missed the widest of them by twelve metres, and that one
# miss stalled the boat for six hours. The number wants to clear how wide a fleet
# actually rounds, with room to spare.
DEFAULT_GATE_REACH_M = 750.0

# What we know a mark's position to, when the mark itself does not say. A laid buoy
# on scope in a tideway is tens of metres; a surveyed transit is nearly nothing.
DEFAULT_MARK_ACCURACY_M = 50.0

_M_PER_DEG_LAT = 111132.0


def enu_m(lat: float, lon: float, lat0: float, lon0: float) -> Tuple[float, float]:
    """Offset in metres (east, north) of a point from an origin."""
    return ((lon - lon0) * 111320.0 * math.cos(math.radians(lat0)),
            (lat - lat0) * _M_PER_DEG_LAT)


def undetermined_side_m(accuracy_m: Optional[float], rounding_radius_m: float) -> float:
    """How far the gate reaches on the *wrong* side: the width of not knowing.

    The rounding radius, because detector 1 has already accepted anything inside it
    without asking about sides, and a gate that then refused the same water would
    have two detectors disagreeing about it. But never less than the mark's own
    position accuracy: a mark we only know to 80 m cannot have its side judged at
    60 m, whatever its radius says.
    """
    band = float(rounding_radius_m or 0.0)
    try:
        if accuracy_m is not None:
            band = max(band, float(accuracy_m))
    except (TypeError, ValueError):
        pass
    return band


def gate_ends(from_point: Dict[str, Any], mark: Dict[str, Any], side: Optional[str],
              reach_m: float, wrong_side_m: float) -> Optional[Tuple[Tuple[float, float],
                                                                    Tuple[float, float],
                                                                    Tuple[float, float]]]:
    """The gate as two planar endpoints, plus the leg direction, in metres about the mark.

    Returns ((correct_end, wrong_end), leg_unit) — or None when there is no leg to
    take a direction from. The frame's origin is the mark itself.
    """
    if not from_point or from_point.get("lat") is None:
        return None
    lx, ly = enu_m(float(from_point["lat"]), float(from_point["lon"]),
                   float(mark["lat"]), float(mark["lon"]))
    # That is the *previous* point relative to the mark, so the direction of travel
    # along the leg is the other way.
    length = math.hypot(lx, ly)
    if length <= 0.0:
        return None
    ux, uy = -lx / length, -ly / length          # unit vector, previous -> mark
    # Right of the direction of travel, in (east, north). A boat leaving the mark to
    # port passes on this side: the mark is then on its port hand.
    rx, ry = uy, -ux
    if side == "starboard":
        rx, ry = -rx, -ry
    elif side != "port":
        return None                              # nothing to be asymmetric about
    correct_end = (rx * reach_m, ry * reach_m)
    wrong_end = (-rx * wrong_side_m, -ry * wrong_side_m)
    return correct_end, wrong_end, (ux, uy)


def crossed_gate(prev_fix: Optional[Dict[str, Any]], fix: Dict[str, Any],
                 from_point: Optional[Dict[str, Any]], mark: Dict[str, Any],
                 side: Optional[str], reach_m: float, wrong_side_m: float) -> bool:
    """Did the boat's path from prev_fix to fix cross this mark's gate, going forward?

    Forward only: a boat that has overstood and is sailing back down the leg must
    not be handed the rounding for re-crossing the same line the wrong way.
    """
    if prev_fix is None or not from_point:
        return False
    ends = gate_ends(from_point, mark, side, reach_m, wrong_side_m)
    if ends is None:
        return False
    correct_end, wrong_end, leg = ends
    p1 = enu_m(float(prev_fix["lat"]), float(prev_fix["lon"]),
               float(mark["lat"]), float(mark["lon"]))
    p2 = enu_m(float(fix["lat"]), float(fix["lon"]),
               float(mark["lat"]), float(mark["lon"]))
    # Travelling along the leg, not back down it.
    if (p2[0] - p1[0]) * leg[0] + (p2[1] - p1[1]) * leg[1] <= 0.0:
        return False
    from core.track import segment_intersection_fraction
    return segment_intersection_fraction(p1, p2, correct_end, wrong_end) is not None
