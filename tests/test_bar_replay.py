"""Replaying a race on the clubhouse display: what is in one, and how long.

The club would like to watch a race back in the bar after sailing. The display
already knows how to draw a race at an arbitrary moment -- that is how the live
view works -- so a replay is a different clock and a list of video, not a new
renderer.

The clock itself lives in the browser, because only the browser knows how a
video is really playing. What is decided here is what a replay contains, which
turned out to be the part with a trap in it.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import app as ro  # noqa: E402
from core import barreplay  # noqa: E402


@pytest.fixture(autouse=True)
def clean_selection(tmp_path, monkeypatch):
    """The selection is a file; give each test its own."""
    monkeypatch.setattr(barreplay, "BAR_REPLAY_PATH", tmp_path / "bar_replay.json")
    yield


def make_race(minutes_ago=120, name="Replay Test"):
    warning = datetime.now() - timedelta(minutes=minutes_ago)
    with ro.get_db() as db:
        cur = db.execute(
            "INSERT INTO races (name, class_name, course_no, start_time, rating_rule, notes,"
            " created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (name, "", 1, warning.isoformat(timespec="seconds"), "DUAL", "",
             datetime.now().isoformat(timespec="seconds")))
        db.commit()
        return int(cur.lastrowid), warning


def add_finish(race_id, boat, at):
    with ro.get_db() as db:
        db.execute("INSERT INTO entries (race_id, boat_name, sail_no, class_name, rating,"
                   " status, finish_time) VALUES (?, ?, ?, 'IRC1', 1.0, 'FINISHED', ?)",
                   (race_id, boat, "GBR1", at.isoformat(timespec="seconds")))
        db.commit()


def add_clip(race_id, at, kind="finish", status="ready", pre=60, post=60, label="",
             public_url="", public_status=""):
    with ro.get_db() as db:
        cur = db.execute(
            "INSERT INTO video_clips (race_id, clip_type, event_time, pre_seconds,"
            " post_seconds, status, label, message, public_url, public_status,"
            " created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?, ?)",
            (race_id, kind, at.isoformat(timespec="seconds"), pre, post, status, label,
             public_url, public_status, datetime.now().isoformat(timespec="seconds"),
             datetime.now().isoformat(timespec="seconds")))
        db.commit()
        return int(cur.lastrowid)


def race_row(race_id):
    with ro.get_db() as db:
        return db.execute("SELECT * FROM races WHERE id = ?", (race_id,)).fetchone()


class TestChoosingARace:
    def test_nothing_is_being_replayed_to_begin_with(self, client):
        assert barreplay.current_replay()["race_id"] is None

    def test_a_race_can_be_chosen_and_stopped(self, client):
        barreplay.start_replay(7, actor="anne")
        chosen = barreplay.current_replay()
        assert chosen["race_id"] == 7 and chosen["by"] == "anne"
        barreplay.stop_replay()
        assert barreplay.current_replay()["race_id"] is None

    def test_choosing_another_replaces_the_first(self, client):
        """One television, one replay."""
        barreplay.start_replay(7)
        barreplay.start_replay(9)
        assert barreplay.current_replay()["race_id"] == 9

    def test_a_selection_nobody_stopped_expires(self, client):
        """A screen should not still be offering to replay Saturday on Tuesday
        because somebody pressed a button and went home."""
        barreplay.start_replay(7)
        stale = (datetime.now() - timedelta(seconds=barreplay.STALE_AFTER_S + 60))
        barreplay.BAR_REPLAY_PATH.write_text(
            '{"race_id": 7, "started_at": "%s"}' % stale.isoformat(timespec="seconds"),
            encoding="utf-8")
        assert barreplay.current_replay()["race_id"] is None

    def test_an_unreadable_file_is_not_a_broken_page(self, client):
        barreplay.BAR_REPLAY_PATH.write_text("{ not json", encoding="utf-8")
        assert barreplay.current_replay()["race_id"] is None


class TestWhatIsInAReplay:
    def test_it_runs_from_the_warning_signal_to_the_last_finish(self, client):
        """The warning is where it starts being worth watching: the flags go up
        and the fleet forms up on the line."""
        race_id, warning = make_race()
        first = warning + timedelta(minutes=40)
        last = warning + timedelta(minutes=52)
        add_finish(race_id, "Early", first)
        add_finish(race_id, "Late", last)
        plan = barreplay.replay_plan(race_row(race_id))
        assert plan["from_ts"] == pytest.approx(warning.timestamp(), abs=1)
        assert plan["to_ts"] == pytest.approx(last.timestamp(), abs=1)

    def test_a_race_nobody_finished_has_nothing_to_replay(self, client):
        race_id, _ = make_race()
        assert barreplay.replay_plan(race_row(race_id)) is None
        assert barreplay.replay_is_possible(race_row(race_id)) is False

    def test_nor_has_a_race_with_no_start_time(self, client):
        race_id, warning = make_race()
        add_finish(race_id, "Boat", warning + timedelta(minutes=30))
        with ro.get_db() as db:
            db.execute("UPDATE races SET start_time = '' WHERE id = ?", (race_id,))
            db.commit()
        assert barreplay.replay_plan(race_row(race_id)) is None

    def test_clips_that_were_never_built_are_left_out(self, client):
        """A replay that stops for a video which never arrives is worse than one
        that does not stop."""
        race_id, warning = make_race()
        add_finish(race_id, "Boat", warning + timedelta(minutes=30))
        add_clip(race_id, warning + timedelta(minutes=5), status="ready")
        add_clip(race_id, warning + timedelta(minutes=15), status="pending")
        add_clip(race_id, warning + timedelta(minutes=25), status="error")
        plan = barreplay.replay_plan(race_row(race_id))
        assert len(plan["clips"]) == 1

    def test_the_clips_are_in_the_order_they_happened(self, client):
        race_id, warning = make_race()
        add_finish(race_id, "Boat", warning + timedelta(minutes=40))
        for minutes in (30, 5, 18):
            add_clip(race_id, warning + timedelta(minutes=minutes))
        plan = barreplay.replay_plan(race_row(race_id))
        assert [c["starts_at"] for c in plan["clips"]] == sorted(
            c["starts_at"] for c in plan["clips"])

    def test_a_clip_carries_the_footage_it_covers_not_just_its_moment(self, client):
        """The display holds the clock across the whole clip, so it needs both
        ends: a finish clip is the minute before the boat crossed as well.

        Its near end is where the file's own footage starts, which is a little
        before the minute that was asked for -- see
        TestWhereAClipsFootageReallyStarts."""
        race_id, warning = make_race()
        add_finish(race_id, "Boat", warning + timedelta(minutes=40))
        at = warning + timedelta(minutes=20)
        add_clip(race_id, at, pre=45, post=30)
        clip = barreplay.replay_plan(race_row(race_id))["clips"][0]
        overhang = barreplay._segment_seconds() / 2.0
        assert clip["starts_at"] == pytest.approx(at.timestamp() - 45 - overhang, abs=1)
        assert clip["ends_at"] == pytest.approx(at.timestamp() + 30, abs=1)
        assert clip["seconds"] == pytest.approx(75 + overhang, abs=1)

    def test_a_published_clip_is_played_from_cloudflare(self, client):
        """Twenty minutes of video is not something to pull off the hut's uplink
        when the same file is already on a CDN."""
        race_id, warning = make_race()
        add_finish(race_id, "Boat", warning + timedelta(minutes=40))
        add_clip(race_id, warning + timedelta(minutes=10),
                 public_url="https://cdn.example/clip.mp4", public_status="ready")
        assert barreplay.replay_plan(race_row(race_id))["clips"][0]["url"] \
            == "https://cdn.example/clip.mp4"

    def test_but_one_that_has_not_been_published_comes_from_the_app(self, client):
        race_id, warning = make_race()
        add_finish(race_id, "Boat", warning + timedelta(minutes=40))
        clip_id = add_clip(race_id, warning + timedelta(minutes=10),
                           public_url="https://cdn.example/half.mp4", public_status="pending")
        assert barreplay.replay_plan(race_row(race_id))["clips"][0]["url"] \
            == f"/public/video/clip/{clip_id}"


class TestTheSameWaterOverAndOver:
    """Found by running this against the club's own races rather than made-up
    ones: race 69 was six minutes long and had twenty minutes of clip. Nine
    boats finishing within a few minutes of each other each get a clip with a
    minute either side, so the clips are mostly the same video. Played in full
    that is a replay longer than the race, showing the same stretch of water
    nine times.
    """

    def test_clips_that_add_nothing_new_are_dropped(self, client):
        race_id, warning = make_race()
        finish = warning + timedelta(minutes=30)
        add_finish(race_id, "Boat", finish)
        # Nine boats over ninety seconds: every clip overlaps its neighbour.
        for offset in range(0, 90, 10):
            add_clip(race_id, finish + timedelta(seconds=offset), label=f"boat {offset}")
        clips = barreplay.replay_plan(race_row(race_id))["clips"]
        assert len(clips) < 4, f"{len(clips)} near-identical clips would be played"

    def test_and_the_one_kept_says_who_else_it_shows(self, client):
        """So the display can caption it honestly rather than naming one boat."""
        race_id, warning = make_race()
        finish = warning + timedelta(minutes=30)
        add_finish(race_id, "Boat", finish)
        add_clip(race_id, finish, label="Mojito")
        add_clip(race_id, finish + timedelta(seconds=10), label="Sgrech")
        clips = barreplay.replay_plan(race_row(race_id))["clips"]
        assert clips[0].get("also_covers") == ["Sgrech"]

    def test_clips_far_enough_apart_are_all_kept(self, client):
        """Dropping the repeats must not drop the race."""
        race_id, warning = make_race()
        add_finish(race_id, "Boat", warning + timedelta(minutes=50))
        for minutes in (5, 20, 35, 50):
            add_clip(race_id, warning + timedelta(minutes=minutes))
        assert len(barreplay.replay_plan(race_row(race_id))["clips"]) == 4

    def test_every_clip_is_still_on_the_race_sheet(self, client):
        """This is a judgement about what the bar watches, not about what was
        recorded. A race officer looking for one boat's finish still finds it."""
        race_id, warning = make_race()
        finish = warning + timedelta(minutes=30)
        add_finish(race_id, "Boat", finish)
        for offset in range(0, 60, 10):
            add_clip(race_id, finish + timedelta(seconds=offset))
        with ro.get_db() as db:
            stored = db.execute("SELECT COUNT(*) FROM video_clips WHERE race_id = ?",
                                (race_id,)).fetchone()[0]
        assert stored == 6
        assert len(barreplay.replay_plan(race_row(race_id))["clips"]) < 6


