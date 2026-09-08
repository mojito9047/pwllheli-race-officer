"""The fleet label is no longer editable, and must not be destroyed either.

The Course & start tab used to carry a free-text **Fleet label**, and the New
race form a **Class / fleet** box. Both were dropped in v0.276: a race's class
comes from the series rating bands, and the free-text field was a second,
disagreeing answer to the same question.

Dropping the input is not the whole job. ``update_race`` read the field with
``request.form.get("class_name", "")``, so the moment the form stopped sending
it every save of that tab would have written an empty string over whatever the
race had — and the Races list, the series race table and the published results
all still show it for the seasons that were run with it. Silent data loss on a
tab a race officer saves repeatedly on race morning.

So ``RaceSettings.class_name`` is now ``None`` for "leave it alone", matching
the convention ``name`` already used, and only an explicitly submitted value
changes anything.
"""
from __future__ import annotations

import core.raceadmin as raceadmin
from core.raceadmin import RaceSettings


def _race_row(race_id):
    from core.db import get_db
    with get_db() as db:
        db.row_factory = __import__("sqlite3").Row
        return db.execute("SELECT * FROM races WHERE id = ?", (race_id,)).fetchone()


def _save(race_id, settings):
    """update_race_settings takes the connection and the row, not an id."""
    from core.db import get_db
    import sqlite3
    with get_db() as db:
        db.row_factory = sqlite3.Row
        race = db.execute("SELECT * FROM races WHERE id = ?", (race_id,)).fetchone()
        raceadmin.update_race_settings(db, race, settings)
        db.commit()


def _post(client, url, data):
    """POST as the logged-in client, with a CSRF token it will accept.

    The csrf_post fixture is bound to the plain `client`; these routes need a
    session that is also logged in, so the token is seeded here the same way.
    Without it the POST is rejected and a test that only checks a value was
    *preserved* passes for the wrong reason.
    """
    token = "test-csrf-token"
    with client.session_transaction() as sess:
        sess["_csrf_token"] = token
    payload = dict(data)
    payload["_csrf_token"] = token
    return client.post(url, data=payload, follow_redirects=True)


def _make_race(name="R1", class_name="IRC 1"):
    """A saved race carrying a fleet label, as the older seasons do."""
    from core.db import get_db
    from datetime import datetime
    now = datetime.now().isoformat(timespec="seconds")
    with get_db() as db:
        cur = db.execute(
            "INSERT INTO races (name, class_name, course_no, start_time, notes, created_at)"
            " VALUES (?,?,?,?,?,?)",
            (name, class_name, 1, "", "", now))
        db.commit()
        return cur.lastrowid


class TestTheLabelSurvivesASaveThatDoesNotMentionIt:
    def test_omitting_the_field_keeps_the_existing_label(self, client):
        race_id = _make_race(class_name="IRC 1")
        _save(race_id, RaceSettings(name="R1 renamed", course_no=1))
        assert _race_row(race_id)["class_name"] == "IRC 1"

    def test_the_rest_of_the_form_still_saves(self, client):
        """Preserving one field must not quietly preserve everything."""
        race_id = _make_race(class_name="IRC 1")
        _save(race_id, RaceSettings(name="R1 renamed", course_no=1))
        assert _race_row(race_id)["name"] == "R1 renamed"

    def test_a_race_without_a_label_stays_without_one(self, client):
        race_id = _make_race(class_name="")
        _save(race_id, RaceSettings(name="R1", course_no=1))
        assert (_race_row(race_id)["class_name"] or "") == ""


class TestAnExplicitValueStillWins:
    """The field is gone from the UI, not from the model: the assistant and the
    on-the-water page still set it, and an import may too."""

    def test_an_explicit_label_is_written(self, client):
        race_id = _make_race(class_name="IRC 1")
        _save(race_id, RaceSettings(name="R1", course_no=1, class_name="IRC 2"))
        assert _race_row(race_id)["class_name"] == "IRC 2"

    def test_an_explicit_empty_string_still_clears_it(self, client):
        """"" is a decision; None is the absence of one. Anything that really
        wants the label gone can still say so."""
        race_id = _make_race(class_name="IRC 1")
        _save(race_id, RaceSettings(name="R1", course_no=1, class_name=""))
        assert (_race_row(race_id)["class_name"] or "") == ""


class TestTheFormNoLongerOffersIt:
    def test_the_course_and_start_tab_has_no_fleet_input(self, logged_in_client):
        race_id = _make_race(class_name="IRC 1")
        html = logged_in_client.get(f"/admin/race/{race_id}").get_data(as_text=True)
        assert 'name="class_name"' not in html
        assert "Fleet label" not in html

    def test_saving_that_tab_over_the_web_keeps_the_label(self, logged_in_client):
        """The end-to-end version of the first test: a real POST of the real
        form, which is where the empty-string default would have bitten."""
        race_id = _make_race(class_name="IRC 1")
        _post(logged_in_client, f"/admin/race/{race_id}/update", {
            "name": "R1 saved", "start_time": "", "series_id": "", "notes": "",
            "course_no": "1", "start_plan_mode": "grid",
        })
        row = _race_row(race_id)
        # The rename proves the POST was accepted. Without it this test passes
        # just as happily when the request is rejected outright, which is the
        # trap in asserting only that something was PRESERVED.
        assert row["name"] == "R1 saved", "the save did not go through"
        assert row["class_name"] == "IRC 1"
