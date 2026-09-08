"""Replaying a race on the clubhouse display.

The bar television is a screen nobody touches: no keyboard, no mouse, left on
all afternoon. So a replay cannot be something you open a URL for at the
television — it is chosen from a race sheet by whoever is in the bar, and the
television finds out on the poll it already makes.

That is the whole of the state here: which race, and when the choosing
happened. Everything else the display needs is worked out from the race itself.

**The clock is the interesting part, and it lives in the browser.** A replay
runs at six times life so an afternoon's racing fits in a bar's attention span,
*except* while a clip is playing, when it drops to real time and follows the
video's own playback. Locking the clock to the video rather than running them
side by side is what stops them drifting: when a two-minute clip ends, the
replay clock has advanced exactly two minutes and the boats on the chart are
where the video left them.

That cannot be computed here, because only the browser knows how the video is
actually playing — buffering, a slow decode, a television that throttles a
background tab. So the server says *what* to replay and *what is in it*; the
display keeps the time.

One consequence worth knowing: a television reloaded mid-replay starts the
replay again from the warning signal. It could be made to resume, but the state
to do it belongs to the browser that was watching, and a bar screen that
restarts a replay is a smaller surprise than one that jumps into the middle of
one.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional

from core import appstate
from core.db import get_db, init_db, row_get
from core.races import race_first_start_dt, race_first_warning_dt
from core.timeutils import parse_dt

# Transient display state, not a setting: nothing here is configuration, and it
# has no business in the settings diff the activity log keeps. A file beside the
# other runtime status files is the existing pattern for "state the pages read".
BAR_REPLAY_PATH = appstate.RUNTIME_DIR / "bar_replay.json"
_LOCK = threading.Lock()

# How much faster than life, when no video is playing.
REPLAY_SPEED = 6
# How long the finishing order stays up at the end before the television goes
# back to being the television. Long enough to read a full fleet, short enough
# that nobody has to go and stop it.
RESULTS_HOLD_S = 90
# A replay that nobody stopped and no display ever picked up should not sit in
# the file for ever claiming the screen.
STALE_AFTER_S = 6 * 3600
# How much footage a clip must add beyond the one before it to be worth playing.
# Clips are cut from the same rolling buffer with a minute either side, so a
# fleet finishing within a few minutes of each other produces clips that are
# mostly the same video: nine boats finishing inside six minutes gave twenty
# minutes of clip covering six minutes of racing. Played in full that is the
# same water over and over, and a replay longer than the race it is replaying.
MIN_NEW_FOOTAGE_S = 25


def _read() -> Dict[str, Any]:
    try:
        return json.loads(BAR_REPLAY_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write(state: Dict[str, Any]) -> None:
    try:
        BAR_REPLAY_PATH.parent.mkdir(parents=True, exist_ok=True)
        BAR_REPLAY_PATH.write_text(json.dumps(state), encoding="utf-8")
    except OSError:
        # A television that does not start a replay is a disappointment; an
        # exception out of a race-sheet button is a broken page.
        pass


def current_replay() -> Dict[str, Any]:
    """Which race the clubhouse display should be replaying, if any."""
    state = _read()
    race_id = state.get("race_id")
    if not race_id:
        return {"race_id": None, "started_at": "", "by": ""}
    started = parse_dt(str(state.get("started_at") or ""))
    if started and (datetime.now() - started).total_seconds() > STALE_AFTER_S:
        return {"race_id": None, "started_at": "", "by": ""}
    return {"race_id": int(race_id), "started_at": str(state.get("started_at") or ""),
            "by": str(state.get("by") or "")}


def start_replay(race_id: int, actor: str = "system") -> Dict[str, Any]:
    """Put the clubhouse display onto a replay of this race."""
    with _LOCK:
        state = {"race_id": int(race_id),
                 "started_at": datetime.now().isoformat(timespec="seconds"),
                 "by": str(actor or "")}
        _write(state)
    return state


def stop_replay() -> None:
    """Send the display back to whatever is happening now."""
    with _LOCK:
        _write({})


def _segment_seconds() -> int:
    """How long one rolling-buffer segment is, in seconds."""
    try:
        from core.video import video_config
        return max(1, int(video_config().get("video_segment_seconds") or 5))
    except Exception:
        return 5


def footage_start_ts(row: Any, event_dt: datetime, pre: int) -> float:
    """When the clip file's own first frame was taken, in epoch seconds.

    Clips are cut by concatenating whole buffer segments, so a file does not
    begin at ``event_time - pre_seconds``: it begins at the segment boundary at
    or before it, up to a whole segment earlier. On the hut, with twenty-second
    segments, a start clip claiming 11:24:00 in fact opened at 11:23:44 -- and
    a replay that believed the claim drew the boats sixteen seconds ahead of the
    picture they were drawn over.

    Clips built from v0.268 record where their footage really starts. For older
    ones the boundary is gone with the buffer and cannot be recovered -- the
    file is always pre + post + one segment long however the overhang falls, so
    even its duration says nothing. The middle of the segment is then the honest
    guess: wrong by at most half a segment rather than by a whole one.
    """
    window = event_dt.timestamp() - pre
    recorded = parse_dt(str(row_get(row, "footage_started_at", "") or ""))
    if recorded:
        # Only if it describes footage that could hold this event: a bad clock
        # or a stray filename must not drag the clip away from its moment.
        if window - 2 * _segment_seconds() <= recorded.timestamp() <= window + 1:
            return recorded.timestamp()
    return window - (_segment_seconds() / 2.0)


def replay_clips(race_id: int) -> List[Dict[str, Any]]:
    """The race's video, in the order it happened.

    Every clip that was actually built: the start, each finish, and anything
    else recorded on the day. A clip that failed or is still pending is left
    out — a replay that stops for a video which never arrives is worse than one
    that does not stop.

    The public URL is preferred where a clip has one. It is the same video, on
    Cloudflare rather than on the hut's uplink, and a replay is twenty minutes
    of it.
    """
    init_db()
    with get_db() as db:
        rows = db.execute(
            "SELECT id, clip_type, event_time, pre_seconds, post_seconds, label,"
            " footage_started_at, public_url, public_status FROM video_clips"
            " WHERE race_id = ? AND status = 'ready' ORDER BY event_time, id",
            (race_id,)).fetchall()
    clips: List[Dict[str, Any]] = []
    for row in rows:
        at = parse_dt(str(row_get(row, "event_time", "") or ""))
        if not at:
            continue
        pre = int(row_get(row, "pre_seconds", 60) or 60)
        post = int(row_get(row, "post_seconds", 60) or 60)
        public_url = str(row_get(row, "public_url", "") or "").strip()
        footage = footage_start_ts(row, at, pre)
        ready_public = str(row_get(row, "public_status", "") or "") == "ready"
        clips.append({
            "id": int(row["id"]),
            "kind": str(row_get(row, "clip_type", "") or ""),
            "label": str(row_get(row, "label", "") or ""),
            # When the clip's own first frame was taken, in race time. The
            # display locks its clock to this while the video plays, so it is
            # the file's real start and not the window that was asked for.
            "starts_at": footage,
            "ends_at": (at.timestamp() + post),
            "seconds": (at.timestamp() + post) - footage,
            "url": public_url if (public_url and ready_public)
                   else f"/public/video/clip/{int(row['id'])}",
        })
    return _without_repeated_footage(clips)


def _without_repeated_footage(clips: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Drop clips that would replay footage the previous one already showed.

    Kept as a separate pass rather than folded into the query because it is a
    judgement about watchability, not about what was recorded: every clip is
    still there on the race sheet, and a race officer looking for one boat's
    finish still finds it. This is only about what the bar sees.
    """
    kept: List[Dict[str, Any]] = []
    for clip in clips:
        if kept and clip["ends_at"] <= kept[-1]["ends_at"] + MIN_NEW_FOOTAGE_S:
            kept[-1].setdefault("also_covers", []).append(clip["label"] or clip["kind"])
            continue
        kept.append(clip)
    return kept


