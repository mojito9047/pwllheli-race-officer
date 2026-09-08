"""Re-shoot the figures no other capture script owns.

Ten images in the guides had no script behind them: they were cut by hand once
and then simply carried forward. That was survivable while the look never
changed, and stopped being survivable at v0.275, when it changed completely and
a manual with two visual languages in it reads worse than one with none.

Everything here is anchored to an element or a heading rather than to pixel
offsets, so it re-cuts itself the next time the look moves.

    RO_CAP_BASE=http://127.0.0.1:5059 RO_CAP_PASSWORD=... \
    RO_CAP_DB=<sandbox>/data/race_officer.db \
    python scripts/capture_theme_refresh.py
"""
import os
import sqlite3
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)
sys.path.insert(0, _REPO)

from playwright.sync_api import sync_playwright     # noqa: E402

BASE = os.environ.get("RO_CAP_BASE", "http://localhost:5050")

def _races_with_results():
    """Race ids worth trying, busiest first.

    Hardcoding race 1 meant one race without a Results tab took every later
    shot in this script with it -- and a capture that dies quietly is how two
    of these came to be photographing /admin/race/None for months.
    """
    import sqlite3
    db = sqlite3.connect(os.path.join(_REPO, "data", "race_officer.db"))
    try:
        rows = db.execute("SELECT race_id FROM entries GROUP BY race_id"
                          " ORDER BY COUNT(*) DESC LIMIT 8").fetchall()
    finally:
        db.close()
    return [r[0] for r in rows] or [1]

SHOTS = os.path.join(_HERE, "screenshots")
REF = os.path.join(_HERE, "ref_screens")
USERNAME = "admin"
PASSWORD = os.environ.get("RO_CAP_PASSWORD", "")
if not PASSWORD:
    # Every other capture script falls back to the file; this one did not, so
    # with no env var set it logged in with an empty password, failed, and shot
    # the login page over and over -- reported only as "SKIP: nothing to frame".
    _pwf = os.path.join(_REPO, "runtime", "initial_admin_password.txt")
    if os.path.exists(_pwf):
        PASSWORD = open(_pwf, encoding="utf-8").read().strip()
if not PASSWORD:
    raise SystemExit("No admin password: set RO_CAP_PASSWORD or provide "
                     "runtime/initial_admin_password.txt")
DB = os.environ.get("RO_CAP_DB", "")
PAD = 12


def _win(path):
    """Accept an MSYS-style /c/Users/... path from Git Bash.

    Windows Python cannot stat one, so the positions database "did not exist",
    seeding was skipped and the battery figure was silently left un-reshot --
    reported only as "low battery seeded: False", which says nothing about why.
    """
    if len(path) > 2 and path[0] == "/" and path[2] == "/" and path[1].isalpha():
        path = path[1].upper() + ":" + path[2:]
    return os.path.normpath(path)


def seed_low_battery():
    """Give an assigned tracker a flat battery so the dashboard warns about it.

    The real units live on a shelf on charge, so the warning has to be staged.
    Done in the sandbox copy: the positions database is a copy too.
    """
    if not DB:
        print("  (no RO_CAP_DB, so no battery to stage)")
        return False
    positions = os.path.join(os.path.dirname(_win(DB)), "track_positions.db")
    if not os.path.exists(positions):
        print("  (no track_positions.db beside %s)" % _win(DB))
        return False
    race_db = sqlite3.connect(_win(DB))
    row = race_db.execute(
        "SELECT unique_id, boat_id FROM trackers WHERE boat_id IS NOT NULL LIMIT 1").fetchone()
    race_db.close()
    if not row:
        return False
    uid, boat_id = row
    p = sqlite3.connect(positions)
    now = time.time()
    for pct, offset in ((8.0, 30), (19.0, 45)):
        p.execute(
            "INSERT INTO track_positions (unique_id, boat_id, lat, lon, fix_time,"
            " server_time, battery_pct, valid) VALUES (?,?,?,?,?,?,?,1)",
            (uid, boat_id, 52.885, -4.41, now - offset, now - offset, pct))
    p.commit()
    p.close()
    return True


