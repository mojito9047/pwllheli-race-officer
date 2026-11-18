"""What counts as news, and that it is told once, to the right phones.

People following a race want to be told when it starts, when it is postponed,
when their boat finishes and when the results are up. The watcher in
``core/notify.py`` works those out by comparing the race office's own records
with what it saw a few seconds earlier: it is never called by the start
sequence or the finish button, so it can never hold one up.

That design has three ways to go wrong, and each has cost real systems their
users' patience, so each is pinned here:

* **announcing the past** -- on a restart, or when notifications are first
  switched on, everything already in the database looks new;
* **announcing something that is then taken back** -- an automatic GPS finish
  can be wrong, the race officer removes it, and a notification cannot be
  unsent;
* **announcing twice** -- a second fleet's start, an edited finish time, a race
  re-opened and closed again.

The first half drives the watcher with plain lists and a clock. The second half
runs it against the real tables, written the way the app writes them.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

import app as ro
from core import notify, webpush
from core.eventlog import log_event

UA_PUBLIC = "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4"
AUTH = "BTBZMqHH6r4Tts7J_aSIgg"


def entry(entry_id, status="RACING", finish="", race_id=1, name="MOJITO", sail="GBR4822R"):
    return {"entry_id": entry_id, "race_id": race_id, "race_name": "Ras Enlli", "boat_name": name,
            "sail_no": sail, "status": status, "finish_time": finish}


class World:
    """The race office's records, as lists a test can change between looks."""

    def __init__(self, entries=(), events=()):
        self.entries = list(entries)
        self.events = list(events)

    def watcher(self):
        return notify.Watcher(
            max_event_id=lambda: max([e["id"] for e in self.events], default=0),
            events_after=lambda after: [e for e in self.events if e["id"] > after],
            had_earlier_start=lambda race_id, before: any(
                e["race_id"] == race_id and e["id"] < before and e["event_type"] == "horn"
                and e["label"].endswith("Start signal") for e in self.events),
            active_entries=lambda now: [dict(e) for e in self.entries])

    def event(self, event_type, label, source="manual", details=None, race_id=1, at="2026-08-08T20:05:00"):
        self.events.append({"id": len(self.events) + 1, "race_id": race_id, "race_name": "Ras Enlli",
                            "event_time": at, "event_type": event_type, "label": label, "source": source,
                            "details": json.dumps(details or {})})


class TestNothingOldIsAnnounced:
    def test_the_first_look_is_the_baseline(self):
        world = World([entry(1, "FINISHED", "2026-08-09T01:18:12")])
        world.event("horn", "Start signal", "central-start-sequence")
        watcher = world.watcher()
        assert watcher.poll(1000.0) == []
        assert watcher.poll(2000.0) == [], "and it stays unannounced however long it waits"

    def test_a_finish_that_was_already_there_is_not_news_after_a_restart(self):
        world = World([entry(1, "FINISHED", "2026-08-09T01:18:12"), entry(2)])
        watcher = world.watcher()            # the app restarting mid-race
        watcher.poll(0.0)
        world.entries[1] = entry(2, "FINISHED", "2026-08-09T02:20:24", name="FINALLY", sail="GBR6939R")
        watcher.poll(5.0)
        told = watcher.poll(5.0 + notify.FINISH_HOLD_S)
        assert [n.body for n in told if n.kind == "finish"] == ["FINALLY finished at 02:20:24."]

    def test_a_result_typed_in_for_a_boat_never_seen_racing_is_not_a_finish(self):
        """The race office entering a past race's results is not boats crossing a line."""
        world = World([entry(1)])
        watcher = world.watcher()
        watcher.poll(0.0)
        world.entries.append(entry(9, "FINISHED", "2026-08-07T15:00:00", race_id=2, name="OLD", sail="1"))
        watcher.poll(5.0)
        assert [n for n in watcher.poll(500.0) if n.kind == "finish"] == []


