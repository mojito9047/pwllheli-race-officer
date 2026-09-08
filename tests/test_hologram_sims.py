"""Asking the SIM provider what a silent tracker's SIM is doing.

A tracker that stops reporting mid-race looks identical from the app whatever
the cause. Almost every cause resolves itself — a boat comes out from behind a
headland, a battery is charged overnight. One does not: a SIM **paused by
system** has hit a usage limit or a low balance, and it will stay silent until
somebody logs in to Hologram. That is the case these tests are really about, and
why the paused ones are lifted out of the table into a warning of their own.

The second reason is the roaming trap. The club's SIM is multi-network, and half
of every Teltonika parameter is live or dead depending on whether the device
counts as roaming, so which carrier a tracker is actually on stopped being a
detail the moment that was discovered.

The rest is defensive. This is a second remote dependency in the render path of
a page a race officer opens on race morning, so most of what follows checks that
Hologram being off, slow, broken or lying costs the page nothing but a column.
"""
from __future__ import annotations

import threading
import time
import urllib.error

import pytest

import app as ro
from core import hologram

# Captured before any test monkeypatches it.
_orig_weeks = hologram._weeks_back


# The warning box's own wording. Matched exactly because the popout's help
# text says something deliberately similar, and a loose match would pass on the
# static script rather than on a SIM actually being paused.
WARNING = "stay silent until the SIM is resumed at Hologram"

GL = "864864070498856"
ATC = "862129082306832"


def _device(imei, state="LIVE", link_id=11, simcard_id=21, iccid="8944…01"):
    return {
        "id": 900 + link_id, "imei": imei, "intended_network_state": state,
        "simcardid": simcard_id, "manufacturer": "Queclink", "model": "GL521MG",
        "links": {"cellular": [{"id": link_id, "sim": iccid, "state": state}]},
    }


def _cfg(**over):
    cfg = {"enabled": True, "api_key": "k-secret-value", "org_id": ""}
    cfg.update(over)
    return cfg


@pytest.fixture(autouse=True)
def _clean_cache():
    """Every test starts with nothing remembered from the last one."""
    hologram.invalidate_cache()
    yield
    hologram.invalidate_cache()


def _serve(monkeypatch, devices=None, usage=None, fail=None):
    """Stand in for Hologram. Records the paths asked for."""
    asked = []

    def fake(cfg, path, params=None, timeout=hologram.TIMEOUT_S):
        asked.append((path, dict(params or {})))
        if fail is not None:
            raise fail
        if path == "/devices":
            return {"success": True, "data": devices or []}
        if path == "/usage/data/":
            return {"success": True, "data": usage or []}
        raise AssertionError(f"unexpected path {path}")

    monkeypatch.setattr(hologram, "_get", fake)
    return asked


class TestTheCaseItExistsFor:
    def test_a_system_paused_sim_is_singled_out(self, monkeypatch):
        """The one failure that never recovers on its own, and the only one
        invisible from every other source the app has."""
        _serve(monkeypatch, [_device(GL, "PAUSED-SYSTEM")])
        status = hologram.sim_status(blocking=True, cfg=_cfg())
        assert status["sims"][GL]["paused"] is True
        assert status["sims"][GL]["state_text"] == "Paused by system"
        assert [s["imei"] for s in hologram.paused_sims(status)] == [GL]

    def test_a_user_paused_sim_counts_too(self, monkeypatch):
        """Somebody paused it deliberately, possibly months ago. The tracker is
        just as silent either way."""
        _serve(monkeypatch, [_device(GL, "PAUSED-USER")])
        assert hologram.paused_sims(hologram.sim_status(blocking=True, cfg=_cfg()))

    def test_a_live_sim_is_not_a_warning(self, monkeypatch):
        _serve(monkeypatch, [_device(GL, "LIVE")])
        status = hologram.sim_status(blocking=True, cfg=_cfg())
        assert status["sims"][GL]["health"] == "ok"
        assert hologram.paused_sims(status) == []

    def test_each_state_carries_its_colour_and_its_one_word(self, monkeypatch):
        """The row shows a dot and a single word, so the mapping belongs here
        beside the states rather than in a template. An unrecognised state reads
        "Check" in amber: not a claimed fault, and not a clean bill either."""
        for state, rag, label in [("LIVE", "green", "OK"),
                                  ("PAUSED-SYSTEM", "red", "Error"),
                                  ("INACTIVE", "red", "Error"),
                                  ("WHO-KNOWS", "amber", "Check")]:
            hologram.invalidate_cache()
            _serve(monkeypatch, [_device(GL, state)])
            sim = hologram.sim_status(blocking=True, cfg=_cfg())["sims"][GL]
            assert (sim["rag"], sim["label"]) == (rag, label), state

    def test_an_unrecognised_state_is_not_quietly_called_healthy(self, monkeypatch):
        """Hologram's state list is longer than the one mapped here and can grow.
        Reporting an unknown state as fine would hide exactly the fault this is
        for, so it is passed through as itself instead."""
        _serve(monkeypatch, [_device(GL, "SOME-NEW-STATE")])
        sim = hologram.sim_status(blocking=True, cfg=_cfg())["sims"][GL]
        assert sim["health"] == "unknown"
        assert sim["paused"] is False           # not claimed as a fault either
        assert "New-State" in sim["state_text"]


class TestTheStatusHologramItselfShows:
    """Hologram's dashboard says **Ready** or **Connected**, not "Live".

    Both are ``LIVE`` on the device record; the difference is whether a data
    session is open at that moment, which only /devices/opensessions knows. It
    is a real distinction on this fleet — the Queclinks hold a long-lived
    connection while the ATC700s sleep between reports — and somebody comparing
    this page against the Hologram dashboard should not have to translate.
    """

    def _with_sessions(self, monkeypatch, open_ids, state="LIVE"):
        def fake(cfg, path, params=None, timeout=None):
            if path == "/devices":
                d = _device(GL, state)
                d["id"] = 5270908
                return {"data": [d]}
            if path == "/devices/opensessions":
                return {"data": {"deviceids": open_ids}}
            return {"data": []}
        monkeypatch.setattr(hologram, "_get", fake)
        return hologram.sim_status(blocking=True, cfg=_cfg())["sims"][GL]

    def test_an_open_session_reads_connected(self, monkeypatch):
        sim = self._with_sessions(monkeypatch, [5270908])
        assert sim["status_text"] == "Connected" and sim["connected"] is True

    def test_no_open_session_reads_ready(self, monkeypatch):
        sim = self._with_sessions(monkeypatch, [999])
        assert sim["status_text"] == "Ready" and sim["connected"] is False

    def test_both_are_still_healthy(self, monkeypatch):
        """Ready is not a fault. A sleeping ATC700 shows it every cycle."""
        for ids in ([5270908], []):
            hologram.invalidate_cache()
            sim = self._with_sessions(monkeypatch, ids)
            assert sim["health"] == "ok" and sim["label"] == "OK"

    def test_a_paused_sim_keeps_its_own_wording(self, monkeypatch):
        """Ready/Connected only describes a live SIM. A paused one has something
        more important to say."""
        sim = self._with_sessions(monkeypatch, [], state="PAUSED-USER")
        assert sim["status_text"] == "Paused by user" and sim["paused"] is True

    def test_a_failed_lookup_does_not_guess(self, monkeypatch):
        """Calling a SIM Ready without having checked would misdescribe a
        perfectly healthy tracker, so the raw state stands instead."""
        def fake(cfg, path, params=None, timeout=None):
            if path == "/devices":
                return {"data": [_device(GL)]}
            if path == "/devices/opensessions":
                raise urllib.error.URLError("down")
            return {"data": []}
        monkeypatch.setattr(hologram, "_get", fake)
        sim = hologram.sim_status(blocking=True, cfg=_cfg())["sims"][GL]
        assert sim["status_text"] == "Live" and sim["connected"] is None

    def test_a_shape_we_did_not_expect_is_survived(self, monkeypatch):
        def fake(cfg, path, params=None, timeout=None):
            if path == "/devices":
                return {"data": [_device(GL)]}
            if path == "/devices/opensessions":
                return {"data": "not a dict"}
            return {"data": []}
        monkeypatch.setattr(hologram, "_get", fake)
        assert hologram.sim_status(blocking=True, cfg=_cfg())["sims"][GL]["status_text"] == "Live"


