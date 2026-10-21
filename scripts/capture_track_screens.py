import os
import re
_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)
from playwright.sync_api import sync_playwright

# Capture the GPS-tracking screenshots for the guides. Point at a running app
# (with the simulator on and a race seeded with tracked boats) via RO_CAP_BASE;
# RO_CAP_RACE_ID selects the race whose Course & start tab is captured. Images
# are saved into scripts/screenshots/ (read by both PDF builders).
BASE = os.environ.get("RO_CAP_BASE", "http://localhost:5050")
RACE_ID = os.environ.get("RO_CAP_RACE_ID", "")
OUT_DIR = os.path.join(_HERE, "screenshots")
os.makedirs(OUT_DIR, exist_ok=True)

USERNAME = "admin"
PASSWORD = os.environ.get("RO_CAP_PASSWORD", "")
if not PASSWORD:
    _pwf = os.path.join(_REPO, "runtime", "initial_admin_password.txt")
    if os.path.exists(_pwf):
        PASSWORD = open(_pwf, encoding="utf-8").read().strip()


def elem_shot(page, selector, name):
    page.locator(selector).screenshot(path=os.path.join(OUT_DIR, name))
    print("saved", name)


# A tracker's id is its IMEI, a 15-digit number, and these pictures go into
# published guides. scripts/README.md said this capture checked for them; it
# never did. It does now: the visible text and the visible form fields (the edit
# boxes hold ids too). An id drawn into an image would still get past it, so the
# picture wants a look before it is committed.
IMEI = re.compile(r"(?<!\d)\d{15}(?!\d)")


def refuse_if_imei(page, selector, name):
    box = page.locator(selector)
    fields = box.locator("input, textarea, select").evaluate_all(
        "els => els.filter(e => e.type !== 'hidden' && e.offsetParent !== null).map(e => e.value)")
    found = IMEI.findall(box.inner_text() + "\n" + "\n".join(fields))
    if found:
        # The count only: printing the ids would put them in a terminal log instead.
        raise SystemExit(f"not saving {name}: {len(found)} 15-digit tracker id(s) on the page, "
                         "which would publish real IMEIs. Capture against a sandbox with made-up ids.")


with sync_playwright() as p:
    browser = p.chromium.launch()
    context = browser.new_context(viewport={"width": 1440, "height": 1000})
    page = context.new_page()

    page.goto(f"{BASE}/login", wait_until="load")
    page.fill('input[name="username"]', USERNAME)
    page.fill('input[name="password"]', PASSWORD)
    page.click('button[type="submit"]')
    page.wait_for_load_state("load")

    # --- Settings → GPS tracking section ---
    page.goto(f"{BASE}/admin/settings", wait_until="load")
    page.wait_for_timeout(400)
    page.evaluate("document.querySelectorAll('.settings-section').forEach(s => s.open = (s.id === 'gps-tracking'))")
    page.evaluate("document.getElementById('gps-tracking').scrollIntoView({block: 'start'})")
    page.wait_for_timeout(300)
    elem_shot(page, "#gps-tracking", "s_gps_tracking.png")

    # --- Trackers page ---
    page.goto(f"{BASE}/admin/trackers", wait_until="load")
    page.wait_for_timeout(1500)  # let the live status poll populate the RAG dots
    refuse_if_imei(page, "section.card", "trackers_page.png")
    elem_shot(page, "section.card", "trackers_page.png")

    # --- Race sheet Course & start tab: live map + leaderboard ---
    if RACE_ID:
        page.goto(f"{BASE}/admin/race/{RACE_ID}", wait_until="load")
        page.wait_for_timeout(6000)  # let the map render and the positions/leaderboard poll run
        try:
            page.locator("details.course-chart-details").scroll_into_view_if_needed()
            page.wait_for_timeout(500)
            elem_shot(page, "details.course-chart-details", "race_track_map.png")
        except Exception as exc:
            print("map shot failed:", exc)
        try:
            page.locator("#raceFleet").scroll_into_view_if_needed()
            page.wait_for_timeout(300)
            elem_shot(page, "#raceFleet", "race_track_leaderboard.png")
        except Exception as exc:
            print("leaderboard shot failed:", exc)
    else:
        print("RO_CAP_RACE_ID not set; skipped race map/leaderboard shots")

    browser.close()
print("done")
