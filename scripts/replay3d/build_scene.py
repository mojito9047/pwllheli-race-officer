"""Build an animated 3D replay scene in Blender from an export_race.py JSON file.

Runs inside Blender's Python, either from the command line

    blender --python scripts/replay3d/build_scene.py -- runtime/replay3d/race_457.json [--speed 30]
        [--shots film|overview|follow] [--follow "Demo Boat A"] [--boat-model hull.glb] [--render]

or through the Blender MCP bridge (or the Python console) by exec-ing this
file's text into a namespace and calling ``build(json_path, speed=..., shots=...)``
from it. ``render_still`` renders one frame to a PNG for a quick look.

What it makes, all inside one new scene named after the race:

* the sea, with a wet, slowly moving procedural water surface;
* the land, if the export carried a terrain grid: textured with the exported
  imagery when there is any, otherwise coloured by height;
* the sun where it actually was for that race (computed from the race time and
  the origin's latitude and longitude), a matching sky, and distance haze;
* every mark that had a position that day as a barrel buoy with its code above
  it, the course marks highlighted, the course as an orange path, the finish
  line in red;
* one yacht per tracked boat, keyframed from the resampled track: position and
  heading from the GPS, heel and sail trim from the true wind. The hull is a
  procedural one unless ``boat_model`` names a glTF/FBX/OBJ file;
* cameras. ``shots="film"`` (the default) cuts between an opening overview, a
  tracking shot of the leader rounding the first mark, a chase camera behind the
  leader, and a wide shot at the finish, all bound to timeline markers so the
  render switches between them. ``"overview"`` and ``"follow"`` are single cameras;
* a race clock and title pinned to whichever camera is live.

Nothing outside the new scene is touched, so it is safe to run in a file that
already has work in it, and running it again replaces the earlier replay scene
of the same race.

Scale: yachts are drawn larger than life (BOAT_SCALE) because a 10 m hull is a
speck against a 3 km course. Positions are exact; only the model is enlarged.
"""
from __future__ import annotations

import datetime as _dt
import json
import math
import os
import sys
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import time

import bmesh
import bpy
from mathutils import Vector

# The scripts beside this one carry the pieces that must agree with the frame
# extractor: the film's clock, and which colour belongs to which boat.
_HERE = os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() else os.getcwd()
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
from replay_time import TimeWarp  # noqa: E402
from replay_style import BOAT_COLOURS, to_linear  # noqa: E402
from wind import Wind  # noqa: E402

# ---------------------------------------------------------------------------
# Tunables.

FPS = 24
# Race seconds per video second. 30 turns an 80-minute race into a 2.7-minute film.
DEFAULT_SPEED = 30.0
# Model enlargement for legibility. Positions stay true.
BOAT_SCALE = 4.0
LOA_M = 10.5            # hull length before BOAT_SCALE
BEAM_M = 3.4
DRAFT_M = 1.9
MAST_M = 15.0
MAX_HEEL_DEG = 22.0
BUOY_RADIUS_M = 6.0     # also larger than life, for the same reason
BUOY_HEIGHT_M = 8.0
# Labels scale with their distance from the live camera so they keep the same
# size on screen: this many metres of text per metre of distance (0.02 is about
# 1.1 degrees tall, a comfortable caption).
LABEL_PER_METRE = 0.020
COURSE_LINE_RADIUS_M = 1.4
# Foam astern of each hull, in unscaled metres (BOAT_SCALE applies on top).
WAKE_LENGTH_M = 18.0
TERRAIN_Z_SCALE = 1.0
# Picture-in-picture: how far in front of the camera the HUD sits, and how wide
# the hut-camera clip is as a fraction of the frame.
VIDEO_DEPTH = 1.6
VIDEO_FRAME_FRACTION = 0.34
# How close the HUD sits to the frame edge (1.0 would touch it).
HUD_MARGIN_X = 0.975
HUD_MARGIN_Y = 0.965
# Haze: clear inside HAZE_NEAR x extent, HAZE_MAX of the way to sky colour at HAZE_FAR x extent.
HAZE_NEAR = 1.5
HAZE_FAR = 9.0
HAZE_MAX = 0.45
# The sun is never let below this elevation: a race sailed into dusk still has to be visible.
MIN_SUN_ELEVATION_DEG = 10.0
# Camera. A 180-degree shutter is the film convention; less than that here because
# the replay already runs at 30x, so a full shutter smears the fleet into streaks.
SHUTTER = 0.4
# Depth of field is nearly a no-op at this geometry (every camera sits hundreds of
# metres out), so the aperture is wide open to buy what little separation there is.
APERTURE_FSTOP = 1.8

PALETTE = [to_linear(c) for c in BOAT_COLOURS]
# How much of each boat's recent track to draw behind it, in race seconds.
TRAIL_SECONDS = 150.0
TRAIL_RADIUS_M = 1.2
# A trail is a tube of a fixed radius in the world, so it is a fine line at a
# kilometre and a pipe across the frame when a chase or mark camera passes
# within a few boat lengths of it. It dissolves between these two distances.
TRAIL_FADE_NEAR_M = 110.0
TRAIL_FADE_FAR_M = 320.0
# The start shot: cut to the line this many race seconds before the gun, and
# back to the wide shot this many after it. The film is running at real time
# through all of that, so these are seconds of screen time too.
START_CUT_IN = 50.0
START_CUT_OUT = 30.0

# Every datablock this script creates is named with this prefix so a rebuild can
# find and remove its own leftovers without touching anything else in the file.
PREFIX = "Replay: "


# ---------------------------------------------------------------------------
# Helpers that do not need a UI context (everything is built through bpy.data
# and bmesh so it works the same from the MCP bridge and from --python).

def _link(scene: bpy.types.Scene, coll: bpy.types.Collection, obj: bpy.types.Object) -> bpy.types.Object:
    coll.objects.link(obj)
    return obj


def _collection(scene: bpy.types.Scene, name: str) -> bpy.types.Collection:
    coll = bpy.data.collections.new(name)
    scene.collection.children.link(coll)
    return coll


def _new_material(name: str) -> bpy.types.Material:
    mat = bpy.data.materials.new(PREFIX + name)
    mat.use_nodes = True
    return mat


def _material(name: str, color: Tuple[float, float, float], *, roughness: float = 0.5,
              metallic: float = 0.0, emission: float = 0.0) -> bpy.types.Material:
    mat = _new_material(name)
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    bsdf.inputs["Base Color"].default_value = (*color, 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    if emission > 0.0:
        bsdf.inputs["Emission Color"].default_value = (*color, 1.0)
        bsdf.inputs["Emission Strength"].default_value = emission
    return mat


def _add_haze(mat: bpy.types.Material, extent: float, sky: Tuple[float, float, float]) -> None:
    """Fade a surface towards the sky colour with distance from the camera.

    Cheaper than a volume and enough for a bay: the far shore softens, the sea
    meets the sky instead of ending at the edge of a plane.
    """
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    output = next(n for n in nodes if n.type == "OUTPUT_MATERIAL")
    shader_link = next((l for l in links if l.to_node == output and l.to_socket.name == "Surface"), None)
    if shader_link is None:
        return
    cam = nodes.new("ShaderNodeCameraData")
    ramp = nodes.new("ShaderNodeMapRange")
    ramp.inputs["From Min"].default_value = extent * HAZE_NEAR
    ramp.inputs["From Max"].default_value = extent * HAZE_FAR
    ramp.inputs["To Max"].default_value = HAZE_MAX
    ramp.clamp = True
    fog = nodes.new("ShaderNodeEmission")
    fog.inputs["Color"].default_value = (*sky, 1.0)
    fog.inputs["Strength"].default_value = 1.0
    mix = nodes.new("ShaderNodeMixShader")
    links.new(cam.outputs["View Distance"], ramp.inputs["Value"])
    links.new(ramp.outputs["Result"], mix.inputs["Fac"])
    links.new(shader_link.from_socket, mix.inputs[1])
    links.new(fog.outputs["Emission"], mix.inputs[2])
    links.remove(shader_link)
    links.new(mix.outputs["Shader"], output.inputs["Surface"])


def _mesh_object(name: str, bm: bmesh.types.BMesh, mat: Optional[bpy.types.Material] = None,
                 smooth: bool = False) -> bpy.types.Object:
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    if smooth:
        mesh.shade_smooth()
    if mat is not None:
        mesh.materials.append(mat)
    return bpy.data.objects.new(name, mesh)


def _cube(name: str, size: Tuple[float, float, float], center: Tuple[float, float, float],
          mat: Optional[bpy.types.Material]) -> bpy.types.Object:
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=Vector(size), verts=bm.verts)
    bmesh.ops.translate(bm, vec=Vector(center), verts=bm.verts)
    return _mesh_object(name, bm, mat)


def _cylinder(name: str, radius: float, height: float, center: Tuple[float, float, float],
              mat: Optional[bpy.types.Material], segments: int = 16, radius2: Optional[float] = None) -> bpy.types.Object:
    bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=True, segments=segments, radius1=radius,
                          radius2=radius if radius2 is None else radius2, depth=height)
    bmesh.ops.translate(bm, vec=Vector(center), verts=bm.verts)
    return _mesh_object(name, bm, mat, smooth=True)


def _triangle(name: str, a: Vector, b: Vector, c: Vector, mat: bpy.types.Material) -> bpy.types.Object:
    bm = bmesh.new()
    va, vb, vc = bm.verts.new(a), bm.verts.new(b), bm.verts.new(c)
    bm.faces.new((va, vb, vc))
    obj = _mesh_object(name, bm, mat)
    # Sails are seen from both sides.
    mat.use_backface_culling = False
    return obj


# The app's typefaces (see static/theme_race_document.css): Archivo for text,
# Archivo Narrow for labels and boards, IBM Plex Mono for every figure.
# export_race.py converts the app's woff2 files to TTF beside the JSON; without
# them Blender's built-in face is used.
FONTS: Dict[str, Optional[bpy.types.VectorFont]] = {"text": None, "narrow": None, "mono": None}
FONT_FILES = {"text": "Archivo.ttf", "narrow": "ArchivoNarrow.ttf", "mono": "PlexMono500.ttf"}
# Approximate advance width per character as a fraction of the size, for laying
# out chips without evaluating the text object.
FONT_ADVANCE = {"text": 0.56, "narrow": 0.46, "mono": 0.60, None: 0.55}

# The theme's colours, sRGB hex -> linear.
def _srgb(hex_colour: str) -> Tuple[float, float, float]:
    h = hex_colour.lstrip("#")
    out = []
    for i in (0, 2, 4):
        c = int(h[i:i + 2], 16) / 255.0
        out.append(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4)
    return (out[0], out[1], out[2])


THEME = {
    "panel": _srgb("#10120f"), "panel_rule": _srgb("#333831"), "panel_ink": _srgb("#e8e6dd"),
    "panel_label": _srgb("#b3b0a4"), "amber": _srgb("#ffb000"),
    "paper": _srgb("#f7f4ec"), "paper_sunk": _srgb("#efeade"), "ink": _srgb("#1c1a15"), "ink_2": _srgb("#4a463c"),
    "rule_strong": _srgb("#a89f89"),
    "port": _srgb("#c8102e"), "starboard": _srgb("#00843d"), "white": _srgb("#ffffff"),
}


def load_fonts(json_path: str) -> Dict[str, Optional[str]]:
    """Load the app's typefaces from <json dir>/fonts/ if the exporter put them there."""
    fonts_dir = os.path.join(os.path.dirname(os.path.abspath(json_path)), "fonts")
    found: Dict[str, Optional[str]] = {}
    for key, filename in FONT_FILES.items():
        path = os.path.join(fonts_dir, filename)
        FONTS[key] = None
        if os.path.exists(path):
            existing = next((f for f in bpy.data.fonts if f.filepath and os.path.abspath(f.filepath) == path), None)
            FONTS[key] = existing or bpy.data.fonts.load(path)
        found[key] = path if FONTS[key] is not None else None
    return found


def _text(name: str, body: str, size: float, mat: bpy.types.Material, align: str = "CENTER",
          font: Optional[str] = None, spacing: float = 1.0) -> bpy.types.Object:
    curve = bpy.data.curves.new(name, type="FONT")
    curve.body = body
    curve.size = size
    curve.align_x = align
    curve.align_y = "CENTER"
    curve.space_character = spacing
    if font and FONTS.get(font) is not None:
        curve.font = FONTS[font]
    curve.materials.append(mat)
    return bpy.data.objects.new(name, curve)


def _text_width(body: str, size: float, font: Optional[str], spacing: float = 1.0) -> float:
    return len(body) * size * FONT_ADVANCE.get(font, 0.55) * spacing


def _flat_material(name: str, color: Tuple[float, float, float], alpha: float = 1.0) -> bpy.types.Material:
    """Unlit, optionally translucent colour for HUD panels and text."""
    mat = _new_material(name)
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    # Compare by type, not identity: bpy hands out a fresh wrapper each time.
    for n in list(nodes):
        if n.type != "OUTPUT_MATERIAL":
            nodes.remove(n)
    output = next(n for n in nodes if n.type == "OUTPUT_MATERIAL")
    emit = nodes.new("ShaderNodeEmission")
    emit.inputs["Color"].default_value = (*color, 1.0)
    emit.inputs["Strength"].default_value = 1.0
    if alpha < 1.0:
        mix = nodes.new("ShaderNodeMixShader")
        transparent = nodes.new("ShaderNodeBsdfTransparent")
        mix.inputs[0].default_value = alpha
        links.new(transparent.outputs["BSDF"], mix.inputs[1])
        links.new(emit.outputs["Emission"], mix.inputs[2])
        links.new(mix.outputs["Shader"], output.inputs["Surface"])
        if hasattr(mat, "surface_render_method"):
            mat.surface_render_method = "BLENDED"       # EEVEE Next
        else:
            mat.blend_method = "BLEND"
    else:
        links.new(emit.outputs["Emission"], output.inputs["Surface"])
    mat.use_backface_culling = False
    return mat


def _quad(name: str, width: float, height: float, mat: bpy.types.Material) -> bpy.types.Object:
    """A unit-thin rectangle in the XY plane with its origin at the bottom-left corner.

    Carries a 0..1 UV map: without one an image texture on it samples nothing
    and the panel renders black, which is exactly what the first
    picture-in-picture did.
    """
    bm = bmesh.new()
    verts = [bm.verts.new((0.0, 0.0, 0.0)), bm.verts.new((width, 0.0, 0.0)),
             bm.verts.new((width, height, 0.0)), bm.verts.new((0.0, height, 0.0))]
    face = bm.faces.new(verts)
    uv_layer = bm.loops.layers.uv.new("UVMap")
    for loop in face.loops:
        loop[uv_layer].uv = (loop.vert.co.x / width if width else 0.0,
                             loop.vert.co.y / height if height else 0.0)
    return _mesh_object(name, bm, mat)


def _empty(name: str) -> bpy.types.Object:
    return bpy.data.objects.new(name, None)


def _track_to(obj: bpy.types.Object, target: bpy.types.Object, axis: str = "TRACK_NEGATIVE_Z") -> None:
    con = obj.constraints.new("TRACK_TO")
    con.target = target
    con.track_axis = axis
    con.up_axis = "UP_Y"
    # A camera focuses on whatever it is pointed at, so the two are set together.
    if obj.type == "CAMERA":
        obj.data.dof.focus_object = target


def _label(name: str, body: str, mat: bpy.types.Material, eye: bpy.types.Object,
           per_metre: float = LABEL_PER_METRE) -> bpy.types.Object:
    """A caption that faces the live camera and keeps a constant size on screen.

    The text is 1 m tall and a driver scales it by its distance from ``eye``;
    the expression is a "simple expression" so it needs no script auto-run.
    """
    obj = _text(name, body, 1.0, mat, font="narrow", spacing=1.04)
    _track_to(obj, eye, "TRACK_Z")
    for axis in range(3):
        drv = obj.driver_add("scale", axis).driver
        drv.type = "SCRIPTED"
        var = drv.variables.new()
        var.name = "d"
        var.type = "LOC_DIFF"
        var.targets[0].id = obj
        var.targets[1].id = eye
        drv.expression = f"d * {per_metre:.4f}"
    return obj


def _remove_scene(name: str) -> None:
    scene = bpy.data.scenes.get(name)
    if scene is None:
        return
    for obj in list(scene.objects):
        data = obj.data
        bpy.data.objects.remove(obj, do_unlink=True)
        if data is not None and data.users == 0:
            if isinstance(data, bpy.types.Mesh):
                bpy.data.meshes.remove(data)
            elif isinstance(data, bpy.types.Curve):
                bpy.data.curves.remove(data)
            elif isinstance(data, bpy.types.Camera):
                bpy.data.cameras.remove(data)
            elif isinstance(data, bpy.types.Light):
                bpy.data.lights.remove(data)
    for coll in list(scene.collection.children_recursive):
        bpy.data.collections.remove(coll)
    world = scene.world
    if len(bpy.data.scenes) > 1:
        bpy.data.scenes.remove(scene)
    if world is not None and world.users == 0 and world.name.startswith(PREFIX):
        bpy.data.worlds.remove(world)
    for mat in list(bpy.data.materials):
        if mat.name.startswith(PREFIX) and mat.users == 0:
            bpy.data.materials.remove(mat)
    for img in list(bpy.data.images):
        if img.name.startswith(PREFIX) and img.users == 0:
            bpy.data.images.remove(img)


def _heading_to_yaw(heading_deg: float) -> float:
    """Compass heading (0 = north, clockwise) to a Blender Z rotation with the bow on +Y."""
    return -math.radians(heading_deg)


