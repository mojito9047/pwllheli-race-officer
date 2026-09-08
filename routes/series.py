"""Series list, detail, results CSV and publish HTML routes.

Split out of app.py into the routes/ package. Registered on the shared Flask
``app`` by importing this module near the end of app.py; endpoint names and the
``/x`` + ``/admin/x`` decorators are unchanged. The app object and every helper
used are read off the running app module (see routes.app_module) rather than
``from app import ...`` so it works whether app.py was started with ``python
app.py`` (module ``__main__``) or imported.
"""
from core import resultspublish
from core.raceadmin import RaceValidationError, delete_series, series_delete_block
from core.series import export_attachment
from routes import app_module

_app = app_module()
app = _app.app
audit = _app.audit
CLASS_CONFIG_HELP = _app.CLASS_CONFIG_HELP
DEFAULT_DISCARD_PROFILE = _app.DEFAULT_DISCARD_PROFILE
DEFAULT_MIN_RACES_TO_CONSTITUTE = _app.DEFAULT_MIN_RACES_TO_CONSTITUTE
NUMERAL_FLAG_OPTIONS = _app.NUMERAL_FLAG_OPTIONS
Response = _app.Response
START_PLAN_HELP = _app.START_PLAN_HELP
build_series_publish_model = _app.build_series_publish_model
build_series_results = _app.build_series_results
class_config_from_grid_form = _app.class_config_from_grid_form
class_config_from_grid_rows = _app.class_config_from_grid_rows
class_config_text = _app.class_config_text
class_grid_from_config = _app.class_grid_from_config
compute_dual_results = _app.compute_dual_results
csv = _app.csv
datetime = _app.datetime
discard_profile_help_text = _app.discard_profile_help_text
dt_full_display = _app.dt_full_display
flash = _app.flash
get_db = _app.get_db
get_entries = _app.get_entries
get_series = _app.get_series
get_series_races = _app.get_series_races
get_boats_by_id = _app.get_boats_by_id
normalise_sailwave_rating_system = _app.normalise_sailwave_rating_system
row_get = _app.row_get
sailwave_rows_for_race = _app.sailwave_rows_for_race
write_sailwave_csv = _app.write_sailwave_csv
init_db = _app.init_db
io = _app.io
json = _app.json
normalise_discard_profile_text = _app.normalise_discard_profile_text
parse_class_config_text = _app.parse_class_config_text
parse_discard_profile = _app.parse_discard_profile
parse_min_races_to_constitute = _app.parse_min_races_to_constitute
parse_start_plan_text = _app.parse_start_plan_text
race_first_start_time = _app.race_first_start_time
redirect = _app.redirect
render_template = _app.render_template
request = _app.request
series_class_config = _app.series_class_config
series_discard_profile = _app.series_discard_profile
series_min_races_to_constitute = _app.series_min_races_to_constitute
series_start_plan = _app.series_start_plan
start_plan_from_grid_form = _app.start_plan_from_grid_form
start_plan_grid_from_plan = _app.start_plan_grid_from_plan
start_plan_text = _app.start_plan_text
url_for = _app.url_for
current_actor = _app.current_actor


