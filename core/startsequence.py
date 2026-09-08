"""Central start-sequence automation: server-side horns and audio on time.

Extracted verbatim from app.py. A daemon scheduler derives each race's warning/
preparatory/one-minute/start signals from the stored start schedule, fires the
horn and queues spoken announcements at the right moments (deduplicated via the
event log and the in-process fired-keys set so restarts and edits do not
re-sound old signals), keeps the camera preset in step and reschedules cleanly
after a race edit.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from core.audio import (
    AUDIO_LOCK,
    AUDIO_RUNTIME_STATE,
    purge_queued_start_sequence_audio,
    queue_central_audio,
    queue_central_tone,
)
from core.classconfig import class_flags_text_for_start, race_start_plan, race_start_schedule
from core.courses import course_announcement_text, course_for_race
from core.db import get_db, init_db, row_get
from core.eventlog import log_event
from core.horn import fire_horn, hardware_config
from core.racesignals import AP_DOWN_WARNING_LEAD_S
from core.races import (
    postponement_is_due_to_end,
    race_first_start_time,
    race_has_finished_for_sequence,
    race_is_postponed,
)
from core.settings import race_console_config
from core.timeutils import parse_dt
from core.video import ensure_start_video_scheduled, schedule_video_clip, update_camera_preset_for_races, video_config

START_SEQUENCE_STATE: Dict[str, Any] = {
    "started": False,
    "fired_keys": set(),
    "last_status": {"ok": True, "message": "Start-sequence scheduler has not started yet."},
}
START_SEQUENCE_LOCK = threading.Lock()


# "Ten. Nine. ... One." spoken as one utterance takes about eleven seconds at a
# speech rate of 185, which is where the old hard-coded eleven came from. Speech
# duration scales inversely with the rate, so the lead is derived rather than
# fixed: at a faster countdown rate the phrase is shorter and must start later,
# and at a slower one it must start earlier. Fixing the lead at eleven is what
# put "One" after the gun when the club slowed its announcements down.
#
# These two are the tuning knobs if the count ever drifts: time the phrase at a
# known rate and set them to what you measured.
COUNTDOWN_REFERENCE_RATE = 185
COUNTDOWN_PHRASE_SECONDS_AT_REFERENCE = 11.0


def countdown_lead_seconds(rate: Any) -> float:
    """How far before the gun to begin the ten-count, at this speech rate."""
    try:
        spoken_at = max(60, int(rate or COUNTDOWN_REFERENCE_RATE))
    except (TypeError, ValueError):
        spoken_at = COUNTDOWN_REFERENCE_RATE
    return round(COUNTDOWN_PHRASE_SECONDS_AT_REFERENCE
                 * COUNTDOWN_REFERENCE_RATE / spoken_at, 1)


def start_sequence_key(race: sqlite3.Row) -> str:
    """Return a stable key for one version of a race start sequence.

    Horns and announcements are allowed to fire once per first-warning/course/
    start-plan version.  The database column race.start_time stores the first
    warning-signal time for compatibility with older databases.  If the RO
    postpones or edits that time, this key changes so server-side horns/audio
    are rescheduled against the new signal plan.

    The body of this was lost in the refactor that moved the scheduler out of
    app.py: the extraction kept the first line and dropped the rest, so it
    returned None for every race and every version of every race. Nothing broke
    loudly, because `reset_start_sequence_state_for_race` clears the in-memory
    keys on every edit and the event-log guard has a scheduled-time fallback --
    which is exactly why it survived. What it cost was the one guarantee named
    in this docstring.
    """
    start_time = str(race["start_time"] or "").strip()
    course_ref = f"course:{row_get(race, 'course_no', '')}"
    try:
        custom_course = row_get(race, "custom_course_json", None)
        if custom_course:
            course_ref = "custom:" + hashlib.sha1(
                str(custom_course).encode("utf-8", "replace")).hexdigest()[:12]
    except Exception:
        pass
    plan_ref = str(row_get(race, "start_plan_json", "") or "")
    material = f"race:{race['id']}|start:{start_time}|{course_ref}|plan:{plan_ref}"
    return hashlib.sha1(material.encode("utf-8", "replace")).hexdigest()[:16]


def start_sequence_event_metadata(race: sqlite3.Row, event: Dict[str, Any]) -> Dict[str, Any]:
    """Build log/deduplication metadata for a scheduled start-sequence event."""
    start_time = str(event.get("start_time") or race["start_time"] or "").strip()
    sec = float(event.get("sec", 0) or 0)
    scheduled_for = ""
    start_dt = parse_dt(start_time)
    if start_dt:
        scheduled_for = (start_dt - timedelta(seconds=sec)).isoformat(timespec="seconds")
    return {
        "sequence_key": start_sequence_key(race),
        "race_start_time": str(race["start_time"] or "").strip(),
        "start_time": start_time,
        "start_name": str(event.get("start_name") or "Start 1"),
        "start_classes": event.get("start_classes") or [],
        "event_sec": sec,
        "scheduled_for": scheduled_for,
        "event_kind": str(event.get("kind") or ""),
    }


def start_sequence_event_logged(race_id: int, label: str, source: str = "central-start-sequence", metadata: Optional[Dict[str, Any]] = None) -> bool:
    """Check whether this exact scheduled start-sequence event has been logged.

    Older versions only checked race+label+source, so moving the first warning
    signal could leave old warning/prep/start logs blocking rescheduled horns
    and audio.  This version deduplicates by the current start-sequence key and
    scheduled event time instead.
    """
    metadata = metadata or {}
    sequence_key = str(metadata.get("sequence_key") or "")
    scheduled_for = parse_dt(str(metadata.get("scheduled_for") or ""))
    event_sec = metadata.get("event_sec")
    try:
        event_sec_float = float(event_sec) if event_sec is not None else None
    except Exception:
        event_sec_float = None
    try:
        with get_db() as db:
            rows = db.execute(
                "SELECT event_time, details FROM race_events WHERE race_id = ? AND label = ? AND source = ? ORDER BY id DESC LIMIT 30",
                (race_id, label, source),
            ).fetchall()
        for row in rows:
            details = {}
            try:
                details = json.loads(row["details"] or "{}")
            except Exception:
                details = {}
            if sequence_key and str(details.get("sequence_key") or "") == sequence_key:
                if event_sec_float is None:
                    return True
                try:
                    if abs(float(details.get("event_sec")) - event_sec_float) < 0.01:
                        return True
                except Exception:
                    return True
            # Backwards-compatible guard for events logged before v0.49: if the
            # previous log was very close to this same scheduled boundary, treat
            # it as already fired.  If the first warning signal was changed,
            # scheduled_for moves, so the old log no longer blocks the new plan.
            if scheduled_for:
                logged_at = parse_dt(row["event_time"] or "")
                if logged_at and abs((logged_at - scheduled_for).total_seconds()) < 5.0:
                    return True
        return False
    except Exception:
        return False


def reset_start_sequence_state_for_race(race_id: int) -> None:
    """Forget in-memory scheduler/cache entries for a race after timing edits."""
    prefix = f"{race_id}:"
    with START_SEQUENCE_LOCK:
        fired_keys = START_SEQUENCE_STATE.setdefault("fired_keys", set())
        START_SEQUENCE_STATE["fired_keys"] = {k for k in fired_keys if not str(k).startswith(prefix)}
        START_SEQUENCE_STATE["last_status"] = {"ok": True, "message": "Start sequence timing was reset after a race edit."}
    removed_audio = purge_queued_start_sequence_audio(race_id)
    if removed_audio:
        with AUDIO_LOCK:
            AUDIO_RUNTIME_STATE["last_status"] = {
                "ok": True,
                "enabled": race_console_config().get("start_automation_audio_enabled"),
                "message": f"Removed {removed_audio} queued start-sequence announcement(s) after a race timing edit.",
            }


def central_start_sequence_events(race: sqlite3.Row, start_item: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Build the server-side horn/audio schedule for one configured RRS-26 start."""
    console = race_console_config()
    course = course_for_race(race)
    course_text = course_announcement_text(race, course)
    normal_rate = int(console.get("central_audio_rate", 185))
    fast_rate = int(console.get("central_audio_fast_rate", 185))
    start_item = start_item or (race_start_schedule(race)[0] if race_start_schedule(race) else {"name": "Start 1", "classes": [], "time": race["start_time"]})
    start_name = str(start_item.get("name") or "Start 1")
    start_classes = [str(c) for c in (start_item.get("classes") or [])]
    multi_start = len(race_start_schedule(race)) > 1
    label_prefix = f"{start_name} - " if multi_start else ""
    class_phrase = ""
    if start_classes and not any(c.lower() in ("all", "all classes", "*") for c in start_classes):
        class_phrase = " Classes " + ", ".join(start_classes) + "."
    # The full name + class list is only spoken on the early (>6 min) course
    # announcements.  Inside six minutes of the start, announcements are kept
    # short and clear: drop the class list (the flags convey it), keeping only
    # the start name, and only when there is more than one start to distinguish.
    full_phrase = f"{start_name}.{class_phrase} " if multi_start or class_phrase else ""
    short_phrase = f"{start_name}. " if multi_start else ""
    start_time = str(start_item.get("time") or race_first_start_time(race) or "")
    class_flags_text = class_flags_text_for_start(race, start_item)

    events: List[Dict[str, Any]] = [
        {"sec": 540, "kind": "audio", "label": f"{label_prefix}Audio: course announcement -09:00", "text": full_phrase + course_text, "rate": normal_rate},
        {"sec": 420, "kind": "audio", "label": f"{label_prefix}Audio: course announcement -07:00", "text": full_phrase + course_text, "rate": normal_rate},
        {"sec": 315, "kind": "audio", "label": f"{label_prefix}Audio: stand by to warning signal", "text": f"{short_phrase}Stand by 15 seconds to warning signal.", "rate": normal_rate},
        {"sec": 300, "kind": "horn", "label": f"{label_prefix}Warning signal / {class_flags_text}"},
        {"sec": 300, "kind": "audio", "label": f"{label_prefix}Audio: five minute warning signal", "text": f"{short_phrase}Five minutes. Warning signal. Raise {class_flags_text}.", "rate": normal_rate},
        {"sec": 255, "kind": "audio", "label": f"{label_prefix}Audio: stand by to preparatory signal", "text": f"{short_phrase}Stand by 15 seconds to preparatory signal.", "rate": normal_rate},
        {"sec": 240, "kind": "horn", "label": f"{label_prefix}Preparatory signal"},
        {"sec": 240, "kind": "audio", "label": f"{label_prefix}Audio: four minute preparatory signal", "text": f"{short_phrase}Four minutes. Preparatory signal.", "rate": normal_rate},
        {"sec": 75, "kind": "audio", "label": f"{label_prefix}Audio: stand by to one minute signal", "text": f"{short_phrase}Stand by 15 seconds to one minute signal.", "rate": normal_rate},
        {"sec": 60, "kind": "horn", "label": f"{label_prefix}One minute signal"},
        {"sec": 60, "kind": "audio", "label": f"{label_prefix}Audio: one minute", "text": f"{short_phrase}One minute.", "rate": normal_rate},
        {"sec": 30, "kind": "audio", "label": f"{label_prefix}Audio: thirty seconds", "text": f"{short_phrase}Thirty seconds.", "rate": normal_rate},
        {"sec": 20, "kind": "audio", "label": f"{label_prefix}Audio: twenty seconds", "text": f"{short_phrase}Twenty seconds.", "rate": normal_rate},
        # Speak the final ten seconds as a single utterance rather than ten
        # separate one-word calls.  Each spoken item re-initialises the TTS
        # engine, so ten back-to-back words drifted late and short words were
        # clipped; one phrase is spoken cleanly.  Periods give ~1s spacing so it
        # tracks the countdown.  The start horn below still fires on the clock.
        #
        # At the **countdown** rate, not the normal one. This is the announcement
        # the setting called "Countdown speech rate" exists for, and it was the
        # only one not using it: slowing the normal rate down to make the course
        # announcements followable stretched the ten-count with it, and "One"
        # landed after the gun. How early to begin therefore has to follow the
        # rate too -- see `countdown_lead_seconds`.
        {"sec": countdown_lead_seconds(fast_rate), "kind": "audio", "label": f"{label_prefix}Audio: countdown 10 to 1", "text": "Ten. Nine. Eight. Seven. Six. Five. Four. Three. Two. One.", "rate": fast_rate},
        {"sec": 0, "kind": "horn", "label": f"{label_prefix}Start signal"},
        {"sec": 0, "kind": "audio", "label": f"{label_prefix}Audio: start", "text": f"{short_phrase}Start.", "rate": fast_rate},
    ]
    if console.get("central_audio_vox_tone_enabled"):
        lead = int(console.get("central_audio_vox_lead_seconds", 2))
        audio_secs = sorted({float(ev["sec"]) for ev in events if ev["kind"] == "audio"}, reverse=True)
        for s in audio_secs:
            events.append({"sec": s + lead, "kind": "tone", "label": f"{label_prefix}VOX tone before -{int(s)}s audio"})
    for ev in events:
        ev["start_name"] = start_name
        ev["start_classes"] = start_classes
        ev["start_time"] = start_time
    return sorted(events, key=lambda ev: ev["sec"], reverse=True)

