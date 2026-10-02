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

* `grammar_parse` is deterministic, needs no network and costs nothing. It is
  what makes the tests run in CI with no provider account: a free, predictable
  stand-in for a model. The page itself does not use it -- as a fallback behind
  the model it never caught a command and repeatedly turned "course N" in a
  question into a proposal, so it was taken out of the page in September 2026.
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
from core.racesignals import (LOWER_AP_MIN_LEAD_S, POSTPONEMENT_KINDS,
                              normalise_postponement_kind)
from core.timeutils import next_whole_minute

# Time to read an AP-down read-back and press Yes, on top of the minute's notice
# the app gives before the flag moves. Proposed at 23:14:30 as "AP down at
# 23:15", the read-back was already too soon to announce, and the Yes pressed
# straight after it was refused: "the earliest is 23:17".
AGREE_ALLOWANCE_S = 30

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
    # The course as it is being sailed, named the way the race officer knows it:
    # "course 20", or "a made-up course, 5p Op 5p Op". A made-up course keeps the
    # fixed number underneath it, and told "its course is 20" beside a description
    # of 5p Op 5p Op, the interpreter timed both and lost the thread.
    current_course_label: str = ""
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
    # Runs a read-only tool and says what it found, so an interpreter can look
    # something up and then answer from it rather than handing the page a list.
    # Supplied by the caller, which is the only thing that can reach the data.
    # None, and a read-only tool comes back as an intent the way it always did.
    read: Optional[Callable[[str, Dict[str, Any]], str]] = None
    # Every look-up made while reading this sentence, as {tool, arguments}. The
    # caller keeps it with the turn, so the next sentence is reminded what was
    # asked for -- a target time, a polar -- and not only what was said.
    looked_up: List[Dict[str, Any]] = field(default_factory=list)
    # Told what the interpreter is doing, as it does it: "thinking", then the
    # name of each tool it runs, then "answering". The caller turns these into
    # words for the page, which otherwise shows nothing for twenty seconds.
    progress: Optional[Callable[[str], None]] = None
    # The club's series by name, and its latest races newest first as {"name",
    # "series", "when"}, so a new race is asked which series it is in and what it
    # is called, with the last race's as the suggestion. A race in a series is
    # entered with that series' boats, so the series is the fleet as well as the
    # scoring -- and "Club Race", which is what an unnamed race used to be
    # called, is not a name anybody would look for in the results. None when the
    # caller has not said, and a new race is then not asked about.
    club_series: Optional[List[str]] = None
    last_races: List[Dict[str, str]] = field(default_factory=list)


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
    # What a one-word reply to that question is added to, when it is not what
    # the parser produced: a plan whose steps were folded into one new race asks
    # about the folded race, and the answer belongs there.
    arguments: Dict[str, Any] = field(default_factory=dict)


# The tools a model may choose from. Deliberately small: each maps to a function
# in core.raceadmin that the race sheet also calls, so there is no intent here
# that the GUI cannot already do.
#
# Each argument is a JSON Schema fragment with a real type. They used to be one
# line of prose apiece, sent to the model as `"type": "string"` whatever the
# prose said -- so a race number, a yes/no and a choice between three words all
# arrived as free text the model had to guess the format of. The `_as_*`
# readers below still accept strings, because not every provider honours a
# schema, but nothing here asks a model to guess any more.
#
# `kind` says what a call does to the conversation:
#   read   -- runs at once and its result goes back to the model, which then
#             answers from it. Nothing to confirm: nothing changes.
#   report -- runs at once and its result is shown to the race officer as it is,
#             ending the turn. The status report is the one: it is the sentence
#             people type most, and a second round trip to put it in other
#             words would double the wait for nothing.
#   write  -- ends the turn as a read-back. Nothing happens until somebody says yes.
READ = "read"
REPORT = "report"
WRITE = "write"


def _race_id(text: str = "Defaults to the race the conversation is about.") -> Dict[str, Any]:
    return {"type": "integer", "description": f"The race's number, e.g. 57. {text}"}


def _race_name() -> Dict[str, Any]:
    return {"type": "string",
            "description": ("The race as the race officer names it, e.g. 'the night race' or "
                            "'Race 4', when they do not give its number. The app matches it "
                            "against the club's races.")}


def _wind(which: str) -> Dict[str, Any]:
    if which == "twd":
        return {"type": "number",
                "description": ("Optional: a wind direction in degrees true to plan for, instead "
                                "of the wind blowing now -- 'if it goes round to the west'.")}
    return {"type": "number",
            "description": "Optional: a wind strength in knots to plan for, instead of now."}


def _polar_boat() -> Dict[str, Any]:
    return {"type": "string",
            "description": ("Optional: time it on this boat's polar -- a boat's name, or a "
                            "polar's name such as 'J109'. Left out, the club's default polar.")}


def _gun_time(text: str) -> Dict[str, Any]:
    return {"type": "string",
            "description": ("ISO 8601 local date and time of the first START (the gun), e.g. "
                            f"2026-08-15T11:00. {text}")}