def _series_form_values(form):
    """Parse the shared series form.

    Both routes post the same fields from the same partial, so they read it the
    same way here rather than each growing its own copy of the parsing -- which
    is how a band added in one place ends up ignored in the other.

    Returns the values plus a list of human warnings; the callers save either
    way, matching what the edit form has always done. A bad discard profile is
    worth a warning, not a refusal to create the series.
    """
    name = form.get("name", "").strip()
    description = form.get("description", "").strip()
    if form.get("class_config_mode") == "grid":
        class_cfg = class_config_from_grid_form(form)
    else:
        class_cfg = parse_class_config_text(form.get("class_config_text", ""))
    if form.get("start_plan_mode") == "grid":
        start_plan = start_plan_from_grid_form(form, {"classes": class_cfg.get("classes", [])})
    else:
        start_plan = parse_start_plan_text(form.get("start_plan_text", ""))
    discard_text = normalise_discard_profile_text(form.get("discard_profile", DEFAULT_DISCARD_PROFILE))
    _values, discard_errors = parse_discard_profile(form.get("discard_profile", DEFAULT_DISCARD_PROFILE))
    min_races, min_races_errors = parse_min_races_to_constitute(
        form.get("min_races_to_constitute", DEFAULT_MIN_RACES_TO_CONSTITUTE))
    messages = []
    if discard_errors:
        messages.append("Discard profile: " + "; ".join(discard_errors))
    if min_races_errors:
        messages.append("Minimum races: " + "; ".join(min_races_errors))
    if class_cfg.get("errors"):
        messages.append("Class config: " + "; ".join(class_cfg["errors"]))
    if start_plan.get("errors"):
        messages.append("Start plan: " + "; ".join(start_plan["errors"]))
    return name, description, class_cfg, start_plan, discard_text, min_races, messages


def _blank_series_form_context():
    """The same context the edit form gets, for a series that does not exist yet."""
    class_cfg = {"classes": []}
    class_grid = class_grid_from_config(class_cfg)
    # Nothing ticked. class_grid_from_config hands back six rows already enabled
    # and named IRC0..YTC2, which is right for the editor -- a series with no
    # bands yet gets a set to fill in -- but wrong at creation: it would have
    # meant every series made with just a name silently arrived with six
    # placeholder bands and no rating limits, where the old create route stored
    # none at all. The names stay as a starting point for anyone who does tick
    # one; the parser ignores an untitled row that is not ticked.
    for rating_type in class_grid:
        for row in class_grid[rating_type]:
            row["enabled"] = False
    editor_cfg = class_config_from_grid_rows(class_grid)
    discard_text = DEFAULT_DISCARD_PROFILE
    return dict(
        series={"name": "", "description": ""},
        class_grid=class_grid,
        start_plan_grid=start_plan_grid_from_plan({"starts": []}, editor_cfg),
        discard_profile=discard_text,
        discard_profile_help=discard_profile_help_text(discard_text),
        min_races_to_constitute=DEFAULT_MIN_RACES_TO_CONSTITUTE,
        numeral_flag_options=NUMERAL_FLAG_OPTIONS,
    )


@app.route("/series/new", methods=["GET", "POST"])
@app.route("/admin/series/new", methods=["GET", "POST"])
def series_new():
    """The add-a-series form, on its own page.

    It used to sit above the list as a permanently-open form, so the first thing
    the page showed was a blank box for something you do a few times a season,
    and the series you came to open were below it. The Boats page already
    solved this with a button to a separate page; this matches it.
    """
    init_db()
    if request.method == "GET":
        return render_template("series_new.html", **_blank_series_form_context())
    (name, description, class_cfg, start_plan, discard_text,
     min_races, messages) = _series_form_values(request.form)
    if not name:
        flash("Series name is required.", "error")
        return redirect(url_for("series_new"))
    now = datetime.now().isoformat(timespec="seconds")
    with get_db() as db:
        cur = db.execute(
            "INSERT INTO race_series (name, description, class_config_json, start_plan_json,"
            " discard_profile, min_races_to_constitute, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (name, description,
             json.dumps({"classes": class_cfg.get("classes", [])}),
             json.dumps({"starts": start_plan.get("starts", [])}),
             discard_text, min_races, now, now),
        )
        series_id = cur.lastrowid
        db.commit()
    audit("series created",
          f"#{series_id} '{name}' · discards={discard_text} "
          f"· min races={min_races} · classes={len(class_cfg.get('classes', []))}")
    if messages:
        flash("Series created with warnings. " + " ".join(messages), "error")
    else:
        flash(f"Series created: {name}", "success")
    return redirect(url_for("series_detail", series_id=series_id))


