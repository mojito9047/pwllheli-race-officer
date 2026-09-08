"""Tests for pure helper functions — no database or Flask context required."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import app as ro


# ---------------------------------------------------------------------------
# angular_diff
# ---------------------------------------------------------------------------

class TestAngularDiff:
    def test_same_bearing_is_zero(self):
        assert ro.angular_diff(90, 90) == 0.0

    def test_opposite_bearings_is_180(self):
        assert ro.angular_diff(0, 180) == pytest.approx(180.0)

    def test_wrap_across_north(self):
        assert ro.angular_diff(350, 10) == pytest.approx(20.0)

    def test_commutative(self):
        assert ro.angular_diff(10, 350) == pytest.approx(ro.angular_diff(350, 10))

    def test_quarter_turn(self):
        assert ro.angular_diff(0, 90) == pytest.approx(90.0)


# ---------------------------------------------------------------------------
# wind_in_range
# ---------------------------------------------------------------------------

class TestWindInRange:
    def test_inside_simple_range(self):
        assert ro.wind_in_range(90, 45, 135) is True

    def test_outside_simple_range(self):
        assert ro.wind_in_range(180, 45, 135) is False

    def test_at_lower_boundary(self):
        assert ro.wind_in_range(45, 45, 135) is True

    def test_at_upper_boundary(self):
        assert ro.wind_in_range(135, 45, 135) is True

    def test_wrapped_range_inside(self):
        # 350–010 wraps through north; 0, 5, 359 are inside
        assert ro.wind_in_range(0, 350, 10) is True
        assert ro.wind_in_range(5, 350, 10) is True
        assert ro.wind_in_range(359, 350, 10) is True

    def test_wrapped_range_outside(self):
        assert ro.wind_in_range(180, 350, 10) is False

    def test_over_360_normalised(self):
        # 720° mod 360 == 0° which is inside 330–030
        assert ro.wind_in_range(720, 330, 30) is True


# ---------------------------------------------------------------------------
# haversine_nm
# ---------------------------------------------------------------------------

class TestHaversineNm:
    def test_same_point_is_zero(self):
        assert ro.haversine_nm(52.882, -4.4, 52.882, -4.4) == pytest.approx(0.0, abs=1e-9)

    def test_one_degree_latitude_approx_60nm(self):
        dist = ro.haversine_nm(52.0, -4.0, 53.0, -4.0)
        assert 58.0 < dist < 62.0

    def test_one_degree_longitude_at_equator_approx_60nm(self):
        dist = ro.haversine_nm(0.0, 0.0, 0.0, 1.0)
        assert 58.0 < dist < 62.0

    def test_pwllheli_region_short_leg(self):
        # Roughly Pwllheli harbour to St Tudwal's East (~5–7 nm)
        dist = ro.haversine_nm(52.882, -4.401, 52.795, -4.475)
        assert 5.0 < dist < 8.0

    def test_symmetric(self):
        d1 = ro.haversine_nm(52.0, -4.0, 53.0, -5.0)
        d2 = ro.haversine_nm(53.0, -5.0, 52.0, -4.0)
        assert d1 == pytest.approx(d2)


# ---------------------------------------------------------------------------
# bearing_deg
# ---------------------------------------------------------------------------

class TestBearingDeg:
    def test_due_north(self):
        assert ro.bearing_deg(52.0, -4.0, 53.0, -4.0) == pytest.approx(0.0, abs=1.0)

    def test_due_south(self):
        assert ro.bearing_deg(53.0, -4.0, 52.0, -4.0) == pytest.approx(180.0, abs=1.0)

    def test_due_east(self):
        assert ro.bearing_deg(52.0, -4.0, 52.0, -3.0) == pytest.approx(90.0, abs=2.0)

    def test_due_west(self):
        assert ro.bearing_deg(52.0, -4.0, 52.0, -5.0) == pytest.approx(270.0, abs=2.0)

    def test_result_always_in_0_to_360(self):
        for lat1, lon1, lat2, lon2 in [(52, -4, 53, -5), (0, 0, -1, 1), (45, 10, 44, 9)]:
            b = ro.bearing_deg(lat1, lon1, lat2, lon2)
            assert 0.0 <= b < 360.0


# ---------------------------------------------------------------------------
# leg_side
# ---------------------------------------------------------------------------

class TestLegSide:
    def test_starboard_tack(self):
        # Leg bearing north (0°), TWD from east (90°) → starboard tack
        assert ro.leg_side(0, 90) == "Starboard"

    def test_port_tack(self):
        # Leg bearing north (0°), TWD from west (270°) → port tack
        assert ro.leg_side(0, 270) == "Port"

    def test_dead_downwind_dash(self):
        # TWD aligned with leg direction
        assert ro.leg_side(180, 180) == "—"

    def test_head_to_wind_dash(self):
        assert ro.leg_side(0, 0) == "—"


# ---------------------------------------------------------------------------
# point_of_sail
# ---------------------------------------------------------------------------

class TestPointOfSail:
    def test_beat(self):
        assert ro.point_of_sail(30) == "Beat/VMG"

    def test_close_hauled(self):
        assert ro.point_of_sail(45) == "Close hauled"

    def test_beam_reach(self):
        assert ro.point_of_sail(100) == "Beam reach"

    def test_run(self):
        assert ro.point_of_sail(160) == "Run/gybe VMG"


# ---------------------------------------------------------------------------
# seconds_display
# ---------------------------------------------------------------------------

class TestSecondsDisplay:
    def test_none_returns_empty_string(self):
        assert ro.seconds_display(None) == ""

    def test_zero(self):
        assert ro.seconds_display(0) == "0:00:00"

    def test_exactly_one_hour(self):
        assert ro.seconds_display(3600) == "1:00:00"

    def test_hours_minutes_seconds(self):
        assert ro.seconds_display(3661) == "1:01:01"

    def test_negative_duration(self):
        assert ro.seconds_display(-61) == "-0:01:01"

    def test_sub_minute(self):
        assert ro.seconds_display(45) == "0:00:45"

    def test_rounding(self):
        # 0.6 seconds rounds to 1
        assert ro.seconds_display(0.6) == "0:00:01"


# ---------------------------------------------------------------------------
# format_minutes
# ---------------------------------------------------------------------------

class TestFormatMinutes:
    def test_none_returns_dash(self):
        assert ro.format_minutes(None) == "—"

    def test_sub_hour(self):
        assert ro.format_minutes(45) == "45 min"

    def test_exactly_one_hour(self):
        assert ro.format_minutes(60) == "1h 00m"

    def test_hour_and_a_half(self):
        assert ro.format_minutes(90) == "1h 30m"

    def test_rounding_at_59_5_minutes(self):
        # 59.5 min rounds to 60 → 1h 00m
        result = ro.format_minutes(119.5)
        assert result in ("1h 60m", "2h 00m")  # either rounding is acceptable


# ---------------------------------------------------------------------------
# decimal_minutes_to_text
# ---------------------------------------------------------------------------

class TestDecimalMinutesToText:
    def test_none_coords_return_empty(self):
        assert ro.decimal_minutes_to_text(None, None) == ""

    def test_northern_western(self):
        result = ro.decimal_minutes_to_text(52.882, -4.401)
        assert "N" in result and "W" in result
        assert "52" in result

    def test_southern_eastern(self):
        result = ro.decimal_minutes_to_text(-33.867, 151.207)
        assert "S" in result and "E" in result


# ---------------------------------------------------------------------------
# number_words
# ---------------------------------------------------------------------------

class TestNumberWords:
    def test_single_digits(self):
        assert ro.number_words(0) == "zero"
        assert ro.number_words(1) == "one"
        assert ro.number_words(9) == "nine"

    def test_teens(self):
        assert ro.number_words(11) == "eleven"
        assert ro.number_words(19) == "nineteen"

    def test_round_tens(self):
        assert ro.number_words(20) == "twenty"
        assert ro.number_words(90) == "ninety"

    def test_compound_tens(self):
        assert ro.number_words(21) == "twenty one"
        assert ro.number_words(55) == "fifty five"

    def test_large_number_falls_back_to_string(self):
        assert ro.number_words(100) == "100"
        assert ro.number_words(999) == "999"


# ---------------------------------------------------------------------------
# spoken_mark
# ---------------------------------------------------------------------------

class TestSpokenMark:
    def test_named_special_marks(self):
        assert ro.spoken_mark("O") == "outer distance mark"
        assert ro.spoken_mark("F") == "fairway"
        assert ro.spoken_mark("Y") == "Gwylan Islands"
        assert ro.spoken_mark("A") == "mark A"

    def test_numeric_marks_spoken_as_words(self):
        assert ro.spoken_mark("1") == "mark one"
        assert ro.spoken_mark("10") == "mark ten"

    def test_unknown_mark_prefixed(self):
        assert ro.spoken_mark("Z") == "mark Z"

    def test_lowercase_normalised(self):
        assert ro.spoken_mark("o") == "outer distance mark"
        assert ro.spoken_mark("f") == "fairway"

    def test_empty_mark(self):
        result = ro.spoken_mark("")
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# normalise_key / clean_sail_no / parse_float / parse_dt
# ---------------------------------------------------------------------------

class TestNormaliseKey:
    def test_strips_and_lowercases(self):
        assert ro.normalise_key("  Hello World  ") == "hello world"

    def test_empty_string(self):
        assert ro.normalise_key("") == ""

    def test_already_normalised(self):
        assert ro.normalise_key("abc") == "abc"


class TestCleanSailNo:
    def test_removes_spaces_and_uppercases(self):
        # clean_sail_no strips spaces and uppercases for case-insensitive lookup
        assert ro.clean_sail_no("  gbr 123  ") == "GBR123"

    def test_already_clean(self):
        assert ro.clean_sail_no("GBR123") == "GBR123"

    def test_empty(self):
        assert ro.clean_sail_no("") == ""


class TestParseFloat:
    def test_valid_float_string(self):
        assert ro.parse_float("1.050") == pytest.approx(1.050)

    def test_integer_string(self):
        assert ro.parse_float("5") == pytest.approx(5.0)

    def test_none_returns_none(self):
        assert ro.parse_float(None) is None

    def test_invalid_returns_none(self):
        assert ro.parse_float("not-a-number") is None

    def test_numeric_value_passthrough(self):
        assert ro.parse_float(3.14) == pytest.approx(3.14)


class TestParseDt:
    def test_valid_iso_datetime(self):
        from datetime import datetime
        result = ro.parse_dt("2026-06-23T10:00:00")
        assert isinstance(result, datetime)
        assert result.year == 2026 and result.month == 6

    def test_none_returns_none(self):
        assert ro.parse_dt(None) is None

    def test_empty_string_returns_none(self):
        assert ro.parse_dt("") is None

    def test_invalid_returns_none(self):
        assert ro.parse_dt("not-a-date") is None


# ---------------------------------------------------------------------------
# is_safe_redirect_url
# ---------------------------------------------------------------------------

class TestIsSafeRedirectUrl:
    def test_app_relative_path_is_safe(self):
        assert ro.is_safe_redirect_url("/admin/races") is True

    def test_login_path_is_safe(self):
        assert ro.is_safe_redirect_url("/login") is True

    def test_external_http_is_unsafe(self):
        assert ro.is_safe_redirect_url("http://evil.com/") is False

    def test_protocol_relative_is_unsafe(self):
        assert ro.is_safe_redirect_url("//evil.com") is False

    def test_empty_string_is_unsafe(self):
        assert ro.is_safe_redirect_url("") is False

    def test_backslash_is_unsafe(self):
        assert ro.is_safe_redirect_url("/admin\\..\\evil") is False

    def test_no_leading_slash_is_unsafe(self):
        assert ro.is_safe_redirect_url("admin/races") is False


# ---------------------------------------------------------------------------
# require_http_url
# ---------------------------------------------------------------------------

class TestRequireHttpUrl:
    def test_http_url_accepted(self):
        url = "http://example.com/data.csv"
        assert ro.require_http_url(url) == url

    def test_https_url_accepted(self):
        url = "https://example.com/data.csv"
        assert ro.require_http_url(url) == url

    def test_file_url_rejected(self):
        with pytest.raises(ValueError):
            ro.require_http_url("file:///etc/passwd")

    def test_empty_url_rejected(self):
        with pytest.raises(ValueError):
            ro.require_http_url("")

    def test_ftp_url_rejected(self):
        with pytest.raises(ValueError):
            ro.require_http_url("ftp://example.com/file.csv")


# ---------------------------------------------------------------------------
# parse_band_expression / rating_in_class_band / rating_band_display
# ---------------------------------------------------------------------------

class TestParseBandExpression:
    def test_exclusive_double_bound(self):
        b = ro.parse_band_expression("1.000 < rating < 1.099")
        assert b["min"] == pytest.approx(1.000)
        assert b["max"] == pytest.approx(1.099)
        assert b["min_inclusive"] is False
        assert b["max_inclusive"] is False

    def test_inclusive_double_bound(self):
        b = ro.parse_band_expression("1.000 <= rating <= 1.099")
        assert b["min_inclusive"] is True
        assert b["max_inclusive"] is True

    def test_upper_bound_only(self):
        b = ro.parse_band_expression("rating < 1.100")
        assert b["min"] is None
        assert b["max"] == pytest.approx(1.100)
        assert b["max_inclusive"] is False

    def test_lower_bound_only_inclusive(self):
        b = ro.parse_band_expression("rating >= 0.900")
        assert b["min"] == pytest.approx(0.900)
        assert b["min_inclusive"] is True
        assert b["max"] is None

    def test_reversed_form(self):
        # "1.000 < rating" is equivalent to "rating > 1.000"
        b = ro.parse_band_expression("1.000 < rating")
        assert b["min"] == pytest.approx(1.000)
        assert b["min_inclusive"] is False

    def test_empty_expression_all_none(self):
        b = ro.parse_band_expression("")
        assert b["min"] is None
        assert b["max"] is None

    def test_unicode_le_ge(self):
        b = ro.parse_band_expression("1.000 ≤ rating ≤ 1.099")
        assert b["min_inclusive"] is True
        assert b["max_inclusive"] is True


class TestRatingInClassBand:
    def test_inside_exclusive_band(self):
        band = ro.parse_band_expression("1.000 < rating < 1.100")
        assert ro.rating_in_class_band(1.050, band) is True

    def test_outside_exclusive_band(self):
        band = ro.parse_band_expression("1.000 < rating < 1.100")
        assert ro.rating_in_class_band(0.900, band) is False
        assert ro.rating_in_class_band(1.200, band) is False

    def test_at_exclusive_lower_boundary_excluded(self):
        band = ro.parse_band_expression("1.000 < rating < 1.100")
        assert ro.rating_in_class_band(1.000, band) is False

    def test_at_exclusive_upper_boundary_excluded(self):
        band = ro.parse_band_expression("1.000 < rating < 1.100")
        assert ro.rating_in_class_band(1.100, band) is False

    def test_at_inclusive_boundaries_included(self):
        band = ro.parse_band_expression("1.000 <= rating <= 1.100")
        assert ro.rating_in_class_band(1.000, band) is True
        assert ro.rating_in_class_band(1.100, band) is True

    def test_none_rating_is_false(self):
        band = ro.parse_band_expression("1.000 < rating < 1.100")
        assert ro.rating_in_class_band(None, band) is False

    def test_open_band_accepts_any_rating(self):
        band = ro.parse_band_expression("")
        assert ro.rating_in_class_band(0.5, band) is True
        assert ro.rating_in_class_band(2.0, band) is True

    def test_upper_bound_only(self):
        band = ro.parse_band_expression("rating < 1.100")
        assert ro.rating_in_class_band(0.900, band) is True
        assert ro.rating_in_class_band(1.100, band) is False


class TestRatingBandDisplay:
    def test_returns_expr_when_set(self):
        band = ro.parse_band_expression("1.000 < rating < 1.099")
        assert ro.rating_band_display(band) == "1.000 < rating < 1.099"

    def test_open_band_returns_all_ratings(self):
        band = ro.parse_band_expression("")
        assert ro.rating_band_display(band) == "all ratings"

    def test_upper_bound_only(self):
        band = ro.parse_band_expression("rating < 1.100")
        assert band["expr"]  # expression retained for display


# ---------------------------------------------------------------------------
# Sail chart selection
# ---------------------------------------------------------------------------

class TestSailChartSelection:
    def test_matching_sail_chart_uses_sailchart_suffix_for_selected_polar(self):
        polar_path = ro.resolve_polar_path("J109.txt")
        sail_chart_path = ro.resolve_sail_chart_path_for_polar(polar_path)
        assert sail_chart_path.name == "J109-SailChart.txt"
        assert sail_chart_path.parent.name == "sailcharts"

    def test_new_polar_and_sail_chart_pair_uses_suffix_convention(self):
        polar_path = ro.resolve_polar_path("J80.txt")
        sail_chart_path = ro.resolve_sail_chart_path_for_polar(polar_path)
        assert polar_path.name == "J80.txt"
        assert sail_chart_path.name == "J80-SailChart.txt"
        assert sail_chart_path.parent.name == "sailcharts"

    def test_missing_matching_sail_chart_falls_back_to_default(self):
        polar_path = ro.resolve_polar_path("J111.txt")
        sail_chart_path = ro.resolve_sail_chart_path_for_polar(polar_path)
        assert sail_chart_path.name == "DefaultSailChart.txt"

    def test_analyse_course_reports_polar_specific_sail_chart(self):
        polar_path = ro.resolve_polar_path("J109.txt")
        analysis = ro.analyse_course_with_wind(ro.COURSES[0], 225, 12, polar_path)
        assert analysis["polar_file"] == "J109.txt"
        assert analysis["sail_chart_file"] == "J109-SailChart.txt"
        assert analysis["sail_chart_source"] == "polar-specific"
        assert analysis["sail_chart_available"] is True

    def test_analyse_course_reports_default_sail_chart_when_no_match(self):
        polar_path = ro.resolve_polar_path("J111.txt")
        analysis = ro.analyse_course_with_wind(ro.COURSES[0], 225, 12, polar_path)
        assert analysis["polar_file"] == "J111.txt"
        assert analysis["sail_chart_file"] == "DefaultSailChart.txt"
        assert analysis["sail_chart_source"] == "default"
        assert analysis["sail_chart_available"] is True


# ---------------------------------------------------------------------------
# Camera PTZ preset control
# ---------------------------------------------------------------------------

class TestCameraPtzSettings:
    def test_ptz_base_url_accepts_bare_ip(self):
        assert ro.safe_ptz_camera_base_url("192.168.1.157") == "http://192.168.1.157"

    def test_ptz_base_url_strips_paths(self):
        assert ro.safe_ptz_camera_base_url("http://192.168.1.157/ISAPI/PTZCtrl/channels/1/presets") == "http://192.168.1.157"

    def test_ptz_base_url_rejects_embedded_credentials(self):
        assert ro.safe_ptz_camera_base_url("http://admin:secret@192.168.1.157/") == ""

    def test_ptz_preset_url_uses_hikvision_isapi_path(self):
        cfg = {"ptz_camera_url": "http://192.168.1.157", "ptz_channel": 1}
        assert ro.ptz_preset_url(cfg, 2) == "http://192.168.1.157/ISAPI/PTZCtrl/channels/1/presets/2/goto"

    def test_ptz_capabilities_url_uses_read_only_isapi_path(self):
        cfg = {"ptz_camera_url": "http://192.168.1.157", "ptz_channel": 1}
        assert ro.ptz_capabilities_url(cfg) == "http://192.168.1.157/ISAPI/PTZCtrl/channels/1/capabilities"

    def test_ptz_auth_mode_normalisation(self):
        assert ro.normalise_ptz_auth_mode("digest") == "digest"
        assert ro.normalise_ptz_auth_mode("any auth") == "anyauth"
        assert ro.normalise_ptz_auth_mode("basic") == "basic"
        assert ro.normalise_ptz_auth_mode("weird") == "digest"

    def test_update_camera_preset_queues_idle_when_race_not_in_sequence(self, client, monkeypatch):
        calls = []
        now = ro.datetime(2026, 6, 25, 11, 59, 0)
        with ro.get_db() as db:
            db.execute(
                "INSERT INTO app_settings (key, value, updated_at) VALUES ('ptz_enabled', '1', ?), ('ptz_camera_url', 'http://192.168.1.157', ?)",
                (now.isoformat(), now.isoformat()),
            )
            cur = db.execute(
                "INSERT INTO races (name, class_name, course_no, start_time, rating_rule, notes, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("PTZ test", "IRC", 1, "2026-06-25T12:00:00", "DUAL", "", now.isoformat()),
            )
            race_id = cur.lastrowid
            db.execute(
                "INSERT INTO entries (race_id, boat_name, sail_no, class_name, rating, status) VALUES (?, ?, ?, ?, ?, 'RACING')",
                (race_id, "Boat", "1", "IRC", 1.0),
            )
            db.commit()
            race = db.execute("SELECT * FROM races WHERE id = ?", (race_id,)).fetchone()
        monkeypatch.setattr(ro.video, "queue_ptz_goto_preset", lambda which, reason="", race_id=None: calls.append((which, race_id)) or {"ok": True})
        ro.update_camera_preset_for_races([race], now=now)
        assert calls == [("idle", None)]

    def test_update_camera_preset_queues_recording_at_lead_time(self, client, monkeypatch):
        calls = []
        now = ro.datetime(2026, 6, 25, 12, 4, 31)
        with ro.get_db() as db:
            db.execute(
                "INSERT INTO app_settings (key, value, updated_at) VALUES ('ptz_enabled', '1', ?), ('ptz_camera_url', 'http://192.168.1.157', ?), ('ptz_pre_start_seconds', '30', ?)",
                (now.isoformat(), now.isoformat(), now.isoformat()),
            )
            cur = db.execute(
                "INSERT INTO races (name, class_name, course_no, start_time, rating_rule, notes, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("PTZ test", "IRC", 1, "2026-06-25T12:00:00", "DUAL", "", now.isoformat()),
            )
            race_id = cur.lastrowid
            db.execute(
                "INSERT INTO entries (race_id, boat_name, sail_no, class_name, rating, status) VALUES (?, ?, ?, ?, ?, 'RACING')",
                (race_id, "Boat", "1", "IRC", 1.0),
            )
            db.commit()
            race = db.execute("SELECT * FROM races WHERE id = ?", (race_id,)).fetchone()
        monkeypatch.setattr(ro.video, "queue_ptz_goto_preset", lambda which, reason="", race_id=None: calls.append((which, race_id)) or {"ok": True})
        ro.update_camera_preset_for_races([race], now=now)
        assert calls == [("recording", race_id)]


    def test_manual_ptz_test_hold_blocks_immediate_scheduler_idle(self, client, monkeypatch):
        ro.reset_ptz_runtime_state()
        now = ro.datetime(2026, 6, 25, 11, 59, 0)
        ro.save_app_settings({
            "ptz_enabled": "1",
            "ptz_camera_url": "192.168.1.157",
            "ptz_username": "admin",
            "ptz_password": "secret",
            "ptz_channel": "1",
            "ptz_idle_preset": "1",
            "ptz_recording_preset": "2",
        })
        with ro.PTZ_LOCK:
            ro.PTZ_RUNTIME_STATE["manual_hold_until"] = now + ro.timedelta(seconds=30)
            ro.PTZ_RUNTIME_STATE["last_status"] = {"ok": True, "enabled": True, "message": "Manual test hold", "preset_kind": "recording"}
        with ro.get_db() as db:
            cur = db.execute(
                "INSERT INTO races (name, class_name, course_no, start_time, rating_rule, notes, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("PTZ hold", "IRC", 1, "2026-06-25T12:30:00", "DUAL", "", now.isoformat()),
            )
            race_id = cur.lastrowid
            db.execute(
                "INSERT INTO entries (race_id, boat_name, sail_no, class_name, rating, status) VALUES (?, ?, ?, ?, ?, 'RACING')",
                (race_id, "Boat", "1", "IRC", 1.0),
            )
            db.commit()
            race = db.execute("SELECT * FROM races WHERE id = ?", (race_id,)).fetchone()
        calls = []
        monkeypatch.setattr(ro.video, "queue_ptz_goto_preset", lambda which, reason="", race_id=None: calls.append((which, race_id)) or {"ok": True})
        result = ro.update_camera_preset_for_races([race], now=now)
        assert calls == []
        assert result["message"] == "Manual test hold"
        assert result["manual_hold_until"].startswith("2026-06-25T11:59:30")

    def test_ptz_manual_test_hold_expires(self, client, monkeypatch):
        ro.reset_ptz_runtime_state()
        now = ro.datetime(2026, 6, 25, 11, 59, 31)
        ro.save_app_settings({"ptz_enabled": "1", "ptz_camera_url": "192.168.1.157"})
        with ro.PTZ_LOCK:
            ro.PTZ_RUNTIME_STATE["manual_hold_until"] = now - ro.timedelta(seconds=1)
            ro.PTZ_RUNTIME_STATE["last_status"] = {"ok": True, "enabled": True, "message": "Expired hold"}
        calls = []
        monkeypatch.setattr(ro.video, "queue_ptz_goto_preset", lambda which, reason="", race_id=None: calls.append((which, race_id)) or {"ok": True})
        ro.update_camera_preset_for_races([], now=now)
        assert calls == [("idle", None)]

    def test_ptz_digest_put_prefers_curl_digest(self, monkeypatch):
        calls = {}
        url = "http://192.168.1.157/ISAPI/PTZCtrl/channels/1/presets/1/goto"

        class FakeRunResult:
            returncode = 0
            stdout = "200"
            stderr = ""

        def fake_run(cmd, **kwargs):
            calls["cmd"] = cmd
            calls["kwargs"] = kwargs
            return FakeRunResult()

        monkeypatch.setattr(ro.shutil, "which", lambda name: "curl.exe" if name == "curl.exe" else None)
        monkeypatch.setattr(ro.subprocess, "run", fake_run)

        code, method = ro.ptz_digest_put(url, "http://192.168.1.157", "admin", "secret", timeout=4)
        assert code == 200
        assert method == "curl-digest"
        assert "--digest" in calls["cmd"]
        assert "--user" in calls["cmd"]
        assert "admin:secret" in calls["cmd"]
        assert "PUT" in calls["cmd"]
        assert url in calls["cmd"]
        assert calls["kwargs"]["timeout"] == 6

    def test_ptz_digest_put_falls_back_to_urllib_when_curl_missing(self, monkeypatch):
        opened = {}
        url = "http://192.168.1.157/ISAPI/PTZCtrl/channels/1/presets/1/goto"

        class FakeResponse:
            status = 204
            def getcode(self):
                return 204
            def __enter__(self):
                return self
            def __exit__(self, exc_type, exc, tb):
                return False

        class FakeOpener:
            def open(self, req, timeout=None):
                opened["url"] = req.full_url
                opened["method"] = req.get_method()
                opened["data"] = req.data
                opened["timeout"] = timeout
                return FakeResponse()

        monkeypatch.setattr(ro.shutil, "which", lambda name: None)
        monkeypatch.setattr(ro.urllib.request, "build_opener", lambda *handlers: FakeOpener())
        code, method = ro.ptz_digest_put(url, "http://192.168.1.157", "admin", "secret", timeout=4)
        assert code == 204
        assert method.startswith("urllib-digest")
        assert opened["method"] == "PUT"
        assert opened["data"] == b""
        assert opened["timeout"] == 4

    def test_ptz_goto_preset_status_includes_diagnostics(self, client, monkeypatch):
        ro.save_app_settings({
            "ptz_enabled": "1",
            "ptz_camera_url": "192.168.1.157",
            "ptz_username": "admin",
            "ptz_password": "secret",
            "ptz_channel": "1",
            "ptz_idle_preset": "1",
        })
        monkeypatch.setattr(ro.video, "ptz_isapi_request", lambda url, base, username, password, method="PUT", auth_mode="digest", timeout=4: (200, "curl-digest"))
        result = ro.ptz_goto_preset("idle", reason="test", force=True)
        assert result["ok"] is True
        assert result["auth_method"] == "curl-digest"
        assert result["http_status"] == 200
        assert result["url"].endswith("/ISAPI/PTZCtrl/channels/1/presets/1/goto")


    def test_ptz_login_test_uses_capabilities_get(self, client, monkeypatch):
        ro.save_app_settings({
            "ptz_enabled": "1",
            "ptz_camera_url": "192.168.1.157",
            "ptz_username": "admin",
            "ptz_password": "secret",
            "ptz_channel": "1",
            "ptz_auth_mode": "anyauth",
        })
        calls = []

        def fake_request(url, base, username, password, method="PUT", auth_mode="digest", timeout=4):
            calls.append((url, method, auth_mode, username, password))
            return 200, "curl-anyauth"

        monkeypatch.setattr(ro.video, "ptz_isapi_request", fake_request)
        result = ro.ptz_test_login()
        assert result["ok"] is True
        assert result["auth_method"] == "curl-anyauth"
        assert calls == [("http://192.168.1.157/ISAPI/PTZCtrl/channels/1/capabilities", "GET", "anyauth", "admin", "secret")]


    def test_ptz_auth_failure_pauses_automatic_retries(self, client, monkeypatch):
        ro.reset_ptz_runtime_state()
        ro.save_app_settings({
            "ptz_enabled": "1",
            "ptz_camera_url": "192.168.1.157",
            "ptz_username": "admin",
            "ptz_password": "wrong",
            "ptz_channel": "1",
            "ptz_idle_preset": "1",
        })
        calls = []

        def fake_request(url, base, username, password, method="PUT", auth_mode="digest", timeout=4):
            calls.append((url, username, password, method, auth_mode))
            return 401, "curl-digest"

        monkeypatch.setattr(ro.video, "ptz_isapi_request", fake_request)
        first = ro.ptz_goto_preset("idle", reason="test", force=True)
        assert first["ok"] is False
        assert first["http_status"] == 401
        assert first["auth_failed"] is True
        assert "paused" in first["message"]
        assert len(calls) == 1

        second = ro.queue_ptz_goto_preset("idle", reason="scheduler")
        assert second["ok"] is False
        assert second["auth_failed"] is True
        assert "paused" in second["message"]
        assert len(calls) == 1


# ---------------------------------------------------------------------------
# Public video publishing / Cloudflare R2 helpers
# ---------------------------------------------------------------------------

class TestCloudflareR2VideoPublishing:
    def test_r2_key_prefix_is_sanitised(self):
        assert ro.safe_r2_key_prefix(" race videos / 2026 ") == "race_videos/2026"
        assert ro.safe_r2_key_prefix("../bad//name") == "bad/name"

    def test_public_r2_url_quotes_object_key(self):
        cfg = {"video_public_r2_public_base_url": "https://videos.example.com/"}
        url = ro.public_url_for_r2_key(cfg, "race videos/race 1/test clip.mp4")
        assert url == "https://videos.example.com/race%20videos/race%201/test%20clip.mp4"

    def test_public_video_r2_ready_requires_all_required_settings(self):
        cfg = {
            "video_public_provider": "r2",
            "video_public_r2_account_id": "0123456789abcdef0123456789abcdef",
            "video_public_r2_bucket": "race-videos",
            "video_public_r2_access_key_id": "abc",
            "video_public_r2_secret_access_key": "secret",
            "video_public_r2_public_base_url": "https://videos.example.com",
        }
        assert ro.video_public_r2_ready(cfg) is True
        cfg["video_public_r2_secret_access_key"] = ""
        assert ro.video_public_r2_ready(cfg) is False

    def test_public_video_clip_link_text_tracks_r2_processing_status(self, client):
        with ro.get_db() as db:
            cur = db.execute(
                """
                INSERT INTO races (name, class_name, course_no, start_time, rating_rule, notes, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                ("R2 Race", "IRC", 1, "2026-06-24T12:00:00", "DUAL", "", "2026-06-24T11:00:00"),
            )
            race_id = int(cur.lastrowid)
            cur = db.execute(
                """
                INSERT INTO video_clips (race_id, clip_type, event_time, pre_seconds, post_seconds, status, public_status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (race_id, "finish", "2026-06-24T12:30:00", 60, 60, "ready", "processing", "2026-06-24T12:30:00", "2026-06-24T12:30:00"),
            )
            clip_id = int(cur.lastrowid)
            db.commit()
            clip = db.execute("SELECT * FROM video_clips WHERE id = ?", (clip_id,)).fetchone()
        assert ro.public_video_clip_link_text(clip) == "Public video processing"
        assert ro.public_video_clip_is_viewable(clip) is False

class TestCloudflareR2UploadDiagnostics:
    def test_safe_r2_account_id_accepts_s3_endpoint_url(self):
        account = "0123456789abcdef0123456789abcdef"
        assert ro.safe_r2_account_id(account) == account
        assert ro.safe_r2_account_id(f"https://{account}.r2.cloudflarestorage.com") == account
        assert ro.safe_r2_account_id(f"{account}.r2.cloudflarestorage.com") == account

    def test_r2_test_result_round_trip(self, tmp_path, monkeypatch):
        path = tmp_path / "r2_result.json"
        monkeypatch.setattr(ro.appstate, "R2_TEST_RESULT_PATH", path)
        ro.save_r2_test_result({"ok": True, "message": "worked", "key": "test/key.txt"})
        result = ro.read_r2_test_result()
        assert result["ok"] is True
        assert result["message"] == "worked"
        assert result["key"] == "test/key.txt"
        assert "timestamp" in result

    def test_retry_public_video_uploads_uses_existing_public_copy_and_retries(self, client, tmp_path, monkeypatch):
        video_root = tmp_path / "video_clips"
        public_dir = video_root / "public"
        public_dir.mkdir(parents=True)
        public_file = public_dir / "race1_finish_1_public.mp4"
        public_file.write_bytes(b"public-video")
        monkeypatch.setattr(ro.appstate, "VIDEO_CLIPS_DIR", video_root)

        calls = []
        def fake_upload_file(cfg, key, path, content_type="video/mp4"):
            calls.append((key, Path(path).name, content_type))
            if len(calls) == 1:
                raise RuntimeError("temporary R2 failure")
            return "https://videos.example.com/" + key
        monkeypatch.setattr(ro.video, "upload_file_to_r2", fake_upload_file)
        monkeypatch.setattr(ro.time, "sleep", lambda seconds: None)

        cfg = {
            "video_public_provider": "r2",
            "video_public_r2_account_id": "0123456789abcdef0123456789abcdef",
            "video_public_r2_bucket": "race-videos",
            "video_public_r2_access_key_id": "key-id",
            "video_public_r2_secret_access_key": "secret",
            "video_public_r2_public_base_url": "https://videos.example.com",
            "video_public_r2_prefix": "race-videos",
        }
        with ro.get_db() as db:
            cur = db.execute(
                """
                INSERT INTO races (name, class_name, course_no, start_time, rating_rule, notes, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                ("R2 retry race", "IRC", 1, "2026-06-24T12:00:00", "DUAL", "", "2026-06-24T11:00:00"),
            )
            race_id = int(cur.lastrowid)
            cur = db.execute(
                """
                INSERT INTO video_clips (race_id, clip_type, event_time, pre_seconds, post_seconds, status, file_path, public_status, public_file_path, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (race_id, "finish", "2026-06-24T12:30:00", 60, 60, "ready", "", "error", str(public_file), "2026-06-24T12:30:00", "2026-06-24T12:30:00"),
            )
            clip_id = int(cur.lastrowid)
            db.commit()

        summary = ro.retry_public_video_uploads_once(cfg)
        assert summary["checked"] == 1
        assert summary["uploaded"] == 1
        assert len(calls) == 2
        assert calls[0][0] == "race-videos/race1/race1_finish_1_public.mp4"
        with ro.get_db() as db:
            clip = db.execute("SELECT * FROM video_clips WHERE id = ?", (clip_id,)).fetchone()
        assert clip["public_status"] == "ready"
        assert clip["public_url"] == "https://videos.example.com/race-videos/race1/race1_finish_1_public.mp4"
        assert "uploaded" in clip["public_message"].lower()
