"""Polar and sail-chart file IO: listing, resolving, loading and parsing.

Extracted from app.py. Reads its directories from core.appstate via attribute
access (appstate.POLARS_DIR, appstate.SAIL_CHARTS_DIR, appstate.SAIL_CHART_PATH,
...) so runtime reassignment and test monkeypatching of those paths are always
seen here. No Flask/request coupling -- the request-aware polar selection
(request_prefer_form, resolve_polar_path) stays in app.py.
"""
from __future__ import annotations

import csv
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

from werkzeug.utils import secure_filename

from core import appstate

POLAR_PATH = appstate.POLARS_DIR / "J122.txt"
ALLOWED_POLAR_EXTENSIONS = {".txt", ".pol", ".csv"}
ALLOWED_SAIL_CHART_EXTENSIONS = {".txt", ".tsv", ".csv"}


def ensure_polars_dir() -> None:
    """Ensure the local polar-file directory exists and migrate the old J/122 copy if present."""
    appstate.POLARS_DIR.mkdir(parents=True, exist_ok=True)
    legacy_polar = appstate.DATA_DIR / "J122.txt"
    if legacy_polar.exists() and not POLAR_PATH.exists():
        try:
            shutil.copy2(legacy_polar, POLAR_PATH)
        except Exception:
            pass


def list_polar_files() -> List[str]:
    """List available polar files for selection in course analysis."""
    ensure_polars_dir()
    names = []
    for path in appstate.POLARS_DIR.iterdir():
        if path.is_file() and path.suffix.lower() in ALLOWED_POLAR_EXTENSIONS:
            names.append(path.name)
    return sorted(set(names), key=lambda x: x.lower())


def default_polar_name(polar_files: Optional[List[str]] = None) -> str:
    """Return the default polar filename used when no race-specific polar has been chosen."""
    polar_files = polar_files if polar_files is not None else list_polar_files()
    return "J122.txt" if "J122.txt" in polar_files else (polar_files[0] if polar_files else "")


def race_saved_polar(race, polar_files: Optional[List[str]] = None) -> str:
    """Return the saved polar for a race, falling back safely to the default polar."""
    polar_files = polar_files if polar_files is not None else list_polar_files()
    saved = ""
    if race is not None and "polar_file" in race.keys():
        saved = (race["polar_file"] or "").strip()
    return saved if saved in polar_files else default_polar_name(polar_files)


def load_polar(path: Path = POLAR_PATH) -> List[Dict[str, Any]]:
    """Load Expedition-style polar rows.

    Format: TWS, then repeated TWA / boat-speed pairs.
    Example: 12  39 6.92  50 7.64 ...
    """
    rows: List[Dict[str, Any]] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.replace(",", "\t").split()
        if len(parts) < 3:
            continue
        try:
            values = [float(x) for x in parts]
        except ValueError:
            continue
        tws = values[0]
        points = []
        for i in range(1, len(values) - 1, 2):
            twa = values[i]
            bsp = values[i + 1]
            if twa >= 0 and bsp >= 0:
                points.append({"twa": twa, "bsp": bsp})
        if points:
            rows.append({"tws": tws, "points": points})
    rows.sort(key=lambda r: float(r["tws"]))
    return rows


def ensure_sailcharts_dir() -> None:
    """Ensure the optional per-polar sail-chart directory exists.

    The default chart remains at data/DefaultSailChart.txt for backwards
    compatibility.  Boat-specific charts live in data/sailcharts and now use
    a -SailChart suffix so they are visually distinct from polar files, for
    example:

        data/polars/J109.txt
        data/sailcharts/J109-SailChart.txt
    """
    appstate.SAIL_CHARTS_DIR.mkdir(parents=True, exist_ok=True)
    default_named_chart = appstate.SAIL_CHARTS_DIR / "Default-SailChart.txt"
    if not appstate.SAIL_CHART_PATH.exists() and default_named_chart.exists():
        try:
            shutil.copy2(default_named_chart, appstate.SAIL_CHART_PATH)
        except Exception:
            pass
    if not appstate.SAIL_CHART_PATH.exists() and appstate.LEGACY_SAIL_CHART_PATH.exists():
        try:
            appstate.LEGACY_SAIL_CHART_PATH.rename(appstate.SAIL_CHART_PATH)
        except Exception:
            try:
                shutil.copy2(appstate.LEGACY_SAIL_CHART_PATH, appstate.SAIL_CHART_PATH)
            except Exception:
                pass
    if appstate.SAIL_CHART_PATH.exists() and not default_named_chart.exists():
        try:
            shutil.copy2(appstate.SAIL_CHART_PATH, default_named_chart)
        except Exception:
            pass


def sail_chart_candidates_for_polar(polar_path: Optional[Path] = None) -> List[Path]:
    """Return possible sail-chart filenames for a selected polar.

    Preferred convention is <polar-stem>-SailChart.<ext>, for example
    J109-SailChart.txt for J109.txt.  Legacy same-name charts are still
    accepted as a last resort so existing hut installations keep working.
    """
    if polar_path is None or not polar_path.name:
        return []
    stem = polar_path.stem
    if not stem:
        return []
    suffixes: List[str] = []
    if polar_path.suffix:
        suffixes.append(polar_path.suffix)
    for suffix in sorted(ALLOWED_SAIL_CHART_EXTENSIONS):
        if suffix not in suffixes:
            suffixes.append(suffix)
    candidates: List[Path] = []
    stems = [f"{stem}-SailChart", f"{stem}-sailchart"]
    for chart_stem in stems:
        for suffix in suffixes:
            candidates.append(appstate.SAIL_CHARTS_DIR / f"{chart_stem}{suffix}")
    # Backwards compatibility for v0.92 installations that used the polar
    # filename directly as the sail-chart filename.  Keep this after the new
    # convention so -SailChart files always win.
    for suffix in suffixes:
        candidates.append(appstate.SAIL_CHARTS_DIR / f"{stem}{suffix}")
    seen = set()
    unique: List[Path] = []
    for candidate in candidates:
        key = str(candidate).lower()
        if key not in seen:
            unique.append(candidate)
            seen.add(key)
    return unique


