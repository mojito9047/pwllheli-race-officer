"""Tracker battery on the Trackers page, and the warning on the dashboard.

The boats carry battery asset trackers run at a reporting rate far above what they were
designed for, so the app's own documentation tells you to charge them between race days.
It just never said *which* ones needed it.

The level was already arriving. Traccar puts it in a position's ``attributes`` and the
GL521MG reports it (@Track protocol V3.05, the ``<Battery Percentage>`` field) — and both
of the app's ingest paths built a fixed dict of lat/lon/speed/course/time and dropped the
attributes on the floor. Confirmed against the club's live Traccar while building this:
the key is ``batteryLevel``, and one unit was sitting at 25%.

It is stored on the *fix* rather than on the tracker, so the Trackers page reads it
exactly as it already reads "last reported" — the newest stored fix per device — and a
race's drain is in the history for nothing extra.
"""
from __future__ import annotations

import time

import pathlib
import pytest

import app as ro
from core import track


class TestReadingItOffAPosition:
    def test_the_key_traccar_actually_uses(self):
        """batteryLevel, confirmed against the club's own Traccar."""
        assert track.battery_from_attributes({"batteryLevel": 87}) == 87.0

    @pytest.mark.parametrize("attrs", [
        {"batterylevel": 40}, {"battery_level": 40}, {"batteryPercent": 40},
    ])
    def test_and_the_spellings_that_turn_up_elsewhere(self, attrs):
        """Traccar names it differently by protocol and firmware, and the club may not
        always be running these units."""
        assert track.battery_from_attributes(attrs) == 40.0

    def test_volts_are_not_treated_as_a_percentage(self):
        """`battery` is volts. Converting it would need a cell chemistry, and guessing
        one would put a made-up number on the page people use to decide what to charge."""
        assert track.battery_from_attributes({"battery": 4.1}) is None

    def test_the_teltonika_key(self):
        """io113 is Teltonika's battery level. The ATC700 sends this and no batteryLevel,
        so without it the tracker that flattens in half a day was the one with no warning."""
        assert track.battery_from_attributes({"io113": 70}) == 70.0

    def test_a_teltonika_position_as_it_actually_arrives(self):
        """The real attribute bag off the club's ATC700, volts and all. The percentage has
        to come from io113 and must not come from `battery`."""
        assert track.battery_from_attributes({
            "battery": 3.715, "distance": 0.41, "event": 0, "hdop": 0.5, "io113": 70,
            "io13266": 1, "io13267": 10, "io1600": 0, "io200": 0, "io68": 0, "io69": 1,
            "io70": 336, "io800": 0, "motion": False, "odometer": 0, "pdop": 0.8,
            "priority": 1, "rssi": 4, "sat": 25, "totalDistance": 192.58,
        }) == 70.0

    def test_batterylevel_still_wins_over_io113(self):
        """A device sending both is a device whose own percentage we should prefer;
        io113 is the fallback, not an override."""
        assert track.battery_from_attributes({"batteryLevel": 88, "io113": 70}) == 88.0

    def test_no_other_io_port_is_read_as_a_battery(self):
        """io113 earned its place by tracking the voltage over 3.5 h. The neighbouring
        ports carry currents, counters and flags, and one of them reading 70 must not
        become a battery percentage."""
        for key in ("io68", "io69", "io70", "io200", "io800", "io1600", "io13266"):
            assert track.battery_from_attributes({key: 70}) is None

    def test_a_percentage_out_of_range_is_ignored(self):
        assert track.battery_from_attributes({"batteryLevel": 140}) is None
        assert track.battery_from_attributes({"batteryLevel": -5}) is None

    def test_nonsense_does_not_raise(self):
        for attrs in (None, "", [], {"batteryLevel": "flat"}, {"batteryLevel": None}, {}):
            assert track.battery_from_attributes(attrs) is None

    def test_zero_is_a_real_reading_and_kept(self):
        """A flat battery is not a missing one."""
        assert track.battery_from_attributes({"batteryLevel": 0}) == 0.0


