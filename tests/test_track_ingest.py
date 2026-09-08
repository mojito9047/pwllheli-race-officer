"""Pushed positions (Traccar's forwarder) and back-filling missed fixes.

Polling costs up to a poll interval before the app sees a fix. That does not
change a recorded finish *time* — crossings are interpolated between fix
timestamps — but it delays everything the delay is visible in: the automatic
horn, and the positions competitors are shown. Traccar's forwarder posts each
fix as it decodes it, and detection runs on receipt.

The poller stays as the safety net, and gains the ability to back-fill a gap:
/api/positions returns only the latest fix per device, so an outage used to
leave a permanent hole in the track.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta

import app as ro
import core.track as track
from core.horn import hardware_config, save_hardware_config

TOKEN = "test-ingest-token"


def _fix(unique_id="SIM-1", *, lat=52.88, lon=-4.40, when=None, speed=6.0, course=270.0, device_id=7):
    """One position shaped like Traccar's JSON forwarder (PositionData)."""
    when = when or datetime.now(tz=None)
    return {
        "position": {
            "deviceId": device_id,
            "protocol": "osmand",
            "fixTime": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(when.timestamp())),
            "latitude": lat, "longitude": lon, "speed": speed, "course": course,
        },
        "device": {"id": device_id, "uniqueId": unique_id, "name": f"Boat {unique_id}"},
    }


def _enable_ingest(secret=TOKEN):
    save_hardware_config({**hardware_config(), "track_enabled": True, "track_ingest_secret": secret})


class TestParsing:
    def test_parses_a_forwarder_payload(self):
        out = track.parse_forwarded_positions(_fix("SIM-2", lat=52.9, lon=-4.41, speed=7.5))
        assert len(out) == 1
        p = out[0]
        assert p["unique_id"] == "SIM-2"
        assert p["lat"] == 52.9 and p["lon"] == -4.41
        assert p["speed_kn"] == 7.5          # Traccar carries speed in knots
        assert p["fix_time"] > 0

    def test_accepts_a_batch(self):
        assert len(track.parse_forwarded_positions([_fix("SIM-1"), _fix("SIM-2")])) == 2

    def test_skips_unusable_items_instead_of_raising(self):
        """This parses data off the network, so junk must not take the app down."""
        bad = [
            "not a dict", 42, {}, {"position": {}},
            {"position": {"latitude": 1}, "device": {"uniqueId": "X"}},          # no longitude
            {"position": {"latitude": 1, "longitude": 2}, "device": {}},         # no uniqueId
            {"position": {"latitude": "x", "longitude": 2}, "device": {"uniqueId": "X"}},
        ]
        assert track.parse_forwarded_positions(bad) == []
        assert track.parse_forwarded_positions(None) == []

    def test_falls_back_to_device_time_when_there_is_no_fix_time(self):
        payload = _fix()
        payload["position"]["deviceTime"] = payload["position"].pop("fixTime")
        assert track.parse_forwarded_positions(payload)[0]["fix_time"] > 0


class TestIngestEndpoint:
    def test_rejects_without_a_token(self, client):
        _enable_ingest()
        assert client.post("/api/track/ingest", json=_fix()).status_code == 401

    def test_rejects_a_wrong_token(self, client):
        _enable_ingest()
        resp = client.post("/api/track/ingest", json=_fix(), headers={"X-RO-Track-Token": "wrong"})
        assert resp.status_code == 401

    def test_closed_when_no_secret_is_configured(self, client):
        save_hardware_config({**hardware_config(), "track_enabled": True, "track_ingest_secret": ""})
        resp = client.post("/api/track/ingest", json=_fix(), headers={"X-RO-Track-Token": "anything"})
        assert resp.status_code == 503

    def test_accepts_and_stores_a_pushed_fix(self, client):
        _enable_ingest()
        resp = client.post("/api/track/ingest", json=_fix("SIM-9"), headers={"X-RO-Track-Token": TOKEN})
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] and body["received"] == 1 and body["stored"] == 1
        assert "SIM-9" in track.latest_positions()

    def test_rejects_a_non_json_body(self, client):
        _enable_ingest()
        resp = client.post("/api/track/ingest", data="rubbish", headers={"X-RO-Track-Token": TOKEN})
        assert resp.status_code == 400

    def test_is_exempt_from_csrf_but_not_from_the_token(self, client):
        """Traccar has no session and cannot send a CSRF token; the secret is the gate."""
        assert "api_track_ingest" in ro.CSRF_EXEMPT_ENDPOINTS
        _enable_ingest()
        # No CSRF token supplied, correct secret -> accepted.
        assert client.post("/api/track/ingest", json=_fix(), headers={"X-RO-Track-Token": TOKEN}).status_code == 200


