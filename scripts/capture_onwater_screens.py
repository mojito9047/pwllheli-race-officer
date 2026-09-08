"""Capture the Virtual Race Officer page for the Reference Manual.

Its own script because the page needs a conversation in it to be worth looking
at, and because the account needs a permission no role grants -- the figure has
to show the page as somebody using it sees it, mid-command, with a read-back
waiting for Yes.

The commands are sent with the built-in grammar deliberately: the figure must
not depend on a provider account, and the grammar produces the same read-back a
model would for these sentences.

    python scripts/capture_onwater_screens.py
"""
import os
import sqlite3
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)
sys.path.insert(0, _REPO)

from playwright.sync_api import sync_playwright     # noqa: E402

BASE = os.environ.get("RO_CAP_BASE", "http://localhost:5050")
OUT_DIR = os.path.join(_HERE, "ref_screens")
os.makedirs(OUT_DIR, exist_ok=True)

USERNAME = "admin"
PASSWORD = os.environ.get("RO_CAP_PASSWORD", "")
if not PASSWORD:
    _pwf = os.path.join(_REPO, "runtime", "initial_admin_password.txt")
    if os.path.exists(_pwf):
        PASSWORD = open(_pwf, encoding="utf-8").read().strip()

# RO_CAP_DB, like the other capture scripts that write. This granted the
# permission on the REPO's database while the shots were being taken against a
# sandbox copy, so the sandbox user never got it and the capture timed out
# waiting for a control it could not reach -- and every run reached into the
# club's live data to do it. The finally below did put it back, but a screenshot
# script has no business writing there at all.
DB = os.environ.get("RO_CAP_DB", os.path.join(_REPO, "data", "race_officer.db"))


def set_permission(allowed: bool) -> None:
    """The page is behind a per-user permission and the figure has to get in.

    Put back afterwards, so capturing a screenshot cannot quietly leave an
    account able to start races from a phone.
    """
    db = sqlite3.connect(DB)
    db.execute("UPDATE users SET can_race_remotely = ? WHERE username = ?",
               (1 if allowed else 0, USERNAME))
    db.commit()
    db.close()


SAY = [
    "status",
    "create a race called Sunday Points at 11am",
]


def main() -> None:
    set_permission(True)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            # A phone, because that is what this page is: a desktop-width figure
            # would show a layout nobody using it ever sees.
            context = browser.new_context(viewport={"width": 430, "height": 900},
                                          device_scale_factor=2)
            page = context.new_page()
            page.goto(f"{BASE}/login", wait_until="load")
            page.fill('input[name="username"]', USERNAME)
            page.fill('input[name="password"]', PASSWORD)
            page.click('button[type="submit"]')
            page.wait_for_load_state("load")

            page.goto(f"{BASE}/vro", wait_until="load")
            page.wait_for_timeout(400)
            for said in SAY:
                page.fill("#sayText", said)
                page.click("#btnSay")
                page.wait_for_function("!document.getElementById('btnSay').disabled",
                                       timeout=40000)
                page.wait_for_timeout(300)

            path = os.path.join(OUT_DIR, "onwater_page.png")
            page.screenshot(path=path, full_page=True)
            print("saved onwater_page.png")
            browser.close()
    finally:
        set_permission(False)


if __name__ == "__main__":
    main()