def login(page):
    """Log in, and refuse to carry on if it did not take.

    A capture script that keeps going while logged out photographs the login
    page into every file it writes, and says nothing about it.
    """
    page.goto(f"{BASE}/login", wait_until="load")
    page.fill('input[name="username"]', USERNAME)
    page.fill('input[name="password"]', PASSWORD)
    page.click('button[type="submit"]')
    page.wait_for_load_state("load")
    if "/login" in page.url:
        raise SystemExit(f"Login failed as {USERNAME}; every shot would be the login page.")


def clip_of(page, js, pad=PAD):
    box = page.evaluate(js)
    if not box:
        return None
    return {"x": max(0, box["x"] - pad), "y": max(0, box["y"] - pad),
            "width": box["width"] + 2 * pad, "height": box["height"] + 2 * pad}


def section(heading):
    """Bounding box from a heading to the next heading of the same or higher level."""
    return f"""
    (() => {{
      const heads = [...document.querySelectorAll('h2,h3,h4')];
      const h = heads.find(e => e.textContent.trim().startsWith({heading!r}));
      if (!h) return null;
      const top = h.getBoundingClientRect().top + scrollY;
      const level = Number(h.tagName[1]);
      const after = heads.filter(e => (e.getBoundingClientRect().top + scrollY) > top + 5
                                   && Number(e.tagName[1]) <= level);
      const end = after.length ? after[0].getBoundingClientRect().top + scrollY
                               : document.body.scrollHeight;
      // Width from the containing block, not the heading: a heading inside a
      // <summary> shrinks to its own words, and taking its width cut an 83px
      // ribbon down a 3316px section.
      const box = h.closest('details, section, .card, main') || h;
      const b = box.getBoundingClientRect();
      return {{x: b.left + scrollX, y: top, width: b.width, height: end - top}};
    }})()"""


def shoot(page, name, clip, out_dir):
    if not clip or clip["height"] < 40:
        print("  SKIP %s: nothing to frame" % name)
        return
    page.screenshot(path=os.path.join(out_dir, name), clip=clip, full_page=True)
    print("  cut  %s  %dx%d" % (name, clip["width"], clip["height"]))


