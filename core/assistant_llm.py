"""A model-backed parser for `core.assistant`, kept behind the same seam.

`core.assistant` stays free of network and provider: it defines the tools, holds
the two club ambiguities, and turns an intent into a read-back. This module is
the other half -- it asks a model which tool the sentence means and hands back
an `Intent` of exactly the shape `grammar_parse` produces. Nothing downstream
can tell which one answered, which is the point: the model is one implementation
of a parser, not a component the app is built around.

Design notes worth keeping:

* **Anything that acts is a tool call; anything else is words.** A tool call is
  structured output, and parsing free text about race timings is how a model's
  guess becomes a horn -- so nothing is ever read out of prose. Prose is still
  carried back (as `ANSWER`) because a race officer asks questions as well as
  giving orders, and there is no path from it to an action.
* **A base URL is configuration.** Pointed at Cloudflare's AI Gateway it gains
  logging, rate limiting and a model fallback without a line changing here.
  Caching, though, wants care: the meaning of "start a race at 11" depends on
  the day it is said, so the request carries the resolved date and the cache key
  must include it -- or caching must be off for this route.
* **Timeouts are short and failure is quiet.** The hut is on contended 4G, this
  call happens while the start scheduler is running, and a parser that returns
  None simply falls through to the grammar. Being slow is worse than being
  unavailable.

Verified against Cloudflare's `anthropic/claude-sonnet-5` from this machine
(`scripts/verify_assistant_model.py`), which is also where the timeout below came
from. The tests drive the whole path through a stubbed transport, so they prove
the wiring and the refusals and never the model's judgement -- that is what the
script is for, and it is not in CI because it costs money and needs an account.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from datetime import datetime
from typing import Any, Callable, Dict, Optional

from core.assistant import ANSWER, TOOLS, CommandContext, Intent

# Measured, not guessed: claude-sonnet-5 through Cloudflare answers these
# commands in 3.4-6.9 s, and the first 12-second setting timed out often enough
# to look like the model was simply stupid -- the page fell back to the grammar
# and said "I did not understand that" to a sentence the model reads perfectly.
# Thirty leaves room for a slow first call without holding a Waitress worker
# thread long enough to matter for a page one or two people use.
DEFAULT_TIMEOUT_S = 30.0
# Every reply this endpoint sends begins with a thinking block, and thinking is
# spent from the same budget as the answer: a plain question came back having
# used 421 of the 512 originally allowed. A reply that runs out mid-thought
# carries no tool call and no words, which reaches the page as a failure -- the
# model looking stupid for a reason that is entirely ours. Raised to 2048, and
# then to this after a sentence on the club's own test server still ran out: a
# long question about several boats is a long think, and the answer itself is
# never more than one tool call or two sentences, so the ceiling costs nothing
# when it is not needed.
MAX_TOKENS = 4096
DEFAULT_MODEL = "claude-sonnet-5"
DEFAULT_BASE_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"

SYSTEM_PROMPT = """You turn a race officer's spoken instruction into one tool call for a \
sailing club's race-management app. You never decide anything: you choose a tool and its \
arguments, and the app applies the rules.

Facts you must use:
- The time a race stores is its FIRST WARNING SIGNAL. The first start (the gun) is five \
minutes later. When somebody says a race "starts at 11", they mean the gun is at 11:00, so \
gun_time is 11:00 and the app derives the warning signal itself. Always report gun_time.
- "An hour long" means the fixed period for a PURSUIT race, but only a target used to \
recommend a course for a STANDARD race. If the sentence does not make the race type clear, \
do not guess: leave race_type unset so the app can ask.
- Times are local to the club and must be returned as ISO 8601 with a date.
- A bare hour of 7 or less, with no am or pm, is the afternoon or evening -- add twelve. The \
club does not race at three in the morning, and "at seven" is a Wednesday evening race at \
19:00. This is the same rule the app's own fallback parser applies, so a model that reads \
"at seven" as 07:00 disagrees with the rest of the app about the same words.

