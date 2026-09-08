"""Start console routes (console, manual horn, log event, shorten course).

Split out of app.py into the routes/ package. Registered on the shared Flask
``app`` by importing this module near the end of app.py; endpoint names and the
``/x`` + ``/admin/x`` decorators are unchanged. The app object and helpers are
read off the running app module (routes.app_module) so it works whether app.py
was started with ``python app.py`` (module ``__main__``) or imported.
"""
from core.raceadmin import (
    RaceValidationError,
    clear_shortening,
    postpone_race,
    resume_race,
    shorten_course_at,
)
from core import barreplay
from core.races import postponement_flag
from routes import app_module

_app = app_module()
app = _app.app
Response = _app.Response
audit = _app.audit
current_actor = _app.current_actor
course_announcement_text = _app.course_announcement_text
course_for_race = _app.course_for_race
course_shorten_options = _app.course_shorten_options
datetime = _app.datetime
dt_full_display = _app.dt_full_display
fire_horn = _app.fire_horn
flash = _app.flash
get_db = _app.get_db
get_events = _app.get_events
get_race = _app.get_race
hardware_config = _app.hardware_config
jsonify = _app.jsonify
log_event = _app.log_event
race_first_start_time = _app.race_first_start_time
race_start_schedule = _app.race_start_schedule
redirect = _app.redirect
render_template = _app.render_template
request = _app.request
row_get = _app.row_get
schedule_video_clip = _app.schedule_video_clip
start_signal_plan_rows = _app.start_signal_plan_rows
threading = _app.threading
url_for = _app.url_for


# Which tab the race sheet comes back to. It remembers the tab in the URL
# fragment, and a plain redirect has none -- so every postpone, resume or
# shorten threw the race officer back to "Course & start", mid-sequence,
# looking at the wrong page.
def back_to(race_id: int, tab: str) -> str:
    return url_for("race_detail", race_id=race_id) + "#" + tab


@app.route("/race/<int:race_id>/start_console")
@app.route("/admin/race/<int:race_id>/start_console")
def start_console(race_id: int):
    """Render the standalone start-console page."""
    race = get_race(race_id)
    if not race:
        flash("Race not found.", "error")
        return redirect(url_for("index"))
    course = course_for_race(race)
    return render_template("start_console.html", race=race, course=course, config=hardware_config(), events=get_events(race_id, limit=None), course_announcement=course_announcement_text(race, course), first_warning_time=str(row_get(race, "start_time", "") or ""), first_start_time=race_first_start_time(race), start_schedule=race_start_schedule(race), signal_plan_rows=start_signal_plan_rows(race),
                           postponed_flag=postponement_flag(race),
                           postponement_ends_at=str(row_get(race, "postponement_ends_at", "") or ""))


@app.route("/race/<int:race_id>/horn", methods=["POST"])
@app.route("/admin/race/<int:race_id>/horn", methods=["POST"])
def race_horn(race_id: int):
    """Fire a manual horn or automatic sequence horn for a race."""
    race = get_race(race_id)
    if not race:
        return Response("Race not found", status=404)
    wants_json = request.is_json or "application/json" in request.headers.get("Accept", "")
    payload = request.get_json(silent=True) or {}
    # Race horn blasts always use the Settings > Default blast duration ms value.
    # Client-supplied durations are deliberately ignored so all signals are consistent.
    label = (payload.get("label") or request.form.get("label") or "Horn").strip()
    source = (payload.get("source") or request.form.get("source") or "manual").strip()
    result = fire_horn(hardware_config()["horn_duration_ms"])
    event_time = datetime.now().isoformat(timespec="seconds")
    event_id = log_event(race_id, "horn", label, source, result)
    if source.strip().lower() == "manual" and label.strip().lower().startswith("manual horn"):
        schedule_video_clip(race_id, "manual_horn", event_time, event_id=event_id, label=label)
    if wants_json:
        return {"result": result, "label": label, "event_id": event_id, "event_time": event_time, "event_time_display": dt_full_display(event_time)}
    flash(f"{label}: {result['message']}", "success" if result.get("ok") else "error")
    return redirect(url_for("race_detail", race_id=race_id))


@app.route("/race/<int:race_id>/event", methods=["POST"])
@app.route("/admin/race/<int:race_id>/event", methods=["POST"])
def race_log_event(race_id: int):
    """Log recall, postpone, abandon or other race-control events."""
    race = get_race(race_id)
    if not race:
        return Response("Race not found", status=404)
    payload = request.get_json(silent=True) or {}
    label = (payload.get("label") or request.form.get("label") or "Event").strip()
    event_type = (payload.get("event_type") or request.form.get("event_type") or "note").strip()
    source = (payload.get("source") or request.form.get("source") or "manual").strip()
    event_time = datetime.now().isoformat(timespec="seconds")
    event_id = log_event(race_id, event_type, label, source, payload if payload else {"form": dict(request.form)})
    if request.is_json or "application/json" in request.headers.get("Accept", ""):
        return {"ok": True, "label": label, "event_id": event_id, "event_time": event_time, "event_time_display": dt_full_display(event_time)}
    flash(f"Logged: {label}", "success")
    return redirect(url_for("race_detail", race_id=race_id))