class TestBackfill:
    def _tracker_on_a_boat(self, unique_id="SIM-5"):
        with ro.get_db() as db:
            now_iso = datetime.now().isoformat(timespec="seconds")
            cur = db.execute("INSERT INTO boats (boat_name, sail_no, status, created_at, updated_at)"
                             " VALUES ('Backfill Boat', 'GBR9', 'ACTIVE', ?, ?)", (now_iso, now_iso))
            boat_id = int(cur.lastrowid)
            db.execute("INSERT INTO trackers (unique_id, traccar_device_id, label, boat_id, active, updated_at)"
                       " VALUES (?, '', 'bf', ?, 1, ?)",
                       (unique_id, boat_id, datetime.now().isoformat(timespec="seconds")))
            db.commit()
        return boat_id

    def test_inserts_older_fixes_that_the_live_path_would_drop(self, client):
        """insert_positions only takes fixes newer than the newest stored, which
        is right for the live feed but would discard every back-filled fix."""
        now = time.time()
        track.insert_positions([{"device_id": "1", "unique_id": "SIM-6", "name": "", "lat": 52.88,
                                 "lon": -4.40, "speed_kn": 5, "course_deg": 0, "fix_time": now}])
        older = [{"device_id": "1", "unique_id": "SIM-6", "name": "", "lat": 52.87, "lon": -4.40,
                  "speed_kn": 5, "course_deg": 0, "fix_time": now - 60}]
        assert track.insert_positions(older) == 0            # live path drops it
        assert track.insert_backfilled_positions(older) == 1  # back-fill keeps it

    def test_does_not_duplicate_a_fix_already_stored(self, client):
        now = time.time()
        rows = [{"device_id": "1", "unique_id": "SIM-7", "name": "", "lat": 52.88, "lon": -4.40,
                 "speed_kn": 5, "course_deg": 0, "fix_time": now - 30}]
        assert track.insert_backfilled_positions(rows) == 1
        assert track.insert_backfilled_positions(rows) == 0

    def test_asks_traccar_only_for_the_gap(self, client, monkeypatch):
        self._tracker_on_a_boat("SIM-5")
        now = time.time()
        track.insert_positions([{"device_id": "3", "unique_id": "SIM-5", "name": "", "lat": 52.88,
                                 "lon": -4.40, "speed_kn": 5, "course_deg": 0, "fix_time": now - 300}])
        asked = {}
        monkeypatch.setattr(track, "traccar_list_devices", lambda cfg: [{"id": 3, "uniqueId": "SIM-5"}])

        def fake_get(cfg, path, timeout=4.0):
            asked["path"] = path
            return [{"deviceId": 3, "latitude": 52.881, "longitude": -4.401, "speed": 5.0, "course": 90.0,
                     "fixTime": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - 200))}]

        monkeypatch.setattr(track, "_traccar_get", fake_get)
        cfg = {**track.track_config(), "base_url": "https://relay/traccar", "token": "t"}
        inserted, error = track.backfill_positions(cfg, now=now)
        assert error is None and inserted == 1
        assert "deviceId=3" in asked["path"] and "from=" in asked["path"] and "to=" in asked["path"]

    def test_the_live_poll_storing_first_does_not_hide_the_gap(self, client, monkeypatch):
        """The poll stores each device's latest fix before the back-fill runs.

        Measuring the gap after that makes it look like seconds, so an outage is
        never fetched — a real 100 s outage recovered exactly one fix per boat.
        The caller passes the pre-poll snapshot instead.
        """
        self._tracker_on_a_boat("SIM-13")
        now = time.time()
        # Where the track had got to before the outage...
        track.insert_positions([{"device_id": "9", "unique_id": "SIM-13", "name": "", "lat": 52.88,
                                 "lon": -4.40, "speed_kn": 5, "course_deg": 0, "fix_time": now - 300}])
        since = track._latest_fix_times()
        # ...then the poll stores the newest fix, closing the apparent gap.
        track.insert_positions([{"device_id": "9", "unique_id": "SIM-13", "name": "", "lat": 52.885,
                                 "lon": -4.405, "speed_kn": 5, "course_deg": 0, "fix_time": now - 2}])
        asked = {}
        monkeypatch.setattr(track, "traccar_list_devices", lambda cfg: [{"id": 9, "uniqueId": "SIM-13"}])

        def fake_get(cfg, path, timeout=4.0):
            asked["path"] = path
            return [{"deviceId": 9, "latitude": 52.882, "longitude": -4.402, "speed": 5.0, "course": 90.0,
                     "fixTime": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - 150))}]

        monkeypatch.setattr(track, "_traccar_get", fake_get)
        cfg = {**track.track_config(), "base_url": "https://relay/traccar", "token": "t"}
        # Without the snapshot the gap reads as 2 s and nothing is fetched.
        assert track.backfill_positions(cfg, now=now) == (0, None)
        assert "path" not in asked
        # With it, the outage window is requested and filled.
        inserted, error = track.backfill_positions(cfg, now=now, since=since)
        assert error is None and inserted == 1
        assert "deviceId=9" in asked["path"]

    def test_skips_when_the_gap_is_just_normal_spacing(self, client, monkeypatch):
        self._tracker_on_a_boat("SIM-8")
        now = time.time()
        track.insert_positions([{"device_id": "4", "unique_id": "SIM-8", "name": "", "lat": 52.88,
                                 "lon": -4.40, "speed_kn": 5, "course_deg": 0, "fix_time": now - 5}])
        called = []
        monkeypatch.setattr(track, "traccar_list_devices", lambda cfg: called.append(1) or [])
        cfg = {**track.track_config(), "base_url": "https://relay/traccar", "token": "t"}
        assert track.backfill_positions(cfg, now=now) == (0, None)
        assert not called                    # no request at all for a 5 s gap

    def test_no_traccar_configured_is_a_no_op(self, client):
        cfg = {**track.track_config(), "base_url": "", "token": ""}
        assert track.backfill_positions(cfg) == (0, None)


