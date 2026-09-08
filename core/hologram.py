"""What the SIM in a tracker is doing, asked of Hologram rather than the tracker.

A tracker that has gone quiet mid-race looks the same from the app whatever the
cause: a flat battery, a boat behind a headland, or a SIM the network has
stopped carrying. Only the last of those is invisible from every other source we
have, and it is the one that will not fix itself — a SIM **paused by system**
has hit a usage limit or a low balance, and no amount of waiting brings it back.
That single distinction is why this module exists.

It answers two questions and deliberately no more:

* **Is this SIM able to carry data at all?** Hologram's state, reduced to
  "fine", "paused" or "not activated". Note that this describes the *SIM*, not
  the tracker: a live SIM proves nothing about whether the device is powered on,
  so a healthy answer here is not evidence of a healthy tracker. Only the
  unhealthy answers mean anything, which is how they are presented.
* **Which network is it on?** The club's SIM is multi-network, and half of every
  Teltonika parameter is live or dead depending on whether the device counts as
  roaming (see ``docs/TRACKERS.md``). Until now that was inferred. The carrier
  name and country come off the most recent data session, so it becomes
  something to read rather than assume — and a carrier that changes mid-race is
  the leading suspect for the degraded reporting spells.

The join needs no new identifier: Hologram indexes devices by IMEI, which is
exactly what the app already stores as a tracker's ``unique_id``.

Everything here fails soft. This is a second remote dependency on a page a race
officer opens on race morning, so a slow or broken Hologram must cost the page
nothing but the column: every function returns empty rather than raising, and
answers are cached so a page refresh does not become another round trip.
"""
from __future__ import annotations

import base64
import datetime
import hashlib
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Iterable, List, Optional

from core import horn

API_ROOT = "https://dashboard.hologram.io/api/1"

# Hologram is asked at most this often for the same thing.
#
# This started at 15 minutes on the reasoning that SIM state changes on the scale
# of hours. That was wrong in the way that matters: somebody pauses a SIM and
# then goes to the app to see whether that is why a tracker is silent, so the
# reading is wanted at exactly the moment it has just changed. Caught in use —
# a SIM paused in the Hologram dashboard still read "OK" here.
#
# Two minutes costs nothing now that refreshing happens off the render path: it
# is one call every two minutes, and only while somebody has the page open. The
# age is shown beside the column regardless, because a cached answer that does
# not say how old it is cannot be argued with.
CACHE_TTL_S = 120.0
# Short, because this sits in the path of a page render. Better a missing column
# than a race officer waiting on somebody else's server.
TIMEOUT_S = 6.0
# One page of devices is plenty for a club fleet, and bounds the response.
DEVICE_LIMIT = 500
# How much history the popout shows, as whole weeks back from today. Four is a
# month of race weekends, which is the span that answers "is this getting more
# expensive" without becoming a chart.
USAGE_WEEKS = 4
# Hologram counts a megabyte as a million bytes, not 2^20. Determined against
# the club's own dashboard rather than assumed: a SIM reading 7,965,927 bytes
# showed as "7.96 MB", which is the decimal division and not the binary one
# (that would be 7.60). Getting this wrong would overstate every cost by 5%.
BYTES_PER_MB = 1_000_000
# Hologram bills in US dollars. The API returns rates as bare numbers with no
# currency field anywhere, so this is the one part of the costing that is told
# to the app rather than read from it.
CURRENCY = "$"
# The Hologram dashboard. No per-SIM URL is documented, so the popouts link to
# the root; its search box takes an ICCID or an IMEI.
DASHBOARD_URL = "https://dashboard.hologram.io/"
# Hologram bills each SIM on its own 30-day cycle from activation, so the fleet's
# renewals are scattered rather than falling together on the 1st.
BILLING_PERIOD_DAYS = 30
# How far ahead the balance is projected, and when to start saying something.
FORECAST_DAYS = 180
TOPUP_URGENT_DAYS = 14
TOPUP_SOON_DAYS = 30


