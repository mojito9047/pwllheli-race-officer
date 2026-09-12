#!/usr/bin/env python3
"""The land: a height grid from a digital elevation model, and imagery draped on it.

Pure GeoTIFF and numpy. No database and no app, which is the point -- a render
machine builds the land for a scene itself, and the only thing it needs from
the hut is where the race was.

This lived in ``export_race.py`` while terrain was something a developer added
by hand with ``--dem``. It moved when the renderer started fetching its own
tiles, because the exporter imports the app and the renderer must not.
"""
from __future__ import annotations

import math
import os
from typing import Any, Dict, Optional, Tuple


# ---------------------------------------------------------------------------
# Terrain (optional).

def _geotiff_georef(tif: Any) -> Tuple[float, float, float, float]:
    """Return (lon of pixel-0 left edge, lat of row-0 top edge, dlon, dlat) from GeoTIFF tags."""
    page = tif.pages[0]
    tags = page.tags
    scale = tags.get("ModelPixelScaleTag")
    tie = tags.get("ModelTiepointTag")
    if scale is None or tie is None:
        raise ValueError("GeoTIFF has no ModelPixelScale/ModelTiepoint tags; only plain geographic GeoTIFFs are supported")
    sx, sy = float(scale.value[0]), float(scale.value[1])
    i, j, _k, x, y = (float(v) for v in tie.value[:5])
    # Tiepoint maps raster (i, j) to model (x, y); PixelIsArea puts (0,0) on the corner.
    lon0 = x - i * sx
    lat0 = y + j * sy
    return lon0, lat0, sx, -sy


def terrain_grid(dem_path: str, lat0: float, lon0: float, radius_m: float, cell_m: float) -> Dict[str, Any]:
    """Sample a regular height grid (metres, sea = 0) around the origin from a GeoTIFF DEM.

    Nearest-cell sampling: the Blender mesh is far coarser than a 30 m DEM, so
    interpolation would add nothing visible. Values at or below sea level become
    0 so the sea plane meets the shore cleanly.
    """
    import numpy as np
    import tifffile

    with tifffile.TiffFile(dem_path) as tif:
        lon_edge, lat_edge, dlon, dlat = _geotiff_georef(tif)
        data = tif.asarray()
    if data.ndim == 3:
        data = data[0]
    rows, cols = data.shape
    nodata = None
    n = int(round(2 * radius_m / cell_m)) + 1
    xs = np.linspace(-radius_m, radius_m, n)
    ys = np.linspace(-radius_m, radius_m, n)
    m_per_deg_lat = 111_320.0
    m_per_deg_lon = 111_320.0 * math.cos(math.radians(lat0))
    lons = lon0 + xs / m_per_deg_lon
    lats = lat0 + ys / m_per_deg_lat
    ci = np.clip(np.floor((lons - lon_edge) / dlon).astype(int), 0, cols - 1)
    ri = np.clip(np.floor((lats - lat_edge) / dlat).astype(int), 0, rows - 1)
    inside_lon = (lons >= lon_edge) & (lons <= lon_edge + cols * dlon)
    inside_lat = (lats <= lat_edge) & (lats >= lat_edge + rows * dlat)
    grid = data[np.ix_(ri, ci)].astype(float)
    grid[~np.isfinite(grid)] = 0.0
    if nodata is not None:
        grid[grid == nodata] = 0.0
    grid[grid < 0.0] = 0.0
    grid[~inside_lat, :] = 0.0
    grid[:, ~inside_lon] = 0.0
    return {
        "source": os.path.basename(dem_path),
        "x0": float(xs[0]), "y0": float(ys[0]),
        "cell_m": float(cell_m), "nx": n, "ny": n,
        # Row-major, y (north) outer, x (east) inner; heights in metres.
        "heights": [round(float(v), 1) for v in grid.ravel()],
        "max_height_m": round(float(grid.max()), 1),
    }


