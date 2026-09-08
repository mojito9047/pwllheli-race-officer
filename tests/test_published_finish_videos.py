"""A finish taken on the horn switch is still a finish video.

Reported from the club website: the competitor page showed **View** against
three boats' finish videos, and the published results document for the same
race, the same three boats, said **No Video**.

Both read the same clips. They disagreed about which ones count.

The race officer can finish a boat two ways. The *Finish* button schedules a
clip with ``clip_type='finish'``. The **physical horn switch** in the hut logs a
horn event and schedules a ``manual_horn`` clip; assigning that horn time to a
boat afterwards -- the Race log's "assign as finish" -- writes the finish onto
the entry and stamps the entry's id onto the clip, but leaves the clip's type
alone. There is no reason for it to be rewritten: the clip genuinely is a
recording of a horn, and the type is what the Video tab lists it under.

So the rule that matters is **a clip carrying an entry_id is that boat's finish
video**, whatever its type. ``video_clip_maps`` has always used exactly that,
which is why the competitor page was right. The published builder asked for
``clip_type == 'finish'`` instead, and so missed every finish taken on the horn:
on the club's own database, 10 of 26.

Measured against that database before and after: the published document went
from 0 "View" links and 16 "No Video" to 10 and 6, the remaining six being boats
with no clip at all.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

import app as ro


def _race_with_entries(boats=("Blue Sky", "THEIA")):
    from core.db import get_db
    now = datetime.now()
    gun = (now - timedelta(hours=2)).replace(microsecond=0)
    with get_db() as db:
        cur = db.execute(
            "INSERT INTO races (name, course_no, course_set, start_time, rating_rule, created_at)"
            " VALUES ('Race 4', 1, 1, ?, 'DUAL', ?)",
            (gun.isoformat(timespec="seconds"), now.isoformat(timespec="seconds")))
        race_id = int(cur.lastrowid)
        ids = {}
        for i, name in enumerate(boats):
            cur = db.execute(
                "INSERT INTO boats (boat_name, sail_no, ytc_rating, status, created_at, updated_at)"
                " VALUES (?, ?, 1000, 'ACTIVE', ?, ?)",
                (name, f"GBR{i}", now.isoformat(timespec="seconds"),
                 now.isoformat(timespec="seconds")))
            boat_id = int(cur.lastrowid)
            finish = (gun + timedelta(minutes=54 + i)).isoformat(timespec="seconds")
            cur = db.execute(
                "INSERT INTO entries (race_id, boat_id, boat_name, sail_no, status,"
                " finish_time, manual_ytc_rating) VALUES (?,?,?,?,'FINISHED',?,1000)",
                (race_id, boat_id, name, f"GBR{i}", finish))
            ids[name] = {"entry_id": int(cur.lastrowid), "finish": finish}
        db.commit()
    return race_id, ids


def _clip(race_id, clip_type, entry_id=None, status="ready", event_time=""):
    from core.db import get_db
    now = datetime.now().isoformat(timespec="seconds")
    with get_db() as db:
        cur = db.execute(
            "INSERT INTO video_clips (race_id, entry_id, clip_type, label, event_time,"
            " status, public_status, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,'off',?,?)",
            (race_id, entry_id, clip_type, f"{clip_type} clip", event_time or now,
             status, now, now))
        db.commit()
        return int(cur.lastrowid)


class TestAHornFinishIsAFinishVideo:
    def test_a_manual_horn_clip_assigned_to_a_boat_is_that_boat_s_video(self, client):
        """The reported case, in the shape the horn-switch flow leaves behind.

        Published to R2, because that is the state the club's own clips are in
        and the only state a standalone document can link to -- see
        TestOnlyLinksThatWorkFromAnywhere.
        """
        from core.db import get_db
        race_id, ids = _race_with_entries()
        entry_id = ids["Blue Sky"]["entry_id"]
        clip_id = _clip(race_id, "manual_horn", entry_id=entry_id)
        public = f"https://assets.pwllhelisailingclub.org/racevideos/clip{clip_id}.mp4"
        with get_db() as db:
            db.execute("UPDATE video_clips SET public_status='ready', public_url=? WHERE id=?",
                       (public, clip_id))
            db.commit()
        with ro.app.test_request_context("/admin/series/1/publish"):
            links = ro.published_video_links_for_race(race_id)
        assert str(entry_id) in links["finish_by_entry"], (
            "a finish taken on the horn switch produced no video link"
        )
        assert links["finish_by_entry"][str(entry_id)]["url"] == public

    def test_the_finish_button_still_works(self, client):
        race_id, ids = _race_with_entries()
        entry_id = ids["THEIA"]["entry_id"]
        _clip(race_id, "finish", entry_id=entry_id)
        with ro.app.test_request_context("/admin/series/1/publish"):
            links = ro.published_video_links_for_race(race_id)
        assert str(entry_id) in links["finish_by_entry"]

    def test_both_kinds_in_one_race(self, client):
        """A race finished partly on the button and partly on the horn: every
        boat that has a clip gets a link, and no boat is dropped for the type
        of thing the race officer happened to press."""
        race_id, ids = _race_with_entries(("Blue Sky", "THEIA"))
        _clip(race_id, "manual_horn", entry_id=ids["Blue Sky"]["entry_id"])
        _clip(race_id, "finish", entry_id=ids["THEIA"]["entry_id"])
        with ro.app.test_request_context("/admin/series/1/publish"):
            links = ro.published_video_links_for_race(race_id)
        assert set(links["finish_by_entry"]) == {
            str(ids["Blue Sky"]["entry_id"]), str(ids["THEIA"]["entry_id"])}


class TestItAgreesWithTheCompetitorPage:
    """The two pages read the same clips and must reach the same answer. They
    disagreed for one reason: one filtered on clip_type and the other did not."""

    def test_the_same_clips_produce_the_same_boats(self, client):
        from core.video import video_clip_maps
        race_id, ids = _race_with_entries(("Blue Sky", "THEIA"))
        _clip(race_id, "manual_horn", entry_id=ids["Blue Sky"]["entry_id"])
        _clip(race_id, "finish", entry_id=ids["THEIA"]["entry_id"])
        _clip(race_id, "start")                       # no entry: belongs to neither

        competitor = {str(k) for k in video_clip_maps(race_id)["by_entry"]}
        with ro.app.test_request_context("/admin/series/1/publish"):
            published = set(ro.published_video_links_for_race(race_id)["finish_by_entry"])
        assert competitor == published, (
            f"competitor page shows {sorted(competitor)}, published shows {sorted(published)}"
        )


class TestWhatStillHasNoVideo:
    def test_a_boat_with_no_clip_at_all(self, client):
        race_id, ids = _race_with_entries()
        _clip(race_id, "manual_horn", entry_id=ids["Blue Sky"]["entry_id"])
        with ro.app.test_request_context("/admin/series/1/publish"):
            links = ro.published_video_links_for_race(race_id)
        assert str(ids["THEIA"]["entry_id"]) not in links["finish_by_entry"]

    def test_a_start_clip_is_not_given_to_a_boat(self, client):
        """A start clip carries no entry, and must not be dressed up as one
        boat's finish just because the filter was loosened."""
        race_id, ids = _race_with_entries()
        _clip(race_id, "start")
        with ro.app.test_request_context("/admin/series/1/publish"):
            links = ro.published_video_links_for_race(race_id)
        assert links["finish_by_entry"] == {}
        assert len(links["start_links"]) == 1

    def test_a_clip_that_is_not_ready_gives_no_link(self, client):
        """Still listed, so the reader can see there was meant to be one, but
        with no href to a video that does not exist."""
        race_id, ids = _race_with_entries()
        entry_id = ids["Blue Sky"]["entry_id"]
        _clip(race_id, "manual_horn", entry_id=entry_id, status="error")
        with ro.app.test_request_context("/admin/series/1/publish"):
            links = ro.published_video_links_for_race(race_id)
        assert links["finish_by_entry"][str(entry_id)]["url"] == ""

