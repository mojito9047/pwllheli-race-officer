"""A phone can ask for notifications, and nothing else can be made of the asking.

Subscribing is the one thing on the public pages that writes anything. A phone
with no login POSTs an address and two keys, and the hut keeps them and later
POSTs to that address. Both halves of that are worth being careful about, on a
PC that also runs the club's racing:

* the request is anonymous, so it cannot carry the token every other POST in
  the app needs, and the routes must be reachable without a login;
* it must not be a way to fill the disk (a size limit, a cap on how many are
  kept, a limit per address);
* and it must not be a way to aim the hut at an address of the caller's choosing
  (only a real browser push service is accepted).

Off until the club switches it on: a new installation offers nothing and accepts
nothing.
"""
from __future__ import annotations

import json
import pathlib
from datetime import datetime

import pytest

import app as ro
from core import notify

UA_PUBLIC = "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4"
AUTH = "BTBZMqHH6r4Tts7J_aSIgg"
ENDPOINT = "https://fcm.googleapis.com/fcm/send/abc"
STATIC = pathlib.Path(__file__).resolve().parent.parent / "static"


def body(endpoint=ENDPOINT, **extra):
    return {"subscription": {"endpoint": endpoint, "keys": {"p256dh": UA_PUBLIC, "auth": AUTH}}, **extra}


@pytest.fixture()
def switched_on(client, monkeypatch):
    """Notifications on in Settings, with a secure public address to send from."""
    with ro.get_db() as db:
        db.execute("INSERT OR REPLACE INTO app_settings (key, value, updated_at) VALUES ('push_enabled','1',?)",
                   (datetime.now().isoformat(timespec="seconds"),))
        db.commit()
    monkeypatch.setattr(notify, "public_base_url", lambda: "https://pro.example.org")
    return client


def rows():
    with notify.get_push_db() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM subscriptions ORDER BY endpoint")]


class TestOffUntilSwitchedOn:
    def test_the_pages_offer_nothing(self, client):
        html = client.get("/public/current").get_data(as_text=True)
        assert "ro-push-key" not in html
        assert 'id="publicNotify" hidden' in html, "the place for the switch is there, empty and hidden"
        assert 'id="publicNotifyPart" hidden' in html, "and so is its heading in the Settings dialog"

    def test_a_subscription_is_not_accepted(self, client):
        resp = client.post("/public/push/subscribe", json=body())
        assert resp.status_code == 404 and resp.get_json()["ok"] is False

    def test_nothing_is_created_on_disk(self, client):
        client.get("/public/current")
        client.post("/public/push/subscribe", json=body())
        assert not notify.PUSH_DB_PATH.exists(), "no key pair is made until notifications are wanted"


class TestSwitchedOn:
    def test_the_pages_hand_out_the_key_and_where_to_send_it(self, switched_on):
        for url in ("/public/current",):
            html = switched_on.get(url).get_data(as_text=True)
            assert f'name="ro-push-key" content="{notify.vapid_keys()[1]}"' in html
            assert 'data-worker="/public/sw.js"' in html
            assert 'data-subscribe="/public/push/subscribe"' in html
            assert 'data-unsubscribe="/public/push/unsubscribe"' in html

    def test_the_race_page_too(self, switched_on):
        from tests.test_competitor_follows_the_current_race import _race
        race_id = _race("Race One")
        html = switched_on.get(f"/public/race/{race_id}").get_data(as_text=True)
        # The key, so that starring a boat here keeps the hut in step; the
        # switch itself is under Settings on the competitor home, and only there.
        assert 'name="ro-push-key"' in html
        assert "publicNotify" not in html

    def test_the_private_key_never_reaches_a_page(self, switched_on):
        private_pem, _public = notify.vapid_keys()
        secret = "".join(private_pem.strip().splitlines()[1:-1])
        for url in ("/public/current", "/public/app.webmanifest", "/public/sw.js"):
            assert secret[:40] not in switched_on.get(url).get_data(as_text=True)

    def test_a_phone_with_no_login_and_no_token_can_subscribe(self, switched_on):
        resp = switched_on.post("/public/push/subscribe",
                                json=body(notices=True, boats=True, boat_keys=["GBR4822R", "gbr 6939r"]))
        assert resp.status_code == 200 and resp.get_json() == {"ok": True}
        (row,) = rows()
        assert row["endpoint"] == ENDPOINT and row["p256dh"] == UA_PUBLIC and row["auth"] == AUTH
        assert (row["notices"], row["boats"]) == (1, 1)
        assert row["boat_keys"] == ",GBR4822R,GBR6939R,", "tidied to the keys the server hands out"

    def test_subscribing_again_updates_rather_than_adds(self, switched_on):
        switched_on.post("/public/push/subscribe", json=body(boat_keys=["GBR4822R"]))
        switched_on.post("/public/push/subscribe", json=body(notices=False, boat_keys=[]))
        (row,) = rows()
        assert (row["notices"], row["boats"], row["boat_keys"]) == (0, 1, "")

    def test_unsubscribing(self, switched_on):
        switched_on.post("/public/push/subscribe", json=body())
        resp = switched_on.post("/public/push/unsubscribe", json={"endpoint": ENDPOINT})
        assert resp.status_code == 200 and rows() == []

    def test_unsubscribing_something_unknown_is_not_an_error(self, switched_on):
        resp = switched_on.post("/public/push/unsubscribe", json={"endpoint": ENDPOINT})
        assert resp.status_code == 200 and resp.get_json() == {"ok": True}

    def test_what_is_kept_about_a_person_is_an_address_and_sail_numbers(self, switched_on):
        switched_on.post("/public/push/subscribe", json=body(boat_keys=["GBR4822R"]),
                         headers={"User-Agent": "A Phone", "CF-Connecting-IP": "203.0.113.9"})
        (row,) = rows()
        assert set(row) == {"endpoint", "p256dh", "auth", "notices", "boats", "boat_keys",
                            "created_at", "updated_at", "last_ok_at"}
        assert "203.0.113.9" not in json.dumps(row) and "A Phone" not in json.dumps(row)