def central_horn_already_sounded_for_time(race_id: int, metadata: Dict[str, Any]) -> bool:
    """Return True if a central horn has already sounded for this exact signal time."""
    sequence_key = str(metadata.get("sequence_key") or "")
    scheduled_for = str(metadata.get("scheduled_for") or "")
    if not sequence_key or not scheduled_for:
        return False
    try:
        with get_db() as db:
            rows = db.execute(
                "SELECT details FROM race_events WHERE race_id = ? AND event_type = 'horn' AND source = 'central-start-sequence' ORDER BY id DESC LIMIT 50",
                (race_id,),
            ).fetchall()
        for row in rows:
            try:
                details = json.loads(row["details"] or "{}")
            except Exception:
                details = {}
            if str(details.get("sequence_key") or "") == sequence_key and str(details.get("scheduled_for") or "") == scheduled_for and not details.get("coalesced_without_sound"):
                return True
    except Exception:
        return False
    return False


def run_due_start_sequence_event(race: sqlite3.Row, event: Dict[str, Any]) -> None:
    """Fire or speak one due central start-sequence event."""
    race_id = int(race["id"])
    label = str(event.get("label") or "Start sequence event")
    metadata = start_sequence_event_metadata(race, event)
    key = f"{race_id}:{metadata.get('sequence_key')}:{event.get('kind')}:{event.get('sec')}:{label}"
    with START_SEQUENCE_LOCK:
        fired_keys = START_SEQUENCE_STATE.setdefault("fired_keys", set())
        if key in fired_keys:
            return
        fired_keys.add(key)
    if start_sequence_event_logged(race_id, label, metadata=metadata):
        return
    kind = event.get("kind")
    if kind == "horn":
        if not race_console_config().get("start_automation_horn_enabled"):
            return
        details = dict(metadata)
        if central_horn_already_sounded_for_time(race_id, metadata):
            details.update({"ok": True, "message": "Horn signal combined with another start signal at the same time.", "coalesced_without_sound": True})
        else:
            result = fire_horn(hardware_config()["horn_duration_ms"])
            details.update(result)
        log_event(race_id, "horn", label, "central-start-sequence", details)
    elif kind == "audio":
        if not race_console_config().get("start_automation_audio_enabled"):
            return
        details = dict(metadata)
        details.update({"text": event.get("text"), "rate": event.get("rate")})
        queue_central_audio(
            str(event.get("text") or ""),
            int(event.get("rate") or race_console_config().get("central_audio_rate", 185)),
            label,
            race_id,
            source="central-start-sequence",
            details=details,
        )
        if event.get("log_event", True):
            log_event(race_id, "audio", str(event.get("log_label") or label), "central-start-sequence", details)
    elif kind == "tone":
        # VOX wake-up tone before an announcement. Not logged (it would double the
        # audio rows); the in-memory fired_keys guard prevents re-firing.
        if race_console_config().get("start_automation_audio_enabled"):
            queue_central_tone(label, race_id, source="central-start-sequence", details=dict(metadata))


