#!/usr/bin/env python3
"""Wind readout burned into the live stream (runs on the relay LXC).

The camera shows the water; this puts the numbers on it — TWD, TWS and gust,
top centre, from the hut's own weather station. Somebody watching from the bar
or the club website then sees the same wind the race officer is looking at,
without a second screen.

**How it stays live.** ffmpeg's ``drawtext`` re-reads its ``textfile`` while it
runs (``reload=1``), so nothing here has to restart the encode. A child process
polls the hut every few seconds and rewrites one small file; the text on the
picture follows.

**Why staleness is the whole problem.** A file-backed readout keeps showing the
last value it was given. If the hut link drops, the stream would carry on
displaying a wind from twenty minutes ago, burned into live-looking footage,
with nothing on screen to say so — and people make decisions on it. So a sample
older than ``stale_after`` renders as **nothing at all**. An empty file draws an
empty string, which is the honest answer: the picture simply stops claiming to
know the wind.

Everything here is best-effort, like the branding it sits beside. No font, no
network, no numbers, a bad response — the stream runs without the readout rather
than not at all.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request

# The hut's public wind endpoint. Already public (it is what the competitor page
# and the clubhouse display poll), so no credentials travel to the relay.
WIND_URL = os.environ.get(
    "WIND_URL", "https://hut-app.pwllhelisailingclub.org/api/weather/current")
# Off unless asked for, like every other overlay here.
WIND_OVERLAY = os.environ.get("WIND_OVERLAY", "0").strip().lower() in ("1", "true", "yes", "on")
# How often the hut is asked. The station itself samples far slower than this;
# five seconds keeps the reading current without being noticeable on a 4G link.
POLL_SECONDS = max(2, int(os.environ.get("WIND_POLL_SECONDS", "5") or 5))
# How old a sample may be before the readout blanks. Generous enough to ride out
# a missed poll or two, short enough that nobody sails on a stale number.
STALE_AFTER_S = max(30, int(os.environ.get("WIND_STALE_SECONDS", "120") or 120))

# Text height as a fraction of the frame, so it holds at any camera resolution.
# Started at 0.045 and halved after seeing it on the real stream: at 1080p that
# was 48px and read as a banner rather than a readout. An env knob because it is
# a matter of taste on a screen nobody developing this is looking at.
FONT_SCALE = float(os.environ.get("WIND_FONT_SCALE", "0.0225") or 0.0225)
# Outlined text by default; set WIND_BOX=1 for a solid panel behind it instead.
BOX_BEHIND_TEXT = os.environ.get("WIND_BOX", "0").strip().lower() in ("1", "true", "yes", "on")

# First font that exists wins. Debian ships DejaVu; Alpine needs ttf-dejavu.
FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
)


def wind_enabled() -> bool:
    """Whether the readout was asked for at all."""
    return WIND_OVERLAY


def find_font(candidates=FONT_CANDIDATES):
    """The first font present, or None. No font means no readout, not no stream."""
    for path in candidates:
        if os.path.exists(path):
            return path
    return None


def _degrees(value) -> str:
    """A compass bearing as three digits: 7 degrees reads 007, never 7."""
    try:
        deg = int(round(float(value))) % 360
    except (TypeError, ValueError):
        return ""
    return f"{deg:03d}"


def _knots(value) -> str:
    try:
        return f"{float(value):.1f}"
    except (TypeError, ValueError):
        return ""


def format_wind(sample, now=None, stale_after=STALE_AFTER_S) -> str:
    """One line for the picture, or ``""`` when there is nothing honest to say.

    Blank rather than stale, blank rather than partial-looking: a reading with no
    direction is not a wind readout. Gust is dropped on its own when the station
    does not report one, because plenty do not and "Gust --" is just noise.
    """
    if not isinstance(sample, dict):
        return ""
    stamp = sample.get("t")
    if stamp is not None:
        try:
            age = (time.time() if now is None else now) - float(stamp)
        except (TypeError, ValueError):
            return ""
        if age > stale_after or age < -stale_after:
            # Too old to trust — or stamped in the future, which means the two
            # clocks disagree and the age cannot be reasoned about either way.
            return ""
    twd, tws = _degrees(sample.get("twd")), _knots(sample.get("tws"))
    if not twd or not tws:
        return ""
    parts = [f"TWD {twd}", f"TWS {tws} kn"]
    gust = _knots(sample.get("gust"))
    if gust:
        parts.append(f"Gust {gust} kn")
    return "   ".join(parts)


# The same reason the manifest fetch above carries one: Cloudflare's bot rules
# 403 the default python-urllib User-Agent, so curl succeeds from the same host
# while urllib gets nothing. That failure is silent here — it looks exactly like
# a station with no wind to report — which is what made it worth a comment twice.
HTTP_HEADERS = {"User-Agent": "Mozilla/5.0 (PwllheliLiveRelay)"}


def fetch_wind(url=None, timeout=4.0):
    """The hut's latest sample, or None. Never raises."""
    try:
        request = urllib.request.Request(url or WIND_URL, headers=HTTP_HEADERS)
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8", errors="replace"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    current = payload.get("current")
    return current if isinstance(current, dict) else None


