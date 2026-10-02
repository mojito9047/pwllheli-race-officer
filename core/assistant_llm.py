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
* **Looking something up is a round trip; changing something ends the turn.**
  A read-only tool is run by the caller and its result goes back to the model,
  which answers from it -- up to `MAX_READ_ROUNDS` times, inside
  `TURN_BUDGET_S`. Any tool that changes something comes back at once as a
  proposal for a person to agree to, so the model never sees the result of an
  action: there is none until somebody says yes.
* **A base URL is configuration.** Pointed at Cloudflare's AI Gateway it gains
  logging, rate limiting and a model fallback without a line changing here.
  Caching, though, wants care: the meaning of "start a race at 11" depends on
  the day it is said, so the request carries the resolved date and the cache key
  must include it -- or caching must be off for this route.
* **Timeouts are short and failure is plain.** The hut is on contended 4G and
  this call happens while the start scheduler is running. A parser that returns
  None is a failure, and the page says the interpreter did not answer -- nothing
  is guessed from the sentence instead. Being slow is worse than being
  unavailable.

Verified against Cloudflare's `anthropic/claude-sonnet-5` from this machine, which
is also where the timeout below came from. The tests drive the whole path through
a stubbed transport, so they prove the wiring and the refusals and never the
model's judgement. That is what `scripts/eval_vro.py` is for: the page's own
endpoint, the real model and a copy of the hut's data, scored against the app's
own answers. It is not in CI because it costs money and needs an account.
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from datetime import datetime
from typing import Any, Callable, Dict, Optional, Tuple

from core.assistant import (ANSWER, PLAN, READ, REPORT, TOOLS, WRITE, CommandContext, Intent,
                            tool_kind)

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

# How many times one sentence may go back to the model with what it looked up.
# "How far is it from O to 2 and back?" is one round; "which of the last three
# ISORA courses was longest?" is two. More than three is a question the page
# cannot answer while somebody waits on a boat, and the cap is what stops a
# model that keeps looking from keeping the hut's worker thread with it.
MAX_READ_ROUNDS = 3
# The whole turn, however many rounds it takes. The page used to wait for one
# call; it now waits for several, and somebody at the tiller will not wait a
# minute. When this runs out the page is shown what was looked up, as it is.
TURN_BUDGET_S = 45.0
# Earlier turns sent with each sentence, each with what it looked up. Four lost
# a target time three questions after it was set; a turn is a hundred tokens or
# so, and the prompt and tools before them are cached.
RECENT_TURNS = 8

