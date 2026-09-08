"""Putting a series' standings on the club website, from the race office.

At the end of a day's racing the results should go up. Until now that meant
*Download HTML* and then somebody with an FTP client, which is a step that
happens on Monday if it happens at all.

**It reuses the public bucket the videos already go to.** The club has one
Cloudflare R2 bucket served publicly, with a base URL configured under Settings →
Video recording. Results are a second kind of public artefact from the same race
office; a second bucket would mean second credentials, a second base URL and a
second thing to get wrong.

**Every publish writes two objects**, and the pair is the design:

* a timestamped one, immutable, cached for a year — the record of what went out
  on Saturday evening, which never changes afterwards;
* ``latest.html``, overwritten each time and told to go stale after a minute —
  the link that goes on the club website, pasted once and current all season.

Getting the cache headers the wrong way round is the failure this feature exists
to prevent, arriving by a different route: Cloudflare would serve Saturday's
standings to the club website all week.

Nothing here talks to Cloudflare. The uploader is the one the video publisher
already uses and is stubbed, so what these hold is the decisions: which keys,
which headers, what is recorded, and what the two pages then show.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from core import resultspublish


@pytest.fixture
def uploads(monkeypatch):
    """Capture what would have gone to R2, and pretend the bucket is set up."""
    sent = []

    def fake_upload(cfg, key, body, content_type="application/octet-stream",
                    cache_control="public, max-age=31536000, immutable"):
        sent.append({"key": key, "body": body, "content_type": content_type,
                     "cache_control": cache_control})
        return "https://results.example.org/" + key

    import core.video as video
    monkeypatch.setattr(video, "upload_bytes_to_r2", fake_upload)
    monkeypatch.setattr(resultspublish, "publish_ready", lambda cfg=None: True)
    monkeypatch.setattr(resultspublish, "publish_config", lambda: {"stub": True})
    return sent


def _series(name="Autumn Series 2026"):
    from core.db import get_db
    now = datetime.now().isoformat(timespec="seconds")
    with get_db() as db:
        cur = db.execute(
            "INSERT INTO race_series (name, description, discard_profile,"
            " min_races_to_constitute, created_at, updated_at) VALUES (?,?,?,?,?,?)",
            (name, "", "0,0,1", 3, now, now))
        db.commit()
        return int(cur.lastrowid)


class TestWhereItGoes:
    def test_each_series_has_its_own_folder(self):
        assert resultspublish.series_folder(6) == "results/series-6"

    def test_the_dated_key_carries_the_series_and_the_moment(self):
        keys = resultspublish.object_keys(6, "Autumn Series 2026",
                                          datetime(2026, 9, 1, 16, 42, 7))
        assert keys["dated"] == \
            "results/series-6/Autumn-Series-2026-results-2026-09-01-164207.html"

    def test_two_publishes_in_one_minute_are_two_objects(self):
        """A mis-click, or a finish remembered late. Without seconds the second
        overwrites the first, and the Published list shows two entries that are
        one file."""
        first = resultspublish.object_keys(6, "S", datetime(2026, 9, 1, 16, 42, 7))
        second = resultspublish.object_keys(6, "S", datetime(2026, 9, 1, 16, 42, 58))
        assert first["dated"] != second["dated"]

    def test_but_a_download_is_still_named_to_the_minute(self):
        """Seconds are for a key that must be unique, not for somebody reading a
        filename in their downloads folder."""
        from core.series import export_filename
        assert export_filename("S", "results", "html", datetime(2026, 9, 1, 16, 42, 7)) == \
            "S-results-2026-09-01-1642.html"

    def test_the_stable_key_never_moves(self):
        for when in (datetime(2026, 9, 1), datetime(2026, 10, 5)):
            keys = resultspublish.object_keys(6, "Autumn Series 2026", when)
            assert keys["stable"] == "results/series-6/latest.html"

    def test_two_series_cannot_overwrite_each_other(self):
        a = resultspublish.object_keys(1, "A")["stable"]
        b = resultspublish.object_keys(2, "A")["stable"]
        assert a != b


class TestPublishing:
    def test_it_uploads_both_objects(self, client, uploads):
        sid = _series()
        resultspublish.publish_series_results(sid, "Autumn Series 2026", "<html>x</html>")
        assert [u["key"].rsplit("/", 1)[-1] for u in uploads][-1] == "latest.html"
        assert len(uploads) == 2

    def test_both_carry_the_same_document(self, client, uploads):
        sid = _series()
        resultspublish.publish_series_results(sid, "Autumn Series 2026", "<html>x</html>")
        assert uploads[0]["body"] == uploads[1]["body"] == b"<html>x</html>"

    def test_the_dated_one_is_immutable(self, client, uploads):
        sid = _series()
        resultspublish.publish_series_results(sid, "Autumn Series 2026", "<html>x</html>")
        assert "immutable" in uploads[0]["cache_control"]

    def test_but_the_latest_one_goes_stale_quickly(self, client, uploads):
        """A long cache here would leave the club website showing Saturday's
        standings on Wednesday -- the very thing this is for."""
        sid = _series()
        resultspublish.publish_series_results(sid, "Autumn Series 2026", "<html>x</html>")
        assert "immutable" not in uploads[1]["cache_control"]
        assert "max-age=60" in uploads[1]["cache_control"]

    def test_it_is_served_as_html(self, client, uploads):
        sid = _series()
        resultspublish.publish_series_results(sid, "Autumn Series 2026", "<html>x</html>")
        for u in uploads:
            assert u["content_type"].startswith("text/html")

    def test_it_records_what_went_out(self, client, uploads):
        sid = _series()
        rec = resultspublish.publish_series_results(
            sid, "Autumn Series 2026", "<html>x</html>", actor="alice")
        rows = resultspublish.published_for_series(sid)
        assert len(rows) == 1
        assert rows[0]["published_by"] == "alice"
        assert rows[0]["stable_url"] == rec["stable_url"]

    def test_publishing_twice_keeps_both_records(self, client, uploads):
        """The list is a history: "what did we put out on Saturday" has to have
        an answer after Sunday's publish."""
        sid = _series()
        resultspublish.publish_series_results(sid, "S", "<html>1</html>",
                                              when=datetime.now() - timedelta(days=1))
        resultspublish.publish_series_results(sid, "S", "<html>2</html>")
        assert len(resultspublish.published_for_series(sid)) == 2

    def test_the_newest_comes_first(self, client, uploads):
        sid = _series()
        old = datetime.now() - timedelta(days=1)
        resultspublish.publish_series_results(sid, "S", "<html>1</html>", when=old)
        resultspublish.publish_series_results(sid, "S", "<html>2</html>")
        rows = resultspublish.published_for_series(sid)
        assert rows[0]["published_at"] > rows[1]["published_at"]

    def test_nothing_is_recorded_when_the_upload_fails(self, client, uploads, monkeypatch):
        """The Published list must never offer a link that was never written."""
        import core.video as video
        monkeypatch.setattr(video, "upload_bytes_to_r2",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no network")))
        sid = _series()
        with pytest.raises(RuntimeError):
            resultspublish.publish_series_results(sid, "S", "<html>x</html>")
        assert resultspublish.published_for_series(sid) == []

    def test_an_unconfigured_bucket_refuses_clearly(self, client, monkeypatch):
        monkeypatch.setattr(resultspublish, "publish_ready", lambda cfg=None: False)
        sid = _series()
        with pytest.raises(RuntimeError, match="Settings"):
            resultspublish.publish_series_results(sid, "S", "<html>x</html>")


class TestTheLatestLookup:
    def test_it_finds_the_newest_for_each_series(self, client, uploads):
        a, b = _series("A"), _series("B")
        old = datetime.now() - timedelta(days=1)
        resultspublish.publish_series_results(a, "A", "<html>old</html>", when=old)
        resultspublish.publish_series_results(a, "A", "<html>new</html>")
        resultspublish.publish_series_results(b, "B", "<html>b</html>")
        latest = resultspublish.latest_published_by_series()
        assert set(latest) == {a, b}

    def test_a_series_with_nothing_published_is_absent(self, client, uploads):
        a = _series("A")
        _series("B")
        resultspublish.publish_series_results(a, "A", "<html>a</html>")
        assert list(resultspublish.latest_published_by_series()) == [a]

    def test_it_is_one_query_for_every_series(self, client, uploads):
        """The competitor landing page draws a roll-up per series and is left
        open all day; a query apiece is a query apiece."""
        for i in range(3):
            sid = _series(f"S{i}")
            resultspublish.publish_series_results(sid, f"S{i}", "<html>x</html>")
        assert len(resultspublish.latest_published_by_series()) == 3


class TestWhatThePagesShow:
    def test_the_series_page_offers_the_published_list(self, logged_in_client, uploads):
        sid = _series()
        resultspublish.publish_series_results(sid, "Autumn Series 2026", "<html>x</html>")
        html = logged_in_client.get(f"/admin/series/{sid}").get_data(as_text=True)
        assert 'data-help="publishedDialog"' in html
        assert "latest.html" in html

    def test_and_a_way_to_copy_the_link(self, logged_in_client, uploads):
        sid = _series()
        resultspublish.publish_series_results(sid, "Autumn Series 2026", "<html>x</html>")
        html = logged_in_client.get(f"/admin/series/{sid}").get_data(as_text=True)
        assert 'data-copy="#publishStableUrl"' in html

    def test_the_competitor_page_links_to_the_latest(self, client, uploads):
        sid = _series()
        from core.db import get_db
        now = datetime.now().isoformat(timespec="seconds")
        with get_db() as db:
            db.execute("INSERT INTO races (name, series_id, course_no, course_set,"
                       " start_time, notes, created_at) VALUES ('R1',?,1,1,'','',?)",
                       (sid, now))
            db.commit()
        resultspublish.publish_series_results(sid, "Autumn Series 2026", "<html>x</html>")
        html = client.get("/public/current").get_data(as_text=True)
        assert "Published results" in html
        assert "results/series-%d/latest.html" % sid in html

    def test_and_says_nothing_when_nothing_is_published(self, client):
        sid = _series()
        from core.db import get_db
        now = datetime.now().isoformat(timespec="seconds")
        with get_db() as db:
            db.execute("INSERT INTO races (name, series_id, course_no, course_set,"
                       " start_time, notes, created_at) VALUES ('R1',?,1,1,'','',?)",
                       (sid, now))
            db.commit()
        html = client.get("/public/current").get_data(as_text=True)
        assert "Published results" not in html


class TestTheRoute:
    def _post(self, client, url):
        token = "test-csrf-token"
        with client.session_transaction() as sess:
            sess["_csrf_token"] = token
        return client.post(url, data={"_csrf_token": token}, follow_redirects=True)

    def test_publishing_from_the_page_records_it(self, logged_in_client, uploads):
        sid = _series()
        self._post(logged_in_client, f"/admin/series/{sid}/publish")
        assert len(resultspublish.published_for_series(sid)) == 1

    def test_a_failure_says_so_and_records_nothing(self, logged_in_client, uploads, monkeypatch):
        import core.video as video
        monkeypatch.setattr(video, "upload_bytes_to_r2",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("bucket asleep")))
        sid = _series()
        resp = self._post(logged_in_client, f"/admin/series/{sid}/publish")
        assert "Could not publish" in resp.get_data(as_text=True)
        assert resultspublish.published_for_series(sid) == []

    def test_it_is_post_only(self, logged_in_client):
        sid = _series()
        assert logged_in_client.get(f"/admin/series/{sid}/publish").status_code == 405

    def test_a_missing_series_does_not_crash(self, logged_in_client, uploads):
        assert self._post(logged_in_client, "/admin/series/999999/publish").status_code == 200