class TestStatusReportsPush:
    def test_status_shows_whether_ingest_is_configured(self, client):
        _enable_ingest()
        assert track.track_runtime_status()["ingest_configured"] is True
        save_hardware_config({**hardware_config(), "track_ingest_secret": ""})
        assert track.track_runtime_status()["ingest_configured"] is False

    def test_status_counts_forwarded_fixes(self, client):
        _enable_ingest()
        before = track.track_runtime_status().get("forward_count", 0)
        client.post("/api/track/ingest", json=_fix("SIM-10"), headers={"X-RO-Track-Token": TOKEN})
        after = track.track_runtime_status()
        assert after["forward_count"] > before
        assert after["last_forward_age"] is not None


class TestPushStatusIsVisible:
    """A token mismatch fails silently: the app polls on, only the horn is late.

    The counters existed from v0.189 but nothing rendered them, so Settings had
    no way to tell a working forwarder from one that had never connected.
    """

    def test_says_nothing_has_arrived_when_push_is_armed_but_silent(self, client):
        text = track.push_status_text(configured=True, forward_count=0)
        assert "no fix has arrived" in text
        assert "forward.header" in text          # names the thing to go and check

    def test_says_polling_only_when_no_token_is_set(self, client):
        assert "not configured" in track.push_status_text(configured=False, forward_count=0)

    def test_reports_the_count_and_how_long_ago(self, client):
        text = track.push_status_text(configured=True, forward_count=1284,
                                      last_forward_at=time.time() - 90)
        assert "1,284 fixes" in text and "1 min ago" in text and "working" in text

    def test_one_fix_is_not_1_fixes(self, client):
        assert "1 fix received" in track.push_status_text(True, 1, time.time())

    def test_the_settings_page_shows_it(self, logged_in_client):
        _enable_ingest()
        logged_in_client.post("/api/track/ingest", json=_fix("SIM-11"),
                              headers={"X-RO-Track-Token": TOKEN})
        html = logged_in_client.get("/admin/settings").get_data(as_text=True)
        assert "Push: working" in html

    def test_the_settings_page_warns_when_push_never_arrives(self, logged_in_client, monkeypatch):
        _enable_ingest()
        # setitem/delitem so the shared monitor state is put back afterwards.
        monkeypatch.setitem(track.TRACK_MONITOR_STATE, "forward_count", 0)
        monkeypatch.delitem(track.TRACK_MONITOR_STATE, "last_forward_at", raising=False)
        html = logged_in_client.get("/admin/settings").get_data(as_text=True)
        assert "no fix has arrived" in html


