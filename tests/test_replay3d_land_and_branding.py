"""A film's land covers every elevation tile it needs, and carries no other club's branding.

Copernicus elevation comes in one-degree tiles. Pwllheli's water sits well inside
one (52-53 N, 4-5 W), so the render machine used only the tile the scene's origin
fell in, and everything past that tile's edge came out as sea. Liverpool's does
not: 3 degrees west runs along the Birkenhead shore of the Mersey, and the whole
Wirral bank would have been drawn flat at sea level under its own satellite
picture -- with the camera placement, which treats anything under 3 m as water,
free to put a camera over Birkenhead.

And a film for another club came out with Pwllheli's branding whenever its own
could not be had: the render machine fell back to Pwllheli's manifest (sponsors)
when the job named none, and to the Pwllheli logo shipped with the app for the
title card.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

np = pytest.importorskip("numpy", reason="the height grid is numpy")
tifffile = pytest.importorskip("tifffile", reason="elevation tiles are GeoTIFFs")

_REPLAY3D = Path(__file__).resolve().parents[1] / "scripts" / "replay3d"

# The Mersey, roughly where a Liverpool YC course sits.
ORIGIN = (53.37, -2.96)


def _load(name):
    """Import one of the render scripts the way the render machine does."""
    if str(_REPLAY3D) not in sys.path:
        sys.path.insert(0, str(_REPLAY3D))
    spec = importlib.util.spec_from_file_location(name, _REPLAY3D / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_tile(path: Path, west: float, north: float, height_m: float, cells: int = 120) -> str:
    """A one-degree elevation tile, every cell the same height."""
    data = np.full((cells, cells), height_m, dtype=np.float32)
    tifffile.imwrite(str(path), data, extratags=[
        (33550, "d", 3, (1.0 / cells, 1.0 / cells, 0.0), True),        # ModelPixelScaleTag
        (33922, "d", 6, (0.0, 0.0, 0.0, west, north, 0.0), True),       # ModelTiepointTag
    ])
    return str(path)


@pytest.fixture()
def mersey_tiles(tmp_path):
    """The two tiles a Mersey scene straddles: Wirral side 40 m, Liverpool side 20 m."""
    wirral = write_tile(tmp_path / "W004.tif", west=-4.0, north=54.0, height_m=40.0)
    liverpool = write_tile(tmp_path / "W003.tif", west=-3.0, north=54.0, height_m=20.0)
    return wirral, liverpool


def heights_either_side(grid, lon_split=-3.0):
    """The grid's heights west and east of a meridian, as two sets."""
    nx, x0, cell = grid["nx"], grid["x0"], grid["cell_m"]
    m_per_deg_lon = 111_320.0 * np.cos(np.radians(ORIGIN[0]))
    lons = ORIGIN[1] + (x0 + np.arange(nx) * cell) / m_per_deg_lon
    rows = np.array(grid["heights"]).reshape(grid["ny"], nx)
    west = rows[:, lons < lon_split - 0.001]
    east = rows[:, lons > lon_split + 0.001]
    return set(np.unique(west).tolist()), set(np.unique(east).tolist())


class TestTheLandAcrossADegreeLine:
    def test_both_sides_have_their_own_heights(self, mersey_tiles):
        terrain = _load("terrain")
        grid = terrain.terrain_grid(list(mersey_tiles), ORIGIN[0], ORIGIN[1], 9000.0, 300.0)
        west, east = heights_either_side(grid)
        assert west == {40.0}, "the Wirral bank came out flat"
        assert east == {20.0}

    def test_with_one_tile_the_rest_is_sea(self, mersey_tiles):
        """What it did before: only the origin's tile, the other side flat at 0."""
        terrain = _load("terrain")
        grid = terrain.terrain_grid(mersey_tiles[1], ORIGIN[0], ORIGIN[1], 9000.0, 300.0)
        west, east = heights_either_side(grid)
        assert west == {0.0} and east == {20.0}

    def test_a_scene_inside_one_tile_is_unchanged(self, tmp_path):
        terrain = _load("terrain")
        tile = write_tile(tmp_path / "pwllheli.tif", west=-5.0, north=53.0, height_m=30.0)
        grid = terrain.terrain_grid(tile, 52.88, -4.40, 6000.0, 300.0)
        assert set(grid["heights"]) == {30.0}

    def test_the_render_machine_builds_its_land_from_every_tile(self, mersey_tiles, tmp_path, monkeypatch):
        assets = _load("assets")
        monkeypatch.setattr(assets, "ensure_dem_tiles", lambda names: (list(mersey_tiles), {"disk": 2, "source": 0}))
        monkeypatch.setattr(assets, "ensure_imagery_tiles",
                            lambda tiles, token=None: (_ for _ in ()).throw(RuntimeError("offline")))
        scene = {"origin": {"lat": ORIGIN[0], "lon": ORIGIN[1]}, "extent_m": 3000.0, "race": {"id": 1}}
        assert len(assets.dem_tiles_for(assets.scene_bounds(scene))) == 2
        grid = assets.land_for_scene(scene, str(tmp_path), cell_m=300.0)
        west, east = heights_either_side(grid)
        assert west == {40.0} and east == {20.0}


class TestNoOtherClubsBranding:
    def test_there_is_no_built_in_manifest(self, monkeypatch):
        monkeypatch.delenv("MANIFEST_URL", raising=False)
        assert _load("branding").BRANDING_MANIFEST_URL == ""

    def test_the_machine_can_still_name_one(self, monkeypatch):
        monkeypatch.setenv("MANIFEST_URL", "https://example.org/api/branding/live")
        assert _load("branding").BRANDING_MANIFEST_URL == "https://example.org/api/branding/live"

    def test_no_club_logo_is_built_into_the_film(self):
        for name in ("renderer.py", "export_race.py", "branding.py"):
            source = (_REPLAY3D / name).read_text(encoding="utf-8")
            assert "pwllheli_sailing_club_logo" not in source, name
            assert "pwllhelisailingclub.org" not in source, name
