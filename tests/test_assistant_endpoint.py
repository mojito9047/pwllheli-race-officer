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


def _confirm(api, token: str, **body):
    return api("/admin/api/assistant/command/confirm", {"pending_token": token, **body})


def _races():
    with ro.get_db() as db:
        return db.execute("SELECT COUNT(*) FROM races").fetchone()[0]


class TestInterpretingChangesNothing:
    def test_a_command_is_read_back_and_not_carried_out(self, api):
        before = _races()
        resp, body = _say(api, "create a race called Sunday Points at 11am")
        assert resp.status_code == 200
        assert body["status"] == "needs_confirmation"
        assert body["pending_token"].startswith("pc_")
        assert _races() == before, "interpreting a command created a race"

    def test_the_readback_shows_the_warning_and_the_gun(self, api):
        _, body = _say(api, "create a race at 11am")
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
        _, body = _say(api, "create a race at 11, an hour long")
        assert body["status"] == "needs_clarification"
        assert set(body["options"]) == {"standard", "pursuit"}


class TestConfirmingActs:
    def test_a_confirmed_create_makes_the_race(self, api):
        before = _races()
        _, body = _say(api, "create a race called Sunday Points at 11am")
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
        _, body = _say(api, "create a race at 11am")
        _confirm(api, body["pending_token"])
        assert seen and seen[0].name in ("", "Club Race")

    def test_declining_carries_nothing_out(self, api):
        before = _races()
        _, body = _say(api, "create a race at 11am")
        _, done = _confirm(api, body["pending_token"], confirm=False)
        assert done["status"] == "dismissed"
        assert _races() == before

    def test_a_token_that_was_never_issued_is_refused(self, api):
        resp, body = _confirm(api, "pc_madeup")
        assert resp.status_code == 404 and body["ok"] is False


class TestSurvivingABadConnection:
    def test_confirming_twice_acts_once(self, api):
        before = _races()
        _, body = _say(api, "create a race at 11am")
        _, first = _confirm(api, body["pending_token"])
        _, second = _confirm(api, body["pending_token"])
        assert first["race_id"] == second["race_id"]
        assert second.get("repeat") is True
        assert _races() == before + 1, "the retry created a second race"

    def test_the_same_command_id_twice_proposes_once(self, api):
        _, first = _say(api, "create a race at 11am", client_command_id="abc-123")
        _, second = _say(api, "create a race at 11am", client_command_id="abc-123")
        assert first["pending_token"] == second["pending_token"]
        assert second.get("repeat") is True

    def test_a_stale_command_is_not_carried_out(self, api):
        _, body = _say(api, "create a race at 11am")
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
        _, created = _say(api, "create a race at 11am")
        _confirm(api, created["pending_token"])
        _, body = _say(api, "shorten at mark ZZ")
        assert body["status"] == "needs_clarification"
        assert "not on that course" in body["question"]

    def test_a_shortening_is_confirmed_before_the_horn_sounds(self, api, monkeypatch):
        fired = []
        from core import raceadmin
        monkeypatch.setattr(raceadmin, "signal_shortened_course",
                            lambda *a, **k: fired.append(a))
        _, created = _say(api, "create a race at 11am")
        _confirm(api, created["pending_token"])
        _, body = _say(api, "shorten at mark 1")
        assert body["status"] == "needs_confirmation"
        assert "horn" in body["readback"].lower()
        assert fired == [], "the horn sounded before anyone confirmed"

    def test_asking_for_status_needs_no_confirmation(self, api):
        """There is nothing to undo, and asking someone to confirm a question
        they just asked is noise."""
        _, created = _say(api, "create a race at 11am")
        _confirm(api, created["pending_token"])
        _, body = _say(api, "status")
        assert body["status"] == "done"
        assert "entered" in body["message"]


