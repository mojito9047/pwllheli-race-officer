#!/usr/bin/env python3
"""The land a scene needs, fetched on demand and cached where the next job can use it.

A job says where the race was. It does not carry the coastline, because the
coastline is the same for every race at a club and 54,000 height samples in
every job would be silly. So the renderer works out which map tiles the scene
covers and gets them: from its own disk if it has them, from the shared cache
in the bucket if another render has already been here, and only otherwise from
Mapbox and Copernicus. Anything it had to fetch goes back into the bucket.

That is the whole point of doing it this way rather than building one big asset
by hand. The first job for a club pays for its area -- 273 tiles and 8.6 MB for
the water Pwllheli usually races on -- and every job after it is free, on any
machine. A passage race out to the Gwylan Islands simply pulls the tiles it
needs on the day. There is nothing for anyone to set up, and nothing to
remember when somebody sets a longer course.

Two secrets live on the render machine and neither may travel in a job, because
a job is publicly readable: the Mapbox token and the bucket's write key. The
elevation data needs no credentials at all; Copernicus serves it from a public
bucket.
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

# Where the shared cache lives in the bucket, beside the jobs and the films.
TILE_PREFIX = "replay3d/assets/tiles"
DEM_PREFIX = "replay3d/assets/dem"

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

class Bucket:
    """The bits of R2 the renderer needs, or nothing at all.

    Constructed with no credentials it still works: everything falls through to
    fetching from source and caching on local disk. That keeps a developer able
    to render without the bucket, and it means a credential problem degrades
    into a slow render rather than a failed one.
    """

    def __init__(self, account_id: str = "", bucket: str = "", access_key: str = "",
                 secret_key: str = "") -> None:
        self.ready = bool(account_id and bucket and access_key and secret_key)
        self.account_id, self.bucket = account_id, bucket
        self.access_key, self.secret_key = access_key, secret_key
        self._r2 = None
        if self.ready:
            root = os.path.dirname(os.path.dirname(_HERE))
            if root not in sys.path:
                sys.path.insert(0, root)
            # core/r2.py is a standalone S3 signer: no database, no app. Using it
            # rather than writing a second one keeps one implementation of SigV4.
            from core import r2 as _r2
            self._r2 = _r2

    def get(self, key: str) -> Optional[bytes]:
        if not self.ready:
            return None
        try:
            return self._r2.get_object(self.account_id, self.bucket, key,
                                       self.access_key, self.secret_key)
        except Exception:
            return None

    def put(self, key: str, body: bytes, content_type: str) -> bool:
        if not self.ready:
            return False
        try:
            self._r2.put_object(self.account_id, self.bucket, key, body,
                                self.access_key, self.secret_key, content_type=content_type)
            return True
        except Exception:
            return False


def _cached_file(path: str, key: str, bucket: Bucket, fetch, content_type: str) -> Tuple[str, str]:
    """Return (path, where it came from): 'disk', 'bucket' or 'source'.

    Local disk first because it is free, then the bucket, then the original.
    Anything fetched from the original is pushed to the bucket so the next
    render -- or the next machine -- does not fetch it again.
    """
    if os.path.exists(path) and os.path.getsize(path) > 0:
        return path, "disk"
    os.makedirs(os.path.dirname(path), exist_ok=True)

    body = bucket.get(key)
    if body:
        with open(path + ".part", "wb") as f:
            f.write(body)
        os.replace(path + ".part", path)
        return path, "bucket"

    body = fetch()
    with open(path + ".part", "wb") as f:
        f.write(body)
    os.replace(path + ".part", path)
    # Each tile is its own object, so two renderers racing on the same one just
    # write the same bytes twice. Nothing assembles a combined file up there.
    bucket.put(key, body, content_type)
    return path, "source"


def _http_get(url: str, timeout: float = 180.0) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def ensure_imagery_tiles(tiles: List[Tuple[int, int, int]], bucket: Bucket,
                         token: Optional[str] = None, workers: int = 6) -> Dict[str, int]:
    """Make sure every satellite tile is on local disk. Returns where they came from."""
    tok = read_token(token)
    counts = {"disk": 0, "bucket": 0, "source": 0}

    def one(z: int, x: int, y: int) -> str:
        path = os.path.join(MAPBOX_CACHE, str(z), str(x), f"{y}.jpg")
        key = f"{TILE_PREFIX}/{z}/{x}/{y}.jpg"
        if os.path.exists(path) and os.path.getsize(path) > 0:
            return "disk"
        body = bucket.get(key)
        if body:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path + ".part", "wb") as f:
                f.write(body)
            os.replace(path + ".part", path)
            return "bucket"
        fetch_tile(z, x, y, tok)                       # writes into the disk cache itself
        with open(path, "rb") as f:
            bucket.put(key, f.read(), "image/jpeg")
        return "source"

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        for got in pool.map(lambda t: one(*t), tiles):
            counts[got] += 1
    return counts


def ensure_dem_tiles(names: List[str], bucket: Bucket) -> Tuple[List[str], Dict[str, int]]:
    """Make sure every elevation tile is on local disk. Returns (paths, provenance)."""
    paths, counts = [], {"disk": 0, "bucket": 0, "source": 0}
    for name in names:
        path = os.path.join(DEM_CACHE, f"{name}.tif")
        key = f"{DEM_PREFIX}/{name}.tif"
        try:
            got_path, where = _cached_file(
                path, key, bucket, lambda n=name: _http_get(f"{DEM_HOST}/{n}/{n}.tif"), "image/tiff")
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

def land_for_scene(scene: Dict[str, Any], out_dir: str, bucket: Bucket, *,
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
    dem_paths, dem_counts = ensure_dem_tiles(dem_names, bucket)
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
        counts = ensure_imagery_tiles(tiles, bucket, token)
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
