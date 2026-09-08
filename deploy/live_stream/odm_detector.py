"""Config-driven ODM (Outer Distance Mark) start-line detector.

Finds the orange start-line buoy in a still frame from the Pwllheli hut camera
and reports its position as **normalised coordinates** (fractions of frame
width/height) so the result is resolution-independent and can drive any
renderer — the live relay overlay and, later, the start/finish recording copies.

Design (see the feasibility notes): classic colour segmentation, not ML —
  HSV orange threshold -> sea-band ROI (minus the pole column) -> connected-blob
  labelling (hand-rolled BFS, no scipy) -> score by saturation/size/shape/fill
  times a Gaussian swing-zone prior -> pick the best, gate on confidence.

Dependency-light: PIL + numpy only. All tunables live in config.json.

Public API:
    cfg = load_config("config.json")
    det = detect(pil_image, cfg)            # -> Detection (normalised)
    img = annotate(pil_image, det, cfg)     # -> annotated PIL image
    sm = LineSmoother(cfg)                   # for live sequences
    smoothed = sm.update(det)               # EMA + stale handling
"""
from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
from PIL import Image, ImageDraw, ImageFont

# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
DEFAULT_CONFIG = {
    "camera_name": "default",
    "color": {"hue_min": 8, "hue_max": 46, "sat_min": 70, "val_min": 55},
    "roi": {"top": 0.42, "bottom": 0.82, "left": 0.03, "right": 0.985},
    "pole": {"x": 0.573, "y": 0.97, "halfwidth": 0.017},
    "swing_zone": {"expect_cx": 0.58, "sigma_cx": 0.15, "expect_cy": 0.565, "sigma_cy": 0.06},
    "shape": {"min_area_px": 90, "max_area_px": 4000, "ideal_area_lo": 150,
              "ideal_area_hi": 2000, "aspect_min": 0.6, "aspect_max": 3.6},
    "conf_min": 0.30,
    # Line rendering (see render_line_overlay). line_color is the default; the
    # start-video renderer will pass red before the start and green after it.
    "line_color": [0, 235, 0],
    "line_opacity": 0.3,
    "line_width": 0.010,
    "smoother": {"ema_alpha": 0.5, "max_consecutive_misses": 6},
}


def _deep_merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in (over or {}).items():
        if k.startswith("_"):
            continue  # skip _comment keys
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(path: Optional[str] = None) -> dict:
    """Load config.json (if given/exists) merged over DEFAULT_CONFIG."""
    if path and os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return _deep_merge(DEFAULT_CONFIG, json.load(f))
    return _deep_merge(DEFAULT_CONFIG, {})


# --------------------------------------------------------------------------- #
# Detection result
# --------------------------------------------------------------------------- #
@dataclass
class Detection:
    found: bool = False
    conf: float = 0.0
    fx: Optional[float] = None       # buoy x as fraction of width
    fy: Optional[float] = None       # buoy y as fraction of height
    bbox_frac: Optional[tuple] = None  # (x0,y0,x1,y1) as fractions
    details: dict = field(default_factory=dict)

    def pixel(self, w: int, h: int):
        if self.fx is None:
            return None
        return (int(round(self.fx * w)), int(round(self.fy * h)))


# --------------------------------------------------------------------------- #
# Core detection
# --------------------------------------------------------------------------- #
def _hsv(img: Image.Image):
    hsv = np.asarray(img.convert("HSV"))
    return hsv[..., 0], hsv[..., 1], hsv[..., 2]


def _roi_mask(h, w, cfg):
    r, p = cfg["roi"], cfg["pole"]
    m = np.zeros((h, w), dtype=bool)
    m[int(r["top"] * h):int(r["bottom"] * h), int(r["left"] * w):int(r["right"] * w)] = True
    px, half = p["x"] * w, p["halfwidth"] * w
    m[:, int(px - half):int(px + half)] = False
    return m


def _color_mask(H, S, V, cfg):
    c = cfg["color"]
    return (H >= c["hue_min"]) & (H <= c["hue_max"]) & (S >= c["sat_min"]) & (V >= c["val_min"])


