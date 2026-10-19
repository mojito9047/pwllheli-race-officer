"""A photograph of the start line at each start signal, for the public race page.

Competitors want to know straight away whether they were over the line. The
start video cannot tell them: it is cut from the recording a minute after the
gun, encoded for minutes more, and kept off the public page until the race has
finished. So at each start signal the hut asks the camera for a still of its
own -- 3840 x 2160, made from the sensor rather than out of compressed video,
and in the hut's hands within about a second -- draws the start line and the
club logo on it, and puts it on the public race page a few seconds after the
gun. Unlike the videos it is public at once: early feedback is the point.

If the camera does not answer (no address or login saved, the login refused,
the camera busy), the same moment is cut out of the recording instead, once
the 20-second segment holding it has closed: later and softer, but still the
start. The camera's own still is kept unaltered beside the published copy, for
the race officer.

The camera is the one under Settings -> Camera zoom / PTZ presets: its address
and login are used here whether or not automatic preset switching is on. One
start photo is booked with each start clip, so starts, postponements and
pursuit start times all follow the start video's own rules; a photo whose start
was moved or postponed while it waited is not taken.
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core import appstate, startline
from core.activitylog import log_activity
from core.classconfig import race_start_schedule
from core.db import get_db, init_db, row_get
from core.races import get_entries, get_race
from core.settings import int_in_range, text_to_bool
from core.timeutils import parse_dt
from core.video import (
    background_priority,
    build_branding_overlay_filter_for_size,
    ffmpeg_executable,
    iter_video_buffer_segments,
    jpeg_dimensions,
    matching_video_segments,
    normalise_ptz_auth_mode,
    normalise_video_public_provider,
    camera_login_refused,
    pause_ptz_after_auth_failure,
    safe_ptz_camera_base_url,
    safe_r2_key_prefix,
    upload_file_to_r2,
    video_config,
    video_public_r2_ready,
    video_segment_start,
)

# The camera is asked at the start signal itself. Asked any later than this,
# what it shows is no longer the start, and the recording has the real moment.
CAMERA_LATE_LIMIT_S = 2.0
CAMERA_TIMEOUT_S = 5
# Before the gun, the camera is asked for a still at each of these many
# seconds before the start, only to find the buoy: at the gun itself the fleet
# is often in front of it. Kept clear of the last seconds, which belong to the
# photo itself.
PRE_START_SAMPLES_S = (90.0, 60.0, 30.0)
# The camera's own JPEG is about 1.2 MB at 3840 x 2160. Re-encoded with the
# line and logo at 3 it stays about the same size and keeps the detail.
START_PHOTO_JPEG_QUALITY = 3
UPLOAD_ATTEMPTS = 3

_ARMED: set = set()
# Photos this process has finished with, one way or another. The start sequence
# books every start clip again on each tick, so the answer for these comes from
# here rather than from the database once a second.
_SETTLED: set = set()
_ARMED_LOCK = threading.Lock()


def start_photo_enabled(cfg: Dict[str, Any]) -> bool:
    """Start photos ride on the recorder's start clips, so both must be on."""
    return bool(cfg.get("video_enabled")) and bool(cfg.get("video_start_photo_enabled"))


def camera_snapshot_url(cfg: Dict[str, Any]) -> str:
    """The camera's own still: Hikvision ISAPI, main stream of the channel."""
    base = safe_ptz_camera_base_url(str(cfg.get("ptz_camera_url") or ""))
    if not base:
        return ""
    channel = int_in_range(cfg.get("ptz_channel"), 1, 1, 32)
    return f"{base}/ISAPI/Streaming/channels/{channel}01/picture"


def camera_problem(cfg: Dict[str, Any]) -> str:
    """Why the camera cannot be asked for a still, or "" when it can."""
    if not camera_snapshot_url(cfg):
        return "no camera address is saved under Camera zoom / PTZ presets"
    if not str(cfg.get("ptz_username") or "").strip():
        return "no camera login is saved under Camera zoom / PTZ presets"
    if camera_login_refused():
        return "the camera login was refused earlier, and the camera is left alone so it does not lock the account"
    return ""


