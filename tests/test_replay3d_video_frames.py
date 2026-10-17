"""The 3D replay's picture-in-picture: footage that is there, or none.

Race 96's film held one frame of MOJITO BACH's finish for 2,055 film frames: the
public copy it was given stopped at 10.9 s of a clip whose finish is at 80 s,
and the extractor held the last frame it had for the whole window.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts" / "replay3d"))

av = pytest.importorskip("av", reason="the extractor decodes with PyAV")
pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg to make a clip")

import prepare_video_frames as frames  # noqa: E402


class OneFramePerSecond:
    """A film clock with no slow motion: film frame n is replay second n."""

    def frame_of(self, t: float) -> int:
        return int(round(t))

    def time_at(self, f: int) -> float:
        return float(f)


@pytest.fixture()
def ten_seconds(tmp_path):
    clip = tmp_path / "clip.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=320x180:rate=5",
                    "-t", "10", "-pix_fmt", "yuv420p", str(clip)], check=True)
    return clip


def _clip(path, t_start, t_end, t_event, offset):
    return {"id": 7, "file": str(path), "t_start": t_start, "t_end": t_end, "t_event": t_event,
            "footage_offset_s": offset}


def test_footage_that_stops_before_its_event_is_left_out(ten_seconds, tmp_path):
    clip = _clip(ten_seconds, 0, 60, 30, offset=30)
    assert frames.extract_clip(clip, str(tmp_path), OneFramePerSecond(), width=0) is None


def test_footage_that_stops_inside_its_window_ends_the_inset_there(ten_seconds, tmp_path):
    clip = _clip(ten_seconds, 0, 20, 5, offset=5)
    info = frames.extract_clip(clip, str(tmp_path), OneFramePerSecond(), width=0)
    # Seconds 0..10 of footage, plus the one second's hold -- not 21 frames
    # with the last ten all the same.
    assert info["count"] == 12


def test_footage_that_covers_its_window_is_all_shown(ten_seconds, tmp_path):
    clip = _clip(ten_seconds, 0, 8, 4, offset=4)
    info = frames.extract_clip(clip, str(tmp_path), OneFramePerSecond(), width=0)
    assert info["count"] == 9


# ---------------------------------------------------------------------------
# Race 97: one finish video on the bucket was half-written, and the render
# failed at the same point on every attempt.
# ---------------------------------------------------------------------------

def _half_written(path):
    path.write_bytes(b"\x00\x00\x00\x08mdat" + b"\x00" * 4096)
    return path


def test_a_half_written_clip_is_not_playable(ten_seconds, tmp_path):
    import renderer
    assert renderer.playable(str(ten_seconds))
    assert not renderer.playable(str(_half_written(tmp_path / "half.mp4")))


def test_a_clip_that_will_not_play_is_left_out_of_the_film(ten_seconds, tmp_path, monkeypatch):
    import io
    import renderer
    good = ten_seconds.read_bytes()
    served = {"https://x/race97_finish_396_public.mp4": good,
              "https://x/race97_finish_400_public.mp4": b"\x00\x00\x00\x08mdat" + b"\x00" * 4096}
    fetched = []

    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def urlopen(req, timeout=None):
        fetched.append(req.full_url)
        return Response(served[req.full_url])
    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    scene = {"videos": [{"id": 396, "url": "https://x/race97_finish_396_public.mp4"},
                        {"id": 400, "url": "https://x/race97_finish_400_public.mp4"}]}
    assert renderer.fetch_clips(scene, str(tmp_path)) == 1
    assert [c["id"] for c in scene["videos"]] == [396]
    assert fetched.count("https://x/race97_finish_400_public.mp4") == 2, "fetched once more before giving up"
    assert not (tmp_path / "video" / "race97_finish_400_public.mp4").exists(), "and not kept for next time"


def test_one_unreadable_clip_does_not_stop_the_frames(ten_seconds, tmp_path):
    import json
    _half_written(tmp_path / "half.mp4")
    scene = {"time": {"duration_s": 60.0, "first_start_rel": 0.0}, "videos": [
        {"id": 1, "kind": "finish", "file": "half.mp4", "t_start": 0, "t_end": 8, "t_event": 4,
         "footage_offset_s": 4, "duration_s": 10.0},
        {"id": 2, "kind": "finish", "file": str(ten_seconds), "t_start": 20, "t_end": 28, "t_event": 24,
         "footage_offset_s": 4}]}
    path = tmp_path / "race.json"
    path.write_text(json.dumps(scene), encoding="utf-8")
    assert frames.main(["--json", str(path), "--slow-finish", "0", "--slow-start", "0"]) == 0
    written = json.loads(path.read_text(encoding="utf-8"))["videos"]
    assert "sequence" not in written[0] and written[1]["sequence"]["count"] > 0