def replay_plan(race: sqlite3.Row) -> Optional[Dict[str, Any]]:
    """Everything the display needs to replay this race, or None if it cannot.

    From the warning signal, because that is where the racing starts being worth
    watching -- the flags go up, the fleet forms up on the line -- to the last
    boat across. A race with no start time or nobody finished has nothing to
    replay, and saying so is better than an empty screen counting up.
    """
    if race is None:
        return None
    race_id = int(race["id"])
    warning = race_first_warning_dt(race)
    if not warning:
        return None
    finishes = []
    with get_db() as db:
        for row in db.execute(
                "SELECT finish_time FROM entries WHERE race_id = ? AND finish_time IS NOT NULL"
                " AND TRIM(finish_time) != ''", (race_id,)).fetchall():
            moment = parse_dt(str(row["finish_time"] or ""))
            if moment:
                finishes.append(moment.timestamp())
    if not finishes:
        return None
    gun = race_first_start_dt(race)
    return {
        "race_id": race_id,
        "name": str(row_get(race, "name", "") or ""),
        "from_ts": warning.timestamp(),
        "gun_ts": gun.timestamp() if gun else None,
        "to_ts": max(finishes),
        "speed": REPLAY_SPEED,
        "results_hold_s": RESULTS_HOLD_S,
        "clips": replay_clips(race_id),
    }


def replay_is_possible(race: sqlite3.Row) -> bool:
    """Whether there is anything to replay, for a button that should say so."""
    return replay_plan(race) is not None
