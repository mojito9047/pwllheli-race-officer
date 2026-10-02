"""The command endpoint: interpret, read back, confirm, act.

The properties worth holding here are the ones that protect somebody typing
one-handed on a boat over a connection that drops: interpreting changes nothing,
a confirmation is required before anything happens, and sending the same thing
twice does it once.
"""
from __future__ import annotations

import json
import pathlib

import pytest

import app as ro
from core.raceadmin import RaceSettings, RaceSpec, create_race, update_race_settings

TOKEN = "test-csrf-token"


def _grant_on_water(username: str = "admin", allowed: bool = True) -> None:
    """Running racing from the water is not implied by any role, so the test
    account is given it explicitly -- exactly as an administrator would."""
    with ro.get_db() as db:
        db.execute("UPDATE users SET can_race_remotely = ? WHERE username = ?",
                   (1 if allowed else 0, username))
        db.commit()


@pytest.fixture(autouse=True)
def an_interpreter_is_configured(monkeypatch):
    """The page is unavailable without one, so every test needs one.

    The stand-in is the built-in grammar: it reads the sentences these tests are
    written in, needs no provider account, and keeps them deterministic. What it
    is standing in for is a model — production no longer falls back to it, which
    is what `TestWithNoInterpreter` below is about.
    """
    from core.assistant import grammar_parse
    from routes import assistant as routes_assistant
    # The dearest facts are cached for twenty seconds in production, which is
    # exactly long enough for one test's patched trackers to answer the next
    # test's question.
    routes_assistant._FACT_CACHE.clear()
    monkeypatch.setattr(routes_assistant, "interpreter_status",
                        lambda config: {"model": "test-model", "grammar_only": False,
                                        "error": "", "text": "test-model"})
    monkeypatch.setattr(routes_assistant, "parser_from_config",
                        lambda config, transport=None: grammar_parse)


@pytest.fixture()
def api(logged_in_client):
    """POST JSON to the assistant endpoints as a signed-in user who may."""
    _grant_on_water()
    with logged_in_client.session_transaction() as sess:
        sess["_csrf_token"] = TOKEN

    def _post(path: str, body: dict):
        resp = logged_in_client.post(path, json={**body, "_csrf_token": TOKEN},
                                     headers={"X-CSRF-Token": TOKEN})
        return resp, (resp.get_json() or {})

    return _post


def _say(api, text: str, **body):
    return api("/admin/api/assistant/command", {"text": text, **body})


def _say_new_race(api, text: str, series: str = "no series", name: str = "Club Race", **body):
    """Say something that creates a race, and answer what every new race is now
    asked: which series it is in, and what it is called. Anything else asked is
    left for the test, which is usually what it is testing."""
    resp, reply = _say(api, text, **body)
    for _ in range(3):
        question = str(reply.get("question") or "")
        if reply.get("status") != "needs_clarification":
            break
        if question.startswith("Which series is the new race in?"):
            resp, reply = _say(api, series)
        elif question.startswith("What is the new race called?"):
            resp, reply = _say(api, name)
        else:
            break
    return resp, reply


def _confirm(api, token: str, **body):
    return api("/admin/api/assistant/command/confirm", {"pending_token": token, **body})


def _races():
    with ro.get_db() as db:
        return db.execute("SELECT COUNT(*) FROM races").fetchone()[0]


class TestInterpretingChangesNothing:
    def test_a_command_is_read_back_and_not_carried_out(self, api):
        before = _races()
        resp, body = _say_new_race(api, "create a race called Sunday Points at 11am")
        assert resp.status_code == 200
        assert body["status"] == "needs_confirmation"
        assert body["pending_token"].startswith("pc_")
        assert _races() == before, "interpreting a command created a race"

    def test_the_readback_shows_the_warning_and_the_gun(self, api):
        _, body = _say_new_race(api, "create a race at 11am")
        assert "10:55" in body["readback"] and "11:00" in body["readback"]
        assert body["resolved"]["first_warning_time"].endswith("10:55")

    def test_a_sentence_it_cannot_read_is_said_plainly(self, api):
        _, body = _say(api, "tell the fleet I am running late")
        assert body["status"] == "not_understood"
        assert body["question"]

    def test_and_an_interpreter_that_failed_says_that_instead(self, api, monkeypatch):
        """The same sentence covered both "nobody could read that" and "the
        interpreter never answered", and on the water those want opposite
        responses: say it again, or stop trying and use the race sheet."""
        from routes import assistant as routes_assistant
        monkeypatch.setattr(routes_assistant, "interpreter_status",
                            lambda config: {"error": "HTTP 402: Insufficient balance"})
        _, body = _say(api, "tell the fleet I am running late")
        assert "did not answer" in body["question"]
        assert "402" in body["question"]

    def test_an_ambiguous_length_asks_which_kind_of_race(self, api):
        _, body = _say_new_race(api, "create a race at 11, an hour long")
        assert body["status"] == "needs_clarification"
        assert set(body["options"]) == {"standard", "pursuit"}


class TestConfirmingActs:
    def test_a_confirmed_create_makes_the_race(self, api):
        before = _races()
        _, body = _say_new_race(api, "create a race called Sunday Points at 11am")
        resp, done = _confirm(api, body["pending_token"])
        assert resp.status_code == 200 and done["status"] == "done"
        assert _races() == before + 1
        race = ro.get_race(done["race_id"])
        assert race["name"] == "Sunday Points"
        # The time named was the gun; what is stored is the warning signal.
        assert str(race["start_time"]).endswith("10:55:00")
        assert ro.race_first_start_time(race).endswith("11:00:00")

    def test_it_goes_through_the_same_service_the_race_sheet_uses(self, api, monkeypatch):
        """Not a second way of creating a race."""
        seen = []
        original = ro.raceadmin.create_race if hasattr(ro, "raceadmin") else None
        from core import raceadmin
        real = raceadmin.create_race
        monkeypatch.setattr(raceadmin, "create_race",
                            lambda db, spec, actor="system": seen.append(spec) or real(db, spec, actor=actor))
        # routes/assistant.py imported the name directly, so patch it there too.
        from routes import assistant as assistant_routes
        monkeypatch.setattr(assistant_routes, "create_race", raceadmin.create_race)
        _, body = _say_new_race(api, "create a race at 11am")
        _confirm(api, body["pending_token"])
        assert seen and seen[0].name in ("", "Club Race")

    def test_declining_carries_nothing_out(self, api):
        before = _races()
        _, body = _say_new_race(api, "create a race at 11am")
        _, done = _confirm(api, body["pending_token"], confirm=False)
        assert done["status"] == "dismissed"
        assert _races() == before

    def test_a_token_that_was_never_issued_is_refused(self, api):
        resp, body = _confirm(api, "pc_madeup")
        assert resp.status_code == 404 and body["ok"] is False


class TestSurvivingABadConnection:
    def test_confirming_twice_acts_once(self, api):
        before = _races()
        _, body = _say_new_race(api, "create a race at 11am")
        _, first = _confirm(api, body["pending_token"])
        _, second = _confirm(api, body["pending_token"])
        assert first["race_id"] == second["race_id"]
        assert second.get("repeat") is True
        assert _races() == before + 1, "the retry created a second race"

    def test_the_same_command_id_twice_proposes_once(self, api):
        said = "create a race called Repeat Test at 11am in no series"
        _, first = _say(api, said, client_command_id="abc-123")
        _, second = _say(api, said, client_command_id="abc-123")
        assert first["pending_token"] == second["pending_token"]
        assert second.get("repeat") is True

    def test_a_stale_command_is_not_carried_out(self, api):
        _, body = _say_new_race(api, "create a race at 11am")
        before = _races()
        with ro.get_db() as db:
            db.execute("UPDATE assistant_commands SET expires_at = '2020-01-01T00:00:00'"
                       " WHERE token = ?", (body["pending_token"],))
            db.commit()
        _, done = _confirm(api, body["pending_token"])
        assert done["status"] == "expired"
        assert _races() == before


class TestItChecksTheCommandAgainstTheActualRace:
    def test_shortening_at_a_mark_not_on_the_course_says_which_marks_there_are(self, api):
        _, created = _say_new_race(api, "create a race at 11am")
        _confirm(api, created["pending_token"])
        _, body = _say(api, "shorten at mark ZZ")
        assert body["status"] == "needs_clarification"
        assert "not on that course" in body["question"]

    def test_a_shortening_is_confirmed_before_the_horn_sounds(self, api, monkeypatch):
        fired = []
        from core import raceadmin
        monkeypatch.setattr(raceadmin, "signal_shortened_course",
                            lambda *a, **k: fired.append(a))
        _, created = _say_new_race(api, "create a race at 11am")
        _confirm(api, created["pending_token"])
        _, body = _say(api, "shorten at mark 1")
        assert body["status"] == "needs_confirmation"
        assert "horn" in body["readback"].lower()
        assert fired == [], "the horn sounded before anyone confirmed"

    def test_asking_for_status_needs_no_confirmation(self, api):
        """There is nothing to undo, and asking someone to confirm a question
        they just asked is noise."""
        _, created = _say_new_race(api, "create a race at 11am")
        _confirm(api, created["pending_token"])
        _, body = _say(api, "status")
        assert body["status"] == "done"
        assert "entered" in body["message"]


class TestItIsAConversation:
    """Reported from the water, and the whole of it: the app asked which kind of
    race it was, was told "standard", and answered "I did not understand that".
    A question whose answer goes nowhere is worse than no question at all."""

    def test_the_answer_to_the_apps_own_question_is_understood(self, api):
        _, asked = _say_new_race(api, "create a race at 11, an hour long")
        assert asked["status"] == "needs_clarification"
        _, body = _say_new_race(api, "standard")
        assert body["status"] == "needs_confirmation", \
            "the app could not hear the answer to its own question"
        # And everything already established survived the round trip.
        assert "11:00" in body["readback"] and "10:55" in body["readback"]
        assert body["resolved"]["race_type"] == "standard"

    def test_the_answer_can_be_a_whole_short_sentence(self, api):
        _say_new_race(api, "create a race at 11, an hour long")
        _, body = _say_new_race(api, "its a standard race")
        assert body["status"] == "needs_confirmation"

    def test_and_the_race_it_makes_is_the_one_that_was_asked_for(self, api):
        _say_new_race(api, "create a race at 11, an hour long")
        _, body = _say_new_race(api, "pursuit")
        _, done = _confirm(api, body["pending_token"])
        race = ro.get_race(done["race_id"])
        assert race["race_type"] == "pursuit"
        assert str(race["start_time"]).endswith("10:55:00")

    def test_a_question_is_only_answered_once(self, api):
        """The second "standard" is a bare word with nothing left to answer, and
        must not quietly propose a second race."""
        _say_new_race(api, "create a race at 11, an hour long")
        _say_new_race(api, "standard")
        _, again = _say(api, "standard")
        assert again["status"] != "needs_confirmation"

    def test_changing_the_subject_mid_question_does_what_it_looks_like(self, api):
        _say_new_race(api, "create a race at 11, an hour long")
        _, body = _say(api, "status")
        assert body["status"] in ("done", "needs_clarification")
        assert body.get("intent") != "create_race"

    def test_the_question_and_its_answer_are_both_in_the_thread(self, api, monkeypatch):
        """What the interpreter is told next time. Recording only the turns that
        changed something is what left "standard" with no antecedent."""
        from core.assistant import grammar_parse
        from routes import assistant as routes_assistant
        seen = {}

        def remembering(config, transport=None):
            # Reads each sentence as the stand-in model does everywhere else in
            # this file; it used to return nothing and leave the page's grammar
            # fallback to read it, and that fallback is gone.
            def parse(text, context):
                seen["recent"] = list(context.recent)
                return grammar_parse(text, context)
            return parse

        monkeypatch.setattr(routes_assistant, "parser_from_config", remembering)
        _say_new_race(api, "create a race at 11, an hour long")
        _say(api, "tell me about it")
        said = [line for pair in seen.get("recent", []) for line in pair]
        assert any("an hour long" in line for line in said)
        assert any("standard race or a pursuit" in line for line in said)


class TestChangingOneThingChangesOneThing:
    """`update_race_settings` writes every field it is given, so a command that
    named only a course arrived with an empty start time and wiped the race's
    start. The read-back said "course 29" and meant "course 29, and no start
    time" — there is nothing in that sentence a person could have checked."""

    def _race_at_eleven(self, api):
        _, created = _say_new_race(api, "create a race at 11am")
        _, done = _confirm(api, created["pending_token"])
        return done["race_id"]

    def test_changing_the_course_leaves_the_start_alone(self, api):
        race_id = self._race_at_eleven(api)
        _, body = _say(api, "use course 1")
        _confirm(api, body["pending_token"])
        race = ro.get_race(race_id)
        assert str(race["start_time"]).endswith("10:55:00"), "the start time was wiped"
        assert int(race["course_no"]) == 1

    def test_it_keeps_the_series_the_race_is_in(self, api, monkeypatch):
        """The worst of them: a race dropped out of its series and so out of the
        standings, from a command that said "course 29"."""
        with ro.get_db() as db:
            db.execute("INSERT INTO race_series (name, created_at, updated_at)"
                       " VALUES ('Summer Series 2026', '2026-01-01T00:00:00',"
                       " '2026-01-01T00:00:00')")
            db.commit()
            series_id = int(db.execute("SELECT id FROM race_series").fetchone()["id"])
        from core.assistant import Intent, grammar_parse
        from routes import assistant as routes_assistant

        def stub(text, context):
            """Only the first sentence needs a model to read it; the rest is the
            ordinary grammar. (Never monkeypatch.undo() here — the fixture that
            points the app at the sandbox database uses monkeypatch too.)"""
            if "series" in text:
                return Intent("create_race", {"series": "Summer Series 2026",
                                              "gun_time": "2099-08-15T11:00"}, source="stub")
            return grammar_parse(text, context)

        monkeypatch.setattr(routes_assistant, "parser_from_config",
                            lambda config, transport=None: stub)
        _, created = _say_new_race(api, "new race in the summer series at 11")
        _, done = _confirm(api, created["pending_token"])
        assert ro.get_race(done["race_id"])["series_id"] == series_id, \
            "creating with a time cleared the series it was just given"
        _, body = _say(api, "use course 1")
        _confirm(api, body["pending_token"])
        assert ro.get_race(done["race_id"])["series_id"] == series_id, \
            "changing the course dropped the race out of its series"

    def test_and_it_keeps_the_finish_line_the_race_is_sailed_to(self, api):
        """An ISORA race quietly moved back to the club line is a finish nobody
        sees happen."""
        from core.track import default_finish_line_key, finish_lines
        race_id = self._race_at_eleven(api)
        keys = [str(line.get("key")) for line in finish_lines()]
        other = next((k for k in keys if k and k != default_finish_line_key()), None)
        if not other:
            pytest.skip("this club has only one finish line configured")
        with ro.get_db() as db:
            db.execute("UPDATE races SET finish_line_key = ? WHERE id = ?", (other, race_id))
            db.commit()
        _, body = _say(api, "use course 1")
        _confirm(api, body["pending_token"])
        assert ro.get_race(race_id)["finish_line_key"] == other

    def test_and_changing_the_start_leaves_the_course_alone(self, api):
        race_id = self._race_at_eleven(api)
        _, chosen = _say(api, "use course 1")
        _confirm(api, chosen["pending_token"])
        _, moved = _say(api, "put the start back 20 minutes")
        _confirm(api, moved["pending_token"])
        race = ro.get_race(race_id)
        assert int(race["course_no"]) == 1
        assert str(race["start_time"]).endswith("11:15:00")


