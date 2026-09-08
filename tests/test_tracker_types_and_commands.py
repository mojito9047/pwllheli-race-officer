"""What kind of tracker each one is, and talking to it from the app.

Two things drove this. The club runs four models with wildly different behaviour — an
ATC700 draining 10%/h beside a GL521MG at 1%/h — and the page that lists them said
nothing about which was which. And every useful thing learned about these units in
August came from sending them commands by hand, outside the app entirely.

The model comes from the first eight digits of the IMEI, the Type Allocation Code the
manufacturer allocates. It is the only field that separates devices Traccar calls the
same thing: the ATC700 and the RUTX50 are both protocol "teltonika", one a battery
asset tracker and the other a mains-powered router.

The command console is administrators-only. The rest of this page is deliberately open
to race officers, because which boat carries which tracker is a race-day job — but a
raw protocol command reconfigures hardware at sea, and the wrong one leaves a unit that
has to come off the boat to be recovered.
"""
from __future__ import annotations

from datetime import datetime

import pytest

import app as ro
from core import track

TOKEN = "test-csrf-token"


def _become_race_officer(client):
    """Log in as a plain race officer instead of the admin the fixture provides."""
    now = datetime.now().isoformat(timespec="seconds")
    with ro.app.app_context():
        with ro.get_db() as db:
            db.execute(
                "INSERT INTO users (username, password_hash, display_name, role,"
                " status, can_set_marks, created_at, updated_at)"
                " VALUES ('ro', ?, 'RO', 'race_officer', 'ACTIVE', 0, ?, ?)",
                (ro.generate_password_hash("ropass123"), now, now))
            db.commit()
    with client.session_transaction() as sess:
        sess["_csrf_token"] = TOKEN
    client.post("/login", data={"_csrf_token": TOKEN, "username": "ro",
                                "password": "ropass123"})
    return client


class TestNamingATypeCode:
    def test_eight_digits_or_nothing(self, client):
        ok, msg = track.upsert_tracker_type("1234567", "Too short")
        assert not ok and "8 digits" in msg
        ok, msg = track.upsert_tracker_type("864864070498856", "That is a whole IMEI")
        assert not ok

    def test_a_model_needs_a_name(self, client):
        ok, msg = track.upsert_tracker_type("12345678", "   ")
        assert not ok and "name" in msg.lower()

    def test_a_new_type_is_used_immediately(self, client):
        """The point of the table: name a model without waiting for a release."""
        assert track.tracker_model("12345678901234x") == "unknown"
        ok, _ = track.upsert_tracker_type("12345678", "Acme Wanderer")
        assert ok
        assert track.tracker_model("123456789012345") == "Acme Wanderer"

    def test_the_table_can_correct_a_built_in(self, client):
        """If a built-in is wrong, the club must be able to fix it in the app rather
        than wait for someone to edit a constant."""
        assert track.tracker_model("864864070498856", "gl200") == "Queclink GL521MG"
        track.upsert_tracker_type("86486407", "Queclink GL521MG rev B")
        assert track.tracker_model("864864070498856", "gl200") == "Queclink GL521MG rev B"

    def test_built_ins_are_listed_even_before_anything_is_saved(self, client):
        tacs = {t["tac"] for t in track.list_tracker_types()}
        assert {"86486407", "86212908", "86030205"} <= tacs


class TestTheTypeShownOnThePage:
    def test_the_column_is_there_with_the_model(self, logged_in_client):
        track.upsert_tracker("862129082306832", label="ATC")
        html = logged_in_client.get("/admin/trackers").get_data(as_text=True)
        assert "<th>Type</th>" in html
        assert "Teltonika ATC700" in html

    def test_an_unknown_type_offers_to_be_named(self, logged_in_client):
        """The person looking at an unfamiliar tracker is the one who knows what it is."""
        track.upsert_tracker("999999999999999", label="Mystery")
        html = logged_in_client.get("/admin/trackers").get_data(as_text=True)
        assert "name it" in html
        assert 'data-tac="99999999"' in html

    def test_two_teltonikas_are_told_apart(self, logged_in_client):
        """Protocol alone calls both of these 'teltonika'. They could hardly be less
        alike, and the page has to say which is which."""
        track.upsert_tracker("862129082306832", label="asset tracker")
        track.upsert_tracker("860302050784478", label="router")
        html = logged_in_client.get("/admin/trackers").get_data(as_text=True)
        assert "Teltonika ATC700" in html and "Teltonika RUTX50" in html