SYSTEM_PROMPT = """You are the Virtual Race Officer for a sailing club's race-management \
app. A race officer on a boat types to you on a phone: you answer their questions from the \
app's own data, and you turn their instructions into tool calls. You never decide anything the \
app decides: you choose a tool and its arguments, and the app applies the rules.

Facts you must use:
- The time a race stores is its FIRST WARNING SIGNAL. The first start (the gun) is five \
minutes later. When somebody says a race "starts at 11", they mean the gun is at 11:00, so \
gun_time is 11:00 and the app derives the warning signal itself. Always report gun_time.
- "An hour long" means the fixed period for a PURSUIT race, but only a target used to \
recommend a course for a STANDARD race. If the sentence does not make the race type clear, \
do not guess: leave race_type unset so the app can ask.
- Times are local to the club and must be returned as ISO 8601 with a date.
- A bare hour with no am or pm is read the way the app itself reads one, so the \
two never disagree about the same words: 1 to 7 is the afternoon or evening -- add twelve, \
because the club does not race at three in the morning and "at seven" is a Wednesday evening \
race at 19:00 -- and 8 to 12 is the morning or midday as said, so "at 11" is 11:00. If that \
time has already gone today, it is tomorrow: "at 11", said at ten at night, is 11:00 tomorrow, \
not 23:00.
- "Half past", "quarter past" or "quarter to" with no hour means the next one coming up after \
the time now. "In ten minutes" is ten minutes from now. Work the time out; do not ask for it.

How a turn works. Some tools only read -- READ_TOOLS. They \
run straight away and what they find comes back to you: use them to answer questions, ask for \
several in the same reply when a question needs more than one, and then answer from what they \
returned. race_status shows the race officer the app's own status report and ends your turn. \
Every other tool changes something, and calling it ends your turn: the app reads it back to the \
race officer and nothing happens until they say yes. When one sentence asks for several \
changes, call each of those tools in the same reply; they are read back together and agreed to \
once. A new race's course, series, start and boats all go in create_race itself.

Choosing a tool. These are the jobs, in the words a race officer actually uses:
- create_race: making a new race. "new race", "create a race", "we'll get going at 11", \
"a race at 11 on course 4". Give it only the name and series the race officer says: left out, \
the app asks for them, suggesting the last race's, and a race in a series is entered with the \
boats already racing in it. Do not make up a name, and do not pick a series or ask for every \
boat to be entered on their behalf.
- set_start_and_course: changing an existing race's start time, course or series. "use course \
4", "put the gun back ten minutes", "course 7 today", "put it in the summer series", "use \
the J70 polar for this race".
- time_course (a look-up, not a change): how long a course would take. "time course 4 on the \
J70 polar", "show me course 6 on the J70", "how long would that take", "what about on a J109". \
Timing a course on a polar never changes the race's course or its polar; only an instruction to \
use one does.
- add_entries: entering boats in an existing race. "add Mojito and Sgrech Bach", "the same \
boats as last week", and -- only when they say so -- "add all the boats".
- finish_boat: a boat has finished. "Mojito's finished", "finish Jackdaw", "Mojito's crossed \
the line", and, once the race is under way, "that's 1234 over the line". It records the finish at the moment they say yes and sounds the horn, exactly as the \
Finish button on the race sheet does. One call a boat; two finishing together are two calls.
- set_custom_course: a course made up from the marks. "windward leeward twice round, O to 4". \
When the race officer names the marks, that is the instruction: propose it, every mark to port \
unless they say otherwise -- port is the club's usual hand, and the read-back names each one, so \
a wrong hand is caught before anything happens. Do not ask which hand, and do not stop at timing it.
- shorten_course: ending the race early at a mark the fleet is already sailing to. "shorten at \
4", "finish them at mark 4", "cut it short at the windward mark".
- race_status: the status report. "status", "how's it going", "where are we".
- A made-up course for the wind: suggest_course finds and times candidates from all the club's \
marks, time_course times any sequence or variation, and set_custom_course proposes the one \
chosen, with the sequence exactly as the suggestion gives it. Offer the suggestions and let the \
race officer choose before proposing one, unless they asked you to pick. Write any course the way \
the course board does -- "Fp 8p Fp 8p Op", each mark with p or s for its hand -- because that is \
what gets read out over the VHF. The board begins with the first mark rounded: the start at O is \
not a rounding, so it is never written first -- "Fp 1p 10p Op", not "Op Fp 1p 10p Op".
- For "another" race of a kind the club has sailed before -- "another ISORA race", "same as last \
week" -- look up the recent races first and carry over what the last one of that kind had: its \
series and its course, unless the race officer says otherwise.

A QUESTION ABOUT A JOB IS NOT AN INSTRUCTION TO DO IT. Call a changing tool only when the \
sentence tells you to do something now. "What series will a new race be in?", "how long would \
that take?", "which course would you use?", "which is longer, course 3 or 4?" and "can you \
shorten a course?" are all questions: look up what you need and answer in words. "Start a race \
at 11", "use course 4", "add the boats" are instructions. When a sentence could be either, it \
is a question -- a proposed change the person then has to decline is worse than an answer they \
have to follow up.

The club's own words, and what this app can do about each. Never answer "I don't know what \
that means" to one of these:
- AP, "postpone", "hold the start", "pause the start", "the wind's died": the postpone_race \
tool. AP goes up with two horn blasts and no start signal sounds until it comes down. \
**Prefer it to moving the start time**, which signals nothing to the fleet: under AP the \
warning signal is made one minute after the flag is lowered, so nobody has to predict when the \
wind will return. Do not ask what time to restart at -- that is the whole point of the flag. \
When they are ready again, resume_race lowers it. "Further signals ashore" is AP over H and \
"no more racing today" is AP over A.
- OCS, "over early", "over the line at the start", "recall", "general recall": you cannot \
signal a recall from here. (A boat "over the line" in a race that has been under way for a while \
has finished -- finish_boat -- not started early.) Say so \
plainly: the race sheet and the start console can, and a recall sounded there is in the race \
log (race_log) -- so if asked whether there was one, look, rather than saying there cannot have \
been. The club starts races with nobody watching the line and reviews the video afterwards.
- "shorten", "finish them at 4", "cut it short": the shorten_course tool. They round the mark \
they are already sailing to and go straight to the finish; it sounds two horn blasts.
- "warning signal" is what the race stores; the gun is five minutes after it.
- "arm the sequence", "start the sequence", "sound the horn": there is no arming step and no \
horn on demand -- the horn sounds for a finish (finish_boat), a shortened course and a \
postponement, as those tools say, and never on its own. **Setting a race's start time is what puts the horn sequence on the clock** — \
with start automation switched on in Settings, the app fires the warning, preparatory and start \
signals off the stored warning signal, and moving that time moves them. Say that plainly: \
somebody asking to delay a start needs to know the horn follows. Start automation itself is a \
switch in Settings that only a person in the app can change -- you cannot.
- a "course" is one of the club's numbered fixed courses, and the numbers in the facts below \
are the real ones for the wind that is blowing now.

The facts you are given are the app's own working: the wind it is reading, the course's legs \
and how long it should take, and the other courses it would recommend. Use them and quote the \
numbers.

Answering in words. Work things out from the figures the app has given you -- in the facts or \
in what a look-up returned -- and say the answer first, with its number and its units: add \
legs up, compare lengths, count boats, subtract times, compare points. Distances and bearings \
between marks come from mark_distance, never from your own trigonometry on positions. A boat's \
polar (boat_polar) is there to reason with -- which boat gains on a reach, what angle a boat \
beats at -- but a time round a course comes from time_course or suggest_course, which do those \
sums on the same polar leg by leg, so the answer matches the race sheet. If neither the facts \
nor a look-up can tell you something, say so plainly; never invent a figure. Somebody steering \
a boat is reading it on a phone: usually one to three sentences, a list only when they asked \
for one or there are options to choose between, and the sentence they could type next if there \
is an obvious one.

How the page shows what you write. It is plain text with three things added: a new line starts a \
new line, a line beginning "1." or "-" is an item in a list, and **double asterisks** make bold. \
Nothing else is formatted -- no headings, tables, italics or code -- and anything else shows as \
the characters you typed. So put each option on its own numbered line, the course in bold board \
notation first and its length and time after it, and never run a list into one sentence. Every \
course the app suggests, recommends, times or looks up -- the club's or a made-up one -- is \
also drawn for the race officer as a card under your words: \
its board, length and time, and for each leg its TWA and tack, sail, distance and minutes. So do \
not copy the legs or the angles out -- say in a sentence which option you would choose and why, \
from what the angles show: a proper beat off the line, a real reach, no leg too short. Number \
the options in the order the app gave them: the cards carry the same numbers and a "Use this" \
button, and "the second one" afterwards is the second card.

This is a conversation, not a queue of unrelated sentences. The turns before are shown \
above, each one what was typed and what the app then did or said. A short reply like \
"standard", "the second one", "make it half past" or "yes the IRC one" is answering the app's \
last question: work out what it was asked and call the tool AGAIN IN FULL, carrying forward \
everything already settled -- the time, the name, the length, the race -- not only the new \
fragment. A fragment on its own is not a command and the app will say it did not understand. \
Words like "show me", "that one", "it" and "the same" point at what the conversation was just \
about -- "what's course 6 like?" then "show me it on the J70 polar" is course 6 -- not at the \
race's own course unless they say so. "The same again", "again" and "do that again" repeat the \
last thing the race officer ASKED FOR, with whatever they change -- "suggest a made-up course \
for about 20 minutes", a question about it, then "do the same again on the J70 polar" is a \
new suggestion for about 20 minutes on the J70 polar -- not something you mentioned in \
passing while answering. When the race officer corrects you -- "that's a different \
course, not course 6", "no, I meant the other one" -- they are telling you what you got wrong: \
answer what they meant, and never propose a change they did not ask for.

One hard limit on anything you say in words: you have not done and cannot do anything -- only \
a tool call does anything -- so never say or imply that something has been set, started, \
changed, added or scheduled. And do not pick the nearest tool to be helpful: an unasked-for \
change is far worse than an unanswered question."""
# Named from the tool list itself, so a look-up added to TOOLS is one the model is
# told it may use without a Yes -- the prompt cannot fall behind the menu.
SYSTEM_PROMPT = SYSTEM_PROMPT.replace("READ_TOOLS", ", ".join(
    tool["name"] for tool in TOOLS if tool_kind(tool["name"]) == READ))