class TestABoatFinishing:
    def _racing(self):
        world = World([entry(1), entry(2, name="FINALLY", sail="GBR6939R")])
        watcher = world.watcher()
        watcher.poll(0.0)
        return world, watcher

    def test_it_is_told_to_that_boats_followers_once_it_has_stood_a_moment(self):
        world, watcher = self._racing()
        world.entries[0] = entry(1, "FINISHED", "2026-08-09T01:18:12")
        assert watcher.poll(5.0) == [], "not the instant it appears"
        assert watcher.poll(5.0 + notify.FINISH_HOLD_S - 1) == []
        (notice,) = watcher.poll(5.0 + notify.FINISH_HOLD_S)
        assert notice.topic == "boats" and notice.boat_key == "GBR4822R"
        assert notice.title == "Ras Enlli" and notice.body == "MOJITO finished at 01:18:12."

    def test_a_finish_the_race_officer_takes_back_is_never_sent(self):
        """An automatic GPS finish on the wrong crossing, removed within the hold."""
        world, watcher = self._racing()
        world.entries[0] = entry(1, "FINISHED", "2026-08-09T01:10:00")
        watcher.poll(5.0)
        world.entries[0] = entry(1)                      # removed
        assert watcher.poll(20.0) == []
        assert watcher.poll(500.0) == []
        world.entries[0] = entry(1, "FINISHED", "2026-08-09T01:18:12")    # the real one
        watcher.poll(600.0)
        (notice,) = watcher.poll(600.0 + notify.FINISH_HOLD_S)
        assert notice.body == "MOJITO finished at 01:18:12."

    def test_it_is_told_once_however_the_time_is_corrected_afterwards(self):
        world, watcher = self._racing()
        world.entries[0] = entry(1, "FINISHED", "2026-08-09T01:18:12")
        watcher.poll(5.0)
        assert len(watcher.poll(5.0 + notify.FINISH_HOLD_S)) == 1
        world.entries[0] = entry(1, "FINISHED", "2026-08-09T01:18:30")    # the race officer edits it
        assert watcher.poll(200.0) == [] and watcher.poll(400.0) == []

    def test_a_retirement_is_not_a_finish(self):
        world, watcher = self._racing()
        world.entries[0] = entry(1, "RET", "")
        watcher.poll(5.0)
        assert [n for n in watcher.poll(500.0) if n.kind == "finish"] == []

    def test_a_boat_with_no_sail_number_is_followed_by_name(self):
        world = World([entry(1, name="Sgrech Bach", sail=""), entry(2)])
        watcher = world.watcher()
        watcher.poll(0.0)
        world.entries[0] = entry(1, "FINISHED", "2026-08-09T14:02:10", name="Sgrech Bach", sail="")
        watcher.poll(5.0)
        (notice,) = watcher.poll(5.0 + notify.FINISH_HOLD_S)
        assert notice.boat_key == "NAMESGRECHBACH"


class TestTheLastBoatHome:
    def test_results_are_announced_once_everyone_is_in(self):
        world = World([entry(1), entry(2, name="FINALLY", sail="GBR6939R")])
        watcher = world.watcher()
        watcher.poll(0.0)
        world.entries[0] = entry(1, "FINISHED", "2026-08-09T01:18:12")
        watcher.poll(5.0)
        told = watcher.poll(100.0)
        assert [n.kind for n in told] == ["finish"], "one boat still racing: no results yet"
        world.entries[1] = entry(2, "FINISHED", "2026-08-09T02:20:24", name="FINALLY", sail="GBR6939R")
        watcher.poll(200.0)
        told = watcher.poll(200.0 + notify.RESULTS_HOLD_S)
        results = [n for n in told if n.kind == "results"]
        assert len(results) == 1 and results[0].topic == "notices"
        assert "Provisional results" in results[0].body
        assert [n for n in watcher.poll(900.0) if n.kind == "results"] == [], "and only once"

    def test_not_if_somebody_is_put_back_racing_within_the_hold(self):
        world = World([entry(1)])
        watcher = world.watcher()
        watcher.poll(0.0)
        world.entries[0] = entry(1, "FINISHED", "2026-08-09T01:18:12")
        watcher.poll(5.0)
        world.entries[0] = entry(1)
        assert [n for n in watcher.poll(500.0) if n.kind == "results"] == []

    def test_a_race_that_was_already_over_does_not_announce_results_on_restart(self):
        world = World([entry(1, "FINISHED", "2026-08-09T01:18:12")])
        watcher = world.watcher()
        watcher.poll(0.0)
        assert watcher.poll(500.0) == []