class TestASimMidChange:
    """Hologram shows **Pausing** before a SIM settles to *Paused by user*.

    Seen on the club's dashboard and gone by the time the API was asked, so
    which field carries it could not be observed directly: ``intended_network_
    state`` is what was asked for, the link's ``state`` is where it has got to,
    and a pending request may sit in ``async_change_requests``. Rather than bet
    on one, any of them disagreeing counts as a change in flight — so whichever
    Hologram actually uses, the app notices.

    A SIM on its way to paused is reported as paused. It is about to stop
    carrying data, and saying "OK" about a SIM being switched off is worse than
    being a minute early.
    """

    def _dev(self, intended, actual=None, pending=False):
        d = _device(GL, intended)
        d["links"]["cellular"][0]["state"] = actual if actual is not None else intended
        if pending:
            d["async_change_requests"] = [{"type": "state", "to": intended}]
        return d

    def _status(self, monkeypatch, device):
        monkeypatch.setattr(hologram, "_get", lambda cfg, path, params=None, timeout=None:
                            {"data": [device]} if path == "/devices" else {"data": []})
        return hologram.sim_status(blocking=True, cfg=_cfg())["sims"][GL]

    def test_the_intent_arriving_first_reads_pausing(self, monkeypatch):
        """Intent flipped, the link has not caught up."""
        sim = self._status(monkeypatch, self._dev("PAUSED-USER", actual="LIVE"))
        assert sim["state_text"] == "Pausing"
        assert sim["paused"] is True and sim["label"] == "Error"

    def test_a_pending_request_reads_pausing_too(self, monkeypatch):
        """The other place it might live, covered the same way."""
        sim = self._status(monkeypatch, self._dev("PAUSED-USER", pending=True))
        assert sim["state_text"] == "Pausing" and sim["blocked"] is True

    def test_coming_back_is_not_yet_working(self, monkeypatch):
        """Resuming is still not carrying data, so it is not called OK."""
        sim = self._status(monkeypatch, self._dev("LIVE", actual="PAUSED-USER"))
        assert sim["state_text"] == "Resuming" and sim["health"] == "paused"

    def test_a_settled_sim_is_unaffected(self, monkeypatch):
        """The ordinary case must not be dragged into the transitional path."""
        sim = self._status(monkeypatch, self._dev("LIVE"))
        assert sim["state_text"] == "Live" and sim["health"] == "ok"

    def test_a_settled_pause_still_names_who_did_it(self, monkeypatch):
        """Once it lands, "Paused by user" is more useful than "Pausing" —
        it says whether somebody chose this or a limit did."""
        sim = self._status(monkeypatch, self._dev("PAUSED-SYSTEM"))
        assert sim["state_text"] == "Paused by system"

    def test_a_device_with_no_link_state_is_not_called_transitional(self, monkeypatch):
        """An absent field is not a disagreement."""
        d = _device(GL, "LIVE")
        d["links"]["cellular"][0].pop("state", None)
        assert self._status(monkeypatch, d)["health"] == "ok"

    def test_a_pausing_sim_reaches_the_dashboard(self, client, monkeypatch):
        """The point of treating it as paused: it is about to go silent, and the
        warning is worth having before it does rather than after."""
        from core import horn, track
        horn.save_hardware_config({"hologram_enabled": "1", "hologram_api_key": "abc"})
        track.upsert_tracker(GL, label="Tracker A", boat_id=1)
        hologram.invalidate_cache()
        monkeypatch.setattr(hologram, "_get", lambda cfg, path, params=None, timeout=None:
                            {"data": [self._dev("PAUSED-USER", actual="LIVE")]}
                            if path == "/devices" else {"data": []})
        hologram.sim_status(blocking=True)
        assert [w["state_text"] for w in hologram.sim_warnings()] == ["Pausing"]


class TestWhichNetworkItIsOn:
    def test_the_carrier_comes_from_the_most_recent_session(self, monkeypatch):
        """Which carrier decides whether a Teltonika's home or roaming half of
        every parameter pair is the live one."""
        _serve(monkeypatch, [_device(GL, link_id=11, simcard_id=21)], usage=[
            {"linkid": 11, "session_begin": "2026-08-19T08:00:00Z",
             "network_name": "Vodafone UK", "country": "United Kingdom",
             "radio_access_technology": "LTE"},
            {"linkid": 11, "session_begin": "2026-08-21T08:00:00Z",
             "network_name": "O2 UK", "country": "United Kingdom",
             "radio_access_technology": "LTE-M"},
        ])
        sim = hologram.sim_status(blocking=True, cfg=_cfg())["sims"][GL]
        assert sim["network"] == "O2 UK"        # newest wins, not last in the list
        assert sim["rat"] == "LTE-M"

    def test_sessions_are_matched_by_link_id_not_iccid(self, monkeypatch):
        """Usage records identify a SIM by link or SIM-card id and never carry an
        ICCID, so joining on ICCID would silently leave every network blank."""
        _serve(monkeypatch, [_device(GL, link_id=77, simcard_id=88)],
               usage=[{"simcardid": 88, "session_begin": "2026-08-21T08:00:00Z",
                       "network_name": "EE"}])
        assert hologram.sim_status(blocking=True, cfg=_cfg())["sims"][GL]["network"] == "EE"

    def test_a_session_for_a_stranger_is_ignored(self, monkeypatch):
        _serve(monkeypatch, [_device(GL, link_id=11)],
               usage=[{"linkid": 999, "network_name": "Three"}])
        assert hologram.sim_status(blocking=True, cfg=_cfg())["sims"][GL]["network"] == ""

    def test_usage_failing_does_not_lose_the_states(self, monkeypatch):
        """The state column is the part that matters. A usage call that dies
        must cost the carrier name and nothing else."""
        def fake(cfg, path, params=None, timeout=hologram.TIMEOUT_S):
            if path == "/devices":
                return {"data": [_device(GL, "PAUSED-SYSTEM")]}
            raise urllib.error.URLError("usage is down")
        monkeypatch.setattr(hologram, "_get", fake)
        sim = hologram.sim_status(blocking=True, cfg=_cfg())["sims"][GL]
        assert sim["paused"] is True and sim["network"] == ""


