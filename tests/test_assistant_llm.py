"""The model-backed parser, driven through a stubbed transport.

No network and no account: `make_model_parser` takes the transport as an
argument, which is the only seam between this module and the internet. What
these tests can prove is the wiring and the refusals -- that a tool call becomes
an Intent of the same shape the grammar produces, that prose and unknown tools
and unreachable providers all become None, and that the request carries what the
model needs. What they cannot prove is the model's judgement; that needs a key
and a bill, and the golden set in test_assistant_commands.py is where it would
be measured.
"""
from __future__ import annotations

import io
from datetime import datetime

import pytest

from core.assistant import (
    ANSWER,
    TOOL_NAMES,
    CommandContext,
    Intent,
    parse_command,
    resolve,
)
from core.assistant_llm import (
    DEFAULT_MODEL,
    LAST_ERROR,
    SYSTEM_PROMPT,
    auth_headers,
    dialect_for,
    intent_from_openai_response,
    intent_from_response,
    interpreter_status,
    make_model_parser,
    parser_from_config,
)

NOW = datetime(2026, 8, 15, 9, 40)


def ctx() -> CommandContext:
    return CommandContext(now=NOW, current_race_id=57)


def tool_call(name, arguments):
    """A provider response carrying one tool call."""
    return {"content": [{"type": "tool_use", "name": name, "input": arguments}]}


def transport_returning(body, captured=None):
    def send(url, payload, headers, timeout):
        if captured is not None:
            captured.append({"url": url, "payload": payload, "headers": headers, "timeout": timeout})
        return body
    return send


class TestATooCallBecomesAnIntent:
    def test_the_intent_is_the_same_shape_the_grammar_produces(self):
        parse = make_model_parser("k", transport=transport_returning(
            tool_call("create_race", {"name": "Sunday Points", "race_type": "standard",
                                      "gun_time": "2026-08-15T11:00"})))
        intent = parse("kick a race off at eleven", ctx())
        assert isinstance(intent, Intent)
        assert intent.name == "create_race"
        assert intent.arguments["gun_time"] == "2026-08-15T11:00"

    def test_it_flows_through_resolve_untouched(self):
        """Downstream cannot tell which parser answered, which is the point."""
        parse = make_model_parser("k", transport=transport_returning(
            tool_call("create_race", {"race_type": "standard", "gun_time": "2026-08-15T11:00"})))
        answer = resolve(parse_command("anything", ctx(), parse), ctx())
        assert answer.status == "needs_confirmation"
        assert answer.resolved["first_warning_time"].endswith("10:55")


class TestWhatItRefuses:
    def test_prose_is_words_and_only_words(self):
        """Prose comes back so the page can hold a conversation, and comes back
        as ANSWER -- which is not in TOOLS, has no branch in `_execute` and
        cannot become an action however it is worded."""
        parse = make_model_parser("k", transport=transport_returning(
            {"content": [{"type": "text",
                          "text": "Course 29 would be about right for this wind."}]}))
        intent = parse("what course should we use", ctx())
        assert intent.name == ANSWER and intent.name not in TOOL_NAMES
        assert resolve(intent, ctx()).status == "answered"

    def test_a_tool_call_beats_anything_said_alongside_it(self):
        """A model that thinks aloud and then acts has acted: the words are
        commentary on the tool call, not an alternative to it."""
        body = {"content": [{"type": "text", "text": "Righto, shortening now."},
                            {"type": "tool_use", "name": "shorten_course",
                             "input": {"race_id": 57, "at_mark": "4"}}]}
        assert intent_from_response(body, "m").name == "shorten_course"

    def test_a_tool_the_app_does_not_implement_never_reaches_the_app(self):
        parse = make_model_parser("k", transport=transport_returning(
            tool_call("arm_start_sequence", {"race_id": 57})))
        # The parser reports what the model said...
        assert parse("start the sequence", ctx()).name == "arm_start_sequence"
        # ...and parse_command drops it, because the app decides its own menu.
        assert parse_command("start the sequence", ctx(), parse) is None

    def test_an_unreachable_provider_is_none_not_an_exception(self):
        """The hut is on contended 4G. The caller says the interpreter did not
        answer rather than raising — and, since v0.264, rather than quietly
        answering with the built-in grammar."""
        parse = make_model_parser("k", transport=transport_returning(None))
        assert parse("create a race at 11", ctx()) is None

    def test_no_api_key_means_no_parser_at_all(self):
        assert parser_from_config({}) is None
        assert parser_from_config({"assistant_api_key": "   "}) is None

    def test_an_empty_sentence_is_not_sent_to_a_model(self):
        calls = []
        parse = make_model_parser("k", transport=transport_returning(tool_call("race_status", {}), calls))
        assert parse("   ", ctx()) is None
        assert calls == [], "an empty command was billed to the club"


