"""Publishing a series' results to the club's public bucket.

At the end of a day's racing the race officer wants the standings so far on the
club website. Until now that meant *Download HTML* and then somebody with an FTP
client, which is a step that happens on Monday if it happens at all.

**It reuses the public bucket the videos already go to.** The club has one
Cloudflare R2 bucket that is served publicly, with a base URL configured under
Settings → Video recording, and start/finish clips are already published to it.
Results are a second kind of public artefact from the same race office; a second
bucket would mean second credentials, a second base URL and a second thing to
get wrong, for no benefit anyone could name.

**Every publish writes two objects.** A timestamped one, which is the record of
what was published and when and never changes afterwards; and ``latest.html``,
overwritten each time. The stable one is what goes on the club website: paste it
once and it keeps up for the rest of the season. The timestamped ones are what
the *Published* list shows, so "what did we put out on Saturday evening" has an
answer.

The two carry different cache headers, and that is the whole reason the split
works: a timestamped object is immutable and can be cached for a year, while
``latest.html`` is told to go stale after a minute, or Cloudflare would go on
serving Saturday's standings to the club website all week.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any, Dict, List, Optional

from core.db import get_db, init_db, row_get
from core.series import export_filename

# The folder each series' results live in, under the public bucket's own prefix.
RESULTS_PREFIX = "results"
STABLE_NAME = "latest.html"

# A timestamped publish never changes, so it can be cached for as long as
# anything is willing to. `latest.html` is overwritten every time, and a long
# cache there would leave the club website showing Saturday's standings on
# Wednesday -- which is the failure this feature exists to prevent, arriving by
# a different route.
IMMUTABLE_CACHE = "public, max-age=31536000, immutable"
LATEST_CACHE = "public, max-age=60"


def series_folder(series_id: int) -> str:
    """The folder one series' results are published into."""
    return f"{RESULTS_PREFIX}/series-{int(series_id)}"


def object_keys(series_id: int, series_name: Any,
                when: Optional[datetime] = None) -> Dict[str, str]:
    """The timestamped key and the stable key for one publish."""
    when = when or datetime.now()
    folder = series_folder(series_id)
    return {
        # To the second: the dated object is the permanent record of one publish,
        # and two publishes in the same minute -- a mis-click, or a finish
        # remembered late -- must not be one object with two rows pointing at it.
        "dated": f"{folder}/{export_filename(series_name, 'results', 'html', when, seconds=True)}",
        "stable": f"{folder}/{STABLE_NAME}",
    }


def publish_config() -> Dict[str, Any]:
    """The public-bucket settings, read the same way the video publisher reads them."""
    from core.video import video_config
    return video_config()


def publish_ready(cfg: Optional[Dict[str, Any]] = None) -> bool:
    """Whether the public bucket is configured well enough to publish to.

    Deliberately the *credentials* check and not the video one: a club may have
    turned video publishing off and still want its results on the website.
    """
    from core.video import video_public_r2_credentials_ready
    return video_public_r2_credentials_ready(cfg if cfg is not None else publish_config())


def publish_series_results(series_id: int, series_name: Any, html: str,
                           actor: str = "system",
                           when: Optional[datetime] = None) -> Dict[str, Any]:
    """Upload one series' results and record what was published.

    Raises whatever the uploader raises; the caller turns that into something a
    race officer can read. Nothing is recorded unless both objects went up, so
    the Published list never offers a link that was never written.
    """
    from core.video import upload_bytes_to_r2
    cfg = publish_config()
    if not publish_ready(cfg):
        raise RuntimeError(
            "Publishing needs the public Cloudflare R2 bucket set up in "
            "Settings → Video recording: account ID, bucket, access key, "
            "secret and public base URL.")
    when = when or datetime.now()
    body = html.encode("utf-8")
    keys = object_keys(series_id, series_name, when)
    dated_url = upload_bytes_to_r2(cfg, keys["dated"], body, "text/html; charset=utf-8",
                                   IMMUTABLE_CACHE)
    # The stable one second: if it fails, the dated object is still up and the
    # next publish will fix the link, which is the better way round.
    stable_url = upload_bytes_to_r2(cfg, keys["stable"], body, "text/html; charset=utf-8",
                                    LATEST_CACHE)
    record = {
        "series_id": int(series_id),
        "object_key": keys["dated"],
        "url": dated_url,
        "stable_url": stable_url,
        "size_bytes": len(body),
        "published_at": when.isoformat(timespec="seconds"),
        "published_by": actor,
    }
    _record(record)
    return record


def _record(record: Dict[str, Any]) -> None:
    init_db()
    with get_db() as db:
        db.execute(
            "INSERT INTO published_results (series_id, object_key, url, stable_url,"
            " size_bytes, published_at, published_by) VALUES (?,?,?,?,?,?,?)",
            (record["series_id"], record["object_key"], record["url"], record["stable_url"],
             record["size_bytes"], record["published_at"], record["published_by"]))
        db.commit()


def published_for_series(series_id: int, limit: int = 20) -> List[sqlite3.Row]:
    """What has been published for one series, newest first."""
    init_db()
    with get_db() as db:
        return db.execute(
            "SELECT * FROM published_results WHERE series_id = ?"
            " ORDER BY published_at DESC, id DESC LIMIT ?",
            (int(series_id), int(limit))).fetchall()


def latest_published(series_id: int) -> Optional[sqlite3.Row]:
    """The most recent publish for one series, or None."""
    rows = published_for_series(series_id, limit=1)
    return rows[0] if rows else None


def latest_published_by_series() -> Dict[int, Dict[str, Any]]:
    """The newest publish for every series, for pages that list many at once.

    One query rather than one per series: the competitor landing page draws a
    roll-up for every series of the season, and a query apiece is a query apiece
    on the page the club leaves open all day.
    """
    init_db()
    with get_db() as db:
        rows = db.execute(
            "SELECT p.* FROM published_results p"
            " JOIN (SELECT series_id, MAX(published_at) AS newest"
            "       FROM published_results GROUP BY series_id) m"
            "   ON m.series_id = p.series_id AND m.newest = p.published_at"
            " GROUP BY p.series_id").fetchall()
    return {int(row_get(r, "series_id")): dict(r) for r in rows}
