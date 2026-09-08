"""The start plan can be set in the same pass as the bands it starts.

Reported straight off the new Add series page: you tick the rating bands, scroll
down to *Default start plan*, and it says **"No classes configured yet; this
start will be for all classes."** — however many bands you just filled in. The
only way through was to create the series and go back and edit it, which is the
two-step flow that page exists to remove.

The class checkboxes in the start rows were rendered from
``enabled_class_slots_from_config``, which answers "which bands does this series
have". That is the right question for *parsing* a submitted plan and the wrong
one for *rendering* the editor: it froze the editor at the config the page was
loaded with. On the edit page that config already had bands, so it looked fine;
on the add page it is empty by design, so there were no checkboxes at all and
nothing for the relabelling script to work on.

All six slots are rendered now and the page hides the ones whose band is off.
Ticking a band reveals it and ticks it on every start — without that last part
the fault would only get quieter: the band would appear, sit unticked on every
row, and the save would refuse the start for having no classes.
"""
from __future__ import annotations

import json
from datetime import datetime

from core.classconfig import (
    all_class_slots_from_config,
    enabled_class_slots_from_config,
    start_plan_grid_from_plan,
)


def _post(client, url, data):
    token = "test-csrf-token"
    with client.session_transaction() as sess:
        sess["_csrf_token"] = token
    payload = dict(data)
    payload["_csrf_token"] = token
    return client.post(url, data=payload, follow_redirects=True)


ONE_BAND = {"classes": [
    {"name": "IRC Fast", "rating_type": "IRC", "flag": "Numeral 1", "min": 1.0},
]}


class TestEverySlotIsOffered:
    def test_an_empty_config_still_has_six_slots(self):
        slots = all_class_slots_from_config({"classes": []})
        assert [s["slot"] for s in slots] == [
            "IRC_0", "IRC_1", "IRC_2", "YTC_0", "YTC_1", "YTC_2"]

    def test_and_none_of_them_is_enabled(self):
        assert not any(s["enabled"] for s in all_class_slots_from_config({"classes": []}))

    def test_a_defined_band_is_enabled_and_named(self):
        slots = {s["slot"]: s for s in all_class_slots_from_config(ONE_BAND)}
        assert slots["IRC_0"]["enabled"] is True
        assert slots["IRC_0"]["name"] == "IRC Fast"
        assert slots["IRC_1"]["enabled"] is False

    def test_the_enabled_only_helper_is_unchanged(self):
        """It still has a job: deciding which classes a *new* start arrives with
        ticked, where offering a band nobody defined would be wrong."""
        assert [s["slot"] for s in enabled_class_slots_from_config(ONE_BAND)] == ["IRC_0"]


class TestTheGridRendersThemAll:
    def test_the_start_grid_offers_six_slots_with_no_bands(self):
        grid = start_plan_grid_from_plan({"starts": []}, {"classes": []})
        assert len(grid["class_slots"]) == 6

    def test_a_new_start_is_not_pre_ticked_for_bands_that_do_not_exist(self):
        """Rendering all six must not mean selecting all six: a series created
        with just a name still has no bands and no classes on its start."""
        grid = start_plan_grid_from_plan({"starts": []}, {"classes": []})
        assert grid["rows"][0]["selected_slots"] == set()

    def test_a_new_start_is_pre_ticked_for_the_bands_that_do(self):
        grid = start_plan_grid_from_plan({"starts": []}, ONE_BAND)
        assert grid["rows"][0]["selected_slots"] == {"IRC_0"}


