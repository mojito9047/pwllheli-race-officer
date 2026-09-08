"""Public competitor race pages (read-only).

Split out of app.py into the routes/ package. Registered on the shared Flask
``app`` by importing this module near the end of app.py. Endpoint names and the
``/x`` + ``/admin/x`` decorators are unchanged, so templates, url_for and the
public-endpoint allow-list keep working untouched. The app object and every
helper/global used are read off the running app module (see routes.app_module)
rather than ``from app import ...`` so this works whether app.py was started
with ``python app.py`` (module ``__main__``) or imported.
"""
from routes import app_module

_app = app_module()
app = _app.app
competitor_race_context = _app.competitor_race_context
competitor_race_state_signature = _app.competitor_race_state_signature
public_render_signature = _app.public_render_signature
from core import barreplay
from core.races import race_is_postponed
public_home_signature = _app.public_home_signature
get_race = _app.get_race
jsonify = _app.jsonify
request = _app.request
public_access_allowed_for_race = _app.public_access_allowed_for_race
public_access_denied_response = _app.public_access_denied_response
public_competitor_home_context = _app.public_competitor_home_context
race_leaderboard = _app.race_leaderboard
race_track_history = _app.race_track_history
redirect = _app.redirect
render_template = _app.render_template
url_for = _app.url_for
time = _app.time
track_config = _app.track_config
get_current_competitor_race = _app.get_current_competitor_race
race_first_start_dt = _app.race_first_start_dt

import core.bardisplay as bardisplay
import core.track as track


@app.route("/public/race/<int:race_id>/state")
def competitor_race_state(race_id: int):
    """Return lightweight race-state JSON for public-page refresh polling."""
    race = get_race(race_id)
    if not race:
        return jsonify({"ok": False, "message": "Race not found."}), 404
    if not public_access_allowed_for_race(race):
        return jsonify({"ok": False, "message": "Public race link is not valid."}), 403
    signature, state = competitor_race_state_signature(race)
    return jsonify({"ok": True, "signature": signature, **state})


@app.route("/public/race/<int:race_id>/positions")
def competitor_race_positions(race_id: int):
    """Public boat positions + position-on-the-water order for a race.

    Read-only and only for a race the public may see. Returns nothing when GPS
    tracking is switched off, so the competitor page simply shows no boats.
    """
    race = get_race(race_id)
    if not race:
        return jsonify({"ok": False, "message": "Race not found.", "boats": [], "leaderboard": []}), 404
    if not public_access_allowed_for_race(race):
        return jsonify({"ok": False, "message": "Public race link is not valid."}), 403
    if not track_config()["enabled"]:
        return jsonify({"ok": True, "enabled": False, "server_now": time.time(), "boats": [], "leaderboard": []})
    at_ts = request.args.get("at", type=float)      # replay: the fleet as it stood then
    board = race_leaderboard(race_id, at_ts=at_ts)
    boats = [b for b in board if b.get("lat") is not None and b.get("lon") is not None]
    response = jsonify({"ok": True, "enabled": True, "server_now": time.time(), "at": at_ts,
                        "boats": boats, "leaderboard": board})
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/public/race/<int:race_id>/track")
def competitor_race_track(race_id: int):
    """Public recorded tracks for a race — the whole thing, for replaying it.

    Same gate as the live positions: nothing at all when GPS tracking is off, and
    only for a race the public may see. The viewer scrubs the timeline locally
    rather than asking the server per frame, so this is fetched once.
    """
    race = get_race(race_id)
    if not race:
        return jsonify({"ok": False, "message": "Race not found.", "boats": []}), 404
    if not public_access_allowed_for_race(race):
        return jsonify({"ok": False, "message": "Public race link is not valid."}), 403
    if not track_config()["enabled"]:
        return jsonify({"ok": True, "enabled": False, "start": None, "end": None, "boats": []})
    since = request.args.get("since", type=float)   # only what is new
    history = race_track_history(race_id, since_ts=since)
    response = jsonify({"ok": True, "enabled": True, "server_now": time.time(), **history})
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/public/current")
def competitor_current_race():
    """Render the default public competitor landing page."""
    return render_template("competitors_home.html",
                           home_signature=public_home_signature(),
                           **public_competitor_home_context(mobile_view=False))


@app.route("/public/current/state")
def competitor_home_state():
    """Whether the landing page needs re-rendering.

    It is left open on phones and in the clubhouse, and it is rendered once: the current
    race, its first start and the clock that counts to it all come from the server. This
    is how it finds out the race officer has set the start.
    """
    return jsonify({"ok": True, "signature": public_home_signature()})


@app.route("/public/mobile/current")
def competitor_current_race_mobile():
    """Legacy mobile link: the single public page is now responsive, so redirect.

    Kept so links already shared with competitors keep working.
    """
    return redirect(url_for("competitor_current_race"))


@app.route("/public/race")
def competitor_current_race_sheet():
    """The public race page for whichever race is current, and it keeps up.

    A competitor on the water opens this from the home page, sails the race and
    finishes. The race officer finishes the fleet and moves on to the next race
    -- and until v0.277 that competitor sat on the race they had just sailed,
    because "Go to current race" handed them /public/race/<id> and pinned them to
    it. Getting to the next race meant knowing to go back to the home page and
    press the same button again, which is not something to work out one-handed on
    a wet phone.

    Exactly the distinction /bar and /bar/<id> already draw for the clubhouse
    television: no id means follow the day. /public/race/<id> still pins, which is
    what a link shared to somebody -- or a race being looked back at -- needs.
    """
    race = get_current_competitor_race()
    if not race:
        return render_template("competitor_race.html", race=None, mobile_view=False), 404
    if not public_access_allowed_for_race(race):
        return public_access_denied_response()
    return render_template("competitor_race.html",
                           **competitor_race_context(race, mobile_view=False, pinned=False))


