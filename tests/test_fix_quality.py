"""Fix quality kept alongside each position, and what kind of tracker sent it.

Two GL521MGs misbehaved during the races of 16 August. One receiver's solution diverged
for 28 minutes — altitude walking from -22 m to -1060 m with horizontal error growing in
lockstep to 627 m — before snapping back the moment it re-acquired. The other held a
+30 m vertical bias for a whole race and passed its marks 20-50 m to the south. Both were
diagnosed afterwards, and only because Traccar keeps ten days of history: none of it was
in this app's database, which stored lat/lon/speed/course and threw the rest away.

Altitude is the field that did the work, because a boat is at sea level — it is the only
value whose truth is known in advance, so a departure from it measures the error
directly. `hdop` sat at 1-3 through both failures and flagged neither.

Nothing here interprets the numbers. They are stored raw so a gate can be written, tuned
and re-tuned later against real history; a value discarded at ingest cannot be looked at
a second time.
"""
from __future__ import annotations

import sqlite3
import time

import pytest

from core import track


# The real attribute bag off the club's ATC700, which reports the lot.
ATC700_POSITION = {
    "deviceId": 48, "protocol": "teltonika", "latitude": 53.47, "longitude": -2.04,
    "speed": 0.0, "course": 0.0, "altitude": 182.6, "accuracy": 0.0, "valid": True,
    "fixTime": "2026-08-20T12:00:00.000+00:00",
    "attributes": {"battery": 3.715, "hdop": 0.4, "pdop": 0.8, "sat": 28, "rssi": 5,
                   "io113": 70, "motion": False},
}
# The GL521MG reports hdop and no satellite count at all.
GL521MG_POSITION = {
    "deviceId": 46, "protocol": "gl200", "latitude": 52.88, "longitude": -4.40,
    "speed": 5.0, "course": 90.0, "altitude": 8.0, "accuracy": 0.0, "valid": True,
    "fixTime": "2026-08-16T10:00:00.000+00:00",
    "attributes": {"batteryLevel": 88, "hdop": 1.0, "type": "FRI", "motion": True},
}


class TestReadingQualityOffAPosition:
    def test_the_teltonika_bag_gives_everything(self):
        q = track.quality_from_position(ATC700_POSITION)
        assert q["altitude"] == 182.6
        assert q["hdop"] == 0.4 and q["pdop"] == 0.8
        assert q["sat"] == 28 and q["rssi"] == 5.0
        assert q["valid"] == 1 and q["protocol"] == "teltonika"

    def test_the_queclink_bag_gives_what_it_has(self):
        """The GL sends hdop and no satellite count. Missing must read as unknown, not
        as zero satellites — a gate that confused the two would condemn every GL fix."""
        q = track.quality_from_position(GL521MG_POSITION)
        assert q["altitude"] == 8.0 and q["hdop"] == 1.0
        assert q["sat"] is None and q["pdop"] is None and q["rssi"] is None

    def test_an_invalid_fix_is_recorded_as_invalid(self):
        """Traccar's own validity flag. One of the club's devices was sending valid=false
        while everything else about the fix looked ordinary."""
        assert track.quality_from_position({"valid": False})["valid"] == 0
        assert track.quality_from_position({"valid": True})["valid"] == 1
        assert track.quality_from_position({})["valid"] is None

    def test_the_diverging_altitude_is_kept_verbatim(self):
        """-1060 m is the fix that mattered on 16 August. Nothing here may clamp or
        discard it: the whole point is that it survives to be looked at."""
        q = track.quality_from_position({"altitude": -1059.9, "attributes": {"hdop": 2.0}})
        assert q["altitude"] == -1059.9 and q["hdop"] == 2.0

    def test_zero_altitude_is_a_reading_not_a_gap(self):
        """Sea level is the expected value for a boat, so 0.0 is the most ordinary
        reading there is and must not be confused with 'not reported'."""
        assert track.quality_from_position({"altitude": 0.0})["altitude"] == 0.0

    def test_booleans_do_not_become_numbers(self):
        """bool is an int subclass in Python, so True would silently store as 1.0 and a
        flag would read as a satellite count of one."""
        q = track.quality_from_position({"altitude": True, "attributes": {"sat": True}})
        assert q["altitude"] is None and q["sat"] is None

    @pytest.mark.parametrize("junk", [None, "", [], 0, {"attributes": "not a dict"}, {}])
    def test_nonsense_does_not_raise(self, junk):
        """This parses data off the network."""
        q = track.quality_from_position(junk)
        assert set(q) == set(track.QUALITY_FIELDS)
        assert all(v is None for v in q.values())


