"""A boat carries one tracker, and the table now enforces it.

It did not, and the cost was a race. The trackers table is keyed on the device,
so pointing a second device at a boat that already had one simply inserted a
row. Every fix from both was then stamped with that boat, and the boat's track
became the two devices interleaved.

That is harmless while both devices are in the same place and ruinous when they
are not. In the club race of 8 August 2026 one of CRACKAJACK's two trackers was
aboard MOJITO for the whole race — median 10 m from Mojito's own tracker, 89% of
its fixes within 50 m — so the replay drew Crackajack flipping between the two
boats several times a minute: 1,632 physically impossible hops in seventeen
hours. Repairing it meant detaching 30,154 rows
(``scripts/fix_track_misattribution.py``).
"""
from __future__ import annotations

import app as ro
import core.track as track


NOW = "2026-08-08T09:00:00"


def _boat(name, sail):
    with ro.app.app_context():
        with ro.get_db() as db:
            cur = db.execute(
                "INSERT INTO boats (boat_name, sail_no, status, created_at, updated_at)"
                " VALUES (?, ?, 'ACTIVE', ?, ?)", (name, sail, NOW, NOW))
            db.commit()
            return int(cur.lastrowid)


def _pairings():
    with ro.app.app_context():
        with ro.get_db() as db:
            return {r["unique_id"]: r["boat_id"]
                    for r in db.execute("SELECT unique_id, boat_id FROM trackers")}


class TestASecondTrackerDisplacesTheFirst:
    def test_the_boat_ends_with_exactly_one(self, client):
        boat = _boat("Crackajack", "GBR7664T")
        track.upsert_tracker("864864070498856", label="the real one", boat_id=boat)
        displaced = track.upsert_tracker("864032050547569", label="the spare", boat_id=boat)

        assert displaced == ["864864070498856"], "the caller is not told what it displaced"
        pairs = _pairings()
        assert pairs["864032050547569"] == boat
        assert pairs["864864070498856"] is None, "two devices are still pointed at one boat"

    def test_the_displaced_tracker_is_kept_not_deleted(self, client):
        """Unassigned, not removed. It is a device the club owns."""
        boat = _boat("Mojito", "GBR4822R")
        track.upsert_tracker("AAA", boat_id=boat)
        track.upsert_tracker("BBB", boat_id=boat)
        assert "AAA" in _pairings()

    def test_other_boats_are_untouched(self, client):
        one, two = _boat("One", "G1"), _boat("Two", "G2")
        track.upsert_tracker("AAA", boat_id=one)
        track.upsert_tracker("BBB", boat_id=two)
        track.upsert_tracker("CCC", boat_id=two)
        pairs = _pairings()
        assert pairs["AAA"] == one, "assigning a tracker to one boat disturbed another"
        assert pairs["BBB"] is None and pairs["CCC"] == two

    def test_assigning_no_boat_displaces_nothing(self, client):
        boat = _boat("Spare", "G3")
        track.upsert_tracker("AAA", boat_id=boat)
        assert track.upsert_tracker("BBB", label="in the drawer") == []
        assert _pairings()["AAA"] == boat

    def test_reassigning_the_same_tracker_is_not_a_displacement(self, client):
        """Renaming a tracker that already has the boat must not report anything."""
        boat = _boat("Finally", "GBR6939R")
        track.upsert_tracker("AAA", label="first name", boat_id=boat)
        assert track.upsert_tracker("AAA", label="better name", boat_id=boat) == []
        assert _pairings()["AAA"] == boat