@app.route("/public/race/state")
def competitor_current_race_state():
    """The state signature of whichever race is current.

    The signature covers the race id, so a page polling this reloads of its own
    accord when the race officer moves on -- no new client code, and the same
    two-second poll that already carries a start time or a shortened course.
    """
    race = get_current_competitor_race()
    if not race:
        return jsonify({"ok": False, "message": "No current race."}), 404
    if not public_access_allowed_for_race(race):
        return jsonify({"ok": False, "message": "Public race link is not valid."}), 403
    signature, state = competitor_race_state_signature(race)
    return jsonify({"ok": True, "signature": signature, **state})


@app.route("/public/race/<int:race_id>")
def competitor_race(race_id: int):
    """Render the public desktop page for one specific race, pinned to it."""
    race = get_race(race_id)
    if not race:
        return render_template("competitor_race.html", race=None, mobile_view=False), 404
    if not public_access_allowed_for_race(race):
        return public_access_denied_response()
    return render_template("competitor_race.html", **competitor_race_context(race, mobile_view=False))


@app.route("/public/mobile/race/<int:race_id>")
def competitor_race_mobile(race_id: int):
    """Legacy mobile link: the single public page is now responsive, so redirect.

    Kept so links already shared with competitors keep working.
    """
    return redirect(url_for("competitor_race", race_id=race_id))


@app.route("/bar")
@app.route("/bar/<int:race_id>")
def bar_display(race_id: int = 0):
    """The clubhouse TV: a full-screen chart of the race with overlays.

    Meant to be opened once on a display in the bar and left alone, so it takes
    the *current* race by default and follows the day rather than needing someone
    to change the page between races. ``/bar/<id>`` pins it to one race, which is
    what you want when showing a race that has already been sailed.
    """
    race = get_race(race_id) if race_id else get_current_competitor_race()
    if not race:
        return render_template("bar_display.html", race=None), 404
    if not public_access_allowed_for_race(race):
        return public_access_denied_response()
    ctx = competitor_race_context(race, mobile_view=False)
    ctx["pinned_race"] = bool(race_id)
    # A replay carries its whole plan in the page: the two ends of the race and
    # every clip, so the display needs no second request and no request per
    # frame. It is asked for explicitly, so an ordinary /bar/<id> is still the
    # live view of that race.
    ctx["replay_plan"] = (barreplay.replay_plan(race)
                          if request.args.get("replay") == "1" else None)
    return render_template("bar_display.html", **ctx)


@app.route("/bar/state/<int:race_id>")
def bar_display_state(race_id: int):
    """What the TV needs that the track payload does not carry.

    The chart, the boats and the order all come down with
    ``/public/race/<id>/track`` exactly as they do on the competitor page. This
    adds the two things that are particular to the bar: whether the camera should
    be up (see ``core.bardisplay``), and — because the display is left running —
    whether the *current* race has moved on to a different one.
    """
    race = get_race(race_id)
    if not race:
        return jsonify({"ok": False, "message": "Race not found."}), 404
    if not public_access_allowed_for_race(race):
        return jsonify({"ok": False, "message": "Public race link is not valid."}), 403
    start_dt = race_first_start_dt(race)
    rows = race_leaderboard(int(race["id"]))
    now = time.time()
    # The ODM is the seaward end of the start/finish line and the thing the hut
    # camera is pointed at, so a boat rounding it mid-race is worth showing.
    line = track.race_finish_line_points(race)
    # Under AP there is no start coming, so the camera must not open its
    # start window and the caption must not count down to the stored time --
    # which is the old one, because postponing does not move it. The rest of the
    # window still applies: a boat rounding the ODM is worth showing whatever
    # the flag says.
    start_ts = start_dt.timestamp() if start_dt and not race_is_postponed(race) else None
    window = bardisplay.video_window(start_ts, rows, now,
                                     mark=line[0] if line else None, mark_name="O")
    current = get_current_competitor_race()
    finishes = [r.get("finish_epoch") for r in rows if r.get("finish_epoch")]
    return jsonify({
        "ok": True,
        "now": now,
        "video": {**window, "caption": bardisplay.caption_for(window)},
        "race_id": int(race["id"]),
        "current_race_id": int(current["id"]) if current else None,
        # The display renders the course, the start time and the flag schedule once,
        # server-side, so it needs telling when any of them change underneath it.
        "render_signature": public_render_signature(race),
        "finished": bardisplay.race_is_over(rows),
        # Nobody touches the television, so this is how a replay reaches it: the
        # poll it was already making. It switches itself.
        "replay": barreplay.current_replay(),
        # So the display can stop the clock at the moment the last boat crossed
        # rather than counting up for ever after a race is over.
        "last_finish": max(finishes) if finishes else None,
    })


@app.route("/bar/replay/finished/<int:race_id>", methods=["POST"])
def bar_replay_finished(race_id: int):
    """The clubhouse display reporting that it has reached the last finish.

    Public, because the television is not signed in to anything -- it is a
    screen on a wall. The only thing it can do is stop a replay that is already
    running, and only the one it names, which is a smaller power than walking
    over and turning the television off.

    Without it the display would go back to the live view and immediately be
    sent into the replay again by the next poll.
    """
    current = barreplay.current_replay()
    if current.get("race_id") == int(race_id):
        barreplay.stop_replay()
    return jsonify({"ok": True})
