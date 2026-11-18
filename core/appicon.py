# Copyright © 2026 CapeNet Ltd. MIT licence; see LICENSE.
"""The home-screen icon for the public pages, drawn from the club's own logo.

A phone that adds the competitor pages to its home screen wants a square icon at
a few fixed sizes. The club logo is neither square nor those sizes, and it is
whatever the club uploaded under Settings -> Club name and branding, so the icon
is drawn from it on request rather than shipped: another club's installation
gets its own logo on the home screen without anybody making image files.

The logo sits inside the middle of a paper-coloured square. Android may cut a
"maskable" icon to a circle covering the central 80%, so the logo keeps inside
that and the same picture serves as both the plain and the maskable icon.

Pillow is an optional dependency of this app (see requirements.txt). Without it,
or with a logo it cannot read, the bundled favicon is served instead: the wrong
size, which a phone scales, rather than no icon at all.
"""
from __future__ import annotations

import io
import threading
from pathlib import Path
from typing import Dict, Optional, Tuple

from core import appstate

# 180 is what an iPhone asks for (apple-touch-icon); 192 and 512 are the two a
# web app manifest is expected to carry.
ICON_SIZES = (180, 192, 512)
PAPER = (247, 244, 236, 255)    # --paper in static/theme_race_document.css
LOGO_SHARE = 0.72               # of the square's side; inside the maskable safe zone
FALLBACK_ICON_PATH = appstate.BASE_DIR / "static" / "img" / "favicon.png"

_CACHE: Dict[Tuple[str, int, int, int], bytes] = {}
_CACHE_LOCK = threading.Lock()
_CACHE_MAX = 12                 # three sizes of a handful of logos, then start again


def _fallback() -> bytes:
    try:
        return FALLBACK_ICON_PATH.read_bytes()
    except OSError:
        return b""


def _draw(logo_path: Path, size: int) -> bytes:
    from PIL import Image    # optional dependency: imported here, not at module load

    with Image.open(logo_path) as source:
        logo = source.convert("RGBA")
    box = max(1, int(round(size * LOGO_SHARE)))
    scale = min(box / logo.width, box / logo.height)
    width = max(1, int(round(logo.width * scale)))
    height = max(1, int(round(logo.height * scale)))
    logo = logo.resize((width, height), Image.LANCZOS)
    icon = Image.new("RGBA", (size, size), PAPER)
    icon.alpha_composite(logo, ((size - width) // 2, (size - height) // 2))
    out = io.BytesIO()
    # No transparency left, and an iPhone paints transparent corners black.
    icon.convert("RGB").save(out, format="PNG", optimize=True)
    return out.getvalue()


def render_app_icon(logo_path: Optional[Path], size: int) -> bytes:
    """PNG bytes of the square icon at ``size`` pixels, from the club logo.

    Kept in memory per (logo file as it is now, size): a phone asks for these
    whenever it refreshes the installed app, and the picture only changes when
    the club uploads a different logo.
    """
    if size not in ICON_SIZES:
        raise ValueError(f"unsupported icon size {size}")
    path = Path(logo_path) if logo_path else appstate.DEFAULT_CLUB_LOGO_PATH
    try:
        stat = path.stat()
    except OSError:
        return _fallback()
    key = (str(path), stat.st_mtime_ns, stat.st_size, size)
    with _CACHE_LOCK:
        held = _CACHE.get(key)
    if held is not None:
        return held
    try:
        data = _draw(path, size)
    except Exception:
        # Pillow missing, or an upload it cannot read. Not cached, so a logo
        # replaced with a good one is picked up without a restart.
        return _fallback()
    with _CACHE_LOCK:
        if len(_CACHE) >= _CACHE_MAX:
            _CACHE.clear()
        _CACHE[key] = data
    return data
