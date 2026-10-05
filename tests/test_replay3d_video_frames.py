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
