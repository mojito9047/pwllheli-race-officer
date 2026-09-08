"""Boat database import subsystem: rating-list fetching, parsing and upserts.

Extracted from app.py. Covers the rating-list CSV/URL source helpers, the
IRC/YTC listing readers/filters/prefill mappers, and the upserts that write
boats into the local database. Reads config (rating-list URLs, cache dir) from
core.appstate and uses core.db.get_db for database access; no Flask coupling.

The request/settings-coupled pieces stay in app.py: get_boat / search_boats
(which call init_db) and rating_lookup_results (which reads listing_config).
"""
from __future__ import annotations

import csv
import hashlib
import io
import re
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

import json

from core import appstate
from core.db import get_db, init_db
from core.timeutils import clean_sail_no, first_present, parse_float


def google_sheet_to_csv_url(url: str) -> str:
    """Convert a Google Sheets sharing/edit URL into a CSV export URL when possible."""
    url = (url or "").strip()
    if "docs.google.com/spreadsheets" not in url:
        return url
    match = re.search(r"/spreadsheets/d/([^/]+)", url)
    if not match:
        return url
    sheet_id = match.group(1)
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    gid = "0"
    if "gid" in query and query["gid"]:
        gid = query["gid"][0]
    else:
        frag = parse_qs(parsed.fragment)
        if "gid" in frag and frag["gid"]:
            gid = frag["gid"][0]
    return f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&gid={gid}"


def require_http_url(url: str, setting_name: str = "URL") -> str:
    """Return a trimmed http/https URL or raise ValueError."""
    url = (url or "").strip()
    parsed = urlparse(url)
    if parsed.scheme.lower() not in ("http", "https") or not parsed.netloc:
        raise ValueError(f"{setting_name} must be an http:// or https:// URL.")
    if parsed.username or parsed.password:
        raise ValueError(f"{setting_name} must not include embedded credentials.")
    return url


def safe_rating_listing_url(url: str, default_url: str) -> str:
    """Normalise a rating-list URL, falling back to a known safe default."""
    candidate = google_sheet_to_csv_url((url or default_url).strip() or default_url)
    try:
        require_http_url(candidate, "Rating-list URL")
    except ValueError:
        return default_url
    return (url or default_url).strip() or default_url


def _csv_cache_path(source: str) -> Path:
    """Return the local cache filename used for a remote rating-list URL."""
    appstate.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(source.encode("utf-8", errors="ignore")).hexdigest()[:24]
    return appstate.CACHE_DIR / f"ratings_{key}.csv"


# One download per listing at a time, whoever asked for it. The boat form warms
# these in the background the moment it opens (see warm_rating_listings), and
# the search the race officer then runs must *join* that download rather than
# start a second one -- otherwise the warm-up saves nothing and the club fetches
# 640 KB twice over the hut's 4G.
_fetch_lock = threading.Lock()
_fetch_inflight: Dict[str, threading.Event] = {}


def _fetch_csv_once(csv_url: str, cache_path: Path, timeout: int) -> bytes:
    """Download `csv_url` into `cache_path`, or wait for the download already
    doing it and read what it wrote.

    The waiter gets its own timeout, so a stalled fetch cannot pin a request
    open: past it, it falls through and downloads for itself.
    """
    with _fetch_lock:
        waiting = _fetch_inflight.get(csv_url)
        if waiting is None:
            _fetch_inflight[csv_url] = threading.Event()
            mine = True
        else:
            mine = False
    if not mine:
        if waiting.wait(timeout) and cache_path.exists():
            return cache_path.read_bytes()
        # Whoever was fetching gave up or is still going: fetch it ourselves
        # rather than hand back nothing.
        with urllib.request.urlopen(csv_url, timeout=timeout) as response:
            return response.read()
    try:
        with urllib.request.urlopen(csv_url, timeout=timeout) as response:
            raw = response.read()
        tmp = cache_path.with_suffix(".tmp")
        tmp.write_bytes(raw)
        tmp.replace(cache_path)
        return raw
    finally:
        with _fetch_lock:
            done = _fetch_inflight.pop(csv_url, None)
        if done is not None:
            done.set()


