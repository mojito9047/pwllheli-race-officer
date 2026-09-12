"""The film's title and results cards, drawn as images.

Blender can set type in 3D, but a results table wants baselines, columns and
tracking, which is fiddly with text objects and trivial with a drawing library.
These are rendered at the film's own resolution and shown full-frame at each
end of the replay, so they carry the app's typefaces and colours exactly:
Archivo for names, Archivo Narrow in caps for labels, Plex Mono for every time.
"""
from __future__ import annotations

import datetime as _dt
import os
from typing import Any, Dict, List, Optional, Sequence, Tuple

# The race-document theme, from static/theme_race_document.css.
PAPER = (247, 244, 236)
PAPER_2 = (255, 253, 247)
PAPER_SUNK = (239, 234, 222)
INK = (28, 26, 21)
INK_2 = (74, 70, 60)
INK_FAINT = (125, 119, 106)
RULE = (207, 199, 180)
RULE_STRONG = (168, 159, 137)
PANEL = (16, 18, 15)
PANEL_2 = (26, 29, 25)
PANEL_RULE = (51, 56, 49)
PANEL_INK = (232, 230, 221)
PANEL_LABEL = (141, 139, 128)
AMBER = (255, 176, 0)
FLAG_RED = (200, 16, 46)


def _font(fonts_dir: str, name: str, size: int):
    from PIL import ImageFont
    path = os.path.join(fonts_dir, name)
    if os.path.exists(path):
        return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def _tracked(draw, xy: Tuple[float, float], text: str, font, fill, tracking: float = 0.0,
             anchor_right: bool = False) -> float:
    """Draw text with letter spacing, which PIL has no setting for. Returns the width."""
    if tracking <= 0:
        draw.text(xy, text, font=font, fill=fill, anchor="rs" if anchor_right else "ls")
        return draw.textlength(text, font=font)
    width = sum(draw.textlength(ch, font=font) + tracking for ch in text) - tracking
    x = xy[0] - width if anchor_right else xy[0]
    for ch in text:
        draw.text((x, xy[1]), ch, font=font, fill=fill, anchor="ls")
        x += draw.textlength(ch, font=font) + tracking
    return width


def _logo(branding: Sequence[str], height: int):
    """The club's mark, from the first of the given paths that exists."""
    from PIL import Image
    for path in branding:
        if path and os.path.exists(path):
            img = Image.open(path).convert("RGBA")
            scale = height / img.height
            return img.resize((max(1, round(img.width * scale)), height), Image.LANCZOS)
    return None


def _race_date(data: Dict[str, Any]) -> str:
    iso = (data.get("race") or {}).get("first_start_iso") or ""
    try:
        when = _dt.datetime.fromisoformat(iso)
    except ValueError:
        return ""
    return when.strftime("%A %d %B %Y").replace(" 0", " ")


def title_card(data: Dict[str, Any], out_path: str, fonts_dir: str, branding: Sequence[str],
               size: Tuple[int, int] = (1920, 1080)) -> str:
    """The opening card: the club's mark, the race, the day, on the instrument ground."""
    from PIL import Image, ImageDraw

    w, h = size
    img = Image.new("RGB", size, PANEL)
    draw = ImageDraw.Draw(img)
    race = data.get("race") or {}

    logo = _logo(branding, height=round(h * 0.20))
    y = round(h * 0.20)
    if logo is not None:
        img.paste(logo, (round((w - logo.width) / 2), y), logo)
        y += logo.height + round(h * 0.06)
    else:
        y += round(h * 0.10)

    _tracked(draw, (w / 2 - _tracked(ImageDraw.Draw(Image.new("RGB", (1, 1))), (0, 0), "PWLLHELI SAILING CLUB",
                                     _font(fonts_dir, "ArchivoNarrow.ttf", round(h * 0.026)), PANEL_LABEL, 5.0) / 2, y),
             "PWLLHELI SAILING CLUB", _font(fonts_dir, "ArchivoNarrow.ttf", round(h * 0.026)), PANEL_LABEL, 5.0)
    y += round(h * 0.075)

    name_font = _font(fonts_dir, "Archivo.ttf", round(h * 0.085))
    draw.text((w / 2, y), str(race.get("name") or "Race"), font=name_font, fill=PANEL_INK, anchor="ma")
    y += round(h * 0.115)

    draw.line([(w * 0.42, y), (w * 0.58, y)], fill=AMBER, width=3)
    y += round(h * 0.045)

    course = str(race.get("course_text") or "")
    line = f"COURSE {race.get('course_no')}   {course}".strip() if race.get("course_no") is not None else course
    sub_font = _font(fonts_dir, "ArchivoNarrow.ttf", round(h * 0.030))
    for text in (_race_date(data).upper(), line.upper()):
        if not text:
            continue
        probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
        width = _tracked(probe, (0, 0), text, sub_font, PANEL_LABEL, 4.0)
        _tracked(draw, (w / 2 - width / 2, y), text, sub_font, PANEL_LABEL, 4.0)
        y += round(h * 0.048)

    wind = data.get("wind") or {}
    if wind.get("twd_deg") is not None:
        mono = _font(fonts_dir, "PlexMono500.ttf", round(h * 0.026))
        gust = f"{wind['twd_deg']:.0f}°  {wind.get('tws_kn') or 0:.1f} kn"
        draw.text((w / 2, h * 0.86), gust, font=mono, fill=PANEL_LABEL, anchor="ma")
    img.save(out_path)
    return out_path


