"""Flask route smoke tests — exercise key pages and API endpoints end-to-end."""
from __future__ import annotations

import sys
from io import BytesIO
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import app as ro


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _post_csrf(client, url: str, data: dict | None = None, **kwargs):
    """POST with a CSRF token injected into the session and form data."""
    token = "test-csrf-token"
    with client.session_transaction() as sess:
        sess["_csrf_token"] = token
    form_data = dict(data or {})
    form_data["_csrf_token"] = token
    return client.post(url, data=form_data, **kwargs)


# ---------------------------------------------------------------------------
# Public routes — accessible without login
# ---------------------------------------------------------------------------

class TestPublicRoutes:
    def test_site_root_redirects_to_competitor_page(self, client):
        resp = client.get("/")
        assert resp.status_code in (301, 302)

    def test_public_current_race_page_loads(self, client):
        resp = client.get("/public/current", follow_redirects=True)
        assert resp.status_code == 200

    def test_public_mobile_current_loads(self, client):
        resp = client.get("/public/mobile/current", follow_redirects=True)
        assert resp.status_code == 200

    def test_static_css_served(self, client):
        resp = client.get("/static/style.css")
        assert resp.status_code == 200

    def test_login_page_renders(self, client):
        # /login redirects to the canonical /admin/login URL (legacy redirect)
        resp = client.get("/admin/login")
        assert resp.status_code == 200
        assert b"username" in resp.data.lower() or b"login" in resp.data.lower()

    def test_legacy_login_url_redirects(self, client):
        resp = client.get("/login")
        assert resp.status_code in (301, 302)
        assert "/admin/login" in resp.headers.get("Location", "")


# ---------------------------------------------------------------------------
# Public JSON APIs — no auth required
# ---------------------------------------------------------------------------