def _unwrap(prev: Optional[float], value: float) -> float:
    """Keep successive yaw angles continuous so a 359->1 step is not a full spin."""
    if prev is None:
        return value
    while value - prev > math.pi:
        value -= 2 * math.pi
    while value - prev < -math.pi:
        value += 2 * math.pi
    return value


def _fcurves(obj: bpy.types.Object) -> List[bpy.types.FCurve]:
    """Every F-curve animating ``obj``, across Blender's layered-action layout (4.4+) or the legacy one."""
    ad = obj.animation_data
    if ad is None or ad.action is None:
        return []
    action = ad.action
    if hasattr(action, "layers"):
        out: List[bpy.types.FCurve] = []
        for layer in action.layers:
            for strip in layer.strips:
                for bag in strip.channelbags:
                    out.extend(bag.fcurves)
        return out
    return list(getattr(action, "fcurves", []))


def _set_interpolation(obj: bpy.types.Object, mode: str, only_prefix: Optional[str] = None) -> None:
    for fc in _fcurves(obj):
        if only_prefix and not fc.data_path.startswith(only_prefix):
            continue
        for kp in fc.keyframe_points:
            kp.interpolation = mode


# ---------------------------------------------------------------------------
# Where the sun was.

def sun_position(t_epoch: float, lat_deg: float, lon_deg: float) -> Tuple[float, float]:
    """Solar (elevation, azimuth) in degrees for a UTC epoch time and place.

    The low-precision algorithm from the Astronomical Almanac: good to a
    fraction of a degree, which is far more than a sun lamp needs.
    """
    n = t_epoch / 86400.0 + 2440587.5 - 2451545.0            # days since J2000
    mean_lon = (280.460 + 0.9856474 * n) % 360.0
    mean_anom = math.radians((357.528 + 0.9856003 * n) % 360.0)
    ecl_lon = math.radians(mean_lon + 1.915 * math.sin(mean_anom) + 0.020 * math.sin(2 * mean_anom))
    obliq = math.radians(23.439 - 0.0000004 * n)
    ra = math.atan2(math.cos(obliq) * math.sin(ecl_lon), math.cos(ecl_lon))
    dec = math.asin(math.sin(obliq) * math.sin(ecl_lon))
    gmst_h = (18.697374558 + 24.06570982441908 * n) % 24.0
    local_sidereal = math.radians(gmst_h * 15.0 + lon_deg)
    hour_angle = local_sidereal - ra
    lat = math.radians(lat_deg)
    elev = math.asin(math.sin(lat) * math.sin(dec) + math.cos(lat) * math.cos(dec) * math.cos(hour_angle))
    az = math.atan2(math.sin(hour_angle),
                    math.cos(hour_angle) * math.sin(lat) - math.tan(dec) * math.cos(lat))
    return math.degrees(elev), (math.degrees(az) + 180.0) % 360.0


def _sun_lamp_rotation(elev_deg: float, az_deg: float) -> Tuple[float, float, float]:
    """Euler XYZ for a sun lamp so its light arrives from compass ``az`` at ``elev`` above the horizon."""
    return (math.radians(90.0 - elev_deg), 0.0, math.radians(-(az_deg + 180.0)))


# ---------------------------------------------------------------------------
# Environment.

def build_sky_and_sun(scene: bpy.types.Scene, coll: bpy.types.Collection, data: Dict[str, Any]
                      ) -> Tuple[Tuple[float, float, float], Dict[str, Any]]:
    """Sun lamp and sky for the moment the race started. Returns (haze colour, sun info)."""
    origin = data["origin"]
    t_start = float(data["time"]["t0_epoch"]) + float(data["time"]["first_start_rel"])
    elev, az = sun_position(t_start, float(origin["lat"]), float(origin["lon"]))
    elev_used = max(elev, MIN_SUN_ELEVATION_DEG)
    # Colour temperature falls with the sun: white at noon, amber near the horizon.
    warmth = max(0.0, min(1.0, 1.0 - elev_used / 35.0))
    sun_color = (1.0, 1.0 - 0.25 * warmth, 1.0 - 0.55 * warmth)

    sun_data = bpy.data.lights.new(PREFIX + "Sun", "SUN")
    # A low sun is dimmer per unit area, so give it a little more energy to keep the scene readable.
    sun_data.energy = 2.8 + 1.2 * warmth
    sun_data.color = sun_color
    # Under Standard this reflection clipped to a white slab and had to be held
    # down to 0.3. AgX rolls the highlight off instead, so the specular can run
    # at full strength and the sun can be its real angular size, which is what
    # turns the reflection into a glitter track rather than a blob.
    sun_data.specular_factor = 1.0
    sun_data.angle = math.radians(0.55)
    sun = bpy.data.objects.new("Sun", sun_data)
    sun.rotation_euler = _sun_lamp_rotation(elev_used, az)
    _link(scene, coll, sun)

    world = bpy.data.worlds.new(PREFIX + "sky")
    world.use_nodes = True
    nodes, links = world.node_tree.nodes, world.node_tree.links
    bg = nodes.get("Background")
    sky = nodes.new("ShaderNodeTexSky")
    haze: Tuple[float, float, float]
    try:
        sky.sky_type = "NISHITA"
        sky.sun_disc = False
        sky.sun_elevation = math.radians(elev_used)
        sky.sun_rotation = math.radians(az)
        sky.altitude = 20.0
        sky.air_density = 1.0
        sky.dust_density = 1.2 + 1.5 * warmth
        links.new(sky.outputs["Color"], bg.inputs["Color"])
        bg.inputs["Strength"].default_value = 0.16        # Nishita is in physical units; tame it
        haze = (0.72 + 0.12 * warmth, 0.79, 0.92 - 0.14 * warmth)
    except Exception:
        nodes.remove(sky)
        bg.inputs["Color"].default_value = (0.55, 0.70, 0.90, 1.0)
        bg.inputs["Strength"].default_value = 1.0
        haze = (0.65, 0.74, 0.86)
    scene.world = world
    info = {"elevation_deg": round(elev, 1), "azimuth_deg": round(az, 1), "elevation_used_deg": round(elev_used, 1),
            "local_time": _dt.datetime.fromtimestamp(t_start).strftime("%H:%M")}
    return haze, info


def build_sea(scene: bpy.types.Scene, coll: bpy.types.Collection, extent: float,
              haze: Tuple[float, float, float], twd: Optional[float] = None,
              tws: Optional[float] = None) -> bpy.types.Object:
    """The water: wind-aligned waves at three scales, a sun glitter track, whitecaps.

    One quad, no displacement. A sea this wide cannot carry real geometry, so it
    all has to be in the shading, and three things do the work. The crests run
    across the wind instead of sitting in random blobs. The roughness varies at
    ripple scale, which breaks the sun's reflection into a track of sparkle
    rather than one hard blob, and that track is the strongest single cue that
    you are looking at real water. The tops go white when it blows.

    The old shader fed its noise no coordinates at all, so it fell back to
    generated ones, which span nought to one across the whole plane: at nine
    kilometres across that is less than one wave from the shore to the horizon,
    which is why the sea was flat.
    """
    mat = _new_material("Sea")
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    bsdf = nodes.get("Principled BSDF")
    bsdf.inputs["IOR"].default_value = 1.33
    bsdf.inputs["Specular IOR Level"].default_value = 0.5

    # Coordinates in metres: the plane sits at the origin unscaled, so object
    # space is world space. Rotated into the wind's frame and squashed across
    # it, which is what makes crests rather than lumps.
    coord = nodes.new("ShaderNodeTexCoord")
    mapping = nodes.new("ShaderNodeMapping")
    mapping.inputs["Rotation"].default_value[2] = -math.radians(float(twd or 0.0))
    mapping.inputs["Scale"].default_value = (1.0, 0.32, 1.0)
    links.new(coord.outputs["Object"], mapping.inputs["Vector"])

    def wave(metres: float, detail: float, roughness: float, seconds: float):
        """One scale of wave: ``metres`` between crests, evolving over ``seconds``."""
        n = nodes.new("ShaderNodeTexNoise")
        n.noise_dimensions = "4D"
        n.inputs["Scale"].default_value = 1.0 / metres
        n.inputs["Detail"].default_value = detail
        n.inputs["Roughness"].default_value = roughness
        links.new(mapping.outputs["Vector"], n.inputs["Vector"])
        drv = n.inputs["W"].driver_add("default_value").driver
        drv.type = "SCRIPTED"
        drv.expression = "frame * %.6f" % (1.0 / (seconds * scene.render.fps))
        return n

    swell = wave(70.0, 2.0, 0.50, 10.0)
    chop = wave(9.0, 3.0, 0.55, 4.0)
    ripple = wave(1.6, 4.0, 0.62, 1.5)

    # Two bumps chained: the long stuff shapes the surface, the ripple sits on
    # top of it and does the sparkling.
    height = nodes.new("ShaderNodeMixRGB")
    height.blend_type = "ADD"
    height.inputs["Fac"].default_value = 0.45
    links.new(swell.outputs["Fac"], height.inputs["Color1"])
    links.new(chop.outputs["Fac"], height.inputs["Color2"])
    coarse = nodes.new("ShaderNodeBump")
    coarse.inputs["Strength"].default_value = 0.55
    coarse.inputs["Distance"].default_value = 2.2
    links.new(height.outputs["Color"], coarse.inputs["Height"])
    fine = nodes.new("ShaderNodeBump")
    fine.inputs["Strength"].default_value = 0.45
    fine.inputs["Distance"].default_value = 0.16
    links.new(ripple.outputs["Fac"], fine.inputs["Height"])
    links.new(coarse.outputs["Normal"], fine.inputs["Normal"])
    links.new(fine.outputs["Normal"], bsdf.inputs["Normal"])

    # Shallower green-blue facing the camera, deep blue at grazing angles.
    facing = nodes.new("ShaderNodeLayerWeight")
    facing.inputs["Blend"].default_value = 0.35
    tint = nodes.new("ShaderNodeMixRGB")
    tint.inputs["Color1"].default_value = (0.020, 0.140, 0.170, 1.0)
    tint.inputs["Color2"].default_value = (0.008, 0.045, 0.085, 1.0)
    links.new(facing.outputs["Facing"], tint.inputs["Fac"])

    # Whitecaps on the steepest chop, and only once there is wind to raise them.
    breeze = 0.35 if tws is None else max(0.0, min(1.0, (float(tws) - 7.0) / 12.0))
    crest = nodes.new("ShaderNodeMapRange")
    crest.inputs["From Min"].default_value = 0.62 - 0.10 * breeze
    crest.inputs["From Max"].default_value = 0.80
    crest.inputs["To Max"].default_value = 0.55 * breeze
    crest.clamp = True
    links.new(chop.outputs["Fac"], crest.inputs["Value"])
    foam = nodes.new("ShaderNodeMixRGB")
    foam.inputs["Color2"].default_value = (0.80, 0.86, 0.90, 1.0)
    links.new(crest.outputs["Result"], foam.inputs["Fac"])
    links.new(tint.outputs["Color"], foam.inputs["Color1"])
    links.new(foam.outputs["Color"], bsdf.inputs["Base Color"])

    # Glitter: near-mirror water whose roughness varies at ripple scale, so the
    # sun's reflection scatters into a path instead of one hard blob. Foam is matt.
    gloss = nodes.new("ShaderNodeMapRange")
    gloss.inputs["To Min"].default_value = 0.045
    gloss.inputs["To Max"].default_value = 0.20
    gloss.clamp = True
    links.new(ripple.outputs["Fac"], gloss.inputs["Value"])
    rough = nodes.new("ShaderNodeMixRGB")
    rough.inputs["Color2"].default_value = (0.62, 0.62, 0.62, 1.0)
    links.new(crest.outputs["Result"], rough.inputs["Fac"])
    links.new(gloss.outputs["Result"], rough.inputs["Color1"])
    links.new(rough.outputs["Color"], bsdf.inputs["Roughness"])

    _add_haze(mat, extent, haze)

    size = extent * 2 * HAZE_FAR
    bm = bmesh.new()
    bmesh.ops.create_grid(bm, x_segments=1, y_segments=1, size=size / 2.0)
    sea = _mesh_object("Sea", bm, mat)
    return _link(scene, coll, sea)


def _wake_material(name: str) -> Tuple[bpy.types.Material, bpy.types.Node]:
    """Foam that fades astern, broken up, and only there when the boat is moving.

    Returns the material and the value node that carries boat speed, which
    ``animate_boat`` keyframes: a boat drifting on the line before the start
    leaves no wake, and without that the fleet sat in white fans at 11:24.
    """
    mat = _new_material(name)
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    for n in list(nodes):
        if n.type != "OUTPUT_MATERIAL":
            nodes.remove(n)
    output = next(n for n in nodes if n.type == "OUTPUT_MATERIAL")

    coord = nodes.new("ShaderNodeTexCoord")
    sep = nodes.new("ShaderNodeSeparateXYZ")
    links.new(coord.outputs["Object"], sep.inputs["Vector"])
    # Local Y runs astern (negative), so the fade is a range over -Y.
    along = nodes.new("ShaderNodeMapRange")
    along.inputs["From Min"].default_value = 0.0
    along.inputs["From Max"].default_value = -WAKE_LENGTH_M * BOAT_SCALE
    along.inputs["To Min"].default_value = 1.0
    along.inputs["To Max"].default_value = 0.0
    along.clamp = True
    links.new(sep.outputs["Y"], along.inputs["Value"])

    churn = nodes.new("ShaderNodeTexNoise")
    churn.noise_dimensions = "4D"
    churn.inputs["Scale"].default_value = 0.35 / BOAT_SCALE
    churn.inputs["Detail"].default_value = 4.0
    links.new(coord.outputs["Object"], churn.inputs["Vector"])
    drv = churn.inputs["W"].driver_add("default_value").driver
    drv.type = "SCRIPTED"
    drv.expression = "frame * 0.05"
    broken = nodes.new("ShaderNodeMath")
    broken.operation = "MULTIPLY"
    links.new(along.outputs["Result"], broken.inputs[0])
    links.new(churn.outputs["Fac"], broken.inputs[1])
    # Speed in knots, keyframed per boat, mapped to how much foam there is.
    speed = nodes.new("ShaderNodeValue")
    speed.name = "Speed"
    speed.label = "Speed"
    speed.outputs[0].default_value = 0.0
    strength = nodes.new("ShaderNodeMapRange")
    strength.inputs["From Min"].default_value = 1.2
    strength.inputs["From Max"].default_value = 6.0
    strength.inputs["To Min"].default_value = 0.0
    strength.inputs["To Max"].default_value = 1.0
    strength.clamp = True
    links.new(speed.outputs[0], strength.inputs["Value"])
    gain = nodes.new("ShaderNodeMath")
    gain.operation = "MULTIPLY"
    links.new(broken.outputs["Value"], gain.inputs[0])
    links.new(strength.outputs["Result"], gain.inputs[1])

    foam = nodes.new("ShaderNodeBsdfDiffuse")
    foam.inputs["Color"].default_value = (0.88, 0.92, 0.95, 1.0)
    transparent = nodes.new("ShaderNodeBsdfTransparent")
    mix = nodes.new("ShaderNodeMixShader")
    links.new(gain.outputs["Value"], mix.inputs[0])
    links.new(transparent.outputs["BSDF"], mix.inputs[1])
    links.new(foam.outputs["BSDF"], mix.inputs[2])
    links.new(mix.outputs["Shader"], output.inputs["Surface"])
    if hasattr(mat, "surface_render_method"):
        mat.surface_render_method = "BLENDED"
    else:
        mat.blend_method = "BLEND"
    mat.use_backface_culling = False
    return mat, speed


def _wake(name: str, mat: bpy.types.Material, scale: float) -> bpy.types.Object:
    """A widening foam strip astern of the transom, in the hull's local frame."""
    length = WAKE_LENGTH_M * scale
    near, far = BEAM_M * 0.40 * scale, BEAM_M * 1.35 * scale
    y0 = -LOA_M / 2.0 * scale
    steps = 14
    bm = bmesh.new()
    rows = []
    for i in range(steps + 1):
        t = i / steps
        y = y0 - t * length
        half = near + (far - near) * t
        z = 0.06 * scale
        rows.append((bm.verts.new((-half, y, z)), bm.verts.new((half, y, z))))
    for i in range(steps):
        (al, ar), (bl, br) = rows[i], rows[i + 1]
        bm.faces.new((al, ar, br, bl))
    return _mesh_object(name, bm, mat)


def _terrain_image(terrain: Dict[str, Any], json_path: str) -> Optional[bpy.types.Image]:
    rel = terrain.get("imagery")
    if not rel:
        return None
    path = rel if os.path.isabs(rel) else os.path.join(os.path.dirname(os.path.abspath(json_path)), rel)
    if not os.path.exists(path):
        print(f"replay3d: imagery {path} not found; falling back to height colours")
        return None
    img = bpy.data.images.load(path, check_existing=False)
    img.name = PREFIX + os.path.basename(path)
    return img


