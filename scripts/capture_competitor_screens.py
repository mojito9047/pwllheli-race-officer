"""Take the screenshots the competitor's guide is built from.

    python scripts/capture_competitor_screens.py

Drives a RUNNING copy of the app (never the hut itself): it signs in, makes a
temporary race in its start sequence, photographs the public pages, and deletes
the race again. The pictures go to scripts/competitor_screens/, and
scripts/build_competitor_guide.py lays them out.

Which app, and which races, come from the environment:

    RO_CAP_BASE            the app's address              http://localhost:5050
    RO_CAP_USERNAME        an administrator there         admin
    RO_CAP_PASSWORD        its password                   runtime/initial_admin_password.txt
    RO_CAP_RESULTS_RACE    a finished race, for results   1
    RO_CAP_CHART_RACE      a finished race WITH tracks    the results race
    RO_CAP_PHOTO_RACE      a race with a start photo      (skipped when unset)
    RO_CAP_PURSUIT_RACE    a pursuit race not yet begun   (skipped when unset)

A phone is photographed as a phone shows it: one screen, 390 x 844, at twice
the pixels so it stays sharp in print. They were whole-page captures once,
nine thousand pixels tall and a postage stamp wide in the guide.

The race page opens on its Chart tab and a finished race has Provisional
results where Entries was (November 2026), so every other tab is opened by
pressing it, as a competitor would.
"""
import os
from datetime import datetime, timedelta

from PIL import Image
from playwright.sync_api import sync_playwright

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)

BASE = os.environ.get("RO_CAP_BASE", "http://localhost:5050")
OUT_DIR = os.path.join(_HERE, "competitor_screens")
os.makedirs(OUT_DIR, exist_ok=True)

USERNAME = os.environ.get("RO_CAP_USERNAME", "admin")
PASSWORD = os.environ.get("RO_CAP_PASSWORD", "")
if not PASSWORD:
    _pwf = os.path.join(_REPO, "runtime", "initial_admin_password.txt")
    if os.path.exists(_pwf):
        PASSWORD = open(_pwf, encoding="utf-8").read().strip()

RESULTS_RACE = os.environ.get("RO_CAP_RESULTS_RACE", "1")
CHART_RACE = os.environ.get("RO_CAP_CHART_RACE", RESULTS_RACE)
PHOTO_RACE = os.environ.get("RO_CAP_PHOTO_RACE", "")
PURSUIT_RACE = os.environ.get("RO_CAP_PURSUIT_RACE", "")

DESKTOP = {"viewport": {"width": 1440, "height": 900}}
PHONE = {"viewport": {"width": 390, "height": 844}, "device_scale_factor": 2,
         "is_mobile": True, "has_touch": True}
# The offer to add the pages to the home screen is not part of any picture.
QUIET = "try { localStorage.setItem('roInstallHint', 'dismissed'); } catch (err) {}"


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


def open_tab(page, tab, settle_ms=600):
    page.click(f'.public-tabs .tab-button[data-tab="{tab}"]')
    page.mouse.move(0, 0)       # a phone has no pointer left hovering over the page
    page.wait_for_timeout(settle_ms)


def tabs_to_top(page):
    """Scroll a phone so the tab row sits just under the page's own header."""
    page.evaluate("""() => {
        const tabs = document.querySelector('.public-tabs');
        const header = document.querySelector('body > header');
        const under = header ? header.getBoundingClientRect().height : 0;
        window.scrollTo(0, tabs.getBoundingClientRect().top + window.scrollY - under - 6);
    }""")
    page.wait_for_timeout(300)