def run_pursuit_sequence(race: sqlite3.Row, now: datetime) -> None:
    """Fire a pursuit race's signals: the first-start warning sequence, one start
    horn at each subsequent boat start time, and the single finish horn."""
    # Imported lazily to avoid an import cycle (pursuit -> series -> ...).
    from core.pursuit import pursuit_finish_dt, pursuit_next_start_announcements, pursuit_start_signal_times
    from core.races import get_entries, race_first_start_dt

    race_id = int(race["id"])
    first_start = race_first_start_dt(race)
    if not first_start:
        return
    entries = get_entries(race_id)
    signal_times = pursuit_start_signal_times(race, entries)
    finish = pursuit_finish_dt(race)

    if video_config().get("video_enabled"):
        for idx, start_dt in enumerate(signal_times, start=1):
            schedule_video_clip(race_id, "start", start_dt.isoformat(timespec="seconds"), label=f"Start {idx} video")

    # First start: a full RRS-26 warning sequence anchored at the first start.
    first_diff = (first_start - now).total_seconds()
    if -30 <= first_diff <= 600:
        for ev in central_start_sequence_events(race, {"name": "First start", "classes": ["1"], "time": first_start.isoformat(timespec="seconds")}):
            sec = float(ev.get("sec", 0))
            if sec - 2.5 < first_diff <= sec:
                run_due_start_sequence_event(race, ev)

    # Every subsequent boat start time gets a single start horn.
    for start_dt in signal_times:
        if start_dt == first_start:
            continue
        diff = (start_dt - now).total_seconds()
        if -2.5 < diff <= 0:
            label = f"Pursuit start {start_dt.strftime('%H:%M:%S')}"
            run_due_start_sequence_event(race, {
                "sec": 0, "kind": "horn", "label": label, "start_name": label,
                "start_classes": [], "start_time": start_dt.isoformat(timespec="seconds"),
            })

    # The single finish signal.
    if finish:
        diff = (finish - now).total_seconds()
        if -2.5 < diff <= 0:
            run_due_start_sequence_event(race, {
                "sec": 0, "kind": "horn", "label": "Pursuit finish signal", "start_name": "Pursuit finish",
                "start_classes": [], "start_time": finish.isoformat(timespec="seconds"),
            })

    # Spoken "next to start" announcements naming the upcoming boat(s).
    console = race_console_config()
    normal_rate = int(console.get("central_audio_rate", 185))
    vox_tone = bool(console.get("central_audio_vox_tone_enabled"))
    vox_lead = int(console.get("central_audio_vox_lead_seconds", 2))
    for ann in pursuit_next_start_announcements(race, entries):
        if vox_tone:
            tone_dt = ann["dt"] - timedelta(seconds=vox_lead)
            tone_diff = (tone_dt - now).total_seconds()
            if -2.5 < tone_diff <= 0:
                run_due_start_sequence_event(race, {
                    "sec": 0, "kind": "tone", "label": f"VOX tone before {ann['label']}",
                    "start_name": ann["label"], "start_classes": [], "start_time": tone_dt.isoformat(timespec="seconds"),
                })
        diff = (ann["dt"] - now).total_seconds()
        if -2.5 < diff <= 0:
            run_due_start_sequence_event(race, {
                "sec": 0, "kind": "audio", "label": ann["label"], "text": ann["text"], "rate": normal_rate,
                "start_name": ann["label"], "start_classes": [], "start_time": ann["dt"].isoformat(timespec="seconds"),
            })


