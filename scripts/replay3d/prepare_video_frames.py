#!/usr/bin/env python3
"""Cut the hut-camera clips into the exact frames the film needs, as image sequences.

    python scripts/replay3d/prepare_video_frames.py --json runtime/replay3d/race_69.json --speed 30

Blender can texture a plane with an .mp4 directly, and on a machine with a
sound graphics driver that is the obvious way to do it. On this laptop it is
not: the movie texture goes through the Intel hardware H.264 decoder and
segfaults the render the moment the clip's window opens -- twice, at the same
frame, with and without a second Blender running. An image sequence decodes
nothing at render time, so this extracts just the frames the film will show
(about a hundred per clip) and the builder maps one image to one film frame.

The mapping matches ``build_scene.build_videos`` exactly, so ``--speed`` and
``--video-speed`` must be the ones the film is built with. The sequences are
written beside the clips and recorded back into the race JSON.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from replay_time import (  # noqa: E402
    DEFAULT_EASE, DEFAULT_POST_ROLL, DEFAULT_PRE_ROLL, DEFAULT_SLOW_FINISH, DEFAULT_SLOW_SPEED,
    DEFAULT_SLOW_START, DEFAULT_SPEED, TimeWarp, slow_windows_for,
)


def extract_clip(clip: Dict[str, Any], base: str, warp: TimeWarp,
                 width: int = 720, quality: int = 86) -> Optional[Dict[str, Any]]:
    """Write one image per film frame of this clip's window. Returns the sequence info.

    The footage is locked to the replay clock, so during the slow-motion windows
    around the start and each finish it runs at its natural speed beside a 3D
    scene doing the same, and the two agree frame for frame.
    """
    import av
    from PIL import Image

    path = clip["file"] if os.path.isabs(clip["file"]) else os.path.join(base, clip["file"])
    if not os.path.exists(path):
        print(f"  {clip['file']}: missing, skipped")
        return None

    t_start, t_end, t_event = float(clip["t_start"]), float(clip["t_end"]), float(clip["t_event"])
    f0, f1 = warp.frame_of(t_start), warp.frame_of(t_end)
    event_at = float(clip.get("footage_offset_s") if clip.get("footage_offset_s") is not None
                     else t_event - t_start)
    duration_s = float(clip.get("duration_s") or 0.0)

    # The video second each film frame should show.
    wanted: List[float] = []
    for f in range(f0, f1 + 1):
        secs = event_at + (warp.time_at(f) - t_event)
        if duration_s:
            secs = max(0.0, min(secs, duration_s - 0.05))
        wanted.append(max(0.0, secs))

    out_dir = os.path.join(os.path.dirname(path), f"frames_{clip.get('id', 0)}")
    os.makedirs(out_dir, exist_ok=True)
    for stale in os.listdir(out_dir):
        os.remove(os.path.join(out_dir, stale))

    # One pass through the span we need, keeping the nearest decoded frame to
    # each wanted moment. Seeking per frame would be slower and less reliable.
    lo, hi = min(wanted), max(wanted)
    written = 0
    with av.open(path) as container:
        stream = container.streams.video[0]
        stream.thread_type = "AUTO"
        if lo > 2.0 and stream.time_base:
            container.seek(int((lo - 2.0) / float(stream.time_base)), stream=stream)
        idx = 0
        last_img = None
        for frame in container.decode(stream):
            if frame.pts is None:
                continue
            t = float(frame.pts * stream.time_base)
            while idx < len(wanted) and t >= wanted[idx]:
                img = frame.to_image()
                if width and img.width > width:
                    img = img.resize((width, round(img.height * width / img.width)), Image.LANCZOS)
                img.save(os.path.join(out_dir, f"frame_{idx + 1:05d}.jpg"), quality=quality)
                written += 1
                idx += 1
            last_img = frame
            if idx >= len(wanted) or t > hi + 1.0:
                break
        # The tail can run past the end of the footage; hold the last frame.
        while idx < len(wanted) and last_img is not None:
            img = last_img.to_image()
            if width and img.width > width:
                img = img.resize((width, round(img.height * width / img.width)), Image.LANCZOS)
            img.save(os.path.join(out_dir, f"frame_{idx + 1:05d}.jpg"), quality=quality)
            written += 1
            idx += 1

    if not written:
        print(f"  {clip['file']}: no frames decoded, skipped")
        return None
    first = os.path.join(out_dir, "frame_00001.jpg")
    from PIL import Image as _Image
    with _Image.open(first) as probe:
        size = probe.size
    return {"dir": os.path.relpath(out_dir, base).replace("\\", "/"),
            "first": os.path.relpath(first, base).replace("\\", "/"),
            "count": written, "frame0": f0, "frame1": f1,
            "width": size[0], "height": size[1]}


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--json", required=True, help="race export from export_race.py")
    ap.add_argument("--speed", type=float, default=DEFAULT_SPEED, help="race seconds per film second")
    ap.add_argument("--slow-speed", type=float, default=DEFAULT_SLOW_SPEED,
                    help="rate through the start and the finishes (1 = real time)")
    ap.add_argument("--slow-start", type=float, default=DEFAULT_SLOW_START,
                    help="seconds either side of the first start to play slowly")
    ap.add_argument("--slow-finish", type=float, default=DEFAULT_SLOW_FINISH,
                    help="seconds either side of each finish to play slowly")
    ap.add_argument("--ease", type=float, default=DEFAULT_EASE, help="race seconds spent changing rate")
    ap.add_argument("--pre-roll", type=float, default=DEFAULT_PRE_ROLL, help="film seconds of title card")
    ap.add_argument("--post-roll", type=float, default=DEFAULT_POST_ROLL, help="film seconds of results card")
    ap.add_argument("--width", type=int, default=720, help="width to save the frames at")
    args = ap.parse_args(argv)

    path = os.path.abspath(args.json)
    base = os.path.dirname(path)
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Plan the film here, and record it, so the builder keyframes against exactly
    # the same clock these frames were cut for.
    warp = TimeWarp(float(data["time"]["duration_s"]),
                    slow_windows_for(data, args.slow_start, args.slow_finish),
                    speed=args.speed, slow_speed=args.slow_speed, ease_s=args.ease,
                    pre_roll_s=args.pre_roll, post_roll_s=args.post_roll)
    data["film"] = warp.to_dict()
    print(f"film: {warp.summary()}")

    total = 0
    for clip in data.get("videos") or []:
        info = extract_clip(clip, base, warp, args.width)
        if info:
            clip["sequence"] = info
            total += info["count"]
            print(f"  {clip['kind']:<7} {info['count']:>5} frames -> {info['dir']} "
                  f"(film frames {info['frame0']}-{info['frame1']}, {info['width']}x{info['height']})")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, separators=(",", ":"))
    print(f"wrote {total} frames and updated {os.path.basename(path)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