TOOLS: List[Dict[str, Any]] = [
    {
        "name": "create_race",
        "kind": WRITE,
        # The schema is what a model is told it may say. It has to be able to
        # express the whole sentence: asked for "a race at 11, hour long" with
        # only a name and a race type to hand, two models dropped the time and
        # one put the length in the pursuit field of a standard race. And "on
        # course 4" had nowhere to go at all, so the app suggested a course for
        # the wind in its place and the race officer had to say no to it.
        "description": ("Create a new race sheet: its first start, its course, its series and its "
                        "boats, all in one call when the instruction gives them."),
        "arguments": {
            "name": {"type": "string",
                     "description": ("The race's name, only if the instruction gives one. Left "
                                     "out, the app asks, suggesting the last race's.")},
            "race_type": {"type": "string", "enum": ["standard", "pursuit"],
                          "description": ("Leave it out if the instruction does not make it "
                                          "clear -- the app will ask.")},
            "gun_time": _gun_time("The warning signal five minutes earlier is derived by the app."),
            "length_min": {"type": "number",
                           "description": ("Minutes, if the instruction gives a length. For a "
                                           "pursuit this is its fixed period; for a standard race "
                                           "it is only a target used to recommend a course.")},
            "course_no": {"type": "integer",
                          "description": ("A fixed course number, only when the instruction names "
                                          "one or asks for the same course as a race you have "
                                          "looked up. Left out, the app suggests one for the wind.")},
            "add_all_active": {"type": "boolean",
                               "description": ("True only if the instruction actually asks for "
                                               "every boat in the boat database to be entered. "
                                               "Rarely wanted: most of them are not racing, and "
                                               "a race in a series is entered with the boats "
                                               "already racing in that series anyway.")},
            "boats": {"type": "string",
                      "description": ("Boats to enter by name, as the instruction says them, "
                                      "comma separated, e.g. 'Mojito, Sgrech Bach'.")},
            "series": {"type": "string",
                       "description": ("The series the race belongs to, as the instruction says "
                                       "it, e.g. 'Wednesday Evening Points', or 'no series' if "
                                       "the race officer says it is in none. Leave it out if "
                                       "no series is named: the app asks, suggesting the last "
                                       "race's series, and enters that series' boats.")},
        },
    },
    {
        "name": "set_start_and_course",
        "kind": WRITE,
        "description": ("Change an existing race's first start time, its course, its series or the "
                        "polar its times are worked out on."),
        "arguments": {
            "race_id": _race_id(),
            "gun_time": _gun_time("The warning signal is five minutes earlier."),
            "course_no": {"type": "integer", "description": "A fixed course number."},
            "target_length_min": {"type": "number",
                                  "description": ("Standard race only: a target in minutes used "
                                                  "to recommend a course.")},
            "series": {"type": "string",
                       "description": ("The series the race should be scored in, as the "
                                       "instruction says it. Use this for anything about putting "
                                       "an existing race into a series.")},
            "polar": {"type": "string",
                      "description": ("The polar the race's predicted times are worked out on -- a "
                                      "polar's name such as 'J70', or a boat whose design has one. "
                                      "It changes the race sheet's timings, never its results.")},
        },
    },
    {
        "name": "add_entries",
        "kind": WRITE,
        "description": ("Enter boats in a race: every active boat, named boats, or the same "
                        "boats as another race. Use this for anything about adding, entering "
                        "or putting boats in."),
        "arguments": {
            "race_id": _race_id(),
            "scope": {"type": "string", "enum": ["all_active", "boat", "same_as"],
                      "description": ("'all_active' for every active boat, 'boat' for named "
                                      "boats, or 'same_as' to enter the same boats as another "
                                      "race.")},
            "boat": {"type": "string",
                     "description": ("The boat names as the instruction says them when scope is "
                                     "'boat', comma separated for more than one, e.g. 'Mojito, "
                                     "Sgrech Bach'. The app matches each against the boat "
                                     "database and asks if it cannot.")},
            "same_as_race_id": {"type": "integer",
                                "description": ("When scope is 'same_as': the race whose boats "
                                                "to copy. Use for \"the same boats\", \"the same "
                                                "fleet as last time\".")},
        },
    },
    {
        "name": "set_custom_course",
        "kind": WRITE,
        "description": ("Make up a course from the club's marks and set it on the race, instead "
                        "of using a numbered one. Use for \"make me a course\", \"windward "
                        "leeward twice round\", and for changing a made-up course: give the "
                        "WHOLE sequence again with the change in it."),
        "arguments": {
            "race_id": _race_id(),
            "marks": {"type": "string",
                      "description": ("The marks in the order they are sailed, each with the "
                                      "hand it is left on: 'O port, 4 port, 2 starboard, O "
                                      "port'. Say 'port' or 'starboard' for each; a waypoint is "
                                      "simply passed and needs neither.")},
        },
        "required": ["marks"],
    },
    {
        "name": "boat_rating",
        "kind": READ,
        "description": ("What a boat is rated, by name or sail number. Looks in the club's boat "
                        "database and, if it is not there or has no rating, in the RORC IRC "
                        "listing and the YTC sheet."),
        "arguments": {
            "boat": {"type": "string",
                     "description": "The boat's name or sail number, e.g. 'Mojito' or 'GBR4822R'."},
        },
        "required": ["boat"],
    },
    {
        "name": "add_boat",
        "kind": WRITE,
        "description": ("Add a boat to the club's boat database from the IRC listing or the YTC "
                        "sheet. Only for a boat that is not in it already -- an existing record is "
                        "never overwritten."),
        "arguments": {
            "boat": {"type": "string",
                     "description": "The boat's name or sail number, as the listing has it."},
        },
        "required": ["boat"],
    },
    {
        "name": "look_up",
        "kind": READ,
        # The alternative was sending every mark, every course and every boat
        # with every command. The club has 24 marks, 67 courses and a boat
        # database: that does not belong in a request typed on 4G, and most of
        # it is not wanted most of the time. So the facts carry what is always
        # relevant, and this fetches the rest when it is asked for.
        "description": ("Detail that was not in the facts: where a mark is, what a course's legs "
                        "are, how many courses there are and how long each is, which boats the "
                        "club has, its series, past races with their series, course and start, "
                        "where the fleet has got to, the polar and the sail chart."),
        "arguments": {
            "topic": {"type": "string",
                      "enum": ["marks", "courses", "course", "boats", "series", "races", "fleet",
                               "polar", "sails"],
                      "description": ("'marks': positions of the club's marks and the "
                                      "start/finish line. 'courses': every fixed course and its "
                                      "length. 'course': one course's legs, distances and points "
                                      "of sail. 'boats': the boat database. 'series': the club's "
                                      "series, newest first. 'races': past races, newest first, "
                                      "each with its series, course, type and start. 'fleet': "
                                      "where each boat has got to on the water. 'polar': the boat "
                                      "speeds course timings are worked out from. 'sails': the "
                                      "sail chart.")},
            "course_no": {"type": "integer", "description": "With topic 'course'."},
            "query": {"type": "string",
                      "description": ("Optional: narrow it to one mark, boat, series or race "
                                      "name, e.g. 'O', 'Mojito' or 'ISORA'. With 'polar' or "
                                      "'sails', a wind strength such as '12'.")},
        },
        "required": ["topic"],
    },
    {
        "name": "mark_distance",
        "kind": READ,
        # Asked how far it was from O to the Causeway, the interpreter was told
        # it could not work things out and the app's table of distances covered
        # only the racing marks. It said so four times running while holding both
        # positions. This is the app's own measurement, the same `haversine_nm`
        # the leg analysis uses, for any two marks at all.
        "description": ("The distance in nautical miles and the bearing from one of the club's "
                        "marks to another, measured by the app. Use it for any distance or "
                        "bearing between marks rather than working one out from positions; for "
                        "several legs, ask for each leg in the same reply and add them up."),
        "arguments": {
            "from_mark": {"type": "string",
                          "description": "The mark's code or name, e.g. 'O', '4' or 'Causeway'."},
            "to_mark": {"type": "string",
                        "description": "The mark's code or name, e.g. 'C' or '2'."},
        },
        "required": ["from_mark", "to_mark"],
    },
    {
        "name": "race_results",
        "kind": READ,
        "description": ("Who finished where in a race that has been sailed, scored by the app: "
                        "each boat's finish time, elapsed and corrected time and the rating it "
                        "was scored on, class by class, and any boat that did not finish. Use "
                        "for results, finishing order, who won, margins and times."),
        "arguments": {
            "race_id": {"type": "integer", "description": "The race's number, if known."},
            "race_name": _race_name(),
            "rating": {"type": "string", "enum": ["IRC", "YTC"],
                       "description": "Optional: one rating system. Left out, both."},
        },
    },
    {
        "name": "series_standings",
        "kind": READ,
        "description": ("Where every boat stands in a series, scored by the app's own series "
                        "rules: rank, total points and each race's score with discards, class by "
                        "class. Use for who is leading, how many points, how far ahead."),
        "arguments": {
            "series": {"type": "string",
                       "description": ("The series as the race officer names it, e.g. 'the "
                                       "autumn series' or 'ISORA'.")},
            "rating": {"type": "string", "enum": ["IRC", "YTC"],
                       "description": "Optional: one rating system. Left out, both."},
            "class_name": {"type": "string", "description": "Optional: one class, e.g. 'IRC1'."},
        },
        "required": ["series"],
        "ask": "Which series?",
    },
    {
        "name": "race_log",
        "kind": READ,
        "race_scoped": True,
        "description": ("What happened in a race and when, from the app's own log: each horn "
                        "signal, flag, postponement, shortening and finish, with its time. Use "
                        "for 'when was the start?', 'when did AP go up?', 'what time did the "
                        "last boat finish?'."),
        "arguments": {
            "race_id": _race_id(),
            "race_name": _race_name(),
            "include_audio": {"type": "boolean",
                              "description": "Also list the spoken announcements. Only if asked."},
        },
    },
    {
        "name": "boat_history",
        "kind": READ,
        "description": ("Every race a boat has been entered in, most recent first, with its "
                        "series and where it finished on IRC and YTC. Use for a boat's season, "
                        "how many races it has sailed, its best or worst result."),
        "arguments": {
            "boat": {"type": "string", "description": "The boat's name or sail number."},
            "series": {"type": "string", "description": "Optional: only this series' races."},
        },
        "required": ["boat"],
        "ask": "Which boat?",
    },
    {
        "name": "race_entries",
        "kind": READ,
        "race_scoped": True,
        "description": ("The boats entered in a race: sail number, status, finish time if they "
                        "have one, and the IRC and YTC ratings the race is scored on -- taken "
                        "when each boat was entered, so they can differ from the boat database."),
        "arguments": {"race_id": _race_id(), "race_name": _race_name()},
    },
    {
        "name": "gps_finishes",
        "kind": READ,
        "race_scoped": True,
        "description": ("The finishes the trackers detected in a race: which are waiting for "
                        "somebody to confirm and which were recorded, with the detected times, "
                        "and whether the race records them by itself."),
        "arguments": {"race_id": _race_id(), "race_name": _race_name()},
    },
    {
        "name": "tracker_status",
        "kind": READ,
        "description": ("Each tracker: the boat it is on, when it last reported and its battery. "
                        "Use for 'is Mojito's tracker working?', 'which trackers are flat?'."),
        "arguments": {"boat": {"type": "string", "description": "Optional: one boat's tracker."}},
    },
    {
        "name": "wind_history",
        "kind": READ,
        "description": ("The wind the club's instrument recorded -- over the last so many minutes, "
                        "or through a race from its warning signal to its last finish: average "
                        "direction and strength, the range, the strongest gust and how it moved."),
        "arguments": {
            "minutes": {"type": "integer", "description": "How far back, up to 360. Default 60."},
            "race_id": {"type": "integer", "description": "A race's number, for its wind."},
            "race_name": _race_name(),
        },
    },
    {
        "name": "recommend_courses",
        "kind": READ,
        "description": ("The club's fixed courses the app would recommend for a race of a given "
                        "length, ranked the way its Recommend page ranks them: length, time on "
                        "the polar and legs by point of sail. The wind now unless another is given."),
        "arguments": {
            "target_minutes": {"type": "number", "description": "How long the race should be."},
            "twd": _wind("twd"),
            "tws": _wind("tws"),
            "boat": _polar_boat(),
        },
        "required": ["target_minutes"],
        "ask": "For how long a race?",
    },
    {
        "name": "suggest_course",
        "kind": READ,
        # Asked for "a course for this wind" the page could only offer the fixed
        # list, and the club's sheet is drawn for sixteen wind bands. The marks
        # are all there, so the app searches them itself and times every
        # candidate the way the race sheet would.
        "description": ("Made-up courses -- from any of the club's marks, not the fixed list -- that "
                        "come closest to a target time in the wind, found and timed by the app: a "
                        "windward-leeward, a triangle, or whatever fits best. Each comes with the "
                        "sequence to give set_custom_course if the race officer wants it."),
        "arguments": {
            "target_minutes": {"type": "number", "description": "How long the race should be."},
            "shape": {"type": "string",
                      "enum": ["windward_leeward", "triangle", "reaching", "any"],
                      "description": ("The kind of course asked for: 'reaching' when they want "
                                      "reaching legs in it. Default 'any'.")},
            "twd": _wind("twd"),
            "tws": _wind("tws"),
            "boat": _polar_boat(),
        },
        "required": ["target_minutes"],
        "ask": "For how long a race?",
    },
    {
        "name": "time_course",
        "kind": READ,
        "description": ("How long a course would take, leg by leg, on the polar: any sequence of "
                        "marks, or a fixed course by number. Distance, bearing, wind angle, point "
                        "of sail, sail and minutes for each leg. Use it to try a variation before "
                        "proposing it, or to answer 'how long would that take?'."),
        "arguments": {
            "marks": {"type": "string",
                      "description": ("The marks in order with the hand each is left on, e.g. "
                                      "'4 port, 7 port, O port'. Leave out to time a fixed course.")},
            "course_no": {"type": "integer", "description": "A fixed course, instead of marks."},
            "laps": {"type": "integer", "description": "Times round the marks given. Default 1."},
            "twd": _wind("twd"),
            "tws": _wind("tws"),
            "boat": _polar_boat(),
        },
        "ask": "Which course -- a number, or the marks in order?",
    },
    {
        "name": "boat_polar",
        "kind": READ,
        "description": ("A boat's polar -- its target speed at each true wind angle for each wind "
                        "strength, and its best angles upwind and down -- found from the boat's "
                        "design, or by the polar's own name. The app's timings use these; use it "
                        "too when a question is about how boats or wind angles compare."),
        "arguments": {
            "boat": {"type": "string",
                     "description": ("A boat's name, or a polar's name such as 'J109'. Left out, "
                                     "the club's default polar.")},
            "tws": {"type": "number", "description": "Optional: just this wind strength."},
        },
    },
    {
        "name": "shorten_course",
        "kind": WRITE,
        "description": ("End the race early at a mark the fleet is already sailing to: they round "
                        "it and go straight to the finish. Sounds two horn blasts."),
        "arguments": {
            "race_id": _race_id(),
            # at_mark, not just at_index: a model knows the mark is called "4",
            # never its position in the course sequence. Offered only an index it
            # could not know, it proposed a status report instead -- the same
            # failure as create_race with no gun_time.
            "at_mark": {"type": "string",
                        "description": ("The mark's name or code as the race officer says it, "
                                        "e.g. '4' or 'O'.")},
            "at_index": {"type": "integer",
                         "description": "Optional: the mark's position in the course, if known."},
        },
    },
    {
        "name": "boat_positions",
        "kind": READ,
        "race_scoped": True,
        # The race sheet's Position on the water rows, the replay, and a boat's
        # stored track: where the trackers say the boats are, and were.
        "description": ("Where the tracked boats are, from their trackers: each boat's position "
                        "(and how far and which way it is from the nearest mark), speed and "
                        "heading over the ground, how old the fix is, marks rounded, the next mark "
                        "with its distance and bearing, and distance still to sail. With at_time, "
                        "the fleet as it stood at that moment. With a boat and from_time/to_time, "
                        "that boat's track over the period -- where it went, distance sailed, "
                        "average and top speed. For 'where's Mojito', 'how fast are they going', "
                        "'who's nearest the mark', 'where was Jackdaw at 19:20', 'how fast did "
                        "Mojito sail the second beat'."),
        "arguments": {
            "race_id": _race_id(),
            "race_name": _race_name(),
            "boat": {"type": "string",
                     "description": "Optional: one boat, by name or sail number. Left out, every boat."},
            "at_time": {"type": "string",
                        "description": ("Optional: ISO 8601 local time, e.g. 2026-09-26T19:20, for "
                                        "where the boats were then rather than now.")},
            "from_time": {"type": "string",
                          "description": ("Optional, with a boat: ISO 8601 local start of the track "
                                          "to describe. Left out with to_time, the race's start.")},
            "to_time": {"type": "string",
                        "description": ("Optional, with a boat: ISO 8601 local end of the track. "
                                        "Left out with from_time, now or the end of the race.")},
        },
    },
    {
        "name": "weather_forecast",
        "kind": READ,
        # From the internet: the URL set in Settings, or Open-Meteo at the start
        # line (core.forecast). The club's instrument is the wind now; this is
        # the only thing in the app that says what it will do next.
        "description": ("The weather forecast for the club, from the internet: the wind hour by "
                        "hour -- direction, speed and gusts. For 'what's the wind going to do', "
                        "'will it build during the race', 'is it going to veer', 'what's it doing "
                        "tomorrow morning'. A forecast, not a measurement: the wind now is the "
                        "club's own instrument, in the facts."),
        "arguments": {
            "race_id": _race_id("Defaults to the race the conversation is about; the forecast "
                                "then covers that race's hours."),
            "from_time": {"type": "string",
                          "description": ("Optional: ISO 8601 local start of the period, e.g. "
                                          "2026-09-26T10:00, for a time that is not the race's.")},
            "to_time": {"type": "string",
                        "description": "Optional: ISO 8601 local end of the period."},
        },
    },
    {
        "name": "finish_boat",
        "kind": WRITE,
        # The Finish button on the race sheet, from the water: the same call
        # (core.raceadmin.finish_entry_now), so the same horn, race-log entry and
        # finish clip. The club's decision -- the horn sounding for a finish is
        # the finish, not a horn on command.
        "description": ("Record that a boat has finished, NOW, and sound the horn -- exactly what "
                        "the Finish button on the race sheet does. The finish time is the moment "
                        "the race officer agrees to it. For 'Mojito has finished', 'finish "
                        "Jackdaw', 'that's Mojito over the line'. One boat a call: two boats "
                        "finishing together are two calls."),
        "arguments": {
            "race_id": _race_id(),
            "boat": {"type": "string",
                     "description": ("The boat as the race officer says it: its name or its "
                                     "sail number. The app matches it against the race's "
                                     "entries.")},
        },
        "required": ["boat"],
    },
    {
        "name": "postpone_race",
        "kind": WRITE,
        # The club used to delay a start by moving the start time, which signals
        # nothing to the fleet. This is the tool that makes the correct thing the
        # easy thing, so its description says what it is *for*, not just what it does.
        "description": ("Postpone a race that has not started -- the wind has died, the line is "
                        "not ready, the fleet is not there. Flies AP and sounds two horn blasts. "
                        "Use this rather than moving the start time: the warning signal is then "
                        "made one minute after AP is lowered, so there is no need to guess when "
                        "the wind will return."),
        "arguments": {
            "race_id": _race_id(),
            "kind": {"type": "string", "enum": ["AP", "AP over H", "AP over A"],
                     "description": ("'AP' (the default), 'AP over H' (postponed, further signals "
                                     "ashore) or 'AP over A' (postponed, no more racing today).")},
        },
    },
    {
        "name": "resume_race",
        "kind": WRITE,
        "description": ("Say when AP comes down on a postponed race. One horn blast at that "
                        "moment, and the warning signal one minute later. Use when the wind is "
                        "back. Leave the time out unless the race officer names one -- the app "
                        "picks the next whole minute, which is what keeps the gun on a round "
                        "number."),
        "arguments": {
            "race_id": _race_id(),
            "lower_at": {"type": "string",
                         "description": ("ISO 8601 local date and time, whole minutes, if the race "
                                         "officer names when the flag comes down, e.g. 'in five "
                                         "minutes'.")},
            "warning_time": {"type": "string",
                             "description": ("Only needed after AP over H, which has no one-minute "
                                             "rule because the next signals are made ashore.")},
        },
    },
    {
        "name": "race_status",
        "kind": REPORT,
        "description": ("Shows the race officer the status report for a race -- its gun, course, "
                        "expected time round and how many boats are racing -- exactly as the app "
                        "writes it, and ends your turn. Use it when they ask for the status as "
                        "such; to answer a particular question, use the facts or a look-up."),
        "arguments": {"race_id": _race_id()},
    },
]

