"""The boat's free-text fleet/class goes the way the race's did.

Three fields answered "what class is this boat in", in three places, and they
disagreed. Two have already gone: the **race's** fleet label came off the New
race form and the Course & start tab in v0.271, and the **per-entry** class
override came off Add from boat database in v0.276. Both for the same reason --
a class comes from the series' rating bands, and free text typed beside it is a
second answer to a question already answered.

The **boat's** own class survived that round, on the grounds that it appears in
the boat picker where it tells two boats apart and claims nothing about a race.
It was the last of the three, and nothing else ever wrote it -- neither IRC nor
YTC import sets it -- so it was only ever as good as what someone typed. Of the
club's 21 boats, 11 had none, and three held the literal string ``"None"``.

Removing the input is not the whole job, twice over:

* the save must stop *reading* it. ``request.form.get("class_name", "")``
  against a form that no longer sends the field writes ``""`` over every boat on
  every save. That is the trap v0.271 found with the race's label, and it is
  written down here so the third time is caught by a test rather than by
  someone noticing a column had gone blank.
* the fleet **scope** goes with it. "Add all <fleet> boats" and the Virtual Race
  Officer's ``scope: fleet`` both selected active boats whose class matched a
  name. Neither end of that comparison is written any more, so it could only
  ever have matched rows typed before today -- and an instruction that silently
  adds nothing is worse than one that is not offered.

What stays is ``entries.class_name``: snapshotted when a boat was entered, it is
the record of the seasons already scored under those labels, and the Class
column still falls back to it for a race with no series to take bands from.
"""
from __future__ import annotations

import pathlib

import pytest

import app as ro

_ROOT = pathlib.Path(__file__).resolve().parent.parent


def _read(*parts):
    return (_ROOT.joinpath(*parts)).read_text(encoding="utf-8")


class TestTheFieldIsGone:
    def test_not_on_the_add_boat_form(self):
        form = _read("templates", "boat_form.html")
        assert 'name="class_name"' not in form
        assert "Fleet / class" not in form

    def test_not_a_column_on_the_boats_page(self):
        page = _read("templates", "boats.html")
        assert "<th>Fleet</th>" not in page
        assert "class_name" not in page

    def test_not_in_the_boat_picker(self):
        """It was there to tell two boats apart. The sail number and the
        ratings beside it already do that, and are not typed guesses."""
        assert "b.class_name" not in _read("templates", "race.html")


class TestThePagesStillRender:
    """Removing markup by hand is how a template ends up with an unclosed tag or
    a name nothing defines any more -- neither of which a string assertion sees."""

    def test_the_boats_list(self, logged_in_client):
        from core.db import get_db
        with get_db() as db:
            db.execute("INSERT INTO boats (boat_name, sail_no, class_name, status, created_at, updated_at)"
                       " VALUES ('Mojito','GBR4822R','Class 1','ACTIVE',datetime('now'),datetime('now'))")
            db.commit()
        page = logged_in_client.get("/admin/boats")
        assert page.status_code == 200
        body = page.get_data(as_text=True)
        assert "Mojito" in body
        assert "Class 1" not in body, "the retired class is still on the page"

    def test_the_add_boat_form(self, logged_in_client):
        page = logged_in_client.get("/admin/boats/new")
        assert page.status_code == 200
        assert "Fleet / class" not in page.get_data(as_text=True)

    def test_the_race_sheet_with_its_bulk_add_and_boat_picker(self, logged_in_client):
        from core.db import get_db
        with get_db() as db:
            db.execute("INSERT INTO boats (boat_name, sail_no, class_name, status, created_at, updated_at)"
                       " VALUES ('Mojito','GBR4822R','Class 1','ACTIVE',datetime('now'),datetime('now'))")
            cur = db.execute("INSERT INTO races (name, class_name, course_no, course_set, start_time, created_at)"
                             " VALUES ('Race 1', 'IRC 1', 1, 1, '2026-09-04T11:14:00', datetime('now'))")
            race_id = int(cur.lastrowid)
            db.commit()
        page = logged_in_client.get(f"/admin/race/{race_id}")
        assert page.status_code == 200
        body = page.get_data(as_text=True)
        assert "Add all active boats" in body, "the bulk-add section did not survive"
        assert "Add all IRC 1 boats" not in body
        assert "Mojito" in body and "Class 1" not in body.split("Add from boat database")[1]


