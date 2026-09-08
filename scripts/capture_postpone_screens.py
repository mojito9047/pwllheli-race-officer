"""Re-capture the Start console & log tab, and capture the postponement.

The tab picture in the guides still showed a **Postpone - AP** button in the
manual controls -- a control that logged a note, did none of the postponing, and
was deleted in v0.266. A picture of a button that no longer exists, printed
directly above the section explaining what replaced it, is worse than no picture:
the reader trusts the screenshot over the prose.

So this re-takes that shot and adds one of AP actually flying, which is the
state the section is about and which no existing screenshot showed.

Run against a temp app as with the other capture scripts:

    RO_COOKIE_SECURE=0 python app.py            # in another shell
    RO_CAP_BASE=http://localhost:5058 python scripts/capture_postpone_screens.py

It postpones a race and puts it back exactly as it found it, because it runs
against whatever database the app is pointed at.
"""
import os
import sqlite3
from datetime import datetime, timedelta

from playwright.sync_api import sync_playwright

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)

BASE = os.environ.get("RO_CAP_BASE", "http://localhost:5050")
OUT_DIR = os.path.join(_HERE, "screenshots")
DB = os.environ.get("RO_CAP_DB", os.path.join(_REPO, "data", "race_officer.db"))
RACE_ID = int(os.environ.get("RO_CAP_RACE_ID", "1"))

USERNAME = "admin"
PASSWORD = os.environ.get("RO_CAP_PASSWORD", "")
if not PASSWORD:
    _pwf = os.path.join(_REPO, "runtime", "initial_admin_password.txt")
    if os.path.exists(_pwf):
        PASSWORD = open(_pwf, encoding="utf-8").read().strip()


def shot(page, name, full_page=True):
    page.screenshot(path=os.path.join(OUT_DIR, name), full_page=full_page)
    print("saved", name)


def race_row():
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    try:
        return db.execute(
            "SELECT start_time, postponed_at, postponement_kind, postponement_ends_at"
            " FROM races WHERE id = ?", (RACE_ID,)).fetchone()
    finally:
        db.close()


def set_race(start_time, postponed_at, kind, ends_at):
    db = sqlite3.connect(DB)
    try:
        db.execute("UPDATE races SET start_time = ?, postponed_at = ?, postponement_kind = ?,"
                   " postponement_ends_at = ? WHERE id = ?",
                   (start_time, postponed_at, kind, ends_at, RACE_ID))
        db.commit()
    finally:
        db.close()


def first_entry():
    """One entry of the race, so it can be put back on the water and restored.

    Every race in the screenshot database has been sailed, so the header reads
    "Race finished" and the flag panel shows nothing -- a finished race is not
    postponed, correctly, but it makes a poor picture of a postponement. Putting
    one boat back to RACING is the smallest change that gives a race with a start
    still to come.
    """
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    try:
        return db.execute("SELECT id, status, finish_time FROM entries WHERE race_id = ?"
                          " ORDER BY id LIMIT 1", (RACE_ID,)).fetchone()
    finally:
        db.close()


def set_entry(entry_id, status, finish_time):
    db = sqlite3.connect(DB)
    try:
        db.execute("UPDATE entries SET status = ?, finish_time = ? WHERE id = ?",
                   (status, finish_time, entry_id))
        db.commit()
    finally:
        db.close()


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    before = race_row()
    if before is None:
        raise SystemExit(f"No race {RACE_ID} in {DB}")
    entry = first_entry()

    # The warning eight minutes off and AP due down in seven puts two of the
    # course announcements before the flag comes down, so the plan shows what is
    # held as well as the two AP rows that replace them.
    now = datetime.now().replace(second=0, microsecond=0)
    warning = now + timedelta(minutes=8)
    ap_down = now + timedelta(minutes=7)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_context(viewport={"width": 1440, "height": 900}).new_page()
            page.goto(f"{BASE}/login", wait_until="load")
            page.fill('input[name="username"]', USERNAME)
            page.fill('input[name="password"]', PASSWORD)
            page.click('button[type="submit"]')
            page.wait_for_load_state("load")

            # 1. The tab as it is now: the Postpone (AP) card, and manual controls
            #    without the button that used to pretend to do it.
            if entry is not None:
                set_entry(entry["id"], "RACING", None)
            set_race(warning.isoformat(timespec="seconds"), None, None, None)
            page.goto(f"{BASE}/admin/race/{RACE_ID}", wait_until="load")
            page.wait_for_timeout(900)
            page.evaluate("document.querySelector('[data-tab=\"tab-start\"]').click()")
            page.evaluate("window.scrollTo(0, 0)")
            page.wait_for_timeout(500)
            shot(page, "10_race_tab3_start.png")

            # 2. AP flying, with a time set for it to come down: the banner, the
            #    flag in the panel, and the signal plan showing what is held and
            #    what happens instead.
            set_race(warning.isoformat(timespec="seconds"),
                     now.isoformat(timespec="seconds"), "AP",
                     ap_down.isoformat(timespec="seconds"))
            page.goto(f"{BASE}/admin/race/{RACE_ID}", wait_until="load")
            page.wait_for_timeout(900)
            page.evaluate("document.querySelector('[data-tab=\"tab-start\"]').click()")
            page.evaluate("window.scrollTo(0, 0)")
            page.wait_for_timeout(1200)      # let the flag panel and clock tick once
            shot(page, "race_tab_start_postponed.png")
            browser.close()
    finally:
        set_race(before["start_time"], before["postponed_at"],
                 before["postponement_kind"], before["postponement_ends_at"])
        if entry is not None:
            set_entry(entry["id"], entry["status"], entry["finish_time"])
        print("race", RACE_ID, "put back as found")


if __name__ == "__main__":
    main()
