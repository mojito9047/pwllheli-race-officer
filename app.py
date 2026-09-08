# Copyright © 2026 CapeNet Ltd. All Rights Reserved.
"""Pwllheli Race Officer.

This module is the Flask web layer: application setup, authentication/CSRF and
sessions, the settings form, background-task startup and the route handlers.
The domain logic lives in the ``core/`` package (config and DB access, course
geometry, polars, ratings, scoring, weather, boats, series, entry sync, backup,
and the horn/audio/video/start-sequence hardware subsystems); ``app.py`` imports
what it needs from there.  See ``docs/DEVELOPER_NOTES.md`` for the full module
map.

When started with ``python app.py`` it is served by Waitress rather than
Flask's development server.  The Windows deployment scripts in ``deploy/windows``
create a Scheduled Task that still uses this same startup path.

Operational decisions remain with the race officer; the app provides timing,
logging, calculation and evidence tools.
"""
from __future__ import annotations

import base64
import csv
import hashlib
import hmac
import io
import ipaddress
import json
import locale
import math
import os
import platform
import queue
import re
import secrets
import shutil
import socket
import sqlite3
import tempfile
import subprocess
import threading
import time
import urllib.error
import urllib.request
import zipfile
from urllib.parse import parse_qs, quote, urlparse
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from flask import Flask, Response, flash, jsonify, make_response, redirect, render_template, request, send_file, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

# Pure helpers extracted to core/ during the incremental module split. Imported
# back into this namespace so existing app.<name> references, the Jinja context
# processor and the test suite keep resolving these by their original names.
from core.timeutils import (
    angular_diff,
    bearing_deg,
    clean_sail_no,
    decimal_minutes_to_text,
    dt_display,
    dt_for_input,
    dt_for_minute_input,
    date_display,
    dt_full_display,
    first_present,
    haversine_nm,
    normalise_key,
    normalise_start_time_value,
    parse_dt,
    parse_float,
    seconds_display,
)
from core.polars import (
    format_minutes,
    format_target,
    interpolate_points,
    interpolate_rows_by_tws,
    leg_side,
    nearest_number,
    point_of_sail,
    polar_usable_points,
    row_best_downwind_vmg,
    row_max_twa,
    row_min_twa,
    row_speed_at_twa,
    sail_for,
    target_speed_info_for,
)
from core.ratings import (
    parse_band_expression,
    rating_band_display,
    rating_in_class_band,
    rating_type_from_class_name,
)
from core import marks as core_marks
from core import resultspublish
from core.courses import course_leg_analysis  # noqa: F401 — re-exported for routes/templates
from core.courses import (
    apply_course_shortening,
    course_shorten_options,
    compound_mark_components,
    course_announcement_text,
    number_words,
    spoken_mark,
    course_for_display,
    course_for_race,
    course_from_sequence,
    course_legs,
    course_length_nm,
    course_numeric_length,
    course_sequence_text,
    course_uses_compound_marks,
    course_wind_score,
    custom_course_from_race,
    expand_course_points,
    mark_display_code,
    mark_sort_key,
    recommend_courses,
    selectable_mark_names,
    validate_course_sequence_json,
    wind_in_range,
)
from core.polar_io import (
    ALLOWED_POLAR_EXTENSIONS,
    ALLOWED_SAIL_CHART_EXTENSIONS,
    POLAR_PATH,
    canonical_sail_chart_name_for_polar,
    default_polar_name,
    delete_sail_charts_for_polar,
    ensure_polars_dir,
    ensure_sailcharts_dir,
    installed_sail_chart_for_polar,
    list_polar_assets,
    list_polar_files,
    load_polar,
    load_sail_chart,
    race_saved_polar,
    resolve_sail_chart_path_for_polar,
    sail_chart_candidates_for_polar,
    save_uploaded_sail_chart_for_polar,
)
from core.boats import (
    boat_by_sail_no,
    boat_rating_for_rule,
    duplicate_sail_numbers,
    get_boat,
    search_boats,
    filter_irc_rows,
    filter_ytc_rows,
    google_sheet_to_csv_url,
    irc_row_to_prefill,
    normalise_ytc_row,
    read_csv_rows,
    rating_listings_are_warm,
    read_irc_listing,
    read_ytc_listing,
    require_http_url,
    warm_rating_listings,
    safe_rating_listing_url,
    upsert_boat_from_irc_row,
    upsert_boat_from_ytc_row,
    ytc_row_to_prefill,
)
from core.races import (
    postponement_flag,
    get_boats_by_id,
    entry_display_status,
    race_has_finished_for_sequence,
    get_entries,
    get_race,
    get_series,
    get_series_races,
    list_series,
    race_first_start_dt,
    race_first_start_time,
    race_first_warning_dt,
    race_status_label,
    races_in_order,
)
from core.classconfig import (
    start_signal_plan_rows,
    signal_panel_schedule,
    CLASS_CONFIG_HELP,
    DEFAULT_CLASS_ROWS,
    MAX_CLASSES_PER_RATING_TYPE,
    MAX_START_PLAN_ROWS,
    NUMERAL_FLAG_OPTIONS,
    RATING_CLASS_TYPES,
    START_PLAN_HELP,
    band_expr_from_limits,
    class_config_from_grid_form,
    class_config_from_grid_rows,
    class_config_text,
    class_flags_for_start,
    class_for_rating_value,
    entry_class_labels_map,
    class_grid_from_config,
    class_rule_by_name,
    class_rules_for_type,
    default_start_plan_from_classes,
    discard_profile_help_text,
    enabled_class_slots_from_config,
    flag_label_for_class,
    format_number_for_form,
    normalize_numeral_flag,
    numeral_from_flag_label,
    parse_class_config_text,
    parse_min_races_to_constitute,
    parse_start_plan_text,
    race_class_config,
    race_series_row,
    race_start_plan,
    race_start_schedule,
    series_class_config,
    series_discard_profile,
    series_discards_for_race_count,
    series_min_races_to_constitute,
    series_start_plan,
    start_plan_from_grid_form,
    start_plan_grid_from_plan,
    start_plan_text,
    start_for_class,
    start_for_class_from_schedule,
)
from core.scoring import (
    DEFAULT_DISCARD_PROFILE,
    DEFAULT_MIN_RACES_TO_CONSTITUTE,
    NON_EXCLUDABLE_STATUS_CODES,
    assign_low_point_race_ranks,
    choose_discard_indexes,
    corrected_seconds_for_result,
    is_non_excludable_score,
    normalise_discard_profile_text,
    ordinal_text,
    parse_discard_profile,
    rating_formula_for_type,
    rating_label_for_type,
    rounded_seconds_for_scoring,
    series_rank_key,
)
from core.series import (
    build_series_result_group,
    html_id_slug,
    publish_rating_text,
    published_score_cell,
    build_series_result_table,
    build_series_results,
    compute_dual_results,
    compute_rating_result_group,
    compute_rating_result_table,
    compute_results,
    entry_elapsed_seconds,
    race_ready_for_series_scoring,
    race_type_classes_share_one_start,
    rating_from_entry_for_result,
    result_start_time_for_class,
    result_start_time_for_class_from_schedule,
    select_result_table_from_group,
    series_competitor_key,
    series_competitor_label,
    series_race_result_group,
    series_races_type_classes_share_one_start,
    series_type_classes_share_one_start,
)
from core.entrysync import (
    add_active_boats_to_race,
    add_active_boats_to_series_races,
    add_boat_database_entry_to_race,
    add_boat_database_entry_to_series_races,
    entry_exists_for_boat,
    legacy_entry_rating_value,
    normalise_entry_status,
    series_entry_status_for_target,
    series_race_order_key,
    series_races_for_sync,
    source_race_for_series_sync,
    sync_race_entries_to_series,
    sync_series_entries_into_race,
)
from core.weather import (
    host_matches_pattern,
    summarise_wind_samples,
    normalise_weather_url,
    parse_weather_livedata,
    race_wind_retention_windows,
    safe_weather_station_url,
    sample_time_in_any_window,
    speed_to_knots,
    validate_weather_station_url,
    value_to_float_and_unit,
    weather_allowed_host_patterns,
    wind_item_id,
)
from core.weather_store import (
    fetch_weather_station_sample,
    weather_samples_between,
    insert_weather_sample,
    latest_weather_sample,
    manual_weather_sample,
    start_weather_background_poller,
    weather_history,
    weather_runtime_status,
)
from core.eventlog import (
    event_for_json,
    get_events,
    get_events_after,
    is_finish_assignable_event,
    log_event,
)
from core.horn import (
    DEFAULT_HARDWARE_CONFIG,
    HARDWARE_IO_LOCK,
    HARDWARE_RUNTIME_STATE,
    PROLOG_ACTIVE_INPUT_LINE,
    PROLOG_HORN_OUTPUT_LINE,
    PROLOG_IDLE_INPUT_LINE,
    PROLOG_SENSE_REFERENCE_LINE,
    fire_horn,
    get_hardware_setting_overrides,
    hardware_config,
    open_serial_for_horn_io,
    read_manual_horn_input,
    read_prolog_horn_feedback,
    save_hardware_config,
    set_serial_output_inactive,
)
from core.audio import (
    AUDIO_LOCK,
    AUDIO_QUEUE,
    AUDIO_RUNTIME_STATE,
    central_audio_status,
    purge_queued_start_sequence_audio,
    queue_central_audio,
    speak_text_locally,
    start_central_audio_worker,
)
from core.video import (
    PTZ_LOCK,
    PTZ_RUNTIME_STATE,
    branding_assets,
    branding_file_path,
    build_public_live_branding_overlay_filter,
    build_public_video_transcode_command,
    build_video_preview_command,
    build_video_recorder_command,
    check_public_r2_url,
    delete_branding_file_if_unused,
    ensure_start_video_scheduled,
    get_video_clips_for_race,
    hold_ptz_after_manual_test,
    list_usb_video_sources,
    matching_video_segments,
    normalise_ptz_auth_mode,
    normalise_video_copy_container,
    normalise_video_preview_size,
    normalise_video_public_live_provider,
    normalise_video_public_provider,
    normalise_video_public_quality,
    normalise_video_recording_mode,
    normalise_video_rtsp_timestamp_mode,
    ptz_capabilities_url,
    ptz_digest_put,
    ptz_goto_preset,
    ptz_preset_url,
    ptz_runtime_status,
    ptz_test_login,
    public_live_branding_cache_key,
    public_live_frame_path,
    public_live_r2_enabled,
    public_live_r2_status,
    public_live_r2_url,
    public_url_for_r2_key,
    public_video_clip_is_viewable,
    public_video_clip_link_text,
    queue_ptz_goto_preset,
    r2_s3_endpoint_host,
    race_delete_summary,
    read_branding_manifest,
    read_r2_test_result,
    repair_mojibake,
    reset_ptz_runtime_state,
    abandon_stuck_public_video_uploads,
    retry_public_video_uploads_once,
    safe_delete_race_video_file,
    safe_ptz_camera_base_url,
    safe_public_video_base_url,
    safe_r2_account_id,
    safe_r2_bucket_name,
    safe_r2_key_prefix,
    save_r2_test_result,
    save_uploaded_branding_image,
    schedule_video_clip,
    start_public_live_r2_uploader,
    start_video_background_recorder,
    start_video_watchdog,
    update_camera_preset_for_races,
    upload_bytes_to_r2,
    upload_file_to_r2,
    upload_public_live_frame_once,
    video_clip_link_text,
    video_clip_maps,
    video_config,
    video_log_health_warnings,
    video_public_r2_credentials_ready,
    video_public_r2_ready,
    video_runtime_status,
    write_branding_manifest,
)
from core.startsequence import (
    START_SEQUENCE_STATE,
    central_start_sequence_events,
    reset_start_sequence_state_for_race,
    start_start_sequence_scheduler,
)
from core.backup import (
    BACKUP_MANIFEST_NAME,
    BACKUP_SECTION_BY_ID,
    BACKUP_SECTION_DEFINITIONS,
    add_directory_to_backup,
    add_file_to_backup,
    backup_member_is_for_section,
    backup_section_prefixes,
    backup_section_restore_targets,
    backup_sections_for_template,
    backup_summary_template,
    backup_zip_has_section,
    clear_restore_target,
    clear_sqlite_sidecars,
    create_data_backup_zip,
    database_backup_section_ids,
    normalise_backup_section_ids,
    open_backup_zip,
    restore_member_destination,
    safe_backup_archive_name,
    safe_restore_member_name,
    sqlite_backup_to_file,
    zip_is_encrypted,
)
from core.sailwave import (
    SAILWAVE_COLUMNS,
    normalise_sailwave_rating_system,
    race_number_in_series,
    sailwave_rows_for_race,
    write_sailwave_csv,
)
from core.offsite import (
    list_offsite_backups,
    offsite_config,
    offsite_dashboard_status,
    read_offsite_status,
    run_offsite_backup_once,
    start_offsite_backup_now,
    start_offsite_backup_worker,
)
from core.settings import (
    bool_to_text,
    course_chart_config,
    float_in_range,
    get_app_setting_overrides,
    int_in_range,
    invalidate_app_settings_cache,
    listing_config,
    race_console_config,
    text_to_bool,
    weather_config,
)
from core.power import (
    init_power_db,
    power_config,
    power_history,
    power_runtime_status,
    start_power_monitor_worker,
)
from core.track import (
    TRACKER_PROTOCOL_FAMILIES,
    low_battery_trackers,
    adopt_tracker,
    command_refusal,
    delete_tracker_type,
    command_templates_for,
    list_tracker_types,
    send_tracker_command,
    tracker_command_results,
    tracker_models_by_unique_id,
    tracker_protocols,
    upsert_tracker_type,
    confirm_proposal,
    course_rounding_sequence,
    dismiss_proposal,
    ingest_forwarded_positions,
    effective_tracker_for_entry,
    init_track_db,
    latest_positions,
    list_trackers,
    pending_proposals_for_race,
    race_leaderboard,
    race_track_history,
    remove_tracker,
    set_next_mark_override,
    start_track_monitor_worker,
    track_config,
    track_runtime_status,
    tracker_for_boat,
    tracker_markers,
    tracker_report_status,
    unregistered_traccar_devices,
    upsert_tracker,
)
# The SIM behind each tracker. Separate from Traccar on purpose: Traccar can only
# say a tracker has gone quiet, not whether its SIM is still allowed to speak.
from core.hologram import (
    hologram_active,
    hologram_config,
    invalidate_cache as hologram_invalidate,
    paused_sims as hologram_paused_sims,
    sim_status as hologram_sim_status,
    sim_warnings as hologram_sim_warnings,
    account as hologram_account,
    balance_warning as hologram_balance_warning,
)

# ---------------------------------------------------------------------------
# Paths, source data and application constants
# ---------------------------------------------------------------------------
# Path, URL, version and start-line constants now live in core/appstate.py (the
# config module for the split). Imported back into this namespace so existing
# app.<name> references, the Jinja context processor and the test suite keep
# resolving them unchanged. Functions still read these names locally, so tests
# that monkeypatch e.g. app.DATA_DIR / app.DB_PATH keep working.
from core.appstate import (
    APP_VERSION,
    BASE_DIR,
    BRANDING_EXTENSIONS,
    BRIDGE_WINDOW_LAT,
    BRIDGE_WINDOW_LON,
    DEFAULT_CLUB_LOGO_PATH,
    DEFAULT_COURSE_CHART_OVERLAY_URL,
    DEFAULT_COURSE_CHART_TILE_URL,
    DEFAULT_WEATHER_STATION_URL,
    IRC_LISTING_URL,
    PUBLIC_LIVE_R2_STATUS_PATH,
    R2_TEST_RESULT_PATH,
    SESSION_APP_VERSION_KEY,
    SOURCE_URL,
    VIDEO_BUFFER_DIR,
    VIDEO_LIVE_DIR,
    VIDEO_LIVE_JPG_PATH,
    VIDEO_LOG_PATH,
    VIDEO_PUBLIC_LIVE_HASH_PATH,
    VIDEO_PUBLIC_LIVE_JPG_PATH,
    VIDEO_RUNTIME_DIR,
    YTC_LISTING_URL,
)
# The appstate module itself is imported so the handful of mutable/monkeypatched
# names (DB_PATH) are read through a single source shared with core.db.
from core import appstate
from core import db as _db
from core import horn as _horn
from core import activitylog
from core.activitylog import log_activity, settings_change_summary
from core import video
from core import offsite
from core import power
# The winter warning: a solar hut fails by the bank quietly ceasing to reach
# full, weeks before anything looks low.
from core.power import recharge_warning as power_recharge_warning
# The write side of a race sheet, callable without a form. core.raceadmin never
# imports app, so this direction is the only one there is.
from core import raceadmin
from core import track
from core import pursuit
from core.pursuit import (
    assign_pursuit_start_times,
    is_pursuit_race,
    pursuit_finish_dt,
    pursuit_rating_type,
    pursuit_start_rows,
    pursuit_start_signal_times,
    recompute_pursuit_start_times,
)
# SQLite access layer extracted to core/db.py. Re-imported so the ~100 get_db()
# call sites and ensure_column/table_columns usages resolve unchanged.
from core.db import ensure_column, get_db, init_db, migrate_legacy_data_layout, row_get, safe_json_loads, table_columns

# The reloadable course/mark data lives in core.appstate. app.py's own code reads
# it as appstate.<name>, but external accessors (chiefly the test suite, e.g.
# app.COURSES / app.MARKS) still expect these on this module. PEP 562 module
# __getattr__ delegates those reads to appstate so they always reflect the
# current (possibly reloaded) data without keeping a stale local copy.
_APPSTATE_DELEGATED = frozenset(
    {"COURSES_DATA", "COURSES", "COURSE_BY_NO", "MARKS_DATA", "MARKS", "START_FINISH"}
)


