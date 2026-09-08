"""How the course chart is drawn: weight, and shapes that stay where they are put.

The chart was too heavy — thick legs, dense marks — and the leg arrows did not
sit on the legs they belonged to. The cause of the second one is worth recording,
because it is a trap the whole file has to keep avoiding:

Leaflet's own stylesheet carries `.leaflet-marker-icon { display: block }`. Our
`.course-map-arrow { display: flex }` has the *same* specificity and loses on
source order, so the flex centring these icons relied on silently never applied.
The triangle sat at the icon's top-left instead of its centre — 6.5px right and
8px down, up to 10.1px off its leg — and the same bug put the boat label beside
the hull rather than under it. Measured in Chromium, not deduced.

The fix is to stop depending on the host stylesheet at all: the shapes are inline
SVG centred on their own viewBox, which cannot drift however Leaflet styles the
element around them.
"""
from __future__ import annotations

import pathlib
import re

JS = pathlib.Path("static/course_map.js").read_text(encoding="utf-8")
CSS = pathlib.Path("static/style.css").read_text(encoding="utf-8")


class TestShapesCentreThemselves:
    def test_the_leg_arrow_is_an_svg_centred_on_its_own_viewbox(self):
        """A viewBox of `-w/2 -h/2 w h` puts 0,0 at the middle of the icon, so the
        chevron drawn around the origin lands on the anchor whatever the CSS does."""
        m = re.search(r'function legArrowSvg[\s\S]{0,400}?viewBox="(-?[\d.]+) (-?[\d.]+) ([\d.]+) ([\d.]+)"', JS)
        assert m, "legArrowSvg no longer emits an svg with a viewBox"
        x, y, w, h = (float(g) for g in m.groups())
        assert (x, y) == (-w / 2, -h / 2)

    def test_the_boat_is_an_svg_centred_on_its_own_viewbox(self):
        m = re.search(r'function boatSvg[\s\S]{0,600}?viewBox="(-?[\d.]+) (-?[\d.]+) ([\d.]+) ([\d.]+)"', JS)
        assert m, "boatSvg no longer emits an svg with a viewBox"
        x, y, w, h = (float(g) for g in m.groups())
        assert (x, y) == (-w / 2, -h / 2)

    def test_neither_icon_relies_on_flex_centring_any_more(self):
        """The rule Leaflet was overriding is gone rather than merely outweighed."""
        arrow = re.search(r'\.course-map-arrow \{([^}]*)\}', CSS)
        assert arrow and "display: flex" not in arrow.group(1)
        boat = re.search(r'\.course-map-boat \{([^}]*)\}', CSS)
        assert boat and "display: flex" not in boat.group(1)

    def test_the_boat_label_is_positioned_absolutely_under_the_hull(self):
        label = re.search(r'\.course-map-boat-label \{([^}]*)\}', CSS)
        assert label and "position: absolute" in label.group(1)


class TestTheBoatIsBoatShaped:
    """A hull in plan view, so heading reads at a glance as well as position."""

    def test_the_hull_has_a_pointed_bow_and_a_flat_transom(self):
        m = re.search(r"const BOAT_HULL = '([^;]+)';", JS, re.S)
        assert m, "BOAT_HULL is gone"
        path = m.group(1).replace("'\n", " ").replace("+ '", " ")
        nums = [float(n) for n in re.findall(r'-?\d+\.?\d*', path)]
        xs, ys = nums[0::2], nums[1::2]
        # The bow is on the centreline at the top of the shape. It appears twice:
        # once as the start of the path and once where the path closes onto it.
        bow_y = min(ys)
        assert {xs[i] for i, y in enumerate(ys) if y == bow_y} == {0.0}
        # The transom is a straight edge across the stern — two points sharing the
        # largest y, symmetric about the centreline.
        stern_y = max(ys)
        stern_x = sorted(xs[i] for i, y in enumerate(ys) if y == stern_y)
        assert len(stern_x) == 2 and stern_x[0] == -stern_x[1] and stern_x[1] > 0

    def test_the_hull_points_north_at_zero_so_rotation_is_the_heading(self):
        """SVG rotate() is clockwise from the +x axis, and the bow is drawn at
        -y, so rotating by COG degrees points the boat where it is going."""
        assert re.search(r'rotate\(\$\{cog\.toFixed\(1\)\}\)', JS)

    def test_a_boat_with_no_heading_is_drawn_as_a_disc_not_a_guess(self):
        assert re.search(r'cog === null\)\s*\n?\s*\?\s*`<circle', JS)


class TestTheChartIsDrawnLightly:
    """Marks and legs should sit on the chart, not bury it."""

    def test_route_marks_are_translucent(self):
        rule = re.search(r'\.course-map-mark-route \{([^}]*)\}', CSS).group(1)
        assert "rgba(" in rule                      # fill lets the chart through

    def test_unlit_marks_are_lighter_still_than_route_marks(self):
        bg = re.search(r'\.course-map-mark-background \{([^}]*)\}', CSS).group(1)
        route = re.search(r'\.course-map-mark-route \{([^}]*)\}', CSS).group(1)
        alpha = lambda s: float(re.search(r'rgba\([^)]*,\s*([\d.]+)\)', s).group(1))
        assert alpha(bg) < alpha(route)

    def test_course_legs_are_hairlines_rather_than_ribbons(self):
        weights = [float(w) for w in re.findall(r'weight:\s*([\d.]+)', JS)]
        assert weights and max(weights) <= 5        # the widest is the line casing


def _hue(hex_colour):
    """Hue in degrees, 0 = red, 120 = green, 240 = blue."""
    import colorsys
    h = hex_colour.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    return colorsys.rgb_to_hls(r, g, b)[0] * 360.0


class TestBoatColoursDoNotClashWithTheCourse:
    """A red boat on a red leg is invisible, and red legs are not negotiable.

    The course says port with red, starboard with green, the finish in black and
    the marks in yellow — all meaning, none of it free to change. So the fleet
    has to live somewhere else on the wheel. Reported from a phone during a real
    race, where a red boat sat on a red leg and a green boat on a green one.
    """

    COURSE = {"port": "#dc2626", "starboard": "#15803d", "marks": "#fde047"}

    def _palette(self):
        js = pathlib.Path("static/race_replay.js").read_text(encoding="utf-8")
        m = re.search(r"const COLOURS = \[([^\]]+)\]", js, re.S)
        assert m, "the boat palette is gone"
        return re.findall(r"#[0-9a-fA-F]{6}", m.group(1))

    def test_the_palette_is_still_there(self):
        assert len(self._palette()) >= 6

    def test_no_boat_is_red_green_or_yellow(self):
        for colour in self._palette():
            h = _hue(colour)
            assert not (h > 340 or h < 25), f"{colour} is a red, like a port rounding"
            assert not (85 <= h <= 170), f"{colour} is a green, like a starboard rounding"
            assert not (35 <= h <= 70), f"{colour} is a yellow, like a mark"

    def test_the_course_colours_are_what_the_palette_is_avoiding(self):
        """Pins the assumption above: if the course palette changes, this fails
        and whoever changed it has to think about the boats too."""
        js = pathlib.Path("static/course_map.js").read_text(encoding="utf-8")
        assert self.COURSE["port"] in js and self.COURSE["starboard"] in js
        css = pathlib.Path("static/style.css").read_text(encoding="utf-8")
        assert "253, 224, 71" in css        # the marks' yellow, as rgba

    def test_the_sail_number_label_takes_the_boats_colour(self):
        js = pathlib.Path("static/course_map.js").read_text(encoding="utf-8")
        assert 'style="border-color:${fill}"' in js