def _weeks_back(now: Optional[float] = None) -> List[Dict[str, Any]]:
    """The last ``USAGE_WEEKS`` seven-day windows, newest first.

    Whole weeks counted back from today rather than calendar weeks, so the
    newest bucket always ends now and "last week" means the seven days before
    that — which is how somebody comparing race weekends reads it.
    """
    today = datetime.datetime.fromtimestamp(now or time.time(),
                                            datetime.timezone.utc).date()
    out = []
    for i in range(USAGE_WEEKS):
        end = today - datetime.timedelta(days=7 * i)
        start = end - datetime.timedelta(days=6)
        # ISO strings, not date objects: this structure is handed to a template
        # and serialised into the page, and ISO dates still compare correctly.
        out.append({"start": start.isoformat(), "end": end.isoformat(),
                    "label": "This week" if i == 0 else
                             ("Last week" if i == 1 else f"{i} weeks ago"),
                    "bytes": 0, "cost": None})
    return out


def _apply_weekly_usage(cfg: Dict[str, Any], sims: Dict[str, Dict[str, Any]],
                        now: Optional[float] = None) -> None:
    """Fill each SIM's ``weeks`` from the daily usage records, in place.

    One call for the whole fleet's month. Daily records are already aggregated
    by Hologram, so this is about thirty rows per SIM rather than every session.
    """
    by_link = {str(s["link_id"]): s for s in sims.values() if s.get("link_id") not in (None, "")}
    for sim in sims.values():
        sim["weeks"] = _weeks_back(now)
    if not by_link:
        return
    first = datetime.date.fromisoformat(
        min(w["start"] for w in next(iter(sims.values()))["weeks"]))
    try:
        payload = _get(cfg, "/usage/data/daily", {
            "linkids": ",".join(by_link),
            "timestart": int(datetime.datetime.combine(
                first, datetime.time.min, datetime.timezone.utc).timestamp()),
            "timeend": int(now or time.time()),
            "limit": 5000,
            "orgid": cfg["org_id"],
        }, timeout=TIMEOUT_S)
    except Exception:
        return
    rows = payload.get("data") if isinstance(payload, dict) else payload
    for record in rows if isinstance(rows, list) else []:
        if not isinstance(record, dict):
            continue
        sim = by_link.get(str(record.get("linkid") or ""))
        if sim is None:
            continue
        day = str(record.get("date") or "")[:10]
        try:
            datetime.date.fromisoformat(day)
        except ValueError:
            continue
        for week in sim["weeks"]:
            if week["start"] <= day <= week["end"]:
                week["bytes"] += _int_or_none(record.get("bytes")) or 0
                break


def _apply_pricing(cfg: Dict[str, Any], sims: Dict[str, Dict[str, Any]]) -> None:
    """Put a rate against each SIM and cost its usage, in place.

    Separated from the usage call because it is the part a restricted key is
    most likely to be refused: the view-only Hologram role covers SIMs and data
    usage, and plan pricing may not be included. A refusal here must leave the
    byte counts standing and only empty the money column.
    """
    plan_ids = {str(s["plan_id"]) for s in sims.values() if s.get("plan_id") not in (None, "")}
    if not plan_ids:
        return
    try:
        payload = _get(cfg, "/plans/pricing", {"planids": ",".join(sorted(plan_ids)),
                                               "orgid": cfg["org_id"]})
    except Exception:
        return
    rows = payload.get("data") if isinstance(payload, dict) else payload
    rates: Dict[tuple, Dict[str, float]] = {}
    for entry in rows if isinstance(rows, list) else []:
        if not isinstance(entry, dict):
            continue
        try:
            rate = {"per_mb": float(entry.get("overage")),
                    "fee": float(entry.get("amount"))}
        except (TypeError, ValueError):
            continue
        rates[(str(entry.get("plan_id")), str(entry.get("zone") or ""))] = rate
        rates.setdefault((str(entry.get("plan_id")), ""), rate)

    for sim in sims.values():
        rate = (rates.get((str(sim["plan_id"]), sim["zone"]))
                or rates.get((str(sim["plan_id"]), "")))
        if not rate:
            continue
        sim["rate_per_mb"] = rate["per_mb"]
        sim["monthly_fee"] = rate["fee"]
        for week in sim["weeks"]:
            week["cost"] = week["bytes"] / BYTES_PER_MB * rate["per_mb"]
        # The month, as it would be charged: the recurring fee plus whatever
        # this billing period's data comes to beyond anything the plan includes.
        used = sim.get("period_bytes")
        if used is not None:
            billable = max(0, used - (sim.get("included_bytes") or 0))
            sim["month_cost"] = rate["fee"] + billable / BYTES_PER_MB * rate["per_mb"]