class TestALevelIsOnlyAsCurrentAsItsFix:
    """A battery reading arrives on a fix. Once that fix is old, so is the reading —
    and the column exists to decide what to put on charge."""

    FRESH = 60.0
    STALE = track.TRACK_RAG_AMBER_S + 60.0

    def test_a_recent_reading_is_presented_plainly(self):
        r = track.battery_reading(65.0, self.FRESH)
        assert r["battery_text"] == "65%"
        assert r["battery_stale"] is False
        assert r["battery_title"] == "Reported with the last fix"

    def test_an_old_reading_says_so(self):
        """The reported case: 65% sat next to '1 d ago' reads as the level now."""
        r = track.battery_reading(65.0, 26 * 3600.0)
        assert r["battery_stale"] is True
        assert "when it last reported" in r["battery_title"]
        assert "nothing since" in r["battery_title"]

    def test_the_number_is_kept_rather_than_dashed(self):
        """A stale *low* reading is the most useful line on the page, and it is what
        the dashboard warning fires on — hiding it here would make the two disagree."""
        r = track.battery_reading(8.0, 3 * 86400.0)
        assert r["battery_text"] == "8%"
        assert r["battery_rag"] == "red"

    def test_no_reading_is_a_dash_however_old_the_fix(self):
        for age in (self.FRESH, self.STALE, None):
            r = track.battery_reading(None, age)
            assert r["battery_text"] == "—"
            assert r["battery_stale"] is False

    def test_an_unknown_age_is_not_called_stale(self):
        """Never-reported devices have no age; that is not evidence of staleness."""
        assert track.battery_reading(65.0, None)["battery_stale"] is False

    def test_the_boundary_is_the_still_reporting_window(self):
        assert track.battery_reading(65.0, track.TRACK_RAG_AMBER_S - 1)["battery_stale"] is False
        assert track.battery_reading(65.0, track.TRACK_RAG_AMBER_S + 1)["battery_stale"] is True

    def test_the_seen_on_the_network_table_ages_the_battery_by_its_own_fix(self):
        """Traccar's lastUpdate and our newest stored fix are two different clocks.
        Traccar can have heard from a device a moment ago while the newest fix we hold
        is a day old — and it is the fix that carried the battery."""
        import inspect
        src = inspect.getsource(track.unregistered_traccar_devices)
        assert "fix[\"fix_time\"]" in src, "battery age must come from the stored fix"
        # the device-level age is still what the Last reported column uses
        assert '"age": age' in src

    def test_both_tables_use_the_one_helper(self):
        """Two copies of one rule is this codebase's recurring bug; the registered and
        the seen-on-the-network tables must not drift apart."""
        import inspect
        for fn in (track.tracker_report_status, track.unregistered_traccar_devices):
            assert "battery_reading(" in inspect.getsource(fn)


class TestThePageAndItsPollAgree:
    """The Trackers page renders the battery server-side and then repaints it from
    /api/trackers/status. The endpoint used to send only rag and text, so the repaint
    read undefined and blanked a column that had a value in it — a page that was right
    until its own poll ran fifteen seconds later."""

    def test_the_status_endpoint_sends_every_field_the_page_repaints(self, logged_in_client):
        import re
        uid = "POLL-1"
        track.upsert_tracker(uid, label="Tracker P")
        track.insert_positions([{"unique_id": uid, "name": uid, "lat": 52.88, "lon": -4.40,
                                 "speed_kn": 0.0, "course_deg": 0.0,
                                 "fix_time": time.time(), "battery_pct": 42.0}])
        page = pathlib.Path("templates/trackers.html").read_text(encoding="utf-8")
        wanted = set(re.findall(r"r\.(battery[a-z_]*)", page))
        assert wanted, "the page no longer repaints the battery — has this moved?"

        body = logged_in_client.get("/api/trackers/status").get_json()
        entry = (body.get("report") or {}).get(uid)
        assert entry is not None, "the tracker we just gave a fix is missing from the report"
        missing = wanted - set(entry)
        assert not missing, f"the page reads {sorted(missing)}, the endpoint does not send it"
        # and the values must be the real ones, not merely present
        assert entry["battery_text"] == "42%"
        assert entry["battery_rag"] == "green"
        assert entry["battery_stale"] is False


