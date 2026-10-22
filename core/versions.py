"""Which versions are running, and whether GitHub has a newer one.

Three machines make up the club's system, and each is installed from a release
of this project: the app on the hut PC, the relay, and the 3D render machine.
The dashboard shows all three beside the latest release on GitHub, so an
upgrade waiting to be done is in plain sight rather than discovered.

* **The app** knows its own version (VERSION).
* **The render machine** sends its release with the heartbeat it already
  writes to the bucket (scripts/replay3d/renderer.py).
* **The relay** is a set of files updated one at a time, by hand, so it has no
  single version to report. relay_version.py on the relay publishes a checksum
  of each, and they are compared here with this release's own copies under
  deploy/live_stream. "Matches v1.020" or "relay_wind.py differs" is the
  answer that stays true when somebody copies one file across. Traccar on the
  relay reports its own version through the API the app already uses.

Nothing here is fetched on a page view. A worker does it: the relay, Traccar and
the render machine every half hour, GitHub every six hours. Unauthenticated
GitHub requests are limited to sixty an hour per address, and the hut shares
its 4G address with nobody it can vouch for. The dashboard shows what was last
found, and says when that was.

RO_UPDATE_CHECK=0 turns the GitHub check off; RO_RELEASES_REPO names another
repository to compare with.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core import appstate

RELEASES_REPO = os.environ.get("RO_RELEASES_REPO", "mojito9047/pwllheli-race-officer").strip()
UPDATE_CHECK_ENABLED = os.environ.get("RO_UPDATE_CHECK", "1").strip().lower() not in ("0", "false", "no", "off")

GITHUB_EVERY_S = 6 * 3600
GITHUB_RETRY_S = 30 * 60          # after a failure: soon, but not every minute
LOCAL_EVERY_S = 30 * 60
TICK_S = 60
HTTP_TIMEOUT_S = 10.0
# Cloudflare's bot rules refuse urllib's own User-Agent, and GitHub requires one.
USER_AGENT = "Mozilla/5.0 (PwllheliRaceOfficer)"

RELAY_FILES_DIR = ("deploy", "live_stream")

VERSIONS_LOCK = threading.Lock()
VERSIONS_STATE: Dict[str, Any] = {
    "started": False,
    "github": {}, "github_at": 0.0,
    "relay": {}, "traccar": {}, "renderer": {}, "local_at": 0.0,
}


# ---------------------------------------------------------------------------
# Comparing versions.
# ---------------------------------------------------------------------------

def version_tuple(text: Any) -> Optional[Tuple[int, ...]]:
    """``"v1.019"`` and ``"1.019"`` as ``(1, 19)``; None for anything without a number."""
    numbers = re.findall(r"\d+", str(text or ""))
    return tuple(int(n) for n in numbers) if numbers else None


def compare(running: Any, latest: Any) -> str:
    """``current``, ``upgrade`` (a newer release is out), ``ahead`` or ``unknown``."""
    mine, theirs = version_tuple(running), version_tuple(latest)
    if mine is None or theirs is None:
        return "unknown"
    if mine == theirs:
        return "current"
    return "upgrade" if mine < theirs else "ahead"


# ---------------------------------------------------------------------------
# Fetching.
# ---------------------------------------------------------------------------

def _get_json(url: str, headers: Optional[Dict[str, str]] = None) -> Any:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json",
                                                   **(headers or {})})
    with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_S) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_latest_release() -> Dict[str, Any]:
    """The newest published release of this project on GitHub."""
    data = _get_json(f"https://api.github.com/repos/{RELEASES_REPO}/releases/latest",
                     {"Accept": "application/vnd.github+json"})
    tag = str(data.get("tag_name") or "")
    return {"tag": tag, "version": tag.lstrip("vV"), "url": str(data.get("html_url") or ""),
            "published_at": str(data.get("published_at") or ""), "error": ""}


def relay_file_hash(path: Path) -> Optional[str]:
    """The checksum relay_version.py takes: SHA-256 with line endings made LF."""
    try:
        data = Path(path).read_bytes()
    except OSError:
        return None
    return hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()


def compare_relay_files(reported: Dict[str, Any]) -> Dict[str, List[str]]:
    """Sort the relay's files into those that match this release, differ, or are missing.

    A name this release has no copy of is "unknown" rather than "differs": the
    relay may have been updated from a newer release than the hut.
    """
    base = appstate.BASE_DIR.joinpath(*RELAY_FILES_DIR)
    out: Dict[str, List[str]] = {"matched": [], "differ": [], "missing": [], "unknown": []}
    for name in sorted(reported):
        if "\\" in name or ".." in Path(name).parts or Path(name).is_absolute():
            continue
        mine = relay_file_hash(base / name)
        theirs = reported[name]
        if mine is None:
            out["unknown"].append(name)
        elif theirs is None:
            out["missing"].append(name)
        elif str(theirs) == mine:
            out["matched"].append(name)
        else:
            out["differ"].append(name)
    return out


def fetch_relay() -> Dict[str, Any]:
    """What the relay says it runs, compared with this release's copies."""
    from core.horn import public_base_url
    base = public_base_url()
    if not base:
        return {"ok": False, "message": "Set Settings → Web server → Public address: it is how the hut reaches "
                                        "its relay."}
    not_listing = {"ok": False, "message": "The relay does not list its files yet. It needs relay_version.py "
                                           "and the /relay/version.json block in its Caddyfile "
                                           "(deploy/live_stream/README.md)."}
    try:
        data = _get_json(base.rstrip("/") + "/relay/version.json")
    except urllib.error.HTTPError as exc:
        # A relay without the block passes the request on to this app, which has
        # no such page: a 404 from here means the relay is up but not listing.
        if exc.code == 404:
            return not_listing
        return {"ok": False, "message": f"The relay answered HTTP {exc.code}."}
    except ValueError:
        return not_listing                # answered, but not with the list
    except Exception as exc:
        return {"ok": False, "message": f"Could not reach the relay ({type(exc).__name__})."}
    files = data.get("files") if isinstance(data, dict) else None
    if not isinstance(files, dict):
        return {"ok": False, "message": "The relay's file list could not be read."}
    return {"ok": True, "generated_at": str(data.get("generated_at") or ""), **compare_relay_files(files)}


