#!/usr/bin/env python3
"""Render a replay film with several background Blenders at once, then join the parts.

    python scripts/replay3d/render_parallel.py runtime/replay3d/race_457.json --workers 3
    python scripts/replay3d/render_parallel.py runtime/replay3d/race_457.json --workers 2 --speed 30 --shots film

Why: Blender renders frames strictly one after another, and on the hut laptop a
single EEVEE render leaves the CPU at 20% and the integrated GPU at 50%. The
time between frames (scene sync, encoding, file writes) is single-threaded, so
two or three renders side by side get most of the machine's throughput.

Each worker runs ``build_scene.py --render --frames A-B`` on its own slice and
writes ``<prefix>partN_<A>-<B>.mp4`` plus a progress log. When all have finished
the parts are joined by copying their compressed frames straight into one file
(PyAV; no re-encode, a few seconds). Every option not listed here is passed
through to build_scene.py (``--shots``, ``--follow``, ``--boat-model``, ...).

``--frame-range 1-96`` renders only that stretch of the film, for a quick check
that the workers and the join behave before committing to the whole race.

Needs ``pip install av``.
"""
from __future__ import annotations

import argparse
import glob
import json
import math
import os
import re
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
BUILD = os.path.join(HERE, "build_scene.py")
COMPOSE = os.path.join(HERE, "compose_film.py")
sys.path.insert(0, HERE)
from replay_time import TimeWarp  # noqa: E402

FPS = 24  # must match build_scene.FPS


def plan_segments(data: Dict[str, Any], slow_step: int) -> List[Dict[str, int]]:
    """Split the film into stretches, each with the frame step it deserves.

    Where the replay runs at real time the boats crawl and the fixes behind them
    are half a minute apart, so every fifth frame held looks the same as every
    frame. Where it runs at 30x they cross the screen, and a held frame shows.
    """
    film = data.get("film") or {}
    if not film.get("total_frames"):
        return []
    warp = TimeWarp.from_dict(film)
    total = warp.total_frames
    slow = sorted((warp.frame_of(a), warp.frame_of(b)) for a, b in warp.windows)

    segments: List[Dict[str, int]] = []
    cursor = 1
    for f0, f1 in slow:
        f0, f1 = max(cursor, f0), min(total, f1)
        if f1 < f0:
            continue
        if f0 > cursor:
            segments.append({"start": cursor, "end": f0 - 1, "step": 1})
        segments.append({"start": f0, "end": f1, "step": max(1, slow_step)})
        cursor = f1 + 1
    if cursor <= total:
        segments.append({"start": cursor, "end": total, "step": 1})
    return segments


