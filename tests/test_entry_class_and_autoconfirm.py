"""What class a boat is in, and who confirms a GPS finish.

Three faults reported off one screenshot of the race sheet.

**The Class column disagreed with itself.** *Add entries* showed the series
rating band a boat is scored in — IRC1, YTC2 — from ``entry_class_labels_map``.
*Entries & finish times*, two tabs away, showed the boat record's own free-text
``class_name`` — "Class 1", "YTC" — which is a different question with a
different answer, typed by hand at some point and never revisited. Two columns
with the same heading, side by side in the same race, saying different things.

**And the free text was still being typed in.** *Add from boat database* carried
a *Class override* box, defaulted to the race's fleet label, writing that text
onto the entry. It is the per-entry version of the fleet field that went in
v0.276 for exactly this reason: a race's classes come from the series' rating
bands, and a second hand-typed answer can only ever disagree with them.

**Auto-confirm was on by default.** Detection proposing a finish and a person
accepting it is one click. A wrong finish written straight into the results of a
race still being sailed has to be noticed first. Detection stays armed by
default — a missed one cannot be recovered — but recording without approval is
now opt-in, and the unmanned on-the-water page opts in explicitly.
"""
from __future__ import annotations

import json
from datetime import datetime

import app as ro
import core.raceadmin as raceadmin
from core.raceadmin import RaceSpec


BANDS = {"classes": [
    {"name": "IRC 1", "rating_type": "IRC", "flag": "Numeral 1", "min": 1.000},
    {"name": "IRC 2", "rating_type": "IRC", "flag": "Numeral 2", "max": 1.000},
]}


def _seed(irc=1.050):
    """A race in a series with two IRC bands, one boat entered, in band 1."""
    from core.db import get_db
    now = datetime.now().isoformat(timespec="seconds")
    with get_db() as db:
        cur = db.execute(
            "INSERT INTO race_series (name, description, class_config_json,"
            " discard_profile, min_races_to_constitute, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?)",
            ("Bands", "", json.dumps(BANDS), "0,0,1", 3, now, now))
        sid = int(cur.lastrowid)
        cur = db.execute(
            "INSERT INTO races (name, series_id, course_no, start_time, notes, created_at)"
            " VALUES (?,?,?,?,?,?)", ("R1", sid, 1, "", "", now))
        rid = int(cur.lastrowid)
        cur = db.execute(
            "INSERT INTO boats (boat_name, sail_no, class_name, irc_rating, status,"
            " created_at, updated_at) VALUES (?,?,?,?,'ACTIVE',?,?)",
            # The boat record carries a stale hand-typed class, which is the whole
            # point: the page must not show this one.
            ("Andromed A", "GBR2093R", "Class 1", irc, now, now))
        bid = int(cur.lastrowid)
        db.execute(
            "INSERT INTO entries (race_id, boat_id, boat_name, sail_no, class_name,"
            " manual_irc_rating, status) VALUES (?,?,?,?,?,?, 'RACING')",
            (rid, bid, "Andromed A", "GBR2093R", "Class 1", irc))
        db.commit()
    return rid, bid


