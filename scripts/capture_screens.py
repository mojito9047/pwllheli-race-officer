import os
_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)
import os
from playwright.sync_api import sync_playwright

BASE = os.environ.get("RO_CAP_BASE", "http://localhost:5050")
OUT_DIR = os.path.join(_HERE, "screenshots")
os.makedirs(OUT_DIR, exist_ok=True)

USERNAME = "admin"
PASSWORD = os.environ.get("RO_CAP_PASSWORD", "")
if not PASSWORD:
    _pwf = os.path.join(_REPO, "runtime", "initial_admin_password.txt")
    if os.path.exists(_pwf):
        PASSWORD = open(_pwf, encoding="utf-8").read().strip()
def shot(page, name, full_page=True):
    path = os.path.join(OUT_DIR, name)
    page.screenshot(path=path, full_page=full_page)
    print("saved", name)


def click_tab(page, tab_id):
    page.evaluate(f"document.querySelector('[data-tab=\"{tab_id}\"]').click()")
    page.evaluate("window.scrollTo(0, 0)")
    page.wait_for_timeout(300)


with sync_playwright() as p:
    browser = p.chromium.launch()
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()

    # --- Login page (logged out) ---
    page.goto(f"{BASE}/login", wait_until="load")
    shot(page, "01_login.png", full_page=False)

    # --- Log in ---
    page.fill('input[name="username"]', USERNAME)
    page.fill('input[name="password"]', PASSWORD)
    page.click('button[type="submit"]')
    page.wait_for_load_state("load")

    # --- Dashboard ---
    page.goto(f"{BASE}/admin", wait_until="load")
    page.wait_for_timeout(800)
    shot(page, "02_dashboard.png")

    # --- Series list ---
    page.goto(f"{BASE}/admin/series", wait_until="load")
    page.wait_for_timeout(300)
    shot(page, "03_series_list.png")

    # --- Series detail (top: header + races) ---
    page.goto(f"{BASE}/admin/series/1", wait_until="load")
    page.wait_for_timeout(500)
    shot(page, "04_series_detail_top.png", full_page=False)

    # --- Series detail: expand "Edit series details" for classes/discard view ---
    page.evaluate("document.querySelector('details summary').click()")
    page.wait_for_timeout(300)
    shot(page, "05_series_detail_classes.png")

    # collapse it back before full-page results shot to keep that shot focused
    page.evaluate("document.querySelector('details').open = false")
    page.wait_for_timeout(200)

    # --- Series detail: scroll to Series results section ---
    page.evaluate("""
        const heading = [...document.querySelectorAll('h3')].find(h => h.textContent.trim() === 'Series results');
        if (heading) heading.scrollIntoView({block: 'start'});
    """)
    page.wait_for_timeout(300)
    shot(page, "06_series_detail_results.png", full_page=False)

    # --- New race form ---
    page.goto(f"{BASE}/admin/race/new", wait_until="load")
    page.wait_for_timeout(300)
    shot(page, "07_new_race_form.png", full_page=False)

    # --- Race detail tabs (race id 1, already has course/entries/results) ---
    page.goto(f"{BASE}/admin/race/1", wait_until="load")
    page.wait_for_timeout(1000)
    page.evaluate("window.scrollTo(0, 0)")
    page.wait_for_timeout(200)
    shot(page, "08_race_tab1_course.png", full_page=True)

    click_tab(page, "tab-entries")
    shot(page, "09_race_tab2_entries.png", full_page=True)

    click_tab(page, "tab-start")
    shot(page, "10_race_tab3_start.png", full_page=True)

    click_tab(page, "tab-admin")
    shot(page, "11_race_tab4_finish.png", full_page=True)

    click_tab(page, "tab-results")
    shot(page, "12_race_tab5_results.png", full_page=True)

    # --- Shorten course tab (v0.161) ---
    click_tab(page, "tab-shorten")
    shot(page, "race_tab_shorten.png", full_page=True)

    # --- Published series results HTML ---
    page.goto(f"{BASE}/admin/series/1/publish.html", wait_until="load")
    page.wait_for_timeout(300)
    shot(page, "13_publish_html.png", full_page=False)

    # --- Boats database ---
    page.goto(f"{BASE}/admin/boats", wait_until="load")
    page.wait_for_timeout(300)
    shot(page, "14_boats_list.png", full_page=False)

    browser.close()

print("DONE")
