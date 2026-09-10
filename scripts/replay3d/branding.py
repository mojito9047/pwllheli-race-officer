#!/usr/bin/env python3
"""The club's logos for the film, from the club's own public manifest.

The film is branded the way the start and finish videos are: the club mark top
left, one sponsor at a time top right. The images come from
``/api/branding/live`` -- the same endpoint the live-stream relay reads -- so
changing a sponsor in Settings changes every published thing at once, with
nothing to copy to the render machine.

It deliberately does **not** read a hut backup. Those are encrypted, and
nothing about making a film should require the backup key to travel to another
machine.

Render-side, and free of app imports, because the machine that needs the logos
is the one doing the drawing. The exporter uses it too.
"""
from __future__ import annotations

import json
import os
import shutil
import urllib.parse
from pathlib import Path
from typing import Any, Dict, Optional

BRANDING_ROTATE_S = 5
# The same manifest the live-stream relay reads (deploy/live_stream/relay_branded_source.py).
BRANDING_MANIFEST_URL = os.environ.get(
    "MANIFEST_URL", "https://pro.pwllhelisailingclub.org/api/branding/live")


def branding_take(src: Path, dest: Path) -> Optional[str]:
    """Copy one branding image beside the export; returns its relative path."""
    if not src.exists() or not src.is_file():
        return None
    target = dest / src.name
    if not target.exists() or target.stat().st_mtime < src.stat().st_mtime:
        shutil.copy2(src, target)
    return f"branding/{target.name}"


def branding_from_api(dest, url: str, timeout: float = 10.0) -> Optional[Dict[str, Any]]:
    """The club's current logos, from the hut's public branding manifest.

    This is the same endpoint the live-stream relay uses, so the film is
    branded from one source of truth rather than from whatever images happen
    to be in the backup. Read-only and public. Returns None if the hut cannot
    be reached, which is the normal case when rendering away from the club.
    """
    import urllib.request

    dest = Path(dest)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "pwllheli-replay3d/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            manifest = json.loads(resp.read().decode("utf-8"))
    except Exception as ex:
        print(f"  branding: hut api unreachable ({type(ex).__name__}); using the data directory")
        return None
    if not manifest.get("enabled"):
        print("  branding: the club has it switched off")
        return {"rotate_s": BRANDING_ROTATE_S, "club": None, "sponsors": []}

    dest.mkdir(parents=True, exist_ok=True)
    out: Dict[str, Any] = {
        "rotate_s": float(manifest.get("rotation_seconds") or BRANDING_ROTATE_S) or BRANDING_ROTATE_S,
        "club": None, "sponsors": [], "source": url,
    }

    base = urllib.parse.urlparse(url)

    def grab(image_url: str) -> Optional[str]:
        parts = urllib.parse.urlparse(image_url)
        name = os.path.basename(parts.path)
        if not name:
            return None
        # The manifest builds absolute URLs from whatever host the app saw, so
        # they come back pointing at the origin over plain http. Serve them
        # from the host we were given instead: same files, encrypted, and the
        # one we already know answers.
        image_url = urllib.parse.urlunparse(parts._replace(scheme=base.scheme, netloc=base.netloc))
        target = dest / name
        if not target.exists() or target.stat().st_size == 0:
            try:
                req = urllib.request.Request(image_url, headers={"User-Agent": "pwllheli-replay3d/1.0"})
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    data = resp.read()
                target.write_bytes(data)
            except Exception as ex:
                print(f"  branding: could not fetch {image_url} ({type(ex).__name__})")
                return None
        return f"branding/{name}"

    club_url = str(manifest.get("club_logo_url") or "")
    if club_url:
        out["club"] = grab(club_url)
    for item in manifest.get("sponsors") or []:
        rel = grab(str((item or {}).get("url") or ""))
        if rel:
            out["sponsors"].append({"label": str(item.get("label") or ""), "file": rel})
    if not out["club"] and not out["sponsors"]:
        return None
    print(f"  branding: {len(out['sponsors'])} sponsor(s) from the hut api")
    return out