def read_csv_rows(source: str, timeout: int = 20, fallback_path: Optional[Path] = None, cache_seconds: Optional[int] = None) -> List[Dict[str, str]]:
    """Read CSV rows from a http/https URL or Google Sheet URL.

    Network rating listings can be large, so callers may pass cache_seconds to
    reuse a local copy. If the live fetch fails and a cached copy exists, the
    stale cache is used rather than making the boat lookup unusable at the hut.
    """
    source = (source or "").strip()
    raw: bytes
    cache_path: Optional[Path] = None
    try:
        csv_url = require_http_url(google_sheet_to_csv_url(source), "Rating-list URL")
        if cache_seconds:
            cache_path = _csv_cache_path(csv_url)
            if cache_path.exists() and (time.time() - cache_path.stat().st_mtime) < cache_seconds:
                raw = cache_path.read_bytes()
            else:
                raw = _fetch_csv_once(csv_url, cache_path, timeout)
        else:
            with urllib.request.urlopen(csv_url, timeout=timeout) as response:
                raw = response.read()
    except Exception:
        if cache_path and cache_path.exists():
            raw = cache_path.read_bytes()
        elif fallback_path and fallback_path.exists():
            raw = fallback_path.read_bytes()
        else:
            raise
    text = raw.decode("utf-8-sig", errors="replace")
    rows = list(csv.DictReader(io.StringIO(text)))
    return [{str(k or "").strip(): str(v or "").strip() for k, v in row.items()} for row in rows]


def boat_by_sail_no(db, sail_no: str, exclude_id: Optional[int] = None):
    """The boat carrying this sail number, ignoring case and spacing.

    A sail number is the one durable name a boat has, and `GBR 1210` and
    `GBR1210` are the same boat however they were typed. The rating importers
    have always matched this way; the Boats page did not, which is how four
    records for one boat came to exist.

    Matters more than tidiness. A series groups a competitor by ``boat_id``
    (see core.series.series_competitor_key), so a second record for the same
    boat is a second competitor: its results sit on a separate line, scored and
    discarded separately. Trackers attach to one boat id, and every stored GPS
    fix carries one, so a duplicate splits the boat's track as well.

    Inactive boats are included on purpose. Deleting a boat that has raced only
    deactivates it — the entries have to keep something to point at — so the
    record being looked for is very often an inactive one.
    """
    key = clean_sail_no(sail_no)
    if not key:
        return None                        # blank is not an identity; several boats may have none
    sql = ("SELECT * FROM boats"
           " WHERE REPLACE(REPLACE(UPPER(COALESCE(sail_no, '')), ' ', ''), CHAR(9), '') = ?")
    params: List[Any] = [key]
    if exclude_id is not None:
        sql += " AND id <> ?"
        params.append(int(exclude_id))
    return db.execute(sql + " ORDER BY id LIMIT 1", params).fetchone()


def duplicate_sail_numbers() -> Dict[str, int]:
    """Normalised sail numbers held by more than one boat, and how many hold them.

    Records that predate the check on adding, so they have to be found and tidied
    by hand. Counted across active *and* inactive boats: deleting a boat that has
    raced only deactivates it, so the older of a duplicated pair is usually the
    inactive one — and it is usually the one carrying the results.
    """
    init_db()
    with get_db() as db:
        rows = db.execute(
            "SELECT REPLACE(REPLACE(UPPER(COALESCE(sail_no, '')), ' ', ''), CHAR(9), '') AS key,"
            "       COUNT(*) AS n FROM boats"
            " WHERE TRIM(COALESCE(sail_no, '')) <> ''"
            " GROUP BY key HAVING COUNT(*) > 1").fetchall()
    return {str(r["key"]): int(r["n"]) for r in rows}


