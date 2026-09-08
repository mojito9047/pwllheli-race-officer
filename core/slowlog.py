"""Slow-request log: one line per request that took too long.

Written when the live system started refusing connections mid-race — Waitress
reporting a climbing task queue and "total open connections reached the
connection limit". There were four plausible causes in the code and no way to
tell which was actually biting, because nothing recorded how long a request took.
Guessing wrong there means fixing three things that were fine and leaving the one
that was not.

So this records, to ``runtime/logs/slow.log``, any request over a threshold, with
the path, the method, the status and the duration. Nothing else: a full access
log on a race-office PC serving video would be large and would itself cost
something, and the fast requests are not the ones in question.

Off by the ordinary case: below the threshold this costs one subtraction and one
comparison. The threshold is RO_SLOW_REQUEST_MS (default 250, 0 disables).
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Optional

from core import appstate
from core.logfiles import DatedFileHandler

_LOGGER: Optional[logging.Logger] = None
_LOCK = threading.Lock()

# Requests slower than this are logged. 250 ms is well clear of anything the app
# does normally — the leaderboard walk for a full fleet is tens of milliseconds —
# so a line here means something genuinely waited.
THRESHOLD_MS = int(os.environ.get("RO_SLOW_REQUEST_MS", "250") or 0)


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
            logger = logging.getLogger("pwllheli.slow")
            logger.setLevel(logging.INFO)
            logger.propagate = False
            if not any(isinstance(h, DatedFileHandler) for h in logger.handlers):
                # Day-stamped, never renamed: see core/logfiles.py for what a
                # rename at midnight costs on Windows.
                handler = DatedFileHandler(log_dir, "slow", keep_days=14)
                handler.setFormatter(logging.Formatter(
                    "%(asctime)s | %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
                logger.addHandler(handler)
            _LOGGER = logger
        except Exception:
            # A logger that cannot be built must not stop the app serving. The
            # whole point of this file is to help during an incident.
            _LOGGER = None
        return _LOGGER


def log_slow(method: str, path: str, status: Any, ms: float, note: str = "") -> None:
    """Append one slow-request line. Best effort; never raises."""
    logger = _get_logger()
    if logger is None:
        return
    try:
        line = f"{ms:7.0f} ms | {str(method):<6} {str(path)[:120]} | {status}"
        if note:
            line += f" | {note}"
        logger.info(line)
    except Exception:
        pass


def install(app) -> None:
    """Attach the timer to a Flask app. A no-op when the threshold is 0."""
    if THRESHOLD_MS <= 0:
        return

    @app.before_request
    def _slow_start():                      # pragma: no cover - trivial
        from flask import g
        g._slow_t0 = time.perf_counter()

    @app.after_request
    def _slow_end(response):
        from flask import g, request
        t0 = getattr(g, "_slow_t0", None)
        if t0 is not None:
            ms = (time.perf_counter() - t0) * 1000.0
            if ms >= THRESHOLD_MS:
                # The query string is deliberately left off: it carries race ids
                # and timestamps that make every line unique and the log useless
                # to group by. The path is what identifies the endpoint.
                log_slow(request.method, request.path, response.status_code, ms)
        return response
