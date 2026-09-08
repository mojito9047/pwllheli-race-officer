import os
_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)
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
with sync_playwright() as p:
    browser = p.chromium.launch()
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    page = ctx.new_page()

    def api_post(path, data):
        return page.request.post(f"{BASE}{path}", form=data)

    page.goto(f"{BASE}/login", wait_until="load")
    csrf = page.eval_on_selector('input[name="_csrf_token"]', "el => el.value")
    api_post("/login", {"username": USERNAME, "password": PASSWORD, "_csrf_token": csrf, "next": ""})

    # create a demo race with every active boat added
    page.goto(f"{BASE}/admin/race/new", wait_until="load")
    csrf = page.evaluate("window.RO_CSRF_TOKEN")
    resp = api_post("/admin/race/new", {
        "name": "R1 - Guide Demo", "class_name": "IRC", "series_id": "",
        "add_all_active": "1", "notes": "", "_csrf_token": csrf,
    })
    print("create race status", resp.status, resp.url)

    page.goto(f"{BASE}/admin/races", wait_until="load")
    race_id = page.eval_on_selector_all(
        "table a",
        """(links) => {
            // textContent, not innerText: the Races page groups races into
            // collapsed roll-ups, and innerText is empty for anything not
            // rendered -- so this found nothing and the capture went to
            // /admin/race/None.
            const l = links.find(a => a.textContent.trim() === 'Open' && a.closest('tr').textContent.includes('Guide Demo'));
            if (!l) return null;
            const m = l.href.match(/race\\/(\\d+)/);
            return m ? m[1] : null;
        }""",
    )
    print("new race id:", race_id)

    # open the race page and switch to the Add entries tab
    page.goto(f"{BASE}/admin/race/{race_id}", wait_until="load")
    page.click('button[data-tab="tab-entries"]')
    page.wait_for_timeout(400)
    page.evaluate("window.scrollTo(0,0)")
    page.wait_for_timeout(150)
    path = os.path.join(OUT_DIR, "09_race_tab2_entries.png")
    page.screenshot(path=path, full_page=True)
    print("saved 09_race_tab2_entries.png")

    # cleanup: delete the demo race
    page.goto(f"{BASE}/admin/race/{race_id}", wait_until="load")
    csrf = page.evaluate("window.RO_CSRF_TOKEN")
    del_resp = api_post(f"/admin/race/{race_id}/delete", {"confirm_delete": "1", "_csrf_token": csrf})
    print("delete race status:", del_resp.status)

    browser.close()

print("DONE")