class TestTheRaceLog:
    def _watching(self):
        world = World([entry(1)])
        watcher = world.watcher()
        watcher.poll(0.0)
        return world, watcher

    def test_the_start(self):
        world, watcher = self._watching()
        world.event("horn", "Start signal", "central-start-sequence", at="2026-08-08T20:05:00")
        (notice,) = watcher.poll(5.0)
        assert (notice.topic, notice.kind, notice.body) == ("notices", "start", "Started at 20:05.")
        assert notice.urgency == "high"
        assert watcher.poll(10.0) == [], "an event is read once"

    def test_the_other_guns_of_the_sequence_are_not_the_start(self):
        world, watcher = self._watching()
        for label in ("Warning signal", "Preparatory signal", "One minute signal"):
            world.event("horn", label, "central-start-sequence")
        world.event("horn", "Finish horn", "manual")
        assert watcher.poll(5.0) == []

    def test_a_second_fleets_start_is_the_same_race_still_starting(self):
        world, watcher = self._watching()
        world.event("horn", "Start 1 - Start signal", "central-start-sequence", at="2026-08-08T20:05:00")
        assert len(watcher.poll(5.0)) == 1
        world.event("horn", "Start 2 - Start signal", "central-start-sequence", at="2026-08-08T20:10:00")
        assert watcher.poll(10.0) == []

    def test_a_pursuit_races_later_boats_are_not_each_a_start(self):
        world, watcher = self._watching()
        world.event("horn", "Start signal", "central-start-sequence")
        watcher.poll(5.0)
        world.event("horn", "Pursuit start 14:07:30", "central-start-sequence")
        assert watcher.poll(10.0) == []

    def test_a_postponement_and_its_ending(self):
        world, watcher = self._watching()
        world.event("postpone", "Postponed: AP up, two sounds", details={"kind": "ashore", "flag": "AP"})
        (up,) = watcher.poll(5.0)
        assert up.body == "Postponed. AP is flying." and up.urgency == "high"
        world.event("postpone", "Postponement ending: AP down at 14:29, one sound; warning signal 14:30",
                    details={"warning_time": "2026-08-08T14:30:00", "ends_at": "2026-08-08T14:29:00"})
        (ending,) = watcher.poll(10.0)
        assert ending.body == "Postponement ending. Warning signal at 14:30."
        # The flag coming down, and the one-minute call, add nothing to that.
        world.event("postpone", "AP coming down in one minute", "central-start-sequence")
        world.event("postpone", "AP down, one sound", "central-start-sequence")
        assert watcher.poll(15.0) == []

    def test_a_shortened_course_and_its_being_cleared(self):
        world, watcher = self._watching()
        world.event("shorten", "Shortened course at mark 4")
        (notice,) = watcher.poll(5.0)
        assert notice.body == "Shortened course at mark 4."
        world.event("shorten", "Shortened course cleared")
        (cleared,) = watcher.poll(10.0)
        assert cleared.body == "The course is no longer shortened."

    def test_abandonment(self):
        world, watcher = self._watching()
        world.event("abandon", "Abandonment")
        (notice,) = watcher.poll(5.0)
        assert notice.body == "Race abandoned." and notice.urgency == "high"

    def test_everything_else_in_the_log_is_not_news(self):
        world, watcher = self._watching()
        world.event("audio", "Course announcement", "central-start-sequence")
        world.event("recall", "General recall")
        world.event("finish", "MOJITO", "gps-auto", {"entry_id": 1})    # told from the entries, held
        world.event("gps", "Back-filled 3 missed position(s) from Traccar")
        assert watcher.poll(5.0) == []


class TestWhatAPhoneIsSent:
    def test_the_message_opens_the_race(self):
        notice = notify.Notice("notices", 61, "ISORA CW5 Night Race", "Started at 20:05.", kind="start")
        message = json.loads(notice.payload())
        assert message == {"title": "ISORA CW5 Night Race", "body": "Started at 20:05.",
                           "url": "/public/race/61", "tag": "r61-start"}

    def test_two_boats_finishing_do_not_replace_each_other_on_the_phone(self):
        one = notify.Notice("boats", 61, "r", "b", kind="finish", entry_id=319)
        two = notify.Notice("boats", 61, "r", "b", kind="finish", entry_id=321)
        assert one.tag() != two.tag()

    def test_a_welsh_name_survives(self):
        notice = notify.Notice("notices", 5, "Ras Ynys Enlli", "Tŷ Gwyn finished at 14:02:10.")
        assert json.loads(notice.payload().decode("utf-8"))["body"] == "Tŷ Gwyn finished at 14:02:10."