def _label_blobs(mask):
    """8-connected labelling via iterative BFS over True pixels (no scipy)."""
    visited = np.zeros_like(mask, dtype=bool)
    h, w = mask.shape
    blobs = []
    ys, xs = np.nonzero(mask)
    neigh = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]
    for sy, sx in zip(ys.tolist(), xs.tolist()):
        if visited[sy, sx]:
            continue
        stack, coords = [(sy, sx)], []
        visited[sy, sx] = True
        while stack:
            y, x = stack.pop()
            coords.append((y, x))
            for dy, dx in neigh:
                ny, nx = y + dy, x + dx
                if 0 <= ny < h and 0 <= nx < w and mask[ny, nx] and not visited[ny, nx]:
                    visited[ny, nx] = True
                    stack.append((ny, nx))
        yy = [c[0] for c in coords]
        xx = [c[1] for c in coords]
        blobs.append({
            "area": len(coords),
            "centroid": (sum(xx) / len(xx), sum(yy) / len(yy)),
            "bbox": (min(xx), min(yy), max(xx), max(yy)),
        })
    return blobs


def _score(blob, S, cfg, w, h, area_scale):
    sh = cfg["shape"]
    area = blob["area"] / area_scale
    if area < sh["min_area_px"] or area > sh["max_area_px"]:
        return 0.0, {}
    x0, y0, x1, y1 = blob["bbox"]
    bw, bh = (x1 - x0 + 1), (y1 - y0 + 1)
    aspect = bh / max(bw, 1)
    if not (sh["aspect_min"] <= aspect <= sh["aspect_max"]):
        return 0.0, {}
    sat = float(S[y0:y1 + 1, x0:x1 + 1].mean()) / 255.0
    if area < sh["ideal_area_lo"]:
        size_score = area / sh["ideal_area_lo"]
    elif area <= sh["ideal_area_hi"]:
        size_score = 1.0
    else:
        size_score = max(0.3, 1.0 - (area - sh["ideal_area_hi"]) / 4000.0)
    fill = blob["area"] / max(bw * bh, 1)
    fill_score = min(fill / 0.45, 1.0)
    sz = cfg["swing_zone"]
    dx = (blob["centroid"][0] / w - sz["expect_cx"]) / sz["sigma_cx"]
    dy = (blob["centroid"][1] / h - sz["expect_cy"]) / sz["sigma_cy"]
    loc = math.exp(-0.5 * (dx * dx + dy * dy))
    score = (0.50 * sat + 0.30 * size_score + 0.20 * fill_score) * loc
    return score, {"area": round(area, 1), "aspect": round(aspect, 2),
                   "sat": round(sat, 2), "fill": round(fill, 2), "loc": round(loc, 2)}


def detect(img: Image.Image, cfg: dict) -> Detection:
    """Detect the ODM buoy. Returns a Detection with normalised coordinates."""
    w, h = img.size
    area_scale = (w * h) / (1920 * 1080)
    H, S, V = _hsv(img)
    mask = _color_mask(H, S, V, cfg) & _roi_mask(h, w, cfg)
    scored = []
    for b in _label_blobs(mask):
        s, det = _score(b, S, cfg, w, h, area_scale)
        if s > 0:
            scored.append((s, b, det))
    scored.sort(key=lambda t: t[0], reverse=True)
    if not scored:
        return Detection(found=False, conf=0.0)
    best_s, best_b, best_det = scored[0]
    conf = best_s
    if len(scored) > 1 and scored[1][0] > 0.6 * best_s:
        conf *= 0.85  # ambiguous: a close runner-up exists
    cx, cy = best_b["centroid"]
    x0, y0, x1, y1 = best_b["bbox"]
    found = conf >= cfg["conf_min"]
    return Detection(
        found=found, conf=round(conf, 3),
        fx=round(cx / w, 5), fy=round(cy / h, 5),
        bbox_frac=(round(x0 / w, 5), round(y0 / h, 5), round(x1 / w, 5), round(y1 / h, 5)),
        details=best_det,
    )


# --------------------------------------------------------------------------- #
# Temporal smoothing (live sequences only)
# --------------------------------------------------------------------------- #
class LineSmoother:
    """EMA smoother for a live frame sequence.

    Feed each frame's Detection; returns the smoothed (fx, fy) to draw, or None
    when there is no reliable position (never detected, or lost for too long ->
    hide the line rather than freeze a stale one).
    """
    def __init__(self, cfg: dict):
        self.alpha = cfg["smoother"]["ema_alpha"]
        self.max_misses = cfg["smoother"]["max_consecutive_misses"]
        self.fx = None
        self.fy = None
        self.misses = 0

    def update(self, det: Detection):
        if det.found:
            if self.fx is None:
                self.fx, self.fy = det.fx, det.fy
            else:
                self.fx = self.alpha * det.fx + (1 - self.alpha) * self.fx
                self.fy = self.alpha * det.fy + (1 - self.alpha) * self.fy
            self.misses = 0
        else:
            self.misses += 1
            if self.misses > self.max_misses:
                self.fx = self.fy = None
        if self.fx is None:
            return None
        return (self.fx, self.fy)