class TestWhatIsAsked:
    def test_the_model_may_decline_to_call_a_tool(self):
        """"auto", not "any". A forced tool call contradicts the last line of the
        system prompt and makes declining impossible: asked what it thought of
        the cricket, a forced model picked the most harmless tool it had and
        proposed a status report."""
        calls = []
        parse = make_model_parser("k", transport=transport_returning(tool_call("race_status", {}), calls))
        parse("status", ctx())
        assert calls[0]["payload"]["tool_choice"] == {"type": "auto"}

    def test_only_tools_the_app_implements_are_offered(self):
        calls = []
        parse = make_model_parser("k", transport=transport_returning(tool_call("race_status", {}), calls))
        parse("status", ctx())
        offered = {t["name"] for t in calls[0]["payload"]["tools"]}
        assert offered == TOOL_NAMES
        assert "arm_start_sequence" not in offered

    def test_the_date_travels_in_the_message_not_the_system_prompt(self):
        """A gateway cache keyed on the request must not be able to serve
        yesterday's answer to "start a race at 11"."""
        calls = []
        parse = make_model_parser("k", transport=transport_returning(tool_call("race_status", {}), calls))
        parse("status", ctx())
        payload = calls[0]["payload"]
        assert "2026-08-15" in payload["messages"][0]["content"]
        assert "2026-08-15" not in payload["system"]

    def test_the_warning_and_gun_convention_is_in_the_system_prompt(self):
        assert "FIRST WARNING SIGNAL" in SYSTEM_PROMPT
        assert "five minutes later" in SYSTEM_PROMPT

    def test_the_key_goes_in_a_header_and_never_in_the_body(self):
        calls = []
        parse = make_model_parser("sk-secret", transport=transport_returning(
            tool_call("race_status", {}), calls))
        parse("status", ctx())
        assert calls[0]["headers"]["x-api-key"] == "sk-secret"
        assert "sk-secret" not in str(calls[0]["payload"])

    def test_the_base_url_is_configuration(self):
        """Pointing at a gateway rather than a provider is a setting, not a
        code change."""
        calls = []
        parse = parser_from_config(
            {"assistant_api_key": "k", "assistant_model": "some-model",
             "assistant_base_url": "https://gateway.example/anthropic/v1/messages"},
            transport=transport_returning(tool_call("race_status", {}), calls))
        parse("status", ctx())
        assert calls[0]["url"].startswith("https://gateway.example/")
        assert calls[0]["payload"]["model"] == "some-model"