# --------------------------------------------------------------------------- against the real tables

def NOW():
    """The real time. Against the real tables the watcher asks "which races are
    recent?", so a clock starting at nought would make 1970 the present."""
    import time
    return time.time()


def _race(name="Ras Enlli", hours_ago=1.0):
    now = datetime.now()
    with ro.get_db() as db:
        cur = db.execute("INSERT INTO races (name, course_no, course_set, start_time, notes, created_at)"
                         " VALUES (?,1,1,?,'',?)",
                         (name, (now - timedelta(hours=hours_ago)).isoformat(timespec="seconds"),
                          now.isoformat(timespec="seconds")))
        race_id = int(cur.lastrowid)
        ids = []
        for boat, sail in (("MOJITO", "GBR4822R"), ("FINALLY", "GBR 6939R")):
            cur = db.execute("INSERT INTO entries (race_id, boat_name, sail_no, status) VALUES (?,?,?,'RACING')",
                             (race_id, boat, sail))
            ids.append(int(cur.lastrowid))
        db.commit()
    return race_id, ids


def _finish(entry_id, clock="14:02:10"):
    with ro.get_db() as db:
        db.execute("UPDATE entries SET status = 'FINISHED', finish_time = ? WHERE id = ?",
                   (f"{datetime.now():%Y-%m-%d}T{clock}", entry_id))
        db.commit()


class TestAgainstTheRealTables:
    def test_a_race_from_start_to_results(self, client):
        race_id, (mojito, finally_) = _race()
        watcher = notify.Watcher()
        t0 = NOW()
        assert watcher.poll(t0) == []

        log_event(race_id, "horn", "Start signal", "central-start-sequence", {})
        (start,) = watcher.poll(t0 + 5.0)
        assert start.kind == "start" and start.title == "Ras Enlli" and start.race_id == race_id

        _finish(mojito, "14:02:10")
        watcher.poll(t0 + 10.0)
        (finish,) = watcher.poll(t0 + 10.0 + notify.FINISH_HOLD_S)
        assert finish.body == "MOJITO finished at 14:02:10." and finish.boat_key == "GBR4822R"

        _finish(finally_, "14:09:45")
        watcher.poll(t0 + 100.0)
        told = watcher.poll(t0 + 100.0 + notify.RESULTS_HOLD_S)
        assert sorted(n.kind for n in told) == ["finish", "results"]
        # The key is the one the public page hands out for the same boat.
        assert [n.boat_key for n in told if n.kind == "finish"] == ["GBR6939R"]

    def test_a_race_from_last_month_is_history(self, client):
        """Correcting an old result must not tell anybody their boat has just finished."""
        _race_id, (mojito, _other) = _race(hours_ago=24 * 30)
        watcher = notify.Watcher()
        t0 = NOW()
        watcher.poll(t0)
        _finish(mojito)
        watcher.poll(t0 + 5.0)
        assert watcher.poll(t0 + 500.0) == []

    def test_an_event_with_no_race_is_ignored(self, client):
        _race()
        watcher = notify.Watcher()
        t0 = NOW()
        watcher.poll(t0)
        log_event(None, "gps", "Back-filled 3 missed position(s) from Traccar")
        assert watcher.poll(t0 + 5.0) == []


# --------------------------------------------------------------------------- who is told

def _subscribe(n, **kwargs):
    notify.subscribe(f"https://fcm.googleapis.com/fcm/send/phone{n}", UA_PUBLIC, AUTH, **kwargs)


class _FakePush:
    def __init__(self, answers=None):
        self.sent = []
        self.answers = answers or {}

    def __call__(self, sub, payload, **kwargs):
        self.sent.append((sub["endpoint"].rsplit("/", 1)[1], json.loads(payload), kwargs))
        return self.answers.get(sub["endpoint"].rsplit("/", 1)[1], webpush.PushResult(201, True, False, False))