def split_jobs(segments: List[Dict[str, int]], workers: int, max_jobs: int = 12) -> List[Dict[str, int]]:
    """Chop the long stretches so the workers finish together.

    A chunk keeps its parent's step and starts on that step's own grid, so the
    composer can map a film frame to a rendered one with plain arithmetic.
    """
    def cost(seg: Dict[str, int]) -> int:
        return max(1, math.ceil((seg["end"] - seg["start"] + 1) / seg["step"]))

    total = sum(cost(s) for s in segments)
    target = max(60, total // max(1, min(max_jobs, workers * 3)))
    jobs: List[Dict[str, int]] = []
    for seg in segments:
        pieces = max(1, math.ceil(cost(seg) / target))
        if pieces == 1:
            jobs.append(dict(seg))
            continue
        rendered = cost(seg)
        per = math.ceil(rendered / pieces)
        start = seg["start"]
        while start <= seg["end"]:
            end = min(seg["end"], start + per * seg["step"] - 1)
            jobs.append({"start": start, "end": end, "step": seg["step"]})
            start = end + 1
    return jobs


def remove_quietly(paths: List[str], tries: int = 6) -> None:
    """Delete files a killed Blender may still be holding.

    Killing a process on Windows is not synchronous: its handles can outlive it
    by a moment, and a PermissionError here once took the whole run down with
    two healthy workers still going.
    """
    for path in paths:
        for attempt in range(tries):
            try:
                os.remove(path)
                break
            except FileNotFoundError:
                break
            except OSError:
                if attempt == tries - 1:
                    print(f"  could not remove {os.path.basename(path)}; leaving it", flush=True)
                else:
                    time.sleep(1.0)


def part_is_sound(path: str, start: int, end: int, step: int) -> bool:
    """Does this part hold every frame it was asked for, and does it open at all?

    A Blender killed by the display driver still leaves an .mp4 behind: the file
    exists, has size, and is unreadable because the index was never written. The
    existence of the file is therefore not evidence that the job succeeded, and
    trusting it once cost an hour's render at the compose step.
    """
    import av

    want = max(1, math.ceil((end - start + 1) / max(1, step)))
    try:
        with av.open(path) as container:
            got = container.streams.video[0].frames
    except Exception:
        return False
    return got >= want


def concat_mp4(parts: List[str], out_path: str) -> int:
    """Join MP4 parts rendered with identical settings by remuxing their packets.

    Every part starts on a keyframe (it is the first frame Blender wrote), so
    the packets can be copied with their timestamps shifted by the running
    total. Returns the number of frames written.
    """
    import av

    frames = 0
    offset = 0
    with av.open(out_path, "w") as out:
        out_stream = None
        for path in parts:
            with av.open(path) as src:
                vs = src.streams.video[0]
                if out_stream is None:
                    if hasattr(out, "add_stream_from_template"):
                        out_stream = out.add_stream_from_template(vs)
                    else:
                        out_stream = out.add_stream(template=vs)
                last_end = offset
                for packet in src.demux(vs):
                    if packet.dts is None:
                        continue
                    packet.pts += offset
                    packet.dts += offset
                    packet.stream = out_stream
                    last_end = max(last_end, packet.pts + (packet.duration or 0))
                    out.mux(packet)
                    frames += 1
                offset = last_end
    return frames


def _blender_runs(path: str) -> bool:
    """Does this path actually start Blender? Existing is not the same thing.

    Windows has two traps here and both surface as an unhelpful
    ``PermissionError: [WinError 5] Access is denied`` from CreateProcess,
    twenty lines into a traceback, after the job has already downloaded its
    tiles and clips:

    * a **directory** passes ``os.path.exists`` (and ``shutil.which``, which
      treats any existing path as executable), so ``BLENDER_BIN`` set to the
      install folder rather than the .exe inside it gets all the way to launch;
    * an **App Execution Alias** under ``WindowsApps`` is a zero-byte reparse
      point that exists whether or not the Store app behind it is installed or
      enabled, and executing a dead one is denied.

    So the only reliable test is to run it.
    """
    if not os.path.isfile(path):
        return False
    try:
        rc = subprocess.call([path, "--version"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        return False
    return rc == 0


def _blender_candidates() -> List[str]:
    """Everywhere Blender might be, best first."""
    out: List[str] = []
    env = (os.environ.get("BLENDER_BIN") or "").strip().strip('"')
    if env:
        out.append(env)
        # A kindness: BLENDER_BIN naming the install folder is the commonest
        # way to get this wrong, and the answer is right there inside it.
        out += [os.path.join(env, name) for name in ("blender.exe", "blender")]
    for name in ("blender.exe", "blender"):
        for d in os.environ.get("PATH", "").split(os.pathsep):
            if d:
                out.append(os.path.join(d, name))
    out.append(os.path.join(os.environ.get("LOCALAPPDATA", ""),
                            "Microsoft", "WindowsApps", "blender-launcher.exe"))
    seen, unique = set(), []
    for path in out:
        key = os.path.normcase(os.path.abspath(path))
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def _run_reporting(cmd: List[str], keep: str) -> Tuple[int, List[str]]:
    """Run Blender, echoing only the lines worth reading and keeping the last few.

    Blender says a great deal on the way up, so this pass used to send both
    streams to DEVNULL. That threw away the one thing worth having: the pass
    renders nothing, so it holds a single core and leaves the GPU idle, and on
    a full-length film that is minutes of complete silence after "writing the
    overlay track" -- which reads as a hang, and was reported as one. It does
    say where it has got to every couple of thousand frames.

    The tail is kept so a failure can say why. Previously it could only say
    that it had happened.
    """
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace", bufsize=1)
    tail: List[str] = []
    for line in proc.stdout:
        line = line.rstrip()
        if not line:
            continue
        tail = (tail + [line])[-12:]
        if keep in line:
            print(line if line.startswith(" ") else f"  {line}", flush=True)
    proc.wait()
    return proc.returncode, tail


def track_is_stale(track_path: str, json_path: str) -> bool:
    """Does the overlay track need writing again for this scene?

    Existence used to be the whole test, and that is wrong the moment a race is
    re-exported. Every screen position in the track belongs to one scene: after
    the Crackajack tracks were repaired and the race re-rendered in the same
    folder, the boats came out right and the name plates carried on following
    where the old fixes had put them, because the track was left over from the
    previous run.

    A track written before this change carries no fingerprint, so it is treated
    as stale and rewritten once. That costs one Blender pass and is the safe way
    round: reusing it is the failure that is hard to see.
    """
    if not os.path.exists(track_path):
        return True
    try:
        with open(track_path, "r", encoding="utf-8") as f:
            stamped = json.load(f).get("scene_sha")
    except (OSError, ValueError):
        return True
    if not stamped:
        print("  the overlay track predates this check; writing it again", flush=True)
        return True
    import hashlib

    h = hashlib.sha256()
    with open(json_path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    if stamped == h.hexdigest():
        return False
    print("  the scene has changed since the overlay track was written", flush=True)
    return True


def blender_path() -> Optional[str]:
    """The first candidate that actually starts Blender, or None."""
    for path in _blender_candidates():
        if _blender_runs(path):
            return path
    return None


def blender_not_found() -> str:
    """What to tell someone whose machine has no working Blender."""
    tried = []
    for path in _blender_candidates():
        if os.path.exists(path):
            why = "is a folder, not the executable" if os.path.isdir(path) else "will not run"
            tried.append("  " + path + "  (" + why + ")")
    message = ("Blender not found. Install Blender 5.2 and put it on PATH, or set "
               "BLENDER_BIN to blender.exe itself (not the folder it lives in).")
    if tried:
        message += chr(10) + "Tried:" + chr(10) + chr(10).join(tried)
    return message


def find_blender() -> str:
    """A Blender that will actually start, from BLENDER_BIN, PATH or the Store."""
    path = blender_path()
    if path:
        return path
    raise SystemExit(blender_not_found())


MAX_COMPOSE_SLICES = 8


def compose_in_parallel(json_path, manifest, out_path, track_path, first, last,
                        workers, compose_script, python_exe):
    """Compose the film in slices at once, then join them.

    The compose stage is one thread doing PIL work on a machine that has just
    finished using every core to render. Measured at about 41 ms a frame, an
    eleven thousand frame film spends eight minutes here -- which on a fast
    graphics card is a third of the whole job, for want of using the other
    fifteen cores.

    Each slice is encoded independently, so each one opens on a keyframe and the
    finished parts can be joined by copying packets rather than encoding a
    second time (concat_mp4, the same join the rendered parts already use).
    Frame numbers stay absolute, so a slice draws exactly what it would have
    drawn in one pass.
    """
    count = last - first + 1
    # Not the render's worker count: that one is sized for the graphics card and
    # four gigabytes a Blender. This stage is PIL on the CPU with the card idle,
    # so the dial is cores. Capped because each slice is a fresh interpreter
    # decoding its own way into a segment, and past a point that costs more than
    # it saves.
    cores = os.cpu_count() or 2
    workers = max(1, min(int(workers or 0), cores, MAX_COMPOSE_SLICES, count))
    workers = max(workers, min(cores, MAX_COMPOSE_SLICES, count))
    if workers == 1:
        return None                                  # let the caller do it inline
    # Between them the slices must not ask for more threads than the machine
    # has, or the saving goes on context switching.
    encoder_threads = max(1, cores // workers)

    # Whole slices, remainder spread over the first few, so no worker is left
    # with a handful of frames while another has a full share.
    size, extra = divmod(count, workers)
    bounds, cursor = [], first
    for i in range(workers):
        n = size + (1 if i < extra else 0)
        bounds.append((cursor, cursor + n - 1))
        cursor += n

    slices = []
    procs = []
    for i, (lo, hi) in enumerate(bounds):
        part = f"{os.path.splitext(out_path)[0]}.slice{i:02d}.mp4"
        slices.append(part)
        cmd = [python_exe, compose_script, "--json", json_path, "--parts", manifest,
               "--out", part, "--frames", f"{lo}-{hi}",
               "--encoder-threads", str(encoder_threads)]
        if track_path and os.path.exists(track_path):
            cmd += ["--track", track_path]
        procs.append((i, lo, hi, part,
                      subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)))

    print(f"  composing {count} frames as {workers} slices, "
          f"{encoder_threads} encoder thread(s) each", flush=True)
    failed = []
    for i, lo, hi, part, proc in procs:
        _out, err = proc.communicate()
        if proc.returncode != 0 or not os.path.exists(part):
            failed.append((i, lo, hi, (err or b"").decode("utf-8", "replace").strip()[-400:]))
    if failed:
        for i, lo, hi, err in failed:
            print(f"  slice {i} ({lo}-{hi}) failed: {err}", flush=True)
        remove_quietly(slices)
        return False

    frames = concat_mp4(slices, out_path)
    if frames != count:
        # Worth stopping for: a short film is the kind of fault that is only
        # noticed by whoever watches it to the end.
        print(f"  joined {frames} frames, expected {count}; the slices are kept", flush=True)
        return False
    remove_quietly(slices)
    return True


def frame_count(json_path: str, speed: float) -> int:
    """How long the film is. The plan written by prepare_video_frames.py wins,
    because a film with slow-motion windows is not a flat compression."""
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    film = data.get("film") or {}
    if film.get("total_frames"):
        return int(film["total_frames"])
    duration = float(data["time"]["duration_s"])
    return int(math.ceil(duration / speed * FPS)) + 1     # same formula as build_scene.build


def tail_progress(log_path: str) -> str:
    try:
        with open(log_path, "r", encoding="utf-8") as f:
            lines = [ln.strip() for ln in f if ln.strip()]
        return lines[-1][9:] if lines else "starting"
    except OSError:
        return "starting"


def progress_frame(log_path: str) -> Optional[int]:
    """The last frame a worker reported, for spotting one that has stopped moving."""
    try:
        with open(log_path, "r", encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return None
    hits = re.findall(r"frame (\d+)/", text)
    return int(hits[-1]) if hits else None


def job_frames(job: Dict[str, Any]) -> int:
    """How many frames a job actually renders: its stretch, at its step."""
    step = max(1, int(job.get("step", 1)))
    return (int(job["end"]) - int(job["start"])) // step + 1


def frames_done_in(job: Dict[str, Any]) -> int:
    """How far a running job has got, counted in frames it has rendered."""
    frame = progress_frame(job["log"])
    if frame is None:
        return 0
    step = max(1, int(job.get("step", 1)))
    return max(0, min(job_frames(job), (int(frame) - int(job["start"])) // step + 1))


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("json", help="race export from export_race.py")
    ap.add_argument("--workers", type=int, default=2, help="background Blenders to run at once")
    ap.add_argument("--speed", type=float, default=30.0, help="race seconds per video second (as build_scene)")
    ap.add_argument("--out", help="final MP4 prefix (default: <json dir>/renders/race_<id>_)")
    ap.add_argument("--keep-parts", action="store_true", help="leave the part files after joining")
    ap.add_argument("--frame-range", help="render only frames A-B of the film (smoke test)")
    ap.add_argument("--stall-minutes", type=float, default=6.0,
                    help="restart a worker that has not reported a new frame for this long (once)")
    ap.add_argument("--slow-step", type=int, default=5,
                    help="render every Nth frame where the film runs at real time, and hold it; "
                         "1 renders every frame")
    ap.add_argument("--overlay-track", action="store_true",
                    help="rewrite the overlay track even if one is already there")
    ap.add_argument("--no-composite", action="store_true",
                    help="draw the hut-camera panel in the 3D instead of laying it over afterwards")
    args, passthrough = ap.parse_known_args(argv)

    blender = find_blender()
    total = frame_count(args.json, args.speed)
    first = 1
    if args.frame_range:
        a, b = (int(v) for v in args.frame_range.split("-", 1))
        first, total = max(1, a), min(total, b)
    with open(args.json, "r", encoding="utf-8") as f:
        data = json.load(f)
    race_id = data["race"]["id"]
    # The panel is laid over the rendered film unless asked otherwise: it must
    # run at full rate even where the 3D behind it is stepped and held.
    composite = (not args.no_composite) and any(c.get("sequence") for c in (data.get("videos") or []))
    # Absolute: Blender resolves a relative render path against its own working directory.
    out_prefix = os.path.abspath(args.out or os.path.join(os.path.dirname(os.path.abspath(args.json)),
                                                         "renders", f"race_{race_id}_"))
    json_path = os.path.abspath(args.json)
    os.makedirs(os.path.dirname(out_prefix), exist_ok=True)

    # Jobs, not equal slices: the film is stepped where it runs at real time,
    # so a stretch of 3000 slow frames is a fifth of the work of 3000 fast ones.
    segments = plan_segments(data, args.slow_step) if args.slow_step > 1 else []
    segments = [s for s in segments if s["end"] >= first and s["start"] <= total]
    for seg in segments:
        seg["start"], seg["end"] = max(seg["start"], first), min(seg["end"], total)
    if not segments:
        segments = [{"start": first, "end": total, "step": 1}]
    jobs = split_jobs(segments, args.workers)
    rendered = sum(math.ceil((j["end"] - j["start"] + 1) / j["step"]) for j in jobs)
    count = total - first + 1
    print(f"frames {first}-{total}: {rendered} to render across {len(jobs)} jobs, "
          f"{args.workers} at a time, using {os.path.basename(blender)}")
    if composite:
        print(f"  the hut-camera panel is composited afterwards, at the full {FPS} fps")

    # Names, mark numbers, clock, course board and cards are drawn in pixels
    # afterwards rather than modelled in the scene (see overlay.py). Only
    # Blender knows where each camera was pointing, so one short pass writes
    # the screen position of every boat and mark, frame by frame, up front.
    track_path = os.path.splitext(json_path)[0] + "_overlay.json"
    if args.overlay_track or track_is_stale(track_path, json_path):
        print("  writing the overlay track", flush=True)
        rc, tail = _run_reporting(
            [blender, "-b", "--python", BUILD, "--", json_path,
             "--speed", str(args.speed), "--overlay-track", track_path] + passthrough,
            keep="overlay track")
        if rc != 0 or not os.path.exists(track_path):
            print("  overlay track failed; names and marks will be missing from the film")
            for line in tail:
                print(f"    {line}")

    def launch(k: int, job: Dict[str, int]) -> subprocess.Popen:
        prefix = f"{out_prefix}part{k:02d}_"
        cmd = [blender, "-b", "--python", BUILD, "--", json_path, "--speed", str(args.speed),
               "--render", "--frames", f"{job['start']}-{job['end']}", "--out", prefix]
        if job["step"] > 1:
            cmd += ["--step", str(job["step"])]
        if not composite:
            # The panel is only modelled in the scene when it is not being laid
            # over afterwards; rendering it twice is wasted work, and stepped
            # frames would turn the footage into a slideshow.
            cmd.append("--with-video")
        return subprocess.Popen(cmd + passthrough)

    for k, job in enumerate(jobs):
        job["prefix"] = f"{out_prefix}part{k:02d}_"
        job["log"] = job["prefix"].rstrip("_-") + ".progress.log"
        job["index"] = k
        remove_quietly(glob.glob(job["prefix"] + "*"))
        step_note = f" every {job['step']}" if job["step"] > 1 else ""
        print(f"  job {k:>2}: frames {job['start']}-{job['end']}{step_note}")

    # Run them a few at a time. A worker can also die without exiting: on
    # integrated graphics a second Blender has crashed inside the display driver
    # and left the process alive, idle and silent, so a job that stops reporting
    # frames is killed and tried once more.
    started = time.time()
    planned_frames = sum(job_frames(j) for j in jobs)
    stall_s = max(60.0, args.stall_minutes * 60.0)
    pending = list(jobs)
    running: List[Dict[str, Any]] = []
    done: List[Dict[str, Any]] = []
    failed: List[int] = []
    def fill() -> None:
        """Start jobs until the workers are busy. Called again the moment one ends,
        so a finished job's slot is not left idle until the next poll."""
        while pending and len(running) < args.workers:
            job = pending.pop(0)
            job["proc"] = launch(job["index"], job)
            job["seen"] = (None, time.time())
            job["retried"] = job.get("retried", False)
            running.append(job)

    while pending or running:
        fill()
        time.sleep(20)
        now = time.time()
        for job in list(running):
            code = job["proc"].poll()
            if code is not None:
                running.remove(job)
                found = glob.glob(job["prefix"] + "*.mp4")
                if found and part_is_sound(found[0], job["start"], job["end"], job["step"]):
                    done.append(job)
                elif not job["retried"]:
                    job["retried"] = True
                    remove_quietly(found)
                    pending.insert(0, job)
                    print(f"  job {job['index']} produced nothing usable; retrying it", flush=True)
                else:
                    failed.append(job["index"])
                continue
            frame = progress_frame(job["log"])
            if frame != job["seen"][0]:
                job["seen"] = (frame, now)
            elif now - job["seen"][1] > stall_s:
                job["proc"].kill()
                running.remove(job)
                time.sleep(2.0)                       # let the handles go before deleting
                remove_quietly(glob.glob(job["prefix"] + "*"))
                if job["retried"]:
                    print(f"  job {job['index']} stalled twice at frame {frame}; giving up on it", flush=True)
                    failed.append(job["index"])
                else:
                    job["retried"] = True
                    pending.insert(0, job)
                    print(f"  job {job['index']} stopped at frame {frame} for "
                          f"{(now - job['seen'][1]) / 60:.0f} min; restarting it", flush=True)
        fill()
        if running:
            status = " | ".join(f"j{j['index']}: {tail_progress(j['log'])}" for j in running)
            # Frames first, because that is what moves. Jobs are a handful of
            # very unequal lumps -- the four for an eight-minute film sat at
            # "0/4 done" for twenty minutes and the race page duly reported 5%
            # and eight hours remaining -- so anything watching this should
            # count frames and treat the job tally as a footnote.
            made = sum(job_frames(j) for j in done) + sum(frames_done_in(j) for j in running)
            print(f"[{(now - started) / 60:5.1f} min] {made}/{planned_frames} frames "
                  f"| {len(done)}/{len(jobs)} done | {status}", flush=True)
    if failed:
        print(f"jobs failed: {sorted(set(failed))}; see the progress logs")
        return 1

    parts = []
    for job in sorted(done, key=lambda j: j["start"]):
        found = glob.glob(job["prefix"] + "*.mp4")
        parts.append({"file": found[0], "start": job["start"], "end": job["end"], "step": job["step"]})
    render_minutes = (time.time() - started) / 60
    print(f"all {len(parts)} parts rendered in {render_minutes:.1f} min", flush=True)

    final = f"{out_prefix}{first:04d}-{total:04d}.mp4"
    if composite or any(p["step"] > 1 for p in parts) or os.path.exists(track_path):
        # Hold the stepped frames out to full rate and lay the hut camera on top.
        manifest = f"{out_prefix}parts.json"
        with open(manifest, "w", encoding="utf-8") as f:
            json.dump(parts, f, indent=1)
        print("composing", flush=True)
        # As many slices as the render used workers. The compose stage is pure
        # CPU and the graphics card is idle by now, so there is no reason for it
        # to be the one part of this that uses a single core.
        done = compose_in_parallel(json_path, manifest, final, track_path, first, total,
                                   args.workers, COMPOSE, sys.executable)
        if done is False:
            print("compose failed; the parts are kept")
            return 1
        if done is None:
            cmd = [sys.executable, COMPOSE, "--json", json_path,
                   "--parts", manifest, "--out", final]
            if os.path.exists(track_path):
                cmd += ["--track", track_path]
            if subprocess.call(cmd) != 0:
                print("compose failed; the parts are kept")
                return 1
        if not args.keep_parts:
            remove_quietly([p["file"] for p in parts] + [manifest])
    else:
        try:
            frames = concat_mp4([p["file"] for p in parts], final)
        except Exception as ex:  # keep the parts: they are an hour of work
            print(f"join failed ({ex!r}); the parts are kept")
            return 1
        if frames != count:
            print(f"warning: joined {frames} frames, expected {count}")
        if not args.keep_parts:
            remove_quietly([p["file"] for p in parts])
    print(f"film: {final}  ({(time.time() - started) / 60:.1f} min total)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