def build_terrain(scene: bpy.types.Scene, coll: bpy.types.Collection, terrain: Dict[str, Any],
                  extent: float, haze: Tuple[float, float, float], json_path: str) -> Optional[bpy.types.Object]:
    nx, ny = int(terrain["nx"]), int(terrain["ny"])
    heights = terrain["heights"]
    if nx < 2 or ny < 2 or len(heights) != nx * ny:
        return None
    x0, y0, cell = float(terrain["x0"]), float(terrain["y0"]), float(terrain["cell_m"])
    verts: List[Tuple[float, float, float]] = []
    for j in range(ny):
        for i in range(nx):
            h = float(heights[j * nx + i])
            # Cells at sea level sit just below the water so the sea plane draws the shore.
            z = h * TERRAIN_Z_SCALE if h > 0.2 else -4.0
            verts.append((x0 + i * cell, y0 + j * cell, z))
    faces = []
    for j in range(ny - 1):
        for i in range(nx - 1):
            a = j * nx + i
            faces.append((a, a + 1, a + nx + 1, a + nx))
    mesh = bpy.data.meshes.new("Land")
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    mesh.shade_smooth()
    # UVs run 0..1 across the grid so a cropped image of the same footprint maps straight on.
    uv = mesh.uv_layers.new(name="UVMap")
    width, height = (nx - 1) * cell, (ny - 1) * cell
    for loop in mesh.loops:
        vx, vy, _vz = mesh.vertices[loop.vertex_index].co
        uv.data[loop.index].uv = ((vx - x0) / width, (vy - y0) / height)

    mat = _new_material("Land")
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    bsdf = nodes.get("Principled BSDF")
    bsdf.inputs["Roughness"].default_value = 0.95
    image = _terrain_image(terrain, json_path)
    if image is not None:
        tex = nodes.new("ShaderNodeTexImage")
        tex.image = image
        tex.extension = "EXTEND"
        links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
    else:
        geom = nodes.new("ShaderNodeNewGeometry")
        sep = nodes.new("ShaderNodeSeparateXYZ")
        ramp = nodes.new("ShaderNodeValToRGB")
        maprange = nodes.new("ShaderNodeMapRange")
        maprange.inputs["From Min"].default_value = 0.0
        maprange.inputs["From Max"].default_value = max(50.0, float(terrain.get("max_height_m") or 50.0))
        el = ramp.color_ramp.elements
        el[0].position = 0.0
        el[0].color = (0.30, 0.36, 0.20, 1.0)          # salt marsh and rough grazing at the shore
        el[1].position = 1.0
        el[1].color = (0.30, 0.26, 0.20, 1.0)          # bare hilltop
        el.new(0.03).color = (0.18, 0.40, 0.14, 1.0)   # pasture
        el.new(0.45).color = (0.28, 0.36, 0.16, 1.0)   # upland grass
        links.new(geom.outputs["Position"], sep.inputs["Vector"])
        links.new(sep.outputs["Z"], maprange.inputs["Value"])
        links.new(maprange.outputs["Result"], ramp.inputs["Fac"])
        links.new(ramp.outputs["Color"], bsdf.inputs["Base Color"])
    _add_haze(mat, extent, haze)
    mesh.materials.append(mat)
    land = bpy.data.objects.new("Land", mesh)
    return _link(scene, coll, land)


# ---------------------------------------------------------------------------
# Marks and course.

def _fade_near_camera(mat: bpy.types.Material, near: float, far: float) -> None:
    """Dissolve a surface as it comes close to the lens.

    The mirror image of ``_add_haze``, which fades things out in the distance.
    This one exists because a trail is drawn at a fixed size in the world, not
    on the screen: useful at a kilometre, a coloured pipe across the bottom of
    the frame when the camera is beside it. Fading by camera distance keeps the
    line that carries the information and loses the one that just fills space.
    """
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    output = next(n for n in nodes if n.type == "OUTPUT_MATERIAL")
    shader_link = next((l for l in links if l.to_node == output and l.to_socket.name == "Surface"), None)
    if shader_link is None:
        return
    cam = nodes.new("ShaderNodeCameraData")
    ramp = nodes.new("ShaderNodeMapRange")
    ramp.inputs["From Min"].default_value = near
    ramp.inputs["From Max"].default_value = far
    ramp.inputs["To Min"].default_value = 0.0
    ramp.inputs["To Max"].default_value = 1.0
    ramp.clamp = True
    transparent = nodes.new("ShaderNodeBsdfTransparent")
    mix = nodes.new("ShaderNodeMixShader")
    links.new(cam.outputs["View Distance"], ramp.inputs["Value"])
    links.new(ramp.outputs["Result"], mix.inputs[0])
    links.new(transparent.outputs["BSDF"], mix.inputs[1])
    links.new(shader_link.from_socket, mix.inputs[2])
    links.remove(shader_link)
    links.new(mix.outputs["Shader"], output.inputs["Surface"])
    if hasattr(mat, "surface_render_method"):
        mat.surface_render_method = "BLENDED"       # EEVEE Next
    else:
        mat.blend_method = "BLEND"


def build_marks(scene: bpy.types.Scene, coll: bpy.types.Collection, data: Dict[str, Any],
                label_mat: bpy.types.Material, eye: bpy.types.Object, reach: float,
                with_labels: bool = True) -> int:
    """Buoys for every mark within ``reach`` metres of the origin. Returns how many."""
    buoy_course = _material("Buoy course", (0.95, 0.75, 0.05), roughness=0.4)
    buoy_other = _material("Buoy other", (0.55, 0.50, 0.30), roughness=0.6)
    drawn = 0
    for m in data["marks"]:
        x, y = m["xy"]
        in_course = bool(m.get("in_course"))
        if not in_course and math.hypot(x, y) > reach:
            continue     # a passage mark far beyond the sea plane
        drawn += 1
        r = BUOY_RADIUS_M * (1.3 if m["code"] == "O" else 1.0)
        buoy = _cylinder(f"Mark {m['code']}", r, BUOY_HEIGHT_M, (x, y, BUOY_HEIGHT_M / 2.0 - 1.0),
                         buoy_course if in_course else buoy_other, segments=20)
        _link(scene, coll, buoy)
        if with_labels:
            label = _label(f"Label {m['code']}", m["code"], label_mat, eye,
                           LABEL_PER_METRE * (1.0 if in_course else 0.7))
            label.location = (x, y, BUOY_HEIGHT_M + 12.0)
            _link(scene, coll, label)
    return drawn


def _polyline(name: str, pts: Sequence[Tuple[float, float, float]], radius: float,
              mat: bpy.types.Material) -> bpy.types.Object:
    curve = bpy.data.curves.new(name, type="CURVE")
    curve.dimensions = "3D"
    curve.bevel_depth = radius
    curve.bevel_resolution = 3
    curve.fill_mode = "FULL"
    spline = curve.splines.new("POLY")
    spline.points.add(len(pts) - 1)
    for p, (x, y, z) in zip(spline.points, pts):
        p.co = (x, y, z, 1.0)
    curve.materials.append(mat)
    return bpy.data.objects.new(name, curve)


def build_course(scene: bpy.types.Scene, coll: bpy.types.Collection, data: Dict[str, Any]) -> None:
    # The course and finish lines are tubes of a fixed size in the world, so
    # they suffer exactly as the trails do when a camera passes close to one:
    # a fine line on the wide shot, a bar across the frame from a mark camera.
    pts = [(p["xy"][0], p["xy"][1], 1.0) for p in data.get("course", [])]
    if len(pts) >= 2:
        mat = _material("Course path", (1.0, 0.45, 0.05), emission=0.35)
        _fade_near_camera(mat, TRAIL_FADE_NEAR_M, TRAIL_FADE_FAR_M)
        path = _polyline("Course path", pts, COURSE_LINE_RADIUS_M, mat)
        _link(scene, coll, path)
    line = data.get("finish_line")
    if line:
        a, b = line["seaward_xy"], line["shore_xy"]
        mat = _material("Finish line", (0.95, 0.05, 0.05), emission=0.6)
        _fade_near_camera(mat, TRAIL_FADE_NEAR_M, TRAIL_FADE_FAR_M)
        fl = _polyline("Finish line", [(a[0], a[1], 1.5), (b[0], b[1], 1.5)],
                       COURSE_LINE_RADIUS_M * 0.9, mat)
        _link(scene, coll, fl)


# ---------------------------------------------------------------------------
# Yachts.

def _hull_mesh(name: str, mat: bpy.types.Material) -> bpy.types.Object:
    """A lofted sailing-yacht hull: fine bow, full midships, transom stern, fin keel.

    Stations run from the transom (y = -L/2) to the stem (y = +L/2); each has a
    U-shaped section whose half-beam and depth follow simple curves. Units are
    metres before BOAT_SCALE; the deck is at z = 0 and the waterline about
    0.45 m below it, so the object sits on the water at location z = 0.
    """
    s = BOAT_SCALE
    L, B, D = LOA_M * s, BEAM_M * s, 1.35 * s          # length, beam, hull depth deck-to-keel line
    freeboard = 0.9 * s
    n_stations = 14
    n_section = 9                                      # points per half-section, deck edge to keel
    bm = bmesh.new()
    rings: List[List[bmesh.types.BMVert]] = []
    for k in range(n_stations + 1):
        f = k / n_stations                             # 0 stern .. 1 bow
        y = -L / 2.0 + f * L
        # Half-beam: full at 55% of the length, tapering to a point at the bow and a transom aft.
        t = (f - 0.55) / 0.55
        half_beam = B / 2.0 * max(0.0, 1.0 - t * t) ** 0.7
        if f < 0.55:
            half_beam = max(half_beam, B * 0.28 * (1.0 - (0.55 - f) / 0.55) + B * 0.22)
        if k == n_stations:
            half_beam = 0.02 * s
        depth = D * max(0.25, 1.0 - ((f - 0.45) / 0.6) ** 2)
        if k == n_stations:
            depth *= 0.6
        deck_z = freeboard * (0.85 + 0.35 * f * f)     # sheer: bow higher than stern
        ring: List[bmesh.types.BMVert] = []
        for i in range(2 * n_section + 1):
            a = -1.0 + i / n_section                    # -1 port deck edge .. 0 keel .. +1 starboard deck edge
            u = abs(a)
            x = a * half_beam * (0.15 + 0.85 * u) ** 0.5 if u > 0 else 0.0
            z = deck_z - (deck_z + depth) * (1.0 - u ** 1.8)
            ring.append(bm.verts.new((x, y, z)))
        rings.append(ring)
    for r0, r1 in zip(rings, rings[1:]):
        for i in range(len(r0) - 1):
            bm.faces.new((r0[i], r0[i + 1], r1[i + 1], r1[i]))
    # Deck and transom.
    bm.faces.new(list(reversed(rings[0])))
    for r0, r1 in zip(rings, rings[1:]):
        bm.faces.new((r0[0], r1[0], r1[-1], r0[-1]))
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=0.01 * s)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    hull = _mesh_object(name, bm, mat, smooth=True)
    return hull


def build_yacht(scene: bpy.types.Scene, coll: bpy.types.Collection, name: str,
                color: Tuple[float, float, float], sail_mat: bpy.types.Material,
                label_mat: bpy.types.Material, eye: bpy.types.Object,
                model_template: Optional[List[bpy.types.Object]] = None, label_lift: float = 0.0,
                with_label: bool = True) -> Dict[str, bpy.types.Object]:
    """A yacht. Returns (root, rig): keyframe the root; rotate the rig for trim.

    Bow points +Y, starboard is +X, the mast foot is 30% of the length aft of the bow.
    With ``model_template`` (objects prepared by ``prepare_boat_model``) the hull is a
    linked copy of that model instead of the procedural one; the rig is the same.
    """
    s = BOAT_SCALE
    hull_mat = _material(f"Hull {name}", color, roughness=0.3)
    deck_mat = _material(f"Deck {name}", (0.90, 0.88, 0.82), roughness=0.7)
    spar_mat = _material(f"Spar {name}", (0.75, 0.76, 0.78), metallic=0.8, roughness=0.3)

    root = _link(scene, coll, _empty(f"Boat {name}"))
    if model_template:
        copies: Dict[bpy.types.Object, bpy.types.Object] = {}
        for src in model_template:
            dup = src.copy()
            dup.name = f"{src.name} {name}"
            copies[src] = dup
            _link(scene, coll, dup)
        for src, dup in copies.items():
            dup.parent = copies.get(src.parent, root) if src.parent in copies else root
    else:
        hull = _hull_mesh("Hull", hull_mat)
        hull.parent = root
        _link(scene, coll, hull)
        cabin = _cube("Cabin", (BEAM_M * 0.55 * s, LOA_M * 0.28 * s, 0.55 * s), (0.0, -LOA_M * 0.02 * s, 1.15 * s), deck_mat)
        bev = cabin.modifiers.new("Bevel", "BEVEL")
        bev.width = 0.12 * s
        bev.segments = 2
        cabin.parent = root
        _link(scene, coll, cabin)
        keel = _cube("Keel", (0.18 * s, LOA_M * 0.22 * s, DRAFT_M * s), (0.0, -LOA_M * 0.02 * s, -DRAFT_M * 0.5 * s - 0.9 * s), hull_mat)
        keel.parent = root
        _link(scene, coll, keel)
        rudder = _cube("Rudder", (0.08 * s, 0.35 * s, 1.4 * s), (0.0, -LOA_M * 0.44 * s, -0.7 * s - 0.6 * s), hull_mat)
        rudder.parent = root
        _link(scene, coll, rudder)

    mast_y = LOA_M * 0.20 * s
    rig = _link(scene, coll, _empty(f"Rig {name}"))
    rig.parent = root
    rig.location = (0.0, mast_y, 1.0 * s)
    mast = _cylinder("Mast", 0.12 * s, MAST_M * s, (0.0, 0.0, MAST_M * s / 2.0), spar_mat, segments=10)
    mast.parent = rig
    _link(scene, coll, mast)
    boom_len = LOA_M * 0.40 * s
    boom = _cylinder("Boom", 0.09 * s, boom_len, (0.0, 0.0, 0.0), spar_mat, segments=8)
    boom.rotation_euler = (math.radians(90.0), 0.0, 0.0)
    boom.location = (0.0, -boom_len / 2.0, 1.4 * s)
    boom.parent = rig
    _link(scene, coll, boom)
    main = _triangle("Mainsail", Vector((0.0, 0.0, 1.5 * s)), Vector((0.0, 0.0, MAST_M * 0.97 * s)),
                     Vector((0.0, -boom_len, 1.5 * s)), sail_mat)
    main.parent = rig
    _link(scene, coll, main)
    # Headsail: tack at the bow, so it lives on the rig but reaches forward of the mast.
    bow = LOA_M * 0.5 * s - mast_y
    jib = _triangle("Headsail", Vector((0.0, bow, 0.9 * s)), Vector((0.0, 0.0, MAST_M * 0.85 * s)),
                    Vector((0.0, -boom_len * 0.35, 1.2 * s)), sail_mat)
    jib.parent = rig
    _link(scene, coll, jib)

    # Spinnaker: on its own pivot at the mast foot (not the rig, whose boom angle
    # would swing it too far), hidden until a downwind leg. Bellied, symmetric.
    kite_pivot = _link(scene, coll, _empty(f"Kite {name}"))
    kite_pivot.parent = root
    kite_pivot.location = (0.0, mast_y, 1.0 * s)
    kite_color = tuple(min(1.0, 0.15 + 0.85 * c) for c in color)
    kite = _spinnaker("Spinnaker", bow, MAST_M * 0.95 * s, bow * 1.0, s, _spinnaker_material(name, kite_color))
    kite.parent = kite_pivot
    kite.hide_render = True
    kite.hide_viewport = True
    _link(scene, coll, kite)
    # Spinnaker pole: mast to the windward clew. Modelled to the port clew and
    # mirrored (scale x = -1) on the other gybe by animate_boat.
    clew = Vector((-bow * _kite_half_width(0.0), bow * 1.25, 0.9 * s))
    foot_of_mast = Vector((0.0, 0.0, 1.3 * s))
    pole_vec = clew - foot_of_mast
    pole = _cylinder("Pole", 0.08 * s, pole_vec.length, (0.0, 0.0, pole_vec.length / 2.0), spar_mat, segments=8)
    pole.rotation_mode = "QUATERNION"
    pole.rotation_quaternion = Vector((0.0, 0.0, 1.0)).rotation_difference(pole_vec.normalized())
    pole.location = foot_of_mast
    pole.parent = kite_pivot
    pole.hide_render = True
    pole.hide_viewport = True
    _link(scene, coll, pole)

    # The name rides above the mast but is not parented to the hull, so it
    # neither heels nor turns with it: a Copy Location constraint follows the root.
    # Wake. It rides on its own pivot that copies the hull's position and heading
    # but not its heel: heel turns about the fore-and-aft axis, which is the
    # wake's own long axis, so a wake parented to the hull would roll its edges
    # ten metres clear of the water every time the boat leaned.
    wake_pivot = _link(scene, coll, _empty(f"Wake pivot {name}"))
    wcon = wake_pivot.constraints.new("COPY_LOCATION")
    wcon.target = root
    wrot = wake_pivot.constraints.new("COPY_ROTATION")
    wrot.target = root
    wrot.use_x = wrot.use_y = False
    wake_mat, wake_speed = _wake_material(f"Wake {name}")
    wake = _wake(f"Wake {name}", wake_mat, s)
    wake.parent = wake_pivot
    _link(scene, coll, wake)

    label = None
    if with_label:
        label = _label(f"Name {name}", name, label_mat, eye, LABEL_PER_METRE * 0.9)
        con = label.constraints.new("COPY_LOCATION")
        con.target = root
        con.use_offset = True                                   # keep our own height above the hull
        label.constraints.move(len(label.constraints) - 1, 0)  # position first, then face the eye
        # Each boat's name is lifted a little further than the last, so a fleet
        # bunched at the start does not stack its labels on top of one another.
        label.location = (0.0, 0.0, (MAST_M + 3.0) * s + label_lift)
        _link(scene, coll, label)
    return {"root": root, "rig": rig, "jib": jib, "kite": kite, "kite_pivot": kite_pivot,
            "pole": pole, "wake": wake, "wake_speed": wake_speed, "label": label}