class TestWhatTheDocumentContains:
    """The published document itself, rather than where it goes."""

    @staticmethod
    def _seed(irc=True, ytc=False, started=True):
        from core.db import get_db
        now = datetime.now().isoformat(timespec="seconds")
        offset = -2 if started else +2
        when = (datetime.now() + timedelta(hours=offset)).isoformat(timespec="seconds")
        fin = (datetime.now() - timedelta(hours=1)).isoformat(timespec="seconds")
        with get_db() as db:
            cur = db.execute(
                "INSERT INTO race_series (name, description, discard_profile,"
                " min_races_to_constitute, created_at, updated_at)"
                " VALUES ('Rating Test','','0,0,1',1,?,?)", (now, now))
            sid = int(cur.lastrowid)
            cur = db.execute(
                "INSERT INTO races (name, series_id, course_no, course_set, start_time,"
                " notes, created_at) VALUES ('R1',?,1,1,?,'',?)", (sid, when, now))
            rid = int(cur.lastrowid)
            db.execute(
                "INSERT INTO entries (race_id, boat_name, sail_no, status, finish_time,"
                " manual_irc_rating, manual_ytc_rating) VALUES (?,?,?,?,?,?,?)",
                (rid, "Mojito", "GBR1", "FINISHED" if started else "RACING",
                 fin if started else None, 1.021 if irc else None, 1015 if ytc else None))
            db.commit()
        return sid

    def test_a_rating_system_nobody_is_rated_under_is_skipped(self, logged_in_client):
        """Not an empty table with a heading over it: the group is dropped, and
        the class/result matrix at the top is built from the groups, so it does
        not offer a link to a section that is not there."""
        sid = self._seed(irc=True, ytc=False)
        html = logged_in_client.get(f"/admin/series/{sid}/publish.html").get_data(as_text=True)
        assert "IRC" in html
        assert "YTC series" not in html

    def test_both_appear_when_both_are_rated(self, logged_in_client):
        sid = self._seed(irc=True, ytc=True)
        html = logged_in_client.get(f"/admin/series/{sid}/publish.html").get_data(as_text=True)
        assert "IRC" in html and "YTC" in html

    def test_a_race_that_has_not_started_says_prestart(self, logged_in_client):
        """An entry is stored RACING from the moment the boat is entered, because
        every calculation keys off it. Published raw, a race hours from its
        warning signal listed a fleet all "RACING", which was true of none of
        them."""
        sid = self._seed(started=False)
        html = logged_in_client.get(f"/admin/series/{sid}/publish.html").get_data(as_text=True)
        assert "PRESTART" in html
        assert ">RACING<" not in html

    def test_the_status_column_is_still_there(self, logged_in_client):
        """It is the only place a DNF, RET or OCS appears in a race table."""
        sid = self._seed()
        html = logged_in_client.get(f"/admin/series/{sid}/publish.html").get_data(as_text=True)
        assert "<th>Status</th>" in html


