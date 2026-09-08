#!/usr/bin/env python3
"""How late the trackers' fixes arrive, measured at Traccar rather than at the app.

A tracker's delay is not one number. A GL-series unit holds its fixes and uploads
them in a batch, so a fix is roughly 45 seconds old by the time Traccar has it,
every time, in normal health; the RUTX50 pushes as it goes and arrives in under a
second. Neither of those is a fault. What matters is the *tail*: the proportion of
fixes that arrive a long way late, because that is what delays a GPS finish being
detected. The finish time itself is interpolated from fix timestamps and stays
right; it is the proposal and the horn that wait.

Traccar stamps every position with both `fixTime` (when the GPS fixed it) and
`serverTime` (when Traccar received it), and keeps them for about ten days. The
difference is the tracker's own uplink delay with the app removed from the
question entirely — which is the honest place to measure from, because the app
polls intermittently and its stored `server_time` carries its own gaps.

    python scripts/tracker_latency.py                       # last 12h, every device
    python scripts/tracker_latency.py --daily --days 10     # one row per device per day
    python scripts/tracker_latency.py --compare 864864073309183 \\
        --control 864864070498856 --flashed 2026-08-18      # did a change help?

Three things this exists to stop you concluding too quickly:

  * **One night proves nothing.** The same tracker has run between 0% and 49%
    late-fix rate across ten consecutive nights with nothing changed on it. Any
    before/after on a single night is inside the noise. `--compare` therefore
    prints the whole baseline, not just a pair of numbers.
  * **Do not start the "after" window at the reboot.** A unit that has just been
    power-cycled spends its first half hour re-acquiring and catching up, and
    those fixes are late for a reason that is not the change you are testing.
    `--compare` skips the rest of the day a change was made for that reason.
  * **The bad spells hit both units at once.** When two independent trackers
    degrade on the same night, the cause is upstream — the relay, the cell
    network, or Traccar — not the tracker. Keeping a control device in the table
    is the only way to see that.

Read-only: it makes GET requests to Traccar and touches nothing.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import statistics
import sys
import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Sequence, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _ROOT)

from core import appstate                      # noqa: E402
from core.track import _traccar_get            # noqa: E402

# Anything beyond this is a device that was switched off and dumped its buffer on
# power-up, not a late fix. Left in, one such burst moves every percentile.
MAX_PLAUSIBLE_DELAY_S = 7200
# The threshold the tail is counted against. A fix later than this has missed the
# next reporting cycle, so a finish crossing in it is a cycle behind the water.
LATE_S = 60


def traccar_config() -> Dict[str, str]:
    """Traccar base URL and token, read straight from the app's settings table.

    Deliberately not via ``core.track.track_config()``: that needs the database
    initialised, which needs ``app.py`` imported, which starts the pollers. A
    read-only diagnostic must not do that on the hut.
    """
    if not appstate.DB_PATH.exists():
        raise SystemExit(f"No database at {appstate.DB_PATH}.")
    db = sqlite3.connect(f"file:{appstate.DB_PATH}?mode=ro", uri=True)
    try:
        rows = db.execute(
            "SELECT key, value FROM hardware_settings WHERE key IN ('traccar_base_url','traccar_token')"
        ).fetchall()
    except sqlite3.OperationalError as exc:
        raise SystemExit(f"Could not read settings: {exc}")
    finally:
        db.close()
    cfg = {k: (v or "").strip() for k, v in rows}
    base = cfg.get("traccar_base_url", "").rstrip("/")
    token = cfg.get("traccar_token", "")
    if not base or not token:
        raise SystemExit("Traccar is not configured in Settings (base URL or token missing).")
    return {"base_url": base, "token": token}


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_time(value: str) -> float:
    return datetime.strptime(value[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).timestamp()


def devices(cfg: Dict[str, str]) -> List[Dict]:
    """Every device the token can see, newest-reporting first."""
    out = _traccar_get(cfg, "/api/devices", timeout=30)
    return [d for d in out if isinstance(d, dict)] if isinstance(out, list) else []


def route(cfg: Dict[str, str], device_id: int, start: datetime, end: datetime) -> List[Tuple[float, float]]:
    """``(fix_time, delay)`` for every position in the window.

    One request serves both the delay figures and the reporting cadence — asking
    twice for the same window doubles the load on the relay for nothing.
    """
    query = urllib.parse.urlencode({"deviceId": device_id, "from": iso(start), "to": iso(end)})
    rows = _traccar_get(cfg, f"/api/reports/route?{query}", timeout=120)
    if not isinstance(rows, list):
        return []
    out = []
    for p in rows:
        if not isinstance(p, dict) or not p.get("fixTime") or not p.get("serverTime"):
            continue
        fixed = parse_time(p["fixTime"])
        delay = parse_time(p["serverTime"]) - fixed
        if 0 <= delay < MAX_PLAUSIBLE_DELAY_S:
            out.append((fixed, delay))
    return out


def latencies(cfg: Dict[str, str], device_id: int, start: datetime, end: datetime) -> List[float]:
    """serverTime - fixTime for every position in the window, in seconds."""
    return [delay for _, delay in route(cfg, device_id, start, end)]


def cadence_of(positions: Sequence[Tuple[float, float]]) -> Optional[float]:
    """Median gap between consecutive fixes — the device's reporting interval."""
    if len(positions) < 3:
        return None
    times = sorted(fixed for fixed, _ in positions)
    gaps = [b - a for a, b in zip(times, times[1:])]
    return statistics.median(gaps) if gaps else None