Choosing a tool. These are the jobs, in the words a race officer actually uses:
- create_race: making a new race. "new race", "create a race", "we'll get going at 11".
- set_start_and_course: changing an existing race's start time or course. "use course 4", "put the gun back ten minutes", "course 7 today".
- add_entries: entering boats. "add all the boats", "put every boat in", "add Mojito and Sgrech Bach", "the same boats as last week".
- shorten_course: ending the race early at a mark the fleet is already sailing to. "shorten at 4", "finish them at mark 4", "cut it short at the windward mark".
- race_status: anything asking how the race is going. "status", "how's it going", "where are we", "how are they getting on".

A QUESTION ABOUT A JOB IS NOT AN INSTRUCTION TO DO IT. Call a tool only when the sentence \
tells you to do something now. "What series will a new race be in?", "how long would that \
take?", "which course would you use?" and "can you shorten a course?" are all questions: \
answer them in words and call nothing. "Start a race at 11", "use course 4", "add the boats" \
are instructions. When a sentence could be either, it is a question -- a proposed race the \
person then has to decline is worse than an answer they have to follow up.

The club's own words, and what this app can do about each. Never answer "I don't know what \
that means" to one of these:
- AP, "postpone", "hold the start", "pause the start", "the wind's died": the postpone_race \
tool. AP goes up with two horn blasts and no start signal sounds until it comes down. \
**Prefer it to moving the start time**, which signals nothing to the fleet: under AP the \
warning signal is made one minute after the flag is lowered, so nobody has to predict when the \
wind will return. Do not ask what time to restart at -- that is the whole point of the flag. \
When they are ready again, resume_race lowers it. "Further signals ashore" is AP over H and \
"no more racing today" is AP over A.
- OCS, "over the line", "recall", "general recall": the app cannot signal a recall. Say so \
plainly. The club starts races with nobody watching the line and reviews the video afterwards.
- "shorten", "finish them at 4", "cut it short": the shorten_course tool. They round the mark \
they are already sailing to and go straight to the finish; it sounds two horn blasts.
- "warning signal" is what the race stores; the gun is five minutes after it.
- "arm the sequence", "start the sequence": there is no arming step to do. **Setting a race's \
start time is what puts the horn sequence on the clock** — with start automation switched on in \
Settings, the app fires the warning, preparatory and start signals off the stored warning signal, \
and moving that time moves them. Say that plainly: somebody asking to delay a start needs to know \
the horn follows.
- a "course" is one of the club's numbered fixed courses, and the numbers in the facts below \
are the real ones for the wind that is blowing now.

The facts you are given are the app's own working: the wind it is reading, the course's legs \
and how long it should take, and the other courses it would recommend. Use them and quote the \
numbers. Only say the app does not tell you something when it really is not in the facts.

This is a conversation, not a queue of unrelated sentences. The turns before are shown \
above, each one what was typed and what the app then did. A short reply like "standard", \
"the second one", "make it half past" or "yes the IRC one" is answering the app's last \
question: work out what it was asked and call the tool AGAIN IN FULL, carrying forward \
everything already settled -- the time, the name, the length, the race -- not only the new \
fragment. A fragment on its own is not a command and the app will say it did not understand.