class TestWhatKindOfTrackerSentIt:
    """The IMEI's first eight digits are its Type Allocation Code, which identifies the
    model. It is derived rather than stored: the IMEI is already on every fix, so a
    stored copy would only be one more thing to keep in step.

    These take the ``client`` fixture because resolving a model consults the editable
    type table, so they touch a database — a temp one, which is what the fixture is for.
    """

    def test_the_two_gl_units_share_a_tac(self, client):
        """Which is what shows the scheme works — same model, same code."""
        assert "864864070498856"[:8] == "864864073309183"[:8]
        assert track.tracker_model("864864070498856", "gl200") == "Queclink GL521MG"
        assert track.tracker_model("864864073309183", "gl200") == "Queclink GL521MG"

    def test_it_separates_two_devices_traccar_calls_the_same_thing(self, client):
        """Both are protocol 'teltonika' and they could hardly be less alike: a 1000 mAh
        asset tracker draining 10%/h against a mains-powered router at zero latency.
        Protocol alone cannot tell them apart, which is the reason the TAC is here."""
        assert track.tracker_model("862129082306832", "teltonika") == "Teltonika ATC700"
        assert track.tracker_model("860302050784478", "teltonika") == "Teltonika RUTX50"

    def test_an_uncatalogued_device_falls_back_to_its_protocol(self, client):
        """The club has a gt06 device nobody has identified. It should report as
        something rather than as a blank."""
        assert track.tracker_model("860796050859679", "gt06") == "Jimi/Concox device"

    def test_a_type_code_alone_is_not_trusted(self, client):
        """A TAC is allocated to whoever certified the radio, and a tracker built around
        an off-the-shelf cellular module often ships with the module vendor's IMEIs —
        the ATC700's datasheet names a Quectel EG915U. So another maker's tracker on the
        same module can carry the same type code, and matching on the code alone would
        confidently call it an ATC700. The protocol has to agree."""
        assert track.tracker_model("862129080000001", "teltonika") == "Teltonika ATC700"
        assert track.tracker_model("862129080000001", "gt06") == "Jimi/Concox device"
        assert track.tracker_model("862129080000001", "gl200") == "Queclink device"

    def test_a_device_that_has_never_reported_still_gets_a_best_guess(self, client):
        """With no protocol observed there is nothing to contradict the type code, and a
        likely model beats a shrug — this is what the page shows for a tracker adopted
        but not yet switched on."""
        assert track.tracker_model("862129082306832", None) == "Teltonika ATC700"

    def test_an_entry_with_no_protocol_matches_anything(self, client):
        """Right for a code the club has confirmed belongs to one product."""
        track.upsert_tracker_type("12345678", "Acme Wanderer")
        assert track.tracker_model("123456789012345", "gt06") == "Acme Wanderer"
        assert track.tracker_model("123456789012345", "teltonika") == "Acme Wanderer"

    def test_an_entry_can_be_pinned_to_one_protocol(self, client):
        track.upsert_tracker_type("12345678", "Acme Wanderer", protocol="gt06")
        assert track.tracker_model("123456789012345", "gt06") == "Acme Wanderer"
        assert track.tracker_model("123456789012345", "teltonika") == "Teltonika device"

    def test_a_phone_is_not_an_imei(self):
        assert track.tracker_model("28426611", "osmand") == "phone or app"

    def test_nothing_known_says_so(self):
        assert track.tracker_model("", None) == "unknown"
        assert track.tracker_model(None, None) == "unknown"
        assert track.tracker_model("not-an-imei", "somethingnew") == "somethingnew"


class TestEveryIngestPathKeepsIt:
    """Three routes bring fixes in — the poll, the push forwarder and the gap back-fill.
    A fix arriving by one must not know less than a fix arriving by another. The battery
    work made exactly this mistake once, patching only the gap-filler."""

    def test_the_poll_keeps_it(self, client, monkeypatch):
        monkeypatch.setattr(track, "_traccar_get",
                            lambda cfg, path, timeout=4.0:
                            [{"id": 46, "uniqueId": "IMEI-GL", "name": "Kite"}]
                            if "devices" in path else [GL521MG_POSITION | {"deviceId": 46}])
        monkeypatch.setattr(track, "track_config", lambda: dict(
            enabled=True, base_url="http://x", token="t", poll_seconds=5,
            retention_days=90, sim_enabled=False, finish_horn=False,
            rounding_radius_m=50, ingest_secret=""))
        rows, err = track.fetch_positions()
        assert err is None and rows, err
        assert rows[0]["altitude"] == 8.0
        assert rows[0]["hdop"] == 1.0
        assert rows[0]["protocol"] == "gl200"

    def test_the_push_forwarder_keeps_it(self, client):
        rows = track.parse_forwarded_positions({
            "device": {"uniqueId": "IMEI-ATC", "name": "Kite"},
            "position": ATC700_POSITION,
        })
        assert rows
        assert rows[0]["altitude"] == 182.6 and rows[0]["sat"] == 28
        assert rows[0]["protocol"] == "teltonika"

    def test_a_fix_without_any_of_it_is_still_a_fix(self, client):
        rows = track.parse_forwarded_positions({
            "device": {"uniqueId": "IMEI-3"},
            "position": {"deviceId": 3, "latitude": 52.88, "longitude": -4.40,
                         "fixTime": "2026-08-05T12:00:00Z"},
        })
        assert rows and rows[0]["altitude"] is None and rows[0]["hdop"] is None