class TestTheRagBuckets:
    @pytest.mark.parametrize("pct,expected", [
        (100.0, "green"), (26.0, "green"),
        (25.0, "amber"), (11.0, "amber"),
        (10.0, "red"), (0.0, "red"),
        (None, "none"),
    ])
    def test_the_boundaries(self, pct, expected):
        assert track.battery_rag(pct) == expected

    def test_the_thresholds_are_about_race_days(self):
        """Below 25% is a unit to charge before Saturday; below 10% may not last a race."""
        assert track.BATTERY_LOW_PCT == 25.0
        assert track.BATTERY_CRITICAL_PCT == 10.0


class TestBothIngestPathsKeepIt:
    """Two paths take fixes in — the Traccar poll and the push forwarder — and a fix
    arriving by one route must not know less than one arriving by the other."""

    def test_the_poll_keeps_it(self, client, monkeypatch):
        monkeypatch.setattr(track, "traccar_list_devices",
                            lambda cfg, include_unowned=False: [{"id": 1, "uniqueId": "IMEI-1",
                                                                 "name": "Kite"}])
        monkeypatch.setattr(track, "_traccar_get", lambda cfg, path, timeout=4.0: [
            {"deviceId": 1, "latitude": 52.88, "longitude": -4.40, "speed": 5.0,
             "course": 90.0, "fixTime": "2026-08-05T12:00:00Z",
             "attributes": {"batteryLevel": 64}},
        ])
        monkeypatch.setattr(track, "track_config", lambda: dict(
            enabled=True, base_url="http://x", token="t", poll_seconds=5,
            retention_days=90, sim_enabled=False, finish_horn=False,
            rounding_radius_m=50, ingest_secret=""))
        rows, err = track.fetch_positions()
        assert err is None and rows, err
        assert rows[0]["battery_pct"] == 64.0

    def test_the_push_forwarder_keeps_it(self, client):
        rows = track.parse_forwarded_positions({
            "device": {"uniqueId": "IMEI-2", "name": "Kite"},
            "position": {"deviceId": 2, "latitude": 52.88, "longitude": -4.40,
                         "speed": 5.0, "course": 90.0, "fixTime": "2026-08-05T12:00:00Z",
                         "attributes": {"batteryLevel": 33}},
        })
        assert rows and rows[0]["battery_pct"] == 33.0

    def test_a_fix_without_a_battery_is_still_a_fix(self, client):
        rows = track.parse_forwarded_positions({
            "device": {"uniqueId": "IMEI-3"},
            "position": {"deviceId": 3, "latitude": 52.88, "longitude": -4.40,
                         "fixTime": "2026-08-05T12:00:00Z"},
        })
        assert rows and rows[0]["battery_pct"] is None


class TestItSurvivesStorage:
    def test_the_column_exists(self, client):
        track.init_track_db()
        with track.get_track_db() as db:
            cols = {r["name"] for r in db.execute("PRAGMA table_info(track_positions)")}
        assert "battery_pct" in cols

    def test_an_older_database_gains_the_column(self, client, monkeypatch, tmp_path):
        """The hut's track database predates this, so the migration has to run."""
        path = tmp_path / "old_track.db"
        monkeypatch.setattr(track, "TRACK_DB_PATH", path)
        monkeypatch.setattr(track, "TRACK_DB_INITIALIZED", False)
        import sqlite3
        con = sqlite3.connect(path)
        con.execute("CREATE TABLE track_positions (id INTEGER PRIMARY KEY AUTOINCREMENT,"
                    " device_id TEXT, unique_id TEXT NOT NULL, name TEXT, lat REAL NOT NULL,"
                    " lon REAL NOT NULL, speed_kn REAL, course_deg REAL,"
                    " fix_time REAL NOT NULL, server_time REAL NOT NULL)")
        con.commit(); con.close()
        track.init_track_db()
        with track.get_track_db() as db:
            cols = {r["name"] for r in db.execute("PRAGMA table_info(track_positions)")}
        assert "battery_pct" in cols

    def test_it_round_trips_through_the_live_insert_path(self, client):
        """insert_positions, not insert_backfilled_positions. There are two, and the
        first pass at this patched only the gap-filler — so every fix the poll stored
        would have carried no battery at all."""
        track.insert_positions([{"unique_id": "BAT-1", "name": "BAT-1", "lat": 52.88,
                                 "lon": -4.40, "speed_kn": 0.0, "course_deg": 0.0,
                                 "fix_time": time.time(), "battery_pct": 41.0}])
        assert track.latest_positions()["BAT-1"]["battery_pct"] == 41.0

    def test_and_through_the_backfill_path(self, client):
        track.insert_backfilled_positions([
            {"unique_id": "BAT-2", "name": "BAT-2", "lat": 52.88, "lon": -4.40,
             "speed_kn": 0.0, "course_deg": 0.0, "fix_time": time.time(),
             "battery_pct": 55.0}])
        assert track.latest_positions()["BAT-2"]["battery_pct"] == 55.0


