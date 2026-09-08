"""Cut the figures that used to be hand-cropped, anchored to the page instead.

The crop_* images were bands of a full-page capture cut by hand in Pillow, so
re-cutting them meant knowing pixel offsets nobody had written down -- and when
the page grew, the old offsets framed the wrong thing. Each figure here is taken
from the element it is a picture of, so it re-cuts itself next time.

    RO_CAP_BASE=http://127.0.0.1:5059 python capture_crops.py
"""
import os
import pathlib

from playwright.sync_api import sync_playwright

_HERE = pathlib.Path(__file__).resolve().parent
REPO = _HERE.parent
SHOTS = _HERE / "screenshots"
REF = _HERE / "ref_screens"
BASE = os.environ.get("RO_CAP_BASE", "http://localhost:5050")
PASSWORD = os.environ.get("RO_CAP_PASSWORD", "") or (
    REPO / "runtime" / "initial_admin_password.txt").read_text(encoding="utf-8").strip()

PAD = 12


def clip_of(page, selector_js, pad=PAD):
    box = page.evaluate(selector_js)
    if not box:
        return None
    return {"x": max(0, box["x"] - pad), "y": max(0, box["y"] - pad),
            "width": box["width"] + 2 * pad, "height": box["height"] + 2 * pad}


def section_after(heading_text):
    """Bounding box from a heading down to the next heading of any level."""
    return f"""
    (() => {{
      const heads = [...document.querySelectorAll('h2,h3,h4')];
      const h = heads.find(e => e.textContent.trim().startsWith({heading_text!r}));
      if (!h) return null;
      const start = h.getBoundingClientRect().top + window.scrollY;
      // Stop at the next heading of the SAME OR HIGHER level: stopping at any
      // heading cut "Rating-band classes" off at its own "IRC classes" child,
      // leaving a 98px strip of heading and nothing else.
      const level = Number(h.tagName[1]);
      const later = heads.filter(e => (e.getBoundingClientRect().top + window.scrollY) > start + 5
                                   && Number(e.tagName[1]) <= level);
      const end = later.length ? later[0].getBoundingClientRect().top + window.scrollY
                               : document.body.scrollHeight;
      const box = h.getBoundingClientRect();
      return {{x: box.left + window.scrollX, y: start, width: box.width, height: end - start}};
    }})()"""


def shoot(page, name, clip, out_dir):
    if not clip or clip["height"] < 40:
        print(f"  SKIP {name}: nothing to frame")
        return
    # full_page, or a clip below the fold is "outside the resulting image".
    page.screenshot(path=str(out_dir / name), clip=clip, full_page=True)
    print(f"  cut  {name}  {int(clip['width'])}x{int(clip['height'])} at y={int(clip['y'])}")


with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_context(viewport={"width": 1440, "height": 900}).new_page()
    page.goto(f"{BASE}/login", wait_until="load")
    page.fill('input[name="username"]', "admin")
    page.fill('input[name="password"]', PASSWORD)
    page.click('button[type="submit"]')
    page.wait_for_load_state("load")

    # --- Series: classes and the default start plan -------------------------
    page.goto(f"{BASE}/admin/series/1", wait_until="load")
    page.evaluate("document.querySelector('details').open = true")
    page.wait_for_timeout(400)
    # The names are not what they look like: both builders caption crop_classes as
    # the discards/publish section and crop_classes2 as the rating bands.
    shoot(page, "crop_classes.png", clip_of(page, section_after("Series discards")), SHOTS)
    shoot(page, "crop_classes2.png", clip_of(page, section_after("Rating-band classes")), SHOTS)
    shoot(page, "crop_startplan.png", clip_of(page, section_after("Default start plan")), SHOTS)

    # --- Race sheet: the Course & start tab ---------------------------------
    page.goto(f"{BASE}/admin/race/1", wait_until="load")
    page.wait_for_timeout(900)
    # The race page lands part-scrolled, and the topbar is sticky: left where it
    # is, it paints itself straight over the heading this crop starts at.
    page.evaluate("window.scrollTo(0, 0)")
    page.wait_for_timeout(250)
    # From the heading down to Save. It used to frame only the form, but the
    # course buttons moved up under the heading in v0.273 and would have been cut
    # off; and `#tab-course form` now matches the help dialog's close-row form
    # first, which is 30px of nothing.
    top = page.evaluate("""(() => {
        const pane = document.getElementById('tab-course');
        if (!pane) return null;
        const h = pane.querySelector('.section-head');
        const f = pane.querySelector('form.wide-form');
        if (!h || !f) return null;
        const a = h.getBoundingClientRect(), b = f.getBoundingClientRect();
        return {x: Math.min(a.left, b.left) + scrollX, y: a.top + scrollY,
                width: Math.max(a.width, b.width),
                height: (b.bottom + scrollY) - (a.top + scrollY)};
    })()""")
    shoot(page, "crop_course_top.png",
          {"x": max(0, top["x"] - PAD), "y": max(0, top["y"] - PAD),
           "width": top["width"] + 2 * PAD, "height": top["height"] + 2 * PAD} if top else None,
          SHOTS)
    # Chart plus the predicted leg-by-leg timing, which is what the caption says.
    # Taking "the first table below the map" caught the GPS positions list instead.
    page.evaluate("""(() => {
        const d = document.getElementById('raceLegAnalysisTable');
        if (d) { const det = d.closest('details'); if (det) det.open = true; }
    })()""")
    page.wait_for_timeout(400)
    chart = page.evaluate("""(() => {
        const m = document.querySelector('.course-map-wrap, .course-map');
        const t = document.getElementById('raceLegAnalysisTable');
        if (!m || !t) return null;
        const b = m.getBoundingClientRect();
        const end = t.getBoundingClientRect().bottom + scrollY;
        return {x: b.left + scrollX, y: b.top + scrollY, width: b.width, height: end - (b.top + scrollY)};
    })()""")
    shoot(page, "crop_course_bottom.png",
          {"x": max(0, chart["x"] - PAD), "y": max(0, chart["y"] - PAD),
           "width": chart["width"] + 2 * PAD, "height": chart["height"] + 2 * PAD} if chart else None, SHOTS)

    # --- Marks: the foot of the list and the Add a mark form ----------------
    page.goto(f"{BASE}/admin/marks", wait_until="load")
    page.wait_for_timeout(500)
    foot = page.evaluate("""(() => {
        const add = [...document.querySelectorAll('h2,h3')].find(e => e.textContent.trim().startsWith('Add a mark'));
        if (!add) return null;
        const form = add.closest('.card, section, form') || add.parentElement;
        const fb = form.getBoundingClientRect();
        const rows = [...document.querySelectorAll('table tr')];
        const last = rows.slice(-6)[0];
        const top = last ? last.getBoundingClientRect().top + scrollY : fb.top + scrollY - 200;
        return {x: fb.left + scrollX, y: top, width: fb.width, height: (fb.bottom + scrollY) - top};
    })()""")
    shoot(page, "marks_top.png",
          {"x": max(0, foot["x"] - PAD), "y": max(0, foot["y"] - PAD),
           "width": foot["width"] + 2 * PAD, "height": foot["height"] + 2 * PAD} if foot else None, REF)

    browser.close()
