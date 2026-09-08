"""Tests for the hut power monitoring subsystem (core/power.py).

Covers the pure VE.Direct parser/normaliser, the separate-DB store and its
retention purge, reading assembly (real via a mocked serial read, simulated,
and not-connected), and the HTTP routes + settings round-trip. No real hardware.
"""
from __future__ import annotations

import pytest

import app as ro
from core import power


def build_frame(fields: dict) -> bytes:
    """Build one checksum-valid VE.Direct frame from an ordered field dict."""
    body = b""
    for key, value in fields.items():
        body += b"\r\n" + str(key).encode("ascii") + b"\t" + str(value).encode("ascii")
    body += b"\r\nChecksum\t"
    checksum = (-sum(body)) % 256
    return body + bytes([checksum])


@pytest.fixture(autouse=True)
def _isolate_power_db(tmp_path, monkeypatch):
    """Point the separate power-history DB at a temp file for every test."""
    monkeypatch.setattr(power, "POWER_DB_PATH", tmp_path / "power_history.db")
    monkeypatch.setattr(power, "POWER_DB_INITIALIZED", False)


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------
class TestVedirectParser:
    def test_parses_a_valid_battery_frame(self):
        frame = build_frame({"V": 13210, "I": -2500, "SOC": 876, "TTG": 1200})
        frames, remainder = power.parse_vedirect_stream(frame)
        assert len(frames) == 1
        assert frames[0] == {"V": "13210", "I": "-2500", "SOC": "876", "TTG": "1200"}
        assert remainder == b""

    def test_rejects_a_bad_checksum(self):
        good = build_frame({"V": 13210, "I": -2500})
        corrupt = good[:-1] + bytes([(good[-1] + 1) % 256])  # break the checksum byte
        frames, _ = power.parse_vedirect_stream(corrupt)
        assert frames == []

    def test_partial_frame_across_reads(self):
        frame = build_frame({"V": 13000, "I": 500, "SOC": 900})
        split = len(frame) - 4
        frames1, remainder1 = power.parse_vedirect_stream(frame[:split])
        assert frames1 == []  # incomplete -> nothing yet
        frames2, remainder2 = power.parse_vedirect_stream(remainder1 + frame[split:])
        assert len(frames2) == 1
        assert frames2[0]["SOC"] == "900"

    def test_two_frames_in_one_buffer(self):
        buf = build_frame({"V": 13000}) + build_frame({"V": 13100})
        frames, remainder = power.parse_vedirect_stream(buf)
        assert [f["V"] for f in frames] == ["13000", "13100"]
        assert remainder == b""


class TestNormaliseFrame:
    def test_battery_units(self):
        b = power.normalise_frame("battery", {"V": "13210", "I": "-2500", "SOC": "876", "TTG": "1200", "P": "-33"})
        assert b["v"] == pytest.approx(13.21)
        assert b["i"] == pytest.approx(-2.5)
        assert b["soc"] == pytest.approx(87.6)
        assert b["ttg"] == 1200
        assert b["p"] == -33

    def test_solar_units_and_state(self):
        s = power.normalise_frame("solar", {"V": "13400", "I": "18000", "VPV": "61230", "PPV": "245", "CS": "3", "H20": "125"})
        assert s["pv_w"] == 245
        assert s["pv_v"] == pytest.approx(61.23)
        assert s["state"] == "Bulk"
        assert s["yield_wh"] == pytest.approx(1250.0)

    def test_charger_state_name(self):
        c = power.normalise_frame("charger", {"V": "13800", "I": "5000", "CS": "5"})
        assert c["state"] == "Float"
        assert c["i"] == pytest.approx(5.0)


# ---------------------------------------------------------------------------
# Store + retention
# ---------------------------------------------------------------------------
class TestPowerStore:
    def test_insert_latest_and_history(self):
        reading = {"battery_v": 13.2, "battery_soc": 88.0, "solar_pv_w": 210.0}
        power.insert_power_sample(reading, retention_days=365)
        latest = power.latest_power_reading()
        assert latest["battery_soc"] == 88.0
        history = power.power_history(60)
        assert len(history) == 1
        assert history[0]["solar_pv_w"] == 210.0

    def test_retention_purges_old_rows(self):
        power.init_power_db()
        # Insert an old row directly (10 days ago), then a fresh sample with a
        # 1-day retention -> the old row must be purged.
        import time
        old_t = time.time() - 10 * 86400
        with power.get_power_db() as db:
            db.execute(
                "INSERT INTO power_samples (sample_time, sample_iso, battery_soc, raw_json) VALUES (?, ?, ?, ?)",
                (old_t, "old", 50.0, "{}"),
            )
            db.commit()
        power.insert_power_sample({"battery_soc": 91.0}, retention_days=1)
        with power.get_power_db() as db:
            rows = db.execute("SELECT COUNT(*) AS n FROM power_samples").fetchone()
        assert rows["n"] == 1  # only the fresh sample remains


