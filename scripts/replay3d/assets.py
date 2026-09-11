#!/usr/bin/env python3
"""The land a scene needs, fetched on demand and cached on this machine.

A job says where the race was. It does not carry the coastline, because the
coastline is the same for every race at a club and 54,000 height samples in
every job would be silly. So the renderer works out which map tiles the scene
covers and gets them: from its own disk if it has them, and only otherwise
from Mapbox and Copernicus.

The cache is a directory under runtime/, shared by every job this machine
renders. The first film over a stretch of water pays for its area -- 273 tiles
and 8.6 MB for the water Pwllheli usually races on -- and every film after it
is free. A passage race out to the Gwylan Islands simply pulls the tiles it
needs on the day. There is nothing for anyone to set up, and nothing to
remember when somebody sets a longer course.

Local, not shared through R2. A club renders a handful of courses and the
whole of Pwllheli bay is a few hundred tiles, comfortably inside Mapbox's free
tier, so a copy in the bucket saved nothing that mattered and cost a cache to
keep in step, an upload on every miss, and the renderer needing write access
to a bucket it otherwise only ever reads a job from.

One secret lives on the render machine and may not travel in a job, because a
job is publicly readable: the Mapbox token. The elevation data needs no
credentials at all; Copernicus serves it from a public bucket.
"""
from __future__ import annotations

import concurrent.futures
import math
import os
import sys
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

from fetch_mapbox import CACHE_DIR as MAPBOX_CACHE, TILE_PX, fetch_tile, lat_to_tile_y, lon_to_tile_x, read_token  # noqa: E402
from terrain import imagery_for_terrain, terrain_grid  # noqa: E402

# Copernicus GLO-30, one degree per tile, served without credentials.
DEM_HOST = "https://copernicus-dem-30m.s3.amazonaws.com"
DEM_CACHE = os.path.join(os.path.dirname(os.path.dirname(_HERE)), "runtime", "replay3d", "dem_cache")

USER_AGENT = "pwllheli-race-officer-replay3d/1.0"

# How far beyond the racing the land should reach. The sea plane runs to nine
# times the extent, and a horizon of nothing but water looks wrong, so the
# coastline wants to be well outside the course.
LAND_MARGIN_M = 6000.0
DEFAULT_ZOOM = 15
DEFAULT_CELL_M = 60.0


# ---------------------------------------------------------------------------
# Which tiles a scene needs.

def scene_bounds(scene: Dict[str, Any], margin_m: float = LAND_MARGIN_M) -> Dict[str, float]:
    """The latitude and longitude box the land has to cover for this scene."""
    origin = scene.get("origin") or {}
    lat0, lon0 = float(origin["lat"]), float(origin["lon"])
    reach = float(scene.get("extent_m") or 500.0) + float(margin_m)
    dlat = reach / 111_320.0
    dlon = reach / (111_320.0 * max(0.05, math.cos(math.radians(lat0))))
    return {"lat0": lat0, "lon0": lon0, "reach_m": reach,
            "south": lat0 - dlat, "north": lat0 + dlat,
            "west": lon0 - dlon, "east": lon0 + dlon}


def imagery_tiles_for(bounds: Dict[str, float], zoom: int = DEFAULT_ZOOM) -> List[Tuple[int, int, int]]:
    """Every (z, x, y) satellite tile covering the box."""
    x_min = int(math.floor(lon_to_tile_x(bounds["west"], zoom)))
    x_max = int(math.floor(lon_to_tile_x(bounds["east"], zoom)))
    y_min = int(math.floor(lat_to_tile_y(bounds["north"], zoom)))   # north edge is the low y
    y_max = int(math.floor(lat_to_tile_y(bounds["south"], zoom)))
    return [(zoom, x, y) for x in range(x_min, x_max + 1) for y in range(y_min, y_max + 1)]


def dem_tile_name(lat: int, lon: int) -> str:
    """The Copernicus name for the one-degree tile whose south-west corner is here."""
    ns = f"{'N' if lat >= 0 else 'S'}{abs(lat):02d}"
    ew = f"{'E' if lon >= 0 else 'W'}{abs(lon):03d}"
    return f"Copernicus_DSM_COG_10_{ns}_00_{ew}_00_DEM"