class TestSayingYesOutLoud:
    """The Yes button is not the only way somebody agrees. With a proposal
    waiting, a typed "yes lets do that" was read as a fresh instruction -- which
    is how agreement to create a race became a course change to a race that had
    already finished, while the new race was never made at all."""

    def test_typing_yes_carries_out_what_is_on_screen(self, api):
        before = _races()
        _say_new_race(api, "create a race at 11am")
        _, body = _say(api, "yes lets do that")
        assert body["status"] == "done"
        assert _races() == before + 1

    @pytest.mark.parametrize("said", ["yes", "Yes please", "ok", "go ahead", "do it"])
    def test_the_ways_people_say_it(self, api, said):
        before = _races()
        _say_new_race(api, "create a race at 11am")
        _, body = _say(api, said)
        assert body["status"] == "done", f"{said!r} did not agree to anything"
        assert _races() == before + 1

    @pytest.mark.parametrize("said", ["no", "no thanks", "cancel"])
    def test_and_the_ways_they_decline(self, api, said):
        before = _races()
        _say_new_race(api, "create a race at 11am")
        _, body = _say(api, said)
        assert body["status"] == "dismissed"
        assert _races() == before

    def test_a_yes_that_carries_a_change_is_not_a_yes(self, api):
        """"yes but make it half past" changes the proposal. Carrying out the
        old one because it started with "yes" is the whole failure again."""
        before = _races()
        _say_new_race(api, "create a race at 11am")
        _, body = _say(api, "yes but make it half past twelve")
        assert body["status"] != "done"
        assert _races() == before

    def test_yes_with_nothing_waiting_is_not_an_action(self, api):
        before = _races()
        _, body = _say(api, "yes")
        assert body["status"] != "done"
        assert _races() == before

    def test_only_one_proposal_is_ever_live(self, api):
        """A second read-back leaves the first still confirmable, and a Yes meant
        for what is on screen must not carry out something said two minutes ago."""
        _, first = _say_new_race(api, "create a race called One at 11am")
        _, second = _say_new_race(api, "create a race called Two at 2pm")
        resp, body = _confirm(api, first["pending_token"])
        assert body.get("status") != "done", "an abandoned proposal was still confirmable"
        _, done = _confirm(api, second["pending_token"])
        assert ro.get_race(done["race_id"])["name"] == "Two"

    def test_the_proposal_is_what_the_conversation_is_about(self, api, monkeypatch):
        """While it waits, a follow-up is about it -- not about whatever race
        happens to be current."""
        from routes import assistant as routes_assistant
        seen = {}

        def watching(config, transport=None):
            def parse(text, context):
                seen["pending"] = context.pending_readback
                return None
            return parse

        _say_new_race(api, "create a race at 11am")
        monkeypatch.setattr(routes_assistant, "parser_from_config", watching)
        _say(api, "can you do a longer one")
        assert "Create" in seen.get("pending", "")


class TestPuttingARaceInASeries:
    """A race in no series is not scored in any standings, which is not
    something to discover in September."""

    def _named(self, series):
        from core.assistant import Intent

        def parser_from_config(config, transport=None):
            return lambda text, context: Intent("create_race", {"series": series}, source="stub")
        return parser_from_config

    def _a_series(self, name="Wednesday Evening Points"):
        with ro.get_db() as db:
            db.execute("INSERT INTO race_series (name, created_at, updated_at)"
                       " VALUES (?, '2026-01-01T00:00:00', '2026-01-01T00:00:00')", (name,))
            db.commit()
            return int(db.execute("SELECT id FROM race_series WHERE name = ?",
                                  (name,)).fetchone()["id"])

    def test_a_named_series_is_matched_and_the_race_joins_it(self, api, monkeypatch):
        series_id = self._a_series()
        from routes import assistant as routes_assistant
        monkeypatch.setattr(routes_assistant, "parser_from_config",
                            self._named("wednesday evening points"))
        _, body = _say_new_race(api, "new race in the wednesday points")
        _, done = _confirm(api, body["pending_token"])
        assert ro.get_race(done["race_id"])["series_id"] == series_id

    def test_a_series_the_club_does_not_have_is_a_question_not_a_guess(self, api, monkeypatch):
        """Quietly creating it outside the points would be a scoring error
        nobody sees until the standings come out wrong."""
        self._a_series()
        from routes import assistant as routes_assistant
        monkeypatch.setattr(routes_assistant, "parser_from_config", self._named("Autumn Series"))
        before = _races()
        _, body = _say_new_race(api, "new race in the autumn series")
        assert body["status"] == "needs_clarification"
        assert "Wednesday Evening Points" in body["question"]
        assert _races() == before


class TestItCanJustAnswer:
    """Not everything said to a race officer is an order."""

    def _talkative(self, said):
        from core.assistant import ANSWER, Intent

        def parser_from_config(config, transport=None):
            return lambda text, context: Intent(ANSWER, {"text": said}, source="stub-model")
        return parser_from_config

    def test_a_question_gets_an_answer_not_a_command(self, api, monkeypatch):
        from routes import assistant as routes_assistant
        monkeypatch.setattr(routes_assistant, "parser_from_config",
                            self._talkative("Wind is 130 degrees at 12 knots."))
        before = _races()
        _, body = _say(api, "whats the wind doing")
        assert body["status"] == "answered"
        assert "130 degrees" in body["answer"]
        assert "pending_token" not in body, "there is nothing to confirm about a sentence"
        assert _races() == before

    def test_an_answer_leaves_nothing_that_can_be_carried_out(self, api, monkeypatch):
        """Every row in this table can be looked up by token; only a command
        anybody agreed to may be executed."""
        from routes import assistant as routes_assistant
        monkeypatch.setattr(routes_assistant, "parser_from_config",
                            self._talkative("Nothing has been changed."))
        _say(api, "what do you reckon then")
        with ro.get_db() as db:
            row = db.execute("SELECT token FROM assistant_commands WHERE status = 'answered'"
                             " ORDER BY id DESC LIMIT 1").fetchone()
        resp, body = _confirm(api, row["token"])
        assert resp.status_code == 404 and body["ok"] is False

    def _facts_from(self, api, monkeypatch, said="how are they getting on"):
        from routes import assistant as routes_assistant
        seen = {"facts": []}

        def watching(config, transport=None):
            def parse(text, context):
                seen["facts"] = list(context.facts)
                return None
            return parse

        monkeypatch.setattr(routes_assistant, "parser_from_config", watching)
        _say(api, said)
        return seen["facts"]

    def test_the_facts_it_may_answer_from_are_the_apps_own(self, api, monkeypatch):
        """A model answering in words can only say what it was told. Nothing is
        invented about a race, because nothing about a race reaches it except
        this list."""
        _, created = _say_new_race(api, "create a race at 11am")
        _confirm(api, created["pending_token"])
        assert any("boats entered" in fact for fact in self._facts_from(api, monkeypatch))

    def _blowing(self, monkeypatch, twd=225.0, tws=12.0):
        """A steady south-westerly. The club's instrument is not in the sandbox,
        and what is being tested is what the app makes of a wind, not whether it
        can read one."""
        from routes import assistant as routes_assistant
        monkeypatch.setattr(routes_assistant, "_wind_now", lambda: (twd, tws))

    def _with_a_course(self, api):
        _, created = _say_new_race(api, "create a race at 11am")
        _confirm(api, created["pending_token"])
        _, chosen = _say(api, "use course 1")
        _confirm(api, chosen["pending_token"])

    def test_it_is_told_what_the_course_will_actually_be_like(self, api, monkeypatch):
        """Every one of these was already computed for the Course & start tab and
        none of it was ever passed on, so "how long will the race be?" and "can we
        have more reaching?" got "the app does not tell me" from an app that knew
        both."""
        self._blowing(monkeypatch)
        self._with_a_course(api)
        facts = " ".join(self._facts_from(api, monkeypatch))
        assert "nautical miles" in facts
        assert "Its legs are:" in facts
        assert "reaching" in facts

    def test_and_says_nothing_about_any_of_it_without_a_wind_reading(self, api, monkeypatch):
        """Course lengths, timings and recommendations are all functions of the
        wind. With no reading there is nothing to say, and inventing a breeze to
        say it with would be worse than silence."""
        from routes import assistant as routes_assistant
        monkeypatch.setattr(routes_assistant, "_wind_now", lambda: None)
        facts = self._facts_from(api, monkeypatch)
        assert any("not reading" in fact for fact in facts)
        assert not any("nautical miles" in fact for fact in facts)

    def test_a_status_report_says_how_long_the_course_should_take(self, api, monkeypatch):
        """The other half of "where are we?"."""
        self._blowing(monkeypatch)
        self._with_a_course(api)
        _, body = _say(api, "status")
        assert "minutes round" in body["message"]

    def test_but_not_when_there_is_no_wind_to_work_it_out_from(self, api, monkeypatch):
        from routes import assistant as routes_assistant
        monkeypatch.setattr(routes_assistant, "_wind_now", lambda: None)
        self._with_a_course(api)
        _, body = _say(api, "status")
        assert "minutes round" not in body["message"]
        assert "course 1" in body["message"]


class TestWhichInterpreterAnswers:
    def test_a_configured_model_is_asked_first(self, api, monkeypatch):
        from core.assistant import Intent
        from routes import assistant as routes_assistant
        asked = []

        def fake_parser_from_config(config, transport=None):
            def parse(text, context):
                asked.append(text)
                return Intent("race_status", {"race_id": context.current_race_id},
                              source="pretend-model")
            return parse

        monkeypatch.setattr(routes_assistant, "parser_from_config", fake_parser_from_config)
        _, body = _say(api, "so how are we all getting on out there then")
        assert asked == ["so how are we all getting on out there then"]
        assert body["status"] in ("done", "needs_clarification")

    def test_an_unreachable_model_is_said_so_and_nothing_is_guessed(self, api, monkeypatch):
        """The grammar used to answer whenever the model failed. On a timeout that
        turned "no, not course 6" into a proposal to set course 6; now a failure
        is a failure, said plainly, and nothing is read from the sentence."""
        from core import assistant_llm
        from routes import assistant as routes_assistant
        monkeypatch.setattr(routes_assistant, "parser_from_config",
                            lambda config, transport=None: (lambda text, context: None))
        monkeypatch.setattr(routes_assistant, "interpreter_status", lambda config: {
            "model": "test-model", "grammar_only": False,
            "error": "TimeoutError: The read operation timed out", "text": "test-model"})
        before = _races()
        _, body = _say_new_race(api, "create a race at 11am")
        assert body["status"] == "not_understood"
        assert "did not answer in time" in body["question"]
        assert "pending_token" not in body and _races() == before


class TestTheStripAtTheTopOfThePage:
    """The two things somebody on the water looks at without asking: how long to
    the gun, and what course the fleet is on."""

    def test_the_page_shows_the_gun_and_the_course(self, logged_in_client):
        _grant_on_water()
        with ro.get_db() as db:
            created = create_race(db, RaceSpec(name="Header Race"))
            race = db.execute("SELECT * FROM races WHERE id = ?", (created.race_id,)).fetchone()
            update_race_settings(db, race, RaceSettings(course_no=1,
                                                        start_time="2099-08-12T10:55"))
        page = logged_in_client.get("/onwater").get_data(as_text=True)
        assert "Header Race" in page
        assert "Course 1" in page
        # The countdown runs to the first gun, five minutes after what is stored.
        assert "2099-08-12T11:00" in page

    def test_the_board_and_the_chart_follow_the_course(self, api):
        """The strip said "course 17" above a picture of course 16: the chart and
        the board are of a course, so they have to change when it does."""
        _, created = _say_new_race(api, "create a race at 11am")
        _confirm(api, created["pending_token"])
        _, body = _say(api, "use course 1")
        _, done = _confirm(api, body["pending_token"])
        header = done["header"]
        assert header["course_marks"], "the chart had no marks to redraw from"
        assert header["course_board"], "the course board was not sent back"
        assert all(m["mark"] for m in header["course_board"])

    def test_a_race_from_the_water_records_its_own_finishes(self, api):
        """Nobody is in the hut to press Finish — that is the premise of the
        page — so both GPS finish boxes are on from the start, and the reply says
        so, because the sentence did not ask for it."""
        _, created = _say_new_race(api, "create a race at 11am")
        assert "GPS finishes armed" in created["readback"]
        _, done = _confirm(api, created["pending_token"])
        race = ro.get_race(done["race_id"])
        assert race["gps_finish_enabled"] == 1 and race["gps_auto_confirm"] == 1
        assert "GPS finishes are armed" in done["message"]

    def test_a_command_that_moves_the_start_moves_the_countdown(self, api):
        """A header still counting to the old gun is worse than no header."""
        _, created = _say_new_race(api, "create a race at 11am")
        _, done = _confirm(api, created["pending_token"])
        assert done["header"]["first_gun"].endswith("11:00:00")
        _, moved = _say(api, "put the start back 20 minutes")
        _, done = _confirm(api, moved["pending_token"])
        assert done["header"]["first_gun"].endswith("11:20:00")

    def test_the_page_is_built_from_the_same_thing_the_replies_are(self, logged_in_client,
                                                                   monkeypatch):
        """One function builds it, so the strip cannot say one thing on load and
        another after a command."""
        _grant_on_water()
        from routes import assistant as routes_assistant
        monkeypatch.setattr(routes_assistant, "_header_state", lambda race: {
            "race_name": "Only From Here", "state": "Waiting",
            "first_gun": "2099-01-01T11:00:00", "course_text": "Course 99 · 9.9 nm",
            "wind_text": "180°T 9.9 kn", "finished": False})
        page = logged_in_client.get("/onwater").get_data(as_text=True)
        for value in ("Only From Here", "2099-01-01T11:00:00", "Course 99 · 9.9 nm"):
            assert value in page

    def test_a_course_nobody_chose_is_not_named_up_there_either(self, api):
        _, created = _say_new_race(api, "create a race at 11am")
        _, done = _confirm(api, created["pending_token"])
        assert done["header"]["course_text"] == "Course not set"

    def test_the_chart_costs_the_page_no_network_at_all(self, logged_in_client):
        """It is drawn from the marks already in the page. A phone on a boat
        should not be pulling map tiles over the 4G the hut is using."""
        _grant_on_water()
        page = logged_in_client.get("/onwater").get_data(as_text=True)
        assert "unpkg.com" not in page and "leaflet" not in page.lower()


