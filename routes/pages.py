"""Top-level display pages (dashboard, races list, recommend, split, documentation, power history).

Split out of app.py into the routes/ package. Registered on the shared Flask
``app`` by importing this module near the end of app.py; endpoint names and the
``/x`` + ``/admin/x`` decorators are unchanged. The app object and every helper
used are read off the running app module (see routes.app_module) rather than
``from app import ...`` so it works whether app.py was started with ``python
app.py`` (module ``__main__``) or imported.
"""
from core import docsview
from core import replay3d
from core import track
from routes import app_module

_app = app_module()
app = _app.app
BASE_DIR = _app.BASE_DIR
DOCUMENTATION_FILES = _app.DOCUMENTATION_FILES
Response = _app.Response
appstate = _app.appstate
course_chart_config = _app.course_chart_config
track_config = _app.track_config
dashboard_current_race_status = _app.dashboard_current_race_status
flash = _app.flash
format_minutes = _app.format_minutes
format_target = _app.format_target
get_db = _app.get_db
get_race = _app.get_race
horn_connection_status = _app.horn_connection_status
init_db = _app.init_db
latest_weather_sample = _app.latest_weather_sample
offsite_dashboard_status = _app.offsite_dashboard_status
low_battery_trackers = _app.low_battery_trackers
hologram_sim_warnings = _app.hologram_sim_warnings
hologram_balance_warning = _app.hologram_balance_warning
power_recharge_warning = _app.power_recharge_warning
list_polar_files = _app.list_polar_files
load_polar = _app.load_polar
load_sail_chart = _app.load_sail_chart
parse_float = _app.parse_float
power_runtime_status = _app.power_runtime_status
ptz_runtime_status = _app.ptz_runtime_status
public_live_r2_status = _app.public_live_r2_status
race_saved_polar = _app.race_saved_polar
read_r2_test_result = _app.read_r2_test_result
recommend_courses_with_polar = _app.recommend_courses_with_polar
redirect = _app.redirect
render_template = _app.render_template
request = _app.request
request_prefer_form = _app.request_prefer_form
resolve_polar_path = _app.resolve_polar_path
resolve_sail_chart_path_for_polar = _app.resolve_sail_chart_path_for_polar
row_get = _app.row_get
races_in_order = _app.races_in_order
get_current_competitor_race = _app.get_current_competitor_race
send_file = _app.send_file
url_for = _app.url_for
video_config = _app.video_config
video_runtime_status = _app.video_runtime_status
weather_config = _app.weather_config
weather_runtime_status = _app.weather_runtime_status


@app.route("/")
def public_root():
    """Redirect the site root to the default public competitor landing page."""
    init_db()
    return redirect(url_for("competitor_current_race"))


@app.route("/admin")
def index():
    """Render the main dashboard."""
    init_db()
    race_status = dashboard_current_race_status()
    weather_status = weather_runtime_status()
    latest_wind = weather_status.get("sample") or weather_status.get("latest") or latest_weather_sample()
    return render_template(
        "index.html",
        start_finish=appstate.START_FINISH,
        current_race_status=race_status,
        weather=weather_config(),
        weather_status=weather_status,
        latest_wind=latest_wind,
        horn_status=horn_connection_status(),
        video_status=video_runtime_status(),
        power_status=power_runtime_status(),
        offsite_status=offsite_dashboard_status(),
        # Cached for a few seconds inside status_snapshot: it reads the bucket.
        replay3d_status=replay3d.status_snapshot(),
        # Only when tracking is on and only for trackers assigned to a boat:
        # a spare in the drawer being flat is not a race-day problem.
        low_batteries=low_battery_trackers() if track_config()['enabled'] else [],
        # Same rule as the batteries beside it: only trackers on a boat. A SIM
        # the network has stopped is the one cause of a silent tracker that
        # never comes back on its own, so it belongs next to them.
        sim_warnings=hologram_sim_warnings(),
        # Running the account dry stops every tracker at once, and unlike a flat
        # battery there is no sign of it coming.
        balance_warning=hologram_balance_warning(),
        # The hut runs on solar with the panels flat on the roof, which in
        # midwinter cannot replace a day's load even in clear weather. The bank
        # failing to reach full is the signal, and it arrives weeks earlier than
        # a low battery does.
        recharge_warning=power_recharge_warning(),
        # The dashboard map shows where things are *now*, so the current mark
        # positions rather than any race's.
        marks_data=appstate.MARKS,
        course_chart=course_chart_config(),
        gps_tracking_enabled=track_config()["enabled"],
    )