class TestSwappingABoatBetweenDevicesInOneSubmit:
    """The Trackers page posts every row at once, so a swap is one request.

    The route has to apply the unassignments before the assignments. In form
    order the set can displace a device the very next row was about to free
    anyway — harmless to the data, but it would report a displacement the
    operator did not cause, and a warning about something you did not do is
    worse than no warning.
    """

    def test_the_swap_works_and_reports_nothing(self, logged_in_client, csrf_post):
        boat = _boat("Crackajack", "GBR7664T")
        track.upsert_tracker("OLD", label="old unit", boat_id=boat)
        track.upsert_tracker("NEW", label="new unit")

        response = csrf_post("/trackers/save", {
            "tracker_uids": ["NEW", "OLD"],          # the new one first, deliberately
            "label_NEW": "new unit", "boat_NEW": str(boat),
            "label_OLD": "old unit", "boat_OLD": "",
        }, follow_redirects=True)
        assert response.status_code == 200

        pairs = _pairings()
        assert pairs["NEW"] == boat and pairs["OLD"] is None
        body = response.get_data(as_text=True)
        assert "was unassigned" not in body and "were unassigned" not in body, \
            "a clean swap reported a displacement the operator did not cause"

    def test_a_real_displacement_is_reported(self, logged_in_client, csrf_post):
        """Pointing a second device at a boat without freeing the first says so."""
        boat = _boat("Mojito", "GBR4822R")
        track.upsert_tracker("OLD", label="old unit", boat_id=boat)
        track.upsert_tracker("NEW", label="new unit")

        response = csrf_post("/trackers/save", {
            "tracker_uids": ["OLD", "NEW"],
            "label_OLD": "old unit", "boat_OLD": str(boat),
            "label_NEW": "new unit", "boat_NEW": str(boat),
        }, follow_redirects=True)
        body = response.get_data(as_text=True)
        assert sum(1 for v in _pairings().values() if v == boat) == 1
        assert "Mojito can only carry one tracker" in body,             "the message does not name the boat it is about"
        assert "it now has NEW" in body, "the message does not say which tracker won"
        assert "OLD was left unassigned" in body, "the message does not say which lost"

    def test_the_winner_is_never_listed_as_unassigned(self, logged_in_client, csrf_post):
        """The complaint that prompted this wording.

        Setting two trackers to one boat in a single save makes the second
        displace the first, so a flat list of everything displaced named a
        tracker that ended up holding the boat after all -- printed next to a
        table showing that same tracker assigned to it.
        """
        boat = _boat("Andromeda", "GBR2093R")
        track.upsert_tracker("FIRST", boat_id=boat)
        track.upsert_tracker("A", label="a")
        track.upsert_tracker("B", label="b")

        response = csrf_post("/trackers/save", {
            "tracker_uids": ["A", "B"],
            "label_A": "a", "boat_A": str(boat),
            "label_B": "b", "boat_B": str(boat),
        }, follow_redirects=True)
        body = response.get_data(as_text=True)

        pairs = _pairings()
        winner = [u for u, b in pairs.items() if b == boat]
        assert winner == ["B"], pairs
        assert "it now has B" in body
        assert "B was left unassigned" not in body and "B were left unassigned" not in body,             "the tracker that kept the boat is being reported as unassigned"
        assert "FIRST" in body and "A" in body


class TestAdoptingASpareOntoABoatThatHasOne:
    """The likeliest way it happened, so it is the message worth having.

    A spare tracker is switched on, Traccar registers it, somebody adopts it
    from the Trackers page and picks the boat off a dropdown. Nothing in that
    flow mentions the device already aboard.
    """

    def test_the_message_says_what_was_unassigned(self, client, monkeypatch):
        boat = _boat("Crackajack", "GBR7664T")
        track.upsert_tracker("864864070498856", label="the real one", boat_id=boat)
        # Adoption with Traccar not configured still records the pairing: it
        # skips the claim and goes straight to writing the assignment.
        cfg = {**track.track_config(), "base_url": "", "token": ""}
        monkeypatch.setattr(track, "track_config", lambda: cfg)
        ok, message = track.adopt_tracker("864032050547569", name="the spare", boat_id=boat)
        assert ok, message
        assert "864864070498856" in message and "unassigned" in message, message
        assert sum(1 for v in _pairings().values() if v == boat) == 1