def _gap_text(seconds: float) -> str:
    """A corrected-time gap to the winner, the way a timing tower shows it."""
    if seconds <= 0:
        return "+0:00"
    total = int(round(seconds))
    h, rest = divmod(total, 3600)
    m, s = divmod(rest, 60)
    return f"+{h}:{m:02d}:{s:02d}" if h else f"+{m}:{s:02d}"


def _gradient(size: Tuple[int, int], top: Tuple[int, int, int], bottom: Tuple[int, int, int]):
    """A vertical wash, so the card has depth instead of a flat fill."""
    from PIL import Image
    w, h = size
    column = Image.new("RGB", (1, h))
    for y in range(h):
        f = y / max(1, h - 1)
        column.putpixel((0, y), tuple(round(top[i] + (bottom[i] - top[i]) * f) for i in range(3)))
    return column.resize(size, Image.BILINEAR)


# The smallest the results card will shrink itself. Below this the fonts, which
# shrink more slowly than the bands they sit in, start to overlap the headers.
MIN_CARD_SCALE = 0.42
# However tight it gets, show at least a podium.
MIN_CARD_ROWS = 3
# A table header is per-table overhead, not per-boat, so shrinking it with the
# rows is a false economy: on a twenty-boat card it bought two more rows and
# cost the ratings their names, "IRC OVERALL" ending up at 12px beside 20px
# boats. It gets a floor.
MIN_HEAD_FRACTION = 0.040


