"""Daily-rotating activity/audit log.

A plain-text history of who did what — logins, and race/boat/entry/settings
changes — written to ``runtime/logs/`` with no UI. There is one file per day, named for the day
(``activity-2026-08-17.log``) and never renamed -- renaming the live file at
midnight is what used to lose it, see :mod:`core.logfiles`. Kept deliberately
simple and best-effort: a logging failure must never break a request.
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Optional

from core import appstate
from core import logfiles

_LOGGER: Optional[logging.Logger] = None
_LOCK = threading.Lock()


def _get_logger() -> Optional[logging.Logger]:
    global _LOGGER
    if _LOGGER is not None:
        return _LOGGER
    with _LOCK:
        if _LOGGER is not None:
            return _LOGGER
        try:
            log_dir = appstate.RUNTIME_DIR / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            logger = logging.getLogger("pwllheli.activity")
            logger.setLevel(logging.INFO)
            logger.propagate = False
            if not logger.handlers:
                handler = logfiles.DatedFileHandler(log_dir, "activity", keep_days=400)
                handler.setFormatter(logging.Formatter("%(asctime)s | %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
                logger.addHandler(handler)
            _LOGGER = logger
        except Exception:
            _LOGGER = None
        return _LOGGER


def log_activity(action: str, details: str = "", user: str = "system") -> None:
    """Append one activity line: ``<time> | user=<user> | <action> | <details>``."""
    logger = _get_logger()
    if logger is None:
        return
    parts = [f"user={user or 'anonymous'}", str(action)]
    if details:
        parts.append(str(details))
    try:
        logger.info(" | ".join(parts))
    except Exception:
        pass


# Settings names whose values must never reach a plain-text file. Matched as
# substrings so a key added later ("relay_api_token", say) is redacted by default
# rather than needing to be remembered here — the safe direction to fail in.
# "access_key" was specific to R2's key pair and would not have caught
# assistant_api_key. A bare "key" is too broad the other way -- finish_line_key
# names a line, not a credential, and redacting it would make the log useless
# for the setting most likely to be looked up after a race with no GPS finishes.
SECRET_NAME_HINTS = ("password", "secret", "passphrase", "token", "access_key", "api_key")

# Long enough for a bucket name, a COM port or a URL's shape; short enough that one
# changed setting cannot push a line to hundreds of characters.
MAX_LOGGED_VALUE = 60


def is_secret_setting(name: str) -> bool:
    """Return whether a settings key holds something that must not be logged."""
    lowered = str(name).lower()
    return any(hint in lowered for hint in SECRET_NAME_HINTS)


def _short(value: object) -> str:
    text = str(value if value is not None else "")
    text = text.replace("\r", " ").replace("\n", " ").strip()
    if not text:
        return "(blank)"
    return text if len(text) <= MAX_LOGGED_VALUE else text[:MAX_LOGGED_VALUE - 1] + "…"


def settings_change_summary(before: dict, after: dict) -> str:
    """Describe what a settings save actually changed, as ``key: old -> new``.

    Answering "who changed that, and what was it before?" is the point of logging a
    settings save at all — the old entry recorded only that somebody pressed Save.
    Knowing the previous value is what lets a change be undone or a fault explained;
    it is how the horn output being switched to active-low would have been traced.

    Secrets are reported as changed without their values, in either direction, so
    the log never becomes somewhere to read a password out of.
    """
    changes = []
    for key in sorted(set(after)):
        old, new = before.get(key), after.get(key)
        if str(old if old is not None else "") == str(new if new is not None else ""):
            continue
        if is_secret_setting(key):
            changes.append(f"{key}: changed" if old else f"{key}: set")
        else:
            changes.append(f"{key}: {_short(old)} -> {_short(new)}")
    return "; ".join(changes)


# ---------------------------------------------------------------------------
# Reading it back (Settings -> Activity log)
# ---------------------------------------------------------------------------
# The file was always readable on the race-office PC, which is no use to somebody
# asking "who changed that?" from the other side of the club over Cloudflare — and
# it is the one record that answers it. Read-only, administrators only: it names
# users and what they did.
LOG_DAY_PATTERN = logfiles.DAY_PATTERN
# A day's racing is a few dozen lines; a day of somebody hammering Save is more. Cap
# what is rendered so one enormous file cannot hang the page, and say when it bites.
MAX_LOG_LINES = 2000


def log_dir() -> Path:
    return appstate.RUNTIME_DIR / "logs"


def log_days() -> list:
    """Which days of activity log exist, today first then newest.

    Reads both namings, so upgrading loses no history: the day-stamped files
    written now, and the ``activity.log`` / ``activity.log.YYYY-MM-DD`` the old
    rotating handler left behind.
    """
    by_day = logfiles.files_by_day(log_dir(), "activity")
    now = logfiles.today()
    out = []
    for day, paths in by_day.items():
        size = 0
        for path in paths:
            try:
                size += path.stat().st_size
            except OSError:
                continue
        is_today = day == now
        # Today keeps an empty ``day``, which is what the page links as "no
        # query string" and highlights as the active tab.
        out.append({"day": "" if is_today else day, "today": is_today,
                    "label": "Today" if is_today else day, "bytes": size})
    out.sort(key=lambda d: d["day"], reverse=True)
    out.sort(key=lambda d: not d["today"])
    return out


def read_log(day: str = "") -> dict:
    """Read one day's activity log, newest line first.

    ``day`` is a bare ``YYYY-MM-DD`` from the query string, so the filename is built
    here from a validated date rather than taken from the request: anything else is
    treated as today. Nothing in this module joins a caller's string onto a path.

    A day can have more than one file after the upgrade — the day-stamped one and
    whatever the old rotating handler left for the same day — so they are read in
    turn and shown as one day.
    """
    day = str(day or "").strip()
    if day and not LOG_DAY_PATTERN.match(day):
        day = ""
    paths = logfiles.files_by_day(log_dir(), "activity").get(day or logfiles.today(), [])
    lines = []
    found = False
    for path in paths:
        try:
            raw = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        found = True
        lines.extend(line for line in raw if line.strip())
    if not found:
        return {"day": day, "lines": [], "total": 0, "truncated": False, "missing": True}
    total = len(lines)
    shown = lines[-MAX_LOG_LINES:]
    shown.reverse()
    return {"day": day, "lines": shown, "total": total,
            "truncated": total > len(shown), "missing": False}