with sync_playwright() as p:
    browser = p.chromium.launch()

    # ---- public contexts (logged out) for the pictures themselves ----
    pub_ctx = browser.new_context(**DESKTOP)
    pub_ctx.add_init_script(QUIET)
    pub = pub_ctx.new_page()
    pub_mobile_ctx = browser.new_context(**PHONE)
    pub_mobile_ctx.add_init_script(QUIET)
    pub_m = pub_mobile_ctx.new_page()

    # Landing page - desktop. Taken before the temporary race exists, so the
    # current race is a real one in a real series.
    pub.goto(f"{BASE}/public/current", wait_until="load")
    pub.wait_for_timeout(1200)
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

    # Landing page - on a phone
    pub_m.goto(f"{BASE}/public/current", wait_until="load")
    pub_m.wait_for_timeout(1200)
    shot(pub_m, "02_landing_mobile.png", full_page=False)

    # ---- admin context: create + configure a temporary demo race ----
    admin_ctx = browser.new_context(**DESKTOP)
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
    if not race_id:
        raise SystemExit("The temporary race was not created: is RO_CAP_USERNAME / RO_CAP_PASSWORD an administrator?")

    try:
        # set warning time to 2 minutes ago (so we're inside the signal window) and a course
        warning_time = (datetime.now() - timedelta(minutes=2)).strftime("%Y-%m-%dT%H:%M")
        page.goto(f"{BASE}/admin/race/{race_id}", wait_until="load")
        csrf = page.evaluate("window.RO_CSRF_TOKEN")
        api_post(f"/admin/race/{race_id}/update", {
            "name": "Club Race - Guide Demo",
            "class_name": "IRC",
            "series_id": "",
            "start_time": warning_time,
            "course_no": "1",
            "_csrf_token": csrf,
        })

        # Specific race page - "before/during start" state (demo race), desktop.
        # It opens on the Chart, which is what the picture shows.
        pub.goto(f"{BASE}/public/race/{race_id}", wait_until="load")
        pub.wait_for_timeout(2500)
        shot(pub, "03_race_live_desktop.png")

        # The same race on a phone: the Entries tab.
        pub_m.goto(f"{BASE}/public/race/{race_id}", wait_until="load")
        pub_m.wait_for_timeout(1500)
        open_tab(pub_m, "ptab-entries")
        shot(pub_m, "04_race_live_mobile.png", full_page=False)
    finally:
        # ---- cleanup: delete the temporary demo race ----
        page.goto(f"{BASE}/admin/race/{race_id}", wait_until="load")
        csrf = page.evaluate("window.RO_CSRF_TOKEN")
        del_resp = api_post(f"/admin/race/{race_id}/delete", {"confirm_delete": "1", "_csrf_token": csrf})
        print("delete race status:", del_resp.status)
        admin_ctx.close()

    # The Chart tab on a phone, part-way through a race that was tracked: the
    # fleet on the course, the timeline, and the Leaderboard tab over the chart.
    pub_m.goto(f"{BASE}/public/race/{CHART_RACE}", wait_until="load")
    pub_m.wait_for_timeout(3500)
    pub_m.evaluate("""() => { const s = document.querySelector('.replay-slider'); if (!s || s.disabled) return;
        s.value = String(Number(s.min) + (Number(s.max) - Number(s.min)) * 0.45);
        s.dispatchEvent(new Event('input', {bubbles: true})); }""")
    if pub_m.query_selector('.replay-view option[value="fleet"]'):
        pub_m.select_option(".replay-view", "fleet")
    pub_m.wait_for_timeout(2500)
    tabs_to_top(pub_m)
    shot(pub_m, "04b_race_chart_mobile.png", full_page=False)

    # Specific race page - "after racing": the Provisional results tab of a
    # finished race.
    pub.goto(f"{BASE}/public/race/{RESULTS_RACE}", wait_until="load")
    pub.wait_for_timeout(1500)
    open_tab(pub, "ptab-results")
    shot(pub, "05_race_results_desktop.png")
    split_tall_shot("05_race_results_desktop.png", "crop_results_top.png", "crop_results_bottom.png")

    # same, on a phone
    pub_m.goto(f"{BASE}/public/race/{RESULTS_RACE}", wait_until="load")
    pub_m.wait_for_timeout(1500)
    open_tab(pub_m, "ptab-results")
    tabs_to_top(pub_m)
    shot(pub_m, "06_race_results_mobile.png", full_page=False)

    # The Start photo tab on a phone.
    if PHOTO_RACE:
        pub_m.goto(f"{BASE}/public/race/{PHOTO_RACE}", wait_until="load")
        pub_m.wait_for_timeout(1500)
        open_tab(pub_m, "ptab-startphoto", settle_ms=400)
        pub_m.wait_for_function(
            "() => { const i = document.querySelector('#ptab-startphoto img[data-start-photo]');"
            " return !i || (i.complete && i.naturalWidth > 0); }", timeout=30000)
        tabs_to_top(pub_m)
        pub_m.wait_for_timeout(800)
        shot(pub_m, "07_race_start_photo_mobile.png", full_page=False)

    # A pursuit race before its first start: the Start times tab.
    if PURSUIT_RACE:
        narrow_ctx = browser.new_context(viewport={"width": 1000, "height": 900})
        narrow_ctx.add_init_script(QUIET)
        narrow = narrow_ctx.new_page()
        narrow.goto(f"{BASE}/public/race/{PURSUIT_RACE}", wait_until="load")
        narrow.wait_for_timeout(1500)
        open_tab(narrow, "ptab-entries")
        shot(narrow, "pursuit_public.png")
        narrow_ctx.close()

    pub_ctx.close()
    pub_mobile_ctx.close()
    browser.close()

print("DONE")