class TestDataAndWhatItCosts:
    """Money is the one thing here that is computed rather than read, so it is
    the one thing that can be quietly wrong. Hologram returns no cost on any
    usage endpoint — only bytes, a per-MB rate and a recurring fee — so every
    figure the popout shows is arithmetic done in this module.
    """

    # 2026-08-21 12:00Z, so "this week" is 15–21 August.
    NOW = 1787313600.0

    def _priced(self, monkeypatch, daily, plan_data=0):
        def fake(cfg, path, params=None, timeout=None):
            if path == "/devices":
                d = _device(GL, link_id=11)
                d["links"]["cellular"][0]["plan"] = {
                    "id": 2096, "zone": "global", "name": "Global G3", "data": plan_data}
                d["links"]["cellular"][0]["cur_billing_data_used"] = sum(
                    r["bytes"] for r in daily)
                d["intended_plan_id"] = 2096
                return {"data": [d]}
            if path == "/usage/data/daily":
                return {"data": [dict(r, linkid=11) for r in daily]}
            if path == "/plans/pricing":
                return {"data": [{"plan_id": 2096, "zone": "global",
                                  "amount": "1.000000", "overage": "0.030000"}]}
            return {"data": []}
        monkeypatch.setattr(hologram, "_get", fake)
        monkeypatch.setattr(hologram, "_weeks_back",
                            lambda now=None: _orig_weeks(self.NOW))
        return hologram.sim_status(blocking=True, cfg=_cfg())["sims"][GL]

    def test_a_megabyte_is_a_million_bytes(self):
        """Determined against the club's dashboard, not assumed: 7,965,927 bytes
        displayed there as "7.96 MB", which is the decimal division. The binary
        one gives 7.60, and using it would overstate every cost by 5%."""
        assert hologram.BYTES_PER_MB == 1_000_000
        assert round(7965927 / hologram.BYTES_PER_MB, 2) == 7.97

    def test_usage_lands_in_the_right_week(self, monkeypatch):
        sim = self._priced(monkeypatch, [
            {"date": "2026-08-20", "bytes": 3_000_000},   # this week
            {"date": "2026-08-15", "bytes": 1_000_000},   # this week, first day
            {"date": "2026-08-14", "bytes": 2_000_000},   # last week, last day
            {"date": "2026-07-25", "bytes": 5_000_000},   # three weeks ago
        ])
        got = {w["label"]: w["bytes"] for w in sim["weeks"]}
        assert got["This week"] == 4_000_000
        assert got["Last week"] == 2_000_000
        assert got["3 weeks ago"] == 5_000_000
        assert got["2 weeks ago"] == 0

    def test_a_week_is_costed_at_the_plan_rate(self, monkeypatch):
        sim = self._priced(monkeypatch, [{"date": "2026-08-20", "bytes": 10_000_000}])
        week = sim["weeks"][0]
        assert week["bytes"] == 10_000_000
        assert round(week["cost"], 4) == round(10 * 0.03, 4)      # 10 MB at $0.03

    def test_the_month_is_the_fee_plus_the_data(self, monkeypatch):
        """What the user asked for: a month total that includes the standing
        charge, not just the traffic."""
        sim = self._priced(monkeypatch, [{"date": "2026-08-20", "bytes": 6_286_241}])
        assert round(sim["month_cost"], 2) == round(1.00 + 6.286241 * 0.03, 2)
        assert sim["monthly_fee"] == 1.0 and sim["rate_per_mb"] == 0.03

    def test_an_included_allowance_is_not_charged_twice(self, monkeypatch):
        """The club's plan includes nothing, so this path is never exercised in
        the hut. It still has to be right, because a plan with an allowance
        would otherwise be billed from the first byte."""
        sim = self._priced(monkeypatch, [{"date": "2026-08-20", "bytes": 5_000_000}],
                           plan_data=4_000_000)
        assert round(sim["month_cost"], 2) == round(1.00 + 1 * 0.03, 2)

    def test_usage_under_the_allowance_costs_only_the_fee(self, monkeypatch):
        sim = self._priced(monkeypatch, [{"date": "2026-08-20", "bytes": 1_000_000}],
                           plan_data=4_000_000)
        assert sim["month_cost"] == 1.00

    def test_no_pricing_leaves_the_bytes_standing(self, monkeypatch):
        """The view-only Hologram role covers SIMs and data usage; plan pricing
        may be refused it. Losing the money must not lose the usage."""
        def fake(cfg, path, params=None, timeout=None):
            if path == "/plans/pricing":
                raise urllib.error.HTTPError("u", 403, "Forbidden", {}, None)
            if path == "/devices":
                d = _device(GL, link_id=11)
                d["intended_plan_id"] = 2096
                return {"data": [d]}
            if path == "/usage/data/daily":
                return {"data": [{"linkid": 11, "date": "2026-08-20", "bytes": 2_000_000}]}
            return {"data": []}
        monkeypatch.setattr(hologram, "_get", fake)
        monkeypatch.setattr(hologram, "_weeks_back", lambda now=None: _orig_weeks(self.NOW))
        sim = hologram.sim_status(blocking=True, cfg=_cfg())["sims"][GL]
        assert sim["weeks"][0]["bytes"] == 2_000_000
        assert sim["weeks"][0]["cost"] is None and sim["month_cost"] is None

    def test_daily_usage_failing_leaves_the_state_standing(self, monkeypatch):
        def fake(cfg, path, params=None, timeout=None):
            if path == "/usage/data/daily":
                raise urllib.error.URLError("down")
            if path == "/devices":
                return {"data": [_device(GL, "PAUSED-SYSTEM", link_id=11)]}
            return {"data": []}
        monkeypatch.setattr(hologram, "_get", fake)
        sim = hologram.sim_status(blocking=True, cfg=_cfg())["sims"][GL]
        assert sim["paused"] is True
        assert [w["bytes"] for w in sim["weeks"]] == [0, 0, 0, 0]

    def test_four_weeks_newest_first(self):
        weeks = _orig_weeks(self.NOW)
        assert len(weeks) == hologram.USAGE_WEEKS == 4
        assert [w["label"] for w in weeks] == [
            "This week", "Last week", "2 weeks ago", "3 weeks ago"]
        assert weeks[0]["end"] == "2026-08-21" and weeks[0]["start"] == "2026-08-15"
        assert weeks[3]["start"] == "2026-07-25"

    def test_the_weeks_are_strings_a_template_can_serialise(self):
        """They are handed to Jinja and embedded in the page as JSON."""
        import json
        json.dumps(_orig_weeks(self.NOW))


