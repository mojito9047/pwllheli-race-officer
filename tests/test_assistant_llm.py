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


def text_of(value) -> str:
    """The words in a message or a system prompt, whether it was sent as a string
    or as a list of blocks -- which it is when it carries a cache marker."""
    if isinstance(value, str):
        return value
    return " ".join(str(block.get("text") or "") for block in value or [])


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
        assert "2026-08-15" in text_of(payload["messages"][0]["content"])
        assert "2026-08-15" not in text_of(payload["system"])

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


def transport_replying(*bodies, captured=None):
    """A provider that answers each request with the next reply in turn."""
    import copy
    replies = list(bodies)

    def send(url, payload, headers, timeout):
        if captured is not None:
            # A copy: each request is built from the last, and the test wants to
            # see each one as it was sent.
            captured.append(copy.deepcopy(payload))
        return replies.pop(0) if replies else None
    return send


def reading(answers=None, seen=None):
    """A context whose look-ups answer from `answers` and are recorded in `seen`."""
    context = ctx()

    def read(name, arguments):
        if seen is not None:
            seen.append((name, arguments))
        return (answers or {}).get(name, f"{name} result")
    context.read = read
    return context


def uses(*calls, text=None, thinking=False):
    """A provider reply calling these tools, optionally thinking first."""
    content = []
    if thinking:
        content.append({"type": "thinking", "thinking": "", "signature": "sig-1"})
    if text:
        content.append({"type": "text", "text": text})
    for index, (name, arguments) in enumerate(calls):
        content.append({"type": "tool_use", "id": f"toolu_{name}_{index}", "name": name,
                        "input": arguments})
    return {"content": content, "stop_reason": "tool_use" if calls else "end_turn"}


def words(text):
    return {"content": [{"type": "text", "text": text}], "stop_reason": "end_turn"}


