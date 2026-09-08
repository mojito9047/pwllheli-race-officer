import os
_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)
import os
from datetime import datetime, timedelta
from PIL import Image
from playwright.sync_api import sync_playwright

BASE = os.environ.get("RO_CAP_BASE", "http://localhost:5050")
OUT_DIR = os.path.join(_HERE, "competitor_screens")
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


def split_tall_shot(name, top_name, bottom_name, overlap=80):
    """Cut one tall screenshot into two page-sized halves for the guides.

    The public results are taller than a PDF page, so the guides show them as
    "1 of 2" / "2 of 2" figures with a little overlap to keep the join readable.
    """
    with Image.open(os.path.join(OUT_DIR, name)) as im:
        width, height = im.size
        middle = height // 2
        im.crop((0, 0, width, min(height, middle + overlap))).save(os.path.join(OUT_DIR, top_name))
        im.crop((0, max(0, middle - overlap), width, height)).save(os.path.join(OUT_DIR, bottom_name))
    print("saved", top_name, "+", bottom_name)


with sync_playwright() as p:
    browser = p.chromium.launch()

    # ---- admin context: create + configure a temporary demo race ----
    admin_ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    page = admin_ctx.new_page()

    def api_post(path, data):
        r = page.request.post(f"{BASE}{path}", form=data)
        return r

    login_page = page.goto(f"{BASE}/login", wait_until="load")
    csrf = page.eval_on_selector('input[name="_csrf_token"]', "el => el.value")
    api_post("/login", {"username": USERNAME, "password": PASSWORD, "_csrf_token": csrf, "next": ""})

    page.goto(f"{BASE}/admin/race/new", wait_until="load")
    csrf = page.evaluate("window.RO_CSRF_TOKEN")
    form = {"name": "Club Race - Guide Demo", "class_name": "IRC", "series_id": "", "add_all_active": "1",
            "notes": "", "_csrf_token": csrf}
    resp = api_post("/admin/race/new", form)
    print("create race status", resp.status, resp.url)

    # find the new race id (highest id, name match)
    page.goto(f"{BASE}/admin/races", wait_until="load")
    race_id = page.eval_on_selector_all(
        "table a",
        """(links) => {
            // textContent, not innerText: the Races page groups races into
            // collapsed roll-ups, and innerText is empty for anything that is
            // not rendered -- so this found nothing and the capture ran on
            // /admin/race/None.
            const l = links.find(a => a.textContent.trim() === 'Open' && a.closest('tr').textContent.includes('Guide Demo'));
            if (!l) return null;
            const m = l.href.match(/race\\/(\\d+)/);
            return m ? m[1] : null;
        }""",
    )
    print("new race id:", race_id)

    # set warning time to 2 minutes ago (so we're inside the signal window) and a course
    warning_time = (datetime.now() - timedelta(minutes=2)).strftime("%Y-%m-%dT%H:%M")
    page.goto(f"{BASE}/admin/race/{race_id}", wait_until="load")
    csrf = page.eval_on_selector('meta', "") if False else None
    csrf = page.evaluate("window.RO_CSRF_TOKEN")
    api_post(f"/admin/race/{race_id}/update", {
        "name": "Club Race - Guide Demo",
        "class_name": "IRC",
        "series_id": "",
        "start_time": warning_time,
        "course_no": "1",
        "_csrf_token": csrf,
    })

    # ---- public context (separate, logged-out) for the actual guide screenshots ----
    pub_ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    pub = pub_ctx.new_page()

    # Landing page - desktop
    pub.goto(f"{BASE}/public/current", wait_until="load")
    pub.wait_for_timeout(600)
    shot(pub, "01_landing_desktop.png")

    # Landing page - the Wind tab (the tall wind-history plot)
    pub.click('#competitorHomeTabs .tab-button[data-tab="htab-wind"]')
    pub.wait_for_timeout(1500)
    # The capture machine has no weather station reachable, so the status line
    # carries a connection error that would be misleading in the guide. Stop the
    # page's own 5-second refresh first, or it writes the message back before the
    # screenshot is taken. The plot is already drawn and stays as it is.
    # Detach it rather than blanking the text: the page's in-flight fetch still
    # holds a reference and would write the message back into a blanked element.
    pub.evaluate("() => { const el = document.getElementById('homeWeatherMessage'); if (el) el.remove(); }")
    shot(pub, "01b_landing_wind_desktop.png")
    pub.click('#competitorHomeTabs .tab-button[data-tab="htab-races"]')
    pub.wait_for_timeout(300)

    # Landing page - mobile
    pub_mobile_ctx = browser.new_context(viewport={"width": 390, "height": 844})
    pub_m = pub_mobile_ctx.new_page()
    pub_m.goto(f"{BASE}/public/current", wait_until="load")
    pub_m.wait_for_timeout(600)
    shot(pub_m, "02_landing_mobile.png")

    # Specific race page - "before/during start" state (demo race), desktop
    pub.goto(f"{BASE}/public/race/{race_id}", wait_until="load")
    pub.wait_for_timeout(600)
    shot(pub, "03_race_live_desktop.png")

    # same on a phone: the Entries tab, then the Chart tab
    pub_m.goto(f"{BASE}/public/race/{race_id}", wait_until="load")
    pub_m.wait_for_timeout(600)
    shot(pub_m, "04_race_live_mobile.png")
    pub_m.click('.public-tabs .tab-button[data-tab="ptab-chart"]')
    pub_m.wait_for_timeout(1500)
    shot(pub_m, "04b_race_chart_mobile.png")

    # Specific race page - "after racing" results state (existing finished race id=1),
    # which now opens on its Leader board tab.
    pub.goto(f"{BASE}/public/race/1", wait_until="load")
    pub.wait_for_timeout(800)
    shot(pub, "05_race_results_desktop.png")
    split_tall_shot("05_race_results_desktop.png", "crop_results_top.png", "crop_results_bottom.png")

    # same, mobile
    pub_m.goto(f"{BASE}/public/race/1", wait_until="load")
    pub_m.wait_for_timeout(800)
    shot(pub_m, "06_race_results_mobile.png")

    pub_ctx.close()
    pub_mobile_ctx.close()

    # ---- cleanup: delete the temporary demo race ----
    page.goto(f"{BASE}/admin/race/{race_id}", wait_until="load")
    csrf = page.evaluate("window.RO_CSRF_TOKEN")
    del_resp = api_post(f"/admin/race/{race_id}/delete", {"confirm_delete": "1", "_csrf_token": csrf})
    print("delete race status:", del_resp.status)

    admin_ctx.close()
    browser.close()

print("DONE")
