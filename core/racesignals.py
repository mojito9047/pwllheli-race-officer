"""The race signals themselves: what AP means, and what it is called.

A leaf module with no imports, for the same reason `static/signal_flags.js`
exists on the browser side. Four layers need this vocabulary and they cannot
share it any other way:

* `core.assistant` turns "hold the start, the wind's gone" into a proposal, and
  imports nothing but the standard library on purpose -- it interprets and
  decides nothing, and a test holds it to that.
* `core.raceadmin` performs the postponement.
* `core.startsequence` asks whether a race is postponed before sounding a horn.
* the race sheet, competitor pages and clubhouse display name the flag.

The alternative was a second copy of "AP_H means AP over H" in the interpreter,
and a mapping that exists twice is a mapping that disagrees with itself the
first time a third option is added.

The meanings are the wording from the Race Signals, because that is what the
race officer is reading back before agreeing to it, and paraphrasing a rule in
a read-back is how you agree to something other than what you meant.
"""
from __future__ import annotations

from typing import Any, Dict

# Race Signals, AP: "The warning signal will be made 1 minute after AP is
# removed." Only plain AP carries that rule -- see `one_minute_rule` below.
RESUME_WARNING_DELAY_S = 60

# One minute before AP comes down, the app says so over the central audio, so
# that the fleet is looking at the mast when the flag moves. That announcement
# needs room to be spoken, so the moment the race officer chooses has to be
# further off than the announcement that precedes it: a flag lowered forty
# seconds from now is one nobody was told about.
AP_DOWN_WARNING_LEAD_S = 60
LOWER_AP_MIN_LEAD_S = 90

POSTPONEMENT_KINDS: Dict[str, Dict[str, Any]] = {
    "AP": {
        "flag": "AP",
        "meaning": "Races not started are postponed. The warning signal will be made one "
                   "minute after AP is removed.",
        "one_minute_rule": True,
    },
    "AP_H": {
        "flag": "AP over H",
        "meaning": "Races not started are postponed. Further signals ashore.",
        # Ashore, the next signals are made ashore: there is no one-minute
        # warning hanging off this flag coming down.
        "one_minute_rule": False,
    },
    "AP_A": {
        "flag": "AP over A",
        "meaning": "Races not started are postponed. No more racing today.",
        "one_minute_rule": False,
    },
}


def normalise_postponement_kind(kind: Any) -> str:
    """Read "AP over H", "ap_h", "AP/H" or "AP H" as the one stored kind.

    A race officer types the flag the way it is spoken and a model repeats it
    however it likes; the database should hold one spelling.
    """
    text = str(kind or "AP").strip().upper().replace("OVER", " ").replace("/", " ")
    text = "_".join(part for part in text.replace("_", " ").split() if part)
    return text or "AP"


def postponement_flag_name(kind: Any) -> str:
    """The flag as it is written on a race sheet: "AP", "AP over H", "AP over A"."""
    spec = POSTPONEMENT_KINDS.get(normalise_postponement_kind(kind))
    return spec["flag"] if spec else ""