def write_once(path, url=None, now=None) -> str:
    """Fetch, format and write the readout file. Returns what was written."""
    text = format_wind(fetch_wind(url), now=now)
    tmp = f"{path}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as handle:
            handle.write(text)
        # Replaced atomically: ffmpeg re-reads this file constantly and must
        # never catch it half-written.
        os.replace(tmp, path)
    except Exception:
        pass
    return text


def start_writer(path, url=None, poll_seconds=POLL_SECONDS):
    """Fork a child that keeps the readout file current. Returns its pid or None.

    The caller goes on to ``exec`` ffmpeg, so the child cannot be a thread — the
    exec would discard it. It watches its parent instead: when MediaMTX kills
    ffmpeg because the last viewer left, this process is reparented to init, the
    parent pid changes, and the child stops. Nothing is left polling the hut on
    behalf of a stream nobody is watching.
    """
    first = write_once(path, url)  # first value before the encode starts
    # Say so out loud. Logging only on failure made "switched off", "started and
    # died" and "working" all look the same in the journal — one line here means
    # silence can be read as off, which is the whole point of a log.
    sys.stderr.write(
        f"wind: readout on, every {poll_seconds}s from {url or WIND_URL} — "
        + (f"first value {first!r}\n" if first
           else "no wind yet (blank until the hut reports something current)\n"))
    try:
        pid = os.fork()
    except Exception as exc:       # no fork (or no permission): run without updates
        sys.stderr.write(f"wind: cannot start updater ({exc}); readout will not refresh\n")
        return None
    if pid:
        return pid
    # --- child ---
    try:
        parent = os.getppid()
        while os.getppid() == parent:
            time.sleep(poll_seconds)
            write_once(path, url)
    except Exception:
        pass
    finally:
        os._exit(0)


def _filter_path(path) -> str:
    """A path ffmpeg's filtergraph parser will read as a single path.

    A colon ends an option, so an unescaped one truncates the filename silently.
    The relay's own paths have none — but a filename is not the place to rely on
    that, and the font path goes through exactly the same parser.
    """
    return str(path).replace("\\", "/").replace(":", "\\:")


def drawtext_chain(label_in, label_out, path, font, height, margin):
    """The filtergraph fragment that paints the readout, top centre.

    Kept as a fragment rather than a whole command so it can be dropped into the
    branding chain and sit *above* the logos: the corners are theirs, the middle
    is free, but on a narrow frame a wide logo can reach in.
    """
    size = max(12, round(height * FONT_SCALE))
    # White alone disappears against a pale overcast sky, which is most of what
    # this camera looks at, so the text needs *something* behind it. An outline
    # does that without the slab a box puts across the picture — the point is a
    # readout on the water, not a caption bar over it. `WIND_BOX=1` brings the
    # box back for anyone who prefers it.
    if BOX_BEHIND_TEXT:
        backing = f":box=1:boxcolor=black@0.35:boxborderw={max(6, size // 3)}"
    else:
        backing = f":borderw={max(1, round(size / 12))}:bordercolor=black@0.85"
    return (
        f"[{label_in}]drawtext=textfile='{_filter_path(path)}':reload=1"
        f":fontfile='{_filter_path(font)}'"
        f":fontcolor=white:fontsize={size}{backing}"
        f":x=(w-text_w)/2:y={margin}[{label_out}]"
    )