class TestItNeverCostsThePage:
    def test_switched_off_asks_nobody(self, monkeypatch):
        monkeypatch.setattr(hologram, "_get", lambda *a, **k: pytest.fail("must not call"))
        assert hologram.sim_status(blocking=True, cfg=_cfg(enabled=False)) == {
            "sims": {}, "error": "", "age_s": 0.0}

    def test_no_key_asks_nobody(self, monkeypatch):
        monkeypatch.setattr(hologram, "_get", lambda *a, **k: pytest.fail("must not call"))
        assert hologram.sim_status(blocking=True, cfg=_cfg(api_key=""))["sims"] == {}

    def test_a_refused_key_explains_itself_rather_than_raising(self, monkeypatch):
        _serve(monkeypatch, fail=urllib.error.HTTPError(
            "u", 403, "Forbidden", {}, None))
        status = hologram.sim_status(blocking=True, cfg=_cfg())
        assert status["sims"] == {}
        assert "API key" in status["error"]

    def test_hologram_being_unreachable_is_not_an_exception(self, monkeypatch):
        _serve(monkeypatch, fail=urllib.error.URLError("no route to host"))
        status = hologram.sim_status(blocking=True, cfg=_cfg())
        assert status["sims"] == {} and "Could not reach" in status["error"]

    def test_nonsense_json_is_survived(self, monkeypatch):
        """A shape we did not expect must render an empty column, not a 500 on
        the Trackers page."""
        for payload in ({"data": "not a list"}, {"data": [None, 7, {}]}, None, []):
            hologram.invalidate_cache()
            monkeypatch.setattr(hologram, "_get", lambda *a, _p=payload, **k: _p)
            assert hologram.sim_status(blocking=True, cfg=_cfg())["sims"] == {}

    def test_a_device_with_no_imei_is_skipped(self, monkeypatch):
        """IMEI is the only join to a tracker. Hologram's own docs have a page on
        devices that do not report one."""
        _serve(monkeypatch, [{"id": 5, "intended_network_state": "LIVE"}])
        assert hologram.sim_status(blocking=True, cfg=_cfg())["sims"] == {}


class TestAskingOnceRatherThanThirtyTimes:
    def test_the_whole_fleet_comes_back_in_one_call(self, monkeypatch):
        asked = _serve(monkeypatch, [_device(GL, link_id=1), _device(ATC, link_id=2)])
        hologram.sim_status(blocking=True, cfg=_cfg())
        assert [p for p, _ in asked].count("/devices") == 1

    def test_a_second_look_is_served_from_memory(self, monkeypatch):
        asked = _serve(monkeypatch, [_device(GL)])
        hologram.sim_status(blocking=True, cfg=_cfg())
        hologram.sim_status(blocking=True, cfg=_cfg())
        assert [p for p, _ in asked].count("/devices") == 1

    def test_a_failure_is_cached_too(self, monkeypatch):
        """Otherwise a Hologram that is down gets re-dialled on every page load,
        and each load pays the timeout."""
        asked = _serve(monkeypatch, fail=urllib.error.URLError("down"))
        hologram.sim_status(blocking=True, cfg=_cfg())
        hologram.sim_status(blocking=True, cfg=_cfg())
        assert len(asked) == 1

    def test_changing_the_key_does_not_serve_the_old_answer(self, monkeypatch):
        asked = _serve(monkeypatch, [_device(GL)])
        hologram.sim_status(blocking=True, cfg=_cfg())
        hologram.sim_status(blocking=True, cfg=_cfg(api_key="a-different-key"))
        assert [p for p, _ in asked].count("/devices") == 2

    def test_changing_the_organisation_does_not_either(self, monkeypatch):
        asked = _serve(monkeypatch, [_device(GL)])
        hologram.sim_status(blocking=True, cfg=_cfg())
        hologram.sim_status(blocking=True, cfg=_cfg(org_id="4321"))
        assert [p for p, _ in asked].count("/devices") == 2

    def test_a_stale_answer_is_refetched(self, monkeypatch):
        asked = _serve(monkeypatch, [_device(GL)])
        hologram.sim_status(blocking=True, cfg=_cfg())
        hologram._cache["at"] = time.time() - hologram.CACHE_TTL_S - 1
        hologram.sim_status(blocking=True, cfg=_cfg())
        assert [p for p, _ in asked].count("/devices") == 2

    def test_the_cache_holds_no_credentials(self, monkeypatch):
        """It is keyed by the credentials, which must not mean keyed *with* them."""
        _serve(monkeypatch, [_device(GL)])
        hologram.sim_status(blocking=True, cfg=_cfg(api_key="k-secret-value"))
        assert "k-secret-value" not in str(hologram._cache)

    def test_filtering_narrows_the_answer_not_the_request(self, monkeypatch):
        """Asking per tracker would turn one page render into thirty round trips."""
        asked = _serve(monkeypatch, [_device(GL, link_id=1), _device(ATC, link_id=2)])
        status = hologram.sim_status([GL], blocking=True, cfg=_cfg())
        assert set(status["sims"]) == {GL}
        assert [p for p, _ in asked].count("/devices") == 1


class TestThePageNeverWaits:
    """The reason for the whole stale-while-revalidate shape. Two calls at a
    six-second timeout is twelve seconds of a race officer's morning, and this
    sits in the render path of the page they open first."""

    def test_a_cold_look_returns_at_once_and_fetches_behind_it(self, monkeypatch):
        started = threading.Event()
        release = threading.Event()

        def slow(cfg, path, params=None, timeout=None):
            started.set()
            release.wait(5)
            return {"data": [_device(GL)]}

        monkeypatch.setattr(hologram, "_get", slow)
        began = time.time()
        status = hologram.sim_status(cfg=_cfg())          # not blocking
        assert time.time() - began < 1.0                  # did not wait on Hologram
        assert status["sims"] == {}                       # nothing known yet
        assert started.wait(5)                            # but it did go and ask
        release.set()

    def test_a_stale_look_serves_the_old_answer_while_refreshing(self, monkeypatch):
        """Being a few minutes behind beats making somebody wait."""
        _serve(monkeypatch, [_device(GL, "LIVE")])
        hologram.sim_status(blocking=True, cfg=_cfg())
        with hologram._cache_lock:
            hologram._cache["at"] = time.time() - hologram.CACHE_TTL_S - 1
        release = threading.Event()
        monkeypatch.setattr(hologram, "_get",
                            lambda *a, **k: (release.wait(5), {"data": []})[1])
        status = hologram.sim_status(cfg=_cfg())
        assert GL in status["sims"]                       # the old answer, served now
        release.set()

    def test_two_page_loads_do_not_start_two_fetches(self, monkeypatch):
        calls = []
        release = threading.Event()

        def slow(cfg, path, params=None, timeout=None):
            calls.append(path)
            release.wait(5)
            return {"data": []}

        monkeypatch.setattr(hologram, "_get", slow)
        hologram.sim_status(cfg=_cfg())
        hologram.sim_status(cfg=_cfg())
        time.sleep(0.2)
        release.set()
        assert calls.count("/devices") == 1

    def test_a_different_account_is_not_served_the_old_answer(self, monkeypatch):
        """Stale is acceptable. Somebody else's fleet is not."""
        _serve(monkeypatch, [_device(GL)])
        hologram.sim_status(blocking=True, cfg=_cfg())
        monkeypatch.setattr(hologram, "_get", lambda *a, **k: {"data": []})
        assert hologram.sim_status(cfg=_cfg(api_key="someone-else"))["sims"] == {}


