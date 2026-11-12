"""Trackers that go out of signal: a passage race across the Irish Sea.

An ISORA race from Pwllheli to Dún Laoghaire leaves both coasts' masts behind for
hours in mid-channel. The club's GL521MGs keep recording through it and send what
they saved once they are back in signal -- *after* their first live fix, because
they run <Buffer Mode> 1 in AT+GTSRI (protocol §3.3.5: "buffered messages will be
sent after the real-time messages").

Before this, that went wrong three ways:

* The push stored only fixes newer than the newest stored, so every saved fix was
  thrown away, and the back-fill looked forward from the newest one and never saw
  the hole behind it. The mid-channel hours were simply missing.
* The app went on asking each racing GL for its position every 20 s. A request
  cannot reach a tracker out of signal, and Traccar holds each one to deliver when
  it reconnects: about 700 waiting on the far side of a four-hour crossing.
* An open chart asks only for fixes newer than the newest it has, so it could never
  draw fixes that arrived behind that.
"""
from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path

import app as ro
import core.track as track
from core.horn import hardware_config, save_hardware_config
from tests.test_race_position_polling import CFG, GL521MG, _clear_state, _race_with_boat
from tests.test_race_replay import _seed_race, _track
from tests.test_track import _make_boat, _make_entry, _make_race, _seed_full_course_track

TOKEN = "offshore-ingest-token"
HOUR = 3600.0


def _push(unique_id, t, lat=53.1, lon=-5.2):
    """One fix as Traccar's forwarder posts it, at epoch ``t``."""
    return {
        "position": {"deviceId": 7, "protocol": "gl200",
                     "fixTime": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t)),
                     "latitude": lat, "longitude": lon, "speed": 6.0, "course": 300.0},
        "device": {"id": 7, "uniqueId": unique_id, "name": unique_id},
    }


def _post(client, payload):
    return client.post("/api/track/ingest", json=payload, headers={"X-RO-Track-Token": TOKEN})


def _stored_times(unique_id):
    with track.get_track_db() as db:
        rows = db.execute("SELECT fix_time FROM track_positions WHERE unique_id = ? ORDER BY fix_time",
                          (unique_id,)).fetchall()
    return [r["fix_time"] for r in rows]


def _row(unique_id, t, lat=53.1, lon=-5.2):
    return {"device_id": "7", "unique_id": unique_id, "name": "", "lat": lat, "lon": lon,
            "speed_kn": 6.0, "course_deg": 300.0, "fix_time": t}


def _on_a_boat(unique_id):
    with ro.get_db() as db:
        now_iso = datetime.now().isoformat(timespec="seconds")
        cur = db.execute("INSERT INTO boats (boat_name, sail_no, status, created_at, updated_at)"
                         " VALUES ('Passage', 'GBR7', 'ACTIVE', ?, ?)", (now_iso, now_iso))
        boat_id = int(cur.lastrowid)
        db.execute("INSERT INTO trackers (unique_id, traccar_device_id, label, boat_id, active, updated_at)"
                   " VALUES (?, '', 'gl', ?, 1, ?)", (unique_id, boat_id, now_iso))
        db.commit()
    return boat_id


def _traccar_cfg():
    return {**track.track_config(), "base_url": "https://relay/traccar", "token": "t"}


