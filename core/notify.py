# Copyright © 2026 CapeNet Ltd. MIT licence; see LICENSE.
"""Phone notifications for people following a race.

Three jobs, kept apart:

* **Who asked** -- the subscriptions: one row per browser that said yes, with the
  two kinds of news it wants (race notices; its favourite boats) and which boats
  those are. No names, no accounts: a push address and a list of sail numbers.
* **What happened** -- the watcher. It reads the race office's own records and
  works out what is news: a race started, postponed, shortened or abandoned; a
  boat finished; the last boat home and the results up.
* **Telling them** -- the sender, which hands each notice to ``core.webpush``
  for every subscription it is for.

**The watcher only reads.** It is not called from the start sequence, the finish
button or the GPS finish detector, and none of them knows it exists. It polls the
race log and the entries every few seconds and compares them with what it saw
last time. That costs a few seconds' delay, and buys two things worth far more:
no notification can ever hold up a gun or a finish, and a finish recorded by any
of the five routes the app has for recording one is noticed without each of them
having to remember to say so.

**Its own database** (``data/push.db``), for the reason ``track_positions.db``
has one: a subscribe request comes from the public internet, and nothing from
out there should be able to hold a lock on the file the finishes are written to.
It also holds the club's push key pair, which every subscription is tied to, so
it is part of the backup.

**Nothing old is announced.** The watcher's first look is its baseline: the world
as it finds it. A restart mid-race announces nothing that had already happened.
While notifications are off, or nobody has subscribed, it does not look at all,
and starts from a fresh baseline when there is somebody to tell, so switching
notifications on does not replay the season.

**A finish is held for a moment before it is told.** An automatic GPS finish can
be wrong, and the race officer removes it; a notification cannot be taken back.
So a finish is announced only if it is still there ``FINISH_HOLD_S`` later.
"""
from __future__ import annotations

import json
import queue
import re
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from core import appstate, webpush
from core.db import get_db, init_db
from core.horn import public_base_url
from core.publicapp import boat_follow_key
from core.settings import get_app_setting_overrides, text_to_bool

PUSH_DB_PATH = appstate.DATA_DIR / "push.db"
PUSH_DB_INIT_LOCK = threading.Lock()
PUSH_DB_INITIALIZED = False

# Bounds on what the public internet can make this PC store and do.
MAX_SUBSCRIPTIONS = 2000            # a club's following, many times over
MAX_BOAT_KEYS = 12                  # as many favourites as a phone keeps
MAX_ENDPOINT_CHARS = 1000
SUBSCRIBE_LIMIT = 30                # subscribe/unsubscribe requests...
SUBSCRIBE_WINDOW_S = 900.0          # ...per address, per quarter of an hour

POLL_S = 5.0                        # how often the watcher looks
FINISH_HOLD_S = 45.0                # a finish must still be there this long after it appears
RESULTS_HOLD_S = 60.0               # and "all boats in" this long
ACTIVE_RACE_DAYS = 3                # races older than this are history, not news
SEND_WORKERS = 8                    # subscriptions written to at once
RETRY_DELAYS_S = (30.0, 120.0, 600.0)
MAX_RETRY_BACKLOG = 200             # messages waiting for another go; beyond this they are dropped

_KEY_CHARS = re.compile(r"[^A-Z0-9]")


# --------------------------------------------------------------------------- database

def get_push_db() -> sqlite3.Connection:
    # A short wait, not the race database's thirty seconds: nothing written here
    # is worth holding a public request for.
    conn = sqlite3.connect(PUSH_DB_PATH, timeout=5.0)
    conn.row_factory = sqlite3.Row
    return conn


