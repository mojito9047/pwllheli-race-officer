"""Asking racing boats where they are, instead of waiting for their own schedule.

A GL521MG reports once a minute and holds each fix until the top of the minute, so its
position reaches the app about 45 seconds old — 185 m behind the boat at 6 knots, which
is the gap a finish gets interpolated across. Asked directly it answers in about a
second, and it will sustain that every 20 s: measured against the club's own units,
where 5 s polling returned duplicates of a cached fix with latency climbing to 57 s.

The window is the warning signal to a minute after a boat stops racing. The minute
matters — a finish is interpolated between the fixes either side of the line, so the one
*after* the crossing is what pins it down.

It costs about +1.1 %/h against an idle 1 %/h, so a whole nine-hour race polled
throughout is roughly 19% of the battery. That is affordable on a Queclink and would not
be on the ATC700, which is why Teltonika is deliberately not in the table: it already
reports every 10 s while moving and answers in a second, so there is nothing to ask for
and no battery to ask with.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta

import pytest

import app as ro
from core import track


# Real type codes, because the choice of who gets asked is made from them: "gl200" is
# Queclink's whole family and the command carries the GL521M's own password, so another
# model on the same protocol would reject it.
GL521MG = "864864070498856"      # type code 86486407
ATC700 = "862129082306832"       # type code 86212908

CFG = {"base_url": "http://x/traccar", "token": "t", "enabled": True, "sim_enabled": False,
       "finish_horn": False, "poll_seconds": 5, "retention_days": 90,
       "rounding_radius_m": 50, "race_poll_enabled": True, "ingest_secret": ""}


def _clear_state():
    track._RACE_POLL_LAST_SENT.clear()
    track._RACE_POLL_LAST_RACING.clear()


def _race_with_boat(warning_minutes_ago=1.0, status="RACING", uid=GL521MG,
                    protocol="gl200"):
    """A race whose warning signal has passed, with one tracked boat on it."""
    now_iso = datetime.now().isoformat(timespec="seconds")
    warning = (datetime.now() - timedelta(minutes=warning_minutes_ago)).isoformat(timespec="seconds")
    with ro.app.app_context():
        with ro.get_db() as db:
            cur = db.execute(
                "INSERT INTO boats (boat_name, sail_no, status, created_at, updated_at)"
                " VALUES ('Kite', 'GBR1', 'ACTIVE', ?, ?)", (now_iso, now_iso))
            boat_id = int(cur.lastrowid)
            cur = db.execute(
                "INSERT INTO races (name, class_name, course_no, start_time, rating_rule,"
                " notes, created_at) VALUES ('R', 'IRC', 1, ?, 'DUAL', '', ?)", (warning, now_iso))
            race_id = int(cur.lastrowid)
            db.execute(
                "INSERT INTO entries (race_id, boat_id, boat_name, sail_no, status)"
                " VALUES (?, ?, 'Kite', 'GBR1', ?)", (race_id, boat_id, status))
            db.commit()
    track.upsert_tracker(uid, label="GL", boat_id=boat_id)
    if protocol:
        track.insert_positions([{
            "device_id": "46", "unique_id": uid, "name": "GL", "lat": 52.88, "lon": -4.40,
            "speed_kn": 5.0, "course_deg": 90.0, "fix_time": time.time(), "protocol": protocol}])
    return race_id, boat_id


class TestWhoGetsAsked:
    def test_a_racing_boat_on_a_started_race(self, client):
        _clear_state()
        _race_with_boat()
        assert GL521MG in track.race_poll_targets()

    def test_nothing_before_the_warning_signal(self, client):
        """The window opens at the warning signal, not when the race was created."""
        _clear_state()
        _race_with_boat(warning_minutes_ago=-30)      # warning is half an hour away
        assert track.race_poll_targets() == []

    def test_a_boat_that_has_finished_is_asked_for_one_more_minute(self, client):
        """A finish is interpolated between the fixes either side of the line, so the
        fix after the crossing is the one that pins it."""
        _clear_state()
        _race_with_boat(status="RACING")
        now = time.time()
        assert track.race_poll_targets(now)            # racing: asked, and remembered
        with ro.app.app_context():
            with ro.get_db() as db:
                db.execute("UPDATE entries SET status = 'FINISHED'")
                db.commit()
        assert track.race_poll_targets(now + 30) == [GL521MG]     # inside the grace
        assert track.race_poll_targets(now + 120) == []             # and past it

    def test_a_race_left_open_for_ever_stops_being_polled(self, client):
        """Nobody closes every race. Without a backstop a forgotten one would keep
        asking a tracker for its position until the battery went."""
        _clear_state()
        _race_with_boat(warning_minutes_ago=60 * 24 * 3)
        assert track.race_poll_targets() == []

    def test_a_teltonika_is_never_asked(self, client):
        """It already reports every 10 s while moving and answers in a second, so there
        is nothing to gain — and at ~10%/h it is the one tracker that cannot spare it."""
        _clear_state()
        _race_with_boat(uid=ATC700, protocol="teltonika")
        assert track.race_poll_targets() == []

    def test_another_queclink_on_the_same_protocol_is_not_asked(self, client):
        """gl200 is a whole family. The command carries "gl521m" — the GL521M's own
        password — so a different Queclink would reject it, and asking is pointless."""
        _clear_state()
        _race_with_boat(uid="861234560000001", protocol="gl200")
        assert track.race_poll_targets() == []

    def test_a_tracker_that_has_never_reported_is_not_guessed_at(self, client):
        """With no protocol seen there is no way to know what dialect it speaks, and
        guessing would send a Queclink string to something else entirely."""
        _clear_state()
        _race_with_boat(uid="861234567890123", protocol=None)
        assert track.race_poll_targets() == []


class TestHowOftenItAsks:
    def test_it_sends_once_and_then_holds_off(self, client, monkeypatch):
        """20 s is the floor the tracker itself sets: below it the device returns
        duplicates of a cached fix, and below 10 s it backs up."""
        _clear_state()
        _race_with_boat()
        sent = []
        monkeypatch.setattr(track, "track_config", lambda: dict(CFG))
        monkeypatch.setattr(track, "send_tracker_command",
                            lambda uid, cmd, cfg=None: sent.append((uid, cmd)) or (True, "ok"))
        now = time.time()
        assert track.run_race_position_polling(now=now) == 1
        assert track.run_race_position_polling(now=now + 5) == 0     # too soon
        assert track.run_race_position_polling(now=now + 21) == 1    # interval elapsed
        assert len(sent) == 2
        assert sent[0][1] == "AT+GTRTO=gl521m,1,,,,,,FFFF$"

    def test_a_traccar_that_refuses_does_not_become_a_retry_loop(self, client, monkeypatch):
        """The send is marked before it is attempted. A relay that is down during a race
        must not turn into a tight loop around the whole fleet."""
        _clear_state()
        _race_with_boat()
        calls = []
        monkeypatch.setattr(track, "track_config", lambda: dict(CFG))
        def boom(uid, cmd, cfg=None):
            calls.append(uid)
            raise RuntimeError("relay down")
        monkeypatch.setattr(track, "send_tracker_command", boom)
        now = time.time()
        track.run_race_position_polling(now=now)
        track.run_race_position_polling(now=now + 5)
        assert len(calls) == 1

    def test_switching_it_off_stops_it(self, client, monkeypatch):
        _clear_state()
        _race_with_boat()
        cfg = dict(CFG); cfg["race_poll_enabled"] = False
        monkeypatch.setattr(track, "track_config", lambda: cfg)
        monkeypatch.setattr(track, "send_tracker_command",
                            lambda *a, **k: pytest.fail("must not send when switched off"))
        assert track.run_race_position_polling() == 0

    def test_it_does_nothing_without_traccar(self, client, monkeypatch):
        _clear_state()
        _race_with_boat()
        cfg = dict(CFG); cfg["base_url"] = ""; cfg["token"] = ""
        monkeypatch.setattr(track, "track_config", lambda: cfg)
        monkeypatch.setattr(track, "send_tracker_command",
                            lambda *a, **k: pytest.fail("nowhere to send it"))
        assert track.run_race_position_polling() == 0


class TestTheSetting:
    def test_it_is_on_by_default(self, client):
        """The club's boats carry Queclinks, and a 45-second-old position is the
        difference between a finish timed to the second and one interpolated across
        185 m of water."""
        assert track.track_config()["race_poll_enabled"] is True

    def test_it_can_be_turned_off_and_back_on(self, client):
        from core import horn
        horn.save_hardware_config({"track_race_poll_enabled": "0"})
        assert track.track_config()["race_poll_enabled"] is False
        horn.save_hardware_config({"track_race_poll_enabled": "1"})
        assert track.track_config()["race_poll_enabled"] is True

    def test_the_settings_page_offers_it(self, logged_in_client):
        html = logged_in_client.get("/admin/settings").get_data(as_text=True)
        assert "track_race_poll_enabled" in html
