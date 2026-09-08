"""The rating listings download while the race officer is still typing.

Looking a boat up on the Add boat page waits on two files fetched from the
internet: the RORC club listing (379 KB) and the YTC sheet (265 KB). Measured
here, on a wired connection with nothing else going on, that is 2.4 s and 0.8 s
-- and the hut reaches the world through a tunnel on 4G, where it is the
reported "quite a while". Every second of it was spent looking at a submitted
form, because both fetches happened inside the request that ran the search.

They are cached for an hour, so this is the *cold* case: the first lookup of a
race morning, which is the one that matters.

Opening the form is the reliable sign a lookup is coming -- a name has to be
typed before there is anything to search -- so the download starts there, in
the background, and the gap covers it.

What makes that worth doing is ``_fetch_csv_once``. Without it the search would
start its own second download of the same file, racing the warm-up it was meant
to benefit from, and the club would fetch 640 KB twice over 4G for one lookup.
A reader that finds a download already running joins it and reads what it
wrote.
"""
from __future__ import annotations

import io
import threading
import time

import pytest

import core.boats as boats


class _Body(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


class _Slow:
    """A urlopen that takes its time and counts how often it was called."""

    def __init__(self, payload=b"Boat Name,Sail No,TCC\nMojito,GBR4822R,1.084\n", delay=0.25):
        self.payload, self.delay = payload, delay
        self.calls = 0
        self._lock = threading.Lock()

    def __call__(self, url, timeout=None):
        with self._lock:
            self.calls += 1
        time.sleep(self.delay)
        return _Body(self.payload)


@pytest.fixture
def cold(tmp_path, monkeypatch):
    """An empty cache directory, so every read is the cold path."""
    from core import appstate
    monkeypatch.setattr(appstate, "CACHE_DIR", tmp_path / "cache")
    boats._fetch_inflight.clear()
    return tmp_path


class TestOneDownloadPerListing:
    def test_a_second_reader_joins_the_first(self, cold, monkeypatch):
        """The whole point: the search must not race the warm-up it benefits
        from. Every reader gets the rows; the file is fetched once."""
        slow = _Slow()
        monkeypatch.setattr(boats.urllib.request, "urlopen", slow)
        got = []

        def read():
            got.append(boats.read_csv_rows("https://example.test/irc.csv", cache_seconds=3600))

        threads = [threading.Thread(target=read) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(10)

        assert slow.calls == 1, f"{slow.calls} downloads of one file"
        assert len(got) == 4
        assert all(rows[0]["Boat Name"] == "Mojito" for rows in got)

    def test_the_next_reader_uses_the_cache(self, cold, monkeypatch):
        slow = _Slow(delay=0)
        monkeypatch.setattr(boats.urllib.request, "urlopen", slow)
        boats.read_csv_rows("https://example.test/irc.csv", cache_seconds=3600)
        boats.read_csv_rows("https://example.test/irc.csv", cache_seconds=3600)
        assert slow.calls == 1

    def test_different_listings_do_not_block_each_other(self, cold, monkeypatch):
        """IRC and YTC go in parallel; one queueing behind the other would make
        the warm-up as slow as fetching them serially, which is what it is for."""
        slow = _Slow(delay=0.4)
        monkeypatch.setattr(boats.urllib.request, "urlopen", slow)
        started = time.perf_counter()
        threads = [threading.Thread(target=boats.read_csv_rows,
                                    args=(f"https://example.test/{n}.csv",),
                                    kwargs={"cache_seconds": 3600})
                   for n in ("irc", "ytc")]
        for t in threads:
            t.start()
        for t in threads:
            t.join(10)
        assert slow.calls == 2
        assert time.perf_counter() - started < 0.75, "the two ran one after the other"

    def test_a_failed_fetch_releases_the_next_reader(self, cold, monkeypatch):
        """A download that raises must not leave the URL marked in-flight for
        ever, or every later lookup waits out its whole timeout for nothing."""
        def boom(url, timeout=None):
            raise OSError("no route to host")
        monkeypatch.setattr(boats.urllib.request, "urlopen", boom)
        with pytest.raises(OSError):
            boats.read_csv_rows("https://example.test/irc.csv", cache_seconds=3600)
        assert boats._fetch_inflight == {}


class TestOpeningTheFormStartsTheDownload:
    @staticmethod
    def _wait(slow, n, seconds=5):
        end = time.time() + seconds
        while time.time() < end and slow.calls < n:
            time.sleep(0.02)
        return slow.calls

    def test_the_add_boat_page_warms_both_listings(self, logged_in_client, cold, monkeypatch):
        slow = _Slow(delay=0)
        monkeypatch.setattr(boats.urllib.request, "urlopen", slow)
        assert logged_in_client.get("/admin/boats/new").status_code == 200
        assert self._wait(slow, 2) == 2, "the form did not start the download"

    def test_the_edit_page_does_too(self, logged_in_client, cold, monkeypatch):
        from core.db import get_db
        with get_db() as db:
            cur = db.execute(
                "INSERT INTO boats (boat_name, sail_no, status, created_at, updated_at)"
                " VALUES ('Mojito','GBR4822R','ACTIVE',datetime('now'),datetime('now'))")
            boat_id = int(cur.lastrowid)
            db.commit()
        slow = _Slow(delay=0)
        monkeypatch.setattr(boats.urllib.request, "urlopen", slow)
        assert logged_in_client.get(f"/admin/boats/{boat_id}/edit").status_code == 200
        assert self._wait(slow, 2) == 2

    def test_a_page_that_is_already_searching_does_not_warm_as_well(self, logged_in_client, cold, monkeypatch):
        """The search fetches what it needs. Warming alongside it would be a
        second pair of downloads for the same two files."""
        slow = _Slow(delay=0)
        monkeypatch.setattr(boats.urllib.request, "urlopen", slow)
        assert logged_in_client.get("/admin/boats/new?lookup_q=mojito").status_code == 200
        time.sleep(0.3)
        assert slow.calls == 2, f"{slow.calls} downloads for one search"

    def test_the_page_renders_when_the_warm_up_cannot_reach_the_listings(self, logged_in_client, cold, monkeypatch):
        """Speculative and silent: there is nothing useful to say about a fetch
        nobody asked for, and the search that follows reports its own trouble."""
        def boom(url, timeout=None):
            raise OSError("no route to host")
        monkeypatch.setattr(boats.urllib.request, "urlopen", boom)
        page = logged_in_client.get("/admin/boats/new")
        assert page.status_code == 200
        assert b"Search IRC + YTC" in page.data


class TestKnowingWhetherItIsWarm:
    def test_cold_to_start_with(self, cold):
        assert boats.rating_listings_are_warm("https://example.test/a.csv",
                                              "https://example.test/b.csv") is False

    def test_warm_once_both_are_cached(self, cold, monkeypatch):
        slow = _Slow(delay=0)
        monkeypatch.setattr(boats.urllib.request, "urlopen", slow)
        boats.read_csv_rows("https://example.test/a.csv", cache_seconds=3600)
        boats.read_csv_rows("https://example.test/b.csv", cache_seconds=3600)
        assert boats.rating_listings_are_warm("https://example.test/a.csv",
                                              "https://example.test/b.csv") is True

    def test_one_of_the_two_is_not_warm(self, cold, monkeypatch):
        slow = _Slow(delay=0)
        monkeypatch.setattr(boats.urllib.request, "urlopen", slow)
        boats.read_csv_rows("https://example.test/a.csv", cache_seconds=3600)
        assert boats.rating_listings_are_warm("https://example.test/a.csv",
                                              "https://example.test/b.csv") is False
