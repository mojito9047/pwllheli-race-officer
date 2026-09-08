"""Class-config and start-plan model: rating-band classes and start scheduling.

Extracted verbatim from app.py. Parses/serialises the series class configuration
(rating-band classes with Numeral flags) and start plans, in both legacy-text
and grid-form representations; resolves the effective class config / start plan
for a race (falling back to its series); and derives the absolute start
schedule from the first warning signal (RRS 26: first start is five minutes
after the warning; per-start offsets are from the first actual start).

Grid-form parsers take the submitted form mapping as an argument, so there is
no Flask request-context coupling.
"""
from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from core.db import row_get, safe_json_loads
from core.timeutils import parse_dt
from core.races import (
    get_series,
    postponement_flag,
    race_first_start_dt,
    race_first_warning_dt,
    race_is_postponed,
)
from core.racesignals import AP_DOWN_WARNING_LEAD_S
from core.ratings import (
    parse_band_expression,
    rating_band_display,
    rating_in_class_band,
    rating_type_from_class_name,
)
from core.scoring import (
    DEFAULT_DISCARD_PROFILE,
    DEFAULT_MIN_RACES_TO_CONSTITUTE,
    normalise_discard_profile_text,
    parse_discard_profile,
)

CLASS_CONFIG_HELP = """Legacy text class parser help.  The normal UI is now graphical.
One class per line: class name, rating type, Numeral 0-9 class flag, and rating band.
Examples:
IRC0, IRC, Numeral 0, 1.100 < rating
IRC1, IRC, Numeral 1, 1.000 < rating < 1.099
IRC2, IRC, Numeral 2, rating < 0.999
YTC0, YTC, Numeral 0, 800 > rating
YTC1, YTC, Numeral 1, 900 > rating > 801
YTC2, YTC, Numeral 2, rating > 901

Older shorthand still works for stored/imported data, for example: IRC1: 1.000 < rating < 1.099"""

START_PLAN_HELP = """Legacy text start-plan parser help.  The normal UI is now graphical.
One start per line: start name, offset minutes from the first actual start, class list.
The first actual start is five minutes after the stored first warning-signal time.
Examples:
Start 1, 0, IRC1, IRC2
Start 2, 5, YTC1, YTC2"""