# --------------------------------------------------------------------------- #
# Production line overlay (used by the relay and, later, the recording copies)
# --------------------------------------------------------------------------- #
def render_line_overlay(cfg, width, height, dest, fx, fy, color=None, opacity=None):
    """Render the start line onto a full-frame transparent RGBA PNG.

    Draws the line (pole base -> buoy at fx,fy) and buoy ring SOLID, then scales
    the whole layer's alpha uniformly so it reads as painted on the water — this
    avoids uneven edges you get from drawing overlapping strokes at low alpha.

    `color` (r,g,b) and `opacity` (0..1) default to config; pass `color`
    explicitly for the start-video red-before/green-after-start behaviour.
    """
    if color is None:
        color = tuple(cfg.get("line_color", [0, 235, 0]))
    if opacity is None:
        opacity = float(cfg.get("line_opacity", 0.5))
    r, g, b = color[0], color[1], color[2]
    im = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    pole = (cfg["pole"]["x"] * width, cfg["pole"]["y"] * height)
    buoy = (fx * width, fy * height)
    lw = max(3, round(height * cfg.get("line_width", 0.006)))
    # single solid colour (no darker outer edge); alpha scaled uniformly below
    d.line([pole, buoy], fill=(r, g, b, 255), width=lw)
    ring = max(8, round(height * 0.014))
    d.ellipse([buoy[0] - ring, buoy[1] - ring, buoy[0] + ring, buoy[1] + ring],
              outline=(r, g, b, 255), width=lw)
    if opacity < 1.0:
        im.putalpha(im.getchannel("A").point(lambda a: int(a * opacity)))
    im.save(dest)
    return dest


# --------------------------------------------------------------------------- #
# Annotation (debug)
# --------------------------------------------------------------------------- #
def annotate(img: Image.Image, det: Detection, cfg: dict, smoothed_fxfy=None) -> Image.Image:
    """Draw ROI, pole base, buoy marker and the start line. If `smoothed_fxfy`
    is given (from LineSmoother) the line is drawn there instead of the raw
    detection."""
    im = img.convert("RGB").copy()
    d = ImageDraw.Draw(im)
    w, h = im.size
    try:
        font = ImageFont.truetype("arial.ttf", max(14, w // 90))
    except Exception:
        font = ImageFont.load_default()
    r = cfg["roi"]
    d.rectangle([int(r["left"] * w), int(r["top"] * h), int(r["right"] * w), int(r["bottom"] * h)],
                outline=(80, 80, 80))
    pole = (int(cfg["pole"]["x"] * w), int(cfg["pole"]["y"] * h))
    d.ellipse([pole[0] - 6, pole[1] - 6, pole[0] + 6, pole[1] + 6], outline=(0, 160, 255), width=3)

    draw_pt = smoothed_fxfy or ((det.fx, det.fy) if det.found else None)
    if draw_pt is not None:
        cx, cy = int(draw_pt[0] * w), int(draw_pt[1] * h)
        d.line([pole, (cx, cy)], fill=(0, 230, 0), width=3)
        if det.bbox_frac and smoothed_fxfy is None:
            bx0, by0, bx1, by1 = det.bbox_frac
            d.rectangle([bx0 * w - 3, by0 * h - 3, bx1 * w + 3, by1 * h + 3], outline=(255, 0, 0), width=3)
        rr = 16
        d.line([cx - rr, cy, cx + rr, cy], fill=(255, 255, 0), width=2)
        d.line([cx, cy - rr, cx, cy + rr], fill=(255, 255, 0), width=2)
        label = f"ODM conf={det.conf:.2f} {det.details}"
        color = (0, 230, 0)
    else:
        label = f"NO CONFIDENT ODM (best conf={det.conf:.2f})"
        color = (255, 80, 80)
    d.rectangle([8, 8, 8 + int(w * 0.6), 8 + font.size + 10], fill=(0, 0, 0))
    d.text((14, 12), label, fill=color, font=font)
    return im