class TestAStateThatHasJustChanged:
    """Found in use, not in a test. A SIM was paused in the Hologram dashboard,
    the tracker went silent, and this page still said OK — the cache was 15
    minutes long and the page did not admit its answer was old.

    The reading is wanted at precisely the moment it has just changed, because
    the reason anybody looks is that a tracker has gone quiet.
    """

    def test_a_pause_is_picked_up_within_a_couple_of_minutes(self, monkeypatch):
        _serve(monkeypatch, [_device(GL, "LIVE")])
        assert hologram.sim_status(blocking=True, cfg=_cfg())["sims"][GL]["label"] == "OK"
        # Somebody pauses it in the dashboard.
        _serve(monkeypatch, [_device(GL, "PAUSED-USER")])
        with hologram._cache_lock:
            hologram._cache["at"] = time.time() - hologram.CACHE_TTL_S - 1
        sim = hologram.sim_status(blocking=True, cfg=_cfg())["sims"][GL]
        assert sim["label"] == "Error" and sim["paused"] is True

    def test_the_window_of_staleness_is_short(self):
        """Two minutes, not fifteen. Refreshing is off the render path now, so a
        short TTL costs API calls and nothing a race officer waits for."""
        assert hologram.CACHE_TTL_S <= 180

    def test_the_answer_says_how_old_it_is(self, monkeypatch):
        """The part that actually misled: a stale answer that will not admit it."""
        _serve(monkeypatch, [_device(GL)])
        hologram.sim_status(blocking=True, cfg=_cfg())
        with hologram._cache_lock:
            hologram._cache["at"] = time.time() - 300
        assert hologram.sim_status(cfg=_cfg())["age_s"] >= 300

    def test_forgetting_the_cache_forces_a_fresh_answer(self, monkeypatch):
        """What the "check again" button does, for the person who has just
        changed something at Hologram and will not wait two minutes."""
        _serve(monkeypatch, [_device(GL, "LIVE")])
        hologram.sim_status(blocking=True, cfg=_cfg())
        _serve(monkeypatch, [_device(GL, "PAUSED-USER")])
        hologram.invalidate_cache()
        assert hologram.sim_status(blocking=True, cfg=_cfg())["sims"][GL]["paused"] is True

    def test_the_page_offers_the_button_and_the_age(self, logged_in_client, monkeypatch):
        from core import horn, track
        horn.save_hardware_config({"hologram_enabled": "1", "hologram_api_key": "abc"})
        track.upsert_tracker(GL, label="Tracker A")
        hologram.invalidate_cache()
        monkeypatch.setattr(hologram, "_get",
                            lambda cfg, path, params=None, timeout=None: {"data": []})
        html = logged_in_client.get("/admin/trackers").get_data(as_text=True)
        assert "SIM status checked" in html and "check again" in html

    def test_check_again_refetches_and_returns_to_the_page(self, logged_in_client, monkeypatch):
        from core import horn
        horn.save_hardware_config({"hologram_enabled": "1", "hologram_api_key": "abc"})
        calls = []
        monkeypatch.setattr(hologram, "_get", lambda cfg, path, params=None, timeout=None: (
            calls.append(path), {"data": [_device(GL, "PAUSED-USER")]})[1])
        token = "test-csrf-token"
        with logged_in_client.session_transaction() as sess:
            sess["_csrf_token"] = token
        r = logged_in_client.post("/admin/trackers/sim-refresh",
                                  data={"_csrf_token": token})
        assert r.status_code in (302, 303)
        assert "/trackers" in r.headers["Location"]
        assert "/devices" in calls          # it really went and asked
        with hologram._cache_lock:
            assert hologram._cache["sims"][GL]["paused"] is True


class TestWhatHologramCannotResolve:
    def test_its_literal_unknown_is_treated_as_absent(self, monkeypatch):
        """It sends the string "Unknown" rather than omitting the field, which
        rendered in the popout as "Unknown Unknown"."""
        rec = _device(GL)
        rec["manufacturer"], rec["model"] = "Unknown", "Unknown"
        _serve(monkeypatch, [rec])
        sim = hologram.sim_status(blocking=True, cfg=_cfg())["sims"][GL]
        assert sim["manufacturer"] == "" and sim["model"] == ""


class TestTheRequestItself:
    def test_it_authenticates_the_way_hologram_asks(self, monkeypatch):
        """Basic auth with the literal username ``apikey`` — not a bearer token,
        which is what the Traccar client beside it uses."""
        seen = {}

        class FakeResponse:
            def read(self): return b'{"data": []}'
            def __enter__(self): return self
            def __exit__(self, *a): return False

        def fake_open(req, timeout=None):
            seen["url"] = req.full_url
            seen["auth"] = req.get_header("Authorization")
            return FakeResponse()

        monkeypatch.setattr(hologram.urllib.request, "urlopen", fake_open)
        hologram._get(_cfg(api_key="abc"), "/devices", {"limit": 5})
        import base64
        assert seen["auth"] == "Basic " + base64.b64encode(b"apikey:abc").decode()
        assert seen["url"].startswith("https://dashboard.hologram.io/api/1/devices?")

    def test_blank_parameters_are_left_out(self, monkeypatch):
        """An empty orgid must not be sent as ``orgid=``, which asks Hologram to
        filter to an organisation named nothing."""
        seen = {}

        class FakeResponse:
            def read(self): return b"{}"
            def __enter__(self): return self
            def __exit__(self, *a): return False

        monkeypatch.setattr(hologram.urllib.request, "urlopen",
                            lambda req, timeout=None: (seen.__setitem__("url", req.full_url),
                                                       FakeResponse())[1])
        hologram._get(_cfg(), "/devices", {"limit": 5, "orgid": ""})
        assert "orgid" not in seen["url"]