class TestWhatIsRefused:
    def test_an_address_that_is_not_a_browsers_push_service(self, switched_on):
        """Otherwise this is a way to make the hut POST to any address at all."""
        for endpoint in ("https://example.com/collect", "http://fcm.googleapis.com/fcm/send/abc",
                         "https://192.168.1.1/admin", "https://fcm.googleapis.com.evil.example/x", ""):
            resp = switched_on.post("/public/push/subscribe", json=body(endpoint=endpoint))
            assert resp.status_code == 400, endpoint
        assert rows() == []

    def test_keys_no_browser_issued(self, switched_on):
        bad = body()
        bad["subscription"]["keys"]["p256dh"] = "AAAA"
        assert switched_on.post("/public/push/subscribe", json=bad).status_code == 400
        bad = body()
        bad["subscription"]["keys"]["auth"] = "not sixteen bytes"
        assert switched_on.post("/public/push/subscribe", json=bad).status_code == 400
        assert rows() == []

    def test_a_body_that_is_not_a_subscription(self, switched_on):
        assert switched_on.post("/public/push/subscribe", data="hello",
                                content_type="text/plain").status_code == 400
        assert switched_on.post("/public/push/subscribe", json=["a", "list"]).status_code == 400
        assert switched_on.post("/public/push/subscribe", json={"subscription": "a string"}).status_code == 400

    def test_a_body_larger_than_any_subscription(self, switched_on):
        huge = body(boat_keys=["X" * 40] * 200)
        assert switched_on.post("/public/push/subscribe", json=huge).status_code == 413
        assert rows() == []

    def test_more_favourites_than_a_phone_keeps_are_cut_off(self, switched_on):
        keys = [f"GBR{n}" for n in range(40)]
        switched_on.post("/public/push/subscribe", json=body(boat_keys=keys))
        (row,) = rows()
        assert len(row["boat_keys"].strip(",").split(",")) == notify.MAX_BOAT_KEYS

    def test_one_address_cannot_keep_asking(self, switched_on):
        answers = [switched_on.post("/public/push/subscribe", json=body(),
                                    headers={"CF-Connecting-IP": "203.0.113.9"}).status_code
                   for _ in range(notify.SUBSCRIBE_LIMIT + 3)]
        assert answers[:notify.SUBSCRIBE_LIMIT] == [200] * notify.SUBSCRIBE_LIMIT
        assert answers[notify.SUBSCRIBE_LIMIT:] == [429] * 3
        # Somebody else is not held up by it.
        assert switched_on.post("/public/push/subscribe", json=body(),
                                headers={"CF-Connecting-IP": "198.51.100.7"}).status_code == 200

    def test_the_list_cannot_grow_without_limit(self, switched_on, monkeypatch):
        monkeypatch.setattr(notify, "MAX_SUBSCRIPTIONS", 3)
        for n in range(3):
            assert switched_on.post("/public/push/subscribe",
                                    json=body(endpoint=f"{ENDPOINT}{n}")).status_code == 200
        full = switched_on.post("/public/push/subscribe", json=body(endpoint=f"{ENDPOINT}-one-more"))
        assert full.status_code == 400 and len(rows()) == 3
        # A phone already on the list can still change what it wants.
        assert switched_on.post("/public/push/subscribe",
                                json=body(endpoint=f"{ENDPOINT}0", notices=False)).status_code == 200


