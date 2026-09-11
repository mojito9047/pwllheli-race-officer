#!/usr/bin/env python3
"""Assemble the finished film: stepped 3D held to full rate, hut camera laid on top.

    python scripts/replay3d/compose_film.py --json runtime/replay3d/race_69.json \
        --parts runtime/replay3d/renders/race_69_parts.json --out runtime/replay3d/renders/race_69.mp4

Where the replay runs at real time the boats crawl, and the fixes behind them are
fifteen to sixty seconds apart, so the frames in between are interpolation of
data nobody measured. Rendering every fourth or fifth frame there and holding it
looks the same and costs a quarter of the time.

The hut-camera footage is the one thing that must not be held: it is real video,
and stepping it turns a start into a slideshow. So the 3D is rendered without it
and the panel is composited here, at the film's full frame rate, from the images
``prepare_video_frames.py`` already cut.

The parts manifest is a list of the rendered segments, each with the film frames
it covers and the step it was rendered at:

    [{"file": "race_69_part0_0001-1610.mp4", "start": 1, "end": 1610, "step": 1}, ...]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from overlay import Overlay, load_track  # noqa: E402
from replay_style import THEME_SRGB  # noqa: E402

# The panel, in pixels of a 1920-wide frame; scaled for other sizes.
PANEL_FRACTION = 0.34
MARGIN_PX = 24
BORDER_PX = 3
LABEL_H_PX = 30


def _font(fonts_dir: str, name: str, size: int):
    from PIL import ImageFont
    path = os.path.join(fonts_dir, name)
    if os.path.exists(path):
        return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def _tracked(draw, xy: Tuple[float, float], text: str, font, fill, tracking: float = 2.0) -> None:
    x, y = xy
    for ch in text:
        draw.text((x, y), ch, font=font, fill=fill, anchor="ls")
        x += draw.textlength(ch, font=font) + tracking


class SegmentReader:
    """Frames of one rendered segment, decoded in order and held between steps."""

    def __init__(self, path: str, start: int, end: int, step: int) -> None:
        import av
        self.path, self.start, self.end, self.step = path, int(start), int(end), max(1, int(step))
        self.container = av.open(path)
        self.stream = self.container.streams.video[0]
        self.stream.thread_type = "AUTO"
        self.iter = self.container.decode(self.stream)
        self.index = -1
        self.current = None

    def frame_for(self, film_frame: int):
        """The rendered image that stands for this film frame."""
        wanted = (film_frame - self.start) // self.step
        while self.index < wanted:
            try:
                self.current = next(self.iter)
            except StopIteration:
                break
            self.index += 1
        return self.current

    def close(self) -> None:
        try:
            self.container.close()
        except Exception:
            pass


def _clip_for(clips: List[Dict[str, Any]], film_frame: int) -> Optional[Tuple[Dict[str, Any], int]]:
    for clip in clips:
        seq = clip.get("sequence")
        if not seq:
            continue
        f0 = int(seq["frame0"])
        if f0 <= film_frame < f0 + int(seq["count"]):
            return clip, film_frame - f0 + 1
    return None


def compose(json_path: str, parts: List[Dict[str, Any]], out_path: str, crf: int = 19,
            fps: Optional[int] = None, track_path: Optional[str] = None,
            frame_range: Optional[Tuple[int, int]] = None,
            encoder_threads: Optional[int] = None) -> Dict[str, Any]:
    import av
    from PIL import Image, ImageDraw

    base = os.path.dirname(os.path.abspath(json_path))
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    film = data.get("film") or {}
    total = int(film.get("total_frames") or 0)
    fps = int(fps or film.get("fps") or 24)
    clips = [c for c in (data.get("videos") or []) if c.get("sequence")]
    t0_epoch = float(data["time"]["t0_epoch"])
    fonts_dir = os.path.join(base, "fonts")

    # Only the frames the given segments actually cover: composing a single
    # segment must not run on to the end of the film holding its last frame.
    parts = sorted(parts, key=lambda p: int(p["start"]))
    first_frame = min(int(p["start"]) for p in parts)
    last_frame = max(int(p["end"]) for p in parts)
    if total:
        last_frame = min(last_frame, total)
    if frame_range:
        # One slice of the film, for composing several at once. The frame
        # numbers stay absolute throughout -- the overlay, the hut-camera inset
        # and the cards are all keyed on the film frame, so a slice draws
        # exactly what it would have drawn in a single pass.
        first_frame = max(first_frame, int(frame_range[0]))
        last_frame = min(last_frame, int(frame_range[1]))
        if last_frame < first_frame:
            raise SystemExit(f"frame range {frame_range} covers none of the rendered parts")
    total = last_frame - first_frame + 1
    readers = [SegmentReader(p["file"] if os.path.isabs(p["file"]) else os.path.join(
        os.path.dirname(os.path.abspath(out_path)), p["file"]), p["start"], p["end"], p.get("step", 1))
        for p in parts]

    first = readers[0].frame_for(readers[0].start)
    if first is None:
        raise SystemExit("the first segment decoded no frames")
    width, height = first.width, first.height
    scale = width / 1920.0
    panel_w = int(round(width * PANEL_FRACTION))
    margin = int(round(MARGIN_PX * scale))
    border = max(1, int(round(BORDER_PX * scale)))
    label_h = int(round(LABEL_H_PX * scale))
    label_font = _font(fonts_dir, "ArchivoNarrow.ttf", max(9, int(round(15 * scale))))

    # Names, mark numbers, clock, course board and cards are drawn here rather
    # than modelled in the scene: see overlay.py for why.
    if track_path is None:
        guess = os.path.splitext(json_path)[0] + "_overlay.json"
        track_path = guess if os.path.exists(guess) else None
    track = load_track(track_path)
    if track_path and track is None:
        print(f"  no overlay track at {track_path}: names and marks will be missing")
    overlay = Overlay(data, track, (width, height), base)

    started = time.time()
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    out = av.open(out_path, "w")
    stream = out.add_stream("libx264", rate=fps)
    stream.width, stream.height, stream.pix_fmt = width, height, "yuv420p"
    stream.options = {"crf": str(crf), "preset": "medium"}
    # libx264 defaults to one thread through PyAV. Encoding is only a sixth of
    # this loop, so this is worth about 15% and no more -- the measurement that
    # matters is below, on the colour conversion.
    #
    # The caller sets this when several slices are being composed at once.
    # Left to take every core, eight slices would ask for eight times the
    # machine's threads between them and spend the difference context
    # switching.
    stream.thread_count = max(1, int(encoder_threads or (os.cpu_count() or 2)))
    stream.thread_type = "FRAME"

    reader_i = 0
    written = 0
    for f in range(first_frame, last_frame + 1):
        while reader_i + 1 < len(readers) and f > readers[reader_i].end:
            readers[reader_i].close()
            reader_i += 1
        frame = readers[reader_i].frame_for(f)
        if frame is None:
            break
        # Eighteen times faster than frame.to_image().convert("RGBA"), and
        # byte-identical: swscale does YUV->RGBA in one pass and PIL wraps the
        # buffer, where to_image() takes a slow path to RGB and then PIL copies
        # the whole frame again to add the alpha channel. Measured on a 1080p
        # frame: 11.25 ms against 0.61 ms, which was a fifth of the whole
        # compose stage spent converting a picture into the same picture.
        img = Image.fromarray(frame.to_ndarray(format="rgba"), "RGBA")
        overlay.draw_scene(img, f)

        found = _clip_for(clips, f)
        if found:
            clip, index = found
            seq = clip["sequence"]
            path = os.path.join(base, seq["dir"].replace("/", os.sep), f"frame_{index:05d}.jpg")
            still = None
            if os.path.exists(path):
                with Image.open(path) as raw:
                    still = raw.convert("RGB")
                    panel_h = int(round(panel_w * still.height / still.width))
                    still = still.resize((panel_w, panel_h), Image.LANCZOS)
            if still is not None:
                x0 = width - margin - panel_w
                y0 = height - margin - still.height
                draw = ImageDraw.Draw(img)
                draw.rectangle([x0 - border, y0 - border - label_h, x0 + panel_w + border, y0 + still.height + border],
                               fill=THEME_SRGB["panel_rule"])
                draw.rectangle([x0 - border, y0 - border - label_h, x0 + panel_w + border, y0 - border],
                               fill=THEME_SRGB["panel"])
                label = (f"{clip['kind'].upper()}  "
                         f"{time.strftime('%H:%M:%S', time.localtime(t0_epoch + float(clip['t_event'])))}")
                _tracked(draw, (x0 + 2 * border, y0 - border - int(label_h * 0.28)), label, label_font,
                         THEME_SRGB["panel_ink"], tracking=1.6 * scale)
                img.paste(still, (x0, y0))

        # Cards are full frame, so they go on last and cover the panel too.
        overlay.draw_cards(img, f)

        packet_frame = av.VideoFrame.from_image(img.convert("RGB"))
        for packet in stream.encode(packet_frame):
            out.mux(packet)
        written += 1
        if written % 500 == 0:
            done = written / total
            elapsed = time.time() - started
            print(f"  composed {written}/{total} ({done:5.1%})  "
                  f"elapsed {elapsed / 60:4.1f} min  eta {(elapsed / done - elapsed) / 60:4.1f} min", flush=True)

    for packet in stream.encode():
        out.mux(packet)
    out.close()
    for reader in readers:
        reader.close()
    return {"out": out_path, "frames": written, "size": [width, height], "fps": fps,
            "minutes": round((time.time() - started) / 60, 1)}


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--json", required=True, help="race export from export_race.py")
    ap.add_argument("--parts", help="JSON manifest of the rendered segments")
    ap.add_argument("--part", action="append", default=[],
                    help="a segment as file:start:end:step, repeatable (instead of --parts)")
    ap.add_argument("--out", required=True, help="the finished MP4")
    ap.add_argument("--crf", type=int, default=19)
    ap.add_argument("--frames", help="compose only film frames A-B (for composing slices in parallel)")
    ap.add_argument("--encoder-threads", type=int, default=None,
                    help="x264 threads (default: every core; lower it when composing slices at once)")
    ap.add_argument("--track", help="overlay track from build_scene.py --overlay-track "
                                    "(default: <json>_overlay.json beside the export)")
    args = ap.parse_args(argv)

    parts: List[Dict[str, Any]] = []
    if args.parts:
        with open(args.parts, "r", encoding="utf-8") as f:
            parts = json.load(f)
    for spec in args.part:
        path, start, end, step = spec.rsplit(":", 3)
        parts.append({"file": path, "start": int(start), "end": int(end), "step": int(step)})
    if not parts:
        ap.error("give --parts or at least one --part")

    frame_range = None
    if args.frames:
        lo, hi = args.frames.split("-", 1)
        frame_range = (int(lo), int(hi))
    info = compose(args.json, parts, args.out, args.crf, track_path=args.track,
                   frame_range=frame_range, encoder_threads=args.encoder_threads)
    print(json.dumps(info, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
