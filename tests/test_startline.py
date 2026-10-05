"""Tests for the ODM start-line overlay on public videos.

Covers the config toggle (default off), the enable gating, and the detector +
overlay render on a synthetic frame. The actual ffmpeg overlay pass is not
exercised (no ffmpeg/clip in CI); it is fully guarded and best-effort.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest
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


# ---------------------------------------------------------------------------
# Race 96: public copies cut short by a shared, looped line image
# ---------------------------------------------------------------------------

def _clip_frame(ffmpeg, path, at_seconds, dest):
    """Stand-in for the frame grab: the synthetic buoy, wherever it is asked for."""
    _synthetic_frame(Path(dest))
    return True


def test_each_clip_gets_line_images_of_its_own(tmp_path, monkeypatch):
    """They were named for the app's process, so every clip it built shared one
    green PNG -- and the next finish rewrote it under the last one's encode."""
    monkeypatch.setattr(startline, "_grab_frame", _clip_frame)
    # The synthetic frame is tuned for the built-in detector settings, not the club's.
    monkeypatch.setattr(startline, "load_startline_config", lambda: odm.load_config(None))
    first, _ = startline.build_startline_spec("ffmpeg", "a.mp4", "manual_horn", 60.0, str(tmp_path))
    second, _ = startline.build_startline_spec("ffmpeg", "b.mp4", "manual_horn", 60.0, str(tmp_path))
    assert first and second
    assert first["green"] != second["green"]
    start, _ = startline.build_startline_spec("ffmpeg", "c.mp4", "start", 64.0, str(tmp_path))
    assert start["red"] not in (first["green"], second["green"])
    for spec in (first, second, start):
        startline.cleanup_spec(spec)
    assert not list(tmp_path.glob(".odm_*")), "the frame and every line image are removed"


def test_the_line_is_read_once_and_held(tmp_path):
    """A looped image is re-read every frame, and the overlay stopped the moment
    that input ended: rewriting the file mid-encode ended the public copy there,
    with FFmpeg exiting 0."""
    spec = {"green": str(tmp_path / "g.png"), "red": str(tmp_path / "r.png"), "offset": 64.0,
            "is_start": True}
    inputs, chain = startline.line_filter_chain(spec, logo_count=2)
    assert "-loop" not in inputs
    assert inputs == ["-i", spec["green"], "-i", spec["red"]]
    assert "shortest=1" not in chain
    assert chain.count("eof_action=repeat") == 2
    assert "[0:v][4:v]" in chain and "[la][3:v]" in chain      # red is input 4, green 3


def test_a_held_line_lasts_the_whole_clip(tmp_path):
    """The other way to get this wrong: a still image that is not looped and not
    held gives a public copy one frame long."""
    import shutil
    import subprocess
    if not shutil.which("ffmpeg"):
        pytest.skip("needs ffmpeg")
    clip = tmp_path / "clip.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=640x360:rate=10",
                    "-t", "6", "-pix_fmt", "yuv420p", str(clip)], check=True)
    line = tmp_path / ".odm_green_test.png"
    Image.new("RGBA", (640, 360), (0, 235, 0, 120)).save(line)
    spec = {"green": str(line), "red": None, "offset": 3.0, "is_start": False}
    out = tmp_path / "public.mp4"
    cmd = ro.build_public_video_transcode_command("ffmpeg", clip, out, {"video_public_quality": "720p"},
                                                  startline_spec=spec)
    subprocess.run(cmd, check=True)
    from core.video import mp4_duration_seconds
    assert abs(mp4_duration_seconds(out) - 6.0) < 0.2
