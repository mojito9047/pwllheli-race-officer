"""Race result and series scoring builders.

Extracted verbatim from app.py. Computes per-race IRC/YTC corrected-time result
tables (including per-class start times from the start schedule), the dual
IRC+YTC result groups, and the full series standings (low-point scoring, DNC
back-fill, discards per the series profile, RRS A8 tie-breaks) via the pure
rules in core.scoring. Reads races/entries/boats through core.races and the
class configuration through core.classconfig.
"""
from __future__ import annotations

import re
import sqlite3
import unicodedata
from urllib.parse import quote
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple

from core.classconfig import (
    class_for_rating_value,
    class_rules_for_type,
    discard_profile_help_text,
    race_class_config,
    race_start_schedule,
    series_class_config,
    series_discard_profile,
    series_discards_for_race_count,
    series_min_races_to_constitute,
    start_for_class,
    start_for_class_from_schedule,
)
from core.db import get_db, row_get
from core.races import (
    get_boats_by_id,
    get_entries,
    get_series,
    get_series_races,
    race_first_start_dt,
    race_first_start_time,
)
from core.scoring import (
    DEFAULT_DISCARD_PROFILE,
    DEFAULT_MIN_RACES_TO_CONSTITUTE,
    NON_EXCLUDABLE_STATUS_CODES,
    assign_low_point_race_ranks,
    choose_discard_indexes,
    corrected_seconds_for_result,
    is_non_excludable_score,
    rating_formula_for_type,
    rating_label_for_type,
    rounded_seconds_for_scoring,
    series_rank_key,
)
from core.timeutils import clean_sail_no, dt_full_display, normalise_key, parse_dt, seconds_display

def result_start_time_for_class_from_schedule(
    race: sqlite3.Row,
    entry: sqlite3.Row,
    class_name: Optional[str],
    schedule: List[Dict[str, Any]],
) -> str:
    """Return the start time to use for a result row from a precomputed schedule."""
    override = row_get(entry, "start_time_override")
    if override:
        return str(override)
    start = start_for_class_from_schedule(schedule, class_name)
    if start and start.get("time"):
        return str(start["time"])
    return race_first_start_time(race)


def result_start_time_for_class(race: sqlite3.Row, entry: sqlite3.Row, class_name: Optional[str]) -> str:
    """Return the start time to use for a result row."""
    return result_start_time_for_class_from_schedule(race, entry, class_name, race_start_schedule(race))


# ---------------------------------------------------------------------------
# Race results and series scoring
# ---------------------------------------------------------------------------
def compute_results(race: sqlite3.Row, entries: Iterable[sqlite3.Row]) -> List[Dict[str, Any]]:
    """Legacy single-rating result view used for editing/manual entries.

    The official race page now shows separate IRC and YTC result tables with the
    correct formula for each rating system.  This function is retained as a
    simple, backwards-compatible view of the rating stored on each entry.
    """
    rows: List[Dict[str, Any]] = []
    for e in entries:
        start_dt = parse_dt(e["start_time_override"]) or race_first_start_dt(race)
        finish_dt = parse_dt(e["finish_time"])
        elapsed_seconds = None
        corrected_seconds = None
        if finish_dt and start_dt and e["status"] in ("FINISHED", "RACING"):
            elapsed_seconds = max(0.0, (finish_dt - start_dt).total_seconds())
            rating = e["rating"] if e["rating"] is not None else 1.0
            if race["rating_rule"] == "NONE":
                corrected_seconds = elapsed_seconds
            elif race["rating_rule"] == "YTC":
                # RYA YTC: Corrected Time = Elapsed Time * 1000 / YTC.
                # If an old/manual entry contains a decimal TCF-style value, retain
                # the historic TCF behaviour rather than producing a nonsensical time.
                corrected_seconds = elapsed_seconds * (1000.0 / float(rating)) if float(rating) > 10 else elapsed_seconds * float(rating)
            else:
                corrected_seconds = elapsed_seconds * float(rating)
        rows.append({
            "entry": e,
            "start_time": start_dt,
            "finish_time": finish_dt,
            "elapsed_seconds": elapsed_seconds,
            "corrected_seconds": corrected_seconds,
            "elapsed_text": seconds_display(elapsed_seconds),
            "corrected_text": seconds_display(corrected_seconds),
            "rank": None,
        })

    finishers = [r for r in rows if r["corrected_seconds"] is not None]
    finishers.sort(key=lambda r: r["corrected_seconds"])
    for rank, row in enumerate(finishers, start=1):
        row["rank"] = rank
    return rows