@pytest.fixture()
def base_url(monkeypatch):
    monkeypatch.setattr(notify, "public_base_url", lambda: "https://pro.example.org")


class TestWhoIsTold:
    def test_a_race_notice_goes_to_those_who_asked_for_notices(self, client, base_url):
        _subscribe(1)
        _subscribe(2, notices=False, boat_keys=["GBR4822R"])
        push = _FakePush()
        outcome = notify.deliver(notify.Notice("notices", 61, "Ras Enlli", "Started at 20:05."), send=push)
        assert outcome["sent"] == 1 and [who for who, _m, _k in push.sent] == ["phone1"]

    def test_a_finish_goes_to_that_boats_followers_only(self, client, base_url):
        _subscribe(1, boat_keys=["GBR4822R", "GBR6939R"])
        _subscribe(2, boat_keys=["GBR6939R"])
        _subscribe(3, boat_keys=["GBR4822R"], boats=False)     # has the boat, turned boats off
        _subscribe(4)                                          # follows nobody
        push = _FakePush()
        notify.deliver(notify.Notice("boats", 61, "Ras Enlli", "MOJITO finished at 01:18:12.",
                                     kind="finish", boat_key="GBR4822R", entry_id=319), send=push)
        assert [who for who, _m, _k in push.sent] == ["phone1"]

    def test_one_sail_number_is_not_matched_inside_another(self, client, base_url):
        _subscribe(1, boat_keys=["GBR48"])
        push = _FakePush()
        notify.deliver(notify.Notice("boats", 61, "r", "b", boat_key="GBR4822R"), send=push)
        assert push.sent == []

    def test_a_phone_that_has_gone_is_forgotten(self, client, base_url):
        _subscribe(1)
        _subscribe(2)
        push = _FakePush({"phone1": webpush.PushResult(410, False, True, False, "gone")})
        outcome = notify.deliver(notify.Notice("notices", 61, "r", "b"), send=push)
        assert outcome["gone"] == 1 and outcome["sent"] == 1
        assert notify.subscription_count() == 1

    def test_a_refused_token_forgets_nobody(self, client, base_url):
        """403 is about the sender. Every phone would answer the same."""
        _subscribe(1)
        _subscribe(2)
        refused = webpush.PushResult(403, False, False, False, "bad token")
        push = _FakePush({"phone1": refused, "phone2": refused})
        outcome = notify.deliver(notify.Notice("notices", 61, "r", "b"), send=push)
        assert outcome["failed"] == 2 and "403" in outcome["error"]
        assert notify.subscription_count() == 2

    def test_a_busy_push_service_is_tried_again_for_that_phone_only(self, client, base_url):
        _subscribe(1)
        _subscribe(2)
        push = _FakePush({"phone2": webpush.PushResult(503, False, False, True, "busy")})
        outcome = notify.deliver(notify.Notice("notices", 61, "r", "b"), send=push)
        assert outcome["sent"] == 1
        assert [s["endpoint"].rsplit("/", 1)[1] for s in outcome["again"]] == ["phone2"]

    def test_it_is_signed_with_the_clubs_key_and_the_public_address(self, client, base_url):
        _subscribe(1)
        push = _FakePush()
        notify.deliver(notify.Notice("notices", 61, "r", "b", kind="start", urgency="high", ttl=1800), send=push)
        (_who, _message, kwargs), = push.sent
        private_pem, public = notify.vapid_keys()
        assert kwargs["private_pem"] == private_pem and kwargs["public_key"] == public
        assert kwargs["subject"] == "https://pro.example.org"
        assert (kwargs["urgency"], kwargs["ttl"], kwargs["topic"]) == ("high", 1800, "r61-start")

    def test_the_test_message_goes_to_every_phone(self, client, base_url):
        _subscribe(1, notices=False, boats=False)
        _subscribe(2)
        push = _FakePush()
        outcome = notify.send_test(send=push)
        assert outcome["sent"] == 2
        assert all("Test notification" in message["body"] for _w, message, _k in push.sent)
        assert notify.notify_runtime_status()["last"]["kind"] == "test"


