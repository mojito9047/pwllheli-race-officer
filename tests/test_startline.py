"""Tests for the ODM start-line overlay on public videos.

Covers the config toggle (default off), the enable gating, and the detector +
overlay render on a synthetic frame. The actual ffmpeg overlay pass is not
exercised (no ffmpeg/clip in CI); it is fully guarded and best-effort.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import app as ro  # noqa: E402
from core import startline, odm_detector as odm  # noqa: E402


def _synthetic_frame(path: Path):
    """Sea/sky/beach with a small saturated-orange buoy in the swing zone."""
    w, h = 1920, 1080
    a = np.zeros((h, w, 3), dtype=np.uint8)
    a[: int(0.42 * h)] = (180, 190, 200)              # sky
    a[int(0.42 * h): int(0.85 * h)] = (60, 80, 95)    # sea
    a[int(0.85 * h):] = (150, 140, 120)               # beach
    a[600:640, 1150:1166] = (230, 140, 20)            # buoy (orange, taller than wide)
    Image.fromarray(a).save(path)
    return path


def test_video_config_defaults_off(client):
    assert ro.video_config()["video_startline_overlay_enabled"] is False


def test_settings_roundtrip_enables(logged_in_client):
    token = "test-csrf-token"
    with logged_in_client.session_transaction() as sess:
        sess["_csrf_token"] = token
    resp = logged_in_client.post(
        "/admin/settings/save",
        data={"_csrf_token": token, "video_startline_overlay_enabled": "1"},
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303)
    assert ro.video_config()["video_startline_overlay_enabled"] is True


def test_enable_gating_respects_toggle():
    # Off -> never enabled, regardless of dependency availability.
    assert startline.startline_overlay_enabled({"video_startline_overlay_enabled": False}) is False
    # On -> enabled iff the optional deps are importable (they are, in CI).
    on = startline.startline_overlay_enabled({"video_startline_overlay_enabled": True})
    assert on == startline.startline_overlay_available()


def test_detect_and_render_semi_transparent(tmp_path):
    cfg = odm.load_config(None)  # built-in tuned defaults
    img = Image.open(_synthetic_frame(tmp_path / "frame.jpg"))
    det = odm.detect(img, cfg)
    assert det.found, "synthetic buoy should be detected"

    out = tmp_path / "line.png"
    odm.render_line_overlay(cfg, img.size[0], img.size[1], str(out), det.fx, det.fy, color=(0, 235, 0))
    overlay = Image.open(out)
    assert overlay.size == img.size
    assert overlay.mode == "RGBA"
    alpha = np.array(overlay.getchannel("A"))
    # Drawn solid then scaled by line_opacity (<1) -> visible but not fully opaque.
    assert 0 < int(alpha.max()) < 255


def test_render_colour_is_applied(tmp_path):
    cfg = odm.load_config(None)
    img = Image.open(_synthetic_frame(tmp_path / "frame.jpg"))
    det = odm.detect(img, cfg)
    red_png = tmp_path / "red.png"
    odm.render_line_overlay(cfg, img.size[0], img.size[1], str(red_png), det.fx, det.fy, color=(235, 0, 0))
    arr = np.array(Image.open(red_png))
    drawn = arr[arr[..., 3] > 0]  # pixels with any alpha
    assert drawn.size > 0
    # Red channel dominates green on the drawn line pixels.
    assert drawn[:, 0].mean() > drawn[:, 1].mean()
