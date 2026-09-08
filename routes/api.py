"""JSON API routes (weather, power, courses, marks, hardware, leg analysis, race events).

Split out of app.py into the routes/ package. Registered on the shared Flask
``app`` by importing this module near the end of app.py; endpoint names and the
``/x`` + ``/admin/x`` decorators are unchanged. The app object and every helper
used are read off the running app module (see routes.app_module) rather than
``from app import ...`` so it works whether app.py was started with ``python
app.py`` (module ``__main__``) or imported.
"""
from routes import app_module

_app = app_module()
app = _app.app
APP_VERSION = _app.APP_VERSION
analyse_course_with_wind = _app.analyse_course_with_wind
appstate = _app.appstate
course_from_sequence = _app.course_from_sequence
current_race_course_payload = _app.current_race_course_payload
event_for_json = _app.event_for_json
fetch_weather_station_sample = _app.fetch_weather_station_sample
get_current_competitor_race = _app.get_current_competitor_race
get_events_after = _app.get_events_after
get_race = _app.get_race
hardware_config = _app.hardware_config
int_in_range = _app.int_in_range
json = _app.json
jsonify = _app.jsonify
latest_weather_sample = _app.latest_weather_sample
leg_analysis_json = _app.leg_analysis_json
parse_float = _app.parse_float
power_history = _app.power_history
power_runtime_status = _app.power_runtime_status
track_runtime_status = _app.track_runtime_status
ingest_forwarded_positions = _app.ingest_forwarded_positions
track_config = _app.track_config
tracker_report_status = _app.tracker_report_status
tracker_markers = _app.tracker_markers
unregistered_traccar_devices = _app.unregistered_traccar_devices
list_trackers = _app.list_trackers
latest_positions = _app.latest_positions
effective_tracker_for_entry = _app.effective_tracker_for_entry
race_leaderboard = _app.race_leaderboard
race_track_history = _app.race_track_history
get_entries = _app.get_entries
read_manual_horn_input = _app.read_manual_horn_input
request = _app.request
secrets = _app.secrets
resolve_polar_path = _app.resolve_polar_path
time = _app.time
validate_course_sequence_json = _app.validate_course_sequence_json
weather_config = _app.weather_config
weather_history = _app.weather_history

# "Out there now" for the dashboard map. An hour is the same window the Trackers
# page calls reporting, so the two pages cannot disagree about who is out.
DASHBOARD_TRACKER_MAX_AGE_S = 3600
weather_runtime_status = _app.weather_runtime_status


@app.route("/api/weather/current")
def api_weather_current():
    """Return the latest hut wind sample as JSON."""
    force = request.args.get("force") in ("1", "true", "yes")
    status = fetch_weather_station_sample(force=True) if force else weather_runtime_status()
    latest = status.get("sample") or status.get("latest") or latest_weather_sample()
    return jsonify({
        "ok": bool(status.get("ok")),
        "enabled": status.get("enabled"),
        "message": status.get("message"),
        "current": latest,
        "config": {"source": weather_config().get("weather_source"), "enabled": weather_config().get("weather_enabled"), "poll_seconds": weather_config().get("weather_poll_seconds"), "manual_twd": weather_config().get("weather_manual_twd"), "manual_tws": weather_config().get("weather_manual_tws")},
    })


@app.route("/api/weather/history")
def api_weather_history():
    """Return recent hut wind history for browser charts."""
    minutes = request.args.get("minutes", 10, type=int)
    status = weather_runtime_status()
    latest = status.get("sample") or status.get("latest") or latest_weather_sample()
    return jsonify({
        "ok": True,
        "enabled": weather_config().get("weather_enabled"),
        "message": status.get("message"),
        "server_now": time.time(),
        "minutes": int_in_range(minutes, 10, 1, 360),
        "current": latest,
        "samples": weather_history(minutes),
    })


@app.route("/api/power/status")
@app.route("/admin/api/power/status")
def api_power_status():
    """Return the current hut power (Victron VE.Direct) status as JSON."""
    status = power_runtime_status()
    status["server_now"] = time.time()
    response = jsonify(status)
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/api/power/history")
@app.route("/admin/api/power/history")
def api_power_history():
    """Return recent hut power samples for the history chart."""
    minutes = request.args.get("minutes", 360, type=int)
    minutes = int_in_range(minutes, 360, 1, 60 * 24 * 30)
    return jsonify({
        "ok": True,
        "server_now": time.time(),
        "minutes": minutes,
        "samples": power_history(minutes),
    })