def _kite_half_width(v: float) -> float:
    """Half-width of a symmetric spinnaker as a fraction of its maximum, foot (0) to head (1).

    Shaped on the classic cut: a foot a little narrower than the shoulders,
    widest about a third of the way up, then convex leeches closing to the head.
    """
    return (1.0 - v) ** 0.42 * (1.0 + 0.65 * v) / 1.02


def _spinnaker(name: str, bow: float, head_z: float, half_max: float, s: float,
               mat: bpy.types.Material) -> bpy.types.Object:
    """A symmetric spinnaker, lofted: rounded shoulders, deep belly, lifting foot.

    Head at the masthead, clews wide and just ahead of the bow, the body bellied
    forward like a filled balloon (cross-sections are arcs) and the foot rising
    in the middle. Sixteen by sixteen quads, smooth shaded. ``half_max`` is the
    half-width at the shoulders, about the boat's J measurement.
    """
    nu, nv = 16, 16
    bm = bmesh.new()
    grid: List[List[bmesh.types.BMVert]] = []
    z_foot = 0.9 * s
    for j in range(nv + 1):
        v = j / nv
        w = half_max * _kite_half_width(v)
        y_axis = bow * (1.25 - 0.95 * v)              # clews ahead of the bow, head over the foredeck
        z_axis = z_foot + (head_z - z_foot) * v
        belly = w * 0.75                              # arc depth of this cross-section
        row: List[bmesh.types.BMVert] = []
        for i in range(nu + 1):
            u = i / nu
            a = math.pi * u                           # 0 = port leech, pi = starboard leech
            x = -w * math.cos(a)
            y = y_axis + belly * math.sin(a) * (0.35 + 0.65 * (1.0 - v) ** 0.5)
            z = z_axis + 0.35 * s * math.sin(a) * (1.0 - v) ** 2   # the foot floats up in the middle
            row.append(bm.verts.new((x, y, z)))
        grid.append(row)
    for j in range(nv):
        for i in range(nu):
            bm.faces.new((grid[j][i], grid[j][i + 1], grid[j + 1][i + 1], grid[j + 1][i]))
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=0.01 * s)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    mat.use_backface_culling = False
    return _mesh_object(name, bm, mat, smooth=True)


def _cloth(mat: bpy.types.Material, translucency: float = 0.55) -> bpy.types.Material:
    """Make a sail material pass light, the way real cloth does.

    Measured on the first cut of this film: a plain diffuse sail, backlit,
    rendered at (166,176,184) against a sky of (168,178,188). Four levels
    apart, so the boats simply vanished whenever the camera looked towards the
    sun. The camera-facing side of a backlit sail is lit by nothing but the
    sky, so diffuse alone can only ever match the sky.

    Real sailcloth is thin and transmits, which is why a backlit sail glows
    rather than silhouettes. Mixing a Translucent BSDF in gives it the sun from
    behind and it separates in both directions.
    """
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    bsdf = nodes.get("Principled BSDF")
    output = next(n for n in nodes if n.type == "OUTPUT_MATERIAL")
    colour = bsdf.inputs["Base Color"]
    trans = nodes.new("ShaderNodeBsdfTranslucent")
    if colour.links:
        links.new(colour.links[0].from_socket, trans.inputs["Color"])
    else:
        trans.inputs["Color"].default_value = colour.default_value
    mix = nodes.new("ShaderNodeMixShader")
    mix.inputs[0].default_value = translucency
    links.new(bsdf.outputs["BSDF"], mix.inputs[1])
    links.new(trans.outputs["BSDF"], mix.inputs[2])
    links.new(mix.outputs["Shader"], output.inputs["Surface"])
    return mat


def _spinnaker_material(name: str, color: Tuple[float, float, float]) -> bpy.types.Material:
    """Panelled kite cloth: the boat's colour with white horizontal panels."""
    mat = _new_material(f"Spinnaker {name}")
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    bsdf = nodes.get("Principled BSDF")
    bsdf.inputs["Roughness"].default_value = 0.8
    coords = nodes.new("ShaderNodeTexCoord")
    sep = nodes.new("ShaderNodeSeparateXYZ")
    band = nodes.new("ShaderNodeMath")
    band.operation = "MODULO"
    band.inputs[1].default_value = 2.0 * BOAT_SCALE            # panel height
    step = nodes.new("ShaderNodeMath")
    step.operation = "GREATER_THAN"
    step.inputs[1].default_value = 1.2 * BOAT_SCALE            # colour:white ratio
    mix = nodes.new("ShaderNodeMixRGB")
    mix.inputs["Color1"].default_value = (*color, 1.0)
    mix.inputs["Color2"].default_value = (0.96, 0.96, 0.93, 1.0)
    links.new(coords.outputs["Object"], sep.inputs["Vector"])
    links.new(sep.outputs["Z"], band.inputs[0])
    links.new(band.outputs["Value"], step.inputs[0])
    links.new(step.outputs["Value"], mix.inputs["Fac"])
    links.new(mix.outputs["Color"], bsdf.inputs["Base Color"])
    return _cloth(mat, 0.6)


# Spinnaker rules: hoist when the true wind angle has been at least this deep for
# KITE_HOIST_S, drop once it has been shallower than KITE_DROP_TWA for KITE_DROP_S.
KITE_HOIST_TWA = 125.0
KITE_DROP_TWA = 108.0
KITE_HOIST_S = 25.0
KITE_DROP_S = 15.0


def prepare_boat_model(path: str, forward: str, template_coll: bpy.types.Collection,
                       waterline_frac: float = 0.3) -> List[bpy.types.Object]:
    """Import a hull model and normalise it to the yacht rig's frame.

    The model is scaled so its length is LOA_M x BOAT_SCALE, rotated so
    ``forward`` (``+Y``, ``-Y``, ``+X``, ``-X``, ``+Z``, ``-Z`` in the file's own
    axes after import) points along +Y, and lowered so ``waterline_frac`` of its
    height is below z = 0. The imported objects are parked in ``template_coll``
    (hidden) and linked copies are made per boat.
    """
    before = set(bpy.data.objects)
    lower = path.lower()
    if lower.endswith((".glb", ".gltf")):
        bpy.ops.import_scene.gltf(filepath=path)
    elif lower.endswith(".fbx"):
        bpy.ops.import_scene.fbx(filepath=path)
    elif lower.endswith(".obj"):
        bpy.ops.wm.obj_import(filepath=path)
    else:
        raise ValueError(f"unsupported boat model format: {path}")
    new = [o for o in bpy.data.objects if o not in before]
    if not new:
        raise ValueError(f"nothing imported from {path}")
    for obj in new:
        for coll in list(obj.users_collection):
            coll.objects.unlink(obj)
        template_coll.objects.link(obj)
    roots = [o for o in new if o.parent not in new]
    holder = _empty("Boat model")
    template_coll.objects.link(holder)
    for r in roots:
        r.parent = holder

    # Measure in world space with the holder at identity.
    bpy.context.view_layer.update()
    corners = [r.matrix_world @ Vector(c) for o in new if o.type == "MESH" for c in o.bound_box for r in [o]]
    if not corners:
        raise ValueError(f"{path} has no mesh objects")
    lo = Vector((min(c.x for c in corners), min(c.y for c in corners), min(c.z for c in corners)))
    hi = Vector((max(c.x for c in corners), max(c.y for c in corners), max(c.z for c in corners)))
    size = hi - lo
    axis = forward.strip().upper()
    axis_index = {"X": 0, "Y": 1, "Z": 2}[axis[-1]]
    length = size[axis_index] or 1.0
    scale = LOA_M * BOAT_SCALE / length
    rot = {"+Y": 0.0, "-Y": math.pi, "+X": math.pi / 2.0, "-X": -math.pi / 2.0}.get(axis, 0.0)
    holder.scale = (scale, scale, scale)
    holder.rotation_euler = (0.0, 0.0, rot)
    centre = (lo + hi) / 2.0
    height = size.z * scale
    holder.location = (0.0, 0.0, 0.0)
    # Centre the model in plan, and put the waterline at z = 0.
    offset = Vector((-centre.x * scale, -centre.y * scale, -(lo.z * scale) - waterline_frac * height))
    offset.rotate(holder.rotation_euler)
    holder.location = offset
    holder.hide_render = True
    holder.hide_viewport = True
    for obj in new:
        obj.hide_render = True
        obj.hide_viewport = True
    return [holder] + new


def _unhide_copies(objs: Sequence[bpy.types.Object]) -> None:
    for o in objs:
        o.hide_render = False
        o.hide_viewport = False


def build_trail(scene: bpy.types.Scene, coll: bpy.types.Collection, boat: Dict[str, Any],
                colour: Tuple[float, float, float], frame_of: Callable[[float], int],
                step_s: float, seconds: float = TRAIL_SECONDS) -> Optional[bpy.types.Object]:
    """A short wake behind the boat, as the chart page draws it.

    The whole track is one curve, and the pair of bevel factors that decide how
    much of a curve is rendered are keyframed to a sliding window: the head
    follows the boat, the tail follows it ``seconds`` of racing later. That is a
    couple of animated numbers rather than a mesh rebuilt every frame.
    """
    live = [s for s in boat["samples"] if s[1] is not None]
    if len(live) < 3:
        return None
    curve = bpy.data.curves.new(f"Trail {boat['name']}", type="CURVE")
    curve.dimensions = "3D"
    curve.bevel_depth = TRAIL_RADIUS_M
    curve.bevel_resolution = 1
    curve.fill_mode = "FULL"
    # RESOLUTION, despite the name, maps a factor onto the evaluated points --
    # which for a poly spline are the fixes themselves. So a factor is a position
    # in the list of fixes, and therefore in time. (SEGMENTS maps by distance
    # along the water, which puts the head of the trail ahead of a slow boat.)
    curve.bevel_factor_mapping_start = "RESOLUTION"
    curve.bevel_factor_mapping_end = "RESOLUTION"
    spline = curve.splines.new("POLY")
    spline.points.add(len(live) - 1)
    for point, sample in zip(spline.points, live):
        point.co = (sample[1], sample[2], 0.6, 1.0)

    mat = _material(f"Trail {boat['name']}", colour, roughness=0.6, emission=0.25)
    _fade_near_camera(mat, TRAIL_FADE_NEAR_M, TRAIL_FADE_FAR_M)
    curve.materials.append(mat)
    obj = bpy.data.objects.new(f"Trail {boat['name']}", curve)
    _link(scene, coll, obj)

    n = len(live) - 1
    back = max(1, int(round(seconds / max(1e-6, step_s))))
    for i, sample in enumerate(live):
        f = frame_of(float(sample[0]))
        curve.bevel_factor_end = i / n
        curve.bevel_factor_start = max(0.0, (i - back) / n)
        curve.keyframe_insert("bevel_factor_end", frame=f)
        curve.keyframe_insert("bevel_factor_start", frame=f)
    for fcurve in _fcurves(curve):
        for kp in fcurve.keyframe_points:
            kp.interpolation = "LINEAR"
    return obj


def _kite_plan(samples: List[List[Optional[float]]], wind: "Wind") -> List[bool]:
    """Whether the spinnaker is flying at each sample, with hoist/drop hysteresis.

    A kite goes up when the boat has been sailing deep for a while and comes
    down once it has been reaching for a while: a single wobbly heading
    should not make it flash in and out.
    """
    if wind.at(0.0)[0] is None or not samples:
        return [False] * len(samples)
    step = (samples[1][0] - samples[0][0]) if len(samples) > 1 else 5.0
    hoist_n = max(1, int(round(KITE_HOIST_S / step)))
    drop_n = max(1, int(round(KITE_DROP_S / step)))
    flying = False
    deep = shallow = 0
    out: List[bool] = []
    for _t, x, _y, heading, speed in samples:
        if x is None:
            out.append(False)
            continue
        # A boat on a steady heading through a thirty-degree shift changes from
        # reaching to running without touching the helm, and that is exactly
        # when a kite goes up.
        angle = wind.twa(float(_t), float(heading))
        twa = abs(angle) if angle is not None else 0.0
        moving = (speed or 0.0) > 1.0
        if twa >= KITE_HOIST_TWA and moving:
            deep += 1
            shallow = 0
        elif twa < KITE_DROP_TWA or not moving:
            shallow += 1
            deep = 0
        else:
            deep = shallow = 0        # in between: hold whatever is set
        if not flying and deep >= hoist_n:
            flying = True
        elif flying and shallow >= drop_n:
            flying = False
        out.append(flying)
    return out


def animate_boat(parts: Dict[str, bpy.types.Object], samples: List[List[Optional[float]]],
                 frame_of: Callable[[float], int], wind: "Wind") -> None:
    """Keyframe position, heading, heel, trim and sail plan from the resampled track."""
    root, rig, jib, kite, kite_pivot, pole = (parts[k] for k in ("root", "rig", "jib", "kite", "kite_pivot", "pole"))
    # The wake hangs off a constrained pivot rather than the hull, so hiding the
    # hull does not hide it; it has to be switched with the boat by hand.
    hides = [root] + [parts[k] for k in ("wake", "label") if parts.get(k)]
    wake_speed = parts.get("wake_speed")
    prev_yaw: Optional[float] = None
    visible: Optional[bool] = None
    kite_up = _kite_plan(samples, wind)
    kite_state: Optional[bool] = None
    pole_side: Optional[float] = None
    for idx, (t_rel, x, y, heading, speed) in enumerate(samples):
        f = frame_of(t_rel)
        if x is None:
            if visible is not False:
                for obj in hides:
                    obj.hide_viewport = obj.hide_render = True
                    obj.keyframe_insert("hide_viewport", frame=f)
                    obj.keyframe_insert("hide_render", frame=f)
                visible = False
            continue
        if visible is not True:
            for obj in hides:
                obj.hide_viewport = obj.hide_render = False
                obj.keyframe_insert("hide_viewport", frame=f)
                obj.keyframe_insert("hide_render", frame=f)
            visible = True
        yaw = _unwrap(prev_yaw, _heading_to_yaw(float(heading)))
        prev_yaw = yaw
        # True wind angle: positive with the wind on the starboard side, taken
        # at this moment rather than from the race's mean.
        now_twd, now_tws = wind.at(float(t_rel))
        twa = wind.twa(float(t_rel), float(heading)) or 0.0
        # Heel follows the breeze as well as the angle: the same boat on the
        # same heading lies over further in eight knots than in two.
        heel_gain = min(1.0, (now_tws or 8.0) / 14.0)
        side = 1.0 if twa >= 0.0 else -1.0
        drive = 1.0 if (speed or 0.0) > 1.0 else 0.3
        heel = -side * math.radians(MAX_HEEL_DEG) * heel_gain * drive * min(1.0, abs(math.sin(math.radians(twa))) + 0.2)
        if wake_speed is not None:
            wake_speed.outputs[0].default_value = float(speed or 0.0)
            wake_speed.outputs[0].keyframe_insert("default_value", frame=f)
        root.location = (float(x), float(y), 0.0)
        root.rotation_euler = (0.0, heel, yaw)
        root.keyframe_insert("location", frame=f)
        root.keyframe_insert("rotation_euler", frame=f)
        # Boom swings away from the wind: close-hauled ~15 deg, running ~80 deg.
        boom_angle = min(80.0, max(12.0, abs(twa) * 0.5))
        rig.rotation_euler = (0.0, 0.0, -side * math.radians(boom_angle))
        rig.keyframe_insert("rotation_euler", frame=f)
        # Sail plan: kite up and headsail away on the deep legs, the reverse otherwise.
        up = kite_up[idx]
        if up != kite_state:
            for obj, hidden in ((kite, not up), (pole, not up), (jib, up)):
                obj.hide_render = hidden
                obj.hide_viewport = hidden
                obj.keyframe_insert("hide_render", frame=f)
                obj.keyframe_insert("hide_viewport", frame=f)
            kite_state = up
        # The pole goes to windward: modelled to port, mirrored when the wind is on the port side.
        if side != pole_side:
            pole.scale = (1.0 if side > 0 else -1.0, 1.0, 1.0)
            pole.keyframe_insert("scale", frame=f)
            pole_side = side
        # The kite leans a little to leeward and squares back as the boat runs deeper.
        kite_pivot.rotation_euler = (0.0, 0.0, -side * math.radians(max(0.0, 30.0 - (abs(twa) - 120.0) * 0.5)))
        kite_pivot.keyframe_insert("rotation_euler", frame=f)
    _set_interpolation(root, "LINEAR")
    _set_interpolation(rig, "LINEAR")
    _set_interpolation(kite_pivot, "LINEAR")
    # Hide/show must step, not blend.
    _set_interpolation(root, "CONSTANT", only_prefix="hide_")
    _set_interpolation(kite, "CONSTANT")
    _set_interpolation(jib, "CONSTANT")
    _set_interpolation(pole, "CONSTANT")


# ---------------------------------------------------------------------------
# Cameras and the shot list.

def _live_samples(boat: Dict[str, Any]) -> List[List[float]]:
    return [s for s in boat["samples"] if s[1] is not None]