class TestThingsTheAppKnewAndWouldNotSay:
    """Four questions from one conversation, each answered "I can't" by an app
    holding the answer: who has entered, what the wind has been doing, the
    results of a past race, and adding one named boat."""

    def _a_boat(self, name="Mojito"):
        stamp = "2026-01-01T00:00:00"
        with ro.get_db() as db:
            db.execute("INSERT INTO boats (boat_name, sail_no, class_name, status, created_at,"
                       " updated_at) VALUES (?, 'GBR1', 'IRC 1', 'ACTIVE', ?, ?)",
                       (name, stamp, stamp))
            db.commit()

    def _facts(self, api, monkeypatch, said="how is it going"):
        from routes import assistant as routes_assistant
        seen = {"facts": []}

        def watching(config, transport=None):
            def parse(text, context):
                seen["facts"] = list(context.facts)
                return None
            return parse

        monkeypatch.setattr(routes_assistant, "parser_from_config", watching)
        _say(api, said)
        return " ".join(seen["facts"])

    def test_the_boats_entered_are_named_not_counted(self, api, monkeypatch):
        self._a_boat()
        _, created = _say_new_race(api, "create a race at 11am")
        _, done = _confirm(api, created["pending_token"])
        _, added = _say(api, "add all the boats")
        _confirm(api, added["pending_token"])
        assert "Mojito" in self._facts(api, monkeypatch, "who has entered")

    def test_one_named_boat_can_be_entered(self, api, monkeypatch):
        self._a_boat()
        _, created = _say_new_race(api, "create a race at 11am")
        _, done = _confirm(api, created["pending_token"])
        from core.assistant import Intent
        from routes import assistant as routes_assistant
        monkeypatch.setattr(
            routes_assistant, "parser_from_config",
            lambda config, transport=None: (lambda text, context: Intent(
                "add_entries", {"scope": "boat", "boat": "mojito",
                                "race_id": context.current_race_id}, source="stub")))
        _, body = _say(api, "add mojito to the race")
        assert body["status"] == "needs_confirmation"
        assert "Mojito" in body["readback"]
        _, done = _confirm(api, body["pending_token"])
        assert done["added"] == 1
        assert any(e["boat_name"] == "Mojito" for e in ro.get_entries(done["race_id"]))

    def test_two_boats_in_one_sentence_are_both_entered(self, api, monkeypatch):
        """"Add Sgrech Bach and Mojito" entered the first and dropped the second
        without a word."""
        self._a_boat("Sgrech Bach")
        self._a_boat("Mojito")
        _, created = _say_new_race(api, "create a race at 11am")
        _, done = _confirm(api, created["pending_token"])
        from core.assistant import Intent
        from routes import assistant as routes_assistant
        monkeypatch.setattr(
            routes_assistant, "parser_from_config",
            lambda config, transport=None: (lambda text, context: Intent(
                "add_entries", {"scope": "boat", "boat": "sgrech bach and mojito",
                                "race_id": context.current_race_id}, source="stub")))
        _, body = _say(api, "add sgrech bach and mojito")
        assert "Sgrech Bach and Mojito" in body["readback"]
        _, added = _confirm(api, body["pending_token"])
        assert added["added"] == 2
        entered = {e["boat_name"] for e in ro.get_entries(done["race_id"])}
        assert {"Sgrech Bach", "Mojito"} <= entered

    def test_the_same_boats_as_last_time(self, api, monkeypatch):
        """"New race in ten minutes, same boats" created the race and quietly
        entered nobody."""
        self._a_boat("Sgrech Bach")
        _, first = _say_new_race(api, "create a race at 11am")
        _, done_first = _confirm(api, first["pending_token"])
        _, filled = _say(api, "add all the boats")
        _confirm(api, filled["pending_token"])
        _, second = _say_new_race(api, "create a race at 2pm")
        _, done_second = _confirm(api, second["pending_token"])

        from core.assistant import Intent
        from routes import assistant as routes_assistant
        monkeypatch.setattr(
            routes_assistant, "parser_from_config",
            lambda config, transport=None: (lambda text, context: Intent(
                "add_entries", {"scope": "same_as", "race_id": done_second["race_id"],
                                "same_as_race_id": done_first["race_id"]}, source="stub")))
        _, body = _say(api, "same boats as the last one")
        assert f"same boats as race #{done_first['race_id']}" in body["readback"]
        _, added = _confirm(api, body["pending_token"])
        assert added["added"] == len(ro.get_entries(done_first["race_id"]))
        assert {e["boat_name"] for e in ro.get_entries(done_second["race_id"])} == \
               {e["boat_name"] for e in ro.get_entries(done_first["race_id"])}

    def test_an_existing_race_can_be_put_into_a_series(self, api, monkeypatch):
        """It answered that it could only set a series when a race is created."""
        with ro.get_db() as db:
            db.execute("INSERT INTO race_series (name, created_at, updated_at)"
                       " VALUES ('ISORA', '2026-01-01T00:00:00', '2026-01-01T00:00:00')")
            db.commit()
            series_id = int(db.execute("SELECT id FROM race_series WHERE name = 'ISORA'")
                            .fetchone()["id"])
        _, created = _say_new_race(api, "create a race at 11am")
        _, done = _confirm(api, created["pending_token"])
        from core.assistant import Intent
        from routes import assistant as routes_assistant
        monkeypatch.setattr(
            routes_assistant, "parser_from_config",
            lambda config, transport=None: (lambda text, context: Intent(
                "set_start_and_course", {"race_id": done["race_id"], "series": "ISORA"},
                source="stub")))
        _, body = _say(api, "add it to the ISORA series")
        assert "series ISORA" in body["readback"]
        _confirm(api, body["pending_token"])
        race = ro.get_race(done["race_id"])
        assert race["series_id"] == series_id
        # And the rest of the race is where it was.
        assert str(race["start_time"]).endswith("10:55:00")

    def test_a_boat_the_club_does_not_have_is_a_question(self, api, monkeypatch):
        """Entering the wrong boat is a boat racing that nobody knows is racing."""
        self._a_boat()
        _, created = _say_new_race(api, "create a race at 11am")
        _confirm(api, created["pending_token"])
        from core.assistant import Intent
        from routes import assistant as routes_assistant
        monkeypatch.setattr(
            routes_assistant, "parser_from_config",
            lambda config, transport=None: (lambda text, context: Intent(
                "add_entries", {"scope": "boat", "boat": "Nonesuch",
                                "race_id": context.current_race_id}, source="stub")))
        _, body = _say(api, "add nonesuch")
        assert body["status"] == "needs_clarification"
        assert "no boat called" in body["question"]

    def test_the_results_of_a_past_race_can_be_reported(self, api, monkeypatch):
        """It has to report a race that actually has results. Asked for real
        ones, the first version of this raised and the page said only "the hut
        did not answer (500)" — a result row carries the entry and the boat,
        not a name."""
        self._a_boat("Andromeda")
        self._a_boat("Sgrech")
        from core.assistant import Intent
        from routes import assistant as routes_assistant
        _, created = _say_new_race(api, "create a race called Night Race at 11am")
        _, done = _confirm(api, created["pending_token"])
        race_id = done["race_id"]
        _, added = _say(api, "add all the boats")
        _confirm(api, added["pending_token"])
        # Finish times taken from the race's own start rather than written into
        # the test: "create a race at 11am" means today or tomorrow depending on
        # the hour it runs at, and a hard-coded date eventually lands *before*
        # the start — where elapsed clamps to zero, both boats tie on nothing,
        # and the test fails for a reason that has nothing to do with results.
        from datetime import timedelta
        start = ro.parse_dt(ro.race_first_start_time(ro.get_race(race_id)))
        with ro.get_db() as db:
            for offset, name in enumerate(("Andromeda", "Sgrech")):
                finish = start + timedelta(hours=1, minutes=offset * 5)
                db.execute("UPDATE entries SET finish_time = ?, status = 'FINISHED',"
                           " manual_irc_rating = 1.0 WHERE race_id = ? AND boat_name = ?",
                           (finish.isoformat(timespec="seconds"), race_id, name))
            db.commit()
        monkeypatch.setattr(
            routes_assistant, "parser_from_config",
            lambda config, transport=None: (lambda text, context: Intent(
                "race_results", {"race_name": "night race"}, source="stub")))
        resp, body = _say(api, "results of the night race")
        assert resp.status_code == 200, body
        assert body["status"] == "done"
        assert "1. Andromeda" in body["message"] and "2. Sgrech" in body["message"]

    def test_a_race_named_in_passing_is_matched_to_a_real_one(self, api, monkeypatch):
        from core.assistant import Intent
        from routes import assistant as routes_assistant
        monkeypatch.setattr(
            routes_assistant, "parser_from_config",
            lambda config, transport=None: (lambda text, context: Intent(
                "race_results", {"race_name": "the one that never happened"}, source="stub")))
        _, body = _say(api, "results of the one that never happened")
        assert body["status"] == "needs_clarification"

    def test_whether_the_trackers_are_reporting(self, api, monkeypatch):
        """"Are the trackers working?" was answered "the app doesn't tell me
        anything about trackers" -- by the app that collects their fixes and
        decides GPS finishes from them."""
        from core import track
        monkeypatch.setattr(track, "track_config", lambda: {"enabled": True})
        monkeypatch.setattr(track, "list_trackers", lambda: [
            {"unique_id": "A"}, {"unique_id": "B"}])
        import time as _time
        monkeypatch.setattr(track, "_latest_fix_times",
                            lambda: {"A": _time.time(), "B": _time.time() - 1800})
        facts = self._facts(api, monkeypatch, "are the trackers working")
        assert "1 of 2 trackers have reported in the last five minutes" in facts
        assert "quietest for 30 minutes" in facts

    def test_a_tracker_silent_for_days_is_said_in_days(self, api, monkeypatch):
        """"Silent for 16593 minutes" is arithmetic, not an answer."""
        from core import track
        import time as _time
        monkeypatch.setattr(track, "track_config", lambda: {"enabled": True})
        monkeypatch.setattr(track, "list_trackers", lambda: [{"unique_id": "A"}])
        monkeypatch.setattr(track, "_latest_fix_times",
                            lambda: {"A": _time.time() - 11 * 86400})
        assert "quietest for 11 days" in self._facts(api, monkeypatch, "trackers?")

    def test_and_says_plainly_when_tracking_is_off(self, api, monkeypatch):
        from core import track
        monkeypatch.setattr(track, "track_config", lambda: {"enabled": False})
        assert "switched off" in self._facts(api, monkeypatch, "are the trackers working")

    def test_what_the_wind_has_been_doing_is_told_not_just_what_it_is(self, api, monkeypatch):
        """"When did the wind last change?" was answered "the app only tells me
        the current reading" -- with an hour of samples behind the wind chart."""
        from routes import assistant as routes_assistant
        monkeypatch.setattr(routes_assistant, "_wind_now", lambda: (225.0, 12.0))
        monkeypatch.setattr(routes_assistant, "weather_history", lambda minutes=60: [
            {"twd": 200.0, "tws": 9.0}, {"twd": 215.0, "tws": 11.0}, {"twd": 230.0, "tws": 13.0}])
        facts = self._facts(api, monkeypatch, "when did the wind last change")
        assert "30 degree shift to the right" in facts
        assert "9.0 to 13.0 knots" in facts


class TestARefreshDoesNotLoseTheConversation:
    """On a boat a refresh is a dropped signal or a locked phone, not a decision
    to start again. Everything said was already recorded; the page never asked."""

    def test_what_was_said_comes_back_with_the_page(self, api, logged_in_client):
        _say_new_race(api, "create a race called Reload Test at 11am")
        _say(api, "status")
        page = logged_in_client.get("/onwater").get_data(as_text=True)
        assert "create a race called Reload Test at 11am" in page
        assert "Reload Test" in page

    def test_and_a_proposal_can_still_be_agreed_to(self, api, logged_in_client):
        """Otherwise the command sits on the server with nothing on screen able
        to answer it."""
        before = _races()
        _, body = _say_new_race(api, "create a race at 11am")
        page = logged_in_client.get("/onwater").get_data(as_text=True)
        assert body["pending_token"] in page
        _, done = _confirm(api, body["pending_token"])
        assert done["status"] == "done" and _races() == before + 1

    def test_a_page_with_nothing_behind_it_is_just_the_greeting(self, logged_in_client):
        _grant_on_water()
        page = logged_in_client.get("/onwater").get_data(as_text=True)
        assert "Nothing happens until you say yes." in page
        assert "ow-you" not in page

    def test_each_message_says_when_it_was_said(self, api, logged_in_client):
        """Two "use course 4"s in one conversation: which was the later?"""
        import re
        _say(api, "status")
        page = logged_in_client.get("/onwater").get_data(as_text=True)
        assert re.search(r'<div class="ow-msg ow-you"><p>status</p><span class="ow-time">\d\d:\d\d</span>', page)


class TestMakingUpACourse:
    """Building a course used to mean dragging marks around a page 1100px wide.
    From a boat it is a sentence, read back mark by mark before anything is set."""

    def _course(self, api, monkeypatch, marks, race_id=None):
        from core.assistant import Intent
        from routes import assistant as routes_assistant
        monkeypatch.setattr(
            routes_assistant, "parser_from_config",
            lambda config, transport=None: (lambda text, context: Intent(
                "set_custom_course",
                {"marks": marks, "race_id": race_id or context.current_race_id}, source="stub")))
        return _say(api, "make me a course")

    def test_a_course_can_be_made_up_and_set(self, api, monkeypatch):
        _, created = _say_new_race(api, "create a race at 11am")
        _, done = _confirm(api, created["pending_token"])
        _, body = self._course(api, monkeypatch, "O port, 1 port, O port", done["race_id"])
        assert "Op 1p Op" in body["readback"], body["readback"]
        _, set_up = _confirm(api, body["pending_token"])
        race = ro.get_race(done["race_id"])
        assert race["custom_course_json"], "no made-up course was stored"
        assert race["course_set"] == 1
        assert "nm" in set_up["message"]

    def test_the_board_is_read_back_before_anything_is_set(self, api, monkeypatch):
        """A course sent to a fleet is worth checking mark by mark."""
        before = _races()
        _, created = _say_new_race(api, "create a race at 11am")
        _, done = _confirm(api, created["pending_token"])
        _, body = self._course(api, monkeypatch, "O port, 4 starboard, O port", done["race_id"])
        assert body["status"] == "needs_confirmation"
        assert "4s" in body["readback"]
        assert ro.get_race(done["race_id"])["custom_course_json"] is None
        assert _races() == before + 1

    def test_a_mark_the_club_does_not_have_is_refused_with_the_ones_it_does(
            self, api, monkeypatch):
        _, created = _say_new_race(api, "create a race at 11am")
        _, done = _confirm(api, created["pending_token"])
        _, body = self._course(api, monkeypatch, "O port, ZZ port", done["race_id"])
        assert body["status"] == "needs_clarification"
        assert "Unknown mark" in body["question"] and "The club's marks are" in body["question"]

    def test_the_race_sheet_and_the_water_save_it_the_same_way(self):
        """One implementation: the course builder route calls the same function."""
        repo = pathlib.Path(__file__).resolve().parent.parent
        source = (repo / "routes" / "race_course.py").read_text(encoding="utf-8")
        assert "set_custom_course(db, race, sequence" in source
        assert "custom_course_json = ?" not in source


