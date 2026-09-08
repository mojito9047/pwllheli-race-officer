#!/usr/bin/env python3
"""Drive the race chart/replay in a real browser and check playback behaves.

The app's own test suite cannot cover this: continuous playback is driven by
requestAnimationFrame, which a browser does not fire while the page is hidden —
so a headless check that merely loads the page proves nothing about playing it.
Playwright's Chromium reports visibilityState "visible" and does fire rAF, so it
can actually run the replay and watch what the boats do.

What it checks, per boat, while the replay plays:

  * the replay clock advances, at roughly the selected speed;
  * every boat steps forward smoothly — no teleport-and-return. This is the
    v0.199 fault: the live-position poller repainted every .course-map on the
    page, including the replay's, so each poll snapped the fleet to its live
    positions and the next frame snapped it back.

Usage (start a temporary app instance first — never point this at the hut):

    RO_CAP_BASE=http://127.0.0.1:5058 .venv/Scripts/python.exe \
        scripts/verify_replay.py --race 115

Exits non-zero if playback stalls or a boat jumps, so it can gate a release.
"""
import argparse
import math
import os
import statistics
import sys

from playwright.sync_api import sync_playwright

BASE = os.environ.get("RO_CAP_BASE", "http://localhost:5050")

# Recording every repaint, rather than sampling the map on a timer. The v0.199
# fault lasted a single animation frame (~16 ms) before the next frame corrected
# it, so any sampling slower than the frame rate walks straight past it. Wrapping
# updateBoats catches every repaint and, crucially, *who asked for it*: a caller
# passing `document` is repainting the replay's map as collateral damage.
INSTALL_HOOK = """
() => {
  window.__replayLog = [];
  const holder = document.querySelector('.replay-map .course-map');
  const orig = window.RaceCourseMap.updateBoats;
  window.RaceCourseMap.updateBoats = function (root, boats, opts) {
    let touches = false;
    try {
      const scope = root || document;
      touches = !!holder && Array.from(scope.querySelectorAll('.course-map')).includes(holder);
    } catch (e) { /* ignore */ }
    if (touches) {
      const slider = document.querySelector('.replay-slider');
      const clockEl = document.querySelector('.replay-clock');
      window.__replayLog.push({
        t: performance.now(),
        clock: slider ? Number(slider.value) : null,
        clockText: clockEl ? clockEl.textContent : '',
        exact: (() => { const pn = document.querySelector('[data-replay]');
                        return pn && pn.dataset.clock ? Number(pn.dataset.clock) : null; })(),
        scope: (root === document ? 'document' : 'replay-panel'),
        boats: (boats || []).map(b => ({n: b.sail_no || b.boat_name, lat: b.lat, lon: b.lon})),
      });
    }
    return orig.apply(this, arguments);
  };
  return true;
}
"""

# Reading every boat's marker straight off the Leaflet layer is the honest
# measurement: it is what the viewer actually drew, not what we think it drew.
READ_STATE = """
() => {
  const panel = document.querySelector('[data-replay]');
  if (!panel) return null;
  const holder = panel.querySelector('.replay-map .course-map');
  const st = holder && holder._courseMap;
  const boats = (st && st.boatLayers ? st.boatLayers : []).map(m => {
    const ll = m.getLatLng();
    return {title: (m.options && m.options.title) || '', lat: ll.lat, lon: ll.lng};
  });
  return {
    clock: panel.querySelector('.replay-clock').textContent,
    sliderValue: Number(panel.querySelector('.replay-slider').value),
    status: panel.querySelector('.replay-status').textContent.trim(),
    playing: panel.querySelector('.replay-play').getAttribute('aria-pressed') === 'true',
    trails: (st && st.trailLayers ? st.trailLayers.length : 0),
    boats,
  };
}
"""