def main():
    seeded = seed_low_battery()
    print("low battery seeded:", seeded)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_context(viewport={"width": 1440, "height": 1000}).new_page()

        # The sign-in page, logged out, before anything else touches the session.
        page.goto(f"{BASE}/login", wait_until="load")
        page.wait_for_timeout(300)
        shoot(page, "crop_login.png", clip_of(page, """
            (() => { const c = document.querySelector('main .card, .login-card, form');
                     if (!c) return null; const b = c.getBoundingClientRect();
                     return {x: b.left + scrollX, y: b.top + scrollY,
                             width: b.width, height: b.height}; })()"""), SHOTS)

        login(page)

        # Dashboard: the tracker-battery warning, staged above.
        page.goto(f"{BASE}/admin", wait_until="load")
        page.wait_for_timeout(1800)
        card = page.query_selector("#trackerBatteryCard")
        if card:
            card.screenshot(path=os.path.join(SHOTS, "dash_battery_warning.png"))
            print("  saved dash_battery_warning.png")
        else:
            print("  SKIP dash_battery_warning.png: no low battery on the dashboard")

        page.goto(f"{BASE}/admin/settings/activity-log", wait_until="load")
        page.wait_for_timeout(500)
        page.screenshot(path=os.path.join(SHOTS, "activity_log.png"), full_page=False)
        print("  saved activity_log.png")

        # Marks: the compound pair, the edit form, and the top of the list.
        page.goto(f"{BASE}/admin/marks", wait_until="load")
        page.wait_for_timeout(700)
        shoot(page, "marks_compound.png", clip_of(page, """
            (() => {
              const rows = [...document.querySelectorAll('table tr')].filter(
                  r => /\\b(Ya|Yb|Aa|Ab)\\b/.test(r.textContent));
              if (!rows.length) return null;
              const f = rows[0].getBoundingClientRect(), l = rows[rows.length-1].getBoundingClientRect();
              return {x: f.left + scrollX, y: f.top + scrollY - 40,
                      width: f.width, height: (l.bottom + scrollY) - (f.top + scrollY) + 40};
            })()"""), REF)
        shoot(page, "marks_edit.png", clip_of(page, section("Add a mark")), REF)
        shoot(page, "marks_top_check.png", clip_of(page, """
            (() => { const t = document.querySelector('table');
                     if (!t) return null; const b = t.getBoundingClientRect();
                     return {x: b.left + scrollX, y: b.top + scrollY,
                             width: b.width, height: Math.min(520, b.height)}; })()"""), REF)

        page.goto(f"{BASE}/admin/marks/ping", wait_until="load")
        page.wait_for_timeout(700)
        page.screenshot(path=os.path.join(REF, "marks_ping.png"), full_page=False)
        print("  saved marks_ping.png")

        # Settings: the polars section only.
        page.goto(f"{BASE}/admin/settings", wait_until="load")
        page.wait_for_timeout(600)
        page.evaluate("document.querySelectorAll('.settings-section').forEach("
                      "s => s.open = /polar/i.test(s.textContent))")
        page.wait_for_timeout(400)
        # The TOP of the section, as the name says: the club's polar library runs
        # to dozens of classes and the whole thing is 3316px, which scaled into a
        # PDF page is a grey smear. The caption already says the list is long.
        polars = clip_of(page, section("Polars"))
        if polars:
            polars["height"] = min(polars["height"], 720)
        shoot(page, "s_polars_top.png", polars, REF)

        # Race results, in two halves, from the race sheet's Results tab.
        for _rid in _races_with_results():
            page.goto(f"{BASE}/admin/race/{_rid}", wait_until="load")
            if page.evaluate("!!document.querySelector('[data-tab=\"tab-results\"]')"):
                break
        else:
            raise SystemExit("no race page offers a Results tab")
        page.evaluate("document.querySelector('[data-tab=\"tab-results\"]').click()")
        page.wait_for_timeout(900)
        cards = page.evaluate("""
            (() => [...document.querySelectorAll('#tab-results .result-card')].map(c => {
               const b = c.getBoundingClientRect();
               return {x: b.left + scrollX, y: b.top + scrollY, width: b.width, height: b.height};
            }))()""")
        if cards:
            half = max(1, (len(cards) + 1) // 2)
            top = {"x": cards[0]["x"], "y": cards[0]["y"], "width": cards[0]["width"],
                   "height": (cards[half - 1]["y"] + cards[half - 1]["height"]) - cards[0]["y"]}
            shoot(page, "crop_results_top.png", {
                "x": max(0, top["x"] - PAD), "y": max(0, top["y"] - PAD),
                "width": top["width"] + 2 * PAD, "height": top["height"] + 2 * PAD}, SHOTS)
            rest = cards[half:] or cards[-1:]
            bot = {"x": rest[0]["x"], "y": rest[0]["y"], "width": rest[0]["width"],
                   "height": (rest[-1]["y"] + rest[-1]["height"]) - rest[0]["y"]}
            shoot(page, "crop_results_bottom.png", {
                "x": max(0, bot["x"] - PAD), "y": max(0, bot["y"] - PAD),
                "width": bot["width"] + 2 * PAD, "height": bot["height"] + 2 * PAD}, SHOTS)
        else:
            print("  SKIP crop_results_*: no result cards on this race")

        browser.close()
    print("done")


if __name__ == "__main__":
    main()