TOOL_NAMES = {t["name"] for t in TOOLS}
TOOL_KINDS = {t["name"]: t.get("kind", WRITE) for t in TOOLS}
# Several changes asked for in one sentence -- "use course 4 and add Mojito" --
# read back together and agreed to once. Not in TOOLS: a model never names it.
# The parser builds one from the tool calls it was given, and `resolve` reads
# each step exactly as it would read that step alone.
PLAN = "plan"
# What a parser is allowed to come back with: an action this app implements, or
# a reply in words. Anything else is dropped -- a model may propose anything, and
# what this app will do is decided here rather than by what came back.
PARSEABLE_NAMES = TOOL_NAMES | {ANSWER, PLAN}


def tool_kind(name: str) -> str:
    """What a call does to the conversation. Anything unknown counts as a
    change, so it ends the turn and reaches `parse_command`, which drops it."""
    return TOOL_KINDS.get(name, WRITE)


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
        m = re.search(r"\bcalled\s+([\w' ]+?)(?:\s+at\b|\s+in\b|\s*$)", said)
        if m:
            args["name"] = original[m.start(1):m.end(1)].strip()
        # A new race is asked which series it is in unless the sentence says.
        if re.search(r"\bin\s+no\s+series\b", said):
            args["series"] = NO_SERIES
        else:
            m = re.search(r"\bin\s+(?:the\s+)?series\s+(.+?)(?:\s+at\b|$)", said)
            if m:
                args["series"] = original[m.start(1):m.end(1)].strip()
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
    # A boat by name or sail number; the caller matches it against the entries.
    "boat": re.compile(r"^(?:it'?s\s+|it was\s+)?(.{1,40}?)\.?$", re.I),
}