class TestTheServiceWorker:
    def test_it_is_served_from_under_the_pages_it_looks_after(self, client):
        """A service worker may only control pages at or below its own folder."""
        resp = client.get("/public/sw.js")
        assert resp.status_code == 200 and "javascript" in resp.mimetype
        assert resp.headers["Cache-Control"] == "no-cache"

    def test_it_shows_notifications_and_caches_nothing(self):
        js = (STATIC / "public_sw.js").read_text(encoding="utf-8")
        code = "\n".join(line for line in js.splitlines() if not line.strip().startswith(("*", "/*", "//")))
        assert "addEventListener('push'" in code and "showNotification" in code
        assert "addEventListener('notificationclick'" in code
        assert "addEventListener('fetch'" not in code, "no fetch handler: a page must never come from a cache"
        assert "caches." not in code

    def test_the_race_office_is_not_inside_its_scope(self, client):
        assert client.get("/sw.js").status_code in (302, 401, 404), "there is no worker at the root"

    def test_tapping_a_notification_never_moves_a_race_office_page(self):
        """The race office is at the same address. A notification may bring a
        competitor page forward; it must not take a race officer's screen."""
        js = (STATIC / "public_sw.js").read_text(encoding="utf-8")
        assert "pathname.startsWith('/public/')" in js
        assert "includeUncontrolled" not in js


class TestTheSettingsPage:
    def test_it_is_off_by_default_and_says_so(self, logged_in_client):
        html = logged_in_client.get("/admin/settings").get_data(as_text=True)
        section = html.split('id="notifications"', 1)[1].split("</details>", 1)[0]
        assert "Switched off." in section and "0 phones subscribed." in section
        assert 'name="push_enabled" value="1" >' in section or 'name="push_enabled" value="1">' in section

    def test_ticking_the_box_switches_it_on_and_unticking_switches_it_off(self, logged_in_client):
        from tests.test_club_branding import post
        post(logged_in_client, "/settings/save", {"push_enabled": "1"})
        assert notify.notify_config()["enabled"] is True
        post(logged_in_client, "/settings/save", {})
        assert notify.notify_config()["enabled"] is False

    def test_saving_settings_from_code_that_says_nothing_about_it_keeps_it(self, logged_in_client):
        from tests.test_club_branding import post
        post(logged_in_client, "/settings/save", {"push_enabled": "1"})
        ro.save_app_settings({"club_name": "Liverpool Yacht Club"})
        assert notify.notify_config()["enabled"] is True

    def test_it_says_why_nothing_can_be_sent(self, logged_in_client, monkeypatch):
        from tests.test_club_branding import post
        monkeypatch.setattr(notify, "public_base_url", lambda: "")
        post(logged_in_client, "/settings/save", {"push_enabled": "1"})
        html = logged_in_client.get("/admin/settings").get_data(as_text=True)
        section = html.split('id="notifications"', 1)[1].split("</details>", 1)[0]
        assert "Cannot send yet." in section and "public address" in section

    def test_the_test_button_queues_one_message_for_every_phone(self, logged_in_client, monkeypatch):
        from tests.test_club_branding import post
        monkeypatch.setattr(notify, "public_base_url", lambda: "https://pro.example.org")
        post(logged_in_client, "/settings/save", {"push_enabled": "1"})
        notify.subscribe(ENDPOINT, UA_PUBLIC, AUTH)
        resp = post(logged_in_client, "/settings/notifications/test", {"push_enabled": "1"})
        assert resp.status_code == 302 and resp.headers["Location"].endswith("#notifications")
        queued = notify.NOTIFY_QUEUE.get_nowait()
        assert queued.topic == "everyone" and queued.kind == "test"
        assert "Nothing has happened" in queued.body

    def test_the_test_button_with_nobody_subscribed_sends_nothing(self, logged_in_client, monkeypatch):
        from tests.test_club_branding import post
        monkeypatch.setattr(notify, "public_base_url", lambda: "https://pro.example.org")
        post(logged_in_client, "/settings/notifications/test", {"push_enabled": "1"})
        assert notify.NOTIFY_QUEUE.empty()

    def test_a_race_officer_who_is_not_an_administrator_cannot_send_it(self, client):
        resp = client.post("/settings/notifications/test", data={})
        assert resp.status_code in (302, 400, 401, 403)
        assert notify.NOTIFY_QUEUE.empty()


class TestItIsBackedUp:
    """Every subscription is tied to the club's key pair. Lose the file and every
    phone has to turn notifications on again."""

    def test_the_backup_has_a_section_for_it(self):
        from core import backup, offsite
        assert "notifications" in [s["id"] for s in backup.BACKUP_SECTION_DEFINITIONS]
        assert backup.backup_section_prefixes("notifications") == ["data/push.db"]
        assert backup.restore_member_destination("data/push.db") == notify.PUSH_DB_PATH
        assert "notifications" in offsite.DEFAULT_OFFSITE_SECTIONS
