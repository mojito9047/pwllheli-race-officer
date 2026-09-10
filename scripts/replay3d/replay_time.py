"""The film's clock: race seconds in, film frames out, with slow-motion windows.

A replay at a flat 30x rushes past the two moments people actually want to
watch. This maps race time to film frames at a chosen compression, drops to
real time (or near it) around the start and each finish, eases between the two
rates so the change is not a jolt, and holds still frames at each end for the
title and the results.

The mapping has to be identical everywhere it is used -- the builder keyframes
boats against it, the frame extractor cuts the hut-camera footage against it,
and the clock in the corner inverts it -- so it lives here, free of ``bpy``, and
both sides construct it from the same parameters stored in the race JSON.
"""
from __future__ import annotations

import bisect
from typing import Any, Dict, List, Optional, Sequence, Tuple

FPS = 24
DEFAULT_SPEED = 30.0
DEFAULT_SLOW_SPEED = 1.0        # real time
DEFAULT_SLOW_START = 60.0       # seconds either side of the first start
DEFAULT_SLOW_FINISH = 30.0      # seconds either side of each finish
DEFAULT_EASE = 20.0             # race seconds spent changing rate
DEFAULT_PRE_ROLL = 4.0          # film seconds of title card before the race moves
DEFAULT_POST_ROLL = 8.0         # film seconds of results card after it stops
# Step of the integration grid, in race seconds. Small enough that an ease is
# smooth, large enough that a two-hour race is still a short table.
GRID_S = 0.5


def merge_windows(windows: Sequence[Sequence[float]]) -> List[List[float]]:
    """Overlapping slow-motion windows become one, so a close finish is not two ramps."""
    out: List[List[float]] = []
    for a, b in sorted([float(w[0]), float(w[1])] for w in windows):
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


def slow_windows_for(data: Dict[str, Any], slow_start: float = DEFAULT_SLOW_START,
                     slow_finish: float = DEFAULT_SLOW_FINISH) -> List[List[float]]:
    """Where the film should run at real time: around the start, and around each finish."""
    duration = float(data["time"]["duration_s"])
    first_start = float(data["time"]["first_start_rel"])
    windows: List[List[float]] = []
    if slow_start > 0:
        windows.append([first_start - slow_start, first_start + slow_start])
    if slow_finish > 0:
        for boat in data.get("boats") or []:
            t = boat.get("finish_t")
            if t is not None:
                windows.append([float(t) - slow_finish, float(t) + slow_finish])
    clamped = [[max(0.0, a), min(duration, b)] for a, b in windows if b > 0 and a < duration]
    return merge_windows(clamped)