class TestHutConsumption:
    def test_derived_load_watts(self):
        # load_A = solar_i + charger_i − battery_i = 10 + 0 − 6 = 4 A; × 13 V = 52 W
        assert power.hut_consumption_w(13.0, 6.0, 10.0, 0.0) == pytest.approx(52.0)

    def test_none_when_battery_unknown(self):
        assert power.hut_consumption_w(None, 1.0, 1.0, 1.0) is None
        assert power.hut_consumption_w(13.0, None, 1.0, 1.0) is None

    def test_missing_sources_treated_as_zero(self):
        # discharging (−3 A) with no solar/charger reported -> load = 3 A × 12.5 V
        assert power.hut_consumption_w(12.5, -3.0, None, None) == pytest.approx(37.5)

    def test_clamped_at_zero(self):
        assert power.hut_consumption_w(13.0, 20.0, 5.0, 0.0) == 0.0

    def test_history_sample_includes_load_w(self):
        power.insert_power_sample(
            {"battery_v": 13.0, "battery_i": 6.0, "solar_i": 10.0, "charger_i": 0.0},
            retention_days=365,
        )
        assert power.power_history(60)[-1]["load_w"] == pytest.approx(52.0)


# ---------------------------------------------------------------------------
# Reading assembly
# ---------------------------------------------------------------------------
def _cfg(**over):
    base = {"smartshunt_port": "", "phoenix_port": "", "smartsolar_port": "",
            "sim_enabled": False, "sample_seconds": 30, "retention_days": 365}
    base.update(over)
    return base


class TestCollectReading:
    def test_reads_real_devices_via_mocked_serial(self, monkeypatch):
        def fake_read(port, read_seconds=1.5):
            return {"V": "13210", "I": "1500", "SOC": "870", "TTG": "-1",
                    "VPV": "60000", "PPV": "230", "CS": "3", "H20": "120"}
        monkeypatch.setattr(power, "read_vedirect_once", fake_read)
        reading, devices, raw = power.collect_reading(_cfg(smartshunt_port="COM4", smartsolar_port="COM6"))
        assert reading is not None
        assert reading["battery_soc"] == pytest.approx(87.0)
        assert reading["solar_pv_w"] == 230
        assert devices["smartshunt"]["connected"] is True
        assert devices["smartsolar"]["source"] == "serial"
        assert devices["phoenix"]["connected"] is False  # no port set

    def test_simulated_when_no_ports(self):
        reading, devices, raw = power.collect_reading(_cfg(sim_enabled=True))
        assert reading is not None
        assert reading["battery_soc"] is not None
        assert devices["smartshunt"]["source"] == "sim"
        assert devices["smartsolar"]["source"] == "sim"

    def test_not_connected_when_no_ports_and_no_sim(self):
        reading, devices, raw = power.collect_reading(_cfg())
        assert reading is None
        assert devices["smartshunt"]["connected"] is False
        assert devices["smartshunt"]["source"] == "none"

    def test_serial_failure_reports_not_connected(self, monkeypatch):
        monkeypatch.setattr(power, "read_vedirect_once", lambda port, read_seconds=1.5: None)
        reading, devices, raw = power.collect_reading(_cfg(smartshunt_port="COM4"))
        assert reading is None
        assert devices["smartshunt"] == {"connected": False, "source": "serial", "port": "COM4"}


# ---------------------------------------------------------------------------
# Routes + settings round-trip
# ---------------------------------------------------------------------------
class TestPowerRoutes:
    def test_status_api_requires_login(self, client):
        resp = client.get("/api/power/status", headers={"Accept": "application/json"})
        assert resp.status_code == 401

    def test_status_api_returns_json(self, logged_in_client):
        resp = logged_in_client.get("/api/power/status", headers={"Accept": "application/json"})
        assert resp.status_code == 200
        data = resp.get_json()
        assert "devices" in data
        assert "server_now" in data

    def test_history_api_returns_samples_list(self, logged_in_client):
        resp = logged_in_client.get("/api/power/history?minutes=60", headers={"Accept": "application/json"})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert isinstance(data["samples"], list)

    def test_history_page_renders(self, logged_in_client):
        resp = logged_in_client.get("/power/history")
        assert resp.status_code == 200
        assert b"Hut power history" in resp.data

    def test_dashboard_shows_power_card_with_consumption(self, logged_in_client):
        resp = logged_in_client.get("/admin")
        assert resp.status_code == 200
        assert b'id="powerCard"' in resp.data
        assert b"Consumption" in resp.data

    def test_status_api_includes_load(self, logged_in_client):
        data = logged_in_client.get("/api/power/status", headers={"Accept": "application/json"}).get_json()
        assert "load" in data

    def test_settings_round_trip_persists_ports(self, logged_in_client):
        with logged_in_client.session_transaction() as sess:
            sess["_csrf_token"] = "test-csrf-token"
        resp = logged_in_client.post(
            "/settings/save",
            data={
                "_csrf_token": "test-csrf-token",
                "vedirect_smartshunt_port": "COM9",
                "vedirect_phoenix_port": "COM10",
                "vedirect_smartsolar_port": "COM11",
                "power_sim_enabled": "1",
                "power_sample_seconds": "45",
                "power_retention_days": "200",
            },
        )
        assert resp.status_code in (200, 302)
        cfg = power.power_config()
        assert cfg["smartshunt_port"] == "COM9"
        assert cfg["phoenix_port"] == "COM10"
        assert cfg["smartsolar_port"] == "COM11"
        assert cfg["sim_enabled"] is True
        assert cfg["sample_seconds"] == 45
        assert cfg["retention_days"] == 200