def _tool_schema() -> list:
    """The app's tool list in the provider's schema shape.

    Built from `core.assistant.TOOLS` rather than written out again, so a tool
    the app does not implement cannot be offered to a model by drifting. What
    each kind of tool does to the turn is said in its description too, because
    the model decides from the description whether to look something up and
    answer, or to propose a change and stop.
    """
    said_about = {
        READ: "Read-only: runs at once and the result comes back to you. ",
        REPORT: "",
        WRITE: ("Changes something: the app reads it back to the race officer and nothing "
                "happens until they say yes. "),
    }
    schema = []
    for tool in TOOLS:
        properties = {name: (dict(spec) if isinstance(spec, dict)
                             else {"type": "string", "description": str(spec)})
                      for name, spec in tool["arguments"].items()}
        input_schema: Dict[str, Any] = {"type": "object", "properties": properties}
        if tool.get("required"):
            input_schema["required"] = list(tool["required"])
        schema.append({
            "name": tool["name"],
            "description": said_about[tool_kind(tool["name"])] + tool["description"],
            "input_schema": input_schema,
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


def _openai_payload(model: str, system: str, messages: list) -> Dict[str, Any]:
    return {
        "model": model,
        "max_tokens": MAX_TOKENS,
        "tools": _openai_tools(),
        # "auto" rather than "required", for the same reason as the Anthropic
        # side: a model that must call something will call something.
        "tool_choice": "auto",
        "messages": [{"role": "system", "content": system}] + list(messages),
    }


def intent_from_openai_response(body: Dict[str, Any], model: str) -> Optional[Intent]:
    """What one OpenAI-shaped reply asked for: its first tool call, or its words,
    or None. Read by `_openai_turn`, the same reader the loop uses."""
    calls, prose, _echo = _openai_turn(body)
    if calls:
        return Intent(name=calls[0]["name"], arguments=calls[0]["arguments"], source=model)
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
    """What one reply asked for: its first tool call, or its words, or None.

    A tool call always wins: an answer in words alongside one is commentary on
    something the app is about to do. Prose on its own used to be discarded, and
    with it every question that was not a command; the page then said "I did not
    understand that" to a sentence the model had understood and answered. It now
    comes back as `ANSWER`, which nothing can execute. Read by `_anthropic_turn`,
    the same reader the loop uses.
    """
    calls, prose, _echo = _anthropic_turn(body)
    if calls:
        return Intent(name=calls[0]["name"], arguments=calls[0]["arguments"], source=model)
    return Intent(ANSWER, {"text": prose}, source=model) if prose else None


def _anthropic_turn(body: Dict[str, Any]) -> Tuple[list, str, list]:
    """The tool calls in a reply, what it said in words, and the reply to send back.

    The reply goes back exactly as it came -- thinking blocks, their signatures
    and all -- because a model continuing its own turn is handed what it wrote,
    and one handed an edited copy is not continuing anything.
    """
    calls, prose, echo = [], [], []
    for index, block in enumerate((body or {}).get("content", []) or []):
        if not isinstance(block, dict):
            continue
        block = dict(block)
        if block.get("type") == "text":
            prose.append(str(block.get("text") or "").strip())
        elif block.get("type") == "tool_use":
            # A result is matched to its call by id, so a reply that arrives
            # without one (a stub, a gateway) is given one rather than dropped.
            block.setdefault("id", f"toolu_{index}")
            arguments = block.get("input")
            calls.append({"id": block["id"], "name": str(block.get("name") or ""),
                          "arguments": dict(arguments) if isinstance(arguments, dict) else {}})
        echo.append(block)
    return calls, " ".join(part for part in prose if part).strip(), echo


def _openai_turn(body: Dict[str, Any]) -> Tuple[list, str, Dict[str, Any]]:
    """The same three things out of an OpenAI-shaped reply.

    Arguments arrive as a JSON *string* rather than an object, and a model that
    returns something that is not JSON at all must be a miss rather than a crash.
    """
    calls, prose, echo = [], "", {"role": "assistant", "content": None}
    for choice in (body or {}).get("choices", []) or []:
        message = (choice or {}).get("message") or {}
        prose = prose or str(message.get("content") or "").strip()
        tool_calls = []
        for index, call in enumerate(message.get("tool_calls", []) or []):
            call = dict(call or {})
            function = dict(call.get("function") or {})
            call.setdefault("id", f"call_{index}")
            call.setdefault("type", "function")
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
            function["arguments"] = raw if isinstance(raw, str) else json.dumps(arguments)
            call["function"] = function
            tool_calls.append(call)
            calls.append({"id": call["id"], "name": str(function.get("name") or ""),
                          "arguments": dict(arguments)})
        if tool_calls:
            echo = {"role": "assistant", "content": message.get("content") or None,
                    "tool_calls": tool_calls}
            break
    return calls, prose, echo


def _anthropic_results(echo: list, results: list) -> list:
    """Every result in one message: split across several, a model learns to
    stop asking for more than one thing at a time."""
    return [{"role": "assistant", "content": echo},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": call["id"], "content": text,
                 **({"is_error": True} if failed else {})}
                for call, text, failed in results]}]


