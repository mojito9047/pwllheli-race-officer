"""What the app understands, and what it refuses to guess at.

A golden set: sentences in, intents and read-backs out. It runs with no network
and no provider account, because every parse here goes through either the
deterministic grammar or a stub parser standing in for a model. That is the
point of the seam -- the expensive, non-deterministic part is a function the
caller supplies, so the rules around it can be pinned in CI.

Nothing in this file executes a command. `resolve` produces a read-back or a
question and stops; the tests assert that it stays that way.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from core import assistant
from core.assistant import (
    ANSWER,
    ANSWERED,
    NEEDS_CLARIFICATION,
    NEEDS_CONFIRMATION,
    NOT_UNDERSTOOD,
    TOOL_NAMES,
    CommandContext,
    Intent,
    answer_to_question,
    grammar_parse,
    parse_command,
    resolve,
)

# A Saturday morning, before racing.
NOW = datetime(2026, 8, 15, 9, 40, 0)


def ctx(**kwargs) -> CommandContext:
    return CommandContext(now=NOW, **kwargs)


def interpret(text: str, parser=grammar_parse, **kwargs):
    context = ctx(**kwargs)
    return resolve(parse_command(text, context, parser), context)


class TestTheSentencesPeopleActuallyType:
    @pytest.mark.parametrize("said,expected", [
        ("start a new race at 11am, an hour long", "create_race"),
        ("create a race called Wednesday Evening at 6.30pm", "create_race"),
        ("new pursuit race at 11, 90 minutes", "create_race"),
        ("add all the boats", "add_entries"),
        ("add fleet IRC1", "add_entries"),
        ("shorten at mark 4", "shorten_course"),
        ("shorten the course on 8", "shorten_course"),
        ("status", "race_status"),
        ("how is the race going", "race_status"),
        ("set the gun for 11:00", "set_start_and_course"),
        ("use course 7", "set_start_and_course"),
    ])
    def test_the_grammar_finds_the_right_intent(self, said, expected):
        intent = parse_command(said, ctx(current_race_id=57))
        assert intent is not None, f"no intent found in {said!r}"
        assert intent.name == expected

    @pytest.mark.parametrize("said", [
        "what do you think of the weather",
        "tell the fleet I'll be late",
        "",
        "protest CRACKAJACK for barging",
    ])
    def test_anything_else_is_said_plainly_not_guessed_at(self, said):
        answer = interpret(said, current_race_id=57)
        assert answer.status == NOT_UNDERSTOOD
        assert answer.question


class TestTheTwoAmbiguitiesTheClubsWordsCarry:
    def test_start_at_eleven_means_the_gun_and_the_readback_shows_both(self):
        """The stored column is the warning signal and the gun is five minutes
        later. A read-back showing one of them cannot be checked by the person
        reading it."""
        answer = interpret("start a race at 11am")
        assert answer.status == NEEDS_CONFIRMATION
        assert answer.resolved["first_gun_time"].endswith("11:00")
        assert answer.resolved["first_warning_time"].endswith("10:55")
        assert "10:55" in answer.readback and "11:00" in answer.readback

    def test_a_length_with_no_race_type_is_a_question_not_a_guess(self):
        """"An hour" is a pursuit's actual period, or a standard race's target
        for recommending a course. Same words, different code path."""
        answer = interpret("create a race at 11, an hour long")
        assert answer.status == NEEDS_CLARIFICATION
        assert set(answer.options) == {"standard", "pursuit"}
        assert "pursuit" in answer.question.lower()

    def test_a_pursuit_length_is_its_period(self):
        answer = interpret("new pursuit race at 11, an hour long")
        assert answer.resolved["pursuit_duration_min"] == 60.0
        assert "target" not in answer.readback.lower()

    def test_a_standard_race_length_is_only_a_target(self):
        answer = interpret("create a standard race at 11, an hour long",
                           parser=lambda t, c: Intent("create_race",
                                                      {"race_type": "standard", "length_min": 60,
                                                       "gun_time": "2026-08-15T11:00"}))
        assert answer.resolved["target_length_min"] == 60.0
        assert "target" in answer.readback.lower()
        assert "pursuit_duration_min" not in answer.resolved


class TestTimesAreAbsoluteAndTheHutsOwn:
    @pytest.mark.parametrize("said,expect", [
        ("race at 11am", "11:00"),
        ("race at 11:05", "11:05"),
        ("race at 6.30pm", "18:30"),
        ("race at 1105 hrs", "11:05"),
        ("race at 3", "15:00"),          # no club race starts at 03:00
    ])
    def test_the_clock_time_is_read_the_way_a_sailor_means_it(self, said, expect):
        answer = interpret("create a " + said)
        assert answer.resolved["first_gun_time"].endswith(expect)

    def test_a_time_already_past_today_is_tomorrow(self):
        """Asked for "at 9" during the Sunday debrief, nobody means a race that
        started forty minutes ago."""
        answer = interpret("create a race at 9am")
        assert answer.resolved["first_gun_time"].startswith("2026-08-16")

    def test_the_readback_gives_absolute_times_never_relative_ones(self):
        """"in twenty minutes" cannot be checked by the person reading it and
        means something different by the time they press yes."""
        import re as _re
        answer = interpret("create a race at 11am")
        assert not _re.search(r"\bin \d+\s*(min|hour)", answer.readback)
        assert "11:00" in answer.readback


class TestItAsksRatherThanAssuming:
    def test_shortening_with_no_mark_asks_which(self):
        answer = interpret("shorten the course", current_race_id=57)
        assert answer.status == NEEDS_CLARIFICATION
        assert "mark" in answer.question.lower()

    def test_a_command_with_no_current_race_asks_which_race(self):
        answer = interpret("add all the boats")
        assert answer.status == NEEDS_CLARIFICATION
        assert "race" in answer.question.lower()

    def test_the_readback_names_the_race_it_would_change(self):
        """"The current race" is the latest one with boats still racing, which
        is not always the one being talked about -- so it is named, not implied."""
        answer = interpret("add all the boats", current_race_id=57,
                           current_race_name="Autumn Series R3")
        assert "Autumn Series R3" in answer.readback

    def test_shortening_says_that_it_sounds_the_horn(self):
        answer = interpret("shorten at mark 4", current_race_id=57)
        assert "horn" in answer.readback.lower()


class TestTheModelIsNotTrusted:
    """The parser seam takes whatever a provider returns; the app decides what
    it is willing to do with it."""

    def test_a_tool_the_app_does_not_have_is_dropped(self):
        rogue = lambda text, context: Intent("delete_everything", {"confirm": True}, source="stub")
        assert parse_command("tidy up", ctx(), rogue) is None

    def test_a_model_shaped_parser_slots_in_unchanged(self):
        """A stub standing in for a provider: same signature, structured output,
        no network. This is what a real model parser has to look like."""
        def stub(text, context):
            assert "11" in text
            return Intent("create_race",
                          {"name": "Club Race", "race_type": "standard",
                           "gun_time": "2026-08-15T11:00"},
                          source="stub-model")
        intent = parse_command("kick off at 11 please", ctx(), stub)
        assert intent.source == "stub-model"
        answer = resolve(intent, ctx())
        assert answer.status == NEEDS_CONFIRMATION
        assert answer.resolved["first_warning_time"].endswith("10:55")

    def test_every_tool_offered_to_a_model_is_one_the_app_implements(self):
        """A schema that offers something unimplemented invites the model to
        propose it, and the failure lands on the person on the water."""
        from core import boats, raceadmin
        implemented = {
            "create_race": raceadmin.create_race,
            "set_start_and_course": raceadmin.update_race_settings,
            "add_entries": raceadmin.add_entries,
            "shorten_course": raceadmin.shorten_course_at,
            "postpone_race": raceadmin.postpone_race,
            "resume_race": raceadmin.resume_race,
            "race_status": True,          # read-only, served from core.races
            "race_results": True,         # read-only, served from core.series
            "look_up": True,              # read-only, answered from the club's own data
            "set_custom_course": raceadmin.set_custom_course,
            "boat_rating": boats.find_boat_ratings,
            "add_boat": boats.insert_boat_from_listings,
        }
        assert TOOL_NAMES == set(implemented)
        assert all(implemented[name] for name in TOOL_NAMES)

    def test_arming_a_start_is_not_on_the_menu(self):
        """Deliberate: the club has settled that a start may run unwatched, but
        who may arm one remotely from a phone is an authorisation decision that
        has not been made. Until it is, the model is not offered the option."""
        assert "arm_start_sequence" not in TOOL_NAMES


class TestAgreeingInWords:
    @pytest.mark.parametrize("said", ["yes", "Yes please", "yep", "ok", "okay", "sure",
                                      "go ahead", "do it", "yes lets do that", "confirm",
                                      "aye", "that's right"])
    def test_what_counts_as_yes(self, said):
        from core.assistant import reads_as_yes
        assert reads_as_yes(said), f"{said!r} is agreement"

    @pytest.mark.parametrize("said", ["no", "nope", "cancel", "leave it", "forget it"])
    def test_what_counts_as_no(self, said):
        from core.assistant import reads_as_no
        assert reads_as_no(said), f"{said!r} is a refusal"

    @pytest.mark.parametrize("said", [
        "yes but make it half past", "yes, change the course to 4", "ok instead use course 7",
        "no, course 4", "yes and also add all the boats",
        "yes we should start the race at eleven with all the boats in the summer series",
    ])
    def test_agreement_carrying_an_instruction_is_an_instruction(self, said):
        """Acting on the old proposal because the sentence began with "yes" is
        the whole failure over again."""
        from core.assistant import reads_as_no, reads_as_yes
        assert not reads_as_yes(said) and not reads_as_no(said)


class TestARaceThatHasAlreadyFinished:
    """A finished race stays the app's current race, so "let's try 29" -- said
    about the next one -- read back as an ordinary course change to the race
    that had just ended, with nothing in it to notice."""

    def over(self, **kwargs):
        return dict(current_race_id=57, current_race_name="Club Race test ph line",
                    race_finished=True, **kwargs)

    def test_the_readback_says_the_race_has_finished(self):
        answer = interpret("use course 29", **self.over())
        assert answer.status == NEEDS_CONFIRMATION
        assert "has finished" in answer.readback
        assert any("new race" in w for w in answer.warnings)

    @pytest.mark.parametrize("said", ["add all the boats", "shorten at mark 4"])
    def test_and_says_it_for_anything_else_that_would_change_it(self, said):
        answer = interpret(said, **self.over())
        assert "has finished" in answer.readback

    def test_but_it_is_not_refused(self):
        """Correcting a finished race is a real thing to want -- the course was
        wrong on the sheet, the results are being tidied. It is confirmed, not
        forbidden."""
        answer = interpret("use course 29", **self.over())
        assert answer.status == NEEDS_CONFIRMATION
        assert answer.resolved["course_no"] == 29

    def test_a_race_still_being_sailed_says_nothing_of_the_kind(self):
        answer = interpret("use course 29", current_race_id=57, current_race_name="Club Race")
        assert "has finished" not in answer.readback
        assert answer.warnings == []


class TestWhatANewRaceWouldBe:
    """Asked "what series will a new race be in?", the app proposed creating a
    race. Twice. A read-back has to say the things a race officer would
    otherwise have to know to ask about."""

    def test_the_readback_says_which_series(self):
        model = lambda text, context: Intent(
            "create_race", {"series": "Wednesday Evening Points"}, source="stub")
        answer = interpret("new race in the wednesday points", model)
        assert "in series Wednesday Evening Points" in answer.readback
        assert answer.resolved["series_name"] == "Wednesday Evening Points"

    def test_and_says_when_there_would_be_none(self):
        """A race in no series is not scored in any standings, which is not
        something to find out in September."""
        answer = interpret("create a race at 11am")
        assert "in no series" in answer.readback

    def test_and_says_when_it_would_have_no_start_time(self):
        answer = interpret("create a race", lambda text, context:
                           Intent("create_race", {}, source="stub"))
        assert "no start time yet" in answer.readback


class TestPuttingTheStartBack:
    """Every way a race officer says it, because the fallback grammar is what
    answers when the hut cannot reach a model -- which is exactly when nobody is
    in the hut to move the start on the race sheet instead."""

    GUN = datetime(2026, 8, 15, 11, 0, 0)

    @pytest.mark.parametrize("said,expected", [
        ("put the start back 20 minutes", "11:20"),
        ("put it back 10 minutes", "11:10"),
        ("delay the start by 15 minutes", "11:15"),
        ("push the gun back 5 minutes", "11:05"),
        ("postpone 30 minutes", "11:30"),
        ("bring the start forward 10 minutes", "10:50"),
    ])
    def test_the_gun_moves_by_the_right_amount(self, said, expected):
        answer = interpret(said, current_race_id=57, current_gun_time=self.GUN)
        assert answer.status == NEEDS_CONFIRMATION, f"{said!r} was not understood"
        assert answer.resolved["first_gun_time"].endswith(expected)

    def test_with_no_race_to_be_relative_to_it_asks(self):
        """Ten minutes later than what?"""
        answer = interpret("put the start back 10 minutes")
        assert answer.status in (NOT_UNDERSTOOD, NEEDS_CLARIFICATION)


class TestAQuestionTheAppCanHearTheAnswerTo:
    """A question nobody can answer is a dead end, and this page had four of them.

    Asked "standard race or pursuit?", a race officer types "standard" -- which
    on its own carries no time, no length and no race, so read as a fresh command
    it is not a command at all. The app asked, was answered, and said it did not
    understand.
    """

    ASKED = {"gun_time": "2026-08-15T19:00", "length_min": 60}

    def test_the_question_says_which_field_it_is_about(self):
        """"start an hours race at seven", read by a model, exactly as reported:
        a length with no race type, which the app is right to ask about."""
        model = lambda text, context: Intent("create_race", dict(self.ASKED), source="stub-model")
        answer = interpret("start an hours race at seven", model)
        assert answer.status == NEEDS_CLARIFICATION
        assert answer.clarify_field == "race_type"

    @pytest.mark.parametrize("said", ["standard", "Standard", "its a standard race",
                                      "it's a standard one"])
    def test_a_one_word_answer_becomes_the_whole_command_again(self, said):
        intent = answer_to_question("create_race", self.ASKED, "race_type",
                                    ["standard", "pursuit"], said)
        assert intent is not None, "the app could not hear the answer to its own question"
        assert intent.arguments["race_type"] == "standard"
        # Everything already settled is still there: the reply supplies the one
        # missing field, it does not start again.
        assert intent.arguments["gun_time"] == "2026-08-15T19:00"
        answer = resolve(intent, ctx())
        assert answer.status == NEEDS_CONFIRMATION
        assert "19:00" in answer.readback and "18:55" in answer.readback

    def test_the_other_answer_is_heard_too(self):
        intent = answer_to_question("create_race", self.ASKED, "race_type",
                                    ["standard", "pursuit"], "pursuit")
        answer = resolve(intent, ctx())
        assert answer.resolved["race_type"] == "pursuit"
        assert answer.resolved["pursuit_duration_min"] == 60

    def test_changing_the_subject_is_not_an_answer(self):
        """Whatever else happens, a sentence that is plainly a new instruction
        must not be stuffed into the field of an old question."""
        for said in ("actually shorten the course at mark 4",
                     "no, tell me the status of the race instead", "status"):
            assert answer_to_question("create_race", self.ASKED, "race_type",
                                      ["standard", "pursuit"], said) is None

    def test_a_bare_mark_answers_which_mark(self):
        intent = answer_to_question("shorten_course", {"race_id": 57}, "at_mark", [], "4")
        assert intent.arguments == {"race_id": 57, "at_mark": "4"}

    def test_but_only_where_a_bare_answer_makes_sense(self):
        """"status" is six characters and could be a mark's name only in a world
        where the app is not listening."""
        assert answer_to_question("shorten_course", {"race_id": 57}, "at_mark", [], "status") is None
        assert answer_to_question("create_race", {}, "name", [], "Sunday Points") is None


class TestItCanAnswerInWords:
    """Not everything said to a race officer is an order. "What's the wind
    doing?" has an answer, and "I did not understand that" is not it."""

    def talkative(self, said="Wind is 130 degrees at 12 knots. Nothing has been changed."):
        return lambda text, context: Intent(ANSWER, {"text": said}, source="stub-model")

    def test_a_reply_in_words_comes_back_as_words(self):
        answer = interpret("whats the wind doing out there", self.talkative())
        assert answer.status == ANSWERED
        assert "130 degrees" in answer.answer

    def test_an_answer_proposes_nothing_and_confirms_nothing(self):
        answer = interpret("tell me about the race", self.talkative())
        assert answer.readback == "" and answer.resolved == {}
        assert answer.status != NEEDS_CONFIRMATION

    def test_the_answer_pseudo_tool_is_not_a_tool(self):
        """It passes the parser so the interpreter can speak; it is not in TOOLS,
        so there is nothing to offer a model and nothing to execute."""
        assert ANSWER not in TOOL_NAMES
        assert ANSWER not in {t["name"] for t in assistant.TOOLS}

    def test_an_empty_reply_is_still_not_understood(self):
        answer = interpret("...", self.talkative(""))
        assert answer.status == NOT_UNDERSTOOD


class TestNothingHereExecutesAnything:
    def test_resolving_a_command_writes_nothing_to_the_database(self, monkeypatch):
        """The read-back step must be safe to run on anything a competitor types,
        including by mistake, including twice."""
        from core import raceadmin
        for name in ("create_race", "update_race_settings", "add_entries",
                     "shorten_course_at", "clear_shortening", "signal_shortened_course"):
            monkeypatch.setattr(raceadmin, name,
                                lambda *a, **k: pytest.fail(f"resolve() called raceadmin.{name}"))
        for said in ("create a race at 11am", "add all the boats", "shorten at mark 4",
                     "status", "set the gun for 11:00"):
            resolve(parse_command(said, ctx(current_race_id=57)), ctx(current_race_id=57))

    def test_the_module_imports_nothing_that_can_execute_a_command(self):
        """core.assistant interprets; core.raceadmin decides and acts. Keeping
        the import out is what stops the two quietly merging."""
        source = (assistant.__file__ or "")
        assert source
        with open(source, encoding="utf-8") as handle:
            text = handle.read()
        assert "import raceadmin" not in text and "from core.raceadmin" not in text
