#!/usr/bin/env python3
"""What the hut and the render machine have to agree on, and nothing else.

Where a job lives in the bucket, what a status object looks like, and when a
claim has gone stale. That is the whole conversation between the two halves --
they never talk directly, they only leave things in R2 for each other.

It is its own module so the render machine can import it without importing the
app. ``core.appstate`` reads courses.json, marks.json and start_finish.json at
import time, so anything that reaches it drags the club's race data onto a
machine whose only job is to turn a finished scene file into a film. The
renderer needs none of that -- the course, the marks and both lines are already
baked into the job it is handed -- and asking a render box to carry a copy of
the club's data directory is both pointless and one more thing to keep in step.
"""
from __future__ import annotations

import time
from typing import Any, Dict, Optional

SCENE_FORMAT = "pwllheli-replay3d/1"


# Where things live in the bucket the race videos already use. The renderer
# never talks to the hut: the hut pushes here, the renderer polls here, and
# nothing has to be opened up on either side.
JOBS_PREFIX = "replay3d/jobs"
STATUS_PREFIX = "replay3d/status"
FILMS_PREFIX = "replay3d/films"
# One file, written by whichever renderer is running, saying it is alive. This
# is what turns "I pressed the button and nothing happened" into "the render
# machine has not checked in since ten past two".
HEARTBEAT_KEY = f"{STATUS_PREFIX}/renderer.json"

# The states a job moves through, in order. Anything not in this list is a bug
# in the renderer rather than a state the app should try to render.
JOB_STATES = ("queued", "claimed", "rendering", "composing", "uploading", "done", "failed")


def job_key(race_id: int) -> str:
    return f"{JOBS_PREFIX}/race_{int(race_id)}.json"


def status_key(race_id: int) -> str:
    return f"{STATUS_PREFIX}/race_{int(race_id)}.json"


def film_key(race_id: int) -> str:
    return f"{FILMS_PREFIX}/race_{int(race_id)}.mp4"

def blank_status(race_id: int, state: str = "queued", message: str = "") -> Dict[str, Any]:
    """The status object a renderer keeps up to date beside the job."""
    now = time.time()
    return {
        "version": 1,
        "race_id": int(race_id),
        "state": state if state in JOB_STATES else "queued",
        "message": message,
        "progress": 0.0,
        "frames_done": 0,
        "frames_total": 0,
        "eta_s": None,
        "renderer": "",
        "film_url": "",
        "error": "",
        "created_at": now,
        "updated_at": now,
    }


def status_is_stale(status: Optional[Dict[str, Any]], now: Optional[float] = None,
                    after_s: float = 600.0) -> bool:
    """True when a job claims to be working but has not said so for a while.

    A render is hours long and a renderer that dies mid-job leaves its last
    status behind looking healthy. Only the moving states go stale; a finished
    or failed job is allowed to sit there for as long as it likes.
    """
    if not status or status.get("state") in ("done", "failed", "queued"):
        return False
    updated = float(status.get("updated_at") or 0.0)
    return ((now if now is not None else time.time()) - updated) > after_s