def results_card(data: Dict[str, Any], out_path: str, fonts_dir: str, branding: Sequence[str],
                 size: Tuple[int, int] = (1920, 1080)) -> Optional[str]:
    """The closing card: a broadcast leaderboard, not a results sheet.

    The app's own tables are deliberately plain because they are documents. This
    is the end of a film, so it is dark, large and staged: the winner set apart,
    each boat carrying the hull colour it wore round the course, and the
    corrected time given the weight it earned.
    """
    from PIL import Image, ImageDraw

    tables = [t for t in (data.get("results") or []) if t.get("rows")]
    if not tables:
        return None
    w, h = size
    img = _gradient(size, (22, 26, 32), (8, 9, 11))
    draw = ImageDraw.Draw(img)
    race = data.get("race") or {}
    margin = round(w * 0.06)

    # Header: mark, race, day.
    logo = _logo(branding, height=round(h * 0.085))
    if logo is not None:
        img.paste(logo, (margin, round(h * 0.048)), logo)
    head_x = margin + (logo.width + round(w * 0.022) if logo is not None else 0)
    draw.text((head_x, round(h * 0.050)), str(race.get("name") or "Race"),
              font=_font(fonts_dir, "Archivo.ttf", round(h * 0.058)), fill=(255, 255, 255), anchor="la")
    _tracked(draw, (head_x + 3, round(h * 0.142)), _race_date(data).upper(),
             _font(fonts_dir, "ArchivoNarrow.ttf", round(h * 0.024)), PANEL_LABEL, 5.0)

    # Every table the scorer produced, and a fleet that will not fit is cut
    # short rather than a whole rating being dropped.
    #
    # It used to drop tables, and the threshold was six boats: a club scoring
    # IRC and YTC together lost the YTC winner from the film entirely, with
    # nothing on the card to say so. Below about sixteen boats it then began to
    # overlap itself, because the row heights shrank with the fit while the
    # fonts shrank on a gentler curve, so the text outgrew the bands holding it
    # -- the headers first, being the shallowest.
    #
    # So the scale has a floor. Above it everything fits and everything shows;
    # at it, the fleets are trimmed to what the card can hold and each says how
    # many more there were. A closing card is the highlight, not the results
    # sheet, and the full order is a click away on the race page.
    shown = tables[:4]
    y = round(h * 0.225)
    budget = h - y - round(h * 0.075)
    head_h, note_h, gap_between = h * 0.062, h * 0.040, h * 0.025
    row_nominal = h * 0.082

    def head_at(sc: float) -> float:
        return max(h * MIN_HEAD_FRACTION, head_h * sc)

    def fixed_at(n_tables: int, sc: float) -> float:
        return n_tables * (head_at(sc) + (note_h + gap_between) * sc)

    nominal = fixed_at(len(shown), 1.0) + row_nominal * sum(len(t["rows"]) for t in shown)
    scale = min(1.0, budget / nominal) if nominal else 1.0
    if scale < MIN_CARD_SCALE:
        scale = MIN_CARD_SCALE
        room = budget - fixed_at(len(shown), scale)
        per_table = int(room / (row_nominal * scale) / max(1, len(shown)))
        keep = max(MIN_CARD_ROWS, per_table)
        # The count travels in the copy, not in a lookup keyed on the original
        # table: replacing the dict and then asking for the old one's id finds
        # nothing, silently, and the card loses the line that says it was cut.
        shown = [{**t, "rows": t["rows"][:keep], "_cut": max(0, len(t["rows"]) - keep)}
                 for t in shown]

    row_h = round(h * 0.082 * scale)
    head_h = round(head_at(scale))
    note_h, gap_between = round(note_h * scale), round(gap_between * scale)
    gap_h = round(h * 0.012 * min(1.0, scale * 1.4))

    # Type shrinks more slowly than the bands holding it -- deliberately, so a
    # tight card stays readable -- which means below a point it outgrows them.
    # That was the overlap: at twenty boats the headers ended up printed through
    # the first row. Capping each font against the band it sits in is the fix,
    # and it is what lets the card go on shrinking to fit a real fleet instead
    # of stopping early with four names on it.
    row_cap = max(9, int((row_h - gap_h) * 0.72))
    head_cap = max(8, int(head_h * 0.46))

    def size(fraction: float, cap: int) -> int:
        return max(9, min(cap, round(h * fraction * (0.55 + 0.45 * scale))))

    label_font = _font(fonts_dir, "ArchivoNarrow.ttf", size(0.021, head_cap))
    head_font = _font(fonts_dir, "ArchivoNarrow.ttf", size(0.030, head_cap))
    name_font = _font(fonts_dir, "Archivo.ttf", size(0.042, row_cap))
    win_font = _font(fonts_dir, "Archivo.ttf", size(0.050, row_cap))
    pos_font = _font(fonts_dir, "Archivo.ttf", size(0.046, row_cap))
    win_pos_font = _font(fonts_dir, "Archivo.ttf", size(0.056, row_cap))
    mono_font = _font(fonts_dir, "PlexMono500.ttf", size(0.040, row_cap))
    win_mono = _font(fonts_dir, "PlexMono500.ttf", size(0.048, row_cap))
    small_mono = _font(fonts_dir, "PlexMono500.ttf", size(0.024, row_cap))
    col_boat = margin + round(w * 0.075)
    col_sail = margin + round(w * 0.400)
    col_elapsed = w - margin - round(w * 0.175)
    col_corrected = w - margin

    for table in shown:
        title = str(table.get("title") or "RESULTS").upper().replace(" RESULTS", "")
        draw.rectangle([margin, y - round(h * 0.004), margin + round(w * 0.006), y + round(h * 0.030)], fill=AMBER)
        _tracked(draw, (margin + round(w * 0.016), y + round(h * 0.026)), title, head_font, (255, 255, 255), 4.0)
        rating = str(table.get("rating_label") or "")
        _tracked(draw, (col_corrected, y + round(h * 0.024)), f"CORRECTED {rating}".strip(),
                 label_font, PANEL_LABEL, 3.0, anchor_right=True)
        _tracked(draw, (col_elapsed, y + round(h * 0.024)), "ELAPSED", label_font, PANEL_LABEL, 3.0,
                 anchor_right=True)
        y += head_h

        # A timing tower: each boat on its own slab, the place in a box, the hull
        # colour as the stripe, and everyone behind the winner shown as a gap.
        leader_s = next((r.get("corrected_s") for r in table["rows"] if r.get("pos") == 1), None)
        box_w = round(w * 0.042)
        for row in table["rows"]:
            first = row.get("pos") == 1
            top, bottom = y, y + row_h - gap_h
            base = y + round((row_h - gap_h) * 0.70)
            draw.rectangle([margin, top, w - margin, bottom], fill=(38, 44, 54) if first else (24, 27, 33))
            draw.rectangle([margin, top, margin + box_w, bottom], fill=AMBER if first else (48, 54, 64))
            draw.text((margin + box_w / 2, base), str(row.get("pos") or ""),
                      font=win_pos_font if first else pos_font,
                      fill=(16, 18, 15) if first else (235, 235, 235), anchor="ms")
            colour = tuple(row.get("colour") or (120, 120, 120))
            draw.rectangle([margin + box_w, top, margin + box_w + round(w * 0.006), bottom], fill=colour)

            draw.text((col_boat, base), str(row.get("boat") or "").upper(),
                      font=win_font if first else name_font, fill=(255, 255, 255), anchor="ls")
            if row.get("sail_no"):
                _tracked(draw, (col_sail, base), str(row["sail_no"]).upper(), label_font, PANEL_LABEL, 2.5)
            if row.get("elapsed"):
                draw.text((col_elapsed, base), str(row["elapsed"]), font=small_mono, fill=(140, 140, 140),
                          anchor="rs")
            if first or leader_s is None or row.get("corrected_s") is None:
                draw.text((col_corrected - round(w * 0.012), base), str(row.get("corrected") or ""),
                          font=win_mono if first else mono_font,
                          fill=AMBER if first else (255, 255, 255), anchor="rs")
            else:
                draw.text((col_corrected - round(w * 0.012), base),
                          _gap_text(float(row["corrected_s"]) - float(leader_s)),
                          font=mono_font, fill=(235, 235, 235), anchor="rs")
            y += row_h
        extra = int(table.get("_cut") or 0)
        # One line. Drawing the two separately put them on top of each other,
        # because the note is positioned from y and nothing had moved y on.
        parts = ([f"AND {extra} MORE"] if extra else []) +                 ([str(table["note"]).upper()] if table.get("note") else [])
        if parts:
            _tracked(draw, (col_boat, y + round(note_h * 0.55)), "   ".join(parts),
                     label_font, PANEL_LABEL if extra else (110, 110, 110), 2.5)
            y += note_h
        y += gap_between

    foot = _font(fonts_dir, "ArchivoNarrow.ttf", round(h * 0.019))
    credit = ((data.get("terrain") or {}).get("imagery_info") or {}).get("credit") or ""
    _tracked(draw, (margin, h - round(h * 0.042)), "PWLLHELI SAILING CLUB", foot, (110, 110, 110), 4.0)
    if credit:
        _tracked(draw, (w - margin, h - round(h * 0.042)), credit.upper(), foot, (110, 110, 110), 3.0,
                 anchor_right=True)
    img.save(out_path)
    return out_path


def build_cards(data: Dict[str, Any], out_dir: str, fonts_dir: str, branding: Sequence[str],
                race_id: int, size: Tuple[int, int] = (1920, 1080)) -> Dict[str, Optional[str]]:
    """Both cards, named after the race, returned as paths relative to ``out_dir``."""
    os.makedirs(out_dir, exist_ok=True)
    title = title_card(data, os.path.join(out_dir, f"race_{race_id}_title.png"), fonts_dir, branding, size)
    results = results_card(data, os.path.join(out_dir, f"race_{race_id}_results.png"), fonts_dir, branding, size)
    return {"title": os.path.basename(title) if title else None,
            "results": os.path.basename(results) if results else None}