class TestHowLongItTakes:
    """Six times life except during video, which is the whole point: an
    afternoon's racing has to fit in a bar's attention span."""

    def running_time(self, plan):
        video = sum(c["seconds"] for c in plan["clips"])
        chart = max(0.0, (plan["to_ts"] - plan["from_ts"]) - video)
        return chart / plan["speed"] + video

    def test_a_ninety_minute_race_becomes_a_bar_length_one(self, client):
        race_id, warning = make_race()
        add_finish(race_id, "Boat", warning + timedelta(minutes=90))
        for minutes in (5, 30, 60, 88):
            add_clip(race_id, warning + timedelta(minutes=minutes))
        minutes = self.running_time(barreplay.replay_plan(race_row(race_id))) / 60
        assert 15 < minutes < 35, f"{minutes:.0f} minutes is not a bar replay"

    def test_the_speed_is_the_one_the_club_asked_for(self):
        assert barreplay.REPLAY_SPEED == 6


class TestStartingOneFromTheRaceSheet:
    """The television has no keyboard, so a replay is chosen from a race sheet
    and the screen finds out on the poll it was already making."""

    def post(self, client, url, data=None):
        token = "test-csrf-token"
        with client.session_transaction() as sess:
            sess["_csrf_token"] = token
        return client.post(url, data=dict(data or {}, _csrf_token=token))

    def replayable_race(self):
        race_id, warning = make_race()
        add_finish(race_id, "Boat", warning + timedelta(minutes=40))
        add_clip(race_id, warning + timedelta(minutes=20))
        return race_id

    def test_a_race_can_be_put_on_the_television(self, logged_in_client):
        race_id = self.replayable_race()
        resp = self.post(logged_in_client, f"/admin/race/{race_id}/replay")
        assert resp.status_code in (302, 303)
        assert barreplay.current_replay()["race_id"] == race_id

    def test_and_taken_off_again(self, logged_in_client):
        race_id = self.replayable_race()
        self.post(logged_in_client, f"/admin/race/{race_id}/replay")
        self.post(logged_in_client, f"/admin/race/{race_id}/replay/stop")
        assert barreplay.current_replay()["race_id"] is None

    def test_a_race_with_nothing_to_replay_says_so(self, logged_in_client):
        """Rather than putting an empty screen up in the bar."""
        race_id, _ = make_race()          # no finishes
        self.post(logged_in_client, f"/admin/race/{race_id}/replay")
        assert barreplay.current_replay()["race_id"] is None

    def test_it_is_recorded_who_started_it(self, logged_in_client):
        race_id = self.replayable_race()
        self.post(logged_in_client, f"/admin/race/{race_id}/replay")
        assert barreplay.current_replay()["by"]

    def test_signing_in_is_enough(self, client):
        """Any signed-in user: it changes what a screen shows and touches no
        race data. An anonymous visitor still cannot."""
        race_id = self.replayable_race()
        token = "test-csrf-token"
        with client.session_transaction() as sess:
            sess["_csrf_token"] = token
        resp = client.post(f"/admin/race/{race_id}/replay", data={"_csrf_token": token})
        assert resp.status_code in (302, 303)
        assert "login" in resp.headers.get("Location", "")
        assert barreplay.current_replay()["race_id"] is None

    def test_the_race_sheet_offers_it(self, logged_in_client):
        race_id = self.replayable_race()
        page = logged_in_client.get(f"/admin/race/{race_id}").get_data(as_text=True)
        assert "Replay this race in the bar" in page

    def test_and_offers_to_stop_the_one_running(self, logged_in_client):
        race_id = self.replayable_race()
        self.post(logged_in_client, f"/admin/race/{race_id}/replay")
        page = logged_in_client.get(f"/admin/race/{race_id}").get_data(as_text=True)
        assert "Stop the replay" in page

    def test_it_says_roughly_how_long_it_will_take(self, logged_in_client):
        """So nobody starts a forty-minute replay at closing time."""
        race_id = self.replayable_race()
        page = logged_in_client.get(f"/admin/race/{race_id}").get_data(as_text=True)
        card = page[page.index("Replay in the bar"):]
        assert "minutes" in card[:1200]


