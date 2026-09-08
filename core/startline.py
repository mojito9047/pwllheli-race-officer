"""Optional ODM start-line overlay for public start/finish video copies.

Draws the actual start line (foreground pole base -> orange ODM buoy) onto the
PUBLIC web copy of a start/finish clip. The full-quality raw evidence clip is
never touched.

Detection runs ONCE per clip on a single grabbed frame (never per-frame). The
line is then burned in as part of the SAME ffmpeg transcode that already makes
the branded public copy — there is no second encode.

  - Start clips: RED before the start signal, GREEN after (the start moment
    inside the clip is known from the segment timestamps).
  - Finish clips: GREEN throughout.

Best-effort: if Pillow/numpy are missing, no frame can be grabbed, or the buoy
isn't confidently found, the public copy is produced without the line and the
reason is logged. Detection reuses core/odm_detector; tuning lives in
data/startline_config.json (falls back to the built-in defaults).
"""
from __future__ import annotations

import os
import subprocess
import sys
from typing import Optional, Tuple

from core import appstate

try:
    from PIL import Image
    from core import odm_detector as odm
    _AVAILABLE = True
except Exception:  # pragma: no cover - environment dependent
    _AVAILABLE = False

RED = (235, 0, 0)
GREEN = (0, 235, 0)


def startline_overlay_available() -> bool:
    return _AVAILABLE


def startline_overlay_enabled(cfg) -> bool:
    """True when the feature is switched on AND its dependencies are present."""
    return _AVAILABLE and bool(cfg.get("video_startline_overlay_enabled"))


def load_startline_config():
    """Detection/appearance config: data/startline_config.json over the defaults."""
    return odm.load_config(str(appstate.DATA_DIR / "startline_config.json"))


def _grab_frame(ffmpeg: str, src: str, at_seconds: float, dest: str) -> bool:
    try:
        subprocess.run(
            [ffmpeg, "-nostdin", "-y", "-hide_banner", "-loglevel", "error",
             "-ss", f"{max(0.0, at_seconds):.2f}", "-i", src, "-frames:v", "1", dest],
            check=True, timeout=30,
        )
        return os.path.exists(dest) and os.path.getsize(dest) > 0
    except Exception:
        return False


def build_startline_spec(ffmpeg: str, evidence_path: str, clip_type: str,
                         event_offset: float, out_dir: str) -> Tuple[Optional[dict], str]:
    """Detect the buoy on one frame and render the line PNG(s) at the evidence
    resolution, ready to overlay in the transcode.

    Returns ``(spec, reason)``. ``spec`` is None when no line should be drawn
    (with ``reason`` explaining why); otherwise it has keys ``green``, ``red``
    (None for finish clips), ``offset`` and ``is_start``. The caller must call
    ``cleanup_spec(spec)`` once the transcode has finished.
    """
    if not _AVAILABLE:
        return None, "skipped: Pillow/numpy not installed"
    frame = os.path.join(out_dir, f".odm_frame_{os.getpid()}.jpg")
    try:
        cfg = load_startline_config()
        at = min(1.0, max(0.5, float(event_offset or 0)))
        if not _grab_frame(ffmpeg, evidence_path, at, frame):
            return None, "skipped: could not grab a frame from the clip"
        img = Image.open(frame)
        w, h = img.size
        det = odm.detect(img, cfg)
        if not det.found:
            return None, (f"skipped: no confident buoy (best conf {det.conf:.2f}"
                          f"{'' if det.fx is None else f' near {det.fx:.2f},{det.fy:.2f}'})")
        is_start = str(clip_type) == "start"
        green = os.path.join(out_dir, f".odm_green_{os.getpid()}.png")
        odm.render_line_overlay(cfg, w, h, green, det.fx, det.fy, color=GREEN)
        spec = {"green": green, "red": None, "offset": float(event_offset or 0), "is_start": is_start}
        if is_start:
            red = os.path.join(out_dir, f".odm_red_{os.getpid()}.png")
            odm.render_line_overlay(cfg, w, h, red, det.fx, det.fy, color=RED)
            spec["red"] = red
            reason = (f"drawn at ({det.fx:.3f},{det.fy:.3f}) conf {det.conf:.2f}, "
                      f"red->green at {spec['offset']:.0f}s")
        else:
            reason = f"drawn green at ({det.fx:.3f},{det.fy:.3f}) conf {det.conf:.2f}"
        return spec, reason
    except Exception as exc:
        return None, f"skipped: error {exc}"
    finally:
        try:
            if os.path.exists(frame):
                os.remove(frame)
        except Exception:
            pass


def cleanup_spec(spec: Optional[dict]) -> None:
    if not spec:
        return
    for key in ("green", "red"):
        path = spec.get(key)
        if path and os.path.exists(path):
            try:
                os.remove(path)
            except Exception:
                pass


def line_filter_chain(spec: dict, logo_count: int) -> Tuple[list, str]:
    """Return (extra ffmpeg -i args, filtergraph producing [lined] from [0:v]).

    Inputs are appended AFTER the base video (0) and any branding logos
    (1..logo_count): green is logo_count+1, red (start only) is logo_count+2.
    The chain overlays the line PNG(s) on the full-resolution [0:v] so it scales
    with the video in the same encode.
    """
    green_idx = logo_count + 1
    inputs = ["-loop", "1", "-i", spec["green"]]
    # shortest=1 stops the overlay when the (finite) video ends; without it the
    # looped -loop 1 PNG input is infinite and the encode never terminates.
    ov = "overlay=0:0:format=auto:shortest=1"
    if spec.get("is_start") and spec.get("red"):
        red_idx = logo_count + 2
        inputs += ["-loop", "1", "-i", spec["red"]]
        off = f"{max(0.0, float(spec['offset'])):.2f}"
        chain = (f"[0:v][{red_idx}:v]{ov}:enable='lt(t\\,{off})'[la];"
                 f"[la][{green_idx}:v]{ov}:enable='gte(t\\,{off})'[lined]")
    else:
        chain = f"[0:v][{green_idx}:v]{ov}[lined]"
    return inputs, chain