class TestItLooksThingsUpAndThenAnswers:
    """Asked how far it was from O to the Causeway, the page said four times that
    it could not work that out -- holding both positions. A look-up went straight
    to the phone and the model never saw it; now it does, and answers from it."""

    def test_what_a_look_up_found_goes_back_and_the_answer_comes_from_it(self):
        sent, seen = [], []
        parse = make_model_parser("k", transport=transport_replying(
            uses(("mark_distance", {"from_mark": "O", "to_mark": "C"}), thinking=True),
            words("It's 11.57 nm from O to the Causeway."), captured=sent))
        intent = parse("how far is it from O to causeway?",
                       reading({"mark_distance": "O to C: 11.57 nm"}, seen))
        assert intent.name == ANSWER and "11.57" in intent.arguments["text"]
        assert seen == [("mark_distance", {"from_mark": "O", "to_mark": "C"})]
        second = sent[1]["messages"]
        assert second[-2]["role"] == "assistant"
        result = second[-1]["content"][0]
        assert result["type"] == "tool_result"
        assert result["tool_use_id"] == "toolu_mark_distance_0"
        assert result["content"] == "O to C: 11.57 nm"

    def test_its_own_reply_goes_back_as_it_came_thinking_and_all(self):
        """A model continuing its turn is handed what it wrote; an edited copy
        -- a thinking block dropped, its signature lost -- is not its turn."""
        sent = []
        parse = make_model_parser("k", transport=transport_replying(
            uses(("look_up", {"topic": "courses"}), thinking=True), words("67."), captured=sent))
        parse("how many courses", reading())
        echoed = sent[1]["messages"][-2]["content"]
        assert echoed[0] == {"type": "thinking", "thinking": "", "signature": "sig-1"}

    def test_several_look_ups_at_once_are_all_answered_in_one_message(self):
        """Split across messages, a model learns to stop asking for more than one
        thing at a time -- and "what are O's and C's positions?" lost C."""
        sent, seen = [], []
        parse = make_model_parser("k", transport=transport_replying(
            uses(("look_up", {"topic": "marks", "query": "O"}),
                 ("look_up", {"topic": "marks", "query": "C"})),
            words("O is at one place and C at another."), captured=sent))
        parse("what are O and C positions?", reading(seen=seen))
        assert len(seen) == 2
        results = sent[1]["messages"][-1]["content"]
        assert [r["tool_use_id"] for r in results] == ["toolu_look_up_0", "toolu_look_up_1"]

    def test_a_change_ends_the_turn_and_nothing_alongside_it_is_run(self):
        seen = []
        parse = make_model_parser("k", transport=transport_replying(
            uses(("look_up", {"topic": "courses"}), ("set_start_and_course", {"course_no": 4}))))
        intent = parse("use course 4", reading(seen=seen))
        assert intent.name == "set_start_and_course"
        assert seen == [], "a look-up ran alongside a change nobody has agreed to"

    def test_two_changes_come_back_as_one_plan(self):
        parse = make_model_parser("k", transport=transport_replying(
            uses(("set_start_and_course", {"course_no": 4}),
                 ("add_entries", {"scope": "boat", "boat": "Mojito"}))))
        intent = parse("use course 4 and add Mojito", reading())
        assert intent.name == "plan"
        assert [s["name"] for s in intent.arguments["steps"]] == [
            "set_start_and_course", "add_entries"]

    def test_the_status_report_is_shown_as_it_is_not_reworded(self):
        """The sentence people type most. A second round trip to put the app's
        own report in other words would double the wait for nothing."""
        calls = []
        parse = make_model_parser("k", transport=transport_replying(
            uses(("race_status", {})), captured=calls))
        assert parse("status", reading()).name == "race_status"
        assert len(calls) == 1

    def test_without_a_way_to_look_things_up_a_look_up_is_the_answer(self):
        """The caller decides. A context with no `read` gets the look-up back as
        an intent, which the page shows as it always did."""
        parse = make_model_parser("k", transport=transport_replying(
            uses(("look_up", {"topic": "courses"}))))
        assert parse("how many courses", ctx()).name == "look_up"

    def test_a_model_that_keeps_looking_is_stopped(self):
        from core.assistant_llm import MAX_READ_ROUNDS
        calls = []
        parse = make_model_parser("k", transport=transport_replying(
            *[uses(("look_up", {"topic": "races"}))] * (MAX_READ_ROUNDS + 3), captured=calls))
        intent = parse("which was the longest ISORA course", reading())
        assert len(calls) == MAX_READ_ROUNDS + 1
        assert intent.name == "look_up", "the page should be shown what was looked up"

    def test_a_provider_that_fails_part_way_still_leaves_what_was_found(self):
        parse = make_model_parser("k", transport=transport_replying(
            uses(("look_up", {"topic": "courses"})), None))
        assert parse("how many courses", reading()).name == "look_up"

    def test_a_look_up_that_raises_is_reported_to_the_model_not_the_page(self):
        sent = []
        parse = make_model_parser("k", transport=transport_replying(
            uses(("look_up", {"topic": "fleet"})), words("Nothing is reporting."), captured=sent))
        context = ctx()

        def broken(name, arguments):
            raise RuntimeError("tracker database locked")
        context.read = broken
        assert parse("where are they", context).name == ANSWER
        result = sent[1]["messages"][-1]["content"][0]
        assert result["is_error"] is True and "tracker database locked" in result["content"]

    def test_an_openai_endpoint_loops_the_same_way(self):
        sent = []
        url = "https://api.cloudflare.com/client/v4/accounts/a/ai/v1/chat/completions"
        parse = make_model_parser("k", base_url=url, transport=transport_replying(
            {"choices": [{"message": {"tool_calls": [
                {"id": "c1", "function": {"name": "mark_distance",
                                          "arguments": '{"from_mark": "O", "to_mark": "C"}'}}]}}]},
            {"choices": [{"message": {"content": "11.57 nm."}}]}, captured=sent))
        intent = parse("O to C?", reading({"mark_distance": "11.57 nm"}))
        assert intent.name == ANSWER
        tail = sent[1]["messages"][-2:]
        assert tail[0]["role"] == "assistant" and tail[0]["tool_calls"][0]["id"] == "c1"
        assert tail[1] == {"role": "tool", "tool_call_id": "c1", "content": "11.57 nm"}