class TestTheTelevisionFindsOut:
    def test_the_poll_carries_the_selection(self, client):
        race_id, warning = make_race()
        add_finish(race_id, "Boat", warning + timedelta(minutes=40))
        barreplay.start_replay(race_id)
        state = client.get(f"/bar/state/{race_id}").get_json()
        assert state["replay"]["race_id"] == race_id

    def test_the_replay_page_carries_the_whole_plan(self, client):
        """No request per frame, and no second source of truth about what is in
        the replay."""
        race_id, warning = make_race()
        add_finish(race_id, "Boat", warning + timedelta(minutes=40))
        add_clip(race_id, warning + timedelta(minutes=20))
        page = client.get(f"/bar/{race_id}?replay=1").get_data(as_text=True)
        assert "data-replay=" in page
        assert "from_ts" in page and "clips" in page

    def test_an_ordinary_bar_page_is_still_the_live_view(self, client):
        race_id, warning = make_race()
        add_finish(race_id, "Boat", warning + timedelta(minutes=40))
        page = client.get(f"/bar/{race_id}").get_data(as_text=True)
        assert "data-replay=" not in page

    def test_the_display_can_report_it_has_finished(self, client):
        """It is not signed in -- it is a screen on a wall -- and without this it
        would return to the live view and be sent straight back into the replay
        by the next poll."""
        race_id, warning = make_race()
        add_finish(race_id, "Boat", warning + timedelta(minutes=40))
        barreplay.start_replay(race_id)
        assert client.post(f"/bar/replay/finished/{race_id}").get_json()["ok"] is True
        assert barreplay.current_replay()["race_id"] is None

    def test_but_only_the_replay_it_names(self, client):
        """So a stale television cannot stop the replay somebody has just put on."""
        barreplay.start_replay(7)
        client.post("/bar/replay/finished/9")
        assert barreplay.current_replay()["race_id"] == 7


