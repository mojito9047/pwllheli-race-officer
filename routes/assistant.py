"""Run the racing by typing a sentence: interpret, read back, confirm, act.

Two endpoints and a hard line between them. `POST /api/assistant/command`
interprets and **changes nothing**; it answers with what it would do and a
short-lived token. `POST /api/assistant/command/confirm` is the only thing that
acts, and it acts by calling the same `core.raceadmin` functions the race sheet
calls -- there is no second way to create a race in this file.

Why the split is worth two round trips: the user is one-handed on a boat, over
a connection that drops, and a misheard command fires a horn at a fleet. A
read-back they have to agree with is the cheapest possible check, and it is the
only one that catches the class of mistake where the app understood something
perfectly and it was not what was meant.

Login is required -- these endpoints are not in the public allow-list, and CSRF
applies as it does everywhere else. That is deliberate while the club decides
who may drive the racing remotely: today it is exactly the people who can
already do all of this on the race sheet.

What is not here: arming a start. The club has settled that a start may run
with nobody watching the line, but *who* may arm one from a phone is a separate
decision, and until it is made the tool is not offered (`core.assistant.TOOLS`).
"""
from __future__ import annotations

import json
import os
import re
import secrets
import time
from datetime import datetime, timedelta

from core import track
from core.boats import find_boat_ratings, insert_boat_from_listings
from core.courses import apply_course_shortening, validate_course_sequence_json
from core.timeutils import bearing_deg, haversine_nm
from core.assistant import (
    ANSWER,
    ANSWERED,
    NEEDS_CLARIFICATION,
    NEEDS_CONFIRMATION,
    NOT_UNDERSTOOD,
    CommandContext,
    answer_to_question,
    grammar_parse,
    parse_command,
    reads_as_no,
    reads_as_yes,
    resolve,
)
from core.assistant_llm import interpreter_status, parser_from_config
from core.raceadmin import (
    EntryScope,
    RaceSettings,
    RaceSpec,
    RaceValidationError,
    add_entries,
    create_race,
    postpone_race,
    resume_race,
    set_custom_course,
    shorten_course_at,
    update_pursuit_settings,
    update_race_settings,
)
from routes import app_module

_app = app_module()
analyse_course_with_wind = _app.analyse_course_with_wind
app = _app.app
appstate = _app.appstate
audit = _app.audit
course_chart_config = _app.course_chart_config
course_for_race = _app.course_for_race
course_shorten_options = _app.course_shorten_options
current_actor = _app.current_actor
compute_dual_results = _app.compute_dual_results
search_boats = _app.search_boats
list_series = _app.list_series
listing_config = _app.listing_config
race_is_finished_for_public = _app.race_is_finished_for_public
current_user_can_race_remotely = _app.current_user_can_race_remotely
get_current_competitor_race = _app.get_current_competitor_race
hardware_config = _app.hardware_config
get_db = _app.get_db
get_entries = _app.get_entries
get_race = _app.get_race
init_db = _app.init_db
jsonify = _app.jsonify
latest_weather_sample = _app.latest_weather_sample
dt_display = _app.dt_display
parse_dt = _app.parse_dt
race_first_start_time = _app.race_first_start_time
recommend_courses_with_polar = _app.recommend_courses_with_polar
render_template = _app.render_template
request = _app.request
resolve_polar_path = _app.resolve_polar_path
resolve_sail_chart_path_for_polar = _app.resolve_sail_chart_path_for_polar
load_polar = _app.load_polar
load_sail_chart = _app.load_sail_chart
target_speed_info_for = _app.target_speed_info_for
from core.races import postponement_flag

row_get = _app.row_get
url_for = _app.url_for
weather_history = _app.weather_history
weather_runtime_status = _app.weather_runtime_status

# Long enough to read a sentence on a phone and press yes; short enough that a
# command confirmed much later is one whose moment has passed.
PENDING_TTL_SECONDS = 300

# Read-only intents skip the confirmation step: there is nothing to undo, and
# asking a competitor to confirm a question they just asked is noise.
READ_ONLY_INTENTS = {"race_status", "race_results", "look_up", "boat_rating"}


# Every turn is remembered, not only the ones that changed something. A question
# the app asked and the answer it got are the two halves of one exchange, and
# recording only the half that acted is what made "standard" -- the answer to the
# app's own question -- come back as "I did not understand that".
THREADED_STATUSES = ("done", "pending", "asked", "answered", "not_understood")


def _remember(actor: str, text: str, intent: str, reply: str, status: str,
              resolved: dict = None) -> str:
    """Record one turn of the conversation, whatever kind of turn it was."""
    now = datetime.now()
    token = "ow_" + secrets.token_urlsafe(12)
    with get_db() as db:
        db.execute(
            "INSERT INTO assistant_commands (token, actor, text, intent, resolved_json,"
            " readback, status, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (token, actor, text, intent or "", json.dumps(resolved or {}), reply or "", status,
             now.isoformat(timespec="seconds"),
             (now + timedelta(seconds=PENDING_TTL_SECONDS)).isoformat(timespec="seconds")))
        db.commit()
    return token


def _recent_exchanges(actor: str, limit: int = 6):
    """The last few turns: what this person said and what the app said back.

    Without it every sentence is read alone, so "make it a pursuit", "add the
    fleet to that" or a one-word answer to a question has no antecedent and the
    interpreter has to guess or decline. Scoped to the one person and to the last
    half hour: a thread is a conversation, not the club's whole history.
    """
    since = (datetime.now() - timedelta(minutes=30)).isoformat(timespec="seconds")
    placeholders = ", ".join("?" for _ in THREADED_STATUSES)
    with get_db() as db:
        rows = db.execute(
            "SELECT text, readback, result_json, status FROM assistant_commands"
            f" WHERE actor = ? AND created_at >= ? AND status IN ({placeholders})"
            " ORDER BY id DESC LIMIT ?",
            (actor, since, *THREADED_STATUSES, limit)).fetchall()
    exchanges = []
    for row in reversed(rows):
        done = json.loads(row["result_json"] or "{}").get("message")
        exchanges.append((row["text"], done or row["readback"] or ""))
    return exchanges


def _thread_for(actor: str, limit: int = 14):
    """The conversation so far, for a page that has just been loaded.

    The thread lived only in the browser, so a refresh -- a dropped signal, a
    phone locking, a fat thumb -- threw away everything said and left a proposal
    on the server that nothing on screen could agree to any more. All of it was
    already recorded; the page simply never asked.
    """
    since = (datetime.now() - timedelta(minutes=30)).isoformat(timespec="seconds")
    placeholders = ", ".join("?" for _ in THREADED_STATUSES)
    with get_db() as db:
        rows = db.execute(
            "SELECT text, readback, result_json, status FROM assistant_commands"
            f" WHERE actor = ? AND created_at >= ? AND status IN ({placeholders})"
            " ORDER BY id DESC LIMIT ?",
            (actor, since, *THREADED_STATUSES, limit)).fetchall()
    kinds = {"done": "ow-done", "pending": "ow-ask", "asked": "ow-ask",
             "not_understood": "ow-ask"}
    turns = []
    for row in reversed(rows):
        said = json.loads(row["result_json"] or "{}").get("message") or row["readback"] or ""
        turns.append({"you": row["text"], "app": said,
                      "kind": kinds.get(row["status"], "")})
    return turns


def _open_question(actor: str):
    """The question this person has been asked and not yet answered, if any."""
    now = datetime.now().isoformat(timespec="seconds")
    with get_db() as db:
        return db.execute(
            "SELECT * FROM assistant_commands WHERE actor = ? AND status = 'asked'"
            " AND expires_at >= ? ORDER BY id DESC LIMIT 1", (actor, now)).fetchone()


def _close_question(row_id: int) -> None:
    with get_db() as db:
        db.execute("UPDATE assistant_commands SET status = 'answered' WHERE id = ?", (row_id,))
        db.commit()


def _reply_to_open_question(actor: str, text: str):
    """Read this sentence as the answer to the question still open, or not at all.

    Deliberately the first thing tried and deliberately narrow: a reply that is
    plainly not an answer falls through to ordinary interpretation, so changing
    the subject mid-question does what it looks like.
    """
    row = _open_question(actor)
    if not row:
        return None
    stored = json.loads(row["resolved_json"] or "{}")
    intent = answer_to_question(row["intent"], stored.get("arguments") or {},
                                stored.get("field") or "", stored.get("options") or [], text)
    if intent is not None:
        _close_question(int(row["id"]))
    return intent


def _recent_races(limit: int = 12):
    """The club's most recent races, newest first.

    So "the results of the night race" can be matched to a race at all. Asked
    for them, the app said it had no way to look up past results -- from the
    database that holds every one of them.
    """
    with get_db() as db:
        return db.execute(
            "SELECT * FROM races ORDER BY COALESCE(NULLIF(start_time, ''), created_at) DESC,"
            " id DESC LIMIT ?", (limit,)).fetchall()


def _pending_command(actor: str):
    """The proposal this person has been read back and not yet answered."""
    now = datetime.now().isoformat(timespec="seconds")
    with get_db() as db:
        return db.execute(
            "SELECT * FROM assistant_commands WHERE actor = ? AND status = 'pending'"
            " AND expires_at >= ? ORDER BY id DESC LIMIT 1", (actor, now)).fetchone()


def _supersede_pending(actor: str, keep_id: int) -> None:
    """One live proposal at a time.

    A second read-back leaves the first one still confirmable, so a Yes meant
    for what is on screen could carry out something said two minutes ago.
    """
    with get_db() as db:
        db.execute("UPDATE assistant_commands SET status = 'superseded'"
                   " WHERE actor = ? AND status = 'pending' AND id != ?", (actor, keep_id))
        db.commit()


def _working_race(actor: str = ""):
    """The race this conversation is about: the last one this person touched.

    Not the same question as "which race is on now", and the difference is the
    whole of what a thread means. A race created from the water for this evening
    is not the current race by the clock, so "put the start back ten minutes"
    said straight afterwards had nothing to be relative to and "add all the
    boats" would have gone somewhere else entirely.

    Falls back to the club's current race, which is right for somebody who has
    just opened the page mid-afternoon and typed "status".
    """
    if actor:
        since = (datetime.now() - timedelta(minutes=30)).isoformat(timespec="seconds")
        with get_db() as db:
            rows = db.execute(
                "SELECT resolved_json, result_json FROM assistant_commands"
                " WHERE actor = ? AND created_at >= ? ORDER BY id DESC LIMIT 10",
                (actor, since)).fetchall()
        for row in rows:
            for blob in (row["result_json"], row["resolved_json"]):
                race_id = json.loads(blob or "{}").get("race_id")
                if not race_id:
                    continue
                race = get_race(int(race_id))
                if race is not None:
                    return race
    return get_current_competitor_race()


