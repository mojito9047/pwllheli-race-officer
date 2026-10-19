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
import secrets
import subprocess
import sys
from typing import Iterable, Optional, Tuple

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
    # Named for this clip alone. They were named for the app's process, so every
    # clip it built shared one green PNG: a finish 21 s behind another rewrote it
    # while the first clip's encode was still reading it, and that encode stopped
    # there -- race 96's start copy came out 5 s long and two finishes 10 s and
    # 96 s, all "ready". Two clips could also swap detections through the frame.
    token = secrets.token_hex(6)
    frame = os.path.join(out_dir, f".odm_frame_{token}.jpg")
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
        green = os.path.join(out_dir, f".odm_green_{token}.png")
        odm.render_line_overlay(cfg, w, h, green, det.fx, det.fy, color=GREEN)
        spec = {"green": green, "red": None, "offset": float(event_offset or 0), "is_start": is_start}
        if is_start:
            red = os.path.join(out_dir, f".odm_red_{token}.png")
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


Buoy = Tuple[float, float, float]          # (fx, fy, confidence)

# Two detections this close (fractions of the frame) are the same buoy. Across
# twelve starts of the 2026 autumn series the buoy's own detections in the
# minute before the gun never spread by more than 0.007; a boat taken for it
# wanders -- race 98's, three frames before the gun, spread by 0.025 -- and at
# 0.02 two of them counted as agreeing and outvoted the buoy in the photo.
SAME_BUOY = 0.01


def detect_buoy(image_path: str) -> Optional[Buoy]:
    """A confident buoy in one image, or None."""
    if not _AVAILABLE:
        return None
    try:
        det = odm.detect(Image.open(image_path), load_startline_config())
    except Exception:
        return None
    return (float(det.fx), float(det.fy), float(det.conf)) if det.found else None


def buoy_consensus(detections: Iterable[Optional[Buoy]]) -> Optional[Tuple[float, float, int]]:
    """Where the buoy is, from detections in several frames of the same view.

    At the gun the buoy is often behind the boats -- race 90 of the 2026 autumn
    series, the fleet on the line and the buoy nowhere in the frame -- and a
    minute either side of it usually is not. The position most frames agree on
    wins, ties to the more confident, so one orange sail taken for the buoy does
    not move the line. Returns (fx, fy, how many frames agreed), or None.
    """
    found = [d for d in detections if d]
    if not found:
        return None

    def near(a: Buoy, b: Buoy) -> bool:
        return abs(a[0] - b[0]) <= SAME_BUOY and abs(a[1] - b[1]) <= SAME_BUOY

    best = max(found, key=lambda d: (sum(1 for e in found if near(d, e)), d[2]))
    group = [e for e in found if near(best, e)]
    return (sum(e[0] for e in group) / len(group), sum(e[1] for e in group) / len(group), len(group))


def line_spec_at(fx: float, fy: float, image_path: str, out_dir: str) -> dict:
    """The line image for a buoy at (fx, fy), at this image's size."""
    cfg = load_startline_config()
    w, h = Image.open(image_path).size
    line = os.path.join(out_dir, f".odm_line_{secrets.token_hex(6)}.png")
    odm.render_line_overlay(cfg, w, h, line, fx, fy, color=GREEN)
    return {"green": line, "red": None, "offset": 0.0, "is_start": False}


def line_spec_for_image(image_path: str, out_dir: str,
                        prior: Iterable[Optional[Buoy]] = ()) -> Tuple[Optional[dict], str]:
    """The line for one still photograph, in the shape ``line_filter_chain`` takes.

    The buoy is looked for in the photo itself and in ``prior``: detections in
    other frames of the same view, a minute or so either side -- which is what
    finds it when the fleet is in front of it at the gun. The caller removes
    the line image with ``cleanup_spec``.
    """
    if not _AVAILABLE:
        return None, "skipped: Pillow/numpy not installed"
    try:
        prior = list(prior)
        here = detect_buoy(image_path)
        where = buoy_consensus(prior + [here])
        frames = len(prior) + 1
        if where is None:
            return None, f"skipped: no confident buoy in {frames} frame{'s' if frames > 1 else ''}"
        fx, fy, agreed = where
        if frames == 1:
            found = "in the photo"
        else:
            found = f"in {agreed} of {frames} frames{'' if here else ', not in the photo itself'}"
        return line_spec_at(fx, fy, image_path, out_dir), f"drawn at ({fx:.3f},{fy:.3f}), buoy found {found}"
    except Exception as exc:
        return None, f"skipped: error {exc}"


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
    # Each line is read ONCE and held for the whole clip (eof_action=repeat),
    # rather than looped. A looped image is re-read from disk every frame, and
    # the overlay was told to stop the moment that input ended -- so anything
    # that rewrote or removed the file mid-encode ended the public copy there,
    # silently, with FFmpeg exiting 0. Read once, nothing that happens to the
    # file afterwards can touch the encode, and a 4K PNG is no longer decoded
    # fifteen times a second. The overlay ends with the video, as before.
    inputs = ["-i", spec["green"]]
    ov = "overlay=0:0:format=auto:eof_action=repeat"
    if spec.get("is_start") and spec.get("red"):
        red_idx = logo_count + 2
        inputs += ["-i", spec["red"]]
        off = f"{max(0.0, float(spec['offset'])):.2f}"
        chain = (f"[0:v][{red_idx}:v]{ov}:enable='lt(t\\,{off})'[la];"
                 f"[la][{green_idx}:v]{ov}:enable='gte(t\\,{off})'[lined]")
    else:
        chain = f"[0:v][{green_idx}:v]{ov}[lined]"
    return inputs, chain