class TestItReachesTheDashboard:
    """A blocked SIM belongs beside the low-battery warning, for the same reason
    that one exists: it is a race-day fact that is invisible everywhere else and
    will not resolve itself. The rule matches the batteries deliberately — only
    trackers assigned to a boat, because a spare in a drawer is a job for
    another day.
    """

    def _blocked(self, monkeypatch, state="PAUSED-SYSTEM"):
        from core import horn
        horn.save_hardware_config({"hologram_enabled": "1", "hologram_api_key": "abc"})
        hologram.invalidate_cache()
        monkeypatch.setattr(hologram, "_get", lambda cfg, path, params=None, timeout=None:
                            {"data": [_device(GL, state)]} if path == "/devices" else {"data": []})
        hologram.sim_status(blocking=True)

    def test_a_blocked_sim_on_a_boat_is_warned_about(self, client, monkeypatch):
        from core import track
        with ro.app.app_context():
            with ro.get_db() as db:
                now = "2026-08-21T09:00:00"
                cur = db.execute(
                    "INSERT INTO boats (boat_name, sail_no, status, created_at, updated_at)"
                    " VALUES ('Kite', 'GBR1', 'ACTIVE', ?, ?)", (now, now))
                boat_id = int(cur.lastrowid)
                db.commit()
        track.upsert_tracker(GL, label="Tracker A", boat_id=boat_id)
        self._blocked(monkeypatch)
        warned = hologram.sim_warnings()
        assert [w["unique_id"] for w in warned] == [GL]
        assert warned[0]["boat_name"] == "Kite"
        assert warned[0]["state_text"] == "Paused by system"

    def test_a_spare_in_the_drawer_is_not_a_race_day_problem(self, client, monkeypatch):
        """Unassigned, so nobody is about to go racing relying on it."""
        from core import track
        track.upsert_tracker(GL, label="Spare")          # no boat
        self._blocked(monkeypatch)
        assert hologram.sim_warnings() == []

    def test_a_healthy_sim_says_nothing(self, client, monkeypatch):
        from core import track
        track.upsert_tracker(GL, label="Tracker A", boat_id=1)
        self._blocked(monkeypatch, state="LIVE")
        assert hologram.sim_warnings() == []

    def test_an_unactivated_sim_counts_as_blocked_too(self, client, monkeypatch):
        """It leaves the tracker just as silent as a paused one."""
        from core import track
        track.upsert_tracker(GL, label="Tracker A", boat_id=1)
        self._blocked(monkeypatch, state="INACTIVE")
        assert [w["unique_id"] for w in hologram.sim_warnings()] == [GL]

    def test_it_says_nothing_when_the_feature_is_off(self, client):
        assert hologram.sim_warnings() == []

    def test_it_can_never_break_the_dashboard(self, client, monkeypatch):
        """This runs on the page the hut leaves open all day. It returns nothing
        rather than raising, whatever happens underneath."""
        from core import horn
        horn.save_hardware_config({"hologram_enabled": "1", "hologram_api_key": "abc"})
        monkeypatch.setattr(hologram, "paused_sims",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
        assert hologram.sim_warnings() == []

    def test_the_dashboard_renders_the_card(self, logged_in_client, monkeypatch):
        from core import track
        with ro.app.app_context():
            with ro.get_db() as db:
                now = "2026-08-21T09:00:00"
                cur = db.execute(
                    "INSERT INTO boats (boat_name, sail_no, status, created_at, updated_at)"
                    " VALUES ('Kite', 'GBR1', 'ACTIVE', ?, ?)", (now, now))
                boat_id = int(cur.lastrowid)
                db.commit()
        track.upsert_tracker(GL, label="Tracker A", boat_id=boat_id)
        self._blocked(monkeypatch)
        html = logged_in_client.get("/admin").get_data(as_text=True)
        assert "simWarningCard" in html and "Kite" in html
        assert "1 blocked" in html


class TestTheFleetTotalsReconcile:
    """The costs popout is read by adding a column up, so it has to survive
    that. It did not: the per-tracker column showed each SIM's *month* cost —
    fee plus data — and the footer then added the fees again, so the page
    disagreed with itself by the whole standing charge.
    """

    def _fleet(self, monkeypatch, n=3, period=5_000_000, rate=0.03, fee=1.0):
        from core import horn
        horn.save_hardware_config({"hologram_enabled": "1", "hologram_api_key": "abc"})
        hologram.invalidate_cache()
        devices = []
        for i in range(n):
            d = _device("86212908000000%d" % i, link_id=100 + i)
            d["intended_plan_id"] = 2096
            d["links"]["cellular"][0]["cur_billing_data_used"] = period
            d["links"]["cellular"][0]["plan"] = {"id": 2096, "zone": "global",
                                                 "name": "G3", "data": 0}
            devices.append(d)

        def fake(cfg, path, params=None, timeout=None):
            if path == "/devices":
                return {"data": devices}
            if path == "/plans/pricing":
                return {"data": [{"plan_id": 2096, "zone": "global",
                                  "amount": "%f" % fee, "overage": "%f" % rate}]}
            return {"data": []}

        monkeypatch.setattr(hologram, "_get", fake)
        hologram.sim_status(blocking=True)
        return hologram.account()

    def test_the_data_column_sums_to_the_data_line(self, client, monkeypatch):
        """What somebody actually does: add the column up and check the footer."""
        acct = self._fleet(monkeypatch)
        column = sum((s["period_bytes"] or 0) / hologram.BYTES_PER_MB * s["rate_per_mb"]
                     for s in acct["sims"].values())
        assert round(column, 2) == round(acct["totals"]["data_cost"], 2)

    def test_data_plus_fees_is_the_fleet_total(self, client, monkeypatch):
        acct = self._fleet(monkeypatch, n=3)
        t = acct["totals"]
        assert round(t["data_cost"] + t["fees"], 2) == round(t["month_cost"], 2)

    def test_the_fee_is_counted_once_per_sim_and_no_more(self, client, monkeypatch):
        """Three SIMs at $1.00 is $3.00 of fees, not $3.00 in every row as well."""
        acct = self._fleet(monkeypatch, n=3, fee=1.0)
        assert acct["totals"]["fees"] == 3.0
        assert acct["totals"]["priced"] == 3
        # 3 SIMs x 5 MB x $0.03
        assert round(acct["totals"]["data_cost"], 2) == 0.45
        assert round(acct["totals"]["month_cost"], 2) == 3.45

    def test_a_single_sim_month_total_still_carries_its_own_fee(self, client, monkeypatch):
        """The per-SIM popout shows one tracker's whole bill, so its month total
        keeps the fee. Only the fleet table's column is data alone."""
        acct = self._fleet(monkeypatch, n=1, period=5_000_000)
        sim = list(acct["sims"].values())[0]
        assert round(sim["month_cost"], 2) == 1.15        # $1.00 + 5 MB at $0.03


class TestWhatPeriodTheFiguresCover:
    """Asked in use: "what is the time period of the costs popout?" — and the
    answer was that there was not one. Hologram bills each SIM on its own
    30-day cycle from the day it was activated, so "this period" spans a
    different window per SIM. The club's ran from one day to sixteen at the same
    moment, which made a fleet total labelled "month to date" badly misleading:
    the newest SIMs had barely started accruing.
    """

    def _fleet(self, monkeypatch, ends):
        from core import horn
        horn.save_hardware_config({"hologram_enabled": "1", "hologram_api_key": "abc"})
        hologram.invalidate_cache()
        devices, billing = [], []
        for i, end in enumerate(ends):
            d = _device("86212908000000%d" % i, link_id=100 + i)
            d["intended_plan_id"] = 2096
            d["links"]["cellular"][0]["cur_billing_data_used"] = 1_000_000
            d["links"]["cellular"][0]["plan"] = {"id": 2096, "zone": "global",
                                                 "name": "G3", "data": 0}
            devices.append(d)
            billing.append({"linkid": 100 + i, "cur_period_end": end, "periods_back": "0"})

        def fake(cfg, path, params=None, timeout=None):
            if path == "/devices":
                return {"data": devices}
            if path == "/usage/data/billing":
                return {"data": billing}
            if path == "/usage/data/daily":
                return {"data": [{"linkid": 100 + i, "date": "2026-08-20",
                                  "bytes": 500_000} for i in range(len(ends))]}
            if path == "/plans/pricing":
                return {"data": [{"plan_id": 2096, "zone": "global",
                                  "amount": "1.000000", "overage": "0.030000"}]}
            return {"data": []}

        monkeypatch.setattr(hologram, "_get", fake)
        hologram.sim_status(blocking=True)
        return hologram.account()

    def test_each_sim_reports_how_far_into_its_own_period_it_is(self, client, monkeypatch):
        """Without this the rows silently compare a day against a fortnight."""
        import datetime
        today = datetime.datetime.now(datetime.timezone.utc).date()
        near = (today + datetime.timedelta(days=29)).isoformat() + " 10:00:00"
        far = (today + datetime.timedelta(days=15)).isoformat() + " 10:00:00"
        acct = self._fleet(monkeypatch, [near, far])
        days = sorted(s["days_in_period"] for s in acct["sims"].values())
        assert days == [1, 15]
        assert all(s["period_start"] for s in acct["sims"].values())

    def test_there_is_a_figure_on_a_window_they_share(self, client, monkeypatch):
        """The point of the addition: something comparable over time, rather
        than a total of seven different-length windows."""
        import datetime
        today = datetime.datetime.now(datetime.timezone.utc).date()
        acct = self._fleet(monkeypatch, [
            (today + datetime.timedelta(days=29)).isoformat() + " 10:00:00",
            (today + datetime.timedelta(days=15)).isoformat() + " 10:00:00"])
        totals = acct["totals"]
        assert totals["recent_days"] == 28
        assert totals["recent_bytes"] == 1_000_000        # 2 SIMs x 500 kB
        assert round(totals["recent_cost"], 3) == round(1 * 0.03, 3)

    def test_the_period_total_is_still_the_sum_of_the_periods(self, client, monkeypatch):
        """It is not comparable, but it is not wrong — it is what has been run
        up so far and what the balance will actually be charged."""
        import datetime
        today = datetime.datetime.now(datetime.timezone.utc).date()
        acct = self._fleet(monkeypatch, [
            (today + datetime.timedelta(days=29)).isoformat() + " 10:00:00",
            (today + datetime.timedelta(days=15)).isoformat() + " 10:00:00"])
        assert acct["totals"]["period_bytes"] == 2_000_000
        assert round(acct["totals"]["month_cost"], 2) == round(2.00 + 0.06, 2)


class TestWhenTheAccountRunsDry:
    """A prepay balance hitting zero stops every tracker at once, and unlike a
    flat battery there is nothing on the water to see it coming.

    The sum is not "balance divided by monthly cost". Each SIM bills on its own
    30-day cycle from the day it was activated, so the fees land in clusters —
    the club's seven fall on two dates a fortnight apart. An average would hide
    both, which is why this walks forward a day at a time.
    """

    TODAY = 1787313600.0        # 2026-08-21 12:00Z

    def _sim(self, period_end, weekly_bytes=0, fee=1.0, rate=0.03):
        return {"rate_per_mb": rate, "monthly_fee": fee, "period_end": period_end,
                "weeks": [{"label": "This week", "start": "2026-08-15",
                           "end": "2026-08-21", "bytes": weekly_bytes, "cost": None}]}

    def test_the_club_case(self):
        """The real fleet on 21 August: $6.14, seven SIMs at $1.00, renewing in
        two clusters, and about 11.5 MB a week between them."""
        sims = {}
        for i, end in enumerate(["2026-09-04 18:45:06", "2026-09-05 12:50:09",
                                 "2026-09-05 18:14:47", "2026-09-05 19:02:46",
                                 "2026-09-19 18:50:50", "2026-09-19 19:03:47",
                                 "2026-09-19 19:11:39"]):
            sims[str(i)] = self._sim(end, weekly_bytes=1_644_000)
        out = hologram.forecast_balance(
            sims, {"balance": 6.14, "min_balance": 5.0, "promo": 0.0}, now=self.TODAY)
        assert out["empty_on"] == "2026-09-19"        # the second cluster finishes it
        assert out["days_left"] == 29
        assert out["below_min_on"] == "2026-09-04"    # the first one dips under $5
        assert out["next_renewal"] == "2026-09-04"
        assert round(out["monthly_fees"], 2) == 7.00

    def test_the_next_renewal_is_the_soonest_not_the_one_after(self):
        """The walk advances each date as it spends it, so reading the minimum
        afterwards gives next month's — which it did, once."""
        sims = {"a": self._sim("2026-09-04 10:00:00"),
                "b": self._sim("2026-09-19 10:00:00")}
        out = hologram.forecast_balance(sims, {"balance": 50.0}, now=self.TODAY)
        assert out["next_renewal"] == "2026-09-04"

    def test_fees_land_in_lumps_not_smoothly(self):
        """Four SIMs renewing on one day and three a fortnight later must empty
        the account on the later date, not on some averaged one between."""
        sims = {str(i): self._sim("2026-09-05 10:00:00") for i in range(4)}
        sims.update({"x%d" % i: self._sim("2026-09-19 10:00:00") for i in range(3)})
        out = hologram.forecast_balance(sims, {"balance": 5.5}, now=self.TODAY)
        assert out["empty_on"] == "2026-09-19"

    def test_a_renewal_already_past_rolls_forward(self):
        """Hologram reports the current period's end; if the app has been shut
        for a while that date may be behind us, and it must not be spent today."""
        sims = {"a": self._sim("2026-07-04 10:00:00")}
        out = hologram.forecast_balance(sims, {"balance": 50.0}, now=self.TODAY)
        assert out["next_renewal"] > "2026-08-21"

    def test_data_alone_drains_it(self):
        """No renewal dates at all, just usage. 7 MB a week at $0.03 is $0.03 a
        day, so $3.00 lasts about a hundred days."""
        sims = {"a": self._sim("", weekly_bytes=7_000_000)}
        out = hologram.forecast_balance(sims, {"balance": 3.0}, now=self.TODAY)
        assert 95 <= out["days_left"] <= 105

    def test_a_healthy_balance_says_nothing_precise(self):
        """Past the horizon a seven-day average cannot support a date, so it
        does not offer one."""
        sims = {"a": self._sim("2026-09-04 10:00:00")}
        out = hologram.forecast_balance(sims, {"balance": 5000.0}, now=self.TODAY)
        assert out["beyond_horizon"] is True and out["empty_on"] == ""

    def test_promotional_credit_counts(self):
        """Promotional credit spends like money, so it has to be added in.
        Both figures are kept inside the forecast horizon deliberately — past it
        there is no date to compare."""
        sims = {"a": self._sim("2026-09-04 10:00:00")}
        bare = hologram.forecast_balance(sims, {"balance": 2.0}, now=self.TODAY)
        with_promo = hologram.forecast_balance(
            sims, {"balance": 2.0, "promo": 3.0}, now=self.TODAY)
        assert bare["days_left"] and with_promo["days_left"]
        assert with_promo["days_left"] > bare["days_left"]

    def test_no_balance_means_no_forecast(self):
        """Hologram refusing the billing permission must not produce a date
        invented from nothing."""
        sims = {"a": self._sim("2026-09-04 10:00:00")}
        assert hologram.forecast_balance(sims, {}) == {}

    def test_no_rate_means_no_forecast(self):
        assert hologram.forecast_balance({"a": {"weeks": []}}, {"balance": 10.0}) == {}


class TestTheTopUpWarning:
    def _account(self, monkeypatch, balance, days_of_credit):
        from core import horn
        horn.save_hardware_config({"hologram_enabled": "1", "hologram_api_key": "abc"})
        hologram.invalidate_cache()
        monkeypatch.setattr(hologram, "account", lambda: {
            "sims": {}, "totals": {}, "balance": balance,
            "forecast": {"days_left": days_of_credit, "empty_on": "2026-09-19"},
            "error": "", "age_s": 0.0, "dashboard_url": hologram.DASHBOARD_URL})

    def test_it_shouts_when_the_money_is_nearly_gone(self, client, monkeypatch):
        self._account(monkeypatch, {"balance": 6.14, "topoff": 25.0}, 10)
        warning = hologram.balance_warning()
        assert warning["level"] == "urgent" and warning["days_left"] == 10
        assert warning["auto_topoff"] is True

    def test_it_mentions_it_when_a_month_is_left(self, client, monkeypatch):
        self._account(monkeypatch, {"balance": 6.14}, 29)
        assert hologram.balance_warning()["level"] == "soon"

    def test_it_stays_quiet_when_there_is_plenty(self, client, monkeypatch):
        self._account(monkeypatch, {"balance": 500.0}, 200)
        assert hologram.balance_warning() is None

    def test_a_postpay_account_has_no_balance_to_run_out(self, client, monkeypatch):
        self._account(monkeypatch, {"balance": 0.0, "postpay": True}, 1)
        assert hologram.balance_warning() is None

    def test_it_says_nothing_without_a_balance(self, client, monkeypatch):
        self._account(monkeypatch, {}, 5)
        assert hologram.balance_warning() is None

    def test_it_can_never_break_the_dashboard(self, client, monkeypatch):
        from core import horn
        horn.save_hardware_config({"hologram_enabled": "1", "hologram_api_key": "abc"})
        monkeypatch.setattr(hologram, "account",
                            lambda: (_ for _ in ()).throw(RuntimeError("boom")))
        assert hologram.balance_warning() is None

    def test_the_dashboard_renders_it(self, logged_in_client, monkeypatch):
        self._account(monkeypatch, {"balance": 6.14, "topoff": 25.0}, 10)
        html = logged_in_client.get("/admin").get_data(as_text=True)
        assert "simBalanceCard" in html and "top up now" in html
        assert "$6.14" in html


class TestTheSetting:
    def test_it_is_off_until_somebody_turns_it_on(self, client):
        """It needs an account the club may not have, and costs an outbound
        call, so it cannot be on by default the way race polling is."""
        assert hologram.hologram_config()["enabled"] is False
        assert hologram.hologram_active() is False

    def test_saving_a_key_turns_it_on(self, client):
        from core import horn
        horn.save_hardware_config({"hologram_enabled": "1", "hologram_api_key": "abc"})
        cfg = hologram.hologram_config()
        assert cfg["enabled"] is True and cfg["api_key"] == "abc"
        assert hologram.hologram_active(cfg) is True

    def test_enabled_without_a_key_is_still_inactive(self, client):
        from core import horn
        horn.save_hardware_config({"hologram_enabled": "1", "hologram_api_key": ""})
        assert hologram.hologram_active() is False

    def test_the_settings_page_offers_it(self, logged_in_client):
        html = logged_in_client.get("/admin/settings").get_data(as_text=True)
        assert "hologram_api_key" in html and "hologram_enabled" in html

    def test_the_trackers_page_survives_hologram_being_off(self, logged_in_client):
        assert logged_in_client.get("/admin/trackers").status_code == 200

    def test_the_trackers_page_shows_a_paused_sim(self, logged_in_client, monkeypatch):
        """End to end, because the value of this is entirely in a race officer
        seeing the warning — a correct module behind a column nobody renders
        would be worth nothing.

        Two loads, deliberately: the page never waits on Hologram, so the first
        one arms the fetch and the second one shows it. That is what a race
        officer opening the page from cold actually sees.
        """
        from core import horn, track
        horn.save_hardware_config({"hologram_enabled": "1", "hologram_api_key": "abc"})
        track.upsert_tracker(GL, label="Tracker A")
        hologram.invalidate_cache()
        monkeypatch.setattr(hologram, "_get", lambda cfg, path, params=None, timeout=None: (
            {"data": [_device(GL, "PAUSED-SYSTEM", link_id=11)]} if path == "/devices"
            else {"data": [{"linkid": 11, "session_begin": "2026-08-21T08:00:00Z",
                            "network_name": "O2 UK"}]}))

        first = logged_in_client.get("/admin/trackers")
        assert first.status_code == 200
        assert WARNING not in first.get_data(as_text=True)

        deadline = time.time() + 5
        while time.time() < deadline and hologram._cache.get("fetching"):
            time.sleep(0.02)
        html = logged_in_client.get("/admin/trackers").get_data(as_text=True)
        assert WARNING in html and "Paused by system" in html
        # The row itself is a red dot and a link, not a paragraph of carrier
        # names: seven of these across a table is most of the page width.
        assert 'class="rag-dot rag-red"' in html
        # A dot and one word, not a button. Three heavy buttons in a cell is what
        # this replaced.
        assert ">Error</button>" in html
        assert "sim-open" in html and 'id="simDialog"' in html
        assert "O2 UK" in html          # carried to the popout, not shown inline