def _wind_now():
    """The wind the club's own instrument is reading, or None. (twd, tws)."""
    try:
        status = weather_runtime_status()
        wind = status.get("sample") or status.get("latest") or latest_weather_sample()
        if not wind or wind.get("twd") is None:
            return None
        return float(wind["twd"]), float(wind.get("tws") or 0.0)
    except Exception:
        return None


def _wind_trend(minutes: int = 60) -> list:
    """What the wind has done in the last hour, from the samples already stored.

    "When did the wind last change?" is the question a race officer asks before
    choosing a course, and the app was answering that it only knew the current
    reading -- while holding an hour of samples behind the wind chart. Given as
    the swing and the extremes rather than a number per minute: what matters is
    whether it is steady, and if not, which way it has gone.
    """
    try:
        history = [row for row in weather_history(minutes) if row.get("twd") is not None]
    except Exception:
        return []
    if len(history) < 3:
        return []
    directions = [float(row["twd"]) for row in history]
    speeds = [float(row["tws"]) for row in history if row.get("tws") is not None]
    first, last = directions[0], directions[-1]
    swing = (last - first + 540) % 360 - 180          # signed, shortest way round
    spread = max(directions) - min(directions)
    if spread > 180:                                   # samples either side of north
        spread = 360 - spread
    way = "right" if swing > 0 else "left"
    facts = [f"Over the last {minutes} minutes the wind has gone from {round(first)} to "
             f"{round(last)} degrees, a {abs(round(swing))} degree shift to the {way}, "
             f"and has swung {round(spread)} degrees in all."]
    if speeds:
        facts.append(f"Its strength over that hour ranged from {round(min(speeds), 1)} to "
                     f"{round(max(speeds), 1)} knots.")
    return facts