def imagery_credit_for(image_path: str, override: Optional[str] = None) -> str:
    """The attribution line an imagery source requires, read from the GeoTIFF's description.

    fetch_mapbox.py and fetch_sentinel2.py both stamp their terms into the file,
    so a race exported months later still carries the right credit.
    """
    if override:
        return override
    try:
        import tifffile
        with tifffile.TiffFile(image_path) as tif:
            tag = tif.pages[0].tags.get("ImageDescription")
            desc = str(tag.value) if tag else ""
    except Exception:
        desc = ""
    if "Mapbox" in desc:
        return "© Mapbox © OpenStreetMap"
    if "Copernicus" in desc:
        return "Contains modified Copernicus Sentinel data"
    return desc.strip()


# How many pixels across the satellite drape may be. Measured on the Pwllheli
# bay scene, which is 14.8 km across:
#
#   2048   7.24 m/px    4.3 MB   the harbour is a smear
#   4096   3.62 m/px   14.1 MB   breakwater, road and buildings resolve
#   8192   1.81 m/px   42.8 MB   sharper again, but well past the knee
#  16384   1.44 m/px            the source runs out first, at 10,281 px
#
# 4096 is the step that matters; the one above it costs three times the disk in
# every job folder for a good deal less. Only beyond 16384 would fetching the
# tiles at a higher zoom mean anything, and that is 4x the tiles for a mosaic
# nobody wants to hold in memory.
DRAPE_MAX_PX = 4096


def imagery_for_terrain(image_path: str, terrain: Dict[str, Any], lat0: float, lon0: float,
                        out_png: str, max_px: int = DRAPE_MAX_PX) -> Dict[str, Any]:
    """Crop a georeferenced lat/lon RGB GeoTIFF to the terrain grid's footprint and save a PNG.

    The PNG is resampled onto the terrain's own metre frame, so pixel (u, v) in
    0..1 is the point ``x0 + u * width, y0 + v * height`` and Blender can map
    it with plain grid UVs. Row 0 of the PNG is the *north* edge as images
    expect; the builder's UVs take that into account.
    """
    import numpy as np
    import tifffile
    from PIL import Image

    with tifffile.TiffFile(image_path) as tif:
        lon_edge, lat_edge, dlon, dlat = _geotiff_georef(tif)
        data = tif.asarray()
    if data.ndim == 2:
        data = np.stack([data] * 3, axis=-1)
    if data.shape[-1] > 3:
        data = data[..., :3]
    rows, cols = data.shape[:2]

    x0, y0 = float(terrain["x0"]), float(terrain["y0"])
    width = (int(terrain["nx"]) - 1) * float(terrain["cell_m"])
    height = (int(terrain["ny"]) - 1) * float(terrain["cell_m"])
    m_per_deg_lat = 111_320.0
    m_per_deg_lon = 111_320.0 * math.cos(math.radians(lat0))
    # Output resolution: the source's, capped. The cap is what decides how
    # sharp the coastline is, not the zoom the tiles were fetched at -- at 2048
    # the drape was 7.2 m/px over a 14.8 km square while the z15 mosaic under
    # it held 1.44, so four fifths of what had been downloaded, mosaicked and
    # stored was thrown away in this one line.
    src_px_m = min(abs(dlon) * m_per_deg_lon, abs(dlat) * m_per_deg_lat)
    n = int(min(max_px, max(256, round(width / src_px_m))))
    xs = x0 + np.linspace(0.0, width, n)
    ys = y0 + np.linspace(height, 0.0, n)          # north row first
    lons = lon0 + xs / m_per_deg_lon
    lats = lat0 + ys / m_per_deg_lat
    ci = np.clip(np.floor((lons - lon_edge) / dlon).astype(int), 0, cols - 1)
    ri = np.clip(np.floor((lats - lat_edge) / dlat).astype(int), 0, rows - 1)
    out = data[np.ix_(ri, ci)]
    if out.dtype != np.uint8:
        hi = float(np.percentile(out, 99.5)) or 1.0
        out = np.clip(out.astype(float) / hi * 255.0, 0, 255).astype(np.uint8)
    Image.fromarray(out, "RGB").save(out_png)
    return {"file": os.path.basename(out_png), "px": n, "source": os.path.basename(image_path),
            "credit": imagery_credit_for(image_path)}