def get_boat(boat_id: int) -> Optional[sqlite3.Row]:
    """Load one boat-database row by id."""
    init_db()
    with get_db() as db:
        return db.execute("SELECT * FROM boats WHERE id = ?", (boat_id,)).fetchone()


def search_boats(q: str = "", include_inactive: bool = False, limit: int = 200) -> List[sqlite3.Row]:
    """Search active boats in the local boat database by name or sail number."""
    init_db()
    q = (q or "").strip()
    with get_db() as db:
        params: List[Any] = []
        where = []
        if not include_inactive:
            where.append("status = 'ACTIVE'")
        if q:
            like = f"%{q}%"
            where.append("(boat_name LIKE ? OR sail_no LIKE ? OR owner LIKE ?)")
            params.extend([like, like, like])
        sql = "SELECT * FROM boats"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY boat_name COLLATE NOCASE LIMIT ?"
        params.append(limit)
        return db.execute(sql, params).fetchall()


def boat_rating_for_rule(boat, rating_rule: str, rating_source: str = "AUTO") -> Tuple[float, str]:
    """Return the boat rating field appropriate for an IRC/YTC rule selector."""
    source = (rating_source or "AUTO").upper()
    rule = (rating_rule or "IRC_TCC").upper()
    rating: Optional[float] = None
    label = source
    if source == "IRC_NS":
        rating = boat["irc_non_spinnaker_tcc"]
        label = "IRC non-spinnaker TCC"
    elif source == "IRC":
        rating = boat["irc_rating"]
        label = "IRC TCC"
    elif source == "YTC":
        rating = boat["ytc_rating"]
        label = "YTC"
    else:
        if rule.startswith("YTC"):
            rating = boat["ytc_rating"]
            label = "YTC"
        elif rule == "NONE":
            rating = 1.0
            label = "No rating"
        elif rule == "DUAL":
            # Entry-admin legacy rating; official results read IRC/YTC directly
            # from the boat database. Prefer IRC if present, otherwise YTC.
            rating = boat["irc_rating"] if boat["irc_rating"] is not None else boat["ytc_rating"]
            label = "Boat database rating"
        else:
            rating = boat["irc_rating"]
            label = "IRC TCC"
    if rating is None:
        rating = 1.0
        label += " missing - using 1.000 for entry admin"
    return float(rating), label