# Hologram's network states, reduced to what a race officer needs to act on.
# The full list is longer; anything not named here is reported as-is rather than
# quietly called healthy, because an unrecognised state is not a safe one.
_STATE_MEANING = {
    "LIVE": ("ok", "Live"),
    "PAUSED-USER": ("paused", "Paused by user"),
    "PAUSED-SYSTEM": ("paused", "Paused by system"),
    "INACTIVE": ("inactive", "Not activated"),
    "FROZEN": ("paused", "Frozen"),
    "DEACTIVATED": ("inactive", "Deactivated"),
}

# The colour and the one word the Trackers page shows, so the mapping lives with
# the states it describes rather than in a template. Amber for unknown rather
# than green: a state this module has not been taught is not evidence of health,
# and "Check" says exactly that without claiming a fault.
_RAG = {"ok": "green", "paused": "red", "inactive": "red", "unknown": "amber"}
_LABEL = {"ok": "OK", "paused": "Error", "inactive": "Error", "unknown": "Check"}

_cache_lock = threading.Lock()
_cache: Dict[str, Any] = {"at": 0.0, "key": "", "sims": {}, "error": "",
                          "fetching": False, "balance": {}}


def hologram_config() -> Dict[str, Any]:
    """Hologram settings, resolved like the rest of the hardware config."""
    cfg = horn.hardware_config()
    return {
        "enabled": bool(cfg.get("hologram_enabled", False)),
        "api_key": str(cfg.get("hologram_api_key", "") or "").strip(),
        # Optional. A key works across every organisation its owner belongs to,
        # so an account with more than one needs telling which. Blank asks for
        # all of them, which is right for the common case of exactly one.
        "org_id": str(cfg.get("hologram_org_id", "") or "").strip(),
    }


def hologram_active(cfg: Optional[Dict[str, Any]] = None) -> bool:
    """True when there is both permission and a key to go and ask."""
    cfg = cfg or hologram_config()
    return bool(cfg["enabled"] and cfg["api_key"])


def _get(cfg: Dict[str, Any], path: str, params: Optional[Dict[str, Any]] = None,
         timeout: float = TIMEOUT_S) -> Any:
    """GET a Hologram API path and return decoded JSON. Raises on error.

    Authentication is HTTP Basic with the literal username ``apikey`` and the
    key as the password — Hologram's own scheme, not a bearer token.
    """
    query = urllib.parse.urlencode({k: v for k, v in (params or {}).items()
                                    if v not in ("", None)})
    url = f"{API_ROOT}{path}" + (f"?{query}" if query else "")
    token = base64.b64encode(f"apikey:{cfg['api_key']}".encode("utf-8")).decode("ascii")
    req = urllib.request.Request(url, method="GET", headers={
        "Accept": "application/json",
        "Authorization": f"Basic {token}",
        "User-Agent": "Mozilla/5.0 (PwllheliRaceOfficer)",
    })
    with urllib.request.urlopen(req, timeout=timeout) as response:
        raw = response.read()
        return json.loads(raw.decode("utf-8", errors="replace")) if raw else None


