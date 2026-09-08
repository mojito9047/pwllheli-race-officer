"""One boat, one record — because everything else hangs off the boat id.

Four records for MOJITO BACH turned up in the database, one of them with a space
in the sail number. They matter more than untidiness:

* a series groups a competitor by ``boat_id`` (core.series.series_competitor_key),
  so a second record is a second competitor — its results on their own line,
  scored and discarded separately;
* a tracker attaches to one boat id, so only one of the records is tracked;
* every stored GPS fix carries a boat id, so the boat's track splits too.

The rating importers had always matched on a sail number normalised for case and
spacing, and reused the record they found. The Boats page did not, and had no
duplicate check at all — so the way to get four records was to delete the boat
and add it back, which is precisely when its history matters most. Deleting a
boat that has raced only deactivates it (the entries must keep something to point
at), so the record to reuse is usually an inactive one.
"""
from __future__ import annotations

from datetime import datetime

import pytest

import app as ro
import core.boats as boats


NOW = datetime.now().isoformat(timespec="seconds")


def make_boat(name, sail, status="ACTIVE"):
    with ro.app.app_context():
        with ro.get_db() as db:
            cur = db.execute(
                "INSERT INTO boats (boat_name, sail_no, status, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?)", (name, sail, status, NOW, NOW))
            db.commit()
            return int(cur.lastrowid)


def boat_rows():
    with ro.app.app_context():
        with ro.get_db() as db:
            return db.execute("SELECT * FROM boats ORDER BY id").fetchall()


def add_form(csrf_post, name, sail, **extra):
    data = {"boat_name": name, "sail_no": sail, "status": "ACTIVE"}
    data.update(extra)
    return csrf_post("/boats/new", data)


class TestFindingABoatByItsSailNumber:
    @pytest.mark.parametrize("stored, typed", [
        ("GBR1210", "GBR1210"),
        ("GBR1210", "gbr1210"),
        ("GBR1210", "GBR 1210"),
        ("GBR 1210", "GBR1210"),
        ("GBR 1210", " gbr  1210 "),
    ])
    def test_spacing_and_case_do_not_make_a_different_boat(self, client, stored, typed):
        make_boat("Mojito Bach", stored)
        with ro.get_db() as db:
            assert boats.boat_by_sail_no(db, typed) is not None

    def test_a_different_number_is_a_different_boat(self, client):
        make_boat("Mojito Bach", "GBR1210")
        with ro.get_db() as db:
            assert boats.boat_by_sail_no(db, "GBR4822R") is None

    def test_a_blank_sail_number_is_not_an_identity(self, client):
        """Several boats may have none; blank must not match blank."""
        make_boat("No Number", "")
        with ro.get_db() as db:
            assert boats.boat_by_sail_no(db, "") is None
            assert boats.boat_by_sail_no(db, "   ") is None

    def test_an_inactive_boat_is_still_found(self, client):
        """The usual case: deleting a boat that has raced deactivates it."""
        make_boat("Mojito Bach", "GBR1210", status="INACTIVE")
        with ro.get_db() as db:
            assert boats.boat_by_sail_no(db, "GBR1210") is not None

    def test_a_boat_can_be_excluded_from_the_search(self, client):
        """So editing a boat does not find itself and refuse its own save."""
        boat_id = make_boat("Mojito Bach", "GBR1210")
        with ro.get_db() as db:
            assert boats.boat_by_sail_no(db, "GBR1210", exclude_id=boat_id) is None