class TestTheDisplayCode:
    """The engine runs unattended on a screen in a bar. These hold the two
    properties that were got wrong while building it, both found by watching a
    real race replay rather than by reasoning about the code."""

    def source(self):
        return (Path(__file__).resolve().parent.parent
                / "static" / "bar_display.js").read_text(encoding="utf-8")

    def test_one_clock_serves_the_whole_display(self):
        """The chart, the clock, the board and the map fit must agree about
        which moment is being shown, so they all ask the same function."""
        js = self.source()
        assert "function nowTs()" in js
        assert js.count("Date.now() / 1000") <= 1, "something still reads the wall clock"

    def test_the_clock_follows_the_video_rather_than_matching_its_speed(self):
        """Locked, not parallel: buffering or a slow decode must not let the
        boats drift away from the picture."""
        js = self.source()
        assert "at = playing.starts_at + ct;" in js

    def test_the_clock_waits_for_a_clip_to_actually_start(self):
        """A clip joined in progress reads currentTime 0 until its seek lands, so
        locking to it straight away threw the clock back to the head of the
        clip."""
        js = self.source()
        assert "if (clipShown) {" in js

    def test_a_clip_is_joined_where_the_replay_has_got_to(self):
        """Clips overlap, so playing each from zero sent the clock backwards --
        +0:52 to +0:02, with the boats sliding back up the course."""
        js = self.source()
        assert "const seenUpTo = Math.max(0, at - clip.starts_at);" in js
        assert "at = Math.max(at, due.starts_at);" in js

    def test_a_refused_play_is_asked_again_rather_than_taken_as_final(self):
        """Measured on the bar page: asking to play while the new source is still
        loading is rejected with AbortError, and the clip then sits ready and
        paused. Taking that as "this television will not play video" cut the
        start clip off after 5.4 seconds."""
        js = self.source()
        assert "p.catch(() => {});" in js
        assert "if (video.paused && !video.ended) askToPlay();" in js
        assert "p.catch(() => endClip())" not in js

    def test_a_clip_that_will_not_play_does_not_stop_the_replay(self):
        """A television that refuses autoplay would otherwise hold for ever on a
        still frame -- so what decides is whether the picture actually moves, not
        what a promise said."""
        js = self.source()
        assert "const CLIP_STALL_S" in js
        assert "(nowReal - clipMoved) / 1000 > CLIP_STALL_S) endClip();" in js

    def test_a_clip_is_only_judged_while_the_screen_is_showing_it(self):
        """A hidden tab has its video suspended by the browser, so the picture
        stops for a reason that says nothing about the clip. Measured: a finish
        clip stalled at 66 seconds purely because the page was not on screen."""
        js = self.source()
        assert "if (document.hidden) clipMoved = nowReal;" in js

    def test_a_clip_nobody_saw_does_not_move_the_clock(self):
        """Skipping to the end of footage that never played hid the finish inside
        it -- and on a race's last clip it carried the replay past the last
        finish, which ended the replay there and then."""
        js = self.source()
        assert "if (clipShown) at = playing.ends_at;" in js

    def test_the_live_poll_does_not_fight_the_replay(self):
        """The camera window and the follow-the-current-race reload are
        statements about now, and would cut in over a clip."""
        assert "A replay owns the screen while it runs." in self.source()