def init_push_db() -> None:
    """Create the schema once per process (own guard, own database)."""
    global PUSH_DB_INITIALIZED
    if PUSH_DB_INITIALIZED and PUSH_DB_PATH.exists():
        return
    with PUSH_DB_INIT_LOCK:
        if PUSH_DB_INITIALIZED and PUSH_DB_PATH.exists():
            return
        PUSH_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        with get_push_db() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS subscriptions (
                    endpoint TEXT PRIMARY KEY,
                    p256dh TEXT NOT NULL,
                    auth TEXT NOT NULL,
                    notices INTEGER NOT NULL DEFAULT 1,
                    boats INTEGER NOT NULL DEFAULT 1,
                    boat_keys TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_ok_at TEXT
                )
                """
            )
            conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            conn.commit()
        PUSH_DB_INITIALIZED = True


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


# --------------------------------------------------------------------------- settings

def notify_config() -> Dict[str, Any]:
    """Settings -> Phone notifications. Off until the club switches it on."""
    try:
        overrides = get_app_setting_overrides()
    except Exception:
        overrides = {}
    return {"enabled": text_to_bool(overrides.get("push_enabled"), False)}


def ready() -> Tuple[bool, str]:
    """Whether a notification could be sent at all, and if not, why, in plain words."""
    if not webpush.available():
        return False, "The pycryptodomex package is not installed."
    if not public_base_url().startswith("https://"):
        return False, ("Set the public address (https) under Settings → Server. Phones only accept "
                       "notifications from a secure address, and the push services are told it.")
    return True, ""


def vapid_keys() -> Tuple[str, str]:
    """The club's push key pair: (private PEM, public base64url). Made on first use.

    Kept in push.db rather than in the settings tables because nobody types it,
    nobody should see it, and it must travel with the subscriptions it signs for.
    """
    init_push_db()
    with get_push_db() as conn:
        rows = {r["key"]: r["value"] for r in conn.execute(
            "SELECT key, value FROM meta WHERE key IN ('vapid_private_pem', 'vapid_public')")}
        if "vapid_private_pem" not in rows or "vapid_public" not in rows:
            private_pem, public = webpush.generate_vapid_keys()
            # INSERT OR IGNORE, then read back: two requests arriving together on
            # a new installation must end up agreeing on one key pair.
            conn.execute("INSERT OR IGNORE INTO meta (key, value) VALUES ('vapid_private_pem', ?)", (private_pem,))
            conn.commit()
            private_pem = conn.execute(
                "SELECT value FROM meta WHERE key = 'vapid_private_pem'").fetchone()["value"]
            public = webpush.public_key_from_private(private_pem)
            conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('vapid_public', ?)", (public,))
            conn.commit()
            return private_pem, public
    return rows["vapid_private_pem"], rows["vapid_public"]


def public_config() -> Dict[str, Any]:
    """What a public page is told: whether to offer notifications, and the key to subscribe with."""
    if not notify_config()["enabled"] or not ready()[0]:
        return {"enabled": False, "key": ""}
    try:
        return {"enabled": True, "key": vapid_keys()[1]}
    except Exception:
        return {"enabled": False, "key": ""}


# --------------------------------------------------------------------------- subscriptions

class SubscriptionRefused(ValueError):
    """A subscribe request that cannot be kept. The message is safe to show."""


def clean_boat_keys(keys: Any) -> List[str]:
    """Favourite keys as the server would have issued them, and no more than a phone keeps."""
    out: List[str] = []
    for key in (keys if isinstance(keys, (list, tuple)) else []):
        tidy = _KEY_CHARS.sub("", str(key or "").upper())[:40]
        if tidy and tidy not in out:
            out.append(tidy)
        if len(out) >= MAX_BOAT_KEYS:
            break
    return out


def _valid_keys(p256dh: str, auth: str) -> bool:
    try:
        public = webpush.b64url_decode(p256dh)
        secret = webpush.b64url_decode(auth)
    except Exception:
        return False
    return len(public) == 65 and public[0] == 4 and len(secret) == 16


def subscribe(endpoint: Any, p256dh: Any, auth: Any, *, notices: bool = True, boats: bool = True,
              boat_keys: Any = ()) -> None:
    """Keep (or bring up to date) one browser's request for notifications."""
    endpoint, p256dh, auth = str(endpoint or "").strip(), str(p256dh or "").strip(), str(auth or "").strip()
    if len(endpoint) > MAX_ENDPOINT_CHARS or not webpush.endpoint_allowed(endpoint):
        raise SubscriptionRefused("That is not a browser's push address.")
    if not _valid_keys(p256dh, auth):
        raise SubscriptionRefused("The subscription's keys are not valid.")
    keys = clean_boat_keys(boat_keys)
    stored_keys = ("," + ",".join(keys) + ",") if keys else ""
    now = _now_iso()
    init_push_db()
    with get_push_db() as conn:
        known = conn.execute("SELECT 1 FROM subscriptions WHERE endpoint = ?", (endpoint,)).fetchone()
        if not known:
            count = conn.execute("SELECT COUNT(*) AS n FROM subscriptions").fetchone()["n"]
            if count >= MAX_SUBSCRIPTIONS:
                raise SubscriptionRefused("No more phones can be added just now.")
            conn.execute(
                "INSERT INTO subscriptions (endpoint, p256dh, auth, notices, boats, boat_keys, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (endpoint, p256dh, auth, int(bool(notices)), int(bool(boats)), stored_keys, now, now))
        else:
            conn.execute(
                "UPDATE subscriptions SET p256dh = ?, auth = ?, notices = ?, boats = ?, boat_keys = ?,"
                " updated_at = ? WHERE endpoint = ?",
                (p256dh, auth, int(bool(notices)), int(bool(boats)), stored_keys, now, endpoint))
        conn.commit()


def unsubscribe(endpoint: Any) -> bool:
    init_push_db()
    with get_push_db() as conn:
        cur = conn.execute("DELETE FROM subscriptions WHERE endpoint = ?", (str(endpoint or "").strip(),))
        conn.commit()
        return cur.rowcount > 0


def subscription_count() -> int:
    init_push_db()
    with get_push_db() as conn:
        return int(conn.execute("SELECT COUNT(*) AS n FROM subscriptions").fetchone()["n"])


def subscriptions_for(notice: "Notice") -> List[Dict[str, Any]]:
    """The subscriptions a notice is for."""
    init_push_db()
    with get_push_db() as conn:
        if notice.topic == "everyone":
            rows = conn.execute("SELECT endpoint, p256dh, auth FROM subscriptions").fetchall()
        elif notice.topic == "boats":
            if not notice.boat_key:
                return []
            rows = conn.execute(
                "SELECT endpoint, p256dh, auth FROM subscriptions WHERE boats = 1 AND boat_keys LIKE ?",
                (f"%,{notice.boat_key},%",)).fetchall()
        else:
            rows = conn.execute("SELECT endpoint, p256dh, auth FROM subscriptions WHERE notices = 1").fetchall()
    return [dict(r) for r in rows]


# One request in a while is a phone; a stream of them is not. Kept in memory: it
# only has to slow somebody down, and a restart forgetting it costs nothing.
_RATE: Dict[str, List[float]] = {}
_RATE_LOCK = threading.Lock()


def allow_request(address: str, now: Optional[float] = None) -> bool:
    """Whether this address may make another subscribe/unsubscribe request yet."""
    now = time.time() if now is None else now
    address = str(address or "unknown")[:80]
    with _RATE_LOCK:
        if len(_RATE) > 5000:               # never let the table itself become the problem
            _RATE.clear()
        recent = [t for t in _RATE.get(address, []) if now - t < SUBSCRIBE_WINDOW_S]
        if len(recent) >= SUBSCRIBE_LIMIT:
            _RATE[address] = recent
            return False
        recent.append(now)
        _RATE[address] = recent
        return True


def reset_rate_limit() -> None:
    with _RATE_LOCK:
        _RATE.clear()


# --------------------------------------------------------------------------- notices

@dataclass
class Notice:
    """One piece of news, and who it is for."""
    topic: str                  # "notices", "boats" (followers of boat_key) or "everyone"
    race_id: int
    title: str
    body: str
    kind: str = "notice"
    boat_key: str = ""
    entry_id: int = 0
    urgency: str = "normal"
    ttl: int = 3600             # how long a push service keeps it for a phone that is off

    def tag(self) -> str:
        """Same race, same kind of news: a later notice replaces an earlier one on the phone."""
        base = f"r{int(self.race_id)}-{self.kind}"
        return f"{base}-{int(self.entry_id)}" if self.entry_id else base

    def payload(self) -> bytes:
        url = f"/public/race/{int(self.race_id)}" if self.race_id else "/public/current"
        return json.dumps({"title": self.title, "body": self.body, "url": url, "tag": self.tag()},
                          ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _clock(iso: Any, seconds: bool = False) -> str:
    """The time of day out of an ISO timestamp, as people say it: 14:05."""
    text = str(iso or "")
    part = text.split("T", 1)[1] if "T" in text else text
    part = part.split(".", 1)[0]
    bits = part.split(":")
    if len(bits) < 2:
        return ""
    return ":".join(bits[:3]) if seconds and len(bits) >= 3 else ":".join(bits[:2])


def _details(raw: Any) -> Dict[str, Any]:
    try:
        value = json.loads(raw) if isinstance(raw, str) and raw else {}
    except ValueError:
        value = {}
    return value if isinstance(value, dict) else {}


# --------------------------------------------------------------------------- reading the race office

def _read_max_event_id() -> int:
    init_db()
    with get_db() as db:
        row = db.execute("SELECT COALESCE(MAX(id), 0) AS n FROM race_events").fetchone()
    return int(row["n"])


def _read_events_after(after_id: int) -> List[Dict[str, Any]]:
    init_db()
    with get_db() as db:
        rows = db.execute(
            "SELECT ev.id, ev.race_id, ev.event_time, ev.event_type, ev.label, ev.source, ev.details,"
            " r.name AS race_name FROM race_events ev JOIN races r ON r.id = ev.race_id"
            " WHERE ev.id > ? ORDER BY ev.id ASC LIMIT 200", (int(after_id),)).fetchall()
    return [dict(r) for r in rows]


def _read_had_earlier_start(race_id: int, before_id: int) -> bool:
    init_db()
    with get_db() as db:
        row = db.execute(
            "SELECT 1 FROM race_events WHERE race_id = ? AND id < ? AND event_type = 'horn'"
            " AND source = 'central-start-sequence' AND label LIKE '%Start signal' LIMIT 1",
            (int(race_id), int(before_id))).fetchone()
    return row is not None


def _read_active_entries(now: float) -> List[Dict[str, Any]]:
    cutoff = (datetime.fromtimestamp(now) - timedelta(days=ACTIVE_RACE_DAYS)).isoformat(timespec="seconds")
    init_db()
    with get_db() as db:
        rows = db.execute(
            "SELECT e.id AS entry_id, e.race_id, e.boat_name, e.sail_no, e.status, e.finish_time,"
            " r.name AS race_name FROM entries e JOIN races r ON r.id = e.race_id"
            " WHERE r.start_time >= ?", (cutoff,)).fetchall()
    return [dict(r) for r in rows]


def _finished(entry: Optional[Dict[str, Any]]) -> bool:
    return bool(entry) and str(entry.get("status") or "").upper() == "FINISHED" and bool(entry.get("finish_time"))


def _racing(entry: Dict[str, Any]) -> bool:
    return str(entry.get("status") or "").upper() == "RACING"


class Watcher:
    """Works out what is news by comparing the race office's records with last time.

    The four readers are parameters so the tests can drive it with a list and a
    clock; in service they are the queries above.
    """

    def __init__(self,
                 max_event_id: Callable[[], int] = _read_max_event_id,
                 events_after: Callable[[int], List[Dict[str, Any]]] = _read_events_after,
                 had_earlier_start: Callable[[int, int], bool] = _read_had_earlier_start,
                 active_entries: Callable[[float], List[Dict[str, Any]]] = _read_active_entries) -> None:
        self._max_event_id = max_event_id
        self._events_after = events_after
        self._had_earlier_start = had_earlier_start
        self._active_entries = active_entries
        self.last_event_id: Optional[int] = None
        self.entries: Optional[Dict[int, Dict[str, Any]]] = None
        self.finish_due: Dict[int, float] = {}
        self.finish_told: set = set()
        self.results_due: Dict[int, float] = {}
        self.results_told: set = set()

    def poll(self, now: Optional[float] = None) -> List[Notice]:
        now = time.time() if now is None else now
        snapshot = {int(e["entry_id"]): e for e in self._active_entries(now)}
        if self.last_event_id is None or self.entries is None:
            # The baseline: the world as found. Nothing in it is news.
            self.last_event_id = self._max_event_id()
            self.entries = snapshot
            return []
        notices = self._from_events()
        notices += self._from_entries(snapshot, now)
        self.entries = snapshot
        return notices

    # -- the race log: start, postponement, shortened course, abandonment ------
    def _from_events(self) -> List[Notice]:
        notices: List[Notice] = []
        for event in self._events_after(int(self.last_event_id or 0)):
            self.last_event_id = max(int(self.last_event_id or 0), int(event["id"]))
            notice = self._notice_for_event(event)
            if notice:
                notices.append(notice)
        return notices

    def _notice_for_event(self, event: Dict[str, Any]) -> Optional[Notice]:
        race_id = event.get("race_id")
        if race_id is None:
            return None
        race_id = int(race_id)
        title = str(event.get("race_name") or "Race")
        kind = str(event.get("event_type") or "")
        label = str(event.get("label") or "")
        source = str(event.get("source") or "")
        at = _clock(event.get("event_time"))
        if kind == "horn" and source == "central-start-sequence" and label.endswith("Start signal"):
            # The first start of the race only. A second fleet's start, minutes
            # later, is the same race still starting.
            if self._had_earlier_start(race_id, int(event["id"])):
                return None
            return Notice("notices", race_id, title, f"Started at {at}." if at else "Started.",
                          kind="start", urgency="high", ttl=1800)
        if kind == "postpone":
            details = _details(event.get("details"))
            if label.startswith("Postponed"):
                flag = str(details.get("flag") or "AP")
                return Notice("notices", race_id, title, f"Postponed. {flag} is flying.",
                              kind="postpone", urgency="high", ttl=3600)
            if label.startswith("Postponement ending"):
                warning = _clock(details.get("warning_time"))
                body = (f"Postponement ending. Warning signal at {warning}." if warning
                        else "Postponement ending.")
                return Notice("notices", race_id, title, body, kind="postpone", urgency="high", ttl=1800)
            return None         # "AP down" and the one-minute call: said already
        if kind == "shorten":
            if label.startswith("Shortened course at mark"):
                return Notice("notices", race_id, title, label.rstrip(".") + ".", kind="shorten", ttl=3600)
            if "cleared" in label.lower():
                return Notice("notices", race_id, title, "The course is no longer shortened.",
                              kind="shorten", ttl=3600)
            return None
        if kind == "abandon":
            return Notice("notices", race_id, title, "Race abandoned.", kind="abandon",
                          urgency="high", ttl=6 * 3600)
        return None

    # -- the entries: a boat finishing, and the last boat home ------------------
    def _from_entries(self, snapshot: Dict[int, Dict[str, Any]], now: float) -> List[Notice]:
        notices: List[Notice] = []
        before = self.entries or {}

        for entry_id, entry in snapshot.items():
            was = before.get(entry_id)
            # An entry never seen before is not news even if it arrives finished:
            # that is the race office typing in a result, not a boat crossing.
            if (was is not None and _finished(entry) and not _finished(was)
                    and entry_id not in self.finish_told and entry_id not in self.finish_due):
                self.finish_due[entry_id] = now + FINISH_HOLD_S
        for entry_id, due in list(self.finish_due.items()):
            entry = snapshot.get(entry_id)
            if not _finished(entry):
                del self.finish_due[entry_id]       # taken back: a wrong automatic finish
                continue
            if now >= due:
                del self.finish_due[entry_id]
                self.finish_told.add(entry_id)
                name = str(entry.get("boat_name") or entry.get("sail_no") or "A boat")
                at = _clock(entry.get("finish_time"), seconds=True)
                notices.append(Notice(
                    "boats", int(entry["race_id"]), str(entry.get("race_name") or "Race"),
                    f"{name} finished at {at}." if at else f"{name} has finished.",
                    kind="finish", entry_id=entry_id,
                    boat_key=boat_follow_key(entry.get("sail_no"), entry.get("boat_name")),
                    ttl=6 * 3600))

        def tally(entries: Iterable[Dict[str, Any]]) -> Dict[int, Tuple[int, int, str]]:
            out: Dict[int, Tuple[int, int, str]] = {}
            for entry in entries:
                race_id = int(entry["race_id"])
                total, racing, name = out.get(race_id, (0, 0, str(entry.get("race_name") or "Race")))
                out[race_id] = (total + 1, racing + (1 if _racing(entry) else 0), name)
            return out

        now_by_race, was_by_race = tally(snapshot.values()), tally(before.values())
        for race_id, (total, racing, _name) in now_by_race.items():
            was_racing = was_by_race.get(race_id, (0, 0, ""))[1]
            if (total and racing == 0 and was_racing > 0
                    and race_id not in self.results_told and race_id not in self.results_due):
                self.results_due[race_id] = now + RESULTS_HOLD_S
        for race_id, due in list(self.results_due.items()):
            total, racing, name = now_by_race.get(race_id, (0, 0, "Race"))
            if not total or racing:
                del self.results_due[race_id]       # somebody is racing again after all
                continue
            if now >= due:
                del self.results_due[race_id]
                self.results_told.add(race_id)
                notices.append(Notice("notices", race_id, name,
                                      "All boats are in. Provisional results are on the race page.",
                                      kind="results", ttl=6 * 3600))
        return notices


# --------------------------------------------------------------------------- telling them

NOTIFY_QUEUE: "queue.Queue[Notice]" = queue.Queue()
NOTIFY_LOCK = threading.Lock()
NOTIFY_STATE: Dict[str, Any] = {"started": False, "last": {}, "watch_error": ""}


def _record(**fields: Any) -> None:
    with NOTIFY_LOCK:
        NOTIFY_STATE["last"] = {**fields, "at": _now_iso()}


def deliver(notice: Notice, subscriptions: Optional[List[Dict[str, Any]]] = None,
            send: Callable[..., webpush.PushResult] = webpush.send) -> Dict[str, Any]:
    """Send one notice to everyone it is for. Returns what happened, and who to try again.

    A subscription whose push service says it is gone is deleted here: that is
    how a phone that uninstalled the app, or cleared its browser, drops out.
    """
    targets = subscriptions if subscriptions is not None else subscriptions_for(notice)
    outcome: Dict[str, Any] = {"sent": 0, "gone": 0, "failed": 0, "again": [], "error": ""}
    if not targets:
        return outcome
    private_pem, public = vapid_keys()
    subject = public_base_url()
    payload = notice.payload()

    def one(sub: Dict[str, Any]) -> Tuple[Dict[str, Any], webpush.PushResult]:
        return sub, send(sub, payload, private_pem=private_pem, public_key=public, subject=subject,
                         ttl=notice.ttl, urgency=notice.urgency, topic=notice.tag())

    with ThreadPoolExecutor(max_workers=SEND_WORKERS) as pool:
        results = list(pool.map(one, targets))
    delivered, gone = [], []
    for sub, result in results:
        if result.ok:
            outcome["sent"] += 1
            delivered.append(sub["endpoint"])
        elif result.gone:
            outcome["gone"] += 1
            gone.append(sub["endpoint"])
        elif result.retry:
            outcome["again"].append(sub)
            outcome["error"] = outcome["error"] or f"{result.status or 'no answer'}: {result.detail}"[:200]
        else:
            outcome["failed"] += 1
            outcome["error"] = outcome["error"] or f"{result.status}: {result.detail}"[:200]
    if delivered or gone:
        now = _now_iso()
        with get_push_db() as conn:
            conn.executemany("UPDATE subscriptions SET last_ok_at = ? WHERE endpoint = ?",
                             [(now, e) for e in delivered])
            conn.executemany("DELETE FROM subscriptions WHERE endpoint = ?", [(e,) for e in gone])
            conn.commit()
    return outcome


def send_test(send: Callable[..., webpush.PushResult] = webpush.send) -> Dict[str, Any]:
    """Settings -> Phone notifications -> Send a test: one message to every phone, now."""
    from core.settings import club_config
    notice = Notice("everyone", 0, club_config()["name"],
                    "Test notification from the race office. Nothing has happened.", kind="test", ttl=600)
    outcome = deliver(notice, send=send)
    _record(kind="test", title=notice.title, body=notice.body, sent=outcome["sent"],
            failed=outcome["failed"] + len(outcome["again"]), gone=outcome["gone"], error=outcome["error"])
    return outcome


def queue_test() -> None:
    """Ask the sender to send the test message, and return at once.

    The Settings button uses this rather than ``send_test``: a few hundred phones
    over the hut's 4G is longer than the tunnel will hold a request open for.
    """
    from core.settings import club_config
    NOTIFY_QUEUE.put(Notice("everyone", 0, club_config()["name"],
                            "Test notification from the race office. Nothing has happened.",
                            kind="test", ttl=600))


def sending_possible() -> bool:
    return notify_config()["enabled"] and ready()[0]


def watch_step(watcher: Optional[Watcher]) -> Optional[Watcher]:
    """One look. Returns the watcher to carry to the next look, or None.

    While notifications are off, cannot be sent, or nobody has subscribed, this
    does nothing at all: an installation that never switches them on never reads
    the race database for them. The watcher is dropped rather than kept, so the
    moment there is somebody to tell is a fresh baseline and nothing that
    happened in between is announced.
    """
    if not sending_possible() or subscription_count() == 0:
        return None
    if watcher is None:
        watcher = Watcher()
    for notice in watcher.poll():
        NOTIFY_QUEUE.put(notice)
    return watcher


def notify_watch_loop() -> None:
    """Look, every few seconds, and queue whatever is news."""
    watcher: Optional[Watcher] = None
    while True:
        try:
            watcher = watch_step(watcher)
            with NOTIFY_LOCK:
                NOTIFY_STATE["watch_error"] = ""
        except Exception as exc:
            with NOTIFY_LOCK:
                NOTIFY_STATE["watch_error"] = f"{type(exc).__name__}: {exc}"[:200]
        time.sleep(POLL_S)


def notify_send_loop() -> None:
    """Send what the watcher queued, and try again what a push service was too busy for."""
    backlog: List[Tuple[float, Notice, List[Dict[str, Any]], int]] = []
    while True:
        try:
            try:
                notice: Optional[Notice] = NOTIFY_QUEUE.get(timeout=1.0)
            except queue.Empty:
                notice = None
            jobs: List[Tuple[Notice, Optional[List[Dict[str, Any]]], int]] = []
            if notice is not None:
                jobs.append((notice, None, 0))
            now = time.time()
            for item in [b for b in backlog if b[0] <= now]:
                backlog.remove(item)
                jobs.append((item[1], item[2], item[3]))
            for job_notice, subs, attempt in jobs:
                outcome = deliver(job_notice, subs)
                if outcome["again"] and attempt < len(RETRY_DELAYS_S) and len(backlog) < MAX_RETRY_BACKLOG:
                    backlog.append((time.time() + RETRY_DELAYS_S[attempt], job_notice,
                                    outcome["again"], attempt + 1))
                    waiting = len(outcome["again"])
                else:
                    waiting = 0
                _record(kind=job_notice.kind, title=job_notice.title, body=job_notice.body,
                        sent=outcome["sent"], gone=outcome["gone"],
                        failed=outcome["failed"] + (0 if waiting else len(outcome["again"])),
                        waiting=waiting, error=outcome["error"])
        except Exception as exc:
            _record(kind="error", title="", body="", sent=0, failed=0, gone=0,
                    error=f"{type(exc).__name__}: {exc}"[:200])
            time.sleep(1.0)


def start_notify_worker() -> None:
    """Start the watcher and the sender, once per process."""
    with NOTIFY_LOCK:
        if NOTIFY_STATE["started"]:
            return
        NOTIFY_STATE["started"] = True
    threading.Thread(target=notify_watch_loop, name="notify-watch", daemon=True).start()
    threading.Thread(target=notify_send_loop, name="notify-send", daemon=True).start()


def notify_runtime_status() -> Dict[str, Any]:
    """For the Settings page: is it on, can it send, how many phones, what was last sent."""
    config = notify_config()
    can_send, reason = ready()
    try:
        subscribers = subscription_count()
    except Exception:
        subscribers = 0
    with NOTIFY_LOCK:
        last = dict(NOTIFY_STATE.get("last") or {})
        watch_error = str(NOTIFY_STATE.get("watch_error") or "")
    return {"enabled": config["enabled"], "ready": can_send, "reason": reason,
            "subscribers": subscribers, "last": last, "watch_error": watch_error}