class TestRefusingToBreakATracker:
    @pytest.mark.parametrize("command", [
        "AT+GTRTO=gl521m,4,,,,,,FFFF$",      # Queclink factory reset
        "AT+GTRTO=gl521m,04,,,,,,FFFF$",     # ... padded
        "at+gtrto=gl521m, 4 ,,,,,,FFFF$",    # ... lower case and spaced
        "factoryreset",
        "deleterecords",
    ])
    def test_a_factory_reset_is_refused(self, command):
        """A unit that forgets its server address stops being findable at all, and
        recovering it means getting the tracker off the boat and onto USB."""
        assert track.command_refusal(command)

    def test_a_reboot_is_allowed(self):
        """Sub-command 3 is REBOOT and keeps the configuration; 4 is the destructive
        one. One digit apart, so the distinction is worth a test."""
        assert track.command_refusal("AT+GTRTO=gl521m,3,,,,,,FFFF$") is None
        assert track.command_refusal("cpureset") is None

    def test_ordinary_commands_are_allowed(self):
        assert track.command_refusal("getparam 10100") is None
        assert track.command_refusal("setparam 10050:10;10150:10") is None

    def test_the_protocol_length_limit_is_enforced(self):
        assert track.command_refusal("x" * 161)
        assert track.command_refusal("getparam " + "1;" * 20) is None

    def test_nothing_is_not_a_command(self):
        assert track.command_refusal("   ")

    def test_every_prebuilt_command_passes_its_own_guard(self):
        for proto, templates in track.COMMAND_TEMPLATES.items():
            for t in templates:
                assert track.command_refusal(t["command"]) is None, (proto, t["label"])

    def test_no_prebuilt_writes_only_one_half_of_a_pair(self):
        """The club's SIM roams, so the roaming half of each Teltonika parameter is the
        live one — and a device accepts a write to the dead half, reads it back
        correctly, and ignores it. A template that set one half would look like it
        worked and do nothing."""
        pairs = [("10000", "10100"), ("10005", "10105"), ("10004", "10104"),
                 ("10050", "10150"), ("10055", "10155"), ("10054", "10154")]
        for templates in track.COMMAND_TEMPLATES.values():
            for t in templates:
                for home, roaming in pairs:
                    assert (home + ":" in t["command"]) == (roaming + ":" in t["command"]), \
                        f"{t['label']} sets only one half of {home}/{roaming}"


class TestOnlyAdministratorsMaySendThem:
    def test_the_console_is_offered_to_an_administrator(self, logged_in_client):
        track.upsert_tracker("862129082306832", label="ATC")
        html = logged_in_client.get("/admin/trackers").get_data(as_text=True)
        assert "commandDialog" in html and "tracker-command-open" in html

    def test_a_race_officer_does_not_see_it(self, client):
        """They keep the rest of the page — assigning a boat is their job."""
        track.upsert_tracker("862129082306832", label="ATC")
        officer = _become_race_officer(client)
        html = officer.get("/trackers").get_data(as_text=True)
        assert "commandDialog" not in html
        assert "tracker-command-open" not in html
        assert "Teltonika ATC700" in html       # the Type column is still theirs

    def test_a_race_officer_posting_directly_is_refused(self, client):
        """Hiding the button is not the control; the endpoint is."""
        officer = _become_race_officer(client)
        with officer.session_transaction() as sess:
            sess["_csrf_token"] = TOKEN
        resp = officer.post("/trackers/command",
                            data={"_csrf_token": TOKEN, "unique_id": "862129082306832",
                                  "command": "getparam 10100"},
                            headers={"Accept": "application/json"})
        assert resp.status_code == 403

    def test_a_race_officer_cannot_read_replies_either(self, client):
        officer = _become_race_officer(client)
        resp = officer.get("/trackers/command/replies?unique_id=862129082306832",
                           headers={"Accept": "application/json"})
        assert resp.status_code == 403


