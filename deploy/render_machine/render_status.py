#!/usr/bin/env python3
"""What the club's bucket says about the renderer watching it.

Its own file rather than a -c one-liner inside the PowerShell script: passing
python source through PowerShell to a native command loses the quotes, and the
first attempt at this died with "'(' was never closed" for exactly that reason.

Run from the repo root with the renderer's settings in the environment:

    .venv/Scripts/python.exe deploy/render_machine/render_status.py
"""
from __future__ import annotations

import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
sys.path.insert(0, os.path.join(_ROOT, "scripts", "replay3d"))
sys.path.insert(0, _ROOT)

# A renderer that spoke more than this long ago is not running: it beats every
# minute while idle and every half minute inside a job.
LIVE_WITHIN_MIN = 10.0


def main() -> int:
    import renderer

    try:
        store = renderer.Store()
    except SystemExit as exc:
        print(f"  {exc}")
        return 1

    print(f"  bucket        : {store.bucket}")
    beat = store.get_json(renderer.HEARTBEAT_KEY) or {}
    try:
        jobs = store.list_jobs()
    except Exception as exc:
        print(f"  could not read the bucket: {type(exc).__name__}: {exc}")
        return 1

    if not beat:
        print("  last check-in : never -- no renderer has run against this bucket")
    else:
        age = (time.time() - float(beat.get("updated_at") or 0)) / 60.0
        live = age < LIVE_WITHIN_MIN
        print(f"  last check-in : {age:.0f} min ago by {beat.get('renderer')}"
              f"{'' if live else '   (so it is not running now)'}")
        busy = beat.get("busy_with")
        if busy and live:
            print(f"  working on    : race {busy}")
        elif busy:
            # The distinction the dashboard card exists to draw: a job left
            # mid-render looks exactly like one being worked on.
            print(f"  working on    : nothing -- it stopped part way through race {busy}")
        else:
            print("  working on    : nothing")

    print(f"  jobs waiting  : {len(jobs)}")
    if jobs and (not beat or (time.time() - float(beat.get("updated_at") or 0)) / 60.0 >= LIVE_WITHIN_MIN):
        print("  NOTE: there is work queued and nothing running to do it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
