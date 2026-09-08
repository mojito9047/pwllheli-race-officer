"""Turning a typed sentence into a command the race office would recognise.

The split this module exists to hold: **something interprets, Python decides.**
A model (or the small grammar below) turns "start a race at 11, hour long" into
a named intent with arguments, and nothing else. Every rule -- what a race is,
when the gun follows the warning, whether a mark is on the course -- stays in
`core.raceadmin` and is applied afterwards, to the model's output as untrusted
input.

Nothing here executes anything. `resolve` produces a read-back for a person to
confirm, or a question when the sentence did not carry enough to act on. The
caller executes only after that confirmation, through the same service functions
the race sheet uses.

Two parsers are provided and the seam between them is the point:

* `grammar_parse` is deterministic, needs no network and costs nothing. It
  covers the handful of commands worth having when the hut cannot reach a model
  at all -- which is precisely when nobody is in the hut to press the buttons.
  It is also what makes the tests below run in CI with no provider account.
* A model parser is a function of the same shape, added when a provider is
  configured. `parse_command` takes whichever it is given, so switching model,
  provider or gateway is a caller's decision rather than an edit here.

The two ambiguities the club's own vocabulary carries are resolved here rather
than left to a model's judgement, because both are silent when wrong:

1. **"Start at 11" is the gun, not the warning signal.** The stored column is
   the first warning signal and the gun is five minutes later. A read-back that
   shows only one of them cannot be checked by the person reading it, so both
   always appear.
2. **"An hour long" means different things** to a pursuit (its actual period)
   and to a standard race (a target used to recommend a course). With the race
   type unknown, that is a question, not a guess.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from typing import Any, Callable, Dict, List, Optional, Tuple

# The one import from `core`, and deliberately of a module that imports nothing
# itself: the names of the race signals. This module still interprets and decides
# nothing -- what it must not import is the service layer that acts.
from core.racesignals import POSTPONEMENT_KINDS, normalise_postponement_kind
from core.timeutils import next_whole_minute

# The gun follows the first warning signal by five minutes (RRS 26). One place,
# because every read-back and every resolved time is derived from it.
WARNING_TO_GUN = timedelta(minutes=5)

NEEDS_CONFIRMATION = "needs_confirmation"
NEEDS_CLARIFICATION = "needs_clarification"
NOT_UNDERSTOOD = "not_understood"
# A reply in words: the interpreter understood and there is nothing to do. Asked
# about the wind, or told "I'd like to talk about the race", the honest answer is
# a sentence -- and answering "I did not understand that" to a sentence that was
# understood perfectly is what made this page feel stupid.
ANSWERED = "answered"

# A pseudo-tool for exactly that. It is not in TOOLS -- nothing executes it, and
# `_execute` has no branch for it -- but it passes `parse_command` so a parser
# can say something rather than only ever naming an action.
ANSWER = "answer"


@dataclass
class CommandContext:
    """What the interpreter is allowed to know.

    `now` is the **hut's** clock. It is not taken from the client: the sender is
    a phone on a boat, and a device an hour out would silently move every
    resolved time with it.
    """

    now: datetime
    current_race_id: Optional[int] = None
    current_race_name: str = ""
    race_type: Optional[str] = None          # of the current race, when known
    # The current race's own times and course. Without these a relative
    # instruction cannot be answered at all: "put the start back ten minutes"
    # needs to know what it is ten minutes later than.
    current_gun_time: Optional[datetime] = None
    current_course_no: Optional[int] = None
    course_is_set: bool = True
    # Whether every boat in it has finished. A finished race is still the race
    # the app calls current, and "let's try 29" a minute after it ended is
    # almost always about the next race rather than that one -- so the read-back
    # has to say which race it means before anybody presses yes.
    race_finished: bool = False
    # A proposal already read back and waiting for a yes. The subject of the
    # conversation while it is there: "can you do a longer one?" means change
    # that proposal, not do something else to some other race.
    pending_readback: str = ""
    pending_intent: str = ""
    # What was said and done just before, so a follow-up like "make it a pursuit"
    # or "add the IRC fleet to that" has something to refer to. Oldest first,
    # each (what was typed, what the app did).
    recent: List[Tuple[str, str]] = field(default_factory=list)
    # Plain sentences about the racing right now -- the wind, how many boats are
    # out, how far round they are. Only what the app itself knows, assembled by
    # the caller. Without them a question like "is that course too long?" can
    # only be guessed at, and a guess about a race is worse than "I don't know".
    facts: List[str] = field(default_factory=list)


@dataclass
class Intent:
    """What the sentence asked for, before any rule has been applied."""

    name: str
    arguments: Dict[str, Any] = field(default_factory=dict)
    source: str = "grammar"                  # or the model that produced it


@dataclass
class Resolution:
    """What the app proposes to do, for a person to confirm. Never executed."""

    status: str
    intent: str = ""
    readback: str = ""
    resolved: Dict[str, Any] = field(default_factory=dict)
    question: str = ""
    options: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    # A reply in words, when there was nothing to do. Never an action.
    answer: str = ""
    # Which argument the question is about, so that a one-word reply to it can be
    # put back where it belongs. A question the app cannot hear the answer to is
    # not a question, it is a dead end -- which is what "standard" hit.
    clarify_field: str = ""


# The tools a model may choose from. Deliberately small: each maps to a function
# in core.raceadmin that the race sheet also calls, so there is no intent here
# that the GUI cannot already do.
TOOLS: List[Dict[str, Any]] = [
    {
        "name": "create_race",
        # The schema is what a model is told it may say. It has to be able to
        # express the whole sentence: asked for "a race at 11, hour long" with
        # only a name and a race type to hand, two models dropped the time and
        # one put the length in the pursuit field of a standard race.
        "description": ("Create a new race sheet, optionally with its first start time. "
                        "The course is chosen separately."),
        "arguments": {
            "name": "str, optional. The race's name if the instruction gives one.",
            "race_type": ("'standard' or 'pursuit'. Leave unset if the instruction does not "
                          "make it clear -- the app will ask."),
            "gun_time": ("ISO 8601 local date and time of the first START (the gun). "
                         "The warning signal five minutes earlier is derived by the app."),
            "length_min": ("number of minutes, if the instruction gives a length. For a pursuit "
                           "this is its fixed period; for a standard race it is only a target "
                           "used to recommend a course."),
            "add_all_active": ("true only if the instruction actually asks for every boat to be "
                               "entered. Do not set it otherwise."),
            "series": ("the series the race belongs to, exactly as the instruction says it, e.g. "
                       "'Wednesday Evening Points'. Leave unset if no series is named; the app "
                       "matches the name against the club's series and asks if it cannot."),
        },
    },
    {
        "name": "set_start_and_course",
        "description": "Set a race's first warning signal and/or its course.",
        "arguments": {
            "race_id": "int",
            "gun_time": "ISO time of the first START (the warning is five minutes earlier)",
            "course_no": "int, a fixed course number",
            "target_length_min": "number, standard race only: a target used to recommend a course",
            "series": ("the series the race should be scored in, as the instruction says it. Use "
                       "this for anything about putting an existing race into a series."),
        },
    },
    {
        "name": "add_entries",
        "description": ("Enter boats in a race: every active boat, named boats, or the same "
                        "boats as another race. Use this for anything about adding, entering "
                        "or putting boats in."),
        "arguments": {
            "race_id": "int, optional. Defaults to the current race.",
            "scope": ("'all_active' for every active boat, 'boat' for named boats, or "
                      "'same_as' to enter the same boats as another race"),
            "boat": ("the boat names as the instruction says them when scope is 'boat', comma "
                     "separated for more than one, e.g. 'Mojito, Sgrech Bach'. The app matches "
                     "each against the boat database and asks if it cannot."),
            "same_as_race_id": ("int, when scope is 'same_as': the race whose boats to copy. "
                                "Use for \"the same boats\", \"the same fleet as last time\"."),
        },
    },
    {
        "name": "set_custom_course",
        "description": ("Make up a course from the club's marks and set it on the race, instead "
                        "of using a numbered one. Use for \"make me a course\", \"windward "
                        "leeward twice round\", and for changing a made-up course: give the "
                        "WHOLE sequence again with the change in it."),
        "arguments": {
            "race_id": "int, optional. Defaults to the race being worked on.",
            "marks": ("the marks in the order they are sailed, each with the hand it is left on: "
                      "'O port, 4 port, 2 starboard, O port'. Say 'port' or 'starboard' for each; "
                      "a waypoint is simply passed and needs neither."),
        },
    },
    {
        "name": "boat_rating",
        "description": ("Read-only: what a boat is rated, by name or sail number. Looks in the "
                        "club's boat database and, if it is not there or has no rating, in the "
                        "RORC IRC listing and the YTC sheet."),
        "arguments": {
            "boat": "the boat's name or sail number, e.g. 'Mojito' or 'GBR4822R'",
        },
    },
    {
        "name": "add_boat",
        "description": ("Add a boat to the club's boat database from the IRC listing or the YTC "
                        "sheet. Only for a boat that is not in it already — an existing record is "
                        "never overwritten."),
        "arguments": {
            "boat": "the boat's name or sail number, as the listing has it",
        },
    },
    {
        "name": "look_up",
        # The alternative was sending every mark, every course and every boat
        # with every command. The club has 24 marks, 67 courses and a boat
        # database: that does not belong in a request typed on 4G, and most of
        # it is not wanted most of the time. So the facts carry what is always
        # relevant, and this fetches the rest when it is asked for.
        "description": ("Read-only: ask the app for detail that was not in the facts — where a "
                        "mark is, what a course's legs are, how many courses there are, which "
                        "boats the club has. Use it rather than saying the app does not tell you."),
        "arguments": {
            "topic": ("one of: 'marks' (positions of the club's marks and the start/finish "
                      "line), 'courses' (how many there are and their lengths), 'course' (one "
                      "course's legs, distances and points of sail), 'boats' (the club's boat "
                      "database), 'series' (the club's series, newest first, with how many races "
                      "each has and when it was last raced), 'races' (recent races), 'fleet' "
                      "(where each boat has got to on the water), 'polar' (the boat speeds the "
                      "course timings are worked out from) or 'sails' (the sail chart for it)"),
            "course_no": "int, with topic 'course'",
            "query": "str, optional: narrow it to one mark or boat, e.g. 'O' or 'Mojito'",
        },
    },
    {
        "name": "race_results",
        "description": ("Read-only: who finished where in a race that has been sailed. Use for "
                        "anything asking about results, finishing order, who won, or how a race "
                        "went."),
        "arguments": {
            "race_id": "int, optional if the race is named",
            "race_name": ("str, optional: the race as the instruction names it, e.g. 'the night "
                          "race'. The app matches it against the club's races."),
        },
    },
    {
        "name": "shorten_course",
        "description": ("End the race early at a mark the fleet is already sailing to: they round "
                        "it and go straight to the finish. Sounds two horn blasts."),
        "arguments": {
            "race_id": "int, optional. Defaults to the current race.",
            # at_mark, not just at_index: a model knows the mark is called "4",
            # never its position in the course sequence. Offered only an index it
            # could not know, it proposed a status report instead -- the same
            # failure as create_race with no gun_time.
            "at_mark": "the mark's name or code as the race officer says it, e.g. '4' or 'O'",
            "at_index": "int, optional: the mark's position in the course, if known",
        },
    },
    {
        "name": "postpone_race",
        # The club used to delay a start by moving the start time, which signals
        # nothing to the fleet. This is the tool that makes the correct thing the
        # easy thing, so its description says what it is *for*, not just what it does.
        "description": ("Postpone a race that has not started -- the wind has died, the line is "
                        "not ready, the fleet is not there. Flies AP and sounds two horn blasts. "
                        "Use this rather than moving the start time: the warning signal is then "
                        "made one minute after AP is lowered, so there is no need to guess when "
                        "the wind will return."),
        "arguments": {
            "race_id": "int, optional. Defaults to the current race.",
            "kind": ("optional, one of 'AP' (default), 'AP over H' (postponed, further signals "
                     "ashore) or 'AP over A' (postponed, no more racing today)"),
        },
    },
    {
        "name": "resume_race",
        "description": ("Say when AP comes down on a postponed race. One horn blast at that "
                        "moment, and the warning signal one minute later. Use when the wind is "
                        "back. Leave the time unset unless the race officer names one -- the app "
                        "picks the next whole minute, which is what keeps the gun on a round "
                        "number."),
        "arguments": {
            "race_id": "int, optional. Defaults to the current race.",
            "lower_at": ("optional ISO 8601 local date and time, whole minutes, if the race "
                         "officer names when the flag comes down, e.g. 'in five minutes'"),
            "warning_time": ("optional: only needed after AP over H, which has no one-minute "
                             "rule because the next signals are made ashore"),
        },
    },
    {
        "name": "race_status",
        "description": "Read-only: what is happening in a race.",
        "arguments": {"race_id": "int, optional"},
    },
]

TOOL_NAMES = {t["name"] for t in TOOLS}
# What a parser is allowed to come back with: an action this app implements, or
# a reply in words. Anything else is dropped -- a model may propose anything, and
# what this app will do is decided here rather than by what came back.
PARSEABLE_NAMES = TOOL_NAMES | {ANSWER}

Parser = Callable[[str, CommandContext], Optional[Intent]]


# ---------------------------------------------------------------------------
# Reading a time out of a sentence
# ---------------------------------------------------------------------------

_TIME_PATTERNS = (
    # 11:05, 11.05, 1105 hrs, with optional am/pm
    re.compile(r"\b(?P<h>\d{1,2})[:.](?P<m>\d{2})\s*(?P<ap>am|pm)?\b", re.I),
    re.compile(r"\b(?P<h>\d{2})(?P<m>\d{2})\s*(?:hrs?|hours)\b", re.I),
    # 11am, 11 am, at 11
    re.compile(r"\b(?:at\s+)?(?P<h>\d{1,2})\s*(?P<ap>am|pm)\b", re.I),
    re.compile(r"\bat\s+(?P<h>\d{1,2})\b(?!\s*(?:min|minute|nm|kn))", re.I),
)


def find_clock_time(text: str, now: datetime):
    """The wall-clock time named in a sentence, with the span it occupied.

    The span matters: "1105 hrs" is a time, and a duration pattern looking at
    the same words reads it as 1105 hours. Whoever reads the time first has to
    take those characters out of the sentence before anything else looks at it.
    """
    for pattern in _TIME_PATTERNS:
        m = pattern.search(text)
        if not m:
            continue
        hour = int(m.group("h"))
        minute = int(m.groupdict().get("m") or 0)
        ap = (m.groupdict().get("ap") or "").lower()
        if ap == "pm" and hour < 12:
            hour += 12
        elif ap == "am" and hour == 12:
            hour = 0
        elif not ap and hour <= 7:
            # No club race starts at 03:00; a bare "at 3" is the afternoon.
            hour += 12
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            return None
        when = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if when < now:
            when += timedelta(days=1)
        return when, m.span()
    return None


def parse_clock_time(text: str, now: datetime) -> Optional[datetime]:
    """The wall-clock time named in a sentence, as an absolute datetime.

    Today unless that is already past, in which case tomorrow -- a race asked
    for "at 11" during Sunday's debrief is Monday's race, not one that started
    an hour ago. Deliberately narrow: this is the fallback for when no model is
    reachable, and a time it cannot read becomes a question rather than a guess.
    """
    found = find_clock_time(text, now)
    return found[0] if found else None


def without_span(text: str, span) -> str:
    """The sentence with a matched span taken out, so the next reader of it
    cannot claim the same characters."""
    if not span:
        return text
    return (text[:span[0]] + " " + text[span[1]:]).strip()


def parse_duration_minutes(text: str) -> Optional[float]:
    """A length of time in a sentence: "an hour", "90 minutes", "1h30".

    The hours form is limited to one or two digits. Four is a clock time --
    "1105 hrs" was read here as 1105 hours, which turned a start time into a
    46-day pursuit and, because a length with no race type is a question, made
    the whole command ask which kind of race it was.
    """
    m = re.search(r"\b(\d{1,2})\s*h(?:ours?|rs?)?\s*(\d+)?\b", text, re.I)
    if m:
        return int(m.group(1)) * 60 + int(m.group(2) or 0)
    m = re.search(r"\b(\d+)\s*(?:min|mins|minutes)\b", text, re.I)
    if m:
        return float(m.group(1))
    if re.search(r"\ban?\s+hour\b", text, re.I):
        return 60.0
    if re.search(r"\bhalf\s+an?\s+hour\b", text, re.I):
        return 30.0
    return None


# ---------------------------------------------------------------------------
# The no-model parser
# ---------------------------------------------------------------------------

def grammar_parse(text: str, context: CommandContext) -> Optional[Intent]:
    """Read the handful of commands worth having with no model reachable.

    This is not natural language understanding and does not pretend to be. It
    recognises the shapes people actually type under pressure, and returns None
    for everything else so the caller can say so plainly.
    """
    # Matching is done in lower case; anything quoted back to a person is taken
    # from the original at the same offsets. Whitespace is normalised on both so
    # the offsets line up. A race name is printed on the course board and in the
    # published results, and "sunday points" is not what anybody typed.
    original = " ".join((text or "").strip().split())
    said = original.lower()
    if not said:
        return None

    if re.search(r"\b(status|how'?s it|how (is|are)|what'?s happening|where are we)\b", said):
        return Intent("race_status", {"race_id": context.current_race_id}, source="grammar")

    if re.search(r"\bshorten\b", said):
        m = re.search(r"\b(?:at|on)\s+(?:mark\s+)?(\w+)\b", said)
        args: Dict[str, Any] = {"race_id": context.current_race_id}
        if m:
            args["at_mark"] = m.group(1)
        return Intent("shorten_course", args, source="grammar")

    if re.search(r"\b(create|new|start)\s+(a\s+)?(race|pursuit)\b", said):
        args = {"race_type": "pursuit" if "pursuit" in said else None,
                "add_all_active": bool(re.search(r"\ball (the )?boats\b|\bevery boat\b", said))}
        # Take the time out of the sentence before looking for a length, so the
        # two readers cannot claim the same characters.
        found = find_clock_time(said, context.now)
        rest = said
        if found:
            args["gun_time"] = found[0].isoformat(timespec="minutes")
            rest = without_span(said, found[1])
        length = parse_duration_minutes(rest)
        if length is not None:
            args["length_min"] = length
        m = re.search(r"\bcalled\s+([\w' ]+?)(?:\s+at\b|\s*$)", said)
        if m:
            args["name"] = original[m.start(1):m.end(1)].strip()
        return Intent("create_race", args, source="grammar")

    if re.search(r"\badd\b.*\b(boats?|fleet|entries)\b", said):
        # "add the fleet" is every active boat. There is no longer a class to
        # narrow it to, and a race officer saying it means the boats sailing.
        return Intent("add_entries", {
            "race_id": context.current_race_id,
            "scope": "all_active",
        }, source="grammar")

    # "put the start back ten minutes" is relative to the race's own gun, which
    # is why the context carries it. Without a race to be relative to, this falls
    # through and the reply asks which race.
    # "put the start back", "put it back", "push the gun back": whatever is being
    # put back sits between the verb and the "back", so it has to be allowed for.
    # Matching only "put back" and "put it back" meant the commonest phrasing of
    # all -- "put the start back ten minutes" -- fell through to not understood.
    shift = re.search(r"\b(delay|postpone"
                      r"|(?:put|push|move)\s+(?:\w+\s+){0,2}back"
                      r"|(?:bring|pull|move)\s+(?:\w+\s+){0,2}forward)\b"
                      r"[^.]{0,20}?(\d+)\s*(?:min|mins|minutes)?\b", said)
    if shift and context.current_gun_time:
        minutes = int(shift.group(2))
        if "forward" in shift.group(1):
            minutes = -minutes
        moved = context.current_gun_time + timedelta(minutes=minutes)
        return Intent("set_start_and_course",
                      {"race_id": context.current_race_id,
                       "gun_time": moved.isoformat(timespec="minutes")}, source="grammar")

    if re.search(r"\b(warning|start|gun|course)\b", said):
        args = {"race_id": context.current_race_id}
        gun = parse_clock_time(said, context.now)
        if gun:
            args["gun_time"] = gun.isoformat(timespec="minutes")
        m = re.search(r"\bcourse\s+(\d+)\b", said)
        if m:
            args["course_no"] = int(m.group(1))
        if len(args) == 1:
            return None
        return Intent("set_start_and_course", args, source="grammar")

    return None


# ---------------------------------------------------------------------------
# Hearing the answer to a question the app asked
# ---------------------------------------------------------------------------

# Which questions can be answered with a bare value, and what a bare answer to
# each looks like. Deliberately narrow, and anchored at both ends: "status" typed
# while a question about a mark is open is a new command, not the name of a mark.
_BARE_ANSWERS = {
    "at_mark": re.compile(r"^(?:at\s+)?(?:mark\s+)?([0-9a-z]{1,3})$", re.I),
    "race_id": re.compile(r"^(?:race\s*)?#?(\d{1,6})$", re.I),
    "course_no": re.compile(r"^(?:course\s+)?(\d{1,3})$", re.I),
}


# Saying yes out loud. The Yes button is not the only way somebody agrees, and
# with a proposal waiting, "yes lets do that" went off to be read as a fresh
# instruction -- which is how agreement to create a race became a course change
# to a race that had already finished.
#
# Recognised by allowing a short list of words rather than by forbidding a short
# list: agreement is agreement and nothing else, so "yes, course 4" and "no,
# make it half past" stay instructions and go to the interpreter, where a change
# of mind carrying an instruction belongs. A blocklist can only ever forbid the
# phrasings somebody thought of.
_YES_WORDS = {"y", "yes", "yep", "yeah", "yup", "ok", "okay", "sure", "aye", "right",
              "correct", "confirm", "confirmed", "go", "ahead", "on", "do", "it", "that",
              "that's", "thats", "this", "lets", "let's", "please", "thanks", "thank", "you",
              "then", "fine", "good", "great", "perfect", "carry"}
_NO_WORDS = {"n", "no", "nope", "nah", "cancel", "stop", "forget", "leave", "it", "that",
             "don't", "dont", "do", "not", "never", "mind", "thanks", "thank", "you",
             "please", "scrap", "abandon", "drop"}
# The first word settles which it is; a sentence merely containing "ok" is not
# an answer to anything.
_YES_OPENERS = {"y", "yes", "yep", "yeah", "yup", "ok", "okay", "sure", "aye", "go",
                "do", "confirm", "confirmed", "please", "correct", "that", "thats", "that's",
                "carry"}
_NO_OPENERS = {"n", "no", "nope", "nah", "cancel", "stop", "forget", "leave", "don't",
               "dont", "scrap", "abandon", "drop", "never"}


def _agreement_words(text: str) -> List[str]:
    return [word for word in re.split(r"[^\w']+", (text or "").lower()) if word]


def _agrees(text: str, openers: set, allowed: set) -> bool:
    words = _agreement_words(text)
    if not words or words[0] not in openers:
        return False
    return all(word in allowed for word in words)


def reads_as_yes(text: str) -> bool:
    """Plain agreement to whatever is on screen, and nothing else besides."""
    return _agrees(text, _YES_OPENERS, _YES_WORDS)


def reads_as_no(text: str) -> bool:
    """Plain refusal of whatever is on screen, and nothing else besides."""
    return _agrees(text, _NO_OPENERS, _NO_WORDS)


def parse_mark_sequence(said: Any) -> List[Dict[str, str]]:
    """"O port, 4 port, 2 starboard, O port" as a course sequence.

    Accepts the shapes a model actually produces: that sentence, a list of
    strings, or a list of `{mark, rounding}` objects. The marks themselves are
    checked against the club's own list afterwards, by the caller — here they are
    only read. Port is the default because it is the club's usual hand, and a
    read-back naming every mark and its side is what catches it if that is wrong.
    """
    items: List[Any]
    if isinstance(said, (list, tuple)):
        items = list(said)
    else:
        items = [part for part in re.split(r",|;| then ", str(said or "")) if part.strip()]
    sequence: List[Dict[str, str]] = []
    for item in items:
        if isinstance(item, dict):
            mark = str(item.get("mark", "")).strip().upper()
            rounding = str(item.get("rounding", "port")).strip().lower()
        else:
            words = str(item).strip().split()
            if not words:
                continue
            mark = words[0].strip().upper().rstrip(".")
            rounding = " ".join(words[1:]).strip().lower()
        if not mark:
            continue
        # A trailing P or S on the mark itself: "4P, 2S", which is how a course
        # board is written and therefore how somebody types it.
        if not rounding and len(mark) > 1 and mark[-1] in ("P", "S") and mark[:-1].isalnum():
            rounding = "starboard" if mark[-1] == "S" else "port"
            mark = mark[:-1]
        sequence.append({"mark": mark,
                         "rounding": "starboard" if rounding.startswith("s") else "port"})
    return sequence


def answer_to_question(name: str, arguments: Dict[str, Any], field_name: str,
                       options: List[str], text: str) -> Optional[Intent]:
    """The question the app asked, plus the reply, back into one whole intent.

    This is the repair the page did not have. Asked "standard race or pursuit?",
    a person answers "standard" -- and a bare word carries no time, no length and
    no race, so read on its own it is not a command at all and the app said so.
    Everything already established is carried forward; only the missing field is
    filled in.

    Returns None whenever the reply is not plausibly an answer, so that changing
    the subject mid-question does exactly what it looks like.
    """
    said = " ".join((text or "").strip().split())
    if not (name and field_name and said):
        return None
    lowered = said.lower()
    if len(lowered.split()) > 6:
        # A whole sentence is a new instruction. "standard" and "it's a standard
        # race" are answers; a fresh command is not.
        return None
    for option in options or []:
        if re.search(rf"\b{re.escape(str(option).lower())}\b", lowered):
            return Intent(name, {**(arguments or {}), field_name: option}, source="reply")
    if options:
        # The question offered a choice and this is not one of them.
        return None
    match = (_BARE_ANSWERS.get(field_name) or re.compile(r"(?!)")).match(lowered)
    if not match:
        return None
    return Intent(name, {**(arguments or {}), field_name: match.group(1)}, source="reply")


def parse_command(text: str, context: CommandContext, parser: Parser = grammar_parse) -> Optional[Intent]:
    """Interpret a sentence with whichever parser the caller supplies.

    The seam: a model-backed parser is a function of this shape, so the provider
    is a caller's choice and never an edit in here. An intent naming a tool that
    does not exist is dropped -- a model may propose anything, and the set of
    things this app will do is fixed here rather than by what came back.
    """
    intent = parser(text, context)
    if intent is None or intent.name not in PARSEABLE_NAMES:
        return None
    return intent


# ---------------------------------------------------------------------------
# Turning an intent into something a person can confirm
# ---------------------------------------------------------------------------

def _hhmm(when: datetime) -> str:
    return when.strftime("%H:%M")


# A model's arguments arrive as strings, whatever the schema says: "true", "90",
# "2026-08-15T11:00". The grammar produces real types. Everything below is read
# through these so the two are the same by the time any rule sees them -- and so
# that bool("false"), which is True, cannot decide to enter every boat in the club.
def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in ("1", "true", "yes", "y", "on")


def _as_number(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _as_int(value: Any) -> Optional[int]:
    number = _as_number(value)
    return None if number is None else int(number)


def _as_datetime(value: Any, now: Optional[datetime] = None) -> Optional[datetime]:
    """A time a model offered, or None. Anything unparseable is not a time.

    A bare "14:15" counts. The prompt asks for a full ISO date and time and the
    model usually obliges, but not always -- and a time-only answer was being
    dropped, which turned "make the gun quarter past two" into "set what?". The
    date is then today, or tomorrow if that hour has already gone, which is the
    same rule the grammar uses.
    """
    if isinstance(value, datetime):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        pass
    if now is None:
        return None
    try:
        clock = time.fromisoformat(text)
    except ValueError:
        return None
    when = now.replace(hour=clock.hour, minute=clock.minute, second=0, microsecond=0)
    return when + timedelta(days=1) if when < now else when


def _which_race(context: CommandContext, race_id: int) -> str:
    """How a read-back names the race it is about to change.

    A finished race is named as finished, every time. It stays the app's current
    race after the last boat is in, so "let's try 29" -- said about the next race
    -- read back as a perfectly ordinary course change to the one that had just
    ended, and there was nothing in the sentence to notice.
    """
    where = context.current_race_name or f"race #{race_id}"
    if context.race_finished and race_id == context.current_race_id:
        return f"{where} (which has finished)"
    return where


def _finished_warning(context: CommandContext, race_id: int) -> List[str]:
    if context.race_finished and race_id == context.current_race_id:
        return ["That race has finished. Say 'new race' if this was meant for the next one."]
    return []


def _clarify_duration(intent: Intent) -> Resolution:
    return Resolution(
        status=NEEDS_CLARIFICATION,
        intent=intent.name,
        question=("Is this a standard race or a pursuit? A length means the pursuit's own "
                  "period for a pursuit race, and only a target for recommending a course "
                  "for a standard one."),
        options=["standard", "pursuit"],
        clarify_field="race_type",
    )


def resolve(intent: Optional[Intent], context: CommandContext) -> Resolution:
    """Work out exactly what would happen, and say it back. Executes nothing."""
    if intent is None:
        return Resolution(status=NOT_UNDERSTOOD,
                          question="I did not understand that. Try 'status', 'create a race at "
                                   "11', 'add all boats' or 'shorten at mark 4'.")

    if intent.name == ANSWER:
        # Words, not an action. Nothing is stored, nothing is confirmed, and the
        # caller has no branch that could execute it.
        said = str(intent.arguments.get("text") or "").strip()
        if not said:
            return Resolution(status=NOT_UNDERSTOOD,
                              question="I did not understand that.")
        return Resolution(status=ANSWERED, intent=ANSWER, answer=said)

    if intent.name == "create_race":
        args = intent.arguments
        race_type = str(args.get("race_type") or "").strip().lower() or None
        if race_type not in (None, "standard", "pursuit"):
            race_type = None
        length = _as_number(args.get("length_min", args.get("pursuit_duration_min")))
        # Same words, different code path: ask rather than pick one.
        if length is not None and not race_type:
            return _clarify_duration(intent)
        name = (args.get("name") or "Club Race").strip()
        resolved: Dict[str, Any] = {
            "name": name,
            "race_type": race_type or "standard",
            "add_all_active": _as_bool(args.get("add_all_active")),
        }
        parts = [f"Create '{name}' as a {resolved['race_type']} race"]
        if resolved["race_type"] == "pursuit" and length is not None:
            resolved["pursuit_duration_min"] = float(length)
            parts.append(f"running for {int(length)} minutes")
        elif length is not None:
            resolved["target_length_min"] = float(length)
            parts.append(f"with a target length of {int(length)} minutes "
                         "(used only to recommend a course)")
        gun = _as_datetime(args.get("gun_time"), context.now)
        if gun:
            warning = gun - WARNING_TO_GUN
            resolved["first_warning_time"] = warning.isoformat(timespec="minutes")
            resolved["first_gun_time"] = gun.isoformat(timespec="minutes")
            parts.append(f"first warning signal {_hhmm(warning)}, first gun {_hhmm(gun)}")
        else:
            # A race with no start time is legitimate -- the course is often
            # chosen first -- but a read-back that simply does not mention the
            # time reads as though one had been understood.
            parts.append("no start time yet")
        # The series is matched against the club's own list by the caller, which
        # is the only thing that can: a name here is what was said, not a series.
        series = str(args.get("series") or "").strip()
        if series:
            resolved["series_name"] = series
            parts.append(f"in series {series}")
        else:
            parts.append("in no series")
        if resolved["add_all_active"]:
            parts.append("adding every active boat")
        # In the read-back as well as the result, because it is a difference in
        # how the race will be run that the sentence did not ask for: from the
        # water the app watches the fleet across the line itself.
        parts.append("with GPS finishes armed and recorded automatically")
        return Resolution(status=NEEDS_CONFIRMATION, intent=intent.name,
                          readback=", ".join(parts) + ".", resolved=resolved)

    if intent.name == "set_start_and_course":
        args = intent.arguments
        race_id = _as_int(args.get("race_id")) or context.current_race_id
        if not race_id:
            return Resolution(status=NEEDS_CLARIFICATION, intent=intent.name,
                              question="Which race? There is no current race to apply that to.",
                              clarify_field="race_id")
        resolved = {"race_id": int(race_id)}
        parts = []
        gun = _as_datetime(args.get("gun_time"), context.now)
        if gun:
            warning = gun - WARNING_TO_GUN
            resolved["first_warning_time"] = warning.isoformat(timespec="minutes")
            resolved["first_gun_time"] = gun.isoformat(timespec="minutes")
            parts.append(f"first warning signal {_hhmm(warning)}, first gun {_hhmm(gun)}")
        course_no = _as_int(args.get("course_no"))
        if course_no is not None:
            resolved["course_no"] = course_no
            parts.append(f"course {course_no}")
        series = str(args.get("series") or "").strip()
        if series:
            # Matched against the club's own list by the caller, as for a new
            # race. Asked to put a race into ISORA, the app used to answer that
            # it could only set a series when the race was created.
            resolved["series_name"] = series
            parts.append(f"series {series}")
        if not parts:
            return Resolution(status=NEEDS_CLARIFICATION, intent=intent.name,
                              question="Set what -- a start time, a course, or both?")
        return Resolution(status=NEEDS_CONFIRMATION, intent=intent.name,
                          readback=f"Set {_which_race(context, int(race_id))}: "
                                   + ", ".join(parts) + ".",
                          resolved=resolved, warnings=_finished_warning(context, int(race_id)))

    if intent.name == "add_entries":
        args = intent.arguments
        race_id = _as_int(args.get("race_id")) or context.current_race_id
        if not race_id:
            return Resolution(status=NEEDS_CLARIFICATION, intent=intent.name,
                              question="Which race should the boats be added to?",
                              clarify_field="race_id")
        scope = str(args.get("scope") or "").strip().lower()
        if scope not in ("boat", "same_as"):
            scope = "all_active"
        # A boat named without the scope to match is still a boat: models set one
        # field and not the other, and "add Mojito" must never be read as
        # entering every boat in the club.
        if args.get("boat") and scope == "all_active":
            scope = "boat"
        if _as_int(args.get("same_as_race_id")) and scope == "all_active":
            scope = "same_as"
        # "Add Sgrech Bach and Mojito" is one instruction about two boats. Read as
        # one name it entered the first and dropped the second without a word.
        names = [part.strip() for part in re.split(r",| and ", str(args.get("boat") or ""))
                 if part.strip()]
        resolved = {"race_id": int(race_id), "scope": scope,
                    "boat_names": names,
                    "same_as_race_id": _as_int(args.get("same_as_race_id"))}
        what = {"boat": " and ".join(names) or "one boat",
                "same_as": f"the same boats as race #{resolved['same_as_race_id']}",
                "all_active": "every active boat"}[scope]
        return Resolution(status=NEEDS_CONFIRMATION, intent=intent.name,
                          readback=f"Add {what} to {_which_race(context, int(race_id))}.",
                          resolved=resolved,
                          warnings=_finished_warning(context, int(race_id)))

    if intent.name == "shorten_course":
        args = intent.arguments
        race_id = _as_int(args.get("race_id")) or context.current_race_id
        if not race_id:
            return Resolution(status=NEEDS_CLARIFICATION, intent=intent.name,
                              question="Which race is being shortened?",
                              clarify_field="race_id")
        if not args.get("at_mark") and args.get("at_index") is None:
            return Resolution(status=NEEDS_CLARIFICATION, intent=intent.name,
                              question="Which mark should the course be shortened at?",
                              clarify_field="at_mark")
        resolved = {"race_id": int(race_id)}
        if _as_int(args.get("at_index")) is not None:
            resolved["at_index"] = _as_int(args["at_index"])
        if args.get("at_mark"):
            resolved["at_mark"] = str(args["at_mark"])
        where = _which_race(context, int(race_id))
        mark = resolved.get("at_mark", resolved.get("at_index"))
        return Resolution(
            status=NEEDS_CONFIRMATION, intent=intent.name,
            readback=(f"Shorten the course for {where} at mark {mark}. This sounds two horn "
                      "blasts and makes the announcement."),
            resolved=resolved)

    if intent.name == "postpone_race":
        args = intent.arguments
        race_id = _as_int(args.get("race_id")) or context.current_race_id
        if not race_id:
            return Resolution(status=NEEDS_CLARIFICATION, intent=intent.name,
                              question="Which race is being postponed?", clarify_field="race_id")
        kind = normalise_postponement_kind(args.get("kind") or "AP")
        if kind not in POSTPONEMENT_KINDS:
            return Resolution(status=NEEDS_CLARIFICATION, intent=intent.name,
                              question=("Postpone with AP, AP over H (further signals ashore) or "
                                        "AP over A (no more racing today)?"),
                              clarify_field="kind")
        spec = POSTPONEMENT_KINDS[kind]
        where = _which_race(context, int(race_id))
        after = {
            "AP": " The warning signal is then made one minute after AP is lowered.",
            "AP_H": " Further signals ashore.",
            "AP_A": " No more racing today.",
        }[kind]
        return Resolution(
            status=NEEDS_CONFIRMATION, intent=intent.name,
            readback=(f"Postpone {where}: {spec['flag']} up, two horn blasts.{after}"),
            resolved={"race_id": int(race_id), "kind": kind})

    if intent.name == "resume_race":
        args = intent.arguments
        race_id = _as_int(args.get("race_id")) or context.current_race_id
        if not race_id:
            return Resolution(status=NEEDS_CLARIFICATION, intent=intent.name,
                              question="Which race is starting again?", clarify_field="race_id")
        resolved = {"race_id": int(race_id)}
        if args.get("warning_time"):
            resolved["warning_time"] = str(args["warning_time"])
        where = _which_race(context, int(race_id))
        # Whole minutes, so the read-back can name the gun and the fleet can
        # count to it. A time the race officer gives is snapped to the minute
        # here; otherwise the next whole minute from the hut's clock.
        lower_at = _as_datetime(args.get("lower_at"), context.now)
        if lower_at:
            lower_at = lower_at.replace(second=0, microsecond=0)
        else:
            lower_at = datetime.fromisoformat(next_whole_minute(context.now))
        resolved["lower_at"] = lower_at.isoformat(timespec="seconds")
        warning = lower_at + timedelta(seconds=60)
        gun = warning + WARNING_TO_GUN
        return Resolution(
            status=NEEDS_CONFIRMATION, intent=intent.name,
            readback=(f"AP down on {where} at {lower_at.strftime('%H:%M')}, one horn blast. "
                      f"Warning signal {warning.strftime('%H:%M')}, first gun "
                      f"{gun.strftime('%H:%M')}."),
            resolved=resolved)

    if intent.name == "set_custom_course":
        args = intent.arguments
        race_id = _as_int(args.get("race_id")) or context.current_race_id
        if not race_id:
            return Resolution(status=NEEDS_CLARIFICATION, intent=intent.name,
                              question="Which race is the course for?", clarify_field="race_id")
        marks = parse_mark_sequence(args.get("marks"))
        if not marks:
            return Resolution(status=NEEDS_CLARIFICATION, intent=intent.name,
                              question="Which marks, in order, and which hand is each left on? "
                                       "For example 'O port, 4 port, 2 starboard, O port'.",
                              clarify_field="marks")
        resolved = {"race_id": int(race_id), "marks": marks}
        board = " ".join(f"{m['mark']}{m['rounding'][:1]}" for m in marks)
        return Resolution(
            status=NEEDS_CONFIRMATION, intent=intent.name,
            readback=(f"Make up a course for {_which_race(context, int(race_id))} and set it: "
                      f"{board} ({len(marks)} marks). It replaces the numbered course."),
            resolved=resolved, warnings=_finished_warning(context, int(race_id)))

    if intent.name in ("boat_rating", "add_boat"):
        boat = str(intent.arguments.get("boat") or "").strip()
        if not boat:
            return Resolution(status=NEEDS_CLARIFICATION, intent=intent.name,
                              question="Which boat — by name or sail number?",
                              clarify_field="boat")
        if intent.name == "boat_rating":
            # Read-only: nothing to undo, so nothing to confirm.
            return Resolution(status=NEEDS_CONFIRMATION, intent=intent.name,
                              readback=f"Look up {boat}'s rating.", resolved={"boat": boat})
        return Resolution(
            status=NEEDS_CONFIRMATION, intent=intent.name,
            readback=(f"Add {boat} to the club's boat database, with the ratings from the IRC "
                      "listing and the YTC sheet. An existing record is left alone."),
            resolved={"boat": boat})

    if intent.name == "look_up":
        topic = str(intent.arguments.get("topic") or "").strip().lower()
        known = ("marks", "courses", "course", "boats", "series", "races", "fleet",
                 "polar", "sails")
        if topic not in known:
            return Resolution(status=NEEDS_CLARIFICATION, intent=intent.name,
                              question="Look up what — " + ", ".join(known) + "?",
                              clarify_field="topic", options=list(known))
        resolved = {"topic": topic, "query": str(intent.arguments.get("query") or "").strip()}
        course_no = _as_int(intent.arguments.get("course_no"))
        if course_no is not None:
            resolved["course_no"] = course_no
        # Read-only, like the other two: nothing to undo, nothing to confirm.
        return Resolution(status=NEEDS_CONFIRMATION, intent=intent.name,
                          readback=f"Look up {topic}.", resolved=resolved)

    if intent.name == "race_results":
        args = intent.arguments
        race_id = _as_int(args.get("race_id"))
        name = str(args.get("race_name") or "").strip()
        if not race_id and not name:
            race_id = context.current_race_id
        if not race_id and not name:
            return Resolution(status=NEEDS_CLARIFICATION, intent=intent.name,
                              question="Which race's results?", clarify_field="race_id")
        # Read-only, like race_status: nothing to undo, and asking somebody to
        # confirm a question they just asked is noise.
        resolved = {}
        if race_id:
            resolved["race_id"] = int(race_id)
        if name:
            resolved["race_name"] = name
        return Resolution(status=NEEDS_CONFIRMATION, intent=intent.name,
                          readback=f"Report the results of {name or f'race #{race_id}'}.",
                          resolved=resolved)

    if intent.name == "race_status":
        race_id = _as_int(intent.arguments.get("race_id")) or context.current_race_id
        if not race_id:
            return Resolution(status=NEEDS_CLARIFICATION, intent=intent.name,
                              question="Which race?", clarify_field="race_id")
        # Read-only, so there is nothing to confirm.
        return Resolution(status=NEEDS_CONFIRMATION, intent=intent.name,
                          readback=f"Report the status of race #{int(race_id)}.",
                          resolved={"race_id": int(race_id)})

    return Resolution(status=NOT_UNDERSTOOD,
                      question=f"{intent.name} is not something this app will do.")