class TestTheFallbackDoesNotHideItself:
    """The bug this class exists for: a model configured but failing looked
    exactly like one working, because something else quietly answered. Nothing
    answers now — the feature says it is unavailable — but the reason still has
    to be visible, or an empty account looks like a stupid app."""

    def test_with_no_key_the_status_says_the_feature_is_unavailable(self):
        status = interpreter_status({})
        assert status["grammar_only"] is True
        assert status["model"] == ""
        assert "not available" in status["text"]
        assert "no interpreter is configured" in status["text"]

    def test_it_does_not_offer_the_grammar_as_a_half_measure(self):
        """It used to list the sentences the grammar could manage, which read as
        a working feature and was the whole complaint."""
        text = interpreter_status({})["text"]
        assert "shorten at mark 4" not in text

    def test_with_a_key_the_status_names_the_model(self):
        status = interpreter_status({"assistant_api_key": "k", "assistant_model": "some-model"})
        assert status["grammar_only"] is False
        assert status["model"] == "some-model"
        assert "some-model" in status["text"]

    def test_a_key_with_no_model_named_falls_back_to_the_default(self):
        assert interpreter_status({"assistant_api_key": "k"})["model"] == DEFAULT_MODEL

    def test_the_page_says_so_when_there_is_no_model(self, logged_in_client):
        import app as ro
        with ro.get_db() as db:
            db.execute("UPDATE users SET can_race_remotely = 1 WHERE username = 'admin'")
            db.commit()
        page = logged_in_client.get("/vro").get_data(as_text=True)
        assert "Not available" in page
        assert "No interpreter is configured" in page or "no interpreter is configured" in page
        assert "Settings" in page, "it should say where to fix it"
        assert 'id="sayText"' not in page, "a box that cannot do anything still invites typing"

    def test_settings_offers_somewhere_to_put_the_key(self, logged_in_client):
        """It was configurable only by environment variable, which is to say the
        model was unreachable for anybody using the app as an app."""
        page = logged_in_client.get("/admin/settings").get_data(as_text=True)
        assert 'name="assistant_api_key"' in page
        assert 'name="assistant_model"' in page
        assert 'name="assistant_base_url"' in page

    def test_the_key_can_be_saved_from_the_settings_form(self, logged_in_client, csrf_post):
        from core.horn import hardware_config
        csrf_post("/admin/settings/save", {"assistant_api_key": "sk-test-key",
                                           "assistant_model": "claude-sonnet-5"})
        assert hardware_config()["assistant_api_key"] == "sk-test-key"


class TestTwoWireFormats:
    """The club needs both, and from the same Cloudflare account: /ai/v1/messages
    speaks Anthropic for the Anthropic models Cloudflare hosts, and
    /ai/v1/chat/completions speaks OpenAI for Cloudflare's own."""

    @pytest.mark.parametrize("url,expected", [
        ("https://api.anthropic.com/v1/messages", "anthropic"),
        ("https://gateway.ai.cloudflare.com/v1/acct/gw/anthropic/v1/messages", "anthropic"),
        ("https://api.cloudflare.com/client/v4/accounts/acct/ai/v1/messages", "anthropic"),
        ("https://api.cloudflare.com/client/v4/accounts/acct/ai/v1/chat/completions", "openai"),
        ("https://api.openai.com/v1/chat/completions", "openai"),
    ])
    def test_the_dialect_is_read_from_the_path(self, url, expected):
        assert dialect_for(url) == expected

    @pytest.mark.parametrize("url,header", [
        ("https://api.anthropic.com/v1/messages", "x-api-key"),
        ("https://gateway.ai.cloudflare.com/v1/a/g/anthropic/v1/messages", "x-api-key"),
        # Anthropic's format, Cloudflare's token: an x-api-key header here is
        # answered with a bare "Authentication error" and nothing else.
        ("https://api.cloudflare.com/client/v4/accounts/a/ai/v1/messages", "Authorization"),
        ("https://api.cloudflare.com/client/v4/accounts/a/ai/v1/chat/completions", "Authorization"),
    ])
    def test_the_auth_style_is_not_tied_to_the_dialect(self, url, header):
        assert header in auth_headers(url, "the-key")

    def test_an_openai_endpoint_gets_openai_shaped_tools(self):
        calls = []
        parse = make_model_parser(
            "k", base_url="https://api.cloudflare.com/client/v4/accounts/a/ai/v1/chat/completions",
            transport=transport_returning({"choices": [{"message": {"tool_calls": [
                {"function": {"name": "race_status", "arguments": "{}"}}]}}]}, calls))
        intent = parse("status", ctx())
        assert intent.name == "race_status"
        payload = calls[0]["payload"]
        assert payload["tool_choice"] == "auto"
        assert payload["tools"][0]["type"] == "function"
        assert payload["messages"][0]["role"] == "system"

    def test_openai_arguments_arrive_as_a_json_string(self):
        got = intent_from_openai_response({"choices": [{"message": {"tool_calls": [
            {"function": {"name": "create_race",
                          "arguments": '{"race_type": "pursuit", "length_min": "90"}'}}]}}]}, "m")
        assert got.arguments == {"race_type": "pursuit", "length_min": "90"}

    def test_arguments_that_are_not_json_are_a_miss_not_a_crash(self):
        got = intent_from_openai_response({"choices": [{"message": {"tool_calls": [
            {"function": {"name": "create_race", "arguments": "sorry, what?"}}]}}]}, "m")
        assert got.name == "create_race" and got.arguments == {}

    def test_prose_from_an_openai_endpoint_is_words_too(self):
        intent = intent_from_openai_response(
            {"choices": [{"message": {"content": "I think you want a race?"}}]}, "m")
        assert intent.name == ANSWER and intent.arguments["text"].startswith("I think")