# Answering "what is it called?" and "which series?" in words rather than with
# a button. What is left once "call it" or "in the ... series" is taken off is
# the answer -- unless it plainly starts a new instruction or declines.
_CALLED = re.compile(r"^(?:(?:call|name) it|it'?s called|it is called|called)\s+", re.I)
_A_SERIES = re.compile(r"^(?:it'?s\s+|it is\s+)?(?:in\s+)?(?:the\s+)?|\s+series$", re.I)
_NOT_AN_ANSWER = re.compile(
    r"^(?:no|yes|ok|okay|cancel|stop|wait|don'?t|never ?mind|make|change|use|add|set|put|"
    r"what|which|how|why|when|where|can|could|is|are|do|does|shorten|postpone|finish|"
    r"recommend|suggest|status)\b", re.I)


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
        # The course board with spaces rather than commas: "4p 7p Op". Read as
        # one item it was a mark called "4P" rounded "7p op" -- every word here
        # is a mark and its hand, so each is its own item.
        expanded: List[Any] = []
        for part in items:
            words = str(part).split()
            if len(words) > 1 and all(re.fullmatch(r"[A-Za-z0-9]{1,3}[psPS]", w) for w in words):
                expanded.extend(words)
            else:
                expanded.append(part)
        items = expanded
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
    if name == PLAN:
        # A question about one step of a plan names the step: "1:race_type". The
        # answer goes into that step and the whole plan is read again, so the
        # other half of "use course 4 and add Mojito" is not lost to a question
        # about the first.
        index_text, _, inner = field_name.partition(":")
        steps = [dict(step) for step in (arguments or {}).get("steps") or []]
        try:
            index = int(index_text)
        except ValueError:
            return None
        if not inner or not 0 <= index < len(steps):
            return None
        answered = answer_to_question(str(steps[index].get("name") or ""),
                                      dict(steps[index].get("arguments") or {}),
                                      inner, options, text)
        if answered is None:
            return None
        steps[index] = {"name": answered.name, "arguments": answered.arguments}
        return Intent(PLAN, {"steps": steps}, source="reply")
    lowered = said.lower()
    if len(lowered.split()) > 6:
        # A whole sentence is a new instruction. "standard" and "it's a standard
        # race" are answers; a fresh command is not.
        return None
    if field_name == "name":
        # Any name is an answer, not only the one suggested -- and matched
        # whole: "Evening Race 12" contains the suggestion "Evening Race", and
        # reading it as that would have named the race after the last one.
        typed = _CALLED.sub("", said).strip(" .'\"")
        # "Autumn, call it Autumn 3" is two answers at once: the interpreter's.
        if not typed or "," in typed or _NOT_AN_ANSWER.match(typed):
            return None
        return Intent(name, {**(arguments or {}), "name": typed}, source="reply")
    if field_name == "series":
        if _means_no_series(said):
            return Intent(name, {**(arguments or {}), "series": NO_SERIES}, source="reply")
        for option in options or []:
            if lowered == str(option).lower():
                return Intent(name, {**(arguments or {}), "series": option}, source="reply")
        # Another series, as it was said: the caller matches it against the
        # club's and asks again if it matches none.
        typed = _A_SERIES.sub("", lowered).strip(" .")
        if not typed or "," in typed or _NOT_AN_ANSWER.match(typed):
            return None
        return Intent(name, {**(arguments or {}), "series": typed}, source="reply")
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
    if intent.name == PLAN:
        # Every step must be a change this app implements. One that is not sinks
        # the whole plan rather than being dropped from it: carrying out half of
        # what was asked, when the read-back cannot say which half, is worse than
        # asking again.
        steps = [step for step in (intent.arguments.get("steps") or []) if isinstance(step, dict)]
        if not steps or any(tool_kind(str(step.get("name") or "")) != WRITE
                            or step.get("name") not in TOOL_NAMES for step in steps):
            return None
        if len(steps) == 1:
            return Intent(str(steps[0]["name"]), dict(steps[0].get("arguments") or {}),
                          source=intent.source)
        return Intent(PLAN, {"steps": [{"name": str(step["name"]),
                                        "arguments": dict(step.get("arguments") or {})}
                                       for step in steps]}, source=intent.source)
    return intent


