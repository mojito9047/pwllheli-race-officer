"""Capture the Virtual Race Officer page for the Reference Manual.

Its own script because the page needs a conversation in it to be worth looking
at: the figure shows the page as somebody using it sees it, with three courses
offered as numbered cards and a read-back waiting for Yes.

It needs no running app and touches no database of yours. The app is imported
and run in this process against a sanitised copy of the newest hut backup in
HutData/ -- the same copy, made the same way, that scripts/eval_vro.py measures
against -- and the browser's every request is answered by it in-process.

The interpreter is a script, not a model. The built-in grammar used to stand in
for one here, and it is gone from the page; a model would make the figure cost
money and come out different every time. The script returns exactly what a
model would for these sentences -- a look-up, an answer in words, a change --
and everything after that is the app's own: the look-up, the cards, the checks
and the read-back.

    python scripts/capture_onwater_screens.py
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlparse

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "scripts"))

from playwright.sync_api import sync_playwright     # noqa: E402

import eval_vro                                       # noqa: E402

OUT_DIR = _REPO / "scripts" / "ref_screens"
HOST = "http://vro.capture"
# The wind the suggestion is asked for in: named, so the figure does not depend
# on what the hut's instrument last read before the backup was taken.
TWD, TWS = 220, 12


def conversation(ro, vro, reads, race_id):
    """The sentences, and what a model would have made of each."""
    from core.assistant import ANSWER, Intent

    def parse(text, context):
        said = text.lower()
        if said == "status":
            return Intent("race_status", {"race_id": race_id})
        if said.startswith("which course"):
            context.read("recommend_courses", {"target_minutes": 60, "twd": TWD, "tws": TWS})
            cards = reads.offered_courses()
            lines = [f"Three of the club's courses for about an hour in {TWS} knots from {TWD}°:"]
            for n, card in enumerate(cards[:3], 1):
                lines.append(f"{n}. **{card['title']}** — {card['length_nm']} nm, about "
                             f"{card['minutes']} min.")
            lines.append("I would sail the first: it is the nearest to the hour.")
            return Intent(ANSWER, {"text": "\n".join(lines)})
        if "second" in said:
            second = int(str(conversation.offered[1]["title"]).split()[-1])
            return Intent("set_start_and_course", {"race_id": race_id, "course_no": second})
        return None

    return parse


def main() -> None:
    zip_path = eval_vro.newest_backup()
    work = Path(tempfile.mkdtemp(prefix="vro-capture-"))
    try:
        files = eval_vro.unpack(zip_path, work)
        # No credentials, nothing outbound, and an interpreter that is never
        # called: the scripted one below stands in for it.
        eval_vro.sanitise(files["race_officer.db"], {"assistant_api_key": "capture",
                                                     "assistant_model": "capture",
                                                     "assistant_base_url": ""})
        import app as ro
        from core import appstate, track
        from routes import assistant as vro
        from routes import assistant_reads as reads

        appstate.DB_PATH = files["race_officer.db"]
        if "track_positions.db" in files:
            track.TRACK_DB_PATH = files["track_positions.db"]
        if "marks.json" in files:
            appstate.MARKS_DATA = json.loads(files["marks.json"].read_text(encoding="utf-8"))
            appstate.MARKS = appstate.MARKS_DATA["marks"]
        if "courses.json" in files:
            appstate.COURSES_DATA = json.loads(files["courses.json"].read_text(encoding="utf-8"))
            appstate.COURSES = appstate.COURSES_DATA["courses"]
            appstate.COURSE_BY_NO = {int(c["course_no"]): c for c in appstate.COURSES}
        ro.app.config["TESTING"] = True
        ro.app.config["SESSION_COOKIE_SECURE"] = False
        with ro.app.app_context():
            ro._init_db_uncached()
            race_id = eval_vro.upcoming_race(ro)

        parse = conversation(ro, vro, reads, race_id)
        vro.parser_from_config = lambda config, transport=None: parse
        vro.interpreter_status = lambda config: {"model": "capture", "grammar_only": False,
                                                 "error": "", "text": "capture"}

        with ro.get_db() as db:
            user_id = int(db.execute("SELECT id FROM users WHERE username = ?",
                                     (eval_vro.EVAL_USER,)).fetchone()[0])
        client = ro.app.test_client()
        with client.session_transaction() as sess:
            sess["user_id"] = user_id
            sess["username"] = eval_vro.EVAL_USER
            sess["_csrf_token"] = eval_vro.TOKEN
            sess[ro.SESSION_APP_VERSION_KEY] = ro.APP_VERSION

        def say(text):
            resp = client.post("/admin/api/assistant/command",
                               json={"text": text, "_csrf_token": eval_vro.TOKEN},
                               headers={"X-CSRF-Token": eval_vro.TOKEN})
            return resp.get_json() or {}

        say("status")
        offered = say("Which course would you use for about an hour in 12 knots from 220?")
        conversation.offered = offered.get("courses") or []
        if len(conversation.offered) < 2:
            raise SystemExit("The recommendation offered fewer than two courses; nothing to choose.")
        waiting = say("Use the second one")
        if waiting.get("status") != "needs_confirmation":
            raise SystemExit(f"Expected a read-back waiting for Yes, got {waiting}")

        def answer(route, request):
            """Every request the page makes, answered by the app in this process."""
            url = urlparse(request.url)
            if f"{url.scheme}://{url.netloc}" != HOST:
                return route.abort()
            if url.path.startswith("/static/"):
                return route.fulfill(path=str(_REPO / "static" / url.path[len("/static/"):]))
            resp = client.open(url.path + (f"?{url.query}" if url.query else ""),
                               method=request.method, data=request.post_data,
                               headers={"Content-Type": request.headers.get("content-type", ""),
                                        "X-CSRF-Token": eval_vro.TOKEN})
            route.fulfill(status=resp.status_code, body=resp.get_data(),
                          headers={"Content-Type": resp.content_type or "text/html"})

        OUT_DIR.mkdir(parents=True, exist_ok=True)
        shots = []
        with sync_playwright() as p:
            browser = p.chromium.launch()
            # A phone, because that is what this page is: a desktop-width figure
            # would show a layout nobody using it ever sees. One screen is not
            # tall enough for the offer and the read-back that chose from it, and
            # one very tall screenshot shrank to an unreadable strip on the page,
            # so it is two screens side by side: the offer, then the read-back.
            context = browser.new_context(viewport={"width": 430, "height": 932},
                                          device_scale_factor=2)
            context.route("**/*", answer)
            page = context.new_page()
            page.goto(f"{HOST}/vro", wait_until="load")
            page.wait_for_selector(".ow-cards .ow-card")
            page.evaluate("""() => {
                const thread = document.getElementById('thread');
                const offer = [...thread.querySelectorAll('.ow-app')]
                    .find(m => m.nextElementSibling && m.nextElementSibling.classList.contains('ow-cards'));
                thread.scrollTop = offer.previousElementSibling.offsetTop - 12;
            }""")
            page.wait_for_timeout(400)
            shots.append(page.screenshot())
            # Where the page opens: the end of the conversation, and Yes.
            page.evaluate("() => { const t = document.getElementById('thread'); t.scrollTop = t.scrollHeight; }")
            page.wait_for_timeout(400)
            shots.append(page.screenshot())
            browser.close()

        import io
        from PIL import Image
        screens = [Image.open(io.BytesIO(shot)).convert("RGB") for shot in shots]
        gap = 60
        sheet = Image.new("RGB", (sum(s.width for s in screens) + gap * (len(screens) - 1),
                                  max(s.height for s in screens)), (255, 255, 255))
        x = 0
        for screen in screens:
            sheet.paste(screen, (x, 0))
            x += screen.width + gap
        path = OUT_DIR / "onwater_page.png"
        sheet.save(path)
        print(f"saved {path.relative_to(_REPO)}")
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    os.environ.setdefault("RO_COOKIE_SECURE", "0")
    main()