@app.route("/race/<int:race_id>/shorten", methods=["POST"])
@app.route("/admin/race/<int:race_id>/shorten", methods=["POST"])
def race_shorten(race_id: int):
    """Call a shortened course at a chosen mark: record it and fire the signal."""
    race = get_race(race_id)
    wants_json = request.is_json or "application/json" in request.headers.get("Accept", "")
    if not race:
        return (jsonify({"ok": False, "error": "Race not found"}), 404) if wants_json else Response("Race not found", status=404)
    payload = request.get_json(silent=True) or {}
    index = payload.get("index", request.form.get("index"))
    try:
        with get_db() as db:
            call = shorten_course_at(db, race, index, actor=current_actor())
    except RaceValidationError as exc:
        if wants_json:
            return jsonify({"ok": False, "error": exc.message}), 400
        flash(exc.message, "error")
        return redirect(url_for("race_detail", race_id=race_id))
    if wants_json:
        return {"ok": True, "mark": call.mark, "announcement": call.announcement,
                "event_id": call.event_id, "event_time": call.at_time,
                "event_time_display": dt_full_display(call.at_time)}
    flash(f"Shortened course called at mark {call.mark}.", "success")
    return redirect(back_to(race_id, "tab-shorten"))


@app.route("/race/<int:race_id>/postpone", methods=["POST"])
@app.route("/admin/race/<int:race_id>/postpone", methods=["POST"])
def race_postpone(race_id: int):
    """Fly AP: postpone a race that has not started, and sound the two signals.

    This exists because the club's alternative was to edit the start time, which
    signals nothing to the fleet and drops the rest of a running sequence.
    """
    race = get_race(race_id)
    wants_json = request.is_json or "application/json" in request.headers.get("Accept", "")
    if not race:
        return (jsonify({"ok": False, "error": "Race not found"}), 404) if wants_json else Response("Race not found", status=404)
    payload = request.get_json(silent=True) or {}
    kind = payload.get("kind", request.form.get("kind")) or "AP"
    try:
        with get_db() as db:
            call = postpone_race(db, race, kind, actor=current_actor())
    except RaceValidationError as exc:
        if wants_json:
            return jsonify({"ok": False, "error": exc.message}), 400
        flash(exc.message, "error")
        return redirect(back_to(race_id, "tab-start"))
    if wants_json:
        return {"ok": True, "flag": call.flag, "kind": call.kind, "meaning": call.meaning,
                "event_id": call.event_id, "event_time": call.at_time,
                "event_time_display": dt_full_display(call.at_time)}
    flash(f"{call.flag} up, two sounds. {call.meaning}", "success")
    return redirect(back_to(race_id, "tab-start"))


@app.route("/race/<int:race_id>/postpone/end", methods=["POST"])
@app.route("/admin/race/<int:race_id>/postpone/end", methods=["POST"])
def race_postpone_end(race_id: int):
    """Lower AP: one sound, and the warning signal one minute later."""
    race = get_race(race_id)
    wants_json = request.is_json or "application/json" in request.headers.get("Accept", "")
    if not race:
        return (jsonify({"ok": False, "error": "Race not found"}), 404) if wants_json else Response("Race not found", status=404)
    payload = request.get_json(silent=True) or {}
    warning_time = payload.get("warning_time", request.form.get("warning_time")) or ""
    lower_at = payload.get("lower_at", request.form.get("lower_at")) or ""
    try:
        with get_db() as db:
            call = resume_race(db, race, actor=current_actor(), lower_at=lower_at or None,
                               warning_time=warning_time or None)
    except RaceValidationError as exc:
        if wants_json:
            return jsonify({"ok": False, "error": exc.message}), 400
        flash(exc.message, "error")
        return redirect(back_to(race_id, "tab-start"))
    if wants_json:
        return {"ok": True, "flag": call.flag, "warning_time": call.warning_time,
                "ends_at": call.ends_at,
                "announcement": call.announcement, "event_id": call.event_id}
    flash(call.announcement, "success")
    return redirect(back_to(race_id, "tab-start"))


@app.route("/race/<int:race_id>/shorten/clear", methods=["POST"])
@app.route("/admin/race/<int:race_id>/shorten/clear", methods=["POST"])
def race_shorten_clear(race_id: int):
    """Clear a shortened-course call (e.g. if it was made in error)."""
    race = get_race(race_id)
    wants_json = request.is_json or "application/json" in request.headers.get("Accept", "")
    if not race:
        return (jsonify({"ok": False, "error": "Race not found"}), 404) if wants_json else Response("Race not found", status=404)
    with get_db() as db:
        event_id = clear_shortening(db, race, actor=current_actor())
    if wants_json:
        return {"ok": True, "event_id": event_id}
    flash("Shortened course cleared.", "success")
    return redirect(back_to(race_id, "tab-shorten"))


@app.route("/race/<int:race_id>/replay", methods=["POST"])
@app.route("/admin/race/<int:race_id>/replay", methods=["POST"])
def race_replay_start(race_id: int):
    """Put the clubhouse television onto a replay of this race.

    Any signed-in user: it changes what a screen in the bar shows and touches no
    race data, so gating it behind an administrator would mostly mean the person
    standing in the bar cannot start one.
    """
    race = get_race(race_id)
    if not race:
        return Response("Race not found", status=404)
    if not barreplay.replay_is_possible(race):
        flash("That race has no start time or nobody finished, so there is nothing to replay.",
              "error")
        return redirect(back_to(race_id, "tab-results"))
    barreplay.start_replay(race_id, actor=current_actor())
    audit("bar replay started", f"#{race_id}")
    flash("The clubhouse display is replaying this race.", "success")
    return redirect(back_to(race_id, "tab-results"))


@app.route("/race/<int:race_id>/replay/stop", methods=["POST"])
@app.route("/admin/race/<int:race_id>/replay/stop", methods=["POST"])
def race_replay_stop(race_id: int):
    """Send the clubhouse television back to whatever is happening now."""
    barreplay.stop_replay()
    audit("bar replay stopped", f"#{race_id}")
    flash("The clubhouse display is back on the current race.", "success")
    return redirect(back_to(race_id, "tab-results"))
