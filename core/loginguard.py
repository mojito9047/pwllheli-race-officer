"""In-memory login brute-force guard.

Tracks failed sign-in attempts per (client-ip, username) in a sliding window and
locks that pair out for a cooldown once too many failures accumulate. Keying on
both ip and username means a wrong-password storm from one attacker cannot lock a
legitimate user signing in from a different address, and a single proxy IP does
not lump every client together.

The app is a single Waitress process, so a module-level dict guarded by a lock is
sufficient; there is nothing to persist across restarts (a restart simply resets
the counters, which is acceptable for brute-force slowing).
"""
from __future__ import annotations

import threading
import time
from typing import Dict, List, Optional

MAX_ATTEMPTS = 8            # failures allowed within the window before lockout
WINDOW_SECONDS = 900       # 15 minutes

_ATTEMPTS: Dict[str, List[float]] = {}
_LOCK = threading.Lock()


def _recent(times: List[float], now: float) -> List[float]:
    return [t for t in times if now - t < WINDOW_SECONDS]


def is_locked(key: str, now: Optional[float] = None) -> bool:
    """Return True when `key` has hit the failure limit within the window."""
    now = time.time() if now is None else now
    with _LOCK:
        return len(_recent(_ATTEMPTS.get(key, []), now)) >= MAX_ATTEMPTS


def record_failure(key: str, now: Optional[float] = None) -> None:
    """Record one failed sign-in for `key`, pruning expired entries."""
    now = time.time() if now is None else now
    with _LOCK:
        times = _recent(_ATTEMPTS.get(key, []), now)
        times.append(now)
        _ATTEMPTS[key] = times


def clear(key: str) -> None:
    """Forget failures for `key` (call on a successful sign-in)."""
    with _LOCK:
        _ATTEMPTS.pop(key, None)


def reset_all() -> None:
    """Clear all tracked attempts (used by tests)."""
    with _LOCK:
        _ATTEMPTS.clear()