def fetch_traccar() -> Dict[str, Any]:
    """Traccar's own version, through the API the app already uses; {} without tracking."""
    from core import track
    cfg = track.track_config()
    if not (cfg.get("base_url") and cfg.get("token")):
        return {}
    data = track._traccar_get(cfg, "/api/server")
    return {"version": str((data or {}).get("version") or "")}


def fetch_renderer() -> Dict[str, Any]:
    """The render machine's last heartbeat, and the release it said it runs."""
    from core import replay3d
    if not replay3d.bucket_config():
        return {"configured": False}
    beat = replay3d.read_heartbeat()
    if not beat:
        return {"configured": True, "seen": False}
    return {"configured": True, "seen": True, "up": replay3d.renderer_is_up(beat),
            "release": str(beat.get("release") or ""), "updated_at": float(beat.get("updated_at") or 0.0)}


def refresh(now: Optional[float] = None) -> None:
    """Fetch whatever is due. Each part fails on its own, and says why."""
    when = now if now is not None else time.time()
    with VERSIONS_LOCK:
        github_due = UPDATE_CHECK_ENABLED and when >= VERSIONS_STATE["github_at"]
        local_due = when >= VERSIONS_STATE["local_at"]
    if github_due:
        try:
            github, next_at = fetch_latest_release(), when + GITHUB_EVERY_S
        except Exception as exc:
            github, next_at = {"error": f"Could not check GitHub ({type(exc).__name__})."}, when + GITHUB_RETRY_S
        with VERSIONS_LOCK:
            VERSIONS_STATE.update(github={**github, "checked_at": when}, github_at=next_at)
    if local_due:
        parts = {}
        for key, fetch in (("relay", fetch_relay), ("traccar", fetch_traccar), ("renderer", fetch_renderer)):
            try:
                parts[key] = fetch()
            except Exception as exc:
                parts[key] = {"ok": False, "error": type(exc).__name__}
        with VERSIONS_LOCK:
            VERSIONS_STATE.update(parts, local_at=when + LOCAL_EVERY_S, local_checked_at=when)


def versions_loop() -> None:
    while True:
        try:
            refresh()
        except Exception:
            pass                      # never let the loop die
        time.sleep(TICK_S)