If the instruction is not one of those jobs, DO NOT call a tool. Reply in one or two short \
sentences instead, using only the facts you were given. Somebody steering a boat is reading \
it on a phone, so be brief and concrete, and offer the sentence they could type next if there \
is an obvious one. Two hard limits on anything you say in words: you have not done and cannot \
do anything -- only a tool call does anything, so never say or imply that something has been \
set, started, changed, added or scheduled -- and if the facts do not tell you something, say \
the app does not tell you rather than working it out or guessing. Do not pick the nearest tool \
to be helpful: an unasked-for command is far worse than an unanswered question."""


def _tool_schema() -> list:
    """The app's tool list in the provider's schema shape.

    Built from `core.assistant.TOOLS` rather than written out again, so a tool
    the app does not implement cannot be offered to a model by drifting.
    """
    schema = []
    for tool in TOOLS:
        properties = {name: {"type": "string", "description": text}
                      for name, text in tool["arguments"].items()}
        schema.append({
            "name": tool["name"],
            "description": tool["description"],
            "input_schema": {"type": "object", "properties": properties},
        })
    return schema


def dialect_for(url: str) -> str:
    """Which wire format an endpoint speaks, inferred from its address.

    Two exist and the club needs both. Anthropic's Messages API is what a direct
    key or a Cloudflare AI Gateway *anthropic* route wants. The OpenAI
    chat-completions shape is what nearly everything else speaks, Workers AI
    included -- its /ai/v1/messages route answers "Anthropic Messages API is not
    supported for model @cf/..." for Cloudflare's own models, which is how this
    came up.
    """
    lowered = (url or "").rstrip("/").lower()
    # The path says which, and nothing else does. Cloudflare serves both from the
    # same account: /ai/v1/messages speaks Anthropic (for the Anthropic models it
    # hosts) and /ai/v1/chat/completions speaks OpenAI (for everything else), so
    # a rule based on the host would get one of them wrong.
    if lowered.endswith("/chat/completions"):
        return "openai"
    if lowered.endswith("/messages"):
        return "anthropic"
    if "api.anthropic.com" in lowered or "/anthropic/" in lowered:
        return "anthropic"
    return "openai"


def auth_headers(url: str, api_key: str) -> Dict[str, str]:
    """Anthropic's own API takes the key in x-api-key; everyone else takes a
    bearer token -- including Cloudflare, even for its Anthropic models.

    Deliberately not tied to the dialect. Cloudflare's /ai/v1/messages speaks
    Anthropic's format while wanting a Cloudflare token, and answered a perfectly
    good token in an x-api-key header with a bare "Authentication error" and no
    other clue.
    """
    lowered = (url or "").lower()
    if "api.anthropic.com" in lowered or "/anthropic/v1/" in lowered:
        return {"x-api-key": api_key, "anthropic-version": ANTHROPIC_VERSION}
    return {"Authorization": f"Bearer {api_key}", "anthropic-version": ANTHROPIC_VERSION}


def _openai_tools() -> list:
    """The same tool list in OpenAI's function-calling envelope."""
    return [{"type": "function",
             "function": {"name": t["name"], "description": t["description"],
                          "parameters": t["input_schema"]}}
            for t in _tool_schema()]


def _openai_payload(model: str, system: str, user: str, history: Optional[list] = None) -> Dict[str, Any]:
    return {
        "model": model,
        "max_tokens": MAX_TOKENS,
        "tools": _openai_tools(),
        # "auto" rather than "required", for the same reason as the Anthropic
        # side: a model that must call something will call something.
        "tool_choice": "auto",
        "messages": ([{"role": "system", "content": system}] + list(history or [])
                     + [{"role": "user", "content": user}]),
    }


def intent_from_openai_response(body: Dict[str, Any], model: str) -> Optional[Intent]:
    """Pull the tool call -- or the reply in words -- out of an OpenAI-shaped
    response, or None.

    Arguments arrive as a JSON *string* rather than an object, and a model that
    returns something that is not JSON at all must be a miss rather than a crash.
    """
    prose = ""
    for choice in (body or {}).get("choices", []) or []:
        message = (choice or {}).get("message") or {}
        prose = prose or str(message.get("content") or "").strip()
        for call in message.get("tool_calls", []) or []:
            function = (call or {}).get("function") or {}
            name = str(function.get("name") or "")
            raw = function.get("arguments")
            if isinstance(raw, dict):
                arguments = raw
            else:
                try:
                    arguments = json.loads(raw or "{}")
                except (ValueError, TypeError):
                    arguments = {}
            if not isinstance(arguments, dict):
                arguments = {}
            return Intent(name=name, arguments=dict(arguments), source=model)
    return Intent(ANSWER, {"text": prose}, source=model) if prose else None


# Why the model last failed, so the fallback is not silent. Falling back to the
# grammar is the right behaviour and a terrible thing to do quietly: the symptom
# is a page that seems stupid, and the cause -- a 402, a wrong model id, a
# timeout -- is sitting in an HTTP response nobody sees.
LAST_ERROR: Dict[str, Any] = {"when": "", "detail": ""}


def _why_nothing(body: Dict[str, Any]) -> str:
    """Whatever the provider said about why the reply stopped."""
    reason = str((body or {}).get("stop_reason") or "")
    if not reason:
        for choice in (body or {}).get("choices", []) or []:
            reason = str((choice or {}).get("finish_reason") or "")
            if reason:
                break
    if reason == "max_tokens" or reason == "length":
        # Said in words somebody on a boat can act on. "Raise max_tokens" is an
        # instruction for whoever maintains the app, and it was appearing on a
        # phone in the middle of a race.
        return "it ran out of room before answering; a shorter sentence will work"
    return f"stop_reason={reason or 'unknown'}"