class TestWhereAClipsFootageReallyStarts:
    """Everything the replay does while a video plays hangs off this.

    A clip is cut by concatenating whole rolling-buffer segments, so its first
    frame is not ``event_time - pre_seconds``: it is the segment boundary at or
    before it. On the hut, with twenty-second segments, the start clip for
    "R9 Summer" claimed 11:24:00 and in fact opened at 11:23:44 -- and the
    replay, locking its clock to a video that began sixteen seconds earlier than
    it believed, drew the fleet sixteen seconds ahead of the picture.
    """

    @pytest.fixture(autouse=True)
    def hut_segments(self, monkeypatch):
        """The hut's buffer is cut into twenty-second segments, which is where
        the sixteen seconds came from."""
        monkeypatch.setattr(barreplay, "_segment_seconds", lambda: 20)

    def clip_row(self, clip_id):
        with ro.get_db() as db:
            return db.execute("SELECT * FROM video_clips WHERE id = ?", (clip_id,)).fetchone()

    def test_the_recorded_start_is_used_when_there_is_one(self, client):
        race_id, warning = make_race()
        event = warning + timedelta(minutes=20)
        clip_id = add_clip(race_id, event)
        opened = event - timedelta(seconds=76)      # 60 asked for, 16 of overhang
        with ro.get_db() as db:
            db.execute("UPDATE video_clips SET footage_started_at = ? WHERE id = ?",
                       (opened.isoformat(timespec="seconds"), clip_id))
            db.commit()
        assert barreplay.footage_start_ts(self.clip_row(clip_id), event, 60) == pytest.approx(
            opened.timestamp(), abs=1)

    def test_an_older_clip_is_put_in_the_middle_of_the_segment_it_began_in(self, client):
        """The boundary is gone with the buffer and cannot be recovered: the file
        is pre + post + one segment long however the overhang falls, so even its
        duration says nothing. Half a segment out beats a whole one."""
        race_id, warning = make_race()
        event = warning + timedelta(minutes=20)
        clip_id = add_clip(race_id, event)
        segment = barreplay._segment_seconds()
        assert barreplay.footage_start_ts(self.clip_row(clip_id), event, 60) == (
            event.timestamp() - 60 - segment / 2.0)

    def test_a_recorded_start_that_could_not_hold_the_event_is_not_believed(self, client):
        """A wrong clock or a stray filename must not drag a clip off its moment."""
        race_id, warning = make_race()
        event = warning + timedelta(minutes=20)
        clip_id = add_clip(race_id, event)
        with ro.get_db() as db:
            db.execute("UPDATE video_clips SET footage_started_at = ? WHERE id = ?",
                       ((event - timedelta(hours=3)).isoformat(timespec="seconds"), clip_id))
            db.commit()
        assert barreplay.footage_start_ts(self.clip_row(clip_id), event, 60) > (
            event.timestamp() - 120)

    def test_the_plan_starts_a_clip_where_its_footage_does(self, client):
        race_id, warning = make_race()
        event = warning + timedelta(minutes=20)
        clip_id = add_clip(race_id, event)
        add_finish(race_id, "Boat", warning + timedelta(minutes=40))
        opened = event - timedelta(seconds=73)
        with ro.get_db() as db:
            db.execute("UPDATE video_clips SET footage_started_at = ? WHERE id = ?",
                       (opened.isoformat(timespec="seconds"), clip_id))
            db.commit()
        clip = barreplay.replay_plan(race_row(race_id))["clips"][0]
        assert clip["starts_at"] == pytest.approx(opened.timestamp(), abs=1)
        # It still stops where the event's own window does: the tail overhang is
        # footage nobody asked for.
        assert clip["ends_at"] == pytest.approx(event.timestamp() + 60, abs=1)

    def test_building_a_clip_records_the_footage_it_holds(self, client):
        from core import video
        race_id, warning = make_race()
        event = warning + timedelta(minutes=20)
        clip_id = add_clip(race_id, event)
        opened = event - timedelta(seconds=64)
        video.record_clip_footage_start(clip_id, opened)
        assert str(self.clip_row(clip_id)["footage_started_at"]).startswith(
            opened.isoformat(timespec="seconds")[:19])