def _leader(data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The boat to follow: first finisher, else the one with the most track."""
    boats = [b for b in data["boats"] if _live_samples(b)]
    if not boats:
        return None
    finished = [b for b in boats if b.get("finish_t") is not None]
    if finished:
        return min(finished, key=lambda b: b["finish_t"])
    return max(boats, key=lambda b: len(_live_samples(b)))


def _rounding_time(boat: Dict[str, Any], mark_xy: Sequence[float], after_t: float,
                   near_m: float = 250.0) -> Optional[float]:
    """When the boat next rounded a mark after ``after_t``: the closest point of
    its first pass within ``near_m``. None if it never came that close.

    The first pass, not the closest ever: a two-lap course is rounded twice and
    the tighter rounding is as likely to be the second one.
    """
    best: Optional[Tuple[float, float]] = None
    for t_rel, x, y, _h, _s in _live_samples(boat):
        if t_rel < after_t:
            continue
        d = math.hypot(x - mark_xy[0], y - mark_xy[1])
        if d <= near_m:
            if best is None or d < best[0]:
                best = (d, t_rel)
        elif best is not None:
            return best[1]                     # left the circle: the pass is over
    return best[1] if best else None


def _roundings(leader: Dict[str, Any], course: List[Dict[str, Any]], first_start: float,
               finish_t: Optional[float], max_laps: int = 6) -> List[Tuple[Dict[str, Any], float]]:
    """Every mark rounding of the leader, in order: (course point, time).

    Walks the course marks in sequence, and keeps cycling through them while
    the leader keeps passing them, so a track with more laps than the recorded
    course (a race shortened after the fact) still gets every rounding cut in.
    """
    marks = [p for p in course[1:] if p.get("mark") != "FINISH"]
    if not marks:
        return []
    out: List[Tuple[Dict[str, Any], float]] = []
    t_after = first_start + 60.0
    deadline = (finish_t - 90.0) if finish_t is not None else float("inf")
    for _lap in range(max_laps):
        found_this_lap = False
        for point in marks:
            t = _rounding_time(leader, point["xy"], t_after)
            if t is None or t > deadline:
                continue
            out.append((point, t))
            t_after = t + 60.0
            found_this_lap = True
        if not found_this_lap:
            break
    return out


def _smoothed_heading(samples: List[List[float]], idx: int, half_window: int = 6) -> float:
    """Mean direction of travel over a window of samples, so a chase camera does not jitter."""
    sx = sy = 0.0
    for k in range(max(0, idx - half_window), min(len(samples), idx + half_window + 1)):
        h = math.radians(samples[k][3])
        sx += math.sin(h)
        sy += math.cos(h)
    return math.degrees(math.atan2(sx, sy)) % 360.0 if (sx or sy) else samples[idx][3]


def _fit_distance(points: Sequence[Sequence[float]], target: Sequence[float], view_dir: Vector,
                  lens: float, res_x: int, res_y: int, sensor_w: float = 36.0,
                  margin: float = 1.14) -> float:
    """How far back a camera must sit, along -``view_dir`` from ``target``, to frame ``points``.

    Exact rather than guessed: for a pinhole camera the horizontal and vertical
    limits are ``|x| <= z*tan(fov/2)``, and only the depth term depends on the
    distance, so each point gives a lower bound on it and the answer is the
    largest. ``margin`` leaves a little air around the outermost point.
    """
    v = view_dir.normalized()
    up_ref = Vector((0.0, 0.0, 1.0))
    right = v.cross(up_ref)
    right = right.normalized() if right.length > 1e-6 else Vector((1.0, 0.0, 0.0))
    up = right.cross(v).normalized()
    tx = (sensor_w / 2.0) / lens / margin
    ty = tx * res_y / res_x
    t = Vector((target[0], target[1], target[2] if len(target) > 2 else 0.0))
    best = 0.0
    for p in points:
        q = Vector((p[0], p[1], p[2] if len(p) > 2 else 0.0)) - t
        depth = q.dot(v)
        best = max(best, abs(q.dot(right)) / tx - depth, abs(q.dot(up)) / ty - depth)
    return max(best, 1.0)


def _terrain_height_at(terrain: Optional[Dict[str, Any]], x: float, y: float) -> float:
    """Land height at a local coordinate, from the exported grid. 0 where there is no grid."""
    if not terrain:
        return 0.0
    nx, ny = int(terrain["nx"]), int(terrain["ny"])
    cell = float(terrain["cell_m"])
    i = int(round((x - float(terrain["x0"])) / cell))
    j = int(round((y - float(terrain["y0"])) / cell))
    if not (0 <= i < nx and 0 <= j < ny):
        return 0.0
    return float(terrain["heights"][j * nx + i])


def _smooth(values: List[float], half_window: int) -> List[float]:
    """Moving average, so a camera driven by the fleet's spread glides instead of pumping."""
    if half_window <= 0 or len(values) < 3:
        return list(values)
    out: List[float] = []
    for i in range(len(values)):
        lo = max(0, i - half_window)
        hi = min(len(values), i + half_window + 1)
        out.append(sum(values[lo:hi]) / (hi - lo))
    return out


def _new_camera(scene: bpy.types.Scene, coll: bpy.types.Collection, name: str, lens: float,
                extent: float) -> bpy.types.Object:
    cam_data = bpy.data.cameras.new(PREFIX + name)
    cam_data.lens = lens
    cam_data.clip_start = 1.0
    cam_data.clip_end = extent * 2.0 * HAZE_FAR * 1.5
    # Focus is wired to the tracked target by _track_to; without a target the
    # focus distance stays at Blender's default and nothing blurs.
    cam_data.dof.use_dof = True
    cam_data.dof.aperture_fstop = float(os.environ.get("REPLAY_FSTOP", APERTURE_FSTOP))
    cam_data.dof.aperture_blades = 7
    cam = bpy.data.objects.new(f"Camera {name}", cam_data)
    return _link(scene, coll, cam)


def build_overview_camera(scene: bpy.types.Scene, coll: bpy.types.Collection, data: Dict[str, Any],
                          extent: float, frame_of: Callable[[float], int], leader: Optional[Dict[str, Any]],
                          roundings: List[Tuple[Dict[str, Any], float]]
                          ) -> Tuple[bpy.types.Object, bpy.types.Object]:
    """The wide shot, keyframed to hold the leg being sailed rather than the whole course.

    At each sample it frames the boats still on the water together with the marks
    at both ends of the leg the leader is on, then sits seaward of that at
    whatever distance fits it, so a long first leg comes in close and a lap of
    the whole course pulls out. Distance and aim are smoothed over two minutes so
    the camera glides instead of pumping between samples.
    """
    lens = 35.0
    u = Vector((0.22, -1.0, 0.42)).normalized()      # offset from the action: seaward, and above
    cam = _new_camera(scene, coll, "overview", lens, extent)
    focus = _link(scene, coll, _empty("Focus overview"))
    _track_to(cam, focus)

    course = data.get("course") or []
    duration = float(data["time"]["duration_s"])
    res_x, res_y = scene.render.resolution_x, scene.render.resolution_y

    # Where the leader is between, over time: the start mark, each rounding, the finish.
    legs: List[Tuple[float, Vector]] = []
    if course:
        legs.append((0.0, Vector((course[0]["xy"][0], course[0]["xy"][1], 0.0))))
    for point, t_round in roundings:
        legs.append((t_round, Vector((point["xy"][0], point["xy"][1], 0.0))))
    line = data.get("finish_line")
    if line:
        mid = (Vector((*line["seaward_xy"], 0.0)) + Vector((*line["shore_xy"], 0.0))) / 2.0
        legs.append((float((leader or {}).get("finish_t") or duration), mid))

    samples = data["boats"][0]["samples"] if data.get("boats") else []
    if not samples or len(legs) < 2:
        # Nothing to follow: frame the course from a fixed position, as before.
        pts = [Vector((p["xy"][0], p["xy"][1], 0.0)) for p in course] or [Vector((0.0, 0.0, 0.0))]
        centre = sum(pts, Vector((0.0, 0.0, 0.0))) / len(pts)
        d = min(_fit_distance(pts, centre, -u, lens, res_x, res_y), extent * 4.0)
        focus.location = centre
        cam.location = centre + u * max(d, 400.0)
        return cam, focus

    tracks = [b["samples"] for b in data["boats"]]
    centres: List[Vector] = []
    dists: List[float] = []
    leg_i = 0
    for idx, sample in enumerate(samples):
        t = float(sample[0])
        while leg_i + 2 < len(legs) and t >= legs[leg_i + 1][0]:
            leg_i += 1
        pts = [legs[leg_i][1], legs[leg_i + 1][1]]
        for track in tracks:
            if idx < len(track) and track[idx][1] is not None:
                pts.append(Vector((track[idx][1], track[idx][2], 0.0)))
        centre = sum(pts, Vector((0.0, 0.0, 0.0))) / len(pts)
        centres.append(centre)
        dists.append(_fit_distance(pts, centre, -u, lens, res_x, res_y))

    half = max(1, int(round(120.0 / max(1e-6, float(data["time"]["step_s"])))))
    cx = _smooth([c.x for c in centres], half)
    cy = _smooth([c.y for c in centres], half)
    cd = _smooth(dists, half)
    for idx, sample in enumerate(samples):
        f = frame_of(float(sample[0]))
        d = max(400.0, min(cd[idx], extent * 4.0))
        centre = Vector((cx[idx], cy[idx], 0.0))
        focus.location = centre
        focus.keyframe_insert("location", frame=f)
        cam.location = centre + u * d
        cam.keyframe_insert("location", frame=f)
    _set_interpolation(cam, "LINEAR")
    _set_interpolation(focus, "LINEAR")
    return cam, focus


def _line_camera(scene: bpy.types.Scene, coll: bpy.types.Collection, name: str,
                 line: Dict[str, Any], frame_pts: Sequence[Vector], from_side: Vector,
                 data: Dict[str, Any], extent: float, lens: float,
                 target: Optional[Vector] = None, rise: float = 0.34,
                 bias: float = 0.12, floor: float = 150.0) -> bpy.types.Object:
    """A camera beyond a line, looking back at the boats coming to it.

    The start and the finish are the same shot with the clock the other way
    round: stand on the far side of the line so the fleet sails towards the
    lens rather than away from it, and frame ``frame_pts``.

    What they do not share is what to point at. A finish is about the line, so
    it aims at the middle of it and takes in both ends. A start is about the
    fleet, which is bunched at whichever end they have chosen: aiming at the
    line there leaves three boats in the corner of a frame filled with beach,
    so the start aims at the boats and lets the far end of the line run out of
    shot. ``rise`` is how far above the water the camera sits, as a fraction of
    its distance, and ``bias`` shifts the aim towards the side they come from.
    """
    a, b = Vector((*line["seaward_xy"], 0.0)), Vector((*line["shore_xy"], 0.0))
    mid = (a + b) / 2.0
    along = (a - b).normalized()
    perp = Vector((-along.y, along.x, 0.0))
    if perp.dot(from_side - mid) < 0:
        perp = -perp                              # now points to the side they come from

    aim = mid if target is None else target.copy()
    pts = list(frame_pts) or [a, b, from_side]
    cam_pos = None
    # Beyond the line looking back up the course first, so the boats sail
    # towards the camera; their own side is the fallback when that would put
    # the camera over land.
    for side in (-1.0, 1.0):
        u = (perp * side + Vector((0.0, 0.0, rise))).normalized()
        d = _fit_distance(pts, aim, -u, lens, scene.render.resolution_x, scene.render.resolution_y,
                          margin=1.2)
        d = min(max(d, floor), extent * 3.0)
        candidate = aim + u * d
        if _terrain_height_at(data.get("terrain"), candidate.x, candidate.y) < 3.0:
            cam_pos = candidate
            break
        cam_pos = cam_pos or candidate
    focus = _link(scene, coll, _empty(f"Focus {name}"))
    focus.location = aim + perp * (a - b).length * bias
    cam = _new_camera(scene, coll, name, lens, extent)
    cam.location = cam_pos
    _track_to(cam, focus)
    return cam


def build_cameras(scene: bpy.types.Scene, coll: bpy.types.Collection, data: Dict[str, Any],
                  boats: Dict[str, bpy.types.Object], extent: float,
                  frame_of: Callable[[float], int], shots: str, follow: Optional[str],
                  eye: bpy.types.Object) -> List[Dict[str, Any]]:
    """Create the cameras and, for ``shots == "film"``, bind them to timeline markers.

    ``eye`` is wired to sit wherever the live camera is. Returns the shot list.
    """
    duration = float(data["time"]["duration_s"])
    first_start = float(data["time"]["first_start_rel"])

    leader = _leader(data)
    leader_root = boats.get(leader["name"]) if leader else None
    if shots == "follow" and follow and follow in boats:
        leader_root = boats[follow]
        leader = next(b for b in data["boats"] if b["name"] == follow)
    roundings = _roundings(leader, data.get("course") or [], first_start, leader.get("finish_t")) if leader else []

    # Overview: still from seaward looking at the shore, but keyframed to hold the
    # leg being sailed. A fixed wide shot had to take in the whole course, which on
    # a long first leg left the boats as specks.
    overview, overview_focus = build_overview_camera(scene, coll, data, extent, frame_of, leader, roundings)

    plan: List[Dict[str, Any]] = [{"name": "overview", "camera": overview, "t0": 0.0}]

    if leader_root is not None and shots in ("film", "follow"):
        # The focus follows the leader's position but not its heel or heading.
        focus = _link(scene, coll, _empty("Focus leader"))
        fcon = focus.constraints.new("COPY_LOCATION")
        fcon.target = leader_root
        fcon.use_offset = True
        focus.location = (0.0, 0.0, 5.0 * BOAT_SCALE)

        # Chase camera: an anchor keyframed behind and above the leader along its
        # smoothed direction of travel, so the camera follows without inheriting heel.
        anchor = _link(scene, coll, _empty("Chase anchor"))
        live = _live_samples(leader)
        back, up = 60.0 * BOAT_SCALE, 7.0 * BOAT_SCALE
        for idx, (t_rel, x, y, _h, _s) in enumerate(live):
            h = math.radians(_smoothed_heading(live, idx))
            anchor.location = (x - math.sin(h) * back, y - math.cos(h) * back, up)
            anchor.keyframe_insert("location", frame=frame_of(t_rel))
        _set_interpolation(anchor, "LINEAR")
        chase = _new_camera(scene, coll, "chase", 40.0, extent)
        chase.parent = anchor
        _track_to(chase, focus)

        if shots == "follow":
            plan = [{"name": "chase", "camera": chase, "t0": 0.0}]
        else:
            course = data.get("course") or []
            finish_t = leader.get("finish_t")

            # The start. The film runs at real time for a minute either side of
            # the gun, so the opening wide shot would otherwise hold for two
            # minutes over the one moment the whole fleet is in one place.
            # Pwllheli starts and finishes on the same line, so an export made
            # before start_line existed falls back to the finish line, which for
            # the club line is the same geometry.
            start_line = data.get("start_line") or data.get("finish_line")
            if start_line and first_start > START_CUT_IN:
                sa = Vector((*start_line["seaward_xy"], 0.0))
                sb = Vector((*start_line["shore_xy"], 0.0))
                smid = (sa + sb) / 2.0
                # The boats come from the side away from the first mark they sail
                # to. Taking it from the course rather than from where the fleet
                # happens to be avoids being fooled by a boat circling early.
                first_leg = next((Vector((p["xy"][0], p["xy"][1], 0.0))
                                  for p in course
                                  if (Vector((p["xy"][0], p["xy"][1], 0.0)) - smid).length > 80.0), None)
                if first_leg is not None:
                    pre_start = smid + (smid - first_leg)
                else:
                    late = next((s for s in live if s[0] >= first_start - 60.0), live[0])
                    pre_start = Vector((late[1], late[2], 0.0))
                fleet = [Vector((s[1], s[2], 0.0))
                         for b in data["boats"] for s in _live_samples(b)
                         if first_start - START_CUT_IN <= s[0] <= first_start + START_CUT_OUT]
                if not fleet:
                    fleet = [smid]
                centre = sum(fleet, Vector((0.0, 0.0, 0.0))) / len(fleet)
                # Both ends of the line and the fleet. A long lens would put
                # the camera most of a kilometre back to take all that in and
                # the boats would be specks in a frame of coastline, so the
                # start uses a wide one instead and stands close: at 24 mm the
                # line spans the frame from about 280 m, which is near enough
                # to read three hulls. Aiming between the middle of the line
                # and the fleet keeps them off the edge without losing the far
                # end. Before the gun the boats barely move, so their own
                # spread is a few tens of metres; hence the floor.
                # Lifted off the water so the camera tilts up a little: aimed
                # level, half the frame was empty sea below the fleet.
                aim = (smid + centre) / 2.0 + Vector((0.0, 0.0, 6.0 * BOAT_SCALE))
                startcam = _line_camera(scene, coll, "start", start_line, fleet + [sa, sb],
                                        pre_start, data, extent, lens=24.0,
                                        target=aim, rise=0.16, bias=0.0, floor=200.0)
                plan.append({"name": "start", "camera": startcam, "t0": first_start - START_CUT_IN})
                plan.append({"name": "overview", "camera": overview, "t0": first_start + START_CUT_OUT})
            last_cut = (finish_t - 140.0) if finish_t is not None else duration

            # A tracking camera beside every mark the leader rounds, in order.
            # Each pass gets its own camera, placed off the approach line for
            # that pass, so a mark rounded twice is filmed from the right side both times.
            cut_in, cut_out = 90.0, 60.0
            chase_from = first_start + 150.0
            for n, (point, t_round) in enumerate(roundings, start=1):
                if t_round - cut_in < chase_from - 20.0 and n > 1:
                    continue                    # too soon after the previous rounding to cut away and back
                mark_xy = point["xy"]
                approach = next((s for s in live if s[0] >= t_round - cut_in), live[0])
                dx, dy = mark_xy[0] - approach[1], mark_xy[1] - approach[2]
                norm = math.hypot(dx, dy) or 1.0
                px, py = -dy / norm, dx / norm                     # perpendicular to the approach
                label = f"mark {point.get('display', point['mark'])} #{n}"
                markcam = _new_camera(scene, coll, label, 50.0, extent)
                markcam.location = (mark_xy[0] + px * 55.0 * BOAT_SCALE + dx / norm * 20.0 * BOAT_SCALE,
                                    mark_xy[1] + py * 55.0 * BOAT_SCALE + dy / norm * 20.0 * BOAT_SCALE,
                                    9.0 * BOAT_SCALE)
                _track_to(markcam, focus)
                # Long leg before this mark: come back to the overview for its middle.
                if t_round - cut_in - chase_from > 330.0:
                    plan.append({"name": "overview", "camera": overview, "t0": chase_from + 150.0})
                plan.append({"name": label, "camera": markcam, "t0": t_round - cut_in})
                chase_from = t_round + cut_out
                if chase_from < last_cut - 30.0:
                    plan.append({"name": "chase", "camera": chase, "t0": chase_from})
            if not any(s["name"].startswith("mark") for s in plan):
                plan.append({"name": "chase", "camera": chase, "t0": chase_from})
            # A long last leg gets the overview for its middle too.
            if last_cut - chase_from > 330.0:
                plan.append({"name": "overview", "camera": overview, "t0": chase_from + 150.0})

            line = data.get("finish_line")
            if finish_t is not None and line:
                # Which side does the fleet finish from? The leader's last approach.
                approach = next((s for s in reversed(live) if s[0] <= finish_t - 60.0), live[0])
                approach_pts = [Vector((s[1], s[2], 0.0)) for s in live
                                if finish_t - 200.0 <= s[0] <= finish_t + 10.0]
                ends = [Vector((*line["seaward_xy"], 0.0)), Vector((*line["shore_xy"], 0.0))]
                finishcam = _line_camera(scene, coll, "finish", line, ends + approach_pts,
                                         Vector((approach[1], approach[2], 0.0)),
                                         data, extent, lens=28.0)
                plan.append({"name": "finish", "camera": finishcam, "t0": finish_t - 140.0})

    # Keep the plan sane: sorted, strictly increasing, inside the film.
    plan.sort(key=lambda s: s["t0"])
    clean: List[Dict[str, Any]] = []
    for shot in plan:
        if shot["t0"] < 0.0 or shot["t0"] >= duration:
            continue
        if clean and shot["camera"] is clean[-1]["camera"]:
            continue                              # same camera again: not a cut
        if clean and shot["t0"] - clean[-1]["t0"] < 20.0:
            clean[-1] = shot                      # a shot shorter than 20 s is not worth the cut
        else:
            clean.append(shot)
    if not clean or clean[0]["t0"] > 0.0:
        clean.insert(0, {"name": "overview", "camera": overview, "t0": 0.0})
    for shot in clean:
        shot["frame"] = frame_of(shot["t0"])
    scene.camera = clean[0]["camera"]

    # Markers switch the active camera during playback and render.
    for shot in clean:
        marker = scene.timeline_markers.new(shot["name"], frame=shot["frame"])
        marker.camera = shot["camera"]

    # The eye follows whichever camera is live, by keyframed constraint influence.
    for shot in clean:
        con = eye.constraints.new("COPY_LOCATION")
        con.target = shot["camera"]
        con.name = shot["name"]
        shot["constraint"] = con
    for i, shot in enumerate(clean):
        con = shot["constraint"]
        starts = shot["frame"]
        ends = clean[i + 1]["frame"] if i + 1 < len(clean) else None
        con.influence = 0.0
        con.keyframe_insert("influence", frame=1)
        con.influence = 1.0
        con.keyframe_insert("influence", frame=starts)
        if ends is not None:
            con.influence = 0.0
            con.keyframe_insert("influence", frame=ends)
    _set_interpolation(eye, "CONSTANT")
    for shot in clean:
        shot.pop("constraint", None)
    return clean


def _video_windows(clips: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Clips in order, de-duplicated and trimmed so only one is ever on screen.

    The hut cuts a clip per finishing boat, so two boats crossing seconds apart
    leave two clips of the same moment; and a fleet finishing inside two minutes
    leaves clips that overlap.
    """
    out: List[Dict[str, Any]] = []
    for clip in sorted(clips, key=lambda c: float(c["t_start"])):
        if not clip.get("file"):
            continue
        if out and clip["kind"] == out[-1]["kind"] and abs(float(clip["t_event"]) - float(out[-1]["t_event"])) < 5.0:
            continue                                   # same event, second copy
        start = float(clip["t_start"])
        if out:
            start = max(start, float(out[-1]["t_end"]))
        if float(clip["t_end"]) - start < 5.0:
            continue
        entry = dict(clip)
        entry["t_start"] = start
        out.append(entry)
    return out


def build_videos(scene: bpy.types.Scene, coll: bpy.types.Collection, shots: List[Dict[str, Any]],
                 data: Dict[str, Any], json_path: str, speed: float, frame_of: Callable[[float], int],
                 video_speed: float = 1.0) -> List[Dict[str, Any]]:
    """Show the hut camera's start and finish clips picture-in-picture, bottom right.

    Each clip appears while the replay clock is inside the window it covers (its
    event, plus the seconds either side the app recorded). ``video_speed`` is the
    playback rate relative to real time: 1.0 plays the footage at natural speed
    centred on the event, which is the only way the moment itself is watchable
    when the replay around it is running 30x; 0 makes the footage follow the
    replay clock instead, fast-forwarding the whole two minutes.
    """
    clips = _video_windows(data.get("videos") or [])
    if not clips:
        return []
    base = os.path.dirname(os.path.abspath(json_path))
    t0_epoch = float(data["time"]["t0_epoch"])
    fps = scene.render.fps
    shown: List[Dict[str, Any]] = []

    for n, clip in enumerate(clips):
        sequence = clip.get("sequence")
        if not sequence:
            print(f"replay3d: clip {clip.get('id')} has no extracted frames; run prepare_video_frames.py. Skipped.")
            continue
        first = sequence["first"] if os.path.isabs(sequence["first"]) else os.path.join(base, sequence["first"])
        if not os.path.exists(first):
            print(f"replay3d: {first} not found; skipped")
            continue
        # An image sequence, not the .mp4 itself: a movie texture is decoded by
        # the graphics driver at render time, and on this machine's Intel chip
        # that segfaults the render the moment a clip's window opens.
        image = bpy.data.images.load(first, check_existing=False)
        image.name = PREFIX + os.path.basename(os.path.dirname(first))
        image.source = "SEQUENCE"
        width, height = float(sequence.get("width") or 1280), float(sequence.get("height") or 720)

        mat = _new_material(f"Video {n}")
        nodes, links = mat.node_tree.nodes, mat.node_tree.links
        for node in list(nodes):
            if node.type != "OUTPUT_MATERIAL":
                nodes.remove(node)
        output = next(node for node in nodes if node.type == "OUTPUT_MATERIAL")
        tex = nodes.new("ShaderNodeTexImage")
        tex.name = "Video"
        tex.image = image
        tex.extension = "EXTEND"
        tex.interpolation = "Linear"
        emit = nodes.new("ShaderNodeEmission")
        emit.inputs["Strength"].default_value = 1.0
        links.new(tex.outputs["Color"], emit.inputs["Color"])
        links.new(emit.outputs["Emission"], output.inputs["Surface"])
        # One extracted image per film frame, so the sequence needs no animation
        # at all: image N is shown at film frame frame0 + N - 1.
        f0 = int(sequence["frame0"])
        f1 = f0 + int(sequence["count"]) - 1
        t_start, t_event = float(clip["t_start"]), float(clip["t_event"])
        user = tex.image_user
        user.frame_start = f0
        user.frame_duration = int(sequence["count"])
        user.frame_offset = 0
        user.use_auto_refresh = True
        user.use_cyclic = False

        # One panel per camera, so whichever is live carries it.
        for shot in shots:
            cam = shot["camera"]
            half_w = cam.data.sensor_width / cam.data.lens * VIDEO_DEPTH / 2.0
            half_h = half_w * scene.render.resolution_y / scene.render.resolution_x
            pip_w = 2.0 * half_w * VIDEO_FRAME_FRACTION
            pip_h = pip_w * height / width
            x0 = half_w * HUD_MARGIN_X - pip_w
            y0 = -half_h * HUD_MARGIN_Y + 0.014
            label = f"{clip['kind'].upper()}  {_dt.datetime.fromtimestamp(t0_epoch + t_event):%H:%M:%S}"
            # Less negative z is nearer the camera: the caption must sit in front
            # of its own backing plate, not behind it.
            parts = [
                (_quad(f"Video border {n} ({shot['name']})", pip_w + 0.004, pip_h + 0.004,
                       _flat_material(f"Video rule {n}", THEME["panel_rule"], 0.85)), x0 - 0.002, y0 - 0.002, -0.003),
                (_quad(f"Video {n} ({shot['name']})", pip_w, pip_h, mat), x0, y0, -0.002),
                (_quad(f"Video label bg {n} ({shot['name']})", pip_w + 0.004, 0.020,
                       _flat_material(f"Video label bg {n}", THEME["panel"], 0.8)),
                 x0 - 0.002, y0 + pip_h + 0.002, -0.002),
            ]
            for obj, x, y, z in parts:
                obj.parent = cam
                obj.location = (x, y, -VIDEO_DEPTH + z)
                _link(scene, coll, obj)
            text = _text(f"Video label {n} ({shot['name']})", label, 0.011,
                         _flat_material(f"Video label {n}", THEME["panel_ink"]), align="LEFT",
                         font="narrow", spacing=1.14)
            text.parent = cam
            text.location = (x0 + 0.004, y0 + pip_h + 0.012, -VIDEO_DEPTH)
            _link(scene, coll, text)
            for obj, _x, _y, _z in parts:
                _keyframe_window(obj, f0, f1)
            _keyframe_window(text, f0, f1)
        shown.append({"kind": clip["kind"], "t_start": round(t_start), "t_event": round(t_event),
                      "frames": [f0, f1], "images": int(sequence["count"]),
                      "from": os.path.basename(sequence["dir"])})
    return shown


def _keyframe_window(obj: bpy.types.Object, f0: int, f1: int) -> None:
    """Hidden except between ``f0`` and ``f1``."""
    for frame, hidden in ((max(1, f0 - 1), True), (f0, False), (f1 + 1, True)):
        obj.hide_render = hidden
        obj.hide_viewport = hidden
        obj.keyframe_insert("hide_render", frame=frame)
        obj.keyframe_insert("hide_viewport", frame=frame)
    _set_interpolation(obj, "CONSTANT")


def _card_material(name: str, image_path: str) -> Tuple[bpy.types.Material, bpy.types.Node]:
    """A full-frame card, lit by nothing, with a fade the caller can keyframe."""
    mat = _new_material(name)
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    for node in list(nodes):
        if node.type != "OUTPUT_MATERIAL":
            nodes.remove(node)
    output = next(node for node in nodes if node.type == "OUTPUT_MATERIAL")
    image = bpy.data.images.load(image_path, check_existing=False)
    image.name = PREFIX + os.path.basename(image_path)
    tex = nodes.new("ShaderNodeTexImage")
    tex.image = image
    tex.extension = "EXTEND"
    emit = nodes.new("ShaderNodeEmission")
    emit.inputs["Strength"].default_value = 1.0
    transparent = nodes.new("ShaderNodeBsdfTransparent")
    mix = nodes.new("ShaderNodeMixShader")
    mix.name = "Fade"
    links.new(tex.outputs["Color"], emit.inputs["Color"])
    links.new(transparent.outputs["BSDF"], mix.inputs[1])
    links.new(emit.outputs["Emission"], mix.inputs[2])
    links.new(mix.outputs["Shader"], output.inputs["Surface"])
    if hasattr(mat, "surface_render_method"):
        mat.surface_render_method = "BLENDED"
    else:
        mat.blend_method = "BLEND"
    mat.use_backface_culling = False
    return mat, mix


def build_cards(scene: bpy.types.Scene, coll: bpy.types.Collection, shots: List[Dict[str, Any]],
                data: Dict[str, Any], json_path: str, warp: TimeWarp,
                fade_s: float = 1.2) -> List[str]:
    """The title at the head of the film and the leaderboard at its tail.

    Both are full-frame images that dissolve rather than cut: the title lifts off
    a moving aerial that is already running underneath it, which reads better
    than opening on a hard cut, and the leaderboard settles over the finish.
    """
    base = os.path.dirname(os.path.abspath(json_path))
    cards = data.get("cards") or {}
    fade = max(1, int(round(fade_s * scene.render.fps)))
    shown: List[str] = []

    plan = []
    if cards.get("title") and warp.pre_roll_frames > 0:
        plan.append(("title", cards["title"], shots[0]["camera"],
                     1, warp.pre_roll_frames, "out"))
    if cards.get("results") and warp.post_roll_frames > 0:
        plan.append(("results", cards["results"], shots[-1]["camera"],
                     warp.race_end_frame, warp.total_frames, "in"))

    for name, filename, cam, f0, f1, direction in plan:
        path = filename if os.path.isabs(filename) else os.path.join(base, filename)
        if not os.path.exists(path):
            print(f"replay3d: card {path} not found; skipped")
            continue
        mat, mix = _card_material(f"Card {name}", path)
        depth = VIDEO_DEPTH - 0.08                      # in front of the overlay, which it covers
        half_w = cam.data.sensor_width / cam.data.lens * depth / 2.0
        half_h = half_w * scene.render.resolution_y / scene.render.resolution_x
        quad = _quad(f"Card {name}", 2 * half_w, 2 * half_h, mat)
        quad.parent = cam
        quad.location = (-half_w, -half_h, -depth)
        _link(scene, coll, quad)

        if direction == "out":
            keys = [(f0, 1.0), (max(f0, f1 - fade), 1.0), (f1, 0.0)]
        else:
            keys = [(f0, 0.0), (min(f1, f0 + fade), 1.0), (f1, 1.0)]
        for frame, alpha in keys:
            mix.inputs[0].default_value = alpha
            mix.inputs[0].keyframe_insert("default_value", frame=frame)
        _set_interpolation(mat.node_tree, "BEZIER")
        # Off screen entirely outside its own moment, so it costs nothing.
        _keyframe_window(quad, f0, f1)
        shown.append(f"{name} {f0}-{f1}")
    return shown


def _course_tokens(course_text: str) -> List[Tuple[str, str]]:
    """Split "7s 6s Op 1p" into (mark, rounding) pairs the way the app's course board does."""
    out: List[Tuple[str, str]] = []
    for token in (course_text or "").split():
        rounding = "starboard" if token[-1:] == "s" else "port" if token[-1:] == "p" else ""
        out.append((token[:-1].upper() if rounding else token.upper(), rounding))
    return out


def build_hud(scene: bpy.types.Scene, coll: bpy.types.Collection, shots: List[Dict[str, Any]],
              data: Dict[str, Any]) -> None:
    """The app's instrument panel and course board as a translucent lower-third, on every camera.

    Styled from static/theme_race_document.css: a near-black panel with a thin
    rule, a small narrow uppercase label, the time in Plex Mono, the state in
    amber; and the course as solid red (port) and green (starboard) chips with
    white narrow capitals, on a paper board with the race name in Archivo.
    Text curves are shared between the per-camera copies so the clock handler
    updates one datablock.
    """
    race = data["race"]
    depth = 1.6
    pad = 0.012
    # Solid enough that the small narrow label still reads over sunlit water.
    alpha_panel, alpha_paper = 0.86, 0.88
    # Materials.
    m_panel = _flat_material("HUD panel", THEME["panel"], alpha_panel)
    m_rule = _flat_material("HUD rule", THEME["panel_rule"], 0.9)
    m_label = _flat_material("HUD label", THEME["panel_label"])
    m_time = _flat_material("HUD time", THEME["panel_ink"])
    m_amber = _flat_material("HUD amber", THEME["amber"])
    m_paper = _flat_material("HUD paper", THEME["paper"], alpha_paper)
    m_ink = _flat_material("HUD ink", THEME["ink"])
    m_ink2 = _flat_material("HUD ink2", THEME["ink_2"])
    m_white = _flat_material("HUD white", THEME["white"])
    m_port = _flat_material("HUD port", THEME["port"], 0.95)
    m_stbd = _flat_material("HUD starboard", THEME["starboard"], 0.95)
    m_neutral = _flat_material("HUD neutral chip", THEME["paper_sunk"], 0.95)
    m_credit = _flat_material("HUD credit", THEME["panel_ink"], 0.55)

    # Shared text datablocks (the clock handler writes to these).
    time_curve = _text("HUD time", "00:00:00", 0.040, m_time, align="LEFT", font="mono").data
    state_curve = _text("HUD state", "START +00:00", 0.019, m_amber, align="LEFT", font="narrow", spacing=1.14).data
    for orphan in [o for o in bpy.data.objects if o.data in (time_curve, state_curve) and not o.users_scene]:
        bpy.data.objects.remove(orphan, do_unlink=True)

    # Instrument block geometry (frame units at `depth`).
    label_size, time_size, state_size = 0.013, 0.040, 0.019
    inst_w = max(_text_width("00:00:00", time_size, "mono"), _text_width("START +00:00", state_size, "narrow", 1.14)) + 2 * pad
    inst_h = label_size + time_size + state_size + 4 * pad
    # Course board geometry.
    name_size, chip_size, chip_h = 0.024, 0.015, 0.026
    tokens = _course_tokens(race.get("course_text", ""))
    course_label = f"COURSE {race['course_no']}" if race.get("course_no") not in (None, "") else "COURSE"
    chips_w = sum(_text_width(t, chip_size, "narrow", 1.1) + 2 * pad for t, _ in tokens) + 0.006 * max(0, len(tokens) - 1)
    board_w = max(_text_width(race["name"], name_size, "text"), _text_width(course_label, label_size, "narrow", 1.18) + 0.01 + chips_w) + 2 * pad
    board_h = name_size + chip_h + 3 * pad

    def place(obj: bpy.types.Object, camera: bpy.types.Object, x: float, y: float, z_off: float = 0.0) -> None:
        obj.parent = camera
        obj.location = (x, y, -depth + z_off)
        _link(scene, coll, obj)

    for shot in shots:
        cam = shot["camera"]
        half_w = cam.data.sensor_width / cam.data.lens * depth / 2.0
        half_h = half_w * scene.render.resolution_y / scene.render.resolution_x
        x0 = -half_w * HUD_MARGIN_X
        y0 = -half_h * HUD_MARGIN_Y
        tag = shot["name"]

        # Instrument panel: rule along the top edge, label, time, state.
        place(_quad(f"HUD panel ({tag})", inst_w, inst_h, m_panel), cam, x0, y0, -0.002)
        place(_quad(f"HUD rule ({tag})", inst_w, 0.0012, m_rule), cam, x0, y0 + inst_h - 0.0012, -0.001)
        y = y0 + inst_h - pad - label_size / 2.0
        place(_text(f"HUD label ({tag})", "RACE CLOCK", label_size, m_label, align="LEFT", font="narrow", spacing=1.18),
              cam, x0 + pad, y)
        y -= label_size / 2.0 + pad + time_size / 2.0
        time_obj = bpy.data.objects.new(f"HUD time ({tag})", time_curve)
        place(time_obj, cam, x0 + pad, y)
        y -= time_size / 2.0 + pad + state_size / 2.0
        state_obj = bpy.data.objects.new(f"HUD state ({tag})", state_curve)
        place(state_obj, cam, x0 + pad, y)

        # Course board to the right, same baseline: race name, then "COURSE N" and the chips.
        bx = x0 + inst_w + 0.01
        place(_quad(f"HUD board ({tag})", board_w, board_h, m_paper), cam, bx, y0, -0.002)
        y = y0 + board_h - pad - name_size / 2.0
        place(_text(f"HUD race ({tag})", race["name"], name_size, m_ink, align="LEFT", font="text"), cam, bx + pad, y)
        y = y0 + pad + chip_h / 2.0
        cx = bx + pad
        place(_text(f"HUD course ({tag})", course_label, label_size, m_ink2, align="LEFT", font="narrow", spacing=1.18),
              cam, cx, y)
        cx += _text_width(course_label, label_size, "narrow", 1.18) + 0.01
        for n, (mark, rounding) in enumerate(tokens):
            w = _text_width(mark, chip_size, "narrow", 1.1) + 2 * pad
            chip_mat = m_port if rounding == "port" else m_stbd if rounding == "starboard" else m_neutral
            text_mat = m_white if rounding else m_ink
            place(_quad(f"HUD chip {n} ({tag})", w, chip_h, chip_mat), cam, cx, y - chip_h / 2.0, -0.001)
            place(_text(f"HUD chip text {n} ({tag})", mark, chip_size, text_mat, align="CENTER", font="narrow", spacing=1.1),
                  cam, cx + w / 2.0, y)
            cx += w + 0.006

        # Imagery attribution, bottom right under the picture-in-picture. Mapbox
        # and Copernicus both require it, and the film is the thing that gets shared.
        credit = ((data.get("terrain") or {}).get("imagery_info") or {}).get("credit")
        if credit:
            obj = _text(f"HUD credit ({tag})", credit, 0.010, m_credit, align="RIGHT",
                        font="narrow", spacing=1.1)
            place(obj, cam, half_w * HUD_MARGIN_X, -half_h * HUD_MARGIN_Y + 0.004)

    scene["replay_t0_epoch"] = float(data["time"]["t0_epoch"])
    scene["replay_first_start_rel"] = float(data["time"]["first_start_rel"])
    scene["replay_clock_curve"] = time_curve.name
    scene["replay_state_curve"] = state_curve.name
    _install_clock_handler()


_WARP_CACHE: Dict[str, TimeWarp] = {}


def _scene_warp(scene: bpy.types.Scene) -> Optional[TimeWarp]:
    """The film's clock, rebuilt once from what the scene carries."""
    spec = scene.get("replay_warp")
    if not spec:
        return None
    warp = _WARP_CACHE.get(spec)
    if warp is None:
        warp = TimeWarp.from_dict(json.loads(spec))
        _WARP_CACHE[spec] = warp
    return warp


def _replay_clock(scene: bpy.types.Scene) -> None:
    name = scene.get("replay_clock_curve")
    if not name:
        return
    curve = bpy.data.curves.get(name)
    if curve is None:
        return
    warp = _scene_warp(scene)
    if warp is not None:
        t_rel = warp.time_at(scene.frame_current)
    else:
        speed = float(scene.get("replay_speed", DEFAULT_SPEED))
        t_rel = (scene.frame_current - 1) / scene.render.fps * speed
    since_start = t_rel - float(scene.get("replay_first_start_rel", 0.0))
    wall = _dt.datetime.fromtimestamp(float(scene.get("replay_t0_epoch", 0.0)) + t_rel)
    sign = "-" if since_start < 0 else "+"
    m, s = divmod(int(abs(since_start)), 60)
    curve.body = f"{wall:%H:%M:%S}"
    state = bpy.data.curves.get(scene.get("replay_state_curve") or "")
    if state is not None:
        state.body = f"START {sign}{m:02d}:{s:02d}" if since_start >= 0 else f"START IN {m:02d}:{s:02d}"


def _install_clock_handler() -> None:
    handlers = bpy.app.handlers.frame_change_pre
    for h in list(handlers):
        if getattr(h, "__name__", "") == "_replay_clock":
            handlers.remove(h)
    handlers.append(_replay_clock)


# ---------------------------------------------------------------------------
# Entry point.

def _set_view_transform(scene: bpy.types.Scene, view: str, look: str, exposure: float) -> str:
    """Set the view transform, its look and the exposure, tolerating older configs.

    The look is named differently depending on the transform ("AgX - Punchy"
    against plain "Punchy"), and the enum does not enumerate in background
    Blender, so the only way to find out is to try setting it.
    """
    vs = scene.view_settings
    try:
        vs.view_transform = view
    except TypeError:
        print(f"replay3d: view transform {view!r} unavailable; left at {vs.view_transform}")
        return vs.view_transform
    for candidate in (f"{view} - {look}", look, "None"):
        try:
            vs.look = candidate
            break
        except TypeError:
            continue
    try:
        vs.exposure = exposure
    except AttributeError:
        pass
    return f"{vs.view_transform} / {vs.look} / exposure {vs.exposure:+.2f}"


def _build_glare(scene: bpy.types.Scene) -> Optional[str]:
    """A little bloom around the sun's glitter track and the brightest sails.

    Depth of field can do almost nothing at this geometry -- every camera sits
    hundreds of metres from its subject, where even a wide aperture leaves the
    background sharp -- so this is what carries the lens instead. Real glass
    scatters a little light around a highlight; a rasteriser does not, and that
    hard-edged brightness is a large part of why a render reads as a render.

    Blender 5 replaced the scene's compositor tree with a node group wired
    between a Group Input and a Group Output; there is no Composite node any
    more, and every Glare setting is an input socket rather than a property.
    """
    if os.environ.get("REPLAY_GLARE", "1") == "0":
        return None
    if not hasattr(scene, "compositing_node_group"):
        return None
    try:
        group = bpy.data.node_groups.new(PREFIX + "Glare", "CompositorNodeTree")
        group.interface.new_socket("Image", in_out="OUTPUT", socket_type="NodeSocketColor")
        # Blender 5 dropped the Composite node, so the tail of the chain is a
        # Group Output; the head is still a Render Layers node.
        source = group.nodes.new("CompositorNodeRLayers")
        source.scene = scene
        sink = group.nodes.new("NodeGroupOutput")
        glare = group.nodes.new("CompositorNodeGlare")
        source.location, glare.location, sink.location = (-320, 0), (-60, 0), (240, 0)
        wanted = {"Type": "Bloom", "Quality": "Medium", "Threshold": 0.85,
                  "Strength": float(os.environ.get("REPLAY_GLARE_STRENGTH", "0.16")),
                  "Size": 0.55, "Smoothness": 0.4}
        for key, value in wanted.items():
            socket = glare.inputs.get(key)
            if socket is None:
                continue
            try:
                socket.default_value = value
            except (TypeError, ValueError):
                pass
        group.links.new(source.outputs["Image"], glare.inputs["Image"])
        group.links.new(glare.outputs["Image"], sink.inputs[0])
        scene.compositing_node_group = group
        scene.render.use_compositing = True
    except Exception as exc:                      # a look, never a reason to lose the render
        print(f"replay3d: glare not built ({type(exc).__name__}: {exc})")
        return None
    return f"{wanted['Type']} strength {wanted['Strength']}"


def build(json_path: str, *, overlays_3d: bool = False,
          speed: float = DEFAULT_SPEED, shots: str = "film", follow: Optional[str] = None,
          boat_model: Optional[str] = None, boat_forward: str = "+Y", boat_waterline: float = 0.3,
          raytrace: bool = False, video_speed: float = 1.0,
          no_video: bool = True) -> Dict[str, Any]:
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if data.get("format") != "pwllheli-replay3d/1":
        raise ValueError(f"not a replay export: {json_path}")
    if shots not in ("film", "overview", "follow"):
        raise ValueError("shots must be 'film', 'overview' or 'follow'")

    race = data["race"]
    scene_name = f"Replay {race['id']} {race['name']}"[:60]
    _remove_scene(scene_name)
    scene = bpy.data.scenes.new(scene_name)
    scene.unit_settings.system = "METRIC"
    scene.render.fps = FPS
    scene.render.resolution_x = 1920
    scene.render.resolution_y = 1080
    scene.render.engine = "BLENDER_EEVEE"
    # Flat-lit sea, hard-edged hulls and text: 8 samples are indistinguishable
    # from 16 at 2x magnification (checked 2026-09-09) and a 1080p frame drops
    # from 0.80 s to about 0.6 s. REPLAY_SAMPLES and REPLAY_RES_PCT override
    # these for render-time experiments.
    scene.eevee.taa_render_samples = int(os.environ.get("REPLAY_SAMPLES", "8"))
    scene.render.resolution_percentage = int(os.environ.get("REPLAY_RES_PCT", "100"))
    # Standard clips every highlight to flat white, which is the clearest single
    # tell of a realtime render. AgX rolls them off the way film does. On its own
    # AgX also pulls the saturation out and the bay goes grey, which is what it
    # did on the first attempt; the Punchy look and a little exposure put the
    # contrast and colour back, and that combination is the whole point.
    _set_view_transform(scene,
                        os.environ.get("REPLAY_VIEW", "AgX"),
                        os.environ.get("REPLAY_LOOK", "Punchy"),
                        float(os.environ.get("REPLAY_EXPOSURE", "0.5")))
    # A camera, not a viewport. Motion blur is the cheap half of that and it is
    # the one the eye notices on a pan; see SHUTTER for why it is under 180 degrees.
    scene.render.use_motion_blur = os.environ.get("REPLAY_MBLUR", "1") != "0"
    scene.render.motion_blur_shutter = float(os.environ.get("REPLAY_SHUTTER", SHUTTER))
    if hasattr(scene.eevee, "motion_blur_steps"):
        scene.eevee.motion_blur_steps = 1          # more steps is more accurate and much slower
    _build_glare(scene)
    if raytrace:
        # Sky reflected in the water. Off by default: it crashed the Intel OpenGL
        # driver on the hut laptop while another Blender was also rendering.
        try:
            scene.eevee.use_raytracing = True
        except AttributeError:
            pass
    # The film's clock. prepare_video_frames.py plans it and records it in the
    # export, because it cut the hut-camera frames against it; without that
    # block the replay is a flat compression, as it used to be.
    if data.get("film"):
        warp = TimeWarp.from_dict(data["film"])
    else:
        warp = TimeWarp(float(data["time"]["duration_s"]), [], speed=speed,
                        pre_roll_s=0.0, post_roll_s=0.0)
    scene["replay_speed"] = float(warp.speed)
    scene["replay_warp"] = json.dumps(warp.to_dict())
    scene.frame_start = 1
    scene.frame_end = warp.total_frames
    scene.frame_current = 1
    frame_of = warp.frame_of

    extent = max(500.0, float(data.get("extent_m") or 500.0))
    fonts_found = load_fonts(json_path)
    label_mat = _material("Labels", (1.0, 1.0, 1.0), emission=1.2)
    sail_mat = _cloth(_material("Sailcloth", (0.94, 0.94, 0.90), roughness=0.8))

    c_env = _collection(scene, "Environment")
    c_marks = _collection(scene, "Marks and course")
    c_boats = _collection(scene, "Boats")
    c_cam = _collection(scene, "Cameras and HUD")

    haze, sun_info = build_sky_and_sun(scene, c_env, data)
    wind = data.get("wind") or {}
    build_sea(scene, c_env, extent, haze, wind.get("twd_deg"), wind.get("tws_kn"))
    if data.get("terrain"):
        build_terrain(scene, c_env, data["terrain"], extent, haze, json_path)

    template: Optional[List[bpy.types.Object]] = None
    if boat_model:
        c_tmpl = _collection(scene, "Boat model template")
        c_tmpl.hide_render = True
        c_tmpl.hide_viewport = True
        template = prepare_boat_model(boat_model, boat_forward, c_tmpl, boat_waterline)

    # The "eye" sits wherever the live camera is; labels face it and scale by
    # their distance from it. It exists before the boats so their labels can
    # reference it; build_cameras wires it to the cameras afterwards.
    eye = _link(scene, c_cam, _empty("Camera eye"))
    wind_series = Wind(data.get("wind"))
    twd = data.get("wind", {}).get("twd_deg")
    tws = data.get("wind", {}).get("tws_kn")
    boats: Dict[str, bpy.types.Object] = {}
    yachts: Dict[str, Dict[str, bpy.types.Object]] = {}
    for i, b in enumerate(data["boats"]):
        colour = to_linear(tuple(b["colour"])) if b.get("colour") else PALETTE[i % len(PALETTE)]
        parts = build_yacht(scene, c_boats, b["name"] or f"Boat {i + 1}", colour,
                            sail_mat, label_mat, eye, template, label_lift=i * 7.0 * BOAT_SCALE,
                            with_label=overlays_3d)
        build_trail(scene, c_boats, b, colour, frame_of, float(data["time"]["step_s"]))
        root = parts["root"]
        if template:
            _unhide_copies([o for o in scene.objects if o.parent is root and o.name.split(" ")[0] in
                            {t.name.split(" ")[0] for t in template}])
            for o in scene.objects:
                if o.name.endswith(f" {b['name']}") and o.name.split(" ")[0] in {t.name.split(" ")[0] for t in template}:
                    o.hide_render = False
                    o.hide_viewport = False
        boats[b["name"]] = root
        yachts[b["name"]] = parts
    for b in data["boats"]:
        animate_boat(yachts[b["name"]], b["samples"], frame_of, wind_series)

    shot_list = build_cameras(scene, c_cam, data, boats, extent, frame_of, shots, follow, eye)

    # The sea plane reaches 2 x HAZE_FAR x extent; anything beyond would float in the sky.
    marks_drawn = build_marks(scene, c_marks, data, label_mat, eye, reach=extent * HAZE_FAR * 0.9,
                              with_labels=overlays_3d)
    build_course(scene, c_marks, data)
    # The overlay -- names, mark numbers, clock, course board, cards -- is drawn
    # in pixels by compose_film.py unless this is a one-off still. Geometry
    # parented to the camera sits 1.6 m from a lens focused hundreds of metres
    # away, so in the 3D it came out blurred by the depth of field and smeared
    # again by the motion blur; see overlay.py.
    if overlays_3d:
        build_hud(scene, c_cam, shot_list, data)
    # The hut-camera panel can be left out and laid over the rendered film
    # instead: a new still for it every frame is the single most expensive thing
    # in the scene, and compositing it afterwards costs seconds, not hours.
    videos = ([] if no_video else
              build_videos(scene, c_cam, shot_list, data, json_path, speed, frame_of, video_speed))
    cards = build_cards(scene, c_cam, shot_list, data, json_path, warp) if overlays_3d else []

    # Output settings for a video render; nothing is rendered here.
    out_dir = os.path.join(os.path.dirname(os.path.abspath(json_path)), "renders")
    settings = scene.render.image_settings
    if hasattr(settings, "media_type"):      # Blender 5.0+: video is a media type, not a file format
        settings.media_type = "VIDEO"
    settings.file_format = "FFMPEG"
    scene.render.ffmpeg.format = "MPEG4"
    scene.render.ffmpeg.codec = "H264"
    scene.render.ffmpeg.constant_rate_factor = "HIGH"
    scene.render.filepath = os.path.join(out_dir, f"race_{race['id']}_")

    # Show the new scene in every open window.
    for window in bpy.context.window_manager.windows:
        window.scene = scene
    return {
        "scene": scene.name,
        "frames": scene.frame_end,
        "seconds_of_video": round(scene.frame_end / FPS, 1),
        "boats": list(boats),
        "marks_drawn": marks_drawn,
        "marks_in_export": len(data["marks"]),
        "terrain": bool(data.get("terrain")),
        "imagery": bool((data.get("terrain") or {}).get("imagery")),
        "sun": sun_info,
        "wind": wind_series.range_text(),
        "fonts": {k: (os.path.basename(v) if v else None) for k, v in fonts_found.items()},
        "videos": videos,
        "cards": cards,
        "film": warp.summary(),
        "shots": [{"name": s["name"], "frame": s["frame"], "t_rel_s": round(s["t0"])} for s in shot_list],
        "overlays_3d": overlays_3d,
        "boat_model": os.path.basename(boat_model) if boat_model else None,
        "render_filepath": scene.render.filepath,
    }


def scene_digest(json_path: str) -> str:
    """A fingerprint of the scene file, for spotting work made from an older one."""
    import hashlib

    h = hashlib.sha256()
    with open(json_path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def write_overlay_track(result: Dict[str, Any], json_path: str, out_path: str) -> Dict[str, Any]:
    """Where every boat and mark lands on screen, frame by frame, as fractions.

    Only Blender knows where the camera was pointing, so the 2D overlay cannot
    work this out for itself. One pass over the film writes it once; the
    compositor then draws names and numbers straight into the pixel grid.
    """
    from bpy_extras.object_utils import world_to_camera_view

    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    scene = bpy.data.scenes[result["scene"]]
    extent = max(500.0, float(data.get("extent_m") or 500.0))
    reach = extent * HAZE_FAR * 0.9

    boat_names = [b["name"] or f"Boat {i + 1}" for i, b in enumerate(data["boats"])]
    roots = [scene.objects.get(f"Boat {n}") for n in boat_names]
    # Two points per boat, not one. The dot and the leader line start at the
    # hull -- anchoring those above the masthead would leave a marker floating
    # in the sky -- but the name plate has to clear the rig, and how tall a rig
    # is on screen depends entirely on how close the camera is. A fixed offset
    # above the hull put the plates straight through the masts whenever the
    # camera came in, which is exactly when the boats are worth seeing.
    #
    # Taken from each boat's own Rig empty rather than computed here: the rig
    # carries the boat's heel, so the masthead leans with it, and asking the
    # object where its own masthead is cannot drift from how the boat is built.
    # Working it out by hand got it wrong by a factor of four the first time --
    # the rig is drawn at BOAT_SCALE, so MAST_M is 60 world metres, not 15.
    lift = 0.0
    masthead_local = Vector((0.0, 0.0, MAST_M * BOAT_SCALE * 1.04))
    rigs = [bpy.data.objects.get(f"Rig {n}") for n in boat_names]

    marks = [m for m in data["marks"]
             if m.get("in_course") or math.hypot(m["xy"][0], m["xy"][1]) <= reach]
    mark_names = [m["code"] for m in marks]
    mark_points = [Vector((m["xy"][0], m["xy"][1], BUOY_HEIGHT_M + 12.0)) for m in marks]

    frames: Dict[str, Dict[str, List[List[float]]]] = {}
    started = time.time()
    total = scene.frame_end - scene.frame_start + 1
    for frame in range(scene.frame_start, scene.frame_end + 1):
        scene.frame_set(frame)
        cam = scene.camera
        if cam is None:
            continue
        entry: Dict[str, List[List[float]]] = {}
        seen = []
        for i, root in enumerate(roots):
            if root is None or root.hide_render:
                continue
            world = root.matrix_world.translation + Vector((0.0, 0.0, lift))
            co = world_to_camera_view(scene, cam, world)
            if co.z <= 0.0 or not (-0.05 <= co.x <= 1.05) or not (-0.05 <= co.y <= 1.05):
                continue
            rig = rigs[i]
            if rig is None:
                seen.append([i, round(co.x, 4), round(co.y, 4), round(co.z, 1)])
                continue
            top = world_to_camera_view(scene, cam, rig.matrix_world @ masthead_local)
            seen.append([i, round(co.x, 4), round(co.y, 4), round(co.z, 1), round(top.y, 4)])
        if seen:
            entry["boats"] = seen
        seen = []
        for i, point in enumerate(mark_points):
            co = world_to_camera_view(scene, cam, point)
            if co.z <= 0.0 or not (0.0 <= co.x <= 1.0) or not (0.0 <= co.y <= 1.0):
                continue
            seen.append([i, round(co.x, 4), round(co.y, 4), round(co.z, 1)])
        if seen:
            entry["marks"] = seen
        if entry:
            frames[str(frame)] = entry
        if (frame - scene.frame_start) % 2000 == 1999:
            done = (frame - scene.frame_start + 1) / total
            print(f"  overlay track {frame}/{scene.frame_end} ({done:5.1%})", flush=True)

    # Stamped with the scene it describes. A track is only valid for one scene:
    # re-export a race after fixing its tracks and every screen position in here
    # is of the old ones, so the film draws corrected boats with the names still
    # following where they used to be. That is exactly what happened after the
    # Crackajack repair -- the boats stopped jumping and the labels did not.
    track = {"fps": scene.render.fps, "size": [scene.render.resolution_x, scene.render.resolution_y],
             "scene_sha": scene_digest(json_path),
             "boats": boat_names, "marks": mark_names, "frames": frames}
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(track, f, separators=(",", ":"))
    return {"out": out_path, "frames": len(frames),
            "seconds": round(time.time() - started, 1)}


def render_still(scene_name: str, frame: int, path: str) -> str:
    """Render one frame of a replay scene to a PNG (for checking the look)."""
    scene = bpy.data.scenes[scene_name]
    scene.frame_set(frame)
    settings = scene.render.image_settings
    has_media = hasattr(settings, "media_type")
    prev = (settings.media_type if has_media else None, settings.file_format, scene.render.filepath)
    if has_media:
        settings.media_type = "IMAGE"
    settings.file_format = "PNG"
    scene.render.filepath = path
    try:
        bpy.ops.render.render(write_still=True, scene=scene_name)
    finally:
        if has_media:
            settings.media_type = prev[0]
        settings.file_format = prev[1]
        scene.render.filepath = prev[2]
    return path


def render_film(scene_name: str, out_path: Optional[str] = None) -> str:
    """Render the whole animation of a replay scene to the MP4 it was set up for.

    Meant for a background Blender (``blender -b --python build_scene.py -- race.json --render``)
    so the interactive session, and the MCP bridge in it, stay responsive. A frame
    takes about a second in EEVEE at 1080p, so an 80-minute race at 30x is an hour.
    """
    scene = bpy.data.scenes[scene_name]
    if out_path:
        # Absolute: Blender resolves a relative render path against its own working directory.
        scene.render.filepath = os.path.abspath(out_path)
    os.makedirs(os.path.dirname(os.path.abspath(scene.render.filepath)) or ".", exist_ok=True)

    # Progress goes to a log beside the output. The Store build of Blender can
    # only be launched through blender-launcher.exe, which gives no console, so
    # this file is the only way to watch a long render from outside.
    import time as _time
    log_path = scene.render.filepath.rstrip("_-") + ".progress.log"
    started = _time.time()

    def _log(line: str) -> None:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"{_time.strftime('%H:%M:%S')} {line}\n")

    # Count frames actually written, not the distance travelled: with a frame
    # step the current frame jumps by five and a milestone of "every 50" is
    # never reached, which left a stepped job looking stalled when it was fine.
    written = {"n": 0}
    step = max(1, int(scene.frame_step))
    to_render = len(range(scene.frame_start, scene.frame_end + 1, step))

    def _on_frame(sc: bpy.types.Scene) -> None:
        written["n"] += 1
        done = written["n"]
        elapsed = _time.time() - started
        eta = elapsed / done * (to_render - done) if done else 0.0
        if done == 1 or done % 25 == 0 or done == to_render:
            _log(f"frame {sc.frame_current}/{sc.frame_end}  {done / to_render:6.1%}  "
                 f"elapsed {elapsed / 60:5.1f} min  eta {eta / 60:5.1f} min")

    def _on_complete(sc: bpy.types.Scene) -> None:
        _log(f"complete in {(_time.time() - started) / 60:.1f} min -> {sc.render.filepath}")

    _log(f"start {scene_name}: frames {scene.frame_start}-{scene.frame_end}"
         + (f" every {step}" if step > 1 else "") + f" ({to_render} to render) -> {scene.render.filepath}")
    bpy.app.handlers.render_write.append(_on_frame)
    bpy.app.handlers.render_complete.append(_on_complete)
    try:
        bpy.ops.render.render(animation=True, scene=scene_name)
    except Exception as ex:  # keep the reason in the log, the console may not exist
        _log(f"FAILED: {ex!r}")
        raise
    finally:
        bpy.app.handlers.render_write.remove(_on_frame)
        bpy.app.handlers.render_complete.remove(_on_complete)
    return scene.render.frame_path(frame=scene.frame_end)


def render_reel(result: Dict[str, Any], seconds_per_shot: float, out_path: Optional[str] = None,
                scale_pct: int = 50) -> str:
    """A quick preview: the first few seconds of every cut, back to back, at reduced size.

    Each cut's frames are rendered straight from the replay scene as PNGs, then
    stitched into one MP4 through an image-sequence strip in a scratch scene.
    (Sequencer *scene* strips looked simpler but did not advance the source
    scene's frame in a background render, giving a film of frame 1.) A 16-cut
    race at three seconds a cut is about three minutes instead of an hour.
    """
    import shutil

    replay = bpy.data.scenes[result["scene"]]
    fps = replay.render.fps
    per_shot = max(1, int(round(seconds_per_shot * fps)))
    shots = sorted(result["shots"], key=lambda s: s["frame"])
    base = out_path or (replay.render.filepath.rstrip("_-") + "_reel_")
    out_dir = os.path.dirname(os.path.abspath(base)) or "."
    frames_dir = os.path.join(out_dir, "reel_frames")
    shutil.rmtree(frames_dir, ignore_errors=True)
    os.makedirs(frames_dir, exist_ok=True)

    # Render the segments from the replay scene itself, then restore its settings.
    saved = (replay.frame_start, replay.frame_end, replay.render.filepath, replay.render.resolution_percentage,
             getattr(replay.render.image_settings, "media_type", None), replay.render.image_settings.file_format)
    try:
        replay.render.resolution_percentage = scale_pct
        if hasattr(replay.render.image_settings, "media_type"):
            replay.render.image_settings.media_type = "IMAGE"
        replay.render.image_settings.file_format = "PNG"
        files: List[str] = []
        for i, shot in enumerate(shots):
            start = shot["frame"]
            end_limit = shots[i + 1]["frame"] if i + 1 < len(shots) else replay.frame_end
            length = max(1, min(per_shot, end_limit - start))
            replay.frame_start = start
            replay.frame_end = start + length - 1
            replay.render.filepath = os.path.join(frames_dir, f"seg{i:02d}_")
            bpy.ops.render.render(animation=True, scene=replay.name)
            files.extend(replay.render.frame_path(frame=f) for f in range(start, start + length))
    finally:
        (replay.frame_start, replay.frame_end, replay.render.filepath, replay.render.resolution_percentage,
         media, fmt) = saved
        if media is not None:
            replay.render.image_settings.media_type = media
        replay.render.image_settings.file_format = fmt
    files = [f for f in files if os.path.exists(f)]
    if not files:
        raise RuntimeError("reel: no frames were rendered")
    # One tidy, gap-free sequence for the image strip.
    seq_files = []
    for n, f in enumerate(files, start=1):
        dest = os.path.join(frames_dir, f"reel_{n:05d}.png")
        os.replace(f, dest)
        seq_files.append(dest)

    reel_name = (replay.name + " reel")[:63]
    old = bpy.data.scenes.get(reel_name)
    if old is not None:
        bpy.data.scenes.remove(old)
    reel = bpy.data.scenes.new(reel_name)
    reel.render.fps = fps
    reel.render.resolution_x = int(replay.render.resolution_x * scale_pct / 100)
    reel.render.resolution_y = int(replay.render.resolution_y * scale_pct / 100)
    reel.render.resolution_percentage = 100
    reel.view_settings.view_transform = "Standard"      # the PNGs are already display-referred
    editor = reel.sequence_editor_create()
    # Renamed in Blender 4.4; an empty collection is falsy, so test by name not truth.
    strips_api = editor.strips if hasattr(editor, "strips") else editor.sequences
    strip = strips_api.new_image("reel", seq_files[0], 1, 1)
    for f in seq_files[1:]:
        strip.elements.append(os.path.basename(f))
    reel.frame_start = 1
    reel.frame_end = len(seq_files)

    settings = reel.render.image_settings
    if hasattr(settings, "media_type"):
        settings.media_type = "VIDEO"
    settings.file_format = "FFMPEG"
    reel.render.ffmpeg.format = "MPEG4"
    reel.render.ffmpeg.codec = "H264"
    reel.render.ffmpeg.constant_rate_factor = "MEDIUM"
    reel.render.filepath = base
    bpy.ops.render.render(animation=True, scene=reel.name)
    shutil.rmtree(frames_dir, ignore_errors=True)
    return reel.render.frame_path(frame=reel.frame_end)


def _parse_cli(argv: List[str]) -> Tuple[str, Dict[str, Any], Dict[str, Any]]:
    opts: Dict[str, Any] = {"speed": DEFAULT_SPEED, "shots": "film", "follow": None,
                            "boat_model": None, "boat_forward": "+Y", "boat_waterline": 0.3, "raytrace": False,
                            "video_speed": 1.0, "no_video": True, "overlays_3d": False}
    extra: Dict[str, Any] = {"render": False, "out": None, "stills": [], "stills_dir": None, "reel": 0.0,
                             "frames": None, "step": 1, "overlay_track": None}
    if not argv:
        raise SystemExit("usage: blender [-b] --python build_scene.py -- race.json [--speed N] "
                         "[--shots film|overview|follow] [--follow BOAT] [--boat-model FILE] "
                         "[--boat-forward +Y] [--boat-waterline 0.3] [--video-speed 1.0] "
                         "[--with-video] "
                         "[--raytrace] [--overlays-3d] [--overlay-track PATH] [--render] [--out PATH] "
                         "[--stills F1,F2,... --stills-dir DIR] [--reel SECONDS_PER_SHOT] [--frames A-B]")
    path = argv[0]
    i = 1
    while i < len(argv):
        arg = argv[i]
        nxt = argv[i + 1] if i + 1 < len(argv) else None
        if arg == "--speed" and nxt:
            opts["speed"] = float(nxt)
            i += 2
        elif arg == "--shots" and nxt:
            opts["shots"] = nxt
            i += 2
        elif arg == "--follow" and nxt:
            opts["follow"] = nxt
            i += 2
        elif arg == "--boat-model" and nxt:
            opts["boat_model"] = nxt
            i += 2
        elif arg == "--boat-forward" and nxt:
            opts["boat_forward"] = nxt
            i += 2
        elif arg == "--boat-waterline" and nxt:
            opts["boat_waterline"] = float(nxt)
            i += 2
        elif arg == "--out" and nxt:
            extra["out"] = nxt
            i += 2
        elif arg == "--stills" and nxt:
            extra["stills"] = [int(v) for v in nxt.split(",") if v.strip()]
            i += 2
        elif arg == "--stills-dir" and nxt:
            extra["stills_dir"] = nxt
            i += 2
        elif arg == "--reel" and nxt:
            extra["reel"] = float(nxt)
            i += 2
        elif arg == "--frames" and nxt:
            a, b = nxt.split("-", 1)
            extra["frames"] = (int(a), int(b))
            i += 2
        elif arg == "--video-speed" and nxt:
            opts["video_speed"] = float(nxt)
            i += 2
        elif arg == "--no-video":
            opts["no_video"] = True                 # the default; kept so old commands still work
            i += 1
        elif arg == "--with-video":
            opts["no_video"] = False
            i += 1
        elif arg == "--overlays-3d":
            opts["overlays_3d"] = True
            i += 1
        elif arg == "--overlay-track" and nxt:
            extra["overlay_track"] = nxt
            i += 2
        elif arg == "--step" and nxt:
            extra["step"] = max(1, int(nxt))
            i += 2
        elif arg == "--raytrace":
            opts["raytrace"] = True
            i += 1
        elif arg == "--render":
            extra["render"] = True
            i += 1
        else:
            i += 1
    return path, opts, extra


def _render_stills(result: Dict[str, Any], frames: List[int], out_dir: Optional[str]) -> List[str]:
    """One PNG per frame, from whichever camera the shot list has live at that frame."""
    scene = bpy.data.scenes[result["scene"]]
    out_dir = out_dir or os.path.dirname(scene.render.filepath)
    os.makedirs(out_dir, exist_ok=True)
    shots = sorted(result["shots"], key=lambda s: s["frame"])
    paths = []
    for frame in frames:
        live = [s for s in shots if s["frame"] <= frame]
        shot = live[-1] if live else shots[0]
        cam = scene.objects.get(f"Camera {shot['name']}")
        if cam is not None:
            scene.camera = cam
        path = os.path.join(out_dir, f"race_{scene.name.split()[1]}_f{frame:05d}_{shot['name'].replace(' ', '_')}.png")
        paths.append(render_still(scene.name, frame, path))
    return paths


if __name__ == "__main__":
    _argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    _path, _opts, _extra = _parse_cli(_argv)
    _result = build(_path, **_opts)
    print(json.dumps(_result, indent=2))
    if _extra["stills"]:
        for _p in _render_stills(_result, _extra["stills"], _extra["stills_dir"]):
            print("still:", _p)
    if _extra["reel"] > 0:
        print("reel:", render_reel(_result, _extra["reel"], _extra["out"]))
    if _extra["frames"]:
        # A worker in a parallel render: only this slice of the film (see render_parallel.py).
        _sc = bpy.data.scenes[_result["scene"]]
        _sc.frame_start = max(_sc.frame_start, _extra["frames"][0])
        _sc.frame_end = min(_sc.frame_end, _extra["frames"][1])
    if _extra["overlay_track"]:
        print("overlay track:", json.dumps(
            write_overlay_track(_result, _path, _extra["overlay_track"])))
    if _extra["step"] > 1:
        # Render every Nth frame. Where the film runs at real time the boats
        # crawl and the fixes behind them are half a minute apart, so the frames
        # between are interpolation of data that was never measured.
        bpy.data.scenes[_result["scene"]].frame_step = _extra["step"]
    if _extra["render"]:
        print("rendered:", render_film(_result["scene"], _extra["out"]))