class TestItCanAskTheAppForMore:
    """The alternative was sending 24 marks, 67 courses, the boat database and a
    polar with every command typed on 4G. The facts carry what is always
    relevant; this fetches the rest when it is asked for."""

    def _ask(self, api, monkeypatch, topic, **args):
        from core.assistant import Intent
        from routes import assistant as routes_assistant
        monkeypatch.setattr(
            routes_assistant, "parser_from_config",
            lambda config, transport=None: (lambda text, context: Intent(
                "look_up", {"topic": topic, **args}, source="stub")))
        _, body = _say(api, f"look up {topic}")
        return body

    @pytest.mark.parametrize("topic,expected", [
        ("marks", "mark(s)"),
        ("courses", "fixed courses"),
        ("series", "no series set up"),
        ("polar", "boat speed by true wind angle"),
        ("sails", "sail chart"),
    ])
    def test_each_topic_answers_from_the_clubs_own_data(self, api, monkeypatch, topic, expected):
        body = self._ask(api, monkeypatch, topic)
        assert body["status"] == "done", body
        assert expected in body["message"]

    def test_a_course_gives_its_legs_and_their_distances(self, api, monkeypatch):
        """"How far is it from O to 1?" got the total course length — from the
        walk the app had just done leg by leg to work that total out."""
        from routes import assistant as routes_assistant
        monkeypatch.setattr(routes_assistant, "_wind_now", lambda: (225.0, 12.0))
        body = self._ask(api, monkeypatch, "course", course_no=1)
        assert "nm" in body["message"] and "Legs:" in body["message"]

    def test_an_exact_mark_wins_over_a_name_that_contains_the_letter(self, api, monkeypatch):
        body = self._ask(api, monkeypatch, "marks", query="o")
        assert body["message"].startswith("1 mark(s): O ")

    def test_a_topic_it_does_not_have_is_a_question_listing_the_ones_it_does(
            self, api, monkeypatch):
        body = self._ask(api, monkeypatch, "tides")
        assert body["status"] == "needs_clarification"
        assert "marks" in body["question"] and "polar" in body["question"]

    def test_looking_something_up_changes_nothing(self, api, monkeypatch):
        before = _races()
        self._ask(api, monkeypatch, "courses")
        assert _races() == before


class TestLookingUpABoatsRating:
    """Three places hold a rating and they are not the same thing: the boat
    database is what a race scores on, and the IRC listing and YTC sheet are
    where it came from. Somebody asking is usually asking because something does
    not look right, so all of it is reported."""

    IRC_ROW = {"Boat Name": "Halcyon", "Sail No": "GBR7777", "TCC": "1.021",
               "Cert No": "IRC/7777"}
    YTC_ROW = {"Boat Name": "Halcyon", "Sail No": "GBR7777", "YTC": "1180"}

    def _listings(self, monkeypatch, irc=None, ytc=None):
        from core import boats
        monkeypatch.setattr(boats, "read_irc_listing",
                            lambda url, timeout=20: [self.IRC_ROW] if irc is None else irc)
        monkeypatch.setattr(boats, "read_ytc_listing",
                            lambda url, timeout=20: [self.YTC_ROW] if ytc is None else ytc)
        from routes import assistant as routes_assistant
        monkeypatch.setattr(routes_assistant, "listing_config",
                            lambda: {"irc_listing_url": "http://irc", "ytc_listing_url": "http://ytc"})

    def _say_tool(self, api, monkeypatch, name, boat):
        from core.assistant import Intent
        from routes import assistant as routes_assistant
        monkeypatch.setattr(
            routes_assistant, "parser_from_config",
            lambda config, transport=None: (lambda text, context: Intent(
                name, {"boat": boat}, source="stub")))
        return _say(api, f"{name} {boat}")

    def _a_boat(self, name, sail, irc=None, ytc=None):
        stamp = "2026-01-01T00:00:00"
        with ro.get_db() as db:
            db.execute("INSERT INTO boats (boat_name, sail_no, irc_rating, ytc_rating, status,"
                       " created_at, updated_at) VALUES (?, ?, ?, ?, 'ACTIVE', ?, ?)",
                       (name, sail, irc, ytc, stamp, stamp))
            db.commit()

    def test_a_boat_in_the_database_reports_both_ratings(self, api, monkeypatch):
        self._a_boat("Mojito", "GBR4822R", irc=1.084, ytc=1150.0)
        self._listings(monkeypatch)
        _, body = self._say_tool(api, monkeypatch, "boat_rating", "Mojito")
        assert body["status"] == "done"
        assert "IRC 1.084" in body["message"] and "YTC 1150.0" in body["message"]

    def test_it_can_be_asked_by_sail_number(self, api, monkeypatch):
        self._a_boat("Sgrech Bach", "GBR933", irc=0.95, ytc=1100.0)
        self._listings(monkeypatch)
        _, body = self._say_tool(api, monkeypatch, "boat_rating", "GBR933")
        assert "Sgrech Bach" in body["message"]

    def test_a_boat_the_club_does_not_have_is_looked_up_in_the_listings(self, api, monkeypatch):
        """The same lookup the race office does when adding a boat."""
        self._listings(monkeypatch)
        _, body = self._say_tool(api, monkeypatch, "boat_rating", "Halcyon")
        assert "not in the club's boat database" in body["message"]
        assert "IRC listing has Halcyon" in body["message"] and "1.021" in body["message"]
        assert "YTC sheet has Halcyon" in body["message"] and "1180" in body["message"]
        assert "add Halcyon to the boat database" in body["message"]

    def test_and_can_then_be_added(self, api, monkeypatch):
        self._listings(monkeypatch)
        _, body = self._say_tool(api, monkeypatch, "add_boat", "Halcyon")
        assert body["status"] == "needs_confirmation", "adding a boat is not read-only"
        assert "existing record is left alone" in body["readback"]
        _, done = _confirm(api, body["pending_token"])
        assert "added to the boat database" in done["message"]
        with ro.get_db() as db:
            row = db.execute("SELECT * FROM boats WHERE boat_name = 'Halcyon'").fetchone()
        assert row["sail_no"] == "GBR7777"
        assert float(row["irc_rating"]) == 1.021 and float(row["ytc_rating"]) == 1180.0

    def test_an_existing_boat_is_never_overwritten(self, api, monkeypatch):
        """A rating taken from a listing over an existing record, from a phone,
        without the two side by side, is how a season is scored on the wrong
        number."""
        self._a_boat("Halcyon", "GBR7777", irc=0.888, ytc=999.0)
        self._listings(monkeypatch)
        _, body = self._say_tool(api, monkeypatch, "add_boat", "Halcyon")
        _, done = _confirm(api, body["pending_token"])
        assert "already in the boat database" in done["message"]
        with ro.get_db() as db:
            row = db.execute("SELECT * FROM boats WHERE boat_name = 'Halcyon'").fetchone()
        assert float(row["irc_rating"]) == 0.888, "the listing overwrote the club's own record"
        assert float(row["ytc_rating"]) == 999.0

    def test_nor_by_sail_number_under_a_different_name(self, api, monkeypatch):
        self._a_boat("Halcyon Days", "GBR 7777", irc=0.888)
        self._listings(monkeypatch)
        _, body = self._say_tool(api, monkeypatch, "add_boat", "GBR7777")
        _, done = _confirm(api, body["pending_token"])
        with ro.get_db() as db:
            count = db.execute("SELECT COUNT(*) FROM boats").fetchone()[0]
        assert count == 1, "a second record was created for the same sail number"

    def test_a_boat_in_neither_place_says_so(self, api, monkeypatch):
        self._listings(monkeypatch, irc=[], ytc=[])
        _, body = self._say_tool(api, monkeypatch, "boat_rating", "Nonesuch")
        assert "not in the IRC listing or the YTC sheet" in body["message"]

    def test_one_listing_being_unreachable_does_not_hide_the_other(self, api, monkeypatch):
        from core import boats
        self._listings(monkeypatch)

        def broken(url, timeout=20):
            raise RuntimeError("connection refused")

        monkeypatch.setattr(boats, "read_irc_listing", broken)
        _, body = self._say_tool(api, monkeypatch, "boat_rating", "Halcyon")
        assert "YTC sheet has Halcyon" in body["message"]
        assert "IRC listing could not be read" in body["message"]


class TestTheHornFollowsTheStartTime:
    """The app told a race officer that setting a start time here "just updates
    the stored gun time". It does not: with start automation on, the scheduler
    fires the whole sequence off that stored warning signal, so moving it moves
    the horn. Saying otherwise on the water is the wrong way round to be wrong."""

    def _facts(self, api, monkeypatch):
        from routes import assistant as routes_assistant
        seen = {"facts": []}

        def watching(config, transport=None):
            def parse(text, context):
                seen["facts"] = list(context.facts)
                return None
            return parse

        monkeypatch.setattr(routes_assistant, "parser_from_config", watching)
        _say(api, "will the horn go off")
        return " ".join(seen["facts"])

    def test_it_is_told_the_horn_follows_the_stored_time(self, api, monkeypatch):
        import app as ro_app
        from routes import assistant as routes_assistant
        monkeypatch.setattr(routes_assistant._app, "race_console_config",
                            lambda: {"start_automation_horn_enabled": True})
        facts = self._facts(api, monkeypatch)
        assert "no separate arming step" in facts
        assert "moving a start time moves the horn" in facts.lower()

    def test_and_told_plainly_when_automation_is_off(self, api, monkeypatch):
        from routes import assistant as routes_assistant
        monkeypatch.setattr(routes_assistant._app, "race_console_config",
                            lambda: {"start_automation_horn_enabled": False})
        assert "no horn will sound by itself" in self._facts(api, monkeypatch)


class TestShorteningAtAMarkTheCoursePassesTwice:
    """Course 3 rounds mark 4 three times, so "shorten at 4" names three
    different finishes — and the app took the first, which is the one answer
    that is certainly wrong: the fleet sailed past it half an hour ago."""

    def _a_race_on(self, api, course_no):
        _, created = _say_new_race(api, "create a race at 11am")
        _, done = _confirm(api, created["pending_token"])
        _, chosen = _say(api, f"use course {course_no}")
        _confirm(api, chosen["pending_token"])
        return done["race_id"]

    def _repeated_mark(self, course_no=3):
        from core.courses import course_shorten_options
        options = course_shorten_options(ro.appstate.COURSE_BY_NO[course_no])
        counts = {}
        for opt in options:
            counts.setdefault(opt["display"], []).append(opt)
        for display, opts in counts.items():
            if len(opts) > 1:
                return display, opts
        pytest.skip("no course with a repeated mark")

    def test_with_nothing_tracking_it_asks_which_rounding(self, api):
        display, opts = self._repeated_mark()
        self._a_race_on(api, 3)
        _, body = _say(api, f"shorten at mark {display}")
        assert body["status"] == "needs_clarification"
        assert "cannot tell which one" in body["question"]
        for opt in opts:
            assert opt["label"] in body["question"]

    def test_with_the_fleet_tracked_it_takes_the_one_they_are_coming_to(self, api, monkeypatch):
        display, opts = self._repeated_mark()
        race_id = self._a_race_on(api, 3)
        # The leader is past the first rounding of that mark.
        from core import track
        monkeypatch.setattr(track, "race_leaderboard",
                            lambda rid, at_ts=None: [{"tracked": True, "boat_name": "A",
                                                      "rounded": opts[0]["index"] + 1,
                                                      "total": 13}])
        _, body = _say(api, f"shorten at mark {display}")
        assert body["status"] == "needs_confirmation"
        assert body["resolved"]["at_index"] == opts[1]["index"], \
            "it shortened at a rounding the fleet had already passed"

    def test_the_readback_says_which_rounding(self, api, monkeypatch):
        display, opts = self._repeated_mark()
        self._a_race_on(api, 3)
        from core import track
        monkeypatch.setattr(track, "race_leaderboard",
                            lambda rid, at_ts=None: [{"tracked": True, "boat_name": "A",
                                                      "rounded": opts[0]["index"] + 1,
                                                      "total": 13}])
        _, body = _say(api, f"shorten at mark {display}")
        assert opts[1]["label"] in body["readback"], body["readback"]

    def test_a_mark_named_once_is_not_made_into_a_question(self, api):
        """The common case stays one step."""
        self._a_race_on(api, 1)
        _, body = _say(api, "shorten at mark 9")
        assert body["status"] == "needs_confirmation"

    def test_a_mark_name_is_never_matched_against_an_index(self, api):
        """"shorten at mark 1" was matching the mark at index 1 — mark 8 on
        course 1 — and survived only because mark 1 happened to come first."""
        self._a_race_on(api, 1)
        _, body = _say(api, "shorten at mark 1")
        assert body["status"] == "needs_confirmation"
        assert body["resolved"]["at_mark"] == "1"
        assert body["resolved"]["at_index"] == 0


class TestWithNoInterpreter:
    """The club's decision: this feature is unavailable until a model is
    configured. The built-in grammar used to answer instead, and on a page whose
    whole premise is "type what you want to do", six sentence shapes read as an
    app that is simply stupid — and the person on the water cannot tell a
    sentence it will not understand from one it has misunderstood."""

    @pytest.fixture(autouse=True)
    def no_model(self, monkeypatch):
        from routes import assistant as routes_assistant
        monkeypatch.setattr(routes_assistant, "interpreter_status",
                            lambda config: {"model": "", "grammar_only": True, "error": "",
                                            "text": "built-in grammar only"})
        monkeypatch.setattr(routes_assistant, "parser_from_config",
                            lambda config, transport=None: None)

    def test_the_command_endpoint_says_it_is_unavailable(self, api):
        before = _races()
        resp, body = _say_new_race(api, "create a race at 11am")
        assert resp.status_code == 503
        assert "not available" in body["error"] and "Settings" in body["error"]
        assert _races() == before

    def test_and_so_does_the_confirm_endpoint(self, api):
        resp, body = _confirm(api, "pc_anything")
        assert resp.status_code == 503

    def test_the_page_says_so_and_offers_no_box_to_type_in(self, logged_in_client):
        _grant_on_water()
        page = logged_in_client.get("/vro").get_data(as_text=True)
        assert "Not available" in page
        assert "Settings → Virtual Race Officer" in page
        assert 'id="sayText"' not in page, "a box that cannot do anything still invites typing"

    def test_nothing_is_read_by_the_grammar_behind_the_scenes(self, api):
        """Not even the sentences it does understand: half a feature that only
        works for six shapes is the thing being removed."""
        before = _races()
        for said in ("status", "add all the boats", "shorten at mark 4"):
            resp, _ = _say(api, said)
            assert resp.status_code == 503, f"{said!r} was answered by the grammar"
        assert _races() == before