def entry_elapsed_seconds(race: sqlite3.Row, entry: sqlite3.Row, start_time_iso: Optional[str] = None) -> Tuple[Optional[float], Optional[datetime], Optional[datetime]]:
    """Calculate an entry's elapsed time, respecting entry overrides and class starts."""
    start_dt = parse_dt(row_get(entry, "start_time_override")) or parse_dt(start_time_iso or "") or race_first_start_dt(race)
    finish_dt = parse_dt(entry["finish_time"])
    if finish_dt and start_dt and entry["status"] in ("FINISHED", "RACING"):
        return max(0.0, (finish_dt - start_dt).total_seconds()), start_dt, finish_dt
    return None, start_dt, finish_dt


def rating_from_entry_for_result(entry: sqlite3.Row, boat: Optional[sqlite3.Row], result_type: str) -> Tuple[Optional[float], str]:
    """Return the rating to use for an IRC or YTC table, or None if missing."""
    result_type = result_type.upper()
    source_label = ""
    rating: Optional[float] = None

    if result_type == "IRC":
        if "manual_irc_rating" in entry.keys() and entry["manual_irc_rating"] is not None:
            rating = float(entry["manual_irc_rating"])
            source_label = "Race entry IRC TCC"
        elif entry["rating"] is not None and entry["rating_source"] and "IRC" in str(entry["rating_source"]).upper():
            rating = float(entry["rating"])
            source_label = entry["rating_source"] or "Race entry IRC TCC"
        elif boat is not None and boat["irc_rating"] is not None:
            # Backwards-compatible fallback for old entries that have not yet
            # been opened after the v0.57 migration.
            rating = float(boat["irc_rating"])
            source_label = "Boat database IRC TCC (fallback)"
    elif result_type == "YTC":
        if "manual_ytc_rating" in entry.keys() and entry["manual_ytc_rating"] is not None:
            rating = float(entry["manual_ytc_rating"])
            source_label = "Race entry YTC"
        elif entry["rating"] is not None and entry["rating_source"] and "YTC" in str(entry["rating_source"]).upper():
            rating = float(entry["rating"])
            source_label = entry["rating_source"] or "Race entry YTC"
        elif boat is not None and boat["ytc_rating"] is not None:
            # Backwards-compatible fallback for old entries that have not yet
            # been opened after the v0.57 migration.
            rating = float(boat["ytc_rating"])
            source_label = "Boat database YTC (fallback)"

    if rating is None or rating <= 0:
        return None, "Missing rating"
    return rating, source_label


