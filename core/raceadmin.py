"""Creating a race sheet, and saving its Course & start settings.

These two operations lived inside the route handlers, reading ``request.form``
and answering with ``flash`` and ``redirect``, so nothing but a browser POST
could perform them. That was fine while the GUI was the only way in. It stops
being fine the moment a second caller wants the same operation -- a natural
language command from the water, a script, a test -- because the only options
are to fake a request or to write the rules out a second time. A second copy of
"create a race" is a copy that drifts, and the drift is silent until two paths
disagree about something like which time a race stores.

So the rules live here, callable, and the route parses the form and calls in.
The form parsing stays in the route because that genuinely is browser-shaped;
what a race *is* does not belong to HTTP.

Two things this module deliberately keeps hold of, rather than leaving to its
callers:

* **The post-save work.** Saving a start time without resynchronising the start
  sequence leaves the horn scheduled for the old one, and a caller that forgets
  gets a race that looks right and sounds wrong. `update_race_settings` does it.
* **The activity-log entry**, with the actor passed in rather than read from a
  Flask session, so an automated caller is recorded as itself.

Validation raises `RaceValidationError`, which carries the message the race
officer should read. Callers turn it into a flash, a JSON error, or a question
back to whoever is typing.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from core import appstate
from core.activitylog import log_activity
from core.audio import queue_central_audio
from core.boats import get_boat
from core.courses import course_for_race, course_shorten_options, custom_course_from_race
from core.db import row_get
from core.entrysync import (
    add_active_boats_to_race,
    add_active_boats_to_series_races,
    add_boat_database_entry_to_race,
    add_boat_database_entry_to_series_races,
    sync_race_entries_to_series,
    sync_series_entries_into_race,
)
from core.eventlog import log_event
from core.horn import fire_horn, hardware_config
from core.polar_io import list_polar_files, race_saved_polar
from core.pursuit import assign_pursuit_start_times, is_pursuit_race, recompute_pursuit_start_times
from core.races import (
    postponement_flag,
    postponement_kind_spec,
    race_first_start_dt,
    race_first_start_time,
    race_is_postponed,
)
from core.racesignals import (
    LOWER_AP_MIN_LEAD_S,
    POSTPONEMENT_KINDS,
    RESUME_WARNING_DELAY_S,
    normalise_postponement_kind,
)
from core.startsequence import reset_start_sequence_state_for_race
from core.timeutils import (
    dt_display,
    next_whole_minute,
    normalise_start_time_value,
    parse_dt,
)
from core.track import finish_lines
from core.video import cancel_pending_start_clips, ensure_start_video_scheduled

# A race with no name is still a race; the club sails a lot of them.
# The gun follows the first warning signal by five minutes (RRS 26).
WARNING_TO_GUN = timedelta(minutes=5)
DEFAULT_RACE_NAME = "Club Race"
# A pursuit with no period cannot compute a single start time, so rather than
# refuse the race we fall back to the length the club usually sails.
DEFAULT_PURSUIT_DURATION_MIN = 90.0
# IRC and YTC are always scored side by side; rating_rule is kept only so older
# databases still load.
DUAL = "DUAL"


class RaceValidationError(ValueError):
    """A race the rules will not accept, with the reason to show the user.

    `field` names the input at fault where there is one, so a caller that can
    ask a follow-up question knows what to ask about.
    """

    def __init__(self, message: str, field: Optional[str] = None) -> None:
        super().__init__(message)
        self.message = message
        self.field = field


@dataclass
class RaceSpec:
    """What to create. Everything optional: a race sheet starts nearly empty,
    and the course and warning signal are set afterwards on the race sheet."""

    name: str = ""
    series_id: Optional[int] = None
    class_name: str = ""
    notes: str = ""
    race_type: str = "standard"          # "standard" or "pursuit"
    pursuit_rating: str = "IRC"          # "IRC" or "YTC"; pursuit races only
    pursuit_duration_min: Optional[float] = None
    add_all_active: bool = False
    # Watch the fleet across the finish line. Detection is on by default: a
    # finish caught is worth more than one the RO was going to time anyway, and
    # a missed detection cannot be recovered after the race.
    gps_finish_enabled: bool = True
    # Recording those finishes without anyone approving them is not, and is off
    # by default. Detection proposes; a person disposes. An ordinary club race
    # has a race officer in the hut, and a detection they can see and dismiss
    # costs one click, where a wrong finish written straight into the results of
    # a race being sailed has to be found before it is noticed. The unmanned
    # case turns it on deliberately -- the tick box on the Entries tab, or
    # `gps_auto_confirm=True` from the on-the-water page, which has nobody in
    # the hut to press anything.
    gps_auto_confirm: bool = False


@dataclass
class RaceCreated:
    race_id: int
    entries_added: int
    race_type: str


@dataclass
class RaceSettings:
    """The Course & start tab of a standard race.

    `start_time` is the **first warning signal**, not the gun: the first start
    is five minutes later. It is the single most misread field in the app, so
    the name matches the column and anything that shows it to a person should
    show both times.

    `start_plan` of None means follow the series default. A dict is a parsed
    plan, `{"starts": [...], "errors": [...]}`; the errors come back as
    warnings rather than refusals, matching what the race sheet does.
    """

    name: Optional[str] = None           # None keeps the current name
    # None keeps the current fleet label, the same convention as `name` above.
    # The Course & start tab stopped offering the field in v0.276 -- a race's
    # class comes from the series rating bands -- and a plain "" default would
    # have meant every save of that tab silently wiped the label off the older
    # races that still carry one.
    class_name: Optional[str] = None
    start_time: str = ""                 # first warning signal; "" = not set yet
    series_id: Optional[int] = None
    notes: str = ""
    polar_file: str = ""                 # "" keeps whatever the race has
    finish_line_key: str = ""            # "" = the club line
    course_no: Optional[int] = None
    start_plan: Optional[Dict[str, Any]] = None
    # Whether this save is somebody choosing the course, or just needs a course
    # number to write. Saving the Course & start tab is choosing; setting only a
    # start time is not, and a caller that had to pass the race's existing
    # fallback number to change the time was marking a course as chosen that
    # nobody had looked at -- the exact state `course_set` exists to tell apart.
    # It is never unset here: a course already chosen stays chosen.
    choosing_course: bool = True
    # Pursuit races only, and both keep the current value when left unset: a
    # caller changing the start time must not have to restate the rating system.
    pursuit_rating: str = ""             # "IRC" or "YTC"
    pursuit_duration_min: Optional[float] = None


@dataclass
class RaceUpdated:
    race_id: int
    warnings: List[str] = field(default_factory=list)


@dataclass
class PursuitUpdated:
    """What saving a pursuit did, including whether the ladder could be built."""

    race_id: int
    assigned: int = 0
    rating_type: str = ""
    ready: bool = False
    warnings: List[str] = field(default_factory=list)


def set_custom_course(db: sqlite3.Connection, race: sqlite3.Row, sequence: List[Dict[str, str]],
                      actor: str = "system", polar_file: str = "") -> int:
    """Save a made-up course on a race: the marks, in order, and which hand each
    is left on.

    Lived in the course-builder route, reading `request.form` and answering with
    a redirect, so the only way to make up a course was to drag marks around a
    page 1100px wide. It is the same save either way, and marking `course_set`
    is part of it: a made-up course is as chosen as a numbered one.
    """
    race_id = int(race["id"])
    if not sequence:
        raise RaceValidationError("A made-up course needs at least one mark.", field="marks")
    payload = {"marks": sequence, "updated_at": datetime.now().isoformat(timespec="seconds")}
    db.execute("UPDATE races SET custom_course_json = ?, polar_file = COALESCE(NULLIF(?, ''),"
               " polar_file), course_set = 1 WHERE id = ?",
               (json.dumps(payload), (polar_file or "").strip(), race_id))
    db.commit()
    log_activity("manual course saved", f"#{race_id} · {len(sequence)} marks", user=actor)
    return len(sequence)


def clear_custom_course(db: sqlite3.Connection, race: sqlite3.Row, actor: str = "system") -> None:
    """Drop a made-up course, returning the race to its fixed course number."""
    race_id = int(race["id"])
    db.execute("UPDATE races SET custom_course_json = NULL WHERE id = ?", (race_id,))
    db.commit()
    log_activity("manual course cleared", f"#{race_id}", user=actor)


def _course_set_after(race: sqlite3.Row, settings: RaceSettings) -> int:
    """Whether the race's course counts as chosen once this save has happened.

    Only ever upwards. Choosing marks it; a save that merely needed a course
    number to write leaves it exactly as it was, so a race whose course nobody
    has looked at still reads that way -- which is what keeps the VHF course
    announcement silent until somebody has.
    """
    if settings.choosing_course:
        return 1
    try:
        return 1 if race["course_set"] else 0
    except (IndexError, KeyError):
        return 0


def _require_series(db: sqlite3.Connection, series_id: Optional[int]) -> Optional[sqlite3.Row]:
    """The series row, or a refusal. A missing series would otherwise attach the
    race to nothing and quietly leave it out of the standings."""
    if not series_id:
        return None
    row = db.execute("SELECT * FROM race_series WHERE id = ?", (series_id,)).fetchone()
    if not row:
        raise RaceValidationError("Selected series was not found.", field="series_id")
    return row


def create_race(db: sqlite3.Connection, spec: RaceSpec, actor: str = "system") -> RaceCreated:
    """Create a race sheet and add whatever entries the spec asks for.

    The course and first warning signal are deliberately not set here: a race is
    created before either is known, and the race sheet is where they are chosen.
    A harmless fixed course number is stored as the fallback until then.
    """
    name = (spec.name or "").strip() or DEFAULT_RACE_NAME
    race_type = "pursuit" if spec.race_type == "pursuit" else "standard"
    notes = (spec.notes or "").strip()

    if race_type == "pursuit":
        # A pursuit has no classes: every boat starts on its own rating.
        class_name = ""
        rating_rule = "YTC" if spec.pursuit_rating == "YTC" else "IRC_TCC"
        duration = spec.pursuit_duration_min
        if not duration or duration <= 0:
            duration = DEFAULT_PURSUIT_DURATION_MIN
        pursuit_duration_min: Optional[float] = float(duration)
    else:
        class_name = (spec.class_name or "").strip()
        rating_rule = DUAL
        pursuit_duration_min = None

    course_no = int(appstate.COURSES[0]["course_no"]) if appstate.COURSES else 1
    start_time = ""

    _require_series(db, spec.series_id)
    cur = db.execute(
        "INSERT INTO races (name, class_name, series_id, course_no, start_time, rating_rule,"
        " race_type, pursuit_duration_min, notes, created_at, gps_finish_enabled,"
        " gps_auto_confirm)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (name, class_name, spec.series_id, course_no, start_time, rating_rule, race_type,
         pursuit_duration_min, notes, datetime.now().isoformat(timespec="seconds"),
         1 if spec.gps_finish_enabled else 0, 1 if spec.gps_auto_confirm else 0),
    )
    race_id = int(cur.lastrowid)

    # Entries always snapshot both IRC and YTC, so a pursuit can change its
    # rating system later without every boat being re-added.
    added = 0
    if spec.series_id:
        added += sync_series_entries_into_race(db, race_id, spec.series_id)
    if spec.add_all_active:
        if spec.series_id:
            added += add_active_boats_to_series_races(db, spec.series_id, race_id, class_name,
                                                      DUAL, rating_source="AUTO")
        else:
            added += add_active_boats_to_race(db, race_id, class_name, DUAL, rating_source="AUTO")
    if race_type == "pursuit":
        race_row = db.execute("SELECT * FROM races WHERE id = ?", (race_id,)).fetchone()
        assign_pursuit_start_times(db, race_row)
    db.commit()

    log_activity("race created", f"#{race_id} '{name}' type={race_type}", user=actor)
    return RaceCreated(race_id=race_id, entries_added=added, race_type=race_type)


def update_race_settings(db: sqlite3.Connection, race: sqlite3.Row, settings: RaceSettings,
                         actor: str = "system") -> RaceUpdated:
    """Save a standard race's Course & start settings.

    Pursuit races have their own update path; this refuses one rather than
    writing standard-race fields over it.
    """
    race_id = int(race["id"])
    if str(race["race_type"] or "standard") == "pursuit":
        raise RaceValidationError("This is a pursuit race; use the pursuit update instead.",
                                  field="race_type")

    name = (settings.name or "").strip() or race["name"]
    class_name = (race["class_name"] or "") if settings.class_name is None         else (settings.class_name or "").strip()
    notes = (settings.notes or "").strip()
    polar_file = (settings.polar_file or "").strip() or race_saved_polar(race, list_polar_files())

    # An unknown finish line falls back to the club line rather than leaving the
    # race with none at all, which would mean no GPS finish could ever be seen.
    finish_line_key = (settings.finish_line_key or "").strip()
    if finish_line_key not in {str(fl.get("key") or "") for fl in finish_lines()}:
        finish_line_key = ""

    # A made-up course keeps its stored fixed course number purely as the
    # fallback for when the manual course is cleared.
    is_custom = bool(custom_course_from_race(race))
    if is_custom:
        course_no = int(race["course_no"])
    else:
        course_no = settings.course_no
        if course_no not in appstate.COURSE_BY_NO:
            raise RaceValidationError("Please select a valid course number.", field="course_no")

    # Blank and unusable are different answers and must not share a code path.
    # normalise_start_time_value returns "" for anything it cannot parse -- a
    # bare "10:55" included -- so the route's old guard, which normalised first
    # and then asked whether the result parsed, could never fire: a time the app
    # did not understand was silently stored as "no time set" and reported as
    # saved. Harmless from a datetime-local input, which sends "" or a full ISO
    # value; not harmless from anything typed.
    raw_start_time = (settings.start_time or "").strip()
    start_time = normalise_start_time_value(raw_start_time)
    if raw_start_time and not start_time:
        raise RaceValidationError(
            "Could not read that as a first warning-signal time. Give a date and time "
            "(for example 2026-08-12T10:55), or leave it blank until the sequence time "
            "is known.", field="start_time")

    _require_series(db, settings.series_id)
    series_id = settings.series_id or None

    warnings: List[str] = []
    start_plan_json: Optional[str] = None
    if settings.start_plan is not None:
        start_plan_json = json.dumps({"starts": settings.start_plan.get("starts", [])})
        warnings = list(settings.start_plan.get("errors", []) or [])

    course_set = _course_set_after(race, settings)
    if is_custom:
        db.execute(
            "UPDATE races SET name = ?, class_name = ?, series_id = ?, start_time = ?,"
            " rating_rule = ?, notes = ?, polar_file = ?, start_plan_json = ?, finish_line_key = ?,"
            " course_set = ? WHERE id = ?",
            (name, class_name, series_id, start_time, DUAL, notes, polar_file,
             start_plan_json, finish_line_key, course_set, race_id),
        )
    else:
        db.execute(
            "UPDATE races SET name = ?, class_name = ?, series_id = ?, course_no = ?,"
            " start_time = ?, rating_rule = ?, notes = ?, polar_file = ?, start_plan_json = ?,"
            " finish_line_key = ?, custom_course_json = NULL, course_set = ? WHERE id = ?",
            (name, class_name, series_id, course_no, start_time, DUAL, notes, polar_file,
             start_plan_json, finish_line_key, course_set, race_id),
        )
    if series_id:
        sync_series_entries_into_race(db, race_id, series_id)
        sync_race_entries_to_series(db, race_id, series_id)
    db.commit()

    # The horn is scheduled from the stored warning signal, so a saved time that
    # does not reach the scheduler is a race that sounds at the old one. This is
    # part of saving, not something a caller may forget to do afterwards.
    updated = db.execute("SELECT * FROM races WHERE id = ?", (race_id,)).fetchone()
    if updated is not None:
        reset_start_sequence_state_for_race(race_id)
        ensure_start_video_scheduled(updated)

    log_activity("race updated", f"#{race_id} '{name}'", user=actor)
    return RaceUpdated(race_id=race_id, warnings=warnings)


def update_pursuit_settings(db: sqlite3.Connection, race: sqlite3.Row, settings: RaceSettings,
                            actor: str = "system") -> PursuitUpdated:
    """Save a pursuit race's Course & start settings and rebuild the ladder.

    The other half of `update_race_settings`, which refuses a pursuit. This lived
    only in the race sheet's form handler, so a pursuit created by any other
    route -- the assistant, on the water -- was made and then could not be given
    a start time at all: it came back "use the pursuit update instead", after the
    race had already been created.

    Two things the form handler did not do, both of which follow from saving a
    time rather than from being a form:

    * `course_set` is marked. Choosing a course on the Course & start tab is the
      whole of what choosing a course means, and a pursuit that had been through
      the tab still read as "Course not set" -- which keeps the VHF announcement
      silent.
    * the start-sequence state is reset and the start video rescheduled, exactly
      as for a standard race. A pursuit fires a real horn sequence off the stored
      warning signal, so an edited time that never reaches the scheduler is a
      race that sounds at the old one.
    """
    race_id = int(race["id"])
    if str(race["race_type"] or "standard") != "pursuit":
        raise RaceValidationError("This is not a pursuit race; use the standard update.",
                                  field="race_type")

    name = (settings.name or "").strip() or race["name"]
    notes = (settings.notes or "").strip()

    rating = (settings.pursuit_rating or "").strip().upper()
    if rating in ("YTC", "IRC", "IRC_TCC"):
        rating_rule = "YTC" if rating == "YTC" else "IRC_TCC"
    else:
        rating_rule = str(race["rating_rule"] or "IRC_TCC")

    duration = settings.pursuit_duration_min
    if not duration or float(duration) <= 0:
        duration = race["pursuit_duration_min"]

    course_no = settings.course_no
    if course_no not in appstate.COURSE_BY_NO:
        course_no = int(race["course_no"])

    # Blank and unusable are different answers, as on the standard path: what is
    # stored is the first warning signal, and a time nobody can read must not be
    # saved as "no time set" and reported as saved.
    raw_start_time = (settings.start_time or "").strip()
    start_time = normalise_start_time_value(raw_start_time)
    if raw_start_time and not start_time:
        raise RaceValidationError(
            "Could not read that as a first warning-signal time. Give a date and time "
            "(for example 2026-08-12T10:55), or leave it blank until the sequence time "
            "is known.", field="start_time")

    _require_series(db, settings.series_id)
    series_id = settings.series_id or None

    db.execute(
        "UPDATE races SET name = ?, class_name = '', series_id = ?, course_no = ?, start_time = ?,"
        " rating_rule = ?, pursuit_duration_min = ?, notes = ?, course_set = ? WHERE id = ?",
        (name, series_id, course_no, start_time, rating_rule, duration, notes,
         _course_set_after(race, settings), race_id),
    )
    updated = db.execute("SELECT * FROM races WHERE id = ?", (race_id,)).fetchone()
    result = assign_pursuit_start_times(db, updated)
    db.commit()

    if updated is not None:
        reset_start_sequence_state_for_race(race_id)
        ensure_start_video_scheduled(updated)

    log_activity("race updated", f"#{race_id} '{name}' (pursuit)", user=actor)
    return PursuitUpdated(race_id=race_id,
                          assigned=int(result.get("assigned") or 0),
                          rating_type=str(result.get("rating_type") or ""),
                          ready=bool(result.get("ready")),
                          warnings=list(result.get("warnings") or []))


# ---------------------------------------------------------------------------
# Entries
# ---------------------------------------------------------------------------

@dataclass
class EntryScope:
    """Which boats to add: every active one, a named boat, or another race's.

    There was a fleet scope, selecting active boats whose own free-text class
    matched. Both ends of that comparison have now retired -- the race's fleet
    label in v0.271 and the boat's in v0.283 -- so it could only ever have
    matched rows typed before then. A fleet worth adding is a series rating
    band, which is a different thing and not derived from typed-in text.

    A race in a series adds across every race in that series, because a
    competitor who joins the series is in all of it -- that is the app's rule,
    not a convenience, and it is why the count returned can exceed the fleet.
    """

    kind: str = "all_active"             # "all_active" | "boat" | "same_as"
    boat_id: Optional[int] = None        # kind="boat"
    # kind="same_as": the race whose boats to enter. "The same boats as last
    # week" is how a race officer describes a fleet, and it is not the same as
    # every active boat -- half the club does not sail on a Wednesday evening.
    same_as_race_id: Optional[int] = None
    class_name: str = ""                 # per-entry class override, kind="boat"
    rating_source: str = "AUTO"


@dataclass
class EntriesAdded:
    added: int
    kind: str
    across_series: bool = False
    boat_name: str = ""


def add_entries(db: sqlite3.Connection, race: sqlite3.Row, scope: EntryScope,
                actor: str = "system") -> EntriesAdded:
    """Add boats to a race, and to the rest of its series where it has one."""
    race_id = int(race["id"])
    series_id = race["series_id"] if "series_id" in race.keys() else None
    across = bool(series_id)
    race_class = race["class_name"] or ""
    rating_rule = race["rating_rule"]
    source = (scope.rating_source or "AUTO").strip() or "AUTO"

    if scope.kind == "same_as":
        # The boats that sailed another race, entered by boat rather than copied
        # as rows: each goes through the ordinary path, so ratings are snapshot
        # the same way and a boat with no database record is simply skipped
        # rather than half-entered. A boat already in this race is left alone.
        source_id = int(scope.same_as_race_id or 0)
        source_race = db.execute("SELECT * FROM races WHERE id = ?", (source_id,)).fetchone()
        if source_race is None:
            raise RaceValidationError(f"There is no race #{source_id} to copy the boats from.",
                                      field="same_as_race_id")
        race_row = db.execute("SELECT * FROM races WHERE id = ?", (race_id,)).fetchone()
        added = 0
        for entry in db.execute("SELECT * FROM entries WHERE race_id = ? ORDER BY id",
                                (source_id,)).fetchall():
            boat = get_boat(int(entry["boat_id"])) if entry["boat_id"] is not None else None
            if boat is None:
                continue
            if series_id:
                added += add_boat_database_entry_to_series_races(
                    db, int(series_id), boat, "", source, source_race_id=race_id)
            elif add_boat_database_entry_to_race(db, race_row, boat, "", source):
                added += 1
        detail = f"race #{race_id} · same boats as #{source_id} · {added} added"
        result = EntriesAdded(added=added, kind="same_as", across_series=across)
        action = "boats copied from another race"
    elif scope.kind == "boat":
        boat = get_boat(scope.boat_id or 0)
        if not boat:
            raise RaceValidationError("Please select a boat from the database.", field="boat_id")
        override = (scope.class_name or "").strip()
        if series_id:
            added = add_boat_database_entry_to_series_races(db, int(series_id), boat, override,
                                                            source, source_race_id=race_id)
        else:
            race_row = db.execute("SELECT * FROM races WHERE id = ?", (race_id,)).fetchone()
            added = 1 if add_boat_database_entry_to_race(db, race_row, boat, override, source) else 0
        detail = f"race #{race_id} · {boat['boat_name']}"
        result = EntriesAdded(added=added, kind="boat", across_series=across,
                              boat_name=boat["boat_name"])
        action = "boat added to race"
    else:
        if series_id:
            added = add_active_boats_to_series_races(db, int(series_id), race_id, race_class,
                                                     rating_rule, rating_source="AUTO")
        else:
            added = add_active_boats_to_race(db, race_id, race_class, rating_rule,
                                             rating_source="AUTO")
        detail = f"race #{race_id} · {added} added"
        result = EntriesAdded(added=added, kind="all_active", across_series=across)
        action = "boats bulk-added to race"
    db.commit()

    # Every boat changes every other boat's start time in a pursuit, so the
    # ladder is recomputed here rather than left to whoever added the entry.
    if is_pursuit_race(race):
        recompute_pursuit_start_times(race_id)
    log_activity(action, detail, user=actor)
    return result


# ---------------------------------------------------------------------------
# Shortening the course
# ---------------------------------------------------------------------------

@dataclass
class ShortenCall:
    mark: str
    index: int
    at_time: str
    announcement: str
    event_id: Optional[int] = None


def signal_shortened_course(race_id: int, announcement: str) -> None:
    """Two horn blasts, then the spoken announcement, repeated once.

    The announcement waits for the horns to finish so it is not drowned out by
    them. Best-effort throughout: a hardware hiccup must not take down the call
    that has already been recorded.
    """
    try:
        duration = hardware_config()["horn_duration_ms"]
        # fire_horn blocks for the blast on real hardware but returns at once in
        # simulation, so sleep by the blast length to space the two sounds and
        # to let the second finish before the announcement begins.
        blast = max(0.4, duration / 1000.0)
        fire_horn(duration)
        time.sleep(blast + 0.3)
        fire_horn(duration)
        time.sleep(blast + 0.6)
        queue_central_audio(announcement, label="Shortened course", race_id=race_id,
                            source="shorten-course")
        time.sleep(6)
        queue_central_audio(announcement, label="Shortened course (repeat)", race_id=race_id,
                            source="shorten-course")
    except Exception:
        pass


def shorten_course_at(db: sqlite3.Connection, race: sqlite3.Row, index: Any,
                      actor: str = "system", signal: bool = True) -> ShortenCall:
    """Call a shortened course at a mark: record it, log it, and sound it.

    The signal is part of the call rather than something the caller adds
    afterwards. A shortened course recorded silently is a fleet still sailing
    the full course, which is the one outcome shortening exists to prevent.
    """
    race_id = int(race["id"])
    options = {str(o["index"]): o for o in course_shorten_options(course_for_race(race))}
    opt = options.get(str(index))
    if not opt:
        raise RaceValidationError("Choose a mark on the course to shorten at.", field="index")

    now = datetime.now().isoformat(timespec="seconds")
    db.execute(
        "UPDATE races SET shortened_at_mark = ?, shortened_at_index = ?, shortened_at_time = ?"
        " WHERE id = ?",
        (opt["display"], opt["index"], now, race_id),
    )
    db.commit()

    announcement = (f"Shortened course called on mark {opt['display']} — after this mark "
                    "proceed to finish.")
    event_id = log_event(race_id, "shorten", f"Shortened course at mark {opt['display']}",
                         "manual", {"mark": opt["display"], "index": opt["index"]})
    if signal:
        threading.Thread(target=signal_shortened_course, args=(race_id, announcement),
                         name=f"shorten-{race_id}", daemon=True).start()
    log_activity("course shortened", f"#{race_id} at mark {opt['display']}", user=actor)
    return ShortenCall(mark=opt["display"], index=opt["index"], at_time=now,
                       announcement=announcement, event_id=event_id)


# ---------------------------------------------------------------------------
# Postponement -- RRS 27.3 and the Race Signals for AP
# ---------------------------------------------------------------------------
# The club used to delay a start by editing the start time. Nothing was
# signalled, so the fleet's only notice was VHF or nothing at all; and because
# the horn scheduler is keyed on that time, editing it during a sequence drops
# the rest of the running sequence and plans a fresh one. A fleet that has heard
# a warning and then gets no preparatory and no gun is a general recall at best.
#
# A postponement is a signal, so it is recorded as one. The race keeps its
# scheduled time; AP is what changes, and the sequence is suspended while it
# flies. The new warning is then computed from the rule instead of guessed:
# the warning signal is made one minute after AP is removed, which is exactly
# why a race officer need not predict when the wind will fill in. That is what
# the flag is for, and it is the part the old habit could not express.

@dataclass
class Postponement:
    kind: str
    flag: str
    meaning: str
    at_time: str
    event_id: Optional[int] = None


@dataclass
class Resumption:
    kind: str
    flag: str
    warning_time: str
    announcement: str
    # When the flag actually comes down. Not always the time asked for: a
    # confirmation agreed to after its moment has passed rolls to the next
    # whole minute, and the caller is told which.
    ends_at: str = ""
    event_id: Optional[int] = None


def signal_postponed(race_id: int, flag: str, meaning: str) -> None:
    """Two sounds for AP going up, then the announcement. Best effort.

    Two sounds is not decoration: it is what Race Signals specifies, and it is
    how a boat too far off to read the flag knows to look at the mast.
    """
    try:
        duration = hardware_config()["horn_duration_ms"]
        # fire_horn blocks for the blast on real hardware but returns at once in
        # simulation, so sleep by the blast length to space the two sounds.
        blast = max(0.4, duration / 1000.0)
        fire_horn(duration)
        time.sleep(blast + 0.3)
        fire_horn(duration)
        time.sleep(blast + 0.6)
        queue_central_audio(f"{flag}. {meaning}", label=f"Postponed ({flag})",
                            race_id=race_id, source="postpone")
    except Exception:
        pass


def signal_resumed(race_id: int, announcement: str) -> None:
    """One sound for AP coming down, then the announcement. Best effort."""
    try:
        duration = hardware_config()["horn_duration_ms"]
        blast = max(0.4, duration / 1000.0)
        fire_horn(duration)
        time.sleep(blast + 0.6)
        queue_central_audio(announcement, label="Postponement ended", race_id=race_id,
                            source="postpone")
    except Exception:
        pass


def normalise_postponement_kind(kind: Any) -> str:
    """Read "AP over H", "ap_h" or "AP/H" as the one stored kind."""
    text = str(kind or "AP").strip().upper().replace("OVER", " ").replace("/", " ")
    text = "_".join(part for part in text.replace("_", " ").split() if part)
    return text or "AP"


def postpone_race(db: sqlite3.Connection, race: sqlite3.Row, kind: Any = "AP",
                  actor: str = "system", signal: bool = True) -> Postponement:
    """Fly AP: record the postponement, sound it, and suspend the sequence.

    The signal is part of the call rather than something a caller adds
    afterwards, for the same reason it is in `shorten_course_at`: a
    postponement recorded silently is a fleet still sailing to the old gun.
    """
    kind = normalise_postponement_kind(kind)
    spec = POSTPONEMENT_KINDS.get(kind)
    if not spec:
        raise RaceValidationError(
            "Postpone with AP, AP over H (further signals ashore) or AP over A "
            "(no more racing today).", field="kind")

    race_id = int(race["id"])
    started = race_first_start_dt(race)
    if started and datetime.now() >= started:
        # AP postpones "races not started". After the starting signal it is
        # abandonment -- flag N -- which has different consequences for boats
        # already round the first mark. Refusing is honest; quietly doing the
        # wrong signal is not.
        raise RaceValidationError(
            "That race has already started, and AP postpones races that have not. "
            "Abandoning a race under way is flag N, which this app cannot signal.",
            field="kind")

    now = datetime.now().isoformat(timespec="seconds")
    db.execute("UPDATE races SET postponed_at = ?, postponement_kind = ? WHERE id = ?",
               (now, kind, race_id))
    db.commit()

    event_id = log_event(race_id, "postpone", f"Postponed: {spec['flag']} up, two sounds",
                         "manual", {"kind": kind, "flag": spec["flag"]})
    # The start video is booked when the time is set, and its builder sleeps
    # until that moment. The fleet will not be starting then, so a clip of the
    # empty line is all it would catch.
    cancel_pending_start_clips(race_id)
    # The scheduler reads the race afresh and finds it postponed. Clearing the
    # fired keys stops a signal that was due at the old time from counting as
    # already sounded once the race does start.
    reset_start_sequence_state_for_race(race_id)
    if signal:
        threading.Thread(target=signal_postponed,
                         args=(race_id, spec["flag"], spec["meaning"]),
                         name=f"postpone-{race_id}", daemon=True).start()
    log_activity("race postponed", f"#{race_id} {spec['flag']}", user=actor)
    return Postponement(kind=kind, flag=spec["flag"], meaning=spec["meaning"],
                        at_time=now, event_id=event_id)


def resume_race(db: sqlite3.Connection, race: sqlite3.Row, actor: str = "system",
                signal: bool = True, lower_at: Optional[str] = None,
                warning_time: Optional[str] = None) -> Resumption:
    """Say when AP comes down. The warning signal follows one minute later.

    The one-minute rule is why this is a command of its own rather than an edit
    of the start time. But "lower it now" is not good enough either: pressed at
    14:15:41 it put the warning signal at 14:16:41 and the gun at 14:21:41, and
    a fleet cannot count down to times like that. So the race officer names the
    minute the flag comes down, and everything follows from it on whole minutes.

    The flag stays up until that moment -- the displays show what is actually on
    the mast, not what has been decided about it -- and the one sound is made
    then, by the same scheduler that makes every other timed signal.

    ``warning_time`` is the older spelling, kept because AP over H asks for the
    warning directly: ashore there is no one-minute rule to work back from.
    """
    race_id = int(race["id"])
    if not race_is_postponed(race):
        # Almost always a second press on a page that has not been reloaded since
        # the flag came down. "That race is not postponed" is true and useless;
        # say what happened and what is now scheduled.
        warning = str(row_get(race, "start_time", "") or "")
        if str(row_get(race, "postponed_at", "") or ""):
            raise RaceValidationError(
                f"AP has already come down. The warning signal is at {dt_display(warning)}.",
                field="race_id")
        raise RaceValidationError("That race is not postponed.", field="race_id")
    kind = normalise_postponement_kind(row_get(race, "postponement_kind", "") or "AP")
    spec = POSTPONEMENT_KINDS.get(kind, POSTPONEMENT_KINDS["AP"])
    if kind == "AP_A":
        raise RaceValidationError(
            "AP over A means no more racing today, so that race is not resumed. Postpone "
            "with plain AP instead if racing is still intended.", field="kind")

    if not spec["one_minute_rule"]:
        # "Further signals ashore" is exactly that: the next signals come later,
        # ashore, and there is no one-minute warning hanging off the flag coming
        # down. So the warning is asked for rather than invented.
        if not warning_time:
            raise RaceValidationError(
                "AP over H means further signals ashore, so there is no one-minute warning to "
                "count from. Give the new warning signal time.", field="warning_time")
        warning = normalise_start_time_value(str(warning_time))
        ends_at = datetime.now().isoformat(timespec="seconds")
    else:
        chosen = normalise_start_time_value(str(lower_at or "")) if lower_at else ""
        chosen_dt = parse_dt(chosen)
        now = datetime.now()
        if not chosen_dt or chosen_dt <= now:
            # A time that has gone -- nothing given, a stale confirmation, or a
            # proposal agreed to minutes after it was read back. Rolling forward
            # keeps the whole-minute promise; the caller is told the time that
            # was actually used rather than the one it asked for.
            chosen = next_whole_minute(at_least_seconds=LOWER_AP_MIN_LEAD_S)
            chosen_dt = parse_dt(chosen)
        elif (chosen_dt - now).total_seconds() < LOWER_AP_MIN_LEAD_S:
            # Too soon to be announced. One minute before the flag moves the app
            # says so, and a flag lowered forty seconds from now is one nobody
            # was told about -- so this is refused rather than quietly moved,
            # because the race officer is choosing a time the fleet will hear.
            earliest = next_whole_minute(at_least_seconds=LOWER_AP_MIN_LEAD_S)
            raise RaceValidationError(
                f"That is too soon: the app announces AP coming down one minute beforehand, "
                f"so give it at least that. The earliest is {dt_display(earliest)}.",
                field="lower_at")
        ends_at = chosen
        warning = (chosen_dt + timedelta(seconds=RESUME_WARNING_DELAY_S)).isoformat(
            timespec="seconds")

    db.execute("UPDATE races SET start_time = ?, postponement_ends_at = ? WHERE id = ?",
               (warning, ends_at, race_id))
    db.commit()

    # Whole minutes throughout, so the seconds `dt_display` adds are always zero
    # -- and this line is spoken aloud, where "sixteen fifty-four and no seconds"
    # is just something else for a race officer to listen past.
    def clock(value: Any) -> str:
        moment = parse_dt(str(value))
        return moment.strftime("%H:%M") if moment else ""

    gun = parse_dt(warning)
    gun_text = (gun + WARNING_TO_GUN).strftime("%H:%M") if gun else ""
    announcement = (f"{spec['flag']} comes down at {clock(ends_at)}. Warning signal "
                    f"{clock(warning)}, first gun {gun_text}.")
    event_id = log_event(race_id, "postpone",
                         f"Postponement ending: {spec['flag']} down at {dt_display(ends_at)}, "
                         f"one sound; warning signal {dt_display(warning)}",
                         "manual", {"kind": kind, "warning_time": warning, "ends_at": ends_at})

    updated = db.execute("SELECT * FROM races WHERE id = ?", (race_id,)).fetchone()
    if updated is not None:
        if is_pursuit_race(updated):
            # Every boat's start moves with the warning in a pursuit.
            recompute_pursuit_start_times(race_id)
        reset_start_sequence_state_for_race(race_id)
        ensure_start_video_scheduled(updated)
        # Booked for the new gun above; anything still waiting for an older
        # one would film an empty line.
        cancel_pending_start_clips(race_id, keep_event_time=race_first_start_time(updated))
    # No horn here: the flag is still up. The single sound is made when it comes
    # down, by core.startsequence, which is what makes every other timed signal.
    # But the fleet is told now, so that boats sitting in a dying breeze can plan
    # rather than watch the mast -- and told again if the time is changed, since
    # this runs afresh each time the race officer sets one.
    queue_central_audio(announcement, label=f"{spec['flag']} coming down at "
                                            f"{dt_display(ends_at)}",
                        race_id=race_id, source="postpone")
    log_activity("postponement ending", f"#{race_id} AP down {dt_display(ends_at)}, "
                                        f"warning {dt_display(warning)}", user=actor)
    return Resumption(kind=kind, flag=spec["flag"], warning_time=warning,
                      ends_at=ends_at, announcement=announcement, event_id=event_id)


def clear_shortening(db: sqlite3.Connection, race: sqlite3.Row, actor: str = "system") -> int:
    """Undo a shortened-course call made in error. Returns the event id."""
    race_id = int(race["id"])
    db.execute(
        "UPDATE races SET shortened_at_mark = NULL, shortened_at_index = NULL,"
        " shortened_at_time = NULL WHERE id = ?",
        (race_id,),
    )
    db.commit()
    event_id = log_event(race_id, "shorten", "Shortened course cleared", "manual", {})
    log_activity("course shortening cleared", f"#{race_id}", user=actor)
    return event_id
# ---------------------------------------------------------------------------
# Deleting a series
# ---------------------------------------------------------------------------

def series_delete_block(db: sqlite3.Connection, series_id: int) -> Optional[str]:
    """The reason this series cannot be deleted, or None if it is safe to.

    Same shape as ``core.marks.mark_delete_block``: the page asks before it
    offers the button, and the route asks again before it acts, so a stale page
    cannot delete something that has since been used.
    """
    row = db.execute("SELECT id FROM race_series WHERE id = ?", (series_id,)).fetchone()
    if not row:
        return "Series not found."
    n = db.execute("SELECT COUNT(*) AS n FROM races WHERE series_id = ?",
                   (series_id,)).fetchone()["n"]
    if n:
        # Deleting the series row alone would leave those races pointing at a
        # series that is not there: they would vanish from the season's
        # standings while still claiming to belong to it, and the class bands
        # and start plan they were scored under would be gone with no record of
        # what they had been. Cascading instead would delete a season's racing
        # from a button on a list page. So neither: say what is in the way.
        return (f"{n} race{'s' if n != 1 else ''} still in this series. Move "
                f"{'them' if n != 1 else 'it'} to another series (or to none) on "
                f"the race's Course & start tab, or delete "
                f"{'the races' if n != 1 else 'the race'} first.")
    return None


def delete_series(db: sqlite3.Connection, series_id: int, actor: str = "system") -> str:
    """Delete an empty series. Returns its name; raises if anything is in it."""
    block = series_delete_block(db, series_id)
    if block:
        raise RaceValidationError(block, field="series_id")
    row = db.execute("SELECT name FROM race_series WHERE id = ?", (series_id,)).fetchone()
    name = (row["name"] if row else "") or f"#{series_id}"
    db.execute("DELETE FROM race_series WHERE id = ?", (series_id,))
    db.commit()
    log_activity("series deleted", f"#{series_id} '{name}'", user=actor)
    return name
