#!/usr/bin/env python3
"""On-demand branded source for the hut live stream (runs on the relay LXC).

MediaMTX calls this via `runOnDemand` when the first viewer connects. It:
  1. fetches the current branding manifest from the hut app (/api/branding/live),
  2. downloads the club + sponsor logos,
  3. builds an ffmpeg overlay that MIRRORS the app's public branding
     (club logo top-left; one sponsor at a time top-right, rotating; logos sized
     ~15% of frame height, semi-transparent), and
  4. execs ffmpeg to pull the camera and publish the branded stream into MediaMTX.

If branding is disabled (or the manifest/logos can't be fetched) it falls back to
`-c copy` with no overlay, so a branding hiccup never takes the stream down. The
overlay encode runs here on the mains-powered relay — the hut still sends only the
raw sub-stream. MediaMTX kills this process when the last viewer leaves
(runOnDemandCloseAfter), which drops the hut pull.

Env (MediaMTX sets MTX_PATH and RTSP_PORT automatically):
  CAMERA_URL    camera RTSP via the cloudflared bridge
                (default rtsp://VIEWUSER:VIEWPASS@localhost:18554/Streaming/Channels/102)
  MANIFEST_URL  hut branding manifest (default https://hut-app.pwllhelisailingclub.org/api/branding/live)
  CAMERA_FPS    sub-stream fps, used for -g (default 15)
  WIND_OVERLAY  set to 1 to burn the hut's TWD/TWS/gust across the top (default off)
  WIND_URL      hut wind endpoint (default https://hut-app.pwllhelisailingclub.org/api/weather/current)
"""
import json
import os
import subprocess
import sys
import tempfile
import urllib.request

CAMERA_URL = os.environ.get("CAMERA_URL", "rtsp://VIEWUSER:VIEWPASS@localhost:18554/Streaming/Channels/102")
MANIFEST_URL = os.environ.get("MANIFEST_URL", "https://hut-app.pwllhelisailingclub.org/api/branding/live")
FPS = int(os.environ.get("CAMERA_FPS", "15"))
PUBLISH_URL = f"rtsp://localhost:{os.environ.get('RTSP_PORT', '8554')}/{os.environ.get('MTX_PATH', 'hut')}"

# Optional ODM start-line overlay (prototype). Imported defensively so a missing
# dependency (numpy/Pillow) or module simply disables the line — never the stream.
STARTLINE_CONFIG = os.environ.get(
    "STARTLINE_CONFIG", os.path.join(os.path.dirname(os.path.abspath(__file__)), "startline_config.json"))

# Optional wind readout, imported the same defensive way: no module, no font or
# no numbers means a stream without the readout, never no stream.
try:
    import relay_wind
except Exception as _exc:      # relay-only path
    relay_wind = None
    sys.stderr.write(f"wind overlay unavailable ({_exc}); streaming without it\n")

try:
    import relay_startline
except Exception as _exc:  # pragma: no cover - environment dependent
    relay_startline = None
    sys.stderr.write(f"start-line overlay unavailable ({_exc}); streaming without it\n")

# -analyzeduration/-probesize: newer ffmpeg defaults analyzeduration to 0, so it gives
# up before it has seen a keyframe/SPS on this camera's sub-stream and reports
# "Could not find codec parameters ... unspecified size" — the encode then never
# starts. Giving it explicit headroom lets it read the H.264 dimensions reliably; it
# stops as soon as it has them, so a healthy stream still starts in ~1-2s.
PROBE_ARGS = ["-analyzeduration", "10M", "-probesize", "10M"]
# -use_wallclock_as_timestamps 1: the camera's RTSP stream arrives without usable
# PTS/DTS, which makes -c copy produce non-monotonic timestamps and unplayable HLS.
# Stamping packets with wall-clock time on input fixes it (works with copy).
INPUT = ["ffmpeg", "-nostdin", "-loglevel", "warning", "-rtsp_transport", "tcp", *PROBE_ARGS, "-use_wallclock_as_timestamps", "1", "-thread_queue_size", "512", "-i", CAMERA_URL]
# Publish to MediaMTX over TCP too — UDP RTP drops packets even locally (big frames).
COPY_TAIL = ["-an", "-c:v", "copy", "-rtsp_transport", "tcp", "-f", "rtsp", PUBLISH_URL]

