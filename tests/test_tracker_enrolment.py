"""Adopting trackers Traccar has already seen, instead of typing an IMEI.

With automatic registration enabled on the relay, a tracker that is switched on
appears in Traccar straight away. The Trackers page lists those devices so a
race officer picks one and assigns a boat — 15-digit ids are error-prone and the
id is not written on every device.
"""
from __future__ import annotations

import time
from datetime import datetime

import app as ro
import core.track as track


def _traccar_configured(monkeypatch, devices, owned=None, users=None, linked=None):
    """Pretend Traccar is set up.

    ``devices`` is everything Traccar holds; ``owned`` is the subset linked to
    the API token's user (what plain /api/devices returns). A device Traccar
    auto-registered belongs to nobody, so it is in ``devices`` but not
    ``owned`` — the case that made a switched-on tracker invisible to the app.
    """
    owned = devices if owned is None else owned
    cfg = {**track.track_config(), "base_url": "https://relay/traccar", "token": "t"}
    monkeypatch.setattr(track, "track_config", lambda: cfg)
    monkeypatch.setattr(track, "traccar_list_devices",
                        lambda c, include_unowned=False: devices if include_unowned else owned)
    if users is not None:
        monkeypatch.setattr(track, "_traccar_request",
                            lambda c, method, path, body=None, timeout=4.0: users)
    if linked is not None:
        monkeypatch.setattr(track, "traccar_link_device",
                            lambda c, device_id, user_id: linked.append((device_id, user_id)))
    return cfg


def _iso(ago_seconds):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - ago_seconds))


def _known_tracker(unique_id="KNOWN-1"):
    with ro.get_db() as db:
        db.execute("INSERT INTO trackers (unique_id, traccar_device_id, label, boat_id, active, updated_at)"
                   " VALUES (?, '5', 'known', NULL, 1, ?)",
                   (unique_id, datetime.now().isoformat(timespec="seconds")))
        db.commit()


class TestListingUnclaimedDevices:
    def test_lists_devices_not_yet_adopted(self, client, monkeypatch):
        _traccar_configured(monkeypatch, [
            {"id": 1, "uniqueId": "NEW-1", "name": "Tracker A", "status": "online", "lastUpdate": _iso(30)},
            {"id": 2, "uniqueId": "NEW-2", "name": "Tracker B", "status": "offline", "lastUpdate": _iso(4000)},
        ])
        devices, error = track.unregistered_traccar_devices()
        assert error is None
        assert [d["unique_id"] for d in devices] == ["NEW-1", "NEW-2"]
        assert devices[0]["rag"] == "green" and devices[1]["rag"] == "red"
        assert "ago" in devices[0]["age_text"]

    def test_hides_devices_already_set_up_here(self, client, monkeypatch):
        _known_tracker("KNOWN-1")
        _traccar_configured(monkeypatch, [
            {"id": 5, "uniqueId": "KNOWN-1", "name": "Mine", "lastUpdate": _iso(10)},
            {"id": 6, "uniqueId": "NEW-9", "name": "Theirs", "lastUpdate": _iso(10)},
        ])
        devices, _ = track.unregistered_traccar_devices()
        assert [d["unique_id"] for d in devices] == ["NEW-9"]

    def test_most_recently_heard_from_first_and_silent_ones_last(self, client, monkeypatch):
        _traccar_configured(monkeypatch, [
            {"id": 1, "uniqueId": "OLD", "lastUpdate": _iso(3000)},
            {"id": 2, "uniqueId": "NEVER"},                       # no lastUpdate at all
            {"id": 3, "uniqueId": "FRESH", "lastUpdate": _iso(5)},
        ])
        devices, _ = track.unregistered_traccar_devices()
        assert [d["unique_id"] for d in devices] == ["FRESH", "OLD", "NEVER"]
        assert devices[-1]["age_text"] == "never reported"

    def test_no_traccar_configured_is_quiet(self, client, monkeypatch):
        unset = {**track.track_config(), "base_url": "", "token": ""}   # snapshot first
        monkeypatch.setattr(track, "track_config", lambda: unset)
        assert track.unregistered_traccar_devices() == ([], None)

    def test_a_traccar_error_is_reported_not_raised(self, client, monkeypatch):
        cfg = {**track.track_config(), "base_url": "https://relay/traccar", "token": "t"}
        monkeypatch.setattr(track, "track_config", lambda: cfg)

        def boom(_cfg, include_unowned=False):
            raise RuntimeError("connection refused")

        monkeypatch.setattr(track, "traccar_list_devices", boom)
        devices, error = track.unregistered_traccar_devices()
        assert devices == [] and "connection refused" in error


