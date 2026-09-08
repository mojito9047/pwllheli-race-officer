import os
_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)
from playwright.sync_api import sync_playwright

BASE = os.environ.get("RO_CAP_BASE", "http://localhost:5050")
OUT_DIR = os.path.join(_HERE, "ref_screens")
os.makedirs(OUT_DIR, exist_ok=True)
USERNAME = "admin"
# RO_CAP_PASSWORD first, as every other capture script does. Reading the repo's
# own password file unconditionally meant that when this was pointed at a
# sandbox app the login quietly failed and the "screenshots" were of the login
# page -- and nothing said so, because the shot itself succeeded.
PASSWORD = os.environ.get("RO_CAP_PASSWORD", "")
if not PASSWORD:
    with open(os.path.join(_REPO, "runtime", "initial_admin_password.txt")) as f:
        PASSWORD = f.read().strip()

with sync_playwright() as p:
    browser = p.chromium.launch()
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()
    page.goto(f"{BASE}/login", wait_until="load")
    page.fill('input[name="username"]', USERNAME)
    page.fill('input[name="password"]', PASSWORD)
    page.click('button[type="submit"]')
    page.wait_for_load_state("load")
    if page.query_selector('input[name="password"]'):
        raise SystemExit("login failed: still on the login page (check RO_CAP_PASSWORD)")

    # Dashboard power card (crop to the card)
    page.goto(f"{BASE}/admin", wait_until="load")
    page.wait_for_timeout(900)  # let dashboard_power.js populate
    page.locator("#powerCard").scroll_into_view_if_needed()
    page.wait_for_timeout(300)
    page.locator("#powerCard").screenshot(path=os.path.join(OUT_DIR, "s_power_card.png"))
    print("saved s_power_card.png")

    # Power history graph page. Pick the longest range that actually holds
    # samples: a screenshot database is a copy taken at some point in the past,
    # so the default "last 6 hours" is empty and the figure shows three empty
    # frames -- which is a picture of nothing, printed under a caption about
    # reading values off the chart.
    # Tall enough that the page needs no scrolling, because a full_page capture
    # scrolls -- and scrolling drags the pointer off the canvas, which fires
    # pointerleave and clears the very cursor this figure is meant to show.
    page.set_viewport_size({"width": 1440, "height": 1200})
    page.goto(f"{BASE}/power/history", wait_until="load")
    page.wait_for_timeout(1400)  # let power_history.js fetch + draw
    for minutes in ("1440", "10080", "43200"):
        page.select_option("#powerHistoryRange", minutes)
        page.wait_for_timeout(1600)
        if page.evaluate("document.querySelectorAll('#powerHistoryEmpty:not([hidden])').length === 0"):
            print("  range", minutes, "min has samples")
            break
    # Park the cursor mid-chart so the figure shows the readout, which is the
    # point of the page: a line down all three panels and the values at it.
    canvas = page.locator("#powerHistoryCanvas")
    box = canvas.bounding_box()
    if box:
        page.mouse.move(box["x"] + box["width"] * 0.62, box["y"] + box["height"] * 0.5)
        page.wait_for_timeout(400)
    page.screenshot(path=os.path.join(OUT_DIR, "s_power_history.png"), full_page=False)
    print("saved s_power_history.png")
    page.set_viewport_size({"width": 1440, "height": 900})

    # Settings -> Hut power section only
    page.goto(f"{BASE}/admin/settings", wait_until="load")
    page.wait_for_timeout(500)
    page.evaluate("document.querySelectorAll('.settings-section').forEach(s => s.open = (s.id === 'power'))")
    page.evaluate("document.getElementById('power').scrollIntoView({block:'start'})")
    page.wait_for_timeout(300)
    page.locator("#power").screenshot(path=os.path.join(OUT_DIR, "s_power_settings.png"))
    print("saved s_power_settings.png")

    browser.close()
print("done")