class TestWhoMayUseIt:
    def test_without_the_permission_the_endpoint_refuses(self, logged_in_client):
        """Being an administrator is not the same as being allowed to start a
        race with nobody at the line."""
        _grant_on_water(allowed=False)
        with logged_in_client.session_transaction() as sess:
            sess["_csrf_token"] = TOKEN
        resp = logged_in_client.post("/admin/api/assistant/command",
                                     json={"text": "create a race at 11am"},
                                     headers={"X-CSRF-Token": TOKEN})
        assert resp.status_code == 403
        assert "permission" in (resp.get_json() or {}).get("error", "")

    def test_without_the_permission_the_page_says_why(self, logged_in_client):
        _grant_on_water(allowed=False)
        resp = logged_in_client.get("/onwater")
        assert resp.status_code == 403
        assert b"does not have permission" in resp.data
        assert b"Virtual Race Officer" in resp.data

    def test_with_the_permission_the_page_opens(self, logged_in_client):
        _grant_on_water(allowed=True)
        resp = logged_in_client.get("/onwater")
        assert resp.status_code == 200
        assert b"Say what to do" in resp.data

    def test_it_is_behind_login(self, client):
        """Not in the public allow-list: today the people who can drive the
        racing by typing are exactly those who can already do it on the race
        sheet."""
        with client.session_transaction() as sess:
            sess["_csrf_token"] = TOKEN
        resp = client.post("/api/assistant/command",
                           json={"text": "create a race at 11am", "_csrf_token": TOKEN},
                           headers={"X-CSRF-Token": TOKEN})
        assert resp.status_code in (302, 401, 403), \
            "the command endpoint answered without a login"

    def test_a_state_changing_call_still_needs_its_csrf_token(self, logged_in_client):
        resp = logged_in_client.post("/admin/api/assistant/command",
                                     json={"text": "create a race at 11am"})
        assert resp.status_code == 400

    def test_what_was_typed_and_what_was_done_are_both_in_the_activity_log(self, api, monkeypatch):
        lines = []
        monkeypatch.setattr(ro, "log_activity",
                            lambda action, details="", user="system": lines.append(action))
        _, body = _say_new_race(api, "create a race at 11am")
        _confirm(api, body["pending_token"])
        assert "assistant command proposed" in lines
        assert "assistant command confirmed" in lines


class TestAShortenedCourseIsShownShortened:
    """The board is what a race officer reads out. It was showing all thirteen
    marks of course 3, and 10.3 nm, beside the words "shortened at 4"."""

    def test_the_board_and_the_length_are_of_the_course_being_sailed(self, api):
        from core.courses import apply_course_shortening, course_for_race
        from routes import assistant as routes_assistant
        _, created = _say_new_race(api, "create a race at 11am")
        _, done = _confirm(api, created["pending_token"])
        _, chosen = _say(api, "use course 3")
        _confirm(api, chosen["pending_token"])
        race = ro.get_race(done["race_id"])
        full = course_for_race(race)
        with ro.get_db() as db:
            db.execute("UPDATE races SET shortened_at_index = 1, shortened_at_mark = '4'"
                       " WHERE id = ?", (done["race_id"],))
            db.commit()
        race = ro.get_race(done["race_id"])
        header = routes_assistant._header_state(race)
        assert len(header["course_board"]) == 2, "the board still showed the whole course"
        assert len(header["course_marks"]) == 2
        assert float(str(header["course_text"]).split(" nm")[0].split("· ")[-1]) \
               < float(full["length_nm"]), "the length was of the course as set"
        assert "shortened at 4" in header["course_text"]


class TestTheContextCannotHangThePage:
    """Reported from the live hut: three commands in a row, including a plain
    "status", answered 504 — Cloudflare's patience ran out waiting for the app.
    Every fact is a database read or a walk over a race, and on the club's own
    hut the track database is a season deep with the relay writing to it while
    the clubhouse display and every phone are reading. "Usually fast" is not
    "bounded"."""

    def test_a_slow_fact_does_not_stop_the_command(self, api, monkeypatch):
        from routes import assistant as routes_assistant
        import time as _time

        def glacial(*a, **k):
            _time.sleep(3.0)
            return ["never seen"]

        monkeypatch.setattr(routes_assistant, "FACTS_BUDGET_S", 0.5)
        monkeypatch.setattr(routes_assistant, "_wind_trend", glacial)
        started = _time.monotonic()
        resp, body = _say(api, "status")
        assert resp.status_code == 200, "a slow fact took the command down with it"
        # The one slow step is paid for once; nothing after it is attempted.
        assert _time.monotonic() - started < 8

    def test_and_the_interpreter_is_told_the_list_is_short(self, api, monkeypatch):
        from routes import assistant as routes_assistant
        import time as _time
        seen = {"facts": []}

        def watching(config, transport=None):
            def parse(text, context):
                seen["facts"] = list(context.facts)
                return None
            return parse

        monkeypatch.setattr(routes_assistant, "FACTS_BUDGET_S", -1.0)
        monkeypatch.setattr(routes_assistant, "parser_from_config", watching)
        _say(api, "how is it going")
        assert any("left out because the app was busy" in f for f in seen["facts"]), \
            "half the facts were presented as all of them"

    def test_one_fact_that_raises_does_not_take_the_others(self, api, monkeypatch):
        from routes import assistant as routes_assistant
        _, created = _say_new_race(api, "create a race at 11am")
        _confirm(api, created["pending_token"])
        monkeypatch.setattr(routes_assistant, "_tracker_facts",
                            lambda: (_ for _ in ()).throw(RuntimeError("traccar is down")))
        routes_assistant._FACT_CACHE.clear()
        resp, body = _say(api, "status")
        assert resp.status_code == 200 and body["status"] == "done"

    def test_the_fleet_walk_is_skipped_for_a_race_that_is_over(self, api, monkeypatch):
        """It walks every fix of every boat, and nobody on the water needs the
        positions in a race that has finished."""
        from routes import assistant as routes_assistant
        called = []
        monkeypatch.setattr(routes_assistant, "_fleet_facts",
                            lambda race: called.append(race) or [])
        monkeypatch.setattr(routes_assistant, "race_is_finished_for_public", lambda rid: True)
        routes_assistant._FACT_CACHE.clear()
        _, created = _say_new_race(api, "create a race at 11am")
        _confirm(api, created["pending_token"])
        _say(api, "status")
        assert called == [], "the finished race's fleet positions were computed anyway"


def _answers_with(monkeypatch, parse):
    """Stand a parser in for the model, for one test."""
    from routes import assistant as routes_assistant
    monkeypatch.setattr(routes_assistant, "parser_from_config",
                        lambda config, transport=None: parse)


def _boat(name, sail="GBR1"):
    stamp = "2026-01-01T00:00:00"
    with ro.get_db() as db:
        db.execute("INSERT INTO boats (boat_name, sail_no, class_name, status, created_at,"
                   " updated_at) VALUES (?, ?, 'IRC 1', 'ACTIVE', ?, ?)", (name, sail, stamp, stamp))
        db.commit()


def _upcoming_race(name="Evening Race"):
    with ro.get_db() as db:
        created = create_race(db, RaceSpec(name=name))
        race = db.execute("SELECT * FROM races WHERE id = ?", (created.race_id,)).fetchone()
        update_race_settings(db, race, RaceSettings(name=name, course_no=1,
                                                    start_time="2099-08-12T18:55"))
    return created.race_id


class TestANewRaceOnTheCourseItWasGiven:
    """"Create a race at 11 on course 4" was read back with course 5, chosen for
    the wind, and a "Say no if you would rather pick your own" -- because the
    tool had nowhere to put the 4."""

    def _create(self, api, monkeypatch, **arguments):
        from core.assistant import Intent
        _answers_with(monkeypatch, lambda text, context: Intent("create_race", {
            "race_type": "standard", "gun_time": "2099-08-12T11:00", **arguments}))
        return _say_new_race(api, "create a race at 11 on course 4")[1]

    def test_the_named_course_is_read_back_with_its_length_and_nothing_else(self, api, monkeypatch):
        body = self._create(api, monkeypatch, course_no=4)
        assert body["status"] == "needs_confirmation", body
        length = ro.appstate.COURSE_BY_NO[4]["length_nm"]
        assert f"course 4 ({length} nm)" in body["readback"]
        assert "chosen for the wind" not in body["readback"]

    def test_and_it_is_the_course_the_race_gets(self, api, monkeypatch):
        body = self._create(api, monkeypatch, course_no=4)
        _, done = _confirm(api, body["pending_token"])
        race = ro.get_race(done["race_id"])
        assert int(race["course_no"]) == 4 and int(race["course_set"]) == 1

    def test_a_course_the_club_does_not_have_is_a_question(self, api, monkeypatch):
        body = self._create(api, monkeypatch, course_no=999)
        assert body["status"] == "needs_clarification"
        assert "no course 999" in body["question"]

    def test_named_boats_are_entered_with_it(self, api, monkeypatch):
        _boat("Mojito")
        _boat("Sgrech Bach", "GBR933")
        body = self._create(api, monkeypatch, course_no=4, boats="mojito, sgrech bach")
        assert "Mojito and Sgrech Bach" in body["readback"], body
        _, done = _confirm(api, body["pending_token"])
        entered = {e["boat_name"] for e in ro.get_entries(done["race_id"])}
        assert entered == {"Mojito", "Sgrech Bach"}


class TestTwoJobsInOneSentence:
    """"Use course 4 and add Mojito" changed the course and never mentioned
    Mojito again."""

    def _plan(self, api, monkeypatch, *steps):
        from core.assistant import Intent
        _answers_with(monkeypatch, lambda text, context: Intent("plan", {"steps": [
            {"name": name, "arguments": args} for name, args in steps]}))
        return _say(api, "use course 4 and add Mojito")[1]

    def test_both_are_read_back_and_both_are_done_on_one_yes(self, api, monkeypatch):
        _boat("Mojito")
        race_id = _upcoming_race()
        body = self._plan(api, monkeypatch,
                          ("set_start_and_course", {"race_id": race_id, "course_no": 4}),
                          ("add_entries", {"race_id": race_id, "scope": "boat", "boat": "mojito"}))
        assert body["status"] == "needs_confirmation", body
        assert body["readback"].startswith("In this order: (1) Set")
        assert "course 4" in body["readback"] and "(2) Add Mojito" in body["readback"]
        _, done = _confirm(api, body["pending_token"])
        assert done["status"] == "done", done
        assert int(ro.get_race(race_id)["course_no"]) == 4
        assert [e["boat_name"] for e in ro.get_entries(race_id)] == ["Mojito"]

    def test_a_step_that_would_fail_is_found_before_anything_is_read_back(self, api, monkeypatch):
        race_id = _upcoming_race()
        body = self._plan(api, monkeypatch,
                          ("set_start_and_course", {"race_id": race_id, "course_no": 4}),
                          ("add_entries", {"race_id": race_id, "scope": "boat", "boat": "Nobody"}))
        assert body["status"] == "needs_clarification"
        assert "Nobody" in body["question"]
        assert int(ro.get_race(race_id)["course_no"]) == 1, "the first step ran anyway"


class TestTheModelCanLookSomethingUp:
    """The page hands the interpreter a way to run a read-only tool, through the
    same path the page would take for it -- and gets the club's own answer."""

    def test_a_distance_between_any_two_marks_is_the_apps_own(self, api, monkeypatch):
        from core.assistant import ANSWER, Intent
        from core.timeutils import haversine_nm
        heard = {}

        def parse(text, context):
            heard["said"] = context.read("mark_distance", {"from_mark": "O", "to_mark": "mark 1"})
            return Intent(ANSWER, {"text": heard["said"]})
        _answers_with(monkeypatch, parse)
        _, body = _say(api, "how far from O to 1?")
        assert body["status"] == "answered"
        o, one = ro.appstate.MARKS["O"], ro.appstate.MARKS["1"]
        nm = haversine_nm(float(o["lat"]), float(o["lon"]), float(one["lat"]), float(one["lon"]))
        assert f"{round(nm, 2)} nm" in heard["said"]
        assert "true" in heard["said"]

    def test_a_mark_it_does_not_have_is_said_so_with_the_ones_it_does(self, api, monkeypatch):
        from core.assistant import ANSWER, Intent
        _answers_with(monkeypatch, lambda text, context: Intent(ANSWER, {
            "text": context.read("mark_distance", {"from_mark": "O", "to_mark": "Atlantis"})}))
        _, body = _say(api, "O to Atlantis?")
        assert "no mark called 'Atlantis'" in body["answer"] and "O" in body["answer"]

    def test_a_change_cannot_be_run_as_a_look_up(self, api, monkeypatch):
        from core.assistant import ANSWER, Intent
        before = _races()
        _answers_with(monkeypatch, lambda text, context: Intent(ANSWER, {
            "text": context.read("create_race", {"race_type": "standard"})}))
        _, body = _say(api, "sneaky")
        assert "cannot be run as a look-up" in body["answer"]
        assert _races() == before

    def test_past_races_come_with_their_series_and_course(self, api, monkeypatch):
        """Asked what course the last ISORA race used, the app had names and
        dates to offer and nothing else."""
        from core.assistant import ANSWER, Intent
        with ro.get_db() as db:
            db.execute("INSERT INTO race_series (name, created_at, updated_at)"
                       " VALUES ('ISORA 2026', '2026-01-01', '2026-01-01')")
            series_id = db.execute("SELECT id FROM race_series WHERE name = 'ISORA 2026'").fetchone()[0]
            db.commit()
            created = create_race(db, RaceSpec(name="ISORA CW5 Night Race", series_id=series_id))
        _answers_with(monkeypatch, lambda text, context: Intent(ANSWER, {
            "text": context.read("look_up", {"topic": "races", "query": "isora"})}))
        _, body = _say(api, "what course was the last isora race?")
        assert f"#{created.race_id} 'ISORA CW5 Night Race'" in body["answer"]
        assert "ISORA 2026" in body["answer"] and "course" in body["answer"]