def parse_class_config_text(text: str) -> Dict[str, Any]:
    """Parse series class definitions from admin text into JSON-safe config.

    Preferred format is:
        Class name, IRC/YTC, Class flag, rating band

    The v0.50 shorthand remains supported:
        Class name: rating band
        Class name, IRC/YTC, rating band
    """
    classes: List[Dict[str, Any]] = []
    errors: List[str] = []
    for line_no, raw in enumerate((text or "").splitlines(), start=1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        name = ""
        rating_type = ""
        flag = ""
        expr = ""

        comma_parts = [p.strip() for p in line.split(",")]
        if len(comma_parts) >= 4 and comma_parts[1].strip().upper() in ("IRC", "YTC"):
            # Class, rating type, flag, band.  Join the remaining fields back
            # together so any future comma in the band/help text does not break it.
            name = comma_parts[0]
            rating_type = comma_parts[1].upper()
            flag = comma_parts[2]
            expr = ",".join(comma_parts[3:]).strip()
        elif len(comma_parts) >= 3 and comma_parts[1].strip().upper() in ("IRC", "YTC"):
            name, rating_type, expr = comma_parts[0], comma_parts[1].upper(), ",".join(comma_parts[2:]).strip()
        elif ":" in line:
            lhs, expr = [p.strip() for p in line.split(":", 1)]
            # Optional shorthand: IRC1 [Numeral 1]: 1.000 < rating < 1.099
            m = re.fullmatch(r"(.+?)\s*\[(.+?)\]", lhs)
            if m:
                name, flag = m.group(1).strip(), m.group(2).strip()
            else:
                name = lhs
            rating_type = rating_type_from_class_name(name)
        else:
            parts = line.split(None, 2)
            if len(parts) >= 3 and parts[1].upper() in ("IRC", "YTC"):
                name, rating_type, expr = parts[0], parts[1].upper(), parts[2]
            elif len(parts) >= 2:
                name, expr = parts[0], " ".join(parts[1:])
                rating_type = rating_type_from_class_name(name)
        if not name or not expr:
            errors.append(f"Line {line_no}: expected 'Class, IRC/YTC, flag, rating band' or 'Class: rating band'.")
            continue
        rule = parse_band_expression(expr)
        rule.update({
            "name": name,
            "rating_type": rating_type_from_class_name(name, rating_type),
            "flag": flag.strip(),
            "expr": expr,
        })
        classes.append(rule)
    return {"classes": classes, "errors": errors}


def class_config_text(config: Any) -> str:
    """Format class config back to editable text."""
    cfg = config if isinstance(config, dict) else safe_json_loads(config, {})
    lines = []
    for rule in cfg.get("classes", []) or []:
        name = str(rule.get("name", "")).strip()
        rating_type = rating_type_from_class_name(name, str(rule.get("rating_type") or "IRC"))
        flag = str(rule.get("flag") or "").strip()
        band = rating_band_display(rule)
        if flag:
            lines.append(f"{name}, {rating_type}, {flag}, {band}")
        else:
            lines.append(f"{name}: {band}")
    return "\n".join(lines)


def parse_start_plan_text(text: str) -> Dict[str, Any]:
    """Parse start plan text into JSON-safe starts with offsets and class names."""
    starts: List[Dict[str, Any]] = []
    errors: List[str] = []
    for line_no, raw in enumerate((text or "").splitlines(), start=1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split(",", 2)]
        if len(parts) < 2:
            errors.append(f"Line {line_no}: expected 'Start name, offset minutes, classes'.")
            continue
        if len(parts) == 2:
            name = f"Start {len(starts) + 1}"
            offset_text, class_text = parts[0], parts[1]
        else:
            name, offset_text, class_text = parts[0], parts[1], parts[2]
        m = re.search(r"-?\d+(?:\.\d+)?", offset_text)
        if not m:
            errors.append(f"Line {line_no}: offset minutes was not a number.")
            continue
        offset_min = float(m.group(0))
        classes = [c.strip() for c in re.split(r"[,;/|]", class_text) if c.strip()]
        starts.append({"name": name or f"Start {len(starts)+1}", "offset_min": offset_min, "classes": classes})
    return {"starts": starts, "errors": errors}


def start_plan_text(plan: Any) -> str:
    """Format a start plan back to editable text."""
    cfg = plan if isinstance(plan, dict) else safe_json_loads(plan, {})
    lines = []
    for idx, start in enumerate(cfg.get("starts", []) or [], start=1):
        name = start.get("name") or f"Start {idx}"
        offset = float(start.get("offset_min") or 0)
        offset_text = f"{offset:g}"
        classes = ", ".join(start.get("classes") or ["All classes"])
        lines.append(f"{name}, {offset_text}, {classes}")
    return "\n".join(lines)


MAX_CLASSES_PER_RATING_TYPE = 3
MAX_START_PLAN_ROWS = 6
RATING_CLASS_TYPES = ("IRC", "YTC")
NUMERAL_FLAG_OPTIONS = [f"Numeral {i}" for i in range(10)]

DEFAULT_CLASS_ROWS: Dict[str, List[Dict[str, Any]]] = {
    "IRC": [
        {"name": "IRC0", "flag": "Numeral 0", "min": 1.100, "max": None, "min_inclusive": False, "max_inclusive": False},
        {"name": "IRC1", "flag": "Numeral 1", "min": 1.000, "max": 1.099, "min_inclusive": False, "max_inclusive": False},
        {"name": "IRC2", "flag": "Numeral 2", "min": None, "max": 0.999, "min_inclusive": False, "max_inclusive": False},
    ],
    "YTC": [
        {"name": "YTC0", "flag": "Numeral 0", "min": None, "max": 800, "min_inclusive": False, "max_inclusive": False},
        {"name": "YTC1", "flag": "Numeral 1", "min": 801, "max": 900, "min_inclusive": False, "max_inclusive": False},
        {"name": "YTC2", "flag": "Numeral 2", "min": 901, "max": None, "min_inclusive": False, "max_inclusive": False},
    ],
}


def format_number_for_form(value: Any) -> str:
    """Return a compact numeric string for template form fields."""
    if value is None or value == "":
        return ""
    try:
        return f"{float(value):g}"
    except Exception:
        return str(value)


def normalize_numeral_flag(value: Any, fallback: str = "Numeral 0") -> str:
    """Return one of the supported class flag labels: Numeral 0 .. Numeral 9.

    Earlier versions allowed free text such as "YTC 1 flag".  Preserve those
    configurations where possible by extracting a single digit and converting it
    to the new controlled vocabulary.
    """
    text = str(value or "").strip()
    match = re.search(r"(?:numeral\s*)?([0-9])", text, flags=re.IGNORECASE)
    if match:
        return f"Numeral {match.group(1)}"
    fallback_match = re.search(r"([0-9])", str(fallback or ""))
    if fallback_match:
        return f"Numeral {fallback_match.group(1)}"
    return "Numeral 0"


def numeral_from_flag_label(label: Any) -> Optional[str]:
    """Extract the displayed numeral from a supported flag label."""
    match = re.search(r"([0-9])", str(label or ""))
    return match.group(1) if match else None


def band_expr_from_limits(min_value: Any, max_value: Any, min_inclusive: bool = False, max_inclusive: bool = False) -> str:
    """Build the human-readable expression stored with a graphical class row."""
    mn = format_number_for_form(min_value)
    mx = format_number_for_form(max_value)
    if mn and mx:
        return f"{mn} {'≤' if min_inclusive else '<'} rating {'≤' if max_inclusive else '<'} {mx}"
    if mn:
        return f"rating {'≥' if min_inclusive else '>'} {mn}"
    if mx:
        return f"rating {'≤' if max_inclusive else '<'} {mx}"
    return "all ratings"


def enabled_class_slots_from_config(config: Any) -> List[Dict[str, Any]]:
    """Return class slots used by the six-row graphical start-plan editor."""
    grid = class_grid_from_config(config, fill_empty_defaults=False)
    slots: List[Dict[str, Any]] = []
    for rating_type in RATING_CLASS_TYPES:
        for row in grid.get(rating_type, []):
            if row.get("enabled") and row.get("name"):
                slots.append({
                    "slot": row["slot"],
                    "rating_type": rating_type,
                    "index": row["index"],
                    "name": row["name"],
                    "flag": row.get("flag", ""),
                })
    return slots


def all_class_slots_from_config(config: Any) -> List[Dict[str, Any]]:
    """Every band slot the form can offer, ticked or not, in grid order.

    ``enabled_class_slots_from_config`` answers "which bands does this series
    have", which is the right question for *parsing* a start plan and the wrong
    one for *rendering* it. Rendering only the enabled slots meant the start-plan
    editor was built from the config as it was when the page loaded -- so on the
    Add series page, where nothing is ticked yet, it had no class checkboxes at
    all and said "No classes configured yet" however many bands you then filled
    in. The only way out was to create the series and come back.

    The six slots are always rendered now; the page's own script hides and
    unticks the ones whose band is off, and reveals one the moment its band is
    ticked. ``name`` falls back to the slot key so a band ticked but not yet
    named still has something to show.
    """
    grid = class_grid_from_config(config, fill_empty_defaults=False)
    slots: List[Dict[str, Any]] = []
    for rating_type in RATING_CLASS_TYPES:
        for row in grid.get(rating_type, []):
            slots.append({
                "slot": row["slot"],
                "rating_type": rating_type,
                "index": row["index"],
                "name": row.get("name") or row["slot"],
                "flag": row.get("flag", ""),
                "enabled": bool(row.get("enabled") and row.get("name")),
            })
    return slots


def class_grid_from_config(config: Any, fill_empty_defaults: bool = True) -> Dict[str, List[Dict[str, Any]]]:
    """Convert class JSON into fixed three-row-per-rating-type form rows."""
    cfg = config if isinstance(config, dict) else safe_json_loads(config, {"classes": []})
    existing_by_type: Dict[str, List[Dict[str, Any]]] = {"IRC": [], "YTC": []}
    for rule in cfg.get("classes", []) or []:
        rating_type = rating_type_from_class_name(str(rule.get("name") or ""), str(rule.get("rating_type") or "IRC"))
        if rating_type in existing_by_type and len(existing_by_type[rating_type]) < MAX_CLASSES_PER_RATING_TYPE:
            existing_by_type[rating_type].append(rule)
    has_any_existing = any(existing_by_type.values())
    grid: Dict[str, List[Dict[str, Any]]] = {"IRC": [], "YTC": []}
    for rating_type in RATING_CLASS_TYPES:
        for idx in range(MAX_CLASSES_PER_RATING_TYPE):
            default = DEFAULT_CLASS_ROWS[rating_type][idx]
            if idx < len(existing_by_type[rating_type]):
                rule = existing_by_type[rating_type][idx]
                enabled = True
            else:
                rule = default if (fill_empty_defaults and not has_any_existing) else {}
                enabled = bool(fill_empty_defaults and not has_any_existing)
            min_value = rule.get("min", default.get("min")) if enabled else rule.get("min")
            max_value = rule.get("max", default.get("max")) if enabled else rule.get("max")
            min_inclusive = bool(rule.get("min_inclusive", default.get("min_inclusive", False))) if enabled else bool(rule.get("min_inclusive", False))
            max_inclusive = bool(rule.get("max_inclusive", default.get("max_inclusive", False))) if enabled else bool(rule.get("max_inclusive", False))
            row = {
                "slot": f"{rating_type}_{idx}",
                "rating_type": rating_type,
                "index": idx,
                "enabled": enabled,
                "name": str(rule.get("name") or default.get("name") or f"{rating_type}{idx}"),
                "flag": normalize_numeral_flag(rule.get("flag"), str(default.get("flag") or f"Numeral {idx}")),
                "min": format_number_for_form(min_value),
                "max": format_number_for_form(max_value),
                "min_inclusive": min_inclusive,
                "max_inclusive": max_inclusive,
            }
            row["expr"] = band_expr_from_limits(row["min"], row["max"], row["min_inclusive"], row["max_inclusive"])
            grid[rating_type].append(row)
    return grid


def class_config_from_grid_rows(grid: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Any]:
    """Build class config from already-normalised graphical rows."""
    classes: List[Dict[str, Any]] = []
    for rating_type in RATING_CLASS_TYPES:
        for row in grid.get(rating_type, []) or []:
            if not row.get("enabled"):
                continue
            try:
                min_value = float(row["min"]) if str(row.get("min") or "").strip() else None
                max_value = float(row["max"]) if str(row.get("max") or "").strip() else None
            except ValueError:
                min_value, max_value = None, None
            classes.append({
                "name": str(row.get("name") or "").strip(),
                "rating_type": rating_type,
                "flag": normalize_numeral_flag(row.get("flag"), f"Numeral {row.get('index', 0)}"),
                "min": min_value,
                "max": max_value,
                "min_inclusive": bool(row.get("min_inclusive")),
                "max_inclusive": bool(row.get("max_inclusive")),
                "expr": band_expr_from_limits(min_value, max_value, bool(row.get("min_inclusive")), bool(row.get("max_inclusive"))),
            })
    return {"classes": [c for c in classes if c.get("name")]}


def class_config_from_grid_form(form: Any) -> Dict[str, Any]:
    """Parse the graphical rating-band class table into JSON-safe class rules."""
    classes: List[Dict[str, Any]] = []
    errors: List[str] = []
    seen_names: set[str] = set()
    for rating_type in RATING_CLASS_TYPES:
        for idx in range(MAX_CLASSES_PER_RATING_TYPE):
            prefix = f"class_{rating_type}_{idx}_"
            enabled = form.get(prefix + "enabled") == "1"
            if not enabled:
                continue
            name = (form.get(prefix + "name") or f"{rating_type}{idx}").strip()
            flag = normalize_numeral_flag(form.get(prefix + "flag"), f"Numeral {idx}")
            min_text = (form.get(prefix + "min") or "").strip()
            max_text = (form.get(prefix + "max") or "").strip()
            min_inclusive = form.get(prefix + "min_inclusive") == "1"
            max_inclusive = form.get(prefix + "max_inclusive") == "1"
            if not name:
                errors.append(f"{rating_type} class {idx + 1}: class name is required.")
                continue
            key = name.upper()
            if key in seen_names:
                errors.append(f"Class name {name!r} is used more than once.")
                continue
            seen_names.add(key)
            try:
                min_value = float(min_text) if min_text else None
                max_value = float(max_text) if max_text else None
            except ValueError:
                errors.append(f"{name}: rating limits must be numbers.")
                continue
            if min_value is not None and max_value is not None and min_value >= max_value:
                errors.append(f"{name}: lower rating limit must be less than upper rating limit.")
                continue
            expr = band_expr_from_limits(min_value, max_value, min_inclusive, max_inclusive)
            rule = {
                "name": name,
                "rating_type": rating_type,
                "flag": flag,
                "min": min_value,
                "max": max_value,
                "min_inclusive": min_inclusive,
                "max_inclusive": max_inclusive,
                "expr": expr,
            }
            classes.append(rule)
    return {"classes": classes, "errors": errors}


def start_plan_grid_from_plan(plan: Any, class_config: Any) -> Dict[str, Any]:
    """Convert a start-plan JSON object into six editable graphical rows."""
    cfg = plan if isinstance(plan, dict) else safe_json_loads(plan, {"starts": []})
    slots = enabled_class_slots_from_config(class_config)
    slot_names = {slot["slot"]: slot["name"] for slot in slots}
    name_to_slot = {str(slot["name"]).strip().upper(): slot["slot"] for slot in slots}
    starts = cfg.get("starts", []) or []
    rows: List[Dict[str, Any]] = []
    for idx in range(MAX_START_PLAN_ROWS):
        if idx < len(starts):
            start = starts[idx]
            enabled = True
            name = str(start.get("name") or f"Start {idx + 1}")
            offset = format_number_for_form(start.get("offset_min", idx * 5))
            raw_classes = [str(c).strip() for c in (start.get("classes") or []) if str(c).strip()]
            if any(c.upper() == "ALL CLASSES" for c in raw_classes):
                selected_slots = set(slot_names.keys())
            else:
                selected_slots = {name_to_slot.get(c.upper()) for c in raw_classes}
                selected_slots.discard(None)
        else:
            enabled = idx == 0 and not starts
            name = f"Start {idx + 1}"
            offset = format_number_for_form(idx * 5)
            selected_slots = set(slot_names.keys()) if enabled else set()
        rows.append({
            "index": idx,
            "enabled": enabled,
            "name": name,
            "offset_min": offset,
            "selected_slots": selected_slots,
        })
    # Two different lists on purpose. `slots` -- the enabled bands -- decides
    # which classes a new start arrives with ticked, and must not offer a band
    # nobody has defined. `class_slots` is what gets rendered, and is all six, so
    # the editor can follow the bands being filled in above it without a reload.
    return {"rows": rows, "class_slots": all_class_slots_from_config(class_config),
            "max_starts": MAX_START_PLAN_ROWS}


def start_plan_from_grid_form(form: Any, class_config: Any) -> Dict[str, Any]:
    """Parse the six-row graphical start-plan editor into JSON-safe starts."""
    slots = enabled_class_slots_from_config(class_config)
    slot_by_key = {slot["slot"]: slot for slot in slots}
    starts: List[Dict[str, Any]] = []
    errors: List[str] = []
    for idx in range(MAX_START_PLAN_ROWS):
        prefix = f"start_{idx}_"
        enabled = form.get(prefix + "enabled") == "1"
        if not enabled:
            continue
        name = (form.get(prefix + "name") or f"Start {idx + 1}").strip()
        offset_text = (form.get(prefix + "offset_min") or "").strip()
        if not offset_text:
            offset_text = "0" if not starts else str(idx * 5)
        try:
            offset_min = float(offset_text)
        except ValueError:
            errors.append(f"{name}: offset minutes must be a number.")
            continue
        selected_classes: List[str] = []
        for slot_key, slot in slot_by_key.items():
            if form.get(prefix + "class_" + slot_key) == "1":
                selected_classes.append(slot["name"])
        if slots and not selected_classes:
            errors.append(f"{name}: select at least one class for this start.")
            continue
        if not slots:
            selected_classes = ["All classes"]
        starts.append({"name": name, "offset_min": offset_min, "classes": selected_classes})
    if not starts:
        # Keep a workable default rather than leaving the race unscheduled.
        class_names = [slot["name"] for slot in slots] or ["All classes"]
        starts.append({"name": "Start 1", "offset_min": 0.0, "classes": class_names})
    return {"starts": starts, "errors": errors}


def series_class_config(series: Optional[sqlite3.Row]) -> Dict[str, Any]:
    """Return class-band config for a series."""
    return safe_json_loads(row_get(series, "class_config_json", ""), {"classes": []})


def series_start_plan(series: Optional[sqlite3.Row]) -> Dict[str, Any]:
    """Return default start plan for a series."""
    return safe_json_loads(row_get(series, "start_plan_json", ""), {"starts": []})


def series_discard_profile(series: Optional[sqlite3.Row]) -> str:
    """Return the configured discard profile for a series."""
    return normalise_discard_profile_text(row_get(series, "discard_profile", "") or DEFAULT_DISCARD_PROFILE)


def parse_min_races_to_constitute(value: Any) -> Tuple[int, List[str]]:
    """Parse the minimum completed races needed to constitute a series."""
    raw = str(value if value is not None else "").strip()
    if not raw:
        return DEFAULT_MIN_RACES_TO_CONSTITUTE, []
    try:
        parsed = int(raw)
    except ValueError:
        return DEFAULT_MIN_RACES_TO_CONSTITUTE, ["Minimum races to constitute a series must be a whole number."]
    if parsed < 1:
        return 1, ["Minimum races to constitute a series must be at least 1."]
    return parsed, []


def series_min_races_to_constitute(series: Optional[sqlite3.Row]) -> int:
    """Return the configured minimum completed races needed to constitute a series."""
    value, _errors = parse_min_races_to_constitute(row_get(series, "min_races_to_constitute", DEFAULT_MIN_RACES_TO_CONSTITUTE))
    return value


def series_discards_for_race_count(race_count: int, series: Optional[sqlite3.Row] = None) -> int:
    """Return discards allowed by the series profile for a completed race count."""
    if race_count <= 0:
        return 0
    profile, _errors = parse_discard_profile(series_discard_profile(series))
    idx = min(race_count, len(profile)) - 1
    return max(0, int(profile[idx]))


def discard_profile_help_text(profile_text: Any) -> str:
    """Return a compact human-readable summary of the discard profile."""
    profile, _errors = parse_discard_profile(profile_text)
    parts = [f"{idx}: {value}" for idx, value in enumerate(profile, start=1)]
    if profile:
        parts.append(f">{len(profile)}: {profile[-1]}")
    return "; ".join(parts)


def race_series_row(race: sqlite3.Row) -> Optional[sqlite3.Row]:
    """Return the series row linked to a race, if any."""
    sid = row_get(race, "series_id")
    return get_series(sid) if sid else None


def race_class_config(race: sqlite3.Row) -> Dict[str, Any]:
    """Return the effective class config for a race."""
    return series_class_config(race_series_row(race))


def default_start_plan_from_classes(class_config: Dict[str, Any]) -> Dict[str, Any]:
    """Build a single-start plan including every configured class."""
    names = [str(c.get("name")) for c in class_config.get("classes", []) if c.get("name")]
    return {"starts": [{"name": "Start 1", "offset_min": 0.0, "classes": names or ["All classes"]}]}


def race_start_plan(race: sqlite3.Row) -> Dict[str, Any]:
    """Return the race-specific start plan, falling back to the series default."""
    # A pursuit race has no class start plan of its own, but the first start
    # still flies a normal flag sequence. Default it to class 1 (numeral-1
    # pennant) so the flag panel behaves like a standard single-class start.
    if str(row_get(race, "race_type", "") or "").lower() == "pursuit":
        return {"starts": [{"name": "First start", "offset_min": 0.0, "classes": ["1"]}]}
    race_plan = safe_json_loads(row_get(race, "start_plan_json", ""), None)
    if isinstance(race_plan, dict) and race_plan.get("starts"):
        return race_plan
    series_plan = series_start_plan(race_series_row(race))
    if series_plan.get("starts"):
        return series_plan
    return default_start_plan_from_classes(race_class_config(race))


def race_start_schedule(race: sqlite3.Row) -> List[Dict[str, Any]]:
    """Return start-plan items with absolute warning and start times.

    race.start_time stores the first warning-signal time.  Start-plan offsets are
    still from the first actual start, so Start 2, 5 is five minutes after Start 1.
    """
    first_warning_dt = race_first_warning_dt(race)
    first_start_dt = race_first_start_dt(race)
    plan = race_start_plan(race)
    starts = plan.get("starts", []) or [{"name": "Start 1", "offset_min": 0.0, "classes": ["All classes"]}]
    schedule: List[Dict[str, Any]] = []
    for idx, item in enumerate(starts, start=1):
        offset = float(item.get("offset_min") or 0.0)
        start_dt = first_start_dt + timedelta(minutes=offset) if first_start_dt else None
        warning_dt = first_warning_dt + timedelta(minutes=offset) if first_warning_dt else None
        classes = [str(c).strip() for c in (item.get("classes") or []) if str(c).strip()]
        schedule.append({
            "index": idx,
            "name": item.get("name") or f"Start {idx}",
            "offset_min": offset,
            "classes": classes,
            "warning_time": warning_dt.isoformat(timespec="seconds") if warning_dt else "",
            "warning_dt": warning_dt,
            "time": start_dt.isoformat(timespec="seconds") if start_dt else "",
            "dt": start_dt,
        })
    return schedule


def class_rules_for_type(class_config: Dict[str, Any], result_type: str) -> List[Dict[str, Any]]:
    """Return configured class rules for one rating type."""
    rt = result_type.upper()
    return [r for r in class_config.get("classes", []) or [] if str(r.get("rating_type") or "").upper() == rt]


def class_for_rating_value(class_config: Dict[str, Any], result_type: str, rating: Optional[float]) -> Optional[Dict[str, Any]]:
    """Find the first configured class whose band contains this rating."""
    for rule in class_rules_for_type(class_config, result_type):
        if rating_in_class_band(rating, rule):
            return rule
    return None


def entry_rating_class_label(class_config: Dict[str, Any], irc_rating: Optional[float], ytc_rating: Optional[float]) -> str:
    """Rating-band class label(s) an entry falls into, e.g. 'IRC1', or
    'IRC1 / YTC2' when both types have bands, or '' if no configured band matches.

    Only rating types that actually have bands configured are considered, so an
    IRC-only series labels boats by their IRC band.
    """
    labels = []
    for rating_type, rating in (("IRC", irc_rating), ("YTC", ytc_rating)):
        if not class_rules_for_type(class_config, rating_type):
            continue
        rule = class_for_rating_value(class_config, rating_type, rating)
        if rule and rule.get("name"):
            labels.append(str(rule["name"]))
    return " / ".join(labels)


def entry_class_labels_map(class_config: Dict[str, Any], entries) -> Dict[Any, str]:
    """Map each entry id -> its rating-band class label. Empty dict when the
    series has no rating-band classes configured (callers then fall back to the
    boat's own division)."""
    if not (class_config or {}).get("classes"):
        return {}
    out = {}
    for e in entries:
        try:
            irc = e["manual_irc_rating"]
            ytc = e["manual_ytc_rating"]
        except (KeyError, IndexError, TypeError):
            irc = ytc = None
        out[e["id"]] = entry_rating_class_label(class_config, irc, ytc)
    return out


def class_rule_by_name(class_config: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Return class rules keyed by lower-case class name."""
    return {str(rule.get("name") or "").strip().lower(): rule for rule in class_config.get("classes", []) or [] if str(rule.get("name") or "").strip()}


def flag_label_for_class(race: sqlite3.Row, class_name: str) -> str:
    """Return the configured flag label for a class, falling back to the class name."""
    cname = str(class_name or "").strip()
    if not cname:
        return "Class flag"
    rule = class_rule_by_name(race_class_config(race)).get(cname.lower())
    if rule:
        flag = str(rule.get("flag") or "").strip()
        if flag:
            return flag
    if cname.lower() in ("all", "all classes", "*"):
        return "Class flag"
    return f"{cname} flag"


def class_flags_for_start(race: sqlite3.Row, start_item: Dict[str, Any]) -> List[str]:
    """Return display flag labels for the classes in one start."""
    classes = [str(c).strip() for c in (start_item.get("classes") or []) if str(c).strip()]
    if not classes or any(c.lower() in ("all", "all classes", "*") for c in classes):
        configured = [str(c.get("name") or "").strip() for c in race_class_config(race).get("classes", []) or [] if str(c.get("name") or "").strip()]
        classes = configured or ["Class flag"]
    labels: List[str] = []
    seen: set = set()
    for cname in classes:
        label = flag_label_for_class(race, cname)
        key = label.lower()
        if key not in seen:
            labels.append(label)
            seen.add(key)
    return labels or ["Class flag"]


def start_for_class_from_schedule(schedule: List[Dict[str, Any]], class_name: Optional[str]) -> Optional[Dict[str, Any]]:
    """Return the configured start for a class from a precomputed schedule."""
    if not schedule:
        return None
    cname = (class_name or "").strip().lower()
    for start in schedule:
        classes = [str(c).strip().lower() for c in start.get("classes") or []]
        if not classes or "all classes" in classes or "all" in classes or "*" in classes:
            return start
        if cname and cname in classes:
            return start
    return schedule[0]


def start_for_class(race: sqlite3.Row, class_name: Optional[str]) -> Optional[Dict[str, Any]]:
    """Return the configured race start for a result class."""
    return start_for_class_from_schedule(race_start_schedule(race), class_name)


def class_flags_text_for_start(race: sqlite3.Row, start_item: Dict[str, Any]) -> str:
    """Return a compact display string for a start's class flags."""
    return " + ".join(class_flags_for_start(race, start_item))


def signal_panel_schedule(race: sqlite3.Row) -> List[Dict[str, Any]]:
    """Return compact JSON data for the live graphical flag panel.

    The panel uses this rather than a single hard-coded class flag, so it can
    show the class numerals actually scheduled for each rolling start.
    """
    schedule: List[Dict[str, Any]] = []
    for start in race_start_schedule(race):
        flags = []
        for label in class_flags_for_start(race, start):
            numeral = numeral_from_flag_label(label)
            flags.append({
                "label": label,
                "kind": "numeral",
                "numeral": numeral or "",
            })
        schedule.append({
            "name": start.get("name") or "Start",
            "time": start.get("time") or "",
            "warning_time": start.get("warning_time") or "",
            "classes": start.get("classes") or [],
            "flags": flags,
        })
    return schedule


SIGNAL_PLAN_TEMPLATE: List[Dict[str, Any]] = [
    {"sec": 540, "rel": "-09:00", "flags": "No change", "action": "Central audio: course announcement", "horn": "—", "sound": False},
    {"sec": 420, "rel": "-07:00", "flags": "No change", "action": "Central audio: course announcement", "horn": "—", "sound": False},
    {"sec": 315, "rel": "-05:15", "flags": "No change", "action": "Central audio: “Stand by 15 seconds”", "horn": "—", "sound": False},
    {"sec": 300, "rel": "-05:00", "flags": "{class_flags} up", "action": "Warning signal / class flag", "horn": "1 sound", "sound": True},
    {"sec": 255, "rel": "-04:15", "flags": "{class_flags} remain up", "action": "Central audio: “Stand by 15 seconds”", "horn": "—", "sound": False},
    {"sec": 240, "rel": "-04:00", "flags": "{class_flags} + P up", "action": "Preparatory signal", "horn": "1 sound", "sound": True},
    {"sec": 180, "rel": "-03:00", "flags": "{class_flags} + P remain up", "action": "Central audio: course announcement", "horn": "—", "sound": False},
    {"sec": 75, "rel": "-01:15", "flags": "{class_flags} + P remain up", "action": "Central audio: “Stand by 15 seconds”", "horn": "—", "sound": False},
    {"sec": 60, "rel": "-01:00", "flags": "{class_flags} up; P down", "action": "Preparatory flag removed", "horn": "1 sound", "sound": True},
    {"sec": 0, "rel": "00:00", "flags": "{class_flags} down", "action": "Start signal", "horn": "1 sound", "sound": True},
]


def start_signal_plan_rows(race: sqlite3.Row) -> List[Dict[str, Any]]:
    """Build display rows for all configured starts, grouping simultaneous signals."""
    raw_rows: List[Dict[str, Any]] = []
    multi_start = len(race_start_schedule(race)) > 1
    for start in race_start_schedule(race):
        start_dt = start.get("dt")
        class_flags = class_flags_text_for_start(race, start)
        classes_text = ", ".join(start.get("classes") or [])
        for item in SIGNAL_PLAN_TEMPLATE:
            signal_dt = start_dt - timedelta(seconds=float(item["sec"])) if start_dt else None
            raw_rows.append({
                "start_index": start.get("index"),
                "start_name": start.get("name") or "Start",
                "classes": start.get("classes") or [],
                "classes_text": classes_text,
                "time_to_start": item["rel"],
                "signal_time": signal_dt.isoformat(timespec="seconds") if signal_dt else "",
                "signal_dt": signal_dt,
                "signal_sec": item["sec"],
                "displayed_flags": str(item["flags"]).format(class_flags=class_flags),
                "action": item["action"],
                "horn": item["horn"],
                "sound": bool(item.get("sound")),
                "multi_start": multi_start,
            })
    # Group rows that happen at the exact same time.  This is important for a
    # rolling sequence where Start 1's start signal is also Start 2's warning
    # signal; it should be shown and sounded once as a combined signal.
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for row in raw_rows:
        key = row["signal_time"] or f"{row['start_index']}:{row['signal_sec']}"
        grouped.setdefault(key, []).append(row)
    combined: List[Dict[str, Any]] = []
    for key, rows in grouped.items():
        rows = sorted(rows, key=lambda r: (r.get("start_index") or 0, -float(r.get("signal_sec") or 0)))
        sound_count = sum(1 for r in rows if r.get("sound"))
        combined.append({
            "signal_time": rows[0].get("signal_time", ""),
            "signal_dt": rows[0].get("signal_dt"),
            "start_names": " / ".join(f"{r['start_name']} {r['time_to_start']}" for r in rows),
            "classes_text": " / ".join(filter(None, [r.get("classes_text") for r in rows])),
            "displayed_flags": "; ".join(r["displayed_flags"] for r in rows),
            "action": "; ".join(f"{r['start_name']}: {r['action']}" if len(rows) > 1 else r["action"] for r in rows),
            "horn": "1 sound (combined)" if sound_count > 1 else ("1 sound" if sound_count == 1 else "—"),
            "signal_sec": rows[0].get("signal_sec"),
        })
    combined = sorted(combined, key=lambda r: (r.get("signal_dt") is None,
                                               r.get("signal_dt") or datetime.max))
    return with_postponement(race, combined)


def with_postponement(race: sqlite3.Row, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Fold AP into the signal plan: what is suppressed, and what happens instead.

    The plan is the list a race officer reads to know what the app is about to
    do, so under AP it was wrong in both directions at once. It listed the course
    announcements due before the flag comes down as though they would be made --
    they are not, because nothing sounds while AP flies -- and it left out the
    two signals that *will* happen: the announcement a minute beforehand and the
    single sound as the flag is lowered.

    A plan that omits the next thing you are going to hear is worse than no plan,
    because it is read instead of thinking.
    """
    if not race_is_postponed(race):
        return rows
    ends = parse_dt(str(row_get(race, "postponement_ends_at", "") or ""))
    flag = postponement_flag(race) or "AP"
    for row in rows:
        signal_dt = row.get("signal_dt")
        # No moment chosen yet: the whole sequence is held, indefinitely.
        if ends is None or (signal_dt is not None and signal_dt < ends):
            row["suppressed"] = True
            row["suppressed_note"] = f"held — {flag} flying"
    if ends is None:
        return rows
    added = [
        {
            "signal_time": (ends - timedelta(seconds=AP_DOWN_WARNING_LEAD_S)).isoformat(
                timespec="seconds"),
            "signal_dt": ends - timedelta(seconds=AP_DOWN_WARNING_LEAD_S),
            "start_names": flag, "classes_text": "",
            "displayed_flags": f"{flag} remains up",
            "action": "Central audio: “Answering pennant will be lowered in one minute”",
            "horn": "—", "signal_sec": None, "postponement": True,
        },
        {
            "signal_time": ends.isoformat(timespec="seconds"),
            "signal_dt": ends,
            "start_names": flag, "classes_text": "",
            "displayed_flags": f"{flag} down",
            "action": "Postponement ends; the warning signal follows one minute later",
            "horn": "1 sound", "signal_sec": None, "postponement": True,
        },
    ]
    return sorted(rows + added, key=lambda r: (r.get("signal_dt") is None,
                                               r.get("signal_dt") or datetime.max))