@app.route("/api/track/status")
@app.route("/admin/api/track/status")
def api_track_status():
    """Return the current GPS-tracking poller status as JSON (RO-only)."""
    status = track_runtime_status()
    status["server_now"] = time.time()
    response = jsonify(status)
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/api/dashboard/trackers")
@app.route("/admin/api/dashboard/trackers")
def api_dashboard_trackers():
    """Trackers that have reported within the last hour, for the dashboard map.

    Deliberately not the Trackers page's endpoint: that one asks Traccar for the
    devices it has not adopted yet, which is a network call out of the hut, and
    the dashboard polls in the background on every race-office screen.
    """
    response = jsonify({
        "ok": True,
        "server_now": time.time(),
        "markers": tracker_markers(max_age_s=DASHBOARD_TRACKER_MAX_AGE_S),
    })
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/api/track/ingest", methods=["POST"])
def api_track_ingest():
    """Accept positions pushed by Traccar's forwarder (relay -> hut app).

    Polling costs up to a poll interval before a fix is seen, which delays the
    automatic horn and what competitors are shown. Traccar posts each fix here
    as it decodes it instead, and finish detection runs on receipt.

    Authenticated by a shared secret in the X-RO-Track-Token header (set the
    same value in Settings and in the relay's forward.header). With no secret
    configured the endpoint stays closed. Kept deliberately terse: it answers a
    machine, and Traccar retries any non-2xx reply.
    """
    cfg = track_config()
    secret = cfg.get("ingest_secret") or ""
    if not secret:
        return jsonify({"ok": False, "message": "Position ingest is not configured."}), 503
    supplied = request.headers.get("X-RO-Track-Token", "")
    if not supplied or not secrets.compare_digest(str(supplied), str(secret)):
        return jsonify({"ok": False, "message": "Bad or missing ingest token."}), 401
    payload = request.get_json(silent=True)
    if payload is None:
        return jsonify({"ok": False, "message": "Expected a JSON body."}), 400
    result = ingest_forwarded_positions(payload)
    response = jsonify({"ok": True, **result})
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/api/trackers/status")
@app.route("/admin/api/trackers/status")
def api_trackers_status():
    """Per-tracker last-reported RAG + the reporting count, for live-updating the
    Trackers page without a refresh (RO-only)."""
    report = tracker_report_status()
    trackers = list_trackers()
    reporting = sum(1 for t in trackers if (report.get(t["unique_id"]) or {}).get("rag") in ("green", "amber"))
    st = track_runtime_status()
    response = jsonify({
        "ok": True,
        "server_now": time.time(),
        "enabled": st.get("enabled"),
        "sim": st.get("sim"),
        # Everything the Trackers page repaints from. The battery travels with the age
        # because it came off the same fix, and because sending only some of these once
        # left the page reading `undefined` and blanking a column that had a value in it.
        "report": {uid: {"rag": r["rag"], "text": r["text"],
                         "battery": r["battery"], "battery_rag": r["battery_rag"],
                         "battery_text": r["battery_text"], "battery_stale": r["battery_stale"],
                         "battery_title": r["battery_title"]}
                   for uid, r in report.items()},
        "reporting": reporting,
        "total": len(trackers),
        # How many devices Traccar has seen that are not adopted yet, so the page
        # can say "a new tracker appeared" without a manual refresh.
        "available": len(unregistered_traccar_devices()[0]),
        "markers": tracker_markers(),
    })
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/api/race/<int:race_id>/positions")
@app.route("/admin/api/race/<int:race_id>/positions")
def api_race_positions(race_id):
    """Return live positions + the ordered course-progress leaderboard (RO-only).

    Both the map and the leaderboard are driven from the same rows, so loaner vs
    boat-linked track resolution stays consistent.
    """
    race = get_race(race_id)
    if not race:
        return jsonify({"ok": False, "error": "race not found", "boats": [], "leaderboard": []}), 404
    at_ts = request.args.get("at", type=float)      # replay: the fleet as it stood then
    board = race_leaderboard(race_id, at_ts=at_ts)
    boats = [b for b in board if b.get("lat") is not None and b.get("lon") is not None]
    response = jsonify({"ok": True, "server_now": time.time(), "at": at_ts,
                        "boats": boats, "leaderboard": board})
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/api/race/<int:race_id>/track")
@app.route("/admin/api/race/<int:race_id>/track")
def api_race_track(race_id):
    """Return every tracked boat's recorded fixes for a race (RO-only).

    The whole race in one response: the replay viewer scrubs locally rather than
    asking the server for each frame. Cacheable — a finished race's track never
    changes — but left uncached here because a race in progress is still growing.
    """
    race = get_race(race_id)
    if not race:
        return jsonify({"ok": False, "error": "race not found", "boats": []}), 404
    since = request.args.get("since", type=float)   # only what is new
    history = race_track_history(race_id, since_ts=since)
    response = jsonify({"ok": True, "server_now": time.time(), **history})
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/api/course/<int:course_no>/leg_analysis")
def api_course_leg_analysis(course_no: int):
    """Return live leg analysis for a fixed course."""
    course = appstate.COURSE_BY_NO.get(int(course_no))
    if not course:
        return jsonify({"ok": False, "message": "Unknown course number."}), 404
    use_live = request.args.get("use_live") in ("1", "true", "yes", "on")
    latest_wind = None
    twd = request.args.get("twd", type=float)
    tws = request.args.get("tws", type=float)
    if use_live:
        status = weather_runtime_status()
        latest_wind = status.get("sample") or status.get("latest") or latest_weather_sample()
        if latest_wind:
            if latest_wind.get("twd") is not None:
                twd = float(latest_wind["twd"])
            if latest_wind.get("tws") is not None:
                tws = float(latest_wind["tws"])
    # When no wind is available, return geometry with the wind-derived cells blank.
    polar_path = resolve_polar_path(request.args.get("polar_file"))
    analysis = analyse_course_with_wind(course, twd, tws, polar_path)
    payload = {
        "ok": True,
        "course_no": course_no,
        "wind": {"twd": twd, "tws": tws, "latest": latest_wind},
        **leg_analysis_json(analysis),
    }
    return jsonify(payload)