def end_due_postponement(race: sqlite3.Row) -> bool:
    """Lower AP at the minute the race officer chose: one sound, and clear it.

    The single sound lives here rather than in `core.raceadmin.resume_race`
    because by then the flag is still up -- the race officer has said *when* it
    comes down, not lowered it. Making the sound at that moment is the same job
    as every other timed signal on this loop, and this is the module that does
    them.

    Clearing the columns is what makes it happen once: a tick that finds nothing
    postponed does nothing. Gated on the same start-automation setting as the
    other signals, so a club that sounds its own horns still sounds this one.
    """
    race_id = int(race["id"])
    ends_at = str(row_get(race, "postponement_ends_at", "") or "")
    warning = str(row_get(race, "start_time", "") or "")
    with get_db() as db:
        changed = db.execute(
            "UPDATE races SET postponed_at = NULL, postponement_kind = NULL,"
            " postponement_ends_at = NULL WHERE id = ? AND postponed_at IS NOT NULL",
            (race_id,)).rowcount
        db.commit()
    if not changed:
        return False
    log_event(race_id, "postpone", "AP down, one sound", "central-start-sequence",
              {"ends_at": ends_at, "start_time": warning})
    try:
        # Unconditional, like the two sounds that put AP up and like a shortened
        # course. This is a signal the race officer asked for and named the minute
        # of, not something the app decided to do on its own -- and AP going up
        # with two sounds and coming down in silence is the worst of both.
        fire_horn(hardware_config()["horn_duration_ms"])
    except Exception:
        # A horn that will not fire must not leave the race postponed in the
        # database while the flag is down on the mast.
        pass
    warning_dt = parse_dt(warning)
    gun = (warning_dt + timedelta(minutes=5)).strftime("%H:%M") if warning_dt else ""
    spoken = "Answering pennant down. That sound was AP coming down."
    if warning_dt:
        spoken += (f" Warning signal at {warning_dt.strftime('%H:%M')}"
                   + (f", first gun {gun}." if gun else "."))
    queue_central_audio(spoken, label="AP down", race_id=race_id, source="postpone")
    return True