@app.route("/series")
@app.route("/admin/series")
def series_page():
    """Render the list of series."""
    init_db()
    with get_db() as db:
        series_list = db.execute(
            """
            SELECT s.*, COUNT(r.id) AS race_count
            FROM race_series s
            LEFT JOIN races r ON r.series_id = s.id
            GROUP BY s.id
            ORDER BY s.updated_at DESC, s.id DESC
            """
        ).fetchall()
        # Asked per row rather than inferred from race_count in the template:
        # the rule about what holds a series open belongs in one place, and the
        # route that acts on it asks the same question again.
        delete_blocks = {int(s["id"]): series_delete_block(db, int(s["id"]))
                         for s in series_list}
    return render_template("series.html", series_list=series_list,
                           series_delete_blocks=delete_blocks)


@app.route("/series/<int:series_id>", methods=["GET", "POST"])
@app.route("/admin/series/<int:series_id>", methods=["GET", "POST"])
def series_detail(series_id: int):
    """Render/edit one series and its races."""
    series = get_series(series_id)
    if not series:
        flash("Series not found.", "error")
        return redirect(url_for("series_page"))
    if request.method == "POST":
        (name, description, class_cfg, start_plan, discard_profile_text,
         min_races_to_constitute, messages) = _series_form_values(request.form)
        # An edit with the name box emptied keeps the series' existing name; the
        # create route rejects it instead, because there is nothing to fall back on.
        name = name or series["name"]
        with get_db() as db:
            db.execute(
                "UPDATE race_series SET name = ?, description = ?, class_config_json = ?, start_plan_json = ?, discard_profile = ?, min_races_to_constitute = ?, updated_at = ? WHERE id = ?",
                (name, description, json.dumps({"classes": class_cfg.get("classes", [])}), json.dumps({"starts": start_plan.get("starts", [])}), discard_profile_text, min_races_to_constitute, datetime.now().isoformat(timespec="seconds"), series_id),
            )
            db.commit()
        # The discard profile and the class bands re-score every race in the
        # series at once, so this is a bigger change than it looks.
        audit("series updated",
              f"#{series_id} '{name}' · discards={discard_profile_text} "
              f"· min races={min_races_to_constitute} · classes={len(class_cfg.get('classes', []))}")
        if messages:
            flash("Series saved with warnings. " + " ".join(messages), "error")
        else:
            flash("Series updated.", "success")
        return redirect(url_for("series_detail", series_id=series_id))
    races = get_series_races(series_id)
    results = build_series_results(series_id)
    with get_db() as db:
        delete_block = series_delete_block(db, series_id)
    published = resultspublish.published_for_series(series_id)
    class_cfg = series_class_config(series)
    start_cfg = series_start_plan(series)
    class_grid = class_grid_from_config(class_cfg)
    editor_class_cfg = class_cfg if class_cfg.get("classes") else class_config_from_grid_rows(class_grid)
    discard_profile_text = series_discard_profile(series)
    min_races_to_constitute = series_min_races_to_constitute(series)
    return render_template(
        "series_detail.html", series=series, races=races, results=results,
        class_config_text=class_config_text(class_cfg), start_plan_text=start_plan_text(start_cfg),
        class_config=class_cfg, start_plan=start_cfg,
        class_grid=class_grid, start_plan_grid=start_plan_grid_from_plan(start_cfg, editor_class_cfg),
        discard_profile=discard_profile_text, discard_profile_help=discard_profile_help_text(discard_profile_text),
        min_races_to_constitute=min_races_to_constitute,
        numeral_flag_options=NUMERAL_FLAG_OPTIONS,
        class_config_help=CLASS_CONFIG_HELP, start_plan_help=START_PLAN_HELP,
        delete_block=delete_block,
        published=published,
        publish_ready=resultspublish.publish_ready(),
    )