@app.route("/api/custom_leg_analysis", methods=["POST"])
def api_custom_leg_analysis():
    """Return live leg analysis for a made-up course sequence."""
    payload = request.get_json(silent=True) or {}
    try:
        sequence = validate_course_sequence_json(json.dumps(payload.get("marks", [])))
    except ValueError as exc:
        return jsonify({"ok": False, "message": str(exc)}), 400
    course = course_from_sequence(sequence, course_no="Made up course", wind_label="Made up course", source="manual")
    use_live = bool(payload.get("use_live", True))
    latest_wind = None
    twd = parse_float(payload.get("twd"))
    tws = parse_float(payload.get("tws"))
    if use_live:
        status = weather_runtime_status()
        latest_wind = status.get("sample") or status.get("latest") or latest_weather_sample()
        if latest_wind:
            if latest_wind.get("twd") is not None:
                twd = float(latest_wind["twd"])
            if latest_wind.get("tws") is not None:
                tws = float(latest_wind["tws"])
    polar_path = resolve_polar_path(payload.get("polar_file"))
    analysis = analyse_course_with_wind(course, twd, tws, polar_path)
    return jsonify({
        "ok": True,
        "course_no": "Made up course",
        "sequence_text": course.get("sequence_text"),
        "length_nm": course.get("length_nm"),
        "wind": {"twd": twd, "tws": tws, "latest": latest_wind},
        **leg_analysis_json(analysis),
    })


@app.route("/api/race/<int:race_id>/events")
@app.route("/admin/api/race/<int:race_id>/events")
def api_race_events(race_id: int):
    """Return live race-log events newer than the event id already shown."""
    race = get_race(race_id)
    if not race:
        return jsonify({"ok": False, "error": "Race not found"}), 404
    after_id = request.args.get("after_id", 0, type=int) or 0
    limit = request.args.get("limit", 30, type=int) or 30
    rows = get_events_after(race_id, after_id=after_id, limit=limit)
    return jsonify({"ok": True, "events": [event_for_json(row) for row in rows]})


@app.route("/api/hardware/status")
@app.route("/admin/api/hardware/status")
def api_hardware_status():
    """Return horn output configuration status."""
    cfg = hardware_config()
    return {"configured": bool(cfg["serial_port"]), "config": cfg}


@app.route("/api/hardware/input_status")
@app.route("/admin/api/hardware/input_status")
def api_hardware_input_status():
    """Return the current manual horn input state as JSON only.

    Settings/start-console JavaScript polls this endpoint frequently.  Keep the
    response explicit JSON so a login/error page or unexpected exception is not
    exposed to the browser as a JSON parsing SyntaxError.
    """
    try:
        race_id = request.args.get("race_id", type=int)
        log_edges = request.args.get("log", "0").lower() in ("1", "true", "yes", "on")
        payload = read_manual_horn_input(race_id=race_id, log_edges=log_edges)
    except Exception as exc:  # pragma: no cover - defensive JSON API boundary
        payload = {"ok": False, "enabled": False, "configured": False, "message": f"Manual horn input status error: {exc}"}
    response = jsonify(payload)
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/api/current_race_course")
def api_current_race_course():
    """Return the current race course and mark geometry for external displays.

    This is public and read-only. It exposes the same race/course/mark data that
    competitors can already see, but in a single JSON payload that the Mojito
    range-and-bearing app can import directly.
    """
    race = get_current_competitor_race()
    if not race:
        return jsonify({
            "ok": False,
            "app": "Pwllheli Race Officer",
            "version": APP_VERSION,
            "message": "No current race is available.",
        }), 404
    return jsonify(current_race_course_payload(race))


@app.route("/api/courses")
def api_courses():
    """Return fixed courses as JSON."""
    return appstate.COURSES_DATA


@app.route("/api/marks")
def api_marks():
    """Return racing mark definitions as JSON."""
    return appstate.MARKS_DATA