class TestItIsAConversation:
    """Reported from the water, and the whole of it: the app asked which kind of
    race it was, was told "standard", and answered "I did not understand that".
    A question whose answer goes nowhere is worse than no question at all."""

    def test_the_answer_to_the_apps_own_question_is_understood(self, api):
        _, asked = _say(api, "create a race at 11, an hour long")
        assert asked["status"] == "needs_clarification"
        _, body = _say(api, "standard")
        assert body["status"] == "needs_confirmation", \
            "the app could not hear the answer to its own question"
        # And everything already established survived the round trip.
        assert "11:00" in body["readback"] and "10:55" in body["readback"]
        assert body["resolved"]["race_type"] == "standard"

    def test_the_answer_can_be_a_whole_short_sentence(self, api):
        _say(api, "create a race at 11, an hour long")
        _, body = _say(api, "its a standard race")
        assert body["status"] == "needs_confirmation"

    def test_and_the_race_it_makes_is_the_one_that_was_asked_for(self, api):
        _say(api, "create a race at 11, an hour long")
        _, body = _say(api, "pursuit")
        _, done = _confirm(api, body["pending_token"])
        race = ro.get_race(done["race_id"])
        assert race["race_type"] == "pursuit"
        assert str(race["start_time"]).endswith("10:55:00")

    def test_a_question_is_only_answered_once(self, api):
        """The second "standard" is a bare word with nothing left to answer, and
        must not quietly propose a second race."""
        _say(api, "create a race at 11, an hour long")
        _say(api, "standard")
        _, again = _say(api, "standard")
        assert again["status"] != "needs_confirmation"

    def test_changing_the_subject_mid_question_does_what_it_looks_like(self, api):
        _say(api, "create a race at 11, an hour long")
        _, body = _say(api, "status")
        assert body["status"] in ("done", "needs_clarification")
        assert body.get("intent") != "create_race"

    def test_the_question_and_its_answer_are_both_in_the_thread(self, api, monkeypatch):
        """What the interpreter is told next time. Recording only the turns that
        changed something is what left "standard" with no antecedent."""
        from routes import assistant as routes_assistant
        seen = {}

        def remembering(config, transport=None):
            def parse(text, context):
                seen["recent"] = list(context.recent)
                return None
            return parse

        monkeypatch.setattr(routes_assistant, "parser_from_config", remembering)
        _say(api, "create a race at 11, an hour long")
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
        _, created = _say(api, "create a race at 11am")
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
        _, created = _say(api, "new race in the summer series at 11")
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
        _say(api, "create a race at 11am")
        _, body = _say(api, "yes lets do that")
        assert body["status"] == "done"
        assert _races() == before + 1

    @pytest.mark.parametrize("said", ["yes", "Yes please", "ok", "go ahead", "do it"])
    def test_the_ways_people_say_it(self, api, said):
        before = _races()
        _say(api, "create a race at 11am")
        _, body = _say(api, said)
        assert body["status"] == "done", f"{said!r} did not agree to anything"
        assert _races() == before + 1

    @pytest.mark.parametrize("said", ["no", "no thanks", "cancel"])
    def test_and_the_ways_they_decline(self, api, said):
        before = _races()
        _say(api, "create a race at 11am")
        _, body = _say(api, said)
        assert body["status"] == "dismissed"
        assert _races() == before

    def test_a_yes_that_carries_a_change_is_not_a_yes(self, api):
        """"yes but make it half past" changes the proposal. Carrying out the
        old one because it started with "yes" is the whole failure again."""
        before = _races()
        _say(api, "create a race at 11am")
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
        _, first = _say(api, "create a race called One at 11am")
        _, second = _say(api, "create a race called Two at 2pm")
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

        _say(api, "create a race at 11am")
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
        _, body = _say(api, "new race in the wednesday points")
        _, done = _confirm(api, body["pending_token"])
        assert ro.get_race(done["race_id"])["series_id"] == series_id

    def test_a_series_the_club_does_not_have_is_a_question_not_a_guess(self, api, monkeypatch):
        """Quietly creating it outside the points would be a scoring error
        nobody sees until the standings come out wrong."""
        self._a_series()
        from routes import assistant as routes_assistant
        monkeypatch.setattr(routes_assistant, "parser_from_config", self._named("Autumn Series"))
        before = _races()
        _, body = _say(api, "new race in the autumn series")
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
        _, created = _say(api, "create a race at 11am")
        _confirm(api, created["pending_token"])
        assert any("boats entered" in fact for fact in self._facts_from(api, monkeypatch))

    def _blowing(self, monkeypatch, twd=225.0, tws=12.0):
        """A steady south-westerly. The club's instrument is not in the sandbox,
        and what is being tested is what the app makes of a wind, not whether it
        can read one."""
        from routes import assistant as routes_assistant
        monkeypatch.setattr(routes_assistant, "_wind_now", lambda: (twd, tws))

    def _with_a_course(self, api):
        _, created = _say(api, "create a race at 11am")
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

    def test_an_unreachable_model_leaves_the_grammar_answering(self, api, monkeypatch):
        """The whole point of keeping the grammar: the hut's uplink is the part of
        this chain most likely to be the problem."""
        from routes import assistant as routes_assistant
        monkeypatch.setattr(routes_assistant, "parser_from_config",
                            lambda config, transport=None: (lambda text, context: None))
        _, body = _say(api, "create a race at 11am")
        assert body["status"] == "needs_confirmation"
        assert "11:00" in body["readback"]


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
        _, created = _say(api, "create a race at 11am")
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
        _, created = _say(api, "create a race at 11am")
        assert "GPS finishes armed" in created["readback"]
        _, done = _confirm(api, created["pending_token"])
        race = ro.get_race(done["race_id"])
        assert race["gps_finish_enabled"] == 1 and race["gps_auto_confirm"] == 1
        assert "GPS finishes are armed" in done["message"]

    def test_a_command_that_moves_the_start_moves_the_countdown(self, api):
        """A header still counting to the old gun is worse than no header."""
        _, created = _say(api, "create a race at 11am")
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
        _, created = _say(api, "create a race at 11am")
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
        _, created = _say(api, "create a race at 11am")
        _, done = _confirm(api, created["pending_token"])
        _, added = _say(api, "add all the boats")
        _confirm(api, added["pending_token"])
        assert "Mojito" in self._facts(api, monkeypatch, "who has entered")

    def test_one_named_boat_can_be_entered(self, api, monkeypatch):
        self._a_boat()
        _, created = _say(api, "create a race at 11am")
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
        _, created = _say(api, "create a race at 11am")
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
        _, first = _say(api, "create a race at 11am")
        _, done_first = _confirm(api, first["pending_token"])
        _, filled = _say(api, "add all the boats")
        _confirm(api, filled["pending_token"])
        _, second = _say(api, "create a race at 2pm")
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
        _, created = _say(api, "create a race at 11am")
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
        _, created = _say(api, "create a race at 11am")
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
        _, created = _say(api, "create a race called Night Race at 11am")
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
        _say(api, "create a race called Reload Test at 11am")
        _say(api, "status")
        page = logged_in_client.get("/onwater").get_data(as_text=True)
        assert "create a race called Reload Test at 11am" in page
        assert "Reload Test" in page

    def test_and_a_proposal_can_still_be_agreed_to(self, api, logged_in_client):
        """Otherwise the command sits on the server with nothing on screen able
        to answer it."""
        before = _races()
        _, body = _say(api, "create a race at 11am")
        page = logged_in_client.get("/onwater").get_data(as_text=True)
        assert body["pending_token"] in page
        _, done = _confirm(api, body["pending_token"])
        assert done["status"] == "done" and _races() == before + 1

    def test_a_page_with_nothing_behind_it_is_just_the_greeting(self, logged_in_client):
        _grant_on_water()
        page = logged_in_client.get("/onwater").get_data(as_text=True)
        assert "Ready — nothing happens until you say yes" in page
        assert "ow-you" not in page


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
        _, created = _say(api, "create a race at 11am")
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
        _, created = _say(api, "create a race at 11am")
        _, done = _confirm(api, created["pending_token"])
        _, body = self._course(api, monkeypatch, "O port, 4 starboard, O port", done["race_id"])
        assert body["status"] == "needs_confirmation"
        assert "4s" in body["readback"]
        assert ro.get_race(done["race_id"])["custom_course_json"] is None
        assert _races() == before + 1

    def test_a_mark_the_club_does_not_have_is_refused_with_the_ones_it_does(
            self, api, monkeypatch):
        _, created = _say(api, "create a race at 11am")
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
        _, created = _say(api, "create a race at 11am")
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
        resp, body = _say(api, "create a race at 11am")
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
        _, body = _say(api, "create a race at 11am")
        _confirm(api, body["pending_token"])
        assert "assistant command proposed" in lines
        assert "assistant command confirmed" in lines


class TestAShortenedCourseIsShownShortened:
    """The board is what a race officer reads out. It was showing all thirteen
    marks of course 3, and 10.3 nm, beside the words "shortened at 4"."""

    def test_the_board_and_the_length_are_of_the_course_being_sailed(self, api):
        from core.courses import apply_course_shortening, course_for_race
        from routes import assistant as routes_assistant
        _, created = _say(api, "create a race at 11am")
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
        _, created = _say(api, "create a race at 11am")
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
        _, created = _say(api, "create a race at 11am")
        _confirm(api, created["pending_token"])
        _say(api, "status")
        assert called == [], "the finished race's fleet positions were computed anyway"