class TestTheEmbeddedLogos:
    """The document embeds every logo so it stays one uploadable file. It was
    embedding the originals: 1.48 MB of PNG, which base64 inflates by a third,
    so a results page went out at 2 MB -- and since publishing, over the hut's
    4G every time."""

    def test_a_large_logo_is_scaled_down(self, client, tmp_path):
        from PIL import Image
        import app as ro
        big = tmp_path / "big.png"
        Image.new("RGBA", (2000, 1200), (200, 30, 40, 255)).save(big)
        full = len(ro.image_data_uri(big))
        small = len(ro.thumbnail_data_uri(big))
        assert small < full / 4, (full, small)

    def test_it_is_not_scaled_past_what_the_page_shows(self, client, tmp_path):
        """The club logo is displayed at most 140x86 and a sponsor at 160x70;
        the bound is twice that, for a high-density screen."""
        from PIL import Image
        import app as ro
        import base64, io as _io
        big = tmp_path / "big.png"
        Image.new("RGBA", (2000, 1200), (200, 30, 40, 255)).save(big)
        uri = ro.thumbnail_data_uri(big)
        raw = base64.b64decode(uri.split(",", 1)[1])
        with Image.open(_io.BytesIO(raw)) as out:
            assert out.width <= 320 and out.height <= 176, out.size

    def test_a_small_logo_keeps_its_original(self, client, tmp_path):
        """Re-encoding an image already below the target can make it bigger --
        measured, a 12 KB palette PNG grew to 15 KB as RGBA. Shrinking is the
        point; re-encoding is only the means."""
        from PIL import Image
        import app as ro
        small = tmp_path / "small.png"
        Image.new("P", (40, 20)).save(small)
        assert len(ro.thumbnail_data_uri(small)) <= len(ro.image_data_uri(small))

    def test_transparency_survives(self, client, tmp_path):
        """A logo flattened onto black would be worse than a large one."""
        from PIL import Image
        import app as ro
        import base64, io as _io
        src = tmp_path / "t.png"
        Image.new("RGBA", (800, 400), (0, 0, 0, 0)).save(src)
        raw = base64.b64decode(ro.thumbnail_data_uri(src).split(",", 1)[1])
        with Image.open(_io.BytesIO(raw)) as out:
            assert out.mode in ("RGBA", "LA", "P")

    def test_no_pillow_still_produces_a_logo(self, client, tmp_path, monkeypatch):
        """Pillow is in requirements.txt but the app has always treated it as
        optional. A larger file is a much better failure than no logos."""
        import builtins
        import app as ro
        from PIL import Image
        src = tmp_path / "t.png"
        Image.new("RGBA", (800, 400), (1, 2, 3, 255)).save(src)
        real_import = builtins.__import__

        def no_pil(name, *a, **k):
            if name.startswith("PIL"):
                raise ImportError("no Pillow")
            return real_import(name, *a, **k)

        monkeypatch.setattr(builtins, "__import__", no_pil)
        assert ro.thumbnail_data_uri(src).startswith("data:image/png;base64,")
