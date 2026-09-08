"""Reading the activity log from the browser.

The log was always there on the race-office PC, which is no help to somebody asking
"who changed that, and what was it before?" from the other side of the club — and it is
the one record that answers it.

Read-only by design: there is no route that can edit or clear it, which is what makes an
audit trail worth keeping. Administrators only, because it names users and what they did.
The day comes from a query string, so the filename is built from a validated date rather
than joined onto a path.
"""
from __future__ import annotations

import logging

import pytest

import app as ro
from core import activitylog


TOKEN = "test-csrf-token"


@pytest.fixture()
def log_files(client, tmp_path, monkeypatch):
    """A logs directory with today plus two rolled-over days."""
    monkeypatch.setattr(ro.appstate, "RUNTIME_DIR", tmp_path)
    logger = logging.getLogger("pwllheli.activity")
    for handler in list(logger.handlers):
        handler.close()
        logger.removeHandler(handler)
    monkeypatch.setattr(activitylog, "_LOGGER", None)
    directory = tmp_path / "logs"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "activity.log").write_text(
        "2026-08-05 10:00:00 | user=admin | login | role=admin\n"
        "2026-08-05 10:01:00 | user=admin | settings saved | serial_port: COM3 -> COM7\n",
        encoding="utf-8")
    (directory / "activity.log.2026-08-04").write_text(
        "2026-08-04 09:00:00 | user=ro | login | role=race_officer\n", encoding="utf-8")
    (directory / "activity.log.2026-08-03").write_text(
        "2026-08-03 08:00:00 | user=admin | race created | #7 'Club Race'\n", encoding="utf-8")
    yield directory
    monkeypatch.setattr(activitylog, "_LOGGER", None)


class TestListingTheDays:
    def test_today_comes_first_then_newest_back(self, log_files):
        days = activitylog.log_days()
        assert [d["label"] for d in days] == ["Today", "2026-08-04", "2026-08-03"]
        assert days[0]["today"] is True

    def test_each_day_carries_its_size(self, log_files):
        assert all(d["bytes"] > 0 for d in activitylog.log_days())

    def test_a_missing_directory_is_not_an_error(self, client, tmp_path, monkeypatch):
        monkeypatch.setattr(ro.appstate, "RUNTIME_DIR", tmp_path / "nothing-here")
        assert activitylog.log_days() == []


class TestReadingADay:
    def test_today_is_the_default(self, log_files):
        out = activitylog.read_log()
        assert out["total"] == 2
        assert out["missing"] is False

    def test_newest_line_first(self, log_files):
        """The question is almost always about the last thing that happened."""
        assert "settings saved" in activitylog.read_log()["lines"][0]

    def test_a_rolled_over_day_reads(self, log_files):
        out = activitylog.read_log("2026-08-04")
        assert out["total"] == 1 and "user=ro" in out["lines"][0]

    def test_a_day_with_no_file_says_so_rather_than_raising(self, log_files):
        out = activitylog.read_log("2020-01-01")
        assert out["missing"] is True and out["lines"] == []

    def test_a_long_day_is_capped_and_says_it_was(self, log_files):
        (log_files / "activity.log").write_text(
            "".join(f"2026-08-05 10:00:00 | user=admin | line {i}\n"
                    for i in range(activitylog.MAX_LOG_LINES + 50)), encoding="utf-8")
        out = activitylog.read_log()
        assert out["truncated"] is True
        assert len(out["lines"]) == activitylog.MAX_LOG_LINES
        assert out["total"] == activitylog.MAX_LOG_LINES + 50

    def test_blank_lines_are_dropped(self, log_files):
        (log_files / "activity.log").write_text("a | b\n\n\nc | d\n", encoding="utf-8")
        assert activitylog.read_log()["total"] == 2


class TestTheDayParameterCannotEscapeTheDirectory:
    """The day arrives from a query string, so it is validated as a date and the
    filename built here — nothing joins a caller's string onto a path."""

    @pytest.mark.parametrize("nasty", [
        "../../../etc/passwd", "..", "../activity.log", "2026-08-04/../../secret",
        "2026-08-04\x00", "%2e%2e%2f", "C:\\Windows\\win.ini", "not-a-date", "",
    ])
    def test_anything_that_is_not_a_date_falls_back_to_today(self, log_files, nasty):
        out = activitylog.read_log(nasty)
        assert out["day"] == ""
        assert out["total"] == 2, "should have read today, not something else"

    def test_a_valid_looking_date_that_has_no_file_is_simply_missing(self, log_files):
        assert activitylog.read_log("1999-12-31")["missing"] is True