class TestTheAddPage:
    def test_it_carries_a_checkbox_for_every_slot(self, logged_in_client):
        html = logged_in_client.get("/admin/series/new").get_data(as_text=True)
        for slot in ("IRC_0", "IRC_1", "IRC_2", "YTC_0", "YTC_1", "YTC_2"):
            assert f'name="start_0_class_{slot}"' in html, slot

    def test_it_no_longer_claims_there_are_no_classes(self, logged_in_client):
        html = logged_in_client.get("/admin/series/new").get_data(as_text=True)
        assert "No classes configured yet" not in html

    def test_the_slots_arrive_hidden_and_disabled(self, logged_in_client):
        """Disabled matters as much as hidden: a disabled checkbox is not
        submitted, so a band the officer never ticked cannot reach the plan."""
        import re
        html = logged_in_client.get("/admin/series/new").get_data(as_text=True)
        box = re.search(r'<input[^>]*name="start_0_class_IRC_0"[^>]*>', html).group(0)
        assert "disabled" in box, box

    def test_the_editor_offers_the_same_slots(self, logged_in_client):
        """Both pages include the same partial; this keeps that true for the
        part that was rendered differently on each."""
        from core.db import get_db
        now = datetime.now().isoformat(timespec="seconds")
        with get_db() as db:
            cur = db.execute(
                "INSERT INTO race_series (name, description, class_config_json,"
                " discard_profile, min_races_to_constitute, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?)",
                ("Existing", "", json.dumps(ONE_BAND), "0,0,1", 3, now, now))
            db.commit()
            sid = int(cur.lastrowid)
        add = logged_in_client.get("/admin/series/new").get_data(as_text=True)
        edit = logged_in_client.get(f"/admin/series/{sid}").get_data(as_text=True)
        for slot in ("IRC_0", "IRC_2", "YTC_2"):
            assert f'name="start_0_class_{slot}"' in add and \
                   f'name="start_0_class_{slot}"' in edit, slot


class TestOnePassActuallySaves:
    """The end of the report: bands and a start plan, in one submit."""

    FORM = {
        "name": "One Pass Series",
        "class_config_mode": "grid", "start_plan_mode": "grid",
        "class_IRC_0_enabled": "1", "class_IRC_0_name": "IRC Fast",
        "class_IRC_0_flag": "Numeral 1", "class_IRC_0_min": "1.000",
        "class_YTC_0_enabled": "1", "class_YTC_0_name": "YTC Fast",
        "class_YTC_0_flag": "Numeral 2", "class_YTC_0_max": "800",
        "start_0_enabled": "1", "start_0_name": "Start 1", "start_0_offset_min": "0",
        "start_0_class_IRC_0": "1", "start_0_class_YTC_0": "1",
        "start_1_enabled": "1", "start_1_name": "Start 2", "start_1_offset_min": "5",
        "start_1_class_YTC_0": "1",
    }

    def _latest(self):
        from core.db import get_db
        import sqlite3
        with get_db() as db:
            db.row_factory = sqlite3.Row
            return db.execute("SELECT * FROM race_series ORDER BY id DESC LIMIT 1").fetchone()

    def test_the_bands_are_saved(self, logged_in_client):
        _post(logged_in_client, "/admin/series/new", self.FORM)
        names = sorted(c["name"] for c in json.loads(self._latest()["class_config_json"])["classes"])
        assert names == ["IRC Fast", "YTC Fast"]

    def test_and_so_is_the_start_plan(self, logged_in_client):
        _post(logged_in_client, "/admin/series/new", self.FORM)
        starts = json.loads(self._latest()["start_plan_json"])["starts"]
        assert [s["name"] for s in starts] == ["Start 1", "Start 2"]

    def test_each_start_keeps_the_classes_it_was_given(self, logged_in_client):
        """The point of a start plan: a second start for one band only. This is
        what could not be expressed on the add page at all."""
        _post(logged_in_client, "/admin/series/new", self.FORM)
        starts = json.loads(self._latest()["start_plan_json"])["starts"]
        assert sorted(starts[0]["classes"]) == ["IRC Fast", "YTC Fast"]
        assert starts[1]["classes"] == ["YTC Fast"]

    def test_a_bare_name_still_gives_one_start_for_all_classes(self, logged_in_client):
        """The other end of the range must not have moved: no bands, no fuss."""
        _post(logged_in_client, "/admin/series/new",
              {"name": "Bare", "class_config_mode": "grid", "start_plan_mode": "grid"})
        starts = json.loads(self._latest()["start_plan_json"])["starts"]
        assert len(starts) == 1 and starts[0]["classes"] == ["All classes"]