class TestAQuestionIsNotTurnedIntoACommand:
    """The page used to run its built-in grammar over any sentence the model had
    answered in words, and a command shape in it won: "which is longer, course 3
    or course 4?" came back as a proposal to set course 3."""

    def test_a_question_the_model_answered_stays_answered(self, api, monkeypatch):
        from core.assistant import ANSWER, Intent
        _upcoming_race()
        _answers_with(monkeypatch, lambda text, context: Intent(ANSWER, {
            "text": "Course 3 is longer."}))
        _, body = _say(api, "Which is longer, course 3 or course 4?")
        assert body["status"] == "answered", body



def _read(api, monkeypatch, name, arguments):
    """What the interpreter would be told for this look-up, through the page."""
    from core.assistant import ANSWER, Intent
    _answers_with(monkeypatch, lambda text, context: Intent(ANSWER, {
        "text": context.read(name, arguments)}))
    return _say(api, f"look up {name}")[1]["answer"]


class TestTheLookUpsBehindTheQuestions:
    """Each answers from the function its own page uses; these hold the wiring
    and the words, and scripts/eval_vro.py holds the answers on real data."""

    def test_standings_name_the_series_and_bracket_the_discards(self, api, monkeypatch):
        from routes import assistant_reads
        with ro.get_db() as db:
            db.execute("INSERT INTO race_series (name, created_at, updated_at)"
                       " VALUES ('Autumn Series 2026', '2026-01-01', '2026-01-01')")
            db.commit()
        table = {"title": "IRC series", "race_count": 3, "discard_count": 1, "constituted": True,
                 "min_races_to_constitute": 2, "class_name": "",
                 "rows": [{"rank": 1, "tied": False, "total": 2.0,
                           "competitor": {"boat_name": "MOJITO"},
                           "scores": [{"code": "1", "points": 1.0, "discard": False},
                                      {"code": "DNC", "points": 6.0, "discard": True},
                                      {"code": "1", "points": 1.0, "discard": False}]}]}
        monkeypatch.setattr(assistant_reads, "build_series_results",
                            lambda series_id: {"irc": {"tables": [table]}, "ytc": {"tables": []}})
        said = _read(api, monkeypatch, "series_standings", {"series": "autumn"})
        assert said.startswith("Autumn Series 2026.")
        assert "1. MOJITO 2 pts (1, [DNC] 6, 1)" in said

    def test_a_series_the_club_does_not_have_is_said_so(self, api, monkeypatch):
        said = _read(api, monkeypatch, "series_standings", {"series": "nope"})
        assert "no series called 'nope'" in said

    def test_the_log_leaves_out_the_spoken_countdown_unless_asked(self, api, monkeypatch):
        from core.eventlog import log_event
        race_id = _upcoming_race()
        log_event(race_id, "horn", "Start signal", source="test")
        log_event(race_id, "audio", "Audio: thirty seconds", source="test")
        said = _read(api, monkeypatch, "race_log", {"race_id": race_id})
        assert "Start signal" in said and "thirty seconds" not in said
        said = _read(api, monkeypatch, "race_log", {"race_id": race_id, "include_audio": True})
        assert "thirty seconds" in said

    def test_entries_carry_the_ratings_the_race_is_scored_on(self, api, monkeypatch):
        _boat("Mojito")
        race_id = _upcoming_race()
        with ro.get_db() as db:
            add_entries_for(db, race_id, "Mojito")
            db.execute("UPDATE entries SET manual_irc_rating = 1.084 WHERE race_id = ?", (race_id,))
            db.commit()
        said = _read(api, monkeypatch, "race_entries", {"race_id": race_id})
        assert "1 entered" in said and "Mojito" in said and "IRC 1.084" in said

    def test_a_detected_finish_waiting_for_a_yes_is_listed(self, api, monkeypatch):
        _boat("Mojito")
        race_id = _upcoming_race()
        with ro.get_db() as db:
            add_entries_for(db, race_id, "Mojito")
            entry_id = db.execute("SELECT id FROM entries WHERE race_id = ?", (race_id,)).fetchone()[0]
            db.execute("INSERT INTO finish_proposals (race_id, entry_id, detected_time, source,"
                       " status, created_at) VALUES (?, ?, '2099-08-12T19:41:05', 'gps', 'pending',"
                       " '2099-08-12T19:41:06')", (race_id, entry_id))
            db.commit()
        said = _read(api, monkeypatch, "gps_finishes", {"race_id": race_id})
        assert "pending (1): Mojito at 19:41:05" in said

    def test_no_wind_recorded_is_said_so(self, api, monkeypatch):
        said = _read(api, monkeypatch, "wind_history", {"minutes": 30})
        assert "recorded nothing in the last 30 minutes" in said

    def test_a_course_on_marks_that_do_not_exist_is_not_timed(self, api, monkeypatch):
        said = _read(api, monkeypatch, "time_course", {"marks": "Zp Op", "twd": 200, "tws": 12})
        assert "Z" in said and "The club's marks are" in said

    def test_a_course_is_timed_leg_by_leg(self, api, monkeypatch):
        said = _read(api, monkeypatch, "time_course", {"marks": "4p Op", "twd": 200, "tws": 12})
        assert said.startswith("4p Op:") and "minutes" in said and "O-4" in said

    def test_a_suggestion_says_how_to_set_it(self, api, monkeypatch):
        said = _read(api, monkeypatch, "suggest_course",
                     {"target_minutes": 60, "twd": 200, "tws": 12})
        assert "Made-up courses" in said and "To set it:" in said


def add_entries_for(db, race_id, boat_name):
    from core.raceadmin import EntryScope, add_entries
    boat_id = db.execute("SELECT id FROM boats WHERE boat_name = ?", (boat_name,)).fetchone()[0]
    add_entries(db, ro.get_race(race_id), EntryScope(kind="boat", boat_id=int(boat_id)))


class TestWhichPolar:
    """A time on another design's polar is a different answer, so which polar
    was used is always said."""

    def test_a_polar_by_its_own_name(self, app_context):
        from routes.assistant_reads import polar_for
        path, which = polar_for("j109")
        assert path.stem == "J109" and which == "on the J109 polar"

    def test_a_boat_by_its_design(self, app_context):
        from routes.assistant_reads import polar_for
        _boat("Halcyon")
        with ro.get_db() as db:
            db.execute("UPDATE boats SET design = 'J/109' WHERE boat_name = 'Halcyon'")
            db.commit()
        path, which = polar_for("Halcyon")
        assert path.stem == "J109" and "Halcyon's polar" in which

    @pytest.mark.parametrize("design,polar", [("J 122 2.20", "J122"), ("J 70 OD", "J70"),
                                              ("J/109", "J109")])
    def test_a_design_as_the_certificate_writes_it(self, app_context, design, polar):
        from routes.assistant_reads import polar_for
        _boat("Halcyon")
        with ro.get_db() as db:
            db.execute("UPDATE boats SET design = ? WHERE boat_name = 'Halcyon'", (design,))
            db.commit()
        assert polar_for("Halcyon")[0].stem == polar

    def test_a_boat_with_no_polar_of_its_own_says_whose_was_used(self, app_context):
        from routes.assistant_reads import polar_for
        _boat("Mystery")
        path, which = polar_for("Mystery")
        assert "default polar" in which and "Mystery has none of its own" in which

    def test_the_polar_says_the_angles_the_timings_sail(self, api, monkeypatch):
        said = _read(api, monkeypatch, "boat_polar", {"boat": "J109", "tws": 8})
        assert "on the J109 polar" in said and "beats at" in said and "runs at" in said


@pytest.fixture()
def app_context(client):
    with ro.app.app_context():
        yield


class TestTheNextTurnIsRemindedWhatWasLookedUp:
    """Asked for courses "in 20 mins", then about the polar, then to use the
    J70's, it timed them for a hundred minutes: the twenty was only ever an
    argument to a look-up, and nothing kept it."""

    def test_a_look_up_and_its_arguments_travel_with_the_turn(self, api, monkeypatch):
        from core.assistant import ANSWER, Intent
        heard = {}

        def parse(text, context):
            if text == "courses for 20 minutes":
                context.looked_up.append({"tool": "suggest_course",
                                          "arguments": {"target_minutes": 20}})
                return Intent(ANSWER, {"text": "Three options."})
            heard["recent"] = list(context.recent)
            return Intent(ANSWER, {"text": "On the J70."})
        _answers_with(monkeypatch, parse)
        _say(api, "courses for 20 minutes")
        _say(api, "use the j70 polar")
        said, did = heard["recent"][-1]
        assert said.startswith("courses for 20 minutes\n[App's note")
        assert "looked up suggest_course: target_minutes 20]" in said
        # Not on the app's side, where it read as how the app writes and was
        # written out in a reply in place of the look-up.
        assert did == "Three options."

    def test_the_facts_no_longer_plant_a_race_length(self, api, monkeypatch):
        """The facts listed the courses recommended for the current course's own
        time, and the interpreter took that time as the race officer's target."""
        from core.assistant import ANSWER, Intent
        from routes import assistant as routes_assistant
        monkeypatch.setattr(routes_assistant, "_wind_now", lambda: (225.0, 12.0))
        _upcoming_race()
        seen = {}

        def parse(text, context):
            seen["facts"] = " ".join(context.facts)
            return Intent(ANSWER, {"text": "ok"})
        _answers_with(monkeypatch, parse)
        _say(api, "is this the best course?")
        assert "would recommend" not in seen["facts"]
        assert "No race length has been asked for" in seen["facts"]


class TestTheWaitIsNotSilent:
    def test_the_page_can_ask_what_is_happening(self, api, monkeypatch, logged_in_client):
        from core.assistant import ANSWER, Intent

        def parse(text, context):
            context.progress("thinking")
            context.progress("suggest_course")
            return Intent(ANSWER, {"text": "Three options."})
        _answers_with(monkeypatch, parse)
        _say(api, "suggest a course", client_command_id="cid-progress-1")
        body = logged_in_client.get("/admin/api/assistant/progress?id=cid-progress-1").get_json()
        assert body["ok"] and body["steps"][:2] == ["Reading that", "Searching the marks for a course"]
        assert body["elapsed"] >= 0

    def test_nobody_else_sees_it(self, api, monkeypatch):
        from routes import assistant as routes_assistant
        routes_assistant._report_progress("someone-else", "cid-theirs", "thinking")
        assert routes_assistant._progress_for("admin", "cid-theirs") == {}

    def test_it_needs_the_same_permission_as_the_page(self, logged_in_client):
        _grant_on_water(allowed=False)
        resp = logged_in_client.get("/admin/api/assistant/progress?id=anything")
        assert resp.status_code == 403


class TestACourseIsDrawnNotDescribed:
    def test_a_suggestion_comes_with_its_cards(self, api, monkeypatch):
        from core.assistant import ANSWER, Intent

        def parse(text, context):
            context.read("suggest_course", {"target_minutes": 60, "twd": 200, "tws": 12})
            return Intent(ANSWER, {"text": "Three options; I would take the first."})
        _answers_with(monkeypatch, parse)
        _, body = _say(api, "suggest a course for an hour")
        assert body["status"] == "answered" and body["courses"]
        card = body["courses"][0]
        assert card["board"][-1] == {"mark": "O", "rounding": "p"}
        assert all(isinstance(leg["twa"], int) and leg["tack"] in ("P", "S", "")
                   for leg in card["legs"])
        assert card["wind"] == "200°T 12.0 kn"

    def test_the_cards_survive_a_refresh(self, api, monkeypatch, logged_in_client):
        from core.assistant import ANSWER, Intent

        def parse(text, context):
            context.read("time_course", {"marks": "4p Op", "twd": 200, "tws": 12})
            return Intent(ANSWER, {"text": "About that long."})
        _answers_with(monkeypatch, parse)
        _say(api, "how long is 4p Op")
        page = logged_in_client.get("/onwater").get_data(as_text=True)
        assert "data-courses='" in page and '"leg": "O-4"' in page

    def test_a_made_up_course_is_drawn_with_its_read_back(self, api, monkeypatch):
        from core.assistant import Intent
        from routes import assistant as routes_assistant
        monkeypatch.setattr(routes_assistant, "_wind_now", lambda: (200.0, 12.0))
        from routes import assistant_reads
        monkeypatch.setattr(assistant_reads, "_wind", lambda resolved: (200.0, 12.0, "the wind now"))
        _upcoming_race()
        _answers_with(monkeypatch, lambda text, context: Intent(
            "set_custom_course", {"marks": "4 port, O port"}))
        _, body = _say(api, "windward leeward O to 4")
        assert body["status"] == "needs_confirmation"
        assert [m["mark"] for m in body["courses"][0]["board"]] == ["4", "O"]


class TestItKnowsWhenAPIsUp:
    """With AP up since 21:17 the interpreter was told only that the gun was at
    21:21 and that Mojito was racing, and it told the race officer AP was down --
    while the competitor page showed the flag."""

    def _postponed_race(self):
        _boat("Mojito")
        race_id = _upcoming_race()
        with ro.get_db() as db:
            # A gun already past, as it was on the dev box: the race looks under
            # way on every other fact.
            db.execute("UPDATE races SET start_time = '2026-09-24T21:16:00',"
                       " postponed_at = '2026-09-24T21:17:48', postponement_kind = 'AP',"
                       " postponement_ends_at = NULL WHERE id = ?", (race_id,))
            db.commit()
            add_entries_for(db, race_id, "Mojito")
        return race_id

    def test_the_facts_say_the_flag_is_up_and_the_race_has_not_started(self, api, monkeypatch):
        from core.assistant import ANSWER, Intent
        race_id = self._postponed_race()
        seen = {}

        def parse(text, context):
            seen["facts"] = list(context.facts)
            return Intent(ANSWER, {"text": "ok"})
        _answers_with(monkeypatch, parse)
        _say(api, "is AP up?")
        said = " ".join(seen["facts"])
        assert "AP IS UP on this race since 21:17" in said
        assert "has NOT started" in said
        # Before anything else about the race, because it changes what the rest means.
        first = next(i for i, f in enumerate(seen["facts"]) if f.startswith(f"Race #{race_id} has"))
        assert "AP IS UP" in seen["facts"][first - 1]

    def test_the_status_report_says_so(self, api, monkeypatch):
        from core.assistant import Intent
        race_id = self._postponed_race()
        _answers_with(monkeypatch, lambda text, context: Intent("race_status", {"race_id": race_id}))
        _, body = _say(api, "status")
        assert "AP UP since 21:17 -- postponed, not started" in body["message"]

    def test_a_race_with_no_flag_says_nothing_about_one(self, api, monkeypatch):
        from core.assistant import ANSWER, Intent
        _upcoming_race()
        seen = {}

        def parse(text, context):
            seen["facts"] = " ".join(context.facts)
            return Intent(ANSWER, {"text": "ok"})
        _answers_with(monkeypatch, parse)
        _say(api, "is AP up?")
        assert "IS UP" not in seen["facts"]


