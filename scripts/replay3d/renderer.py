#!/usr/bin/env python3
"""The render machine: take jobs out of the bucket, make films, put them back.

    python scripts/replay3d/renderer.py --once      # one job, then stop
    python scripts/replay3d/renderer.py             # keep watching

This runs nowhere near the hut. The hut PC cannot render a film in a useful
time and would be competing with the racing for the same machine, so it writes
a job into the club's video bucket and stops. This polls that bucket, does the
work, and writes the film back beside the race videos.

Nothing here can reach the hut and nothing needs to. The only connection is the
bucket, which means a render machine can be a laptop under a desk or a cloud
box that exists for two hours and disappears, with no port opened, no tunnel
and no account on the race office app.

What it needs:

    R2_ACCOUNT_ID, R2_BUCKET, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY
    R2_PUBLIC_BASE_URL      where the bucket is served from, for the film's address
    MAPBOX_TOKEN            for satellite tiles it has not cached yet
    BLENDER_BIN             optional; found on PATH otherwise

The Mapbox token and the bucket keys stay here. Neither may travel in a job,
because a job is publicly readable.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import traceback
from typing import Any, Dict, List, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
sys.path.insert(0, _HERE)
sys.path.insert(0, _ROOT)

import assets  # noqa: E402
import render_parallel  # noqa: E402  (beside this script)
from branding import (  # noqa: E402  (beside this script)
    BRANDING_MANIFEST_URL,
    branding_from_api,
)
from fonts import ensure_fonts  # noqa: E402  (beside this script)
from core import r2  # noqa: E402  (a standalone S3 signer; no app, no database)
from core.replay3d_protocol import (  # noqa: E402  (the bucket contract, shared with the hut)
    HEARTBEAT_KEY,
    JOBS_PREFIX,
    blank_status,
    film_key,
    job_key,
    status_is_stale,
    status_key,
)

BUILD = os.path.join(_HERE, "build_scene.py")
PREPARE = os.path.join(_HERE, "prepare_video_frames.py")
PARALLEL = os.path.join(_HERE, "render_parallel.py")
WORK_DIR = os.path.join(_ROOT, "runtime", "replay3d", "jobs")

POLL_SECONDS = 30.0
HEARTBEAT_SECONDS = 60.0
# How often progress goes back to the bucket. A render is hours long and the
# dashboard card refreshes every few seconds, so half a minute is already finer
# than anything anybody watches.
PROGRESS_SECONDS = 30.0


# ---------------------------------------------------------------------------
# Talking to the bucket.

class Store:
    def __init__(self) -> None:
        self.account_id = os.environ.get("R2_ACCOUNT_ID", "").strip()
        self.bucket = os.environ.get("R2_BUCKET", "").strip()
        self.access_key = os.environ.get("R2_ACCESS_KEY_ID", "").strip()
        self.secret_key = os.environ.get("R2_SECRET_ACCESS_KEY", "").strip()
        self.public_base = os.environ.get("R2_PUBLIC_BASE_URL", "").strip().rstrip("/")
        missing = [n for n, v in (("R2_ACCOUNT_ID", self.account_id), ("R2_BUCKET", self.bucket),
                                  ("R2_ACCESS_KEY_ID", self.access_key),
                                  ("R2_SECRET_ACCESS_KEY", self.secret_key)) if not v]
        if missing:
            raise SystemExit(f"missing environment: {', '.join(missing)}")

    def _args(self):
        return self.account_id, self.bucket

    def get_json(self, key: str) -> Optional[Dict[str, Any]]:
        return r2.get_json(self.account_id, self.bucket, key, self.access_key, self.secret_key)

    def put_json(self, key: str, value: Dict[str, Any]) -> None:
        r2.put_object(self.account_id, self.bucket, key,
                      json.dumps(value, separators=(",", ":")).encode("utf-8"),
                      self.access_key, self.secret_key, content_type="application/json")

    def put_file(self, key: str, path: str, content_type: str) -> None:
        from pathlib import Path
        r2.put_file(self.account_id, self.bucket, key, Path(path),
                    self.access_key, self.secret_key, content_type=content_type,
                    cache_control="public, max-age=86400")

    def delete(self, key: str) -> None:
        try:
            r2.delete_object(self.account_id, self.bucket, key, self.access_key, self.secret_key)
        except Exception:
            pass

    def list_jobs(self) -> List[str]:
        objects = r2.list_objects(self.account_id, self.bucket, JOBS_PREFIX + "/",
                                  self.access_key, self.secret_key)
        return sorted(o["key"] for o in objects if str(o.get("key", "")).endswith(".json"))

    def public_url(self, key: str) -> str:
        from urllib.parse import quote
        return self.public_base + "/" + "/".join(quote(p, safe="") for p in key.split("/"))


# ---------------------------------------------------------------------------
# One job.

class Progress:
    """Keeps the status object in the bucket roughly up to date."""

    def __init__(self, store: Store, race_id: int, who: str) -> None:
        self.store, self.race_id, self.who = store, race_id, who
        self.status = blank_status(race_id, "claimed", "starting")
        self.status["renderer"] = who
        self.last_put = 0.0
        self.last_beat = 0.0
        self.started = time.time()

    def set(self, state: str, message: str = "", progress: Optional[float] = None,
            force: bool = False, **extra: Any) -> None:
        self.status["state"] = state
        self.status["message"] = message or state
        if progress is not None:
            self.status["progress"] = max(0.0, min(1.0, float(progress)))
            done = self.status["progress"]
            elapsed = time.time() - self.started
            self.status["eta_s"] = round(elapsed / done - elapsed) if done > 0.02 else None
        self.status.update(extra)
        self.status["updated_at"] = time.time()
        if force or (time.time() - self.last_put) >= PROGRESS_SECONDS:
            try:
                self.store.put_json(status_key(self.race_id), self.status)
                self.last_put = time.time()
            except Exception as exc:
                print(f"  (could not write status: {exc})", flush=True)
        # The loop cannot beat while it is inside a job, and a job is hours
        # long, so the heartbeat has to come from in here. Without this the
        # dashboard says no render machine has checked in for the whole of a
        # render, which is the one moment it should say the opposite.
        if (time.time() - self.last_beat) >= HEARTBEAT_SECONDS:
            heartbeat(self.store, self.who, busy_with=self.race_id)
            self.last_beat = time.time()


# Frames, not jobs: render_parallel splits a film into a few lumps of very
# different sizes, so the job tally is nearly useless as progress.
_DONE_RE = re.compile(r"\]\s*(\d+)/(\d+)\s+frames")
_COMPOSED_RE = re.compile(r"composed\s+(\d+)/(\d+)")


def _run(cmd: List[str], progress: Progress, stage: str, lo: float, hi: float) -> None:
    """Run a step, turning whatever it prints into a fraction between lo and hi."""
    print("  $ " + " ".join(os.path.basename(c) if c.endswith(".py") else c for c in cmd), flush=True)
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace", bufsize=1)
    tail: List[str] = []
    for line in proc.stdout:
        line = line.rstrip()
        tail = (tail + [line])[-30:]
        m = _DONE_RE.search(line) or _COMPOSED_RE.search(line)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            if b:
                progress.set(stage, f"{stage} {a * 100 // b}%", lo + (hi - lo) * a / b,
                             frames_done=a, frames_total=b)
    proc.wait()
    if proc.returncode != 0:
        raise RuntimeError(f"{os.path.basename(cmd[1] if len(cmd) > 1 else cmd[0])} "
                           f"exited {proc.returncode}:\n" + "\n".join(tail[-12:]))


def fetch_clips(scene: Dict[str, Any], work: str) -> int:
    """Download the published start and finish clips this scene refers to."""
    import urllib.parse
    import urllib.request

    got = 0
    for clip in scene.get("videos") or []:
        url = clip.get("url") or ""
        if not url:
            continue
        name = os.path.basename(urllib.parse.urlparse(url).path)
        dest = os.path.join(work, "video", name)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        if not os.path.exists(dest) or os.path.getsize(dest) == 0:
            req = urllib.request.Request(url, headers={"User-Agent": "pwllheli-renderer/1.0"})
            with urllib.request.urlopen(req, timeout=300) as resp, open(dest + ".part", "wb") as f:
                shutil.copyfileobj(resp, f)
            os.replace(dest + ".part", dest)
        clip["file"] = os.path.relpath(dest, work).replace("\\", "/")
        got += 1
    return got


def run_job(store: Store, job: Dict[str, Any], who: str, blender: str) -> str:
    """Everything for one race, from a fetched job to a film in the bucket."""
    meta = job.get("job") or {}
    race_id = int(meta.get("race_id") or (job.get("race") or {}).get("id") or 0)
    render = meta.get("render") or {}
    work = os.path.join(WORK_DIR, f"race_{race_id}")
    os.makedirs(work, exist_ok=True)
    progress = Progress(store, race_id, who)
    progress.set("claimed", "preparing", force=True)

    # The overlay looks for TTFs beside the scene file and quietly falls back
    # to PIL's default bitmap face when they are not there. That fallback is
    # not obvious in a log and very obvious in the film, so unpack them first.
    fonts = ensure_fonts(work)
    if fonts.get("skipped"):
        print(f"  FONTS: {fonts['skipped']} - the film will be set in the wrong face",
              flush=True)

    scene = {k: v for k, v in job.items() if k != "job"}

    print(f"  land for race {race_id}", flush=True)
    token = os.environ.get("MAPBOX_TOKEN", "").strip() or None
    bucket = assets.Bucket(store.account_id, store.bucket, store.access_key, store.secret_key)
    try:
        land = assets.land_for_scene(scene, work, bucket, token=token)
        if land:
            scene["terrain"] = land
    except Exception as exc:
        # A film with sea and boats beats no film. Say so and carry on.
        print(f"  land: skipped ({type(exc).__name__}: {exc})", flush=True)

    # The club mark and the sponsors, from the club's own public manifest.
    # Not carried in the job: it is read now, which may be hours later, and a
    # sponsor added this morning should be on this afternoon's film.
    # The hut names its own public address; falling back to the club's usual
    # one means a render machine still gets the logos when the hut has no
    # public base URL configured, which is the normal state in development.
    manifest = (meta.get("branding_manifest") or "").strip() or BRANDING_MANIFEST_URL
    if manifest:
        try:
            brand = branding_from_api(os.path.join(work, "branding"), manifest)
            if brand:
                scene["branding"] = brand
        except Exception as exc:
            # An unbranded film is still a film.
            print(f"  branding: skipped ({type(exc).__name__}: {exc})", flush=True)
    else:
        print("  branding: no manifest URL, so the film carries no logos "
              "(set the public base URL in Settings -> Web server)", flush=True)

    clips = fetch_clips(scene, work)
    print(f"  {clips} clip(s) downloaded", flush=True)

    scene_path = os.path.join(work, f"race_{race_id}.json")
    with open(scene_path, "w", encoding="utf-8") as f:
        json.dump(scene, f, separators=(",", ":"))

    # The film plan has to exist before anything renders: it decides the frame
    # count and cuts the hut footage against the same clock.
    progress.set("rendering", "planning the film", 0.02, force=True)
    _run([sys.executable, PREPARE, "--json", scene_path,
          "--speed", str(render.get("speed", 30.0))], progress, "rendering", 0.02, 0.05)

    out_prefix = os.path.join(work, "renders", f"race_{race_id}_")
    os.makedirs(os.path.dirname(out_prefix), exist_ok=True)
    cmd = [sys.executable, PARALLEL, scene_path,
           "--workers", os.environ.get("RENDER_WORKERS", "2"),
           "--slow-step", str(render.get("slow_step", 5)),
           "--speed", str(render.get("speed", 30.0)),
           "--out", out_prefix]
    # Commissioning a new render machine should not mean waiting two hours to
    # find out the bucket credentials are wrong. This renders a slice instead.
    if os.environ.get("RENDER_FRAME_RANGE", "").strip():
        cmd += ["--frame-range", os.environ["RENDER_FRAME_RANGE"].strip()]
        print(f"  NOTE: RENDER_FRAME_RANGE={os.environ['RENDER_FRAME_RANGE']}, "
              f"this is a smoke test and not the whole film", flush=True)
    _run(cmd, progress, "rendering", 0.05, 0.97)

    # Newest wins, not last alphabetically. A re-render leaves the previous
    # film beside the new one -- race_457_0001-0120.mp4 from a smoke test next
    # to race_457_0001-1200.mp4 from the real thing -- and which of those sorts
    # last is an accident of the frame numbers, not of which one we just made.
    renders = os.path.dirname(out_prefix)
    films = [f for f in os.listdir(renders) if f.endswith(".mp4") and "part" not in f]
    if not films:
        raise RuntimeError("the render produced no film")
    film = max((os.path.join(renders, f) for f in films), key=os.path.getmtime)
    size_mb = os.path.getsize(film) / 1048576

    key = meta.get("out_key") or film_key(race_id)
    progress.set("uploading", f"uploading {size_mb:.0f} MB", 0.98, force=True)
    # Retried, because by this point the film has cost two hours and the thing
    # most likely to go wrong is a few seconds of network. Seen for real: a DNS
    # failure on the last step threw all of it away.
    for attempt in range(1, 4):
        try:
            store.put_file(key, film, "video/mp4")
            break
        except Exception as exc:
            if attempt == 3:
                raise
            wait = 10 * attempt
            print(f"  upload attempt {attempt} failed ({exc}); retrying in {wait}s", flush=True)
            progress.set("uploading", f"upload failed, retrying ({attempt}/3)", 0.98, force=True)
            time.sleep(wait)

    # A version token, because the key is just the race id and the film is
    # served with a day of cache (see Store.put_file). Without this a
    # re-render is invisible: R2 has the new film, the CDN keeps handing out
    # the old one until tomorrow, and it looks for all the world like the
    # upload failed. Seen for real on race 457 -- 9.4 MB in the bucket, 1.2 MB
    # coming back from the URL, cf-cache-status: HIT. The token changes the
    # cache key, so the film stays cacheable and a new one still shows up now.
    url = f"{store.public_url(key)}?v={int(time.time())}"
    progress.set("done", "the film is ready", 1.0, force=True, film_url=url)
    # The job is done with. Leaving it would have the next poll pick it up again.
    store.delete(job_key(race_id))
    return url


# ---------------------------------------------------------------------------
# The loop.

def heartbeat(store: Store, who: str, busy_with: Optional[int] = None) -> None:
    try:
        store.put_json(HEARTBEAT_KEY, {"version": 1, "renderer": who, "updated_at": time.time(),
                                       "busy_with": busy_with})
    except Exception as exc:
        print(f"heartbeat failed: {exc}", flush=True)


def claimable(store: Store, race_id: int) -> bool:
    """True when nobody else is working on this race.

    A status in a moving state that has kept reporting belongs to a live
    renderer. One that stopped reporting belongs to a dead one, and that job is
    fair game again -- otherwise a crash would strand a race for ever.
    """
    status = store.get_json(status_key(race_id))
    if not status or status.get("state") in ("queued", "failed"):
        return True
    if status.get("state") == "done":
        return False
    return status_is_stale(status)


def poll_once(store: Store, who: str, blender: str) -> bool:
    """Do at most one job. Returns whether there was one."""
    for key in store.list_jobs():
        job = store.get_json(key)
        if not job:
            continue
        race_id = int((job.get("job") or {}).get("race_id") or 0)
        if not race_id or not claimable(store, race_id):
            continue
        print(f"\n=== race {race_id}: {(job.get('race') or {}).get('name', '')}", flush=True)
        started = time.time()
        try:
            url = run_job(store, job, who, blender)
            print(f"=== done in {(time.time() - started) / 60:.0f} min -> {url}", flush=True)
        except Exception as exc:
            traceback.print_exc()
            status = blank_status(race_id, "failed", "the render failed")
            status.update({"renderer": who, "error": f"{type(exc).__name__}: {exc}"[:500],
                           "updated_at": time.time()})
            try:
                store.put_json(status_key(race_id), status)
            except Exception:
                pass
        return True
    return False


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--once", action="store_true", help="do one job (or nothing) and stop")
    ap.add_argument("--poll", type=float, default=POLL_SECONDS, help="seconds between looks")
    ap.add_argument("--name", default="", help="how this machine names itself in the status")
    args = ap.parse_args(argv)

    store = Store()
    who = args.name or f"{socket.gethostname()}"
    # Resolved here, at startup, rather than left to the first job. A render
    # machine with no working Blender is not a render machine, and finding
    # that out after it has claimed a race, fetched its tiles and downloaded
    # the clips wastes both the setup and the race officer's afternoon.
    # shutil.which is no good for this: on Windows it happily returns a
    # directory, which then fails as "Access is denied" from CreateProcess.
    try:
        blender = render_parallel.find_blender()
    except SystemExit as exc:
        print(str(exc), flush=True)
        return 1
    print(f"renderer '{who}' watching {store.bucket}/{JOBS_PREFIX} (blender: {blender})",
          flush=True)

    last_beat = 0.0
    while True:
        if time.time() - last_beat >= HEARTBEAT_SECONDS:
            heartbeat(store, who)
            last_beat = time.time()
        try:
            did = poll_once(store, who, blender)
        except Exception as exc:
            print(f"poll failed: {type(exc).__name__}: {exc}", flush=True)
            did = False
        if args.once:
            return 0
        if not did:
            time.sleep(max(5.0, args.poll))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