class TestAdopting:
    def test_adopts_and_links_the_traccar_device_id(self, client, monkeypatch):
        _traccar_configured(monkeypatch, [{"id": 42, "uniqueId": "NEW-1", "name": "Tracker A"}])
        ok, message = track.adopt_tracker("NEW-1", boat_id=None)
        assert ok and "NEW-1" in message or "Tracker A" in message
        row = next(t for t in track.list_trackers() if t["unique_id"] == "NEW-1")
        assert str(row["traccar_device_id"]) == "42"
        assert row["label"] == "Tracker A"          # falls back to the Traccar name

    def test_assigns_the_chosen_boat(self, client, monkeypatch):
        now_iso = datetime.now().isoformat(timespec="seconds")
        with ro.get_db() as db:
            cur = db.execute("INSERT INTO boats (boat_name, sail_no, status, created_at, updated_at)"
                             " VALUES ('Adopt Boat', 'GBR7', 'ACTIVE', ?, ?)", (now_iso, now_iso))
            boat_id = int(cur.lastrowid)
            db.commit()
        _traccar_configured(monkeypatch, [{"id": 43, "uniqueId": "NEW-2", "name": ""}])
        ok, _ = track.adopt_tracker("NEW-2", name="Loaner 2", boat_id=boat_id)
        assert ok
        row = next(t for t in track.list_trackers() if t["unique_id"] == "NEW-2")
        assert row["boat_id"] == boat_id and row["label"] == "Loaner 2"

    def test_refuses_a_device_traccar_no_longer_has(self, client, monkeypatch):
        _traccar_configured(monkeypatch, [{"id": 1, "uniqueId": "SOMETHING-ELSE"}])
        ok, message = track.adopt_tracker("GONE-1")
        assert not ok and "no longer lists" in message

    def test_refuses_an_empty_id(self, client):
        ok, message = track.adopt_tracker("")
        assert not ok and "No tracker" in message


class TestTrackersPage:
    def test_page_offers_the_seen_devices(self, logged_in_client, monkeypatch):
        _traccar_configured(monkeypatch, [{"id": 7, "uniqueId": "SEEN-1", "name": "Deck 1",
                                           "lastUpdate": _iso(20)}])
        html = logged_in_client.get("/admin/trackers").get_data(as_text=True)
        assert "Trackers seen on the network" in html
        assert "SEEN-1" in html
        assert 'action="/trackers/adopt"' in html or "/admin/trackers/adopt" in html

    def test_page_says_so_when_there_are_none(self, logged_in_client, monkeypatch):
        _traccar_configured(monkeypatch, [])
        html = logged_in_client.get("/admin/trackers").get_data(as_text=True)
        assert "No unclaimed devices in Traccar" in html

    def test_adopt_route_adds_the_tracker(self, logged_in_client, csrf_post, monkeypatch):
        _traccar_configured(monkeypatch, [{"id": 8, "uniqueId": "SEEN-2", "name": "Deck 2"}])
        resp = csrf_post("/admin/trackers/adopt", {"unique_id": "SEEN-2", "label": "Deck 2"})
        assert resp.status_code in (302, 303)
        assert any(t["unique_id"] == "SEEN-2" for t in track.list_trackers())

    def test_status_api_reports_how_many_are_waiting(self, logged_in_client, monkeypatch):
        _traccar_configured(monkeypatch, [{"id": 9, "uniqueId": "SEEN-3", "lastUpdate": _iso(15)}])
        data = logged_in_client.get("/api/trackers/status").get_json()
        assert data["available"] == 1


