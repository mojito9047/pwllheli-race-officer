"""Video subsystem: FFmpeg recorder, clips, PTZ presets, R2 publishing, branding.

Extracted verbatim from app.py. Covers the rolling FFmpeg segment recorder and
live preview, USB/RTSP source handling, evidence-clip scheduling and building,
public-clip transcode + Cloudflare R2 upload (signed or via curl), the public
live-frame branding/upload loop, Hikvision ISAPI PTZ preset control with
auth-failure/manual-hold guards, and the uploaded branding-image manifest with
sponsor-rotation overlay filters.

Flask-facing pieces stay in app.py: branding_assets_for_template (url_for) and
delete_race_and_related_data (race admin). core.horn's VIDEO_CLIP_SCHEDULER
hook is registered at the bottom of this module, so importing core.video wires
manual-horn evidence clips to schedule_video_clip.
"""
from __future__ import annotations

import atexit
import base64
import hashlib
import json
import locale
import os
import platform
import re
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
from typing import Any, Dict, Iterable, List, Optional, Tuple

import secrets
from urllib.parse import quote, urlparse

from werkzeug.utils import secure_filename

from core import appstate
from core import r2
from core import startline
from core.activitylog import log_activity
from core.classconfig import race_series_row, race_start_schedule
from core.courses import custom_course_from_race
from core.db import get_db, init_db, row_get
from core.eventlog import log_event
# The upload allowances and the Signature V4 signing live in core.r2 so the
# off-site backup, which needs HEAD/LIST/DELETE as well, shares them rather than
# carrying a second copy of the same rules. Re-exported here under their original
# names because this module's callers and tests read them off core.video.
from core.r2 import (
    R2_CONNECT_TIMEOUT_S,
    R2_MIN_BYTES_PER_S,
    R2_STALL_BYTES_PER_S,
    R2_STALL_SECONDS,
    R2_UPLOAD_BASE_TIMEOUT_S,
    R2_UPLOAD_MAX_TIMEOUT_S,
    r2_upload_timeout_s,
    safe_r2_account_id,
    safe_r2_bucket_name,
    safe_r2_key_prefix,
)
from core.races import (
    get_entries,
    race_first_start_dt,
    race_first_start_time,
    race_has_finished_for_sequence,
)
from core.settings import (
    bool_to_text,
    float_in_range,
    get_app_setting_overrides,
    int_in_range,
    text_to_bool,
)
from core.timeutils import dt_full_display, parse_dt

PUBLIC_VIDEO_UPLOAD_LOCK = threading.Lock()
PUBLIC_VIDEO_UPLOAD_RETRY_ATTEMPTS = 4

PUBLIC_BRANDING_ROTATION_SECONDS = 5
# A live-preview ffmpeg can stay alive but stop producing frames if its RTSP read
# to the camera stalls (seen when another consumer — e.g. the live-stream relay —
# competes for the camera). The process never exits, so the JPEG freezes until a
# manual restart. If the preview has been up this long yet the live JPEG hasn't
# been refreshed within this window, treat it as hung and relaunch it.
PREVIEW_STALL_SECONDS = 30
# The same stall costs far more on the recorder, which is why FFmpeg is now told
# to give up on a silent camera instead of waiting for it forever. Left to
# itself it waits indefinitely: a black-holed RTSP address was still being
# waited on after 25 seconds with no timeout set, and exited in 8.8 with one.
# The camera reboots on a schedule, and a recorder that sits through the reboot
# holding a segment open records nothing until somebody notices.
#
# Ten seconds is far longer than any gap between frames on a working camera and
# short enough that the watchdog's restart is the recovery, rather than the
# morning. FFmpeg wants microseconds, and the rtsp demuxer spells it `-timeout`
# (it was `-stimeout` before the rename) — `-rw_timeout` is the AVIO-level
# option and is not what the RTSP socket reads honour.
VIDEO_RTSP_TIMEOUT_SECONDS = float(os.environ.get("RO_VIDEO_RTSP_TIMEOUT_S", "10"))
PUBLIC_LIVE_BRANDING_LOCK = threading.Lock()
PUBLIC_LIVE_R2_LOCK = threading.Lock()
PUBLIC_LIVE_R2_STATE: Dict[str, Any] = {
    "started": False,
    "last_status": {"ok": False, "enabled": False, "message": "Public live-image R2 upload is disabled."},
    "last_upload_at": None,
    "last_url": "",
    "last_key": "",
}


# Background video recorder state.  The recorder keeps a short rolling FFmpeg
# segment buffer so start/finish clips can include video before the event.
VIDEO_RUNTIME_STATE: Dict[str, Any] = {
    "process": None,
    "preview_process": None,
    "config_hash": None,
    "last_status": {"ok": False, "enabled": False, "message": "Video recording is disabled."},
    "started_at": None,
    "preview_started_at": None,
    "last_cleanup_at": 0.0,
    "last_command": "",
    "last_preview_command": "",
    # The watchdog's own bookkeeping: when it last brought the recorder back and
    # how many times, so the dashboard can say "it died and was restarted"
    # rather than only "it is running now".
    "watchdog_started": False,
    "restarts": 0,
    # When a launch last failed, and for which settings. See
    # start_video_background_recorder: a camera that is not answering must not
    # be dialled again by every web request.
    "failed_launch_at": 0.0,
    "failed_launch_hash": None,
    "last_restart_at": None,
}
VIDEO_LOCK = threading.Lock()

# How often the watchdog looks. The rolling buffer is the club's evidence for a
# protest, so what is being minimised is the window in which it can be silently
# dead: a minute of missing video either side of a finish can often be read from
# the segments around it, a morning of it cannot.
VIDEO_WATCHDOG_SECONDS = 30

# How long to leave a camera alone after a launch that failed immediately.
#
# start_video_background_recorder runs from a before_request hook, so with a
# camera that is not answering it ran on *every* request -- every page, every
# stylesheet, every image -- and each attempt spawns FFmpeg and blocks 250 ms
# waiting to see whether it survived. Measured at 286 ms a request while the
# camera was down, which took a static stylesheet to 583 ms to serve.
#
# The cost scales with traffic, which is the wrong way round: the busier the
# app, the slower every request in it. The recorder log holds 54,023 launch
# attempts, and its heaviest bursts line up with whatever was making the most
# requests at the time. A single page pays this once per asset it loads.
#
# One attempt per watchdog interval is what the watchdog is for. A settings save
# changes the config hash and retries at once, so "I have fixed the camera, try
# again" still works on the button press.
VIDEO_RETRY_SECONDS = VIDEO_WATCHDOG_SECONDS


# Optional PTZ/zoom preset control for IP cameras used by the video recorder.
# The scheduler switches between an idle/wide preset and a race/start-finish
# preset without repeatedly hammering the camera API.
PTZ_RUNTIME_STATE: Dict[str, Any] = {
    "last_preset": None,
    "pending_preset": None,
    "last_status": {"ok": True, "enabled": False, "message": "Camera preset control is disabled."},
    "last_switch_at": None,
    # When the RO presses a settings-page test button, hold that preset briefly
    # so the automatic scheduler does not immediately switch back to the idle
    # view before the RO can see whether the camera moved.
    "manual_hold_until": None,
    # Avoid repeatedly retrying bad digest credentials.  Hikvision cameras can
    # lock the admin login for a period after several failed attempts; a single
    # HTTP 401/403 pauses automatic switching until the RO saves settings or
    # presses one of the explicit test buttons again.
    "auth_failed": False,
}
PTZ_LOCK = threading.Lock()




# Central start-sequence scheduler state.  The scheduler is deliberately
# server-side; automatic horns and VHF announcements continue even if the RO is
# on a tablet, adding entries, or viewing a different page.


def safe_ptz_camera_base_url(value: str) -> str:
    """Return a safe base URL for a local PTZ camera, or blank if unusable.

    The Hikvision ISAPI preset call is built from this origin, so any path
    entered by the RO is ignored.  Credentials belong in the separate username
    and password fields rather than inside the URL.
    """
    text = str(value or "").strip()
    if not text:
        return ""
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", text):
        text = "http://" + text
    try:
        parsed = urlparse(text)
    except Exception:
        return ""
    if parsed.scheme.lower() not in ("http", "https") or not parsed.netloc:
        return ""
    if "@" in parsed.netloc:
        return ""
    return f"{parsed.scheme.lower()}://{parsed.netloc}"


def normalise_ptz_auth_mode(value: Optional[str] = None) -> str:
    """Return the camera HTTP authentication mode for ISAPI PTZ calls.

    Hikvision installations can be configured for digest, basic, or mixed web
    authentication.  Digest remains the default because it matched the original
    manual ``curl --digest`` test, but Settings also exposes ``AnyAuth`` and
    ``Basic`` for diagnosing cameras whose web-auth mode differs from RTSP.
    """
    mode = str(value or "digest").strip().lower().replace("_", "-").replace(" ", "-")
    if mode in ("any", "anyauth", "any-auth"):
        return "anyauth"
    if mode in ("basic", "http-basic"):
        return "basic"
    return "digest"


def normalise_video_recording_mode(source_type: str, value: Optional[str] = None) -> str:
    """Return the recording mode supported by the selected video source.

    RTSP/IP cameras can usually provide H.264/H.265 already, so stream-copy is
    the default and avoids the huge files created by re-encoding. USB webcams
    still use the re-encode path because most devices cannot be opened twice and
    the app needs to create both rolling segments and a live JPEG preview.
    """
    source = str(source_type or "usb").strip().lower()
    mode = str(value or "").strip().lower()
    if mode not in ("copy", "reencode"):
        mode = "copy" if source == "rtsp" else "reencode"
    if source != "rtsp" and mode == "copy":
        return "reencode"
    return mode


def normalise_video_copy_container(value: Optional[str] = None) -> str:
    """Return the rolling-buffer container for RTSP stream-copy mode.

    Fragmented MP4 is the default because it works with the Hikvision H.265
    stream seen in hut testing and starts producing buffer files quickly.  MPEG-TS
    remains available as an advanced fallback for cameras that dislike MP4
    segmenting, but some Hikvision streams can stall before producing TS files.
    """
    value = str(value or "").strip().lower()
    if value in ("ts", "mpegts", "mpeg-ts"):
        return "ts"
    return "mp4"




def normalise_video_rtsp_timestamp_mode(value: Optional[str] = None) -> str:
    """Return how FFmpeg should timestamp RTSP packets.

    Camera RTP timestamps are the default. They usually produce smoother clips
    because the output timing follows the camera rather than packet arrival time.
    Wall-clock timestamps remain available as a fallback for unusual streams that
    send missing or reset PTS/DTS values.
    """
    value = str(value or "").strip().lower().replace("_", "-")
    if value in ("wallclock", "wall-clock", "wall clock", "arrival", "arrival-time"):
        return "wallclock"
    return "camera"


def normalise_video_preview_size(value: Optional[str] = None) -> str:
    """Return the JPEG preview scale setting.

    ``native`` preserves the dimensions of the configured preview/sub-stream,
    which is the best quality option when channel 102 is set to a sensible size.
    Numeric values scale to that width while preserving aspect ratio.
    """
    text = str(value or "").strip().lower()
    if text in ("native", "source", "original", "full"):
        return "native"
    if text in ("640", "960", "1280", "1920"):
        return text
    return "native"


def build_video_preview_filter(cfg: Dict[str, Any]) -> str:
    """Build the FFmpeg filter used for live JPEG preview snapshots."""
    filters: List[str] = []
    if text_to_bool(cfg.get("video_preview_keyframes_only"), False):
        # Updating only on I-frames gives sharp, self-contained snapshots and
        # avoids asking FFmpeg to JPEG-encode intermediate predicted frames. The
        # preview rate then follows the camera I-frame interval.
        filters.append(r"select='eq(pict_type\,I)'")
    else:
        filters.append(f"fps={int_in_range(cfg.get('video_preview_fps'), 2, 1, 10)}")

    size = normalise_video_preview_size(cfg.get("video_preview_size"))
    if size != "native":
        filters.append(f"scale={size}:-2")
    return ",".join(filters) or "null"




def safe_branding_filename(name: str) -> str:
    """Return a safe uploaded branding image filename, or an empty string."""
    filename = secure_filename(Path(str(name or "").replace("\\", "/")).name)
    if not filename or Path(filename).suffix.lower() not in appstate.BRANDING_EXTENSIONS:
        return ""
    return filename