class TestSendingOne:
    def test_a_refused_command_never_reaches_traccar(self, logged_in_client, monkeypatch):
        def boom(*a, **k):
            raise AssertionError("a refused command must not reach Traccar")
        monkeypatch.setattr(track, "traccar_list_devices", boom)
        with logged_in_client.session_transaction() as sess:
            sess["_csrf_token"] = TOKEN
        resp = logged_in_client.post(
            "/admin/trackers/command",
            data={"_csrf_token": TOKEN, "unique_id": "862129082306832",
                  "command": "AT+GTRTO=gl521m,4,,,,,,FFFF$"},
            headers={"Accept": "application/json"})
        assert resp.status_code == 200
        assert resp.get_json()["ok"] is False

    def test_a_good_command_is_posted_to_traccar(self, logged_in_client, monkeypatch):
        sent = {}
        monkeypatch.setattr(track, "track_config", lambda: dict(
            enabled=True, base_url="http://x", token="t", poll_seconds=5, retention_days=90,
            sim_enabled=False, finish_horn=False, rounding_radius_m=50, ingest_secret=""))
        monkeypatch.setattr(track, "traccar_list_devices",
                            lambda cfg, include_unowned=False:
                            [{"id": 48, "uniqueId": "862129082306832"}])
        monkeypatch.setattr(track, "_traccar_request",
                            lambda cfg, method, path, body=None, timeout=6.0:
                            sent.update({"path": path, "body": body}) or {})
        with logged_in_client.session_transaction() as sess:
            sess["_csrf_token"] = TOKEN
        resp = logged_in_client.post(
            "/admin/trackers/command",
            data={"_csrf_token": TOKEN, "unique_id": "862129082306832",
                  "command": "getparam 10100"},
            headers={"Accept": "application/json"})
        assert resp.get_json()["ok"] is True
        assert sent["path"] == "/api/commands/send"
        assert sent["body"]["type"] == "custom"
        assert sent["body"]["attributes"]["data"] == "getparam 10100"

    def test_an_unknown_device_is_reported_not_guessed(self, logged_in_client, monkeypatch):
        monkeypatch.setattr(track, "track_config", lambda: dict(
            enabled=True, base_url="http://x", token="t", poll_seconds=5, retention_days=90,
            sim_enabled=False, finish_horn=False, rounding_radius_m=50, ingest_secret=""))
        monkeypatch.setattr(track, "traccar_list_devices", lambda cfg, include_unowned=False: [])
        ok, msg = track.send_tracker_command("862129082306832", "getparam 10100")
        assert not ok and "does not know" in msg

    def test_with_no_traccar_it_says_so(self, client, monkeypatch):
        monkeypatch.setattr(track, "track_config", lambda: dict(
            enabled=False, base_url="", token="", poll_seconds=5, retention_days=90,
            sim_enabled=False, finish_horn=False, rounding_radius_m=50, ingest_secret=""))
        ok, msg = track.send_tracker_command("862129082306832", "getparam 10100")
        assert not ok and "not configured" in msg

    def test_replies_come_back_from_the_event_feed(self, client, monkeypatch):
        """The device answers out of band: Traccar records a commandResult event
        carrying the text, seconds to minutes after the command went out."""
        monkeypatch.setattr(track, "track_config", lambda: dict(
            enabled=True, base_url="http://x", token="t", poll_seconds=5, retention_days=90,
            sim_enabled=False, finish_horn=False, rounding_radius_m=50, ingest_secret=""))
        monkeypatch.setattr(track, "traccar_list_devices",
                            lambda cfg, include_unowned=False:
                            [{"id": 48, "uniqueId": "862129082306832"}])
        monkeypatch.setattr(track, "_traccar_get", lambda cfg, path, timeout=4.0: [
            {"type": "commandResult", "eventTime": "2026-08-20T12:00:00.000+00:00",
             "attributes": {"result": "Param ID:10100 Value:300"}},
            {"type": "deviceOnline", "eventTime": "2026-08-20T12:00:01.000+00:00",
             "attributes": {}},
        ])
        replies = track.tracker_command_results("862129082306832")
        assert len(replies) == 1
        assert replies[0]["result"] == "Param ID:10100 Value:300"

    def test_a_traccar_failure_is_not_an_exception(self, client, monkeypatch):
        """This page is reached during a race."""
        monkeypatch.setattr(track, "track_config", lambda: dict(
            enabled=True, base_url="http://x", token="t", poll_seconds=5, retention_days=90,
            sim_enabled=False, finish_horn=False, rounding_radius_m=50, ingest_secret=""))
        def boom(*a, **k):
            raise RuntimeError("relay down")
        monkeypatch.setattr(track, "traccar_list_devices", boom)
        assert track.tracker_command_results("862129082306832") == []
        ok, msg = track.send_tracker_command("862129082306832", "getparam 1")
        assert not ok and "Could not reach Traccar" in msg