class TestTheTrackersPage:
    def _battery_cell(self, client, uid):
        """The rendered battery cell for one tracker.

        Not the whole page: the live-repaint script carries the same words, so a
        substring search over the document passes no matter what the cell says. The
        first version of these tests did exactly that.
        """
        import re
        html = client.get("/admin/trackers").get_data(as_text=True)
        m = re.search(r'<td class="small tracker-battery" data-uid="%s".*?</td>' % re.escape(uid),
                      html, re.S)
        assert m, f"no battery cell rendered for {uid}"
        return m.group(0)

    def _tracker_with_battery(self, uid="BAT-P", pct=18.0, with_boat=True):
        boat_id = None
        if with_boat:
            now = "2026-08-05T12:00:00"
            with ro.get_db() as db:
                boat_id = int(db.execute(
                    "INSERT INTO boats (boat_name, sail_no, status, created_at, updated_at)"
                    " VALUES ('Kittiwake', 'GBR 7', 'ACTIVE', ?, ?)", (now, now)).lastrowid)
                db.commit()
        track.upsert_tracker(uid, label="Tracker A", boat_id=boat_id)
        if pct is not None:
            track.insert_positions([{"unique_id": uid, "name": uid, "lat": 52.88, "lon": -4.40,
                                     "speed_kn": 0.0, "course_deg": 0.0,
                                     "fix_time": time.time(), "battery_pct": pct}])
        return uid

    def test_the_status_carries_the_level_and_its_bucket(self, client):
        uid = self._tracker_with_battery(pct=18.0)
        status = track.tracker_report_status()[uid]
        assert status["battery"] == 18.0
        assert status["battery_rag"] == "amber"
        assert status["battery_text"] == "18%"

    def test_a_tracker_that_reports_none_shows_a_dash_not_a_zero(self, client):
        """Nought per cent reads as flat. A dash reads as not known, which is the truth
        and is what stops somebody charging a unit that was never going to say."""
        uid = self._tracker_with_battery(uid="BAT-Q", pct=None)
        track.insert_positions([{"unique_id": uid, "name": uid, "lat": 52.88, "lon": -4.40,
                                 "speed_kn": 0.0, "course_deg": 0.0, "fix_time": time.time()}])
        status = track.tracker_report_status()[uid]
        assert status["battery"] is None
        assert status["battery_text"] == "—"
        assert status["battery_rag"] == "none"

    def test_the_page_has_a_battery_column(self, logged_in_client):
        self._tracker_with_battery(uid="BAT-R", pct=64.0)
        html = logged_in_client.get("/admin/trackers").get_data(as_text=True)
        head = html.split("<thead>")[1].split("</thead>")[0]
        assert "Battery" in head
        assert "64%" in html

    def test_a_level_off_an_old_fix_is_marked_on_the_page(self, logged_in_client):
        """The reported case: 65% sat beside "1 d ago" with nothing to say the level is
        exactly as old as the fix. The helper knowing it is stale is no use if the page
        does not show it."""
        uid = "BAT-S"
        track.upsert_tracker(uid, label="Tracker S")
        old_fix = time.time() - 26 * 3600
        track.insert_positions([{"unique_id": uid, "name": uid, "lat": 52.88, "lon": -4.40,
                                 "speed_kn": 0.0, "course_deg": 0.0,
                                 "fix_time": old_fix, "battery_pct": 65.0}])
        cell = self._battery_cell(logged_in_client, uid)
        assert "65%" in cell, "the level itself is still shown — a stale low one is the useful case"
        assert "last seen" in cell
        assert "when it last reported" in cell      # the tooltip

    def test_a_current_level_is_not_marked(self, logged_in_client):
        uid = self._tracker_with_battery(uid="BAT-T", pct=71.0)
        cell = self._battery_cell(logged_in_client, uid)
        assert "71%" in cell
        assert "last seen" not in cell