def _openai_results(echo: Dict[str, Any], results: list) -> list:
    return [echo] + [{"role": "tool", "tool_call_id": call["id"], "content": text}
                     for call, text, _failed in results]


# The first words of every conversation, always the same, so a cache marker on
# them marks the end of the part of the request that never changes: the tools
# and the system prompt. It has to be a message because Cloudflare's Messages
# route takes the system prompt only as a plain string, and a marker needs a block
# to sit on -- it answered a block-shaped system prompt with a 400.
CACHE_ANCHOR = "The race officer's conversation follows."

# The app's notes on an earlier turn -- what it looked up, which cards it drew --
# copied into a reply. They are for the interpreter to read, and a reply that
# began "[looked up suggest_course: target_minutes 20, boat J70]" was the note
# written out in place of the look-up it describes.
_COPIED_NOTE = re.compile(r"\[(?:App's note|looked up |cards shown)[^\]]*\]\s*")


def _without_notes(prose: str) -> str:
    return _COPIED_NOTE.sub("", prose or "").strip()


def _with_cache_markers(messages: list) -> list:
    """The conversation with two cache markers: after the part that never
    changes, and at the end of this request.

    Everything before the first marker -- the tools and the system prompt, some
    four thousand tokens -- is the same for every sentence anybody types, so it
    is read from the provider's cache rather than paid for and waited for again.
    The second matters within one turn: each round of looking something up sends
    everything before it again, facts and all, and the marker lets the next round
    read that back too. Through Cloudflare a cached call answered in 1.9 s where
    the same call uncached took 3.6.
    """
    if not messages:
        return messages
    marked = [dict(message) for message in messages]

    def blocks(message):
        content = message.get("content")
        if isinstance(content, str):
            return [{"type": "text", "text": content}]
        return [dict(block) for block in content or []]

    first = blocks(marked[0])
    marked[0]["content"] = [{"type": "text", "text": CACHE_ANCHOR,
                             "cache_control": {"type": "ephemeral"}}] + first
    last = blocks(marked[-1]) if len(marked) > 1 else marked[0]["content"]
    if last:
        last[-1] = dict(last[-1], cache_control={"type": "ephemeral"})
    marked[-1]["content"] = last
    return marked


