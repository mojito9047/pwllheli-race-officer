import os
_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)
from datetime import datetime, timedelta
from playwright.sync_api import sync_playwright

BASE = os.environ.get("RO_CAP_BASE", "http://localhost:5050")
SCREENSHOTS = os.path.join(_HERE, "screenshots")
COMP = os.path.join(_HERE, "competitor_screens")
PW = os.environ.get("RO_CAP_PASSWORD", "")
if not PW:
    _pwf = os.path.join(_REPO, "runtime", "initial_admin_password.txt")
    if os.path.exists(_pwf):
        PW = open(_pwf, encoding="utf-8").read().strip()
with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width": 1440, "height": 900})
    pg = ctx.new_page()
    pg.goto(f"{BASE}/login", wait_until="load")
    pg.fill('input[name="username"]', "admin"); pg.fill('input[name="password"]', PW)
    pg.click('button[type="submit"]'); pg.wait_for_load_state("load")
    csrf = lambda: pg.evaluate("window.RO_CSRF_TOKEN")

    # new-race form with Pursuit selected
    pg.goto(f"{BASE}/admin/race/new", wait_until="load")
    pg.select_option('#raceType', 'pursuit'); pg.wait_for_timeout(300)
    pg.screenshot(path=os.path.join(SCREENSHOTS, "pursuit_new_race.png"), full_page=True)

    # create the race + boats + timing
    pg.request.post(f"{BASE}/admin/race/new", form={"name": "Pursuit Guide", "race_type": "pursuit",
        "pursuit_rating": "IRC_TCC", "pursuit_duration_min": "90", "series_id": "", "add_all_active": "1", "_csrf_token": csrf()})
    pg.goto(f"{BASE}/admin/races", wait_until="load")
    rid = pg.eval_on_selector_all("table a", """(l)=>{const x=l.find(a=>a.textContent.trim()==='Open'&&a.closest('tr').textContent.includes('Pursuit Guide'));if(!x)return null;const m=x.href.match(/race\\/(\\d+)/);return m?m[1]:null;}""")
    warn = (datetime.now() - timedelta(minutes=2)).strftime("%Y-%m-%dT%H:%M")
    pg.goto(f"{BASE}/admin/race/{rid}", wait_until="load")
    pg.request.post(f"{BASE}/admin/race/{rid}/update", form={"name": "Pursuit Guide", "start_time": warn,
        "pursuit_duration_min": "90", "pursuit_rating": "IRC_TCC", "course_no": "1", "series_id": "", "notes": "", "_csrf_token": csrf()})

    # admin start console ladder
    pg.goto(f"{BASE}/admin/race/{rid}", wait_until="domcontentloaded"); pg.wait_for_timeout(500)
    pg.click('button[data-tab="tab-start"]'); pg.wait_for_timeout(1200)
    pg.screenshot(path=os.path.join(SCREENSHOTS, "pursuit_start_console.png"), full_page=True)

    # competitor start-times page
    pub = b.new_context(viewport={"width": 1000, "height": 1150})
    pp = pub.new_page()
    pp.goto(f"{BASE}/public/race/{rid}", wait_until="domcontentloaded"); pp.wait_for_timeout(1200)
    pp.screenshot(path=os.path.join(COMP, "pursuit_public.png"), full_page=True)
    pub.close()

    # cleanup
    pg.goto(f"{BASE}/admin/race/{rid}", wait_until="load")
    pg.request.post(f"{BASE}/admin/race/{rid}/delete", form={"confirm_delete": "1", "_csrf_token": csrf()})
    b.close()
print("DONE")