class TestAddingABoatThatIsAlreadyOnFile:
    def test_no_second_record_is_created(self, logged_in_client, csrf_post):
        make_boat("Mojito Bach", "GBR1210")
        add_form(csrf_post, "Mojito Bach", "GBR1210")
        assert len(boat_rows()) == 1

    def test_nor_when_the_spacing_differs(self, logged_in_client, csrf_post):
        """The exact shape of the fourth record that turned up."""
        make_boat("Mojito Bach", "GBR1210")
        add_form(csrf_post, "Mojito Bach", "GBR 1210")
        assert len(boat_rows()) == 1

    def test_the_old_record_is_reused_so_its_results_stay_with_it(self,
                                                                  logged_in_client,
                                                                  csrf_post):
        """The point of the whole thing: the boat keeps its race history."""
        boat_id = make_boat("Mojito Bach", "GBR1210", status="INACTIVE")
        with ro.app.app_context():
            with ro.get_db() as db:
                cur = db.execute("INSERT INTO races (name, course_no, start_time, created_at)"
                                 " VALUES ('Past', 1, ?, ?)", (NOW, NOW))
                db.execute("INSERT INTO entries (race_id, boat_id, boat_name, sail_no, status)"
                           " VALUES (?, ?, 'Mojito Bach', 'GBR1210', 'FINISHED')",
                           (int(cur.lastrowid), boat_id))
                db.commit()
        add_form(csrf_post, "Mojito Bach", "GBR1210")
        rows = boat_rows()
        assert len(rows) == 1 and int(rows[0]["id"]) == boat_id
        with ro.app.app_context():
            with ro.get_db() as db:
                kept = db.execute("SELECT COUNT(*) n FROM entries WHERE boat_id = ?",
                                  (boat_id,)).fetchone()["n"]
        assert kept == 1, "the entry must still point at the surviving boat"

    def test_re_adding_it_makes_it_active_again(self, logged_in_client, csrf_post):
        make_boat("Mojito Bach", "GBR1210", status="INACTIVE")
        add_form(csrf_post, "Mojito Bach", "GBR1210")
        assert boat_rows()[0]["status"] == "ACTIVE"

    def test_the_submitted_details_are_applied_to_it(self, logged_in_client, csrf_post):
        """Re-adding is still an edit — the new details are not thrown away."""
        make_boat("Mojito Bach", "GBR1210")
        add_form(csrf_post, "Mojito Bach", "GBR1210", owner="New Owner")
        assert boat_rows()[0]["owner"] == "New Owner"

    def test_it_says_what_it_did(self, logged_in_client, csrf_post):
        """Silently doing something other than what the button said would be
        worse than the duplicate."""
        make_boat("Mojito Bach", "GBR1210")
        resp = csrf_post("/boats/new", {"boat_name": "Mojito Bach",
                                        "sail_no": "GBR1210", "status": "ACTIVE"},
                         follow_redirects=True)
        html = resp.get_data(as_text=True)
        assert "already on file" in html

    def test_a_genuinely_new_boat_is_still_added(self, logged_in_client, csrf_post):
        make_boat("Mojito Bach", "GBR1210")
        add_form(csrf_post, "Something Else", "GBR9999")
        assert len(boat_rows()) == 2

    def test_boats_without_sail_numbers_are_not_merged_together(self,
                                                                logged_in_client,
                                                                csrf_post):
        make_boat("First", "")
        add_form(csrf_post, "Second", "")
        assert len(boat_rows()) == 2


class TestEditingASailNumberOntoAnother:
    def test_it_is_refused(self, logged_in_client, csrf_post):
        keep = make_boat("Mojito Bach", "GBR1210")
        other = make_boat("Mojito", "GBR4822R")
        csrf_post(f"/boats/{other}/edit", {"boat_name": "Mojito",
                                           "sail_no": "GBR1210", "status": "ACTIVE"})
        with ro.app.app_context():
            with ro.get_db() as db:
                row = db.execute("SELECT sail_no FROM boats WHERE id = ?", (other,)).fetchone()
        assert row["sail_no"] == "GBR4822R", "the clashing edit must not be saved"
        assert keep  # the original is untouched

    def test_a_boat_can_still_be_saved_with_its_own_number(self, logged_in_client,
                                                           csrf_post):
        """It must not find itself and refuse."""
        boat_id = make_boat("Mojito Bach", "GBR1210")
        csrf_post(f"/boats/{boat_id}/edit", {"boat_name": "Mojito Bach II",
                                             "sail_no": "GBR1210", "status": "ACTIVE"})
        assert boat_rows()[0]["boat_name"] == "Mojito Bach II"


class TestShowingTheOnesAlreadyThere:
    def test_duplicates_are_reported(self, client):
        make_boat("Mojito Bach", "GBR1210")
        make_boat("Mojito Bach", "GBR 1210")
        make_boat("Mojito", "GBR4822R")
        assert boats.duplicate_sail_numbers() == {"GBR1210": 2}

    def test_blank_sail_numbers_are_not_duplicates_of_each_other(self, client):
        make_boat("One", "")
        make_boat("Two", "")
        assert boats.duplicate_sail_numbers() == {}

    def test_the_boats_page_marks_them(self, logged_in_client):
        make_boat("Mojito Bach", "GBR1210")
        make_boat("Mojito Bach", "GBR 1210")
        html = logged_in_client.get("/admin/boats").get_data(as_text=True)
        assert "duplicate" in html

    def test_and_leaves_a_single_record_unmarked(self, logged_in_client):
        make_boat("Mojito", "GBR4822R")
        html = logged_in_client.get("/admin/boats").get_data(as_text=True)
        assert ">duplicate<" not in html