def pct(values: Sequence[float], p: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * p))]


def summarise(values: Sequence[float]) -> Optional[Dict[str, float]]:
    if len(values) < 20:
        return None
    return {
        "n": len(values),
        "median": statistics.median(values),
        "p90": pct(values, 0.90),
        "p99": pct(values, 0.99),
        "late": 100.0 * sum(1 for v in values if v > LATE_S) / len(values),
    }


def window_for(day: datetime, spec: str) -> Tuple[datetime, datetime]:
    """Turn ``HH:MM-HH:MM`` into a UTC window on ``day`` (wrapping past midnight)."""
    a, _, b = spec.partition("-")
    sh, sm = (int(x) for x in a.split(":"))
    eh, em = (int(x) for x in b.split(":"))
    start = day.replace(hour=sh, minute=sm, second=0, microsecond=0)
    end = day.replace(hour=eh, minute=em, second=0, microsecond=0)
    if end <= start:
        end += timedelta(days=1)
    return start, end


def resolve(devs: List[Dict], wanted: str) -> Optional[Dict]:
    for d in devs:
        if str(d.get("uniqueId")) == wanted or str(d.get("id")) == wanted:
            return d
    return None


def snapshot(cfg: Dict[str, str], devs: List[Dict], hours: int) -> None:
    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=hours)
    print(f"Fix -> received by Traccar, last {hours}h ({iso(start)} to {iso(end)})\n")
    print(f"{'tracker':<18}{'name':<22}{'n':>6}{'median':>8}{'p90':>7}{'p99':>7}{'>%ds' % LATE_S:>8}{'cadence':>9}")
    for d in devs:
        positions = route(cfg, d["id"], start, end)
        stats = summarise([delay for _, delay in positions])
        name = str(d.get("name") or "")[:21]
        if not stats:
            print(f"{str(d.get('uniqueId')):<18}{name:<22}{len(positions):>6}   (not reporting)")
            continue
        gap = cadence_of(positions)
        print(f"{str(d.get('uniqueId')):<18}{name:<22}{stats['n']:>6}{stats['median']:>7.0f}s"
              f"{stats['p90']:>6.0f}s{stats['p99']:>6.0f}s{stats['late']:>7.1f}%"
              f"{(f'{gap:.0f}s' if gap else '-'):>9}")


def daily(cfg: Dict[str, str], devs: List[Dict], days: int, spec: str) -> Dict[str, List[Tuple[str, Dict]]]:
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    history: Dict[str, List[Tuple[str, Dict]]] = {}
    for d in devs:
        uid = str(d.get("uniqueId"))
        print(f"\n{uid}  {str(d.get('name') or '')}   window {spec}Z")
        print(f"  {'day':<12}{'n':>6}{'median':>8}{'p90':>7}{'>%ds' % LATE_S:>8}")
        rows: List[Tuple[str, Dict]] = []
        for back in range(days, -1, -1):
            day = today - timedelta(days=back)
            start, end = window_for(day, spec)
            if start > datetime.now(timezone.utc):
                continue
            stats = summarise(latencies(cfg, d["id"], start, end))
            label = day.strftime("%m-%d")
            if not stats:
                print(f"  {label:<12}     -   (no data)")
                continue
            rows.append((label, stats))
            print(f"  {label:<12}{stats['n']:>6}{stats['median']:>7.0f}s{stats['p90']:>6.0f}s{stats['late']:>7.1f}%")
        history[uid] = rows
    return history


