#!/usr/bin/env python3
"""Fetch a cloud-free Sentinel-2 true-colour image of the bay as a lat/lon GeoTIFF.

For draping over the replay terrain (``export_race.py --imagery``). Sentinel-2 is
the EU's Copernicus imaging satellite: 10 m pixels, free and open under the
Copernicus data licence (credit "Contains modified Copernicus Sentinel data").
Nothing here touches Google or any other imagery whose terms forbid this use.

    python scripts/replay3d/fetch_sentinel2.py --search                 # list clear scenes
    python scripts/replay3d/fetch_sentinel2.py --fetch S2B_30UUD_20250712_0_L2A --out data/dem/pwllheli_s2.tif

The search asks the public Earth Search STAC catalogue (no account needed) for
scenes over the marks with little cloud, newest and clearest first. The fetch
reads only the tiles of the scene's cloud-optimised GeoTIFF that cover the area
(HTTP range requests, a few megabytes rather than the whole 100 MB+ scene),
reprojects them from UTM to plain lat/lon, and writes a GeoTIFF the exporter
understands.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

STAC_URL = "https://earth-search.aws.element84.com/v1/search"
# Centre of the CHPSC racing area and the half-width of the area wanted, in degrees.
DEFAULT_LAT, DEFAULT_LON = 52.8791, -4.3993
DEFAULT_HALF_DEG = 0.09           # ~10 km north-south, ~6 km east-west either side
OUT_RES_DEG = 0.0001              # ~11 m; Sentinel-2 is 10 m
USER_AGENT = "pwllheli-race-officer-replay3d/1.0"


# ---------------------------------------------------------------------------
# STAC search.

def search(lat: float, lon: float, half_deg: float, max_cloud: float, since: str, limit: int) -> List[Dict[str, Any]]:
    body = {
        "collections": ["sentinel-2-l2a"],
        "bbox": [lon - half_deg, lat - half_deg, lon + half_deg, lat + half_deg],
        "datetime": f"{since}T00:00:00Z/..",
        "query": {"eo:cloud_cover": {"lt": max_cloud}},
        "limit": limit,
        "sortby": [{"field": "properties.eo:cloud_cover", "direction": "asc"}],
    }
    req = urllib.request.Request(STAC_URL, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as resp:
        items = json.load(resp).get("features", [])
    out = []
    for it in items:
        p = it["properties"]
        visual = it["assets"].get("visual") or {}
        out.append({
            "id": it["id"],
            "date": p.get("datetime", "")[:10],
            "cloud_pct": p.get("eo:cloud_cover"),
            "tile": p.get("grid:code") or p.get("s2:mgrs_tile"),
            "epsg": p.get("proj:epsg") or (p.get("proj:code") or "").replace("EPSG:", ""),
            "href": visual.get("href"),
            "shape": visual.get("proj:shape") or p.get("proj:shape"),
            "transform": visual.get("proj:transform") or p.get("proj:transform"),
        })
    return out


# ---------------------------------------------------------------------------
# Lazy HTTP range reader so tifffile reads only the tiles it is asked for.

class RangeFile:
    """A read-only file-like object over an HTTP URL, fetching 1 MiB blocks on demand."""

    BLOCK = 1 << 20

    def __init__(self, url: str) -> None:
        self.url = url
        self.pos = 0
        self.blocks: Dict[int, bytes] = {}
        self.fetched = 0
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=60) as resp:
            self.size = int(resp.headers["Content-Length"])
        self.name = url.rsplit("/", 1)[-1]

    def _block(self, idx: int) -> bytes:
        blk = self.blocks.get(idx)
        if blk is None:
            start = idx * self.BLOCK
            end = min(start + self.BLOCK, self.size) - 1
            req = urllib.request.Request(self.url, headers={"Range": f"bytes={start}-{end}", "User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=120) as resp:
                blk = resp.read()
            self.blocks[idx] = blk
            self.fetched += len(blk)
        return blk

    def read(self, n: int = -1) -> bytes:
        if n < 0:
            n = self.size - self.pos
        n = max(0, min(n, self.size - self.pos))
        out = bytearray()
        while n > 0:
            idx, off = divmod(self.pos, self.BLOCK)
            blk = self._block(idx)
            chunk = blk[off:off + n]
            out.extend(chunk)
            self.pos += len(chunk)
            n -= len(chunk)
            if not chunk:
                break
        return bytes(out)

    def seek(self, offset: int, whence: int = 0) -> int:
        if whence == 0:
            self.pos = offset
        elif whence == 1:
            self.pos += offset
        else:
            self.pos = self.size + offset
        return self.pos

    def tell(self) -> int:
        return self.pos

    def seekable(self) -> bool:
        return True

    def readable(self) -> bool:
        return True

    def close(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Geodesy: lat/lon (WGS84) to UTM, enough to index a Sentinel-2 tile.

def latlon_to_utm(lat: float, lon: float, zone: int, north: bool = True) -> Tuple[float, float]:
    a, f = 6378137.0, 1 / 298.257223563
    k0 = 0.9996
    e2 = f * (2 - f)
    ep2 = e2 / (1 - e2)
    lon0 = math.radians((zone - 1) * 6 - 180 + 3)
    phi, lam = math.radians(lat), math.radians(lon)
    n = a / math.sqrt(1 - e2 * math.sin(phi) ** 2)
    t = math.tan(phi) ** 2
    c = ep2 * math.cos(phi) ** 2
    A = math.cos(phi) * (lam - lon0)
    m = a * ((1 - e2 / 4 - 3 * e2 ** 2 / 64 - 5 * e2 ** 3 / 256) * phi
             - (3 * e2 / 8 + 3 * e2 ** 2 / 32 + 45 * e2 ** 3 / 1024) * math.sin(2 * phi)
             + (15 * e2 ** 2 / 256 + 45 * e2 ** 3 / 1024) * math.sin(4 * phi)
             - (35 * e2 ** 3 / 3072) * math.sin(6 * phi))
    x = k0 * n * (A + (1 - t + c) * A ** 3 / 6 + (5 - 18 * t + t ** 2 + 72 * c - 58 * ep2) * A ** 5 / 120) + 500000.0
    y = k0 * (m + n * math.tan(phi) * (A ** 2 / 2 + (5 - t + 9 * c + 4 * c ** 2) * A ** 4 / 24
                                       + (61 - 58 * t + t ** 2 + 600 * c - 330 * ep2) * A ** 6 / 720))
    if not north:
        y += 10000000.0
    return x, y


# ---------------------------------------------------------------------------
# Windowed read of the cloud-optimised GeoTIFF.

def fetch_window(item: Dict[str, Any], lat: float, lon: float, half_deg: float, out_path: str) -> Dict[str, Any]:
    import numpy as np
    import tifffile

    if not item.get("href"):
        raise SystemExit("scene has no 'visual' (true colour) asset")
    epsg = int(item["epsg"])
    zone = epsg % 100
    north = 32600 <= epsg < 32700

    fh = RangeFile(item["href"])
    tif = tifffile.TiffFile(fh)
    page = tif.pages[0]
    if not page.is_tiled:
        raise SystemExit("expected a tiled cloud-optimised GeoTIFF")
    # Georeference from the file's own tags (UTM metres).
    scale = page.tags["ModelPixelScaleTag"].value
    tie = page.tags["ModelTiepointTag"].value
    px, py = float(scale[0]), float(scale[1])
    e0, n0 = float(tie[3]) - float(tie[0]) * px, float(tie[4]) + float(tie[1]) * py
    rows, cols = page.imagelength, page.imagewidth
    th, tw = page.tilelength, page.tilewidth
    tiles_across = (cols + tw - 1) // tw

    # Output grid in lat/lon, north row first.
    n_lon = int(round(2 * half_deg / OUT_RES_DEG))
    n_lat = n_lon
    lons = lon - half_deg + (np.arange(n_lon) + 0.5) * (2 * half_deg / n_lon)
    lats = lat + half_deg - (np.arange(n_lat) + 0.5) * (2 * half_deg / n_lat)
    LAT, LON = np.meshgrid(lats, lons, indexing="ij")
    # Vectorised projection would be nicer; 3-4 million points in a Python loop is too slow,
    # so project the grid corners' rows/cols of a coarse lattice and interpolate. UTM is very
    # nearly affine over 20 km, so a 64 x 64 lattice is accurate to well under a pixel.
    lat_k = np.linspace(lats[-1], lats[0], 65)
    lon_k = np.linspace(lons[0], lons[-1], 65)
    E = np.empty((65, 65))
    N = np.empty((65, 65))
    for i, la in enumerate(lat_k):
        for j, lo in enumerate(lon_k):
            E[i, j], N[i, j] = latlon_to_utm(la, lo, zone, north)
    from numpy import interp

    def bilinear(grid: Any, la: Any, lo: Any) -> Any:
        fi = interp(la, lat_k, np.arange(65))
        fj = interp(lo, lon_k, np.arange(65))
        i0 = np.clip(np.floor(fi).astype(int), 0, 63)
        j0 = np.clip(np.floor(fj).astype(int), 0, 63)
        di, dj = fi - i0, fj - j0
        return (grid[i0, j0] * (1 - di) * (1 - dj) + grid[i0 + 1, j0] * di * (1 - dj)
                + grid[i0, j0 + 1] * (1 - di) * dj + grid[i0 + 1, j0 + 1] * di * dj)

    easting = bilinear(E, LAT, LON)
    northing = bilinear(N, LAT, LON)
    col = np.floor((easting - e0) / px).astype(int)
    row = np.floor((n0 - northing) / py).astype(int)
    inside = (col >= 0) & (col < cols) & (row >= 0) & (row < rows)
    if not inside.any():
        raise SystemExit("the area lies outside this scene")

    # Which tiles are needed, then decode only those.
    tile_rows = np.unique(row[inside] // th)
    tile_cols = np.unique(col[inside] // tw)
    out = np.zeros((n_lat, n_lon, 3), dtype=np.uint8)
    n_tiles = 0
    for tr in tile_rows:
        for tc in tile_cols:
            index = int(tr * tiles_across + tc)
            offset = page.dataoffsets[index]
            count = page.databytecounts[index]
            if count == 0:
                continue
            fh.seek(offset)
            data = fh.read(count)
            decoded, _indices, _shape = page.decode(data, index)
            tile = np.asarray(decoded)
            tile = tile.reshape(tile.shape[-3], tile.shape[-2], tile.shape[-1]) if tile.ndim > 3 else tile
            if tile.ndim == 2:
                tile = np.stack([tile] * 3, axis=-1)
            sel = inside & (row // th == tr) & (col // tw == tc)
            out[sel] = tile[(row[sel] - tr * th).clip(0, tile.shape[0] - 1),
                            (col[sel] - tc * tw).clip(0, tile.shape[1] - 1), :3]
            n_tiles += 1

    lon_edge = float(lons[0] - OUT_RES_DEG / 2)
    lat_edge = float(lats[0] + OUT_RES_DEG / 2)
    extratags = [
        (33550, "d", 3, (2 * half_deg / n_lon, 2 * half_deg / n_lat, 0.0), True),   # ModelPixelScaleTag
        (33922, "d", 6, (0.0, 0.0, 0.0, lon_edge, lat_edge, 0.0), True),             # ModelTiepointTag
    ]
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    tifffile.imwrite(out_path, out, photometric="rgb", compression="zlib", extratags=extratags,
                     description=f"Contains modified Copernicus Sentinel data {item['date'][:4]}; scene {item['id']}")
    return {"out": out_path, "px": [n_lat, n_lon], "tiles_read": n_tiles, "bytes_fetched": fh.fetched,
            "scene": item["id"], "date": item["date"], "cloud_pct": item["cloud_pct"]}


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--search", action="store_true", help="list clear scenes over the area")
    ap.add_argument("--fetch", metavar="SCENE_ID", help="fetch this scene's true-colour window")
    ap.add_argument("--out", default="data/dem/pwllheli_sentinel2.tif", help="output GeoTIFF (with --fetch)")
    ap.add_argument("--lat", type=float, default=DEFAULT_LAT)
    ap.add_argument("--lon", type=float, default=DEFAULT_LON)
    ap.add_argument("--half-deg", type=float, default=DEFAULT_HALF_DEG, help="half-width of the area in degrees")
    ap.add_argument("--max-cloud", type=float, default=15.0, help="scene cloud cover ceiling, percent")
    ap.add_argument("--since", default="2024-04-01", help="earliest acquisition date")
    ap.add_argument("--limit", type=int, default=30)
    args = ap.parse_args(argv)

    if not args.search and not args.fetch:
        ap.error("--search or --fetch is required")
    items = search(args.lat, args.lon, args.half_deg, args.max_cloud, args.since, args.limit)
    if args.search:
        if not items:
            print("no scenes matched; raise --max-cloud or widen --since")
        for it in items:
            print(f"{it['id']:<34} {it['date']}  cloud {it['cloud_pct']:5.1f}%  tile {it['tile']}  EPSG:{it['epsg']}")
        return 0
    item = next((it for it in items if it["id"] == args.fetch), None)
    if item is None:
        raise SystemExit(f"{args.fetch} is not in the search results; run --search (adjust --max-cloud/--since)")
    info = fetch_window(item, args.lat, args.lon, args.half_deg, args.out)
    print(json.dumps(info, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
