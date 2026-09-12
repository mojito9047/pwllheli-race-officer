"""The silent stage at the front of a render, made to say something.

Before a single frame is drawn, one Blender process walks every frame of the
film working out where each boat and mark lands on screen -- only Blender knows
where the camera was pointing. It renders nothing, so it holds a single core and
leaves the graphics card idle, and on a full-length film that is minutes of it.

It reported itself all along, every couple of thousand frames. Blender is noisy
on the way up, so the pass was launched with both streams sent to DEVNULL, and
that progress went with the noise: the log said "writing the overlay track" and
then nothing, the dashboard card said "rendering 5%", and the machine looked
asleep. It was reported as a hang.

So the line is let through, and the card says what is happening -- without
moving the bar, because none of the film is made yet.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_REPLAY3D = Path(__file__).resolve().parents[1] / "scripts" / "replay3d"


def _load(name):
    """Import one of the render scripts the way the render machine does."""
    if str(_REPLAY3D) not in sys.path:
        sys.path.insert(0, str(_REPLAY3D))
    spec = importlib.util.spec_from_file_location(name, _REPLAY3D / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _says(*lines, code=0):
    """A stand-in for Blender: prints what we tell it, exits how we tell it."""
    body = "; ".join(f"print({line!r})" for line in lines)
    return [sys.executable, "-c", f"{body}; import sys; sys.exit({code})"]


NOISE = "Blender 5.2.0 LTS | Read prefs | Warning: property 'x' not found"
PROGRESS = "  overlay track 2000/5420 ( 36.9%)"
# As render_parallel really prints it, so the two patterns are told apart
# on the genuine article rather than on something shaped to pass.
FRAMES = "[  3.2 min] 120/5420 frames | 1/4 done | part00 rendering"


class TestTheOverlayPassIsHeard:
    """render_parallel lets the progress line through and keeps the rest."""

    def test_the_progress_line_is_printed(self, capfd):
        rp = _load("render_parallel")
        rc, _ = rp._run_reporting(_says(NOISE, PROGRESS), keep="overlay track")
        assert rc == 0
        assert "overlay track 2000/5420" in capfd.readouterr().out

    def test_blenders_own_noise_is_not(self, capfd):
        """Which is why this was sent to DEVNULL in the first place."""
        rp = _load("render_parallel")
        rp._run_reporting(_says(NOISE, PROGRESS), keep="overlay track")
        assert "Read prefs" not in capfd.readouterr().out

    def test_a_failure_can_now_say_why(self, capfd):
        """It could only say that it had happened, which helped nobody."""
        rp = _load("render_parallel")
        rc, tail = rp._run_reporting(
            _says(NOISE, "Error: cannot open the scene file", code=1), keep="overlay track")
        assert rc == 1
        assert any("cannot open the scene file" in line for line in tail)

    def test_the_tail_is_bounded(self):
        """A Blender traceback must not become the whole log."""
        rp = _load("render_parallel")
        _, tail = rp._run_reporting(_says(*[f"line {i}" for i in range(200)], code=1),
                                    keep="nothing matches this")
        assert len(tail) == 12
        assert tail[-1] == "line 199"


class TestTheCardSaysWhatIsHappening:
    """The renderer turns that line into a status the hut can show."""

    class Recorder:
        def __init__(self):
            self.calls = []

        def set(self, state, message="", progress=None, **extra):
            self.calls.append({"state": state, "message": message, "progress": progress})

    def _drive(self, *lines):
        renderer = _load("renderer")
        progress = self.Recorder()
        renderer._run(_says(*lines), progress, "rendering", 0.05, 0.97)
        return progress.calls

    def test_it_reports_the_pass_by_name(self):
        calls = self._drive(NOISE, PROGRESS)
        assert calls, "the overlay pass still says nothing at all"
        assert "placing names and marks 36%" == calls[-1]["message"]

    def test_it_does_not_move_the_bar(self):
        """No frame of the film exists yet, so any fraction would be a lie.

        It also has to not reach into the 5%-to-97% band the frames use, or the
        bar would run to the end and start again.
        """
        calls = self._drive(PROGRESS)
        assert [c["progress"] for c in calls] == [None]

    def test_a_real_frame_count_still_moves_it(self):
        """The line this must not be confused with."""
        calls = self._drive(FRAMES)
        assert calls and calls[-1]["progress"] == pytest.approx(0.05 + 0.92 * 120 / 5420)
        assert calls[-1]["message"] == "rendering 2%"

    def test_the_two_do_not_read_each_other(self):
        """Both carry an a/b, and one of them must not drive the other."""
        calls = self._drive(PROGRESS, FRAMES)
        assert calls[0]["progress"] is None
        assert calls[1]["progress"] is not None
