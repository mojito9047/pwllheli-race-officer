#!/usr/bin/env python3
"""Everything drawn on top of the render: boat tags, mark numbers, HUD, cards.

These all used to be geometry in the 3D scene, parented to whichever camera was
live. That had three problems and the pictures showed all of them. The camera
focuses on what it is pointed at, hundreds of metres away, so a panel sitting
1.6 m from the lens was thrown far out of focus and the race clock came out as a
smear. Motion blur hit them for the same reason. And text built from curves and
rasterised at whatever size the perspective gave it can never be as crisp as
type drawn straight into the pixel grid.

Drawing them here instead fixes all three at once, costs a few milliseconds a
frame rather than geometry in every shot, and means the whole overlay can be
restyled without re-rendering a single 3D frame.

Screen positions for the things that are anchored to the world -- the boats and
the marks -- cannot be worked out here, because only Blender knows where the
camera was. ``build_scene.py --overlay-track`` writes them out once, and this
reads that file.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import sys
from typing import Any, Dict, List, Optional, Sequence, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from replay_style import THEME_SRGB, boat_colour  # noqa: E402
from replay_time import TimeWarp  # noqa: E402
from wind import Wind  # noqa: E402

# Everything below is a fraction of the frame height, so the overlay is the same
# size whatever resolution the film is rendered at.
PAD = 0.011
TAG_LEADER = 0.075          # the plate floats this far above the boat
TAG_GAP = 0.008             # clearance between two plates that would collide
TAG_TEXT = 0.017
TAG_PLATE_H = 0.030
MARK_TEXT = 0.021
HUD_LABEL = 0.013
HUD_TIME = 0.040
HUD_STATE = 0.019
HUD_NAME = 0.024
HUD_CHIP = 0.015
HUD_CHIP_H = 0.026
CREDIT_TEXT = 0.010
GAP = 0.010                 # between the panels of the lower third
MARGIN_X = 0.0125           # from the frame edge, as a fraction of WIDTH
MARGIN_Y = 0.0175

# Branding, to the same numbers core/video.py burns into the club's start and
# finish videos: the club mark top left, one sponsor at a time top right,
# changing every five seconds.
BRAND_MARGIN = 0.022        # of frame height
BRAND_CAP_H = 0.15          # of frame height
BRAND_CLUB_W = 0.22         # of frame width
BRAND_SPONSOR_W = 0.24      # of frame width
BRAND_CLUB_ALPHA = 0.82
BRAND_SPONSOR_ALPHA = 0.90

PANEL_ALPHA = 220
PAPER_ALPHA = 224
PLATE_ALPHA = 205


def _rgba(key: str, alpha: int = 255) -> Tuple[int, int, int, int]:
    r, g, b = THEME_SRGB[key]
    return (r, g, b, alpha)


class Overlay:
    """Draws the overlay layer for one film, frame by frame."""

    def __init__(self, data: Dict[str, Any], track: Optional[Dict[str, Any]],
                 size: Tuple[int, int], base_dir: str) -> None:
        from PIL import ImageFont

        self.data = data
        self.track = track or {}
        self.width, self.height = size
        self.base = base_dir
        self.scale = self.height / 1080.0
        fonts_dir = os.path.join(base_dir, "fonts")

        def font(name: str, fraction: float):
            path = os.path.join(fonts_dir, name)
            px = max(7, int(round(self.height * fraction)))
            if os.path.exists(path):
                return ImageFont.truetype(path, px)
            return ImageFont.load_default()

        self.f_tag = font("ArchivoNarrow.ttf", TAG_TEXT)
        self.f_mark = font("ArchivoNarrow.ttf", MARK_TEXT)
        self.f_label = font("ArchivoNarrow.ttf", HUD_LABEL)
        self.f_time = font("PlexMono600.ttf", HUD_TIME)
        self.f_state = font("ArchivoNarrow.ttf", HUD_STATE)
        self.f_name = font("Archivo.ttf", HUD_NAME)
        self.f_chip = font("ArchivoNarrow.ttf", HUD_CHIP)
        self.f_credit = font("ArchivoNarrow.ttf", CREDIT_TEXT)

        self.warp = TimeWarp.from_dict(data["film"]) if data.get("film") else None
        self.t0_epoch = float(data["time"]["t0_epoch"])
        self.first_start = float(data["time"]["first_start_rel"])
        self.speed = float((data.get("film") or {}).get("speed") or 30.0)
        self.fps = int((data.get("film") or {}).get("fps") or 24)

        race = data["race"]
        self.race_name = race.get("name") or ""
        self.course_label = (f"COURSE {race['course_no']}"
                             if race.get("course_no") not in (None, "") else "COURSE")
        self.tokens = _course_tokens(race.get("course_text", ""))
        self.credit = ((data.get("terrain") or {}).get("imagery_info") or {}).get("credit")

        self.colours: Dict[str, Tuple[int, int, int]] = {}
        for i, boat in enumerate(data.get("boats") or []):
            name = boat.get("name") or f"Boat {i + 1}"
            self.colours[name] = tuple(boat.get("colour") or boat_colour(i))

        self._cards: Dict[str, Any] = {}
        self._card_plan = self._plan_cards()

        # The same lookup build_scene sets the sails from, so the number on
        # screen and the trim of the boat under it cannot disagree.
        self.wind = Wind(data.get("wind"))

        brand = data.get("branding") or {}
        self.brand_rotate_s = max(1.0, float(brand.get("rotate_s") or 5))
        self._brand_club = brand.get("club")
        self._brand_sponsors = [s.get("file") for s in (brand.get("sponsors") or []) if s.get("file")]
        self._brand_cache: Dict[str, Any] = {}

    # -- geometry ----------------------------------------------------------
    def px(self, fraction: float) -> int:
        return int(round(self.height * fraction))

    def _plan_cards(self) -> List[Tuple[str, int, int, str]]:
        """(file, first frame, last frame, 'out' | 'in') for the title and results."""
        cards = self.data.get("cards") or {}
        warp = self.warp
        if warp is None:
            return []
        plan = []
        if cards.get("title") and warp.pre_roll_frames > 0:
            plan.append((cards["title"], 1, warp.pre_roll_frames, "out"))
        if cards.get("results") and warp.post_roll_frames > 0:
            plan.append((cards["results"], warp.race_end_frame, warp.total_frames, "in"))
        return plan

    def _frame_track(self, frame: int) -> Dict[str, Any]:
        return (self.track.get("frames") or {}).get(str(frame)) or {}

    # -- pieces ------------------------------------------------------------
    def _tags(self, img, frame: int) -> None:
        """A plate with the boat's name, on a leader line up from the hull.

        Plates are placed nearest boat first and pushed up out of each other's
        way, because at the start the whole fleet is inside a boat length of
        the line and a fixed offset per boat is not enough to separate them.
        """
        from PIL import Image, ImageDraw

        entries = self._frame_track(frame).get("boats") or []
        if not entries:
            return
        layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(layer)
        stripe = max(2, self.px(0.0035))
        plate_h = self.px(TAG_PLATE_H)
        gap = self.px(TAG_GAP)
        pad = self.px(PAD * 0.7)
        lead = self.px(TAG_LEADER)

        placed: List[Tuple[float, float, float, float]] = []
        drawn: List[Tuple[Any, ...]] = []
        # Nearest first: a close boat keeps the spot it wants, the far ones move.
        for index, sx, sy, depth in sorted(entries, key=lambda e: e[3]):
            name = self._boat_name(index)
            x = sx * self.width
            y = (1.0 - sy) * self.height
            w = int(draw.textlength(name, font=self.f_tag) + stripe + 3 * pad)
            x0 = max(0.0, min(self.width - w, x - w / 2.0))
            y0 = y - lead - plate_h
            for _ in range(len(placed) + 1):
                clash = next((r for r in placed
                              if x0 < r[2] and x0 + w > r[0] and y0 < r[3] and y0 + plate_h > r[1]),
                             None)
                if clash is None:
                    break
                y0 = clash[1] - plate_h - gap
            y0 = max(0.0, y0)
            placed.append((x0, y0, x0 + w, y0 + plate_h))
            drawn.append((name, self.colours.get(name, (255, 255, 255)), x, y, x0, y0, w))

        for name, colour, x, y, x0, y0, w in drawn:
            draw.line([(x, y), (x0 + w / 2.0, y0 + plate_h)],
                      fill=_rgba("panel_ink", 130), width=max(1, self.px(0.0014)))
            draw.ellipse([x - stripe, y - stripe, x + stripe, y + stripe], fill=(*colour, 240))
            draw.rectangle([x0, y0, x0 + w, y0 + plate_h], fill=_rgba("panel", PLATE_ALPHA))
            draw.rectangle([x0, y0, x0 + stripe, y0 + plate_h], fill=(*colour, 255))
            draw.text((x0 + stripe + pad, y0 + plate_h / 2.0), name, font=self.f_tag,
                      fill=_rgba("panel_ink"), anchor="lm")
        img.alpha_composite(layer)

    def _boat_name(self, index: int) -> str:
        boats = self.track.get("boats") or []
        if 0 <= index < len(boats):
            return boats[index]
        return f"Boat {index + 1}"

    def _marks(self, img, frame: int) -> None:
        from PIL import Image, ImageDraw

        entries = self._frame_track(frame).get("marks") or []
        if not entries:
            return
        layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(layer)
        names = self.track.get("marks") or []
        for index, sx, sy, depth in entries:
            label = names[index] if 0 <= index < len(names) else "?"
            x, y = sx * self.width, (1.0 - sy) * self.height
            draw.text((x + 1, y + 1), label, font=self.f_mark, fill=(0, 0, 0, 120), anchor="mm")
            draw.text((x, y), label, font=self.f_mark, fill=_rgba("white", 235), anchor="mm")
        img.alpha_composite(layer)

    def _t_rel(self, frame: int) -> float:
        """Seconds into the race this frame shows. The film's clock is not the
        film's own: it runs at 30x except in the real-time windows."""
        if self.warp is not None:
            return self.warp.time_at(frame)
        return (frame - 1) / self.fps * self.speed

    def _wind(self, frame: int) -> Optional[Tuple[str, str]]:
        """True wind direction and speed at this moment, or None if unrecorded.

        A race with no wind log gets no panel rather than a panel of dashes:
        an instrument showing nothing is worse than no instrument, because it
        reads as a broken instrument.
        """
        twd, tws = self.wind.at(self._t_rel(frame))
        if twd is None and tws is None:
            return None
        return (f"{twd:.0f}°" if twd is not None else "--°",
                f"{tws:.1f} kn" if tws is not None else "-- kn")

    def _clock(self, frame: int) -> Tuple[str, str]:
        t_rel = self._t_rel(frame)
        since = t_rel - self.first_start
        wall = _dt.datetime.fromtimestamp(self.t0_epoch + t_rel)
        m, s = divmod(int(abs(since)), 60)
        state = f"START +{m:02d}:{s:02d}" if since >= 0 else f"START IN {m:02d}:{s:02d}"
        return f"{wall:%H:%M:%S}", state

    def _hud(self, img, frame: int) -> None:
        """The app's instrument panel and course board, as a lower-third."""
        from PIL import Image, ImageDraw

        layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(layer)
        pad = self.px(PAD)
        clock, state = self._clock(frame)

        inst_w = int(max(draw.textlength(clock, font=self.f_time),
                         draw.textlength("START IN 00:00", font=self.f_state)) + 2 * pad)
        inst_h = self.px(HUD_LABEL + HUD_TIME + HUD_STATE) + 4 * pad
        x0 = int(self.width * MARGIN_X)
        y1 = int(self.height * (1.0 - MARGIN_Y))
        y0 = y1 - inst_h

        draw.rectangle([x0, y0, x0 + inst_w, y1], fill=_rgba("panel", PANEL_ALPHA))
        draw.rectangle([x0, y0, x0 + inst_w, y0 + max(1, self.px(0.0015))],
                       fill=_rgba("panel_rule", 235))
        y = y0 + pad
        draw.text((x0 + pad, y), "RACE CLOCK", font=self.f_label, fill=_rgba("panel_label"), anchor="la")
        y += self.px(HUD_LABEL) + pad
        draw.text((x0 + pad, y), clock, font=self.f_time, fill=_rgba("panel_ink"), anchor="la")
        y += self.px(HUD_TIME) + pad
        draw.text((x0 + pad, y), state, font=self.f_state, fill=_rgba("amber"), anchor="la")

        # Wind, in the same instrument case as the clock and to the same
        # rhythm -- label, the figure that matters, then the second line in
        # amber -- so the two read as one panel of instruments rather than as
        # two designs sharing a lower third. Speed on top: it is what says
        # whether this was a drift or a thrash, and the direction is already
        # legible from the boats themselves.
        wind = self._wind(frame)
        wind_w = 0
        if wind is not None:
            twd_text, tws_text = wind
            # Sized for the widest it could ever read, not for what it reads
            # now. 99 degrees to 100, or 9.9 knots to 10.0, is a wider string,
            # and sizing to the moment would have this panel breathe and shove
            # the course board sideways every time the wind ticked over. The
            # clock next to it is only steady because it is monospaced and
            # always eight characters.
            wind_w = int(max(draw.textlength("00.0 kn", font=self.f_time),
                             draw.textlength("360°", font=self.f_state),
                             draw.textlength("TRUE WIND", font=self.f_label)) + 2 * pad)
            wx = x0 + inst_w + self.px(GAP)
            draw.rectangle([wx, y0, wx + wind_w, y1], fill=_rgba("panel", PANEL_ALPHA))
            draw.rectangle([wx, y0, wx + wind_w, y0 + max(1, self.px(0.0015))],
                           fill=_rgba("panel_rule", 235))
            wy = y0 + pad
            draw.text((wx + pad, wy), "TRUE WIND", font=self.f_label,
                      fill=_rgba("panel_label"), anchor="la")
            wy += self.px(HUD_LABEL) + pad
            draw.text((wx + pad, wy), tws_text, font=self.f_time, fill=_rgba("panel_ink"), anchor="la")
            wy += self.px(HUD_TIME) + pad
            draw.text((wx + pad, wy), twd_text, font=self.f_state, fill=_rgba("amber"), anchor="la")
            wind_w += self.px(GAP)

        # Course board: the race name over "COURSE N" and the rounding chips.
        chip_h = self.px(HUD_CHIP_H)
        chips = []
        for mark, rounding in self.tokens:
            w = int(draw.textlength(mark, font=self.f_chip) + 2 * pad)
            chips.append((mark, rounding, w))
        chips_w = sum(w for _, _, w in chips) + self.px(0.006) * max(0, len(chips) - 1)
        label_w = draw.textlength(self.course_label, font=self.f_label)
        board_w = int(max(draw.textlength(self.race_name, font=self.f_name),
                          label_w + self.px(0.01) + chips_w) + 2 * pad)
        board_h = self.px(HUD_NAME) + chip_h + 3 * pad
        bx = x0 + inst_w + self.px(GAP) + wind_w
        by0 = y1 - board_h
        draw.rectangle([bx, by0, bx + board_w, y1], fill=_rgba("paper", PAPER_ALPHA))
        draw.text((bx + pad, by0 + pad), self.race_name, font=self.f_name,
                  fill=_rgba("ink"), anchor="la")
        cy = y1 - pad - chip_h / 2.0
        cx = bx + pad
        draw.text((cx, cy), self.course_label, font=self.f_label, fill=_rgba("ink_2"), anchor="lm")
        cx += label_w + self.px(0.01)
        for mark, rounding, w in chips:
            fill = (_rgba("port") if rounding == "port"
                    else _rgba("starboard") if rounding == "starboard" else _rgba("paper_sunk"))
            ink = _rgba("white") if rounding else _rgba("ink")
            draw.rectangle([cx, cy - chip_h / 2.0, cx + w, cy + chip_h / 2.0], fill=fill)
            draw.text((cx + w / 2.0, cy), mark, font=self.f_chip, fill=ink, anchor="mm")
            cx += w + self.px(0.006)

        if self.credit:
            draw.text((self.width * (1.0 - MARGIN_X), self.height * (1.0 - MARGIN_Y * 0.35)),
                      self.credit, font=self.f_credit, fill=_rgba("panel_ink", 150), anchor="rd")
        img.alpha_composite(layer)

    def _logo(self, rel: str, max_w: int, cap_h: int, alpha: float):
        """A branding image scaled to fit its box, at the club's opacity."""
        from PIL import Image

        key = f"{rel}|{max_w}|{cap_h}|{alpha}"
        if key not in self._brand_cache:
            path = rel if os.path.isabs(rel) else os.path.join(self.base, rel.replace("/", os.sep))
            if not os.path.exists(path):
                self._brand_cache[key] = None
            else:
                with Image.open(path) as raw:
                    logo = raw.convert("RGBA")
                logo.thumbnail((max_w, cap_h), Image.LANCZOS)
                if alpha < 1.0:
                    logo.putalpha(logo.getchannel("A").point(lambda v: int(v * alpha)))
                self._brand_cache[key] = logo
        return self._brand_cache[key]

    def _branding(self, img, frame: int) -> None:
        """Club mark top left, the sponsor whose turn it is top right."""
        if not self._brand_club and not self._brand_sponsors:
            return
        margin = max(12, self.px(BRAND_MARGIN))
        cap_h = max(32, self.px(BRAND_CAP_H))
        if self._brand_club:
            club = self._logo(self._brand_club, int(self.width * BRAND_CLUB_W), cap_h,
                              BRAND_CLUB_ALPHA)
            if club is not None:
                img.alpha_composite(club, (margin, margin))
        if self._brand_sponsors:
            # Whose turn it is, by the film's own clock: the same rotation the
            # club's videos use, so the two match when they play side by side.
            seconds = (frame - 1) / float(self.fps)
            which = int(seconds // self.brand_rotate_s) % len(self._brand_sponsors)
            sponsor = self._logo(self._brand_sponsors[which],
                                 int(self.width * BRAND_SPONSOR_W), cap_h, BRAND_SPONSOR_ALPHA)
            if sponsor is not None:
                img.alpha_composite(sponsor, (self.width - sponsor.width - margin, margin))

    def _card_image(self, filename: str):
        from PIL import Image

        if filename not in self._cards:
            path = filename if os.path.isabs(filename) else os.path.join(self.base, filename)
            if not os.path.exists(path):
                self._cards[filename] = None
            else:
                with Image.open(path) as raw:
                    card = raw.convert("RGBA")
                if card.size != (self.width, self.height):
                    card = card.resize((self.width, self.height), Image.LANCZOS)
                self._cards[filename] = card
        return self._cards[filename]

    def _cards_for(self, img, frame: int, fade_s: float = 1.2) -> None:
        from PIL import Image

        fade = max(1, int(round(fade_s * self.fps)))
        for filename, f0, f1, direction in self._card_plan:
            if not (f0 <= frame <= f1):
                continue
            card = self._card_image(filename)
            if card is None:
                continue
            if direction == "out":
                alpha = 1.0 if frame <= f1 - fade else max(0.0, (f1 - frame) / float(fade))
            else:
                alpha = 1.0 if frame >= f0 + fade else max(0.0, (frame - f0) / float(fade))
            if alpha <= 0.001:
                continue
            if alpha >= 0.999:
                img.alpha_composite(card)
            else:
                faded = card.copy()
                faded.putalpha(card.getchannel("A").point(lambda v: int(v * alpha)))
                img.alpha_composite(faded)

    # -- entry points ------------------------------------------------------
    def draw_scene(self, img, frame: int) -> None:
        """Anything anchored to the world or the frame, under the video panel."""
        self._marks(img, frame)
        self._tags(img, frame)
        self._hud(img, frame)
        self._branding(img, frame)

    def draw_cards(self, img, frame: int) -> None:
        """Full-frame cards, which cover everything including the video panel."""
        self._cards_for(img, frame)


def _course_tokens(course_text: str) -> List[Tuple[str, str]]:
    """Split "7s 6s Op 1p" into (mark, rounding) pairs, as the app's board does."""
    out: List[Tuple[str, str]] = []
    for token in (course_text or "").split():
        rounding = "starboard" if token[-1:] == "s" else "port" if token[-1:] == "p" else ""
        out.append((token[:-1].upper() if rounding else token.upper(), rounding))
    return out


def load_track(path: Optional[str]) -> Optional[Dict[str, Any]]:
    if not path or not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)
