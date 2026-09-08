import os
_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)
from playwright.sync_api import sync_playwright

# The figures the v0.243-v0.253 features added or invalidated. Two sources,
# because no single instance shows both halves well:
#
#   RO_CAP_BASE      the race-office PC, where the hardware cards and the
#                    hardware event log have real content in them.
#   RO_CAP_DEMO_BASE a seeded throwaway instance, for the tracker-battery
#                    warning. The real trackers are on a shelf on charge, and
#                    photographing a warning means having flat batteries to
#                    hand — so that half is staged rather than waited for.
#
# Set only RO_CAP_BASE and the demo shots are skipped, and vice versa.
BASE = os.environ.get("RO_CAP_BASE", "")
DEMO = os.environ.get("RO_CAP_DEMO_BASE", "")
OUT_DIR = os.path.join(_HERE, "screenshots")
os.makedirs(OUT_DIR, exist_ok=True)

USERNAME = "admin"


def password_for(which):
    pw = os.environ.get(which, "")
    if pw:
        return pw
    path = os.path.join(_REPO, "runtime", "initial_admin_password.txt")
    return open(path, encoding="utf-8").read().strip() if os.path.exists(path) else ""


def login(page, base, password):
    page.goto(f"{base}/login", wait_until="load")
    page.fill('input[name="username"]', USERNAME)
    page.fill('input[name="password"]', password)
    page.click('button[type="submit"]')
    page.wait_for_load_state("load")


def elem_shot(page, selector, name, **kw):
    page.locator(selector).screenshot(path=os.path.join(OUT_DIR, name), **kw)
    print("saved", name)


with sync_playwright() as p:
    browser = p.chromium.launch()

    if BASE:
        page = browser.new_context(viewport={"width": 1440, "height": 1000}).new_page()
        login(page, BASE, password_for("RO_CAP_PASSWORD"))

        # Dashboard: the side menu gained a Current race link, between Dashboard
        # and New race. Full page, since the hardware cards below are the rest of
        # what this figure is for.
        page.goto(f"{BASE}/admin", wait_until="load")
        page.wait_for_timeout(3000)          # weather dial, chart tiles, status polls
        page.screenshot(path=os.path.join(OUT_DIR, "02_dashboard.png"), full_page=True)
        print("saved 02_dashboard.png")

        page.context.close()

    if DEMO:
        page = browser.new_context(viewport={"width": 1440, "height": 1000}).new_page()
        login(page, DEMO, password_for("RO_CAP_DEMO_PASSWORD"))
        page.goto(f"{DEMO}/admin", wait_until="load")
        page.wait_for_timeout(1500)
        elem_shot(page, "#trackerBatteryCard", "dash_battery_warning.png")

        # The activity log, from the demo instance rather than the race-office PC:
        # the real one names the club's trackers by IMEI, and a published guide is
        # no place for them.
        page.goto(f"{DEMO}/admin/settings/activity-log", wait_until="load")
        page.wait_for_timeout(400)
        page.screenshot(path=os.path.join(OUT_DIR, "activity_log.png"), full_page=False)
        print("saved activity_log.png")
        page.context.close()

    browser.close()
print("done")
