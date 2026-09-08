"""Nightly off-site backup: an encrypted copy of the club's data, off the hut PC.

Everything the race office cannot recreate lives on one PC in one hut. The
in-app Backup/restore page produces a ZIP, but only when somebody remembers to
click it, and the download lands on a laptop in the same building. This takes
the same ZIP and pushes it to Cloudflare R2 on a schedule.

Shape of it, and why:

* **It pushes.** The hut sits behind a cloudflared tunnel and opens nothing
  inbound, so nothing off-site can come and collect a backup.
* **It reuses the existing backup ZIP.** Same builder, same ``data/...`` member
  names, so the Backup/restore page restores an off-site copy with no new code —
  and the format is not a second thing to keep working.
* **It is encrypted** with AES-256 (see core/backup.py). The archive leaves the
  club's control, and it contains the members table, the users table and every
  password hash. The passphrase belongs in the club password manager next to the
  admin login: without it this file is not recoverable by anyone, including us.
* **No video.** Public web copies of race videos are already on R2, so backing
  them up would pay twice for the same bytes. Stated plainly because it has a
  real consequence: those R2 copies are branded, re-encoded web versions, so the
  unbranded evidence originals stay hut-only and are not off-site at all.
* **Not during racing.** The hut is on 4G shared with a caravan park and has
  already lost video uploads to Saturday-evening contention. A backup that
  competes with a start-video upload can cost the club the video, which is worth
  more than one night of backup, so the job stands aside and tries later.
* **Verified, and visible.** The upload is checked with a HEAD rather than
  assumed from a 200, and the dashboard carries the age of the last success. A
  backup job that quietly stopped months ago is worse than no backup job,
  because the club believes it has one.

The relay copy in the agreed design is deliberately not here: it needs a
transport that does not exist yet. This is the R2 half, which is the off-site
half.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core import appstate, backup, r2
from core.activitylog import log_activity
from core.db import get_db, init_db
from core.r2 import safe_r2_account_id, safe_r2_bucket_name, safe_r2_key_prefix
from core.settings import get_app_setting_overrides, int_in_range, text_to_bool
from core.timeutils import parse_dt

OFFSITE_LOCK = threading.Lock()
OFFSITE_STATE: Dict[str, Any] = {
    "started": False,
    "running": False,
    "last_status": {},
}

# Sections in a nightly off-site backup unless Settings says otherwise: all of
# them except the video clips, which are the one section measured in gigabytes.
DEFAULT_OFFSITE_SECTIONS = ("database", "tracks", "power_history", "marks_courses",
                            "polars_sailcharts", "branding")
DEFAULT_OFFSITE_PREFIX = "race-officer-backups"
DEFAULT_OFFSITE_HOUR = 3
DEFAULT_OFFSITE_MINUTE = 15
DEFAULT_OFFSITE_KEEP = 30

# How long to stand aside when the hut is busy before looking again.
BUSY_RETRY_MINUTES = 20
# How long to wait before retrying after a failed upload. Long enough not to
# hammer a 4G link that is having a bad night, short enough to still catch the
# same night if it recovers.
FAILURE_RETRY_MINUTES = 45
# A race sheet older than this cannot make the hut "busy" any more. Without a
# bound, one abandoned sheet left with boats still marked RACING would defer the
# backup every night for ever — which is exactly the silent failure this feature
# exists to prevent.
RACING_WINDOW_HOURS = 12
# How far ahead of a start to stand aside: the pre-start sequence is the busiest
# the hut ever is.
PRE_START_HOURS = 3
# A video upload only counts as "in progress" if its clip was touched recently;
# a clip wedged in "uploading" since last season must not block backups either.
VIDEO_QUEUE_WINDOW_HOURS = 6
# Age at which the dashboard stops calling the off-site copy healthy.
STALE_AFTER_HOURS = 36

SCHEDULER_TICK_SECONDS = 60


def normalise_offsite_sections(value: Any) -> List[str]:
    """Parse stored/submitted section ids, falling back to the default set."""
    if isinstance(value, str):
        requested = [part.strip() for part in value.split(",")]
    elif value is None:
        requested = []
    else:
        requested = [str(part).strip() for part in value]
    sections = backup.normalise_backup_section_ids([part for part in requested if part])
    return sections or list(DEFAULT_OFFSITE_SECTIONS)


def offsite_config() -> Dict[str, Any]:
    """Return normalised off-site backup settings.

    The R2 account and keys are the ones already configured for public video —
    the club's decision, to avoid a second set of credentials to look after — but
    the **bucket is separate**. The video bucket is served publicly, and an
    encrypted backup sitting behind a guessable public URL is one weak passphrase
    away from being the club's whole database.
    """
    overrides = get_app_setting_overrides()
    return {
        "offsite_backup_enabled": text_to_bool(overrides.get("offsite_backup_enabled"), False),
        "offsite_backup_bucket": safe_r2_bucket_name(overrides.get("offsite_backup_bucket") or ""),
        "offsite_backup_prefix": safe_r2_key_prefix(overrides.get("offsite_backup_prefix") or DEFAULT_OFFSITE_PREFIX),
        "offsite_backup_hour": int_in_range(overrides.get("offsite_backup_hour"), DEFAULT_OFFSITE_HOUR, 0, 23),
        "offsite_backup_minute": int_in_range(overrides.get("offsite_backup_minute"), DEFAULT_OFFSITE_MINUTE, 0, 59),
        "offsite_backup_keep": int_in_range(overrides.get("offsite_backup_keep"), DEFAULT_OFFSITE_KEEP, 1, 365),
        "offsite_backup_sections": normalise_offsite_sections(overrides.get("offsite_backup_sections")),
        "offsite_backup_passphrase": overrides.get("offsite_backup_passphrase") or "",
        "offsite_backup_passphrase_set": bool(overrides.get("offsite_backup_passphrase")),
        # Credentials shared with public-video publishing.
        "r2_account_id": safe_r2_account_id(overrides.get("video_public_r2_account_id") or ""),
        "r2_access_key_id": str(overrides.get("video_public_r2_access_key_id") or "").strip(),
        "r2_secret_access_key": overrides.get("video_public_r2_secret_access_key") or "",
        # Carried only so the readiness check can refuse to put backups in it.
        "video_bucket": safe_r2_bucket_name(overrides.get("video_public_r2_bucket") or ""),
    }


def offsite_credentials_ready(cfg: Dict[str, Any]) -> Tuple[bool, str]:
    """Return whether a backup could be uploaded, and what is missing if not."""
    if not cfg.get("r2_account_id") or not cfg.get("r2_access_key_id") or not cfg.get("r2_secret_access_key"):
        return False, ("Cloudflare R2 account ID, access key ID and secret key are needed. "
                       "Off-site backup reuses the credentials from the Video & camera settings.")
    if not cfg.get("offsite_backup_bucket"):
        return False, "Choose a separate R2 bucket for backups — do not use the public video bucket."
    if cfg.get("offsite_backup_bucket") == cfg.get("video_bucket"):
        # Refused rather than warned about. The video bucket is reachable from the
        # public base URL, so a backup in it is one guessed object key away from
        # being downloadable by anyone — and it holds every user account and
        # password hash in the club.
        return False, ("The backup bucket is the same as the public video bucket, which is served publicly. "
                       "Create a separate private bucket for backups.")
    if not cfg.get("offsite_backup_passphrase"):
        return False, "Set a backup passphrase. The off-site copy is never uploaded unencrypted."
    if not backup.encryption_available():
        return False, ("The pyzipper library is not installed, so encrypted backups cannot be written. "
                       "Run: pip install -r requirements.txt")
    return True, ""


# ---------------------------------------------------------------------------
# Status: persisted so the dashboard is honest across a restart.
# ---------------------------------------------------------------------------

def save_offsite_status(status: Dict[str, Any]) -> None:
    """Persist and cache the latest off-site backup status."""
    safe_status = dict(status)
    safe_status.setdefault("updated_at", datetime.now().isoformat(timespec="seconds"))
    with OFFSITE_LOCK:
        OFFSITE_STATE["last_status"] = dict(safe_status)
    try:
        appstate.RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        offsite_status_path().write_text(json.dumps(safe_status, indent=2, sort_keys=True), encoding="utf-8")
    except OSError:
        pass                            # a status file we cannot write is not worth failing a backup over


def offsite_status_path() -> Path:
    """Return the status file path, read at call time so tests can redirect it.

    ``appstate.RUNTIME_DIR`` is monkeypatched to a temp folder per test; binding
    the path at import would make a test write into the developer's runtime
    folder and, worse, read a real status left there by the app.
    """
    return appstate.RUNTIME_DIR / "offsite_backup_status.json"


def read_offsite_status() -> Dict[str, Any]:
    """Return the last known off-site backup status, from memory or from disk."""
    with OFFSITE_LOCK:
        cached = dict(OFFSITE_STATE.get("last_status") or {})
        running = bool(OFFSITE_STATE.get("running"))
        started = bool(OFFSITE_STATE.get("started"))
    status = cached
    if not status:
        try:
            path = offsite_status_path()
            if path.exists():
                loaded = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    status = loaded
        except (OSError, ValueError):
            status = {}
    status = dict(status)
    status["running"] = running
    status["background_started"] = started
    return status


def hours_since(iso_text: Any) -> Optional[float]:
    """Return how many hours ago an ISO timestamp was, or None if unparseable."""
    moment = parse_dt(str(iso_text or "").strip()) if iso_text else None
    if not moment:
        return None
    return max(0.0, (datetime.now() - moment).total_seconds() / 3600.0)


def offsite_dashboard_status() -> Dict[str, Any]:
    """Return a compact off-site backup summary for the dashboard card.

    Deliberately reports "never" and "overdue" as loudly as it reports success.
    The failure this guards against is a job that stopped months ago while
    everyone assumed it was running.
    """
    cfg = offsite_config()
    status = read_offsite_status()
    enabled = bool(cfg["offsite_backup_enabled"])
    last_success = status.get("last_success_at") or ""
    age_hours = hours_since(last_success)
    summary = {
        "enabled": enabled,
        "last_success_at": last_success,
        "age_hours": age_hours,
        "last_message": status.get("message") or "",
        "last_error": status.get("last_error") or "",
        "size_bytes": status.get("size_bytes") or 0,
        "running": bool(status.get("running")),
        # The in-progress line ("Uploading 39.3 MB…"), kept separate from the
        # summary verdict below so the Settings box can show both while a
        # background run is under way.
        "progress": status.get("message") or "" if status.get("running") else "",
        "keep": cfg["offsite_backup_keep"],
        "schedule": f"{int(cfg['offsite_backup_hour']):02d}:{int(cfg['offsite_backup_minute']):02d}",
    }
    if not enabled:
        summary.update(ok=False, state="off", message="Off-site backup is switched off — the only copies are in the hut.")
        return summary
    ready, why = offsite_credentials_ready(cfg)
    if not ready:
        summary.update(ok=False, state="unconfigured", message=why)
        return summary
    if age_hours is None:
        summary.update(ok=False, state="never", message="No off-site backup has succeeded yet.")
        return summary
    if age_hours > STALE_AFTER_HOURS:
        summary.update(ok=False, state="stale",
                       message=f"Last off-site backup was {describe_age(age_hours)} ago — it should run nightly.")
        return summary
    summary.update(ok=True, state="ok", message=f"Last off-site backup {describe_age(age_hours)} ago.")
    return summary


def describe_age(age_hours: float) -> str:
    """Return a short human age such as "20 minutes" or "3 days"."""
    if age_hours < 1.0:
        minutes = max(1, int(round(age_hours * 60)))
        return f"{minutes} minute{'s' if minutes != 1 else ''}"
    if age_hours < 48.0:
        hours = int(round(age_hours))
        return f"{hours} hour{'s' if hours != 1 else ''}"
    days = int(age_hours // 24)
    return f"{days} day{'s' if days != 1 else ''}"


# ---------------------------------------------------------------------------
# Is the hut busy? A backup must never be the reason a race video is lost.
# ---------------------------------------------------------------------------

def hut_is_busy(now: Optional[datetime] = None) -> Tuple[bool, str]:
    """Return whether racing or a video upload should hold the backup back.

    Every test here is bounded by a time window on purpose. The naive versions —
    "any entry still RACING", "any clip still uploading" — never become false
    again once a race sheet is abandoned or a clip upload is given up on, and a
    backup job that can be switched off for ever by one stale row is not a
    backup job.
    """
    now = now or datetime.now()
    try:
        init_db()
        with get_db() as db:
            # Every race, parsed in Python rather than ordered and limited in SQL.
            # start_time is TEXT, and a row written in a different ISO shape sorts
            # lexicographically in the wrong place — which with a LIMIT could push
            # today's race out of the window and let a backup run mid-race. A club
            # has tens of races a year, so reading them all costs nothing.
            recent_races = db.execute("SELECT id, name, start_time FROM races").fetchall()
            racing_ids = {
                int(row["race_id"])
                for row in db.execute("SELECT DISTINCT race_id FROM entries WHERE status = 'RACING'").fetchall()
            }
            queued_clips = db.execute(
                """
                SELECT COUNT(*) AS n FROM video_clips
                 WHERE COALESCE(public_status, '') IN ('pending', 'processing', 'uploading')
                   AND updated_at >= ?
                """,
                ((now - timedelta(hours=VIDEO_QUEUE_WINDOW_HOURS)).isoformat(timespec="seconds"),),
            ).fetchone()
    except Exception as exc:
        # Not knowing is not a reason to skip the backup; it is a reason to say so.
        return False, f"Could not check whether the hut is busy ({exc}); continuing."

    for race in recent_races:
        start = parse_dt(str(race["start_time"] or ""))
        if not start:
            continue
        if start - timedelta(hours=PRE_START_HOURS) <= now <= start:
            return True, f"{race['name']} starts at {start.strftime('%H:%M')}."
        if int(race["id"]) in racing_ids and start <= now <= start + timedelta(hours=RACING_WINDOW_HOURS):
            return True, f"Boats are still racing in {race['name']}."

    if queued_clips and int(queued_clips["n"] or 0) > 0:
        return True, (f"{int(queued_clips['n'])} race video upload(s) still in progress. "
                      "Settings → Video & camera can give up on them if they are never going to finish.")
    return False, ""


# ---------------------------------------------------------------------------
# Object naming, upload, verification and retention.
# ---------------------------------------------------------------------------

def offsite_object_key(prefix: str, created_at: datetime, suffix: str = ".zip") -> str:
    """Return the R2 object key for one backup.

    Timestamped rather than rotated through fixed names, so a bad backup cannot
    overwrite the last good one and the newest-first ordering a listing gives is
    the order the retention sweep needs.
    """
    stamp = created_at.strftime("%Y%m%d-%H%M%S")
    parts = [part for part in (safe_r2_key_prefix(prefix), f"race-officer-backup-{stamp}{suffix}") if part]
    return "/".join(parts)


def file_sha256(path: Path) -> str:
    """Return the SHA-256 of a file, read in chunks so a large ZIP is not loaded whole."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def upload_offsite_object(cfg: Dict[str, Any], key: str, body: bytes, content_type: str) -> None:
    """Upload one object to the backup bucket."""
    r2.put_object(
        cfg["r2_account_id"],
        cfg["offsite_backup_bucket"],
        key,
        body,
        cfg["r2_access_key_id"],
        cfg["r2_secret_access_key"],
        content_type=content_type,
        # A backup must never be served from a cache, and these are private.
        cache_control="private, no-store",
    )