class TestTemplates:
    def test_a_queclink_gets_queclink_commands(self, client, monkeypatch):
        monkeypatch.setattr(track, "tracker_protocols", lambda: {"864864070498856": "gl200"})
        labels = [t["command"] for t in track.command_templates_for("864864070498856")]
        assert any(c.startswith("AT+GTRTO=") for c in labels)
        assert not any(c.startswith("getparam") for c in labels)

    def test_a_teltonika_gets_teltonika_commands(self, client, monkeypatch):
        monkeypatch.setattr(track, "tracker_protocols", lambda: {"862129082306832": "teltonika"})
        labels = [t["command"] for t in track.command_templates_for("862129082306832")]
        assert any(c.startswith("getparam") for c in labels)
        assert not any(c.startswith("AT+GTRTO=") for c in labels)

    def test_every_template_says_what_the_app_will_actually_show(self):
        """Queclink answers a query with a separate +RESP message, and Traccar's decoder
        keeps only "+ACK:GTRTO" — verified against the club's units for a signal-strength
        query, a device-info query and a config read, all of which produced an ACK alone.
        A template that promises "returns the AT+GTCFG line" is therefore lying, and the
        first version of these did. Every one now declares what comes back."""
        for proto, templates in track.COMMAND_TEMPLATES.items():
            for t in templates:
                assert t.get("reply") in ("data", "position", "ack"), (proto, t["label"])

    def test_the_queclink_reads_are_honest_about_being_invisible(self):
        """They still run on the tracker; the answer just cannot be seen from here. The
        label has to say so, because a console that looks like it failed is worse than
        one that explains itself."""
        for t in track.COMMAND_TEMPLATES["gl200"]:
            if t["reply"] == "ack" and "Read" in t["label"]:
                assert "not visible" in t["label"]

    def test_the_console_explains_replies_per_protocol_not_once_for_all(self, logged_in_client):
        """How a reply arrives is a property of the protocol. Stating it once for every
        tracker meant a Teltonika carried a paragraph about a Queclink limitation it does
        not have — explaining a problem the reader has not got is its own confusion. The
        paragraph is now filled in from the device's protocol, so the page must carry the
        protocol to the dialog and leave the text empty by default."""
        track.upsert_tracker("862129082306832", label="ATC")
        html = logged_in_client.get("/admin/trackers").get_data(as_text=True)
        assert 'id="cmdProtocolNote"' in html and "hidden" in html
        assert "data-protocol=" in html
        # the Queclink caveat must not be baked into the page for every tracker
        assert "sends its answer as a separate message" not in html.split("<script")[0]

    def test_a_position_request_is_the_one_queclink_query_that_answers(self, client):
        """Because its answer is a fix rather than text, so it arrives through the
        ordinary position feed instead of the command channel."""
        rtl = [t for t in track.COMMAND_TEMPLATES["gl200"] if t["reply"] == "position"]
        assert len(rtl) == 1
        assert rtl[0]["command"] == "AT+GTRTO=gl521m,1,,,,,,FFFF$"

    def test_an_unknown_protocol_gets_none_rather_than_the_wrong_ones(self, client, monkeypatch):
        """Offering Queclink syntax to a Jimi tracker would be worse than offering
        nothing: it would look authoritative and do nothing."""
        monkeypatch.setattr(track, "tracker_protocols", lambda: {"864032050547569": "gt06"})
        assert track.command_templates_for("864032050547569") == []