class TestItDoesNothingWhileThereIsNobodyToTell:
    """An installation that never switches notifications on should never read the
    race database on their account."""

    @pytest.fixture()
    def no_reading(self, monkeypatch):
        def refuse(*args, **kwargs):
            raise AssertionError("the watcher read the race records with nobody to tell")
        for reader in ("_read_max_event_id", "_read_events_after", "_read_active_entries"):
            monkeypatch.setattr(notify, reader, refuse)
        # Watcher's defaults were bound when the class was made.
        monkeypatch.setattr(notify.Watcher, "poll", refuse)

    def _switch_on(self):
        with ro.get_db() as db:
            db.execute("INSERT OR REPLACE INTO app_settings (key, value, updated_at) VALUES ('push_enabled','1',?)",
                       (datetime.now().isoformat(timespec="seconds"),))
            db.commit()

    def test_switched_off(self, client, base_url, no_reading):
        _subscribe(1)
        assert notify.watch_step(None) is None

    def test_switched_on_with_nobody_subscribed(self, client, base_url, no_reading):
        self._switch_on()
        assert notify.watch_step(None) is None

    def test_switched_on_but_unable_to_send(self, client, monkeypatch, no_reading):
        self._switch_on()
        _subscribe(1)
        monkeypatch.setattr(notify, "public_base_url", lambda: "")
        assert notify.watch_step(None) is None

    def test_with_somebody_to_tell_it_watches_and_queues(self, client, base_url):
        self._switch_on()
        _subscribe(1)
        race_id, _entries = _race()
        watcher = notify.watch_step(None)
        assert isinstance(watcher, notify.Watcher) and notify.NOTIFY_QUEUE.empty(), "the first look is the baseline"
        log_event(race_id, "horn", "Start signal", "central-start-sequence", {})
        assert notify.watch_step(watcher) is watcher
        assert notify.NOTIFY_QUEUE.get_nowait().kind == "start"

    def test_what_happened_while_it_was_off_is_not_announced_when_it_comes_back(self, client, base_url):
        self._switch_on()
        _subscribe(1)
        race_id, _entries = _race()
        watcher = notify.watch_step(None)
        notify.unsubscribe("https://fcm.googleapis.com/fcm/send/phone1")
        assert notify.watch_step(watcher) is None, "nobody left: the watcher is dropped"
        log_event(race_id, "horn", "Start signal", "central-start-sequence", {})     # nobody to tell
        _subscribe(2)
        assert isinstance(notify.watch_step(None), notify.Watcher)
        assert notify.NOTIFY_QUEUE.empty(), "the start that nobody was subscribed for stays untold"


class TestTheClubsKeyPair:
    def test_it_is_made_once_and_kept(self, client):
        first = notify.vapid_keys()
        assert notify.vapid_keys() == first
        assert webpush.public_key_from_private(first[0]) == first[1]

    def test_it_survives_the_process_restarting(self, client, monkeypatch):
        first = notify.vapid_keys()
        monkeypatch.setattr(notify, "PUSH_DB_INITIALIZED", False)      # a new process, the same file
        assert notify.vapid_keys() == first


class TestOffUntilSwitchedOn:
    def test_a_new_installation_offers_nothing(self, client):
        assert notify.notify_config() == {"enabled": False}
        assert notify.public_config() == {"enabled": False, "key": ""}

    def test_on_but_with_no_secure_address_it_still_offers_nothing(self, client, monkeypatch):
        with ro.get_db() as db:
            db.execute("INSERT OR REPLACE INTO app_settings (key, value, updated_at) VALUES ('push_enabled','1',?)",
                       (datetime.now().isoformat(timespec="seconds"),))
            db.commit()
        monkeypatch.setattr(notify, "public_base_url", lambda: "")
        assert notify.notify_config()["enabled"] is True
        assert notify.public_config() == {"enabled": False, "key": ""}
        assert "public address" in notify.ready()[1]

    def test_on_and_reachable_it_hands_out_the_public_key(self, client, base_url):
        with ro.get_db() as db:
            db.execute("INSERT OR REPLACE INTO app_settings (key, value, updated_at) VALUES ('push_enabled','1',?)",
                       (datetime.now().isoformat(timespec="seconds"),))
            db.commit()
        config = notify.public_config()
        assert config["enabled"] is True and config["key"] == notify.vapid_keys()[1]