class TestTheDashboardWarning:
    def _assigned_tracker(self, uid, pct):
        now = "2026-08-05T12:00:00"
        with ro.get_db() as db:
            boat_id = int(db.execute(
                "INSERT INTO boats (boat_name, sail_no, status, created_at, updated_at)"
                " VALUES (?, 'GBR 1', 'ACTIVE', ?, ?)", (f"Boat {uid}", now, now)).lastrowid)
            db.commit()
        track.upsert_tracker(uid, label=f"Label {uid}", boat_id=boat_id)
        track.insert_positions([{"unique_id": uid, "name": uid, "lat": 52.88, "lon": -4.40,
                                 "speed_kn": 0.0, "course_deg": 0.0,
                                 "fix_time": time.time(), "battery_pct": pct}])
        return uid

    def test_a_low_tracker_is_reported(self, client):
        self._assigned_tracker("LOW-1", 15.0)
        warned = track.low_battery_trackers()
        assert [w["unique_id"] for w in warned] == ["LOW-1"]
        assert warned[0]["boat_name"] == "Boat LOW-1"

    def test_a_healthy_one_is_not(self, client):
        self._assigned_tracker("OK-1", 80.0)
        assert track.low_battery_trackers() == []

    def test_an_unassigned_tracker_is_not_warned_about(self, client):
        """A spare in the drawer being flat is a job for another day; this is the
        dashboard of a race being run."""
        track.upsert_tracker("SPARE-1", label="Spare", boat_id=None)
        track.insert_positions([{"unique_id": "SPARE-1", "name": "SPARE-1", "lat": 52.88,
                                 "lon": -4.40, "speed_kn": 0.0, "course_deg": 0.0,
                                 "fix_time": time.time(), "battery_pct": 5.0}])
        assert track.low_battery_trackers() == []

    def test_one_that_never_reports_a_battery_is_silent(self, client):
        """Warning about an unknown would train people to ignore the card."""
        self._assigned_tracker("QUIET-1", None)
        assert track.low_battery_trackers() == []

    def test_the_flattest_is_listed_first(self, client):
        self._assigned_tracker("LOW-A", 22.0)
        self._assigned_tracker("LOW-B", 4.0)
        assert [w["unique_id"] for w in track.low_battery_trackers()] == ["LOW-B", "LOW-A"]

    def test_critical_is_flagged_separately(self, client):
        self._assigned_tracker("CRIT-1", 6.0)
        assert track.low_battery_trackers()[0]["critical"] is True
        self._assigned_tracker("MEH-1", 20.0)
        meh = [w for w in track.low_battery_trackers() if w["unique_id"] == "MEH-1"][0]
        assert meh["critical"] is False

    def test_the_card_appears_on_the_dashboard(self, logged_in_client):
        ro.save_hardware_config({"track_enabled": "1"})
        self._assigned_tracker("DASH-1", 9.0)
        html = logged_in_client.get("/admin").get_data(as_text=True)
        assert 'id="trackerBatteryCard"' in html
        assert "9%" in html
        assert "Boat DASH-1" in html

    def test_and_stays_away_when_there_is_nothing_to_say(self, logged_in_client):
        """A card that is always there is furniture; one that only appears is a warning."""
        ro.save_hardware_config({"track_enabled": "1"})
        self._assigned_tracker("FINE-1", 95.0)
        html = logged_in_client.get("/admin").get_data(as_text=True)
        assert 'id="trackerBatteryCard"' not in html

    def test_and_stays_away_entirely_when_tracking_is_off(self, logged_in_client):
        """No trackers, no tracker warnings — the club can run without GPS at all.
        Worth pinning: the first draft of the test above passed because tracking was
        off, not because the battery was healthy."""
        ro.save_hardware_config({"track_enabled": "0"})
        self._assigned_tracker("OFF-1", 3.0)
        html = logged_in_client.get("/admin").get_data(as_text=True)
        assert 'id="trackerBatteryCard"' not in html