def read_branding_manifest() -> Dict[str, Any]:
    """Load the optional public-camera/video branding manifest from data/branding."""
    try:
        if appstate.BRANDING_MANIFEST_PATH.exists():
            data = json.loads(appstate.BRANDING_MANIFEST_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                sponsors = data.get("sponsors") if isinstance(data.get("sponsors"), list) else []
                return {
                    "club_logo": str(data.get("club_logo") or "").strip(),
                    "sponsors": [s for s in sponsors if isinstance(s, dict)],
                }
    except Exception:
        pass
    return {"club_logo": "", "sponsors": []}


def write_branding_manifest(manifest: Dict[str, Any]) -> None:
    """Persist the public branding manifest under data/branding."""
    appstate.BRANDING_DIR.mkdir(parents=True, exist_ok=True)
    sponsors = manifest.get("sponsors") if isinstance(manifest.get("sponsors"), list) else []
    safe_manifest = {
        "club_logo": safe_branding_filename(str(manifest.get("club_logo") or "")),
        "sponsors": [],
    }
    for item in sponsors:
        filename = safe_branding_filename(str(item.get("filename") or ""))
        if not filename:
            continue
        safe_manifest["sponsors"].append({
            "id": str(item.get("id") or secrets.token_hex(6)),
            "label": str(item.get("label") or Path(filename).stem).strip()[:80],
            "filename": filename,
        })
    appstate.BRANDING_MANIFEST_PATH.write_text(json.dumps(safe_manifest, indent=2, sort_keys=True), encoding="utf-8")


def branding_file_path(filename: str) -> Optional[Path]:
    """Resolve an uploaded branding image only inside data/branding."""
    filename = safe_branding_filename(filename)
    if not filename:
        return None
    path = (appstate.BRANDING_DIR / filename).resolve()
    try:
        root = appstate.BRANDING_DIR.resolve()
        if path == root or root in path.parents:
            return path if path.exists() and path.is_file() else None
    except OSError:
        return None
    return None


def branding_assets() -> Dict[str, Any]:
    """Return public branding assets for templates and FFmpeg overlay filters.

    The default club logo is the bundled Pwllheli Sailing Club logo. Uploaded
    sponsor logos live in data/branding and are backup-worthy race assets.
    """
    cfg = video_config()
    manifest = read_branding_manifest()
    uploaded_club = branding_file_path(str(manifest.get("club_logo") or ""))
    club_path = uploaded_club if uploaded_club else appstate.DEFAULT_CLUB_LOGO_PATH
    sponsors: List[Dict[str, Any]] = []
    for item in manifest.get("sponsors") or []:
        path = branding_file_path(str(item.get("filename") or ""))
        if not path:
            continue
        sponsors.append({
            "id": str(item.get("id") or ""),
            "label": str(item.get("label") or path.stem),
            "filename": path.name,
            "path": path,
        })
    return {
        "enabled": text_to_bool(cfg.get("public_branding_enabled"), True),
        "club_logo_enabled": text_to_bool(cfg.get("public_branding_club_logo_enabled"), True),
        "club_logo_uploaded": bool(uploaded_club),
        "club_logo_file": uploaded_club.name if uploaded_club else appstate.DEFAULT_CLUB_LOGO_PATH.name,
        "club_logo_path": club_path if club_path.exists() else None,
        "sponsors": sponsors,
    }


def save_uploaded_branding_image(upload: Any, filename_prefix: str) -> str:
    """Save an uploaded public branding image and return its safe filename."""
    if not upload or not getattr(upload, "filename", ""):
        raise ValueError("Choose a PNG or JPG image to upload.")
    original = safe_branding_filename(upload.filename)
    if not original:
        raise ValueError("Branding logos must be .png, .jpg or .jpeg files.")
    appstate.BRANDING_DIR.mkdir(parents=True, exist_ok=True)
    suffix = Path(original).suffix.lower()
    stem = secure_filename(filename_prefix) or "logo"
    filename = f"{stem}{suffix}"
    # Sponsor uploads use a unique prefix to allow several logos with the same
    # original name; the club logo deliberately replaces the previous file.
    upload.save(appstate.BRANDING_DIR / filename)
    return filename


def delete_branding_file_if_unused(filename: str, manifest: Optional[Dict[str, Any]] = None) -> None:
    """Delete an uploaded branding file if no manifest entry still references it."""
    filename = safe_branding_filename(filename)
    if not filename:
        return
    manifest = manifest or read_branding_manifest()
    referenced = {safe_branding_filename(str(manifest.get("club_logo") or ""))}
    referenced.update(safe_branding_filename(str(s.get("filename") or "")) for s in manifest.get("sponsors") or [])
    if filename in referenced:
        return
    path = branding_file_path(filename)
    if path:
        try:
            path.unlink()
        except OSError:
            pass


def _branding_logo_items(sponsor_count: int = 8) -> List[Tuple[str, Path]]:
    """Return enabled club/sponsor branding images in display order."""
    assets = branding_assets()
    if not assets.get("enabled"):
        return []

    logo_items: List[Tuple[str, Path]] = []
    if assets.get("club_logo_enabled") and assets.get("club_logo_path"):
        club_path = Path(assets["club_logo_path"])
        if club_path.exists():
            logo_items.append(("club", club_path))
    for sponsor in (assets.get("sponsors") or [])[:max(0, int(sponsor_count))]:
        raw_path = sponsor.get("path")
        if not raw_path:
            continue
        path = Path(raw_path)
        if path.exists():
            logo_items.append(("sponsor", path))
    return logo_items


def _sponsor_rotation_enable_expr(index: int, total: int, interval_seconds: int = PUBLIC_BRANDING_ROTATION_SECONDS) -> str:
    """Return an FFmpeg enable expression for a rotating sponsor logo.

    FFmpeg filter expressions use commas as function argument separators, so the
    commas must be escaped before the expression is embedded in filter_complex.
    """
    total = max(1, int(total or 1))
    interval_seconds = max(1, int(interval_seconds or PUBLIC_BRANDING_ROTATION_SECONDS))
    index = max(0, int(index or 0)) % total
    return f"eq(mod(floor(t/{interval_seconds})\\,{total})\\,{index})"


def current_public_branding_rotation_index(total: int, now: Optional[float] = None) -> int:
    """Return the sponsor logo index for the current five-second live-image slot."""
    total = max(1, int(total or 1))
    timestamp = time.time() if now is None else float(now)
    return int(timestamp // PUBLIC_BRANDING_ROTATION_SECONDS) % total


def build_branding_overlay_filter_for_size(
    base_filter: str,
    target_w: int,
    target_h: int,
    sponsor_count: int = 8,
    *,
    static_sponsor_index: Optional[int] = None,
) -> Tuple[List[str], str, str]:
    """Return logo inputs and FFmpeg overlay chain for a known output size.

    Public media keeps the PSC/club logo at the top-left. Sponsor logos are no
    longer laid across the whole sky band: start/finish videos rotate one
    sponsor at a time in the top-right every five seconds, and the public live
    JPEG selects the matching sponsor for the current five-second slot.
    """
    logo_items = _branding_logo_items(sponsor_count=sponsor_count)
    if not logo_items:
        return [], "", ""

    target_w = max(320, int(target_w or 1280))
    target_h = max(180, int(target_h or 720))
    margin = max(12, round(target_h * 0.022))
    logo_cap_h = max(32, round(target_h * 0.15))
    club_items = [(kind, path) for kind, path in logo_items if kind == "club"][:1]
    sponsor_items = [(kind, path) for kind, path in logo_items if kind == "sponsor"]
    if static_sponsor_index is not None and sponsor_items:
        sponsor_items = [sponsor_items[int(static_sponsor_index) % len(sponsor_items)]]

    overlay_items = club_items + sponsor_items
    if not overlay_items:
        return [], "", ""

    club_max_w = round(target_w * 0.22) if club_items else 0
    sponsor_max_w = round(target_w * 0.24) if sponsor_items else 0

    chains = [f"[0:v]{base_filter}[v0]"]
    current = "v0"
    sponsor_total = len([1 for kind, _path in sponsor_items if kind == "sponsor"])
    sponsor_idx = 0
    for idx, (kind, _path) in enumerate(overlay_items):
        in_idx = idx + 1
        logo_label = f"logo{idx}"
        out_label = f"v{idx + 1}"
        if kind == "club":
            chains.append(
                f"[{in_idx}:v]scale={club_max_w}:{logo_cap_h}:force_original_aspect_ratio=decrease,"
                f"format=rgba,colorchannelmixer=aa=0.82[{logo_label}]"
            )
            chains.append(
                f"[{current}][{logo_label}]overlay={margin}:{margin}:"
                f"format=auto:shortest=1:eof_action=endall[{out_label}]"
            )
        else:
            chains.append(
                f"[{in_idx}:v]scale={sponsor_max_w}:{logo_cap_h}:force_original_aspect_ratio=decrease,"
                f"format=rgba,colorchannelmixer=aa=0.90[{logo_label}]"
            )
            enable = ""
            if static_sponsor_index is None and sponsor_total > 1:
                enable = f":enable='{_sponsor_rotation_enable_expr(sponsor_idx, sponsor_total)}'"
            chains.append(
                f"[{current}][{logo_label}]overlay=main_w-overlay_w-{margin}:{margin}:"
                f"format=auto:shortest=1:eof_action=endall{enable}[{out_label}]"
            )
            sponsor_idx += 1
        current = out_label
    return [str(path) for _kind, path in overlay_items], ";".join(chains), current


def build_public_branding_overlay_filter(quality: str, sponsor_count: int) -> Tuple[List[str], str, str]:
    """Return FFmpeg inputs/filter for public start/finish video branding.

    Public web videos are already re-encoded for R2. This burns the club logo
    into the top-left and rotates one sponsor logo at a time in the top-right,
    changing every five seconds, without touching the full-quality evidence clip.
    """
    if not branding_assets().get("enabled"):
        return [], "", ""
    quality = normalise_video_public_quality(quality)
    video_scale, _crf = public_video_transcode_args(quality)
    target_h = 1080 if quality == "1080p" else 720
    target_w = round(target_h * 16 / 9)
    return build_branding_overlay_filter_for_size(f"{video_scale},format=yuv420p", target_w, target_h, sponsor_count)


def build_public_live_branding_overlay_filter(width: int, height: int, sponsor_count: int = 8, rotation_index: Optional[int] = None) -> Tuple[List[str], str, str]:
    """Return overlay inputs/filter for a branded public live-camera JPEG.

    A JPEG is a single frame, so it cannot animate by itself. The selected sponsor
    is based on the same five-second rotation slot used by the video overlay; the
    next generated/uploaded JPEG will carry the next sponsor logo.
    """
    if not branding_assets().get("enabled"):
        return [], "", ""
    sponsor_total = sum(1 for kind, _path in _branding_logo_items(sponsor_count=sponsor_count) if kind == "sponsor")
    static_index = None
    if sponsor_total > 1:
        static_index = current_public_branding_rotation_index(sponsor_total) if rotation_index is None else rotation_index
    return build_branding_overlay_filter_for_size("format=yuv420p", width, height, sponsor_count, static_sponsor_index=static_index)


def normalise_video_public_provider(value: Optional[str] = None) -> str:
    """Return the configured public video publishing provider."""
    value = str(value or "").strip().lower()
    if value in ("r2", "cloudflare-r2", "cloudflare_r2"):
        return "r2"
    return "off"


def normalise_video_public_live_provider(value: Optional[str] = None) -> str:
    """Return where the public live-camera JPEG should be served from."""
    value = str(value or "local").strip().lower()
    if value in ("r2", "cloudflare-r2", "cloudflare_r2", "bucket"):
        return "r2"
    return "local"


def normalise_video_public_quality(value: Optional[str] = None) -> str:
    """Return the public web-video transcode quality preset."""
    value = str(value or "").strip().lower().replace("_", "-")
    if value in ("1080", "1080p", "normal", "hd"):
        return "1080p"
    if value in ("720", "720p", "small", "sd"):
        return "720p"
    return "720p"


def safe_public_video_base_url(value: str) -> str:
    """Accept only http(s) public base URLs for served R2 objects."""
    value = str(value or "").strip().rstrip("/")
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return ""
    return value

def video_config() -> Dict[str, Any]:
    """Return normalised video-recorder settings from the database."""
    overrides = get_app_setting_overrides()
    source_type = (overrides.get("video_source_type") or "usb").strip().lower()
    if source_type not in ("usb", "rtsp"):
        source_type = "usb"
    return {
        "video_enabled": text_to_bool(overrides.get("video_enabled"), False),
        "video_source_type": source_type,
        "video_usb_source": repair_mojibake((overrides.get("video_usb_source") or "").strip()),
        "video_rtsp_url": (overrides.get("video_rtsp_url") or "").strip(),
        "video_preview_rtsp_url": (overrides.get("video_preview_rtsp_url") or "").strip(),
        "video_recording_mode": normalise_video_recording_mode(source_type, overrides.get("video_recording_mode")),
        "video_copy_container": normalise_video_copy_container(overrides.get("video_copy_container")),
        "video_rtsp_timestamp_mode": normalise_video_rtsp_timestamp_mode(overrides.get("video_rtsp_timestamp_mode")),
        "video_preview_size": normalise_video_preview_size(overrides.get("video_preview_size")),
        "video_preview_fps": int_in_range(overrides.get("video_preview_fps"), 2, 1, 10),
        "video_preview_jpeg_quality": int_in_range(overrides.get("video_preview_jpeg_quality"), 3, 2, 12),
        "video_preview_keyframes_only": text_to_bool(overrides.get("video_preview_keyframes_only"), False),
        "video_ffmpeg_path": (overrides.get("video_ffmpeg_path") or "ffmpeg").strip() or "ffmpeg",
        "video_pre_seconds": int_in_range(overrides.get("video_pre_seconds"), 60, 5, 300),
        "video_post_seconds": int_in_range(overrides.get("video_post_seconds"), 60, 5, 300),
        "video_segment_seconds": int_in_range(overrides.get("video_segment_seconds"), 5, 2, 30),
        "video_buffer_minutes": int_in_range(overrides.get("video_buffer_minutes"), 20, 5, 180),
        "video_public_provider": normalise_video_public_provider(overrides.get("video_public_provider")),
        "video_public_quality": normalise_video_public_quality(overrides.get("video_public_quality")),
        "video_startline_overlay_enabled": text_to_bool(overrides.get("video_startline_overlay_enabled"), False),
        "video_public_live_provider": normalise_video_public_live_provider(overrides.get("video_public_live_provider")),
        "video_public_live_interval_seconds": int_in_range(overrides.get("video_public_live_interval_seconds"), 5, 2, 60),
        "video_public_r2_account_id": safe_r2_account_id(overrides.get("video_public_r2_account_id") or ""),
        "video_public_r2_bucket": safe_r2_bucket_name(overrides.get("video_public_r2_bucket") or ""),
        "video_public_r2_access_key_id": (overrides.get("video_public_r2_access_key_id") or "").strip(),
        "video_public_r2_secret_access_key": overrides.get("video_public_r2_secret_access_key") or "",
        "video_public_r2_secret_access_key_set": bool(overrides.get("video_public_r2_secret_access_key")),
        "video_public_r2_public_base_url": safe_public_video_base_url(overrides.get("video_public_r2_public_base_url") or ""),
        "video_public_r2_prefix": safe_r2_key_prefix(overrides.get("video_public_r2_prefix") or "race-videos"),
        "public_branding_enabled": text_to_bool(overrides.get("public_branding_enabled"), True),
        "public_branding_club_logo_enabled": text_to_bool(overrides.get("public_branding_club_logo_enabled"), True),
        "ptz_enabled": text_to_bool(overrides.get("ptz_enabled"), False),
        "ptz_camera_url": safe_ptz_camera_base_url(overrides.get("ptz_camera_url") or ""),
        "ptz_username": (overrides.get("ptz_username") or "").strip(),
        "ptz_password": overrides.get("ptz_password") or "",
        "ptz_password_set": bool(overrides.get("ptz_password")),
        "ptz_auth_mode": normalise_ptz_auth_mode(overrides.get("ptz_auth_mode")),
        "ptz_channel": int_in_range(overrides.get("ptz_channel"), 1, 1, 32),
        "ptz_idle_preset": int_in_range(overrides.get("ptz_idle_preset"), 1, 1, 300),
        "ptz_recording_preset": int_in_range(overrides.get("ptz_recording_preset"), 2, 1, 300),
        "ptz_pre_start_seconds": int_in_range(overrides.get("ptz_pre_start_seconds"), 30, 0, 600),
    }




# ---------------------------------------------------------------------------
# FFmpeg video recording and live finish-camera preview
# ---------------------------------------------------------------------------
def repair_mojibake(value: str) -> str:
    """Best-effort repair for UTF-8 device names decoded as Windows cp1252.

    FFmpeg DirectShow camera names often contain characters such as ®.  Some
    Python/Windows combinations decode FFmpeg stderr using the active code page,
    which can turn "Microsoft®" into "MicrosoftÂ®".  If that mojibake value is
    saved and later passed back to FFmpeg, DirectShow cannot find the camera.
    """
    text = str(value or "")
    if not text:
        return ""
    # Common signature of UTF-8 bytes decoded as cp1252/latin-1.
    if "Ã" in text or "Â" in text or "â" in text:
        for enc in ("cp1252", "latin-1"):
            try:
                repaired = text.encode(enc).decode("utf-8")
            except UnicodeError:
                continue
            # Only accept a repair if it actually removed the mojibake markers.
            if repaired and repaired != text and repaired.count("Ã") + repaired.count("Â") + repaired.count("â") < text.count("Ã") + text.count("Â") + text.count("â"):
                return repaired
    return text


def decode_ffmpeg_output(raw: bytes) -> str:
    """Decode FFmpeg device-list output, preferring UTF-8 for modern builds."""
    if not raw:
        return ""
    candidates = ["utf-8", locale.getpreferredencoding(False), "cp1252", "latin-1"]
    best = ""
    best_score = 10**9
    for enc in dict.fromkeys([c for c in candidates if c]):
        try:
            text = raw.decode(enc, errors="replace")
        except LookupError:
            continue
        repaired = repair_mojibake(text)
        score = repaired.count("\ufffd") * 100 + repaired.count("Ã") * 10 + repaired.count("Â") * 10 + repaired.count("â") * 10
        if score < best_score:
            best = repaired
            best_score = score
    return best


def run_ffmpeg_list_command(cmd: List[str], timeout: int = 8) -> str:
    """Run FFmpeg device discovery and capture the combined output."""
    proc = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=timeout)
    return decode_ffmpeg_output(proc.stderr or b"")

def ffmpeg_executable(cfg: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """Return the configured FFmpeg executable path."""
    cfg = cfg or video_config()
    configured = str(cfg.get("video_ffmpeg_path") or "ffmpeg").strip() or "ffmpeg"
    if Path(configured).exists():
        return configured
    found = shutil.which(configured)
    return found or None


def list_usb_video_sources(cfg: Optional[Dict[str, Any]] = None) -> List[Dict[str, str]]:
    """Return USB/webcam inputs that FFmpeg should be able to open.

    The returned value is deliberately conservative: it includes detected devices
    plus the currently configured source so the settings page never loses a
    working manual value.
    """
    cfg = cfg or video_config()
    current = repair_mojibake(str(cfg.get("video_usb_source") or "").strip())
    items: List[Dict[str, str]] = []
    system = platform.system().lower()

    def add(value: str, label: Optional[str] = None) -> None:
        """Append one route/chart point to a list when coordinates are available."""
        value = repair_mojibake(str(value or "").strip())
        label = repair_mojibake(str(label or value).strip())
        if not value:
            return
        if any(i["value"] == value for i in items):
            return
        items.append({"value": value, "label": label})

    try:
        if system == "linux":
            for path in sorted(Path("/dev").glob("video*"), key=lambda p: p.name):
                if not re.fullmatch(r"video\d+", path.name):
                    continue
                name_path = Path("/sys/class/video4linux") / path.name / "name"
                name = ""
                try:
                    name = name_path.read_text(encoding="utf-8", errors="ignore").strip()
                except Exception:
                    pass
                add(str(path), f"{path} — {name}" if name else str(path))
        elif system == "windows":
            ffmpeg = ffmpeg_executable(cfg)
            if ffmpeg:
                output = run_ffmpeg_list_command([ffmpeg, "-hide_banner", "-list_devices", "true", "-f", "dshow", "-i", "dummy"], timeout=8)
                # FFmpeg versions differ in how DirectShow devices are listed.
                # Older builds emit a "DirectShow video devices" section followed
                # by quoted names.  Newer FFmpeg 8.x builds can instead emit
                # lines like:  "Microsoft® LifeCam HD-3000" (video)
                # without the section header.  Support both formats and ignore
                # alternative symbolic device names and audio devices.
                in_video = False
                for line in output.splitlines():
                    lower = line.lower()
                    typed = re.search(r'"([^"]+)"\s*\((video|audio)\)', line, flags=re.IGNORECASE)
                    if typed:
                        name = typed.group(1).strip()
                        kind = typed.group(2).lower()
                        if kind == "video" and name and not name.startswith("@"):
                            add(name, name)
                        continue
                    if "directshow video devices" in lower:
                        in_video = True
                        continue
                    if "directshow audio devices" in lower:
                        in_video = False
                        continue
                    if in_video:
                        if "alternative name" in lower:
                            continue
                        m = re.search(r'"([^"]+)"', line)
                        if m and not m.group(1).startswith("@"):  # Skip alternative symbolic links.
                            add(m.group(1), m.group(1))
        elif system == "darwin":
            ffmpeg = ffmpeg_executable(cfg)
            if ffmpeg:
                output = run_ffmpeg_list_command([ffmpeg, "-hide_banner", "-f", "avfoundation", "-list_devices", "true", "-i", ""], timeout=8)
                in_video = False
                for line in output.splitlines():
                    lower = line.lower()
                    if "avfoundation video devices" in lower:
                        in_video = True
                        continue
                    if "avfoundation audio devices" in lower:
                        in_video = False
                    if in_video:
                        m = re.search(r"\[(\d+)\]\s+(.+)$", line)
                        if m:
                            add(m.group(1), f"{m.group(1)} — {m.group(2).strip()}")
    except Exception:
        # Enumeration is best-effort; manual source entry remains available.
        pass

    if current and not any(i["value"] == current for i in items):
        add(current, f"{current} (configured)")

    if not items:
        if system == "windows":
            add("USB Camera", "USB Camera (default)")
        elif system == "darwin":
            add("0", "0 (default camera)")
        else:
            add("/dev/video0", "/dev/video0 (default)")
    return items


def video_input_args(cfg: Dict[str, Any], preview: bool = False) -> List[str]:
    """Build FFmpeg input arguments for the configured USB or RTSP video source.

    When ``preview`` is true and a separate RTSP preview URL is set, the
    sub-stream is used for the live JPEG page preview. The recording input always
    uses the main RTSP URL so evidence clips keep the best available quality.
    """
    source_type = str(cfg.get("video_source_type") or "usb").lower()
    if source_type == "rtsp":
        url = str((cfg.get("video_preview_rtsp_url") if preview else "") or cfg.get("video_rtsp_url") or "").strip()
        if not url:
            raise ValueError("RTSP video source is selected, but no RTSP URL is set.")
        # Use TCP for reliability. By default keep the camera/RTP timestamps;
        # hut testing showed that deriving timestamps from packet-arrival time
        # could make clips pause/jump when the camera stream had jitter. Avoid
        # discardcorrupt in stream-copy mode so FFmpeg preserves as much usable
        # video as possible; the status page warns if RTP sequence errors appear.
        args = ["-rtsp_transport", "tcp", "-rtsp_flags", "prefer_tcp", "-fflags", "+genpts"]
        # Fail rather than hang when the camera stops talking. Without this
        # FFmpeg waits for a silent camera indefinitely, holding the segment it
        # was writing open, which is how a scheduled camera reboot in the small
        # hours cost the club the video for a whole race.
        if VIDEO_RTSP_TIMEOUT_SECONDS > 0:
            args.extend(["-timeout", str(int(VIDEO_RTSP_TIMEOUT_SECONDS * 1_000_000))])
        if normalise_video_rtsp_timestamp_mode(cfg.get("video_rtsp_timestamp_mode")) == "wallclock":
            args.extend(["-use_wallclock_as_timestamps", "1"])
        args.extend(["-i", url])
        return args

    source = repair_mojibake(str(cfg.get("video_usb_source") or "").strip())
    system = platform.system().lower()
    if system == "windows":
        # FFmpeg DirectShow accepts sources like: video=USB Camera
        if not source:
            source = "video=USB Camera"
        elif not source.lower().startswith("video="):
            source = "video=" + source
        return ["-f", "dshow", "-rtbufsize", "256M", "-i", source]
    if system == "darwin":
        return ["-f", "avfoundation", "-i", source or "0"]
    return ["-f", "v4l2", "-i", source or "/dev/video0"]


def ffmpeg_filter_path(path: str) -> str:
    """Escape a path for use inside an FFmpeg filtergraph option."""
    value = str(path or "").replace("\\", "/")
    value = value.replace(":", r"\:")
    value = value.replace("'", r"\'")
    return value


def video_timestamp_overlay_filter() -> str:
    """Burn the local race-office clock into the top-left corner of recorded video.

    The FFmpeg filter must start with ``drawtext=``.  v0.28 accidentally built a
    chain like ``fontfile=...:drawtext=...``, which FFmpeg rejects before any
    buffer segments are written.  Keep the timestamp format simple by using
    FFmpeg's default ``%{localtime}`` expansion; it avoids fragile escaping of
    the colons in ``HH:MM:SS`` on Windows.
    """
    options = []
    if platform.system().lower() == "windows":
        windir = os.environ.get("WINDIR") or os.environ.get("SystemRoot") or r"C:\Windows"
        for candidate in (Path(windir) / "Fonts" / "arial.ttf", Path(windir) / "Fonts" / "segoeui.ttf"):
            if candidate.exists():
                options.append(f"fontfile='{ffmpeg_filter_path(str(candidate))}'")
                break
    options.extend([
        "text='%{localtime}'",
        "x=10",
        "y=10",
        "fontsize=24",
        "fontcolor=white",
        "box=1",
        "boxcolor=black@0.55",
    ])
    return "drawtext=" + ":".join(options)


def tail_text_file(path: Path, max_chars: int = 1600) -> str:
    """Return the tail of a text file for diagnostics display.

    Seeks. It used to read the file whole and slice the last 1600 bytes off the
    end, which is fine for a log of a few KB and not for the recorder's, found
    at **65 MB**: 23 ms and a 65 MB allocation to show two lines, on a path the
    failed-launch handler takes. A log grows; a tail does not.

    Four bytes are read per character asked for, so a UTF-16 or multi-byte tail
    still yields max_chars of text; a partial character at the cut is dropped by
    the decoder rather than shown.
    """
    try:
        size = path.stat().st_size
    except OSError:
        return ""
    want = max(0, int(max_chars)) * 4
    try:
        with path.open("rb") as fh:
            if size > want:
                fh.seek(size - want)
            data = fh.read(want)
    except OSError:
        return ""
    return decode_ffmpeg_output(data).strip()[-max_chars:]


def video_log_health_warnings(text: str) -> List[str]:
    """Return short operator warnings inferred from the FFmpeg recorder log."""
    lower = (text or "").lower()
    warnings: List[str] = []
    if "bad cseq" in lower or "max delay reached" in lower:
        warnings.append("RTSP packet loss or camera/network jitter detected. Try the camera main stream at H.264, 25 fps, CBR, and a lower bitrate/resolution; use channel 102 for preview.")
    if "non-monotonic dts" in lower or "timestamps are unset" in lower:
        warnings.append("RTSP timestamp problems detected. Camera timestamp mode is recommended; use wall-clock mode only as a fallback.")
    if "error while decoding" in lower or "bytestream" in lower:
        warnings.append("The camera stream contains corrupt frames. Lower the camera bitrate/frame rate or disable Smart Codec/H.264+/H.265+.")
    if "connection reset" in lower or "failed reading rtsp data" in lower:
        warnings.append("The RTSP connection dropped. Check camera/network stability and avoid opening the 4K main stream for preview.")
    return warnings


def iter_video_buffer_segments() -> List[Path]:
    """Return rolling-buffer segment files in all supported container formats.

    RTSP stream-copy mode can write either fragmented MP4 or MPEG-TS segments.
    The re-encode path and the default stream-copy path use MP4. Existing TS
    buffers from earlier releases are still recognised until they age out.
    """
    paths: List[Path] = []
    for suffix in ("*.ts", "*.mp4"):
        try:
            paths.extend(appstate.VIDEO_BUFFER_DIR.glob(suffix))
        except Exception:
            pass
    return paths


def recent_video_segments(max_age_seconds: Optional[int] = None) -> List[Path]:
    """List recent rolling-buffer video segments."""
    try:
        paths = sorted(iter_video_buffer_segments(), key=lambda p: p.stat().st_mtime, reverse=True)
    except Exception:
        return []
    if max_age_seconds is None:
        return paths
    cutoff = time.time() - max_age_seconds
    out = []
    for path in paths:
        try:
            if path.stat().st_mtime >= cutoff and path.stat().st_size > 0:
                out.append(path)
        except Exception:
            pass
    return out


def video_config_hash(cfg: Dict[str, Any]) -> str:
    """Return a hash representing the current video-recorder configuration."""
    material = {k: cfg.get(k) for k in ("video_enabled", "video_source_type", "video_usb_source", "video_rtsp_url", "video_preview_rtsp_url", "video_recording_mode", "video_copy_container", "video_rtsp_timestamp_mode", "video_preview_size", "video_preview_fps", "video_preview_jpeg_quality", "video_preview_keyframes_only", "video_ffmpeg_path", "video_segment_seconds", "public_branding_enabled", "public_branding_club_logo_enabled")}
    return hashlib.sha256(json.dumps(material, sort_keys=True).encode("utf-8")).hexdigest()


def reset_ptz_runtime_state(message: str = "Camera preset state reset after settings change.") -> None:
    """Forget the last camera preset and clear any paused-auth/manual-test state."""
    with PTZ_LOCK:
        PTZ_RUNTIME_STATE["last_preset"] = None
        PTZ_RUNTIME_STATE["pending_preset"] = None
        PTZ_RUNTIME_STATE["auth_failed"] = False
        PTZ_RUNTIME_STATE["manual_hold_until"] = None
        PTZ_RUNTIME_STATE["last_status"] = {"ok": True, "enabled": video_config().get("ptz_enabled"), "message": message}
        PTZ_RUNTIME_STATE["last_switch_at"] = None


def hold_ptz_after_manual_test(status: Dict[str, Any], seconds: int = 30) -> Dict[str, Any]:
    """Hold a successful manual PTZ test preset before automatic switching resumes."""
    if not status.get("ok"):
        return status
    hold_until = datetime.now() + timedelta(seconds=seconds)
    status = dict(status)
    status["manual_hold_until"] = hold_until.isoformat(timespec="seconds")
    status["message"] = (
        f"{status.get('message', 'Camera preset test succeeded.')} "
        f"Automatic preset switching is held for {seconds} seconds so you can see the test position."
    )
    with PTZ_LOCK:
        PTZ_RUNTIME_STATE["manual_hold_until"] = hold_until
        PTZ_RUNTIME_STATE["last_status"] = status
    return status


def ptz_manual_hold_status(now: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
    """Return the current PTZ status while a settings-page manual test hold is active."""
    now = now or datetime.now()
    with PTZ_LOCK:
        hold_until = PTZ_RUNTIME_STATE.get("manual_hold_until")
        if isinstance(hold_until, datetime) and hold_until > now:
            status = dict(PTZ_RUNTIME_STATE.get("last_status") or {})
            status.setdefault("ok", True)
            status["enabled"] = bool(video_config().get("ptz_enabled"))
            status["manual_hold_until"] = hold_until.isoformat(timespec="seconds")
            return status
        if hold_until is not None:
            PTZ_RUNTIME_STATE["manual_hold_until"] = None
    return None


def ptz_auth_failure_is_paused() -> bool:
    """Return True when automatic PTZ switching is paused after HTTP 401/403."""
    with PTZ_LOCK:
        return bool(PTZ_RUNTIME_STATE.get("auth_failed"))


def pause_ptz_after_auth_failure(status: Dict[str, Any]) -> None:
    """Pause automatic PTZ retries after a camera authentication failure."""
    status["auth_failed"] = True
    status["message"] = (
        f"{status.get('message', 'Camera preset authentication failed.')} "
        "Automatic PTZ switching has been paused to avoid locking the camera. "
        "Check the saved camera username/password, wait for the camera lockout to clear, then save settings or run one preset test."
    )
    with PTZ_LOCK:
        PTZ_RUNTIME_STATE["auth_failed"] = True
        PTZ_RUNTIME_STATE["pending_preset"] = None
        PTZ_RUNTIME_STATE["last_status"] = status


def ptz_preset_url(cfg: Dict[str, Any], preset_id: int) -> str:
    """Build the Hikvision-compatible ISAPI preset goto URL."""
    base = safe_ptz_camera_base_url(str(cfg.get("ptz_camera_url") or ""))
    channel = int_in_range(cfg.get("ptz_channel"), 1, 1, 32)
    preset = int_in_range(preset_id, 1, 1, 300)
    return f"{base}/ISAPI/PTZCtrl/channels/{channel}/presets/{preset}/goto"


def ptz_curl_put(url: str, username: str, password: str, method: str = "PUT", auth_mode: str = "digest", timeout: int = 4) -> Tuple[int, str]:
    """Call a Hikvision ISAPI URL using curl.

    The hut camera was originally proven with curl from a Windows command
    prompt.  Use the same executable for both the preset PUT and the separate
    credentials test.  The password is passed as one subprocess argument and is
    never written to app logs/status messages.
    """
    curl_path = shutil.which("curl.exe") or shutil.which("curl")
    if not curl_path:
        raise RuntimeError("curl.exe was not found on PATH; install curl or use the urllib fallback.")
    mode = normalise_ptz_auth_mode(auth_mode)
    auth_flag = {"digest": "--digest", "basic": "--basic", "anyauth": "--anyauth"}.get(mode, "--digest")
    cmd = [
        curl_path,
        auth_flag,
        "--user",
        f"{username}:{password}",
        "--request",
        method.upper(),
        "--silent",
        "--show-error",
        "--output",
        os.devnull,
        "--write-out",
        "%{http_code}",
        "--max-time",
        str(timeout),
        url,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 2)
    status_text = (result.stdout or "").strip()
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or f"curl exited {result.returncode}").strip()
        raise RuntimeError(detail)
    try:
        code = int(status_text[-3:])
    except ValueError as exc:
        raise RuntimeError(f"curl did not return an HTTP status code: {status_text!r}") from exc
    return code, f"curl-{mode}"


def ptz_urllib_put(url: str, base: str, username: str, password: str, method: str = "PUT", auth_mode: str = "digest", timeout: int = 4) -> Tuple[int, str]:
    """Call a Hikvision ISAPI URL using Python urllib authentication."""
    data = b"" if method.upper() in ("PUT", "POST", "PATCH") else None
    headers = {"Content-Length": "0"} if data is not None else {}
    req = urllib.request.Request(url, data=data, method=method.upper(), headers=headers)
    if username:
        password_mgr = urllib.request.HTTPPasswordMgrWithDefaultRealm()
        # Register both the origin and the exact ISAPI URL.  Some auth
        # implementations use the full path when asking the password manager.
        password_mgr.add_password(None, [base, url], username, password)
        mode = normalise_ptz_auth_mode(auth_mode)
        handlers = []
        if mode in ("digest", "anyauth"):
            handlers.append(urllib.request.HTTPDigestAuthHandler(password_mgr))
        if mode in ("basic", "anyauth"):
            handlers.append(urllib.request.HTTPBasicAuthHandler(password_mgr))
        opener = urllib.request.build_opener(*handlers)
        with opener.open(req, timeout=timeout) as response:
            return int(getattr(response, "status", response.getcode())), f"urllib-{mode}"
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return int(getattr(response, "status", response.getcode())), "urllib"


def ptz_isapi_request(url: str, base: str, username: str, password: str, method: str = "PUT", auth_mode: str = "digest", timeout: int = 4) -> Tuple[int, str]:
    """Call a Hikvision ISAPI URL using curl first, then urllib fallback."""
    if username:
        try:
            return ptz_curl_put(url, username, password, method=method, auth_mode=auth_mode, timeout=timeout)
        except Exception as curl_exc:
            try:
                code, used_method = ptz_urllib_put(url, base, username, password, method=method, auth_mode=auth_mode, timeout=timeout)
                return code, f"{used_method}; curl failed: {curl_exc}"
            except Exception:
                raise curl_exc
    return ptz_urllib_put(url, base, username, password, method=method, auth_mode=auth_mode, timeout=timeout)


def ptz_digest_put(url: str, base: str, username: str, password: str, timeout: int = 4) -> Tuple[int, str]:
    """Backward-compatible wrapper used by older tests/helpers."""
    return ptz_isapi_request(url, base, username, password, method="PUT", auth_mode="digest", timeout=timeout)


def ptz_capabilities_url(cfg: Dict[str, Any]) -> str:
    """Build a read-only Hikvision PTZ capabilities URL for login testing."""
    base = safe_ptz_camera_base_url(str(cfg.get("ptz_camera_url") or ""))
    channel = int_in_range(cfg.get("ptz_channel"), 1, 1, 32)
    return f"{base}/ISAPI/PTZCtrl/channels/{channel}/capabilities"


def ptz_test_login() -> Dict[str, Any]:
    """Test camera credentials with a single read-only ISAPI GET."""
    cfg = video_config()
    base = safe_ptz_camera_base_url(str(cfg.get("ptz_camera_url") or ""))
    if not base:
        status = {"ok": False, "enabled": bool(cfg.get("ptz_enabled")), "message": "No valid camera URL is configured for PTZ login test."}
        with PTZ_LOCK:
            PTZ_RUNTIME_STATE["last_status"] = status
        return status
    url = ptz_capabilities_url(cfg)
    username = str(cfg.get("ptz_username") or "")
    password = str(cfg.get("ptz_password") or "")
    auth_mode = normalise_ptz_auth_mode(cfg.get("ptz_auth_mode"))
    try:
        code, auth_method = ptz_isapi_request(url, base, username, password, method="GET", auth_mode=auth_mode, timeout=4)
        ok = 200 <= code < 300
        status = {
            "ok": ok,
            "enabled": bool(cfg.get("ptz_enabled")),
            "http_status": code,
            "auth_method": auth_method,
            "auth_mode": auth_mode,
            "url": url,
            "username_set": bool(username),
            "password_set": bool(password),
            "message": "Camera login test succeeded." if ok else f"Camera login test returned HTTP {code} using {auth_method}.",
        }
        if code in (401, 403):
            pause_ptz_after_auth_failure(status)
        else:
            with PTZ_LOCK:
                PTZ_RUNTIME_STATE["last_status"] = status
                if ok:
                    PTZ_RUNTIME_STATE["auth_failed"] = False
        return status
    except Exception as exc:
        status = {
            "ok": False,
            "enabled": bool(cfg.get("ptz_enabled")),
            "auth_mode": auth_mode,
            "url": url,
            "username_set": bool(username),
            "password_set": bool(password),
            "message": f"Camera login test failed: {exc}",
        }
        with PTZ_LOCK:
            PTZ_RUNTIME_STATE["last_status"] = status
        return status


def ptz_goto_preset(which: str, reason: str = "", race_id: Optional[int] = None, force: bool = False) -> Dict[str, Any]:
    """Send the camera to the configured idle or recording preset.

    Hikvision cameras use HTTP authentication and a PUT to
    ``/ISAPI/PTZCtrl/channels/1/presets/<id>/goto``.  The function avoids
    duplicate calls once a preset is already active, so the 4 Hz start scheduler
    can safely ask for the current desired camera view.
    """
    cfg = video_config()
    enabled = bool(cfg.get("ptz_enabled"))
    preset_kind = "recording" if str(which).lower() in ("recording", "race", "active", "start", "finish") else "idle"
    preset = int(cfg.get("ptz_recording_preset" if preset_kind == "recording" else "ptz_idle_preset") or 0)
    if not enabled:
        status = {"ok": True, "enabled": False, "message": "Camera preset control is disabled."}
        with PTZ_LOCK:
            PTZ_RUNTIME_STATE["last_status"] = status
        return status
    if not force and ptz_auth_failure_is_paused():
        with PTZ_LOCK:
            status = dict(PTZ_RUNTIME_STATE.get("last_status") or {})
            status.setdefault("ok", False)
            status["enabled"] = True
            status["auth_failed"] = True
            status.setdefault("message", "Automatic PTZ switching is paused after a camera authentication failure.")
            return status
    base = safe_ptz_camera_base_url(str(cfg.get("ptz_camera_url") or ""))
    if not base:
        status = {"ok": False, "enabled": True, "message": "Camera preset control is enabled, but no valid camera URL is configured."}
        with PTZ_LOCK:
            PTZ_RUNTIME_STATE["last_status"] = status
        return status
    target_key = f"{base}|ch{cfg.get('ptz_channel')}|preset{preset}"
    with PTZ_LOCK:
        if not force and PTZ_RUNTIME_STATE.get("last_preset") == target_key and (PTZ_RUNTIME_STATE.get("last_status") or {}).get("ok"):
            return dict(PTZ_RUNTIME_STATE.get("last_status") or {})
    url = ptz_preset_url(cfg, preset)
    message_reason = f" ({reason})" if reason else ""
    try:
        username = str(cfg.get("ptz_username") or "")
        password = str(cfg.get("ptz_password") or "")
        auth_mode = normalise_ptz_auth_mode(cfg.get("ptz_auth_mode"))
        code, auth_method = ptz_isapi_request(url, base, username, password, method="PUT", auth_mode=auth_mode, timeout=4)
        ok = 200 <= code < 300
        status = {
            "ok": ok,
            "enabled": True,
            "preset": preset,
            "preset_kind": preset_kind,
            "auth_method": auth_method,
            "auth_mode": auth_mode,
            "http_status": code,
            "url": url,
            "username_set": bool(username),
            "password_set": bool(password),
            "manual_curl_hint": f"curl --{auth_mode if auth_mode != 'anyauth' else 'anyauth'} -u {username}:<password> -X PUT {url}" if username else f"curl -X PUT {url}",
            "message": f"Camera preset {preset} selected for {preset_kind} view{message_reason} using {auth_method}." if ok else f"Camera returned HTTP {code} for preset {preset} using {auth_method}.",
        }
        if code in (401, 403):
            pause_ptz_after_auth_failure(status)
            return status
        with PTZ_LOCK:
            if ok:
                PTZ_RUNTIME_STATE["last_preset"] = target_key
                PTZ_RUNTIME_STATE["last_switch_at"] = datetime.now().isoformat(timespec="seconds")
                PTZ_RUNTIME_STATE["auth_failed"] = False
            PTZ_RUNTIME_STATE["last_status"] = status
        if ok:
            log_event(race_id, "camera", f"Camera preset {preset} ({preset_kind})", "ptz-camera", {"preset": preset, "preset_kind": preset_kind, "reason": reason})
        return status
    except Exception as exc:
        http_status = getattr(exc, "code", None) or getattr(exc, "status", None)
        auth_mode = normalise_ptz_auth_mode(cfg.get("ptz_auth_mode"))
        status = {"ok": False, "enabled": True, "preset": preset, "preset_kind": preset_kind, "auth_mode": auth_mode, "url": url, "message": f"Could not select camera preset {preset}: {exc}"}
        if http_status:
            status["http_status"] = int(http_status)
        if int(http_status or 0) in (401, 403):
            pause_ptz_after_auth_failure(status)
            return status
        with PTZ_LOCK:
            PTZ_RUNTIME_STATE["last_status"] = status
        return status


def queue_ptz_goto_preset(which: str, reason: str = "", race_id: Optional[int] = None) -> Dict[str, Any]:
    """Queue a camera preset change without blocking the start scheduler."""
    cfg = video_config()
    if not cfg.get("ptz_enabled"):
        return ptz_runtime_status()
    preset_kind = "recording" if str(which).lower() in ("recording", "race", "active", "start", "finish") else "idle"
    if ptz_auth_failure_is_paused():
        with PTZ_LOCK:
            status = dict(PTZ_RUNTIME_STATE.get("last_status") or {})
            status.setdefault("ok", False)
            status["enabled"] = True
            status["auth_failed"] = True
            status.setdefault("message", "Automatic PTZ switching is paused after a camera authentication failure.")
            return status
    base = safe_ptz_camera_base_url(str(cfg.get("ptz_camera_url") or ""))
    preset = int(cfg.get("ptz_recording_preset" if preset_kind == "recording" else "ptz_idle_preset") or 0)
    target_key = f"{base}|ch{cfg.get('ptz_channel')}|preset{preset}"
    with PTZ_LOCK:
        current_status = dict(PTZ_RUNTIME_STATE.get("last_status") or {})
        if current_status.get("ok") and PTZ_RUNTIME_STATE.get("last_preset") == target_key:
            return current_status
        if PTZ_RUNTIME_STATE.get("pending_preset") == target_key:
            return current_status or {"ok": True, "enabled": True, "message": "Camera preset change already queued."}
        PTZ_RUNTIME_STATE["pending_preset"] = target_key
        PTZ_RUNTIME_STATE["last_status"] = {"ok": True, "enabled": True, "message": f"Camera preset {preset} ({preset_kind}) queued."}

    def worker() -> None:
        try:
            ptz_goto_preset(preset_kind, reason=reason, race_id=race_id, force=True)
        finally:
            with PTZ_LOCK:
                if PTZ_RUNTIME_STATE.get("pending_preset") == target_key:
                    PTZ_RUNTIME_STATE["pending_preset"] = None

    threading.Thread(target=worker, name=f"ptz-{preset_kind}-{preset}", daemon=True).start()
    return ptz_runtime_status()


def ptz_runtime_status() -> Dict[str, Any]:
    """Return the last PTZ camera-control status for Settings display."""
    cfg = video_config()
    with PTZ_LOCK:
        status = dict(PTZ_RUNTIME_STATE.get("last_status") or {})
        status.setdefault("ok", True)
        status["enabled"] = bool(cfg.get("ptz_enabled"))
        status["last_switch_at"] = PTZ_RUNTIME_STATE.get("last_switch_at")
        status["last_preset"] = PTZ_RUNTIME_STATE.get("last_preset")
        status["pending_preset"] = PTZ_RUNTIME_STATE.get("pending_preset")
        hold_until = PTZ_RUNTIME_STATE.get("manual_hold_until")
        if isinstance(hold_until, datetime) and hold_until > datetime.now():
            status["manual_hold_until"] = hold_until.isoformat(timespec="seconds")
        status["auth_failed"] = bool(PTZ_RUNTIME_STATE.get("auth_failed") or status.get("auth_failed"))
        return status


def race_needs_recording_camera_preset(race: sqlite3.Row, now: Optional[datetime] = None, cfg: Optional[Dict[str, Any]] = None) -> bool:
    """Return True when a race should use the start/finish camera preset.

    The idle preset is used before the sequence.  At the configured number of
    seconds before the first scheduled start, the camera moves to the recording
    preset and stays there until all entries in that race are no longer racing.
    """
    if not race:
        return False
    if race_has_finished_for_sequence(int(race["id"])):
        return False
    cfg = cfg or video_config()
    now = now or datetime.now()
    starts = [s.get("dt") for s in race_start_schedule(race) if s.get("dt")]
    if not starts:
        return False
    first_start = min(starts)
    lead = int_in_range(cfg.get("ptz_pre_start_seconds"), 30, 0, 600)
    return now >= first_start - timedelta(seconds=lead)


def update_camera_preset_for_races(races: Iterable[sqlite3.Row], now: Optional[datetime] = None) -> Dict[str, Any]:
    """Set the camera preset according to all known active races."""
    cfg = video_config()
    if not cfg.get("ptz_enabled"):
        return ptz_runtime_status()
    now = now or datetime.now()
    manual_status = ptz_manual_hold_status(now)
    if manual_status is not None:
        return manual_status
    for race in races:
        if race_needs_recording_camera_preset(race, now, cfg):
            return queue_ptz_goto_preset("recording", reason=f"race {race['id']} start/finish window", race_id=int(race["id"]))
    return queue_ptz_goto_preset("idle", reason="no race in start/finish window")


def ffmpeg_process_is_running(proc: Any) -> bool:
    """Return True when a stored subprocess is still alive."""
    return bool(proc is not None and getattr(proc, "poll", lambda: 1)() is None)


def video_process_is_running() -> bool:
    """Return whether the main FFmpeg recorder process is still alive."""
    return ffmpeg_process_is_running(VIDEO_RUNTIME_STATE.get("process"))


def video_preview_process_is_running() -> bool:
    """Return whether the optional FFmpeg live-preview process is still alive."""
    return ffmpeg_process_is_running(VIDEO_RUNTIME_STATE.get("preview_process"))


def stop_ffmpeg_process(proc: Any) -> None:
    """Terminate an FFmpeg subprocess, falling back to kill if needed."""
    if proc is not None and ffmpeg_process_is_running(proc):
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass


_EXIT_HOOK_REGISTERED = False


def stop_ffmpeg_children_at_exit() -> None:
    """Kill the recorder and preview when the app stops.

    Nothing did. FFmpeg is a child process, not a thread, so closing the app left
    it running. Confirmed both ways: before this, a parent that exited cleanly
    left its recorder alive behind it; after it, the child goes with the parent.

    They accumulate one or two per run of the app, hold 40-110 MB each, and keep
    the recorder log open -- which is the other reason it could not be rotated.
    An orphan pointed at a camera that is not answering turns out to be mostly
    idle, so this is about file handles and tidiness rather than CPU.

    Best effort and silent: this runs while the interpreter is shutting down,
    where raising achieves nothing and hides the exit.
    """
    for key in ("process", "preview_process"):
        try:
            stop_ffmpeg_process(VIDEO_RUNTIME_STATE.get(key))
        except Exception:
            pass


def register_ffmpeg_exit_hook() -> None:
    """Register the shutdown killer once."""
    global _EXIT_HOOK_REGISTERED
    if _EXIT_HOOK_REGISTERED:
        return
    _EXIT_HOOK_REGISTERED = True
    atexit.register(stop_ffmpeg_children_at_exit)


def stop_video_recorder_locked(message: str = "Video recorder stopped.") -> None:
    """Stop FFmpeg recorder/preview processes while holding the video lock."""
    stop_ffmpeg_process(VIDEO_RUNTIME_STATE.get("process"))
    stop_ffmpeg_process(VIDEO_RUNTIME_STATE.get("preview_process"))
    VIDEO_RUNTIME_STATE["process"] = None
    VIDEO_RUNTIME_STATE["preview_process"] = None
    VIDEO_RUNTIME_STATE["config_hash"] = None
    VIDEO_RUNTIME_STATE["last_status"] = {"ok": False, "enabled": False, "message": message}


def cleanup_video_segments(cfg: Optional[Dict[str, Any]] = None) -> None:
    """Delete old rolling-buffer video segments beyond the retention window."""
    cfg = cfg or video_config()
    appstate.VIDEO_BUFFER_DIR.mkdir(parents=True, exist_ok=True)
    cutoff = time.time() - int(cfg.get("video_buffer_minutes", 20)) * 60
    for path in iter_video_buffer_segments():
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)
        except Exception:
            pass


def build_video_recorder_command(ffmpeg: str, cfg: Dict[str, Any]) -> List[str]:
    """Build the FFmpeg command for the rolling evidence recorder.

    In ``copy`` mode, used for RTSP/IP cameras, this command records only the
    main camera stream into small rolling buffer segments. Fragmented MP4 is the
    default, with MPEG-TS available as an advanced fallback. The live JPEG
    preview runs in a separate FFmpeg process so a flaky preview/sub-stream
    cannot stop the evidence recorder.

    In ``reencode`` mode, used for USB webcams and as a fallback, one FFmpeg
    process still splits the decoded input into rolling MP4 segments plus the
    live JPEG preview. That avoids trying to open the same USB webcam twice.
    """
    segment_seconds = int(cfg.get("video_segment_seconds") or 5)
    mode = normalise_video_recording_mode(str(cfg.get("video_source_type") or "usb"), cfg.get("video_recording_mode"))
    copy_container = normalise_video_copy_container(cfg.get("video_copy_container"))
    segment_ext = copy_container if mode == "copy" else "mp4"
    pattern = str(appstate.VIDEO_BUFFER_DIR / f"%Y%m%dT%H%M%S.{segment_ext}")
    live_jpg = str(appstate.VIDEO_LIVE_JPG_PATH)
    base_cmd = [str(ffmpeg), "-y", "-nostdin", "-hide_banner", "-loglevel", "warning"]

    if mode == "copy":
        cmd = [
            *base_cmd,
            *video_input_args(cfg, preview=False),
            "-map", "0:v:0", "-an",
            "-c:v", "copy",
            "-muxdelay", "0", "-muxpreload", "0",
            "-f", "segment",
            "-segment_time", str(segment_seconds),
            "-break_non_keyframes", "1",
            "-strftime", "1",
            "-reset_timestamps", "1",
        ]
        if copy_container == "ts":
            cmd.extend([
                "-segment_format", "mpegts",
                "-segment_format_options", "mpegts_flags=+resend_headers",
                pattern,
            ])
        else:
            # Fragmented MP4 keeps the no-reencode path, but avoids waiting for
            # a whole file moov atom and starts producing useful segments quickly.
            # This is the safer default for the Hikvision HEVC stream tested at
            # the hut; MPEG-TS remains an advanced fallback in Settings.
            cmd.extend([
                "-segment_format", "mp4",
                "-segment_format_options", "movflags=+frag_keyframe+empty_moov+default_base_moof",
                pattern,
            ])
        return cmd

    input_args = video_input_args(cfg)
    # Split the camera input inside the single FFmpeg recorder process: one
    # branch writes the rolling MP4 segments, the other updates a near-live JPEG
    # preview for the race officer UI. This avoids opening a USB webcam twice,
    # which often fails on Windows.
    preview_filter = build_video_preview_filter(cfg)
    filter_complex = f"[0:v]{video_timestamp_overlay_filter()},split=2[vseg][vjpg];[vjpg]{preview_filter}[vjpgout]"
    return [
        *base_cmd,
        *input_args,
        "-filter_complex", filter_complex,
        "-map", "[vseg]", "-an",
        "-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency", "-pix_fmt", "yuv420p",
        "-force_key_frames", f"expr:gte(t,n_forced*{segment_seconds})",
        "-f", "segment", "-segment_time", str(segment_seconds), "-strftime", "1", "-reset_timestamps", "1", pattern,
        "-map", "[vjpgout]", "-an", "-q:v", str(int_in_range(cfg.get("video_preview_jpeg_quality"), 3, 2, 12)), "-update", "1", live_jpg,
    ]


def build_video_preview_command(ffmpeg: str, cfg: Dict[str, Any]) -> Optional[List[str]]:
    """Build a separate FFmpeg command for the RTSP live JPEG preview.

    The separate command is only used in RTSP stream-copy mode. It decodes the
    preview/sub-stream, preferably Hikvision channel 102, and writes the latest
    JPEG for the web pages. It is deliberately independent of the main recorder:
    if the preview stream drops, evidence recording can continue.
    """
    mode = normalise_video_recording_mode(str(cfg.get("video_source_type") or "usb"), cfg.get("video_recording_mode"))
    if str(cfg.get("video_source_type") or "usb").lower() != "rtsp" or mode != "copy":
        return None
    return [
        str(ffmpeg), "-y", "-nostdin", "-hide_banner", "-loglevel", "warning",
        *video_input_args(cfg, preview=True),
        "-map", "0:v:0", "-an",
        "-vf", build_video_preview_filter(cfg),
        "-q:v", str(int_in_range(cfg.get("video_preview_jpeg_quality"), 3, 2, 12)), "-update", "1", str(appstate.VIDEO_LIVE_JPG_PATH),
    ]


def jpeg_dimensions(path: Path) -> Tuple[int, int]:
    """Read JPEG dimensions without adding an image-processing dependency."""
    try:
        data = Path(path).read_bytes()
        i = 0
        while i + 9 < len(data):
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                height = int.from_bytes(data[i + 5:i + 7], "big")
                width = int.from_bytes(data[i + 7:i + 9], "big")
                if width > 0 and height > 0:
                    return width, height
                break
            if marker in (0xD8, 0xD9):
                i += 2
                continue
            if i + 4 > len(data):
                break
            seg_len = int.from_bytes(data[i + 2:i + 4], "big")
            if seg_len <= 0:
                break
            i += 2 + seg_len
    except Exception:
        pass
    return 1280, 720


def public_live_branding_cache_key(source_path: Path, cfg: Dict[str, Any], logo_inputs: List[str], rotation_index: Optional[int] = None) -> str:
    """Return a cache key for the branded public live JPEG."""
    items: List[Dict[str, Any]] = []
    for path_text in [str(source_path), *logo_inputs]:
        try:
            st = Path(path_text).stat()
            items.append({"path": str(path_text), "mtime_ns": st.st_mtime_ns, "size": st.st_size})
        except OSError:
            items.append({"path": str(path_text), "missing": True})
    material = {
        "app_version": appstate.APP_VERSION,
        "settings": {
            "public_branding_enabled": cfg.get("public_branding_enabled"),
            "public_branding_club_logo_enabled": cfg.get("public_branding_club_logo_enabled"),
            "video_preview_jpeg_quality": cfg.get("video_preview_jpeg_quality"),
            "public_branding_rotation_seconds": PUBLIC_BRANDING_ROTATION_SECONDS,
            "public_branding_rotation_index": rotation_index,
        },
        "files": items,
    }
    return hashlib.sha256(json.dumps(material, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def build_public_live_branding_command(ffmpeg: str, source_path: Path, out_path: Path, cfg: Dict[str, Any]) -> Optional[Tuple[List[str], str]]:
    """Build the FFmpeg command that burns public logos into the live JPEG."""
    width, height = jpeg_dimensions(source_path)
    sponsor_total = sum(1 for kind, _path in _branding_logo_items(sponsor_count=8) if kind == "sponsor")
    rotation_index = current_public_branding_rotation_index(sponsor_total) if sponsor_total > 1 else None
    logo_inputs, filter_complex, output_label = build_public_live_branding_overlay_filter(width, height, sponsor_count=8, rotation_index=rotation_index)
    if not filter_complex:
        return None
    cache_key = public_live_branding_cache_key(source_path, cfg, logo_inputs, rotation_index=rotation_index)
    cmd = [str(ffmpeg), "-y", "-hide_banner", "-loglevel", "error", "-i", str(source_path)]
    for logo in logo_inputs:
        cmd.extend(["-loop", "1", "-i", logo])
    cmd.extend([
        "-filter_complex", filter_complex,
        "-map", f"[{output_label}]",
        "-frames:v", "1",
        "-q:v", str(int_in_range(cfg.get("video_preview_jpeg_quality"), 3, 2, 12)),
        str(out_path),
    ])
    return cmd, cache_key


def public_live_frame_path() -> Path:
    """Return a branded public live JPEG, generating it from the current preview.

    The public competitor page serves the branded JPEG itself rather than drawing
    logos as browser overlays.  That means any screenshot or saved image from the
    public live feed includes the PSC and sponsor logos, matching the public
    start/finish videos.  If branding generation fails, fall back to the raw live
    frame rather than breaking the camera view.
    """
    source_path = appstate.VIDEO_LIVE_JPG_PATH
    if not source_path.exists() or source_path.stat().st_size <= 0:
        return source_path
    cfg = video_config()
    if not text_to_bool(cfg.get("public_branding_enabled"), True):
        return source_path
    ffmpeg = str(cfg.get("video_ffmpeg_path") or "ffmpeg")
    appstate.VIDEO_LIVE_DIR.mkdir(parents=True, exist_ok=True)
    command_info = build_public_live_branding_command(ffmpeg, source_path, appstate.VIDEO_PUBLIC_LIVE_JPG_PATH, cfg)
    if not command_info:
        return source_path
    cmd, cache_key = command_info
    try:
        if appstate.VIDEO_PUBLIC_LIVE_JPG_PATH.exists() and appstate.VIDEO_PUBLIC_LIVE_HASH_PATH.exists():
            if appstate.VIDEO_PUBLIC_LIVE_HASH_PATH.read_text(encoding="utf-8").strip() == cache_key:
                if appstate.VIDEO_PUBLIC_LIVE_JPG_PATH.stat().st_size > 0:
                    return appstate.VIDEO_PUBLIC_LIVE_JPG_PATH
    except OSError:
        pass

    with PUBLIC_LIVE_BRANDING_LOCK:
        try:
            if appstate.VIDEO_PUBLIC_LIVE_JPG_PATH.exists() and appstate.VIDEO_PUBLIC_LIVE_HASH_PATH.exists():
                if appstate.VIDEO_PUBLIC_LIVE_HASH_PATH.read_text(encoding="utf-8").strip() == cache_key:
                    if appstate.VIDEO_PUBLIC_LIVE_JPG_PATH.stat().st_size > 0:
                        return appstate.VIDEO_PUBLIC_LIVE_JPG_PATH
            tmp_path = appstate.VIDEO_PUBLIC_LIVE_JPG_PATH.with_suffix(".tmp.jpg")
            cmd[-1] = str(tmp_path)
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            if proc.returncode != 0:
                raise RuntimeError((proc.stderr or proc.stdout or f"FFmpeg exited with {proc.returncode}").strip()[:500])
            tmp_path.replace(appstate.VIDEO_PUBLIC_LIVE_JPG_PATH)
            appstate.VIDEO_PUBLIC_LIVE_HASH_PATH.write_text(cache_key, encoding="utf-8")
            return appstate.VIDEO_PUBLIC_LIVE_JPG_PATH
        except Exception as exc:
            try:
                with appstate.VIDEO_LOG_PATH.open("a", encoding="utf-8", errors="replace") as log:
                    log.write(f"\nPublic live branding failed {datetime.now().isoformat(timespec='seconds')}: {exc}\n")
            except Exception:
                pass
            return source_path


# The recorder log is FFmpeg's stderr, appended to for the life of the app and
# never rotated. A camera that will not answer writes several lines per attempt,
# and the club's had reached 65 MB. Two generations, so the last failure before
# this one is still readable.
VIDEO_LOG_MAX_BYTES = 8 * 1024 * 1024


def rotate_video_log_if_large(path: Path, max_bytes: int = VIDEO_LOG_MAX_BYTES) -> bool:
    """Move the log aside once it is too big. True when it was rotated.

    Called before a launch, when the previous FFmpeg is not running and nothing
    holds the handle -- a rename under an open handle fails on Windows. Failure
    is not worth reporting: a log that could not be rotated is still a log.
    """
    try:
        if path.stat().st_size <= max_bytes:
            return False
    except OSError:
        return False
    try:
        path.replace(path.with_suffix(path.suffix + ".1"))
        return True
    except OSError:
        return False


def launch_ffmpeg_process(cmd: List[str], log_heading: str) -> Tuple[Optional[subprocess.Popen], Optional[int], str]:
    """Launch an FFmpeg command and return process, immediate return code and command text."""
    cmd_for_log = " ".join(str(part) for part in cmd)
    rotate_video_log_if_large(appstate.VIDEO_LOG_PATH)
    with appstate.VIDEO_LOG_PATH.open("a", encoding="utf-8", errors="replace") as log:
        log.write("\n=== " + log_heading + " " + datetime.now().isoformat(timespec="seconds") + " ===\n")
        log.write(cmd_for_log + "\n")
    log_handle = appstate.VIDEO_LOG_PATH.open("ab")
    proc = subprocess.Popen(cmd, stdout=log_handle, stderr=log_handle)
    try:
        log_handle.close()
    except Exception:
        pass
    time.sleep(0.25)
    return proc, proc.poll(), cmd_for_log


def live_preview_appears_stalled() -> bool:
    """Return True if the preview process is up but the live JPEG has gone stale.

    ffmpeg does not exit when an RTSP read blocks, so a dropped/starved camera
    connection leaves the preview alive but no longer overwriting the JPEG. Only
    judge once the preview has been running longer than the stall window, so a
    freshly launched preview (or a stale JPEG left over from a previous run) is
    never mistaken for a hang. Caller must hold VIDEO_LOCK.
    """
    started = float(VIDEO_RUNTIME_STATE.get("preview_started_at") or 0)
    if not started or (time.time() - started) < PREVIEW_STALL_SECONDS:
        return False
    try:
        if not appstate.VIDEO_LIVE_JPG_PATH.exists():
            return True
        age = time.time() - appstate.VIDEO_LIVE_JPG_PATH.stat().st_mtime
        return age > PREVIEW_STALL_SECONDS
    except OSError:
        return False


def start_video_background_recorder() -> None:
    """Start or restart the rolling FFmpeg buffer when settings change."""
    cfg = video_config()
    cfg_hash = video_config_hash(cfg)
    with VIDEO_LOCK:
        if not cfg.get("video_enabled"):
            if video_process_is_running() or video_preview_process_is_running():
                stop_video_recorder_locked("Video recording is disabled.")
            else:
                VIDEO_RUNTIME_STATE["last_status"] = {"ok": False, "enabled": False, "message": "Video recording is disabled."}
            return
        # A launch that failed a moment ago, with these same settings, is not
        # worth 250 ms of every request until something changes.
        failed_at = float(VIDEO_RUNTIME_STATE.get("failed_launch_at") or 0.0)
        if (not video_process_is_running()
                and VIDEO_RUNTIME_STATE.get("failed_launch_hash") == cfg_hash
                and (time.time() - failed_at) < VIDEO_RETRY_SECONDS):
            return
        if video_process_is_running() and VIDEO_RUNTIME_STATE.get("config_hash") == cfg_hash:
            # In RTSP stream-copy mode, keep the live preview alive separately;
            # restarting it must not interrupt evidence recording.
            ffmpeg = ffmpeg_executable(cfg)
            mode = normalise_video_recording_mode(str(cfg.get("video_source_type") or "usb"), cfg.get("video_recording_mode"))
            # Watchdog: a preview that is alive but has stopped writing fresh JPEGs
            # (RTSP read stalled) never exits on its own, so kill it here and let the
            # relaunch below bring it back. Without this the public live image freezes
            # until the whole app is restarted.
            if video_preview_process_is_running() and live_preview_appears_stalled():
                stop_ffmpeg_process(VIDEO_RUNTIME_STATE.get("preview_process"))
                VIDEO_RUNTIME_STATE["preview_process"] = None
                VIDEO_RUNTIME_STATE["preview_started_at"] = None
                try:
                    with appstate.VIDEO_LOG_PATH.open("a", encoding="utf-8", errors="replace") as log:
                        log.write("\n=== Restarting stalled live preview (no fresh frame) "
                                  + datetime.now().isoformat(timespec="seconds") + " ===\n")
                except Exception:
                    pass
            if ffmpeg and mode == "copy" and str(cfg.get("video_source_type") or "").lower() == "rtsp" and not video_preview_process_is_running():
                try:
                    preview_cmd = build_video_preview_command(ffmpeg, cfg)
                    if preview_cmd:
                        preview_proc, preview_rc, preview_text = launch_ffmpeg_process(preview_cmd, "Starting preview")
                        VIDEO_RUNTIME_STATE["preview_process"] = None if preview_rc is not None else preview_proc
                        VIDEO_RUNTIME_STATE["preview_started_at"] = time.time() if preview_rc is None else None
                        VIDEO_RUNTIME_STATE["last_preview_command"] = preview_text
                except Exception:
                    # Preview is useful but not critical for evidence recording.
                    VIDEO_RUNTIME_STATE["preview_process"] = None
            if time.time() - float(VIDEO_RUNTIME_STATE.get("last_cleanup_at") or 0) > 60:
                cleanup_video_segments(cfg)
                VIDEO_RUNTIME_STATE["last_cleanup_at"] = time.time()
            return
        if video_process_is_running() or video_preview_process_is_running():
            stop_video_recorder_locked("Restarting video recorder after settings change.")

        ffmpeg = ffmpeg_executable(cfg)
        if not ffmpeg:
            VIDEO_RUNTIME_STATE["last_status"] = {"ok": False, "enabled": True, "message": "FFmpeg was not found. Install FFmpeg or set its full path in Settings."}
            return
        try:
            appstate.VIDEO_BUFFER_DIR.mkdir(parents=True, exist_ok=True)
            appstate.VIDEO_CLIPS_DIR.mkdir(parents=True, exist_ok=True)
            appstate.VIDEO_LIVE_DIR.mkdir(parents=True, exist_ok=True)
            appstate.VIDEO_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            recorder_cmd = build_video_recorder_command(ffmpeg, cfg)
            recorder_proc, recorder_rc, recorder_text = launch_ffmpeg_process(recorder_cmd, "Starting recorder")
            VIDEO_RUNTIME_STATE["last_command"] = recorder_text
            VIDEO_RUNTIME_STATE["last_cleanup_at"] = time.time()
            if recorder_rc is not None:
                tail = tail_text_file(appstate.VIDEO_LOG_PATH)
                VIDEO_RUNTIME_STATE["last_status"] = {"ok": False, "enabled": True, "message": f"Video recorder stopped immediately with exit code {recorder_rc}. Check camera source and FFmpeg settings.", "log_tail": tail, "command": recorder_text}
                VIDEO_RUNTIME_STATE["process"] = None
                VIDEO_RUNTIME_STATE["preview_process"] = None
                VIDEO_RUNTIME_STATE["config_hash"] = None
                VIDEO_RUNTIME_STATE["failed_launch_at"] = time.time()
                VIDEO_RUNTIME_STATE["failed_launch_hash"] = cfg_hash
                return

            register_ffmpeg_exit_hook()
            VIDEO_RUNTIME_STATE["process"] = recorder_proc
            VIDEO_RUNTIME_STATE["config_hash"] = cfg_hash
            VIDEO_RUNTIME_STATE["started_at"] = time.time()
            VIDEO_RUNTIME_STATE["failed_launch_at"] = 0.0
            VIDEO_RUNTIME_STATE["failed_launch_hash"] = None

            preview_cmd = build_video_preview_command(ffmpeg, cfg)
            preview_message = ""
            if preview_cmd:
                try:
                    preview_proc, preview_rc, preview_text = launch_ffmpeg_process(preview_cmd, "Starting preview")
                    VIDEO_RUNTIME_STATE["last_preview_command"] = preview_text
                    if preview_rc is None:
                        VIDEO_RUNTIME_STATE["preview_process"] = preview_proc
                        VIDEO_RUNTIME_STATE["preview_started_at"] = time.time()
                    else:
                        VIDEO_RUNTIME_STATE["preview_process"] = None
                        VIDEO_RUNTIME_STATE["preview_started_at"] = None
                        preview_message = " Live preview did not start, but the evidence recorder is running."
                except Exception as exc:
                    VIDEO_RUNTIME_STATE["preview_process"] = None
                    VIDEO_RUNTIME_STATE["preview_started_at"] = None
                    preview_message = f" Live preview did not start: {exc}. Evidence recording is still running."

            copy_detail = f"/{cfg.get('video_copy_container', 'mp4')}" if cfg.get('video_recording_mode') == 'copy' else ""
            VIDEO_RUNTIME_STATE["last_status"] = {"ok": True, "enabled": True, "message": f"Video recorder running from {cfg['video_source_type']} source in {cfg.get('video_recording_mode', 'reencode')}{copy_detail} mode. Waiting for buffered segments..." + preview_message, "log_tail": tail_text_file(appstate.VIDEO_LOG_PATH), "command": recorder_text, "preview_command": VIDEO_RUNTIME_STATE.get("last_preview_command") or ""}
        except Exception as exc:
            VIDEO_RUNTIME_STATE["process"] = None
            VIDEO_RUNTIME_STATE["preview_process"] = None
            VIDEO_RUNTIME_STATE["failed_launch_at"] = time.time()
            VIDEO_RUNTIME_STATE["failed_launch_hash"] = cfg_hash
            VIDEO_RUNTIME_STATE["last_status"] = {"ok": False, "enabled": True, "message": f"Could not start video recorder: {exc}", "log_tail": tail_text_file(appstate.VIDEO_LOG_PATH)}


def recorder_appears_stalled(cfg: Dict[str, Any], started_at: float) -> bool:
    """True when the recorder is alive but has stopped writing segments.

    Generous on purpose: several segment lengths plus half a minute, and never
    within that window of starting, because a recorder that has only just been
    launched has not written anything yet and restarting it in a loop would
    guarantee the very silence this is here to prevent.
    """
    segment = max(1, int(cfg.get("video_segment_seconds") or 5))
    window = segment * 4 + 30
    if (time.time() - started_at) < window:
        return False
    return not recent_video_segments(max_age_seconds=window)


def _log_recorder_restart(reason: str) -> None:
    """Record a restart, at most once every ten minutes.

    A camera that is unplugged fails every time the watchdog looks, and an
    activity log with two restarts a minute in it is one nobody can read the
    rest of. The recorder's own status carries the running count for whoever
    wants the detail.
    """
    now = time.time()
    with VIDEO_LOCK:
        last = float(VIDEO_RUNTIME_STATE.get("last_restart_log_at") or 0)
        quiet = (now - last) < 600
        if not quiet:
            VIDEO_RUNTIME_STATE["last_restart_log_at"] = now
        count = int(VIDEO_RUNTIME_STATE.get("restarts") or 0)
    if quiet:
        return
    log_activity("video recorder restarted", f"{reason}"
                 + (f" (restart {count + 1} since the app started)" if count else ""),
                 user="system")
    try:
        with appstate.VIDEO_LOG_PATH.open("a", encoding="utf-8", errors="replace") as log:
            log.write(f"\n=== Watchdog restart: {reason} "
                      + datetime.now().isoformat(timespec="seconds") + " ===\n")
    except Exception:
        pass


def video_watchdog_tick() -> bool:
    """Bring the recorder back if it has died. Returns True if it restarted one.

    Nothing supervised it before. `start_video_background_recorder` was called
    at app start, when video settings were saved, and when a clip was scheduled
    or built -- so a recorder that stopped in the small hours stayed stopped.
    The log shows both halves of that: one recorder ended on 13 August and was
    not started again until somebody saved a setting two days later, while the
    live preview (which did have a watchdog) was restarted overnight and kept
    the dashboard looking healthy throughout.

    The race was lost to the other half. The recorder stayed *alive* and stopped
    writing at about 03:00, so the buffer trim went on running and deleted every
    closed segment as it aged out -- except the one FFmpeg still had open, which
    Windows refuses to delete while another process holds it. Hence a folder
    holding exactly one orphaned 03:00 file rather than being empty, and hence a
    clip build that could only report, correctly and uselessly, that no buffered
    segments covered the event.

    Deliberately thin. It calls exactly what the settings page calls, so there is
    no second way to start a recorder; the restart is the ordinary one, and
    whatever is wrong with the camera will simply fail again and be logged again
    rather than being hidden by a cleverer supervisor.
    """
    cfg = video_config()
    if not cfg.get("video_enabled"):
        return False
    with VIDEO_LOCK:
        alive = video_process_is_running()
        started_at = float(VIDEO_RUNTIME_STATE.get("started_at") or 0)
    reason = ""
    if not alive:
        reason = "the recorder was not running"
    elif started_at and recorder_appears_stalled(cfg, started_at):
        # Alive and writing nothing is the same loss of evidence as dead, and it
        # is what an RTSP source does when the camera goes away without closing
        # the connection: FFmpeg sits there. The live preview has had this
        # watchdog for a while; the recorder that the protests depend on did not.
        reason = "the recorder was running but had written no segments"
    if not reason:
        # Still healthy: the ordinary call also trims the buffer, which is the
        # other thing that stops when the recorder does.
        start_video_background_recorder()
        return False
    if reason.endswith("no segments"):
        with VIDEO_LOCK:
            stop_video_recorder_locked("Restarting a recorder that had stalled.")
    _log_recorder_restart(reason)
    start_video_background_recorder()
    with VIDEO_LOCK:
        VIDEO_RUNTIME_STATE["restarts"] = int(VIDEO_RUNTIME_STATE.get("restarts") or 0) + 1
        VIDEO_RUNTIME_STATE["last_restart_at"] = time.time()
    return True


def video_watchdog_loop() -> None:
    """Watch the rolling recorder for as long as the app runs."""
    while True:
        try:
            video_watchdog_tick()
        except Exception:
            # A watchdog that can die is not one. Whatever went wrong will be in
            # the recorder's own status the next time anybody looks.
            pass
        time.sleep(VIDEO_WATCHDOG_SECONDS)


def start_video_watchdog() -> None:
    """Start the recorder watchdog thread if it is not already running."""
    with VIDEO_LOCK:
        if VIDEO_RUNTIME_STATE.get("watchdog_started"):
            return
        VIDEO_RUNTIME_STATE["watchdog_started"] = True
    threading.Thread(target=video_watchdog_loop, name="video-recorder-watchdog",
                     daemon=True).start()


def video_runtime_status() -> Dict[str, Any]:
    """Return a human-readable health/status object for the recorder."""
    cfg = video_config()
    with VIDEO_LOCK:
        status = dict(VIDEO_RUNTIME_STATE.get("last_status") or {})
        proc = VIDEO_RUNTIME_STATE.get("process")
        preview_proc = VIDEO_RUNTIME_STATE.get("preview_process")
        running = video_process_is_running()
        preview_running = video_preview_process_is_running()
        status["running"] = running
        status["preview_running"] = preview_running
        status["enabled"] = bool(cfg.get("video_enabled"))
        status["config"] = cfg
        status.setdefault("log_tail", tail_text_file(appstate.VIDEO_LOG_PATH))
        status["warnings"] = video_log_health_warnings(str(status.get("log_tail") or ""))
        status.setdefault("command", VIDEO_RUNTIME_STATE.get("last_command") or "")
        status.setdefault("preview_command", VIDEO_RUNTIME_STATE.get("last_preview_command") or "")
        recent = recent_video_segments(max_age_seconds=max(30, int(cfg.get("video_segment_seconds", 5)) * 4))
        status["segment_count"] = len(recent)
        status["latest_segment"] = str(recent[0].name) if recent else ""
        # So the dashboard can say the recorder *has been* failing, not only
        # whether it happens to be up at the moment somebody looks.
        status["restarts"] = int(VIDEO_RUNTIME_STATE.get("restarts") or 0)
        last_restart = VIDEO_RUNTIME_STATE.get("last_restart_at")
        status["last_restart_age_seconds"] = (
            round(time.time() - float(last_restart)) if last_restart else None)
        try:
            if appstate.VIDEO_LIVE_JPG_PATH.exists() and appstate.VIDEO_LIVE_JPG_PATH.stat().st_size > 0:
                age = max(0.0, time.time() - appstate.VIDEO_LIVE_JPG_PATH.stat().st_mtime)
                status["live_frame"] = True
                status["live_frame_age_seconds"] = round(age, 1)
            else:
                status["live_frame"] = False
                status["live_frame_age_seconds"] = None
        except Exception:
            status["live_frame"] = False
            status["live_frame_age_seconds"] = None
        if proc is not None and not running and bool(cfg.get("video_enabled")):
            rc = getattr(proc, "returncode", None)
            status["ok"] = False
            status["message"] = f"Video recorder is not running. FFmpeg exit code: {rc}. Check the recorder log below."
            VIDEO_RUNTIME_STATE["last_status"] = status
            VIDEO_RUNTIME_STATE["process"] = None
            VIDEO_RUNTIME_STATE["config_hash"] = None
        elif running and bool(cfg.get("video_enabled")):
            started_at = float(VIDEO_RUNTIME_STATE.get("started_at") or 0)
            age = time.time() - started_at if started_at else 0
            wait = int(cfg.get("video_segment_seconds", 5)) + 8
            if not recent and age > wait:
                status["ok"] = False
                status["message"] = f"Video recorder process is running, but no buffer segments have been written yet. Check camera format/source and FFmpeg log."
            elif recent:
                status["ok"] = True
                copy_detail = f"/{cfg.get('video_copy_container', 'mp4')}" if cfg.get('video_recording_mode') == 'copy' else ""
                msg = f"Video recorder running from {cfg['video_source_type']} source in {cfg.get('video_recording_mode', 'reencode')}{copy_detail} mode. Latest segment: {recent[0].name}."
                if build_video_preview_command(str(cfg.get("video_ffmpeg_path") or "ffmpeg"), cfg) and not preview_running:
                    msg += " Live preview is not running, but recording is still running."
                if status.get("warnings"):
                    msg += " Warning: " + " ".join(status["warnings"][:2])
                status["message"] = msg
        if preview_proc is not None and not preview_running:
            VIDEO_RUNTIME_STATE["preview_process"] = None
    return status


def video_segment_start(path: Path) -> Optional[datetime]:
    """Parse the start timestamp encoded in a buffer segment filename."""
    try:
        return datetime.strptime(path.stem[:15], "%Y%m%dT%H%M%S")
    except Exception:
        return None


def matching_video_segments(window_start: datetime, window_end: datetime, cfg: Dict[str, Any]) -> List[Path]:
    """Return buffer segments that overlap an event clip window."""
    segment_seconds = int(cfg.get("video_segment_seconds") or 5)
    matches: List[Tuple[datetime, Path]] = []
    for path in iter_video_buffer_segments():
        st = video_segment_start(path)
        if not st:
            continue
        # Include segments that overlap the requested window.
        seg_end = st.timestamp() + segment_seconds + 1
        if seg_end >= window_start.timestamp() and st.timestamp() <= window_end.timestamp():
            matches.append((st, path))
    return [p for _, p in sorted(matches, key=lambda item: item[0])]


def record_clip_footage_start(clip_id: int, started: Optional[datetime]) -> None:
    """Store the moment the clip's own first frame was taken.

    Not the same as ``event_time - pre_seconds``: clips are cut from whole
    rolling-buffer segments, so the file starts at a segment boundary at or
    before the window. A replay that assumes otherwise draws the boats up to a
    segment ahead of the picture they are drawn over.
    """
    if not started:
        return
    init_db()
    with get_db() as db:
        db.execute("UPDATE video_clips SET footage_started_at = ? WHERE id = ?",
                   (started.isoformat(timespec="seconds"), clip_id))
        db.commit()


def update_video_clip_status(clip_id: int, status: str, message: str = "", file_path: str = "") -> None:
    """Persist a clip status update in the database."""
    init_db()
    with get_db() as db:
        db.execute(
            "UPDATE video_clips SET status = ?, message = ?, file_path = COALESCE(NULLIF(?, ''), file_path), updated_at = ? WHERE id = ?",
            (status, message, file_path, datetime.now().isoformat(timespec="seconds"), clip_id),
        )
        db.commit()


def update_video_clip_public_status(clip_id: int, status: str, message: str = "", public_file_path: str = "", public_object_key: str = "", public_url: str = "") -> None:
    """Persist public web-video publishing status for a clip."""
    init_db()
    now = datetime.now().isoformat(timespec="seconds")
    with get_db() as db:
        db.execute(
            """
            UPDATE video_clips
            SET public_status = ?,
                public_message = ?,
                public_file_path = COALESCE(NULLIF(?, ''), public_file_path),
                public_object_key = COALESCE(NULLIF(?, ''), public_object_key),
                public_url = COALESCE(NULLIF(?, ''), public_url),
                updated_at = ?
            WHERE id = ?
            """,
            (status, message, public_file_path, public_object_key, public_url, now, clip_id),
        )
        db.commit()


def video_public_r2_credentials_ready(cfg: Dict[str, Any]) -> bool:
    """Return whether Cloudflare R2 has enough credentials/settings to upload."""
    return (
        bool(cfg.get("video_public_r2_account_id"))
        and bool(cfg.get("video_public_r2_bucket"))
        and bool(cfg.get("video_public_r2_access_key_id"))
        and bool(cfg.get("video_public_r2_secret_access_key"))
        and bool(cfg.get("video_public_r2_public_base_url"))
    )


def video_public_r2_ready(cfg: Dict[str, Any]) -> bool:
    """Return whether Cloudflare R2 public-video publishing has enough settings."""
    return normalise_video_public_provider(cfg.get("video_public_provider")) == "r2" and video_public_r2_credentials_ready(cfg)


def public_video_transcode_args(quality: str) -> Tuple[str, str]:
    """Return FFmpeg scale filter and CRF for a public web-video preset."""
    quality = normalise_video_public_quality(quality)
    if quality == "1080p":
        return "scale=-2:1080:force_original_aspect_ratio=decrease", "25"
    return "scale=-2:720:force_original_aspect_ratio=decrease", "27"


def public_clip_output_path(clip: sqlite3.Row) -> Path:
    """Return the local public-web-video path for a clip."""
    public_dir = appstate.VIDEO_CLIPS_DIR / "public"
    safe_type = secure_filename(str(clip["clip_type"] or "clip")) or "clip"
    return public_dir / f"race{clip['race_id']}_{safe_type}_{clip['id']}_public.mp4"


def r2_object_key_for_clip(clip: sqlite3.Row, public_path: Path, cfg: Dict[str, Any]) -> str:
    """Build a stable Cloudflare R2 object key for a public race video."""
    prefix = safe_r2_key_prefix(str(cfg.get("video_public_r2_prefix") or "race-videos"))
    parts = [p for p in (prefix, f"race{clip['race_id']}", public_path.name) if p]
    return "/".join(parts)


def public_url_for_r2_key(cfg: Dict[str, Any], key: str) -> str:
    """Return the externally served URL for an uploaded R2 object key."""
    base = safe_public_video_base_url(str(cfg.get("video_public_r2_public_base_url") or ""))
    quoted = "/".join(quote(part, safe="") for part in str(key).split("/"))
    return base.rstrip("/") + "/" + quoted


def r2_s3_endpoint_host(cfg: Dict[str, Any]) -> str:
    """Return the Cloudflare R2 S3 API host for the configured account."""
    return r2.endpoint_host(safe_r2_account_id(str(cfg.get("video_public_r2_account_id") or "")))


def _upload_bytes_to_r2_signed(cfg: Dict[str, Any], key: str, body: bytes, content_type: str, cache_control: str) -> str:
    """Upload bytes to R2 using the built-in AWS Signature V4 PUT request."""
    key = str(key).lstrip("/")
    if not key:
        raise ValueError("Cloudflare R2 object key is empty.")
    r2.put_object(
        safe_r2_account_id(str(cfg.get("video_public_r2_account_id") or "")),
        safe_r2_bucket_name(str(cfg.get("video_public_r2_bucket") or "")),
        key,
        body,
        str(cfg.get("video_public_r2_access_key_id") or "").strip(),
        str(cfg.get("video_public_r2_secret_access_key") or ""),
        content_type=content_type,
        cache_control=cache_control,
    )
    return public_url_for_r2_key(cfg, key)


def _upload_bytes_to_r2_with_curl(cfg: Dict[str, Any], key: str, body: bytes, content_type: str, cache_control: str) -> str:
    """Upload bytes to R2 using curl's AWS SigV4 support as a fallback."""
    curl_path = shutil.which("curl") or shutil.which("curl.exe")
    if not curl_path:
        raise RuntimeError("curl was not found for fallback R2 upload.")
    account_id = safe_r2_account_id(str(cfg.get("video_public_r2_account_id") or ""))
    bucket = safe_r2_bucket_name(str(cfg.get("video_public_r2_bucket") or ""))
    access_key = str(cfg.get("video_public_r2_access_key_id") or "").strip()
    secret_key = str(cfg.get("video_public_r2_secret_access_key") or "")
    if not (account_id and bucket and access_key and secret_key):
        raise ValueError("Cloudflare R2 account ID/endpoint, bucket, access key ID and secret access key are required.")
    host = r2_s3_endpoint_host(cfg)
    key = str(key).lstrip("/")
    quoted_key = "/".join(quote(part, safe="") for part in key.split("/"))
    url = f"https://{host}/{quote(bucket, safe='')}/{quoted_key}"
    with tempfile.NamedTemporaryFile(prefix="ro_r2_upload_", delete=False) as tmp:
        tmp.write(body)
        tmp_path = tmp.name
    try:
        allowance = r2_upload_timeout_s(len(body))
        cmd = [
            curl_path,
            "--fail",
            "--silent",
            "--show-error",
            "--request", "PUT",
            "--upload-file", tmp_path,
            "--header", f"Content-Type: {content_type}",
            "--header", f"Cache-Control: {cache_control}",
            "--aws-sigv4", "aws:amz:auto:s3",
            "--user", f"{access_key}:{secret_key}",
            "--connect-timeout", str(R2_CONNECT_TIMEOUT_S),
            # Give up on a dead link, not on a slow one.
            "--speed-limit", str(R2_STALL_BYTES_PER_S),
            "--speed-time", str(R2_STALL_SECONDS),
            url,
        ]
        # Past the allowance curl is not transferring, or it would have finished;
        # the margin covers its own shutdown.
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=allowance + 30)
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or f"curl exited with {proc.returncode}").strip()
        raise RuntimeError(detail[:700])
    return public_url_for_r2_key(cfg, key)


def upload_bytes_to_r2(cfg: Dict[str, Any], key: str, body: bytes, content_type: str = "application/octet-stream", cache_control: str = "public, max-age=31536000, immutable") -> str:
    """Upload bytes to Cloudflare R2 and return the public object URL.

    The app first uses its built-in Signature V4 uploader. If that fails and a
    system ``curl`` with AWS SigV4 support is available, it tries curl as a
    fallback. The fallback matches the documented S3-compatible endpoint shape
    and gives clearer diagnostics on Windows installations.
    """
    try:
        return _upload_bytes_to_r2_signed(cfg, key, body, content_type, cache_control)
    except Exception as first_exc:
        try:
            return _upload_bytes_to_r2_with_curl(cfg, key, body, content_type, cache_control)
        except Exception as second_exc:
            raise RuntimeError(
                "Built-in upload failed: " + str(first_exc) + " | curl fallback failed: " + str(second_exc)
            ) from first_exc


def upload_file_to_r2(
    cfg: Dict[str, Any],
    key: str,
    path: Path,
    content_type: str = "video/mp4",
    cache_control: str = "public, max-age=31536000, immutable",
) -> str:
    """Upload a local file to Cloudflare R2 and return its public URL."""
    return upload_bytes_to_r2(
        cfg,
        key,
        path.read_bytes(),
        content_type=content_type,
        cache_control=cache_control,
    )


def public_live_r2_enabled(cfg: Dict[str, Any]) -> bool:
    """Return whether the public live JPEG should be pushed to Cloudflare R2."""
    return normalise_video_public_live_provider(cfg.get("video_public_live_provider")) == "r2" and video_public_r2_credentials_ready(cfg)


def r2_object_key_for_public_live_frame(cfg: Dict[str, Any]) -> str:
    """Return the stable R2 object key for the public live-camera JPEG."""
    prefix = safe_r2_key_prefix(str(cfg.get("video_public_r2_prefix") or "race-videos"))
    parts = [p for p in (prefix, "live", "latest_public.jpg") if p]
    return "/".join(parts)


def public_live_r2_url(cfg: Dict[str, Any]) -> str:
    """Return the public URL where competitors can fetch the R2 live JPEG."""
    return public_url_for_r2_key(cfg, r2_object_key_for_public_live_frame(cfg))


def save_public_live_r2_status(status: Dict[str, Any]) -> None:
    """Persist the latest live-image R2 uploader status for the Settings page."""
    appstate.VIDEO_RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    safe_status = dict(status)
    safe_status.setdefault("updated_at", datetime.now().isoformat(timespec="seconds"))
    try:
        appstate.PUBLIC_LIVE_R2_STATUS_PATH.write_text(json.dumps(safe_status, indent=2, sort_keys=True), encoding="utf-8")
    except OSError:
        pass


def public_live_r2_status() -> Dict[str, Any]:
    """Return the latest public live-image R2 uploader status."""
    with PUBLIC_LIVE_R2_LOCK:
        status = dict(PUBLIC_LIVE_R2_STATE.get("last_status") or {})
        status.setdefault("last_upload_at", PUBLIC_LIVE_R2_STATE.get("last_upload_at"))
        status.setdefault("public_url", PUBLIC_LIVE_R2_STATE.get("last_url") or "")
        status.setdefault("key", PUBLIC_LIVE_R2_STATE.get("last_key") or "")
    if status.get("updated_at"):
        return status
    try:
        if appstate.PUBLIC_LIVE_R2_STATUS_PATH.exists():
            data = json.loads(appstate.PUBLIC_LIVE_R2_STATUS_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                for key, value in data.items():
                    status.setdefault(key, value)
    except Exception:
        pass
    return status


def update_public_live_r2_state(status: Dict[str, Any]) -> None:
    """Update in-memory and on-disk status for live-image R2 upload."""
    now = datetime.now().isoformat(timespec="seconds")
    status = dict(status)
    status.setdefault("updated_at", now)
    with PUBLIC_LIVE_R2_LOCK:
        PUBLIC_LIVE_R2_STATE["last_status"] = status
        if status.get("public_url"):
            PUBLIC_LIVE_R2_STATE["last_url"] = str(status.get("public_url") or "")
        if status.get("key"):
            PUBLIC_LIVE_R2_STATE["last_key"] = str(status.get("key") or "")
        if status.get("ok") and status.get("enabled"):
            PUBLIC_LIVE_R2_STATE["last_upload_at"] = now
    save_public_live_r2_status(status)


def upload_public_live_frame_once(cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Upload the latest branded public live-camera JPEG to Cloudflare R2 once."""
    cfg = cfg or video_config()
    if normalise_video_public_live_provider(cfg.get("video_public_live_provider")) != "r2":
        status = {"ok": False, "enabled": False, "message": "Public live-image R2 upload is disabled."}
        update_public_live_r2_state(status)
        return status
    if not video_public_r2_credentials_ready(cfg):
        status = {"ok": False, "enabled": True, "message": "Cloudflare R2 settings are incomplete for public live-image upload."}
        update_public_live_r2_state(status)
        return status
    frame_path = public_live_frame_path()
    if not frame_path.exists() or frame_path.stat().st_size <= 0:
        status = {"ok": False, "enabled": True, "message": "Live camera frame is not ready yet."}
        update_public_live_r2_state(status)
        return status
    key = r2_object_key_for_public_live_frame(cfg)
    try:
        # The live JPEG is intentionally a stable object that browsers refresh
        # with a query string.  Do not ask browsers or Cloudflare to cache it for
        # long; competitors should see the newest upload on their next refresh.
        with PUBLIC_VIDEO_UPLOAD_LOCK:
            url = upload_file_to_r2(
                cfg,
                key,
                frame_path,
                content_type="image/jpeg",
                cache_control="no-store, no-cache, must-revalidate, max-age=0",
            )
        age = max(0.0, time.time() - frame_path.stat().st_mtime)
        status = {
            "ok": True,
            "enabled": True,
            "message": "Public live image uploaded to Cloudflare R2.",
            "public_url": url,
            "key": key,
            "frame_age_seconds": round(age, 1),
        }
    except Exception as exc:
        status = {
            "ok": False,
            "enabled": True,
            "message": f"Could not upload public live image to Cloudflare R2: {str(exc)[:700]}",
            "public_url": public_live_r2_url(cfg),
            "key": key,
        }
    update_public_live_r2_state(status)
    return status


def public_live_r2_uploader_loop() -> None:
    """Background loop that pushes the branded public live JPEG to R2."""
    while True:
        cfg = video_config()
        interval = int_in_range(cfg.get("video_public_live_interval_seconds"), 5, 2, 60)
        if normalise_video_public_live_provider(cfg.get("video_public_live_provider")) != "r2":
            update_public_live_r2_state({"ok": False, "enabled": False, "message": "Public live-image R2 upload is disabled."})
            time.sleep(5)
            continue
        upload_public_live_frame_once(cfg)
        time.sleep(interval)


def start_public_live_r2_uploader() -> None:
    """Start the optional public live-image R2 uploader when configured."""
    cfg = video_config()
    if normalise_video_public_live_provider(cfg.get("video_public_live_provider")) != "r2":
        with PUBLIC_LIVE_R2_LOCK:
            if not PUBLIC_LIVE_R2_STATE.get("started"):
                PUBLIC_LIVE_R2_STATE["last_status"] = {"ok": False, "enabled": False, "message": "Public live-image R2 upload is disabled."}
        return
    with PUBLIC_LIVE_R2_LOCK:
        if PUBLIC_LIVE_R2_STATE.get("started"):
            return
        PUBLIC_LIVE_R2_STATE["started"] = True
        PUBLIC_LIVE_R2_STATE["last_status"] = {"ok": False, "enabled": True, "message": "Starting public live-image R2 uploader..."}
    thread = threading.Thread(target=public_live_r2_uploader_loop, name="public-live-r2-uploader", daemon=True)
    thread.start()


def resolve_video_clip_path(stored_path: Any, allowed_roots: Optional[List[Path]] = None) -> Optional[Path]:
    """Resolve a stored video path if it still points inside an allowed clip folder."""
    text = str(stored_path or "").strip()
    if not text:
        return None
    stored = Path(text)
    path = stored if stored.is_absolute() else (appstate.BASE_DIR / stored)
    try:
        path = path.resolve()
    except OSError:
        return None
    roots = allowed_roots or [appstate.VIDEO_CLIPS_DIR, appstate.LEGACY_VIDEO_CLIPS_DIR]
    resolved_roots = []
    for root in roots:
        try:
            resolved_roots.append(root.resolve())
        except OSError:
            continue
    if not any(path == root or root in path.parents for root in resolved_roots):
        return None
    return path if path.exists() and path.is_file() else None


def existing_public_video_path(clip: sqlite3.Row) -> Optional[Path]:
    """Return the already-created public web copy for a clip, if present."""
    public_root = (appstate.VIDEO_CLIPS_DIR / "public").resolve()
    stored = resolve_video_clip_path(row_get(clip, "public_file_path"), [appstate.VIDEO_CLIPS_DIR])
    if stored:
        try:
            if stored == public_root or public_root in stored.parents:
                return stored
        except OSError:
            pass
    expected = public_clip_output_path(clip)
    return expected if expected.exists() and expected.is_file() else None


def evidence_video_path_for_clip(clip: sqlite3.Row) -> Optional[Path]:
    """Return the full-quality local evidence clip path for a ready clip."""
    return resolve_video_clip_path(row_get(clip, "file_path"), [appstate.VIDEO_CLIPS_DIR, appstate.LEGACY_VIDEO_CLIPS_DIR])


def upload_public_video_file_with_retries(clip_id: int, clip: sqlite3.Row, public_path: Path, cfg: Dict[str, Any], attempts: int = PUBLIC_VIDEO_UPLOAD_RETRY_ATTEMPTS) -> bool:
    """Upload a public web-video file to R2 with short retries and status updates."""
    object_key = str(row_get(clip, "public_object_key") or "").strip() or r2_object_key_for_clip(clip, public_path, cfg)
    try:
        rel_public = str(public_path.relative_to(appstate.BASE_DIR))
    except ValueError:
        rel_public = str(public_path)
    last_error = ""
    for attempt in range(1, max(1, int(attempts)) + 1):
        update_video_clip_public_status(
            clip_id,
            "uploading",
            f"Uploading public video to Cloudflare R2 (attempt {attempt}/{attempts})...",
            rel_public,
            object_key,
        )
        try:
            # Keep R2 uploads serial. Multiple finish clips can complete close together;
            # on a 4G hut connection concurrent uploads are more likely to fail, and the
            # first finisher was observed to be the one left in public-video error.
            with PUBLIC_VIDEO_UPLOAD_LOCK:
                public_url = upload_file_to_r2(cfg, object_key, public_path, content_type="video/mp4")
            update_video_clip_public_status(clip_id, "ready", "Public video uploaded to Cloudflare R2.", rel_public, object_key, public_url)
            return True
        except Exception as exc:
            last_error = str(exc)
            if attempt < attempts:
                update_video_clip_public_status(
                    clip_id,
                    "uploading",
                    f"R2 upload attempt {attempt}/{attempts} failed; retrying shortly: {last_error[:300]}",
                    rel_public,
                    object_key,
                )
                time.sleep(min(30, 2 ** attempt))
    update_video_clip_public_status(clip_id, "error", f"Could not upload public video after {attempts} attempts: {last_error[:700]}", rel_public, object_key)
    return False


def abandon_stuck_public_video_uploads() -> int:
    """Give up on public-video uploads that are stuck, and report how many.

    A backlog of clips sitting in pending/uploading is not only untidy: the
    off-site backup stands aside while race videos are still uploading, so 84 of
    them left over from a misconfigured bucket kept deferring the nightly backup
    (reported from the hut). There was a button to *retry* them and nothing to
    stop trying.

    They are marked ``abandoned`` rather than deleted. No clip, no evidence file
    and no public copy is touched — only the publishing state — and ``abandoned``
    is still picked up by the retry button, so fixing R2 later and pressing Retry
    puts them all back in the queue.
    """
    init_db()
    now = datetime.now().isoformat(timespec="seconds")
    with get_db() as db:
        cursor = db.execute(
            """
            UPDATE video_clips
               SET public_status = 'abandoned',
                   public_message = 'Publishing given up on from Settings. Retry public video uploads to try again.',
                   updated_at = ?
             WHERE COALESCE(public_status, '') IN ('pending', 'processing', 'uploading')
            """,
            (now,),
        )
        db.commit()
        return int(cursor.rowcount or 0)


def retry_public_video_uploads_once(cfg: Optional[Dict[str, Any]] = None, limit: int = 50) -> Dict[str, int]:
    """Retry failed or stuck R2 public-video uploads using existing local files.

    This is used by the Settings retry button and is safe to run after a race:
    if the smaller public copy already exists, it uploads that file directly; if
    only the evidence clip exists it recreates the public copy first.
    """
    init_db()
    cfg = cfg or video_config()
    summary = {"checked": 0, "uploaded": 0, "republished": 0, "skipped": 0, "errors": 0}
    if normalise_video_public_provider(cfg.get("video_public_provider")) != "r2" or not video_public_r2_ready(cfg):
        return summary
    with get_db() as db:
        clips = db.execute(
            """
            SELECT * FROM video_clips
            WHERE status = 'ready'
              AND COALESCE(public_status, '') IN ('pending', 'processing', 'uploading', 'error', 'abandoned')
            ORDER BY id ASC
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
    ffmpeg = ffmpeg_executable(cfg)
    for clip in clips:
        summary["checked"] += 1
        public_path = existing_public_video_path(clip)
        if public_path:
            if upload_public_video_file_with_retries(int(clip["id"]), clip, public_path, cfg):
                summary["uploaded"] += 1
            else:
                summary["errors"] += 1
            continue
        evidence_path = evidence_video_path_for_clip(clip)
        if evidence_path and ffmpeg:
            publish_public_video_clip(int(clip["id"]), evidence_path, cfg, ffmpeg)
            summary["republished"] += 1
        else:
            update_video_clip_public_status(int(clip["id"]), "error", "Could not retry public upload: local public copy or evidence clip was not found.")
            summary["skipped"] += 1
    return summary


def save_r2_test_result(result: Dict[str, Any]) -> None:
    """Persist the last R2 test result for display on the Settings page."""
    appstate.VIDEO_RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    safe_result = dict(result)
    safe_result["timestamp"] = datetime.now().isoformat(timespec="seconds")
    try:
        appstate.R2_TEST_RESULT_PATH.write_text(json.dumps(safe_result, indent=2, sort_keys=True), encoding="utf-8")
    except OSError:
        pass


def read_r2_test_result() -> Optional[Dict[str, Any]]:
    """Return the most recent R2 test result, if one has been recorded."""
    try:
        if not appstate.R2_TEST_RESULT_PATH.exists():
            return None
        data = json.loads(appstate.R2_TEST_RESULT_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def check_public_r2_url(url: str, expected_text: str = "Pwllheli Race Officer") -> Tuple[bool, str]:
    """Check whether the public R2 URL can be read without credentials."""
    if not safe_public_video_base_url(url):
        return False, "No valid public URL was produced."
    try:
        req = urllib.request.Request(url, headers={"Cache-Control": "no-cache", "User-Agent": f"PwllheliRaceOfficer/{appstate.APP_VERSION}"})
        with urllib.request.urlopen(req, timeout=20) as response:
            body = response.read(512).decode("utf-8", errors="replace")
            if response.status not in (200, 206):
                return False, f"Public URL returned HTTP {response.status}."
            if expected_text and expected_text not in body:
                return False, "Public URL opened but did not contain the expected test text. Check the Public base URL and object prefix."
            return True, "Public URL opened successfully."
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read().decode("utf-8", errors="replace").strip()
        except Exception:
            body = ""
        suffix = f": {body[:300]}" if body else ""
        return False, f"Public URL returned HTTP {exc.code}{suffix}"
    except Exception as exc:
        return False, f"Could not open public URL: {exc}"


def build_public_video_transcode_command(ffmpeg: str, evidence_path: Path, out_path: Path, cfg: Dict[str, Any], startline_spec: Optional[dict] = None) -> List[str]:
    """Build the FFmpeg command that creates the small public web-video copy.

    The full-quality evidence clip remains unchanged.  Branding — and, when
    provided, the ODM start line — are burned into the public H.264 copy in a
    SINGLE encode. The start line is overlaid on the full-resolution source
    before the branding scale, so it scales down with the video.
    """
    quality = normalise_video_public_quality(cfg.get("video_public_quality"))
    scale_filter, crf = public_video_transcode_args(quality)
    logo_inputs, filter_complex, output_label = build_public_branding_overlay_filter(quality, sponsor_count=8)
    cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", str(evidence_path)]
    for logo in logo_inputs:
        cmd.extend(["-loop", "1", "-i", logo])

    line_inputs, line_chain = ([], "")
    if startline_spec:
        line_inputs, line_chain = startline.line_filter_chain(startline_spec, len(logo_inputs))
        cmd.extend(line_inputs)

    if filter_complex:
        if line_chain:
            # feed the line-overlaid frames into the branding chain (which starts at [0:v])
            filter_complex = line_chain + ";" + filter_complex.replace("[0:v]", "[lined]", 1)
        cmd.extend(["-filter_complex", filter_complex, "-map", f"[{output_label}]", "-an"])
    elif line_chain:
        cmd.extend(["-filter_complex", f"{line_chain};[lined]{scale_filter},format=yuv420p[out]",
                    "-map", "[out]", "-an"])
    else:
        cmd.extend(["-map", "0:v:0", "-an", "-vf", scale_filter])
    cmd.extend([
        "-shortest",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", crf,
        "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        str(out_path),
    ])
    return cmd


def publish_public_video_clip(clip_id: int, evidence_path: Path, cfg: Dict[str, Any], ffmpeg: str) -> None:
    """Create the smaller, branded web copy and hand it to the serialised R2 uploader."""
    if normalise_video_public_provider(cfg.get("video_public_provider")) != "r2":
        update_video_clip_public_status(clip_id, "off", "Public video publishing is off.")
        return
    if not video_public_r2_ready(cfg):
        update_video_clip_public_status(clip_id, "error", "Cloudflare R2 publishing is enabled but account, bucket, keys or public URL are incomplete.")
        return
    with get_db() as db:
        clip = db.execute("SELECT * FROM video_clips WHERE id = ?", (clip_id,)).fetchone()
    if not clip:
        return

    out_path = public_clip_output_path(clip)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    branding_note = " with public logo overlay" if branding_assets().get("enabled") else ""
    # Optional: draw the ODM start line on the public copy only (best-effort;
    # detected once per clip and burned in during the same transcode). The raw
    # evidence clip is never touched. Start clips switch red->green at the start
    # signal; finish clips stay green.
    startline_spec = None
    if startline.startline_overlay_enabled(cfg):
        try:
            event_dt = parse_dt(clip["event_time"])
            pre = int(clip["pre_seconds"] or 0)
            event_offset = float(pre)
            if event_dt:
                segs = matching_video_segments(
                    event_dt - timedelta(seconds=pre),
                    event_dt + timedelta(seconds=int(clip["post_seconds"] or 0)), cfg)
                first = video_segment_start(segs[0]) if segs else None
                if first:
                    event_offset = (event_dt - first).total_seconds()
            startline_spec, reason = startline.build_startline_spec(
                ffmpeg, str(evidence_path), clip["clip_type"], event_offset, out_path.parent)
            log_activity("start line", f"clip #{clip_id} ({clip['clip_type']}): {reason}", user="system")
            branding_note += " + start line" if startline_spec else ""
        except Exception as exc:
            log_activity("start line", f"clip #{clip_id}: error {exc}", user="system")
            startline_spec = None
    update_video_clip_public_status(clip_id, "processing", f"Creating {normalise_video_public_quality(cfg.get('video_public_quality'))} public web video{branding_note}...")
    cmd = build_public_video_transcode_command(ffmpeg, evidence_path, out_path, cfg, startline_spec=startline_spec)
    try:
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, timeout=600)
        if not out_path.exists() or out_path.stat().st_size <= 0:
            raise RuntimeError("FFmpeg created no public video file.")
        rel_public = str(out_path.relative_to(appstate.BASE_DIR))
        object_key = r2_object_key_for_clip(clip, out_path, cfg)
        update_video_clip_public_status(clip_id, "uploading", "Uploading public video to Cloudflare R2...", rel_public, object_key)
        upload_public_video_file_with_retries(clip_id, clip, out_path, cfg)
    except Exception as exc:
        update_video_clip_public_status(clip_id, "error", f"Could not create public video: {exc}")


def build_video_clip_after_delay(clip_id: int) -> None:
    """Build an event clip from rolling-buffer segments after post-roll has elapsed."""
    init_db()
    with get_db() as db:
        clip = db.execute("SELECT * FROM video_clips WHERE id = ?", (clip_id,)).fetchone()
    if not clip:
        return
    event_dt = parse_dt(clip["event_time"])
    if not event_dt:
        update_video_clip_status(clip_id, "error", "Invalid event time for video clip.")
        return
    cfg = video_config()
    if not cfg.get("video_enabled"):
        update_video_clip_status(clip_id, "disabled", "Video recording is disabled in Settings.")
        return
    start_video_background_recorder()
    post_seconds = int(clip["post_seconds"] or cfg.get("video_post_seconds", 60))
    # Wait until the requested post-event video has had time to be recorded and segment files closed.
    wait_until = event_dt.timestamp() + post_seconds + int(cfg.get("video_segment_seconds") or 5) + 2
    delay = wait_until - time.time()
    if delay > 0:
        time.sleep(delay)

    # The start may have been postponed while this thread slept -- it is booked
    # when the time is set and can wait an hour. A clip of the empty line at the
    # old time is not evidence of anything, and building it would also spend the
    # buffer read and the upload on it.
    with get_db() as db:
        current = db.execute("SELECT status FROM video_clips WHERE id = ?", (clip_id,)).fetchone()
    if current and str(current["status"] or "") == "cancelled":
        return

    ffmpeg = ffmpeg_executable(cfg)
    if not ffmpeg:
        update_video_clip_status(clip_id, "error", "FFmpeg was not found when building the clip.")
        return
    pre_seconds = int(clip["pre_seconds"] or cfg.get("video_pre_seconds", 60))
    window_start = event_dt - timedelta(seconds=pre_seconds)
    window_end = event_dt + timedelta(seconds=post_seconds)
    segments = matching_video_segments(window_start, window_end, cfg)
    if not segments:
        update_video_clip_status(clip_id, "error", "No buffered video segments were available for this event. Check the video source and that the recorder was running before the event.")
        return

    # The concat below copies whole segments, so the file begins at the first
    # segment's boundary -- up to one segment before the window was asked for.
    # Recording that is what lets the replay hold the boats on the chart against
    # the picture; without it the clock ran ahead of the camera by the overhang.
    record_clip_footage_start(clip_id, video_segment_start(segments[0]))

    appstate.VIDEO_CLIPS_DIR.mkdir(parents=True, exist_ok=True)
    safe_type = secure_filename(str(clip["clip_type"] or "clip")) or "clip"
    out_path = appstate.VIDEO_CLIPS_DIR / f"race{clip['race_id']}_{safe_type}_{clip_id}.mp4"
    list_path = appstate.VIDEO_CLIPS_DIR / f"race{clip['race_id']}_{safe_type}_{clip_id}.txt"
    try:
        list_path.write_text("".join(f"file '{p.resolve().as_posix()}'\n" for p in segments), encoding="utf-8")
        cmd = [
            ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
            "-fflags", "+genpts",
            "-f", "concat", "-safe", "0", "-i", str(list_path),
            "-map", "0:v:0", "-an",
            "-c", "copy",
            "-avoid_negative_ts", "make_zero",
            "-movflags", "+faststart",
            str(out_path),
        ]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, timeout=180)
        if out_path.exists() and out_path.stat().st_size > 0:
            update_video_clip_status(clip_id, "ready", "Evidence video clip ready.", str(out_path.relative_to(appstate.BASE_DIR)))
            publish_public_video_clip(clip_id, out_path, cfg, ffmpeg)
        else:
            update_video_clip_status(clip_id, "error", "FFmpeg created no usable clip.")
    except Exception as exc:
        update_video_clip_status(clip_id, "error", f"Could not build video clip: {exc}")
    finally:
        try:
            list_path.unlink(missing_ok=True)
        except Exception:
            pass


def schedule_video_clip(race_id: int, clip_type: str, event_time: str, entry_id: Optional[int] = None, event_id: Optional[int] = None, label: str = "", pre_seconds: Optional[int] = None, post_seconds: Optional[int] = None) -> Optional[int]:
    """Record metadata and queue an asynchronous event video clip."""
    cfg = video_config()
    if not event_time or not parse_dt(event_time):
        return None
    pre = int(pre_seconds if pre_seconds is not None else cfg.get("video_pre_seconds", 60))
    post = int(post_seconds if post_seconds is not None else cfg.get("video_post_seconds", 60))
    now = datetime.now().isoformat(timespec="seconds")
    with get_db() as db:
        # Avoid duplicating scheduled start clips when the race page is refreshed.
        existing = None
        if clip_type == "start":
            existing = db.execute("SELECT id FROM video_clips WHERE race_id = ? AND clip_type = 'start' AND event_time = ? ORDER BY id DESC LIMIT 1", (race_id, event_time)).fetchone()
        elif event_id:
            existing = db.execute("SELECT id FROM video_clips WHERE event_id = ? ORDER BY id DESC LIMIT 1", (event_id,)).fetchone()
        elif entry_id and clip_type == "finish":
            existing = db.execute("SELECT id FROM video_clips WHERE race_id = ? AND entry_id = ? AND clip_type = 'finish' AND event_time = ? ORDER BY id DESC LIMIT 1", (race_id, entry_id, event_time)).fetchone()
        if existing:
            return int(existing["id"])
        status = "pending" if cfg.get("video_enabled") else "disabled"
        message = "Video clip queued." if cfg.get("video_enabled") else "Video recording is disabled in Settings."
        public_status = "pending" if cfg.get("video_enabled") and normalise_video_public_provider(cfg.get("video_public_provider")) == "r2" else "off"
        public_message = "Public video will be uploaded to Cloudflare R2." if public_status == "pending" else "Public video publishing is off."
        cur = db.execute(
            """
            INSERT INTO video_clips (race_id, entry_id, event_id, clip_type, event_time, pre_seconds, post_seconds, status, label, message, public_status, public_message, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (race_id, entry_id, event_id, clip_type, event_time, pre, post, status, label, message, public_status, public_message, now, now),
        )
        db.commit()
        clip_id = int(cur.lastrowid)
    if cfg.get("video_enabled"):
        start_video_background_recorder()
        thread = threading.Thread(target=build_video_clip_after_delay, args=(clip_id,), name=f"video-clip-{clip_id}", daemon=True)
        thread.start()
    return clip_id





def ensure_start_video_scheduled(race: sqlite3.Row) -> None:
    """Schedule start-line clips for each configured start when recording is enabled."""
    if not race or not video_config().get("video_enabled"):
        return
    for start in race_start_schedule(race):
        start_time = str(start.get("time") or "").strip()
        if parse_dt(start_time):
            label = f"{start.get('name') or 'Start'} video"
            schedule_video_clip(int(race["id"]), "start", start_time, label=label)


def cancel_pending_start_clips(race_id: int, keep_event_time: str = "") -> int:
    """Drop start clips booked for a start that is not going to happen then.

    A start clip is booked as soon as a start time is set, and its builder
    thread then sleeps until that moment and cuts the clip out of the rolling
    buffer. Postponing does not change the stored time -- the flag is what
    changed -- so without this the club gets a minute of empty start line filed
    as the start video, and the race officer looking for the start afterwards
    has two clips to choose between, one of them of nothing.

    Returns how many were cancelled. `keep_event_time` spares the one that is
    now correct, so resuming does not cancel what it has just booked.
    """
    init_db()
    with get_db() as db:
        rows = db.execute(
            "SELECT id, event_time FROM video_clips WHERE race_id = ? AND clip_type = 'start'"
            " AND status = 'pending'", (race_id,)).fetchall()
    cancelled = 0
    for row in rows:
        if keep_event_time and str(row["event_time"] or "") == keep_event_time:
            continue
        update_video_clip_status(int(row["id"]), "cancelled",
                                 "The start was postponed, so this clip was not built.")
        cancelled += 1
    return cancelled


def get_video_clips_for_race(race_id: int) -> List[sqlite3.Row]:
    """Load all video clips attached to a race."""
    init_db()
    with get_db() as db:
        return db.execute("SELECT * FROM video_clips WHERE race_id = ? ORDER BY created_at DESC, id DESC", (race_id,)).fetchall()


def video_clip_maps(race_id: int) -> Dict[str, Any]:
    """Build dictionaries for start, entry and event video clips."""
    clips = get_video_clips_for_race(race_id)
    start_clip = next((c for c in clips if c["clip_type"] == "start"), None)
    by_entry: Dict[int, sqlite3.Row] = {}
    by_event: Dict[int, sqlite3.Row] = {}
    for c in clips:
        if c["entry_id"] is not None and int(c["entry_id"]) not in by_entry:
            by_entry[int(c["entry_id"])] = c
        if c["event_id"] is not None and int(c["event_id"]) not in by_event:
            by_event[int(c["event_id"])] = c
    return {"clips": clips, "start_clip": start_clip, "by_entry": by_entry, "by_event": by_event}


def race_delete_summary(race: sqlite3.Row, entries: List[sqlite3.Row], events: List[sqlite3.Row], video_clips: List[sqlite3.Row]) -> Dict[str, Any]:
    """Summarise race activity before allowing a destructive race delete."""
    first_warning = str(row_get(race, "start_time", "") or "").strip()
    first_warning_dt = parse_dt(first_warning)
    first_start_dt = race_first_start_dt(race)
    now = datetime.now()
    finished_entries = [e for e in entries if e["finish_time"] or e["status"] == "FINISHED"]
    scored_statuses = {"FINISHED", "DNF", "DNS", "DNC", "RET", "OCS", "DSQ", "DNE", "DGM"}
    non_racing_entries = [e for e in entries if str(e["status"] or "").upper() in scored_statuses and str(e["status"] or "").upper() != "RACING"]
    race_started = bool(
        (first_warning_dt and now >= first_warning_dt)
        or (first_start_dt and now >= first_start_dt)
        or finished_entries
        or events
    )
    course_label = ""
    custom = custom_course_from_race(race)
    if custom:
        course_label = "Made up course"
    elif row_get(race, "course_no") is not None:
        course_label = f"Course {race['course_no']}"
    return {
        "start_time_set": bool(first_warning),
        "first_warning": first_warning,
        "first_start": race_first_start_time(race),
        "course_set": bool(course_label),
        "course_label": course_label,
        "race_started": race_started,
        "entry_count": len(entries),
        "finished_count": len(finished_entries),
        "scored_or_non_racing_count": len(non_racing_entries),
        "event_count": len(events),
        "video_clip_count": len(video_clips),
        "series_name": row_get(race_series_row(race) or {}, "name", ""),
        "has_activity": bool(first_warning or course_label or race_started or entries or events or video_clips),
    }


def safe_delete_race_video_file(file_path: Optional[str]) -> bool:
    """Delete a race video clip file only when it is inside a known clip folder."""
    if not file_path:
        return False
    raw = Path(str(file_path))
    candidates = []
    if raw.is_absolute():
        candidates.append(raw)
    else:
        candidates.append((appstate.BASE_DIR / raw).resolve())
        candidates.append((appstate.DATA_DIR / raw).resolve())
    allowed_roots = [appstate.VIDEO_CLIPS_DIR.resolve(), appstate.LEGACY_VIDEO_CLIPS_DIR.resolve()]
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
            if not any(resolved == root or root in resolved.parents for root in allowed_roots):
                continue
            if resolved.exists() and resolved.is_file():
                resolved.unlink()
                return True
        except OSError:
            continue
    return False


def video_clip_link_text(clip: Optional[sqlite3.Row]) -> str:
    """Return race-office link text for an evidence clip status."""
    if not clip:
        return "No video"
    if clip["status"] == "ready":
        return "View video"
    if clip["status"] == "pending":
        return "Video pending"
    if clip["status"] == "disabled":
        return "Video disabled"
    return "Video error"


def public_video_clip_link_text(clip: Optional[sqlite3.Row]) -> str:
    """Return competitor-facing text for the public/web copy of a clip."""
    if not clip:
        return "No video"
    if clip["status"] != "ready":
        return video_clip_link_text(clip)
    public_status = str(row_get(clip, "public_status") or "off").lower()
    if public_status in ("", "off", "ready"):
        return "View video"
    if public_status == "pending":
        return "Public video pending"
    if public_status == "processing":
        return "Public video processing"
    if public_status == "uploading":
        return "Public video uploading"
    return "Public video error"


def public_video_clip_is_viewable(clip: Optional[sqlite3.Row]) -> bool:
    """Return whether a competitor should see an active video link."""
    if not clip or clip["status"] != "ready":
        return False
    public_status = str(row_get(clip, "public_status") or "off").lower()
    return public_status in ("", "off", "ready")


# Manual-horn events recorded inside core.horn schedule evidence clips through
# this hook; registering here means importing core.video wires it up.
from core import horn as _horn  # noqa: E402

_horn.VIDEO_CLIP_SCHEDULER = schedule_video_clip