class TestBadTrackerClock:
    """One fix dated in the future must not shut a tracker out for good.

    The live path only accepts fixes newer than the newest stored for that
    device. A tracker whose clock is wrong (or a stale test fix) would otherwise
    set that high-water mark far ahead and silently drop every real fix after it
    — which is exactly what happened during push testing.
    """

    def test_a_future_dated_fix_is_not_stored(self, client):
        now = time.time()
        rows = [{"device_id": "1", "unique_id": "SIM-11", "name": "", "lat": 52.88, "lon": -4.40,
                 "speed_kn": 5, "course_deg": 0, "fix_time": now + 3600}]
        assert track.insert_positions(rows) == 0

    def test_real_fixes_still_land_after_one_slipped_through(self, client):
        now = time.time()
        # Simulate the damage already done: a stored fix an hour ahead.
        track.insert_backfilled_positions([
            {"device_id": "1", "unique_id": "SIM-12", "name": "", "lat": 52.88, "lon": -4.40,
             "speed_kn": 5, "course_deg": 0, "fix_time": now + 3600}])
        good = [{"device_id": "1", "unique_id": "SIM-12", "name": "", "lat": 52.881, "lon": -4.401,
                 "speed_kn": 5, "course_deg": 0, "fix_time": now}]
        assert track.insert_positions(good) == 1


class TestSimulatorContract:
    """The test script and the app must agree on the payload shape.

    scripts/simulate_trackers.py --forward-to posts what it believes Traccar's
    forwarder sends; if the two drift apart the simulator would quietly test
    nothing.
    """

    def _simulator(self):
        import importlib.util
        import pathlib
        path = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "simulate_trackers.py"
        spec = importlib.util.spec_from_file_location("simulate_trackers", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_the_app_parses_what_the_simulator_sends(self):
        sim = self._simulator()
        body = sim.forward_body("SIM-4", (52.8845, -4.3995), time.time(), 6.4, 271.0)
        parsed = track.parse_forwarded_positions(body)
        assert len(parsed) == 1
        assert parsed[0]["unique_id"] == "SIM-4"
        assert round(parsed[0]["lat"], 4) == 52.8845
        assert parsed[0]["speed_kn"] == 6.4

    def test_the_simulator_can_send_a_battery_the_app_stores(self):
        """--battery is only worth having if the level survives the round trip."""
        sim = self._simulator()
        body = sim.forward_body("SIM-4", (52.8845, -4.3995), time.time(), 6.4, 271.0, 42.0)
        parsed = track.parse_forwarded_positions(body)
        assert parsed[0]["battery_pct"] == 42.0

    def test_the_simulator_omits_the_battery_when_not_asked(self):
        """The older units send no level at all, and that has to stay reachable."""
        sim = self._simulator()
        body = sim.forward_body("SIM-4", (52.8845, -4.3995), time.time(), 6.4, 271.0)
        assert "attributes" not in body["position"]
        assert track.parse_forwarded_positions(body)[0].get("battery_pct") is None

    def test_the_simulated_fleet_spans_the_battery_colours(self):
        """A column of identical numbers would not show the thresholds working."""
        sim = self._simulator()
        devices = ["SIM-1", "SIM-2", "SIM-3", "SIM-4", "SIM-5"]
        rags = {track.battery_rag(sim.battery_for(d, devices, 95.0)) for d in devices}
        assert {"green", "amber", "red"} <= rags

    def test_the_simulator_reports_near_the_line(self):
        """--fast-near-line needs a distance-to-line measure that actually works."""
        sim = self._simulator()
        line_a, line_b = (52.8791, -4.3993), (sim.BRIDGE_LAT, sim.BRIDGE_LON)
        on_line = ((line_a[0] + line_b[0]) / 2, (line_a[1] + line_b[1]) / 2)
        assert sim.dist_to_line_m(on_line, line_a, line_b) < 50
        far = (line_a[0] + 0.05, line_a[1])
        assert sim.dist_to_line_m(far, line_a, line_b) > 1000
