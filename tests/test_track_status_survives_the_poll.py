"""The tracking status box must report the poll it just did.

Found on a live settings page, which said **No trackers reporting yet** directly
above **Push: working — 12 fixes received, last just now**. Both lines are true
of the same feature at the same moment, and they cannot both be right.

The cause was a misplaced ``else``. The hourly purge was inserted between
``if track_active(cfg):`` and its ``else:``, so Python re-bound the ``else`` to
the purge's ``try``. The purge returns normally on every cycle — it has an
hourly guard that *returns* rather than raising — so the ``else`` fired every
time and replaced the status just built from a real poll with an empty one.

It reads as a display nuisance and is worse than that: the message is how a race
officer checks the fleet is being tracked before a start, and it said nothing was
being tracked while positions were arriving. Shipped since v0.256.

The test drives one real iteration of the background loop rather than asserting
on the source, because the bug was invisible in the source — the code looked
exactly like code that works.
"""
from __future__ import annotations

import time

import pytest

from core import track

# Captured at import, which happens before conftest's autouse fixture replaces
# it with a no-op. That fixture exists so a stray daemon cannot poll and write to
# the developer's real database after a test ends — a good reason, and it means
# the only way to exercise the real loop is to have kept a reference first.
_REAL_LOOP = track.track_background_loop


class _StopAfterOneLoop(Exception):
    """Raised from the sleep at the bottom of the loop to end the iteration."""


def _one_iteration(monkeypatch, *, active=True, positions=None, purge_raises=False):
    """Run the loop body exactly once and return what it stored as the status."""
    track.TRACK_MONITOR_STATE["last_status"] = {"ok": False, "message": "not run",
                                                "devices": []}

    monkeypatch.setattr(track, "track_config", lambda: {
        "enabled": active, "sim_enabled": False, "base_url": "http://x", "token": "t",
        "poll_seconds": 5, "retention_days": 90, "rounding_radius_m": 50,
        "gate_reach_m": 750, "finish_horn": False, "race_poll_enabled": False,
        "ingest_secret": "", "sim": False})
    monkeypatch.setattr(track, "track_active", lambda cfg=None: active)
    monkeypatch.setattr(track, "_latest_fix_times", lambda: {})
    monkeypatch.setattr(track, "collect_positions",
                        lambda cfg: (list(positions or []), None, "traccar"))
    monkeypatch.setattr(track, "insert_positions", lambda *a, **k: None)
    monkeypatch.setattr(track, "backfill_positions", lambda cfg, since=None: (0, None))
    monkeypatch.setattr(track, "run_finish_detection", lambda *a, **k: None)
    monkeypatch.setattr(track, "run_race_position_polling", lambda *a, **k: 0)
    monkeypatch.setattr(track, "_boat_labels_by_unique_id", lambda: {})

    def purge(*a, **k):
        if purge_raises:
            raise RuntimeError("database is locked")
        return 0                      # the ordinary case: nothing to drop, no error

    monkeypatch.setattr(track, "purge_old_positions", purge)

    def stop(_seconds):
        raise _StopAfterOneLoop

    monkeypatch.setattr(track.time, "sleep", stop)
    with pytest.raises(_StopAfterOneLoop):
        _REAL_LOOP()
    return dict(track.TRACK_MONITOR_STATE["last_status"])


def _fix(unique_id):
    return {"unique_id": unique_id, "name": unique_id, "lat": 52.88, "lon": -4.40,
            "speed_kn": 5.0, "course_deg": 90.0, "fix_time": time.time()}


class TestThePollIsWhatGetsReported:
    def test_a_reporting_tracker_is_reported(self, monkeypatch):
        """The whole bug in one assertion: two trackers had just been polled and
        the box said none were reporting."""
        status = _one_iteration(monkeypatch, positions=[_fix("A"), _fix("B")])
        assert [d["unique_id"] for d in status["devices"]] == ["A", "B"]
        assert "2 of 2" in status["message"]
        assert "No trackers reporting yet" not in status["message"]

    def test_the_ordinary_purge_does_not_wipe_it(self, monkeypatch):
        """The purge returning normally is the common path — it has an hourly
        guard that returns 0 — and it was the path that destroyed the status."""
        status = _one_iteration(monkeypatch, positions=[_fix("A")])
        assert status["devices"], "a clean purge threw the poll's status away"

    def test_a_purge_that_fails_does_not_wipe_it_either(self, monkeypatch):
        """Housekeeping failing is not news about the fleet."""
        status = _one_iteration(monkeypatch, positions=[_fix("A")], purge_raises=True)
        assert status["devices"] and "1 of 1" in status["message"]

    def test_nothing_polled_still_says_so(self, monkeypatch):
        """The empty message has to survive, for the case it was written for."""
        status = _one_iteration(monkeypatch, positions=[])
        assert status["devices"] == []
        assert "No trackers reporting yet" in status["message"]

    def test_tracking_switched_off_reports_that(self, monkeypatch):
        """The real job of that else: when the poller is not running at all."""
        status = _one_iteration(monkeypatch, active=False)
        assert status["devices"] == []
        assert "disabled" in status["message"].lower()

    def test_the_settings_box_and_the_push_line_cannot_disagree(self, monkeypatch):
        """What was on screen: 'No trackers reporting yet' above 'Push: working'.
        If fixes are arriving by either route, the headline must not deny it."""
        _one_iteration(monkeypatch, positions=[_fix("A")])
        with track.TRACK_MONITOR_LOCK:
            track.TRACK_MONITOR_STATE["forward_count"] = 12
            track.TRACK_MONITOR_STATE["last_forward_at"] = time.time()
        monkeypatch.setattr(track, "track_config", lambda: dict(
            track.track_config.__wrapped__() if hasattr(track.track_config, "__wrapped__")
            else {"enabled": True, "sim_enabled": False, "base_url": "http://x",
                  "token": "t", "poll_seconds": 5, "retention_days": 90,
                  "rounding_radius_m": 50, "gate_reach_m": 750, "finish_horn": False,
                  "race_poll_enabled": False, "ingest_secret": "shared"}))
        status = track.track_runtime_status()
        assert "Push: working" in status["push_text"]
        assert "No trackers reporting yet" not in status["message"]
