"""Tests for shortening a race course (core.courses helpers + the routes)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import app as ro  # noqa: E402
from core import courses  # noqa: E402
from core import raceadmin  # noqa: E402

COURSE = {"course_no": 1, "marks": [
    {"mark": "1", "rounding": "port"},
    {"mark": "8", "rounding": "port"},
    {"mark": "O", "rounding": "starboard"},
    {"mark": "O", "rounding": "port"},
]}


def test_shorten_options_number_repeats():
    opts = courses.course_shorten_options(COURSE)
    assert [o["index"] for o in opts] == [0, 1, 2, 3]
    assert opts[0]["label"] == "1"          # single occurrence: plain
    assert opts[1]["label"] == "8"
    assert opts[2]["label"] == "O (rounding 1)"   # repeated mark: numbered
    assert opts[3]["label"] == "O (rounding 2)"


def test_apply_shortening_truncates_inclusive():
    s = courses.apply_course_shortening(COURSE, 1)
    assert [m["mark"] for m in s["marks"]] == ["1", "8"]
    assert [m["mark"] for m in COURSE["marks"]] == ["1", "8", "O", "O"]  # original untouched


def test_apply_shortening_invalid_index_returns_unchanged():
    assert courses.apply_course_shortening(COURSE, 99) is COURSE
    assert courses.apply_course_shortening(COURSE, None) is COURSE
    assert courses.apply_course_shortening(COURSE, -1) is COURSE


# --- routes ---------------------------------------------------------------
def _make_race(course_no=1):
    with ro.get_db() as db:
        cur = db.execute(
            "INSERT INTO races (name, class_name, course_no, start_time, rating_rule, notes, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("Shorten Test", "", course_no, "2026-07-25T10:00:00", "DUAL", "", "2026-07-25T09:00:00"),
        )
        db.commit()
        return int(cur.lastrowid)


def _post(client, url, data):
    token = "test-csrf-token"
    with client.session_transaction() as s:
        s["_csrf_token"] = token
    d = dict(data)
    d["_csrf_token"] = token
    return client.post(url, data=d, follow_redirects=False)


def test_shorten_records_state_and_signals(logged_in_client, monkeypatch):
    calls = []
    monkeypatch.setattr(raceadmin, "signal_shortened_course", lambda *a, **k: calls.append(a))
    rid = _make_race()
    resp = _post(logged_in_client, f"/admin/race/{rid}/shorten", {"index": "1"})
    assert resp.status_code in (302, 303)
    race = ro.get_race(rid)
    assert race["shortened_at_index"] == 1
    assert race["shortened_at_mark"]              # display code recorded
    assert race["shortened_at_time"]
    assert calls, "the horn/announcement signal should be fired"
    labels = [e["label"] for e in ro.get_events(rid, limit=10)]
    assert any("Shortened course" in (l or "") for l in labels)


def test_shorten_clear(logged_in_client, monkeypatch):
    monkeypatch.setattr(raceadmin, "signal_shortened_course", lambda *a, **k: None)
    rid = _make_race()
    _post(logged_in_client, f"/admin/race/{rid}/shorten", {"index": "0"})
    assert ro.get_race(rid)["shortened_at_index"] == 0
    resp = _post(logged_in_client, f"/admin/race/{rid}/shorten/clear", {})
    assert resp.status_code in (302, 303)
    assert ro.get_race(rid)["shortened_at_index"] is None


def test_shorten_rejects_out_of_range_index(logged_in_client, monkeypatch):
    monkeypatch.setattr(raceadmin, "signal_shortened_course", lambda *a, **k: None)
    rid = _make_race()
    resp = _post(logged_in_client, f"/admin/race/{rid}/shorten", {"index": "999"})
    assert resp.status_code in (302, 303)
    assert ro.get_race(rid)["shortened_at_index"] is None