def compute_rating_result_table(
    race: sqlite3.Row,
    entries: Iterable[sqlite3.Row],
    result_type: str,
    class_rule: Optional[Dict[str, Any]] = None,
    class_config: Optional[Dict[str, Any]] = None,
    start_schedule: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Build one ordered corrected-time result table for a race/rating/class."""
    result_type = result_type.upper()
    entries_list = list(entries)
    boats_by_id = get_boats_by_id(e["boat_id"] for e in entries_list)
    class_config = class_config if class_config is not None else race_class_config(race)
    start_schedule = start_schedule if start_schedule is not None else race_start_schedule(race)
    rating_class_rules = class_rules_for_type(class_config, result_type)
    class_name = str(class_rule.get("name") or "") if class_rule else ""
    start_item = start_for_class_from_schedule(start_schedule, class_name if class_name else None)
    rows: List[Dict[str, Any]] = []
    excluded: List[sqlite3.Row] = []

    for e in entries_list:
        boat = boats_by_id.get(int(e["boat_id"])) if e["boat_id"] is not None else None
        rating, rating_source = rating_from_entry_for_result(e, boat, result_type)
        if rating is None:
            if class_rule is None:
                excluded.append(e)
            continue
        assigned_rule = class_for_rating_value(class_config, result_type, rating) if rating_class_rules else None
        assigned_class_name = str(assigned_rule.get("name") or "") if assigned_rule else (result_type if not rating_class_rules else "")
        if class_rule is not None and assigned_class_name != class_name:
            continue
        if class_rule is None and rating_class_rules and not assigned_class_name:
            excluded.append(e)
            continue
        result_start_iso = result_start_time_for_class_from_schedule(race, e, assigned_class_name, start_schedule)
        elapsed_seconds, start_dt, finish_dt = entry_elapsed_seconds(race, e, result_start_iso)
        corrected_seconds = corrected_seconds_for_result(elapsed_seconds, rating, result_type)
        rows.append({
            "entry": e,
            "boat": boat,
            "rating": rating,
            "rating_source": rating_source,
            "result_class": assigned_class_name,
            "start_label": (start_for_class_from_schedule(start_schedule, assigned_class_name) or {}).get("name", "Start 1"),
            "start_time": start_dt,
            "finish_time": finish_dt,
            "elapsed_seconds": elapsed_seconds,
            "corrected_seconds": corrected_seconds,
            "elapsed_text": seconds_display(elapsed_seconds),
            "corrected_text": seconds_display(corrected_seconds),
            "rank": None,
        })

    assign_low_point_race_ranks(rows)
    rows.sort(key=lambda r: (r["rank"] is None, r["rank"] or 9999, normalise_key(r["entry"]["boat_name"] or "")))
    title_prefix = class_name if class_name else result_type
    start_note = ""
    if start_item and start_item.get("time"):
        start_note = f"{start_item.get('name')}: {dt_full_display(start_item.get('time'))}"
    return {
        "type": result_type,
        "class_name": class_name,
        "title": f"{title_prefix} results",
        "rating_label": rating_label_for_type(result_type),
        "formula": rating_formula_for_type(result_type),
        "start_note": start_note,
        "rows": rows,
        "excluded": excluded,
    }


def compute_pursuit_result_group(race: sqlite3.Row, entries: Iterable[sqlite3.Row], result_type: str) -> Dict[str, Any]:
    """Build a single result table for a pursuit race, ranked by the race
    officer's on-the-water finishing positions rather than corrected time.

    Boats appear in the IRC or YTC table when they carry that rating (so the
    pursuit result can feed either series standing); the finishing order itself
    is the same regardless of rating system.
    """
    result_type = result_type.upper()
    entries_list = list(entries)
    boats_by_id = get_boats_by_id(e["boat_id"] for e in entries_list)
    rows: List[Dict[str, Any]] = []
    excluded: List[Dict[str, Any]] = []
    for e in entries_list:
        boat = boats_by_id.get(int(e["boat_id"])) if e["boat_id"] is not None else None
        rating, rating_source = rating_from_entry_for_result(e, boat, result_type)
        if rating is None:
            excluded.append({"entry": e, "reason": "Missing " + rating_label_for_type(result_type)})
            continue
        position = row_get(e, "pursuit_position")
        rows.append({
            "entry": e, "boat": boat, "rating": rating, "rating_source": rating_source,
            "result_class": result_type, "start_label": "", "start_time": parse_dt(row_get(e, "start_time_override")),
            "finish_time": parse_dt(e["finish_time"]), "elapsed_seconds": None, "corrected_seconds": None,
            "elapsed_text": "", "corrected_text": "",
            "pursuit_position": int(position) if position else None,
            "rank": None, "points": None, "rank_text": "", "tie": False,
        })
    # Rank by finishing position; boats without a position stay unranked and are
    # scored by their status (DNF/DNS/...) at the series level.
    finishers = sorted([r for r in rows if r["pursuit_position"]], key=lambda r: r["pursuit_position"])
    for place, row in enumerate(finishers, start=1):
        row["rank"] = place
        row["points"] = float(place)
        row["rank_text"] = str(place)
    rows.sort(key=lambda r: (r["rank"] is None, r["rank"] or 9999, normalise_key(r["entry"]["boat_name"] or "")))
    table = {
        "type": result_type, "class_name": "", "title": f"{result_type} results (pursuit finishing order)",
        "rating_label": rating_label_for_type(result_type), "formula": "Pursuit finishing order (positions on the water)",
        "start_note": "", "rows": rows, "excluded": excluded,
    }
    return {
        "type": result_type.lower(), "title": f"{result_type} results",
        "rating_label": rating_label_for_type(result_type), "formula": "Pursuit finishing order",
        "tables": [table], "rows": rows, "excluded": excluded,
    }


def compute_rating_result_group(race: sqlite3.Row, entries: Iterable[sqlite3.Row], result_type: str) -> Dict[str, Any]:
    """Build classed result tables for one rating type."""
    if str(row_get(race, "race_type", "") or "").lower() == "pursuit":
        return compute_pursuit_result_group(race, entries, result_type)
    result_type = result_type.upper()
    entries_list = list(entries)
    class_config = race_class_config(race)
    start_schedule = race_start_schedule(race)
    rules = class_rules_for_type(class_config, result_type)
    if not rules:
        table = compute_rating_result_table(race, entries_list, result_type, None, class_config, start_schedule)
        return {
            "type": result_type.lower(),
            "title": f"{result_type} results",
            "rating_label": rating_label_for_type(result_type),
            "formula": rating_formula_for_type(result_type),
            "tables": [table],
            "rows": table["rows"],
            "excluded": table["excluded"],
        }

    class_tables = [compute_rating_result_table(race, entries_list, result_type, rule, class_config, start_schedule) for rule in rules]
    tables = list(class_tables)
    if race_type_classes_share_one_start(race, result_type, class_config):
        overall_table = compute_rating_result_table(race, entries_list, result_type, None, class_config, start_schedule)
        overall_table["title"] = f"{result_type} Overall results"
        overall_table["is_overall"] = True
        overall_table["start_note"] = overall_table.get("start_note") or "All classes share one start"
        tables = [overall_table] + tables
    rows = [row for table in class_tables for row in table["rows"]]

    # Group-level exclusions: missing rating, or rating present but outside every band.
    boats_by_id = get_boats_by_id(e["boat_id"] for e in entries_list)
    excluded: List[Dict[str, Any]] = []
    for e in entries_list:
        boat = boats_by_id.get(int(e["boat_id"])) if e["boat_id"] is not None else None
        rating, _source = rating_from_entry_for_result(e, boat, result_type)
        if rating is None:
            excluded.append({"entry": e, "reason": "Missing " + rating_label_for_type(result_type)})
        elif class_for_rating_value(class_config, result_type, rating) is None:
            excluded.append({"entry": e, "reason": f"{rating:g} is outside the configured {result_type} class bands"})

    return {
        "type": result_type.lower(),
        "title": f"{result_type} results",
        "rating_label": rating_label_for_type(result_type),
        "formula": rating_formula_for_type(result_type),
        "tables": tables,
        "rows": rows,
        "excluded": excluded,
    }


def compute_dual_results(race: sqlite3.Row, entries: Iterable[sqlite3.Row]) -> Dict[str, Dict[str, Any]]:
    """Build IRC and YTC result groups, split into configured classes when available."""
    entries_list = list(entries)
    return {
        "irc": compute_rating_result_group(race, entries_list, "IRC"),
        "ytc": compute_rating_result_group(race, entries_list, "YTC"),
    }

def series_competitor_key(entry: sqlite3.Row) -> str:
    """Return the stable key used to group a boat across series races."""
    if entry["boat_id"] is not None:
        return f"boat:{int(entry['boat_id'])}"
    sail = clean_sail_no(entry["sail_no"] or "")
    name = normalise_key(entry["boat_name"] or "")
    return f"manual:{sail}:{name}"


def series_competitor_label(entry: sqlite3.Row, boat: Optional[sqlite3.Row] = None) -> Dict[str, Any]:
    """Return the display name for a series competitor.

    The name and sail number come from the boat record where there is one, so a
    boat renamed mid-season reads the same down the whole series. The class does
    not: it is the entry's, snapshotted when the boat was entered. The boat's own
    free-text class retired with its field in v0.283, and a series scored under
    one label must not silently re-label its earlier races anyway.
    """
    return {
        "boat_name": (boat["boat_name"] if boat is not None else entry["boat_name"]),
        "sail_no": (boat["sail_no"] if boat is not None else entry["sail_no"]),
        "class_name": entry["class_name"],
        "boat_id": (boat["id"] if boat is not None else entry["boat_id"]),
    }


def select_result_table_from_group(group: Dict[str, Any], class_name: Optional[str] = None) -> Dict[str, Any]:
    """Select one table from a grouped result object."""
    tables = group.get("tables") or []
    if class_name:
        for table in tables:
            if str(table.get("class_name") or "") == str(class_name):
                return table
    return tables[0] if tables else {"rows": [], "excluded": [], "title": "Results"}


def race_type_classes_share_one_start(race: sqlite3.Row, result_type: str, class_config: Optional[Dict[str, Any]] = None) -> bool:
    """Return True when configured classes for one rating type share the same race start.

    This permits a combined Overall table across rating-band classes without
    mixing boats that were scheduled to start at different times.
    """
    class_config = class_config if class_config is not None else race_class_config(race)
    rules = class_rules_for_type(class_config, result_type)
    if len(rules) <= 1:
        return False
    schedule = race_start_schedule(race)
    start_times = []
    for rule in rules:
        class_name = str(rule.get("name") or "")
        start = start_for_class_from_schedule(schedule, class_name)
        start_times.append(str((start or {}).get("time") or race_first_start_time(race) or ""))
    start_times = [value for value in start_times if value]
    return bool(start_times) and len(set(start_times)) == 1


def series_races_type_classes_share_one_start(races: Iterable[sqlite3.Row], class_config: Dict[str, Any], result_type: str) -> bool:
    """Return True when every supplied race can have a combined class result.

    This helper lets the series scorer reuse the races and class config it has
    already loaded instead of re-querying them for each class table.
    """
    race_list = list(races)
    if len(class_rules_for_type(class_config, result_type)) <= 1:
        return False
    return bool(race_list) and all(race_type_classes_share_one_start(race, result_type, class_config) for race in race_list)


def series_type_classes_share_one_start(series_id: int, result_type: str) -> bool:
    """Return True when every race can safely have a combined class result."""
    series = get_series(series_id)
    class_config = series_class_config(series)
    races = get_series_races(series_id)
    return series_races_type_classes_share_one_start(races, class_config, result_type)


def race_ready_for_series_scoring(race: sqlite3.Row) -> bool:
    """Return True when a race should count as a series-scoring race.

    A race is held out of the series table while any entry is still RACING.
    That prevents unfinished races from giving provisional DNC/DNF-style scores
    and altering the series standings before the race is closed.
    """
    entries = get_entries(int(race["id"]))
    return bool(entries) and all(str(entry["status"] or "").upper() != "RACING" for entry in entries)


def series_race_result_group(
    race: sqlite3.Row,
    result_type: str,
    group_cache: Optional[Dict[Tuple[int, str], Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Return a cached classed race-result group for series-level calculations.

    A series page needs the same race result several times: once for each class
    table and again for any Overall table.  Reusing the grouped result avoids
    recalculating corrected times, class allocation and A7 ties for every class
    column on every page refresh.
    """
    key = (int(race["id"]), result_type.upper())
    if group_cache is not None and key in group_cache:
        return group_cache[key]
    entries = get_entries(int(race["id"]))
    group = compute_rating_result_group(race, entries, result_type)
    if group_cache is not None:
        group_cache[key] = group
    return group


def build_series_result_table(
    series_id: int,
    result_type: str,
    class_name: Optional[str] = None,
    *,
    series: Optional[sqlite3.Row] = None,
    races: Optional[List[sqlite3.Row]] = None,
    group_cache: Optional[Dict[Tuple[int, str], Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Aggregate Appendix A low-point scores for one series/rating/class table."""
    series = series if series is not None else get_series(series_id)
    races = races if races is not None else get_series_races(series_id)
    result_type = result_type.upper()
    race_items: List[Dict[str, Any]] = []
    competitors: Dict[str, Dict[str, Any]] = {}

    for race in races:
        # Do not include an in-progress race in series scoring.  The race only
        # becomes part of the series table once every entry has been moved away
        # from RACING, for example FINISHED, DNF, RET, DNS or DNC.
        if not race_ready_for_series_scoring(race):
            continue
        group = series_race_result_group(race, result_type, group_cache)
        table = select_result_table_from_group(group, class_name)
        # Include every completed race that had entries in this rating/class
        # table.  A race where nobody in the class finished still counts for the
        # series table: those boats receive DNC/DNF/RET-style scores rather than
        # the whole race disappearing from the column list.
        if not table.get("rows"):
            continue
        race_item = {"race": race, "table": table, "entries_by_key": {}, "race_competitors": len(table.get("rows", []))}
        for row in table.get("rows", []):
            entry = row["entry"]
            key = series_competitor_key(entry)
            race_item["entries_by_key"][key] = row
            competitors.setdefault(key, series_competitor_label(entry, row.get("boat")))
        race_items.append(race_item)

    scored_race_count = len(race_items)
    discard_count = series_discards_for_race_count(scored_race_count, series)
    dnc_points = len(competitors) + 1 if competitors else 0
    rows: List[Dict[str, Any]] = []

    for key, comp in competitors.items():
        scores: List[Dict[str, Any]] = []
        for item in race_items:
            race = item["race"]
            row = item["entries_by_key"].get(key)
            if row is None:
                points = float(dnc_points)
                scores.append({"race": race, "points": points, "code": "DNC", "rank": None, "discard": False, "non_excludable": False})
                continue
            entry = row["entry"]
            if row["rank"] is not None:
                points = float(row.get("points", row["rank"]))
                code = str(row.get("rank_text") or row["rank"])
            else:
                points = float(dnc_points)
                code = (entry["status"] or "DNF").upper()
            scores.append({
                "race": race,
                "points": points,
                "code": code,
                "rank": row["rank"],
                "discard": False,
                "non_excludable": str(code).upper() in NON_EXCLUDABLE_STATUS_CODES,
            })

        discard_indexes = choose_discard_indexes(scores, discard_count)
        kept_total = 0.0
        for i, score in enumerate(scores):
            score["discard"] = i in discard_indexes
            if not score["discard"]:
                kept_total += float(score["points"])

        # RRS A8.1: list non-excluded scores in order best to worst.  RRS A8.2:
        # if still tied, compare the last race, then the next-to-last, and so on;
        # these scores are used even if excluded.
        kept_best_to_worst = sorted(float(score["points"]) for score in scores if not score["discard"])
        latest_to_earliest = [float(score["points"]) for score in reversed(scores)]
        rows.append({
            "key": key,
            "competitor": comp,
            "scores": scores,
            "total": kept_total,
            "rank": None,
            "tie_break_a81": kept_best_to_worst,
            "tie_break_a82": latest_to_earliest,
        })

    rows.sort(key=lambda r: (series_rank_key(r), normalise_key(r["competitor"]["boat_name"] or "")))
    previous_key: Optional[Tuple[Any, ...]] = None
    previous_rank = 0
    for position, row in enumerate(rows, start=1):
        key_tuple = series_rank_key(row)
        if previous_key is not None and key_tuple == previous_key:
            row["rank"] = previous_rank
            row["tied"] = True
        else:
            row["rank"] = position
            row["tied"] = False
            previous_rank = position
            previous_key = key_tuple

    has_class_rules = bool(class_rules_for_type(series_class_config(series), result_type))
    if class_name:
        title = f"{result_type} {class_name} series"
        is_overall = False
    elif has_class_rules:
        title = f"{result_type} Overall series"
        is_overall = True
    else:
        title = f"{result_type} series"
        is_overall = False
    profile_text = series_discard_profile(series)
    min_races_to_constitute = series_min_races_to_constitute(series)
    return {
        "type": result_type,
        "class_name": class_name or "",
        "title": title,
        "is_overall": is_overall,
        "rating_label": rating_label_for_type(result_type),
        "races": [item["race"] for item in race_items],
        "rows": rows,
        "discard_count": discard_count,
        "race_count": scored_race_count,
        "min_races_to_constitute": min_races_to_constitute,
        "constituted": scored_race_count >= min_races_to_constitute,
        "dnc_points": dnc_points,
        "discard_profile": profile_text,
        "discard_profile_help": discard_profile_help_text(profile_text),
    }


def build_series_result_group(series_id: int, result_type: str) -> Dict[str, Any]:
    """Calculate series standings, split by configured classes when present."""
    result_type = result_type.upper()
    series = get_series(series_id)
    races = get_series_races(series_id)
    class_config = series_class_config(series)
    rules = class_rules_for_type(class_config, result_type)
    group_cache: Dict[Tuple[int, str], Dict[str, Any]] = {}

    def build_table(class_name: Optional[str] = None) -> Dict[str, Any]:
        return build_series_result_table(
            series_id,
            result_type,
            class_name,
            series=series,
            races=races,
            group_cache=group_cache,
        )

    if not rules:
        table = build_table()
        return {
            "type": result_type.lower(),
            "title": f"{result_type} series",
            "rating_label": rating_label_for_type(result_type),
            "tables": [table],
            "rows": table["rows"],
            "races": table["races"],
            "discard_count": table["discard_count"],
            "race_count": table["race_count"],
            "min_races_to_constitute": table.get("min_races_to_constitute", DEFAULT_MIN_RACES_TO_CONSTITUTE),
            "constituted": table["constituted"],
            "discard_profile": table.get("discard_profile", DEFAULT_DISCARD_PROFILE),
        }

    class_tables = [build_table(str(rule.get("name") or "")) for rule in rules]
    tables = list(class_tables)
    if series_races_type_classes_share_one_start(races, class_config, result_type):
        tables = [build_table(None)] + tables
    return {
        "type": result_type.lower(),
        "title": f"{result_type} series",
        "rating_label": rating_label_for_type(result_type),
        "tables": tables,
        "rows": [row for table in class_tables for row in table["rows"]],
        "races": sorted({int(r["id"]): r for table in tables for r in table["races"]}.values(), key=lambda r: (row_get(r, "start_time", ""), row_get(r, "id", 0))),
        "discard_count": max([table["discard_count"] for table in tables] or [0]),
        "race_count": max([table["race_count"] for table in tables] or [0]),
        "min_races_to_constitute": tables[0].get("min_races_to_constitute", DEFAULT_MIN_RACES_TO_CONSTITUTE) if tables else DEFAULT_MIN_RACES_TO_CONSTITUTE,
        "constituted": any(table["constituted"] for table in tables),
        "discard_profile": tables[0].get("discard_profile", DEFAULT_DISCARD_PROFILE) if tables else DEFAULT_DISCARD_PROFILE,
    }


def build_series_results(series_id: int) -> Dict[str, Dict[str, Any]]:
    """Calculate IRC and YTC low-point series standings."""
    return {
        "irc": build_series_result_group(series_id, "IRC"),
        "ytc": build_series_result_group(series_id, "YTC"),
    }


def html_id_slug(value: Any, fallback: str = "item") -> str:
    """Return a safe HTML id fragment for generated published-result sections."""
    text = re.sub(r"[^a-z0-9]+", "_", normalise_key(str(value or ""))).strip("_")
    return text or fallback


def export_filename(name: Any, what: str, ext: str,
                    when: Optional[datetime] = None, ascii_only: bool = True,
                    seconds: bool = False) -> str:
    """Name a downloaded file after what it holds and the moment it was taken.

    ``series_1_results_publish.html`` and ``race_9_irc_sailwave.csv`` told you
    nothing you wanted to know. These files are downloaded to be sent to somebody
    or filed, often several times as a season is scored, and the two things you
    need in the folder afterwards are which race or series it is and which run
    it was. ``name`` is whatever names it -- a series, or a series and a race.

    ASCII only, and hyphenated: the name goes into a Content-Disposition header
    and then onto whatever filesystem the browser is saving to. A Welsh series
    name loses its circumflexes here rather than arriving as mojibake -- the
    header also carries the real name in the RFC 5987 ``filename*`` field, which
    every current browser prefers.
    """
    # Minutes are enough for a download somebody is about to look at. Seconds
    # are for a key that must be unique: two publishes a minute apart would
    # otherwise write the same object twice and leave the Published list showing
    # two entries that are one file.
    stamp = (when or datetime.now()).strftime("%Y-%m-%d %H%M%S" if seconds else "%Y-%m-%d %H%M")
    text = str(name or "").strip()
    if ascii_only:
        text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    # ONE slugging rule for both forms, so the only difference between them is
    # whether the accents survived. Slugging them differently -- collapsing
    # punctuation in the ASCII form and not in the other -- made the two differ
    # for a plain ASCII name, so the header carried a filename* that disagreed
    # with its filename, and the browser preferred the worse of the two:
    # "Cruisers---Race-2" from a " - " that the other branch had collapsed.
    slug = re.sub(r"[^\w]+", "-", text, flags=re.UNICODE).strip("-")
    return f"{slug or 'export'} {what} {stamp}.{ext}".replace(" ", "-")


def attachment_header(ascii_name: str, utf8_name: str = "") -> str:
    """A Content-Disposition that survives a Welsh name.

    ``filename=`` carries the ASCII form, quoted, for anything old; ``filename*``
    carries the real one UTF-8 encoded, which every current browser prefers.
    Without the second, a name with a circumflex in it arrives on disk as
    mojibake or is dropped for a default. Omitted when the two are the same,
    because a header repeating itself invites the question of which one is right.
    """
    header = 'attachment; filename="%s"' % ascii_name.replace('"', "")
    if utf8_name and utf8_name != ascii_name:
        header += "; filename*=UTF-8''%s" % quote(utf8_name, safe="")
    return header


def export_attachment(name: Any, what: str, ext: str,
                      when: Optional[datetime] = None) -> str:
    """Both halves of the header for one download, from one name and moment."""
    when = when or datetime.now()
    return attachment_header(export_filename(name, what, ext, when),
                             export_filename(name, what, ext, when, ascii_only=False))


def published_score_cell(score: Dict[str, Any]) -> Dict[str, Any]:
    """Format one series race score for the single-file website export."""
    code = str(score.get("code") or "")
    points = score.get("points")
    try:
        points_text = f"{float(points):g}"
    except (TypeError, ValueError):
        points_text = str(points or "")
    if code.replace('.', '', 1).isdigit():
        label = points_text
    elif points_text and code:
        label = f"{points_text} {code}"
    else:
        label = code or points_text
    if score.get("non_excludable"):
        label = f"{label} NE"
    return {
        "text": label,
        "discard": bool(score.get("discard")),
        "rank": score.get("rank"),
        "points": points,
    }


def publish_rating_text(value: Any, result_type: str) -> str:
    """Format a race-entry rating for the published HTML export."""
    try:
        rating = float(value)
    except (TypeError, ValueError):
        return ""
    if result_type.upper() == "YTC":
        return f"{rating:.0f}" if rating >= 10 else f"{rating:.4f}"
    return f"{rating:.3f}"