def dem_tiles_for(bounds: Dict[str, float]) -> List[str]:
    """Every one-degree elevation tile the box touches. Usually one; two at a border."""
    names = []
    for lat in range(int(math.floor(bounds["south"])), int(math.floor(bounds["north"])) + 1):
        for lon in range(int(math.floor(bounds["west"])), int(math.floor(bounds["east"])) + 1):
            names.append(dem_tile_name(lat, lon))
    return names


# ---------------------------------------------------------------------------
# The shared cache.

def _cached_file(path: str, fetch) -> Tuple[str, str]:
    """Return (path, where it came from): 'disk' or 'source'.

    The cache is a directory on this machine, shared by every job it renders,
    so the first film over a stretch of water pays for the tiles and every
    film after it is free. It deliberately does not also push them to R2: a
    club renders a handful of courses, the download is a few hundred tiles
    inside Mapbox's free tier, and a second copy in the bucket bought nothing
    but a second thing to keep in step.
    """
    if os.path.exists(path) and os.path.getsize(path) > 0:
        return path, "disk"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    body = fetch()
    # Written aside and moved into place, so an interrupted download cannot
    # leave a half tile in the cache to be trusted for ever afterwards.
    with open(path + ".part", "wb") as f:
        f.write(body)
    os.replace(path + ".part", path)
    return path, "source"


def _http_get(url: str, timeout: float = 180.0) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def ensure_imagery_tiles(tiles: List[Tuple[int, int, int]],
                         token: Optional[str] = None, workers: int = 6) -> Dict[str, int]:
    """Make sure every satellite tile is on local disk. Returns where they came from."""
    tok = read_token(token)
    counts = {"disk": 0, "source": 0}

    def one(z: int, x: int, y: int) -> str:
        path = os.path.join(MAPBOX_CACHE, str(z), str(x), f"{y}.jpg")
        if os.path.exists(path) and os.path.getsize(path) > 0:
            return "disk"
        fetch_tile(z, x, y, tok)                       # writes into the disk cache itself
        return "source"

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        for got in pool.map(lambda t: one(*t), tiles):
            counts[got] += 1
    return counts


def ensure_dem_tiles(names: List[str]) -> Tuple[List[str], Dict[str, int]]:
    """Make sure every elevation tile is on local disk. Returns (paths, provenance)."""
    paths, counts = [], {"disk": 0, "source": 0}
    for name in names:
        path = os.path.join(DEM_CACHE, f"{name}.tif")
        try:
            got_path, where = _cached_file(
                path, lambda n=name: _http_get(f"{DEM_HOST}/{n}/{n}.tif"))
        except Exception as exc:
            # A tile that is all sea is not published at all, which is not an
            # error: the grid simply has no land in that square.
            print(f"  dem {name}: not available ({type(exc).__name__})")
            continue
        paths.append(got_path)
        counts[where] += 1
    return paths, counts


# ---------------------------------------------------------------------------
# Putting the land into a scene.