def announce_postponement_ending_soon(race: sqlite3.Row, now: datetime) -> bool:
    """A minute before AP comes down, say so. Returns True if it announced.

    So the fleet is watching the mast when the flag moves, rather than being told
    by a single horn they may or may not have heard. This is why the race officer
    cannot choose a moment less than `LOWER_AP_MIN_LEAD_S` away: there has to be
    room for this to be spoken first.

    Deduplicated on the moment itself, so changing the time re-arms it.
    """
    ends_at = str(row_get(race, "postponement_ends_at", "") or "")
    ends = parse_dt(ends_at)
    if not ends:
        return False
    remaining = (ends - now).total_seconds()
    if remaining <= 0 or remaining > AP_DOWN_WARNING_LEAD_S:
        return False
    key = f"{int(race['id'])}:ap-down-warning:{ends_at}"
    with START_SEQUENCE_LOCK:
        fired_keys = START_SEQUENCE_STATE.setdefault("fired_keys", set())
        if key in fired_keys:
            return False
        fired_keys.add(key)
    queue_central_audio(
        f"Answering pennant will be lowered in one minute, at {ends.strftime('%H:%M')}.",
        label="AP coming down", race_id=int(race["id"]), source="postpone")
    log_event(int(race["id"]), "postpone", "AP coming down in one minute",
              "central-start-sequence", {"ends_at": ends_at})
    return True


