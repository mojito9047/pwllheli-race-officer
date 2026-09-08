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
# (output name, start heading text, end boundary)
# end boundary: {"h4": "..."} = next heading, {"sel": "..."} = element's top, None = end of #video.
# s_video_2 deliberately stops before .settings-status-stack so the live R2 test
# diagnostics (real Cloudflare account endpoint / bucket URLs) are NOT in the manual.
RANGES = [
    ("s_video_1.png", "Camera input and recording mode", {"h4": "Public video and live image publishing"}),
    ("s_video_2.png", "Public video and live image publishing", {"sel": "#video .settings-status-stack"}),
    ("s_video_3.png", "Camera zoom / PTZ presets", None),
]

RECT_JS = """
([startText, end]) => {
  const hs = [...document.querySelectorAll('#video h4')];
  const start = hs.find(h => h.textContent.trim() === startText);
  const video = document.getElementById('video');
  const parent = start.parentElement.getBoundingClientRect();
  const sTop = start.getBoundingClientRect().top + window.scrollY;
  let bottom;
  if (end && end.h4) {
    const e = hs.find(h => h.textContent.trim() === end.h4);
    bottom = e.getBoundingClientRect().top + window.scrollY;
  } else if (end && end.sel) {
    bottom = document.querySelector(end.sel).getBoundingClientRect().top + window.scrollY - 8;
  } else {
    bottom = video.getBoundingClientRect().bottom + window.scrollY - 14;
  }
  const x = parent.left - 14;
  const width = parent.width + 28;
  return {x, y: sTop - 12, width, height: bottom - sTop + 4};
}
"""

with sync_playwright() as p:
    browser = p.chromium.launch()
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    page = ctx.new_page()
    page.goto(f"{BASE}/login", wait_until="load")
    page.fill('input[name="username"]', USERNAME)
    page.fill('input[name="password"]', PASSWORD)
    page.click('button[type="submit"]')
    page.wait_for_load_state("load")

    page.goto(f"{BASE}/admin/settings", wait_until="load")
    page.wait_for_timeout(400)
    page.evaluate("document.querySelectorAll('.settings-section').forEach(s => s.open = (s.id === 'video'))")
    page.evaluate("window.scrollTo(0,0)")
    page.wait_for_timeout(400)

    for name, start_text, end_text in RANGES:
        rect = page.evaluate(RECT_JS, [start_text, end_text])
        page.screenshot(path=os.path.join(OUT_DIR, name), clip=rect, full_page=True)
        print("saved", name, {k: round(v) for k, v in rect.items()})

    browser.close()
print("DONE")