def land_for_scene(scene: Dict[str, Any], out_dir: str, *,
                   zoom: int = DEFAULT_ZOOM, cell_m: float = DEFAULT_CELL_M,
                   token: Optional[str] = None, margin_m: float = LAND_MARGIN_M
                   ) -> Optional[Dict[str, Any]]:
    """Fetch what is missing, build the height grid and the drape, return the terrain.

    Returns None rather than raising when the land cannot be built. A film with
    sea and boats and no coastline is worth having; a render that dies two
    hours in because a tile server was down is not.
    """
    bounds = scene_bounds(scene, margin_m)
    race_id = int((scene.get("race") or {}).get("id") or 0)

    dem_names = dem_tiles_for(bounds)
    dem_paths, dem_counts = ensure_dem_tiles(dem_names)
    if not dem_paths:
        print("  land: no elevation tiles, so the scene gets sea only")
        return None
    print(f"  elevation: {len(dem_paths)} tile(s) {dem_counts}")

    # One tile is the normal case at a club. Two only happens on a passage race
    # that crosses a degree line, and stitching those is not built yet.
    if len(dem_paths) > 1:
        print(f"  land: the scene spans {len(dem_paths)} elevation tiles; using the one "
              f"the origin falls in")
    origin_tile = dem_tile_name(int(math.floor(bounds["lat0"])), int(math.floor(bounds["lon0"])))
    dem_path = next((p for p in dem_paths if origin_tile in p), dem_paths[0])

    grid = terrain_grid(dem_path, bounds["lat0"], bounds["lon0"], bounds["reach_m"], cell_m)

    tiles = imagery_tiles_for(bounds, zoom)
    try:
        counts = ensure_imagery_tiles(tiles, token)
        print(f"  imagery: {len(tiles)} tile(s) {counts}")
        mosaic = _mosaic_path(bounds, zoom, out_dir)
        _build_mosaic(tiles, bounds, zoom, mosaic)
        png = os.path.join(out_dir, f"race_{race_id}_imagery.png")
        info = imagery_for_terrain(mosaic, grid, bounds["lat0"], bounds["lon0"], png)
        grid["imagery"] = info["file"]
        grid["imagery_info"] = info
    except Exception as exc:
        print(f"  imagery: skipped ({type(exc).__name__}: {exc})")
    return grid


def _mosaic_path(bounds: Dict[str, float], zoom: int, out_dir: str) -> str:
    return os.path.join(out_dir, f"imagery_z{zoom}_{bounds['lat0']:.4f}_{bounds['lon0']:.4f}.tif")


def _build_mosaic(tiles: List[Tuple[int, int, int]], bounds: Dict[str, float], zoom: int,
                  out_path: str) -> str:
    """Stitch the cached tiles into one lat/lon GeoTIFF for draping."""
    import numpy as np
    from PIL import Image
    import tifffile

    if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
        return out_path
    xs = sorted({t[1] for t in tiles})
    ys = sorted({t[2] for t in tiles})
    canvas = Image.new("RGB", (len(xs) * TILE_PX, len(ys) * TILE_PX))
    for z, x, y in tiles:
        path = os.path.join(MAPBOX_CACHE, str(z), str(x), f"{y}.jpg")
        if not os.path.exists(path):
            continue
        with Image.open(path) as im:
            canvas.paste(im.convert("RGB").resize((TILE_PX, TILE_PX)),
                         ((x - xs[0]) * TILE_PX, (y - ys[0]) * TILE_PX))

    def tile_x_to_lon(tx: float) -> float:
        return tx / (2 ** zoom) * 360.0 - 180.0

    def tile_y_to_lat(ty: float) -> float:
        n = math.pi - 2.0 * math.pi * ty / (2 ** zoom)
        return math.degrees(math.atan(math.sinh(n)))

    west, east = tile_x_to_lon(xs[0]), tile_x_to_lon(xs[-1] + 1)
    north, south = tile_y_to_lat(ys[0]), tile_y_to_lat(ys[-1] + 1)
    arr = np.asarray(canvas)
    # The description is not decoration: imagery_credit_for reads the terms back
    # out of it months later, so a film exported next season still carries the
    # attribution Mapbox requires. Without it the credit line comes out as
    # tifffile's own default, which is the array shape.
    description = ("Mapbox satellite tiles, zoom %d. (c) Mapbox (c) OpenStreetMap. "
                   "Cached and re-rendered under the Mapbox terms of service." % zoom)
    # ModelTiepoint + ModelPixelScale, the same georeferencing fetch_mapbox writes.
    tifffile.imwrite(
        out_path, arr, photometric="rgb", description=description,
        extratags=[(33922, "d", 6, (0.0, 0.0, 0.0, west, north, 0.0), True),
                   (33550, "d", 3, ((east - west) / arr.shape[1],
                                    (north - south) / arr.shape[0], 0.0), True)],
    )
    return out_path