def preflight_offsite_bucket(cfg: Dict[str, Any]) -> None:
    """Write a few bytes to the bucket before attempting the real archive.

    This exists because of how a refused upload fails. R2 answers a request it does
    not like — a token that does not cover this bucket, a bucket that is not there
    — and closes the connection; if the client is still streaming a 40 MB body at
    that point, the socket is torn down and the HTTP response, which is where R2
    explains itself, is never read. On the hut that surfaced as
    "Network error: [WinError 10053] An established connection was aborted by the
    software in your host machine" — which reads like a broken link and sends you
    to the router rather than to the token's bucket scope.

    A tiny object cannot fail that way: it is sent in one go, so the real status and
    R2's error code come back. The object is left in place, overwritten each run —
    it is a few dozen bytes, and the retention sweep only touches ``.zip`` keys.
    """
    key = "/".join(part for part in (safe_r2_key_prefix(cfg["offsite_backup_prefix"]), ".preflight") if part)
    body = (f"Pwllheli Race Officer {appstate.APP_VERSION} off-site backup write test, "
            f"{datetime.now().isoformat(timespec='seconds')}\n").encode("utf-8")
    try:
        upload_offsite_object(cfg, key, body, "text/plain; charset=utf-8")
    except Exception as exc:
        raise RuntimeError(
            f"Could not write to the backup bucket '{cfg['offsite_backup_bucket']}', so the archive was not built. {exc}"
        ) from exc