# ---------------------------------------------------------------------------
# Turning an intent into something a person can confirm
# ---------------------------------------------------------------------------

def _hhmm(when: datetime) -> str:
    return when.strftime("%H:%M")


# A model's arguments can still arrive as strings whatever the schema says --
# "true", "90", "2026-08-15T11:00" -- because not every provider holds a model to
# it. The grammar produces real types. Everything below is read
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


NO_SERIES = "no series"
_NO_SERIES = re.compile(r"^(?:no|none|nope|no series|not in (?:a|any) series|without a series"
                        r"|it'?s not in (?:a|one|any))$")


def _means_no_series(said: Any) -> bool:
    """"No series" and the ways of saying it, as against a series that is called something."""
    return bool(_NO_SERIES.match(" ".join(str(said or "").lower().strip(" .!").split())))


def _ask_series(intent: Intent, context: CommandContext) -> Resolution:
    """Which series a new race is in, suggesting the one the last race was in.

    A race in a series is entered with the boats already racing in it, so this
    is the fleet as well as the scoring. Before, an unnamed series meant no
    series and no boats, and the only quick way to a fleet was every active boat
    in the database -- most of which are not racing.
    """
    races = context.last_races or []
    last = races[0] if races else None
    in_series = next((r for r in races if r.get("series")), None)
    if last and last.get("series"):
        said = f" The last race, '{last['name']}' {last.get('when', '')}".rstrip() \
               + f", was in {last['series']}."
        options = [last["series"], NO_SERIES]
    elif last and in_series:
        said = (f" The last race, '{last['name']}' {last.get('when', '')}".rstrip()
                + ", was in no series; the last one in a series was "
                + f"'{in_series['name']}' {in_series.get('when', '')}".rstrip()
                + f", in {in_series['series']}.")
        options = [NO_SERIES, in_series["series"]]
    else:
        said, options = "", [NO_SERIES]
    return Resolution(
        status=NEEDS_CLARIFICATION, intent=intent.name,
        question=("Which series is the new race in?" + said
                  + " A race in a series is entered with the boats already racing in it."),
        options=options, clarify_field="series")