class TestTheClassColumnAgreesWithItself:
    def test_the_finish_tab_shows_the_rating_band(self, logged_in_client):
        rid, _ = _seed()
        html = logged_in_client.get(f"/admin/race/{rid}").get_data(as_text=True)
        assert "IRC 1" in html

    def test_it_does_not_show_the_boat_record_class(self, logged_in_client):
        """"Class 1" is what the boat database says and what the tab used to
        show. It may still appear in the boat *picker*, where it helps tell two
        boats apart and claims nothing about the race; it may not appear in a
        table cell, which is where it read as this race's answer."""
        rid, _ = _seed()
        html = logged_in_client.get(f"/admin/race/{rid}").get_data(as_text=True)
        assert ">Class 1<" not in html

    def test_both_tabs_render_the_same_cell(self, logged_in_client):
        """Not "both happen to be right today": the same expression, so a change
        to one cannot leave the other behind. Counted, because the label appears
        once per tab and nowhere else."""
        rid, _ = _seed()
        html = logged_in_client.get(f"/admin/race/{rid}").get_data(as_text=True)
        assert html.count("entry_class_labels") == 0     # rendered, not leaked
        assert html.count(">IRC 1<") >= 2, html.count(">IRC 1<")

    def test_a_race_with_no_bands_still_shows_something(self, logged_in_client):
        """The fallback matters: without a series there are no bands to name, and
        an empty column would be worse than the old free text."""
        from core.db import get_db
        now = datetime.now().isoformat(timespec="seconds")
        with get_db() as db:
            cur = db.execute("INSERT INTO races (name, course_no, start_time, notes,"
                             " created_at) VALUES ('Solo', 1, '', '', ?)", (now,))
            rid = int(cur.lastrowid)
            db.execute("INSERT INTO entries (race_id, boat_name, sail_no, class_name,"
                       " status) VALUES (?,?,?,?, 'RACING')",
                       (rid, "Lone", "GBR1", "Cruiser"))
            db.commit()
        html = logged_in_client.get(f"/admin/race/{rid}").get_data(as_text=True)
        assert "Cruiser" in html


class TestTheClassOverrideIsGone:
    def test_the_box_is_not_on_the_page(self, logged_in_client):
        rid, _ = _seed()
        html = logged_in_client.get(f"/admin/race/{rid}").get_data(as_text=True)
        assert "Class override" not in html

    def test_adding_a_boat_still_works_without_it(self, logged_in_client):
        """The field is gone from the form, not from the model, and an add that
        does not mention it must still add the boat."""
        from core.db import get_db
        rid, _ = _seed()
        now = datetime.now().isoformat(timespec="seconds")
        with get_db() as db:
            cur = db.execute(
                "INSERT INTO boats (boat_name, sail_no, irc_rating, status, created_at,"
                " updated_at) VALUES ('Mojito','GBR4822R',1.084,'ACTIVE',?,?)", (now, now))
            bid = int(cur.lastrowid)
            db.commit()
        token = "test-csrf-token"
        with logged_in_client.session_transaction() as sess:
            sess["_csrf_token"] = token
        logged_in_client.post(f"/admin/race/{rid}/entry/add_boat",
                              data={"boat_id": bid, "_csrf_token": token},
                              follow_redirects=True)
        with get_db() as db:
            n = db.execute("SELECT COUNT(*) FROM entries WHERE race_id = ? AND boat_id = ?",
                           (rid, bid)).fetchone()[0]
        assert n == 1


class TestAutoConfirmIsOptIn:
    def _race(self, race_id):
        from core.db import get_db
        import sqlite3
        with get_db() as db:
            db.row_factory = sqlite3.Row
            return db.execute("SELECT * FROM races WHERE id = ?", (race_id,)).fetchone()

    def test_detection_is_still_armed(self, client):
        """Only the confirming changed. A detection missed is gone for good."""
        from core.db import get_db
        with get_db() as db:
            created = raceadmin.create_race(db, RaceSpec(name="Club Race"))
        assert int(self._race(created.race_id)["gps_finish_enabled"]) == 1

    def test_but_nothing_is_recorded_without_a_person(self, client):
        from core.db import get_db
        with get_db() as db:
            created = raceadmin.create_race(db, RaceSpec(name="Club Race"))
        assert int(self._race(created.race_id)["gps_auto_confirm"]) == 0

    def test_the_unmanned_case_can_still_ask_for_it(self, client):
        from core.db import get_db
        with get_db() as db:
            created = raceadmin.create_race(
                db, RaceSpec(name="From the water", gps_auto_confirm=True))
        assert int(self._race(created.race_id)["gps_auto_confirm"]) == 1

    def test_the_tick_box_arrives_unticked(self, logged_in_client):
        from core.db import get_db
        with get_db() as db:
            created = raceadmin.create_race(db, RaceSpec(name="Club Race"))
        html = logged_in_client.get(f"/admin/race/{created.race_id}").get_data(as_text=True)
        if 'name="gps_auto_confirm"' in html:      # only rendered when tracking is on
            import re
            box = re.search(r'<input[^>]*name="gps_auto_confirm"[^>]*>', html).group(0)
            assert "checked" not in box, box
