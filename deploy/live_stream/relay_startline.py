"""Start-line overlay for the relay stream (prototype).

Grabs one frame from the camera, detects the orange ODM buoy, and renders a
full-frame transparent PNG with the start line drawn from the configured pole
base to the buoy. The relay's ffmpeg then composites that PNG like any other
overlay layer.

Deliberately BEST-EFFORT: every entry point returns None (or False) on any
problem — a missing dependency, no camera frame, or no confident detection — so
a hiccup never takes the live stream down; it just streams without the line.

Detection cadence: the buoy is anchored and moves slowly, so we detect ONCE at
stream start (runOnDemand). The line is fixed for that viewing session. A
periodic refresh can be added later if drift over long sessions matters.
"""
from __future__ import annotations

import os
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from PIL import Image  # noqa: E402
import odm_detector as odm  # noqa: E402


def startline_enabled(cfg_path: str) -> bool:
    """Cheap check so the caller can skip the frame grab/probe when disabled."""
    try:
        return bool(odm.load_config(cfg_path).get("relay_overlay_enabled", True))
    except Exception:
        return False


def _grab_frame(camera_url: str, dest: str, timeout: int = 20) -> str:
    """Pull a single frame from the camera to `dest` (raises on failure)."""
    subprocess.run(
        # -analyzeduration/-probesize: newer ffmpeg defaults analyzeduration to 0 and
        # then fails to read this sub-stream's H.264 dimensions ("unspecified size"),
        # so the frame grab never produces a frame. Give it headroom to find them.
        ["ffmpeg", "-nostdin", "-loglevel", "error", "-rtsp_transport", "tcp",
         "-analyzeduration", "10M", "-probesize", "10M",
         "-use_wallclock_as_timestamps", "1", "-i", camera_url,
         "-frames:v", "1", "-y", dest],
        check=True, timeout=timeout,
    )
    return dest


def build_startline_overlay(camera_url: str, width: int, height: int,
                            cfg_path: str, tmp: str):
    """Return a path to the rendered start-line PNG, or None if unavailable.

    None means: disabled, no frame, no confident detection, or any error — in
    every case the stream should simply run without the line.
    """
    try:
        cfg = odm.load_config(cfg_path)
        if not cfg.get("relay_overlay_enabled", True):
            return None
        frame_path = _grab_frame(camera_url, os.path.join(tmp, "odm_frame.jpg"))
        det = odm.detect(Image.open(frame_path), cfg)
        if not det.found:
            sys.stderr.write("startline: no confident ODM detection; streaming without line\n")
            return None
        sys.stderr.write(
            f"startline: ODM at ({det.fx:.3f},{det.fy:.3f}) conf={det.conf:.2f}; drawing line\n")
        # Live relay has no start concept -> use the config default colour (green).
        return odm.render_line_overlay(cfg, width, height, os.path.join(tmp, "startline.png"),
                                       det.fx, det.fy)
    except Exception as exc:
        sys.stderr.write(f"startline overlay failed ({exc}); streaming without line\n")
        return None