class TestThePage:
    def test_an_admin_can_read_it(self, log_files, logged_in_client):
        html = logged_in_client.get("/admin/settings/activity-log").get_data(as_text=True)
        assert "settings saved" in html
        assert "serial_port: COM3 -&gt; COM7" in html, "lines must be escaped, not raw"

    def test_it_offers_the_other_days(self, log_files, logged_in_client):
        html = logged_in_client.get("/admin/settings/activity-log").get_data(as_text=True)
        assert "2026-08-04" in html and "2026-08-03" in html

    def test_a_chosen_day_is_shown(self, log_files, logged_in_client):
        html = logged_in_client.get("/admin/settings/activity-log?day=2026-08-03").get_data(as_text=True)
        assert "race created" in html
        assert "settings saved" not in html

    def test_settings_links_to_it(self, log_files, logged_in_client):
        html = logged_in_client.get("/admin/settings").get_data(as_text=True)
        assert "/admin/settings/activity-log" in html

    def test_a_race_officer_is_refused(self, client, log_files):
        """It names who did what, so it follows the same rule as the rest of
        Settings rather than being readable by everyone who can run a race."""
        from datetime import datetime
        now = datetime.now().isoformat(timespec="seconds")
        with ro.app.app_context():
            with ro.get_db() as db:
                db.execute(
                    "INSERT INTO users (username, password_hash, display_name, role,"
                    " status, can_set_marks, created_at, updated_at)"
                    " VALUES ('ro', ?, 'RO', 'race_officer', 'ACTIVE', 0, ?, ?)",
                    (ro.generate_password_hash("ropass123"), now, now))
                db.commit()
        with client.session_transaction() as sess:
            sess["_csrf_token"] = TOKEN
        client.post("/login", data={"_csrf_token": TOKEN, "username": "ro",
                                    "password": "ropass123"})
        resp = client.get("/admin/settings/activity-log")
        assert resp.status_code == 302
        assert "settings" in resp.headers.get("Location", "")

    def test_it_needs_a_login_at_all(self, client, log_files):
        resp = client.get("/admin/settings/activity-log")
        assert resp.status_code == 302
        assert "login" in resp.headers.get("Location", "")

    def test_there_is_no_way_to_change_it(self, client):
        """An audit trail that the app can rewrite is not one. Nothing may POST."""
        rules = [r for r in ro.app.url_map.iter_rules() if "activity" in r.rule]
        assert rules, "the page should exist"
        for rule in rules:
            assert rule.methods <= {"GET", "HEAD", "OPTIONS"}, f"{rule} accepts writes"


class TestTheCurrentRaceLink:
    """The race officer's most-visited page had no link: they went Races, then found
    today's race in the list, every time."""

    def test_the_nav_offers_it_between_dashboard_and_new_race(self, logged_in_client):
        html = logged_in_client.get("/admin").get_data(as_text=True)
        nav = html.split('class="side-nav"')[1].split("</nav>")[0]
        assert "/admin/race/current" in nav
        assert nav.index("Dashboard") < nav.index("Current race") < nav.index("New race")

    def test_it_lands_on_a_race_sheet(self, logged_in_client):
        from datetime import datetime, timedelta
        now = datetime.now()
        with ro.app.app_context():
            with ro.get_db() as db:
                race_id = int(db.execute(
                    "INSERT INTO races (name, course_no, start_time, rating_rule, created_at)"
                    " VALUES ('Today', 1, ?, 'DUAL', ?)",
                    ((now - timedelta(minutes=20)).isoformat(timespec="seconds"),
                     now.isoformat(timespec="seconds"))).lastrowid)
                db.commit()
        resp = logged_in_client.get("/admin/race/current")
        assert resp.status_code == 302
        assert "/race/" in resp.headers["Location"]

    def test_with_no_races_it_says_so_instead_of_erroring(self, logged_in_client):
        with ro.app.app_context():
            with ro.get_db() as db:
                db.execute("DELETE FROM entries")
                db.execute("DELETE FROM races")
                db.commit()
        resp = logged_in_client.get("/admin/race/current")
        assert resp.status_code == 302
        assert "race/new" in resp.headers["Location"]


class TestTheClubhouseDisplay:
    def test_the_board_has_a_speed_column(self, client):
        """Asked for on the clubhouse display: the order tells you who is ahead, the
        speed tells you whether that is about to change."""
        from datetime import datetime, timedelta
        now = datetime.now()
        with ro.app.app_context():
            with ro.get_db() as db:
                db.execute(
                    "INSERT INTO races (name, course_no, start_time, rating_rule, created_at)"
                    " VALUES ('On the telly', 1, ?, 'DUAL', ?)",
                    ((now - timedelta(minutes=20)).isoformat(timespec="seconds"),
                     now.isoformat(timespec="seconds")))
                db.commit()
        html = client.get("/bar").get_data(as_text=True)
        head = html.split("<thead>")[1].split("</thead>")[0] if "<thead>" in html else ""
        assert "SOG" in head, "no speed column on the board"
        # The last column's heading is rewritten per board mode by bar_display.js
        # (thead th:last-child), so SOG must not be last or it would be overwritten.
        assert head.rindex("SOG") < head.rindex("To go")

    def test_the_rounding_directions_are_the_solid_port_starboard_colours(self):
        """Pale tints read as plain grey from across a room, which is the only place
        this page is ever looked at."""
        css = (ro.appstate.BASE_DIR / "static" / "style.css").read_text(encoding="utf-8")
        block = css.split(".bar-sequence .mark.s")[1].split("}")[0]
        assert "var(--starboard)" in block
        port = css.split(".bar-sequence .mark.p")[1].split("}")[0]
        assert "var(--port)" in port
        for stale in ("#dcfce7", "#fee2e2"):
            assert stale not in css.split(".bar-sequence")[1][:400], f"{stale} still used"