@app.route("/series/<int:series_id>/publish", methods=["POST"])
@app.route("/admin/series/<int:series_id>/publish", methods=["POST"])
def series_publish(series_id: int):
    """Put this series' results on the club's public bucket.

    The same document *Preview HTML* shows, rendered now rather than at some
    point in the past, so what goes out is the standings as they stand at the
    end of the day's racing.
    """
    series = get_series(series_id)
    if not series:
        flash("Series not found.", "error")
        return redirect(url_for("series_page"))
    back = url_for("series_detail", series_id=series_id)
    try:
        html = render_template("series_publish.html",
                               **build_series_publish_model(series_id))
        record = resultspublish.publish_series_results(
            series_id, series["name"], html, actor=current_actor())
    except Exception as exc:
        # A failed publish must not look like a successful one. The bucket is
        # over a 4G link from the hut and the credentials are somebody else's
        # to fix, so the message says what went wrong rather than "try again".
        flash(f"Could not publish the results: {exc}", "error")
        return redirect(back)
    audit("series results published",
          f"#{series_id} '{series['name']}' -> {record['object_key']}")
    flash(f"Published: {record['stable_url']}", "success")
    return redirect(back + "#published")


@app.route("/series/<int:series_id>/delete", methods=["POST"])
@app.route("/admin/series/<int:series_id>/delete", methods=["POST"])
def series_delete(series_id: int):
    """Delete a series, if nothing is in it.

    Not admin-gated, unlike deleting a mark. A mark is club property that other
    people's saved courses point at; an empty series holds no racing and no
    results, and the person most likely to want one gone is the race officer who
    has just created it by mistake. What stops a season being deleted is the
    guard, not the role.
    """
    init_db()
    if not get_series(series_id):
        flash("Series not found.", "error")
        return redirect(url_for("series_page"))
    try:
        with get_db() as db:
            name = delete_series(db, series_id, actor=current_actor())
    except RaceValidationError as exc:
        # Back to the series itself: the races holding it open are listed there,
        # which is where somebody has to go next anyway.
        flash(f"Cannot delete this series: {exc.message}", "error")
        return redirect(url_for("series_detail", series_id=series_id))
    audit("series deleted", f"#{series_id} '{name}'")
    flash(f"Series deleted: {name}", "success")
    return redirect(url_for("series_page"))


@app.route("/series/<int:series_id>/results.csv")
@app.route("/admin/series/<int:series_id>/results.csv")
def series_results_csv(series_id: int):
    """Export a whole series as one Sailwave import CSV.

    ``?rating=irc`` (the default) or ``?rating=ytc``. One row per competitor per
    race, races numbered in series order, which is the shape Sailwave's importer
    reads: it creates the races as it goes and scores them itself. The series
    standings are not exported as such — Sailwave works out its own totals and
    discards from the race results, which is the point of importing them.
    """
    series = get_series(series_id)
    if not series:
        return Response("Series not found", status=404)
    rating_system = normalise_sailwave_rating_system(request.args.get("rating"))
    rows = []
    for race_no, race in enumerate(get_series_races(series_id), start=1):
        entries = get_entries(int(race["id"]))
        boats_by_id = get_boats_by_id([row_get(e, "boat_id") for e in entries])
        rows.extend(sailwave_rows_for_race(
            race, compute_dual_results(race, entries), rating_system,
            race_no=race_no, boats_by_id=boats_by_id))
    # Named the same way as the HTML download beside it. The two sat in a
    # downloads folder as series_1_results_publish.html and
    # series_1_irc_sailwave.csv, which tells you neither which series nor which
    # run -- and these get downloaded more than once as a season is scored.
    return Response(write_sailwave_csv(rows), mimetype="text/csv", headers={
        "Content-Disposition": export_attachment(
            series["name"], f"{rating_system}-sailwave", "csv")})


@app.route("/series/<int:series_id>/publish.html")
@app.route("/admin/series/<int:series_id>/publish.html")
def series_publish_html(series_id: int):
    """Render/download a standalone Sailwave-style HTML results file for a series."""
    series = get_series(series_id)
    if not series:
        return Response("Series not found", status=404)
    model = build_series_publish_model(series_id)
    html = render_template("series_publish.html", **model)
    headers = {}
    if request.args.get("download") == "1":
        headers["Content-Disposition"] = export_attachment(
            series["name"], "results", "html")
    return Response(html, mimetype="text/html; charset=utf-8", headers=headers)