class TestUnownedDevices:
    """Traccar hides devices that belong to no user — the ones it auto-registers.

    A tracker switched on for the first time creates exactly such a device: it
    shows in Traccar's web UI only under "All Devices", plain /api/devices omits
    it, and /api/positions will not return its fixes either. Without asking for
    them the pick-list stayed empty however long you waited.
    """

    def test_an_auto_registered_device_is_still_offered(self, client, monkeypatch):
        phone = {"id": 15, "uniqueId": "28426611", "name": "28426611", "lastUpdate": _iso(20)}
        mine = {"id": 1, "uniqueId": "860302050784478", "name": "Mojito", "lastUpdate": _iso(5)}
        _known_tracker("860302050784478")       # already set up in the app
        _traccar_configured(monkeypatch, devices=[phone, mine], owned=[mine])
        devices, error = track.unregistered_traccar_devices()
        assert error is None
        assert [d["unique_id"] for d in devices] == ["28426611"]

    def test_adopting_an_unowned_device_claims_it(self, client, monkeypatch):
        """Otherwise the poller and back-fill never see that boat's positions."""
        phone = {"id": 15, "uniqueId": "28426611", "name": "Phone"}
        linked = []
        _traccar_configured(monkeypatch, devices=[phone], owned=[], users=[{"id": 1, "administrator": True}],
                            linked=linked)
        ok, message = track.adopt_tracker("28426611")
        assert ok, message
        assert linked == [("15", 1)]            # device linked to the token's user

    def test_a_device_we_already_own_is_not_relinked(self, client, monkeypatch):
        owned = {"id": 3, "uniqueId": "OWNED-1", "name": "Ours"}
        linked = []
        _traccar_configured(monkeypatch, devices=[owned], owned=[owned], linked=linked)
        ok, _ = track.adopt_tracker("OWNED-1")
        assert ok and linked == []

    def test_says_so_when_the_account_cannot_be_told(self, client, monkeypatch):
        """Several admins: guessing which account owns the token would be wrong."""
        phone = {"id": 15, "uniqueId": "28426611"}
        _traccar_configured(monkeypatch, devices=[phone], owned=[],
                            users=[{"id": 1, "administrator": True}, {"id": 2, "administrator": True}])
        ok, message = track.adopt_tracker("28426611")
        assert ok and "could not be claimed automatically" in message

    def test_still_adopted_when_claiming_fails(self, client, monkeypatch):
        phone = {"id": 15, "uniqueId": "28426611"}

        def boom(cfg, device_id, user_id):
            raise RuntimeError("403 Forbidden")

        _traccar_configured(monkeypatch, devices=[phone], owned=[], users=[{"id": 1, "administrator": True}])
        monkeypatch.setattr(track, "traccar_link_device", boom)
        ok, message = track.adopt_tracker("28426611")
        assert ok and "could not be claimed" in message
        assert any(t["unique_id"] == "28426611" for t in track.list_trackers())


class TestWhoTheTokenBelongsTo:
    def test_a_single_user_is_unambiguous(self, client, monkeypatch):
        cfg = {**track.track_config(), "base_url": "https://relay/traccar", "token": "t"}
        monkeypatch.setattr(track, "_traccar_request",
                            lambda c, m, p, body=None, timeout=4.0: [{"id": 7, "administrator": False}])
        assert track.traccar_user_id(cfg) == 7

    def test_falls_back_to_the_only_administrator(self, client, monkeypatch):
        cfg = {**track.track_config(), "base_url": "https://relay/traccar", "token": "t"}
        monkeypatch.setattr(track, "_traccar_request", lambda c, m, p, body=None, timeout=4.0: [
            {"id": 1, "administrator": True}, {"id": 2, "administrator": False}])
        assert track.traccar_user_id(cfg) == 1

    def test_gives_up_rather_than_guess(self, client, monkeypatch):
        cfg = {**track.track_config(), "base_url": "https://relay/traccar", "token": "t"}
        monkeypatch.setattr(track, "_traccar_request", lambda c, m, p, body=None, timeout=4.0: [
            {"id": 1, "administrator": True}, {"id": 2, "administrator": True}])
        assert track.traccar_user_id(cfg) is None