class TimeWarp:
    """Race seconds to film frames, and back again."""

    def __init__(self, duration_s: float, windows: Optional[Sequence[Sequence[float]]] = None,
                 speed: float = DEFAULT_SPEED, slow_speed: float = DEFAULT_SLOW_SPEED,
                 ease_s: float = DEFAULT_EASE, pre_roll_s: float = DEFAULT_PRE_ROLL,
                 post_roll_s: float = DEFAULT_POST_ROLL, fps: int = FPS) -> None:
        self.duration_s = float(duration_s)
        self.windows = merge_windows(windows or [])
        self.speed = float(speed)
        self.slow_speed = max(0.01, float(slow_speed))
        self.ease_s = max(0.0, float(ease_s))
        self.pre_roll_s = max(0.0, float(pre_roll_s))
        self.post_roll_s = max(0.0, float(post_roll_s))
        self.fps = int(fps)
        self.pre_roll_frames = int(round(self.pre_roll_s * self.fps))
        self.post_roll_frames = int(round(self.post_roll_s * self.fps))
        self._ts: List[float] = []
        self._fs: List[float] = []
        self._build()

    # -- construction ----------------------------------------------------
    def rate_at(self, t: float) -> float:
        """Race seconds per film second at this moment, easing in and out of each window."""
        best = self.speed
        for a, b in self.windows:
            if a - self.ease_s < t < b + self.ease_s:
                if a <= t <= b:
                    rate = self.slow_speed
                elif t < a:
                    f = (a - t) / self.ease_s if self.ease_s else 1.0
                    rate = self.slow_speed + (self.speed - self.slow_speed) * f
                else:
                    f = (t - b) / self.ease_s if self.ease_s else 1.0
                    rate = self.slow_speed + (self.speed - self.slow_speed) * f
                best = min(best, rate)
        return max(0.01, best)

    def _build(self) -> None:
        t = 0.0
        f = float(self.pre_roll_frames)
        self._ts.append(0.0)
        self._fs.append(f)
        while t < self.duration_s:
            step = min(GRID_S, self.duration_s - t)
            # Trapezium over the step: the rate changes linearly inside an ease.
            r0, r1 = self.rate_at(t), self.rate_at(t + step)
            f += step * self.fps * 0.5 * (1.0 / r0 + 1.0 / r1)
            t += step
            self._ts.append(t)
            self._fs.append(f)
        self.race_end_frame = int(round(self._fs[-1]))
        self.total_frames = self.race_end_frame + self.post_roll_frames

    # -- use -------------------------------------------------------------
    def frame_of(self, t_rel: float) -> int:
        """The film frame that shows this moment of the race (1-based)."""
        t = max(0.0, min(float(t_rel), self.duration_s))
        i = bisect.bisect_left(self._ts, t)
        if i <= 0:
            return int(round(self._fs[0]))
        if i >= len(self._ts):
            return int(round(self._fs[-1]))
        t0, t1 = self._ts[i - 1], self._ts[i]
        f0, f1 = self._fs[i - 1], self._fs[i]
        span = t1 - t0
        return int(round(f0 + (f1 - f0) * ((t - t0) / span if span else 0.0)))

    def time_at(self, frame: float) -> float:
        """The moment of the race a film frame shows: the inverse, for the clock."""
        f = float(frame)
        if f <= self._fs[0]:
            return 0.0
        if f >= self._fs[-1]:
            return self.duration_s
        i = bisect.bisect_left(self._fs, f)
        f0, f1 = self._fs[i - 1], self._fs[i]
        t0, t1 = self._ts[i - 1], self._ts[i]
        span = f1 - f0
        return t0 + (t1 - t0) * ((f - f0) / span if span else 0.0)

    # -- carrying it between the scripts ---------------------------------
    def to_dict(self) -> Dict[str, Any]:
        return {"fps": self.fps, "speed": self.speed, "slow_speed": self.slow_speed,
                "ease_s": self.ease_s, "pre_roll_s": self.pre_roll_s, "post_roll_s": self.post_roll_s,
                "windows": [[round(a, 2), round(b, 2)] for a, b in self.windows],
                "duration_s": self.duration_s, "total_frames": self.total_frames,
                "race_end_frame": self.race_end_frame,
                "pre_roll_frames": self.pre_roll_frames, "post_roll_frames": self.post_roll_frames}

    @classmethod
    def from_dict(cls, spec: Dict[str, Any]) -> "TimeWarp":
        return cls(duration_s=float(spec["duration_s"]), windows=spec.get("windows") or [],
                   speed=float(spec.get("speed", DEFAULT_SPEED)),
                   slow_speed=float(spec.get("slow_speed", DEFAULT_SLOW_SPEED)),
                   ease_s=float(spec.get("ease_s", DEFAULT_EASE)),
                   pre_roll_s=float(spec.get("pre_roll_s", DEFAULT_PRE_ROLL)),
                   post_roll_s=float(spec.get("post_roll_s", DEFAULT_POST_ROLL)),
                   fps=int(spec.get("fps", FPS)))

    def summary(self) -> str:
        slow = sum(b - a for a, b in self.windows)
        return (f"{self.total_frames} frames, {self.total_frames / self.fps:.0f} s of film "
                f"({len(self.windows)} slow windows covering {slow:.0f} s of racing at "
                f"{self.slow_speed:g}x, the rest at {self.speed:g}x)")