class TestPublicApiEndpoints:
    def test_api_courses_returns_course_list(self, client):
        resp = client.get("/api/courses")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "courses" in data
        assert isinstance(data["courses"], list)
        assert len(data["courses"]) > 0

    def test_api_marks_returns_mark_data(self, client):
        resp = client.get("/api/marks")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "marks" in data

    def test_api_weather_current_returns_ok_key(self, client):
        resp = client.get("/api/weather/current")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "ok" in data

    def test_api_weather_history_returns_samples(self, client):
        resp = client.get("/api/weather/history")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "samples" in data

    def test_api_course_leg_analysis_valid_course(self, client):
        course_no = ro.COURSES[0]["course_no"]
        resp = client.get(f"/api/course/{course_no}/leg_analysis?twd=225&tws=12")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "ok" in data

    def test_api_course_leg_analysis_uses_matching_sail_chart_for_polar(self, client):
        course_no = ro.COURSES[0]["course_no"]
        resp = client.get(f"/api/course/{course_no}/leg_analysis?twd=225&tws=12&polar_file=J109.txt")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert data["polar_file"] == "J109.txt"
        assert data["sail_chart_file"] == "J109-SailChart.txt"
        assert data["sail_chart_source"] == "polar-specific"

    def test_api_course_leg_analysis_unknown_course(self, client):
        resp = client.get("/api/course/99999/leg_analysis")
        assert resp.status_code == 404

    def test_api_custom_leg_analysis_no_auth(self, client):
        # Custom leg analysis endpoint is public (used by competitor page)
        resp = client.post(
            "/api/custom_leg_analysis",
            json={"course": {"marks": []}, "twd": 225, "tws": 12},
        )
        assert resp.status_code in (200, 400)  # 400 if payload invalid, 200 if handled

    def test_api_current_race_course_without_race_returns_404(self, client):
        resp = client.get("/api/current_race_course")
        assert resp.status_code == 404
        data = resp.get_json()
        assert data["ok"] is False
        assert data["version"] == ro.APP_VERSION

    def test_api_current_race_course_returns_current_fixed_course(self, client):
        with ro.get_db() as db:
            cur = db.execute(
                """
                INSERT INTO races (name, class_name, course_no, start_time, rating_rule, notes, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                ("API Race", "IRC", 1, "2026-06-24T12:00:00", "DUAL", "", "2026-06-24T11:00:00"),
            )
            race_id = int(cur.lastrowid)
            db.execute(
                """
                INSERT INTO entries (race_id, boat_name, sail_no, class_name, rating, status)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (race_id, "Test Boat", "GBR 1", "IRC", 1.0, "RACING"),
            )
            db.commit()

        resp = client.get("/api/current_race_course")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert data["version"] == ro.APP_VERSION
        assert data["race"]["id"] == race_id
        assert data["race"]["status"] == "Racing"
        assert data["course"]["source"] == "fixed"
        assert data["course"]["course_no"] == 1
        assert data["course"]["marks"]
        assert data["course"]["expanded_marks"]
        assert data["course"]["legs"]
        assert "O" in data["marks"]
        assert data["course"]["expanded_marks"][0]["code"] == "O"

    def test_api_current_race_course_expands_compound_manual_course(self, client):
        custom_course = {
            "marks": [
                {"mark": "Y", "rounding": "port"},
                {"mark": "A", "rounding": "starboard"},
            ]
        }
        with ro.get_db() as db:
            cur = db.execute(
                """
                INSERT INTO races (name, class_name, course_no, start_time, rating_rule, notes, created_at, custom_course_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ("Compound API Race", "IRC", 1, "2026-06-24T13:00:00", "DUAL", "", "2026-06-24T12:30:00", ro.json.dumps(custom_course)),
            )
            race_id = int(cur.lastrowid)
            db.execute(
                "INSERT INTO entries (race_id, boat_name, sail_no, class_name, rating, status) VALUES (?, ?, ?, ?, ?, ?)",
                (race_id, "Compound Boat", "GBR 2", "IRC", 1.0, "RACING"),
            )
            db.commit()

        resp = client.get("/api/current_race_course")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["race"]["id"] == race_id
        assert data["course"]["source"] == "manual"
        assert data["race"]["custom_course"] is True
        assert [mark["code"] for mark in data["course"]["marks"]] == ["Y", "A"]
        assert [component["code"] for component in data["course"]["marks"][0]["expanded_components"]] == ["YA", "YB"]
        assert [component["code"] for component in data["course"]["marks"][1]["expanded_components"]] == ["AB", "AA"]
        expanded_codes = [mark["code"] for mark in data["course"]["expanded_marks"]]
        assert expanded_codes[:5] == ["O", "YA", "YB", "AB", "AA"]
        assert data["course"]["expanded_marks"][1]["parent_mark"] == "Y"
        assert data["course"]["expanded_marks"][3]["parent_mark"] == "A"


# ---------------------------------------------------------------------------
# Authentication flow
# ---------------------------------------------------------------------------

class TestAuthFlow:
    def test_protected_page_redirects_to_login(self, client):
        resp = client.get("/admin/races")
        assert resp.status_code in (301, 302)
        location = resp.headers.get("Location", "")
        assert "login" in location.lower()

    def test_admin_api_without_auth_returns_401_json(self, client):
        resp = client.get(
            "/api/hardware/status",
            headers={"Accept": "application/json"},
        )
        assert resp.status_code == 401

    def test_login_post_without_csrf_token_recovers(self, client):
        # A login POST without a valid session CSRF token is pre-auth, so instead
        # of a dead-end 400 it bounces back to a fresh login page (see
        # tests/test_login_csrf.py). It must not authenticate the user.
        resp = client.post(
            "/login",
            data={"username": "admin", "password": "testadminpass"},
            follow_redirects=False,
        )
        assert resp.status_code in (302, 303)
        assert "/login" in resp.headers.get("Location", "")
        with client.session_transaction() as sess:
            assert not sess.get("user_id")

    def test_login_with_wrong_password_shows_error(self, client):
        resp = _post_csrf(
            client,
            "/login",
            {"username": "admin", "password": "wrongpassword"},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert b"invalid" in resp.data.lower()

    def test_login_with_unknown_user_shows_error(self, client):
        resp = _post_csrf(
            client,
            "/login",
            {"username": "nobody", "password": "anything"},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert b"invalid" in resp.data.lower()

    def test_successful_login_redirects(self, client):
        resp = _post_csrf(
            client,
            "/login",
            {"username": "admin", "password": "testadminpass"},
        )
        # Should redirect to dashboard on success
        assert resp.status_code in (200, 302)
        with client.session_transaction() as sess:
            assert sess.get(ro.SESSION_APP_VERSION_KEY) == ro.APP_VERSION

    def test_authenticated_session_expires_after_app_update(self, logged_in_client, monkeypatch):
        monkeypatch.setattr(ro, "APP_VERSION", f"{ro.APP_VERSION}-next")
        resp = logged_in_client.get("/admin/races")
        assert resp.status_code in (301, 302)
        assert "login" in resp.headers.get("Location", "").lower()
        with logged_in_client.session_transaction() as sess:
            assert "user_id" not in sess
            assert ro.SESSION_APP_VERSION_KEY not in sess

    def test_stale_admin_api_session_returns_401(self, logged_in_client, monkeypatch):
        monkeypatch.setattr(ro, "APP_VERSION", f"{ro.APP_VERSION}-next")
        resp = logged_in_client.get(
            "/api/hardware/status",
            headers={"Accept": "application/json"},
        )
        assert resp.status_code == 401
        assert b"app update" in resp.data.lower()

    def test_unsafe_next_url_ignored(self, client):
        # next=http://evil.com must not be followed
        resp = _post_csrf(
            client,
            "/login",
            {
                "username": "admin",
                "password": "testadminpass",
                "next": "http://evil.com/steal",
            },
            follow_redirects=False,
        )
        location = resp.headers.get("Location", "")
        assert "evil.com" not in location

    def test_logout_clears_session(self, logged_in_client):
        # Following the logout redirect causes the test client to process the
        # /login page response, which gives it the updated (empty) session cookie.
        logged_in_client.get("/logout", follow_redirects=True)
        resp = logged_in_client.get("/admin/races")
        assert resp.status_code in (301, 302)


# ---------------------------------------------------------------------------
# Authenticated race-officer pages
# ---------------------------------------------------------------------------

class TestAdminPages:
    def test_dashboard(self, logged_in_client):
        resp = logged_in_client.get("/admin")
        assert resp.status_code == 200

    def test_dashboard_includes_analog_wind_gauge(self, logged_in_client):
        resp = logged_in_client.get("/admin")
        assert resp.status_code == 200
        assert b'id="dashboardWindGauge"' in resp.data
        assert b'class="wind-dial"' in resp.data
        # The dial markup and its script are shared with the public home page
        # (v0.181): one partial, one wind_gauge.js driving every .wind-gauge.
        assert b'wind_gauge.js' in resp.data
        assert b'class="wind-gauge' in resp.data
        assert b'Analog wind speed and direction indicator' in resp.data

    def test_dashboard_wind_javascript_served(self, logged_in_client):
        resp = logged_in_client.get("/static/wind_gauge.js")
        assert resp.status_code == 200
        assert b'/api/weather/current' in resp.data
        assert b'.wind-gauge' in resp.data

    def test_races_page(self, logged_in_client):
        resp = logged_in_client.get("/admin/races")
        assert resp.status_code == 200

    def test_boats_page(self, logged_in_client):
        resp = logged_in_client.get("/admin/boats")
        assert resp.status_code == 200

    def test_series_page(self, logged_in_client):
        resp = logged_in_client.get("/admin/series")
        assert resp.status_code == 200

    def test_settings_page(self, logged_in_client):
        resp = logged_in_client.get("/admin/settings")
        assert resp.status_code == 200

    def test_settings_video_section_has_subsections(self, logged_in_client):
        resp = logged_in_client.get("/admin/settings")
        assert resp.status_code == 200
        assert b'Camera input and recording mode' in resp.data
        assert b'Live preview and buffer' in resp.data
        assert b'Public video and live image publishing' in resp.data
        assert b'Camera zoom / PTZ presets' in resp.data
        assert b'Recorder status' in resp.data

    def test_settings_horn_section_has_subsections(self, logged_in_client):
        resp = logged_in_client.get("/admin/settings")
        assert resp.status_code == 200
        assert b'Horn output, input sensing and central audio' in resp.data
        assert b'Horn output' in resp.data
        assert b'Manual horn input sensing' in resp.data
        assert b'Start automation and central audio' in resp.data
        assert b'Save and test horn output' in resp.data
        assert b'Current output:' in resp.data
        assert b'This is wired like the ProLog interface' not in resp.data

    def test_settings_manual_horn_poll_uses_admin_json_api(self, logged_in_client):
        resp = logged_in_client.get("/admin/settings")
        assert resp.status_code == 200
        page = resp.data.decode("utf-8")
        assert "/admin/api/hardware/input_status?log=1" in page
        assert "content-type" in page
        assert "Input status unavailable:" in page
        assert "Unexpected token" not in page

    def test_manual_horn_input_status_api_returns_json(self, logged_in_client):
        resp = logged_in_client.get("/admin/api/hardware/input_status?log=1", headers={"Accept": "application/json"})
        assert resp.status_code == 200
        assert resp.headers["Content-Type"].startswith("application/json")
        assert resp.headers["Cache-Control"] == "no-store"
        data = resp.get_json()
        assert data["ok"] is True
        assert data["enabled"] is False
        assert "Manual horn input sensing is disabled" in data["message"]

    def test_manual_horn_input_status_api_requires_login_as_json(self, client):
        resp = client.get("/admin/api/hardware/input_status?log=1", headers={"Accept": "application/json"})
        assert resp.status_code == 401
        assert resp.headers["Content-Type"].startswith("application/json")
        assert resp.get_json()["error"] == "Authentication required"

    def test_wind_history_tws_scale_starts_at_zero(self, client):
        resp = client.get("/static/wind_history.js")
        assert resp.status_code == 200
        js = resp.data.decode("utf-8")
        assert "twsStartAtZero: true" in js
        assert "let twsMin = opts.twsStartAtZero ? 0" in js

    def test_wind_history_has_extra_scale_ticks_and_tws_bars(self, client):
        resp = client.get("/static/wind_history.js")
        assert resp.status_code == 200
        js = resp.data.decode("utf-8")
        assert "scaleTickCount: 7" in js
        assert "twsRenderMode: 'bars'" in js
        assert "function plotHorizontalBars" in js
        assert "plotHorizontalBars(visible, xTws, 'tws'" in js

    def test_hardware_page_redirects_to_settings(self, logged_in_client):
        # /admin/hardware is now a shim that redirects to the Settings page's
        # hardware section; follow the redirect to confirm the destination loads.
        resp = logged_in_client.get("/admin/hardware", follow_redirects=True)
        assert resp.status_code == 200

    def test_marks_page(self, logged_in_client):
        resp = logged_in_client.get("/admin/marks")
        assert resp.status_code == 200

    def test_recommend_page(self, logged_in_client):
        resp = logged_in_client.get("/admin/recommend")
        assert resp.status_code == 200

    def test_legacy_url_redirected(self, logged_in_client):
        # Old /races URL should redirect to /admin/races
        resp = logged_in_client.get("/races")
        assert resp.status_code in (301, 302)
        location = resp.headers.get("Location", "")
        assert "/admin/races" in location

    def test_split_screen_requires_login(self, client):
        resp = client.get("/split")
        assert resp.status_code in (301, 302)
        assert "login" in resp.headers.get("Location", "").lower()

    def test_split_screen_embeds_admin_and_public_pages(self, logged_in_client):
        resp = logged_in_client.get("/split")
        assert resp.status_code == 200
        assert b'class="split-screen-layout"' in resp.data
        assert b'src="/admin"' in resp.data
        assert b'src="/public/current"' in resp.data
        assert b'Race officer admin dashboard' in resp.data
        assert b'Public competitor page' in resp.data

    def test_admin_split_alias_loads(self, logged_in_client):
        resp = logged_in_client.get("/admin/split")
        assert resp.status_code == 200
        assert b'class="split-screen-layout"' in resp.data

    def test_sidebar_does_not_include_split_screen_link(self, logged_in_client):
        resp = logged_in_client.get("/admin")
        assert resp.status_code == 200
        assert b'href="/split"' not in resp.data
        assert b'Split screen' not in resp.data

    def test_css_defines_75_25_split_layout(self, client):
        resp = client.get("/static/style.css")
        assert resp.status_code == 200
        css = resp.data.decode("utf-8")
        assert ".split-screen-layout" in css
        assert "grid-template-columns: 3fr 1fr" in css


# ---------------------------------------------------------------------------
# Race creation and management
# ---------------------------------------------------------------------------

class TestRaceManagement:
    def test_new_race_form_renders(self, logged_in_client):
        resp = logged_in_client.get("/admin/race/new")
        assert resp.status_code == 200

    def test_create_race_and_view_it(self, logged_in_client):
        course_no = ro.COURSES[0]["course_no"]
        resp = _post_csrf(
            logged_in_client,
            "/admin/race/new",
            {
                "name": "Test Race",
                "course_no": str(course_no),
                "start_time": "2026-06-23T10:00",
                "rating_rule": "IRC_TCC",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        # Should land on the race detail page
        assert b"Test Race" in resp.data or b"race" in resp.data.lower()

    def test_add_entries_tab_shows_simple_entry_list(self, logged_in_client):
        with ro.get_db() as db:
            cur = db.execute(
                "INSERT INTO races (name, class_name, course_no, start_time, rating_rule, notes, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("Entry List Test", "IRC", ro.COURSES[0]["course_no"], "2026-06-23T10:00:00", "IRC_TCC", "", "2026-06-23T09:00:00"),
            )
            race_id = int(cur.lastrowid)
            db.execute(
                "INSERT INTO entries (race_id, boat_name, sail_no, class_name, rating, status) VALUES (?, ?, ?, ?, ?, ?)",
                (race_id, "Mojito", "GBR 122", "IRC", 1.0, "RACING"),
            )
            db.commit()

        resp = logged_in_client.get(f"/admin/race/{race_id}#tab-entries")
        assert resp.status_code == 200
        assert b"Entries in this race" in resp.data
        assert b"Mojito" in resp.data
        assert b"GBR 122" in resp.data

    def test_start_tab_has_compact_horn_config_text(self, logged_in_client):
        with ro.get_db() as db:
            cur = db.execute(
                "INSERT INTO races (name, class_name, course_no, start_time, rating_rule, notes, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("Start Tab Test", "IRC", ro.COURSES[0]["course_no"], "2026-06-23T10:00:00", "IRC_TCC", "", "2026-06-23T09:00:00"),
            )
            db.commit()
            race_id = int(cur.lastrowid)

        resp = logged_in_client.get(f"/admin/race/{race_id}")
        assert resp.status_code == 200
        assert b"horn-config-line" in resp.data
        assert b"ProLog feedback is" not in resp.data

    def test_race_detail_redirects_for_unknown_id(self, logged_in_client):
        # Unknown race IDs flash an error and redirect to the dashboard (302).
        resp = logged_in_client.get("/admin/race/99999")
        assert resp.status_code == 302


# ---------------------------------------------------------------------------
# Database schema
# ---------------------------------------------------------------------------

class TestDatabaseSchema:
    EXPECTED_TABLES = {
        "races", "entries", "boats", "race_events", "users",
        "app_settings", "hardware_settings", "weather_samples",
        "video_clips", "race_series",
    }

    def test_all_expected_tables_exist(self, client):
        with ro.get_db() as db:
            tables = {
                row[0]
                for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
        assert self.EXPECTED_TABLES.issubset(tables)

    def test_admin_user_created_on_init(self, client):
        with ro.get_db() as db:
            user = db.execute(
                "SELECT * FROM users WHERE lower(username) = 'admin'"
            ).fetchone()
        assert user is not None
        assert user["role"] == "admin"
        assert user["status"] == "ACTIVE"

    def test_entries_has_irc_ytc_snapshot_columns(self, client):
        with ro.get_db() as db:
            cols = ro.table_columns(db, "entries")
        assert "manual_irc_rating" in cols
        assert "manual_ytc_rating" in cols

    def test_races_has_series_and_polar_columns(self, client):
        with ro.get_db() as db:
            cols = ro.table_columns(db, "races")
        assert "series_id" in cols
        assert "polar_file" in cols

    def test_race_series_has_class_config(self, client):
        with ro.get_db() as db:
            cols = ro.table_columns(db, "race_series")
        assert "class_config_json" in cols
        assert "discard_profile" in cols


class TestVersionDisplay:
    def test_admin_dashboard_shows_app_version(self, logged_in_client):
        resp = logged_in_client.get("/admin")
        assert resp.status_code == 200
        assert f"v{ro.APP_VERSION}".encode() in resp.data

    def test_public_competitor_home_shows_app_version(self, client):
        resp = client.get("/public/current", follow_redirects=True)
        assert resp.status_code == 200
        assert f"v{ro.APP_VERSION}".encode() in resp.data


class TestLiveRaceLogUpdates:
    def _create_race(self) -> int:
        with ro.get_db() as db:
            cur = db.execute(
                "INSERT INTO races (name, class_name, course_no, start_time, rating_rule, notes, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("Live Log Test", "IRC", ro.COURSES[0]["course_no"], "2026-06-23T10:00:00", "IRC_TCC", "", "2026-06-23T09:00:00"),
            )
            db.commit()
            return int(cur.lastrowid)

    def test_live_race_log_api_returns_only_events_after_known_id(self, logged_in_client):
        race_id = self._create_race()
        first_id = ro.log_event(race_id, "audio", "Audio: course announcement", "central-start-sequence")
        second_id = ro.log_event(race_id, "horn", "Start signal", "central-start-sequence")

        resp = logged_in_client.get(f"/api/race/{race_id}/events?after_id={first_id}")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert [ev["id"] for ev in data["events"]] == [second_id]
        assert data["events"][0]["label"] == "Start signal"
        assert "event_time_display" in data["events"][0]

    def test_live_race_log_api_requires_login(self, client):
        race_id = self._create_race()
        resp = client.get(f"/api/race/{race_id}/events", headers={"Accept": "application/json"})
        assert resp.status_code == 401

    def test_final_audio_countdown_is_one_loggable_summary_event(self, client):
        race_id = self._create_race()
        race = ro.get_race(race_id)
        events = ro.central_start_sequence_events(race)
        countdown = [ev for ev in events if str(ev.get("label", "")).startswith("Audio: countdown")]
        loggable = [ev for ev in countdown if ev.get("log_event", True)]
        # The final countdown is spoken as a single utterance over the last ten
        # seconds, so there is exactly one event.
        assert len(countdown) == 1
        assert len(loggable) == 1
        assert loggable[0]["label"] == "Audio: countdown 10 to 1"
        # Started early enough that "One" lands on the start signal rather than a
        # second late. How early depends on the countdown speech rate -- the phrase
        # is one utterance and a slower voice takes longer over it -- so this is
        # derived rather than the fixed eleven it used to be.
        from core.startsequence import countdown_lead_seconds
        rate = ro.race_console_config().get("central_audio_fast_rate")
        assert loggable[0]["sec"] == countdown_lead_seconds(rate)
        assert loggable[0]["rate"] == rate, "the ten-count is not on the countdown rate"
        assert loggable[0]["text"] == "Ten. Nine. Eight. Seven. Six. Five. Four. Three. Two. One."

    def test_vox_tone_events_precede_audio_when_enabled(self, client):
        race_id = self._create_race()
        race = ro.get_race(race_id)
        assert not any(ev.get("kind") == "tone" for ev in ro.central_start_sequence_events(race))
        ro.save_app_settings({"central_audio_vox_tone_enabled": "1", "central_audio_vox_lead_seconds": "2"})
        events = ro.central_start_sequence_events(race)
        tones = [ev for ev in events if ev.get("kind") == "tone"]
        assert tones, "expected VOX tone events when enabled"
        tone_secs = {ev["sec"] for ev in tones}
        # A tone two seconds before the countdown and before the 0 s start call.
        # The countdown's own position follows the speech rate, so this follows it.
        from core.startsequence import countdown_lead_seconds
        lead = countdown_lead_seconds(ro.race_console_config().get("central_audio_fast_rate"))
        assert lead + 2 in tone_secs and 2 in tone_secs, sorted(tone_secs)


class TestCompetitorRaceHeaderLayout:
    def test_competitor_race_page_uses_non_overlapping_header_layout(self, client):
        with ro.get_db() as db:
            cur = db.execute(
                "INSERT INTO races (name, class_name, course_no, start_time, rating_rule, notes, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("Public Header Test", "IRC", ro.COURSES[0]["course_no"], "2026-06-23T10:00:00", "IRC_TCC", "", "2026-06-23T09:00:00"),
            )
            db.commit()
            race_id = int(cur.lastrowid)
        resp = client.get(f"/public/race/{race_id}")
        assert resp.status_code == 200
        assert b"competitor-race-page" in resp.data

    def test_competitor_race_page_lists_entries_with_ratings(self, client):
        with ro.get_db() as db:
            cur = db.execute(
                "INSERT INTO races (name, class_name, course_no, start_time, rating_rule, notes, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("Entries List Test", "IRC", ro.COURSES[0]["course_no"], "2026-06-23T10:00:00", "IRC_TCC", "", "2026-06-23T09:00:00"),
            )
            race_id = int(cur.lastrowid)
            db.execute(
                "INSERT INTO entries (race_id, boat_name, sail_no, class_name, status, manual_irc_rating, manual_ytc_rating) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (race_id, "Firefly", "GBR1234", "IRC1", "RACING", 1.045, 0.962),
            )
            db.commit()
        resp = client.get(f"/public/race/{race_id}")
        assert resp.status_code == 200
        body = resp.data.decode("utf-8")
        assert 'id="publicEntriesPanel"' in body
        # entries panel appears above the course analysis panel
        assert body.index('id="publicEntriesPanel"') < body.index('id="publicCourseAnalysisPanel"')
        for token in ("Firefly", "GBR1234", "IRC1", "1.045", "0.962", "RACING"):
            assert token in body, token

    def test_competitor_race_page_adds_a_leader_board_tab_when_finished(self, client):
        with ro.get_db() as db:
            cur = db.execute(
                "INSERT INTO races (name, class_name, course_no, start_time, rating_rule, notes, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("Finished Entries Test", "IRC", ro.COURSES[0]["course_no"], "2026-06-23T10:00:00", "IRC_TCC", "", "2026-06-23T09:00:00"),
            )
            race_id = int(cur.lastrowid)
            db.execute(
                "INSERT INTO entries (race_id, boat_name, sail_no, class_name, status, finish_time, rating, manual_irc_rating, manual_ytc_rating) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (race_id, "Firefly", "GBR1234", "IRC1", "FINISHED", "2026-06-23T11:30:00", 1.045, 1.045, 0.962),
            )
            db.commit()
        resp = client.get(f"/public/race/{race_id}")
        assert resp.status_code == 200
        body = resp.data.decode("utf-8")
        # Once everyone has finished, the results appear as a fourth "Leader board"
        # tab and open first — but the entries, chart and course analysis stay
        # available as a record of the race (v0.180).
        assert 'id="publicResultsPanel"' in body
        assert 'data-tab="ptab-results"' in body
        assert ">Leader board</button>" in body
        assert 'class="tab-pane active" id="ptab-results"' in body
        assert 'id="publicEntriesPanel"' in body
        assert 'id="publicCourseAnalysisPanel"' in body

    def test_css_places_competitor_race_navigation_below_logo(self, client):
        resp = client.get("/static/style.css")
        assert resp.status_code == 200
        css = resp.data.decode("utf-8")
        assert ".competitor-race-page > header" in css
        assert "flex-direction: column" in css

    def test_css_centres_public_logo_and_buttons(self, client):
        resp = client.get("/static/style.css")
        assert resp.status_code == 200
        css = resp.data.decode("utf-8")
        assert ".public-action-row" in css
        assert ".public-page .public-club-brand" in css
        assert "justify-content: center" in css


class TestPolarFileManagement:
    def _isolate_polar_dirs(self, tmp_path, monkeypatch):
        polar_dir = tmp_path / "polars"
        sail_dir = tmp_path / "sailcharts"
        default_chart = tmp_path / "DefaultSailChart.txt"
        default_chart.write_text("\t35\n10\tJ1\n", encoding="utf-8")
        monkeypatch.setattr(ro.appstate, "POLARS_DIR", polar_dir)
        monkeypatch.setattr(ro.appstate, "SAIL_CHARTS_DIR", sail_dir)
        monkeypatch.setattr(ro.appstate, "SAIL_CHART_PATH", default_chart)
        polar_dir.mkdir()
        sail_dir.mkdir()
        return polar_dir, sail_dir

    def test_settings_page_lists_polars_with_expected_sail_chart_name(self, logged_in_client, tmp_path, monkeypatch):
        polar_dir, sail_dir = self._isolate_polar_dirs(tmp_path, monkeypatch)
        (polar_dir / "TestBoat.txt").write_text("6 52 4.6 60 4.9\n", encoding="utf-8")

        resp = logged_in_client.get("/admin/settings")
        assert resp.status_code == 200
        assert b"TestBoat.txt" in resp.data
        assert b"TestBoat-SailChart.txt" in resp.data
        assert b"Optional matching sail chart" in resp.data

    def test_upload_polar_with_optional_sail_chart_uses_canonical_chart_name(self, logged_in_client, tmp_path, monkeypatch):
        polar_dir, sail_dir = self._isolate_polar_dirs(tmp_path, monkeypatch)
        resp = _post_csrf(
            logged_in_client,
            "/admin/settings/polars/upload",
            {
                "polar_upload": (BytesIO(b"6 52 4.66 60 4.92\n"), "J70.txt"),
                "sail_chart_upload": (BytesIO(b"\t35\t40\n10\tJ1\tJ1\n"), "anything.csv"),
            },
            content_type="multipart/form-data",
        )
        assert resp.status_code in (301, 302)
        assert (polar_dir / "J70.txt").exists()
        assert (sail_dir / "J70-SailChart.csv").exists()
        assert not (sail_dir / "anything.csv").exists()

    def test_upload_sail_chart_for_existing_polar_replaces_matching_chart(self, logged_in_client, tmp_path, monkeypatch):
        polar_dir, sail_dir = self._isolate_polar_dirs(tmp_path, monkeypatch)
        (polar_dir / "J109.txt").write_text("6 52 4.6 60 4.9\n", encoding="utf-8")

        resp = _post_csrf(
            logged_in_client,
            "/admin/settings/polars/sailchart/upload",
            {
                "polar_file": "J109.txt",
                "sail_chart_upload": (BytesIO(b"\t35\n10\tJ1\n"), "J109 chart.txt"),
            },
            content_type="multipart/form-data",
        )
        assert resp.status_code in (301, 302)
        assert (sail_dir / "J109-SailChart.txt").exists()

    def test_delete_sail_chart_keeps_polar(self, logged_in_client, tmp_path, monkeypatch):
        polar_dir, sail_dir = self._isolate_polar_dirs(tmp_path, monkeypatch)
        (polar_dir / "J109.txt").write_text("6 52 4.6 60 4.9\n", encoding="utf-8")
        (sail_dir / "J109-SailChart.txt").write_text("\t35\n10\tJ1\n", encoding="utf-8")

        resp = _post_csrf(logged_in_client, "/admin/settings/polars/sailchart/delete", {"polar_file": "J109.txt"})
        assert resp.status_code in (301, 302)
        assert (polar_dir / "J109.txt").exists()
        assert not (sail_dir / "J109-SailChart.txt").exists()

    def test_delete_polar_also_deletes_matching_sail_chart(self, logged_in_client, tmp_path, monkeypatch):
        polar_dir, sail_dir = self._isolate_polar_dirs(tmp_path, monkeypatch)
        (polar_dir / "J109.txt").write_text("6 52 4.6 60 4.9\n", encoding="utf-8")
        (sail_dir / "J109-SailChart.txt").write_text("\t35\n10\tJ1\n", encoding="utf-8")

        resp = _post_csrf(logged_in_client, "/admin/settings/polars/delete", {"polar_file": "J109.txt"})
        assert resp.status_code in (301, 302)
        assert not (polar_dir / "J109.txt").exists()
        assert not (sail_dir / "J109-SailChart.txt").exists()

    def test_bad_sail_chart_extension_is_rejected(self, logged_in_client, tmp_path, monkeypatch):
        polar_dir, sail_dir = self._isolate_polar_dirs(tmp_path, monkeypatch)
        (polar_dir / "J109.txt").write_text("6 52 4.6 60 4.9\n", encoding="utf-8")

        resp = _post_csrf(
            logged_in_client,
            "/admin/settings/polars/sailchart/upload",
            {
                "polar_file": "J109.txt",
                "sail_chart_upload": (BytesIO(b"bad"), "bad.exe"),
            },
            content_type="multipart/form-data",
        )
        assert resp.status_code in (301, 302)
        assert not any(sail_dir.iterdir())


# ---------------------------------------------------------------------------
# Video camera PTZ settings routes
# ---------------------------------------------------------------------------

class TestVideoPtzSettingsRoutes:
    def test_settings_page_contains_ptz_controls(self, logged_in_client):
        resp = logged_in_client.get("/admin/settings")
        assert resp.status_code == 200
        assert b"Camera zoom / PTZ presets" in resp.data
        assert b"ptz_camera_url" in resp.data
        assert b"ptz_idle_preset" in resp.data
        assert b"ptz_recording_preset" in resp.data
        assert b"ptz_auth_mode" in resp.data
        assert b"Save and test camera login only" in resp.data

    def test_settings_page_warns_when_ptz_auth_is_paused(self, logged_in_client):
        ro.reset_ptz_runtime_state()
        with ro.PTZ_LOCK:
            ro.PTZ_RUNTIME_STATE["auth_failed"] = True
            ro.PTZ_RUNTIME_STATE["last_status"] = {
                "ok": False,
                "enabled": True,
                "auth_failed": True,
                "http_status": 401,
                "message": "Camera returned HTTP 401 for preset 1 using curl-digest.",
            }
        resp = logged_in_client.get("/admin/settings")
        assert resp.status_code == 200
        assert b"Automatic PTZ switching is paused" in resp.data
        assert b"prevents repeated bad-login attempts" in resp.data

    def test_settings_save_persists_ptz_config(self, logged_in_client):
        resp = _post_csrf(
            logged_in_client,
            "/admin/settings/save",
            {
                "ptz_enabled": "1",
                "ptz_camera_url": "192.168.1.157",
                "ptz_username": "admin",
                "ptz_password": "secret",
                "ptz_auth_mode": "anyauth",
                "ptz_channel": "1",
                "ptz_idle_preset": "1",
                "ptz_recording_preset": "2",
                "ptz_pre_start_seconds": "30",
            },
            follow_redirects=False,
        )
        assert resp.status_code in (302, 303)
        cfg = ro.video_config()
        assert cfg["ptz_enabled"] is True
        assert cfg["ptz_camera_url"] == "http://192.168.1.157"
        assert cfg["ptz_username"] == "admin"
        assert cfg["ptz_password"] == "secret"
        assert cfg["ptz_auth_mode"] == "anyauth"
        assert cfg["ptz_idle_preset"] == 1
        assert cfg["ptz_recording_preset"] == 2
        assert cfg["ptz_pre_start_seconds"] == 30

    def test_blank_ptz_password_keeps_saved_value(self, logged_in_client):
        ro.save_app_settings({
            "ptz_enabled": "1",
            "ptz_camera_url": "192.168.1.157",
            "ptz_username": "admin",
            "ptz_password": "secret",
        })
        ro.save_app_settings({
            "ptz_enabled": "1",
            "ptz_camera_url": "192.168.1.157",
            "ptz_username": "admin",
            "ptz_password": "",
        })
        assert ro.video_config()["ptz_password"] == "secret"

    def test_clear_ptz_password_removes_saved_value(self, logged_in_client):
        ro.save_app_settings({
            "ptz_enabled": "1",
            "ptz_camera_url": "192.168.1.157",
            "ptz_username": "admin",
            "ptz_password": "secret",
        })
        ro.save_app_settings({
            "ptz_enabled": "1",
            "ptz_camera_url": "192.168.1.157",
            "ptz_username": "admin",
            "ptz_password": "",
            "ptz_password_clear": "1",
        })
        assert ro.video_config()["ptz_password"] == ""


class TestVideoStreamCopySettings:
    def test_settings_page_contains_stream_copy_controls(self, logged_in_client):
        resp = logged_in_client.get("/admin/settings")
        assert resp.status_code == 200
        assert b"video_recording_mode" in resp.data
        assert b"Camera stream copy" in resp.data
        assert b"video_copy_container" in resp.data
        assert b"Fragmented MP4" in resp.data
        assert b"video_preview_rtsp_url" in resp.data
        assert b"video_preview_size" in resp.data
        assert b"Native preview stream size" in resp.data
        assert b"video_preview_keyframes_only" in resp.data
        assert b"video_rtsp_timestamp_mode" in resp.data
        assert b"Camera timestamps" in resp.data
        assert b"Streaming/Channels/102" in resp.data

    def test_settings_save_persists_stream_copy_config(self, logged_in_client):
        resp = _post_csrf(
            logged_in_client,
            "/admin/settings/save",
            {
                "video_enabled": "1",
                "video_source_type": "rtsp",
                "video_rtsp_url": "rtsp://user:pass@192.168.1.157/Streaming/Channels/101",
                "video_preview_rtsp_url": "rtsp://user:pass@192.168.1.157/Streaming/Channels/102",
                "video_recording_mode": "copy",
                "video_copy_container": "mp4",
                "video_rtsp_timestamp_mode": "camera",
                "video_preview_size": "1280",
                "video_preview_fps": "5",
                "video_preview_jpeg_quality": "2",
                "video_preview_keyframes_only": "1",
                "video_segment_seconds": "5",
            },
            follow_redirects=False,
        )
        assert resp.status_code in (302, 303)
        cfg = ro.video_config()
        assert cfg["video_source_type"] == "rtsp"
        assert cfg["video_recording_mode"] == "copy"
        assert cfg["video_copy_container"] == "mp4"
        assert cfg["video_rtsp_timestamp_mode"] == "camera"
        assert cfg["video_preview_size"] == "1280"
        assert cfg["video_preview_fps"] == 5
        assert cfg["video_preview_jpeg_quality"] == 2
        assert cfg["video_preview_keyframes_only"] is True
        assert cfg["video_preview_rtsp_url"].endswith("/Streaming/Channels/102")

    def test_usb_source_forces_reencode_mode(self):
        assert ro.normalise_video_recording_mode("usb", "copy") == "reencode"
        assert ro.normalise_video_recording_mode("rtsp", "copy") == "copy"
        assert ro.normalise_video_recording_mode("rtsp", "") == "copy"
        assert ro.normalise_video_copy_container("") == "mp4"
        assert ro.normalise_video_copy_container("mpegts") == "ts"
        assert ro.normalise_video_rtsp_timestamp_mode("") == "camera"
        assert ro.normalise_video_rtsp_timestamp_mode("wall_clock") == "wallclock"
        assert ro.normalise_video_preview_size("") == "native"
        assert ro.normalise_video_preview_size("source") == "native"
        assert ro.normalise_video_preview_size("1280") == "1280"
        assert ro.normalise_video_preview_size("9999") == "native"

    def test_stream_copy_command_records_main_stream_only(self):
        cfg = {
            "video_source_type": "rtsp",
            "video_rtsp_url": "rtsp://camera/Streaming/Channels/101",
            "video_preview_rtsp_url": "rtsp://camera/Streaming/Channels/102",
            "video_recording_mode": "copy",
            "video_copy_container": "mp4",
            "video_rtsp_timestamp_mode": "camera",
            "video_segment_seconds": 5,
        }
        cmd = ro.build_video_recorder_command("ffmpeg", cfg)
        text = " ".join(cmd)
        assert "rtsp://camera/Streaming/Channels/101" in cmd
        assert "rtsp://camera/Streaming/Channels/102" not in cmd
        assert "-rtsp_flags prefer_tcp" in text
        assert "-use_wallclock_as_timestamps" not in cmd
        assert "+genpts+discardcorrupt" not in text
        assert "-c:v copy" in text
        assert "libx264" not in cmd
        assert "-force_key_frames" not in cmd
        assert "-segment_format mp4" in text
        assert "movflags=+frag_keyframe+empty_moov+default_base_moof" in text
        assert "-break_non_keyframes 1" in text
        assert any(str(part).endswith("%Y%m%dT%H%M%S.mp4") for part in cmd)

    def test_stream_copy_command_can_use_ts_fallback(self):
        cfg = {
            "video_source_type": "rtsp",
            "video_rtsp_url": "rtsp://camera/Streaming/Channels/101",
            "video_recording_mode": "copy",
            "video_copy_container": "ts",
            "video_segment_seconds": 5,
        }
        cmd = ro.build_video_recorder_command("ffmpeg", cfg)
        text = " ".join(cmd)
        assert "-segment_format mpegts" in text
        assert "mpegts_flags=+resend_headers" in text
        assert any(str(part).endswith("%Y%m%dT%H%M%S.ts") for part in cmd)

    def test_stream_copy_preview_command_decodes_preview_stream_separately(self):
        cfg = {
            "video_source_type": "rtsp",
            "video_rtsp_url": "rtsp://camera/Streaming/Channels/101",
            "video_preview_rtsp_url": "rtsp://camera/Streaming/Channels/102",
            "video_recording_mode": "copy",
            "video_segment_seconds": 5,
        }
        cmd = ro.build_video_preview_command("ffmpeg", cfg)
        assert cmd is not None
        text = " ".join(cmd)
        assert "rtsp://camera/Streaming/Channels/102" in cmd
        assert "rtsp://camera/Streaming/Channels/101" not in cmd
        assert "-c:v copy" not in text
        assert "fps=2" in text
        assert "scale=" not in text
        assert "-q:v 3" in text
        assert any(str(part).endswith("latest.jpg") for part in cmd)

    def test_stream_copy_preview_command_can_scale_and_use_keyframes(self):
        cfg = {
            "video_source_type": "rtsp",
            "video_rtsp_url": "rtsp://camera/Streaming/Channels/101",
            "video_preview_rtsp_url": "rtsp://camera/Streaming/Channels/102",
            "video_recording_mode": "copy",
            "video_preview_size": "1280",
            "video_preview_jpeg_quality": "2",
            "video_preview_keyframes_only": True,
        }
        cmd = ro.build_video_preview_command("ffmpeg", cfg)
        assert cmd is not None
        text = " ".join(cmd)
        assert "select='eq(pict_type\\,I)',scale=1280:-2" in text
        assert "fps=" not in text
        assert "-q:v 2" in text

    def test_stream_copy_wallclock_timestamp_mode_is_explicit_fallback(self):
        cfg = {
            "video_source_type": "rtsp",
            "video_rtsp_url": "rtsp://camera/Streaming/Channels/101",
            "video_recording_mode": "copy",
            "video_copy_container": "mp4",
            "video_rtsp_timestamp_mode": "wallclock",
            "video_segment_seconds": 5,
        }
        cmd = ro.build_video_recorder_command("ffmpeg", cfg)
        assert "-use_wallclock_as_timestamps" in cmd

    def test_video_log_health_warnings_detect_rtp_loss(self):
        warnings = ro.video_log_health_warnings("RTP: PT=60: bad cseq 90e1 expected=840a")
        assert warnings
        assert "packet loss" in warnings[0]

    def test_reencode_command_keeps_timestamp_overlay_path(self):
        cfg = {
            "video_source_type": "rtsp",
            "video_rtsp_url": "rtsp://camera/Streaming/Channels/101",
            "video_recording_mode": "reencode",
            "video_segment_seconds": 5,
        }
        cmd = ro.build_video_recorder_command("ffmpeg", cfg)
        text = " ".join(cmd)
        assert "libx264" in cmd
        assert "drawtext=" in text
        assert "-force_key_frames" in cmd

    def test_reencode_command_still_writes_mp4_segments(self):
        cfg = {
            "video_source_type": "rtsp",
            "video_rtsp_url": "rtsp://camera/Streaming/Channels/101",
            "video_recording_mode": "reencode",
            "video_segment_seconds": 5,
        }
        cmd = ro.build_video_recorder_command("ffmpeg", cfg)
        assert any(str(part).endswith("%Y%m%dT%H%M%S.mp4") for part in cmd)
        assert "-segment_format" not in cmd

    def test_matching_video_segments_accepts_ts_and_mp4_buffers(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ro.appstate, "VIDEO_BUFFER_DIR", tmp_path)
        ts_path = tmp_path / "20260628T120000.ts"
        mp4_path = tmp_path / "20260628T120005.mp4"
        ts_path.write_bytes(b"ts")
        mp4_path.write_bytes(b"mp4")
        matches = ro.matching_video_segments(
            ro.datetime(2026, 6, 28, 11, 59, 59),
            ro.datetime(2026, 6, 28, 12, 0, 7),
            {"video_segment_seconds": 5},
        )
        assert matches == [ts_path, mp4_path]

class TestCloudflareR2SettingsRoute:
    def test_settings_page_contains_r2_test_status(self, logged_in_client, tmp_path, monkeypatch):
        monkeypatch.setattr(ro.appstate, "R2_TEST_RESULT_PATH", tmp_path / "r2_result.json")
        ro.save_r2_test_result({
            "ok": True,
            "timestamp": "2026-06-24T12:00:00",
            "message": "Upload to Cloudflare R2 worked.",
            "bucket": "race-videos",
            "key": "race-videos/test/race-officer-r2-test.txt",
            "endpoint": "https://0123456789abcdef0123456789abcdef.r2.cloudflarestorage.com",
            "public_url": "https://videos.example.com/race-videos/test/race-officer-r2-test.txt",
            "public_check_message": "Public URL opened successfully.",
        })
        resp = logged_in_client.get("/admin/settings")
        assert resp.status_code == 200
        assert b"Last R2 test" in resp.data
        assert b"race-videos/test/race-officer-r2-test.txt" in resp.data

    def test_r2_test_route_uploads_and_verifies_public_url(self, logged_in_client, monkeypatch, tmp_path):
        monkeypatch.setattr(ro.appstate, "R2_TEST_RESULT_PATH", tmp_path / "r2_result.json")
        calls = {}
        def fake_upload(cfg, key, body, content_type="application/octet-stream", cache_control=""):
            calls["key"] = key
            calls["body"] = body.decode("utf-8")
            calls["content_type"] = content_type
            return "https://videos.example.com/" + key
        monkeypatch.setattr(ro, "upload_bytes_to_r2", fake_upload)
        monkeypatch.setattr(ro, "check_public_r2_url", lambda url, expected_text="": (True, "Public URL opened successfully."))
        resp = _post_csrf(
            logged_in_client,
            "/admin/settings/video/r2_test",
            {
                "video_public_provider": "r2",
                "video_public_r2_account_id": "https://0123456789abcdef0123456789abcdef.r2.cloudflarestorage.com",
                "video_public_r2_bucket": "race-videos",
                "video_public_r2_access_key_id": "key-id",
                "video_public_r2_secret_access_key": "secret",
                "video_public_r2_public_base_url": "https://videos.example.com",
                "video_public_r2_prefix": "race-videos",
            },
            follow_redirects=False,
        )
        assert resp.status_code in (302, 303)
        assert calls["key"] == "race-videos/test/race-officer-r2-test.txt"
        assert "Pwllheli Race Officer" in calls["body"]
        result = ro.read_r2_test_result()
        assert result["ok"] is True
        assert result["public_check_ok"] is True
        assert result["public_url"].endswith("race-videos/test/race-officer-r2-test.txt")

    def test_settings_page_contains_r2_retry_button(self, logged_in_client):
        resp = logged_in_client.get("/admin/settings")
        assert resp.status_code == 200
        assert b"Save and retry failed public video uploads" in resp.data

    def test_upload_file_to_r2_accepts_cache_control(self, tmp_path, monkeypatch):
        upload_path = tmp_path / "latest_public.jpg"
        upload_path.write_bytes(b"fake-jpeg")
        calls = {}

        def fake_upload_bytes(cfg, key, body, content_type="application/octet-stream", cache_control=""):
            calls["cfg"] = cfg
            calls["key"] = key
            calls["body"] = body
            calls["content_type"] = content_type
            calls["cache_control"] = cache_control
            return "https://media.example.com/" + key

        monkeypatch.setattr(ro.video, "upload_bytes_to_r2", fake_upload_bytes)
        url = ro.upload_file_to_r2(
            {"video_public_r2_public_base_url": "https://media.example.com"},
            "race-videos/live/latest_public.jpg",
            upload_path,
            content_type="image/jpeg",
            cache_control="no-store, no-cache, must-revalidate, max-age=0",
        )

        assert url == "https://media.example.com/race-videos/live/latest_public.jpg"
        assert calls["body"] == b"fake-jpeg"
        assert calls["content_type"] == "image/jpeg"
        assert calls["cache_control"] == "no-store, no-cache, must-revalidate, max-age=0"

# ---------------------------------------------------------------------------
# Public branding for live camera and public videos
# ---------------------------------------------------------------------------

class TestPublicBrandingSettings:
    # Every test here takes `client` even when it never issues a request: that
    # fixture is what points the database at a temp file. Without it,
    # save_app_settings() rewrites the developer's real data/race_officer.db and
    # wipes the settings it was not given (v0.183 — it cleared the live-stream URL).
    def test_settings_page_contains_public_branding_controls(self, logged_in_client):
        resp = logged_in_client.get("/admin/settings")
        assert resp.status_code == 200
        assert b"Public branding" in resp.data
        assert b"public_branding_enabled" in resp.data
        assert b"Upload sponsor logo" in resp.data

    def test_public_home_uses_branded_live_frame_endpoint(self, client):
        resp = client.get("/public/current")
        assert resp.status_code == 200
        assert b"/public/video/live_frame.jpg" in resp.data
        assert b"public-live-club-logo" not in resp.data

    def test_public_home_can_use_r2_live_image_url(self, client, monkeypatch):
        monkeypatch.setattr(ro.video, "start_public_live_r2_uploader", lambda: None)
        ro.save_app_settings({
            "video_public_live_provider": "r2",
            "video_public_live_interval_seconds": "7",
            "video_public_r2_account_id": "0123456789abcdef0123456789abcdef",
            "video_public_r2_bucket": "race-media",
            "video_public_r2_access_key_id": "key-id",
            "video_public_r2_secret_access_key": "secret",
            "video_public_r2_public_base_url": "https://media.example.com",
            "video_public_r2_prefix": "race-videos",
        })
        resp = client.get("/public/current")
        assert resp.status_code == 200
        assert b"https://media.example.com/race-videos/live/latest_public.jpg" in resp.data
        assert b'data-provider="r2"' in resp.data
        # v0.182: the refresh interval moved onto the live-camera container, which
        # owns the snapshot loop (and the swap to the relay's live stream).
        assert b'data-snapshot-refresh-ms="7000"' in resp.data

    def test_upload_public_live_frame_once_uses_stable_r2_key(self, tmp_path, monkeypatch):
        frame = tmp_path / "latest_public.jpg"
        frame.write_bytes(b"fake-jpeg")
        monkeypatch.setattr(ro.appstate, "PUBLIC_LIVE_R2_STATUS_PATH", tmp_path / "r2_live_status.json")
        monkeypatch.setattr(ro.video, "public_live_frame_path", lambda: frame)
        calls = {}

        def fake_upload(cfg, key, path, content_type="application/octet-stream", cache_control=""):
            calls["key"] = key
            calls["path"] = path
            calls["content_type"] = content_type
            calls["cache_control"] = cache_control
            return "https://media.example.com/" + key

        monkeypatch.setattr(ro.video, "upload_file_to_r2", fake_upload)
        cfg = {
            "video_public_live_provider": "r2",
            "video_public_r2_account_id": "0123456789abcdef0123456789abcdef",
            "video_public_r2_bucket": "race-media",
            "video_public_r2_access_key_id": "key-id",
            "video_public_r2_secret_access_key": "secret",
            "video_public_r2_public_base_url": "https://media.example.com",
            "video_public_r2_prefix": "race-videos",
        }
        status = ro.upload_public_live_frame_once(cfg)
        assert status["ok"] is True
        assert calls["key"] == "race-videos/live/latest_public.jpg"
        assert calls["path"] == frame
        assert calls["content_type"] == "image/jpeg"
        assert "no-store" in calls["cache_control"]
        assert status["public_url"].endswith("race-videos/live/latest_public.jpg")

    def test_upload_and_serve_sponsor_logo(self, logged_in_client, tmp_path, monkeypatch):
        monkeypatch.setattr(ro.appstate, "BRANDING_DIR", tmp_path / "branding")
        monkeypatch.setattr(ro.appstate, "BRANDING_MANIFEST_PATH", tmp_path / "branding" / "sponsor_logos.json")
        resp = _post_csrf(
            logged_in_client,
            "/admin/settings/branding/sponsor",
            {
                "sponsor_label": "Mark A Sponsor",
                "sponsor_logo_upload": (BytesIO(b"fake-png"), "mark-a.png"),
            },
            content_type="multipart/form-data",
            follow_redirects=False,
        )
        assert resp.status_code in (302, 303)
        manifest = ro.read_branding_manifest()
        assert len(manifest["sponsors"]) == 1
        filename = manifest["sponsors"][0]["filename"]
        assert filename.endswith(".png")
        assert (tmp_path / "branding" / filename).exists()
        public = logged_in_client.get(f"/public/branding/{filename}")
        assert public.status_code == 200
        assert public.mimetype == "image/png"

    def test_public_video_transcode_command_rotates_sponsor_logos_top_right(self, client, tmp_path, monkeypatch):
        brand_dir = tmp_path / "branding"
        brand_dir.mkdir()
        club = brand_dir / "club_logo.png"
        sponsor_a = brand_dir / "sponsor_a.png"
        sponsor_b = brand_dir / "sponsor_b.png"
        club.write_bytes(b"club")
        sponsor_a.write_bytes(b"sponsor-a")
        sponsor_b.write_bytes(b"sponsor-b")
        monkeypatch.setattr(ro.appstate, "BRANDING_DIR", brand_dir)
        monkeypatch.setattr(ro.appstate, "BRANDING_MANIFEST_PATH", brand_dir / "sponsor_logos.json")
        ro.write_branding_manifest({
            "club_logo": club.name,
            "sponsors": [
                {"id": "a", "label": "Sponsor A", "filename": sponsor_a.name},
                {"id": "b", "label": "Sponsor B", "filename": sponsor_b.name},
            ],
        })
        ro.save_app_settings({
            "public_branding_enabled": "1",
            "public_branding_club_logo_enabled": "1",
            "video_public_quality": "720p",
        })
        cmd = ro.build_public_video_transcode_command("ffmpeg", tmp_path / "in.mp4", tmp_path / "out.mp4", ro.video_config())
        text = " ".join(str(x) for x in cmd)
        assert str(club) in cmd
        assert str(sponsor_a) in cmd
        assert str(sponsor_b) in cmd
        assert "-filter_complex" in cmd
        assert "scale=282:108:force_original_aspect_ratio=decrease" in text
        assert "overlay=16:16" in text
        assert "main_w-overlay_w-16:16" in text
        assert r"enable='eq(mod(floor(t/5)\,2)\,0)'" in text
        assert r"enable='eq(mod(floor(t/5)\,2)\,1)'" in text
        assert "shortest=1" in text
        assert "eof_action=endall" in text
        assert "-map [v3]" in text

    def test_public_live_branding_filter_selects_one_sponsor_for_rotation_slot(self, client, tmp_path, monkeypatch):
        brand_dir = tmp_path / "branding"
        brand_dir.mkdir()
        club = brand_dir / "club_logo.png"
        sponsor_a = brand_dir / "sponsor_a.png"
        sponsor_b = brand_dir / "sponsor_b.png"
        club.write_bytes(b"club")
        sponsor_a.write_bytes(b"sponsor-a")
        sponsor_b.write_bytes(b"sponsor-b")
        monkeypatch.setattr(ro.appstate, "BRANDING_DIR", brand_dir)
        monkeypatch.setattr(ro.appstate, "BRANDING_MANIFEST_PATH", brand_dir / "sponsor_logos.json")
        ro.write_branding_manifest({
            "club_logo": club.name,
            "sponsors": [
                {"id": "a", "label": "Sponsor A", "filename": sponsor_a.name},
                {"id": "b", "label": "Sponsor B", "filename": sponsor_b.name},
            ],
        })
        ro.save_app_settings({"public_branding_enabled": "1", "public_branding_club_logo_enabled": "1"})
        inputs, filter_complex, output_label = ro.build_public_live_branding_overlay_filter(1280, 720, sponsor_count=8, rotation_index=1)
        assert str(club) in inputs
        assert str(sponsor_a) not in inputs
        assert str(sponsor_b) in inputs
        assert "scale=282:108:force_original_aspect_ratio=decrease" in filter_complex
        assert "overlay=16:16" in filter_complex
        assert "main_w-overlay_w-16:16" in filter_complex
        assert "enable=" not in filter_complex
        assert "eof_action=endall" in filter_complex
        assert output_label == "v2"

    def test_public_live_branding_cache_key_changes_with_rotation_slot(self, tmp_path):
        frame = tmp_path / "frame.jpg"
        logo = tmp_path / "logo.png"
        frame.write_bytes(b"frame")
        logo.write_bytes(b"logo")
        cfg = {"public_branding_enabled": "1", "public_branding_club_logo_enabled": "1", "video_preview_jpeg_quality": "3"}
        first = ro.public_live_branding_cache_key(frame, cfg, [str(logo)], rotation_index=0)
        second = ro.public_live_branding_cache_key(frame, cfg, [str(logo)], rotation_index=1)
        assert first != second

    def test_disabling_public_branding_uses_plain_scale_filter(self, client, tmp_path):
        ro.save_app_settings({"public_branding_enabled": "0", "video_public_quality": "720p"})
        cmd = ro.build_public_video_transcode_command("ffmpeg", tmp_path / "in.mp4", tmp_path / "out.mp4", ro.video_config())
        assert "-filter_complex" not in cmd
        assert "-vf" in cmd
        assert "scale=-2:720:force_original_aspect_ratio=decrease" in cmd

# ---------------------------------------------------------------------------
# Backup / restore
# ---------------------------------------------------------------------------

class TestBackupRestoreRoutes:
    def test_sqlite_backup_to_file_closes_both_connections(self, tmp_path, monkeypatch):
        events = []

        class FakeConnection:
            def __init__(self, name):
                self.name = str(name)

            def backup(self, destination):
                events.append(("backup", self.name, destination.name))

            def commit(self):
                events.append(("commit", self.name))

            def close(self):
                events.append(("close", self.name))

        from core import backup as _backup
        # sqlite_backup_to_file now lives in core.backup and calls that module's
        # imported init_db, so patch it there.
        monkeypatch.setattr(_backup, "init_db", lambda: events.append(("init",)))
        monkeypatch.setattr(ro.appstate, "DB_PATH", tmp_path / "live.db")
        # It skips a source that isn't there (a hut that never used tracking has
        # no track database), so the fake live file has to exist.
        (tmp_path / "live.db").touch()
        monkeypatch.setattr(ro.sqlite3, "connect", lambda path: FakeConnection(path))

        ro.sqlite_backup_to_file(tmp_path / "backup" / "race_officer.db")

        assert ("init",) in events
        assert any(event[0] == "backup" for event in events)
        assert events[-2:] == [
            ("close", str(tmp_path / "backup" / "race_officer.db")),
            ("close", str(tmp_path / "live.db")),
        ]

    def test_backup_restore_page_loads_and_warns_about_video_size(self, logged_in_client):
        resp = logged_in_client.get("/admin/backup")
        assert resp.status_code == 200
        assert b"Backup / restore" in resp.data
        assert b"Videos" in resp.data
        assert b"very large" in resp.data.lower() or b"large" in resp.data.lower()

    def test_backup_section_defaults_keep_videos_opt_in(self):
        sections = {section["id"]: section for section in ro.backup_sections_for_template()}

        assert sections["database"]["default_backup"] is True
        assert sections["marks_courses"]["default_backup"] is True
        assert sections["polars_sailcharts"]["default_backup"] is True
        assert sections["branding"]["default_backup"] is True
        assert sections["videos"]["default_backup"] is False
        assert sections["videos"]["default_restore"] is False
        assert "large" in sections["videos"].get("warning", "").lower()

    def test_backup_download_contains_selected_sections(self, logged_in_client):
        resp = _post_csrf(
            logged_in_client,
            "/admin/backup/download",
            {"sections": ["database", "marks_courses", "polars_sailcharts", "branding"]},
        )
        assert resp.status_code == 200
        assert resp.mimetype == "application/zip"
        with ro.zipfile.ZipFile(BytesIO(resp.data), "r") as zf:
            names = set(zf.namelist())
            assert ro.BACKUP_MANIFEST_NAME in names
            assert "data/race_officer.db" in names
            assert "data/marks.json" in names
            assert "data/courses.json" in names
            assert "data/start_finish.json" in names
            assert not any(name.startswith("data/video_clips/") for name in names)
            manifest = ro.json.loads(zf.read(ro.BACKUP_MANIFEST_NAME).decode("utf-8"))
            assert manifest["app"] == "Pwllheli Race Officer"
            assert manifest["app_version"] == ro.APP_VERSION
            assert set(manifest["sections"]) == {"database", "marks_courses", "polars_sailcharts", "branding"}
            assert "created_at" in manifest
            assert manifest["files"]["database"] == 1
            assert "videos" not in manifest["sections"]

    def test_restore_selected_marks_courses_replaces_json_files(self, logged_in_client, tmp_path, monkeypatch):
        data_dir = tmp_path / "data"
        runtime_dir = tmp_path / "runtime"
        data_dir.mkdir(exist_ok=True)
        runtime_dir.mkdir(exist_ok=True)        # conftest already redirects RUNTIME_DIR here
        monkeypatch.setattr(ro.appstate, "DATA_DIR", data_dir)
        monkeypatch.setattr(ro.appstate, "RUNTIME_DIR", runtime_dir)
        monkeypatch.setattr(ro.appstate, "POLARS_DIR", data_dir / "polars")
        monkeypatch.setattr(ro.appstate, "SAIL_CHARTS_DIR", data_dir / "sailcharts")
        monkeypatch.setattr(ro.appstate, "BRANDING_DIR", data_dir / "branding")
        monkeypatch.setattr(ro.appstate, "VIDEO_CLIPS_DIR", data_dir / "video_clips")
        monkeypatch.setattr(ro.appstate, "LEGACY_VIDEO_CLIPS_DIR", data_dir / "video" / "clips")
        monkeypatch.setattr(ro.appstate, "BRANDING_MANIFEST_PATH", data_dir / "branding" / "sponsor_logos.json")
        (data_dir / "marks.json").write_text('{"marks":{"OLD":{"lat":0,"lon":0}}}', encoding="utf-8")
        (data_dir / "courses.json").write_text('{"courses":[{"course_no":1,"name":"Old","marks":[]}]}', encoding="utf-8")
        (data_dir / "start_finish.json").write_text('{"old":true}', encoding="utf-8")

        backup_bytes = BytesIO()
        with ro.zipfile.ZipFile(backup_bytes, "w") as zf:
            zf.writestr("data/marks.json", '{"marks":{"NEW":{"lat":1,"lon":2}}}')
            zf.writestr("data/courses.json", '{"courses":[{"course_no":42,"name":"Restored","marks":[]}]}')
            zf.writestr("data/start_finish.json", '{"restored":true}')
        backup_bytes.seek(0)

        resp = _post_csrf(
            logged_in_client,
            "/admin/backup/restore",
            {"sections": ["marks_courses"], "backup_file": (backup_bytes, "backup.zip")},
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert ro.MARKS == {"NEW": {"lat": 1, "lon": 2}}
        assert 42 in ro.COURSE_BY_NO
        assert ro.START_FINISH == {"restored": True}
        assert ro.json.loads((data_dir / "marks.json").read_text(encoding="utf-8"))["marks"]["NEW"]["lat"] == 1

    def test_restore_rejects_path_traversal_members(self):
        assert ro.safe_restore_member_name("../../outside.txt") is None
        assert ro.safe_restore_member_name("data/../outside.txt") is None
        assert ro.safe_restore_member_name("/data/marks.json") == "data/marks.json"


# ---------------------------------------------------------------------------
# Public video links: R2 vs hut-PC (competitor race page)
# ---------------------------------------------------------------------------

class TestPublicVideoClipHref:
    def test_prefers_r2_public_url_when_ready(self):
        clip = {
            "id": 7,
            "public_status": "ready",
            "public_url": "https://assets.pwllhelisailingclub.org/race9/clip7_public.mp4",
        }
        assert ro.public_video_clip_href(clip) == "https://assets.pwllhelisailingclub.org/race9/clip7_public.mp4"

    def test_falls_back_to_hut_when_public_not_ready(self):
        clip = {"id": 7, "public_status": "pending", "public_url": ""}
        with ro.app.test_request_context():
            href = ro.public_video_clip_href(clip)
        assert href == "/public/video/clip/7"

    def test_falls_back_to_hut_when_publishing_off(self):
        # public_status "off" (R2 disabled) with no public_url -> hut route
        clip = {"id": 7, "public_status": "off", "public_url": ""}
        with ro.app.test_request_context():
            href = ro.public_video_clip_href(clip)
        assert href == "/public/video/clip/7"

    def test_ready_status_but_missing_url_uses_hut(self):
        clip = {"id": 7, "public_status": "ready", "public_url": ""}
        with ro.app.test_request_context():
            href = ro.public_video_clip_href(clip)
        assert href == "/public/video/clip/7"


class TestManualHornInputNonBlocking:
    """The manual-horn status poll must not wait on the serial IO lock.

    fire_horn holds HARDWARE_IO_LOCK for the full blast; the console polls the
    input status several times a second.  If the poll blocked on the lock the UI
    would freeze for the horn's duration, so a held lock must return immediately.
    """

    def test_poll_returns_immediately_while_horn_is_firing(self, monkeypatch):
        import core.horn as horn_mod

        monkeypatch.setattr(horn_mod, "hardware_config", lambda: {
            "serial_port": "COM-TEST",
            "horn_input_enabled": True,
            "horn_input_line": "CTS",
            "horn_input_active": True,
        })

        def _should_not_open(*args, **kwargs):  # pragma: no cover - must not run
            raise AssertionError("serial port must not be opened while horn is firing")

        monkeypatch.setattr(horn_mod, "open_serial_for_horn_io", _should_not_open)

        # Simulate a horn blast holding the IO lock, then poll from "another
        # thread" (same thread here, but the non-blocking acquire behaves the
        # same because the lock is already held elsewhere -> we hold it twice and
        # the RLock would normally re-enter; take it on a worker thread instead).
        import threading

        holder_has_lock = threading.Event()
        release_holder = threading.Event()

        def _hold_lock():
            horn_mod.HARDWARE_IO_LOCK.acquire()
            holder_has_lock.set()
            release_holder.wait(2.0)
            horn_mod.HARDWARE_IO_LOCK.release()

        t = threading.Thread(target=_hold_lock)
        t.start()
        try:
            assert holder_has_lock.wait(2.0)
            result = horn_mod.read_manual_horn_input(race_id=None, log_edges=True)
        finally:
            release_holder.set()
            t.join(2.0)

        assert result["ok"] is True
        assert result["enabled"] is True
        assert result["active"] is True
        assert result["horn_active"] is True
        assert result["logged"] is False
        assert "Horn firing" in result["message"]

    def test_poll_reads_hardware_when_lock_is_free(self, monkeypatch):
        import core.horn as horn_mod

        monkeypatch.setattr(horn_mod, "hardware_config", lambda: {
            "serial_port": "COM-TEST",
            "horn_input_enabled": True,
            "horn_input_line": "CTS",
            "horn_input_active": True,
        })

        opened = {"count": 0}

        class _FakeSerial:
            cts = False
            cd = True

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def _open(cfg, timeout=0.05):
            opened["count"] += 1
            return _FakeSerial()

        monkeypatch.setattr(horn_mod, "open_serial_for_horn_io", _open)
        horn_mod.HARDWARE_RUNTIME_STATE["last_input_active"] = None

        result = horn_mod.read_manual_horn_input(race_id=None, log_edges=False)

        assert opened["count"] == 1
        assert result["ok"] is True
        assert result["active"] is False
        # Lock must be released after a normal read so the next poll/horn can run.
        assert horn_mod.HARDWARE_IO_LOCK.acquire(blocking=False) is True
        horn_mod.HARDWARE_IO_LOCK.release()