def verify_offsite_object(cfg: Dict[str, Any], key: str, expected_bytes: int) -> Dict[str, Any]:
    """HEAD an uploaded backup and confirm R2 holds the whole thing.

    An S3 PUT is atomic, so an object of the right length is the right object;
    what this catches is the upload that reported success without the bytes
    arriving, which is the failure mode nobody notices until a restore.
    """
    head = r2.head_object(
        cfg["r2_account_id"], cfg["offsite_backup_bucket"], key,
        cfg["r2_access_key_id"], cfg["r2_secret_access_key"],
    )
    if int(head.get("size_bytes") or 0) != int(expected_bytes):
        raise RuntimeError(
            f"Off-site backup verification failed: R2 holds {head.get('size_bytes')} bytes, "
            f"expected {expected_bytes}."
        )
    return head


def list_offsite_backups(cfg: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Return stored backup archives, newest first.

    This is what makes the feature checkable: the Settings page can show what is
    actually in the bucket rather than what the app believes it put there.
    """
    cfg = cfg or offsite_config()
    ready, _why = offsite_credentials_ready(cfg)
    if not ready:
        return []
    objects = r2.list_objects(
        cfg["r2_account_id"], cfg["offsite_backup_bucket"], safe_r2_key_prefix(cfg["offsite_backup_prefix"]),
        cfg["r2_access_key_id"], cfg["r2_secret_access_key"],
    )
    archives = [obj for obj in objects if str(obj.get("key", "")).endswith(".zip")]
    return sorted(archives, key=lambda obj: str(obj.get("key", "")), reverse=True)


def prune_offsite_backups(cfg: Dict[str, Any], keep: int) -> Dict[str, Any]:
    """Delete all but the newest ``keep`` backups, and their manifests with them.

    Retention runs in the app rather than as a bucket lifecycle rule so that it
    is visible here and cannot be silently absent on a bucket someone recreated.
    """
    result = {"removed": 0, "kept": 0, "errors": []}
    archives = list_offsite_backups(cfg)
    result["kept"] = min(len(archives), max(1, int(keep)))
    for obj in archives[max(1, int(keep)):]:
        key = str(obj["key"])
        for doomed in (key, manifest_key_for(key)):
            try:
                r2.delete_object(
                    cfg["r2_account_id"], cfg["offsite_backup_bucket"], doomed,
                    cfg["r2_access_key_id"], cfg["r2_secret_access_key"],
                )
            except Exception as exc:
                result["errors"].append(f"{doomed}: {exc}")
        result["removed"] += 1
    return result


def manifest_key_for(archive_key: str) -> str:
    """Return the sidecar manifest key for a backup archive key."""
    key = str(archive_key)
    return (key[: -len(".zip")] if key.endswith(".zip") else key) + ".manifest.json"


def build_offsite_manifest(summary: Dict[str, Any], zip_path: Path, sha256: str, key: str) -> Dict[str, Any]:
    """Return the unencrypted sidecar manifest describing one backup.

    Stored in the clear next to the archive on purpose. It names sections, sizes
    and the SHA-256, so the club can see what is off-site, and check a downloaded
    archive, without having to decrypt anything first. It deliberately contains
    no race data — only counts.
    """
    return {
        "app": "Pwllheli Race Officer",
        "app_version": appstate.APP_VERSION,
        "created_at": summary.get("created_at") or datetime.now().isoformat(timespec="seconds"),
        "object_key": key,
        "archive_name": zip_path.name,
        "encrypted": True,
        "encryption": "WinZip AES-256 (readable by 7-Zip with the passphrase)",
        "sections": list(summary.get("sections") or []),
        "file_counts": dict(summary.get("files") or {}),
        "zip_size_bytes": int(summary.get("zip_size_bytes") or 0),
        "sha256": sha256,
    }


def run_offsite_backup_once(cfg: Optional[Dict[str, Any]] = None, force: bool = False) -> Dict[str, Any]:
    """Build, encrypt, upload and verify one off-site backup.

    ``force`` skips only the busy check, for the Settings page's "Back up now"
    button — somebody standing at the PC asking for it knows better than the
    guard does. It never skips encryption or verification.
    """
    cfg = cfg or offsite_config()
    started_at = datetime.now()
    previous = read_offsite_status()
    status: Dict[str, Any] = {
        "ok": False,
        "enabled": bool(cfg["offsite_backup_enabled"]),
        "started_at": started_at.isoformat(timespec="seconds"),
        "bucket": cfg["offsite_backup_bucket"],
        "sections": list(cfg["offsite_backup_sections"]),
        # Carry the last success forward: a failure tonight must not erase the
        # fact that Tuesday worked, or the dashboard would read "never".
        "last_success_at": previous.get("last_success_at") or "",
    }

    # Before anything else, including the busy check. A scheduler tick landing
    # while a "back up now" run is in flight used to fall through to the busy
    # check, defer, and *save* that status over the running one — which is how the
    # hut ended up reporting the contradictory "Running now: Deferred — 84 race
    # video upload(s)". A run already in progress is not something to report on.
    with OFFSITE_LOCK:
        if OFFSITE_STATE.get("running"):
            return {**status, "message": "An off-site backup is already running.", "running": True}

    ready, why = offsite_credentials_ready(cfg)
    if not ready:
        status.update(message=why, last_error=why)
        save_offsite_status(status)
        return status

    if not force:
        busy, reason = hut_is_busy(started_at)
        if busy:
            status.update(
                deferred=True,
                message=f"Deferred — {reason} Off-site backup will try again shortly.",
                next_attempt_after=(started_at + timedelta(minutes=BUSY_RETRY_MINUTES)).isoformat(timespec="seconds"),
            )
            save_offsite_status(status)
            return status

    with OFFSITE_LOCK:
        if OFFSITE_STATE.get("running"):
            status.update(message="An off-site backup is already running.")
            return status
        OFFSITE_STATE["running"] = True
    zip_path: Optional[Path] = None
    try:
        # Before building anything: prove we can write to the bucket. Building the
        # archive is the expensive part — it snapshots three databases and encrypts
        # tens of megabytes — and there is no point paying for it to find out at the
        # end that the credentials cannot write here.
        save_offsite_status({**status, "message": "Checking the backup bucket can be written to…"})
        preflight_offsite_bucket(cfg)

        save_offsite_status({**status, "message": "Building an encrypted backup…"})
        zip_path, summary = backup.create_data_backup_zip(
            cfg["offsite_backup_sections"], passphrase=cfg["offsite_backup_passphrase"]
        )
        size_bytes = int(summary.get("zip_size_bytes") or zip_path.stat().st_size)
        sha256 = file_sha256(zip_path)
        key = offsite_object_key(cfg["offsite_backup_prefix"], started_at)
        status.update(size_bytes=size_bytes, sha256=sha256, object_key=key, file_counts=dict(summary.get("files") or {}))
        save_offsite_status({**status, "message": f"Uploading {size_bytes / 1_048_576:.1f} MB to Cloudflare R2…"})

        # Streamed from disk rather than read into memory, and via curl when it is
        # there, so a slow-but-working 4G link is not mistaken for a dead one.
        method = r2.put_file(
            cfg["r2_account_id"], cfg["offsite_backup_bucket"], key, zip_path,
            cfg["r2_access_key_id"], cfg["r2_secret_access_key"],
            content_type="application/zip", cache_control="private, no-store",
        )
        status.update(upload_method=method)
        head = verify_offsite_object(cfg, key, size_bytes)
        manifest = build_offsite_manifest(summary, zip_path, sha256, key)
        upload_offsite_object(cfg, manifest_key_for(key), (json.dumps(manifest, indent=2) + "\n").encode("utf-8"),
                              "application/json")

        finished_at = datetime.now()
        pruned = prune_offsite_backups(cfg, int(cfg["offsite_backup_keep"]))
        removed = int(pruned.get("removed") or 0)
        removed_note = f", {removed} old copy{'ies' if removed != 1 else 'y'} removed" if removed else ""
        status.update(
            ok=True,
            last_success_at=finished_at.isoformat(timespec="seconds"),
            finished_at=finished_at.isoformat(timespec="seconds"),
            duration_seconds=int((finished_at - started_at).total_seconds()),
            etag=head.get("etag", ""),
            removed_old=removed,
            prune_errors=pruned.get("errors", []),
            last_error="",
            message=f"Off-site backup verified in R2 ({size_bytes / 1_048_576:.1f} MB{removed_note}).",
        )
        log_activity("off-site backup uploaded", f"{key} ({size_bytes} bytes)")
    except Exception as exc:
        detail = str(exc)[:700]
        status.update(
            ok=False,
            last_error=detail,
            message=f"Off-site backup failed: {detail}",
            next_attempt_after=(datetime.now() + timedelta(minutes=FAILURE_RETRY_MINUTES)).isoformat(timespec="seconds"),
        )
        log_activity("off-site backup failed", detail)
    finally:
        # The archive is a full copy of the club's data sitting in runtime/;
        # remove it whether the upload worked or not.
        if zip_path is not None:
            try:
                zip_path.unlink(missing_ok=True)
            except OSError:
                pass
        with OFFSITE_LOCK:
            OFFSITE_STATE["running"] = False
    save_offsite_status(status)
    return status


# ---------------------------------------------------------------------------
# Scheduling.
# ---------------------------------------------------------------------------

def most_recent_due_time(cfg: Dict[str, Any], now: datetime) -> datetime:
    """Return the latest scheduled run time at or before ``now``."""
    due_today = now.replace(hour=int(cfg["offsite_backup_hour"]), minute=int(cfg["offsite_backup_minute"]),
                            second=0, microsecond=0)
    return due_today if due_today <= now else due_today - timedelta(days=1)


def offsite_backup_due(cfg: Dict[str, Any], status: Dict[str, Any], now: Optional[datetime] = None) -> bool:
    """Return whether a scheduled off-site backup should run now.

    Expressed as "has the most recent scheduled time been served?" rather than
    "is it 03:15?", so a PC that was switched off at 03:15 — the normal state of
    a hut PC — still takes its backup when it next comes up, instead of missing
    a day for every night it was off.
    """
    now = now or datetime.now()
    if not cfg.get("offsite_backup_enabled"):
        return False
    next_attempt = parse_dt(str(status.get("next_attempt_after") or ""))
    if next_attempt and now < next_attempt:
        return False
    due = most_recent_due_time(cfg, now)
    last_success = parse_dt(str(status.get("last_success_at") or ""))
    return last_success is None or last_success < due


def start_offsite_backup_now() -> Tuple[bool, str]:
    """Run one backup in a background thread, for the "back up now" button.

    It cannot be done inside the request. A backup snapshots three databases,
    encrypts tens of megabytes and pushes them over the hut's 4G — minutes of
    work — and the hut is reached through a cloudflared tunnel, which gives up on
    a request at around 100 seconds. Doing it synchronously returned Cloudflare's
    "Gateway time-out 504" to the race officer while the backup carried on and
    completed behind it: the archive was in R2 and the page said it had failed,
    which is the worst of both.

    So the button starts the work and returns at once, and the status box on the
    Settings page reports progress and the outcome. That status is already
    persisted for the dashboard, so nothing new is needed to observe it.
    """
    with OFFSITE_LOCK:
        if OFFSITE_STATE.get("running"):
            return False, "An off-site backup is already running. This page shows its progress."
    threading.Thread(target=run_offsite_backup_once, kwargs={"force": True},
                     name="offsite-backup-now", daemon=True).start()
    return True, ("Off-site backup started. It builds, encrypts, uploads and verifies in the background — "
                  "this page shows its progress and the result.")


def offsite_backup_loop() -> None:
    """Background loop that takes the nightly off-site backup."""
    while True:
        try:
            cfg = offsite_config()
            if cfg["offsite_backup_enabled"] and offsite_backup_due(cfg, read_offsite_status()):
                run_offsite_backup_once(cfg)
        except Exception as exc:                        # never let the loop die
            save_offsite_status({**read_offsite_status(), "ok": False,
                                 "message": f"Off-site backup scheduler error: {exc}"})
        time.sleep(SCHEDULER_TICK_SECONDS)


def start_offsite_backup_worker() -> None:
    """Start the off-site backup scheduler thread if it is not already running."""
    with OFFSITE_LOCK:
        if OFFSITE_STATE.get("started"):
            return
        OFFSITE_STATE["started"] = True
    threading.Thread(target=offsite_backup_loop, name="offsite-backup", daemon=True).start()