def _ask_name(intent: Intent, context: CommandContext, series: str) -> Resolution:
    """What a new race is called, suggesting what the last one in its series was."""
    races = context.last_races or []
    if series and not _means_no_series(series):
        wanted = series.lower()
        same = next((r for r in races if r.get("series")
                     and (r["series"].lower() == wanted or wanted in r["series"].lower())), None)
        said = f" The last race in {same['series']} was '{same['name']}'." if same else ""
    else:
        same = next((r for r in races if not r.get("series")), None)
        said = f" The last race outside a series was '{same['name']}'." if same else ""
    return Resolution(
        status=NEEDS_CLARIFICATION, intent=intent.name,
        question="What is the new race called?" + said,
        options=[same["name"]] if same and same.get("name") else [],
        clarify_field="name")


def _boat_names(said: Any) -> List[str]:
    """"Sgrech Bach and Mojito" is one instruction about two boats. Read as one
    name it entered the first and dropped the second without a word."""
    return [part.strip() for part in re.split(r",| and ", str(said or "")) if part.strip()]


# What each change is called when it cannot be folded into a race that does not
# exist yet, so the question can say what to ask for afterwards.
_LATER = {"set_custom_course": "the made-up course", "shorten_course": "the shortening",
          "postpone_race": "the postponement", "resume_race": "lowering AP"}