def __getattr__(name):
    if name in _APPSTATE_DELEGATED:
        return getattr(appstate, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

appstate.DATA_DIR.mkdir(parents=True, exist_ok=True)
appstate.RUNTIME_DIR.mkdir(parents=True, exist_ok=True)

# Runtime caches/locks.  v0.85 security hardening made the first-run admin
# password check deliberately expensive, but init_db() is called by many public
# and admin request paths.  These guards keep the migration/credential check to
# application startup/first request instead of repeating it on every refresh.
def load_secret_key() -> str:
    """Load the Flask session signing key from env or a persistent local file.

    The app must not fall back to a public, hard-coded value because Flask uses
    this key to sign session cookies.  For hut deployments that do not set
    RO_SECRET_KEY, create one once and keep it under runtime/.
    """
    env_key = os.environ.get("RO_SECRET_KEY", "").strip()
    if env_key:
        return env_key
    path = appstate.RUNTIME_DIR / "secret_key.txt"
    if path.exists():
        saved = path.read_text(encoding="utf-8").strip()
        if saved:
            return saved
    key = secrets.token_urlsafe(48)
    path.write_text(key + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return key


app = Flask(__name__)
app.secret_key = load_secret_key()

# --- Security hardening ------------------------------------------------------
# Session cookie: HttpOnly (Flask default, set explicitly), SameSite=Lax, and
# Secure by default. In production the browser talks HTTPS to Cloudflare, so the
# Secure flag is honoured even though the app itself speaks HTTP to the tunnel.
# Local http testing must set RO_COOKIE_SECURE=0 or the browser won't store the
# session cookie.
_cookie_secure_env = os.environ.get("RO_COOKIE_SECURE")
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=(
        _cookie_secure_env not in ("0", "false", "no", "")
        if _cookie_secure_env is not None else True
    ),
    # Cap request bodies to blunt memory/disk DoS via the public API and uploads.
    # A very large backup-restore ZIP (video clips) may need RO_MAX_UPLOAD_MB raised.
    MAX_CONTENT_LENGTH=int(os.environ.get("RO_MAX_UPLOAD_MB", "64")) * 1024 * 1024,
)
# HSTS is only meaningful (and only sent) on an HTTPS deployment.
_SEND_HSTS = bool(app.config["SESSION_COOKIE_SECURE"])


# Times every request and logs the slow ones to runtime/logs/slow.log. Installed
# here so it wraps everything; a no-op unless RO_SLOW_REQUEST_MS is above zero.
from core import slowlog          # noqa: E402
slowlog.install(app)

# What Waitress was actually started with. Settings can be changed at any time but
# only take effect on restart, and "I changed it and nothing happened" is the kind
# of thing that gets fiddled with twice more during a race. Empty when running
# under the Flask dev server, where none of it applies.
SERVER_RUNTIME: Dict[str, Any] = {}


def record_server_runtime(threads: int, connection_limit: int, channel_timeout: int) -> None:
    SERVER_RUNTIME.update({"threads": int(threads),
                           "connection_limit": int(connection_limit),
                           "channel_timeout": int(channel_timeout)})


def server_runtime_status() -> Dict[str, Any]:
    """Running vs saved web-server sizing, and whether a restart is pending."""
    saved = hardware_config()
    want = {"threads": int(saved.get("server_threads") or 8),
            "connection_limit": int(saved.get("server_connection_limit") or 100),
            "channel_timeout": int(saved.get("server_channel_timeout") or 120)}
    running = dict(SERVER_RUNTIME)
    return {"running": running, "saved": want,
            "restart_pending": bool(running) and running != want}


@app.after_request
def apply_security_headers(response: Response) -> Response:
    """Attach baseline security headers to every response."""
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    if _SEND_HSTS:
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
    return response

# Reloadable course/mark/start-finish data now lives in core/appstate.py (loaded
# once at import) so every reader shares one source. This reloader stays here in
# app.py because it must read the appstate.DATA_DIR the restore code wrote to (which tests
# monkeypatch on this module) and then reassign the appstate globals in place.
def reload_course_mark_data() -> None:
    """Reload JSON course, mark and start/finish files after a restore.

    These files are normally loaded once at startup for speed.  The backup
    restore page can replace them while the app is running, so refresh the
    in-process globals immediately rather than requiring a restart.
    """
    with (appstate.DATA_DIR / "courses.json").open("r", encoding="utf-8") as f:
        appstate.COURSES_DATA = json.load(f)
    appstate.COURSES = appstate.COURSES_DATA["courses"]
    appstate.COURSE_BY_NO = {int(c["course_no"]): c for c in appstate.COURSES}
    with (appstate.DATA_DIR / "marks.json").open("r", encoding="utf-8") as f:
        appstate.MARKS_DATA = json.load(f)
    appstate.MARKS = appstate.MARKS_DATA["marks"]
    with (appstate.DATA_DIR / "start_finish.json").open("r", encoding="utf-8") as f:
        appstate.START_FINISH = json.load(f)


def initial_admin_password() -> str:
    """Return the first-run admin password from env or a local generated file."""
    env_password = os.environ.get("RO_INITIAL_ADMIN_PASSWORD", "").strip()
    if env_password:
        return env_password
    path = appstate.RUNTIME_DIR / "initial_admin_password.txt"
    if path.exists():
        saved = path.read_text(encoding="utf-8").strip().splitlines()[0].strip()
        if saved:
            return saved
    password = secrets.token_urlsafe(18)
    path.write_text(password + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    print(f"Pwllheli Race Officer first-run admin password written to {path}", flush=True)
    return password


def replace_insecure_default_admin_password(db: sqlite3.Connection) -> None:
    """Replace any remaining admin/admin account unless explicitly allowed."""
    if os.environ.get("RO_ALLOW_DEFAULT_ADMIN", "").strip().lower() in ("1", "true", "yes", "on"):
        return
    row = db.execute("SELECT id, password_hash FROM users WHERE lower(username) = 'admin' AND status = 'ACTIVE' LIMIT 1").fetchone()
    if row and check_password_hash(row["password_hash"], "admin"):
        new_password = initial_admin_password()
        now = datetime.now().isoformat(timespec="seconds")
        db.execute("UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?", (generate_password_hash(new_password), now, row["id"]))
        print("Pwllheli Race Officer replaced insecure admin/admin credentials. Use runtime/initial_admin_password.txt or RO_INITIAL_ADMIN_PASSWORD.", flush=True)

def _init_db_uncached() -> None:
    """Create or gently upgrade the local SQLite schema.

    The app uses additive migrations via ensure_column() so existing club data
    can survive version upgrades without a separate migration tool.
    """
    with get_db() as db:
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS races (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                class_name TEXT,
                course_no INTEGER NOT NULL,
                start_time TEXT NOT NULL,
                rating_rule TEXT NOT NULL DEFAULT 'IRC_TCC',
                notes TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                race_id INTEGER NOT NULL,
                boat_name TEXT NOT NULL,
                sail_no TEXT,
                class_name TEXT,
                rating REAL,
                start_time_override TEXT,
                finish_time TEXT,
                finish_source TEXT,
                status TEXT NOT NULL DEFAULT 'RACING',
                notes TEXT,
                FOREIGN KEY (race_id) REFERENCES races(id) ON DELETE CASCADE
            )
            """
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS boats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                boat_name TEXT NOT NULL,
                sail_no TEXT,
                class_name TEXT,
                owner TEXT,
                design TEXT,
                club TEXT,
                irc_rating REAL,
                irc_non_spinnaker_tcc REAL,
                irc_cert_no TEXT,
                irc_issue_date TEXT,
                irc_cert_year TEXT,
                ytc_rating REAL,
                status TEXT NOT NULL DEFAULT 'ACTIVE',
                source TEXT,
                source_updated_at TEXT,
                notes TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        db.execute("CREATE INDEX IF NOT EXISTS idx_boats_name ON boats(boat_name)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_boats_sail ON boats(sail_no)")
        ensure_column(db, "entries", "boat_id", "INTEGER")
        ensure_column(db, "entries", "rating_source", "TEXT")
        ensure_column(db, "entries", "manual_irc_rating", "REAL")
        ensure_column(db, "entries", "manual_ytc_rating", "REAL")
        # Pursuit races rank boats by the race officer's on-the-water finishing
        # order rather than corrected time; this holds that place (1, 2, 3, ...).
        ensure_column(db, "entries", "pursuit_position", "INTEGER")
        # GPS tracking: a per-race loaner tracker assigned to this entry, which
        # overrides the boat's permanent tracker (trackers table) for this race.
        ensure_column(db, "entries", "tracker_unique_id", "TEXT")
        # The race officer's correction to which mark a boat is sailing to, for a
        # wide rounding the app did not see. Course progress is otherwise recomputed
        # from the boat's fixes on every poll, so there is nothing to nudge without
        # somewhere to keep the correction. Two halves, and both are needed: the
        # index into the rounding sequence, and the moment it was set — the override
        # applies to fixes from then on, never retroactively. See
        # core.track.boat_course_progress for why that matters.
        ensure_column(db, "entries", "next_mark_override_idx", "INTEGER")
        ensure_column(db, "entries", "next_mark_override_at", "REAL")
        ensure_column(db, "races", "finish_line_key", "TEXT")
        # GPS auto-finish arming, per race (opt-in). gps_finish_enabled turns on
        # crossing detection; gps_auto_confirm records finishes without waiting
        # for RO confirmation (for an unmanned finish).
        ensure_column(db, "races", "gps_finish_enabled", "INTEGER")
        ensure_column(db, "races", "gps_auto_confirm", "INTEGER")
        # Trackers: the device catalogue + a permanent tracker->boat assignment.
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS trackers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                unique_id TEXT NOT NULL UNIQUE,
                traccar_device_id TEXT,
                label TEXT,
                boat_id INTEGER,
                active INTEGER NOT NULL DEFAULT 1,
                updated_at TEXT,
                FOREIGN KEY (boat_id) REFERENCES boats(id) ON DELETE SET NULL
            )
            """
        )
        # What model a tracker is, keyed by the first eight digits of its IMEI (the
        # Type Allocation Code, which identifies manufacturer and model). Editable
        # because the club buys kit faster than anyone edits a constant, and because
        # Traccar's own `protocol` cannot tell an ATC700 from a RUTX50 — both are
        # "teltonika", one a battery tracker draining 10%/h, the other a mains-powered
        # router. core.track seeds the models the club already runs.
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS tracker_types (
                tac TEXT PRIMARY KEY,
                model TEXT NOT NULL,
                notes TEXT,
                protocol TEXT,
                updated_at TEXT
            )
            """
        )
        # A type code identifies whoever certified the radio, which for a tracker built
        # around an off-the-shelf cellular module is often the module vendor rather than
        # the tracker maker — the ATC700's datasheet names a Quectel EG915U. So an entry
        # may name the protocol it applies to, and a device that speaks something else
        # does not match it.
        ensure_column(db, "tracker_types", "protocol", "TEXT")
        # GPS finish proposals: a detected crossing awaiting RO confirmation, or a
        # record of a confirmed/auto finish (provenance for later review).
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS finish_proposals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                race_id INTEGER NOT NULL,
                entry_id INTEGER NOT NULL,
                detected_time TEXT NOT NULL,
                lat REAL, lon REAL,
                source TEXT NOT NULL DEFAULT 'gps',
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL,
                FOREIGN KEY (race_id) REFERENCES races(id) ON DELETE CASCADE,
                FOREIGN KEY (entry_id) REFERENCES entries(id) ON DELETE CASCADE
            )
            """
        )
        db.execute("CREATE INDEX IF NOT EXISTS idx_finish_proposals_race ON finish_proposals(race_id, status)")
        # A command typed in words, read back, and awaiting a yes. Kept in the
        # database rather than in memory for two reasons: a confirmation that
        # arrives after a restart still finds its command, and a repeated POST
        # over a dropped 4G connection is answered with the original result
        # instead of doing the thing twice.
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS assistant_commands (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token TEXT NOT NULL UNIQUE,
                client_command_id TEXT,
                actor TEXT NOT NULL DEFAULT '',
                text TEXT NOT NULL,
                intent TEXT NOT NULL,
                resolved_json TEXT NOT NULL DEFAULT '{}',
                readback TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'pending',
                result_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                executed_at TEXT
            )
            """
        )
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_assistant_client_id"
                   " ON assistant_commands(client_command_id) WHERE client_command_id IS NOT NULL")
        # v0.57: race entries store a snapshot of the IRC/YTC ratings used for
        # that race.  The existing manual_* columns are retained as the per-race
        # editable rating fields so old databases upgrade without a destructive
        # schema change.  Back-fill older boat-database entries once from the
        # current boat record; future edits to boats do not alter race entries.
        db.execute(
            """
            UPDATE entries
            SET manual_irc_rating = (SELECT boats.irc_rating FROM boats WHERE boats.id = entries.boat_id)
            WHERE boat_id IS NOT NULL
              AND manual_irc_rating IS NULL
              AND EXISTS (SELECT 1 FROM boats WHERE boats.id = entries.boat_id AND boats.irc_rating IS NOT NULL)
            """
        )
        db.execute(
            """
            UPDATE entries
            SET manual_ytc_rating = (SELECT boats.ytc_rating FROM boats WHERE boats.id = entries.boat_id)
            WHERE boat_id IS NOT NULL
              AND manual_ytc_rating IS NULL
              AND EXISTS (SELECT 1 FROM boats WHERE boats.id = entries.boat_id AND boats.ytc_rating IS NOT NULL)
            """
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS race_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                race_id INTEGER,
                event_time TEXT NOT NULL,
                event_type TEXT NOT NULL,
                label TEXT,
                source TEXT NOT NULL DEFAULT 'manual',
                details TEXT,
                FOREIGN KEY (race_id) REFERENCES races(id) ON DELETE CASCADE
            )
            """
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS hardware_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                display_name TEXT,
                role TEXT NOT NULL DEFAULT 'race_officer',
                status TEXT NOT NULL DEFAULT 'ACTIVE',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        ensure_column(db, "users", "last_login_at", "TEXT")
        # Who may re-measure a mark's position from a phone on the water.
        # Deliberately not a role: the person who takes the RIB out after a
        # storm is often neither an administrator nor the duty race officer.
        ensure_column(db, "users", "can_set_marks", "INTEGER NOT NULL DEFAULT 0")
        # Running racing from the water is a capability of its own. It is not
        # implied by being an administrator: configuring the app and starting a
        # race with nobody watching the line are different jobs, and the club
        # names who may do the second.
        ensure_column(db, "users", "can_race_remotely", "INTEGER NOT NULL DEFAULT 0")
        # Whether anybody has actually chosen this race's course. A new race
        # stores the first fixed course as a fallback so the geometry has
        # something to work with, and that fallback used to be indistinguishable
        # from a choice: the course board, the spoken announcement and the
        # competitor page all said "Course 1" before a race officer had looked.
        course_set_is_new = ensure_column(db, "races", "course_set", "INTEGER NOT NULL DEFAULT 0")
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS weather_samples (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sample_time REAL NOT NULL,
                sample_iso TEXT NOT NULL,
                twd REAL,
                tws_kt REAL,
                gust_kt REAL,
                source TEXT,
                raw_json TEXT
            )
            """
        )
        db.execute("CREATE INDEX IF NOT EXISTS idx_weather_samples_time ON weather_samples(sample_time)")
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS video_clips (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                race_id INTEGER NOT NULL,
                entry_id INTEGER,
                event_id INTEGER,
                clip_type TEXT NOT NULL,
                event_time TEXT NOT NULL,
                pre_seconds INTEGER NOT NULL DEFAULT 60,
                post_seconds INTEGER NOT NULL DEFAULT 60,
                status TEXT NOT NULL DEFAULT 'pending',
                label TEXT,
                file_path TEXT,
                message TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (race_id) REFERENCES races(id) ON DELETE CASCADE,
                FOREIGN KEY (entry_id) REFERENCES entries(id) ON DELETE SET NULL,
                FOREIGN KEY (event_id) REFERENCES race_events(id) ON DELETE SET NULL
            )
            """
        )
        db.execute("CREATE INDEX IF NOT EXISTS idx_video_clips_race ON video_clips(race_id, clip_type, event_time)")
        ensure_column(db, "video_clips", "public_status", "TEXT")
        ensure_column(db, "video_clips", "public_message", "TEXT")
        ensure_column(db, "video_clips", "public_file_path", "TEXT")
        ensure_column(db, "video_clips", "public_object_key", "TEXT")
        ensure_column(db, "video_clips", "public_url", "TEXT")
        # v0.268: the first frame of a clip is not event_time - pre_seconds.
        # Clips are cut from whole rolling-buffer segments, so the file begins
        # at a segment boundary up to one segment earlier. The replay locks its
        # clock to the picture, so it needs the moment the footage really starts.
        ensure_column(db, "video_clips", "footage_started_at", "TEXT")
        # v0.68: saved clips are backup-worthy data, so new clips live directly
        # under data/video_clips/. Rewrite the old default path when an older
        # database is opened; send_video_clip_response still tolerates legacy
        # absolute/relative paths for safety.
        db.execute(
            """
            UPDATE video_clips
            SET file_path = REPLACE(file_path, 'data/video/clips/', 'data/video_clips/')
            WHERE file_path LIKE 'data/video/clips/%'
            """
        )
        db.execute(
            """
            UPDATE video_clips
            SET file_path = REPLACE(file_path, 'data\\video\\clips\\', 'data/video_clips/')
            WHERE file_path LIKE 'data\\video\\clips\\%'
            """
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS race_series (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                description TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        # What has been put on the club website, and when. The app's own record
        # rather than a listing of the bucket: the competitor page needs the
        # latest link on every render, and asking Cloudflare for it would put a
        # signed round trip -- and the bucket credentials -- on a public page.
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS published_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                series_id INTEGER NOT NULL,
                object_key TEXT NOT NULL,
                url TEXT NOT NULL,
                stable_url TEXT NOT NULL,
                size_bytes INTEGER,
                published_at TEXT NOT NULL,
                published_by TEXT
            )
            """
        )
        db.execute("CREATE INDEX IF NOT EXISTS idx_published_results_series"
                   " ON published_results (series_id, published_at DESC)")
        ensure_column(db, "race_series", "class_config_json", "TEXT")
        ensure_column(db, "race_series", "start_plan_json", "TEXT")
        ensure_column(db, "race_series", "discard_profile", "TEXT")
        ensure_column(db, "race_series", "min_races_to_constitute", "INTEGER")
        ensure_column(db, "races", "series_id", "INTEGER")
        ensure_column(db, "races", "polar_file", "TEXT")
        ensure_column(db, "races", "custom_course_json", "TEXT")
        ensure_column(db, "races", "start_plan_json", "TEXT")
        # Pursuit races: race_type distinguishes them from the standard
        # class-based race; pursuit_duration_min is the fixed period from the
        # first start to the single finish signal. For a pursuit race rating_rule
        # holds the chosen system ('IRC_TCC' or 'YTC') that drives the staggered
        # start times, rather than the standard 'DUAL'.
        ensure_column(db, "races", "race_type", "TEXT")
        ensure_column(db, "races", "pursuit_duration_min", "REAL")
        # Shorten course: the race officer can shorten at a mark on the course, after
        # which boats proceed straight to the finish. Store the mark (display code,
        # for the announcement/banner), its position in the course sequence (to
        # truncate the course), and when it was called.
        ensure_column(db, "races", "shortened_at_mark", "TEXT")
        ensure_column(db, "races", "shortened_at_index", "INTEGER")
        ensure_column(db, "races", "shortened_at_time", "TEXT")
        # A postponement is a signal that was made, not a new start time. Storing
        # it is what lets the sequence be suspended rather than silently re-planned,
        # and what puts AP on the pages that show flags. Kinds: AP, AP_H (further
        # signals ashore), AP_A (no more racing today) -- RRS 27.3.
        ensure_column(db, "races", "postponed_at", "TEXT")
        ensure_column(db, "races", "postponement_kind", "TEXT")
        # When AP is to come down. The race officer picks a whole minute, so the
        # warning signal one minute later is a whole minute too -- a countdown to
        # 14:16:41 is one no competitor can follow.
        ensure_column(db, "races", "postponement_ends_at", "TEXT")
        # Back-filled here rather than beside the column, because it reads
        # custom_course_json, which is ensured further down this same block: on a
        # fresh database the earlier position raised "no such column".
        #
        # ONCE, when the column is added, and never again. init_db runs on every
        # request, so this ran on every request -- and it says "a race with a
        # start time has a chosen course", which was true of every race that
        # existed when the column arrived and is not true of one being set up
        # now. Setting the first warning signal before choosing the course is an
        # ordinary thing to do on a race morning; the next restart then marked
        # that race as having a course nobody had picked, and the board, the
        # chart and the competitor page went back to showing the fallback. The
        # whole point of the column, undone by its own migration.
        if course_set_is_new:
            db.execute("UPDATE races SET course_set = 1 WHERE course_set = 0"
                       " AND (start_time <> '' OR custom_course_json IS NOT NULL)")
        db.execute("UPDATE races SET race_type = 'standard' WHERE race_type IS NULL OR race_type = ''")
        existing_users = db.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
        if existing_users == 0:
            now = datetime.now().isoformat(timespec="seconds")
            db.execute(
                """
                INSERT OR IGNORE INTO users (username, password_hash, display_name, role, status, created_at, updated_at)
                VALUES (?, ?, ?, 'admin', 'ACTIVE', ?, ?)
                """,
                ("admin", generate_password_hash(initial_admin_password()), "Administrator", now, now),
            )
        else:
            replace_insecure_default_admin_password(db)
        db.commit()


# Register the schema builder with core.db so its init_db() can invoke it without
# a circular import. init_db (and the DB_INITIALIZED guard) now live in core.db.
_db.SCHEMA_INITIALIZER = _init_db_uncached


# ---------------------------------------------------------------------------
# Data-directory backup and restore helpers
# ---------------------------------------------------------------------------
def verify_restore_passphrase(zf: Any) -> None:
    """Decrypt one small member to check the passphrase before anything is deleted.

    The manifest is the cheapest proof: it is a few hundred bytes and every
    Race Officer backup carries one. A backup without it (hand-made, or from a
    much older build) falls back to the smallest member present.
    """
    # Non-empty only: reading one byte of a zero-length member succeeds whatever
    # the passphrase is, so an empty file would make this check pass and hand the
    # destructive path a passphrase it has not actually verified.
    members = [info for info in zf.infolist() if not info.filename.endswith("/") and info.file_size > 0]
    if not members:
        return
    probe = next((info for info in members if info.filename == BACKUP_MANIFEST_NAME),
                 min(members, key=lambda info: info.file_size))
    try:
        with zf.open(probe, "r") as handle:
            handle.read(1)
    except RuntimeError as exc:
        # pyzipper reports both a wrong passphrase and a missing one as RuntimeError.
        raise ValueError("The backup passphrase is not correct, so nothing was restored.") from exc


def restore_data_backup_zip(upload: Any, selected_sections: Iterable[Any], passphrase: str = "") -> Dict[str, Any]:
    """Restore selected sections from an uploaded Race Officer backup ZIP.

    Accepts an encrypted archive — the nightly off-site copy is AES-256 — given
    the passphrase. Member *names* are readable either way, so which sections a
    backup contains is known before anything is decrypted.
    """
    section_ids = normalise_backup_section_ids(selected_sections)
    if not section_ids:
        raise ValueError("Select at least one restore section.")
    if not upload or not getattr(upload, "filename", ""):
        raise ValueError("Choose a backup ZIP file to restore.")
    restore_root = Path(tempfile.mkdtemp(prefix="race_officer_restore_", dir=appstate.RUNTIME_DIR))
    upload_path = restore_root / "uploaded_backup.zip"
    upload.save(upload_path)
    restored_counts: Dict[str, int] = {section_id: 0 for section_id in section_ids}
    skipped_sections: List[str] = []
    restored_database = False
    restored_course_files = False
    sidecars_removed: List[str] = []
    database_sections = database_backup_section_ids()
    try:
        if not zipfile.is_zipfile(upload_path):
            raise ValueError("The uploaded file is not a valid ZIP backup.")
        with open_backup_zip(upload_path, passphrase) as zf:
            available_sections = [section_id for section_id in section_ids if backup_zip_has_section(zf, section_id)]
            skipped_sections = [section_id for section_id in section_ids if section_id not in available_sections]
            if not available_sections:
                raise ValueError("The uploaded ZIP does not contain any of the selected restore sections.")
            # Prove the passphrase BEFORE anything is deleted. The clearing step
            # below removes the current branding, polars and clips, and a wrong
            # passphrase only shows up when the first member is decrypted — which
            # would leave the folders emptied and nothing restored into them.
            if zip_is_encrypted(upload_path):
                verify_restore_passphrase(zf)
            # File/directory sections are cleared before extraction so deleted
            # logos, polars or video clips do not survive a restore.  The
            # database is overwritten file-by-file instead, then reopened below.
            for section_id in available_sections:
                if section_id != "database":
                    for target in backup_section_restore_targets(section_id):
                        clear_restore_target(target)
            for info in zf.infolist():
                safe_name = safe_restore_member_name(info.filename)
                if not safe_name:
                    continue
                matched_section = next((section_id for section_id in available_sections if backup_member_is_for_section(safe_name, section_id)), None)
                if not matched_section:
                    continue
                destination = restore_member_destination(safe_name)
                if destination is None:
                    continue
                destination.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info, "r") as src, destination.open("wb") as dst:
                    shutil.copyfileobj(src, dst)
                # The databases run in WAL mode, so a live -wal beside the file we
                # have just overwritten would be replayed over it on the next open
                # and hand back the OLD rows — a restore that reports success and
                # silently restores nothing. See clear_sqlite_sidecars.
                if matched_section in database_sections:
                    sidecars_removed.extend(clear_sqlite_sidecars(destination))
                restored_counts[matched_section] += 1
                if matched_section == "database":
                    restored_database = True
                if matched_section == "marks_courses":
                    restored_course_files = True
        if restored_database:
            _db.DB_INITIALIZED = False
            invalidate_app_settings_cache()
            init_db()
        # The track and power histories are separate SQLite files opened lazily
        # behind a module-level "already initialised" latch. Drop the latch and
        # re-open, so the restored file is picked up now and brought up to the
        # current schema rather than being trusted as-is on first use.
        if restored_counts.get("tracks"):
            track.TRACK_DB_INITIALIZED = False
            track.init_track_db()
        if restored_counts.get("power_history"):
            power.POWER_DB_INITIALIZED = False
            power.init_power_db()
        if restored_course_files:
            reload_course_mark_data()
        return {
            "ok": True,
            "restored_counts": restored_counts,
            "skipped_sections": skipped_sections,
            "restored_database": restored_database,
            "restored_course_files": restored_course_files,
            "sidecars_removed": sidecars_removed,
        }
    finally:
        try:
            shutil.rmtree(restore_root)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# General parsing/formatting helpers
# ---------------------------------------------------------------------------
def request_prefer_form(name: str, default: Any = None) -> Any:
    """Read a value from submitted form data before falling back to query-string arguments."""
    if request.method == "POST" and name in request.form:
        return request.form.get(name)
    return request.args.get(name, default)

def resolve_polar_path(name: Optional[str]) -> Path:
    """Resolve a selected polar filename without breaking names that contain spaces.

    The dropdown values come from files already present in data/polars, many of which
    have useful display names such as "Beneteau 40.7.txt".  Werkzeug's
    secure_filename() turns that into "Beneteau_40.7.txt", which made the lookup
    fail and caused the page to fall back to J122.  For selections, accept only the
    basename supplied by the browser and then match it against the known local
    polar files.  For uploads we still use secure_filename() when saving.
    """
    ensure_polars_dir()
    raw = (name or "").strip()
    if raw:
        # Drop any attempted directory component, but preserve spaces/dots in the
        # actual filename so existing polar names from the supplied zip still work.
        selected = Path(raw.replace("\\", "/")).name
        allowed = {p.name: p for p in appstate.POLARS_DIR.iterdir() if p.is_file() and p.suffix.lower() in ALLOWED_POLAR_EXTENSIONS}
        if selected in allowed:
            return allowed[selected]
        # Backwards compatibility for old uploads that may have been sanitised.
        sanitised = secure_filename(selected)
        if sanitised in allowed:
            return allowed[sanitised]
    fallback = appstate.POLARS_DIR / POLAR_PATH.name
    if fallback.exists():
        return fallback
    return POLAR_PATH


def recommend_courses_with_polar(twd: float, tws: float, target_minutes: float, polar_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Score and rank fixed courses using wind, polar speed and sail-change penalties."""
    polar_path = polar_path or resolve_polar_path(None)
    polar_rows = load_polar(polar_path)
    sail_chart = load_sail_chart(resolve_sail_chart_path_for_polar(polar_path))
    rows: List[Dict[str, Any]] = []
    for c in appstate.COURSES:
        legs = course_leg_analysis(c, twd, tws, polar_rows, sail_chart)
        leg_minutes = [x["leg_minutes"] for x in legs if x.get("leg_minutes") is not None]
        predicted_minutes = sum(leg_minutes) if leg_minutes and len(leg_minutes) == len(legs) else None
        wind_score = course_wind_score(c, twd)
        first_twa = next((x.get("twa") for x in legs if x.get("twa") is not None), None)
        first_leg_score = min(float(first_twa or 0), 90.0) * 0.20

        sails = [x.get("sail", "—") for x in legs]
        clean_sails = [s for s in sails if s and s != "—"]
        sail_changes = sum(1 for a, b in zip(clean_sails, clean_sails[1:]) if a != b)

        upwind_legs = sum(1 for x in legs if x.get("target") and x["target"].get("mode") == "upwind")
        downwind_legs = sum(1 for x in legs if x.get("target") and x["target"].get("mode") == "downwind")
        reach_legs = sum(1 for x in legs if x.get("twa") is not None and 55 <= x["twa"] <= 135)

        if predicted_minutes is None:
            time_score = 999.0
        else:
            time_score = abs(predicted_minutes - target_minutes)

        # Wind band is still important because the CHPSC course sheet is designed around it,
        # but polar-predicted duration and course shape decide the order within the band.
        score = wind_score * 5.0 + time_score + first_leg_score + sail_changes * 2.0

        rows.append({
            **c,
            "legs_analysis": legs,
            "polar_available": bool(polar_rows),
            "sail_chart_available": bool(sail_chart.get("rows")),
            "predicted_minutes": predicted_minutes,
            "predicted_display": format_minutes(predicted_minutes),
            "time_error_minutes": None if predicted_minutes is None else predicted_minutes - target_minutes,
            "time_error_abs": time_score,
            "wind_score": wind_score,
            "first_twa": first_twa,
            "first_leg_display": "—" if first_twa is None else f"{first_twa:.0f}° {legs[0].get('side', '—')}",
            "sail_changes": sail_changes,
            "upwind_legs": upwind_legs,
            "reach_legs": reach_legs,
            "downwind_legs": downwind_legs,
            "score": score,
        })
    return sorted(rows, key=lambda r: (r["score"], r["time_error_abs"]))


def analyse_course_with_wind(course: Dict[str, Any], twd: Optional[float], tws: Optional[float], polar_path: Optional[Path] = None, marks: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Analyse a single course using the same polar/sail logic as course recommendation.

    ``marks`` pins the geometry to a past race's mark positions — pass
    ``track.race_marks(race)``. Left None, the legs are measured against the marks
    as they are now, which is what a course being planned should use.
    """
    polar_path = polar_path or resolve_polar_path(None)
    polar_rows = load_polar(polar_path)
    sail_chart_path = resolve_sail_chart_path_for_polar(polar_path)
    sail_chart = load_sail_chart(sail_chart_path)
    if twd is None or tws is None:
        # Keep the geometry available even when no live/manual wind is present.
        legs = [
            {**leg, "twa": None, "side": "—", "point_of_sail": "—", "sail": "—", "sail_twa": None, "target": None, "leg_minutes": None}
            for leg in course_legs(course, marks=marks)
        ]
    else:
        legs = course_leg_analysis(course, float(twd), float(tws), polar_rows, sail_chart, marks=marks)
    leg_minutes = [x["leg_minutes"] for x in legs if x.get("leg_minutes") is not None]
    predicted_minutes = sum(leg_minutes) if leg_minutes and len(leg_minutes) == len(legs) else None
    first_twa = next((x.get("twa") for x in legs if x.get("twa") is not None), None)
    sails = [x.get("sail", "—") for x in legs]
    clean_sails = [s for s in sails if s and s != "—"]
    sail_changes = sum(1 for a, b in zip(clean_sails, clean_sails[1:]) if a != b)
    upwind_legs = sum(1 for x in legs if x.get("target") and x["target"].get("mode") == "upwind")
    downwind_legs = sum(1 for x in legs if x.get("target") and x["target"].get("mode") == "downwind")
    reach_legs = sum(1 for x in legs if x.get("twa") is not None and 55 <= x["twa"] <= 135)
    return {
        "course": course,
        "legs_analysis": legs,
        "polar_available": bool(polar_rows),
        "sail_chart_available": bool(sail_chart.get("rows")),
        "polar_file": polar_path.name,
        "sail_chart_file": sail_chart_path.name,
        "sail_chart_path": str(sail_chart_path.relative_to(appstate.DATA_DIR)) if sail_chart_path.is_relative_to(appstate.DATA_DIR) else sail_chart_path.name,
        "sail_chart_source": "polar-specific" if sail_chart_path.parent == appstate.SAIL_CHARTS_DIR else "default",
        "predicted_minutes": predicted_minutes,
        "predicted_display": format_minutes(predicted_minutes),
        "first_twa": first_twa,
        "first_leg_display": "—" if first_twa is None else f"{first_twa:.0f}° {legs[0].get('side', '—')}",
        "sail_changes": sail_changes,
        "upwind_legs": upwind_legs,
        "reach_legs": reach_legs,
        "downwind_legs": downwind_legs,
    }


def leg_analysis_json(analysis: Dict[str, Any]) -> Dict[str, Any]:
    """Serialise analysed legs for live table updates in the browser."""
    rows = []
    for leg in analysis.get("legs_analysis", []):
        rows.append({
            "from": leg.get("from"),
            "to": leg.get("to"),
            "to_rounding": leg.get("to_rounding"),
            "distance_nm": leg.get("distance_nm"),
            "bearing_deg": leg.get("bearing_deg"),
            "twa": leg.get("twa"),
            "side": leg.get("side", "—"),
            "sail": leg.get("sail", "—"),
            "sail_twa": round(leg.get("sail_twa"), 1) if leg.get("sail_twa") is not None else None,
            "target": leg.get("target"),
            "target_display": format_target(leg.get("target")),
            "leg_minutes": leg.get("leg_minutes"),
            "leg_time_display": format_minutes(leg.get("leg_minutes")),
            "point_of_sail": leg.get("point_of_sail", "—"),
        })
    return {
        "polar_file": analysis.get("polar_file"),
        "sail_chart_file": analysis.get("sail_chart_file"),
        "sail_chart_path": analysis.get("sail_chart_path"),
        "sail_chart_source": analysis.get("sail_chart_source"),
        "predicted_minutes": analysis.get("predicted_minutes"),
        "predicted_display": analysis.get("predicted_display"),
        "first_leg_display": analysis.get("first_leg_display"),
        "sail_changes": analysis.get("sail_changes"),
        "upwind_legs": analysis.get("upwind_legs"),
        "reach_legs": analysis.get("reach_legs"),
        "downwind_legs": analysis.get("downwind_legs"),
        "legs": rows,
    }


# ---------------------------------------------------------------------------
# Boat database, IRC/YTC lookup and rating helpers
# ---------------------------------------------------------------------------
def rating_lookup_results(q: str, irc_limit: int = 40, ytc_limit: int = 40) -> Dict[str, Any]:
    """Search both configured rating sources and return normalised rows for the add/edit boat form."""
    q = (q or "").strip()
    urls = listing_config()
    results: Dict[str, Any] = {"q": q, "irc": [], "ytc": [], "errors": [], "urls": urls}
    if not q:
        return results
    try:
        results["irc"] = [irc_row_to_prefill(row) for row in filter_irc_rows(read_irc_listing(urls["irc_listing_url"]), q, limit=irc_limit)]
    except (urllib.error.URLError, TimeoutError, csv.Error, UnicodeDecodeError, ValueError, OSError) as exc:
        results["errors"].append(f"IRC listing: {exc}")
    try:
        results["ytc"] = [ytc_row_to_prefill(row) for row in filter_ytc_rows(read_ytc_listing(urls["ytc_listing_url"]), q, limit=ytc_limit)]
    except (urllib.error.URLError, TimeoutError, csv.Error, UnicodeDecodeError, ValueError, OSError) as exc:
        results["errors"].append(f"YTC sheet: {exc}")
    return results


def image_data_uri(path: Path) -> str:
    """Return a data URI for a local PNG/JPEG image, or an empty string."""
    try:
        suffix = path.suffix.lower()
        if suffix == ".png":
            mimetype = "image/png"
        elif suffix in (".jpg", ".jpeg"):
            mimetype = "image/jpeg"
        else:
            return ""
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    except OSError:
        return ""
    return f"data:{mimetype};base64,{encoded}"


# The published results page shows the club logo at most 140x86 and each sponsor
# at most 160x70. Twice that covers a high-density screen and is where the
# scaling stops.
PUBLISHED_LOGO_MAX = (320, 176)


def thumbnail_data_uri(path: Path, max_size: tuple = PUBLISHED_LOGO_MAX) -> str:
    """A data URI for a logo, scaled to the size it is actually displayed at.

    The published results document embeds every logo so it stays a single file
    somebody can upload anywhere. It was embedding the ORIGINALS: 1.48 MB of PNG
    across six logos, which base64 inflates by a third, so a results page went
    out at 2 MB -- and since v0.281 that goes over the hut's 4G on every
    publish. One sponsor logo alone was 742 KB, displayed at 160px wide.

    Falls back to the full-size embed if Pillow is missing or the image cannot
    be read. Pillow is in requirements.txt but the app has always treated it as
    optional, and a larger file is a much better failure than no logos.
    """
    try:
        from PIL import Image
    except Exception:
        return image_data_uri(path)
    try:
        with Image.open(path) as img:
            img.load()
            # RGBA throughout: these are logos, and flattening a palette image
            # to RGB would put a black box behind a transparent one.
            if img.mode not in ("RGBA", "LA"):
                img = img.convert("RGBA")
            img.thumbnail(max_size, Image.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, format="PNG", optimize=True)
        encoded = base64.b64encode(buf.getvalue()).decode("ascii")
        scaled = f"data:image/png;base64,{encoded}"
    except Exception:
        return image_data_uri(path)
    # Whichever is smaller. A logo already below the target size gains nothing
    # from being re-encoded and can come out LARGER -- measured, one 12 KB
    # sponsor logo grew to 15 KB, because the original was a tightly packed
    # palette PNG and this writes RGBA. Shrinking is the point; re-encoding is
    # only the means.
    original = image_data_uri(path)
    return scaled if not original or len(scaled) <= len(original) else original


def published_logo_data_uri() -> str:
    """Embed the default club logo so the published results HTML remains standalone."""
    return thumbnail_data_uri(DEFAULT_CLUB_LOGO_PATH)


def published_branding_logos() -> List[Dict[str, str]]:
    """Return embedded club/sponsor logo data for standalone results publishing.

    The downloadable/published HTML is commonly uploaded outside the Race
    Officer app, so every enabled branding image is embedded as a data URI
    rather than linked to /public/branding/*.
    """
    assets = branding_assets()
    logos: List[Dict[str, str]] = []

    def add_logo(kind: str, name: str, path: Optional[Path]) -> None:
        if not path:
            return
        data_uri = thumbnail_data_uri(path)
        if data_uri:
            logos.append({"kind": kind, "name": name, "data_uri": data_uri})

    if assets.get("enabled"):
        if assets.get("club_logo_enabled"):
            club_path = assets.get("club_logo_path")
            add_logo("club", "Pwllheli Sailing Club", club_path if isinstance(club_path, Path) else None)
        for sponsor in assets.get("sponsors") or []:
            raw_path = sponsor.get("path")
            add_logo("sponsor", str(sponsor.get("name") or "Sponsor"), raw_path if isinstance(raw_path, Path) else None)

    # Preserve the previous behaviour of showing the PSC logo even when no
    # upload/manifest branding has been configured yet.
    if not any(logo["kind"] == "club" for logo in logos):
        add_logo("club", "Pwllheli Sailing Club", DEFAULT_CLUB_LOGO_PATH)
    return logos


def published_video_url_for_clip(clip: Optional[sqlite3.Row]) -> str:
    """The public URL for a clip in a standalone results document, or nothing.

    **Only a URL that works from anywhere.** This document is uploaded to the
    club's bucket and read from the club website, on phones on the pontoon and
    computers at home, so a link into the hut is no link at all.

    It used to fall back to ``url_for(..., _external=True)`` when a clip had not
    been published to R2, which builds an address from whatever host the race
    officer happened to publish from: a file full of
    ``http://raceofficer.local:5050/public/video/clip/124`` -- 43 of them in one
    measured document -- every one of them dead for the people it was published
    for. A row that honestly says there is no video beats a link that fails.

    The competitor pages keep that fallback, in public_video_clip_href, and
    should: they are served *by* the hut, so a hut address is exactly right
    there. Publishing videos to R2 is what makes one publicly linkable, and
    ``video_public_r2_*`` in Settings is where that is turned on.
    """
    if not public_video_clip_is_viewable(clip):
        return ""
    public_status = str(row_get(clip, "public_status") or "").strip().lower()
    public_url = str(row_get(clip, "public_url") or "").strip()
    if public_url and public_status == "ready":
        return public_url
    return ""


def public_video_clip_href(clip: Optional[sqlite3.Row]) -> str:
    """Return the best link for a viewable clip on the live competitor pages.

    Prefer the Cloudflare R2 public copy when it is ready so competitors load
    video from R2 rather than the hut PC; otherwise fall back to serving the
    clip from the hut. Mirrors published_video_url_for_clip, but returns a
    relative hut URL (the competitor page is loaded from the hut/tunnel host).
    """
    if not clip:
        return ""
    public_status = str(row_get(clip, "public_status") or "").strip().lower()
    public_url = str(row_get(clip, "public_url") or "").strip()
    if public_url and public_status == "ready":
        return public_url
    try:
        return url_for("public_video_clip_file", clip_id=int(clip["id"]))  # type: ignore[index]
    except Exception:
        return ""


def published_video_link_for_clip(clip: Optional[sqlite3.Row], default_label: str) -> Optional[Dict[str, str]]:
    """Return display data for a public video link/status in published HTML."""
    if not clip:
        return None
    label = str(row_get(clip, "label") or default_label).strip() or default_label
    url = published_video_url_for_clip(clip)
    status_text = "View video" if url else public_video_clip_link_text(clip)
    message = str(row_get(clip, "public_message") or row_get(clip, "message") or "").strip()
    return {
        "id": str(row_get(clip, "id") or ""),
        "label": label,
        "url": url,
        "status_text": status_text,
        "message": message,
    }


def published_video_links_for_race(race_id: int) -> Dict[str, Any]:
    """Return public start/finish video links for one race results export."""
    clips = get_video_clips_for_race(race_id)
    start_links: List[Dict[str, str]] = []
    finish_by_entry: Dict[str, Dict[str, str]] = {}

    start_clips = [c for c in clips if str(row_get(c, "clip_type") or "") == "start"]
    start_clips.sort(key=lambda c: (str(row_get(c, "event_time") or ""), int(row_get(c, "id") or 0)))
    for index, clip in enumerate(start_clips, start=1):
        default_label = "Start video" if len(start_clips) == 1 else f"Start video {index}"
        link = published_video_link_for_clip(clip, default_label)
        if link:
            start_links.append(link)

    race = get_race(race_id)
    if race is not None and is_pursuit_race(race):
        # A pursuit race has a start video per boat and no finish video, so the
        # per-boat video column shows each boat's start video instead.
        clip_by_entry = pursuit_start_clip_by_entry(get_entries(race_id), clips)
        for entry_id, clip in clip_by_entry.items():
            link = published_video_link_for_clip(clip, "Start video")
            if link:
                finish_by_entry[str(entry_id)] = link
        # A pursuit race lists each boat's start video in the per-boat column, so
        # the separate start-videos row above the table is not needed.
        return {"start_links": [], "finish_by_entry": finish_by_entry, "video_column_label": "Start video"}

    # **Any clip carrying an entry_id is that boat's finish video**, whatever its
    # clip_type -- the same rule video_clip_maps uses for the competitor page,
    # and the reason the two disagreed.
    #
    # A finish taken on the *physical horn switch* is logged as a `manual_horn`
    # clip. Assigning that horn time to a boat afterwards (the Race log's "assign
    # as finish") writes the finish onto the entry and stamps the entry_id onto
    # the clip, but leaves its type alone -- there is no reason for the flow to
    # rewrite it, and the clip genuinely is a recording of a horn. Asking for
    # clip_type == "finish" therefore missed every finish taken that way: on the
    # club's hut, 10 of 26. The competitor page showed them and the published
    # document said "No Video" for the same three boats in the same race.
    for clip in clips:
        entry_id = row_get(clip, "entry_id")
        if entry_id is None:
            continue
        key = str(entry_id)
        link = published_video_link_for_clip(clip, "Finish video")
        if not link:
            continue
        existing = finish_by_entry.get(key)
        # Prefer a viewable public link over a pending/error status if multiple
        # finish clips exist for the same entry. Otherwise keep the latest clip
        # returned by get_video_clips_for_race().
        if existing is None or (link.get("url") and not existing.get("url")):
            finish_by_entry[key] = link

    return {"start_links": start_links, "finish_by_entry": finish_by_entry, "video_column_label": "Finish video"}


def publish_race_table_for_series_class(
    race: sqlite3.Row,
    result_type: str,
    class_name: str,
    group_cache: Optional[Dict[Tuple[int, str], Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Build the race-table object matching one published series summary table."""
    group = series_race_result_group(race, result_type, group_cache)
    table = select_result_table_from_group(group, class_name)
    return {
        "race": race,
        "title": f"{race['name']} - {table.get('title', f'{result_type} results')}",
        "caption": f"First warning {dt_full_display(row_get(race, 'start_time')) or 'not set'} · First start {dt_full_display(race_first_start_time(race)) or 'not set'} · {table.get('start_note') or ''}".strip(" ·"),
        "table": table,
        "rows": table.get("rows", []),
        "videos": published_video_links_for_race(int(race["id"])),
    }


def build_series_publish_model(series_id: int) -> Dict[str, Any]:
    """Create the single-file Sailwave-style website publishing model for a series."""
    series = get_series(series_id)
    if not series:
        raise ValueError("Series not found")
    races = get_series_races(series_id)
    results = build_series_results(series_id)
    generated_at = datetime.now().isoformat(timespec="seconds")
    groups: List[Dict[str, Any]] = []
    publish_group_cache: Dict[Tuple[int, str], Dict[str, Any]] = {}

    for result_key in ("irc", "ytc"):
        result_group = results[result_key]
        result_type = str(result_group.get("title", result_key)).split()[0].upper()
        for table_index, series_table in enumerate(result_group.get("tables", []), start=1):
            class_name = str(series_table.get("class_name") or "")
            label = series_table.get("title") or f"{result_type} series"
            slug = html_id_slug(f"{result_type}_{class_name or table_index}", f"{result_type.lower()}_{table_index}")
            race_tables = []
            for race_index, race in enumerate(races, start=1):
                race_table = publish_race_table_for_series_class(race, result_type, class_name, publish_group_cache)
                if race_table.get("rows"):
                    race_table["race_index"] = race_index
                    race_table["id"] = f"race_{race_index}_{slug}"
                    race_tables.append(race_table)
            include_group = bool(series_table.get("rows")) or bool(race_tables)
            if not include_group:
                continue
            groups.append({
                "id": f"summary_{slug}",
                "slug": slug,
                "title": label,
                "result_type": result_type,
                "rating_label": series_table.get("rating_label") or rating_label_for_type(result_type),
                "series_table": series_table,
                "race_tables": race_tables,
            })

    return {
        "series": series,
        "races": races,
        "results": results,
        "groups": groups,
        "club_logo_data_uri": published_logo_data_uri(),
        "branding_logos": published_branding_logos(),
        "generated_at": generated_at,
        "generated_at_text": dt_full_display(generated_at) or generated_at,
    }


def save_app_settings(settings: Dict[str, Any]) -> str:
    """Persist app-level settings from a settings form submission.

    Returns a redacted ``key: old -> new`` summary of what actually changed, for the
    activity log. The old values are read here anyway for the blank-means-keep rule
    on the secrets, so the diff costs nothing extra.
    """
    init_db()
    existing = get_app_setting_overrides()
    ptz_password = str(settings.get("ptz_password", ""))
    if text_to_bool(settings.get("ptz_password_clear"), False):
        ptz_password = ""
    elif not ptz_password:
        # Password fields are deliberately blank in the Settings form; keep the
        # saved camera password unless the RO explicitly clears or replaces it.
        ptz_password = existing.get("ptz_password", "")
    r2_secret = str(settings.get("video_public_r2_secret_access_key", ""))
    if text_to_bool(settings.get("video_public_r2_secret_access_key_clear"), False):
        r2_secret = ""
    elif not r2_secret:
        # Keep the saved R2 secret access key unless the RO explicitly clears or replaces it.
        r2_secret = existing.get("video_public_r2_secret_access_key", "")
    offsite_passphrase = str(settings.get("offsite_backup_passphrase", ""))
    if text_to_bool(settings.get("offsite_backup_passphrase_clear"), False):
        offsite_passphrase = ""
    elif not offsite_passphrase:
        # Same rule as the camera and R2 secrets: a blank field means "keep".
        # Losing this one silently would be worse than the others — every archive
        # already in the bucket was encrypted with it and nothing can open them
        # without it.
        offsite_passphrase = existing.get("offsite_backup_passphrase", "")
    now = datetime.now().isoformat(timespec="seconds")
    values = {
        "irc_listing_url": safe_rating_listing_url(str(settings.get("irc_listing_url", IRC_LISTING_URL)), IRC_LISTING_URL),
        "ytc_listing_url": safe_rating_listing_url(str(settings.get("ytc_listing_url", YTC_LISTING_URL)), YTC_LISTING_URL),
        "weather_source": str(settings.get("weather_source", "station")).strip().lower() if str(settings.get("weather_source", "station")).strip().lower() in ("station", "manual") else "station",
        "weather_enabled": bool_to_text(str(settings.get("weather_source", "station")).strip().lower() == "station"),
        "weather_station_url": safe_weather_station_url(str(settings.get("weather_station_url", DEFAULT_WEATHER_STATION_URL))),
        "weather_poll_seconds": str(int_in_range(settings.get("weather_poll_seconds"), 5, 1, 120)),
        "weather_wind_dir_offset": str(float_in_range(settings.get("weather_wind_dir_offset"), 0.0, -180.0, 180.0)),
        "weather_manual_twd": "" if parse_float(settings.get("weather_manual_twd")) is None else f"{float_in_range(settings.get('weather_manual_twd'), 0.0, 0.0, 359.9):.1f}",
        "weather_manual_tws": "" if parse_float(settings.get("weather_manual_tws")) is None else f"{float_in_range(settings.get('weather_manual_tws'), 0.0, 0.0, 120.0):.1f}",
        "video_enabled": bool_to_text(text_to_bool(settings.get("video_enabled"), False)),
        "video_source_type": str(settings.get("video_source_type", "usb")).strip().lower() if str(settings.get("video_source_type", "usb")).strip().lower() in ("usb", "rtsp") else "usb",
        "video_usb_source": str(settings.get("video_usb_source", "")).strip(),
        "video_rtsp_url": str(settings.get("video_rtsp_url", "")).strip(),
        "video_preview_rtsp_url": str(settings.get("video_preview_rtsp_url", "")).strip(),
        "video_recording_mode": str(settings.get("video_recording_mode", "copy")).strip().lower() if str(settings.get("video_recording_mode", "copy")).strip().lower() in ("copy", "reencode") else "copy",
        "video_copy_container": normalise_video_copy_container(settings.get("video_copy_container")),
        "video_rtsp_timestamp_mode": normalise_video_rtsp_timestamp_mode(settings.get("video_rtsp_timestamp_mode")),
        "video_preview_size": normalise_video_preview_size(settings.get("video_preview_size")),
        "video_preview_fps": str(int_in_range(settings.get("video_preview_fps"), 2, 1, 10)),
        "video_preview_jpeg_quality": str(int_in_range(settings.get("video_preview_jpeg_quality"), 3, 2, 12)),
        "video_preview_keyframes_only": bool_to_text(text_to_bool(settings.get("video_preview_keyframes_only"), False)),
        "video_ffmpeg_path": str(settings.get("video_ffmpeg_path", "ffmpeg")).strip() or "ffmpeg",
        "video_pre_seconds": str(int_in_range(settings.get("video_pre_seconds"), 60, 5, 300)),
        "video_post_seconds": str(int_in_range(settings.get("video_post_seconds"), 60, 5, 300)),
        "video_segment_seconds": str(int_in_range(settings.get("video_segment_seconds"), 5, 2, 30)),
        "video_buffer_minutes": str(int_in_range(settings.get("video_buffer_minutes"), 20, 5, 180)),
        "video_public_provider": normalise_video_public_provider(settings.get("video_public_provider")),
        "video_public_quality": normalise_video_public_quality(settings.get("video_public_quality")),
        "video_startline_overlay_enabled": bool_to_text(text_to_bool(settings.get("video_startline_overlay_enabled"), False)),
        "video_public_live_provider": normalise_video_public_live_provider(settings.get("video_public_live_provider")),
        "video_public_live_interval_seconds": str(int_in_range(settings.get("video_public_live_interval_seconds"), 5, 2, 60)),
        "video_public_r2_account_id": safe_r2_account_id(str(settings.get("video_public_r2_account_id", ""))),
        "video_public_r2_bucket": safe_r2_bucket_name(str(settings.get("video_public_r2_bucket", ""))),
        "video_public_r2_access_key_id": str(settings.get("video_public_r2_access_key_id", "")).strip(),
        "video_public_r2_secret_access_key": r2_secret,
        "video_public_r2_public_base_url": safe_public_video_base_url(str(settings.get("video_public_r2_public_base_url", ""))),
        "video_public_r2_prefix": safe_r2_key_prefix(str(settings.get("video_public_r2_prefix", "race-videos"))),
        "public_branding_enabled": bool_to_text(text_to_bool(settings.get("public_branding_enabled"), True)),
        "public_branding_club_logo_enabled": bool_to_text(text_to_bool(settings.get("public_branding_club_logo_enabled"), True)),
        "ptz_enabled": bool_to_text(text_to_bool(settings.get("ptz_enabled"), False)),
        "ptz_camera_url": safe_ptz_camera_base_url(str(settings.get("ptz_camera_url", ""))),
        "ptz_username": str(settings.get("ptz_username", "")).strip(),
        "ptz_password": ptz_password,
        "ptz_auth_mode": normalise_ptz_auth_mode(settings.get("ptz_auth_mode")),
        "ptz_channel": str(int_in_range(settings.get("ptz_channel"), 1, 1, 32)),
        "ptz_idle_preset": str(int_in_range(settings.get("ptz_idle_preset"), 1, 1, 300)),
        "ptz_recording_preset": str(int_in_range(settings.get("ptz_recording_preset"), 2, 1, 300)),
        "ptz_pre_start_seconds": str(int_in_range(settings.get("ptz_pre_start_seconds"), 30, 0, 600)),
        "course_chart_tile_url": str(settings.get("course_chart_tile_url", DEFAULT_COURSE_CHART_TILE_URL)).strip(),
        "course_chart_overlay_url": str(settings.get("course_chart_overlay_url", DEFAULT_COURSE_CHART_OVERLAY_URL)).strip(),
        "start_automation_horn_enabled": bool_to_text(text_to_bool(settings.get("start_automation_horn_enabled"), True)),
        "start_automation_audio_enabled": bool_to_text(text_to_bool(settings.get("start_automation_audio_enabled"), True)),
        "central_audio_rate": str(int_in_range(settings.get("central_audio_rate"), 185, 80, 320)),
        "central_audio_fast_rate": str(int_in_range(settings.get("central_audio_fast_rate"), 285, 120, 420)),
        "central_audio_vox_tone_enabled": bool_to_text(text_to_bool(settings.get("central_audio_vox_tone_enabled"), False)),
        "central_audio_vox_lead_seconds": str(int_in_range(settings.get("central_audio_vox_lead_seconds"), 2, 1, 10)),
        "public_live_stream_url": sanitize_public_url(settings.get("public_live_stream_url", "")),
        "offsite_backup_enabled": bool_to_text(text_to_bool(settings.get("offsite_backup_enabled"), False)),
        "offsite_backup_bucket": safe_r2_bucket_name(str(settings.get("offsite_backup_bucket", ""))),
        "offsite_backup_prefix": safe_r2_key_prefix(str(settings.get("offsite_backup_prefix", "")) or offsite.DEFAULT_OFFSITE_PREFIX),
        "offsite_backup_hour": str(int_in_range(settings.get("offsite_backup_hour"), offsite.DEFAULT_OFFSITE_HOUR, 0, 23)),
        "offsite_backup_minute": str(int_in_range(settings.get("offsite_backup_minute"), offsite.DEFAULT_OFFSITE_MINUTE, 0, 59)),
        "offsite_backup_keep": str(int_in_range(settings.get("offsite_backup_keep"), offsite.DEFAULT_OFFSITE_KEEP, 1, 365)),
        "offsite_backup_sections": ",".join(offsite.normalise_offsite_sections(settings.get("offsite_backup_sections"))),
        "offsite_backup_passphrase": offsite_passphrase,
    }
    with get_db() as db:
        for key, value in values.items():
            db.execute(
                """
                INSERT INTO app_settings (key, value, updated_at) VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                """,
                (key, value, now),
            )
        db.commit()
    invalidate_app_settings_cache()
    return settings_change_summary(existing, values)


def public_live_image_context() -> Dict[str, Any]:
    """Return the URL and refresh cadence for the public live-camera image."""
    cfg = video_config()
    interval_seconds = int_in_range(cfg.get("video_public_live_interval_seconds"), 5, 2, 60)
    if public_live_r2_enabled(cfg):
        return {
            "provider": "r2",
            "src": public_live_r2_url(cfg),
            "refresh_ms": interval_seconds * 1000,
            "message": "Live image is served from Cloudflare R2.",
        }
    return {
        "provider": "local",
        "src": url_for("public_video_live_frame"),
        "refresh_ms": 2000,
        "message": "Live image is served from the hut app.",
    }


def race_is_finished_for_public(race_id: int) -> bool:
    """Return whether a public race page may show final results and videos."""
    entries = get_entries(race_id)
    if not entries:
        return False
    return all(e["status"] != "RACING" for e in entries)


def send_video_clip_response(clip_id: int, public: bool = False) -> Response:
    """Serve a video clip file after applying access checks."""
    init_db()
    with get_db() as db:
        clip = db.execute("SELECT * FROM video_clips WHERE id = ?", (clip_id,)).fetchone()
    if not clip or clip["status"] != "ready" or not clip["file_path"]:
        return Response("Video clip is not ready", status=404)
    if public:
        race = get_race(int(clip["race_id"]))
        if not public_access_allowed_for_race(race):
            return public_access_denied_response()
        if not race_is_finished_for_public(int(clip["race_id"])):
            return Response("Video clips are only public after the race has finished", status=403)
        public_url = str(row_get(clip, "public_url") or "").strip()
        public_status = str(row_get(clip, "public_status") or "").strip().lower()
        if public_url and public_status == "ready":
            return redirect(public_url)
        # When R2 publishing is enabled for this clip, do not fall back to serving
        # the evidence-quality local file through the hut 4G tunnel.
        if public_status in ("pending", "processing", "uploading", "error"):
            return Response(row_get(clip, "public_message") or "Public video clip is not ready", status=404)
    stored = Path(str(clip["file_path"]))
    path = stored if stored.is_absolute() else (BASE_DIR / stored)
    path = path.resolve()
    clips_root = appstate.VIDEO_CLIPS_DIR.resolve()
    legacy_clips_root = appstate.LEGACY_VIDEO_CLIPS_DIR.resolve()
    if not path.exists() and stored.name:
        migrated = appstate.VIDEO_CLIPS_DIR / stored.name
        if migrated.exists():
            path = migrated.resolve()
    if not ((clips_root in path.parents or path == clips_root) or (legacy_clips_root in path.parents or path == legacy_clips_root)):
        return Response("Invalid video path", status=404)
    if not path.exists():
        return Response("Video file was not found", status=404)
    return send_file(path, mimetype="video/mp4", as_attachment=False, download_name=path.name)


def branding_assets_for_template() -> Dict[str, Any]:
    """Return public branding with URLs for Jinja templates."""
    assets = branding_assets()
    club_url = ""
    if assets.get("club_logo_enabled") and assets.get("club_logo_path"):
        club_path = Path(assets["club_logo_path"])
        if club_path.parent == (BASE_DIR / "static" / "img"):
            club_url = url_for("static", filename=f"img/{club_path.name}")
        else:
            club_url = url_for("public_branding_file", filename=club_path.name)
    sponsors = []
    for sponsor in assets.get("sponsors") or []:
        sponsors.append({
            **sponsor,
            "url": url_for("public_branding_file", filename=sponsor["filename"]),
        })
    return {**assets, "club_logo_url": club_url, "sponsors": sponsors}


def sanitize_public_url(value: Any) -> str:
    """Return an http(s) URL safe to place in an href, or empty string."""
    url = str(value or "").strip()
    return url if url.lower().startswith(("http://", "https://")) else ""


def public_live_stream_url() -> str:
    """Return the configured external live-stream page URL, if any.

    Drives the "Watch live" link on the competitor pages: shown only once the
    external stream relay (see deploy/live_stream) has been set up and its URL
    saved in Settings. Validated to an http(s) URL.
    """
    return sanitize_public_url(get_app_setting_overrides().get("public_live_stream_url", ""))


def delete_race_and_related_data(race_id: int) -> Dict[str, int]:
    """Delete a race plus its entries, events and video-clip records/files."""
    init_db()
    with get_db() as db:
        clips = db.execute("SELECT * FROM video_clips WHERE race_id = ?", (race_id,)).fetchall()
        deleted_files = 0
        for clip in clips:
            if safe_delete_race_video_file(row_get(clip, "file_path")):
                deleted_files += 1
            if safe_delete_race_video_file(row_get(clip, "public_file_path")):
                deleted_files += 1
        counts = {
            "entries": db.execute("SELECT COUNT(*) AS n FROM entries WHERE race_id = ?", (race_id,)).fetchone()["n"],
            "events": db.execute("SELECT COUNT(*) AS n FROM race_events WHERE race_id = ?", (race_id,)).fetchone()["n"],
            "video_clips": len(clips),
            "video_files": deleted_files,
        }
        db.execute("DELETE FROM video_clips WHERE race_id = ?", (race_id,))
        db.execute("DELETE FROM race_events WHERE race_id = ?", (race_id,))
        db.execute("DELETE FROM entries WHERE race_id = ?", (race_id,))
        db.execute("DELETE FROM races WHERE id = ?", (race_id,))
        db.commit()
    return counts


    course_ref = f"course:{race['course_no']}"
    try:
        custom_course = race["custom_course_json"] if "custom_course_json" in race.keys() else None
        if custom_course:
            course_ref = "custom:" + hashlib.sha1(str(custom_course).encode("utf-8", "replace")).hexdigest()[:12]
    except Exception:
        pass
    start_plan_ref = json.dumps(race_start_plan(race), sort_keys=True, default=str)
    material = f"race:{race['id']}|start:{start_time}|{course_ref}|starts:{start_plan_ref}"
    return hashlib.sha1(material.encode("utf-8", "replace")).hexdigest()[:16]


@app.before_request
def ensure_background_tasks_running() -> None:
    # If the app is imported by a WSGI server instead of started with
    # `python app.py`, this makes sure the background services still start on
    # the first web request.
    """Start background services required by request handlers."""
    start_weather_background_poller()
    start_video_background_recorder()
    # Supervises the recorder from here on: it used to be restarted only by a
    # settings save or a scheduled clip, so an FFmpeg that exited overnight
    # stayed exited and a race went unrecorded.
    start_video_watchdog()
    start_public_live_r2_uploader()
    start_central_audio_worker()
    start_start_sequence_scheduler()
    start_power_monitor_worker()
    start_track_monitor_worker()
    start_offsite_backup_worker()


def public_access_allowed_for_race(race: Optional[sqlite3.Row]) -> bool:
    """Check whether this request may see a public read-only race page/API."""
    return bool(race)


def public_access_denied_response() -> Response:
    """Return a deliberately plain public access error."""
    return Response("Public race was not found.", status=404)


def public_race_url_args(race: sqlite3.Row, **extra: Any) -> Dict[str, Any]:
    """Build URL args for a public read-only race link."""
    args: Dict[str, Any] = {"race_id": int(race["id"])}
    for k, v in extra.items():
        if v not in (None, ""):
            args[k] = v
    return args


def public_link_args_from_request(race: sqlite3.Row, **extra: Any) -> Dict[str, Any]:
    """Build URL args for links rendered inside the public page."""
    return public_race_url_args(race, **extra)


def public_context_urls(race: sqlite3.Row, selected_polar: Optional[str] = None,
                        pinned: bool = True) -> Dict[str, str]:
    """Return relative public URLs for links inside a rendered public page.

    ``pinned`` is the difference between /public/race/<id> and /public/race, and
    the only thing it changes here is where the page polls. An unpinned page asks
    the *current race* endpoint, whose signature covers the race id, so when the
    race officer moves on to the next race the signature changes, the page's
    existing reload fires, and it reloads its own URL onto the new race. The
    boat positions and the track stay pinned to the race being rendered: they are
    fetched between reloads, and following the current race there would mean a
    page drawing one race's boats over another's course for a few seconds.
    """
    polar_args = {"polar_file": selected_polar} if selected_polar else {}
    return {
        "current": url_for("competitor_current_race"),
        "desktop": url_for("competitor_race", race_id=int(race["id"]), **polar_args),
        "mobile": url_for("competitor_race_mobile", race_id=int(race["id"]), **polar_args),
        "state": (url_for("competitor_race_state", race_id=int(race["id"])) if pinned
                  else url_for("competitor_current_race_state")),
        "positions": url_for("competitor_race_positions", race_id=int(race["id"])),
        "track": url_for("competitor_race_track", race_id=int(race["id"])),
    }


def public_race_links(race: sqlite3.Row, selected_polar: Optional[str] = None) -> Dict[str, str]:
    """Return absolute and relative public links for display on the race page."""
    desktop_args = public_race_url_args(race, polar_file=selected_polar)
    mobile_args = public_race_url_args(race, polar_file=selected_polar)
    return {
        "desktop": url_for("competitor_race", _external=True, **desktop_args),
        "mobile": url_for("competitor_race_mobile", _external=True, **mobile_args),
        "desktop_relative": url_for("competitor_race", **desktop_args),
        "mobile_relative": url_for("competitor_race_mobile", **mobile_args),
        "current": url_for("public_root", _external=True),
        "current_relative": url_for("public_root"),
    }


def list_users() -> List[sqlite3.Row]:
    """Return all application users for Settings -> Users.

    The column list is explicit to keep password_hash off the page, so anything
    the users table needs has to be named here. can_set_marks was not, which the
    page had no way to show: the checkbox read as unchecked however it was saved,
    so there was no telling who held the permission.
    """
    init_db()
    with get_db() as db:
        return db.execute(
            "SELECT id, username, display_name, role, status, can_set_marks, can_race_remotely,"
            " created_at, updated_at, last_login_at FROM users ORDER BY username"
        ).fetchall()


def get_user(user_id: int) -> Optional[sqlite3.Row]:
    """Load one user by username."""
    init_db()
    with get_db() as db:
        return db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def current_user() -> Optional[sqlite3.Row]:
    """Return the logged-in user row for the active session."""
    user_id = session.get("user_id")
    if not user_id:
        return None
    return get_user(int(user_id))


# User roles. Admin can change everything; race_officer can do everything a race
# needs but sees Settings read-only (settings-write routes are admin-only).
#
# mark_layer is deliberately much smaller than either: it reaches the phone page
# for re-measuring a mark and nothing else. The person who takes a RIB out after
# a storm is often neither an administrator nor the duty race officer, and giving
# them a race-officer login to press one button would hand them the start
# sequence, the finish times and the results as well.
USER_ROLES = ("admin", "race_officer", "mark_layer")


def normalize_role(role: str) -> str:
    """Coerce a submitted role string to one of the known roles."""
    role = (role or "").strip().lower()
    return role if role in USER_ROLES else "race_officer"


def user_is_admin(user: Optional[sqlite3.Row]) -> bool:
    """Return True when the given user row has the admin role."""
    return bool(user and (user["role"] or "").strip().lower() == "admin")


def current_user_is_admin() -> bool:
    """Return True when the logged-in user is an administrator."""
    return user_is_admin(current_user())


def user_role(user: Optional[sqlite3.Row]) -> str:
    """The user's role, lowercased, or "" when there is no user."""
    if not user:
        return ""
    try:
        return (user["role"] or "").strip().lower()
    except (KeyError, IndexError, TypeError):
        return ""


def user_is_mark_layer(user: Optional[sqlite3.Row]) -> bool:
    """Return True for the role that may only re-measure marks."""
    return user_role(user) == "mark_layer"


def current_user_is_mark_layer() -> bool:
    """Return True when the logged-in user holds the mark-layer role."""
    return user_is_mark_layer(current_user())


def user_can_set_marks(user: Optional[sqlite3.Row]) -> bool:
    """Return True when this user may re-measure a mark's position.

    Administrators always may — they can already add and delete marks, so
    withholding the ability to correct one would be a strange line to draw.
    Mark layers always may too: it is the only thing the role exists to do, and a
    mark layer without the permission could log in and reach nothing at all.
    Everyone else needs the per-user permission.
    """
    if user_is_admin(user) or user_is_mark_layer(user):
        return True
    try:
        return bool(user and user["can_set_marks"])
    except (KeyError, IndexError, TypeError):
        return False


def current_user_can_set_marks() -> bool:
    """Return True when the logged-in user may re-measure a mark's position."""
    return user_can_set_marks(current_user())


def user_can_race_remotely(user: Optional[sqlite3.Row]) -> bool:
    """Return True when this user may run racing from the water.

    Nobody holds this by virtue of a role, administrators included. Every other
    permission in the app is about what someone may change; this one is about
    starting a race when there is no race officer at the line to see it, which
    the club decides per person rather than per role.
    """
    try:
        return bool(user and user["can_race_remotely"])
    except (KeyError, IndexError, TypeError):
        return False


def current_user_can_race_remotely() -> bool:
    """Return True when the logged-in user may run racing from the water."""
    return user_can_race_remotely(current_user())


def current_actor() -> str:
    """Who the activity log should credit for what is happening now.

    Resolved in one place because it is also what `core.raceadmin` and anything
    else that logs on a caller's behalf needs passing in: outside a request
    there is no session, and "system" is the honest answer.
    """
    try:
        return session.get("username") or "anonymous"
    except Exception:
        return "system"


def audit(action: str, details: str = "") -> None:
    """Write an activity-log entry attributed to the current logged-in user."""
    log_activity(action, details, user=current_actor())


# Endpoints only administrators may reach. Race officers get a read-only Settings
# page (and can download a backup but not restore one); these POST handlers reject
# them as a backstop. Restoring a backup overwrites data, so it is admin-only.
ADMIN_ONLY_ENDPOINTS = frozenset({
    "settings_save",
    "settings_user_add", "settings_user_password", "settings_user_delete",
    "hardware_test_horn", "settings_test_audio",
    "settings_video_ptz_login_test", "settings_video_ptz_test",
    "settings_video_r2_test", "settings_video_r2_retry", "settings_video_uploads_abandon",
    "settings_offsite_backup_now",
    "settings_branding_club_logo_upload", "settings_branding_club_logo_delete",
    "settings_branding_sponsor_upload", "settings_branding_sponsor_delete",
    "settings_weather_test",
    "settings_polar_upload", "settings_sail_chart_upload",
    "settings_polar_delete", "settings_sail_chart_delete",
    "backup_restore",
    # Read-only, but it names who did what: administrators only.
    "settings_activity_log",
    # Adding and deleting marks is admin-only: both change the set of marks that
    # every course sequence refers to. *Editing* one is not — see mark_edit, which
    # checks the Set marks permission itself. Somebody trusted to re-measure a mark
    # from the water can already move it, so withholding the ability to correct its
    # name or rounding radius from the same page would be a strange line to draw.
    "marks_add", "mark_delete",
    # Sending raw protocol commands to a tracker. The rest of the trackers page is
    # deliberately open to race officers — which boat carries which tracker is a
    # race-day job — but this reconfigures hardware on a boat at sea, and a mistyped
    # command can leave a unit that has to come off the boat to be recovered.
    "trackers_command", "trackers_command_replies",
})


def clear_stale_authenticated_session() -> bool:
    """Clear a logged-in browser session created by an older app version.

    The Flask signing key is intentionally persistent so an app restart does not
    invalidate every browser cookie.  A version marker in the authenticated
    session restores the expected update behaviour: once a new build is
    installed, race-office users must sign in again before using admin pages.
    Anonymous/public sessions are left alone.
    """
    if not session.get("user_id"):
        return False
    if session.get(SESSION_APP_VERSION_KEY) == APP_VERSION:
        return False
    session.clear()
    return True


def public_endpoint() -> bool:
    """Return whether an endpoint is allowed without login.

    Keep this list deliberately narrow. The Cloudflare Tunnel should expose the
    whole app service, while Flask keeps race-office/admin routes behind login.
    """
    endpoint = request.endpoint or ""
    public_endpoints = {
        "static",
        "public_root",
        "login",
        "competitor_current_race",
        # Read-only: answers one hash saying whether the landing page needs
        # re-rendering. No race data, no ids, nothing a caller does not already have.
        "competitor_home_state",
        "competitor_race",
        # The page that follows whichever race is current, and its state poll.
        # Same data as the two pinned ones directly above and below -- the only
        # difference is which race it resolves -- so it belongs on the same
        # footing. This list is an allow-list on purpose: a route added later is
        # private until somebody decides otherwise, and this one is that decision.
        "competitor_current_race_sheet",
        "competitor_current_race_state",
        "competitor_current_race_mobile",
        "competitor_race_mobile",
        "competitor_race_state",
        "competitor_race_positions",
        "competitor_race_track",
        "bar_display",
        "bar_display_state",
        # The television reporting that a replay has reached the end. Same
        # reasoning as the two above: it is the screen on the wall, signed in to
        # nothing. All it can do is stop the replay it names.
        "bar_replay_finished",
        "public_video_clip_file",
        "public_video_live_frame",
        "public_video_status",
        "public_branding_file",
        "api_branding_live",
        "api_course_leg_analysis",
        "api_custom_leg_analysis",
        "api_weather_current",
        "api_weather_history",
        "api_courses",
        "api_marks",
        "api_current_race_course",
        # Machine ingest: reachable without a login, but rejected unless it
        # carries the configured shared secret.
        "api_track_ingest",
    }
    if endpoint == "documentation_file" and (request.view_args or {}).get("slug") in PUBLIC_DOCUMENTATION_SLUGS:
        return True
    return endpoint in public_endpoints or request.path.startswith("/static/")


def is_safe_redirect_url(url: str) -> bool:
    """Only allow app-relative redirects such as /admin/races."""
    url = (url or "").strip()
    if not url or not url.startswith("/") or url.startswith("//") or "\\" in url:
        return False
    parsed = urlparse(url)
    return not parsed.scheme and not parsed.netloc


def csrf_token() -> str:
    """Return the per-session CSRF token, creating one when needed."""
    token = session.get("_csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["_csrf_token"] = token
    return str(token)


def request_csrf_token() -> str:
    """Read a CSRF token from form data or common AJAX headers."""
    return (
        request.form.get("_csrf_token")
        or request.headers.get("X-CSRFToken")
        or request.headers.get("X-CSRF-Token")
        or request.headers.get("X-CSRF")
        or ""
    )


CSRF_EXEMPT_ENDPOINTS = {
    # This public endpoint is a read-only calculator used by the competitor page.
    "api_custom_leg_analysis",
    # Traccar's position forwarder POSTs here from the relay. It has no session
    # and cannot carry a CSRF token; it authenticates with a shared secret
    # header instead (see api_track_ingest).
    "api_track_ingest",
    # The clubhouse television saying it has reached the end of a replay. It is
    # a screen on a wall, signed in to nothing, and the page it runs is public
    # so it has no session to carry a token in. What it can do is stop a replay
    # it names by id and nothing else -- less than anyone in the room could do by
    # walking over to the television.
    "bar_replay_finished",
}


@app.before_request
def protect_state_changing_requests():
    """Reject cross-site state-changing requests that do not carry a session token."""
    if request.method not in ("POST", "PUT", "PATCH", "DELETE"):
        return None
    if request.endpoint in CSRF_EXEMPT_ENDPOINTS:
        return None
    expected = session.get("_csrf_token")
    supplied = request_csrf_token()
    if expected and supplied and secrets.compare_digest(str(expected), str(supplied)):
        return None
    # The login form is submitted before the user is authenticated, so the only
    # threat it faces is login-CSRF (negligible here). A missing/stale session
    # token on login usually means the login page was served without a fresh
    # session cookie (e.g. an HTML page served from a browser/CDN cache) — not an
    # attack. Rather than dead-ending on a raw "Bad request", bounce back to a
    # fresh login page, which re-establishes the session cookie and token so the
    # user can simply sign in again.
    if request.endpoint == "login":
        flash("Your session had expired. Please sign in again.", "error")
        next_value = request.form.get("next", "").strip()
        return redirect(url_for("login", next=next_value or None))
    if request.is_json or "application/json" in request.headers.get("Accept", ""):
        return jsonify({"ok": False, "error": "CSRF token missing or invalid"}), 400
    return Response("Bad request: CSRF token missing or invalid", status=400)


LEGACY_ADMIN_PREFIXES = (
    "/recommend",
    "/series",
    "/races",
    "/race",
    "/boats",
    "/settings",
    "/hardware",
    "/marks",
    "/backup",
)


def legacy_admin_redirect():
    """Redirect old race-office page URLs into the /admin namespace.

    The old POST/API URLs are kept working for compatibility with existing
    forms, browser tabs and JavaScript calls, but human-facing GET pages now
    live under /admin.
    """
    if request.method != "GET":
        return None
    path = request.path.rstrip("/") or "/"
    if path in ("/",) or path.startswith(("/admin", "/public", "/static", "/api", "/video")):
        return None
    if path in ("/login", "/logout") or any(path == prefix or path.startswith(prefix + "/") for prefix in LEGACY_ADMIN_PREFIXES):
        target = "/admin" + request.path
        if request.query_string:
            target += "?" + request.query_string.decode("utf-8", errors="ignore")
        return redirect(target, code=302)
    return None


@app.before_request
def require_authenticated_user():
    """Require login for race-officer pages while leaving public pages open."""
    init_db()
    legacy_redirect = legacy_admin_redirect()
    if legacy_redirect is not None:
        return legacy_redirect
    if public_endpoint():
        return None
    if clear_stale_authenticated_session():
        if request.path.startswith(("/api/", "/admin/api/")) or request.accept_mimetypes.best == "application/json":
            return jsonify({"ok": False, "error": "Authentication required after app update"}), 401
        return redirect(url_for("login", next=request.full_path if request.query_string else request.path))
    if session.get("user_id"):
        return None
    if request.path.startswith(("/api/", "/admin/api/")) or request.accept_mimetypes.best == "application/json":
        return jsonify({"ok": False, "error": "Authentication required"}), 401
    return redirect(url_for("login", next=request.full_path if request.query_string else request.path))


@app.before_request
def require_admin_for_writes():
    """Block non-admins from admin-only routes (Settings and backup restore)."""
    if request.endpoint not in ADMIN_ONLY_ENDPOINTS:
        return None
    if current_user_is_admin():
        return None
    if request.path.startswith(("/api/", "/admin/api/")) or request.accept_mimetypes.best == "application/json":
        return jsonify({"ok": False, "error": "Administrator access required"}), 403
    flash("This action requires administrator access.", "error")
    redirect_endpoint = "backup_restore_page" if request.endpoint == "backup_restore" else "settings_page"
    return redirect(url_for(redirect_endpoint))


# The whole of the race-office app a mark layer may reach. An allow-list rather
# than a block-list: a block-list would silently admit every route added later,
# which for a role that exists to be small is the wrong way round.
MARK_LAYER_ENDPOINTS = frozenset({
    "marks_ping_page",       # the one page the role is for
    "mark_set_position",     # and the one thing that page does
    "account_page",          # their own password, nobody else's
    "login", "logout",
    "static",
})


@app.before_request
def confine_mark_layer():
    """Keep mark layers on the phone page and off the rest of the app.

    They are sent back to the ping page rather than shown an error: on a phone in
    a RIB, a stray tap on a bookmark should land somewhere useful rather than on
    a dead end, and there is only one page for them to be on.
    """
    if not session.get("user_id"):
        return None
    if public_endpoint():
        return None
    if request.endpoint in MARK_LAYER_ENDPOINTS:
        return None
    if not current_user_is_mark_layer():
        return None
    if request.path.startswith(("/api/", "/admin/api/")) or request.accept_mimetypes.best == "application/json":
        return jsonify({"ok": False, "error": "This account may only set mark positions"}), 403
    return redirect(url_for("marks_ping_page"))


@app.context_processor
def inject_helpers():
    """Expose common formatting helpers to Jinja templates."""
    return {
        "COURSE_BY_NO": appstate.COURSE_BY_NO,
        "MARKS": appstate.MARKS,
        "dt_display": dt_display,
        "date_display": date_display,
        "dt_full_display": dt_full_display,
        "dt_for_input": dt_for_input,
        "dt_for_minute_input": dt_for_minute_input,
        "seconds_display": seconds_display,
        "decimal_minutes_to_text": decimal_minutes_to_text,
        "format_minutes": format_minutes,
        "format_target": format_target,
        "ordinal_text": ordinal_text,
        "published_score_cell": published_score_cell,
        "publish_rating_text": publish_rating_text,
        "is_finish_assignable_event": is_finish_assignable_event,
        "entry_display_status": entry_display_status,
        "current_user": current_user(),
        "is_admin": current_user_is_admin(),
        "can_edit_settings": current_user_is_admin(),
        # The side menu only offers the on-the-water page to accounts that hold
        # the permission, so it is not a door everyone can see and not open.
        "can_race_remotely": current_user_can_race_remotely(),
        "csrf_token": csrf_token,
        # Every page that shows a live camera panel needs this: the public pages
        # and the race sheet's finish camera both offer the relay's stream.
        "public_live_stream_url": public_live_stream_url(),
        "app_version": APP_VERSION,
        # The public pages link back to the source. Defined here rather than
        # written into each footer, because two copies of one URL is one that
        # goes stale in whichever template nobody edited.
        "source_url": SOURCE_URL,
    }


def dashboard_current_race_status() -> Dict[str, Any]:
    """Return compact dashboard information about the current race sheet."""
    race = get_current_competitor_race()
    if not race:
        return {"race": None, "message": "No race sheets have been created yet."}
    entries = get_entries(int(race["id"]))
    racing_count = sum(1 for e in entries if e["status"] == "RACING")
    finished_count = sum(1 for e in entries if e["finish_time"] or e["status"] == "FINISHED")
    first_start = race_first_start_time(race)
    race_finished = bool(entries) and racing_count == 0
    postponed = postponement_flag(race)
    # One function, so the hut and the competitors are never told two different
    # things about the same race.
    status = race_status_label(race, entries)
    # The course board, so the dashboard shows the marks the fleet is sailing
    # rather than only the name of the race they are sailing them in. Shortened
    # if it has been, and EMPTY until somebody has chosen a course -- the same
    # rule the race sheet and the competitor page follow, and the reason it is
    # `course_set` being asked and not `course_no`, which a new race always has.
    board_marks: List[Dict[str, Any]] = []
    course_shortened = False
    if int(row_get(race, "course_set", 1) or 0):
        try:
            course = course_for_race(race)
            shorten_index = row_get(race, "shortened_at_index", None)
            course_shortened = shorten_index is not None and str(shorten_index) != ""
            if course_shortened:
                course = apply_course_shortening(course, shorten_index)
            board_marks = list(course.get("board_marks") or course.get("marks") or [])
        except Exception:
            # The dashboard is the page the hut leaves open all day; a course it
            # cannot read is a missing board, not a missing dashboard.
            board_marks = []
    return {
        "race": race,
        "postponed_flag": postponed,
        "postponement_ends_at": str(row_get(race, "postponement_ends_at", "") or ""),
        "first_start_time": first_start,
        "status": status,
        "entry_count": len(entries),
        "finished_count": finished_count,
        "racing_count": racing_count,
        "race_finished": race_finished,
        "board_marks": board_marks,
        "course_shortened": course_shortened,
        "admin_url": url_for("race_detail", race_id=int(race["id"])),
        "public_url": url_for("competitor_current_race"),
    }


def horn_connection_status() -> Dict[str, Any]:
    """Return a non-blocking horn connection summary for the dashboard."""
    cfg = hardware_config()
    configured = bool(str(cfg.get("serial_port") or "").strip())
    if not configured:
        message = "Simulation mode — no serial horn port configured."
    else:
        line = cfg.get("horn_line") or PROLOG_HORN_OUTPUT_LINE
        message = f"Configured on {cfg.get('serial_port')} using {line}."
        if cfg.get("horn_input_enabled"):
            message += " Manual input sensing enabled."
    return {"ok": configured, "configured": configured, "message": message, "config": cfg}


def public_race_card(race: sqlite3.Row, current_race_id: Optional[int] = None) -> Dict[str, Any]:
    """Return compact public-list information for one race."""
    entries = get_entries(int(race["id"]))
    racing_count = sum(1 for e in entries if e["status"] == "RACING")
    finished_count = sum(1 for e in entries if e["finish_time"] or e["status"] == "FINISHED")
    first_start = race_first_start_time(race)
    race_finished = bool(entries) and racing_count == 0
    postponed = postponement_flag(race)
    status = race_status_label(race, entries)
    return {
        "race": race,
        "postponed_flag": postponed,
        "postponement_ends_at": str(row_get(race, "postponement_ends_at", "") or ""),
        "first_start_time": first_start,
        "status": status,
        "entry_count": len(entries),
        "finished_count": finished_count,
        "racing_count": racing_count,
        "is_current": current_race_id is not None and int(race["id"]) == int(current_race_id),
        "public_url": url_for("competitor_race", race_id=int(race["id"])),
    }


def public_year_race_groups(current_race: Optional[sqlite3.Row], current_series_id: Optional[int]) -> List[Dict[str, Any]]:
    """Group this calendar year's races by series for the public Races tab.

    Every series is rolled up (a closed <details>) except the current one, so a
    season's worth of racing stays scannable on a phone. Races with no series
    are collected into a final group. Groups run most-recent-first, matching
    what a competitor arriving on race day wants at the top.
    """
    year = str(datetime.now().year)
    init_db()
    with get_db() as db:
        races = db.execute(
            """
            SELECT * FROM races
            WHERE substr(COALESCE(NULLIF(start_time, ''), created_at), 1, 4) = ?
            ORDER BY id
            """,
            (year,),
        ).fetchall()
    races = races_in_order(races)
    current_race_id = int(current_race["id"]) if current_race else None
    # One query for every series, not one per roll-up: this page is left open
    # all day in the clubhouse and on phones on the water.
    published = resultspublish.latest_published_by_series()
    groups: Dict[Any, Dict[str, Any]] = {}
    for race in races:
        series_id = row_get(race, "series_id", None) or None
        key = int(series_id) if series_id else None
        group = groups.get(key)
        if group is None:
            series = get_series(key) if key else None
            group = {
                "series": series,
                "series_id": key,
                "title": series["name"] if series else "Races not in a series",
                "cards": [],
                "is_current_series": bool(key and current_series_id and int(key) == int(current_series_id)),
                "has_current_race": False,
                "published": None,
                "last_date": "",
            }
            groups[key] = group
        card = public_race_card(race, current_race_id)
        group["cards"].append(card)
        group["published"] = published.get(key)
        group["has_current_race"] = group["has_current_race"] or bool(card["is_current"])
        group["last_date"] = str(row_get(race, "start_time", "") or row_get(race, "created_at", "") or "")
    ordered = sorted(groups.values(), key=lambda g: (g["is_current_series"] or g["has_current_race"], g["last_date"]), reverse=True)
    for group in ordered:
        group["race_count"] = len(group["cards"])
        # The current series (or whichever group holds the current race) opens
        # expanded; everything else stays rolled up.
        group["open"] = bool(group["is_current_series"] or group["has_current_race"])
    return ordered


def public_competitor_home_context(mobile_view: bool = False) -> Dict[str, Any]:
    """Build the public landing page for competitors.

    The landing page is deliberately read-only and is intended as the default
    internet-facing page at the club URL.  It gives competitors a simple route
    to the active race, the current series race list, hut wind and optional
    camera preview without exposing race-office controls.
    """
    current_race = get_current_competitor_race()
    current_series = get_series(row_get(current_race, "series_id")) if current_race and row_get(current_race, "series_id") else None
    if current_series:
        races = get_series_races(int(current_series["id"]))
        race_list_title = f"Races in {current_series['name']}"
    elif current_race:
        races = [current_race]
        race_list_title = "Current race"
    else:
        races = []
        race_list_title = "Races"
    weather_status = weather_runtime_status()
    latest_wind = weather_status.get("sample") or weather_status.get("latest") or latest_weather_sample()
    video_status = video_runtime_status()
    race_cards = [public_race_card(r, int(current_race["id"]) if current_race else None) for r in races]
    current_card = next((card for card in race_cards if card.get("is_current")), None)
    return {
        "current_race": current_race,
        "current_series": current_series,
        "race_list_title": race_list_title,
        "race_cards": race_cards,
        "race_year": datetime.now().year,
        "race_groups": public_year_race_groups(current_race, int(current_series["id"]) if current_series else None),
        "current_race_card": current_card,
        "weather_status": weather_status,
        "latest_wind": latest_wind,
        "video_status": video_status,
        "public_live_image": public_live_image_context(),
        "public_branding": branding_assets_for_template(),
        "public_live_stream_url": public_live_stream_url(),
        "mobile_view": mobile_view,
    }


@app.errorhandler(404)
def handle_not_found(error):
    """Redirect a trailing-slash URL to its slash-less form before 404ing.

    Flask routes here are registered without a trailing slash (e.g. /admin), and
    Werkzeug does not auto-redirect /admin/ -> /admin, so a stray trailing slash
    a user types or pastes would otherwise dead-end on a bare 404. Retry once
    without the trailing slash; if that still doesn't match, the real 404 shows.
    """
    path = request.path
    if len(path) > 1 and path.endswith("/"):
        target = path.rstrip("/")
        if request.query_string:
            target += "?" + request.query_string.decode("utf-8", errors="ignore")
        return redirect(target, code=308)
    return error


def ensure_pursuit_start_videos_scheduled(race: sqlite3.Row) -> None:
    """Schedule one start-line clip per distinct boat start time for a pursuit race."""
    if not race or not video_config().get("video_enabled"):
        return
    entries = get_entries(int(race["id"]))
    for idx, start_dt in enumerate(pursuit_start_signal_times(race, entries), start=1):
        schedule_video_clip(int(race["id"]), "start", start_dt.isoformat(timespec="seconds"), label=f"Start {idx} video")


def pursuit_signal_plan_rows(race: sqlite3.Row, entries: List[sqlite3.Row]) -> List[Dict[str, Any]]:
    """Build the start-console signal plan for a pursuit race.

    The first start uses the normal RRS-26 sequence (warning/prep/one-minute/start,
    class 1 flags). Each subsequent boat start time is a single start signal, then
    the single finish signal.
    """
    rows = list(start_signal_plan_rows(race))  # first-start sequence (class 1) when timing is set
    first_start = race_first_start_dt(race)
    boats_by_iso: Dict[str, List[str]] = {}
    for entry in entries:
        start_dt = parse_dt(row_get(entry, "start_time_override"))
        if start_dt:
            boats_by_iso.setdefault(start_dt.isoformat(timespec="seconds"), []).append(entry["boat_name"])
    for start_dt in pursuit_start_signal_times(race, entries):
        if first_start and start_dt == first_start:
            continue  # the first start is already covered by the RRS sequence above
        iso = start_dt.isoformat(timespec="seconds")
        names = ", ".join(boats_by_iso.get(iso, []))
        rows.append({
            "signal_time": iso, "signal_dt": start_dt,
            "start_names": f"Start — {names}" if names else "Start",
            "classes_text": "", "displayed_flags": "—", "action": "Start signal", "horn": "1 sound",
        })
    for ann in pursuit.pursuit_next_start_announcements(race, entries):
        rows.append({
            "signal_time": ann["dt"].isoformat(timespec="seconds"), "signal_dt": ann["dt"],
            "start_names": "Announcement", "classes_text": "", "displayed_flags": "—",
            "action": "Central audio: " + ann["text"], "horn": "—",
        })
    finish_dt = pursuit_finish_dt(race)
    if finish_dt:
        rows.append({
            "signal_time": finish_dt.isoformat(timespec="seconds"), "signal_dt": finish_dt,
            "start_names": "Finish", "classes_text": "", "displayed_flags": "—",
            "action": "Finish signal", "horn": "1 sound",
        })
    rows.sort(key=lambda r: (r.get("signal_dt") is None, r.get("signal_dt") or datetime.max))
    return rows


def pursuit_start_clip_by_entry(entries: List[sqlite3.Row], clips: List[sqlite3.Row]) -> Dict[int, sqlite3.Row]:
    """Map each pursuit entry id to its start-video clip, matched by start time.

    A pursuit boat's start video is the start clip scheduled at that boat's start
    time; boats sharing an exact start time share the clip.
    """
    clip_by_time: Dict[str, sqlite3.Row] = {}
    for clip in clips:
        if str(row_get(clip, "clip_type") or "") == "start":
            clip_by_time.setdefault(str(row_get(clip, "event_time") or ""), clip)
    out: Dict[int, sqlite3.Row] = {}
    for entry in entries:
        start_iso = str(row_get(entry, "start_time_override") or "")
        if start_iso and start_iso in clip_by_time:
            out[int(entry["id"])] = clip_by_time[start_iso]
    return out


def _render_pursuit_race_detail(race: sqlite3.Row):
    """Render the pursuit-race sheet (its own simplified template)."""
    race_id = int(race["id"])
    ensure_pursuit_start_videos_scheduled(race)
    entries = get_entries(race_id)
    entry_boats = get_boats_by_id(e["boat_id"] for e in entries)
    rating_type = pursuit_rating_type(race)
    start_rows = pursuit.pursuit_display_rows(race, entries, entry_boats, rating_type)
    missing_rating = [r["entry"]["boat_name"] for r in start_rows if r["no_rating"]]
    timing_ready = bool(race_first_start_dt(race) and pursuit.pursuit_duration_seconds(race))
    boats = search_boats(limit=400)
    fleets = sorted({(b["class_name"] or "") for b in boats if b["class_name"]})
    finished_count = sum(1 for e in entries if e["finish_time"] or e["status"] == "FINISHED")
    racing_count = sum(1 for e in entries if e["status"] == "RACING")
    race_finished = bool(entries) and racing_count == 0
    # Finishing order (positions) for the results view.
    ranked = [e for e in entries if row_get(e, "pursuit_position")]
    ranked.sort(key=lambda e: int(e["pursuit_position"]))
    polar_files = list_polar_files()
    selected_polar = request.args.get("polar_file") or race_saved_polar(race, polar_files)
    course = course_for_race(race)
    is_custom_course = bool(custom_course_from_race(race))
    video_maps = video_clip_maps(race_id)
    start_video_by_entry = pursuit_start_clip_by_entry(entries, video_maps["clips"])
    series_list = list_series()
    current_series = get_series(race["series_id"]) if "series_id" in race.keys() and race["series_id"] else None
    delete_summary = race_delete_summary(race, entries, get_events(race_id, limit=100000), video_maps["clips"])
    # A pursuit race can be shortened like any other (the shorten handler does not
    # refuse one), and the public page shows Code flag S when it has been. This page
    # never received the flag, so the race officer saw nothing while competitors saw S.
    shorten_index = row_get(race, "shortened_at_index", None)
    course_shortened = shorten_index is not None and str(shorten_index) != ""
    return render_template(
        "race_pursuit.html",
        race=race, entries=entries, entry_boats=entry_boats, start_rows=start_rows,
        missing_rating=missing_rating, ranked_entries=ranked, timing_ready=timing_ready,
        rating_type=rating_type, duration_min=row_get(race, "pursuit_duration_min", None),
        first_warning_time=str(row_get(race, "start_time", "") or ""), first_start_time=race_first_start_time(race),
        finish_time=(pursuit_finish_dt(race).isoformat(timespec="seconds") if pursuit_finish_dt(race) else ""),
        courses=appstate.COURSES, course=course, is_custom_course=is_custom_course,
        course_set=bool(row_get(race, "course_set", 1)), selected_polar=selected_polar,
        boats=boats, all_active_count=len(boats), fleets=fleets,
        finished_count=finished_count, racing_count=racing_count, race_finished=race_finished,
        course_shortened=course_shortened, postponed_flag=postponement_flag(race),
        postponement_ends_at=str(row_get(race, "postponement_ends_at", "") or ""),
        events=get_events(race_id, limit=None), config=hardware_config(),
        signal_panel_schedule=signal_panel_schedule(race), signal_plan_rows=pursuit_signal_plan_rows(race, entries),
        video_status=video_runtime_status(), start_video_by_entry=start_video_by_entry, video_clip_link_text=video_clip_link_text,
        series_list=series_list, current_series=current_series, status_choices=PURSUIT_STATUS_CHOICES,
        public_links=public_race_links(race, ""), race_delete_summary=delete_summary, now_ts=int(time.time()),
    )


def get_current_competitor_race() -> Optional[sqlite3.Row]:
    """Choose the current race for the public competitor page."""
    init_db()
    with get_db() as db:
        # Prefer the latest race that still has boats racing, then otherwise the
        # latest created/started race. This keeps /public/current useful at the hut.
        row = db.execute(
            """
            SELECT r.*, SUM(CASE WHEN e.status = 'RACING' THEN 1 ELSE 0 END) AS racing_count
            FROM races r
            LEFT JOIN entries e ON e.race_id = r.id
            GROUP BY r.id
            HAVING racing_count > 0
            ORDER BY COALESCE(NULLIF(r.start_time, ''), r.created_at) DESC, r.id DESC
            LIMIT 1
            """
        ).fetchone()
        if row:
            return db.execute("SELECT * FROM races WHERE id = ?", (row["id"],)).fetchone()
        return db.execute(
            "SELECT * FROM races ORDER BY COALESCE(NULLIF(start_time, ''), created_at) DESC, id DESC LIMIT 1"
        ).fetchone()


def public_render_signature(race: sqlite3.Row) -> str:
    """A hash of everything the public pages are *rendered* from and cannot refresh.

    The clubhouse display and the competitor pages keep their boats, board, clock, wind
    and flags up to date on their own. But the course chart, the sequence across the top,
    the start time the clock counts from and the start schedule the flags are driven by
    are all written into the page once, by the server. So a race officer who shortens the
    course, changes it, sets the start time or re-lays a mark changes nothing the page can
    see, and a television in the bar goes on showing the old race until somebody finds a
    keyboard. Both faults were reported from the hut within a day of each other.

    Deliberately narrower than competitor_race_state_signature: that one also covers
    entries, finishes and video, so it changes every time a boat crosses the line.
    Reloading the clubhouse display in the middle of a finish sequence would restart the
    map, the camera and the board cycle at the worst possible moment.
    """
    course = course_for_race(race)
    payload = {
        # The header, the clock, and the flag schedule all come from these.
        "name": row_get(race, "name", ""),
        "start_time": row_get(race, "start_time", ""),
        "start_plan_json": row_get(race, "start_plan_json", ""),
        "class_config": race_class_config(race),
        "start_plan": race_start_plan(race),
        # The chart and the sequence.
        "course_no": row_get(race, "course_no", None),
        "custom_course_json": row_get(race, "custom_course_json", None),
        "shortened_at_index": row_get(race, "shortened_at_index", None),
        "shortened_at_mark": row_get(race, "shortened_at_mark", ""),
        "shortened_at_time": row_get(race, "shortened_at_time", ""),
        # AP going up or coming down changes the flags, the clock and the
        # start time the clock counts from -- none of which the page can work
        # out for itself. Left out, the television in the bar would go on
        # counting down to a gun that is not coming.
        "postponed_at": row_get(race, "postponed_at", ""),
        "postponement_kind": row_get(race, "postponement_kind", ""),
        "postponement_ends_at": row_get(race, "postponement_ends_at", ""),
        "sequence": [(m.get("mark"), m.get("rounding")) for m in course.get("marks", [])],
        # A mark moved from the water changes where the chart draws it, and that is a
        # race-day event the marks_ping page exists for.
        "marks": {code: (md.get("lat"), md.get("lon")) for code, md in appstate.MARKS.items()},
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:16]


def public_home_signature() -> str:
    """A hash of what the competitor landing page is rendered from.

    That page had no state poll at all — it ticked its countdown and refreshed the wind,
    and never asked whether anything else had changed. So a race officer setting the first
    warning signal left every competitor's home page reading "Not set" with a dead clock
    until they reloaded, which is the one moment they are all looking at it.

    Covers the race that is current, what that race is rendered from, and the list of
    races on the page, so a race created or renamed shows up too.
    """
    current = get_current_competitor_race()
    with get_db() as db:
        races = db.execute("SELECT id, name, start_time FROM races ORDER BY id").fetchall()
    payload = {
        "current": int(current["id"]) if current else None,
        "current_render": public_render_signature(current) if current else "",
        "races": [(int(r["id"]), r["name"], r["start_time"]) for r in races],
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:16]


def competitor_race_state_signature(race: sqlite3.Row) -> Tuple[str, Dict[str, Any]]:
    """Return a compact signature for public-page changes that should reload the page.

    Wind is intentionally excluded because the course-analysis panel updates live
    without a full reload. Course, start, race summary and entry/result changes
    are included so competitor displays update quickly when the RO changes them.
    """
    course = course_for_race(race)
    entries = get_entries(int(race["id"]))
    current_series = get_series(race["series_id"]) if "series_id" in race.keys() and race["series_id"] else None
    # Include the class/start configuration in the public-page signature so
    # competitor displays reload when the RO changes class bands, race-specific
    # start overrides, or class-to-start assignments.
    effective_class_config = race_class_config(race)
    effective_start_plan = race_start_plan(race)
    racing_count = sum(1 for e in entries if e["status"] == "RACING")
    finished_count = sum(1 for e in entries if e["finish_time"] or e["status"] == "FINISHED")
    race_finished = bool(entries) and racing_count == 0
    video_clips = get_video_clips_for_race(int(race["id"]))
    state = {
        "race_id": int(race["id"]),
        "name": race["name"],
        "class_name": race["class_name"],
        "series_id": race["series_id"] if "series_id" in race.keys() else None,
        "series_name": current_series["name"] if current_series else None,
        "start_time": race["start_time"],
        "course_no": race["course_no"],
        "custom_course_json": race["custom_course_json"] if "custom_course_json" in race.keys() else None,
        "polar_file": race["polar_file"] if "polar_file" in race.keys() else None,
        "race_start_plan_json": row_get(race, "start_plan_json", ""),
        "effective_class_config": effective_class_config,
        "effective_start_plan": effective_start_plan,
        "course_sequence": [(m.get("mark"), m.get("rounding")) for m in course.get("marks", [])],
        # Shortening the course changed nothing in this signature, so the competitor
        # page and the clubhouse display went on showing the full course, no Code
        # flag S and no shorten-to-finish leg until somebody reloaded by hand.
        # course_sequence above cannot stand in for it: course_for_race returns the
        # *unshortened* course, and the truncation is applied separately for display.
        "shortened_at_index": row_get(race, "shortened_at_index", None),
        "shortened_at_mark": row_get(race, "shortened_at_mark", ""),
        "shortened_at_time": row_get(race, "shortened_at_time", ""),
        "entry_count": len(entries),
        "finished_count": finished_count,
        "racing_count": racing_count,
        "race_finished": race_finished,
        "video_clips": [
            {
                "id": c["id"],
                "entry_id": c["entry_id"],
                "event_id": c["event_id"],
                "clip_type": c["clip_type"],
                "status": c["status"],
                "file_path": c["file_path"],
                "public_status": row_get(c, "public_status"),
                "public_url": row_get(c, "public_url"),
                "updated_at": c["updated_at"],
            }
            for c in video_clips
        ],
        "entries": [
            {
                "id": e["id"],
                "boat_id": e["boat_id"],
                "boat_name": e["boat_name"],
                "sail_no": e["sail_no"],
                "class_name": e["class_name"],
                "status": e["status"],
                "finish_time": e["finish_time"],
                "rating": e["rating"] if "rating" in e.keys() else None,
                "rating_source": e["rating_source"] if "rating_source" in e.keys() else None,
                "manual_irc_rating": e["manual_irc_rating"] if "manual_irc_rating" in e.keys() else None,
                "manual_ytc_rating": e["manual_ytc_rating"] if "manual_ytc_rating" in e.keys() else None,
                "start_time_override": e["start_time_override"] if "start_time_override" in e.keys() else None,
            }
            for e in entries
        ],
    }
    signature = hashlib.sha256(json.dumps(state, sort_keys=True, default=str).encode("utf-8")).hexdigest()
    return signature, state


def api_mark_payload(code: str, rounding: str = "", token: str = "", parent_mark: str = "", marks: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Return one racing mark in a stable JSON shape for external apps.

    ``marks`` pins the position to a past race's — see core.marks.marks_as_of.
    """
    mark_code = str(code or "").strip().upper()
    md = (appstate.MARKS if marks is None else marks).get(mark_code) or {}
    payload: Dict[str, Any] = {
        "code": mark_code,
        "display": mark_display_code(mark_code),
        "name": md.get("name", ""),
        "lat": md.get("lat"),
        "lon": md.get("lon"),
        "lat_text": md.get("lat_text", ""),
        "lon_text": md.get("lon_text", ""),
        "buoy": md.get("buoy", ""),
        "top_mark": md.get("top_mark", ""),
    }
    if rounding:
        payload["rounding"] = rounding
    if token:
        payload["token"] = token
    if parent_mark:
        payload["parent_mark"] = parent_mark
        payload["parent_display"] = mark_display_code(parent_mark)
    if md.get("compound"):
        payload["compound"] = True
        payload["components"] = list(md.get("components") or [])
        payload["rounding_order"] = md.get("rounding_order") or {}
    if md.get("component_of"):
        payload["component_of"] = md.get("component_of")
    if md.get("hidden_from_picker"):
        payload["hidden_from_picker"] = True
    return payload


def api_course_mark_payload(item: Dict[str, Any], marks: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Return a parent course mark, including its expanded geometry components."""
    code = str(item.get("mark", "")).strip().upper()
    rounding = "starboard" if str(item.get("rounding", "port")).lower().startswith("s") else "port"
    token = str(item.get("token") or f"{code}{rounding[0]}")
    payload = api_mark_payload(code, rounding=rounding, token=token, marks=marks)
    components = compound_mark_components(code, rounding)
    payload["expanded_components"] = [api_mark_payload(component, rounding=rounding, token=token, parent_mark=code, marks=marks) for component in components]
    return payload


def api_expanded_course_point_payload(point: Dict[str, Any], marks: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Return one physical course point after compound-mark expansion."""
    code = str(point.get("mark", "")).strip().upper()
    rounding = str(point.get("rounding") or "")
    parent = str(point.get("parent_mark") or code).strip().upper()
    payload = api_mark_payload(code, rounding=rounding, token=str(point.get("token") or ""), parent_mark=parent, marks=marks)
    payload["sequence_mark"] = parent
    payload["sequence_display"] = mark_display_code(parent)
    payload["is_compound_component"] = bool(point.get("is_compound_component"))
    # A turning point, not a mark: the chart bends the leg there and draws nothing.
    payload["waypoint"] = bool(point.get("waypoint"))
    return payload


def current_race_course_payload(race: sqlite3.Row) -> Dict[str, Any]:
    """Build the public JSON payload used by external range/bearing displays."""
    course = course_for_race(race)
    entries = get_entries(int(race["id"]))
    current_series = get_series(row_get(race, "series_id")) if row_get(race, "series_id") else None
    racing_count = sum(1 for entry in entries if entry["status"] == "RACING")
    finished_count = sum(1 for entry in entries if entry["finish_time"] or entry["status"] == "FINISHED")
    race_finished = bool(entries) and racing_count == 0
    first_start = race_first_start_time(race)
    first_start_dt = parse_dt(first_start)
    if race_finished:
        race_status = "Finished"
    elif first_start_dt and first_start_dt > datetime.now():
        race_status = "Start sequence pending"
    elif racing_count > 0:
        race_status = "Racing"
    elif first_start_dt:
        race_status = "Started"
    else:
        race_status = "Awaiting start time"

    # Every position in this payload is the one this race was sailed to, so a
    # mark corrected since does not move the buoys under a race already run.
    at_marks = track.race_marks(race)
    course_marks = [api_course_mark_payload(item, marks=at_marks) for item in course.get("marks", [])]
    expanded_points = [api_expanded_course_point_payload(point, marks=at_marks) for point in expand_course_points(course)]
    legs = []
    # The race's own marks: a course drawn for a past race is measured
    # against the buoys it was sailed to, not wherever they are today.
    for leg in course_legs(course, marks=at_marks):
        legs.append({
            "from": leg.get("from"),
            "to": leg.get("to"),
            "from_mark": leg.get("from_mark"),
            "to_mark": leg.get("to_mark"),
            "from_parent_mark": leg.get("from_parent_mark"),
            "to_parent_mark": leg.get("to_parent_mark"),
            "to_rounding": leg.get("to_rounding", ""),
            "distance_nm": round(float(leg["distance_nm"]), 3) if leg.get("distance_nm") is not None else None,
            "bearing_deg": round(float(leg["bearing_deg"]), 1) if leg.get("bearing_deg") is not None else None,
        })

    return {
        "ok": True,
        "app": "Pwllheli Race Officer",
        "version": APP_VERSION,
        "race": {
            "id": int(race["id"]),
            "name": race["name"],
            "class_name": race["class_name"],
            "series_id": row_get(race, "series_id"),
            "series_name": current_series["name"] if current_series else None,
            "course_no": race["course_no"],
            "custom_course": bool(custom_course_from_race(race)),
            "rating_rule": race["rating_rule"],
            "start_time": race["start_time"],
            "first_start_time": first_start,
            "status": race_status,
            "entry_count": len(entries),
            "finished_count": finished_count,
            "racing_count": racing_count,
            "race_finished": race_finished,
            "public_urls": public_context_urls(race),
        },
        "course": {
            "source": course.get("source") or "fixed",
            "course_no": course.get("course_no"),
            "name": course.get("wind_label") or f"Course {course.get('course_no')}",
            "wind_label": course.get("wind_label"),
            "wind_bearing_deg": course.get("wind_bearing_deg"),
            "wind_range_deg": course.get("wind_range_deg"),
            "sequence_text": course.get("sequence_text") or course_sequence_text(course),
            "length_nm": course.get("length_nm"),
            "marks": course_marks,
            "expanded_marks": expanded_points,
            "legs": legs,
        },
        "marks": {code: api_mark_payload(code, marks=at_marks) for code in sorted(appstate.MARKS.keys(), key=mark_sort_key)},
    }


def race_wind_record(race: sqlite3.Row, entries: List[sqlite3.Row]) -> Optional[Dict[str, Any]]:
    """Average the hut wind over a race, from the warning signal to the last finish.

    Once everyone has stopped racing, the public race page shows this instead of
    the live wind: a finished race's chart and course analysis are a record of
    the conditions that were sailed in, not a forecast for a race under way.
    Returns None when the window or the samples inside it are not usable, in
    which case callers fall back to the live wind.
    """
    start_dt = race_first_warning_dt(race) or race_first_start_dt(race)
    if not start_dt:
        return None
    finish_dts = [parse_dt(row_get(entry, "finish_time", "")) for entry in entries]
    end_dt = max([dt for dt in finish_dts if dt], default=None)
    if not end_dt:
        # Nobody has a finish time (an all-DNF/RET race): close the window at the
        # last thing logged for the race rather than letting it run to "now".
        event_dts = [parse_dt(row_get(event, "event_time", "")) for event in get_events(int(race["id"]), limit=200)]
        end_dt = max([dt for dt in event_dts if dt], default=None)
    if not end_dt or end_dt <= start_dt:
        return None
    samples = weather_samples_between(start_dt.timestamp(), end_dt.timestamp())
    summary = summarise_wind_samples(samples)
    if not summary["count"] or (summary["twd"] is None and summary["tws"] is None):
        return None
    summary["from_iso"] = start_dt.isoformat(timespec="seconds")
    summary["to_iso"] = end_dt.isoformat(timespec="seconds")
    return summary


def _is_the_current_race(race: sqlite3.Row) -> bool:
    """Whether this race is the one the app calls current. Never raises: it only
    decides whether to offer a link, and a public page must render regardless."""
    try:
        current = get_current_competitor_race()
        return bool(current and int(current["id"]) == int(race["id"]))
    except Exception:
        return True


def competitor_race_context(race: sqlite3.Row, mobile_view: bool = False,
                            pinned: bool = True) -> Dict[str, Any]:
    """Build shared context for public competitor pages.

    ``pinned=False`` is the page that follows whichever race is current, the same
    distinction /bar and /bar/<id> already draw for the clubhouse television.
    """
    course = course_for_race(race)
    shorten_index = row_get(race, "shortened_at_index", None)
    course_shortened = shorten_index is not None and str(shorten_index) != ""
    if course_shortened:
        course = apply_course_shortening(course, shorten_index)
    entries = get_entries(int(race["id"]))
    polar_files = list_polar_files()
    selected_polar = request.args.get("polar_file") or race_saved_polar(race, polar_files)
    polar_path = resolve_polar_path(selected_polar)
    weather_status = weather_runtime_status()
    latest_wind = weather_status.get("sample") or weather_status.get("latest") or latest_weather_sample()
    twd = latest_wind.get("twd") if latest_wind else None
    tws = latest_wind.get("tws") if latest_wind else None
    racing_count = sum(1 for e in entries if e["status"] == "RACING")
    finished_count = sum(1 for e in entries if e["finish_time"] or e["status"] == "FINISHED")
    race_finished = bool(entries) and racing_count == 0
    # A finished race's chart and analysis are a record of the conditions raced
    # in, so they use the average wind over the race rather than the wind now.
    wind_record = race_wind_record(race, entries) if race_finished else None
    if wind_record:
        if wind_record.get("twd") is not None:
            twd = wind_record["twd"]
        if wind_record.get("tws") is not None:
            tws = wind_record["tws"]
    course_analysis = analyse_course_with_wind(course, twd, tws, polar_path,
                                               marks=track.race_marks(race))
    current_series = get_series(race["series_id"]) if "series_id" in race.keys() and race["series_id"] else None
    public_state_signature, _public_state = competitor_race_state_signature(race)
    video_maps = video_clip_maps(int(race["id"]))
    pursuit_race = is_pursuit_race(race)
    pursuit_ranked = []
    if pursuit_race:
        pursuit_ranked = sorted(
            [e for e in entries if row_get(e, "pursuit_position")],
            key=lambda e: int(e["pursuit_position"]),
        )
    return {
        "race": race,
        "is_pursuit": pursuit_race,
        "pursuit_start_rows": pursuit.pursuit_display_rows(race, entries, get_boats_by_id(e["boat_id"] for e in entries)) if pursuit_race else [],
        "pursuit_timing_ready": bool(pursuit_race and race_first_start_dt(race) and pursuit.pursuit_duration_seconds(race)),
        "pursuit_start_video_by_entry": pursuit_start_clip_by_entry(entries, video_maps["clips"]) if pursuit_race else {},
        "pursuit_rating_type": pursuit_rating_type(race) if pursuit_race else "",
        "pursuit_finish_time": (pursuit_finish_dt(race).isoformat(timespec="seconds") if (pursuit_race and pursuit_finish_dt(race)) else ""),
        "pursuit_ranked": pursuit_ranked,
        "first_warning_time": str(row_get(race, "start_time", "") or ""),
        "first_start_time": race_first_start_time(race),
        "start_schedule": race_start_schedule(race),
        "signal_panel_schedule": signal_panel_schedule(race),
        "course": course,
        "course_shortened": course_shortened,
        "postponed_flag": postponement_flag(race),
        "postponement_ends_at": str(row_get(race, "postponement_ends_at", "") or ""),
        "shorten_mark": row_get(race, "shortened_at_mark", "") or "",
        "entries": entries,
        "entry_class_labels": entry_class_labels_map(race_class_config(race), entries),
        "finished_count": finished_count,
        "racing_count": racing_count,
        "race_finished": race_finished,
        "current_series": current_series,
        "polar_files": polar_files,
        "selected_polar": polar_path.name,
        "latest_wind": latest_wind,
        "weather_status": weather_status,
        "wind_record": wind_record,
        "analysis_twd": twd,
        "course_analysis": course_analysis,
        "legs": course_analysis["legs_analysis"],
        "dual_results": compute_dual_results(race, entries) if race_finished else None,
        "start_video_clip": video_maps["start_clip"],
        "finish_video_by_entry": video_maps["by_entry"],
        "video_clip_link_text": video_clip_link_text,
        "public_video_clip_link_text": public_video_clip_link_text,
        "public_video_clip_is_viewable": public_video_clip_is_viewable,
        "public_video_clip_href": public_video_clip_href,
        "public_live_image": public_live_image_context(),
        "gps_tracking_enabled": track_config()["enabled"],
        "is_custom_course": bool(custom_course_from_race(race)),
        # So the competitor page does not name a course nobody has chosen.
        "course_set": bool(row_get(race, "course_set", 1)),
        "course_chart": {**course_chart_config(), **track.race_chart_line(race)},
        # The competitor chart and the bar display both draw from this, so a mark
        # corrected today must not move the buoys on a race sailed last month.
        "marks_data": track.race_marks(race),
        "public_state_signature": public_state_signature,
        "public_render_signature": public_render_signature(race),
        "public_urls": public_context_urls(race, polar_path.name, pinned=pinned),
        "pinned_race": pinned,
        # Only asked on a pinned page, and only so it can offer the way out: a
        # link shared last month, or a bookmark, leaves somebody on a race that
        # finished without anything on the page saying where the racing went.
        "is_current_race": _is_the_current_race(race) if pinned else True,
        "public_live_stream_url": public_live_stream_url(),
        "mobile_view": mobile_view,
    }


def _update_pursuit_race(race: sqlite3.Row):
    """Save Course & start edits for a pursuit race and recompute start times.

    The form is read here; the saving is `core.raceadmin.update_pursuit_settings`,
    which is also what the on-the-water assistant calls. It used to be written out
    here alone, which meant a pursuit created from a phone could never be given a
    start time.
    """
    race_id = int(race["id"])
    settings = raceadmin.RaceSettings(
        name=request.form.get("name", "").strip(),
        start_time=request.form.get("start_time", "").strip(),
        series_id=request.form.get("series_id", type=int) or None,
        notes=request.form.get("notes", "").strip(),
        course_no=request.form.get("course_no", type=int),
        pursuit_rating=request.form.get("pursuit_rating", ""),
        pursuit_duration_min=request.form.get("pursuit_duration_min", type=float),
    )
    try:
        with get_db() as db:
            result = raceadmin.update_pursuit_settings(db, race, settings,
                                                       actor=current_actor())
    except raceadmin.RaceValidationError as exc:
        flash(exc.message, "error")
        return redirect(url_for("race_detail", race_id=race_id) + "#tab-course")
    msg = "Pursuit race saved."
    if result.ready:
        msg += f" Start times computed for {result.assigned} boat(s) using {result.rating_type}."
    if result.warnings:
        msg += " No rating for: " + ", ".join(result.warnings) + "."
    flash(msg, "success")
    return redirect(url_for("race_detail", race_id=race_id) + "#tab-course")


PURSUIT_STATUS_CHOICES = ("RACING", "FINISHED", "DNF", "DNS", "DNC", "RET", "OCS", "DSQ")


def read_settings_form() -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Parse the large grouped Settings form into app and hardware config dictionaries."""
    listing_settings = {
        "irc_listing_url": request.form.get("irc_listing_url", IRC_LISTING_URL),
        "ytc_listing_url": request.form.get("ytc_listing_url", YTC_LISTING_URL),
        "public_live_stream_url": request.form.get("public_live_stream_url", ""),
        "offsite_backup_enabled": request.form.get("offsite_backup_enabled", "0"),
        "offsite_backup_bucket": request.form.get("offsite_backup_bucket", ""),
        "offsite_backup_prefix": request.form.get("offsite_backup_prefix", ""),
        "offsite_backup_hour": request.form.get("offsite_backup_hour", str(offsite.DEFAULT_OFFSITE_HOUR)),
        "offsite_backup_minute": request.form.get("offsite_backup_minute", str(offsite.DEFAULT_OFFSITE_MINUTE)),
        "offsite_backup_keep": request.form.get("offsite_backup_keep", str(offsite.DEFAULT_OFFSITE_KEEP)),
        "offsite_backup_sections": request.form.getlist("offsite_backup_sections"),
        "offsite_backup_passphrase": request.form.get("offsite_backup_passphrase", ""),
        "offsite_backup_passphrase_clear": request.form.get("offsite_backup_passphrase_clear", "0"),
        "weather_source": request.form.get("weather_source", "station"),
        "weather_enabled": "1" if request.form.get("weather_source", "station") == "station" else "0",
        "weather_station_url": request.form.get("weather_station_url", DEFAULT_WEATHER_STATION_URL),
        "weather_poll_seconds": request.form.get("weather_poll_seconds", "5"),
        "weather_wind_dir_offset": request.form.get("weather_wind_dir_offset", "0"),
        "weather_manual_twd": request.form.get("weather_manual_twd", ""),
        "weather_manual_tws": request.form.get("weather_manual_tws", ""),
        "video_enabled": request.form.get("video_enabled", "0"),
        "video_source_type": request.form.get("video_source_type", "usb"),
        "video_usb_source": repair_mojibake((request.form.get("video_usb_source_manual", "").strip() or request.form.get("video_usb_source", ""))),
        "video_rtsp_url": request.form.get("video_rtsp_url", ""),
        "video_preview_rtsp_url": request.form.get("video_preview_rtsp_url", ""),
        "video_recording_mode": request.form.get("video_recording_mode", "copy"),
        "video_copy_container": request.form.get("video_copy_container", "mp4"),
        "video_rtsp_timestamp_mode": request.form.get("video_rtsp_timestamp_mode", "camera"),
        "video_preview_size": request.form.get("video_preview_size", "native"),
        "video_preview_fps": request.form.get("video_preview_fps", "2"),
        "video_preview_jpeg_quality": request.form.get("video_preview_jpeg_quality", "3"),
        "video_preview_keyframes_only": request.form.get("video_preview_keyframes_only", "0"),
        "video_ffmpeg_path": request.form.get("video_ffmpeg_path", "ffmpeg"),
        "video_pre_seconds": request.form.get("video_pre_seconds", "60"),
        "video_post_seconds": request.form.get("video_post_seconds", "60"),
        "video_segment_seconds": request.form.get("video_segment_seconds", "5"),
        "video_buffer_minutes": request.form.get("video_buffer_minutes", "20"),
        "video_public_provider": request.form.get("video_public_provider", "off"),
        "video_public_quality": request.form.get("video_public_quality", "720p"),
        "video_startline_overlay_enabled": request.form.get("video_startline_overlay_enabled", "0"),
        "video_public_live_provider": request.form.get("video_public_live_provider", "local"),
        "video_public_live_interval_seconds": request.form.get("video_public_live_interval_seconds", "5"),
        "video_public_r2_account_id": request.form.get("video_public_r2_account_id", ""),
        "video_public_r2_bucket": request.form.get("video_public_r2_bucket", ""),
        "video_public_r2_access_key_id": request.form.get("video_public_r2_access_key_id", ""),
        "video_public_r2_secret_access_key": request.form.get("video_public_r2_secret_access_key", ""),
        "video_public_r2_secret_access_key_clear": request.form.get("video_public_r2_secret_access_key_clear", "0"),
        "video_public_r2_public_base_url": request.form.get("video_public_r2_public_base_url", ""),
        "video_public_r2_prefix": request.form.get("video_public_r2_prefix", "race-videos"),
        "public_branding_enabled": request.form.get("public_branding_enabled", "0"),
        "public_branding_club_logo_enabled": request.form.get("public_branding_club_logo_enabled", "0"),
        "ptz_enabled": request.form.get("ptz_enabled", "0"),
        "ptz_camera_url": request.form.get("ptz_camera_url", ""),
        "ptz_username": request.form.get("ptz_username", ""),
        "ptz_password": request.form.get("ptz_password", ""),
        "ptz_password_clear": request.form.get("ptz_password_clear", "0"),
        "ptz_auth_mode": request.form.get("ptz_auth_mode", "digest"),
        "ptz_channel": request.form.get("ptz_channel", "1"),
        "ptz_idle_preset": request.form.get("ptz_idle_preset", "1"),
        "ptz_recording_preset": request.form.get("ptz_recording_preset", "2"),
        "ptz_pre_start_seconds": request.form.get("ptz_pre_start_seconds", "30"),
        "course_chart_tile_url": request.form.get("course_chart_tile_url", DEFAULT_COURSE_CHART_TILE_URL),
        "course_chart_overlay_url": request.form.get("course_chart_overlay_url", DEFAULT_COURSE_CHART_OVERLAY_URL),
        "start_automation_horn_enabled": request.form.get("start_automation_horn_enabled", "0"),
        "start_automation_audio_enabled": request.form.get("start_automation_audio_enabled", "0"),
        "central_audio_rate": request.form.get("central_audio_rate", "185"),
        "central_audio_fast_rate": request.form.get("central_audio_fast_rate", "285"),
        "central_audio_vox_tone_enabled": request.form.get("central_audio_vox_tone_enabled", "0"),
        "central_audio_vox_lead_seconds": request.form.get("central_audio_vox_lead_seconds", "2"),
    }
    hardware_settings = {
        "serial_port": request.form.get("serial_port", ""),
        "horn_line": request.form.get("horn_line", "RTS"),
        "horn_active": request.form.get("horn_active") == "1",
        "horn_duration_ms": request.form.get("horn_duration_ms", "1200"),
        "horn_input_enabled": request.form.get("horn_input_enabled") == "1",
        "horn_input_line": request.form.get("horn_input_line", "CTS"),
        "horn_input_active": request.form.get("horn_input_active") == "1",
        "horn_input_poll_ms": request.form.get("horn_input_poll_ms", "250"),
        "vedirect_smartshunt_port": request.form.get("vedirect_smartshunt_port", ""),
        "vedirect_phoenix_port": request.form.get("vedirect_phoenix_port", ""),
        "vedirect_smartsolar_port": request.form.get("vedirect_smartsolar_port", ""),
        "power_sim_enabled": request.form.get("power_sim_enabled") == "1",
        "power_sample_seconds": request.form.get("power_sample_seconds", "30"),
        "power_retention_days": request.form.get("power_retention_days", "365"),
        "server_threads": request.form.get("server_threads", "8"),
        "server_connection_limit": request.form.get("server_connection_limit", "100"),
        "server_channel_timeout": request.form.get("server_channel_timeout", "120"),
        "track_enabled": request.form.get("track_enabled") == "1",
        "traccar_base_url": request.form.get("traccar_base_url", ""),
        "traccar_token": request.form.get("traccar_token", ""),
        "track_poll_seconds": request.form.get("track_poll_seconds", "5"),
        "track_retention_days": request.form.get("track_retention_days", "90"),
        "track_sim_enabled": request.form.get("track_sim_enabled") == "1",
        "gps_finish_horn": request.form.get("gps_finish_horn") == "1",
        "track_race_poll_enabled": request.form.get("track_race_poll_enabled") == "1",
        "track_rounding_radius_m": request.form.get("track_rounding_radius_m", "50"),
        "track_gate_reach_m": request.form.get("track_gate_reach_m", "750"),
        "track_ingest_secret": request.form.get("track_ingest_secret", ""),
        "hologram_enabled": request.form.get("hologram_enabled") == "1",
        "hologram_api_key": request.form.get("hologram_api_key", ""),
        "hologram_org_id": request.form.get("hologram_org_id", ""),
        "assistant_api_key": request.form.get("assistant_api_key", ""),
        "assistant_model": request.form.get("assistant_model", ""),
        "assistant_base_url": request.form.get("assistant_base_url", ""),
    }
    return listing_settings, hardware_settings


DOCUMENTATION_FILES = {
    "race-officer-guide": (
        "Pwllheli_Race_Officer_Series_Guide.pdf",
        "Race Officer's Guide",
        "Step-by-step: running a series of races and publishing results.",
    ),
    "reference-manual": (
        "Pwllheli_Race_Officer_Reference_Manual.pdf",
        "Reference Manual",
        "Full reference: Windows setup, horn/audio/RTSP camera/weather station hardware, Cloudflare, and every admin screen.",
    ),
    "competitor-guide": (
        "Pwllheli_Competitor_Guide.pdf",
        "Competitor's Guide",
        "What competitors see on the public race-day pages.",
    ),
    "relay-guide": (
        "Pwllheli_Relay_Guide.pdf",
        "Relay & Front Door Guide",
        "The machine the club is reached through: front door, live camera, GPS tracking and visitor logging.",
    ),
}
PUBLIC_DOCUMENTATION_SLUGS = {"competitor-guide"}


# ---------------------------------------------------------------------------
# Route modules split out of app.py. Imported last so every shared helper and
# request hook above is defined first; each module registers its views on the
# shared ``app`` under their original endpoint names.
# ---------------------------------------------------------------------------
from routes import media  # noqa: E402,F401
from routes import race_console  # noqa: E402,F401
from routes import race_entries  # noqa: E402,F401
from routes import race_course  # noqa: E402,F401
from routes import race  # noqa: E402,F401
from routes import settings_actions  # noqa: E402,F401
from routes import settings  # noqa: E402,F401
from routes import trackers  # noqa: E402,F401
from routes import pages  # noqa: E402,F401
from routes import backup  # noqa: E402,F401
from routes import marks  # noqa: E402,F401
from routes import boats  # noqa: E402,F401
from routes import series  # noqa: E402,F401
from routes import api  # noqa: E402,F401
from routes import competitor  # noqa: E402,F401
from routes import auth  # noqa: E402,F401
from routes import assistant  # noqa: E402,F401


if __name__ == "__main__":
    init_db()
    start_weather_background_poller()
    start_video_background_recorder()
    start_video_watchdog()
    start_public_live_r2_uploader()
    start_central_audio_worker()
    start_start_sequence_scheduler()
    start_power_monitor_worker()
    start_track_monitor_worker()
    start_offsite_backup_worker()

    from waitress import serve

    host = os.environ.get("RO_HOST", "0.0.0.0")
    port = int(os.environ.get("RO_PORT", "5050"))
    # Sizing comes from Settings, defaulting to the environment. Waitress reads
    # these once, here, so a change on the settings page needs a restart — the
    # page says so, and says what the running process actually started with.
    #
    # connection_limit was previously left at the Waitress default of 100. It
    # counts open sockets rather than active requests, and a browser holds up to
    # six per origin, so a race with sixteen people watching could exhaust it
    # while every request was fast.
    srv = hardware_config()
    threads = int(srv.get("server_threads") or 8)
    connection_limit = int(srv.get("server_connection_limit") or 100)
    channel_timeout = int(srv.get("server_channel_timeout") or 120)
    record_server_runtime(threads, connection_limit, channel_timeout)
    print(f"Pwllheli Race Officer {APP_VERSION} serving with Waitress on http://{host}:{port} "
          f"({threads} threads, {connection_limit} connections, {channel_timeout}s idle timeout)",
          flush=True)
    serve(app, host=host, port=port, threads=threads,
          connection_limit=connection_limit, channel_timeout=channel_timeout)
