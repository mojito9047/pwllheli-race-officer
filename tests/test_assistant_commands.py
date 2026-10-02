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
        from routes import assistant_reads as reads
        implemented = {
            "create_race": raceadmin.create_race,
            "set_start_and_course": raceadmin.update_race_settings,
            "add_entries": raceadmin.add_entries,
            "shorten_course": raceadmin.shorten_course_at,
            # The race sheet's own Finish button calls the same function.
            "finish_boat": raceadmin.finish_entry_now,
            "postpone_race": raceadmin.postpone_race,
            "resume_race": raceadmin.resume_race,
            "race_status": True,          # read-only, served from core.races
            "race_results": True,         # read-only, served from core.series
            "look_up": True,              # read-only, answered from the club's own data
            "mark_distance": True,        # read-only, measured with core.timeutils
            # The rest of the look-ups, each a function in routes/assistant_reads.py
            # built on the one its own page uses.
            **{name: reads.READS[name] for name in reads.READS if name != "race_results"},
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
        assert answer_to_question("create_race", {}, "name", [], "status") is None
        assert answer_to_question("create_race", {}, "name", [], "make it a pursuit") is None

    def test_a_new_race_is_told_its_name_and_series_in_words(self):
        """Asked what it is called, any name is the answer -- and taken whole:
        "Evening Race 12" holds the suggested "Evening Race", and reading it as
        that would have named it after the last race."""
        said = answer_to_question("create_race", {"gun_time": "x"}, "name", ["Evening Race"],
                                  "Evening Race 12")
        assert said.arguments == {"gun_time": "x", "name": "Evening Race 12"}
        assert answer_to_question("create_race", {}, "name", [], "call it Autumn Pursuit"
                                  ).arguments["name"] == "Autumn Pursuit"
        options = ["Wednesday Evening Points", "no series"]
        assert answer_to_question("create_race", {}, "series", options, "none"
                                  ).arguments["series"] == "no series"
        assert answer_to_question("create_race", {}, "series", options, "the autumn series"
                                  ).arguments["series"] == "autumn"
        # Two answers at once are the interpreter's to read.
        assert answer_to_question("create_race", {}, "series", options,
                                  "autumn, call it Autumn 3") is None


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


def stub(intent: Intent):
    """A parser standing in for a model that produced exactly this."""
    return lambda text, context: intent


def plan(*steps):
    return Intent(assistant.PLAN, {"steps": [{"name": name, "arguments": args}
                                             for name, args in steps]}, source="stub")


class TestSeveralChangesInOneSentence:
    """"Use course 4 and add Mojito" is two jobs. Read as one, the course changed
    and Mojito was never mentioned again; it is now read back as both, in order,
    and agreed to once."""

    def test_two_changes_are_read_back_together_and_in_order(self):
        answer = interpret("use course 4 and add Mojito", stub(plan(
            ("set_start_and_course", {"course_no": 4}),
            ("add_entries", {"scope": "boat", "boat": "Mojito"}))), current_race_id=57)
        assert answer.status == NEEDS_CONFIRMATION
        assert answer.intent == assistant.PLAN
        assert answer.readback.startswith("In this order: (1) Set race #57: course 4.")
        assert "(2) Add Mojito to race #57." in answer.readback
        assert [s["intent"] for s in answer.resolved["steps"]] == [
            "set_start_and_course", "add_entries"]

    def test_a_step_the_app_does_not_implement_sinks_the_whole_plan(self):
        """Half of what was asked, carried out when the read-back cannot say
        which half, is worse than asking again."""
        said = plan(("set_start_and_course", {"course_no": 4}), ("arm_start_sequence", {}))
        assert parse_command("use course 4 and arm it", ctx(), stub(said)) is None

    def test_a_look_up_is_not_a_step(self):
        said = plan(("set_start_and_course", {"course_no": 4}), ("race_status", {}))
        assert parse_command("use course 4, how are we", ctx(), stub(said)) is None

    def test_a_plan_of_one_is_just_that_change(self):
        got = parse_command("use course 4", ctx(),
                            stub(plan(("set_start_and_course", {"course_no": 4}))))
        assert got.name == "set_start_and_course" and got.arguments == {"course_no": 4}

    def test_everything_aimed_at_a_new_race_goes_into_it(self):
        """The second and third calls name no race, so read alone they would have
        changed the current one -- a different race from the one being made."""
        answer = interpret("create a race at 11, course 4, and add all the boats", stub(plan(
            ("create_race", {"race_type": "standard", "gun_time": "2026-08-15T11:00"}),
            ("set_start_and_course", {"course_no": 4}),
            ("add_entries", {"scope": "all_active"}))), current_race_id=57)
        assert answer.status == NEEDS_CONFIRMATION
        assert answer.intent == "create_race"
        assert answer.resolved["course_no"] == 4 and answer.resolved["add_all_active"]
        assert "course 4" in answer.readback and "race #57" not in answer.readback

    def test_a_change_a_new_race_cannot_carry_is_asked_for_afterwards(self):
        answer = interpret("new race and shorten at 4", stub(plan(
            ("create_race", {"race_type": "standard"}),
            ("shorten_course", {"at_mark": "4"}))), current_race_id=57)
        assert answer.status == NEEDS_CLARIFICATION
        assert "create the race first" in answer.question

    def test_a_question_about_one_step_is_answered_into_that_step(self):
        """Asked which race type, "standard" must come back as the whole plan
        with the type filled in -- not as the one step it was about."""
        said = plan(("create_race", {"gun_time": "2026-08-15T11:00", "length_min": 60}),
                    ("add_entries", {"race_id": 12, "scope": "all_active"}))
        answer = interpret("an hour race at 11, and everyone into race 12", stub(said),
                           current_race_id=57)
        assert answer.status == NEEDS_CLARIFICATION
        assert answer.clarify_field == "0:race_type"
        again = answer_to_question(assistant.PLAN, answer.arguments, answer.clarify_field,
                                   answer.options, "standard")
        assert again.name == assistant.PLAN
        assert again.arguments["steps"][0]["arguments"]["race_type"] == "standard"
        assert again.arguments["steps"][1] == {"name": "add_entries",
                                               "arguments": {"race_id": 12, "scope": "all_active"}}
        assert resolve(again, ctx(current_race_id=57)).status == NEEDS_CONFIRMATION


class TestANewRaceCarriesWhatItWasGiven:
    def test_a_named_course_is_the_course(self):
        answer = interpret("create a race at 11 on course 4", stub(Intent("create_race", {
            "race_type": "standard", "gun_time": "2026-08-15T11:00", "course_no": 4})))
        assert answer.resolved["course_no"] == 4
        assert "first gun 11:00, course 4" in answer.readback

    def test_named_boats_are_entered_by_name(self):
        answer = interpret("race at 11 with Mojito and Sgrech Bach", stub(Intent("create_race", {
            "race_type": "standard", "boats": "Mojito, Sgrech Bach"})))
        assert answer.resolved["boat_names"] == ["Mojito", "Sgrech Bach"]
        assert "adding Mojito and Sgrech Bach" in answer.readback

    def test_every_boat_wins_over_a_list_of_some(self):
        answer = interpret("race with everyone", stub(Intent("create_race", {
            "race_type": "standard", "add_all_active": True, "boats": "Mojito"})))
        assert "adding every active boat" in answer.readback
        assert "boat_names" not in answer.resolved


class TestMeasuringBetweenMarks:
    def test_it_needs_both_marks(self):
        answer = interpret("how far to C", stub(Intent("mark_distance", {"to_mark": "C"})))
        assert answer.status == NEEDS_CLARIFICATION

    def test_it_is_read_only(self):
        assert assistant.tool_kind("mark_distance") == assistant.READ
        answer = interpret("O to C", stub(Intent("mark_distance",
                                                 {"from_mark": "O", "to_mark": "C"})))
        assert answer.resolved == {"from_mark": "O", "to_mark": "C"}


class TestEveryToolSaysWhatItDoesToTheTurn:
    def test_each_has_a_kind(self):
        assert {t["name"]: t["kind"] for t in assistant.TOOLS}.keys() == TOOL_NAMES
        assert all(t["kind"] in (assistant.READ, assistant.REPORT, assistant.WRITE)
                   for t in assistant.TOOLS)

    def test_the_status_report_is_the_only_report(self):
        assert [t["name"] for t in assistant.TOOLS if t["kind"] == assistant.REPORT] == [
            "race_status"]

    def test_an_unknown_tool_counts_as_a_change(self):
        """So it ends the turn and reaches parse_command, which drops it --
        rather than being run as a look-up nobody wrote."""
        assert assistant.tool_kind("arm_start_sequence") == assistant.WRITE

    def test_every_argument_has_a_real_type(self):
        for tool in assistant.TOOLS:
            for name, spec in tool["arguments"].items():
                assert isinstance(spec, dict) and spec.get("type"), (tool["name"], name)


class TestTheNewLookUpsAreReadThroughTheirSchemas:
    """Standings, the log, a boat's season and the rest need no rule of their
    own: their arguments are read to their types, what is required is asked for,
    and a look-up about a race is about the race being talked about."""

    def test_a_series_is_required_and_asked_for(self):
        answer = interpret("standings", stub(Intent("series_standings", {})))
        assert answer.status == NEEDS_CLARIFICATION
        assert answer.question == "Which series?"

    def test_a_choice_is_read_whatever_its_case(self):
        answer = interpret("autumn on irc", stub(Intent("series_standings",
                                                        {"series": "autumn", "rating": "irc"})))
        assert answer.resolved == {"series": "autumn", "rating": "IRC"}

    def test_a_word_that_is_not_one_of_the_choices_is_dropped(self):
        answer = interpret("autumn on PHRF", stub(Intent("series_standings",
                                                         {"series": "autumn", "rating": "PHRF"})))
        assert "rating" not in answer.resolved

    def test_a_race_look_up_is_about_the_race_in_the_conversation(self):
        answer = interpret("when was the start", stub(Intent("race_log", {})), current_race_id=90)
        assert answer.resolved["race_id"] == 90

    def test_a_race_can_be_named_rather_than_numbered(self):
        answer = interpret("log of race 4", stub(Intent("race_log", {"race_name": "Race 4"})),
                           current_race_id=90)
        assert "race_id" not in answer.resolved and answer.resolved["race_name"] == "Race 4"

    def test_numbers_are_numbers(self):
        answer = interpret("an hour", stub(Intent("suggest_course", {"target_minutes": "60",
                                                                     "tws": "12.5"})))
        assert answer.resolved["target_minutes"] == 60.0 and answer.resolved["tws"] == 12.5

    def test_a_course_to_time_needs_marks_or_a_number(self):
        answer = interpret("how long would it take", stub(Intent("time_course", {})))
        assert answer.status == NEEDS_CLARIFICATION

    def test_every_new_look_up_is_read_only(self):
        assert all(assistant.tool_kind(name) == assistant.READ for name in assistant.GENERIC_READS)


class TestTheCourseBoardAsTyped:
    @pytest.mark.parametrize("said", ["4p 7p Op", "4p, 7p, Op", "4 port, 7 port, O port"])
    def test_every_way_of_writing_it_is_the_same_course(self, said):
        assert assistant.parse_mark_sequence(said) == [
            {"mark": "4", "rounding": "port"}, {"mark": "7", "rounding": "port"},
            {"mark": "O", "rounding": "port"}]

    def test_a_starboard_rounding_on_the_board_is_starboard(self):
        assert assistant.parse_mark_sequence("Fp 2p Os")[2] == {"mark": "O", "rounding": "starboard"}