def _remember_error(detail: str) -> None:
    LAST_ERROR["when"] = datetime.now().isoformat(timespec="seconds")
    LAST_ERROR["detail"] = detail[:300]


def _provider_message(body: str) -> str:
    """The provider's own words, if it sent any, else the raw body."""
    try:
        parsed = json.loads(body)
    except (ValueError, TypeError):
        return body.strip()[:200]
    error = parsed.get("error")
    if isinstance(error, dict) and error.get("message"):
        return str(error["message"])[:200]
    errors = parsed.get("errors")
    if isinstance(errors, list) and errors and isinstance(errors[0], dict):
        return str(errors[0].get("message") or "")[:200]
    return body.strip()[:200]


def _post_json(url: str, payload: Dict[str, Any], headers: Dict[str, str],
               timeout: float) -> Optional[Dict[str, Any]]:
    request = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                     headers={"Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
        LAST_ERROR["when"] = ""
        LAST_ERROR["detail"] = ""
        return body
    except urllib.error.HTTPError as exc:
        # The interesting ones all land here: 401 wrong auth style, 402 no
        # credit, 400 no such model. Each is a sentence the provider wrote.
        try:
            detail = _provider_message(exc.read().decode("utf-8", "replace"))
        except Exception:
            detail = ""
        _remember_error(f"HTTP {exc.code}" + (f": {detail}" if detail else ""))
        return None
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
        # Unreachable, slow, or answering something that is not JSON. The caller
        # falls back to the grammar; a command that cannot be parsed at all is
        # better than one parsed from a broken reply.
        _remember_error(f"{type(exc).__name__}: {str(exc)[:160]}")
        return None


def intent_from_response(body: Dict[str, Any], model: str) -> Optional[Intent]:
    """Pull the tool call -- or the reply in words -- out of a provider response.

    A tool call always wins: an answer in words alongside one is commentary on
    something the app is about to do. Prose on its own used to be discarded, and
    with it every question that was not a command; the page then said "I did not
    understand that" to a sentence the model had understood and answered. It now
    comes back as `ANSWER`, which nothing can execute.
    """
    prose = []
    for block in (body or {}).get("content", []) or []:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text":
            prose.append(str(block.get("text") or "").strip())
            continue
        if block.get("type") != "tool_use":
            continue
        name = str(block.get("name") or "")
        arguments = block.get("input")
        if not isinstance(arguments, dict):
            arguments = {}
        return Intent(name=name, arguments=dict(arguments), source=model)
    said = " ".join(part for part in prose if part).strip()
    return Intent(ANSWER, {"text": said}, source=model) if said else None


def make_model_parser(api_key: str, model: str = DEFAULT_MODEL, base_url: str = DEFAULT_BASE_URL,
                      timeout_s: float = DEFAULT_TIMEOUT_S,
                      transport: Optional[Callable[..., Optional[Dict[str, Any]]]] = None):
    """Build a parser of the same shape as `grammar_parse`.

    `transport` exists so the tests can drive the whole path without a network
    or an account: it is the one seam between this module and the internet.
    """
    send = transport or _post_json

    dialect = dialect_for(base_url)

    def parse(text: str, context: CommandContext) -> Optional[Intent]:
        if not api_key or not (text or "").strip():
            return None
        # The date goes in the user message rather than the system prompt so a
        # gateway cache keyed on the request cannot serve yesterday's answer to
        # "start a race at 11".
        facts = [f"Local date and time now: {context.now.isoformat(timespec='minutes')}."]
        if context.current_race_id:
            facts.append(f"The current race is #{context.current_race_id} "
                         f"'{context.current_race_name}'.")
            if context.current_gun_time:
                # So "put the start back ten minutes" has something to be ten
                # minutes later than.
                facts.append("Its first gun is "
                             f"{context.current_gun_time.isoformat(timespec='minutes')}.")
            if context.course_is_set and context.current_course_no:
                facts.append(f"Its course is {context.current_course_no}.")
            elif not context.course_is_set:
                facts.append("Its course has not been chosen yet.")
        # Everything else the app knows about right now: the wind, how many boats
        # are out, how far round they are. This is the whole of what may be said
        # in an answer -- there is no other source, which is the point.
        facts.extend(context.facts or [])
        if context.pending_readback:
            # While a proposal is waiting to be agreed to, it is what the
            # conversation is about. Without this, "can you do a longer one?"
            # was answered about the race that happened to be current -- one
            # that had already finished -- while the new race sat unmade.
            facts.append(f"WAITING FOR A YES OR NO: {context.pending_readback} "
                         "Nothing has been done yet. If this instruction changes that "
                         f"proposal, call {context.pending_intent} again in full with the "
                         "change and everything else it already had. If it agrees with it, "
                         "call no tool and say to press Yes. It is not about any other race.")
        said = "\n".join(facts) + f"\nInstruction: {text.strip()}"
        # What was said before, so "make it a pursuit" or "add the fleet to that"
        # has an antecedent. Each earlier turn is the sentence typed and a line
        # saying what the app did with it -- not the model's own words, which it
        # never saw the consequences of.
        history: list = []
        for said_before, did_before in (context.recent or [])[-4:]:
            history.append({"role": "user", "content": said_before})
            history.append({"role": "assistant", "content": did_before})

        if dialect == "anthropic":
            payload: Dict[str, Any] = {
                "model": model,
                "max_tokens": MAX_TOKENS,
                "system": SYSTEM_PROMPT,
                "tools": _tool_schema(),
                # "auto", not "any". Forcing a tool call contradicts the last
                # line of the system prompt and makes declining impossible: asked
                # what I thought of the cricket, a forced model picked the most
                # harmless tool it had and proposed a status report. Prose comes
                # back as no intent, which is the honest answer.
                "tool_choice": {"type": "auto"},
                "messages": history + [{"role": "user", "content": said}],
            }
        else:
            payload = _openai_payload(model, SYSTEM_PROMPT, said, history)
        body = send(base_url, payload, auth_headers(base_url, api_key), timeout_s)
        if not body:
            return None
        intent = (intent_from_response(body, model) if dialect == "anthropic"
                  else intent_from_openai_response(body, model))
        if intent is None:
            # A reply carrying neither a tool call nor a word is a failure, and
            # it is indistinguishable on the page from a sentence nobody could
            # read. Recorded so the notice says which -- "ran out of room
            # thinking" is a setting, not the model being stupid.
            _remember_error(f"answered with nothing usable ({_why_nothing(body)})")
        return intent

    return parse


def interpreter_status(config: Dict[str, Any]) -> Dict[str, Any]:
    """Whether there is an interpreter at all, and whether it is answering.

    `grammar_only` now reads as "no interpreter": the built-in grammar no longer
    answers commands, because on a page that invites plain English a handful of
    sentence shapes is indistinguishable from an app that understands nothing.
    Both the page and Settings show this, and with no model the page says the
    feature is unavailable rather than appearing to work.
    """
    key = str((config or {}).get("assistant_api_key") or "").strip()
    model = str((config or {}).get("assistant_model") or "").strip() or DEFAULT_MODEL
    if not key:
        return {"model": "", "grammar_only": True, "error": "",
                "text": "not available — no interpreter is configured, so nothing typed on the "
                        "Virtual Race Officer page can be read. Add a model below."}
    failure = LAST_ERROR.get("detail") or ""
    if failure:
        # Configured but failing, which otherwise looks identical to configured
        # and working badly.
        return {"model": model, "grammar_only": False, "error": failure,
                "text": (f"{model} is configured but its last request failed — {failure}. "
                         "Commands will not be read until it answers again.")}
    return {"model": model, "grammar_only": False, "error": "",
            "text": f"{model}, answering."}


def parser_from_config(config: Dict[str, Any], transport=None):
    """The configured parser, or None when no model is set up.

    None is a working state, not an error: the app falls back to the grammar,
    which is what runs the racing when the hut cannot reach anything.
    """
    key = str((config or {}).get("assistant_api_key") or "").strip()
    if not key:
        return None
    return make_model_parser(
        api_key=key,
        model=str(config.get("assistant_model") or DEFAULT_MODEL).strip() or DEFAULT_MODEL,
        base_url=str(config.get("assistant_base_url") or DEFAULT_BASE_URL).strip() or DEFAULT_BASE_URL,
        transport=transport,
    )