def _int_or_none(value: Any) -> Optional[int]:
    """An integer, or None when the field is absent or not a number."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _known(value: Any) -> str:
    """A field's value, treating Hologram's literal "Unknown" as absent.

    It sends that string rather than omitting the field when it cannot resolve
    an IMEI, which rendered as "Unknown Unknown" in the popout where the row
    should simply not have appeared.
    """
    text = str(value or "").strip()
    return "" if text.lower() in ("", "unknown", "none", "null") else text


def _state_of(record: Dict[str, Any], link: Optional[Dict[str, Any]] = None) -> tuple:
    """``(health, wording)`` for a device's network state, mid-change included.

    Hologram shows a **Pausing** step before a SIM settles to *Paused by user*,
    and the field carrying it is not obvious: ``intended_network_state`` is the
    state asked for, the link's ``state`` is where it has got to, and a pending
    request may sit in ``async_change_requests``. Rather than bet on one, treat
    any of those disagreeing as a change in flight.

    A SIM on its way to paused is reported as paused. It is about to stop
    carrying data, and a page that says "OK" about a SIM being switched off is
    worse than one that is a minute early.
    """
    link = link or {}
    intended = str(record.get("intended_network_state") or "").strip().upper()
    actual = str(link.get("state") or "").strip().upper()
    pending = bool(record.get("async_change_requests") or link.get("async_change_requests"))
    settled = _STATE_MEANING.get(intended, ("unknown", intended.title() if intended else "Unknown"))
    if not (pending or (actual and intended and actual != intended)):
        return settled
    # In flight. Name it for where it is going, which is what the operator just
    # asked for and what will shortly be true.
    if settled[0] in ("paused", "inactive"):
        return (settled[0], "Pausing" if settled[0] == "paused" else settled[1])
    if _STATE_MEANING.get(actual, ("unknown",))[0] in ("paused", "inactive"):
        # Heading back to life but not there yet: still not carrying data.
        return ("paused", "Resuming")
    return settled


def _cellular_link(record: Dict[str, Any]) -> Dict[str, Any]:
    """The device's first cellular link, or an empty dict.

    A device can carry more than one profile; the club's carry one, and picking
    the first is honest for that. Written as a lookup rather than inline so the
    day a dual-profile SIM appears there is one place to change.
    """
    links = (record.get("links") or {}).get("cellular") or []
    for link in links:
        if isinstance(link, dict):
            return link
    return {}


def _parse_devices(payload: Any) -> Dict[str, Dict[str, Any]]:
    """Turn a /devices response into ``{imei: summary}``."""
    rows = payload.get("data") if isinstance(payload, dict) else payload
    out: Dict[str, Dict[str, Any]] = {}
    for record in rows if isinstance(rows, list) else []:
        if not isinstance(record, dict):
            continue
        imei = str(record.get("imei") or "").strip()
        if not imei:
            continue
        link = _cellular_link(record)
        plan = link.get("plan") if isinstance(link.get("plan"), dict) else {}
        health, wording = _state_of(record, link)
        out[imei] = {
            "imei": imei,
            "device_id": record.get("id"),
            "iccid": str(link.get("sim") or "").strip(),
            # Usage records identify a SIM by link id or SIM-card id, never by
            # ICCID, so both are kept purely to join the two calls together.
            "link_id": link.get("id"),
            "simcard_id": record.get("simcardid"),
            "health": health,
            # Hologram's dashboard wording, filled in below once the open
            # sessions are known. Left as the raw state until then, because
            # calling a SIM "Ready" without having checked would be a guess.
            "status_text": wording,
            "connected": None,
            "rag": _RAG.get(health, "amber"),
            "label": _LABEL.get(health, "Check"),
            "state_text": wording,
            "paused": health == "paused",
            # Cannot carry data, whichever way it got there. A paused SIM and an
            # unactivated one leave the tracker equally silent.
            "blocked": health in ("paused", "inactive"),
            # Hologram resolves these from the IMEI itself. Kept as a
            # cross-check on the app's own type-code map, never as a
            # replacement: a type code names whoever certified the radio, so
            # Hologram is guessing from exactly the same evidence we are.
            "manufacturer": _known(record.get("manufacturer")),
            "model": _known(record.get("model")),
            # For pricing: the rate belongs to the plan and the zone, and the
            # zone is on the link's own plan rather than beside it.
            "plan_id": plan.get("id", record.get("intended_plan_id")),
            "plan_name": str(plan.get("name") or "").strip(),
            "zone": str(plan.get("zone") or link.get("zone") or "").strip(),
            # Included data per billing period. Zero on a pay-as-you-go plan,
            # which is what the club is on, and then every byte is charged.
            "included_bytes": plan.get("data"),
            "rate_per_mb": None,
            "monthly_fee": None,
            # Hologram keeps the running totals itself, so the current and
            # previous billing periods cost no extra call.
            "period_bytes": _int_or_none(link.get("cur_billing_data_used")),
            # When the next monthly fee lands, and when the current one began;
            # filled in from the billing call.
            "period_end": "",
            "period_start": "",
            "days_in_period": None,
            "last_period_bytes": _int_or_none(link.get("last_billing_data_used")),
            "month_cost": None,
            # ``weeks`` is newest first: [{"label", "bytes", "cost"}, ...]
            "weeks": [],
            # The device's own record of where it last attached, which is more
            # current than scanning sessions and needs no second call. The
            # session below fills in the country and radio it cannot give.
            "network": _known(link.get("last_network_used")),
            "last_connect": str(link.get("last_connect_time") or "").strip(),
            "country": "",
            "rat": "",
            "last_session": None,
        }
    return out


def _apply_last_session(cfg: Dict[str, Any], sims: Dict[str, Dict[str, Any]],
                        since_s: float = 7 * 24 * 3600) -> None:
    """Add the carrier of each SIM's most recent data session, in place.

    The device record says whether a SIM *may* carry data; only a session says
    what it actually connected to. Failure here is not failure overall — the
    state column is the part that matters, so a usage call that does not answer
    leaves the network blank and nothing else.
    """
    by_link: Dict[str, Dict[str, Any]] = {}
    for sim in sims.values():
        for value in (sim.get("link_id"), sim.get("simcard_id")):
            if value not in (None, ""):
                by_link[str(value)] = sim
    if not by_link:
        return
    try:
        payload = _get(cfg, "/usage/data/", {
            "timestart": int(time.time() - since_s),
            "timeend": int(time.time()),
            "limit": 1000,
            "getcountryinfo": "true",
            "orgid": cfg["org_id"],
        }, timeout=TIMEOUT_S)
    except Exception:
        return
    rows = payload.get("data") if isinstance(payload, dict) else payload
    # Oldest first, so the last write for a SIM is its most recent session.
    records = [r for r in (rows if isinstance(rows, list) else []) if isinstance(r, dict)]
    records.sort(key=lambda r: r.get("session_begin") or r.get("timestamp") or "")
    for record in records:
        sim = (by_link.get(str(record.get("linkid") or ""))
               or by_link.get(str(record.get("simcardid") or "")))
        if sim is None:
            continue
        sim["session_network"] = str(record.get("network_name") or "").strip()
        sim["country"] = str(record.get("country") or "").strip()
        sim["rat"] = str(record.get("radio_access_technology") or "").strip()
        sim["last_session"] = record.get("session_begin") or record.get("timestamp")
    # The device's own ``last_network_used`` is more current than a session scan,
    # so it wins; the sessions are the fallback and supply country and radio
    # either way.
    for sim in sims.values():
        sim["network"] = sim["network"] or sim.pop("session_network", "")
        sim.pop("session_network", None)


def _fetch(cfg: Dict[str, Any], key: str) -> None:
    """Ask Hologram and replace the cache. Never raises — it runs on a thread."""
    sims: Dict[str, Dict[str, Any]] = {}
    balance: Dict[str, Any] = {}
    error = ""
    try:
        sims = _parse_devices(_get(cfg, "/devices", {"limit": DEVICE_LIMIT,
                                                     "orgid": cfg["org_id"]}))
        _apply_last_session(cfg, sims)
        _apply_weekly_usage(cfg, sims)
        _apply_period_end(cfg, sims)
        _apply_open_sessions(cfg, sims)
        _apply_pricing(cfg, sims)
        balance = _fetch_balance(cfg)
    except urllib.error.HTTPError as exc:
        error = (f"Hologram refused the request ({exc.code}). "
                 "Check the API key and the organisation." if exc.code in (401, 403)
                 else f"Hologram returned HTTP {exc.code}.")
    except Exception as exc:
        error = f"Could not reach Hologram: {str(exc)[:120]}"
    with _cache_lock:
        # A failure replaces the previous answer rather than leaving it in place.
        # Showing a carrier from an hour ago as though it were current is worse
        # than showing nothing, because the whole point is catching a change.
        _cache.update({"at": time.time(), "key": key, "sims": sims, "error": error,
                       "fetching": False, "balance": balance})


def _apply_period_end(cfg: Dict[str, Any], sims: Dict[str, Dict[str, Any]]) -> None:
    """When each SIM's billing period next rolls over, in place.

    Each SIM runs its own 30-day cycle from the day it was activated, so the
    fleet's monthly fees fall on scattered dates rather than together. Knowing
    which and when is the difference between "the balance looks fine" and "four
    fees land next Friday".
    """
    by_link = {str(s["link_id"]): s for s in sims.values()
               if s.get("link_id") not in (None, "")}
    if not by_link:
        return
    try:
        payload = _get(cfg, "/usage/data/billing", {"limit": 5000, "maxperiodsback": 1,
                                                    "orgid": cfg["org_id"]})
    except Exception:
        return
    rows = payload.get("data") if isinstance(payload, dict) else payload
    for record in rows if isinstance(rows, list) else []:
        if not isinstance(record, dict) or str(record.get("periods_back") or "0") != "0":
            continue
        sim = by_link.get(str(record.get("linkid") or ""))
        if sim is None:
            continue
        sim["period_end"] = str(record.get("cur_period_end") or "").strip()
        # How far into its own period this SIM is. Without it the usage figures
        # are not comparable with each other at all: a SIM one day into its
        # cycle and one sixteen days in are both labelled "this period".
        try:
            end = datetime.date.fromisoformat(sim["period_end"][:10])
        except ValueError:
            continue
        start = end - datetime.timedelta(days=BILLING_PERIOD_DAYS)
        sim["period_start"] = start.isoformat()
        sim["days_in_period"] = max(
            0, (datetime.datetime.now(datetime.timezone.utc).date() - start).days)


def _fetch_balance(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """The organisation's prepay balance, or an empty dict.

    Needs Hologram's ``billing_visible`` permission, which a view-only role may
    well not carry, so this is its own call with its own failure: losing the
    balance must not lose the SIM states beside it.
    """
    if not cfg["org_id"]:
        # The endpoint is per organisation and has no fleet-wide form, so with
        # no id there is nothing to ask for.
        return {}
    try:
        payload = _get(cfg, "/organizations/%s/balance/" % cfg["org_id"], {})
    except Exception:
        return {}
    data = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(data, dict):
        return {}

    def money(key):
        try:
            return float(data.get(key))
        except (TypeError, ValueError):
            return None

    return {"balance": money("balance"), "promo": money("promobalance"),
            "min_balance": money("minbalance"), "topoff": money("topoffamount"),
            "pending": money("pendingcharges"),
            "postpay": bool(data.get("has_postpay"))}


def _apply_open_sessions(cfg: Dict[str, Any], sims: Dict[str, Dict[str, Any]]) -> None:
    """Say **Ready** or **Connected**, the way Hologram's own dashboard does.

    Both are ``LIVE`` on the device record — the difference is whether a data
    session is open at this moment, which only ``/devices/opensessions`` knows.
    It is worth showing because it is not noise: the club's Queclinks hold a
    long-lived connection and read *Connected*, while the ATC700s sleep between
    reports and read *Ready*. Someone comparing this page against the Hologram
    dashboard should not have to translate.

    A failure leaves the raw state showing rather than guessing at one of the
    two, since guessing wrong here would misdescribe a healthy tracker.
    """
    try:
        payload = _get(cfg, "/devices/opensessions", {"orgid": cfg["org_id"]})
    except Exception:
        return
    data = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(data, dict):
        return
    open_ids = {str(i) for i in (data.get("deviceids") or []) if i is not None}
    for sim in sims.values():
        if sim.get("health") != "ok":
            continue                    # paused and inactive already say enough
        sim["connected"] = str(sim.get("device_id")) in open_ids
        sim["status_text"] = "Connected" if sim["connected"] else "Ready"


def forecast_balance(sims: Dict[str, Dict[str, Any]], balance: Dict[str, Any],
                     now: Optional[float] = None) -> Dict[str, Any]:
    """When the prepay balance runs out, walked forward a day at a time.

    Two things drain it and they behave differently. **Data** trickles out every
    day, and the recent daily average is the only guide there is. **Fees** land
    in lumps on each SIM's own renewal date. That is why this simulates days
    rather than dividing the balance by a monthly figure: the club's SIMs renew
    in two clusters a fortnight apart, and an average would hide both.

    Returns nothing when there is nothing to go on, because a guess presented as
    a date is worse than saying nothing.
    """
    money = balance.get("balance")
    if money is None:
        return {}
    priced = [s for s in sims.values() if s.get("rate_per_mb") is not None]
    if not priced:
        return {}
    today = datetime.datetime.fromtimestamp(now or time.time(),
                                            datetime.timezone.utc).date()

    # Daily data spend, from the last seven days across the whole fleet: short
    # enough to reflect a change in how the trackers are used, long enough to
    # cover a race weekend.
    week_bytes = sum((s["weeks"][0]["bytes"] if s.get("weeks") else 0) for s in priced)
    rate = priced[0]["rate_per_mb"]
    daily_cost = week_bytes / 7.0 / BYTES_PER_MB * rate

    renewals = []
    for sim in priced:
        end = str(sim.get("period_end") or "")[:10]
        fee = sim.get("monthly_fee")
        if not end or not fee:
            continue
        try:
            when = datetime.date.fromisoformat(end)
        except ValueError:
            continue
        while when < today:
            when += datetime.timedelta(days=BILLING_PERIOD_DAYS)
        renewals.append([when, float(fee)])
    if not renewals and daily_cost <= 0:
        return {}

    # Read before the walk below, which advances each date as it consumes it.
    next_renewal = min((r[0] for r in renewals), default=None)

    running = float(money) + float(balance.get("promo") or 0.0)
    low_mark = float(balance.get("min_balance") or 0.0)
    below_min_on = None
    empty_on = None
    for offset in range(FORECAST_DAYS + 1):
        day = today + datetime.timedelta(days=offset)
        if offset:
            running -= daily_cost
        for renewal in renewals:
            while renewal[0] == day:
                running -= renewal[1]
                renewal[0] += datetime.timedelta(days=BILLING_PERIOD_DAYS)
        if below_min_on is None and low_mark and running < low_mark:
            below_min_on = day
        if running <= 0:
            empty_on = day
            break

    return {
        "daily_cost": daily_cost,
        "monthly_fees": sum(r[1] for r in renewals),
        "sims_priced": len(renewals),
        "empty_on": empty_on.isoformat() if empty_on else "",
        "below_min_on": below_min_on.isoformat() if below_min_on else "",
        "days_left": (empty_on - today).days if empty_on else None,
        # Past the horizon there is no useful answer, and quoting the horizon
        # itself would imply a precision a seven-day average does not have.
        "beyond_horizon": empty_on is None,
        "next_renewal": next_renewal.isoformat() if next_renewal else "",
    }


def account() -> Dict[str, Any]:
    """Balance and the whole fleet's costs, for the summary popout. Never raises."""
    try:
        status = sim_status()
        with _cache_lock:
            balance = dict(_cache.get("balance") or {})
        sims = status["sims"]
        priced = [s for s in sims.values() if s.get("month_cost") is not None]
        totals = {
            "sims": len(sims),
            "period_bytes": sum(s.get("period_bytes") or 0 for s in sims.values()),
            "fees": sum(s.get("monthly_fee") or 0 for s in priced),
            "data_cost": sum((s.get("period_bytes") or 0) / BYTES_PER_MB
                             * (s.get("rate_per_mb") or 0) for s in priced),
            # How many SIMs the fees line covers, so the reader can check it.
            "priced": len(priced),
            # A figure that is actually comparable. Every "this period" number
            # above covers a different window, because each SIM bills from its
            # own activation date — right now the club's range from one day into
            # a cycle to sixteen. The last four whole weeks are the same window
            # for every SIM, so this is the one to watch over time.
            "recent_bytes": sum(w["bytes"] for s in sims.values()
                                for w in (s.get("weeks") or [])),
            "recent_days": USAGE_WEEKS * 7,
        }
        totals["recent_cost"] = sum(
            sum(w["bytes"] for w in (s.get("weeks") or [])) / BYTES_PER_MB
            * (s.get("rate_per_mb") or 0) for s in priced)
        totals["month_cost"] = totals["fees"] + totals["data_cost"]
        return {"sims": sims, "totals": totals, "balance": balance,
                "forecast": forecast_balance(sims, balance),
                "error": status["error"], "age_s": status["age_s"],
                "dashboard_url": DASHBOARD_URL}
    except Exception:
        return {"sims": {}, "totals": {}, "balance": {}, "forecast": {},
                "error": "", "age_s": 0.0, "dashboard_url": DASHBOARD_URL}