def start_versions_worker() -> None:
    with VERSIONS_LOCK:
        if VERSIONS_STATE.get("started"):
            return
        VERSIONS_STATE["started"] = True
    threading.Thread(target=versions_loop, name="versions", daemon=True).start()


# ---------------------------------------------------------------------------
# What the dashboard shows.
# ---------------------------------------------------------------------------

def _when(ts: Any) -> str:
    try:
        return datetime.fromtimestamp(float(ts)).strftime("%d %b %H:%M")
    except (TypeError, ValueError, OSError):
        return ""


def _relay_row(state: Dict[str, Any], latest: str) -> Dict[str, Any]:
    relay, traccar = state.get("relay") or {}, state.get("traccar") or {}
    row: Dict[str, Any] = {"key": "relay", "name": "Relay", "running": "", "state": "unknown", "detail": ""}
    if not state.get("local_checked_at"):
        row.update(state="checking", detail="Not checked yet.")
    elif not relay.get("ok"):
        row["detail"] = relay.get("message") or "Could not check the relay."
    elif relay.get("differ") or relay.get("missing"):
        mine = appstate.APP_VERSION
        names = relay.get("differ", []) + [f"{n} (missing)" for n in relay.get("missing", [])]
        row.update(state="differs",
                   detail=(f"{len(names)} files differ" if len(names) != 1 else "1 file differs")
                   + f" from v{mine}: " + ", ".join(names)
                   + ". Update the relay from this release (deploy/live_stream/README.md, 5a), "
                     "or the hut, if the relay is the newer.")
    else:
        row.update(running=f"v{appstate.APP_VERSION}", state=compare(appstate.APP_VERSION, latest),
                   detail=f"All {len(relay.get('matched', []))} relay files match this release.")
    if traccar.get("version"):
        row["detail"] = (row["detail"] + " " if row["detail"] else "") + f"Traccar {traccar['version']}."
    return row


def _renderer_row(state: Dict[str, Any], latest: str) -> Optional[Dict[str, Any]]:
    renderer = state.get("renderer") or {}
    if state.get("local_checked_at") and not renderer.get("configured", True):
        return None                   # no bucket, so no render machine to speak of
    row: Dict[str, Any] = {"key": "renderer", "name": "Render machine", "running": "", "state": "unknown", "detail": ""}
    if not state.get("local_checked_at"):
        row.update(state="checking", detail="Not checked yet.")
    elif renderer.get("error"):
        row["detail"] = f"Could not read its heartbeat ({renderer['error']})."
    elif not renderer.get("seen"):
        row["detail"] = "No render machine has checked in."
    elif not renderer.get("release"):
        row["detail"] = "It checks in, but runs a release from before render machines reported theirs."
    else:
        row.update(running=f"v{renderer['release']}", state=compare(renderer["release"], latest))
    if renderer.get("seen") and not renderer.get("up"):
        row["detail"] = (row["detail"] + " " if row["detail"] else "") + \
            f"Last checked in {_when(renderer.get('updated_at'))}; it may be switched off."
    return row


def versions_status() -> Dict[str, Any]:
    """The rows for the dashboard card, from what the worker last found."""
    with VERSIONS_LOCK:
        state = {key: (dict(value) if isinstance(value, dict) else value) for key, value in VERSIONS_STATE.items()}
    github = state.get("github") or {}
    latest = str(github.get("version") or "")
    rows = [{"key": "app", "name": "Race Officer (this PC)", "running": f"v{appstate.APP_VERSION}",
             "state": compare(appstate.APP_VERSION, latest), "detail": ""},
            _relay_row(state, latest)]
    renderer = _renderer_row(state, latest)
    if renderer:
        rows.append(renderer)
    if not UPDATE_CHECK_ENABLED:
        note = "Checking GitHub for new releases is switched off (RO_UPDATE_CHECK=0)."
    elif github.get("error"):
        note = github["error"]
    elif not latest:
        note = "Checking GitHub…"
    else:
        note = ""
    return {
        "rows": rows,
        "latest": github if latest else {},
        "latest_published": (github.get("published_at") or "")[:10],
        "note": note,
        "checked": _when(state.get("local_checked_at")),
        "upgrade_available": any(row["state"] in ("upgrade", "differs") for row in rows),
    }