def _is_jpeg(path: Path) -> bool:
    """A whole JPEG: its start marker, and its end one -- a still cut off on
    the way from the camera has the first and not the second."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(3)
            fh.seek(-2, os.SEEK_END)
            tail = fh.read(2)
        return head == b"\xff\xd8\xff" and tail == b"\xff\xd9"
    except OSError:
        return False


def _refused(code: int) -> None:
    """Stop asking a camera that refuses the login: Hikvision locks the account."""
    pause_ptz_after_auth_failure({
        "ok": False,
        "enabled": True,
        "http_status": code,
        "message": f"The camera refused the start photo's login (HTTP {code}).",
    })


def _curl_snapshot(url: str, username: str, password: str, auth_mode: str, dest: Path) -> int:
    """GET the still with curl, the way the hut first proved the camera works."""
    curl_path = shutil.which("curl.exe") or shutil.which("curl")
    if not curl_path:
        raise RuntimeError("curl was not found")
    flag = {"digest": "--digest", "basic": "--basic", "anyauth": "--anyauth"}.get(auth_mode, "--digest")
    cmd = [curl_path, flag, "--user", f"{username}:{password}", "--silent", "--show-error",
           "--output", str(dest), "--write-out", "%{http_code}", "--max-time", str(CAMERA_TIMEOUT_S), url]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=CAMERA_TIMEOUT_S + 2)
    if result.returncode != 0:
        raise RuntimeError((result.stderr or f"curl exited {result.returncode}").strip()[:200])
    try:
        return int((result.stdout or "").strip()[-3:])
    except ValueError:
        raise RuntimeError("curl did not report an HTTP status")


def _urllib_snapshot(url: str, username: str, password: str, auth_mode: str, dest: Path) -> int:
    manager = urllib.request.HTTPPasswordMgrWithDefaultRealm()
    manager.add_password(None, url, username, password)
    handlers = []
    if auth_mode in ("digest", "anyauth"):
        handlers.append(urllib.request.HTTPDigestAuthHandler(manager))
    if auth_mode in ("basic", "anyauth"):
        handlers.append(urllib.request.HTTPBasicAuthHandler(manager))
    opener = urllib.request.build_opener(*handlers)
    try:
        with opener.open(urllib.request.Request(url), timeout=CAMERA_TIMEOUT_S) as response:
            dest.write_bytes(response.read())
            return int(getattr(response, "status", response.getcode()))
    except urllib.error.HTTPError as exc:
        return int(exc.code)


def take_camera_snapshot(cfg: Dict[str, Any], dest: Path) -> Tuple[float, float]:
    """Ask the camera for a still now. Returns (asked, answered) as epoch seconds.

    The picture is made between the two -- on a LAN, a few tenths of a second.
    """
    problem = camera_problem(cfg)
    if problem:
        raise RuntimeError(problem)
    url = camera_snapshot_url(cfg)
    username = str(cfg.get("ptz_username") or "").strip()
    password = str(cfg.get("ptz_password") or "")
    mode = normalise_ptz_auth_mode(cfg.get("ptz_auth_mode"))
    asked = time.time()
    try:
        code = _curl_snapshot(url, username, password, mode, dest)
    except Exception as curl_exc:
        try:
            code = _urllib_snapshot(url, username, password, mode, dest)
        except Exception:
            raise RuntimeError(f"the camera did not answer: {curl_exc}")
    answered = time.time()
    if code == 401:
        _refused(code)
        raise RuntimeError("the camera refused the login (HTTP 401)")
    if code == 403:
        raise RuntimeError("the camera accepted the login but refused the photo (HTTP 403): "
                           "check this camera user may view live video")
    if code != 200:
        raise RuntimeError(f"the camera answered HTTP {code}")
    if not _is_jpeg(dest):
        raise RuntimeError("the camera's answer was not a photograph")
    return asked, answered


def frame_from_recording(cfg: Dict[str, Any], ffmpeg: str, event_dt: datetime, dest: Path, work: Path) -> List[Any]:
    """Cut the frame at the start signal out of the rolling recording.

    The segment holding the start is only whole once the next one has begun,
    up to a segment (20 s at the hut) after the gun. The one before it is taken
    as well, so there is always a keyframe ahead of the start to decode from.
    """
    segment_s = int(cfg.get("video_segment_seconds") or 5)
    event_ts = event_dt.timestamp()
    deadline = time.time() + segment_s + 20
    while True:
        if any((video_segment_start(p) or datetime.min).timestamp() >= event_ts + 1
               for p in iter_video_buffer_segments() if video_segment_start(p)):
            break
        if time.time() > deadline:
            raise RuntimeError("the recording did not move past the start signal")
        time.sleep(1)
    segments = matching_video_segments(event_dt - timedelta(seconds=segment_s), event_dt + timedelta(seconds=1), cfg)
    first = video_segment_start(segments[0]) if segments else None
    if not first:
        raise RuntimeError("no recording covers the start signal")
    listing = work / "segments.txt"
    joined = work / "start.mp4"
    listing.write_text("".join(f"file '{p.resolve().as_posix()}'\n" for p in segments), encoding="utf-8")
    subprocess.run([ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-fflags", "+genpts",
                    "-f", "concat", "-safe", "0", "-i", str(listing), "-map", "0:v:0", "-an", "-c", "copy",
                    "-avoid_negative_ts", "make_zero", "-movflags", "+faststart", str(joined)],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, timeout=120)
    offset = max(0.0, (event_dt - first).total_seconds())
    subprocess.run([ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{offset:.3f}",
                    "-i", str(joined), "-frames:v", "1", "-q:v", "2", str(dest)],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, timeout=120,
                   **background_priority())
    if not _is_jpeg(dest):
        raise RuntimeError("no frame could be cut from the recording")
    # Where the buoy was in the seconds before the gun, for when the fleet is
    # in front of it at the gun. Returned rather than drawn: see compose.
    prior = []
    if startline.startline_overlay_enabled(cfg):
        for lead in (15.0, 8.0):
            if offset - lead < 0.5:
                continue
            earlier = work / f"before_{int(lead)}.jpg"
            try:
                subprocess.run([ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{offset - lead:.3f}",
                                "-i", str(joined), "-frames:v", "1", "-q:v", "3", str(earlier)],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, timeout=120,
                               **background_priority())
                prior.append(startline.detect_buoy(str(earlier)))
            except Exception:
                pass
    return prior


def compose_start_photo(ffmpeg: Optional[str], raw: Path, out: Path, cfg: Dict[str, Any], work: Path,
                        prior: Any = ()) -> Tuple[bool, str]:
    """The published copy: the photo with the start line and the club logo.

    No sponsor logos -- this is for reading the line. Returns (line drawn,
    what the line detector said). With neither line nor logo the camera's own
    file is published unchanged rather than encoded again for nothing.
    ``prior``: where the buoy was found in other frames of the same view (see
    ``startline.buoy_consensus``) -- at the gun the fleet is often in front of it.
    """
    spec, note = (None, "start line drawing is off")
    if startline.startline_overlay_enabled(cfg):
        spec, note = startline.line_spec_for_image(str(raw), str(work), prior=prior)
    try:
        width, height = jpeg_dimensions(raw)
        logo_inputs, chain, label = build_branding_overlay_filter_for_size(
            "format=yuv420p", width, height, sponsor_count=0)
        if not ffmpeg or (not spec and not chain):
            shutil.copyfile(raw, out)
            return False, note
        cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", str(raw)]
        for logo in logo_inputs:
            cmd.extend(["-loop", "1", "-i", logo])
        line_chain = ""
        if spec:
            line_inputs, line_chain = startline.line_filter_chain(spec, len(logo_inputs))
            cmd.extend(line_inputs)
        if chain:
            graph = f"{line_chain};{chain.replace('[0:v]', '[lined]', 1)}" if line_chain else chain
        else:
            graph, label = f"{line_chain};[lined]format=yuv420p[photo]", "photo"
        cmd.extend(["-filter_complex", graph, "-map", f"[{label}]", "-frames:v", "1",
                    "-q:v", str(START_PHOTO_JPEG_QUALITY), str(out)])
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60, **background_priority())
        if result.returncode != 0 or not _is_jpeg(out):
            # The photo matters more than the decoration on it.
            shutil.copyfile(raw, out)
            return False, f"{note}; adding the line and logo failed, so the photo is published as taken"
        return bool(spec), note
    finally:
        startline.cleanup_spec(spec)


def start_photo_paths(clip: sqlite3.Row) -> Tuple[Path, Path]:
    """(published copy, the camera's own still) for a start clip."""
    name = f"race{clip['race_id']}_start_{clip['id']}_photo.jpg"
    return appstate.VIDEO_CLIPS_DIR / "public" / name, appstate.VIDEO_CLIPS_DIR / name


def _relative(path: Path) -> str:
    try:
        return str(path.relative_to(appstate.BASE_DIR))
    except ValueError:
        return str(path)


def resolve_photo_path(stored: Any) -> Optional[Path]:
    """A stored photo path, only if it is inside the clips folder and exists."""
    if not stored:
        return None
    raw = Path(str(stored))
    path = (raw if raw.is_absolute() else appstate.BASE_DIR / raw).resolve()
    root = appstate.VIDEO_CLIPS_DIR.resolve()
    if root not in path.parents or not path.is_file():
        return None
    return path


def publish_start_photo(clip: sqlite3.Row, path: Path, cfg: Dict[str, Any]) -> str:
    """Upload to the club's bucket. "" when publishing to R2 is not set up.

    Not queued behind the video uploads: those hold their lock for minutes on
    the hut's 4G link, and this is a megabyte that is wanted now.
    """
    if normalise_video_public_provider(cfg.get("video_public_provider")) != "r2" or not video_public_r2_ready(cfg):
        return ""
    prefix = safe_r2_key_prefix(str(cfg.get("video_public_r2_prefix") or "race-videos"))
    key = "/".join(p for p in (prefix, f"race{clip['race_id']}", path.name) if p)
    last = ""
    for attempt in range(1, UPLOAD_ATTEMPTS + 1):
        try:
            return upload_file_to_r2(cfg, key, path, content_type="image/jpeg")
        except Exception as exc:
            last = str(exc)[:300]
            if attempt < UPLOAD_ATTEMPTS:
                time.sleep(2 * attempt)
    raise RuntimeError(f"could not upload it to Cloudflare R2: {last}")


def _clip(clip_id: int) -> Optional[sqlite3.Row]:
    init_db()
    with get_db() as db:
        return db.execute("SELECT * FROM video_clips WHERE id = ?", (clip_id,)).fetchone()


def update_start_photo(clip_id: int, **fields: Any) -> None:
    """Set photo_* columns. updated_at is left alone: the public page reloads on
    a photo becoming ready, not on every step on the way there."""
    if not fields:
        return
    names = ", ".join(f"photo_{k} = ?" for k in fields)
    with get_db() as db:
        db.execute(f"UPDATE video_clips SET {names} WHERE id = ?", (*fields.values(), clip_id))
        db.commit()


def _race_start_times(race: sqlite3.Row) -> List[str]:
    """Every start time the race has now, as the start clips store them."""
    if str(row_get(race, "race_type", "") or "").lower() == "pursuit":
        from core.pursuit import pursuit_start_signal_times
        return [dt.isoformat(timespec="seconds")
                for dt in pursuit_start_signal_times(race, get_entries(int(race["id"])))]
    return [str(s.get("time") or "").strip() for s in race_start_schedule(race)]


def not_wanted_reason(clip: Optional[sqlite3.Row]) -> str:
    """Why this photo should not be taken any more, or "" if it should."""
    if not clip:
        return "its start clip was removed"
    if str(clip["status"] or "") == "cancelled":
        return "the start was postponed"
    race = get_race(int(clip["race_id"]))
    if not race:
        return "the race was removed"
    try:
        if str(clip["event_time"]) not in _race_start_times(race):
            return "the start time was changed"
    except Exception:
        pass
    return ""


# Everything that can wait on the database is done this long before the gun,
# so that at the gun itself the only thing left is asking the camera. The
# start horn's own log entry is written at that same moment, and a write
# waiting on it would push the photo past the signal.
GET_READY_S = 3.0


def _wait_until(clip_id: int, until: float) -> bool:
    """Sleep until ``until`` (epoch). False if the start went away meanwhile.

    Wakes at least every 20 s to see whether the start was moved, and to follow
    the clock if it is corrected.
    """
    while True:
        remaining = until - time.time()
        if remaining <= 0:
            return True
        time.sleep(min(remaining, 20.0))
        reason = not_wanted_reason(_clip(clip_id))
        if reason:
            update_start_photo(clip_id, status="cancelled", message=f"Not taken: {reason}.")
            return False


def _wait_until_nearly_time(clip_id: int, target: float) -> bool:
    """Sleep until just before the start signal. False if the start went away."""
    return _wait_until(clip_id, target - GET_READY_S)


def _buoy_before_the_gun(clip_id: int, target: float, work: Path) -> Tuple[bool, List[Any]]:
    """Stills before the start, to find the buoy. (still wanted, detections).

    Best effort: a camera that will not answer, or a start booked too late for
    them, costs nothing but the samples. Each still is looked at and deleted.
    """
    detections: List[Any] = []
    for lead in PRE_START_SAMPLES_S:
        when = target - lead
        if not _wait_until(clip_id, when):
            return False, detections
        if time.time() - when > 5:          # armed too late for this one
            continue
        cfg = video_config()
        if not (start_photo_enabled(cfg) and startline.startline_overlay_enabled(cfg)) or camera_problem(cfg):
            continue
        still = work / f"before_{int(lead)}.jpg"
        try:
            take_camera_snapshot(cfg, still)
            detections.append(startline.detect_buoy(str(still)))
        except Exception:
            pass
        finally:
            try:
                still.unlink()
            except OSError:
                pass
    return True, detections


def _iso_ms(epoch: float) -> str:
    return datetime.fromtimestamp(epoch).isoformat(timespec="milliseconds")


def take_start_photo(clip_id: int) -> None:
    """The start photo's thread: wait for the gun, take it, publish it."""
    work: Optional[Path] = None
    try:
        clip = _clip(clip_id)
        event_dt = parse_dt(clip["event_time"]) if clip else None
        if not clip or not event_dt:
            return
        target = event_dt.timestamp()
        scratch = appstate.VIDEO_RUNTIME_DIR / "start_photos"
        scratch.mkdir(parents=True, exist_ok=True)
        work = Path(tempfile.mkdtemp(prefix=f"clip{clip_id}_", dir=str(scratch)))
        wanted, prior = _buoy_before_the_gun(clip_id, target, work)
        if not wanted or not _wait_until_nearly_time(clip_id, target):
            return
        cfg = video_config()
        if not start_photo_enabled(cfg):
            update_start_photo(clip_id, status="off", message="Start photos are switched off.")
            return
        clip = _clip(clip_id)
        reason = not_wanted_reason(clip)
        if reason:
            update_start_photo(clip_id, status="cancelled", message=f"Not taken: {reason}.")
            return
        raw = work / "camera.jpg"
        source, taken_at, answer_ms, notes = "", target, None, []
        # The gun. Nothing between here and the camera but the clock.
        remaining = target - time.time()
        if remaining > 0:
            time.sleep(remaining)
        late = time.time() - target
        if late <= CAMERA_LATE_LIMIT_S:
            try:
                asked, answered = take_camera_snapshot(cfg, raw)
                source, taken_at, answer_ms = "camera", asked, int(round((answered - asked) * 1000))
            except Exception as exc:
                notes.append(f"the camera gave no photo ({exc})")
        else:
            notes.append(f"the hut was not ready to ask the camera at the gun ({late:.0f} s late)")
        update_start_photo(clip_id, status="taking", message="Making the start photo...")
        ffmpeg = ffmpeg_executable(cfg)
        if not source:
            if not ffmpeg:
                raise RuntimeError("; ".join(notes + ["FFmpeg was not found, so the recording could not be used"]))
            update_start_photo(clip_id, message="Taking the start photo from the recording...")
            prior = list(prior) + list(frame_from_recording(cfg, ffmpeg, event_dt, raw, work) or [])
            source, taken_at = "recording", target
        public_path, raw_path = start_photo_paths(clip)
        public_path.parent.mkdir(parents=True, exist_ok=True)
        part = public_path.with_name(public_path.stem + ".part.jpg")
        line_drawn, line_note = compose_start_photo(ffmpeg, raw, part, cfg, work, prior=prior)
        os.replace(part, public_path)
        shutil.copyfile(raw, raw_path)
        update_start_photo(clip_id, file_path=_relative(public_path), raw_path=_relative(raw_path),
                           source=source, taken_at=_iso_ms(taken_at), answer_ms=answer_ms,
                           message="Uploading the start photo...")
        url = ""
        upload_problem = ""
        try:
            url = publish_start_photo(clip, public_path, cfg)
        except Exception as exc:
            upload_problem = str(exc)
        # From the gun to the photo being where the public page can show it --
        # the figure that says whether "a few seconds after the gun" is true.
        ready_after = time.time() - target
        if upload_problem:
            update_start_photo(clip_id, status="error", message=f"Taken, but {upload_problem}")
        else:
            where = "by the camera" if source == "camera" else f"from the recording, because {notes[0]}" if notes else "from the recording"
            update_start_photo(clip_id, status="ready", url=url,
                               message=f"Taken {where} at the start signal"
                                       f"{'' if answer_ms is None else f'; the camera answered in {answer_ms / 1000:.1f} s'}"
                                       f"; ready {ready_after:.0f} s after the gun"
                                       f"{'' if line_drawn else ' (no start line drawn)'}.")
        log_activity("start photo", f"clip #{clip_id}: {source}"
                                    f"{'' if answer_ms is None else f' in {answer_ms} ms'}; line {line_note}"
                                    f"{''.join('; ' + n for n in notes)}"
                                    f"{'; ' + upload_problem if upload_problem else ''}"
                                    f"; {'tried' if upload_problem else 'ready'} {ready_after:.1f} s after the gun",
                     user="system")
    except Exception as exc:
        update_start_photo(clip_id, status="error", message=f"Could not take the start photo: {exc}")
        log_activity("start photo", f"clip #{clip_id}: error {exc}", user="system")
    finally:
        if work is not None:
            shutil.rmtree(work, ignore_errors=True)
        with _ARMED_LOCK:
            _ARMED.discard(clip_id)
            _SETTLED.add(clip_id)


def arm_start_photo(clip_id: int) -> bool:
    """Book the photo for a start clip and start its waiting thread, once.

    Called whenever a start clip is booked -- which the start sequence does on
    every tick -- so a photo still waiting when the app restarts is picked up
    again. A clip booked before photos existed, or while they were off, is
    taken on too if its start is still to come or still in the recording.
    """
    with _ARMED_LOCK:
        if clip_id in _ARMED or clip_id in _SETTLED:
            return False
    cfg = video_config()
    if not start_photo_enabled(cfg):
        return False
    clip = _clip(clip_id)
    if not clip or str(clip["clip_type"]) != "start" or str(clip["status"] or "") == "cancelled":
        return False
    status = str(row_get(clip, "photo_status") or "").lower()
    event_dt = parse_dt(clip["event_time"])
    buffer_s = int(cfg.get("video_buffer_minutes") or 20) * 60
    if status not in ("", "pending", "taking") or not event_dt or event_dt.timestamp() < time.time() - buffer_s:
        if status in ("pending", "taking"):
            update_start_photo(clip_id, status="error", message="Not taken: the hut was not running at the start signal.")
        with _ARMED_LOCK:
            _SETTLED.add(clip_id)
        return False
    with _ARMED_LOCK:
        if clip_id in _ARMED:
            return False
        _ARMED.add(clip_id)
    if status != "pending":
        update_start_photo(clip_id, status="pending", message="The photo is taken at the start signal.")
    threading.Thread(target=take_start_photo, args=(clip_id,), name=f"start-photo-{clip_id}", daemon=True).start()
    return True


def start_photos_for_race(race_id: int) -> List[Dict[str, Any]]:
    """Every start photo the race has or is waiting for, in start order."""
    init_db()
    with get_db() as db:
        rows = db.execute(
            "SELECT * FROM video_clips WHERE race_id = ? AND clip_type = 'start'"
            " AND COALESCE(photo_status, '') NOT IN ('', 'off', 'cancelled') ORDER BY event_time, id",
            (race_id,)).fetchall()
    photos = []
    for row in rows:
        label = str(row_get(row, "label") or "").strip()
        if label.lower().endswith(" video"):
            label = label[:-len(" video")]
        event_dt = parse_dt(row["event_time"])
        answer_ms = row_get(row, "photo_answer_ms")
        photos.append({
            "clip_id": int(row["id"]),
            "label": label or "Start",
            "time": event_dt.strftime("%H:%M:%S") if event_dt else str(row["event_time"]),
            "status": str(row_get(row, "photo_status") or ""),
            "message": str(row_get(row, "photo_message") or ""),
            "source": str(row_get(row, "photo_source") or ""),
            "answer_s": None if answer_ms is None else round(int(answer_ms) / 1000.0, 1),
            "url": str(row_get(row, "photo_url") or ""),
            "has_file": resolve_photo_path(row_get(row, "photo_file_path")) is not None,
            "has_raw": resolve_photo_path(row_get(row, "photo_raw_path")) is not None,
        })
    return photos


def retry_start_photo_uploads(cfg: Optional[Dict[str, Any]] = None) -> int:
    """Upload again every start photo that was taken but did not reach R2."""
    cfg = cfg or video_config()
    init_db()
    with get_db() as db:
        rows = db.execute(
            "SELECT * FROM video_clips WHERE clip_type = 'start' AND photo_status = 'error'"
            " AND COALESCE(photo_file_path, '') != ''").fetchall()
    done = 0
    for row in rows:
        path = resolve_photo_path(row_get(row, "photo_file_path"))
        if not path:
            continue
        try:
            url = publish_start_photo(row, path, cfg)
        except Exception as exc:
            update_start_photo(int(row["id"]), message=f"Taken, but {exc}")
            continue
        update_start_photo(int(row["id"]), status="ready", url=url, message="Taken, and uploaded on retry.")
        done += 1
    return done


def test_photo_path() -> Path:
    return appstate.VIDEO_RUNTIME_DIR / "start_photo_test.jpg"


def take_test_start_photo() -> Dict[str, Any]:
    """Settings' test: ask the camera now and make the published copy, locally.

    Nothing is uploaded: this is for seeing, before race day, that the camera
    answers, how long it takes, and whether the line is found.
    """
    cfg = video_config()
    scratch = appstate.VIDEO_RUNTIME_DIR / "start_photos"
    scratch.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="test_", dir=str(scratch)))
    try:
        raw = work / "camera.jpg"
        try:
            asked, answered = take_camera_snapshot(cfg, raw)
        except Exception as exc:
            return {"ok": False, "message": f"No start photo: {exc}."}
        width, height = jpeg_dimensions(raw)
        out = work / "photo.jpg"
        line_drawn, note = compose_start_photo(ffmpeg_executable(cfg), raw, out, cfg, work)
        os.replace(out, test_photo_path())
        size_mb = test_photo_path().stat().st_size / 1024 / 1024
        return {"ok": True, "message": (
            f"The camera answered in {answered - asked:.1f} s with a {width} x {height} photo "
            f"({size_mb:.1f} MB as published). Start line: {note}.")}
    finally:
        shutil.rmtree(work, ignore_errors=True)