def balance_warning() -> Optional[Dict[str, Any]]:
    """A top-up warning for the dashboard, or None. Never raises.

    Silent when the account is postpay (no balance to run out), when Hologram
    will not say what the balance is, and when there is comfortably more than a
    month of it left.
    """
    try:
        if not hologram_active():
            return None
        info = account()
        balance, forecast = info["balance"], info["forecast"]
        if not balance or balance.get("postpay") or not forecast:
            return None
        days = forecast.get("days_left")
        if days is None:
            return None
        if days <= TOPUP_URGENT_DAYS:
            level = "urgent"
        elif days <= TOPUP_SOON_DAYS:
            level = "soon"
        else:
            return None
        return {"level": level, "days_left": days,
                "balance": balance.get("balance"),
                "empty_on": forecast.get("empty_on"),
                "auto_topoff": bool(balance.get("topoff"))}
    except Exception:
        return None


def sim_status(imeis: Optional[Iterable[str]] = None,
               cfg: Optional[Dict[str, Any]] = None,
               blocking: bool = False) -> Dict[str, Any]:
    """``{"sims": {imei: summary}, "error": str, "age_s": float}``. Never raises.

    **Returns immediately with whatever is already known**, and refreshes on a
    background thread when that has gone stale. A page render must never wait on
    somebody else's server: two calls at a six-second timeout is twelve seconds
    of a race officer's morning, which is far worse than a column that is a few
    minutes behind. The cost is that the very first load after start-up shows
    nothing and the next one is populated.

    ``imeis`` filters the answer but not the request: one call fetches the whole
    fleet, because asking per tracker would turn one page render into thirty
    round trips. ``blocking`` waits for the answer, which is for tests and for
    callers that are not rendering anything.
    """
    cfg = cfg or hologram_config()
    if not hologram_active(cfg):
        return {"sims": {}, "error": "", "age_s": 0.0}
    # The credentials are part of the cache identity, so changing either in
    # Settings takes effect at once rather than after the TTL — hashed, because
    # an in-memory structure should not hold the key itself.
    key = hashlib.sha256(f"{cfg['api_key']}:{cfg['org_id']}".encode()).hexdigest()[:16]
    with _cache_lock:
        same = _cache["key"] == key
        stale = (not same) or _cache["at"] <= 0 or (time.time() - _cache["at"]) >= CACHE_TTL_S
        if not same:
            # Different credentials: the old answer is not this account's, so it
            # is dropped rather than served while the new one is fetched.
            _cache.update({"at": 0.0, "key": key, "sims": {}, "error": ""})
        start = stale and not _cache.get("fetching")
        if start:
            _cache["fetching"] = True
        sims, error, at = dict(_cache["sims"]), _cache["error"], _cache["at"]
    if start:
        if blocking:
            _fetch(cfg, key)
            with _cache_lock:
                sims, error, at = dict(_cache["sims"]), _cache["error"], _cache["at"]
        else:
            threading.Thread(target=_fetch, args=(cfg, key), daemon=True,
                             name="hologram-refresh").start()
    if imeis is not None:
        wanted = {str(i).strip() for i in imeis}
        sims = {k: v for k, v in sims.items() if k in wanted}
    return {"sims": sims, "error": error,
            "age_s": max(0.0, time.time() - at) if at > 0 else 0.0}