def start_sequence_scheduler_loop() -> None:
    """Server-side scheduler for start horns and VHF/audio announcements."""
    while True:
        try:
            now = datetime.now()
            init_db()
            with get_db() as db:
                races = db.execute("SELECT * FROM races WHERE start_time IS NOT NULL AND TRIM(start_time) != '' ORDER BY start_time DESC LIMIT 50").fetchall()
            update_camera_preset_for_races(races, now)
            checked = 0
            for race in races:
                if race_has_finished_for_sequence(int(race["id"])):
                    continue
                if postponement_is_due_to_end(race, now):
                    # The minute the race officer chose for AP to come down. One
                    # sound, and the ordinary sequence takes over from the
                    # warning signal a minute later.
                    end_due_postponement(race)
                elif race_is_postponed(race, now):
                    # AP is flying: no warning, preparatory or starting signal
                    # sounds until it comes down. That is the difference between
                    # postponing a race and quietly re-planning it -- the race
                    # keeps its scheduled time, and `resume_race` sets the new
                    # warning to one minute after the flag is lowered. The one
                    # thing that does happen under AP is the announcement that it
                    # is about to move.
                    announce_postponement_ending_soon(race, now)
                    continue
                if str(row_get(race, "race_type", "") or "").lower() == "pursuit":
                    run_pursuit_sequence(race, now)
                    checked += 1
                    continue
                ensure_start_video_scheduled(race)
                for start_item in race_start_schedule(race):
                    start_dt = start_item.get("dt")
                    if not start_dt:
                        continue
                    diff = (start_dt - now).total_seconds()
                    if diff > 600 or diff < -30:
                        continue
                    checked += 1
                    for ev in central_start_sequence_events(race, start_item):
                        sec = float(ev.get("sec", 0))
                        # Fire shortly after the scheduled boundary.  Events that are
                        # more than a few seconds late are skipped to avoid confusing
                        # delayed signals after a pause/restart.
                        if diff <= sec and diff > sec - 2.5:
                            run_due_start_sequence_event(race, ev)
            with START_SEQUENCE_LOCK:
                START_SEQUENCE_STATE["last_status"] = {"ok": True, "message": f"Central start scheduler running; {checked} start(s) in sequence window."}
        except Exception as exc:
            with START_SEQUENCE_LOCK:
                START_SEQUENCE_STATE["last_status"] = {"ok": False, "message": f"Central start scheduler error: {exc}"}
        time.sleep(0.25)


def start_start_sequence_scheduler() -> None:
    """Start the central start-sequence scheduler thread if needed."""
    with START_SEQUENCE_LOCK:
        if START_SEQUENCE_STATE.get("started"):
            return
        START_SEQUENCE_STATE["started"] = True
        START_SEQUENCE_STATE["last_status"] = {"ok": True, "message": "Central start scheduler is running."}
    thread = threading.Thread(target=start_sequence_scheduler_loop, name="central-start-sequence-scheduler", daemon=True)
    thread.start()