# Cloudflare's bot/WAF blocks the default Python-urllib user-agent with a 403, so send
# a normal User-Agent on the manifest + logo fetches (curl works, plain urllib doesn't).
HTTP_HEADERS = {"User-Agent": "Mozilla/5.0 (PwllheliLiveRelay)"}


def _http_get(url, timeout=8):
    return urllib.request.urlopen(urllib.request.Request(url, headers=HTTP_HEADERS), timeout=timeout)


def run_copy():
    os.execvp("ffmpeg", INPUT + COPY_TAIL)


def fetch_manifest():
    try:
        with _http_get(MANIFEST_URL) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as exc:
        sys.stderr.write(f"branding manifest fetch failed ({exc}); streaming unbranded\n")
        return {"enabled": False}


def download(url, dest):
    try:
        with _http_get(url) as r, open(dest, "wb") as f:
            f.write(r.read())
        return True
    except Exception as exc:
        sys.stderr.write(f"logo download failed {url}: {exc}\n")
        return False


def probe_dimensions():
    """Return (width, height) of the camera stream so logo sizes/margins match the
    app's proportions. Falls back to 1280x720 if ffprobe isn't available."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x",
             "-rtsp_transport", "tcp", *PROBE_ARGS, CAMERA_URL],
            capture_output=True, text=True, timeout=15,
        )
        w, h = out.stdout.strip().split("x")
        return int(w), int(h)
    except Exception as exc:
        sys.stderr.write(f"probe failed ({exc}); assuming 1280x720\n")
        return 1280, 720


def build_branded_cmd(logos, interval, width, height, line_path=None,
                      wind_path=None, wind_font=None):
    """Build the ffmpeg argv for a branded encode, mirroring the app's public
    branding layout. `logos` is [(kind, path), ...] with kind in {club, sponsor};
    `line_path`, if given, is a full-frame transparent PNG (the ODM start line)
    composited under the logos. `wind_path`/`wind_font` add the top-centre wind
    readout. Returns None if there is nothing to overlay."""
    want_wind = bool(wind_path and wind_font and relay_wind is not None)
    if not logos and not line_path and not want_wind:
        return None
    # Match core/video.py build_branding_overlay_filter_for_size proportions.
    margin = max(12, round(height * 0.022))
    logo_cap_h = max(32, round(height * 0.15))
    club_max_w = round(width * 0.22)
    sponsor_max_w = round(width * 0.24)
    n_sponsors = sum(1 for kind, _ in logos if kind == "sponsor")

    cmd = list(INPUT)
    for _, path in logos:
        cmd += ["-i", path]   # single-frame image; overlay eof_action=repeat holds it
    line_idx = None
    if line_path:
        cmd += ["-i", line_path]
        line_idx = len(logos) + 1   # inputs: 0=video, 1..N=logos, N+1=line

    chains = ["[0:v]format=yuv420p[base]"]
    for in_i, (kind, _) in enumerate(logos, start=1):
        cap_w = club_max_w if kind == "club" else sponsor_max_w
        alpha = "0.82" if kind == "club" else "0.90"
        chains.append(
            f"[{in_i}:v]scale={cap_w}:{logo_cap_h}:force_original_aspect_ratio=decrease,"
            f"format=rgba,colorchannelmixer=aa={alpha}[l{in_i}]"
        )
    cur = "base"
    # Draw the start line first, so the corner logos always sit on top of it.
    if line_idx is not None:
        chains.append(f"[{line_idx}:v]format=rgba[sline]")
        chains.append(f"[{cur}][sline]overlay=0:0:format=auto:eof_action=repeat[vline]")
        cur = "vline"
    sponsor_idx = 0
    for in_i, (kind, _) in enumerate(logos, start=1):
        out = f"v{in_i}"
        if kind == "club":
            # top-left
            chains.append(f"[{cur}][l{in_i}]overlay={margin}:{margin}:format=auto:eof_action=repeat[{out}]")
        else:
            # top-right, one sponsor at a time on a rotation (commas escaped for filtergraph)
            enable = ""
            if n_sponsors > 1:
                enable = f":enable='eq(mod(floor(t/{interval})\\,{n_sponsors})\\,{sponsor_idx})'"
            chains.append(f"[{cur}][l{in_i}]overlay=main_w-overlay_w-{margin}:{margin}:format=auto:eof_action=repeat{enable}[{out}]")
            sponsor_idx += 1
        cur = out

    # The readout goes on last so it sits above everything: the corners belong to
    # the logos and the middle is free, but on a narrow frame a wide logo reaches in.
    if want_wind:
        chains.append(relay_wind.drawtext_chain(cur, "vwind", wind_path, wind_font,
                                                height, margin))
        cur = "vwind"

    cmd += [
        "-filter_complex", ";".join(chains),
        "-map", f"[{cur}]", "-an",
        "-c:v", "libx264", "-preset", "veryfast", "-tune", "zerolatency",
        "-g", str(FPS), "-pix_fmt", "yuv420p",
        "-rtsp_transport", "tcp", "-f", "rtsp", PUBLISH_URL,
    ]
    return cmd


def main():
    manifest = fetch_manifest()
    tmp = tempfile.mkdtemp(prefix="relay_brand_")

    logos = []  # (kind, path) in overlay order
    if manifest.get("enabled"):
        club = manifest.get("club_logo_url")
        if club:
            path = os.path.join(tmp, "club.png")
            if download(club, path):
                logos.append(("club", path))
        for i, sponsor in enumerate(manifest.get("sponsors") or []):
            url = sponsor.get("url")
            path = os.path.join(tmp, f"sponsor{i}")
            if url and download(url, path):
                logos.append(("sponsor", path))

    # Optional ODM start-line overlay. Only probe/grab when it is actually wanted.
    want_line = relay_startline is not None and relay_startline.startline_enabled(STARTLINE_CONFIG)

    # Optional wind readout. A missing font disables it here rather than letting
    # ffmpeg fail on a filter it cannot build.
    wind_font = relay_wind.find_font() if (relay_wind is not None and relay_wind.wind_enabled()) else None
    if relay_wind is not None and relay_wind.wind_enabled() and not wind_font:
        sys.stderr.write("wind: no usable font on this host; streaming without the readout\n")
    elif relay_wind is not None and not relay_wind.wind_enabled():
        # Said once per stream because the alternative is a silent nothing, which
        # is indistinguishable from a readout that started and failed.
        sys.stderr.write("wind: readout off (set WIND_OVERLAY=1 in /etc/relay/relay.env)\n")
    want_wind = bool(wind_font)

    if not logos and not want_line and not want_wind:
        run_copy()
        return

    width, height = probe_dimensions()
    line_path = None
    if want_line:
        line_path = relay_startline.build_startline_overlay(CAMERA_URL, width, height, STARTLINE_CONFIG, tmp)

    wind_path = None
    if want_wind:
        wind_path = os.path.join(tmp, "wind.txt")
        # Started before the exec, and it stops itself when ffmpeg goes away.
        relay_wind.start_writer(wind_path)

    cmd = build_branded_cmd(logos, int(manifest.get("rotation_seconds") or 5) or 5,
                            width, height, line_path=line_path,
                            wind_path=wind_path, wind_font=wind_font)
    if cmd is None:
        run_copy()
        return
    os.execvp("ffmpeg", cmd)


if __name__ == "__main__":
    main()