class TestSavedFixesPushedAfterTheLiveOne:
    def test_they_are_stored(self, client):
        """Back in signal, the live fix comes first and four hours of saved ones follow."""
        save_hardware_config({**hardware_config(), "track_enabled": True, "track_ingest_secret": TOKEN})
        now = float(int(time.time()))
        assert _post(client, _push("GL-A", now - 4 * HOUR - 60)).get_json()["stored"] == 1   # last before the gap
        assert _post(client, _push("GL-A", now)).get_json()["stored"] == 1                   # live, on reconnecting
        saved = [now - 4 * HOUR + i * 60 for i in range(5)]
        for t in saved:
            assert _post(client, _push("GL-A", t)).get_json()["stored"] == 1
        assert _stored_times("GL-A") == [now - 4 * HOUR - 60, *saved, now]

    def test_a_push_the_relay_sends_twice_is_stored_once(self, client):
        """The relay resends a push it got no answer for, so a repeat is normal."""
        save_hardware_config({**hardware_config(), "track_enabled": True, "track_ingest_secret": TOKEN})
        now = float(int(time.time()))
        _post(client, _push("GL-B", now))
        assert _post(client, _push("GL-B", now - 600)).get_json()["stored"] == 1
        assert _post(client, _push("GL-B", now - 600)).get_json()["stored"] == 0
        assert _post(client, _push("GL-B", now)).get_json()["stored"] == 0
        assert len(_stored_times("GL-B")) == 2

    def test_a_late_fix_does_not_become_the_newest(self, client):
        """The newest fix is what the next live one is compared with, and what the
        Trackers page reads as 'last reported'."""
        now = time.time()
        track.insert_positions([_row("GL-C", now)])
        assert track.insert_positions([_row("GL-C", now - 900)], accept_late=True) == 1
        assert track._latest_fix_times(["GL-C"])["GL-C"] == now
        assert track.insert_positions([_row("GL-C", now + 20)]) == 1

    def test_the_poll_still_ignores_an_older_fix(self, client):
        """The poll only ever sees each device's latest fix, so anything older from
        it is a repeat, and it keeps the cheap rule."""
        now = time.time()
        track.insert_positions([_row("GL-D", now)])
        assert track.insert_positions([_row("GL-D", now - 900)]) == 0


class TestTheHoleIsAskedForAgain:
    def test_a_fix_that_closes_a_long_hole_is_remembered(self, client):
        now = time.time()
        track.insert_positions([_row("GL-E", now - 4 * HOUR)])
        track.insert_positions([_row("GL-E", now)])
        assert ("GL-E", now - 4 * HOUR) in track._CLOSED_GAPS

    def test_ordinary_spacing_is_not_a_hole(self, client):
        """A GL reports once a minute; that is not something missing."""
        now = time.time()
        track.insert_positions([_row("GL-F", now - 60)])
        track.insert_positions([_row("GL-F", now)])
        assert not track._CLOSED_GAPS

    def test_the_back_fill_asks_traccar_for_the_hole_behind_the_newest_fix(self, client, monkeypatch):
        """The poll loop's own snapshot already holds the live fix, so looking
        forward finds nothing; the hole is behind it."""
        _on_a_boat("GL-G")
        now = float(int(time.time()))
        track.insert_positions([_row("GL-G", now - 4 * HOUR)])
        track.insert_positions([_row("GL-G", now - 2)])          # back in signal
        asked = []
        monkeypatch.setattr(track, "traccar_list_devices", lambda cfg: [{"id": 3, "uniqueId": "GL-G"}])

        def fake_get(cfg, path, timeout=4.0):
            asked.append(path)
            return [{"deviceId": 3, "latitude": 53.2, "longitude": -5.5, "speed": 6.0, "course": 300.0,
                     "fixTime": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - 3 * HOUR + i * 60))}
                    for i in range(3)]

        monkeypatch.setattr(track, "_traccar_get", fake_get)
        # The poller's clock, which has moved on since the fix was stored.
        inserted, error = track.backfill_positions(_traccar_cfg(), now=time.time() + 1,
                                                   since=track._latest_fix_times())
        assert error is None and inserted == 3
        assert len(asked) == 1
        assert f"from={track._iso_utc(now - 4 * HOUR)}" in asked[0]
        assert f"to={track._iso_utc(now - 3)}" in asked[0]
        assert len(_stored_times("GL-G")) == 5

    def test_it_is_asked_about_again_while_the_saved_fixes_arrive_then_forgotten(self, client):
        """Saved fixes can take a while to drain, so one look is not enough."""
        found = 1_000_000.0
        track.note_closed_gap("GL-H", found - 4 * HOUR, found, now=found)
        seen = [t for t in range(0, 3000, 10) if track._due_closed_gaps(found + t)]
        assert seen == [int(s) for s in track.BACKFILL_GAP_RECHECKS_S]
        assert not track._CLOSED_GAPS

    def test_a_tracker_on_no_boat_is_not_asked_about(self, client, monkeypatch):
        """Same rule as the back-fill has always had: unassigned history belongs to nobody."""
        now = time.time()
        track.insert_positions([_row("GL-I", now - 4 * HOUR)])
        track.insert_positions([_row("GL-I", now)])
        called = []
        monkeypatch.setattr(track, "traccar_list_devices", lambda cfg: called.append(1) or [])
        assert track.backfill_positions(_traccar_cfg(), now=now, since=track._latest_fix_times()) == (0, None)
        assert not called

    def test_a_tracker_switched_off_for_days_is_asked_only_for_a_race_length(self, client, monkeypatch):
        _on_a_boat("GL-J")
        now = float(int(time.time()))
        track.note_closed_gap("GL-J", now - 5 * 24 * HOUR, now, now=now)
        asked = []
        monkeypatch.setattr(track, "traccar_list_devices", lambda cfg: [{"id": 4, "uniqueId": "GL-J"}])
        monkeypatch.setattr(track, "_traccar_get", lambda cfg, path, timeout=4.0: asked.append(path) or [])
        track.backfill_positions(_traccar_cfg(), now=now, since={})
        assert f"from={track._iso_utc(now - track.BACKFILL_GAP_MAX_HOURS * HOUR)}" in asked[0]