class TestItSurvivesStorage:
    def test_the_columns_exist(self, client):
        track.init_track_db()
        with track.get_track_db() as db:
            cols = {r["name"] for r in db.execute("PRAGMA table_info(track_positions)")}
        assert set(track.QUALITY_FIELDS) <= cols

    def test_an_older_database_gains_them(self, client, monkeypatch, tmp_path):
        """The hut's track database has hundreds of thousands of rows and predates all of
        this, so the migration has to run rather than the schema simply being right."""
        path = tmp_path / "old_track.db"
        monkeypatch.setattr(track, "TRACK_DB_PATH", path)
        monkeypatch.setattr(track, "TRACK_DB_INITIALIZED", False)
        con = sqlite3.connect(path)
        con.execute("CREATE TABLE track_positions (id INTEGER PRIMARY KEY AUTOINCREMENT,"
                    " device_id TEXT, unique_id TEXT NOT NULL, name TEXT, lat REAL NOT NULL,"
                    " lon REAL NOT NULL, speed_kn REAL, course_deg REAL,"
                    " fix_time REAL NOT NULL, server_time REAL NOT NULL)")
        con.commit()
        con.close()
        track.init_track_db()
        with track.get_track_db() as db:
            cols = {r["name"] for r in db.execute("PRAGMA table_info(track_positions)")}
        assert set(track.QUALITY_FIELDS) <= cols

    def _fix(self, **over):
        row = {"device_id": "48", "unique_id": "IMEI-STORE", "name": "n",
               "lat": 52.88, "lon": -4.40, "speed_kn": 1.0, "course_deg": 10.0,
               "fix_time": time.time(), "battery_pct": 70.0}
        row.update(track.quality_from_position(ATC700_POSITION))
        row.update(over)
        return row

    def test_it_round_trips_through_the_live_insert(self, client):
        track.init_track_db()
        assert track.insert_positions([self._fix()]) == 1
        with track.get_track_db() as db:
            r = db.execute("SELECT altitude, hdop, pdop, sat, rssi, valid, protocol"
                           " FROM track_positions WHERE unique_id = 'IMEI-STORE'").fetchone()
        assert r["altitude"] == 182.6 and r["hdop"] == 0.4 and r["sat"] == 28
        assert r["valid"] == 1 and r["protocol"] == "teltonika"

    def test_it_round_trips_through_the_backfill_insert(self, client):
        """The gap-filler is the path that recovers an outage — exactly the stretch most
        worth examining afterwards, so it must not come back stripped."""
        track.init_track_db()
        assert track.insert_backfilled_positions([self._fix(unique_id="IMEI-BF")]) == 1
        with track.get_track_db() as db:
            r = db.execute("SELECT altitude, sat, battery_pct FROM track_positions"
                           " WHERE unique_id = 'IMEI-BF'").fetchone()
        assert r["altitude"] == 182.6 and r["sat"] == 28
        assert r["battery_pct"] == 70.0

    def test_a_historic_row_reads_as_unknown_not_as_bad(self, client):
        """Every row already in the hut's database has NULL for all of this. Whatever
        reads these columns has to treat that as 'not known' — a check that took NULL for
        a failure would condemn the entire archive."""
        track.init_track_db()
        with track.get_track_db() as db:
            db.execute("INSERT INTO track_positions (unique_id, lat, lon, fix_time, server_time)"
                       " VALUES ('IMEI-OLD', 52.88, -4.40, ?, ?)", (time.time(), time.time()))
            db.commit()
            r = db.execute("SELECT altitude, hdop, sat, valid, protocol FROM track_positions"
                           " WHERE unique_id = 'IMEI-OLD'").fetchone()
        assert all(r[k] is None for k in ("altitude", "hdop", "sat", "valid", "protocol"))
