"""Tests for pursuit-race start-time maths and helpers."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import app as ro  # noqa: E402
from core import pursuit  # noqa: E402

NOW = "2026-06-01T08:00:00"


def _make_pursuit_race(db, rating_rule="IRC_TCC", duration_min=60, warning="2026-06-01T10:55:00"):
    cur = db.execute(
        "INSERT INTO races (name, class_name, course_no, start_time, rating_rule, race_type, pursuit_duration_min, notes, created_at)"
        " VALUES (?, '', 1, ?, ?, 'pursuit', ?, '', ?)",
        ("Pursuit Demo", warning, rating_rule, duration_min, NOW),
    )
    return int(cur.lastrowid)


def _add_entry(db, race_id, name, sail, irc=None, ytc=None):
    cur = db.execute(
        "INSERT INTO entries (race_id, boat_name, sail_no, class_name, status, manual_irc_rating, manual_ytc_rating)"
        " VALUES (?, ?, ?, '', 'RACING', ?, ?)",
        (race_id, name, sail, irc, ytc),
    )
    return int(cur.lastrowid)


def _race(db, race_id):
    return db.execute("SELECT * FROM races WHERE id = ?", (race_id,)).fetchone()


def _offset_seconds(db, race_id, entry_id):
    from core.timeutils import parse_dt
    race = _race(db, race_id)
    first_start = ro.race_first_start_dt(race)
    st = parse_dt(db.execute("SELECT start_time_override FROM entries WHERE id = ?", (entry_id,)).fetchone()[0])
    return (st - first_start).total_seconds() if st else None


class TestPursuitStartMaths:
    def test_irc_slowest_starts_first(self, client):
        with ro.get_db() as db:
            rid = _make_pursuit_race(db, "IRC_TCC", duration_min=60)
            slow = _add_entry(db, rid, "Slow", "1", irc=0.900)
            mid = _add_entry(db, rid, "Mid", "2", irc=1.000)
            fast = _add_entry(db, rid, "Fast", "3", irc=1.100)
            res = pursuit.assign_pursuit_start_times(db, _race(db, rid))
            db.commit()
            assert res["assigned"] == 3
            assert res["rating_type"] == "IRC"
            assert _offset_seconds(db, rid, slow) == 0
            # 3600 * (1 - 0.9/1.0) = 360
            assert abs(_offset_seconds(db, rid, mid) - 360) < 1
            # 3600 * (1 - 0.9/1.1) = 654.5 -> stored to whole seconds
            assert abs(_offset_seconds(db, rid, fast) - 654.5) < 1.5

    def test_ytc_higher_number_starts_first(self, client):
        with ro.get_db() as db:
            rid = _make_pursuit_race(db, "YTC", duration_min=60)
            slow = _add_entry(db, rid, "Slow", "1", ytc=1200)
            mid = _add_entry(db, rid, "Mid", "2", ytc=1000)
            fast = _add_entry(db, rid, "Fast", "3", ytc=900)
            res = pursuit.assign_pursuit_start_times(db, _race(db, rid))
            db.commit()
            assert res["rating_type"] == "YTC"
            assert _offset_seconds(db, rid, slow) == 0
            # 3600 * (1 - 1000/1200 relationship): offset = 3600*(1 - YTC_i/YTC_slow) with slowest=largest YTC
            assert abs(_offset_seconds(db, rid, mid) - 600) < 1   # 3600*(1-1000/1200)
            assert abs(_offset_seconds(db, rid, fast) - 900) < 1  # 3600*(1-900/1200)

    def test_missing_rating_excluded_with_warning(self, client):
        with ro.get_db() as db:
            rid = _make_pursuit_race(db, "IRC_TCC", duration_min=60)
            _add_entry(db, rid, "Rated", "1", irc=1.000)
            no_rating = _add_entry(db, rid, "Kestrel", "2", irc=None)
            res = pursuit.assign_pursuit_start_times(db, _race(db, rid))
            db.commit()
            assert res["assigned"] == 1
            assert "Kestrel" in res["warnings"]
            assert _offset_seconds(db, rid, no_rating) is None

    def test_finish_time_is_first_start_plus_duration(self, client):
        with ro.get_db() as db:
            rid = _make_pursuit_race(db, "IRC_TCC", duration_min=90, warning="2026-06-01T10:55:00")
            _add_entry(db, rid, "A", "1", irc=1.000)
            res = pursuit.assign_pursuit_start_times(db, _race(db, rid))
            db.commit()
            # first start = warning + 5 = 11:00; + 90 min = 12:30
            assert res["first_start"] == "2026-06-01T11:00:00"
            assert res["finish"] == "2026-06-01T12:30:00"
            assert pursuit.pursuit_finish_dt(_race(db, rid)).isoformat() == "2026-06-01T12:30:00"

    def test_start_rows_sorted_and_signal_times_distinct(self, client):
        with ro.get_db() as db:
            rid = _make_pursuit_race(db, "IRC_TCC", duration_min=60)
            _add_entry(db, rid, "Slow", "1", irc=1.000)
            _add_entry(db, rid, "Slow2", "2", irc=1.000)  # same rating -> same start
            _add_entry(db, rid, "Fast", "3", irc=1.200)
            pursuit.assign_pursuit_start_times(db, _race(db, rid))
            db.commit()
            entries = db.execute("SELECT * FROM entries WHERE race_id = ? ORDER BY id", (rid,)).fetchall()
            rows = pursuit.pursuit_start_rows(_race(db, rid), entries)
            assert [r["entry"]["boat_name"] for r in rows][:2] == ["Slow", "Slow2"]  # earliest first
            times = pursuit.pursuit_start_signal_times(_race(db, rid), entries)
            assert len(times) == 2  # two boats share the first start time


def _post_csrf(client, url, data=None, **kwargs):
    token = "test-csrf-token"
    with client.session_transaction() as sess:
        sess["_csrf_token"] = token
    form = dict(data or {})
    form["_csrf_token"] = token
    return client.post(url, data=form, **kwargs)


class TestPursuitRoutes:
    def test_new_race_creates_pursuit(self, logged_in_client):
        resp = _post_csrf(logged_in_client, "/admin/race/new", {
            "name": "P-Route", "race_type": "pursuit", "pursuit_rating": "YTC", "pursuit_duration_min": "75",
        }, follow_redirects=False)
        assert resp.status_code in (302, 303)
        with ro.get_db() as db:
            r = db.execute("SELECT * FROM races WHERE name = 'P-Route' ORDER BY id DESC").fetchone()
        assert r["race_type"] == "pursuit"
        assert r["rating_rule"] == "YTC"
        assert r["pursuit_duration_min"] == 75

    def test_public_pursuit_page_shows_start_times(self, client):
        with ro.get_db() as db:
            rid = _make_pursuit_race(db, "IRC_TCC", 60)
            _add_entry(db, rid, "Slow", "1", irc=0.900)
            _add_entry(db, rid, "Fast", "2", irc=1.100)
            pursuit.assign_pursuit_start_times(db, _race(db, rid))
            db.commit()
        body = client.get(f"/public/race/{rid}").get_data(as_text=True)
        assert "publicPursuitPanel" in body
        assert "Start times" in body

    def test_public_pursuit_page_has_course_analysis(self, client):
        with ro.get_db() as db:
            rid = _make_pursuit_race(db, "IRC_TCC", 60)
            _add_entry(db, rid, "A", "1", irc=1.000)
            pursuit.assign_pursuit_start_times(db, _race(db, rid))
            db.commit()
        body = client.get(f"/public/race/{rid}").get_data(as_text=True)
        assert "publicCourseAnalysisPanel" in body  # course analysis shown for pursuit too
        assert "publicPursuitPanel" in body

    def test_save_positions_sets_ranks(self, logged_in_client):
        with ro.get_db() as db:
            rid = _make_pursuit_race(db, "IRC_TCC", 60)
            e1 = _add_entry(db, rid, "A", "1", irc=1.000)
            e2 = _add_entry(db, rid, "B", "2", irc=1.100)
            pursuit.assign_pursuit_start_times(db, _race(db, rid))
            db.commit()
        _post_csrf(logged_in_client, f"/admin/race/{rid}/pursuit/positions",
                   {f"position_{e1}": "2", f"position_{e2}": "1"})
        with ro.get_db() as db:
            rows = {int(r["id"]): r for r in db.execute("SELECT * FROM entries WHERE race_id = ?", (rid,)).fetchall()}
        assert rows[e1]["pursuit_position"] == 2 and rows[e1]["status"] == "FINISHED"
        assert rows[e2]["pursuit_position"] == 1 and rows[e2]["status"] == "FINISHED"


class TestPursuitSeriesScoring:
    def test_series_uses_finishing_positions(self, client):
        now = "2026-06-01T08:00:00"
        with ro.get_db() as db:
            sid = int(db.execute(
                "INSERT INTO race_series (name, description, discard_profile, min_races_to_constitute, created_at, updated_at)"
                " VALUES ('Pursuit Series', '', ?, 1, ?, ?)",
                (ro.DEFAULT_DISCARD_PROFILE, now, now),
            ).lastrowid)
            rid = int(db.execute(
                "INSERT INTO races (name, class_name, series_id, course_no, start_time, rating_rule, race_type, pursuit_duration_min, notes, created_at)"
                " VALUES ('PR', '', ?, 1, ?, 'IRC_TCC', 'pursuit', 60, '', ?)",
                (sid, "2026-06-01T10:55:00", now),
            ).lastrowid)
            a = _add_entry(db, rid, "Alpha", "1", irc=1.000)
            b = _add_entry(db, rid, "Beta", "2", irc=1.100)
            db.execute("UPDATE entries SET pursuit_position = 1, status = 'FINISHED', finish_time = ? WHERE id = ?", ("2026-06-01T12:00:00", b))
            db.execute("UPDATE entries SET pursuit_position = 2, status = 'FINISHED', finish_time = ? WHERE id = ?", ("2026-06-01T12:00:00", a))
            db.commit()
        table = ro.build_series_result_table(sid, "IRC")
        rows_by_name = {r["competitor"]["boat_name"]: r for r in table["rows"]}
        assert rows_by_name["Beta"]["scores"][0]["code"] == "1"
        assert rows_by_name["Alpha"]["scores"][0]["code"] == "2"


class TestPursuitFlagsAndRatings:
    def test_flags_default_to_class_1(self, client):
        from core.classconfig import signal_panel_schedule
        with ro.get_db() as db:
            rid = _make_pursuit_race(db, "IRC_TCC", 60)
            db.commit()
            race = _race(db, rid)
        schedule = signal_panel_schedule(race)
        assert len(schedule) == 1
        assert any(f.get("numeral") == "1" for f in schedule[0]["flags"])

    def test_display_rows_show_offsets_before_timing(self, client):
        with ro.get_db() as db:
            # No warning time set, but a period is → offsets should be available.
            cur = db.execute(
                "INSERT INTO races (name, class_name, course_no, start_time, rating_rule, race_type, pursuit_duration_min, notes, created_at)"
                " VALUES ('P', '', 1, '', 'IRC_TCC', 'pursuit', 60, '', ?)",
                (NOW,),
            )
            rid = int(cur.lastrowid)
            _add_entry(db, rid, "Slow", "1", irc=0.900)
            _add_entry(db, rid, "Fast", "2", irc=1.100)
            db.commit()
            race = _race(db, rid)
            entries = db.execute("SELECT * FROM entries WHERE race_id = ? ORDER BY id", (rid,)).fetchall()
        rows = pursuit.pursuit_display_rows(race, entries, {})
        texts = {r["entry"]["boat_name"]: r["offset_text"] for r in rows}
        assert texts["Slow"] == "first start"
        assert texts["Fast"].startswith("+")
        assert rows[0]["entry"]["boat_name"] == "Slow"  # sorted by offset

    def test_missing_rating_ids_only_flags_unrated(self, client):
        with ro.get_db() as db:
            rid = _make_pursuit_race(db, "IRC_TCC", 60)
            rated = _add_entry(db, rid, "Rated", "1", irc=1.000)
            unrated = _add_entry(db, rid, "Unrated", "2", irc=None)
            db.commit()
            entries = db.execute("SELECT * FROM entries WHERE race_id = ? ORDER BY id", (rid,)).fetchall()
        missing = pursuit.pursuit_missing_rating_ids(entries, "IRC", {})
        assert unrated in missing and rated not in missing


class TestPursuitStartVideos:
    def test_start_video_maps_to_boat_and_publish_column(self, client):
        with ro.get_db() as db:
            rid = _make_pursuit_race(db, "IRC_TCC", 60)
            e1 = _add_entry(db, rid, "Slow", "1", irc=1.000)
            pursuit.assign_pursuit_start_times(db, _race(db, rid))
            db.commit()
            start_iso = db.execute("SELECT start_time_override FROM entries WHERE id = ?", (e1,)).fetchone()[0]
            db.execute(
                "INSERT INTO video_clips (race_id, clip_type, event_time, status, pre_seconds, post_seconds, created_at, updated_at)"
                " VALUES (?, 'start', ?, 'ready', 60, 60, ?, ?)",
                (rid, start_iso, NOW, NOW),
            )
            db.commit()
            entries = db.execute("SELECT * FROM entries WHERE race_id = ?", (rid,)).fetchall()
            clips = ro.get_video_clips_for_race(rid)
        mapping = ro.pursuit_start_clip_by_entry(entries, clips)
        assert e1 in mapping
        links = ro.published_video_links_for_race(rid)
        assert str(e1) in links["finish_by_entry"]
        assert links["video_column_label"] == "Start video"
        assert links["start_links"] == []  # not needed above the table for pursuit


class TestPursuitAnnouncements:
    def test_next_start_announcements_group_and_time(self, client):
        with ro.get_db() as db:
            rid = _make_pursuit_race(db, "IRC_TCC", 60)
            a = _add_entry(db, rid, "Alpha", "1", irc=1.000)
            bb = _add_entry(db, rid, "Bravo", "2", irc=1.000)
            c = _add_entry(db, rid, "Charlie", "3", irc=1.000)
            db.execute("UPDATE entries SET start_time_override = ? WHERE id = ?", ("2026-06-01T10:26:00", a))
            db.execute("UPDATE entries SET start_time_override = ? WHERE id = ?", ("2026-06-01T10:28:55", bb))
            db.execute("UPDATE entries SET start_time_override = ? WHERE id = ?", ("2026-06-01T10:29:00", c))
            db.commit()
            race = _race(db, rid)
            entries = db.execute("SELECT * FROM entries WHERE race_id = ?", (rid,)).fetchall()
        anns = pursuit.pursuit_next_start_announcements(race, entries)
        # Alpha alone; Bravo+Charlie start within 20s so are one announcement,
        # spoken 10s after Alpha started (10:26:10).
        assert len(anns) == 1
        assert "Bravo" in anns[0]["text"] and "Charlie" in anns[0]["text"]
        assert anns[0]["dt"].isoformat() == "2026-06-01T10:26:10"


class TestPursuitRaceDetection:
    def test_is_pursuit_and_rating_type(self, client):
        with ro.get_db() as db:
            rid = _make_pursuit_race(db, "YTC", duration_min=45)
            race = _race(db, rid)
            assert pursuit.is_pursuit_race(race) is True
            assert pursuit.pursuit_rating_type(race) == "YTC"
            assert pursuit.pursuit_duration_seconds(race) == 45 * 60