@app.route("/admin/split")
@app.route("/split")
def split_screen():
    """Render a large-screen race-officer split view.

    The left pane is the authenticated race-office app.  The right pane is the
    public competitor page so the race officer can manage the race and see the
    competitor-facing view on one monitor.
    """
    return render_template(
        "split.html",
        admin_url=url_for("index"),
        public_url=url_for("competitor_current_race"),
    )


@app.route("/recommend", methods=["GET", "POST"])
@app.route("/admin/recommend", methods=["GET", "POST"])
def recommend():
    """Render and process course recommendation/change-course workflow."""
    race_id = request.values.get("race_id", type=int)
    race = get_race(race_id) if race_id else None
    if race_id and not race:
        flash("Race not found.", "error")
        return redirect(url_for("index"))
    use_live_wind = (request.form.get("use_live_wind") if request.method == "POST" else request.args.get("use_live_wind")) in ("1", "true", "on", "yes")
    if race and request.method == "GET" and "use_live_wind" not in request.args:
        use_live_wind = True
    weather_status = weather_runtime_status()
    latest_wind = weather_status.get("sample") or weather_status.get("latest") or latest_weather_sample()
    twd = parse_float(request_prefer_form("twd"))
    tws = parse_float(request_prefer_form("tws", "12.0")) or 12.0
    if use_live_wind and latest_wind:
        if latest_wind.get("twd") is not None:
            twd = float(latest_wind["twd"])
        if latest_wind.get("tws") is not None:
            tws = float(latest_wind["tws"])
    target_minutes = parse_float(request_prefer_form("target_minutes", "60")) or 60.0
    polar_files = list_polar_files()
    selected_polar = request_prefer_form("polar_file") or race_saved_polar(race, polar_files)
    polar_path = resolve_polar_path(selected_polar)

    rows = []
    polar_rows = load_polar(polar_path)
    sail_chart_path = resolve_sail_chart_path_for_polar(polar_path)
    sail_chart = load_sail_chart(sail_chart_path)
    if twd is not None:
        rows = recommend_courses_with_polar(twd, tws, target_minutes, polar_path)[:18]
    return render_template(
        "recommend.html",
        rows=rows,
        twd=twd,
        tws=tws,
        target_minutes=target_minutes,
        polar_files=polar_files,
        selected_polar=polar_path.name,
        polar_file=polar_path.name,
        polar_available=bool(polar_rows),
        sail_chart_file=sail_chart_path.name,
        sail_chart_source="polar-specific" if sail_chart_path.parent == appstate.SAIL_CHARTS_DIR else "default",
        sail_chart_available=bool(sail_chart.get("rows")),
        use_live_wind=use_live_wind,
        weather=weather_config(),
        weather_status=weather_status,
        video=video_config(),
        video_status=video_runtime_status(),
        r2_test_result=read_r2_test_result(),
        public_live_r2_status=public_live_r2_status(),
        ptz_status=ptz_runtime_status(),
        latest_wind=latest_wind,
        race=race,
        marks_data=track.race_marks(race),
        course_chart=course_chart_config(),
        format_minutes=format_minutes,
        format_target=format_target,
    )