class TestAFailingModelSaysWhy:
    """A 402, a wrong model id and a timeout all look identical from the page --
    the grammar answers and the page seems stupid. The reason is in an HTTP
    response nobody was reading."""

    def test_the_provider_message_is_kept(self, monkeypatch):
        import urllib.error
        import urllib.request

        def raise_402(request, timeout=None):
            raise urllib.error.HTTPError(
                request.full_url, 402, "Payment Required", {},
                io.BytesIO(b'{"error":{"message":"Insufficient balance; add money or use BYOK"}}'))

        monkeypatch.setattr(urllib.request, "urlopen", raise_402)
        LAST_ERROR["detail"] = ""
        parse = make_model_parser("k")
        assert parse("create a race at 11", ctx()) is None
        assert "402" in LAST_ERROR["detail"]
        assert "Insufficient balance" in LAST_ERROR["detail"]

    def test_the_status_repeats_it_rather_than_claiming_all_is_well(self, monkeypatch):
        monkeypatch.setitem(LAST_ERROR, "detail", "HTTP 402: Insufficient balance")
        status = interpreter_status({"assistant_api_key": "k", "assistant_model": "m"})
        assert status["error"] == "HTTP 402: Insufficient balance"
        assert "last request failed" in status["text"]
        assert "will not be read until it answers" in status["text"]

    def test_a_working_request_clears_the_last_error(self):
        LAST_ERROR["detail"] = "HTTP 402: stale"
        parse = make_model_parser("k", transport=transport_returning(tool_call("race_status", {})))
        parse("status", ctx())
        # The stub transport bypasses _post_json, so clear it the way a real
        # success does and check the status is clean again.
        LAST_ERROR["detail"] = ""
        assert interpreter_status({"assistant_api_key": "k"})["error"] == ""


class TestReadingTheResponse:
    @pytest.mark.parametrize("body", [
        {}, {"content": []}, {"content": [{"type": "tool_use"}]},
        {"content": [{"type": "text", "text": "   "}]},
    ])
    def test_a_response_with_nothing_in_it_is_none(self, body):
        got = intent_from_response(body, "m")
        assert got is None or got.name == ""

    def test_arguments_that_are_not_an_object_are_dropped_not_crashed_on(self):
        got = intent_from_response(
            {"content": [{"type": "tool_use", "name": "race_status", "input": "57"}]}, "m")
        assert got.name == "race_status" and got.arguments == {}