class TestTheSaveStoppedReadingIt:
    """The v0.271 trap: a form that stops sending a field the save still reads."""

    def test_the_save_does_not_read_the_field(self):
        assert 'request.form.get("class_name"' not in _read("routes", "boats.py")

    def test_nor_write_the_column(self):
        saved = _read("routes", "boats.py")
        assert "class_name" not in saved

    def test_editing_a_boat_leaves_its_stored_class_alone(self, logged_in_client, csrf_post):
        """Not blanked, not carried forward: simply untouched. Anything already
        on file is the record of how that boat was entered."""
        from core.db import get_db
        with get_db() as db:
            cur = db.execute(
                "INSERT INTO boats (boat_name, sail_no, class_name, status, created_at, updated_at)"
                " VALUES ('Mojito','GBR4822R','Class 1','ACTIVE',datetime('now'),datetime('now'))")
            boat_id = int(cur.lastrowid)
            db.commit()

        resp = csrf_post(f"/admin/boats/{boat_id}/edit",
                         {"boat_name": "Mojito", "sail_no": "GBR4822R", "owner": "P Dunlop",
                          "status": "ACTIVE"})
        assert resp.status_code in (200, 302)

        with get_db() as db:
            row = db.execute("SELECT boat_name, owner, class_name FROM boats WHERE id = ?",
                             (boat_id,)).fetchone()
        assert row["owner"] == "P Dunlop", "the save did not take"
        assert row["class_name"] == "Class 1", "the save blanked a field it no longer shows"

    def test_a_new_boat_saves_without_one(self, logged_in_client, csrf_post):
        from core.db import get_db
        resp = csrf_post("/admin/boats/new",
                         {"boat_name": "Sgrech Bach", "sail_no": "GBR933", "status": "ACTIVE"})
        assert resp.status_code in (200, 302)
        with get_db() as db:
            row = db.execute("SELECT boat_name FROM boats WHERE sail_no = 'GBR933'").fetchone()
        assert row is not None and row["boat_name"] == "Sgrech Bach"


class TestTheFleetScopeWentWithIt:
    def test_there_is_no_add_fleet_route(self):
        rules = {str(r.rule) for r in ro.app.url_map.iter_rules()}
        assert not [r for r in rules if "add_fleet" in r]

    def test_the_bulk_add_button_is_gone_from_the_race_sheet(self):
        assert "add_fleet_entries" not in _read("templates", "race.html")

    def test_the_entry_scope_no_longer_takes_a_fleet(self):
        from core.raceadmin import EntryScope
        with pytest.raises(TypeError):
            EntryScope(kind="fleet", fleet="IRC 1")

    def test_the_virtual_race_officer_does_not_offer_it(self):
        from core.assistant import TOOLS
        add = next(t for t in TOOLS if t["name"] == "add_entries")
        assert "fleet" not in add["arguments"]
        assert "'fleet' for one class" not in add["arguments"]["scope"]

    def test_add_the_fleet_now_means_every_active_boat(self):
        """A race officer saying "add the fleet" means the boats that are
        sailing. With no class to narrow it to, that is all of them -- not a
        silent nothing."""
        from datetime import datetime
        from core.assistant import CommandContext, grammar_parse
        intent = grammar_parse("add the fleet",
                               CommandContext(now=datetime.now(), current_race_id=1))
        assert intent.name == "add_entries"
        assert intent.arguments["scope"] == "all_active"
        assert "fleet" not in intent.arguments


class TestWhatTheEntriesKeep:
    def test_the_entry_keeps_its_own_class(self):
        """113 entries are stored 'Class 1' and 59 'Class 2'. Those are scored
        results, not a field to tidy up."""
        entrysync = _read("core", "entrysync.py")
        assert "INSERT INTO entries (race_id, boat_id, boat_name, sail_no, class_name" in entrysync

    def test_but_it_no_longer_comes_from_the_boat(self):
        """A new entry taking its class from a field nobody can see or correct
        is worse than taking it from the race, which at least shows."""
        entrysync = _read("core", "entrysync.py")
        assert 'boat["class_name"]' not in entrysync

    def test_the_class_column_still_falls_back_to_the_entry(self):
        """For a race with no series there are no rating bands to name, and the
        entry's stored label is all there is."""
        race = _read("templates", "race.html")
        assert race.count("{% else %}{{ e.class_name or '' }}{% endif %}") == 2

    def test_a_series_competitor_is_labelled_from_its_entry(self):
        from core.series import series_competitor_label
        entry = {"boat_name": "Mojito", "sail_no": "GBR4822R",
                 "class_name": "Class 1", "boat_id": 7}
        boat = {"boat_name": "Mojito", "sail_no": "GBR4822R",
                "class_name": "Class 2", "id": 7}
        assert series_competitor_label(entry, boat)["class_name"] == "Class 1"