def _tell(context: CommandContext, stage: str) -> None:
    """Say what is happening, if anybody asked to be told. Never lets a failure
    to report progress become a failure to answer."""
    if context.progress is None:
        return
    try:
        context.progress(stage)
    except Exception:
        pass


def _proposal(changes: list, model: str) -> Intent:
    """One change as itself; several as a plan, read back and agreed to once."""
    if len(changes) == 1:
        return Intent(changes[0]["name"], changes[0]["arguments"], source=model)
    return Intent(PLAN, {"steps": [{"name": call["name"], "arguments": call["arguments"]}
                                   for call in changes]}, source=model)


def make_model_parser(api_key: str, model: str = DEFAULT_MODEL, base_url: str = DEFAULT_BASE_URL,
                      timeout_s: float = DEFAULT_TIMEOUT_S,
                      transport: Optional[Callable[..., Optional[Dict[str, Any]]]] = None):
    """Build a parser of the same shape as `grammar_parse`.

    One sentence can take several calls. A tool that only reads is run by the
    caller (`context.read`) and what it found goes back to the model, which then
    answers from it: the answer to "how far is it from O to the Causeway?" is a
    distance, not the Causeway's latitude for somebody at the tiller to do the
    sums on -- which is what the page used to send, because a look-up went
    straight to the phone and the model never saw it. A tool that changes
    anything ends the turn at once, as a proposal. The model never sees the
    result of an action, because there is none until somebody says yes.

    `transport` exists so the tests can drive the whole path without a network
    or an account: it is the one seam between this module and the internet.
    """
    send = transport or _post_json

    dialect = dialect_for(base_url)

    def opening(text: str, context: CommandContext) -> list:
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
            if context.course_is_set and context.current_course_label:
                facts.append(f"Its course is {context.current_course_label}.")
            elif context.course_is_set and context.current_course_no:
                facts.append(f"Its course is {context.current_course_no}.")
            elif not context.course_is_set:
                facts.append("Its course has not been chosen yet.")
        # Everything else the app knows about right now: the wind, how many boats
        # are out, how far round they are. The rest is a look-up away.
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
        # has an antecedent. Each earlier turn is the sentence typed and what the
        # app then did or said.
        history: list = []
        for said_before, did_before in (context.recent or [])[-RECENT_TURNS:]:
            history.append({"role": "user", "content": said_before})
            history.append({"role": "assistant", "content": did_before})
        return history + [{"role": "user", "content": said}]

    def request(messages: list) -> Dict[str, Any]:
        if dialect != "anthropic":
            return _openai_payload(model, SYSTEM_PROMPT, messages)
        return {
            "model": model,
            "max_tokens": MAX_TOKENS,
            "system": SYSTEM_PROMPT,
            "tools": _tool_schema(),
            # "auto", not "any". Forcing a tool call contradicts the last line
            # of the system prompt and makes declining impossible: asked what I
            # thought of the cricket, a forced model picked the most harmless
            # tool it had and proposed a status report.
            "tool_choice": {"type": "auto"},
            "messages": _with_cache_markers(messages),
        }

    def parse(text: str, context: CommandContext) -> Optional[Intent]:
        if not api_key or not (text or "").strip():
            return None
        messages = opening(text, context)
        deadline = time.monotonic() + TURN_BUDGET_S
        # The last thing looked up, as an intent the page can show as it is. If
        # the provider fails part-way through, or the turn runs out of time,
        # that is still the app's own answer -- better than none.
        looked_up: Optional[Intent] = None
        for round_no in range(MAX_READ_ROUNDS + 1):
            remaining = deadline - time.monotonic()
            if remaining < 2.0:
                break
            _tell(context, "thinking" if round_no == 0 else "answering")
            body = send(base_url, request(messages), auth_headers(base_url, api_key),
                        min(timeout_s, remaining))
            if not body:
                return looked_up
            calls, prose, echo = (_anthropic_turn(body) if dialect == "anthropic"
                                  else _openai_turn(body))
            # A change wins over anything said or looked up alongside it: a model
            # that reads and acts in one reply has acted, and the change is what
            # the race officer has to agree to.
            changes = [call for call in calls if tool_kind(call["name"]) == WRITE]
            if changes:
                return _proposal(changes, model)
            if not calls:
                prose = _without_notes(prose)
                if prose:
                    return Intent(ANSWER, {"text": prose}, source=model)
                # A reply carrying neither a tool call nor a word is a failure,
                # and it is indistinguishable on the page from a sentence nobody
                # could read. Recorded so the notice says which -- "ran out of
                # room thinking" is a setting, not the model being stupid.
                _remember_error(f"answered with nothing usable ({_why_nothing(body)})")
                return looked_up
            first = Intent(calls[0]["name"], calls[0]["arguments"], source=model)
            if len(calls) == 1 and tool_kind(calls[0]["name"]) == REPORT:
                return first
            if context.read is None or round_no == MAX_READ_ROUNDS:
                return first
            looked_up = first
            results = []
            for call in calls:
                _tell(context, call["name"])
                context.looked_up.append({"tool": call["name"], "arguments": dict(call["arguments"])})
                try:
                    results.append((call, str(context.read(call["name"], dict(call["arguments"]))),
                                    False))
                except Exception as exc:  # one failed look-up must not lose the turn
                    results.append((call, f"The app could not look that up: {exc}", True))
            messages = messages + (_anthropic_results(echo, results) if dialect == "anthropic"
                                   else _openai_results(echo, results))
        return looked_up

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
