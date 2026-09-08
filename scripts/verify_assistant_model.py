"""Measure how well the configured model reads race-officer commands.

Not part of the test suite: it needs an API key and every run costs the club a
little money. The suite proves the wiring with a stub; this answers the question
the suite cannot -- is the model actually better than the built-in grammar at
the sentences people type?

    python scripts/verify_assistant_model.py            # the configured model
    python scripts/verify_assistant_model.py --grammar  # the fallback, for comparison
    python scripts/verify_assistant_model.py --repeat 3 # models are not deterministic

Nothing is executed. Every sentence is interpreted and read back only, so this
is safe to run against the live race database.
"""
import argparse
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app  # noqa: F401,E402 — registers the schema builder so settings load
from core.assistant import (  # noqa: E402
    NEEDS_CLARIFICATION,
    CommandContext,
    grammar_parse,
    parse_command,
    resolve,
)
from core.assistant_llm import parser_from_config  # noqa: E402
from core.horn import hardware_config  # noqa: E402

# (what somebody says, the tool it should choose or None, a phrase the read-back
# must contain). The phrases are the parts that would be wrong silently: a time
# read as the warning signal instead of the gun, a length put on the wrong kind
# of race.
CASES = [
    ("create a race at 11am", "create_race", "first gun 11:00"),
    ("we'll get going about half eleven, standard race", "create_race", "first gun 11:30"),
    ("new pursuit race at 11, hour and a half", "create_race", "running for 90 minutes"),
    ("make the gun quarter past two", "set_start_and_course", "first gun 14:15"),
    ("use course 7", "set_start_and_course", "course 7"),
    ("put every boat in the race please", "add_entries", "every active boat"),
    ("enter the IRC 1 fleet", "add_entries", "IRC 1"),
    ("how's it going out there", "race_status", "status"),
    ("where are we", "race_status", "status"),
    ("wind's dropped, finish them at mark 4", "shorten_course", "mark 4"),
    ("shorten at 8", "shorten_course", "mark 8"),
    # Must ask rather than guess: a length with no race type means different
    # things to a pursuit and to a standard race.
    ("create a race at 11, an hour long", "create_race", "standard race or a pursuit"),
    # Must refuse: not a command, or a command this app does not offer.
    ("what do you reckon to the cricket", None, ""),
    ("arm the start sequence", None, ""),
    ("delete race 5", None, ""),
    ("tell the fleet I'm running late", None, ""),
]


def run_once(parser, context):
    passed, failures = 0, []
    for said, expected_intent, expected_phrase in CASES:
        intent = parse_command(said, context, parser)
        answer = resolve(intent, context)
        got = intent.name if intent else None
        text = answer.readback or answer.question or ""
        ok = (got == expected_intent)
        if ok and expected_phrase:
            ok = expected_phrase.lower() in text.lower()
        if expected_intent is None:
            ok = got is None
        if ok:
            passed += 1
        else:
            failures.append((said, expected_intent or "no command", got or "no command",
                             answer.status, text[:90]))
    return passed, failures


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--grammar", action="store_true", help="test the fallback grammar instead")
    ap.add_argument("--repeat", type=int, default=1, help="runs, since a model is not deterministic")
    args = ap.parse_args()

    cfg = hardware_config()
    model = None if args.grammar else parser_from_config(cfg)
    if not args.grammar and model is None:
        print("No model configured (Settings -> On the water). Use --grammar to test the fallback.")
        return 1

    def layered(text, context):
        """What the endpoint uses: the model, with the grammar underneath."""
        return model(text, context) or grammar_parse(text, context)

    parser = grammar_parse if args.grammar else layered
    label = "built-in grammar" if args.grammar else (cfg.get("assistant_model") or "configured model")
    context = CommandContext(now=datetime(2026, 8, 15, 9, 40), current_race_id=597,
                             current_race_name="Club Race")

    print(f"{label}: {len(CASES)} cases, {args.repeat} run(s)\n")
    total = 0
    for run in range(args.repeat):
        passed, failures = run_once(parser, context)
        total += passed
        print(f"  run {run + 1}: {passed}/{len(CASES)}")
        for said, want, got, status, text in failures:
            print(f"     MISS  {said!r}\n           wanted {want}, got {got} ({status}) — {text}")
    print(f"\n{total}/{len(CASES) * args.repeat} overall")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