def paused_sims(status: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """The SIMs that cannot carry data, which is the whole point of asking.

    A blocked SIM explains a silent tracker completely and will not recover on
    its own, so it is worth saying out loud rather than leaving in a column.
    """
    status = status if status is not None else sim_status()
    return [s for s in status["sims"].values() if s.get("blocked")]


def sim_warnings() -> List[Dict[str, Any]]:
    """Blocked SIMs in trackers that are on a boat, for the dashboard.

    Deliberately the same rule as the low-battery card beside it: **only
    trackers assigned to a boat**. A spare in a drawer with a paused SIM is a
    job for another day; a boat that is about to race and cannot report is the
    one worth interrupting somebody about. Named by boat, because a warning that
    gives only an IMEI makes the reader go and look it up.

    Never raises and returns nothing at all when the feature is off, so the
    dashboard cannot be broken by it.
    """
    try:
        if not hologram_active():
            return []
        # Imported here rather than at module scope: core.track is a far larger
        # module and importing it eagerly would tie the two together for no
        # reason. Same pattern core.track itself uses for core.races.
        from core.races import get_boats_by_id
        from core.track import list_trackers

        blocked = {s["imei"]: s for s in paused_sims()}
        if not blocked:
            return []
        trackers = [t for t in list_trackers()
                    if t.get("boat_id") and str(t.get("unique_id")) in blocked]
        boats = get_boats_by_id([t.get("boat_id") for t in trackers])
        out = []
        for tk in trackers:
            uid = str(tk.get("unique_id"))
            boat = boats.get(tk.get("boat_id")) or {}
            name = ""
            try:
                name = boat["boat_name"] or ""
            except (KeyError, IndexError, TypeError):
                name = ""
            out.append({"unique_id": uid, "label": tk.get("label") or uid,
                        "boat_name": name, "state_text": blocked[uid]["state_text"]})
        out.sort(key=lambda r: (r["boat_name"] or r["label"]).lower())
        return out
    except Exception:
        return []


def invalidate_cache() -> None:
    """Forget the cached answer — used when the settings change."""
    with _cache_lock:
        _cache.update({"at": 0.0, "key": "", "sims": {}, "error": "",
                       "fetching": False, "balance": {}})
