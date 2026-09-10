"""Colours shared by the film and its cards.

The app's own palette is deliberately plain, because it is a race document read
in daylight by somebody with a horn button. The film is not a document, so the
cards may be dramatic; what the two must agree on is which colour belongs to
which boat, so the hull you followed round the course is the colour beside its
name on the leaderboard.

Values are sRGB 0-255. ``build_scene`` converts them for the renderer, which
works in linear light; the card drawing uses them as they are.
"""
from __future__ import annotations

from typing import Dict, List, Tuple

# One per boat, in the order the export lists them. Chosen to stay apart from
# each other and from the sea at a distance, and to survive being shrunk to a
# hull a few pixels wide.
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

# The app's race-document theme, for the parts of the overlay that quote it.
THEME_SRGB: Dict[str, Tuple[int, int, int]] = {
    "paper": (247, 244, 236), "paper_2": (255, 253, 247), "paper_sunk": (239, 234, 222),
    "ink": (28, 26, 21), "ink_2": (74, 70, 60), "ink_faint": (125, 119, 106),
    "rule": (207, 199, 180), "rule_strong": (168, 159, 137),
    "panel": (16, 18, 15), "panel_2": (26, 29, 25), "panel_rule": (51, 56, 49),
    "panel_ink": (232, 230, 221), "panel_label": (179, 176, 164), "amber": (255, 176, 0),
    "flag_red": (200, 16, 46), "flag_green": (0, 132, 61), "flag_blue": (0, 52, 120),
    "white": (255, 255, 255),
    # The course board's rounding chips, same values the app uses.
    "port": (200, 16, 46), "starboard": (0, 132, 61),
}


def boat_colour(index: int) -> Tuple[int, int, int]:
    return BOAT_COLOURS[index % len(BOAT_COLOURS)]


def to_linear(rgb: Tuple[int, int, int]) -> Tuple[float, float, float]:
    """sRGB 0-255 to the linear floats Blender's shaders expect."""
    out = []
    for value in rgb:
        c = value / 255.0
        out.append(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4)
    return (out[0], out[1], out[2])