class TestLoweringAPWhenAskedToLowerIt:
    """At 23:14:30 "AP down" was read back as "AP down at 23:15". The app gives a
    minute and a half's notice before the flag moves, so the Yes pressed straight
    after it was refused -- "the earliest is 23:17" -- and the refusal was never
    recorded: the proposal stayed waiting, its Yes button went, and the
    interpreter spent a minute and a half telling the race officer to press it."""

    @pytest.fixture(autouse=True)
    def no_horn(self, monkeypatch):
        from core import raceadmin
        monkeypatch.setattr(raceadmin, "signal_postponed", lambda *a, **k: None)
        monkeypatch.setattr(raceadmin, "signal_resumed", lambda *a, **k: None)

    def _postponed(self):
        race_id = _upcoming_race()
        with ro.get_db() as db:
            db.execute("UPDATE races SET postponed_at = ?, postponement_kind = 'AP',"
                       " postponement_ends_at = NULL WHERE id = ?",
                       (ro.datetime.now().isoformat(timespec="seconds"), race_id))
            db.commit()
        return race_id

    def _ask_to_lower(self, api, monkeypatch, race_id, **arguments):
        from core.assistant import Intent
        _answers_with(monkeypatch, lambda text, context: Intent(
            "resume_race", {"race_id": race_id, **arguments}))
        return _say(api, "AP down")[1]

    def test_the_read_back_names_a_minute_that_can_be_announced(self, api, monkeypatch):
        from datetime import datetime
        from core.racesignals import LOWER_AP_MIN_LEAD_S
        body = self._ask_to_lower(api, monkeypatch, self._postponed())
        lower_at = datetime.fromisoformat(body["resolved"]["lower_at"])
        assert (lower_at - datetime.now()).total_seconds() >= LOWER_AP_MIN_LEAD_S

    def test_a_yes_straight_away_lowers_it(self, api, monkeypatch):
        race_id = self._postponed()
        body = self._ask_to_lower(api, monkeypatch, race_id)
        _, done = _confirm(api, body["pending_token"])
        assert done["status"] == "done", done
        with ro.get_db() as db:
            row = db.execute("SELECT postponement_ends_at FROM races WHERE id = ?",
                             (race_id,)).fetchone()
        assert row["postponement_ends_at"] == body["resolved"]["lower_at"]

    def test_a_yes_too_late_for_that_minute_takes_the_next_one(self, api, monkeypatch):
        """Nobody named the minute, so it is as soon as possible -- not refused."""
        from datetime import datetime, timedelta
        race_id = self._postponed()
        body = self._ask_to_lower(api, monkeypatch, race_id)
        too_soon = (datetime.now() + timedelta(seconds=20)).isoformat(timespec="seconds")
        with ro.get_db() as db:
            stored = json.loads(db.execute("SELECT resolved_json FROM assistant_commands"
                                           " WHERE token = ?", (body["pending_token"],)).fetchone()[0])
            stored["lower_at"] = too_soon
            db.execute("UPDATE assistant_commands SET resolved_json = ? WHERE token = ?",
                       (json.dumps(stored), body["pending_token"]))
            db.commit()
        _, done = _confirm(api, body["pending_token"])
        assert done["status"] == "done", done

    def test_a_minute_the_race_officer_named_too_soon_is_refused_and_remembered(
            self, api, monkeypatch):
        from datetime import datetime, timedelta
        race_id = self._postponed()
        soon = (datetime.now() + timedelta(seconds=40)).replace(second=0, microsecond=0)
        if soon <= datetime.now():
            soon += timedelta(minutes=1)
        body = self._ask_to_lower(api, monkeypatch, race_id, lower_at=soon.isoformat())
        resp, refused = _confirm(api, body["pending_token"])
        assert resp.status_code == 400 and "too soon" in refused["error"]
        with ro.get_db() as db:
            status = db.execute("SELECT status FROM assistant_commands WHERE token = ?",
                                (body["pending_token"],)).fetchone()[0]
        assert status == "failed", "a refused Yes left the proposal waiting"
        from routes import assistant as routes_assistant
        assert routes_assistant._still_waiting("admin") == {}
        said, did = routes_assistant._recent_exchanges("admin")[-1]
        assert did.startswith("That could not be done: That is too soon")


class TestTheYesButtonStaysWithItsProposal:
    def test_a_question_asked_in_between_does_not_take_it_away(self, api, monkeypatch):
        from core.assistant import ANSWER, Intent
        _upcoming_race()
        _answers_with(monkeypatch, lambda text, context: Intent("set_start_and_course",
                                                                {"course_no": 7}))
        _, proposed = _say(api, "use course 7")
        _answers_with(monkeypatch, lambda text, context: Intent(ANSWER, {"text": "Press Yes."}))
        _, body = _say(api, "is that a good one?")
        assert body["pending_token"] == proposed["pending_token"]
        assert "course 7" in body["pending_readback"]

    def test_with_nothing_waiting_there_is_no_button(self, api, monkeypatch):
        from core.assistant import ANSWER, Intent
        _answers_with(monkeypatch, lambda text, context: Intent(ANSWER, {"text": "Hello."}))
        _, body = _say(api, "hello")
        assert "pending_token" not in body


class TestAMadeUpCourseIsCalledWhatItIs:
    """A made-up course keeps the fixed number underneath it. Told "its course is
    20" beside a description of 5p Op 5p Op, the interpreter timed both, answered
    about the wrong one, and lost what "show me" referred to."""

    def _made_up(self):
        from core.raceadmin import set_custom_course
        race_id = _upcoming_race()
        with ro.get_db() as db:
            set_custom_course(db, ro.get_race(race_id),
                              [{"mark": "5", "rounding": "port"}, {"mark": "O", "rounding": "port"},
                               {"mark": "5", "rounding": "port"}, {"mark": "O", "rounding": "port"}])
        return race_id

    def test_the_interpreter_is_told_the_course_being_sailed(self, api, monkeypatch):
        from core.assistant import ANSWER, Intent
        self._made_up()
        seen = {}

        def parse(text, context):
            seen["label"] = context.current_course_label
            seen["facts"] = " ".join(context.facts)
            return Intent(ANSWER, {"text": "ok"})
        _answers_with(monkeypatch, parse)
        _say(api, "what course are we on?")
        assert seen["label"] == "a made-up course, 5p Op 5p Op"
        assert "course 1," not in seen["facts"]

    def test_and_the_status_report_says_it_too(self, api, monkeypatch):
        from core.assistant import Intent
        race_id = self._made_up()
        _answers_with(monkeypatch, lambda text, context: Intent("race_status", {"race_id": race_id}))
        _, body = _say(api, "status")
        assert "a made-up course, 5p Op 5p Op" in body["message"]
        assert "; course 1;" not in body["message"]

    def test_a_numbered_course_is_still_its_number(self, api, monkeypatch):
        from core.assistant import ANSWER, Intent
        _upcoming_race()
        seen = {}

        def parse(text, context):
            seen["label"] = context.current_course_label
            return Intent(ANSWER, {"text": "ok"})
        _answers_with(monkeypatch, parse)
        _say(api, "what course?")
        assert seen["label"] == "course 1"


class TestTheGrammarDoesNotOverruleAnAnswerTheModelWorkedOut:
    """"Time course 4 on the J70 polar" was timed and answered -- and then the
    built-in grammar, seeing "course 4" in a sentence that was not a question,
    replaced the answer with a proposal to set course 4."""

    def test_an_answer_after_a_look_up_stays_an_answer(self, api, monkeypatch):
        from core.assistant import ANSWER, Intent
        _upcoming_race()

        def parse(text, context):
            context.looked_up.append({"tool": "time_course",
                                      "arguments": {"course_no": 4, "boat": "J70"}})
            return Intent(ANSWER, {"text": "Course 4 on the J70: about 47 minutes."})
        _answers_with(monkeypatch, parse)
        _, body = _say(api, "Time course 4 on the J70 polar")
        assert body["status"] == "answered", body

    def test_and_nothing_is_guessed_from_the_sentence_at_all(self, api, monkeypatch):
        """The grammar is gone from the page. An answer in words is an answer:
        if the model ever chats back to a command, the race officer says it
        again -- which is better than a change nobody asked for."""
        from core.assistant import ANSWER, Intent
        _upcoming_race()
        _answers_with(monkeypatch, lambda text, context: Intent(ANSWER, {"text": "Sure thing."}))
        _, body = _say(api, "use course 4")
        assert body["status"] == "answered"


class TestChoosingANumberedCourseOverAMadeUpOne:
    """"Race #660 updated: course 64", three times, and nothing changed: the
    Course & start save keeps a made-up course on purpose, so a number given to
    it alone was ignored -- and the reply said what had been asked for."""

    def _made_up(self):
        from core.raceadmin import set_custom_course
        race_id = _upcoming_race()
        with ro.get_db() as db:
            set_custom_course(db, ro.get_race(race_id),
                              [{"mark": "5", "rounding": "port"}, {"mark": "O", "rounding": "port"}])
        return race_id

    def test_it_takes_and_the_made_up_course_goes(self, api, monkeypatch):
        from core.assistant import Intent
        race_id = self._made_up()
        _answers_with(monkeypatch, lambda text, context: Intent(
            "set_start_and_course", {"race_id": race_id, "course_no": 4}))
        _, body = _say(api, "use course 4")
        assert "It replaces a made-up course, 5p Op." in body["readback"]
        _, done = _confirm(api, body["pending_token"])
        race = ro.get_race(race_id)
        assert int(race["course_no"]) == 4 and not race["custom_course_json"]
        assert "course 4" in done["message"]


class TestAWindReadingThatIsNotNow:
    def test_an_old_reading_is_said_to_be_old(self, api, monkeypatch):
        """"321° at 0.6 knots" was the wind now, while the wind history said
        nothing had come in for an hour."""
        import time as _time
        from core.assistant import ANSWER, Intent
        from routes import assistant as routes_assistant
        monkeypatch.setattr(routes_assistant, "_wind_now", lambda: (321.0, 0.6))
        monkeypatch.setattr(routes_assistant, "_wind_reading", lambda: {
            "twd": 321.0, "tws": 0.6, "age_s": 3 * 3600.0, "manual": False})
        seen = {}

        def parse(text, context):
            seen["facts"] = " ".join(context.facts)
            return Intent(ANSWER, {"text": "ok"})
        _answers_with(monkeypatch, parse)
        _say(api, "what's the wind?")
        assert "last reading was 3 hours ago" in seen["facts"]
        assert "the wind now is NOT known" in seen["facts"]
        assert "Wind now:" not in seen["facts"]

    def test_a_fresh_reading_is_the_wind_now(self, api, monkeypatch):
        from core.assistant import ANSWER, Intent
        from routes import assistant as routes_assistant
        monkeypatch.setattr(routes_assistant, "_wind_now", lambda: (225.0, 12.0))
        monkeypatch.setattr(routes_assistant, "_wind_reading", lambda: {
            "twd": 225.0, "tws": 12.0, "age_s": 20.0, "manual": False})
        seen = {}

        def parse(text, context):
            seen["facts"] = " ".join(context.facts)
            return Intent(ANSWER, {"text": "ok"})
        _answers_with(monkeypatch, parse)
        _say(api, "what's the wind?")
        assert "Wind now: 225 degrees true at 12.0 knots." in seen["facts"]


class TestTheCardsCanBeReferredTo:
    """Three courses with nothing to call them by: which one is "the second"?"""

    def test_the_next_turn_is_told_which_card_was_which(self, api, monkeypatch):
        from core.assistant import ANSWER, Intent
        heard = {}

        def parse(text, context):
            if text.startswith("recommend"):
                context.read("recommend_courses", {"target_minutes": 60, "twd": 200, "tws": 12})
                return Intent(ANSWER, {"text": "1, 2 or 3?"})
            heard["recent"] = list(context.recent)
            return Intent(ANSWER, {"text": "ok"})
        _answers_with(monkeypatch, parse)
        _, first = _say(api, "recommend a course for an hour")
        assert len(first["courses"]) == 3
        _say(api, "the second one")
        said, did = heard["recent"][-1]
        second = first["courses"][1]["title"]
        assert "cards shown, numbered: 1 Course" in said and f"2 {second} (" in said
        assert "cards shown" not in did

    def test_a_card_on_a_read_back_has_nothing_to_choose(self, api, monkeypatch):
        from core.assistant import Intent
        from routes import assistant_reads
        monkeypatch.setattr(assistant_reads, "_wind", lambda resolved: (200.0, 12.0, "the wind now"))
        _upcoming_race()
        _answers_with(monkeypatch, lambda text, context: Intent(
            "set_custom_course", {"marks": "4 port, O port"}))
        _, body = _say(api, "windward leeward O to 4")
        assert body["courses"][0]["choosable"] is False


def _boat_id(name):
    with ro.get_db() as db:
        return int(db.execute("SELECT id FROM boats WHERE boat_name = ?", (name,)).fetchone()[0])


def _enter(race_id, *names):
    from core.raceadmin import EntryScope, add_entries
    with ro.get_db() as db:
        for name in names:
            add_entries(db, ro.get_race(race_id), EntryScope(kind="boat", boat_id=_boat_id(name)))


class TestANewRaceIsAskedItsSeriesAndName:
    """A race with no series had no boats, and "Add all boats" entered the whole
    boat database -- most of which is not racing. The series is the fleet: a new
    race is asked which it is in, suggesting the last race's, and gets the boats
    already racing in it. And it is asked its name, rather than being called
    "Club Race"."""

    def _last_race_in(self, series_name):
        stamp = "2026-01-01T00:00:00"
        with ro.get_db() as db:
            series_id = int(db.execute(
                "INSERT INTO race_series (name, created_at, updated_at) VALUES (?, ?, ?)",
                (series_name, stamp, stamp)).lastrowid)
            db.commit()
            created = create_race(db, RaceSpec(name="Thursday Evening", series_id=series_id))
            update_race_settings(db, ro.get_race(created.race_id), RaceSettings(
                name="Thursday Evening", course_no=1, start_time="2099-09-03T18:50",
                series_id=series_id))
        _boat("Alpha Test", "GBR 1001")
        _boat("Bravo Test", "GBR 1002")
        _enter(created.race_id, "Alpha Test", "Bravo Test")
        return created.race_id

    def test_it_is_asked_the_series_the_last_race_was_in(self, api):
        self._last_race_in("Zeta Test Series")
        _, asked = _say(api, "create a race at 11am")
        assert asked["status"] == "needs_clarification"
        assert asked["question"].startswith("Which series is the new race in?")
        assert "The last race, 'Thursday Evening'" in asked["question"]
        assert "was in Zeta Test Series" in asked["question"]
        assert asked["options"] == ["Zeta Test Series", "no series"]

    def test_then_its_name_after_the_last_race_in_that_series(self, api):
        self._last_race_in("Zeta Test Series")
        _say(api, "create a race at 11am")
        _, asked = _say(api, "Zeta Test Series")
        assert asked["question"] == ("What is the new race called? The last race in Zeta Test "
                                     "Series was 'Thursday Evening'.")
        assert asked["options"] == ["Thursday Evening"]

    def test_and_the_read_back_names_the_boats_it_will_have(self, api):
        self._last_race_in("Zeta Test Series")
        _say(api, "create a race at 11am")
        _say(api, "Zeta Test Series")
        _, body = _say(api, "Thursday Evening 2")
        assert body["status"] == "needs_confirmation"
        assert "Create 'Thursday Evening 2'" in body["readback"]
        assert ("in series Zeta Test Series, entering its 2 boat(s): Alpha Test, Bravo Test"
                in body["readback"])
        _, done = _confirm(api, body["pending_token"])
        names = sorted(e["boat_name"] for e in ro.get_entries(done["race_id"]))
        assert names == ["Alpha Test", "Bravo Test"]

    def test_no_series_is_an_answer_and_says_the_race_starts_empty(self, api):
        self._last_race_in("Zeta Test Series")
        _say(api, "create a race at 11am")
        _say(api, "none")
        _, body = _say(api, "Pop-up Race")
        assert "in no series, with no boats entered yet" in body["readback"]

    def test_a_series_or_name_in_the_sentence_is_not_asked_for(self, api):
        _, body = _say(api, "create a race called Named Race at 11am in no series")
        assert body["status"] == "needs_confirmation"
        assert "Create 'Named Race'" in body["readback"]

    def test_there_is_no_add_all_boats_button(self, logged_in_client):
        _grant_on_water()
        page = logged_in_client.get("/onwater").get_data(as_text=True)
        assert 'data-say="add all the boats"' not in page