class TestTheDisplayShowsOneMoment:
    """Every panel must be showing the same moment of the race. The clock, the
    chart and the map fit were routed through the replay clock; the order on the
    water and the wind were not, and both went on describing now."""

    def source(self, name):
        return (Path(__file__).resolve().parent.parent
                / "static" / name).read_text(encoding="utf-8")

    def test_the_board_is_chosen_by_the_clock_not_taken_from_the_end(self):
        """Taking the last board is right live and wrong replaying: it is the
        finishing order, so every boat read "Finished" a minute before the
        start."""
        js = self.source("bar_display.js")
        assert "function boardRows()" in js
        assert "data.boards.rows[data.boards.rows.length - 1]" not in js

    def test_the_wind_comes_from_the_race_being_replayed(self):
        """Not from the weather station, which is the wind outside now."""
        js = self.source("bar_display.js")
        assert "WindGauge.readingAt(data && data.wind, nowTs())" in js

    def test_the_gauge_is_taken_off_the_live_poll_for_a_replay(self):
        html = (Path(__file__).resolve().parent.parent / "templates"
                / "bar_display.html").read_text(encoding="utf-8")
        assert "gauge_manual=(replay_plan is not none)" in html

    def test_the_flags_fly_at_the_moment_being_replayed(self):
        """A replayed race is finished in life, and the panel asked the wall
        clock, so the class flag and the preparatory never appeared: the start
        sequence -- the part most worth watching back -- ran with an empty
        flagstaff."""
        js = self.source("bar_display.js")
        assert "SignalFlags.liveFlags(flagSchedule, shownAt)" in js
        assert "replay ? nowTs() >= replayPlan.to_ts" in js
        assert "new Date()" not in js, "something still reads the wall clock"

    def test_what_the_wind_was_at_a_moment_is_decided_in_one_place(self):
        """So a replay in the bar and one on the race page cannot disagree."""
        assert "readingAt: readingAt" in self.source("wind_gauge.js")
        replay = self.source("race_replay.js")
        assert "window.WindGauge.readingAt(series, t)" in replay
        assert "const WIND_STALE_S" not in replay