class TestOnlyLinksThatWorkFromAnywhere:
    """The document is read from the club website, not from the hut.

    The fallback used to be ``url_for(..., _external=True)``, which builds an
    address out of whatever host the race officer published from. A real
    measurement of one series document: 43 links reading
    ``http://raceofficer.local:5050/public/video/clip/124``, every one of them
    dead for the people it was published for. A row that says there is no video
    is honest; a link that cannot resolve is not.

    A clip is publicly linkable when it has been uploaded to R2, which is what
    ``video_public_r2_*`` in Settings turns on.
    """

    def test_a_clip_not_published_to_r2_gets_no_link(self, client):
        race_id, ids = _race_with_entries()
        entry_id = ids["Blue Sky"]["entry_id"]
        _clip(race_id, "manual_horn", entry_id=entry_id)      # ready, but public_status 'off'
        with ro.app.test_request_context("/admin/series/1/publish",
                                         base_url="http://raceofficer.local:5050"):
            links = ro.published_video_links_for_race(race_id)
        assert links["finish_by_entry"][str(entry_id)]["url"] == ""

    def test_and_the_hut_address_is_nowhere_in_the_document(self, client):
        race_id, ids = _race_with_entries()
        _clip(race_id, "manual_horn", entry_id=ids["Blue Sky"]["entry_id"])
        _clip(race_id, "start")
        with ro.app.test_request_context("/admin/series/1/publish",
                                         base_url="http://raceofficer.local:5050"):
            links = ro.published_video_links_for_race(race_id)
        urls = [l["url"] for l in links["start_links"]] + [
            v["url"] for v in links["finish_by_entry"].values()]
        assert not [u for u in urls if u], f"published a link into the hut: {urls}"

    def test_an_r2_published_clip_keeps_its_link(self, client):
        """The whole point of publishing a video: an address anybody can open."""
        from core.db import get_db
        race_id, ids = _race_with_entries()
        entry_id = ids["Blue Sky"]["entry_id"]
        clip_id = _clip(race_id, "manual_horn", entry_id=entry_id)
        public = "https://assets.pwllhelisailingclub.org/racevideos/race1/clip.mp4"
        with get_db() as db:
            db.execute("UPDATE video_clips SET public_status='ready', public_url=? WHERE id=?",
                       (public, clip_id))
            db.commit()
        with ro.app.test_request_context("/admin/series/1/publish",
                                         base_url="http://raceofficer.local:5050"):
            links = ro.published_video_links_for_race(race_id)
        assert links["finish_by_entry"][str(entry_id)]["url"] == public

    def test_the_competitor_page_keeps_its_hut_link(self, client):
        """It is served *by* the hut, so a hut address is right there. Only the
        standalone document has to travel."""
        race_id, ids = _race_with_entries()
        clip_id = _clip(race_id, "manual_horn", entry_id=ids["Blue Sky"]["entry_id"])
        from core.db import get_db
        with get_db() as db:
            clip = db.execute("SELECT * FROM video_clips WHERE id = ?", (clip_id,)).fetchone()
        with ro.app.test_request_context("/public/race/1"):
            assert str(clip_id) in ro.public_video_clip_href(clip)