def _folded_into_the_new_race(steps: List[Intent]) -> Tuple[List[Intent], str]:
    """A plan that creates a race, with everything aimed at that race put into it.

    "Create a race at 11, course 4, and add all the boats" can come back from a
    model as three calls. The second and third name no race, so read one at a
    time they would change *the current one* -- a different race from the one
    being made, and the read-back would say so in words nobody reads twice. The
    new race can carry its course, its series, its time and its boats itself, so
    those are folded in. What it cannot carry is asked for once it exists.

    Returns the steps and, if they cannot be folded, the question to ask instead.
    """
    creates = [step for step in steps if step.name == "create_race"]
    if not creates:
        return steps, ""
    if len(creates) > 1:
        return steps, "One new race at a time, please. Which should I create first?"
    create = creates[0]
    merged = dict(create.arguments)
    before, after = [], []
    for step in steps:
        if step is create:
            continue
        args = step.arguments
        if step.name == "add_boat":
            # Into the boat database first, so the new race can enter it.
            before.append(step)
            continue
        if _as_int(args.get("race_id")):
            # A race named by number is an existing race, not the new one.
            after.append(step)
            continue
        if step.name == "add_entries":
            scope = str(args.get("scope") or "").strip().lower()
            if args.get("boat"):
                merged["boats"] = ", ".join(n for n in (str(merged.get("boats") or "").strip(),
                                                        str(args["boat"]).strip()) if n)
            elif scope in ("", "all_active"):
                merged["add_all_active"] = True
            else:
                return steps, ("I can create the race first and then copy another race's boats "
                               "into it. Shall I create it?")
            continue
        if step.name == "set_start_and_course":
            for said, into in (("gun_time", "gun_time"), ("course_no", "course_no"),
                               ("series", "series"), ("target_length_min", "length_min")):
                if args.get(said) not in (None, "") and merged.get(into) in (None, ""):
                    merged[into] = args[said]
            continue
        later = _LATER.get(step.name, step.name.replace("_", " "))
        return steps, f"I can create the race first; ask for {later} once it exists."
    return before + [Intent("create_race", merged, source=create.source)] + after, ""


# The reads with no rule of their own to apply: their arguments are read to their
# schema types and checked for what is required, and a read about a race defaults
# to the race the conversation is about. `resolve` keeps its own branches for the
# older tools, whose read-backs and questions the tests pin down.
GENERIC_READS = {"series_standings", "race_log", "boat_history", "race_entries", "gps_finishes",
                 "tracker_status", "wind_history", "recommend_courses", "suggest_course",
                 "time_course", "boat_polar", "weather_forecast", "boat_positions"}
TOOL_BY_NAME = {tool["name"]: tool for tool in TOOLS}