class TestFinishingABoatFromTheWater:
    """The Finish button on the race sheet, from the water: the same call, so the
    same horn, race-log entry and clip -- with the time taken at the yes."""

    @pytest.fixture(autouse=True)
    def a_horn(self, monkeypatch):
        from core import raceadmin
        self.blasts, self.clips = [], []
        monkeypatch.setattr(raceadmin, "fire_horn", lambda ms=None: self.blasts.append(ms) or {
            "ok": True, "mode": "serial", "message": "Horn fired."})
        monkeypatch.setattr(raceadmin, "schedule_video_clip",
                            lambda *a, **k: self.clips.append((a, k)))

    def _racing(self, started_minutes_ago=45):
        from datetime import datetime, timedelta
        warning = datetime.now() - timedelta(minutes=started_minutes_ago + 5)
        with ro.get_db() as db:
            created = create_race(db, RaceSpec(name="Finish Race"))
            update_race_settings(db, ro.get_race(created.race_id), RaceSettings(
                name="Finish Race", course_no=1, start_time=warning.strftime("%Y-%m-%dT%H:%M")))
        _boat("Finisher Test", "GBR 4242")
        _boat("Second Test", "GBR 5151")
        _enter(created.race_id, "Finisher Test", "Second Test")
        return created.race_id

    def _finish(self, api, monkeypatch, race_id, boat):
        from core.assistant import Intent
        _answers_with(monkeypatch, lambda text, context: Intent(
            "finish_boat", {"race_id": race_id, "boat": boat}))
        return _say(api, f"{boat} has finished")[1]

    def _entry(self, race_id, name):
        return next(e for e in ro.get_entries(race_id) if e["boat_name"] == name)

    def test_it_is_read_back_and_nothing_happens_until_yes(self, api, monkeypatch):
        race_id = self._racing()
        body = self._finish(api, monkeypatch, race_id, "finisher test")
        assert body["status"] == "needs_confirmation"
        assert body["readback"].startswith("Finish Finisher Test (GBR 4242) in ")
        assert "sound the horn" in body["readback"] and "the moment you say yes" in body["readback"]
        assert not self.blasts and not self._entry(race_id, "Finisher Test")["finish_time"]

    def test_yes_records_it_now_and_sounds_the_horn(self, api, monkeypatch):
        from datetime import datetime
        race_id = self._racing()
        body = self._finish(api, monkeypatch, race_id, "Finisher Test")
        before = datetime.now().isoformat(timespec="seconds")
        _, done = _confirm(api, body["pending_token"])
        entry = self._entry(race_id, "Finisher Test")
        assert entry["status"] == "FINISHED" and entry["finish_source"] == "manual-now"
        assert entry["finish_time"] >= before, "the time is the yes, not the sentence"
        assert len(self.blasts) == 1 and len(self.clips) == 1
        assert done["message"] == f"Finisher Test (GBR 4242) finished at {entry['finish_time'][11:19]}. Horn sounded."

    def test_by_sail_number(self, api, monkeypatch):
        race_id = self._racing()
        body = self._finish(api, monkeypatch, race_id, "5151")
        assert body["readback"].startswith("Finish Second Test (GBR 5151) in ")

    def test_not_before_the_start(self, api, monkeypatch):
        race_id = self._racing(started_minutes_ago=-30)
        body = self._finish(api, monkeypatch, race_id, "Finisher Test")
        assert body["status"] == "needs_clarification"
        assert "has not started" in body["question"]

    def test_a_boat_not_in_the_race_is_a_question_listing_those_that_are(self, api, monkeypatch):
        race_id = self._racing()
        body = self._finish(api, monkeypatch, race_id, "Nobody At All")
        assert body["status"] == "needs_clarification"
        assert "'Nobody At All' is not entered in Finish Race" in body["question"]
        assert "Finisher Test (GBR 4242)" in body["question"]

    def test_a_second_finish_says_it_replaces_the_first(self, api, monkeypatch):
        race_id = self._racing()
        _confirm(api, self._finish(api, monkeypatch, race_id, "Finisher Test")["pending_token"])
        body = self._finish(api, monkeypatch, race_id, "Finisher Test")
        assert "Finisher Test already has a finish at" in body["readback"]
        assert "this replaces it" in body["readback"]

    def test_a_horn_that_fails_is_said_to_have_failed(self, api, monkeypatch):
        from core import raceadmin
        monkeypatch.setattr(raceadmin, "fire_horn", lambda ms=None: {
            "ok": False, "mode": "error", "message": "Could not fire horn on COM3: gone"})
        race_id = self._racing()
        _, done = _confirm(api, self._finish(api, monkeypatch, race_id, "Finisher Test")["pending_token"])
        assert "the horn did NOT sound: Could not fire horn on COM3: gone" in done["message"]
        assert self._entry(race_id, "Finisher Test")["status"] == "FINISHED"


class TestTheForecastForTheRace:
    """"Will it build during the race?" had nothing to be answered from: the
    club's instrument says what the wind is doing now, and nothing said what it
    would do next."""

    def test_the_look_up_covers_the_race_it_is_asked_about(self, api, monkeypatch):
        from datetime import datetime, timedelta
        from core.assistant import ANSWER, Intent
        from core.forecast import Forecast
        from routes import assistant_reads
        race_id = _upcoming_race()
        gun = ro.race_first_start_dt(ro.get_race(race_id))
        hours = [{"time": (gun + timedelta(hours=h)).strftime("%Y-%m-%dT%H:00"),
                  "twd": 200 + 10 * h, "tws": 10.0 + h, "gust": 15.0 + h} for h in range(-2, 5)]
        monkeypatch.setattr(assistant_reads, "fetch", lambda url: Forecast(
            source="Open-Meteo (UK Met Office model)", url=url, fetched_at=0.0, hours=hours))
        heard = {}

        def parse(text, context):
            heard["found"] = context.read("weather_forecast", {})
            return Intent(ANSWER, {"text": "It builds."})
        _answers_with(monkeypatch, parse)
        _say(api, "will the wind build during the race?")
        found = heard["found"]
        assert f"Evening Race: its first gun at {gun:%H:%M}" in found
        assert "veering" in found and "gusts to" in found

    def test_a_page_that_is_not_data_is_quoted_as_the_pages_own_words(self, api, monkeypatch):
        from core.forecast import Forecast
        from routes import assistant_reads
        monkeypatch.setattr(assistant_reads, "fetch", lambda url: Forecast(
            source="windy.app", url=url, fetched_at=0.0, text="Light wind – 5.9 m/s"))
        found = assistant_reads.weather_forecast({})
        assert "its own words, quoted here as data" in found and "Light wind" in found


class TestWhereTheBoatsAre:
    """"Where's Mojito?" and "how fast are they going?" had nothing behind them
    but the order on the water, while the race sheet drew every boat on a chart."""

    MARKS = {"4": {"lat": 52.87, "lon": -4.39}, "O": {"lat": 52.879, "lon": -4.399}}
    SEQ = [{"code": "4", "lat": 52.87, "lon": -4.39}, {"code": "O", "lat": 52.879, "lon": -4.399}]

    def _board(self, monkeypatch, rows, seen=None):
        from core import track
        def board(race_id, at_ts=None):
            if seen is not None:
                seen.append(at_ts)
            return rows
        monkeypatch.setattr(track, "race_leaderboard", board)
        monkeypatch.setattr(track, "race_marks", lambda race: self.MARKS)
        monkeypatch.setattr(track, "course_rounding_sequence", lambda race: self.SEQ)

    def _row(self, **kw):
        row = {"boat_name": "MOJITO", "sail_no": "GBR 1", "tracked": True, "finished": False,
               "lat": 52.866, "lon": -4.397, "sog": 6.2, "cog": 215.0, "age": 12,
               "rounded": 0, "total": 2, "next_mark": "4", "dist_remaining_nm": 2.4}
        row.update(kw)
        return row

    def test_each_boat_where_it_is_and_how_fast(self, api, monkeypatch):
        from routes import assistant_reads
        race_id = _upcoming_race()
        self._board(monkeypatch, [self._row(), self._row(boat_name="JACKDAW", sail_no="",
                                                         tracked=False)])
        said = assistant_reads.boat_positions({"race_id": race_id})
        # 0.24 nm south and 0.25 nm west of the mark: 0.35 nm, bearing 047° to it.
        assert "MOJITO (GBR 1): 6.2 kn on 215°, 0.3 nm SW of mark 4 (52°51.96'N 4°23.82'W)" in said
        assert "fix 12 s old; 0 of 2 marks rounded, 0.35 nm to mark 4, bearing 047°" in said
        assert "2.4 nm still to sail" in said
        assert "JACKDAW: no tracker, so not known" in said

    def test_one_boat_by_sail_number(self, api, monkeypatch):
        from routes import assistant_reads
        race_id = _upcoming_race()
        self._board(monkeypatch, [self._row(), self._row(boat_name="JACKDAW", sail_no="GBR 77")])
        said = assistant_reads.boat_positions({"race_id": race_id, "boat": "77"})
        assert "JACKDAW (GBR 77)" in said and "MOJITO" not in said

    def test_the_fleet_as_it_stood_at_a_time(self, api, monkeypatch):
        from datetime import datetime
        from routes import assistant_reads
        race_id = _upcoming_race()
        seen = []
        self._board(monkeypatch, [self._row()], seen)
        said = assistant_reads.boat_positions({"race_id": race_id, "at_time": "2026-08-16T12:00"})
        assert seen == [datetime(2026, 8, 16, 12, 0).timestamp()]
        assert "at 12:00 on Sun 16 Aug" in said

    def test_a_boats_track_over_a_period(self, api, monkeypatch):
        from datetime import datetime
        from core import track
        from routes import assistant_reads
        race_id = _upcoming_race()
        _boat("Tracked Test", "GBR 9")
        _enter(race_id, "Tracked Test")
        t0 = datetime(2026, 8, 16, 12, 0).timestamp()
        # Two fixes a mile apart, ten minutes apart: six knots.
        fixes = [{"lat": 52.87, "lon": -4.39, "t": t0, "speed_kn": 5.0, "course_deg": 0.0},
                 {"lat": 52.87 + 1 / 60, "lon": -4.39, "t": t0 + 600, "speed_kn": 7.0,
                  "course_deg": 0.0}]
        monkeypatch.setattr(track, "positions_for_entry_since", lambda entry, a, b: fixes)
        monkeypatch.setattr(track, "race_marks", lambda race: self.MARKS)
        said = assistant_reads.boat_positions({"race_id": race_id, "boat": "tracked test",
                                               "from_time": "2026-08-16T12:00",
                                               "to_time": "2026-08-16T12:10"})
        assert said.startswith("Tracked Test's track from 12:00:00 to 12:10:00 (2 fixes): "
                               "1.00 nm sailed, averaging 6.0 kn, fastest 7.0 kn")
        assert "12:00:00  at mark 4, 5.0 kn on 000°" in said

    def test_a_finished_boat_is_its_race_gun_to_finish(self, api, monkeypatch):
        """"How fast did Mojito go?" is the race, not the milling about before
        the start or the drift home after it."""
        from core import track
        from routes import assistant_reads
        race_id = _upcoming_race()
        _boat("Done Test", "GBR 8")
        _enter(race_id, "Done Test")
        with ro.get_db() as db:
            db.execute("UPDATE entries SET finish_time = '2099-08-12T20:10:00', status = 'FINISHED'"
                       " WHERE race_id = ? AND boat_name = 'Done Test'", (race_id,))
            db.commit()
        asked = {}

        def fixes(entry, since, until):
            asked["window"] = (since, until)
            return [{"lat": 52.87, "lon": -4.39, "t": since, "speed_kn": 5.0, "course_deg": 0.0},
                    {"lat": 52.88, "lon": -4.39, "t": until, "speed_kn": 6.0, "course_deg": 0.0}]
        monkeypatch.setattr(track, "positions_for_entry_since", fixes)
        monkeypatch.setattr(track, "race_marks", lambda race: self.MARKS)
        said = assistant_reads.boat_positions({"race_id": race_id, "boat": "done test"})
        gun = ro.race_first_start_dt(ro.get_race(race_id))
        assert asked["window"][0] == gun.timestamp()
        assert "It finished at 20:10:00." in said


class TestTheStripFollowsTheRaceSheet:
    """A course changed on the race sheet left this page showing the old course,
    board and chart, while every other page followed it."""

    def test_a_course_changed_elsewhere_is_in_the_next_strip(self, api, logged_in_client):
        race_id = _upcoming_race()
        _say(api, "status")                    # the conversation is about this race
        before = logged_in_client.get("/admin/api/assistant/header").get_json()["header"]
        assert before["race_id"] == race_id and before["course_text"].startswith("Course 1")
        with ro.get_db() as db:
            update_race_settings(db, ro.get_race(race_id), RaceSettings(
                name="Evening Race", course_no=4, start_time="2099-08-12T18:55"))
        resp = logged_in_client.get("/admin/api/assistant/header")
        after = resp.get_json()["header"]
        assert after["course_text"].startswith("Course 4")
        assert after["course_board"] != before["course_board"]
        assert resp.headers["Cache-Control"] == "no-store"

    def test_it_needs_the_same_permission_as_the_page(self, logged_in_client):
        _grant_on_water(allowed=False)
        assert logged_in_client.get("/admin/api/assistant/header").status_code == 403
