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


class TestTheLandReachesTheHorizon:
    """The detailed land ended 6 km beyond the racing and the sea ran on to nine
    times the race's extent, so on the Mersey -- land all round -- the land
    stopped in straight lines and sea filled the view behind Bootle and the Wirral."""

    SCENE = {"origin": {"lat": ORIGIN[0], "lon": ORIGIN[1]}, "extent_m": 2683.0, "race": {"id": 1}}

    def land(self, assets, tiles, tmp_path, monkeypatch, scene=None):
        monkeypatch.setattr(assets, "ensure_dem_tiles", lambda names: (list(tiles), {"disk": 2, "source": 0}))
        monkeypatch.setattr(assets, "ensure_imagery_tiles",
                            lambda tiles, token=None: (_ for _ in ()).throw(RuntimeError("offline")))
        return assets.land_for_scene(scene or self.SCENE, str(tmp_path), cell_m=300.0)

    def test_a_coarse_ring_goes_out_to_the_edge_of_the_sea(self, mersey_tiles, tmp_path, monkeypatch):
        assets = _load("assets")
        terrain = _load("terrain")
        grid = self.land(assets, mersey_tiles, tmp_path, monkeypatch)
        far = grid["far"]
        reach = 2683.0 * terrain.HAZE_FAR
        assert far["x0"] == pytest.approx(-reach) and far["cell_m"] >= assets.FAR_MIN_CELL_M
        # Its hole is exactly the detailed square.
        near_side = (grid["nx"] - 1) * grid["cell_m"]
        assert far["hole"] == pytest.approx([grid["x0"], grid["y0"], grid["x0"] + near_side, grid["y0"] + near_side])

    def test_the_ring_has_the_wirral_and_liverpool_in_it(self, mersey_tiles, tmp_path, monkeypatch):
        grid = self.land(_load("assets"), mersey_tiles, tmp_path, monkeypatch)
        west, east = heights_either_side({**grid["far"], "x0": grid["far"]["x0"]})
        assert west == {40.0} and east == {20.0}

    def test_no_ring_when_the_detailed_land_already_reaches_the_edge(self, mersey_tiles, tmp_path, monkeypatch):
        scene = dict(self.SCENE, extent_m=500.0)        # 4.5 km of scene, 6.5 km of detailed land
        grid = self.land(_load("assets"), mersey_tiles, tmp_path, monkeypatch, scene)
        assert "far" not in grid

    def test_a_passage_race_does_not_send_it_out_180_km(self, mersey_tiles, tmp_path, monkeypatch):
        assets = _load("assets")
        scene = dict(self.SCENE, extent_m=20_000.0)
        grid = self.land(assets, mersey_tiles, tmp_path, monkeypatch, scene)
        assert grid["far"]["x0"] == pytest.approx(-assets.FAR_MAX_REACH_M)

    def test_the_ring_s_tiles_stay_a_dozen_across(self):
        assets = _load("assets")
        bounds = assets.scene_bounds(self.SCENE, margin_m=2683.0 * 8)
        zoom = assets.far_zoom(bounds)
        across = assets.lon_to_tile_x(bounds["east"], zoom) - assets.lon_to_tile_x(bounds["west"], zoom)
        assert across <= assets.FAR_MAX_TILES_ACROSS and zoom >= 9

    def test_the_ring_tucks_under_the_detailed_square(self):
        terrain = _load("terrain")
        grid = {"nx": 5, "ny": 5, "x0": -200.0, "y0": -200.0, "cell_m": 100.0, "heights": [30.0] * 25}
        verts, faces = terrain.land_mesh(grid, hole=(-150.0, -150.0, 150.0, 150.0), drop_m=25.0)
        assert len(faces) == 16 - 4                      # the four quads wholly inside are gone
        centre = verts[12]
        assert centre[:2] == (0.0, 0.0) and centre[2] == 5.0   # dropped under the fine grid
        assert verts[0][2] == 30.0                       # the edge of the ring is at its height

    def test_without_a_hole_it_is_the_land_as_before(self):
        terrain = _load("terrain")
        grid = {"nx": 3, "ny": 3, "x0": 0.0, "y0": 0.0, "cell_m": 60.0, "heights": [0.0, 12.0] + [5.0] * 7}
        verts, faces = terrain.land_mesh(grid)
        assert len(faces) == 4
        assert verts[0][2] == terrain.LAND_UNDER_SEA_Z and verts[1][2] == 12.0


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


class TestNoMapboxToken:
    """A render machine with no Mapbox token still makes the film.

    read_token raises SystemExit when there is none, which is right for the
    command-line fetchers and wrong inside the renderer: SystemExit is not an
    Exception, so it went past every handler, the renderer stopped mid-job and
    the race was left claimed until the claim went stale. It was also asked for
    before the cache was looked at, so even water whose tiles were all on disk
    needed a token.
    """

    @pytest.fixture()
    def no_token(self, tmp_path, monkeypatch):
        assets = _load("assets")
        monkeypatch.delenv("MAPBOX_TOKEN", raising=False)
        # This may be the render machine, with a real token on disk.
        monkeypatch.setitem(assets.read_token.__globals__, "TOKEN_FILE", str(tmp_path / "no_token.txt"))
        monkeypatch.setattr(assets, "MAPBOX_CACHE", str(tmp_path / "mapbox"))
        fetched = []
        monkeypatch.setattr(assets, "fetch_tile", lambda z, x, y, tok: fetched.append((z, x, y)))
        return assets, tmp_path / "mapbox", fetched

    @staticmethod
    def cache(root, z, x, y):
        path = root / str(z) / str(x) / f"{y}.jpg"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"jpeg")

    def test_cached_tiles_need_no_token(self, no_token):
        assets, root, fetched = no_token
        for y in (1, 2):
            self.cache(root, 15, 7, y)
        assert assets.ensure_imagery_tiles([(15, 7, 1), (15, 7, 2)]) == {"disk": 2, "source": 0}
        assert fetched == []

    def test_a_tile_to_fetch_is_an_ordinary_failure(self, no_token):
        assets, root, fetched = no_token
        self.cache(root, 15, 7, 1)
        with pytest.raises(RuntimeError, match="no Mapbox token"):
            assets.ensure_imagery_tiles([(15, 7, 1), (15, 7, 2)])
        assert fetched == []

    def test_a_refused_token_is_an_ordinary_failure_too(self, no_token, monkeypatch):
        """A token Mapbox rejects (401/403), or a tile that fails three times."""
        assets, _root, _fetched = no_token
        monkeypatch.setenv("MAPBOX_TOKEN", "pk.expired")

        def refused(z, x, y, tok):
            raise SystemExit("Mapbox rejected the token (401); check it is valid and has tile scope")

        monkeypatch.setattr(assets, "fetch_tile", refused)
        with pytest.raises(RuntimeError, match="rejected the token"):
            assets.ensure_imagery_tiles([(15, 7, 1)])

    def test_the_land_is_still_built(self, no_token, mersey_tiles, tmp_path, monkeypatch):
        """Height colours instead of the satellite picture, but land, and a film."""
        assets, _root, _fetched = no_token
        monkeypatch.setattr(assets, "ensure_dem_tiles", lambda names: (list(mersey_tiles), {"disk": 2, "source": 0}))
        scene = {"origin": {"lat": ORIGIN[0], "lon": ORIGIN[1]}, "extent_m": 3000.0, "race": {"id": 1}}
        grid = assets.land_for_scene(scene, str(tmp_path), cell_m=300.0)
        assert grid["nx"] > 1 and "imagery" not in grid
        assert "imagery" not in (grid.get("far") or {})