def resolve_sail_chart_path_for_polar(polar_path: Optional[Path] = None) -> Path:
    """Return the sail chart that matches a selected polar, or the default."""
    ensure_sailcharts_dir()
    for candidate in sail_chart_candidates_for_polar(polar_path):
        if candidate.is_file() and candidate.suffix.lower() in ALLOWED_SAIL_CHART_EXTENSIONS:
            return candidate
    return appstate.SAIL_CHART_PATH


def canonical_sail_chart_name_for_polar(polar_filename: str, sail_chart_filename: str = "") -> str:
    """Return the enforced sail-chart filename for a polar upload.

    A polar named ``J109.txt`` must use a matching chart named
    ``J109-SailChart.txt`` (or .csv/.tsv if that was the uploaded chart
    extension).  This keeps the two file types visually distinct while making
    the match deterministic.
    """
    polar_name = Path((polar_filename or "").replace("\\", "/")).name
    stem = Path(polar_name).stem.strip()
    if not stem:
        raise ValueError("A valid polar filename is required.")
    requested_ext = Path((sail_chart_filename or "").replace("\\", "/")).suffix.lower()
    ext = requested_ext if requested_ext in ALLOWED_SAIL_CHART_EXTENSIONS else ".txt"
    return f"{stem}-SailChart{ext}"


def save_uploaded_sail_chart_for_polar(upload: Any, polar_filename: str) -> Optional[str]:
    """Save an uploaded sail chart using the enforced per-polar filename."""
    if not upload or not getattr(upload, "filename", ""):
        return None
    source_name = secure_filename(upload.filename)
    if not source_name or Path(source_name).suffix.lower() not in ALLOWED_SAIL_CHART_EXTENSIONS:
        raise ValueError("Sail chart upload must be a .txt, .tsv or .csv file.")
    ensure_sailcharts_dir()
    chart_name = canonical_sail_chart_name_for_polar(polar_filename, source_name)
    upload.save(appstate.SAIL_CHARTS_DIR / chart_name)
    return chart_name


def installed_sail_chart_for_polar(polar_filename: str) -> Optional[Path]:
    """Return the installed sail chart for a polar, if a polar-specific file exists."""
    polar_path = appstate.POLARS_DIR / Path((polar_filename or "").replace("\\", "/")).name
    for candidate in sail_chart_candidates_for_polar(polar_path):
        if candidate.is_file() and candidate.suffix.lower() in ALLOWED_SAIL_CHART_EXTENSIONS:
            return candidate
    return None


def list_polar_assets() -> List[Dict[str, Any]]:
    """Return polar files with their matching per-polar sail chart, if any."""
    ensure_polars_dir()
    ensure_sailcharts_dir()
    assets: List[Dict[str, Any]] = []
    for polar_name in list_polar_files():
        chart_path = installed_sail_chart_for_polar(polar_name)
        assets.append({
            "polar_file": polar_name,
            "sail_chart_file": chart_path.name if chart_path else "",
            "has_sail_chart": bool(chart_path),
            "expected_sail_chart_file": canonical_sail_chart_name_for_polar(polar_name),
        })
    return assets


def delete_sail_charts_for_polar(polar_filename: str) -> List[str]:
    """Delete all sail-chart files that can be matched to a polar name."""
    ensure_sailcharts_dir()
    deleted: List[str] = []
    polar_name = Path((polar_filename or "").replace("\\", "/")).name
    polar_path = appstate.POLARS_DIR / polar_name
    for candidate in sail_chart_candidates_for_polar(polar_path):
        if candidate.is_file() and candidate.parent == appstate.SAIL_CHARTS_DIR:
            try:
                candidate.unlink()
                deleted.append(candidate.name)
            except OSError:
                pass
    return sorted(set(deleted), key=lambda x: x.lower())


def load_sail_chart(path: Optional[Path] = None) -> Dict[str, Any]:
    """Load a tab-delimited sail chart: first row is TWA headers, first column is TWS."""
    path = path or appstate.SAIL_CHART_PATH
    ensure_sailcharts_dir()
    if not path.exists():
        return {"twas": [], "rows": []}
    rows = list(csv.reader(path.read_text(encoding="utf-8-sig").splitlines(), delimiter="\t"))
    if not rows:
        return {"twas": [], "rows": []}
    twas = []
    for x in rows[0][1:]:
        if str(x).strip():
            try:
                twas.append(int(float(x)))
            except ValueError:
                pass
    chart_rows = []
    for row in rows[1:]:
        if not row or not str(row[0]).strip():
            continue
        try:
            tws = float(row[0])
        except ValueError:
            continue
        sails = row[1:]
        sails += [""] * max(0, len(twas) - len(sails))
        chart_rows.append({"tws": tws, "sails": sails[:len(twas)]})
    return {"twas": twas, "rows": chart_rows}
