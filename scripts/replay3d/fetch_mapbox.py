#!/usr/bin/env python3
"""Fetch Mapbox satellite imagery of the bay as a lat/lon GeoTIFF, caching the tiles.

Sharper than the free Sentinel-2 route (sub-metre in most places against 10 m),
and Mapbox's terms do allow caching tiles and using them in your own renders
with attribution -- unlike Google, Bing or Esri, whose imagery may only be shown
inside their own map products.

    python scripts/replay3d/fetch_mapbox.py --zoom 15
    python scripts/replay3d/fetch_mapbox.py --zoom 16 --half-deg 0.05 --out data/dem/pwllheli_mapbox_z16.tif

**Anything published from the imagery must carry "(C) Mapbox (C) OpenStreetMap".**

The access token is read from ``--token``, then ``$MAPBOX_TOKEN``, then
``runtime/replay3d/mapbox_token.txt`` (gitignored). It is never written to a
tracked file, and never appears in the output.

Tiles are cached under ``runtime/replay3d/mapbox_cache/<z>/<x>/<y>.jpg``, so a
second fetch of the same area costs nothing and re-running after a change of
extent only downloads what it does not already hold.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import math
import os
import sys
import time
import urllib.error
import urllib.request
from typing import Dict, List, Optional, Tuple

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CACHE_DIR = os.path.join(_ROOT, "runtime", "replay3d", "mapbox_cache")
TOKEN_FILE = os.path.join(_ROOT, "runtime", "replay3d", "mapbox_token.txt")
TILESET = "mapbox.satellite"
TILE_PX = 512                      # @2x tiles
CREDIT = "(C) Mapbox (C) OpenStreetMap"
USER_AGENT = "pwllheli-race-officer-replay3d/1.0"

# Centre of the CHPSC racing area, and the default half-width of the area wanted.
DEFAULT_LAT, DEFAULT_LON = 52.8791, -4.3993
DEFAULT_HALF_DEG = 0.09


def read_token(explicit: Optional[str] = None) -> str:
    if explicit:
        return explicit.strip()
    env = os.environ.get("MAPBOX_TOKEN")
    if env:
        return env.strip()
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, "r", encoding="utf-8") as f:
            token = f.read().strip()
        if token:
            return token
    raise SystemExit(f"no Mapbox token: pass --token, set MAPBOX_TOKEN, or write one to {TOKEN_FILE}")


# ---------------------------------------------------------------------------
# Web Mercator tile maths.

def lon_to_tile_x(lon: float, z: float) -> float:
    return (lon + 180.0) / 360.0 * (2.0 ** z)


def lat_to_tile_y(lat: float, z: float) -> float:
    r = math.radians(max(-85.05112878, min(85.05112878, lat)))
    return (1.0 - math.log(math.tan(r) + 1.0 / math.cos(r)) / math.pi) / 2.0 * (2.0 ** z)


def tile_url(z: int, x: int, y: int, token: str) -> str:
    return f"https://api.mapbox.com/v4/{TILESET}/{z}/{x}/{y}@2x.jpg90?access_token={token}"


def fetch_tile(z: int, x: int, y: int, token: str, retries: int = 3) -> Tuple[str, bool]:
    """Return (path, downloaded). Cached tiles are reused untouched."""
    path = os.path.join(CACHE_DIR, str(z), str(x), f"{y}.jpg")
    if os.path.exists(path) and os.path.getsize(path) > 0:
        return path, False
    os.makedirs(os.path.dirname(path), exist_ok=True)
    last: Optional[Exception] = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(tile_url(z, x, y, token), headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=45) as resp:
                data = resp.read()
            if not data:
                raise ValueError("empty tile")
            tmp = path + ".part"
            with open(tmp, "wb") as f:
                f.write(data)
            os.replace(tmp, path)
            return path, True
        except urllib.error.HTTPError as ex:
            if ex.code in (401, 403):
                raise SystemExit(f"Mapbox rejected the token ({ex.code}); check it is valid and has tile scope")
            last = ex
        except Exception as ex:  # transient network trouble
            last = ex
        time.sleep(1.0 + attempt)
    raise SystemExit(f"tile {z}/{x}/{y} failed after {retries} tries: {last!r}")


# ---------------------------------------------------------------------------
# Fetch and mosaic.

def fetch_area(lat: float, lon: float, half_deg: float, zoom: int, out_path: str,
               max_px: int = 4096, token: Optional[str] = None, workers: int = 6) -> Dict[str, object]:
    import numpy as np
    from PIL import Image
    import tifffile

    tok = read_token(token)
    lon0, lon1 = lon - half_deg, lon + half_deg
    lat0, lat1 = lat - half_deg, lat + half_deg          # south, north

    x_min = int(math.floor(lon_to_tile_x(lon0, zoom)))
    x_max = int(math.floor(lon_to_tile_x(lon1, zoom)))
    y_min = int(math.floor(lat_to_tile_y(lat1, zoom)))   # north edge is the low y
    y_max = int(math.floor(lat_to_tile_y(lat0, zoom)))
    n_tiles = (x_max - x_min + 1) * (y_max - y_min + 1)
    span_m = 2 * half_deg * 111_320.0 * math.cos(math.radians(lat))
    res_m = 156543.03392 * math.cos(math.radians(lat)) / (2 ** zoom) / (TILE_PX / 256)
    print(f"zoom {zoom}: {x_max - x_min + 1} x {y_max - y_min + 1} = {n_tiles} tiles "
          f"({res_m:.2f} m/px, area {span_m / 1000:.1f} km across)")

    jobs = [(zoom, x, y) for x in range(x_min, x_max + 1) for y in range(y_min, y_max + 1)]
    downloaded = 0
    paths: Dict[Tuple[int, int], str] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(fetch_tile, z, x, y, tok): (x, y) for z, x, y in jobs}
        for i, fut in enumerate(concurrent.futures.as_completed(futures), start=1):
            x, y = futures[fut]
            path, got = fut.result()
            paths[(x, y)] = path
            downloaded += 1 if got else 0
            if i % 25 == 0 or i == len(jobs):
                print(f"  {i}/{len(jobs)} tiles ({downloaded} downloaded, {i - downloaded} cached)")

    # Mosaic in tile-pixel space.
    mosaic_w = (x_max - x_min + 1) * TILE_PX
    mosaic_h = (y_max - y_min + 1) * TILE_PX
    mosaic = np.zeros((mosaic_h, mosaic_w, 3), dtype=np.uint8)
    for (x, y), path in paths.items():
        with Image.open(path) as im:
            tile = np.asarray(im.convert("RGB"))
        if tile.shape[0] != TILE_PX:
            with Image.open(path) as im:
                tile = np.asarray(im.convert("RGB").resize((TILE_PX, TILE_PX), Image.LANCZOS))
        r0 = (y - y_min) * TILE_PX
        c0 = (x - x_min) * TILE_PX
        mosaic[r0:r0 + TILE_PX, c0:c0 + TILE_PX] = tile

    # Resample the mercator mosaic onto a plain lat/lon grid, north row first,
    # which is what export_race.py --imagery expects.
    n = int(min(max_px, max(512, round(span_m / res_m))))
    lons = np.linspace(lon0, lon1, n)
    lats = np.linspace(lat1, lat0, n)
    tx = (lons + 180.0) / 360.0 * (2.0 ** zoom)                # lon_to_tile_x, vectorised
    col = np.clip(((tx - x_min) * TILE_PX).astype(int), 0, mosaic_w - 1)
    ty = np.array([lat_to_tile_y(v, zoom) for v in lats])
    row = np.clip(((ty - y_min) * TILE_PX).astype(int), 0, mosaic_h - 1)
    out = mosaic[np.ix_(row, col)]

    extratags = [
        (33550, "d", 3, (2 * half_deg / n, 2 * half_deg / n, 0.0), True),                 # ModelPixelScaleTag
        (33922, "d", 6, (0.0, 0.0, 0.0, float(lon0), float(lat1), 0.0), True),            # ModelTiepointTag
    ]
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    tifffile.imwrite(out_path, out, photometric="rgb", compression="zlib", extratags=extratags,
                     description=f"Mapbox {TILESET} z{zoom}; {CREDIT}")
    return {"out": out_path, "px": [n, n], "zoom": zoom, "tiles": n_tiles, "downloaded": downloaded,
            "cached": n_tiles - downloaded, "res_m_per_px": round(res_m, 2), "credit": CREDIT}


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", default="data/dem/pwllheli_mapbox.tif", help="output GeoTIFF")
    ap.add_argument("--zoom", type=int, default=15, help="tile zoom (15 ~ 2.9 m/px, 16 ~ 1.4 m/px)")
    ap.add_argument("--lat", type=float, default=DEFAULT_LAT)
    ap.add_argument("--lon", type=float, default=DEFAULT_LON)
    ap.add_argument("--half-deg", type=float, default=DEFAULT_HALF_DEG, help="half-width of the area in degrees")
    ap.add_argument("--max-px", type=int, default=4096, help="cap on the output image's size")
    ap.add_argument("--token", help="Mapbox access token (else $MAPBOX_TOKEN, else the token file)")
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args(argv)

    info = fetch_area(args.lat, args.lon, args.half_deg, args.zoom, args.out,
                      max_px=args.max_px, token=args.token, workers=args.workers)
    print(json.dumps(info, indent=2))
    print(f"\nremember the credit: {CREDIT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