def compare(cfg: Dict[str, str], devs: List[Dict], target: str, control: Optional[str],
            flashed: str, days: int, spec: str) -> None:
    """Before/after around a change, with the baseline spread that judges it."""
    picked = [resolve(devs, target)]
    if control:
        picked.append(resolve(devs, control))
    for want, got in zip([target, control], picked):
        if want and not got:
            raise SystemExit(f"Traccar has no device '{want}'.")
    cut = datetime.strptime(flashed, "%Y-%m-%d").replace(tzinfo=timezone.utc)

    print(f"Change made on {flashed}. That whole day is skipped: a unit that has just "
          f"rebooted\nspends its first hour catching up, which is not the change speaking.\n")
    history = daily(cfg, [d for d in picked if d], days, spec)

    print(f"\n{'':<20}{'baseline (before)':>34}{'after':>22}")
    print(f"{'tracker':<20}{'nights':>8}{'median':>9}{'late range':>17}{'median':>10}{'late':>12}")
    for d in picked:
        if not d:
            continue
        uid = str(d.get("uniqueId"))
        before, after = [], []
        for label, stats in history.get(uid, []):
            day = datetime.strptime(f"{cut.year}-{label}", "%Y-%m-%d").replace(tzinfo=timezone.utc)
            (before if day < cut else after if day > cut else []).append(stats)
        if not before or not after:
            print(f"{uid:<20}  not enough either side of {flashed}")
            continue
        b_late = [s["late"] for s in before]
        role = "  <- changed" if uid == str(picked[0].get("uniqueId")) else "  (control)"
        print(f"{uid:<20}{len(before):>8}{statistics.median([s['median'] for s in before]):>8.0f}s"
              f"{f'{min(b_late):.1f}-{max(b_late):.1f}%':>17}"
              f"{statistics.median([s['median'] for s in after]):>9.0f}s"
              f"{statistics.median([s['late'] for s in after]):>11.1f}%{role}")
        if uid == str(picked[0].get("uniqueId")):
            after_late = statistics.median([s["late"] for s in after])
            spread = max(b_late) - min(b_late)
            shift = after_late - statistics.median(b_late)
            print(f"{'':<20}  tail moved {shift:+.1f} points against a baseline that itself "
                  f"swings {spread:.1f} points\n{'':<20}  -> "
                  + ("WITHIN the noise; nothing proven either way"
                     if abs(shift) < spread else "OUTSIDE the usual swing; worth a closer look"))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--hours", type=int, default=12, help="snapshot span (default 12)")
    ap.add_argument("--daily", action="store_true", help="one row per device per day")
    ap.add_argument("--days", type=int, default=10, help="days of history (default 10; Traccar keeps ~10)")
    ap.add_argument("--window", default="00:00-06:00",
                    help="UTC window sampled each day (default 00:00-06:00, boats moored and quiet)")
    ap.add_argument("--device", action="append", default=[],
                    help="limit to this IMEI/uniqueId (repeatable)")
    ap.add_argument("--compare", metavar="UID", help="device that changed")
    ap.add_argument("--control", metavar="UID", help="comparable device that did not change")
    ap.add_argument("--flashed", metavar="YYYY-MM-DD", help="date of the change")
    args = ap.parse_args()

    cfg = traccar_config()
    devs = devices(cfg)
    if not devs:
        raise SystemExit("Traccar returned no devices for this token.")

    if args.compare:
        if not args.flashed:
            raise SystemExit("--compare needs --flashed YYYY-MM-DD.")
        compare(cfg, devs, args.compare, args.control, args.flashed, args.days, args.window)
        return 0

    if args.device:
        devs = [d for d in devs if str(d.get("uniqueId")) in args.device]
        if not devs:
            raise SystemExit("None of those devices are in Traccar.")

    if args.daily:
        daily(cfg, devs, args.days, args.window)
    else:
        snapshot(cfg, devs, args.hours)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
