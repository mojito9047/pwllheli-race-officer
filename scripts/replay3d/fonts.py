#!/usr/bin/env python3
"""The app's typefaces, converted to something Blender and PIL can read.

The race-document theme ships its faces as woff2, which neither Blender's text
objects nor PIL will open, so they are unpacked to TTF next to whatever is
being rendered.

This is on the render side because the renderer is what needs them. That was
found the hard way: the first films off the render machine came out with the
clock, the course board and the leaderboard all set in PIL's default bitmap
face, because the conversion lived in the exporter and the renderer never ran
it. The faces are SIL Open Font Licence (static/fonts/OFL.txt).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))


FONT_SOURCES = {
    "Archivo.ttf": "archivo-latin.woff2",
    "ArchivoNarrow.ttf": "archivo-narrow-latin.woff2",
    "PlexMono500.ttf": "plex-mono-500-latin.woff2",
    # The overlay sets the race clock in the 600 weight. It was missing from
    # this list and only worked because an old copy happened to be lying in
    # the runtime folder; a fresh render machine got PIL's default face.
    "PlexMono600.ttf": "plex-mono-600-latin.woff2",
}


def ensure_fonts(out_dir: str) -> Dict[str, Any]:
    """Convert the app's woff2 typefaces to TTF beside the exports, for Blender's text objects.

    Blender reads TTF/OTF, not woff2, so the race-document theme's fonts are
    unpacked once into <out_dir>/fonts/. Needs fontTools with brotli; without
    them the builder falls back to Blender's built-in face and says so.
    The faces are SIL Open Font Licence (static/fonts/OFL.txt).
    """
    fonts_dir = Path(out_dir) / "fonts"
    src_dir = Path(_ROOT) / "static" / "fonts"
    done: Dict[str, Any] = {"dir": str(fonts_dir), "source_dir": str(src_dir),
                            "written": [], "present": [], "missing": [], "skipped": None}
    try:
        from fontTools.ttLib import TTFont
    except ImportError:
        done["skipped"] = "fontTools not installed (pip install fonttools brotli)"
        return done
    fonts_dir.mkdir(parents=True, exist_ok=True)
    for dst, src in FONT_SOURCES.items():
        target = fonts_dir / dst
        source = src_dir / src
        if target.exists() and target.stat().st_size > 0:
            done["present"].append(dst)
            continue
        # Every way this can fail is recorded rather than skipped. It used to
        # `continue` on a missing source, which produced an empty fonts folder,
        # no message of any kind, and a film set in PIL's default bitmap face.
        # Nobody finds that until they watch the film.
        if not source.exists():
            done["missing"].append(f"{dst} (no {src})")
            continue
        try:
            font = TTFont(str(source))
            font.flavor = None
            font.save(str(target))
        except Exception as exc:
            # woff2 needs brotli to decompress; without it fontTools imports
            # cleanly and then fails here, one font at a time.
            done["missing"].append(f"{dst} ({type(exc).__name__}: {exc})")
            continue
        done["written"].append(dst)
    return done


def font_trouble(done: Dict[str, Any]) -> Optional[str]:
    """One line saying what is wrong with a set of fonts, or None if it is fine.

    Worth saying out loud wherever fonts are unpacked: nothing downstream
    fails, PIL quietly falls back to an 11px bitmap face, and the only symptom
    is a finished film with the wrong typeface in it.
    """
    if done.get("skipped"):
        return f"{done['skipped']} - the film will be set in the wrong face"
    if done.get("missing"):
        return (f"{len(done['missing'])} typeface(s) not converted from {done.get('source_dir')}: "
                f"{', '.join(done['missing'])} - the film will be set in the wrong face")
    if not (done.get("written") or done.get("present")):
        return f"no typefaces found in {done.get('source_dir')} - the film will be set in the wrong face"
    return None