def _plain_age(seconds: float) -> str:
    """A length of time as somebody would say it out loud.

    "Silent for 16593 minutes" is arithmetic, not an answer; eleven days is the
    same fact in a form that tells you the tracker is in a cupboard ashore.
    """
    minutes = max(0, int(seconds // 60))
    if minutes < 90:
        return f"{minutes} minutes"
    if minutes < 60 * 36:
        return f"{round(minutes / 60)} hours"
    return f"{round(minutes / 1440)} days"


def _fleet_facts(race) -> list:
    """Where the boats have got to: the order on the water, mark by mark.

    The same rows the race sheet's *Position on the water* list is drawn from,
    so the two cannot disagree. Asked "what leg are they on?" and "who is
    winning?", the app answered that it only had entry counts -- while computing
    this for the race sheet, the competitor page and the clubhouse television.
    """
    if race is None:
        return []
    try:
        board = track.race_leaderboard(int(race["id"]))
    except Exception:
        return []
    tracked = [row for row in board if row.get("tracked")]
    if not tracked:
        return []
    said = []
    for row in tracked[:15]:
        name = str(row.get("boat_name") or "")
        if row.get("finished"):
            said.append(f"{name}: finished")
            continue
        where = f"{row.get('rounded')} of {row.get('total')} marks rounded"
        if row.get("next_mark"):
            where += f", sailing to {row['next_mark']}"
        if row.get("dist_remaining_nm") is not None:
            where += f", {row['dist_remaining_nm']} nm still to sail"
        said.append(f"{name}: {where}")
    return ["The order on the water, leader first (this takes no account of handicap): "
            + "; ".join(said) + "."]


def _tracker_facts() -> list:
    """Whether the trackers are actually reporting, and how recently.

    "Are the trackers working?" was answered "the app doesn't tell me anything
    about trackers" -- by the app that collects their fixes and decides GPS
    finishes from them. It is also the question worth asking before relying on
    one: a tracker silent for twenty minutes will not finish anybody.
    """
    try:
        if not track.track_config().get("enabled"):
            return ["GPS tracking is switched off, so no tracker is reporting."]
        trackers = track.list_trackers()
        if not trackers:
            return ["GPS tracking is on, but no trackers are set up."]
        newest = track._latest_fix_times()
        now = time.time()
        fresh = [t for t in trackers
                 if now - float(newest.get(str(t.get("unique_id")), 0) or 0) <= 300]
        stale = len(trackers) - len(fresh)
        said = (f"{len(fresh)} of {len(trackers)} trackers have reported in the last five "
                "minutes")
        if stale:
            ages = [now - float(newest.get(str(t.get("unique_id")), 0) or 0)
                    for t in trackers if t not in fresh
                    and float(newest.get(str(t.get("unique_id")), 0) or 0) > 0]
            said += (f"; {stale} have not"
                     + (f", the quietest for {_plain_age(max(ages))}" if ages
                        else " and have never reported at all"))
        return [said + "."]
    except Exception:
        return []


def _leg_shape(analysis: dict) -> str:
    """The course as a race officer describes it: mark to mark, how far, and on
    what point of sail.

    The distances are the app's own measurement between the marks as they stand.
    Without them, asked how far it is from O to 1, it could only give the total
    course length -- from the walk it had just done leg by leg to work that total
    out.
    """
    legs = []
    for leg in analysis.get("legs_analysis", []) or []:
        point = str(leg.get("point_of_sail") or "").strip().lower()
        if not point or point == "—":
            continue
        distance = leg.get("distance_nm")
        far = f" {round(float(distance), 2)} nm" if distance is not None else ""
        legs.append(f"{leg.get('from')} to {leg.get('to')}{far} {point}")
    return ", ".join(legs)


def _leader_has_rounded(race, known: bool = False) -> int:
    """How many marks the leading boat has rounded, or 0 when nothing is tracking.

    `known=True` asks the other question — whether anything is reporting at all —
    because "nobody has rounded a mark yet" and "no idea where anybody is" are
    the same number and must not be the same answer.
    """
    try:
        board = track.race_leaderboard(int(race["id"]))
    except Exception:
        return 0 if not known else 0
    rounded = [int(row.get("rounded") or 0) for row in board if row.get("tracked")]
    if known:
        return 1 if rounded else 0
    return max(rounded) if rounded else 0


def _course_as_sailed(race):
    """The race's course, truncated if it has been shortened.

    `course_for_race` gives the course as set; a shortened race is sailed to the
    marks up to the one called and then to the finish. The board, the chart and
    the time round should all be of the course the fleet is actually sailing.
    """
    course = course_for_race(race)
    index = row_get(race, "shortened_at_index", None)
    if index is None:
        return course
    return apply_course_shortening(course, index)


def _expected_time_round(race):
    """How long the race's course should take in the wind now, and by when.

    (minutes, "HH:MM") or None -- None whenever the wind is not being read, the
    course cannot be analysed or there is no start time, because each of those
    makes the number a guess rather than the app's own working.
    """
    wind = _wind_now()
    if not wind or race is None or not row_get(race, "course_set", 1):
        return None
    try:
        analysis = analyse_course_with_wind(_course_as_sailed(race), wind[0], wind[1])
        minutes = analysis.get("predicted_minutes")
        if not minutes:
            return None
        gun = parse_dt(race_first_start_time(race))
        finishing = (gun + timedelta(minutes=float(minutes))).strftime("%H:%M") if gun else ""
        return round(float(minutes)), finishing
    except Exception:
        return None


def _header_state(race) -> dict:
    """The strip at the top of the page: the race, the gun, the course.

    Sent back with every command as well as rendered into the page, because a
    confirmed change to the start or the course has to move the countdown that
    was the reason for making it -- a header still counting to the old gun is
    worse than no header. One function, so the page and the reply cannot
    disagree about the same three facts.
    """
    if race is None:
        return {"race_name": "No current race", "state": "Nothing to run",
                "first_gun": "", "course_text": "", "wind_text": "", "finished": False,
                "postponed_flag": "", "postponement_ends_at": ""}
    wind = _wind_now()
    # The course as it is now being sailed. After a shortening the board was
    # still showing all thirteen marks of course 3 beside the words "shortened
    # at 4" — the fleet rounds 4 and goes to the finish, and the board is the
    # thing that gets read out.
    course = _course_as_sailed(race)
    expected = _expected_time_round(race)
    if course and row_get(race, "course_set", 1):
        # A made-up course has no number: its course_no reads "Made up course",
        # which the ordinary prefix turned into "Course Made up course".
        number = course.get("course_no")
        bits = [str(number) if not str(number).strip().isdigit() else f"Course {number}"]
        length = course.get("length_nm")
        if length and str(length) != "—":
            bits.append(f"{length} nm")
        if expected:
            bits.append(f"about {expected[0]} min")
        if row_get(race, "shortened_at_mark", ""):
            bits.append(f"shortened at {row_get(race, 'shortened_at_mark', '')}")
        course_text = " · ".join(bits)
    else:
        course_text = "Course not set"
    return {
        "race_name": str(row_get(race, "name", "") or ""),
        "state": "Waiting",
        "first_gun": race_first_start_time(race) or "",
        "course_text": course_text,
        "wind_text": f"{round(wind[0])}°T {round(wind[1], 1)} kn" if wind else "",
        "finished": race_is_finished_for_public(int(race["id"])),
        # Under AP the race keeps its scheduled time, so the strip counted down
        # to a gun that was not coming -- on the one page whose whole premise is
        # that nobody is in the hut to notice.
        "postponed_flag": postponement_flag(race),
        "postponement_ends_at": str(row_get(race, "postponement_ends_at", "") or ""),
        # The chart and the course board are drawn from these, so a course
        # change redraws both. Without them the strip said course 17 above a
        # picture of course 16.
        "course_marks": (course or {}).get("marks") or [],
        "course_board": _board_marks(course),
        "wind_direction": round(wind[0]) if wind else "",
    }


def _header_for(result: dict) -> dict:
    """The strip after a command: the race it acted on, or the current one.

    Following the race just touched rather than whatever is "current" matters on
    the water -- a race created for this evening is not the current race by the
    clock, and a header that ignored it would leave somebody watching a
    countdown to a race they are not running.
    """
    race_id = (result or {}).get("race_id")
    race = get_race(int(race_id)) if race_id else None
    return _header_state(race if race is not None else _working_race(current_actor()))


def _course_facts(race, wind) -> list:
    """What the fixed courses look like in the wind that is blowing now.

    All of this was already computed -- it is what the Course & start tab draws
    and what the recommendation ranks by -- and none of it was ever told to the
    interpreter, so "how long will the race be?" and "can we have more reaching?"
    got "the app does not tell me" from an app that knew both. Sixty-seven
    courses analyse in about twenty milliseconds, so this costs the request
    nothing worth measuring.
    """
    if not wind:
        # Every one of these numbers is a function of the wind. Without a reading
        # there is nothing to say, and inventing a breeze to say it with would be
        # worse than silence.
        return []
    twd, tws = wind
    facts = []
    target = 60.0
    course = _course_as_sailed(race) if race is not None else None
    if course and race is not None and row_get(race, "course_set", 1):
        analysis = analyse_course_with_wind(course, twd, tws)
        minutes = analysis.get("predicted_minutes")
        said = f"Course {course.get('course_no')} is {course.get('length_nm')} nautical miles"
        if minutes:
            target = float(minutes)
            said += (f" and should take about {round(minutes)} minutes round in this wind, "
                     "for a boat sailing to the polar")
        facts.append(said + ".")
        shape = _leg_shape(analysis)
        if shape:
            facts.append(f"Its legs are: {shape}.")
        # Named as the app's own tally, because it is: a close-hauled leg counts
        # as upwind only when the polar says it is sailed as a beat, so "two
        # close hauled legs, none upwind" is right and reads like a mistake.
        facts.append(f"By the app's tally that is {analysis.get('upwind_legs')} upwind, "
                     f"{analysis.get('reach_legs')} reaching and {analysis.get('downwind_legs')} "
                     "downwind legs; a leg counts as upwind only if it is sailed as a beat.")
        if minutes and race is not None:
            gun = parse_dt(race_first_start_time(race))
            if gun:
                facts.append("On that course the first boats would finish about "
                             f"{(gun + timedelta(minutes=float(minutes))).strftime('%H:%M')}.")
    elif race is not None:
        facts.append("No course has been chosen for it yet.")

    rows = recommend_courses_with_polar(twd, tws, target, resolve_polar_path(None))
    for row in rows[:5]:
        facts.append(f"Course {row['course_no']}: {row.get('length_nm')} nm, about "
                     f"{round(row['predicted_minutes'])} minutes, {row.get('upwind_legs')} upwind / "
                     f"{row.get('reach_legs')} reaching / {row.get('downwind_legs')} downwind legs."
                     if row.get("predicted_minutes") else
                     f"Course {row['course_no']}: {row.get('length_nm')} nm.")
    reaching = [r for r in rows if r.get("predicted_minutes")]
    if reaching:
        most = max(reaching, key=lambda r: (r.get("reach_legs") or 0, -abs(r["predicted_minutes"] - target)))
        facts.append(f"The most reaching available in this wind is course {most['course_no']} with "
                     f"{most.get('reach_legs')} reaching legs ({most.get('length_nm')} nm, about "
                     f"{round(most['predicted_minutes'])} minutes).")
    return facts


# What the whole of fact-gathering is allowed to cost. Every fact is a database
# read or an arithmetic walk over a race, and on the club's own hut -- where the
# track database is a season deep and the tracker relay is writing to it while
# the clubhouse display and every competitor's phone are reading -- "usually
# fast" is not the same as "bounded". A page that takes longer than Cloudflare's
# patience answers 504, which is what happened on the water: three commands in a
# row, including a plain "status".
#
# So the context has a budget. Whatever is not assembled inside it is left out,
# and the interpreter is told that some detail is missing -- the command still
# works, with less to go on, which is the right way for this to degrade.
FACTS_BUDGET_S = float(os.environ.get("RO_VRO_FACTS_BUDGET_S", "2.0"))

# The two dearest facts, briefly remembered. This page is used a few times a
# minute at most, and where the fleet has got to does not change meaningfully in
# twenty seconds -- but recomputing it walks every fix of every boat in the race.
_FACT_CACHE: dict = {}
FACT_CACHE_S = 20.0


def _cached(key, build):
    """`build()`, at most once every FACT_CACHE_S."""
    now = time.monotonic()
    hit = _FACT_CACHE.get(key)
    if hit and now - hit[0] < FACT_CACHE_S:
        return hit[1]
    value = build()
    _FACT_CACHE[key] = (now, value)
    if len(_FACT_CACHE) > 64:                     # a race id per entry; never large
        for stale in [k for k, v in _FACT_CACHE.items() if now - v[0] > FACT_CACHE_S * 4]:
            _FACT_CACHE.pop(stale, None)
    return value


def _facts(race) -> list:
    """What the app knows about the racing right now, in plain sentences.

    This is the whole of what an answer in words may be built from -- there is no
    other source, which is the point. Anything that fails to assemble is left
    out, and so is anything that takes too long: each step is tried in turn and
    the rest are dropped once the budget is spent. A fact the app cannot produce
    quickly is one the interpreter can do without; a page that does not answer is
    not.
    """
    deadline = time.monotonic() + FACTS_BUDGET_S
    facts: list = []
    wind = _wind_now()

    def the_wind():
        if wind:
            facts.append(f"Wind now: {round(wind[0])} degrees true at {round(wind[1], 1)} knots.")
            facts.extend(_wind_trend())
        else:
            facts.append("The club's wind instrument is not reading, so nothing about the wind, "
                         "course lengths or timings can be worked out.")

    def the_race():
        if race is None:
            facts.append("There is no current race.")
            return
        race_id = int(race["id"])
        entries = get_entries(race_id)
        racing = sum(1 for e in entries if e["status"] == "RACING")
        finished = sum(1 for e in entries if e["finish_time"] or e["status"] == "FINISHED")
        facts.append(f"Race #{race_id} has {len(entries)} boats entered, {racing} racing "
                     f"and {finished} finished.")
        # By name and sail number, because "who has entered?" and "is GBR4822
        # racing?" are both ordinary questions, and the count alone made the app
        # answer that it only had a count -- from the list it had just counted.
        named = ", ".join(
            str(e["boat_name"]) + (f" ({row_get(e, 'sail_no', '')})"
                                   if row_get(e, "sail_no", "") else "")
            for e in entries[:30])
        if named:
            facts.append(f"The boats entered are: {named}"
                         + (" and more" if len(entries) > 30 else "") + ".")
        facts.append(f"It is a {row_get(race, 'race_type', 'standard') or 'standard'} race.")
        if race_is_finished_for_public(race_id):
            facts.append("Every boat in it has finished, so that race is over. Anything "
                         "about the next race means a new race rather than a change to "
                         "this one — say so and offer to create one.")
        if row_get(race, "shortened_at_mark", ""):
            facts.append("It has been shortened at mark "
                         f"{row_get(race, 'shortened_at_mark', '')}.")

    def the_boats():
        # The club's own boats. Asked "so I can't add any more boats?", the app
        # answered from the entry list alone and said it had no other boat data
        # -- then entered Mojito from the database a moment later, because
        # add_entries looks names up there.
        boats = search_boats("")
        listed = ", ".join(
            str(b["boat_name"]) + (f" ({row_get(b, 'sail_no', '')})"
                                   if row_get(b, "sail_no", "") else "")
            for b in boats[:60])
        if listed:
            facts.append("The club's active boats, any of which can be entered by name, are: "
                         f"{listed}" + (" and more" if len(boats) > 60 else "") + ".")

    def the_series_and_races():
        # Asked "what series will a new race be in?", the app proposed creating
        # one -- twice -- because it had no answer and no way to say so.
        names = ", ".join(str(row["name"]) for row in list_series())
        facts.append(f"The club's race series are: {names or 'none set up'}. A race created "
                     "from here is in no series unless the instruction names one, and a race "
                     "in no series is not scored in any standings.")
        # Enough to recognise a race somebody names in passing -- "the night
        # race", "Wednesday's" -- and hand it to race_results.
        recent = "; ".join(
            f"#{row['id']} '{row['name']}'"
            + (f" {str(row['start_time'])[:16].replace('T', ' ')}" if row["start_time"] else "")
            for row in _recent_races())
        if recent:
            facts.append(f"The most recent races are: {recent}. Use race_results to report on "
                         "any of them.")

    def the_trackers():
        facts.extend(_cached("trackers", _tracker_facts))

    def the_fleet():
        # Where the fleet has got to, for a race still being sailed. On a race
        # that is over it is neither wanted nor cheap: it walks every fix of
        # every boat, and an ISORA night race is nine hours of them.
        if race is None or race_is_finished_for_public(int(race["id"])):
            return
        facts.extend(_cached(f"fleet:{int(race['id'])}", lambda: _fleet_facts(race)))

    def the_horn():
        # Whether a stored start time will actually sound. There is no arming
        # step: the scheduler fires the sequence off the warning signal, so
        # moving a start time from the water moves the horn -- and the app was
        # saying the opposite, that only the race office could make it sound.
        if _app.race_console_config().get("start_automation_horn_enabled"):
            facts.append("Start automation is on: the app sounds the warning, preparatory and "
                         "start signals itself, off the race's stored warning signal. Setting or "
                         "moving a start time moves the horn with it — there is no separate "
                         "arming step.")
        else:
            facts.append("Start automation is switched off in Settings, so no horn will sound "
                         "by itself whatever start time is set.")

    def the_courses():
        facts.extend(_course_facts(race, wind))

    # In the order somebody on the water would want them if only the first few
    # arrive: the wind and the race first, the club's catalogues last.
    for step in (the_wind, the_race, the_trackers, the_fleet, the_horn,
                 the_courses, the_boats, the_series_and_races):
        if time.monotonic() > deadline:
            # Said out loud, because an interpreter answering from half the facts
            # must not present them as all of them.
            facts.append("Some details were left out because the app was busy; ask again for "
                         "anything missing.")
            break
        try:
            step()
        except Exception:
            # One fact that cannot be assembled must not take the others with it.
            continue
    return facts


def _context(actor: str = "") -> CommandContext:
    """What the interpreter is told, assembled here rather than sent by the client.

    `now` in particular: the sender is a phone on a boat and its clock is not
    the one the horn fires on.
    """
    race = _working_race(actor)
    gun = parse_dt(race_first_start_time(race)) if race else None
    return CommandContext(
        now=datetime.now(),
        current_race_id=int(race["id"]) if race else None,
        current_race_name=str(row_get(race, "name", "") or "") if race else "",
        race_type=str(row_get(race, "race_type", "standard") or "standard") if race else None,
        current_gun_time=gun,
        current_course_no=int(row_get(race, "course_no", 0) or 0) if race else None,
        course_is_set=bool(row_get(race, "course_set", 1)) if race else True,
        race_finished=race_is_finished_for_public(int(race["id"])) if race else False,
        recent=_recent_exchanges(actor) if actor else [],
        facts=_facts(race),
    )


def _parser():
    """The configured model. There is no longer anything underneath it.

    The built-in grammar used to answer when no model was configured or one
    could not be reached, on the reasoning that "status" and "shorten at mark 4"
    are worth having when the hut can reach nothing. In use that was worse than
    nothing: a page whose whole premise is "type what you want to do" answering
    six sentence shapes reads as an app that is simply stupid, and the person on
    the water cannot tell a sentence it will not understand from one it has
    misunderstood. The club's decision is that this feature is unavailable until
    a model is configured and answering, and that is now what it says.

    `grammar_parse` stays in `core.assistant`: it is what lets the golden set of
    sentences be tested in CI with no provider account, and it is still the
    deterministic reading used when a model has already produced something.
    """
    model = parser_from_config(hardware_config())
    if model is None:
        return None

    def either(text, context):
        reading = model(text, context)
        if reading is not None and reading.name != ANSWER:
            return reading
        # The model chose to answer in words rather than act. If the grammar can
        # see an actual command in the same sentence, that wins: "shorten at mark
        # 4" must shorten the course, however chatty the reply to it would be.
        fallback = grammar_parse(text, context)
        if fallback is None or reading is None:
            return fallback or reading
        # But a deterministic *question* does not beat a better question. Told
        # "shorten course", the grammar recognised the shape and could only ask
        # which mark; the model had already asked which mark **and named the one
        # the fleet was sailing to**, and that sentence was being thrown away.
        if resolve(fallback, context).status == NEEDS_CLARIFICATION:
            return reading
        return fallback

    return either


def _fail(message: str, status: int = 400):
    return jsonify({"ok": False, "error": message}), status


UNCONFIGURED = ("The Virtual Race Officer is not available: no interpreter is configured. "
                "An administrator sets one up in Settings, Virtual Race Officer. Until then "
                "the racing is run from the race sheet.")


def _refuse_without_an_interpreter():
    """No model, no page. The club's decision, and the honest one.

    Answering with the small built-in grammar instead looked like the app
    working and was not: it understands a handful of sentence shapes, and on a
    page that invites plain English that is indistinguishable from an app that
    cannot understand anything. Better to say so once than to be stupid all
    afternoon.
    """
    if interpreter_status(hardware_config()).get("grammar_only"):
        return jsonify({"ok": False, "error": UNCONFIGURED}), 503
    return None


def _refuse_without_permission():
    """The gate on all three: the page and both endpoints.

    Checked on the endpoints and not only on the page, because the page is not
    what protects anything -- a POST is a POST. Answered as 403 rather than a
    redirect so the phone shows the reason instead of a login form.
    """
    if current_user_can_race_remotely():
        return None
    return jsonify({"ok": False, "error": "This account does not have permission to run racing "
                                          "from the water. An administrator can enable it in "
                                          "Settings, Users."}), 403


def _suggested_course(target_minutes: float):
    """The course the app would recommend for the wind right now.

    Offered with a new race because the alternative is a race sheet with no
    course and somebody on the water having to know that a course is a separate
    step. The read-back names it, so agreeing to the read-back sets it: telling
    a person the course and then not using it was the worst of both.
    Best-effort -- no wind, no suggestion, and a suggestion that cannot be made
    never stops a race being created.
    """
    try:
        status = weather_runtime_status()
        wind = status.get("sample") or status.get("latest") or latest_weather_sample()
        if not wind or wind.get("twd") is None:
            return None
        twd = float(wind["twd"])
        tws = float(wind.get("tws") or 12.0)
        rows = recommend_courses_with_polar(twd, tws, float(target_minutes or 60.0),
                                            resolve_polar_path(None))
        if not rows:
            return None
        best = rows[0]
        return {"course_no": int(best["course_no"]), "length_nm": best.get("length_nm"),
                "twd": round(twd), "tws": round(tws, 1),
                "name": str(best.get("name") or "").strip()}
    except Exception:
        # A suggestion is a courtesy; failing to make one must not stop a race
        # being created.
        return None


# ---------------------------------------------------------------------------
# Checking the resolved command against the actual race
# ---------------------------------------------------------------------------

def _check_against_the_race(intent: str, resolved: dict):
    """Re-check every field that depends on data, after the interpreter.

    The interpreter knows about words. It does not know whether race 57 exists,
    or whether mark 4 is on its course, so those are asked here -- before the
    read-back, so the read-back cannot promise something that will then fail.
    Returns (resolved, error_message, field_the_error_is_about) -- the field so
    that a one-word correction to it can be understood as one.
    """
    race_id = resolved.get("race_id")
    race = get_race(int(race_id)) if race_id else None
    if race_id and not race:
        return resolved, f"There is no race #{int(race_id)}.", "race_id"

    if intent in ("create_race", "set_start_and_course") and resolved.get("series_name"):
        # The interpreter has a name; only the database has series. A name that
        # matches nothing is a question, not a race quietly created outside the
        # points -- which is a scoring error nobody would see until the season's
        # standings came out wrong.
        wanted = str(resolved["series_name"]).strip().lower()
        series = list_series()
        match = next((s for s in series if str(s["name"]).strip().lower() == wanted), None)
        if match is None:
            match = next((s for s in series if wanted in str(s["name"]).strip().lower()), None)
        if match is None:
            names = ", ".join(str(s["name"]) for s in series) or "none"
            return resolved, (f"There is no series called '{resolved['series_name']}'. "
                              f"The club's series are: {names}."), "series"
        resolved = dict(resolved, series_id=int(match["id"]), series_name=str(match["name"]))

    if intent == "shorten_course":
        options = course_shorten_options(_course_as_sailed(race))
        # A name is matched against names and an index against indices, never
        # both: "shorten at mark 1" was matching the mark *at index 1*, which on
        # course 1 is mark 8. It survived because the old code took the first
        # match and mark 1 happens to come first — on another course it would
        # have finished the fleet at a mark nobody named.
        wanted = str(resolved.get("at_mark", "")).strip().lower()
        if wanted:
            matching = [opt for opt in options
                        if str(opt["display"]).strip().lower() == wanted
                        or str(opt.get("label", "")).strip().lower() == wanted]
        else:
            matching = [opt for opt in options
                        if str(opt["index"]) == str(resolved.get("at_index", "")).strip()]
        match = matching[0] if matching else None
        if len(matching) > 1:
            # A course can pass the same mark three times, and "shorten at 4"
            # then names three different places to finish. Taking the first is
            # the one answer that is certainly wrong: the fleet has already
            # sailed past it. The mark they are *coming to* is the one meant, so
            # the leader's progress decides — and when nothing is tracking, the
            # app asks rather than guessing.
            ahead = [opt for opt in matching if opt["index"] >= _leader_has_rounded(race)]
            if len(ahead) == 1:
                match = ahead[0]
            elif ahead:
                match = ahead[0]
                resolved = dict(resolved, ambiguous_rounding=True)
            else:
                labels = ", ".join(o["label"] for o in matching)
                return resolved, (f"This course passes mark {resolved.get('at_mark')} "
                                  f"{len(matching)} times and the fleet is past them all. "
                                  f"Which rounding — {labels}?"), "at_mark"
            if not _leader_has_rounded(race, known=True):
                labels = ", ".join(o["label"] for o in matching)
                return resolved, (f"This course passes mark {resolved.get('at_mark')} "
                                  f"{len(matching)} times and nothing is reporting a position, "
                                  f"so I cannot tell which one the fleet is coming to. Which "
                                  f"rounding — {labels}?"), "at_mark"
        if not match:
            marks = ", ".join(str(o["display"]) for o in options) or "none"
            return resolved, (f"Mark {resolved.get('at_mark', resolved.get('at_index'))} is not "
                              f"on that course. Marks that can be shortened at: {marks}."), "at_mark"
        # Store the index: the display label can repeat on a course that passes
        # the same mark twice, and the index is what shortening actually takes.
        # The *label* goes in the read-back, so "rounding 3" is on screen before
        # anybody agrees to it.
        resolved = dict(resolved, at_index=int(match["index"]), at_mark=str(match["display"]),
                        at_label=str(match.get("label") or match["display"]))

    if intent == "set_custom_course":
        # The interpreter has mark names; only the app has marks. An unknown one
        # is a question listing the real ones -- a course sent to a fleet has to
        # be sailable, and a typo here is a leg to somewhere that is not there.
        try:
            resolved = dict(resolved, marks=validate_course_sequence_json(
                json.dumps(resolved.get("marks") or [])))
        except ValueError as exc:
            return resolved, (f"{exc} The club's marks are: "
                              + ", ".join(sorted(appstate.MARKS)) + "."), "marks"

    if intent == "add_entries" and resolved.get("scope") == "boat":
        # The interpreter has names; only the boat database has boats. An
        # unmatched name is a question, never the nearest boat: entering the
        # wrong one is a boat racing that nobody knows is racing.
        matched = []
        for typed in resolved.get("boat_names") or []:
            wanted = typed.strip().lower()
            boats = search_boats(typed)
            match = next((b for b in boats if str(b["boat_name"]).strip().lower() == wanted), None)
            if match is None:
                match = boats[0] if len(boats) == 1 else None
            if match is None:
                return resolved, (f"There is no boat called '{typed}' in the boat database, or "
                                  "the name matches more than one."), "boat"
            matched.append({"id": int(match["id"]), "name": str(match["boat_name"])})
        if not matched:
            return resolved, "Which boat should be added?", "boat"
        resolved = dict(resolved, boats=matched,
                        boat_names=[b["name"] for b in matched])

    if intent == "race_results" and not resolved.get("race_id"):
        wanted = str(resolved.get("race_name") or "").strip().lower()
        races = _recent_races(40)
        match = next((r for r in races if str(r["name"]).strip().lower() == wanted), None)
        if match is None:
            near = [r for r in races if wanted and wanted in str(r["name"]).strip().lower()]
            match = near[0] if near else None
        if match is None:
            recent = ", ".join(f"#{r['id']} {r['name']}" for r in races[:6]) or "none"
            return resolved, (f"I could not find a race called '{resolved.get('race_name')}'. "
                              f"The most recent races are: {recent}."), "race_name"
        resolved = dict(resolved, race_id=int(match["id"]), race_name=str(match["name"]))

    if intent == "set_start_and_course" and race is not None:
        # update_race_settings needs a course number; keep the one the race has
        # unless the command named a different one. Whether one *was* named is
        # recorded first, because filling the gap in here would otherwise be
        # indistinguishable afterwards from somebody choosing that course.
        resolved["course_chosen"] = resolved.get("course_no") is not None
        resolved.setdefault("course_no", int(race["course_no"]))
        # And the same for the time, for the same reason and with worse
        # consequences: `update_race_settings` writes every field it is given, so
        # a command that named only a course arrived with an empty start time and
        # **wiped the race's start**. The read-back said "course 29" and meant
        # "course 29, and no start time" -- there is nothing in that sentence a
        # person could have checked.
        resolved.setdefault("first_warning_time", str(row_get(race, "start_time", "") or ""))
    return resolved, "", ""


# ---------------------------------------------------------------------------
# Doing it
# ---------------------------------------------------------------------------

def _board_marks(course) -> list:
    """The course board: each mark and which hand it is left on.

    The same thing the race sheet and the clubhouse display show, and the thing
    a race officer on the water actually reads out. `board_marks` where the
    course has them -- a compound mark shows as its two corners there.
    """
    marks = (course or {}).get("board_marks") or (course or {}).get("marks") or []
    return [{"mark": str(m.get("mark", "")),
             "rounding": str(m.get("rounding", "") or "")[:1].lower()}
            for m in marks]


def _mark_distances() -> str:
    """How far and on what bearing between the marks races are actually sailed on.

    Every leg of every made-up course is one of these, so this is what somebody
    designing a course needs — and it is the app's own measurement, the same
    `haversine_nm` the leg analysis uses, rather than a calculation done from
    latitudes by whoever is reading them.

    Limited to the marks the fixed courses use: the outlying ones (the Gwylan
    Islands, the Causeway) are passage marks, and every pair of all twenty-four
    would be three hundred numbers nobody asked for.
    """
    used = []
    for course in appstate.COURSES:
        for mark in course.get("marks") or []:
            key = str(mark.get("mark") or "")
            if key and key not in used and key in appstate.MARKS:
                used.append(key)
    used = sorted(used)[:14]
    pairs = []
    for i, a in enumerate(used):
        for b in used[i + 1:]:
            one, two = appstate.MARKS[a], appstate.MARKS[b]
            if one.get("lat") is None or two.get("lat") is None:
                continue
            nm = haversine_nm(float(one["lat"]), float(one["lon"]),
                              float(two["lat"]), float(two["lon"]))
            course_deg = bearing_deg(float(one["lat"]), float(one["lon"]),
                                     float(two["lat"]), float(two["lon"]))
            pairs.append(f"{a}-{b} {round(nm, 2)} nm {round(course_deg)}°")
    if not pairs:
        return ""
    return (" Distance and bearing between the racing marks (the reverse leg is the same distance, "
            "bearing plus 180): " + ", ".join(pairs) + ".")


def _rating_line(boat) -> str:
    """A boat's two ratings as the club records them."""
    irc = row_get(boat, "irc_rating", None)
    ytc = row_get(boat, "ytc_rating", None)
    parts = [f"IRC {irc}" if irc is not None else "no IRC rating",
             f"YTC {ytc}" if ytc is not None else "no YTC rating"]
    return " and ".join(parts) + "."


def _rating_report(found: dict) -> str:
    """What the club knows about a boat's ratings, and where each figure is from.

    The database is what a race actually scores on, because an entry snapshots
    the rating when the boat is added; the listings are where it came from and
    either may have moved since. Somebody asking is usually asking because
    something does not look right, so all of it is said rather than the first
    figure found.
    """
    boat = found.get("boat")
    said = []
    if boat is not None:
        said.append(f"{boat['boat_name']}"
                    + (f" ({row_get(boat, 'sail_no', '')})" if row_get(boat, "sail_no", "") else "")
                    + " is in the boat database: " + _rating_line(boat))
    else:
        said.append(f"'{found.get('query')}' is not in the club's boat database.")

    for row in (found.get("irc") or [])[:3]:
        said.append(f"The IRC listing has {row.get('boat_name')} ({row.get('sail_no')}) "
                    f"at TCC {row.get('irc_rating')}"
                    + (f", certificate {row.get('irc_cert_no')}" if row.get("irc_cert_no") else "")
                    + ".")
    for row in (found.get("ytc") or [])[:3]:
        said.append(f"The YTC sheet has {row.get('boat_name')} ({row.get('sail_no')}) "
                    f"at {row.get('ytc_rating')}.")
    if found.get("errors"):
        said.append("(" + "; ".join(found["errors"]) + ")")
    if boat is None and (found.get("irc") or found.get("ytc")):
        said.append(f"Say 'add {found.get('query')} to the boat database' to add it.")
    elif boat is None:
        said.append("It is not in the IRC listing or the YTC sheet either.")
    return " ".join(said)


def _as_wind_speed(said: str):
    """A wind strength out of "12", "12 knots", "at 12 kn", or None."""
    found = re.search(r"(\d+(?:\.\d+)?)", str(said or ""))
    return float(found.group(1)) if found else None


def _look_up(resolved: dict, race_id_hint=None) -> str:
    """Answer a read-only question about the club's own data, in words.

    The app answers this itself rather than handing it back to the interpreter
    for a second round trip -- on a boat that would be another six seconds. It
    goes into the thread like anything else, so the *next* question can be
    answered from it.
    """
    topic = str(resolved.get("topic") or "").lower()
    query = str(resolved.get("query") or "").strip().lower()

    if topic == "marks":
        marks = appstate.MARKS
        # An exact mark wins outright: "where is O" must not answer with the
        # nine marks whose names happen to contain an o.
        exact = {k: v for k, v in marks.items() if query and k.lower() == query}
        wanted = exact or {k: v for k, v in marks.items() if not query or query in k.lower()
                           or query in str(v.get("name", "")).lower()}
        if not wanted:
            return f"There is no mark matching '{resolved.get('query')}'. The club's marks are: " \
                   + ", ".join(sorted(marks)) + "."
        said = "; ".join(
            f"{key} ({row.get('name')}) at {row.get('lat_text') or round(float(row['lat']), 5)} "
            f"{row.get('lon_text') or round(float(row['lon']), 5)}"
            for key, row in sorted(wanted.items()) if row.get("lat") is not None)
        chart = course_chart_config()
        line = (f" The start and finish line runs from the CHPSC bridge window "
                f"({round(float(chart['bridge_window_lat']), 5)}, "
                f"{round(float(chart['bridge_window_lon']), 5)}) to mark "
                f"{chart.get('start_finish_mark')}.") if not query else ""
        # And the distance and bearing between the racing marks, worked out by
        # the app. Asked for "a course with lots of reaching, about 20 nm", the
        # interpreter was given twenty-four latitudes and longitudes and had to
        # do the trigonometry itself: it ran out of room mid-calculation and the
        # page reported a failure. This is the same arithmetic, done once, in the
        # place that already knows how.
        table = _mark_distances() if not query else ""
        return f"{len(wanted)} mark(s): {said}.{line}{table}"

    if topic == "courses":
        rows = sorted(appstate.COURSES, key=lambda c: int(c["course_no"]))
        said = ", ".join(f"{c['course_no']} ({c.get('length_nm')} nm)" for c in rows[:70])
        return f"The club has {len(rows)} fixed courses: {said}."

    if topic == "course":
        number = resolved.get("course_no")
        course = appstate.COURSE_BY_NO.get(int(number)) if number else None
        if course is None:
            return (f"There is no course {number}." if number
                    else "Which course? Say 'look up course 17'.")
        wind = _wind_now()
        if not wind:
            board = ", ".join(f"{m['mark']}{str(m.get('rounding', ''))[:1]}"
                              for m in course.get("marks", []))
            return (f"Course {number} is {course.get('length_nm')} nm: {board}. With no wind "
                    "reading the legs cannot be analysed.")
        analysis = analyse_course_with_wind(course, wind[0], wind[1])
        minutes = analysis.get("predicted_minutes")
        return (f"Course {number} is {course.get('length_nm')} nm"
                + (f", about {round(float(minutes))} minutes round in this wind" if minutes else "")
                + f". Legs: {_leg_shape(analysis)}.")

    if topic == "boats":
        boats = search_boats(resolved.get("query") or "")
        if not boats:
            return f"No active boat matches '{resolved.get('query')}'."
        said = ", ".join(str(b["boat_name"]) + (f" ({row_get(b, 'sail_no', '')})"
                                                if row_get(b, "sail_no", "") else "")
                         for b in boats[:80])
        return f"{len(boats)} active boat(s): {said}."

    if topic in ("polar", "sails"):
        # The boat the course recommendation is timed against, and what it would
        # be carrying. A race officer choosing a course asks both.
        path = resolve_polar_path(None)
        if topic == "polar":
            rows = load_polar(path)
            wind = _wind_now()
            asked = _as_wind_speed(query)
            if asked is None and wind:
                asked = wind[1]
            if asked is not None and rows:
                rows = [min(rows, key=lambda r: abs(float(r.get("tws") or 0) - float(asked)))]
            said = []
            for row in rows:
                points = ", ".join(f"{int(p['twa'])}° {p['bsp']} kn" for p in row["points"]
                                   if p.get("twa"))
                said.append(f"at {int(row['tws'])} kn true: {points}")
            here = ""
            if wind:
                target = target_speed_info_for(90.0, wind[1], rows)
                if target:
                    here = (f" On a beam reach in the {round(wind[1], 1)} knots blowing now that "
                            f"is about {round(float(target.get('bsp') or 0), 2)} knots.")
            return (f"The polar in use is {path.name} — boat speed by true wind angle for each "
                    f"wind strength: {'; '.join(said)}.{here}")
        chart_path = resolve_sail_chart_path_for_polar(path)
        chart = load_sail_chart(chart_path)
        angles = [int(a) for a in (chart.get("twas") or [])]
        rows = list(chart.get("rows") or [])
        # One wind strength, not fourteen. The question is "what would we carry
        # now" or "at twelve knots", and the whole chart is four hundred words on
        # a phone. `query` names a strength; failing that, the wind blowing.
        asked = _as_wind_speed(query)
        if asked is None and _wind_now():
            asked = _wind_now()[1]
        if asked is not None and rows:
            rows = [min(rows, key=lambda r: abs(float(r.get("tws") or 0) - float(asked)))]
        lines = []
        for row in rows:
            # Where the sail *changes*, not one entry per angle: the chart has
            # twenty-nine columns and mostly repeats itself, and "J1 out to 60,
            # then A2" is what somebody actually wants read back.
            spans, last = [], None
            for index, sail in enumerate(row.get("sails") or []):
                if index >= len(angles) or not sail:
                    continue
                if sail != last:
                    spans.append([sail, angles[index], angles[index]])
                    last = sail
                else:
                    spans[-1][2] = angles[index]
            if spans:
                lines.append(f"at {int(row['tws'])} kn: "
                             + ", ".join(f"{s} from {a}° to {b}°" for s, a, b in spans))
        if not lines:
            return f"There is no sail chart for {path.name}."
        return (f"The sail chart for {path.name} is {chart_path.name} — the sail for each "
                f"wind strength and angle: {'; '.join(lines)}.")

    if topic == "series":
        # Newest first, with how many races each has and when it was last
        # sailed, so "put it in the latest series" has something to be latest by.
        # A name alone cannot say which of five is the one running now.
        rows = list_series()
        if not rows:
            return "The club has no series set up."
        said = []
        with get_db() as db:
            for row in rows:
                stats = db.execute(
                    "SELECT COUNT(*) AS races, MAX(COALESCE(NULLIF(start_time, ''), created_at))"
                    " AS latest FROM races WHERE series_id = ?", (int(row["id"]),)).fetchone()
                when = str(stats["latest"] or "")[:10]
                said.append(f"{row['name']} ({stats['races']} race(s)"
                            + (f", last {when}" if when else ", none yet") + ")")
        return ("The club's series, most recently updated first: " + "; ".join(said)
                + ". The first named is the one being raced now.")

    if topic == "races":
        rows = _recent_races(20)
        said = "; ".join(f"#{r['id']} '{r['name']}'"
                         + (f" {str(r['start_time'])[:16].replace('T', ' ')}"
                            if r["start_time"] else "") for r in rows)
        return f"The most recent races are: {said}." if said else "There are no races yet."

    if topic == "fleet":
        race = get_race(int(race_id_hint)) if race_id_hint else _working_race(current_actor())
        facts = _fleet_facts(race)
        return facts[0] if facts else "No boat in that race is reporting a position."

    return f"There is nothing to look up under '{topic}'."


def _boat_name(result_row: dict) -> str:
    """The boat's name out of a result row, whichever half of it carries one."""
    for key in ("boat", "entry"):
        holder = (result_row or {}).get(key)
        if holder is None:
            continue
        name = str(row_get(holder, "boat_name", "") or "").strip()
        if name:
            return name
    return "?"


def _settings_from(race, **changes) -> RaceSettings:
    """The race as it stands, with only what the command actually named changed.

    `RaceSettings` is the whole Course & start form, and `update_race_settings`
    writes every field of it — which is right for a form, where every field
    was on screen. A command names one or two things, so building a `RaceSettings`
    from those alone silently cleared everything else: "use course 29" wiped the
    race's **start time**, its **series** (so it scored in no standings), its
    class, its notes and its finish line. The read-back said "course 29", and
    there was nothing in that sentence anybody could have checked.
    """
    settings = RaceSettings(
        name=str(row_get(race, "name", "") or ""),
        class_name=str(row_get(race, "class_name", "") or ""),
        start_time=str(row_get(race, "start_time", "") or ""),
        series_id=row_get(race, "series_id", None),
        notes=str(row_get(race, "notes", "") or ""),
        polar_file=str(row_get(race, "polar_file", "") or ""),
        finish_line_key=str(row_get(race, "finish_line_key", "") or ""),
        course_no=int(row_get(race, "course_no", 0) or 0),
        pursuit_rating="YTC" if str(row_get(race, "rating_rule", "") or "") == "YTC" else "IRC",
        pursuit_duration_min=row_get(race, "pursuit_duration_min", None),
        # A save that carries the race's own course forward is not a choice of
        # course; only a command that names one is.
        choosing_course=False,
    )
    for field, value in changes.items():
        setattr(settings, field, value)
    return settings


def _save_start_and_course(db, race, settings: RaceSettings, actor: str):
    """Save a race's start time and course, whichever kind of race it is.

    A pursuit is saved by a different function, and picking the wrong one is a
    refusal rather than a wrong answer -- which is exactly what happened to a
    pursuit created from the water: the race was made, then "use the pursuit
    update instead", and it was left with no start time at all.
    """
    if str(row_get(race, "race_type", "standard") or "standard") == "pursuit":
        return update_pursuit_settings(db, race, settings, actor=actor)
    return update_race_settings(db, race, settings, actor=actor)


def _execute(intent: str, resolved: dict, actor: str) -> dict:
    """Carry out a confirmed command through the ordinary service functions."""
    if intent == "create_race":
        spec = RaceSpec(
            name=resolved.get("name", ""),
            race_type=resolved.get("race_type", "standard"),
            pursuit_duration_min=resolved.get("pursuit_duration_min"),
            add_all_active=bool(resolved.get("add_all_active")),
            series_id=resolved.get("series_id"),
            # Detection is on by default; auto-confirm is not, and this is the
            # case that overrides it. A race created from the water has nobody in
            # the hut to press Finish or to approve a detection, so it has to
            # record its own -- which is the whole reason the page exists.
            gps_finish_enabled=True,
            gps_auto_confirm=True,
        )
        with get_db() as db:
            created = create_race(db, spec, actor=actor)
            race = get_race(created.race_id)
            # A create that named a time is two operations, not a second way of
            # creating a race: the same update the Course & start tab performs.
            course_no = resolved.get("suggested_course_no")
            if resolved.get("first_warning_time") or course_no:
                # Built from the race as just created, so the series the spec
                # asked for survives this second save -- it was being written
                # and then immediately cleared.
                _save_start_and_course(db, race, _settings_from(
                    race,
                    course_no=int(course_no or race["course_no"]),
                    start_time=resolved.get("first_warning_time", "")
                    or str(row_get(race, "start_time", "") or ""),
                    # Setting a time needs a course number to write and is not a
                    # choice of course. Without this, a race created with a time
                    # and no suggestion read as "course chosen" while the reply
                    # said "course not yet set" -- both from the same command.
                    choosing_course=bool(course_no),
                ), actor)
                race = get_race(created.race_id)
        message = (f"Race #{created.race_id} '{race['name']}' created")
        if resolved.get("first_gun_time"):
            message += f". First gun {race_first_start_time(race)[11:16]}"
        if created.entries_added:
            message += f", {created.entries_added} boat(s) added"
        suggested = resolved.get("suggested_course_no")
        message += (f", course {suggested}." if suggested
                    else ". Course not yet set — say 'use course 4' or 'recommend a course'.")
        # Said out loud, because it is a real difference in how the race will be
        # run and nobody asked for it in the sentence: from here the app watches
        # the fleet across the line and records the finishes itself.
        message += (" GPS finishes are armed and will be recorded automatically"
                    if track.track_config().get("enabled")
                    else " GPS finishes are armed for when tracking is switched on")
        message += "; the Entries tab can turn that off."
        return {"message": message, "race_id": created.race_id,
                "links": {"race_sheet": f"/admin/race/{created.race_id}"}}

    if intent == "set_start_and_course":
        race_id = int(resolved["race_id"])
        with get_db() as db:
            race = get_race(race_id)
            changes = {"course_no": resolved.get("course_no"),
                       "start_time": resolved.get("first_warning_time", ""),
                       "choosing_course": bool(resolved.get("course_chosen"))}
            if resolved.get("series_id"):
                changes["series_id"] = int(resolved["series_id"])
            result = _save_start_and_course(db, race, _settings_from(race, **changes), actor)
        race = get_race(race_id)
        bits = []
        if resolved.get("first_gun_time"):
            bits.append(f"first gun {race_first_start_time(race)[11:16]}")
        if resolved.get("course_chosen") and resolved.get("course_no") is not None:
            bits.append(f"course {resolved['course_no']}")
        if resolved.get("series_name"):
            bits.append(f"series {resolved['series_name']}")
        return {"message": f"Race #{race_id} updated: " + ", ".join(bits) + ".",
                "race_id": race_id, "warnings": result.warnings}

    if intent == "add_entries":
        race_id = int(resolved["race_id"])
        kind = resolved.get("scope", "all_active")
        if kind == "boat":
            # One instruction, several boats: each goes through the same service
            # call, one at a time, so a name that turns out to be entered already
            # says so instead of stopping the rest. "Add Sgrech Bach and Mojito"
            # used to enter the first and drop the second without a word.
            said, count = [], 0
            with get_db() as db:
                for boat in resolved.get("boats") or []:
                    outcome = add_entries(db, get_race(race_id),
                                          EntryScope(kind="boat", boat_id=int(boat["id"])),
                                          actor=actor)
                    count += outcome.added
                    said.append(str(boat["name"])
                                + ("" if outcome.added else " (already entered)"))
            return {"message": ", ".join(said) + f" — {count} added to race #{race_id}.",
                    "race_id": race_id, "added": count}
        scope = EntryScope(kind=kind, same_as_race_id=resolved.get("same_as_race_id"))
        with get_db() as db:
            added = add_entries(db, get_race(race_id), scope, actor=actor)
        if added.kind == "same_as":
            source = resolved.get("same_as_race_id")
            message = (f"{added.added} boat(s) added to race #{race_id}, the same as race "
                       f"#{source}." if added.added
                       else f"Race #{source} has no boats to copy, so none were added.")
            return {"message": message, "race_id": race_id, "added": added.added}
        if not added.added:
            message = f"Every active boat is already entered in race #{race_id}."
        else:
            message = f"{added.added} boat(s) added to race #{race_id}."
        return {"message": message, "race_id": race_id, "added": added.added}

    if intent == "shorten_course":
        race_id = int(resolved["race_id"])
        with get_db() as db:
            call = shorten_course_at(db, get_race(race_id), resolved["at_index"], actor=actor)
        return {"message": f"Shortened course called at mark {call.mark}. Two horn blasts and "
                           "the announcement are going out now.",
                "race_id": race_id, "mark": call.mark}

    if intent == "postpone_race":
        race_id = int(resolved["race_id"])
        with get_db() as db:
            call = postpone_race(db, get_race(race_id), resolved.get("kind") or "AP", actor=actor)
        return {"message": f"{call.flag} up on race #{race_id}. Two horn blasts are going out now, "
                           "and no start signal will sound until it comes down.",
                "race_id": race_id, "flag": call.flag, "kind": call.kind}

    if intent == "resume_race":
        race_id = int(resolved["race_id"])
        with get_db() as db:
            call = resume_race(db, get_race(race_id), actor=actor,
                               lower_at=resolved.get("lower_at") or None,
                               warning_time=resolved.get("warning_time") or None)
        # The times it actually used, not the ones proposed: a confirmation
        # agreed to after its moment has passed rolls to the next whole minute,
        # and the person reading this needs the real answer.
        return {"message": f"Race #{race_id}: {call.announcement}",
                "race_id": race_id, "warning_time": call.warning_time,
                "ends_at": call.ends_at}

    if intent == "set_custom_course":
        race_id = int(resolved["race_id"])
        with get_db() as db:
            count = set_custom_course(db, get_race(race_id), resolved["marks"], actor=actor)
        race = get_race(race_id)
        course = course_for_race(race)
        board = " ".join(f"{m['mark']}{str(m.get('rounding', ''))[:1]}"
                         for m in (course.get("marks") or []))
        expected = _expected_time_round(race)
        return {"message": f"Made-up course set on race #{race_id}: {board} — {count} marks, "
                           f"{course.get('length_nm')} nm"
                           + (f", about {expected[0]} minutes round in this wind." if expected
                              else ".")
                           + " Say 'use course 4' to go back to a numbered one.",
                "race_id": race_id}

    if intent == "boat_rating":
        found = find_boat_ratings(resolved["boat"], listing_config())
        return {"message": _rating_report(found)}

    if intent == "add_boat":
        found = find_boat_ratings(resolved["boat"], listing_config())
        boat = found.get("boat")
        if boat is not None:
            # Never overwritten: the record wins and the caller is told what it
            # already says. Taking a listing row over an existing rating is a
            # decision to make with both in front of you, on the boats page.
            return {"message": f"{boat['boat_name']} is already in the boat database. "
                               + _rating_line(boat)
                               + " Change it on the Boats page if it is wrong."}
        irc = (found.get("irc") or [None])[0]
        ytc = (found.get("ytc") or [None])[0]
        if not irc and not ytc:
            return {"message": f"'{resolved['boat']}' is not in the IRC listing or the YTC sheet, "
                               "so there is nothing to add it from."
                               + (" (" + "; ".join(found["errors"]) + ")" if found["errors"] else "")}
        name = (irc or ytc).get("boat_name") or ""
        sail = (irc or ytc).get("sail_no") or ""
        boat_id, what = insert_boat_from_listings(name, sail, irc=irc, ytc=ytc)
        audit("boat added from the water" if what == "created" else "boat add skipped",
              f"#{boat_id} {name} {sail}".strip())
        if what == "exists":
            return {"message": f"{name} was already in the boat database, so it was left alone."}
        added = []
        if irc and irc.get("irc_rating"):
            added.append(f"IRC {irc['irc_rating']}")
        if ytc and ytc.get("ytc_rating"):
            added.append(f"YTC {ytc['ytc_rating']}")
        return {"message": f"{name}"
                           + (f" ({sail})" if sail else "")
                           + " added to the boat database"
                           + (" with " + " and ".join(added) if added else " with no rating")
                           + ". It can be entered in a race now."}

    if intent == "look_up":
        return {"message": _look_up(resolved, race_id_hint=resolved.get("race_id"))}

    if intent == "race_results":
        race_id = int(resolved["race_id"])
        race = get_race(race_id)
        entries = get_entries(race_id)
        groups = compute_dual_results(race, entries)
        parts = []
        for rating in ("irc", "ytc"):
            for table in (groups.get(rating) or {}).get("tables", []) or []:
                placed = [row for row in table.get("rows", []) or [] if row.get("rank")]
                if not placed:
                    continue
                placed.sort(key=lambda row: row["rank"])
                # A result row carries the entry and the boat, not a name: asked
                # for the results of the night race, this raised and the page said
                # only "the hut did not answer (500)".
                order = ", ".join(f"{row['rank']}. {_boat_name(row)}" for row in placed[:8])
                label = str(table.get("title") or table.get("class_name") or "").strip()
                parts.append(f"{rating.upper()}{f' {label}' if label else ''}: {order}")
        if not parts:
            # A race with nobody placed is not a results failure: it may not have
            # been sailed, or the finishes may not be in yet. Say which.
            racing = sum(1 for e in entries if e["status"] == "RACING")
            return {"message": f"Race #{race_id} '{race['name']}' has no results yet — "
                               f"{len(entries)} entered, {racing} still racing.",
                    "race_id": race_id}
        return {"message": f"Race #{race_id} '{race['name']}' — " + "; ".join(parts) + ".",
                "race_id": race_id}

    if intent == "race_status":
        race_id = int(resolved["race_id"])
        race = get_race(race_id)
        entries = get_entries(race_id)
        racing = sum(1 for e in entries if e["status"] == "RACING")
        finished = sum(1 for e in entries if e["finish_time"] or e["status"] == "FINISHED")
        warning = str(row_get(race, "start_time", "") or "")
        parts = [f"Race #{race_id} '{race['name']}'"]
        parts.append(f"first gun {race_first_start_time(race)[11:16]}" if warning
                     else "no start time set")
        if row_get(race, "course_set", 1):
            parts.append(f"course {row_get(race, 'course_no', '')}")
            # How long it should take is the other half of "where are we?", and
            # the app has always known it -- it is what the course recommendation
            # ranks by. Asked outright, the interpreter reached for this report
            # and the report did not say.
            expected = _expected_time_round(race)
            if expected:
                minutes, finishing = expected
                parts.append(f"about {minutes} minutes round in this wind"
                             + (f", first boats about {finishing}" if finishing else ""))
        else:
            parts.append("course not set")
        parts.append(f"{len(entries)} entered, {racing} racing, {finished} finished")
        if row_get(race, "shortened_at_mark", ""):
            parts.append(f"shortened at mark {row_get(race, 'shortened_at_mark', '')}")
        return {"message": "; ".join(parts) + ".", "race_id": race_id,
                "entries": len(entries), "racing": racing, "finished": finished}

    raise RaceValidationError(f"{intent} is not something this app will do.")


# ---------------------------------------------------------------------------
# Interpret
# ---------------------------------------------------------------------------

@app.route("/api/assistant/command", methods=["POST"])
@app.route("/admin/api/assistant/command", methods=["POST"])
def assistant_command():
    """Interpret a typed command and say back what it would do. Changes nothing."""
    refused = _refuse_without_permission() or _refuse_without_an_interpreter()
    if refused:
        return refused
    init_db()
    payload = request.get_json(silent=True) or {}
    text = str(payload.get("text") or "").strip()
    if not text:
        return _fail("Say what you would like to do.")
    client_command_id = str(payload.get("client_command_id") or "").strip() or None

    # The same command id twice is the same command, not a second one: a phone
    # on 4G retries, and the retry must not produce a second pending token.
    if client_command_id:
        with get_db() as db:
            row = db.execute("SELECT * FROM assistant_commands WHERE client_command_id = ?",
                             (client_command_id,)).fetchone()
        if row:
            if row["status"] == "done":
                return jsonify({"ok": True, "status": "done", "repeat": True,
                                **json.loads(row["result_json"] or "{}")})
            return jsonify({"ok": True, "status": NEEDS_CONFIRMATION, "repeat": True,
                            "intent": row["intent"], "readback": row["readback"],
                            "resolved": json.loads(row["resolved_json"] or "{}"),
                            "pending_token": row["token"]})

    actor = current_actor()

    # Agreeing to what is on screen, in words. The Yes button is not the only
    # way somebody says yes, and with a proposal waiting a typed "yes lets do
    # that" was read as a fresh instruction -- which is how agreement to create
    # a race became a course change to a race that had already finished.
    waiting = _pending_command(actor)
    if waiting is not None:
        if reads_as_yes(text):
            # The pending row itself becomes the record of what was done, so this
            # turn only has to say that the yes was heard.
            _remember(actor, text, waiting["intent"], "Carrying that out.", "answered")
            return _carry_out(waiting)
        if reads_as_no(text):
            _remember(actor, text, waiting["intent"], "Left alone — nothing was changed.",
                      "answered")
            return _dismiss(waiting)

    context = _context(actor)
    if waiting is not None:
        context.pending_readback = str(waiting["readback"] or "")
        context.pending_intent = str(waiting["intent"] or "")
    # A question the app asked is answered first, before this sentence is read as
    # a command in its own right. "standard" is a whole command once you know
    # what it is answering, and nothing at all if you do not.
    intent = _reply_to_open_question(actor, text)
    if intent is None:
        intent = parse_command(text, context, _parser())
    answer = resolve(intent, context)

    if answer.status == NOT_UNDERSTOOD:
        question = answer.question
        # "I did not understand that" is the same sentence whether nobody could
        # read what was typed or the interpreter failed to answer at all, and on
        # the water those want completely different responses -- say it again, or
        # stop trying and use the race sheet. The notice on the page says which,
        # but only to somebody who reloads it.
        failure = interpreter_status(hardware_config()).get("error") or ""
        if failure:
            question += f" (The interpreter did not answer: {failure}.)"
        _remember(actor, text, "", question, "not_understood")
        return jsonify({"ok": True, "status": NOT_UNDERSTOOD, "question": question})
    if answer.status == ANSWERED:
        # Words. Nothing is pending, nothing can be confirmed, and there is no
        # branch anywhere that could execute this.
        _remember(actor, text, ANSWER, answer.answer, "answered")
        return jsonify({"ok": True, "status": ANSWERED, "answer": answer.answer})
    if answer.status == NEEDS_CLARIFICATION:
        _remember(actor, text, answer.intent, answer.question, "asked",
                  resolved={"arguments": dict(intent.arguments) if intent else {},
                            "field": answer.clarify_field, "options": answer.options})
        return jsonify({"ok": True, "status": NEEDS_CLARIFICATION, "intent": answer.intent,
                        "question": answer.question, "options": answer.options})

    resolved, error, field = _check_against_the_race(answer.intent, dict(answer.resolved))
    if error:
        _remember(actor, text, answer.intent, error, "asked",
                  resolved={"arguments": resolved, "field": field, "options": []})
        return jsonify({"ok": True, "status": NEEDS_CLARIFICATION, "intent": answer.intent,
                        "question": error})

    readback = answer.readback
    # A name matched against the club's own records is read back in the club's
    # spelling, not as it was typed: "add mojito" is agreed to as "Add Mojito",
    # which is the boat that will actually be entered.
    # The read-back must say which rounding, on a course that passes the mark
    # more than once: "shorten at 4" on a course with three 4s is three
    # different finishes, and the label is the only thing that tells them apart.
    if answer.intent == "shorten_course" and resolved.get("at_label"):
        typed = str(answer.resolved.get("at_mark") or "")
        label = str(resolved["at_label"])
        if typed and label and typed != label:
            readback = readback.replace(f"mark {typed}", f"mark {label}", 1)
        if resolved.get("ambiguous_rounding"):
            readback += (" This course passes it more than once; this is the next one the fleet "
                         "comes to.")
    for field in ("series_name", "at_mark"):
        typed = str(answer.resolved.get(field) or "")
        matched = str(resolved.get(field) or "")
        if typed and matched and typed != matched:
            readback = readback.replace(typed, matched, 1)
    for typed, matched in zip(answer.resolved.get("boat_names") or [],
                              resolved.get("boat_names") or []):
        if typed and matched and typed != matched:
            readback = readback.replace(typed, matched, 1)
    if answer.intent == "create_race":
        suggestion = _suggested_course(resolved.get("target_length_min")
                                       or resolved.get("pursuit_duration_min") or 60.0)
        if suggestion:
            resolved["suggested_course_no"] = suggestion["course_no"]
            readback += (f" Course {suggestion['course_no']}"
                         + (f" ({suggestion['length_nm']} nm)" if suggestion.get("length_nm") else "")
                         + f", chosen for the wind now ({suggestion['twd']}°T, "
                         f"{suggestion['tws']} kn). Say no if you would rather pick your own.")

    if answer.intent in READ_ONLY_INTENTS:
        try:
            result = _execute(answer.intent, resolved, actor)
        except RaceValidationError as exc:
            return _fail(exc.message)
        # In the thread like everything else: "and how many are still out?" is a
        # perfectly ordinary thing to say after a status report.
        _remember(actor, text, answer.intent, result.get("message", ""), "done")
        return jsonify({"ok": True, "status": "done", "intent": answer.intent,
                        "header": _header_for(result), **result})

    now = datetime.now()
    token = "pc_" + secrets.token_urlsafe(16)
    with get_db() as db:
        # These rows live in the race database, which is backed up and shipped
        # off-site, so they are not allowed to accumulate for ever. A fortnight
        # is long past any use as a record; the activity log keeps the history.
        db.execute("DELETE FROM assistant_commands WHERE created_at < ?",
                   ((now - timedelta(days=14)).isoformat(timespec="seconds"),))
        cursor = db.execute(
            "INSERT INTO assistant_commands (token, client_command_id, actor, text, intent,"
            " resolved_json, readback, status, created_at, expires_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)",
            (token, client_command_id, actor, text, answer.intent, json.dumps(resolved),
             readback, now.isoformat(timespec="seconds"),
             (now + timedelta(seconds=PENDING_TTL_SECONDS)).isoformat(timespec="seconds")),
        )
        new_id = int(cursor.lastrowid)
        db.commit()
    # Only one proposal is live at a time: a second read-back leaves the first
    # still confirmable, and a Yes meant for what is on screen must not carry
    # out something said two minutes ago.
    _supersede_pending(actor, new_id)
    audit("assistant command proposed", f"{answer.intent} · {text[:120]}")
    return jsonify({"ok": True, "status": NEEDS_CONFIRMATION, "intent": answer.intent,
                    "readback": readback, "resolved": resolved,
                    "pending_token": token, "warnings": answer.warnings})


# ---------------------------------------------------------------------------
# Confirm
# ---------------------------------------------------------------------------

def _dismiss(row):
    with get_db() as db:
        db.execute("UPDATE assistant_commands SET status = 'dismissed' WHERE id = ?",
                   (row["id"],))
        db.commit()
    return jsonify({"ok": True, "status": "dismissed", "intent": row["intent"]})


def _carry_out(row):
    """Do what a pending, agreed-to command says. The only path to an action.

    Reached from the Yes button and from somebody typing "yes" -- one function,
    because two ways of agreeing must not become two ways of acting.
    """
    if datetime.now() > datetime.fromisoformat(row["expires_at"]):
        with get_db() as db:
            db.execute("UPDATE assistant_commands SET status = 'expired' WHERE id = ?",
                       (row["id"],))
            db.commit()
        return jsonify({"ok": True, "status": "expired", "intent": row["intent"],
                        "question": "That command is older than five minutes, so it was not "
                                    "carried out. Say it again if it still applies."})

    resolved = json.loads(row["resolved_json"] or "{}")
    # Checked once before the read-back and again now: a course can be changed,
    # or a race deleted, in the time between reading and agreeing.
    resolved, error, field = _check_against_the_race(row["intent"], resolved)
    if error:
        _remember(row["actor"] or current_actor(), row["text"], row["intent"], error, "asked",
                  resolved={"arguments": resolved, "field": field, "options": []})
        return jsonify({"ok": True, "status": NEEDS_CLARIFICATION, "intent": row["intent"],
                        "question": error})
    try:
        result = _execute(row["intent"], resolved, row["actor"] or current_actor())
    except RaceValidationError as exc:
        return _fail(exc.message)

    with get_db() as db:
        db.execute("UPDATE assistant_commands SET status = 'done', result_json = ?,"
                   " executed_at = ? WHERE id = ?",
                   (json.dumps(result), datetime.now().isoformat(timespec="seconds"), row["id"]))
        db.commit()
    audit("assistant command confirmed", f"{row['intent']} · {row['readback'][:160]}")
    # The strip at the top of the page moves with what was just done: a start
    # put back twenty minutes must not leave a countdown running to the old gun.
    return jsonify({"ok": True, "status": "done", "intent": row["intent"],
                    "header": _header_for(result), **result})


@app.route("/api/assistant/command/confirm", methods=["POST"])
@app.route("/admin/api/assistant/command/confirm", methods=["POST"])
def assistant_command_confirm():
    """Carry out a command that has been read back and agreed to."""
    refused = _refuse_without_permission() or _refuse_without_an_interpreter()
    if refused:
        return refused
    init_db()
    payload = request.get_json(silent=True) or {}
    token = str(payload.get("pending_token") or "").strip()
    if not token:
        return _fail("Which command? Send the pending_token from the read-back.")
    with get_db() as db:
        row = db.execute("SELECT * FROM assistant_commands WHERE token = ?", (token,)).fetchone()
    if not row:
        return _fail("That command was not found. Say it again.", 404)

    # Answering an already-executed command with its original result is what
    # makes a retry safe over a connection that drops mid-reply.
    if row["status"] == "done":
        return jsonify({"ok": True, "status": "done", "repeat": True,
                        "intent": row["intent"], **json.loads(row["result_json"] or "{}")})
    if row["status"] == "dismissed":
        return jsonify({"ok": True, "status": "dismissed", "intent": row["intent"]})
    if row["status"] != "pending":
        # Questions asked, answers given and sentences nobody understood share
        # this table so the conversation can be read back. None of them is a
        # command anybody agreed to, so none of them can be carried out.
        return _fail("There is nothing to confirm for that. Say it again.", 404)

    if payload.get("confirm") is False:
        return _dismiss(row)
    return _carry_out(row)

# ---------------------------------------------------------------------------
# The page
# ---------------------------------------------------------------------------

@app.route("/vro")
@app.route("/admin/vro")
# The club calls this the Virtual Race Officer; it was "On the water" until
# v0.264, and briefly /vrm before the club's own abbreviation was settled as
# VRO. Every address still answers: they are on phones already, and a bookmark
# that 404s at sea helps nobody.
@app.route("/vrm")
@app.route("/admin/vrm")
@app.route("/onwater")
@app.route("/admin/onwater")
def onwater_page():
    """The one-handed command page for somebody running racing from a boat."""
    if not current_user_can_race_remotely():
        return render_template("onwater_denied.html"), 403
    init_db()
    actor = current_actor()
    race = _working_race(actor)
    # A proposal outstanding when the page reloads is still outstanding: the Yes
    # button comes back live rather than the command being quietly stranded.
    waiting = _pending_command(actor)
    return render_template(
        "onwater.html",
        current_race=race,
        assistant_status=interpreter_status(hardware_config()),
        thread=_thread_for(actor),
        pending_token=str(waiting["token"]) if waiting is not None else "",
        # The two things somebody on the water looks at without asking: how long
        # until the gun, and what course the fleet is being sent round.
        header=_header_state(race),
        course=course_for_race(race) if race is not None else None,
        # The chart draws itself from these; nothing is fetched. Leaflet is
        # deliberately not loaded here -- static/course_map.js falls back to a
        # self-contained SVG, and a phone on a boat should not be pulling map
        # tiles over the same 4G the hut is already using.
        marks_data=track.race_marks(race) if race is not None else appstate.MARKS,
        course_chart={**course_chart_config(),
                      **(track.race_chart_line(race) if race is not None else {})},
        course_shortened=bool(row_get(race, "shortened_at_mark", "")) if race is not None else False,
    )