@app.route("/races")
@app.route("/admin/races")
def races_page():
    """List all races grouped by series, plus standalone races."""
    init_db()
    with get_db() as db:
        series_rows = db.execute(
            """
            SELECT s.*, COUNT(r.id) AS race_count
            FROM race_series s
            LEFT JOIN races r ON r.series_id = s.id
            GROUP BY s.id
            ORDER BY s.updated_at DESC, s.id DESC
            """
        ).fetchall()
        race_rows = db.execute(
            """
            SELECT r.*, s.name AS series_name
            FROM races r
            LEFT JOIN race_series s ON s.id = r.series_id
            ORDER BY r.id
            """
        ).fetchall()
    # The same order the series page and the competitor page use. All three used
    # to sort by COALESCE(start_time, created_at) -- this one descending, the
    # others ascending -- so the same six races appeared in three different
    # orders, and giving one of them a start time moved it in all three.
    race_rows = races_in_order(race_rows)

    grouped = {int(s["id"]): [] for s in series_rows}
    standalone_races = []
    for race in race_rows:
        sid = row_get(race, "series_id")
        if sid and int(sid) in grouped:
            grouped[int(sid)].append(race)
        else:
            standalone_races.append(race)

    series_groups = [{"series": series, "races": grouped.get(int(series["id"]), [])} for series in series_rows]
    # Which roll-up opens. It used to be "the standalone one, if there are any
    # standalone races", which is never what anybody wants: the club has
    # standalone races most of the year, so the page always opened the one group
    # the current race is least likely to be in, and every real season sat shut.
    # The current race is the app's own answer to that question -- the same race
    # the sidebar's Current race link goes to -- so the roll-up holding it opens.
    current = get_current_competitor_race()
    current_race_id = int(current["id"]) if current else None
    current_series_id = int(row_get(current, "series_id") or 0) or None if current else None
    return render_template("races.html", series_groups=series_groups,
                           standalone_races=standalone_races,
                           current_race_id=current_race_id,
                           current_series_id=current_series_id)


@app.route("/documentation")
@app.route("/admin/documentation")
def documentation_page():
    """List the bundled PDF user guides."""
    docs_dir = BASE_DIR / "docs"
    items = [
        {
            "slug": slug,
            "title": title,
            "description": description,
            "available": (docs_dir / filename).exists(),
        }
        for slug, (filename, title, description) in DOCUMENTATION_FILES.items()
    ]
    return render_template("documentation.html", items=items,
                           markdown_documents=docsview.markdown_documents())


@app.route("/documentation/reference/<slug>")
@app.route("/admin/documentation/reference/<slug>")
def documentation_markdown(slug):
    """Render one of the bundled docs/*.md reference documents.

    Behind login like the rest of the Documentation page: these describe the race
    office, and only the competitor guide is public. The slug is looked up in the
    globbed list rather than joined onto a path — see core/docsview.py.
    """
    document = docsview.read_document(slug)
    if not document:
        return Response("Document not found.", status=404)
    # Links between documents are rewritten to point at this viewer and at the PDF
    # route, because the documentation cross-references itself constantly and a
    # rendered page whose internal links all 404 is worse than no rendering.
    body = docsview.rewrite_internal_links(
        document["html"],
        md_url=url_for("documentation_markdown", slug="__SLUG__").replace("__SLUG__", "{slug}"),
        pdf_urls={filename: url_for("documentation_file", slug=pdf_slug)
                  for pdf_slug, (filename, _t, _d) in DOCUMENTATION_FILES.items()},
    )
    return render_template(
        "documentation_markdown.html",
        document=document,
        body=body,
        documents=docsview.markdown_documents(),
        rendering_available=docsview.rendering_available(),
    )


@app.route("/documentation/<slug>")
@app.route("/admin/documentation/<slug>")
def documentation_file(slug):
    """Serve one of the bundled PDF user guides."""
    entry = DOCUMENTATION_FILES.get(slug)
    if not entry:
        return Response("Document not found.", status=404)
    filename, _title, _description = entry
    path = BASE_DIR / "docs" / filename
    if not path.exists():
        return Response("This document has not been generated yet.", status=404)
    return send_file(path, mimetype="application/pdf", as_attachment=False, download_name=filename)


@app.route("/power/history")
@app.route("/admin/power/history")
def power_history_page():
    """Render the hut power history graph page."""
    return render_template("power_history.html", power_status=power_runtime_status())