def _typed(value: Any, spec: Dict[str, Any]) -> Any:
    """One argument as its schema says it should be, or None."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    kind = spec.get("type")
    if kind == "integer":
        return _as_int(value)
    if kind == "number":
        return _as_number(value)
    if kind == "boolean":
        return _as_bool(value)
    said = str(value).strip()
    if spec.get("enum"):
        # "irc" is IRC. A word that is none of the choices is not a choice.
        return next((choice for choice in spec["enum"] if choice.lower() == said.lower()), None)
    return said


def _resolve_read(intent: Intent, context: CommandContext) -> Resolution:
    tool = TOOL_BY_NAME[intent.name]
    resolved: Dict[str, Any] = {}
    for name, spec in tool["arguments"].items():
        value = _typed(intent.arguments.get(name), spec)
        if value is not None:
            resolved[name] = value
    missing = [name for name in tool.get("required", []) if name not in resolved]
    if missing:
        return Resolution(status=NEEDS_CLARIFICATION, intent=intent.name,
                          question=tool.get("ask") or f"What {missing[0].replace('_', ' ')}?",
                          clarify_field=missing[0])
    if intent.name == "time_course" and not resolved.get("marks") and not resolved.get("course_no"):
        return Resolution(status=NEEDS_CLARIFICATION, intent=intent.name,
                          question=tool["ask"], clarify_field="marks")
    if tool.get("race_scoped") and not resolved.get("race_id") and not resolved.get("race_name"):
        if not context.current_race_id:
            return Resolution(status=NEEDS_CLARIFICATION, intent=intent.name,
                              question="Which race?", clarify_field="race_id")
        resolved["race_id"] = int(context.current_race_id)
    # Read-only: nothing to undo, nothing to confirm.
    return Resolution(status=NEEDS_CONFIRMATION, intent=intent.name,
                      readback=f"Look up {intent.name.replace('_', ' ')}.", resolved=resolved)


def _resolve_plan(intent: Intent, context: CommandContext) -> Resolution:
    """Several changes, read back as one and agreed to once, in the order given."""
    steps = [Intent(str(step.get("name") or ""), dict(step.get("arguments") or {}),
                    source=intent.source)
             for step in intent.arguments.get("steps") or []]
    steps, problem = _folded_into_the_new_race(steps)
    if problem:
        return Resolution(status=NEEDS_CLARIFICATION, intent=PLAN, question=problem)
    if len(steps) == 1:
        one = resolve(steps[0], context)
        # A question about the folded race is answered into the folded race.
        one.arguments = dict(steps[0].arguments)
        return one
    as_steps = [{"name": step.name, "arguments": step.arguments} for step in steps]
    resolutions = []
    for index, step in enumerate(steps):
        one = resolve(step, context)
        if one.status == NEEDS_CLARIFICATION:
            return Resolution(status=NEEDS_CLARIFICATION, intent=PLAN, question=one.question,
                              options=one.options,
                              clarify_field=f"{index}:{one.clarify_field}" if one.clarify_field else "",
                              arguments={"steps": as_steps})
        if one.status != NEEDS_CONFIRMATION:
            return one
        resolutions.append(one)
    readback = "In this order: " + " ".join(f"({n}) {one.readback}"
                                            for n, one in enumerate(resolutions, 1))
    return Resolution(
        status=NEEDS_CONFIRMATION, intent=PLAN, readback=readback,
        resolved={"steps": [{"intent": one.intent, "resolved": one.resolved,
                             "readback": one.readback} for one in resolutions]},
        warnings=[warning for one in resolutions for warning in one.warnings])


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

    if intent.name == PLAN:
        return _resolve_plan(intent, context)

    if intent.name in GENERIC_READS:
        return _resolve_read(intent, context)

    if intent.name == "create_race":
        args = intent.arguments
        race_type = str(args.get("race_type") or "").strip().lower() or None
        if race_type not in (None, "standard", "pursuit"):
            race_type = None
        length = _as_number(args.get("length_min", args.get("pursuit_duration_min")))
        # Same words, different code path: ask rather than pick one.
        if length is not None and not race_type:
            return _clarify_duration(intent)
        series = str(args.get("series") or "").strip()
        if context.club_series is not None:
            # The series first, because it decides the suggestion for the name:
            # what the last race in that series was called.
            if not series and context.club_series:
                return _ask_series(intent, context)
            if not str(args.get("name") or "").strip():
                return _ask_name(intent, context, series)
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
        course_no = _as_int(args.get("course_no"))
        if course_no is not None:
            # Named, so it is the race officer's choice; the caller checks the
            # course exists and does not offer one for the wind instead.
            resolved["course_no"] = course_no
            parts.append(f"course {course_no}")
        # The series is matched against the club's own list by the caller, which
        # is the only thing that can: a name here is what was said, not a series.
        if series and not _means_no_series(series):
            resolved["series_name"] = series
            parts.append(f"in series {series}")
        else:
            parts.append("in no series")
        names = _boat_names(args.get("boats"))
        if resolved["add_all_active"]:
            parts.append("adding every active boat")
        elif names:
            # Matched against the boat database by the caller, as add_entries is.
            resolved["boat_names"] = names
            parts.append("adding " + " and ".join(names))
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
        polar = str(args.get("polar") or "").strip()
        if polar:
            # Matched against the club's polars by the caller, as a series is.
            resolved["polar_name"] = polar
            parts.append(f"timed on the {polar} polar")
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
        names = _boat_names(args.get("boat"))
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

    if intent.name == "finish_boat":
        args = intent.arguments
        race_id = _as_int(args.get("race_id")) or context.current_race_id
        boat = str(args.get("boat") or "").strip()
        if not race_id:
            return Resolution(status=NEEDS_CLARIFICATION, intent=intent.name,
                              question="Which race is the boat finishing?", clarify_field="race_id")
        if not boat:
            return Resolution(status=NEEDS_CLARIFICATION, intent=intent.name,
                              question="Which boat has finished?", clarify_field="boat")
        # Matched against the race's entries by the caller, which has them. The
        # time is said to be the yes, not the sentence: that is when it is taken.
        return Resolution(
            status=NEEDS_CONFIRMATION, intent=intent.name,
            readback=(f"Finish {boat} in {_which_race(context, int(race_id))} now, and sound "
                      "the horn. The finish time is the moment you say yes."),
            resolved={"race_id": int(race_id), "boat": boat})

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
        # here; otherwise the first minute the flag can be announced for, with
        # time left to agree to it -- "AP down" means as soon as it can be done.
        lower_at = _as_datetime(args.get("lower_at"), context.now)
        resolved["lower_at_named"] = bool(lower_at)
        if lower_at:
            lower_at = lower_at.replace(second=0, microsecond=0)
        else:
            lower_at = datetime.fromisoformat(next_whole_minute(
                context.now, at_least_seconds=LOWER_AP_MIN_LEAD_S + AGREE_ALLOWANCE_S))
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

    if intent.name == "mark_distance":
        one = str(intent.arguments.get("from_mark") or "").strip()
        two = str(intent.arguments.get("to_mark") or "").strip()
        if not one or not two:
            return Resolution(status=NEEDS_CLARIFICATION, intent=intent.name,
                              question="Between which two marks?")
        # Read-only: nothing to undo, nothing to confirm.
        return Resolution(status=NEEDS_CONFIRMATION, intent=intent.name,
                          readback=f"Measure from {one} to {two}.",
                          resolved={"from_mark": one, "to_mark": two})

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
        rating = _typed(args.get("rating"), {"type": "string", "enum": ["IRC", "YTC"]})
        if rating:
            resolved["rating"] = rating
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