class TestNotAskingATrackerThatIsOutOfSignal:
    def _sent(self, monkeypatch):
        sent = []
        monkeypatch.setattr(track, "track_config", lambda: dict(CFG))
        monkeypatch.setattr(track, "send_tracker_command",
                            lambda uid, cmd, cfg=None, **kw: sent.append(kw) or (True, "ok"))
        return sent

    def test_a_quiet_tracker_is_not_asked(self, client, monkeypatch):
        _clear_state()
        _race_with_boat()                    # its last fix is now
        sent = self._sent(monkeypatch)
        later = time.time() + track.RACE_POLL_SILENT_S + 30
        assert track.run_race_position_polling(now=later) == 0
        assert sent == []

    def test_it_is_asked_again_as_soon_as_it_reports(self, client, monkeypatch):
        _clear_state()
        _race_with_boat()
        sent = self._sent(monkeypatch)
        later = time.time() + track.RACE_POLL_SILENT_S + 30
        track.run_race_position_polling(now=later)
        track.insert_positions([{**_row(GL521MG, later), "protocol": "gl200"}])
        assert track.run_race_position_polling(now=later + 1) == 1

    def test_a_tracker_that_is_reporting_is_still_asked(self, client, monkeypatch):
        """A working GL is never quieter than about 105 s: a 60 s report 45 s late."""
        _clear_state()
        _race_with_boat()
        self._sent(monkeypatch)
        assert track.run_race_position_polling(now=time.time() + 105) == 1

    def test_the_request_is_not_left_queued(self, client, monkeypatch):
        _clear_state()
        _race_with_boat()
        sent = self._sent(monkeypatch)
        track.run_race_position_polling()
        assert sent == [{"queue": False}]

    def test_traccar_is_told_not_to_queue_it_but_the_console_still_queues(self, client, monkeypatch):
        """The Commands console wants queuing: an ATC700 asleep between reports
        should still get its setparam when it wakes."""
        bodies = []
        cfg = _traccar_cfg()
        monkeypatch.setattr(track, "traccar_list_devices",
                            lambda cfg, include_unowned=False: [{"id": 46, "uniqueId": GL521MG}])
        monkeypatch.setattr(track, "_traccar_request",
                            lambda cfg, method, path, body=None, timeout=10.0: bodies.append(body))
        track.send_tracker_command(GL521MG, "AT+GTRTO=gl521m,1,,,,,,FFFF$", cfg, queue=False)
        track.send_tracker_command(GL521MG, "AT+GTRTO=gl521m,1,,,,,,FFFF$", cfg)
        assert bodies[0]["attributes"].get("noQueue") is True
        assert "noQueue" not in bodies[1]["attributes"]


