"""Route-level smoke tests: a safety net for the app.py module-split refactor.

The point of these tests is NOT to assert rich behaviour -- the domain logic
(scoring, series, wind retention, course geometry) is covered elsewhere. The
point is to catch the failure mode a mechanical module split actually causes:
broken wiring. An import that moved wrong, a route that failed to register, a
shared-state global that didn't get imported, a template that can't be found --
all of those surface as a 5xx (or a failed registration) on a route that used
to work.

Two layers:
  1. A GET sweep that introspects the live URL map and hits every GET-able
     route with a well-seeded database, asserting the response is < 500.
  2. Happy-path write flows (create race -> add entry -> finish -> delete,
     boat CRUD, series create + publish) so the core write handlers -- which
     had almost no HTTP-level coverage -- are at least exercised end to end.

If any of these break during the refactor, the wiring is wrong.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import app as ro


def _post_csrf(client, url: str, data: dict | None = None, **kwargs):
    """POST with a CSRF token injected into the session and form data."""
    token = "test-csrf-token"
    with client.session_transaction() as sess:
        sess["_csrf_token"] = token
    form_data = dict(data or {})
    form_data["_csrf_token"] = token
    return client.post(url, data=form_data, **kwargs)


# ---------------------------------------------------------------------------
# Seeding: one of everything, so parameterised routes have valid ids to hit
# ---------------------------------------------------------------------------

def _seed_everything(db):
    """Insert one race (with course + entry + event), one boat, one series
    (with a race in it), and return the ids the GET sweep substitutes into
    parameterised URLs."""
    now = "2026-06-23T09:00:00"
    course_no = ro.COURSES[0]["course_no"]

    # A series with one race already attached, so series publish/results routes
    # have something to render.
    cur = db.execute(
        """
        INSERT INTO race_series (name, description, discard_profile, min_races_to_constitute, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        ("Smoke Series", "", ro.DEFAULT_DISCARD_PROFILE, 1, now, now),
    )
    series_id = int(cur.lastrowid)

    cur = db.execute(
        """
        INSERT INTO races (name, class_name, series_id, course_no, start_time, rating_rule, notes, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("Smoke Race", "IRC", series_id, course_no, "2026-06-23T10:00:00", "DUAL", "", now),
    )
    race_id = int(cur.lastrowid)

    cur = db.execute(
        """
        INSERT INTO boats (boat_name, sail_no, class_name, owner, design, club, irc_rating, ytc_rating, status, notes, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'ACTIVE', '', ?, ?)
        """,
        ("Smoke Boat", "GBR 1", "IRC", "", "", "", 1.000, 1000, now, now),
    )
    boat_id = int(cur.lastrowid)

    cur = db.execute(
        "INSERT INTO entries (race_id, boat_id, boat_name, sail_no, class_name, rating, status) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (race_id, boat_id, "Smoke Boat", "GBR 1", "IRC", 1.000, "RACING"),
    )
    entry_id = int(cur.lastrowid)

    cur = db.execute(
        "INSERT INTO race_events (race_id, event_time, event_type, label, source) VALUES (?, ?, ?, ?, ?)",
        (race_id, "2026-06-23T10:00:05", "horn", "Smoke horn", "manual"),
    )
    event_id = int(cur.lastrowid)

    user_row = db.execute("SELECT id FROM users ORDER BY id LIMIT 1").fetchone()
    user_id = int(user_row["id"]) if user_row else 1

    db.commit()
    return {
        "race_id": race_id,
        "entry_id": entry_id,
        "event_id": event_id,
        "boat_id": boat_id,
        "series_id": series_id,
        "user_id": user_id,
        "course_no": course_no,
        "clip_id": 1,           # no matching row -> 404 is fine (< 500)
        "slug": "competitor-guide",
        "filename": "style.css",
        "preset_kind": "idle",
    }


# Endpoints intentionally excluded from the automated GET sweep.
_SWEEP_SKIP_ENDPOINTS = {
    "logout",   # would drop the session mid-sweep, weakening every route after it
    "static",   # framework route, not app code
}

_ARG_TOKEN = re.compile(r"<(?:[^:>]+:)?([^>]+)>")


def _fill_rule(rule_str: str, values: dict) -> str | None:
    """Substitute known ids into a Werkzeug rule string. Returns None if the
    rule needs an argument we have no value for."""
    missing = []

    def repl(match):
        name = match.group(1)
        if name not in values:
            missing.append(name)
            return match.group(0)
        return str(values[name])

    filled = _ARG_TOKEN.sub(repl, rule_str)
    return None if missing else filled


def _get_sweep_urls():
    """Every GET-able route as a concrete URL, using placeholder ids that the
    seeded DB will make valid. Uses the app's live URL map so new routes are
    covered automatically."""
    # Placeholder values are resolved lazily against the seed dict at call time;
    # here we only need the rule strings and argument names.
    urls = []
    skipped = []
    for rule in ro.app.url_map.iter_rules():
        if rule.endpoint in _SWEEP_SKIP_ENDPOINTS:
            continue
        methods = rule.methods or set()
        if "GET" not in methods:
            continue
        urls.append((rule.endpoint, rule.rule))
    return urls, skipped


_SWEEP_RULES, _ = _get_sweep_urls()


@pytest.mark.parametrize("endpoint,rule_str", _SWEEP_RULES, ids=[f"{e}:{r}" for e, r in _SWEEP_RULES])
def test_get_route_does_not_5xx(logged_in_client, endpoint, rule_str):
    """Every GET route must wire up and execute without a server error."""
    with ro.get_db() as db:
        values = _seed_everything(db)
    url = _fill_rule(rule_str, values)
    if url is None:
        pytest.skip(f"no placeholder for an argument in {rule_str}")
    resp = logged_in_client.get(url)
    assert resp.status_code < 500, f"{endpoint} ({url}) returned {resp.status_code}"


# ---------------------------------------------------------------------------
# Happy-path write flows -- the core handlers that had no HTTP-level coverage
# ---------------------------------------------------------------------------

class TestRaceWriteFlow:
    def test_create_add_finish_delete_flow(self, logged_in_client):
        course_no = ro.COURSES[0]["course_no"]

        # Create a boat to enter.
        with ro.get_db() as db:
            cur = db.execute(
                """
                INSERT INTO boats (boat_name, sail_no, class_name, owner, design, club, irc_rating, ytc_rating, status, notes, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'ACTIVE', '', ?, ?)
                """,
                ("Flow Boat", "GBR 42", "IRC", "", "", "", 1.010, 1005, "2026-06-23T08:00:00", "2026-06-23T08:00:00"),
            )
            boat_id = int(cur.lastrowid)
            db.commit()

        # Create a race via the route.
        resp = _post_csrf(
            logged_in_client,
            "/admin/race/new",
            {"name": "Flow Race", "course_no": str(course_no), "start_time": "2026-06-23T10:00", "rating_rule": "IRC_TCC"},
        )
        assert resp.status_code in (200, 302)
        with ro.get_db() as db:
            race = db.execute("SELECT id FROM races WHERE name = 'Flow Race'").fetchone()
        assert race is not None, "race was not created via /admin/race/new"
        race_id = int(race["id"])

        # Add the boat as an entry.
        resp = _post_csrf(logged_in_client, f"/admin/race/{race_id}/entry/add_boat", {"boat_id": str(boat_id)})
        assert resp.status_code in (200, 302)
        with ro.get_db() as db:
            entry = db.execute("SELECT id FROM entries WHERE race_id = ?", (race_id,)).fetchone()
        assert entry is not None, "entry was not created"
        entry_id = int(entry["id"])

        # Record a finish for the entry.
        resp = _post_csrf(logged_in_client, f"/admin/race/{race_id}/entry/{entry_id}/finish_now")
        assert resp.status_code in (200, 302)
        with ro.get_db() as db:
            row = db.execute("SELECT finish_time, status FROM entries WHERE id = ?", (entry_id,)).fetchone()
        assert row["finish_time"] or row["status"] == "FINISHED", "finish_now did not record a finish"

        # Delete the entry.
        resp = _post_csrf(logged_in_client, f"/admin/race/{race_id}/entry/{entry_id}/delete")
        assert resp.status_code in (200, 302)
        with ro.get_db() as db:
            assert db.execute("SELECT id FROM entries WHERE id = ?", (entry_id,)).fetchone() is None

        # Delete the race (requires an explicit confirm flag).
        resp = _post_csrf(logged_in_client, f"/admin/race/{race_id}/delete", {"confirm_delete": "1"})
        assert resp.status_code in (200, 302)
        with ro.get_db() as db:
            assert db.execute("SELECT id FROM races WHERE id = ?", (race_id,)).fetchone() is None


class TestBoatWriteFlow:
    def test_create_edit_delete_boat(self, logged_in_client):
        resp = _post_csrf(
            logged_in_client,
            "/admin/boats/new",
            {"boat_name": "CRUD Boat", "sail_no": "GBR 99", "class_name": "IRC", "irc_rating": "1.020", "ytc_rating": "1010"},
        )
        assert resp.status_code in (200, 302)
        with ro.get_db() as db:
            boat = db.execute("SELECT id FROM boats WHERE boat_name = 'CRUD Boat'").fetchone()
        assert boat is not None, "boat was not created via /admin/boats/new"
        boat_id = int(boat["id"])

        resp = _post_csrf(
            logged_in_client,
            f"/admin/boats/{boat_id}/edit",
            {"boat_name": "CRUD Boat Renamed", "sail_no": "GBR 99", "class_name": "IRC", "irc_rating": "1.025", "ytc_rating": "1010"},
        )
        assert resp.status_code in (200, 302)

        resp = _post_csrf(logged_in_client, f"/admin/boats/{boat_id}/delete")
        assert resp.status_code in (200, 302)


class TestSeriesWriteFlow:
    def test_series_publish_and_csv_render(self, logged_in_client):
        # Seed a series with a race so the publish/results routes have content.
        with ro.get_db() as db:
            now = "2026-06-01T08:00:00"
            cur = db.execute(
                """
                INSERT INTO race_series (name, description, discard_profile, min_races_to_constitute, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                ("Publish Series", "", ro.DEFAULT_DISCARD_PROFILE, 1, now, now),
            )
            series_id = int(cur.lastrowid)
            db.execute(
                """
                INSERT INTO races (name, class_name, series_id, course_no, start_time, rating_rule, notes, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ("Publish Race", "IRC", series_id, ro.COURSES[0]["course_no"], "2026-06-01T10:00:00", "DUAL", "", now),
            )
            db.commit()

        resp = logged_in_client.get(f"/admin/series/{series_id}/publish.html")
        assert resp.status_code == 200

        resp = logged_in_client.get(f"/admin/series/{series_id}/results.csv")
        assert resp.status_code == 200