def metres(a, b):
    """Rough great-circle distance; fine at course scale."""
    lat = math.radians((a[0] + b[0]) / 2.0)
    dx = math.radians(b[1] - a[1]) * math.cos(lat) * 6371000.0
    dy = math.radians(b[0] - a[0]) * 6371000.0
    return math.hypot(dx, dy)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--race", type=int, required=True, help="race id to replay")
    ap.add_argument("--samples", type=int, default=25, help="how many frames to sample")
    ap.add_argument("--interval", type=float, default=0.4, help="seconds between samples")
    ap.add_argument("--speed-clicks", type=int, default=0,
                    help="times to press the speed button before playing (0 = leave at 4x)")
    ap.add_argument("--from", dest="start_fraction", type=float, default=0.35,
                    help="seek this far into the race before playing (0-1). The window starts at "
                         "the warning signal, so 0 is usually before anyone has reported.")
    ap.add_argument("--track-step-ratio", type=float, default=8.0,
                    help="a recorded track whose largest step exceeds this many times its median "
                         "is bad data, not a viewer fault")
    ap.add_argument("--teleport-metres", type=float, default=300.0,
                    help="a single repaint moving a boat further than this is a teleport. Far above "
                         "any real step: at 60x a 30-knot boat covers ~15 m per frame.")
    ap.add_argument("--headed", action="store_true", help="show the browser")
    args = ap.parse_args()

    url = f"{BASE}/public/race/{args.race}#ptab-chart"
    problems = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.headed)
        page = browser.new_context(viewport={"width": 1400, "height": 1000}).new_page()
        page.goto(url, wait_until="load")
        page.wait_for_timeout(500)
        page.click('.public-tabs .tab-button[data-tab="ptab-chart"]')

        # Wait for the track to arrive rather than guessing a delay.
        page.wait_for_function(
            "() => { const s = document.querySelector('.replay-slider');"
            " return s && !s.disabled && Number(s.max) > Number(s.min); }",
            timeout=15000)
        state = page.evaluate(READ_STATE)
        print(f"loaded: {state['status']}")

        # Seek to where boats are actually on the water before playing.
        page.evaluate(
            """(frac) => {
                const s = document.querySelector('.replay-slider');
                const lo = Number(s.min), hi = Number(s.max);
                s.value = String(Math.round(lo + (hi - lo) * frac));
                s.dispatchEvent(new Event('input', {bubbles: true}));
            }""", args.start_fraction)
        page.wait_for_timeout(700)

        for _ in range(args.speed_clicks):
            page.click(".replay-speed")
        speed_label = page.inner_text(".replay-speed")

        page.evaluate(INSTALL_HOOK)
        page.click(".replay-play")
        page.wait_for_timeout(300)

        samples = []
        for _ in range(args.samples):
            samples.append(page.evaluate(READ_STATE))
            page.wait_for_timeout(int(args.interval * 1000))
        page.click(".replay-play")          # pause
        repaints = page.evaluate("() => window.__replayLog || []")
        browser.close()

    # --- did the clock actually move? -------------------------------------
    first, last = samples[0], samples[-1]
    advanced = last["sliderValue"] - first["sliderValue"]
    wall = args.interval * (len(samples) - 1)
    print(f"speed {speed_label}: replay advanced {advanced:.0f}s over {wall:.1f}s of wall clock "
          f"({advanced / wall:.1f}x)" if wall else "")
    if advanced <= 0:
        problems.append("the replay clock did not advance while playing "
                        "(requestAnimationFrame not firing, or playback broken)")
    if not any(s["playing"] for s in samples):
        problems.append("the play button never reported the playing state")

    # --- who repainted the replay's map? ----------------------------------
    foreign = [r for r in repaints if r["scope"] == "document"]
    print(f"repaints of the replay map: {len(repaints)} total, {len(foreign)} from another script")
    if foreign:
        problems.append(
            f"{len(foreign)} repaints of the replay map came from a caller passing `document` — "
            "that is another script (the live-position poller) overwriting the replayed fleet, "
            "which makes the boats jump")

    # --- did any boat teleport, across every repaint? ----------------------
    #
    # Distance alone cannot tell a teleport from a dropped animation frame: both
    # are a big step. What separates them is *time*. A dropped frame moves the
    # boat and the clock together, so the implied speed stays the boat's own; a
    # teleport moves the boat while no replay time passes at all.
    #
    # Time comes from performance.now() scaled by the playback multiplier, not
    # from the slider: the slider is whole seconds, so at 4x it only ticks every
    # fifteenth frame and most frames would read a zero interval.
    try:
        multiplier = float(speed_label.rstrip("x×"))
    except ValueError:
        multiplier = 1.0
    by_boat = {}
    for r in repaints:
        for b in r["boats"]:
            if b["lat"] is None or b["lon"] is None:
                continue
            by_boat.setdefault(b["n"] or "?", []).append((r["t"], b["lat"], b["lon"]))
    if not by_boat:
        problems.append("no boat markers were drawn at all")

    for title, points in sorted(by_boat.items()):
        steps, implied = [], []
        for i in range(1, len(points)):
            (t0, la0, lo0), (t1, la1, lo1) = points[i - 1], points[i]
            d = metres((la0, lo0), (la1, lo1))
            steps.append(d)
            dt = (t1 - t0) / 1000.0 * multiplier      # replay-seconds elapsed
            if d > 5.0:                                # ignore sub-5 m noise
                implied.append(((d / max(dt, 1e-3)) * 1.94384, d, dt))   # knots
        if len(steps) < 4:
            continue
        median = statistics.median(steps) or 0.0
        biggest = max(steps)
        # Judge on absolute distance. The implied-speed ratio is reported for
        # context but is not the gate: at a 2 ms frame gap even a 7 m step implies
        # four figures of knots, so it flags ordinary browser jank as a teleport.
        worst = max(implied, default=(0.0, 0.0, 0.0))
        teleport = biggest > args.teleport_metres
        flag = "JUMP" if teleport else "ok  "
        name = title.split(" · ")[0][:22]
        print(f"  {flag} {name:24} median step {median:5.1f} m   largest {biggest:7.1f} m"
              f"   (fastest implied {worst[0]:6.0f} kn)")
        if teleport:
            problems.append(
                f"{name} moved {biggest:.0f} m in a single repaint (median step {median:.1f} m) "
                f"— that is a teleport, not sailing.")

    # --- did the replay ever run backwards? --------------------------------
    #
    # A boat regressing to an earlier point on its OWN track is a different fault
    # from a teleport: the position stays plausible, it is the clock that moved
    # the wrong way. Map each drawn position back to the time on that boat's
    # track, and check those times only ever increase.
    def nearest_time(fixes, lat, lon, near_t=None, window=180.0):
        """Time on the track that best matches a drawn position.

        A course that starts and finishes on the same line crosses itself, so a
        position near the line matches two different times equally well and a
        global search flips between them — reporting rewinds that never happened.
        Given the previous match, search only around it and let continuity pick.
        """
        best_t, best_d = None, None
        for f in fixes:
            if near_t is not None and abs(f[0] - near_t) > window:
                continue
            d = metres((lat, lon), (f[1], f[2]))
            if best_d is None or d < best_d:
                best_d, best_t = d, f[0]
        if best_t is None and near_t is not None:      # nothing nearby: search it all
            return nearest_time(fixes, lat, lon, None)
        return best_t

    # --- is the recorded track itself sane? --------------------------------
    #
    # This check exists because the harness once reported "playback OK" while
    # boats were visibly jumping on screen. They were: the viewer was faithfully
    # drawing a track that jumped. Two simulator instances had been feeding the
    # same device ids, so the stored track alternated between two boats a
    # kilometre apart. A replay can only ever be as good as its data, so say
    # which of the two is wrong rather than blaming the viewer.
    import urllib.request, json as _json
    try:
        with urllib.request.urlopen(f"{BASE}/public/race/{args.race}/track", timeout=15) as fh:
            track_json = _json.loads(fh.read().decode())
        tracks = {(b["sail_no"] or b["boat_name"]): b["fixes"] for b in track_json.get("boats", [])}
    except Exception as exc:                       # measurement only; never fail on it
        tracks = {}
        print(f"(could not fetch the track: {exc})")

    if tracks:
        print("recorded track quality:")
        for name, fixes in sorted(tracks.items()):
            if len(fixes) < 10:
                continue
            steps = [metres((fixes[i - 1][1], fixes[i - 1][2]), (fixes[i][1], fixes[i][2]))
                     for i in range(1, len(fixes))]
            med = statistics.median(steps) or 0.0
            worst = max(steps)
            ratio = worst / med if med else 0.0
            bad = ratio > args.track_step_ratio
            print(f"  {'BAD ' if bad else 'ok  '} {name:12} {len(fixes):4} fixes   "
                  f"median step {med:6.1f} m   largest {worst:7.1f} m  ({ratio:.0f}x median)")
            if bad:
                problems.append(
                    f"{name}'s RECORDED TRACK jumps {worst:.0f} m between fixes against a "
                    f"{med:.0f} m median — the data is bad, not the viewer. Check nothing else is "
                    f"feeding the same device id.")

    # The exact clock is definitive; the position mapping below is a cross-check
    # that is confounded by tracks which cross themselves (out and back on the
    # same line), where "nearest fix" is genuinely ambiguous.
    exact = [r["exact"] for r in repaints if r.get("exact") is not None]
    if exact:
        drops = [(exact[i - 1], exact[i]) for i in range(1, len(exact)) if exact[i] < exact[i - 1] - 0.001]
        worst_drop = max((a - b for a, b in drops), default=0.0)
        print(f"replay clock: {len(exact)} frames, {len(drops)} went backwards"
              + (f", worst {worst_drop:.2f}s" if drops else ""))
        if drops:
            problems.append(f"the replay clock ran backwards {len(drops)} times "
                            f"(worst {worst_drop:.2f}s) — playback is rewinding")

    if tracks:
        # This is the check that matches the reported symptom most directly:
        # "the boat went back to a previous point on its own track". It was first
        # written off as ambiguous on self-crossing courses — wrongly. It had
        # been reporting the corrupt demo data accurately, and reads 0 once the
        # data is clean. The clock and track-quality checks above say which of
        # the two is at fault.
        print("rewind check (drawn position mapped back to the boat's own track):")
        for name, fixes in sorted(tracks.items()):
            if not fixes:
                continue
            seq = [(r["t"], b["lat"], b["lon"], r.get("clockText", ""))
                   for r in repaints for b in r["boats"]
                   if (b["n"] or "") == name and b["lat"] is not None]
            if len(seq) < 5:
                continue
            times, prev_t = [], None
            for (w, la, lo, ct) in seq:
                prev_t = nearest_time(fixes, la, lo, prev_t)
                times.append((w, prev_t, ct))
            backs = [(times[i - 1], times[i]) for i in range(1, len(times))
                     if times[i][1] < times[i - 1][1] - 1.0]
            worst = max((a[1] - b[1] for a, b in backs), default=0.0)
            flag = "REWIND" if backs else "ok    "
            print(f"  {flag} {name:12} {len(backs):3} backward steps"
                  + (f", worst {worst:.0f}s (clock {backs[0][0][2]} -> {backs[0][1][2]})" if backs else ""))
            if backs:
                problems.append(
                    f"{name} went backwards along its own track {len(backs)} times "
                    f"(worst {worst:.0f}s of replay time). Read it with the two checks above: "
                    f"a monotonic clock plus a jumpy recorded track means the DATA is going "
                    f"backwards; a clean track means playback is.")

    trails = [s["trails"] for s in samples]
    if trails and max(trails) == 0:
        problems.append("no trails were drawn at any point")

    print()
    if problems:
        print("FAILED:")
        for p_ in problems:
            print("  -", p_)
        return 1
    print(f"playback OK — {len(by_boat)} boats, clock advanced, no jumps.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
