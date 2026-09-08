"""Adding a series sets it up in one pass, not a blank record to go and edit.

Series creation used to be a name and a description box sitting permanently open
above the list, and everything that actually decides how a series scores -- the
discard profile, the rating bands, the default start plan -- could only be
reached afterwards under *Edit series details*. So the honest flow was: create a
blank series, land on it, immediately open the editor and fill it in.

The add page now carries the same form, and it is literally the same form:
``templates/partials/series_form.html`` is included by both, and both routes
read it through ``_series_form_values``. These tests exist to keep that true --
a band or a start added to the editor and silently ignored by the create path
would be invisible until somebody scored a season with it.
"""
from __future__ import annotations

import json


def _series_row(series_id):
    from core.db import get_db
    import sqlite3
    with get_db() as db:
        db.row_factory = sqlite3.Row
        return db.execute("SELECT * FROM race_series WHERE id = ?", (series_id,)).fetchone()


def _created_id():
    from core.db import get_db
    with get_db() as db:
        row = db.execute("SELECT id FROM race_series ORDER BY id DESC LIMIT 1").fetchone()
    return row[0] if row else None


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


FULL_FORM = {
    "name": "Autumn Series 2026",
    "description": "Sundays, September to November",
    "class_config_mode": "grid",
    "start_plan_mode": "grid",
    "discard_profile": "0,0,1,1,2",
    "min_races_to_constitute": "4",
    # One IRC band...
    "class_IRC_0_enabled": "1",
    "class_IRC_0_name": "IRC 1",
    "class_IRC_0_flag": "Numeral 1",
    "class_IRC_0_min": "1.000",
    "class_IRC_0_min_inclusive": "1",
    # ...and one YTC band.
    "class_YTC_0_enabled": "1",
    "class_YTC_0_name": "YTC Fast",
    "class_YTC_0_flag": "Numeral 2",
    "class_YTC_0_max": "1000",
    # A first start carrying the IRC band.
    "start_0_enabled": "1",
    "start_0_name": "Start 1",
    "start_0_offset_min": "0",
    "start_0_class_IRC_0": "1",
}


class TestTheAddPageOffersTheWholeSetup:
    def test_the_form_is_on_the_page(self, logged_in_client):
        html = logged_in_client.get("/admin/series/new").get_data(as_text=True)
        for field in ("discard_profile", "min_races_to_constitute",
                      "class_IRC_0_name", "start_0_name"):
            assert f'name="{field}"' in html, f"{field} missing from the add page"

    def test_it_is_the_same_partial_the_editor_uses(self, logged_in_client):
        """Not a lookalike: both pages must render the same field names."""
        from core.db import get_db
        from datetime import datetime
        now = datetime.now().isoformat(timespec="seconds")
        with get_db() as db:
            cur = db.execute(
                "INSERT INTO race_series (name, description, discard_profile,"
                " min_races_to_constitute, created_at, updated_at) VALUES (?,?,?,?,?,?)",
                ("Existing", "", "0,0,1", 3, now, now))
            db.commit()
            sid = cur.lastrowid
        add = logged_in_client.get("/admin/series/new").get_data(as_text=True)
        edit = logged_in_client.get(f"/admin/series/{sid}").get_data(as_text=True)
        for field in ("class_IRC_0_min_inclusive", "class_YTC_2_max",
                      "start_5_offset_min", "class_config_mode"):
            assert f'name="{field}"' in add and f'name="{field}"' in edit, field


class TestWhatIsPostedIsWhatIsSaved:
    def test_discards_and_minimum_races_are_stored(self, logged_in_client):
        _post(logged_in_client, "/admin/series/new", FULL_FORM)
        row = _series_row(_created_id())
        assert row["discard_profile"] == "0,0,1,1,2"
        assert row["min_races_to_constitute"] == 4

    def test_the_rating_bands_are_stored(self, logged_in_client):
        _post(logged_in_client, "/admin/series/new", FULL_FORM)
        classes = json.loads(_series_row(_created_id())["class_config_json"])["classes"]
        names = sorted(c["name"] for c in classes)
        assert names == ["IRC 1", "YTC Fast"], names

    def test_the_start_plan_is_stored(self, logged_in_client):
        _post(logged_in_client, "/admin/series/new", FULL_FORM)
        starts = json.loads(_series_row(_created_id())["start_plan_json"])["starts"]
        assert starts and starts[0]["name"] == "Start 1"

    def test_a_bare_name_still_creates_a_usable_series(self, logged_in_client):
        """The quick path must survive: a name alone, defaults for the rest."""
        _post(logged_in_client, "/admin/series/new",
              {"name": "Just A Name", "class_config_mode": "grid", "start_plan_mode": "grid"})
        row = _series_row(_created_id())
        assert row["name"] == "Just A Name"
        assert (row["discard_profile"] or "").strip() != ""

    def test_a_bare_name_stores_no_rating_bands(self, logged_in_client):
        """The add page shows the six band rows unticked, and an untouched form
        must therefore store none.

        class_grid_from_config hands them back already enabled and named
        IRC0..YTC2. Rendered as-is, every series created with just a name would
        have arrived carrying six placeholder bands with no rating limits --
        which is not what the old create route did, and would quietly change how
        the season scored."""
        html = logged_in_client.get("/admin/series/new").get_data(as_text=True)
        assert "class_IRC_0_enabled" in html
        import re
        row0 = re.search(r'name="class_IRC_0_enabled"[^>]*>', html).group(0)
        assert "checked" not in row0, "the add page arrives with a band already ticked"

        _post(logged_in_client, "/admin/series/new",
              {"name": "Bare", "class_config_mode": "grid", "start_plan_mode": "grid"})
        classes = json.loads(_series_row(_created_id())["class_config_json"] or '{"classes": []}')
        assert classes["classes"] == [], classes

    def test_no_name_is_refused(self, logged_in_client):
        before = _created_id()
        _post(logged_in_client, "/admin/series/new", {"name": "   "})
        assert _created_id() == before, "a nameless series was created"
