import os
_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)
import os
from playwright.sync_api import sync_playwright

BASE = os.environ.get("RO_CAP_BASE", "http://localhost:5050")
OUT_DIR = os.path.join(_HERE, "ref_screens")
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


def section_shot(page, section_id, name):
    page.evaluate(f"document.querySelectorAll('.settings-section').forEach(s => s.open = (s.id === '{section_id}'))")
    page.evaluate(f"document.getElementById('{section_id}').scrollIntoView({{block: 'start'}})")
    page.wait_for_timeout(200)
    loc = page.locator(f"#{section_id}")
    loc.screenshot(path=os.path.join(OUT_DIR, name))
    print("saved", name)


with sync_playwright() as p:
    browser = p.chromium.launch()
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()

    page.goto(f"{BASE}/login", wait_until="load")
    page.fill('input[name="username"]', USERNAME)
    page.fill('input[name="password"]', PASSWORD)
    page.click('button[type="submit"]')
    page.wait_for_load_state("load")

    # --- Settings sections, one at a time ---
    page.goto(f"{BASE}/admin/settings", wait_until="load")
    page.wait_for_timeout(500)

    section_shot(page, "users", "s_users.png")
    section_shot(page, "hardware", "s_hardware.png")
    section_shot(page, "weather", "s_weather.png")
    section_shot(page, "video", "s_video.png")
    section_shot(page, "offsite-backup", "s_offsite.png")
    section_shot(page, "branding", "s_branding.png")
    section_shot(page, "rating-sources", "s_rating.png")
    section_shot(page, "polars", "s_polars.png")

    # collapsed overview of the settings page (all closed)
    page.evaluate("document.querySelectorAll('.settings-section').forEach(s => s.open = false)")
    page.evaluate("window.scrollTo(0,0)")
    page.wait_for_timeout(200)
    shot(page, "s_overview.png", full_page=True)

    # --- Boats: list + add/lookup form ---
    page.goto(f"{BASE}/admin/boats", wait_until="load")
    page.wait_for_timeout(300)
    shot(page, "boats_list.png", full_page=False)

    page.goto(f"{BASE}/admin/boats/new", wait_until="load")
    page.wait_for_timeout(300)
    shot(page, "boat_form.png", full_page=True)

    # --- Marks ---
    page.goto(f"{BASE}/admin/marks", wait_until="load")
    page.wait_for_timeout(300)
    shot(page, "marks.png", full_page=True)

    # --- Backup / restore ---
    page.goto(f"{BASE}/admin/backup", wait_until="load")
    page.wait_for_timeout(300)
    shot(page, "backup_restore.png", full_page=True)

    browser.close()

print("DONE")
