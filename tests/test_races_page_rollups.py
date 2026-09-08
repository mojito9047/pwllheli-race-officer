"""Which roll-up the Races page opens, and how much room they take.

The page opened *Standalone races* whenever any standalone race existed, which
at this club is most of the year — so the group least likely to hold the race
you came for was the one always open, and every real season sat shut. The rule
it should have had is the one a race officer assumes: open the roll-up holding
the **current race**, the same race the sidebar's *Current race* link goes to.

The whitespace was a second, unrelated fault with a measurable cause, recorded
in ``style.css`` beside the fix: ``.race-rollup`` sets ``padding: 0`` because the
summary carries its own, and the theme layer loads afterwards and re-declares a
bare ``.card { padding: 16px 18px }`` — equal specificity, later file, so it won.
Every collapsed roll-up was inset twice and 99px tall for one line of text, with
44px of dead space under it (18px card margin + 14px grid gap + 12px). These
tests hold the CSS, because it is the load order that broke it and load order is
exactly what nobody notices changing.
"""
from __future__ import annotations

import pathlib
import re
from datetime import datetime, timedelta

_ROOT = pathlib.Path(__file__).resolve().parent.parent


def _series(name):
    from core.db import get_db
    now = datetime.now().isoformat(timespec="seconds")
    with get_db() as db:
        cur = db.execute(
            "INSERT INTO race_series (name, description, discard_profile,"
            " min_races_to_constitute, created_at, updated_at) VALUES (?,?,?,?,?,?)",
            (name, "", "0,0,1", 3, now, now))
        db.commit()
        return int(cur.lastrowid)


def _race(name, series_id=None, *, racing=False, hours_ago=1):
    """A race, optionally with a boat still RACING — which is what makes it current."""
    from core.db import get_db
    now = datetime.now().isoformat(timespec="seconds")
    when = (datetime.now() - timedelta(hours=hours_ago)).isoformat(timespec="seconds")
    with get_db() as db:
        cur = db.execute(
            "INSERT INTO races (name, series_id, course_no, start_time, notes, created_at)"
            " VALUES (?,?,?,?,?,?)", (name, series_id, 1, when, "", now))
        rid = int(cur.lastrowid)
        db.execute("INSERT INTO entries (race_id, boat_name, sail_no, status)"
                   " VALUES (?,?,?,?)",
                   (rid, name + " boat", "GBR" + str(rid),
                    "RACING" if racing else "FINISHED"))
        db.commit()
        return rid


def _open_summaries(html):
    """The names inside every <details ... open>."""
    out = []
    for m in re.finditer(r'<details[^>]*\bopen\b[^>]*>(.*?)</summary>', html, re.S):
        name = re.search(r"<strong>(.*?)</strong>", m.group(1))
        if name:
            out.append(name.group(1).strip())
    return out


class TestTheRightRollUpOpens:
    def test_the_series_holding_the_current_race(self, logged_in_client):
        sid = _series("Autumn")
        _race("Autumn R1", sid, racing=True)
        _race("A stray", None)
        assert "Autumn" in _open_summaries(
            logged_in_client.get("/admin/races").get_data(as_text=True))

    def test_and_not_the_standalone_group(self, logged_in_client):
        """The reported fault, exactly: a standalone race exists, so the old page
        opened that group instead."""
        sid = _series("Autumn")
        _race("Autumn R1", sid, racing=True)
        _race("A stray", None)
        assert "Standalone races" not in _open_summaries(
            logged_in_client.get("/admin/races").get_data(as_text=True))

    def test_only_one_roll_up_opens(self, logged_in_client):
        a, b = _series("Autumn"), _series("Summer")
        _race("Autumn R1", a, racing=True)
        _race("Summer R1", b)
        _race("A stray", None)
        assert len(_open_summaries(
            logged_in_client.get("/admin/races").get_data(as_text=True))) == 1

    def test_standalone_opens_when_the_current_race_is_standalone(self, logged_in_client):
        _series("Autumn")
        _race("A stray", None, racing=True)
        assert _open_summaries(
            logged_in_client.get("/admin/races").get_data(as_text=True)) == ["Standalone races"]

    def test_nothing_opens_when_there_are_no_races(self, logged_in_client):
        _series("Autumn")
        assert _open_summaries(
            logged_in_client.get("/admin/races").get_data(as_text=True)) == []

    def test_the_current_race_is_named_as_such(self, logged_in_client):
        """Otherwise an opened roll-up looks arbitrary."""
        sid = _series("Autumn")
        _race("Autumn R1", sid, racing=True)
        html = logged_in_client.get("/admin/races").get_data(as_text=True)
        assert ">current<" in html

    def test_only_one_race_is_marked_current(self, logged_in_client):
        sid = _series("Autumn")
        _race("Autumn R1", sid, racing=True)
        _race("Autumn R2", sid)
        _race("A stray", None)
        html = logged_in_client.get("/admin/races").get_data(as_text=True)
        assert html.count(">current<") == 1


class TestTheRollUpsAreNotPaddedTwice:
    CSS = (_ROOT / "static" / "style.css").read_text(encoding="utf-8")
    THEME = (_ROOT / "static" / "theme_race_document.css").read_text(encoding="utf-8")

    def test_the_padding_reset_outranks_a_bare_card_rule(self):
        """One class deep, `.race-rollup { padding: 0 }` loses to the theme's
        `.card { padding }` on load order alone."""
        assert ".race-rollups > .race-rollup { padding: 0; margin: 0; }" in self.CSS

    def test_the_theme_still_pads_ordinary_cards(self):
        """The fix must be a carve-out, not a removal: every other card wants it."""
        assert ".card { padding: 16px 18px; }" in self.THEME

    def test_the_grid_still_does_the_spacing(self):
        """With the card margin off, the gap is the only thing between roll-ups —
        so it has to still be there."""
        assert re.search(r"\.race-rollups\s*\{[^}]*gap:", self.CSS)