class TestTheHornIsForAFinishThatJustHappened:
    def _horn(self, monkeypatch):
        fired = []
        cfg = {**track.track_config(), "finish_horn": True}
        monkeypatch.setattr(track, "track_config", lambda: cfg)
        monkeypatch.setattr(track.horn, "fire_horn", lambda *a, **k: fired.append(1))
        return fired

    def _finish(self, race_id, uid, monkeypatch):
        boat_id = _make_boat()
        track.upsert_tracker(uid, boat_id=boat_id)
        eid = _make_entry(race_id, boat_id)
        _seed_full_course_track(race_id, uid)
        assert track.run_finish_detection() >= 1
        with ro.get_db() as db:
            e = db.execute("SELECT * FROM entries WHERE id = ?", (eid,)).fetchone()
        assert e["status"] == "FINISHED" and e["finish_source"] == "gps-auto"
        return datetime.fromisoformat(e["finish_time"]).timestamp()

    def test_a_finish_found_in_late_fixes_is_recorded_without_the_horn(self, client, monkeypatch):
        fired = self._horn(monkeypatch)
        crossed = self._finish(_make_race(gps_finish=1, gps_auto=1, minutes_ago=60), "GL-K", monkeypatch)
        assert time.time() - crossed > track.GPS_FINISH_HORN_MAX_LATE_S
        assert fired == []

    def test_a_finish_that_just_happened_still_sounds_it(self, client, monkeypatch):
        fired = self._horn(monkeypatch)
        crossed = self._finish(_make_race(gps_finish=1, gps_auto=1, minutes_ago=10), "GL-L", monkeypatch)
        assert time.time() - crossed <= track.GPS_FINISH_HORN_MAX_LATE_S
        assert fired == [1]


class TestAnOpenChartRedrawsForLateFixes:
    def _live_race(self, client):
        """Alpha reported for a while after the gun, went quiet, and is back now."""
        race_id, ids, gun = _seed_race(client, boats=("Alpha",))
        gun = float(int(gun))
        now = float(int(time.time()))
        _track("SIMR-Alpha", ids["Alpha"]["boat_id"],
               [(gun + i * 60, 52.88, -4.40 - i * 1e-3) for i in range(5)] + [(now - 5, 53.2, -6.0)])
        full = track.race_track_history(race_id)
        since = max(f[0] for b in full["boats"] for f in b["fixes"])
        return race_id, gun, since, full["stored"]

    def test_the_reply_carries_a_cursor(self, client):
        race_id, _gun, _since, stored = self._live_race(client)
        with track.get_track_db() as db:
            assert stored == db.execute("SELECT MAX(id) FROM track_positions").fetchone()[0]

    def test_saved_fixes_arriving_behind_the_viewer_send_the_whole_race(self, client):
        race_id, gun, since, stored = self._live_race(client)
        saved = [_row("SIMR-Alpha", gun + 600 + i * 60) for i in range(10)]
        assert track.insert_positions(saved, accept_late=True) == 10
        reply = track.race_track_history(race_id, since_ts=since, stored_id=stored)
        assert reply["incremental"] is False
        assert len(reply["boats"][0]["fixes"]) == 16
        assert reply["stored"] > stored

    def test_nothing_late_stays_incremental(self, client):
        race_id, _gun, since, stored = self._live_race(client)
        track.insert_positions([_row("SIMR-Alpha", since + 2)])
        reply = track.race_track_history(race_id, since_ts=since, stored_id=stored)
        assert reply["incremental"] is True and len(reply["boats"][0]["fixes"]) == 1

    def test_a_report_a_minute_late_does_not_resend_everything(self, client):
        """A GL's own report lands 45 s late in ordinary running; the whole race every
        minute for that would undo what incremental replies are for."""
        race_id, _gun, since, stored = self._live_race(client)
        track.insert_positions([_row("SIMR-Alpha", since - 45)], accept_late=True)
        assert track.race_track_history(race_id, since_ts=since, stored_id=stored)["incremental"] is True

    def test_a_viewer_with_no_cursor_is_answered_as_before(self, client):
        """A page loaded before this change sends no cursor; it must not break."""
        race_id, gun, since, _stored = self._live_race(client)
        track.insert_positions([_row("SIMR-Alpha", gun + 600)], accept_late=True)
        assert track.race_track_history(race_id, since_ts=since)["incremental"] is True

    def test_the_endpoint_takes_the_cursor(self, logged_in_client):
        race_id, gun, since, stored = self._live_race(logged_in_client)
        track.insert_positions([_row("SIMR-Alpha", gun + 600)], accept_late=True)
        reply = logged_in_client.get(f"/api/race/{race_id}/track?since={since}&stored={stored}").get_json()
        assert reply["ok"] and reply["incremental"] is False

    def test_both_viewers_send_it_back(self):
        static = Path(__file__).resolve().parent.parent / "static"
        for name in ("race_replay.js", "bar_display.js"):
            assert "&stored=" in (static / name).read_text(encoding="utf-8"), name