class TestWhatTheModelIsToldAboutEachTool:
    def _tools(self):
        calls = []
        parse = make_model_parser("k", transport=transport_returning(tool_call("race_status", {}), calls))
        parse("status", ctx())
        return {t["name"]: t for t in calls[0]["payload"]["tools"]}

    def test_numbers_are_numbers_and_choices_are_choices(self):
        tools = self._tools()
        props = lambda name: tools[name]["input_schema"]["properties"]  # noqa: E731
        assert props("set_start_and_course")["race_id"]["type"] == "integer"
        assert props("create_race")["add_all_active"]["type"] == "boolean"
        assert props("add_entries")["scope"]["enum"] == ["all_active", "boat", "same_as"]
        assert tools["mark_distance"]["input_schema"]["required"] == ["from_mark", "to_mark"]

    def test_a_new_race_can_be_given_its_course(self):
        """"Create a race at 11 on course 4" had nowhere to put the 4."""
        assert "course_no" in self._tools()["create_race"]["input_schema"]["properties"]

    def test_each_says_whether_it_reads_or_changes(self):
        tools = self._tools()
        assert tools["look_up"]["description"].startswith("Read-only")
        assert tools["create_race"]["description"].startswith("Changes something")


class TestTheUnchangingPartIsCached:
    """Every round of a turn sends everything before it again, and the tools and
    the prompt are the same for every sentence anybody types."""

    def _payload(self):
        calls = []
        parse = make_model_parser("k", transport=transport_returning(tool_call("race_status", {}), calls))
        parse("status", ctx())
        return calls[0]["payload"]

    def test_the_system_prompt_is_a_plain_string(self):
        """Cloudflare's Messages route answered a block-shaped one with a 400."""
        assert isinstance(self._payload()["system"], str)

    def test_a_fixed_opening_marks_the_end_of_what_never_changes(self):
        from core.assistant_llm import CACHE_ANCHOR
        first = self._payload()["messages"][0]["content"][0]
        assert first == {"type": "text", "text": CACHE_ANCHOR,
                         "cache_control": {"type": "ephemeral"}}

    def test_the_end_of_the_request_is_marked_too(self):
        last = self._payload()["messages"][-1]["content"][-1]
        assert last["cache_control"] == {"type": "ephemeral"}
        assert "Instruction: status" in last["text"]


class TestItRemembersWhatItLookedUpAndSaysWhatItIsDoing:
    def test_each_look_up_is_recorded_with_its_arguments(self):
        context = reading()
        parse = make_model_parser("k", transport=transport_replying(
            uses(("suggest_course", {"target_minutes": 20})), words("Three options.")))
        parse("course for 20 minutes", context)
        assert context.looked_up == [{"tool": "suggest_course", "arguments": {"target_minutes": 20}}]

    def test_it_says_what_it_is_doing_in_order(self):
        told = []
        context = reading()
        context.progress = told.append
        parse = make_model_parser("k", transport=transport_replying(
            uses(("series_standings", {"series": "autumn"})), words("Mojito leads.")))
        parse("who is leading", context)
        assert told == ["thinking", "series_standings", "answering"]

    def test_a_progress_report_that_fails_does_not_fail_the_answer(self):
        context = reading()

        def broken(stage):
            raise RuntimeError("nobody listening")
        context.progress = broken
        parse = make_model_parser("k", transport=transport_replying(words("Hello.")))
        assert parse("hello", context).name == ANSWER

    def test_eight_earlier_turns_are_sent_not_four(self):
        from core.assistant_llm import RECENT_TURNS
        calls = []
        context = ctx()
        context.recent = [(f"said {n}", f"did {n}") for n in range(12)]
        parse = make_model_parser("k", transport=transport_returning(words("ok"), calls))
        parse("and now?", context)
        sent = calls[0]["payload"]["messages"]
        assert RECENT_TURNS == 8
        assert text_of(sent[0]["content"]).endswith("said 4")
        assert len(sent) == RECENT_TURNS * 2 + 1


class TestTheAppsNotesAreNotRepeated:
    def test_a_note_copied_into_a_reply_is_taken_out(self):
        """It began a reply "[looked up suggest_course: target_minutes 20, boat
        J70]" -- the app's note on an earlier turn, written out as though it
        were how the app talks."""
        parse = make_model_parser("k", transport=transport_replying(
            words('[looked up suggest_course: target_minutes 20, boat "J70"] '
                  "Here are three on the J70. [cards shown, numbered: 1 Course 4 (4p Op)]")))
        intent = parse("do the same again on the J70 polar", reading())
        assert intent.name == ANSWER
        assert intent.arguments["text"] == "Here are three on the J70."

    def test_brackets_of_its_own_are_left_alone(self):
        parse = make_model_parser("k", transport=transport_replying(
            words("Course 4 [the long one] takes about an hour.")))
        intent = parse("how long is course 4?", reading())
        assert intent.arguments["text"] == "Course 4 [the long one] takes about an hour."