def upsert_boat_from_irc_row(row: Dict[str, str]) -> int:
    """Create or update a boat record from an IRC listing row."""
    name = (row.get("Boat Name") or row.get("Boat") or row.get("Name") or "").strip()
    sail = (row.get("Sail No") or row.get("Sail") or row.get("SailNo") or "").strip()
    if not name and not sail:
        raise ValueError("IRC row has no boat name or sail number")
    tcc = parse_float(row.get("TCC"))
    ns_tcc = parse_float(row.get("Non Spi TCC"))
    cert_no = (row.get("Cert No") or "").strip()
    issue_date = (row.get("Issue Date") or "").strip()
    cert_year = (row.get("Cert Year") or "").strip()
    now = datetime.now().isoformat(timespec="seconds")
    with get_db() as db:
        existing = None
        if sail:
            existing = db.execute("SELECT * FROM boats WHERE REPLACE(UPPER(COALESCE(sail_no,'')), ' ', '') = ?", (clean_sail_no(sail),)).fetchone()
        if existing is None and name:
            existing = db.execute("SELECT * FROM boats WHERE UPPER(boat_name) = UPPER(?)", (name,)).fetchone()
        if existing:
            db.execute(
                """
                UPDATE boats
                SET boat_name = COALESCE(NULLIF(?, ''), boat_name), sail_no = COALESCE(NULLIF(?, ''), sail_no),
                    irc_rating = ?, irc_non_spinnaker_tcc = ?, irc_cert_no = ?, irc_issue_date = ?, irc_cert_year = ?,
                    status = 'ACTIVE', source = 'RORC IRC listing', source_updated_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (name, sail, tcc, ns_tcc, cert_no, issue_date, cert_year, now, now, existing["id"]),
            )
            boat_id = int(existing["id"])
        else:
            cur = db.execute(
                """
                INSERT INTO boats (boat_name, sail_no, irc_rating, irc_non_spinnaker_tcc, irc_cert_no, irc_issue_date,
                                   irc_cert_year, status, source, source_updated_at, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'ACTIVE', 'RORC IRC listing', ?, ?, ?)
                """,
                (name or sail, sail, tcc, ns_tcc, cert_no, issue_date, cert_year, now, now, now),
            )
            boat_id = int(cur.lastrowid)
        db.commit()
        return boat_id


def read_irc_listing(url: str, timeout: int = 20) -> List[Dict[str, str]]:
    """Read the cached or live IRC listing as normalised CSV rows.

    ClubListing.csv is no longer bundled in data/ because current IRC data is
    downloaded and cached under runtime/cache/.
    """
    return read_csv_rows(url or appstate.IRC_LISTING_URL, timeout=timeout, fallback_path=None, cache_seconds=3600)


def filter_irc_rows(rows: List[Dict[str, str]], q: str, limit: int = 50) -> List[Dict[str, str]]:
    """Filter IRC listing rows by boat name or sail number."""
    q_norm = q.strip().lower()
    sail_norm = clean_sail_no(q)
    matches: List[Dict[str, str]] = []
    for row in rows:
        name = row.get("Boat Name", "")
        sail = row.get("Sail No", "")
        if not q_norm or q_norm in name.lower() or q_norm in sail.lower() or (sail_norm and sail_norm in clean_sail_no(sail)):
            matches.append(row)
        if len(matches) >= limit:
            break
    return matches


YTC_NAME_ALIASES = ["Boat Name", "Yacht Name", "Boat", "Yacht", "Name"]
YTC_SAIL_ALIASES = ["Sail No", "Sail Number", "Sail", "SailNo", "Sail Num", "Sail Num."]
YTC_RATING_ALIASES = ["YTC", "YTC Rating", "YTC TCF", "TCF", "Rating", "Handicap", "Base TCF", "Base Rating"]
YTC_OWNER_ALIASES = ["Owner", "Owner / Skipper", "Skipper", "Helm"]
YTC_DESIGN_ALIASES = ["Design", "Boat Type", "Type", "Class", "Model"]
YTC_CLUB_ALIASES = ["Club", "Club Name", "Sailing Club", "Yacht Club"]
YTC_DATE_ALIASES = ["Date", "Issue Date", "Updated", "Last Updated", "Certificate Date"]


def normalise_ytc_row(row: Dict[str, str]) -> Dict[str, str]:
    """Normalise a YTC spreadsheet row with tolerant column names."""
    return {
        "boat_name": first_present(row, YTC_NAME_ALIASES),
        "sail_no": first_present(row, YTC_SAIL_ALIASES),
        "rating": first_present(row, YTC_RATING_ALIASES),
        "owner": first_present(row, YTC_OWNER_ALIASES),
        "design": first_present(row, YTC_DESIGN_ALIASES),
        "club": first_present(row, YTC_CLUB_ALIASES),
        "issue_date": first_present(row, YTC_DATE_ALIASES),
        "raw": json.dumps(row, ensure_ascii=False),
    }


def read_ytc_listing(url: str, timeout: int = 20) -> List[Dict[str, str]]:
    """Read the cached or live YTC listing as normalised CSV rows."""
    return read_csv_rows(url or appstate.YTC_LISTING_URL, timeout=timeout, cache_seconds=3600)


LISTING_CACHE_SECONDS = 3600


def rating_listings_are_warm(irc_url: str, ytc_url: str) -> bool:
    """True when both listings are cached and fresh, so a search will be instant."""
    for url in (irc_url or appstate.IRC_LISTING_URL, ytc_url or appstate.YTC_LISTING_URL):
        try:
            path = _csv_cache_path(require_http_url(google_sheet_to_csv_url(url), "Rating-list URL"))
        except Exception:
            return False
        if not path.exists() or (time.time() - path.stat().st_mtime) >= LISTING_CACHE_SECONDS:
            return False
    return True


def warm_rating_listings(irc_url: str = "", ytc_url: str = "") -> None:
    """Start downloading both rating listings, without waiting for them.

    Opening the add/edit boat form is the reliable sign that a lookup is coming:
    the race officer has to type a name before they can search, and that gap is
    long enough to cover the download. 640 KB of IRC and YTC comes over the
    hut's 4G in about three seconds on a good day and rather longer on a bad
    one, and every second of it used to be spent staring at a submitted form.

    The two go in parallel because they are independent and IRC is much the
    slower. Failure is silent by design: the search that follows will report a
    listing it cannot reach, and there is nothing useful to say about a
    speculative fetch that did not come off. ``_fetch_csv_once`` is what makes
    this worth doing -- the search joins this download rather than starting its
    own.
    """
    # Nothing to warm when both are already cached and fresh, which is the usual
    # case. Without this check the reader still ran, and reading the cache means
    # re-parsing 650 KB of CSV -- 24 ms of pointless work behind every load of
    # the page, thrown at the CPU while the next page is being rendered.
    if rating_listings_are_warm(irc_url, ytc_url):
        return
    for url, reader in ((irc_url or appstate.IRC_LISTING_URL, read_irc_listing),
                        (ytc_url or appstate.YTC_LISTING_URL, read_ytc_listing)):
        def warm(u=url, read=reader):
            try:
                read(u)
            except Exception:
                pass
        threading.Thread(target=warm, name="rating-listing-warm", daemon=True).start()


def filter_ytc_rows(rows: List[Dict[str, str]], q: str, limit: int = 75) -> List[Dict[str, str]]:
    """Filter YTC listing rows by boat name or sail number."""
    q_norm = q.strip().lower()
    sail_norm = clean_sail_no(q)
    matches: List[Dict[str, str]] = []
    for raw in rows:
        row = normalise_ytc_row(raw)
        name = row.get("boat_name", "")
        sail = row.get("sail_no", "")
        rating = parse_float(row.get("rating"))
        if rating is None or (not name and not sail):
            continue
        if not q_norm or q_norm in name.lower() or q_norm in sail.lower() or (sail_norm and sail_norm in clean_sail_no(sail)):
            matches.append(row)
        if len(matches) >= limit:
            break
    return matches


def upsert_boat_from_ytc_row(row: Dict[str, str]) -> int:
    """Create or update a boat record from a YTC listing row."""
    name = (row.get("boat_name") or "").strip()
    sail = (row.get("sail_no") or "").strip()
    if not name and not sail:
        raise ValueError("YTC row has no boat name or sail number")
    rating = parse_float(row.get("rating"))
    if rating is None:
        raise ValueError("YTC row has no usable rating/TCF")
    owner = (row.get("owner") or "").strip()
    design = (row.get("design") or "").strip()
    club = (row.get("club") or "").strip()
    issue_date = (row.get("issue_date") or "").strip()
    now = datetime.now().isoformat(timespec="seconds")
    with get_db() as db:
        existing = None
        if sail:
            existing = db.execute("SELECT * FROM boats WHERE REPLACE(UPPER(COALESCE(sail_no,'')), ' ', '') = ?", (clean_sail_no(sail),)).fetchone()
        if existing is None and name:
            existing = db.execute("SELECT * FROM boats WHERE UPPER(boat_name) = UPPER(?)", (name,)).fetchone()
        if existing:
            db.execute(
                """
                UPDATE boats
                SET boat_name = COALESCE(NULLIF(?, ''), boat_name),
                    sail_no = COALESCE(NULLIF(?, ''), sail_no),
                    owner = COALESCE(NULLIF(?, ''), owner),
                    design = COALESCE(NULLIF(?, ''), design),
                    club = COALESCE(NULLIF(?, ''), club),
                    ytc_rating = ?,
                    status = 'ACTIVE',
                    source = CASE
                        WHEN source IS NULL OR source = '' THEN 'YTC sheet'
                        WHEN source LIKE '%YTC sheet%' THEN source
                        ELSE source || '; YTC sheet'
                    END,
                    source_updated_at = COALESCE(NULLIF(?, ''), ?),
                    updated_at = ?
                WHERE id = ?
                """,
                (name, sail, owner, design, club, rating, issue_date, now, now, existing["id"]),
            )
            boat_id = int(existing["id"])
        else:
            cur = db.execute(
                """
                INSERT INTO boats (boat_name, sail_no, owner, design, club, ytc_rating, status, source, source_updated_at, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 'ACTIVE', 'YTC sheet', ?, ?, ?)
                """,
                (name or sail, sail, owner, design, club, rating, issue_date or now, now, now),
            )
            boat_id = int(cur.lastrowid)
        db.commit()
        return boat_id


def irc_row_to_prefill(row: Dict[str, str]) -> Dict[str, Any]:
    """Convert an IRC listing row into values for the add/edit boat form."""
    return {
        "source": "IRC",
        "source_label": "IRC listing",
        "boat_name": (row.get("Boat Name") or row.get("Boat") or row.get("Name") or "").strip(),
        "sail_no": (row.get("Sail No") or row.get("Sail") or row.get("SailNo") or "").strip(),
        "owner": "",
        "design": "",
        "club": "",
        "irc_rating": row.get("TCC", ""),
        "irc_non_spinnaker_tcc": row.get("Non Spi TCC", ""),
        "irc_cert_no": row.get("Cert No", ""),
        "irc_issue_date": row.get("Issue Date", ""),
        "irc_cert_year": row.get("Cert Year", ""),
        "ytc_rating": "",
    }


def ytc_row_to_prefill(row: Dict[str, str]) -> Dict[str, Any]:
    """Convert a YTC listing row into values for the add/edit boat form."""
    return {
        "source": "YTC",
        "source_label": "YTC sheet",
        "boat_name": row.get("boat_name", ""),
        "sail_no": row.get("sail_no", ""),
        "owner": row.get("owner", ""),
        "design": row.get("design", ""),
        "club": row.get("club", ""),
        "irc_rating": "",
        "irc_non_spinnaker_tcc": "",
        "irc_cert_no": "",
        "irc_issue_date": "",
        "irc_cert_year": "",
        "ytc_rating": row.get("rating", ""),
        "ytc_issue_date": row.get("issue_date", ""),
    }


# ---------------------------------------------------------------------------
# Ratings, wherever they are recorded
# ---------------------------------------------------------------------------

def find_boat_ratings(query: str, listing_urls: Optional[Dict[str, str]] = None,
                      timeout: int = 20) -> Dict[str, Any]:
    """Everything the club knows about one boat's ratings, by name or sail number.

    Three places hold a rating and they are not the same thing. The **boat
    database** is what a race actually scores on, because an entry snapshots the
    rating at the moment the boat is added. The **RORC IRC listing** and the
    **YTC sheet** are the sources it was taken from, and either can have moved
    since. So all three are reported rather than the first one found: a race
    officer asking "what is she rated?" is usually asking because something does
    not look right.

    The listings are only read when the boat is not in the database, or when it
    is and has no rating: they are an HTTP fetch (cached for an hour), and the
    question is nearly always about a boat the club already has.
    """
    urls = listing_urls or {}
    found: Dict[str, Any] = {"query": query, "boat": None, "irc": [], "ytc": [], "errors": []}
    wanted = (query or "").strip()
    if not wanted:
        return found

    local = [b for b in search_boats(wanted, include_inactive=True, limit=10)]
    exact = [b for b in local
             if str(b["boat_name"]).strip().lower() == wanted.lower()
             or clean_sail_no(str(b["sail_no"] or "")) == clean_sail_no(wanted)]
    if exact:
        found["boat"] = exact[0]
    elif len(local) == 1:
        found["boat"] = local[0]
    found["other_local"] = [b for b in local if found["boat"] is None or b["id"] != found["boat"]["id"]]

    boat = found["boat"]
    has_both = boat is not None and boat["irc_rating"] is not None and boat["ytc_rating"] is not None
    if has_both:
        return found

    # Only now, and each listing separately: one being unreachable must not hide
    # the other, which is exactly what a single try/except around both would do.
    if urls.get("irc_listing_url"):
        try:
            found["irc"] = [irc_row_to_prefill(row) for row in
                            filter_irc_rows(read_irc_listing(urls["irc_listing_url"], timeout), wanted, limit=6)]
        except Exception as exc:
            found["errors"].append(f"the IRC listing could not be read ({exc})")
    if urls.get("ytc_listing_url"):
        try:
            found["ytc"] = [ytc_row_to_prefill(row) for row in
                            filter_ytc_rows(read_ytc_listing(urls["ytc_listing_url"], timeout), wanted, limit=6)]
        except Exception as exc:
            found["errors"].append(f"the YTC sheet could not be read ({exc})")
    return found


def insert_boat_from_listings(name: str, sail_no: str = "", irc: Optional[Dict[str, Any]] = None,
                              ytc: Optional[Dict[str, Any]] = None) -> Tuple[int, str]:
    """Add a boat to the database from the rating listings. Never updates one.

    The import screens use `upsert_boat_from_*`, which is right there: somebody
    looking at a listing row beside the existing record has decided to take the
    new one. This is the other case — a boat named in passing, from a phone —
    and there the safe answer is that an existing record wins and the caller is
    told so. Overwriting a rating from the water, without the record in front of
    you, is how a season gets scored on the wrong number.

    Returns (boat_id, what_happened): "created" or "exists".
    """
    name = (name or "").strip()
    sail = (sail_no or "").strip()
    if not name and not sail:
        raise ValueError("A boat needs a name or a sail number.")
    init_db()
    now = datetime.now().isoformat(timespec="seconds")
    with get_db() as db:
        existing = None
        if sail:
            existing = db.execute(
                "SELECT * FROM boats WHERE REPLACE(UPPER(COALESCE(sail_no,'')), ' ', '') = ?",
                (clean_sail_no(sail),)).fetchone()
        if existing is None and name:
            existing = db.execute("SELECT * FROM boats WHERE UPPER(boat_name) = UPPER(?)",
                                  (name,)).fetchone()
        if existing is not None:
            return int(existing["id"]), "exists"
        irc = irc or {}
        ytc = ytc or {}
        cur = db.execute(
            """
            INSERT INTO boats (boat_name, sail_no, owner, design, club, irc_rating,
                               irc_non_spinnaker_tcc, irc_cert_no, irc_issue_date, irc_cert_year,
                               ytc_rating, status, source, source_updated_at, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ACTIVE', ?, ?, ?, ?)
            """,
            (name or sail, sail, irc.get("owner") or ytc.get("owner") or "",
             irc.get("design") or ytc.get("design") or "",
             irc.get("club") or ytc.get("club") or "",
             parse_float(irc.get("irc_rating")), parse_float(irc.get("irc_non_spinnaker_tcc")),
             irc.get("irc_cert_no") or "", irc.get("irc_issue_date") or "",
             irc.get("irc_cert_year") or "", parse_float(ytc.get("ytc_rating")),
             "RORC IRC listing" if irc else ("YTC sheet" if ytc else "added from the water"),
             now, now, now),
        )
        db.commit()
        return int(cur.lastrowid), "created"
