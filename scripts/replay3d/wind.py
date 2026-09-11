#!/usr/bin/env python3
"""The wind through a race, so everything that reads it agrees.

The scene file carries the whole series -- a direction and a speed every thirty
seconds -- rather than one figure for the race. For R9 Summer the mean was 162
degrees while the wind actually went from 122 to 196 and built from two knots
to eight, so a fleet trimmed to the mean was trimmed to a wind that was only
briefly true, and the shift that decided the race was not on screen at all.

Its own module because two very different things need it and neither can
import the other: build_scene.py runs inside Blender and sets the sails, and
overlay.py runs under the system Python with PIL and draws the readout. A third
copy of the lookup is a third chance for the picture and the numbers to
disagree.

The counterpart of ``core.replay3d.wind_at``, which the app uses on the same
file. Duplicated rather than imported because the render machine must not
import the app; the binning is fixed by the file format, so the two cannot
drift without the format changing.
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple


class Wind:
    """The wind through the race, so the fleet is trimmed to the wind of the moment.

    The scene used to carry one mean direction for the whole race, which for
    race 69 was 162 degrees while the wind actually went from 122 to 196 and
    built from two knots to eight. Every boat was therefore trimmed to a wind
    that was only briefly true, and the shift that decided the race was not on
    screen at all.

    The counterpart of ``core.replay3d.wind_at``, which the app uses on the same
    scene file. Duplicated rather than imported because the renderer must not
    import the app; the binning is fixed by the file format, so the two cannot
    drift without the format changing.
    """

    def __init__(self, wind: Optional[Dict[str, Any]]) -> None:
        wind = wind or {}
        series = wind.get("series") or {}
        self.twd_list = series.get("twd") or []
        self.tws_list = series.get("tws") or []
        self.step_s = max(1.0, float(series.get("step_s") or 1.0))
        self.mean_twd = wind.get("twd_deg")
        self.mean_tws = wind.get("tws_kn")

    @property
    def varies(self) -> bool:
        return len(self.twd_list) > 1

    def at(self, t_rel: float) -> Tuple[Optional[float], Optional[float]]:
        """Direction and speed at this moment, holding the last known over a gap."""
        if not self.twd_list:
            return self.mean_twd, self.mean_tws
        i = min(int(max(0.0, float(t_rel)) // self.step_s), len(self.twd_list) - 1)
        twd = next((self.twd_list[j] for j in range(i, -1, -1)
                    if self.twd_list[j] is not None), None)
        tws = next((self.tws_list[j] for j in range(min(i, len(self.tws_list) - 1), -1, -1)
                    if self.tws_list[j] is not None), None)
        return (twd if twd is not None else self.mean_twd,
                tws if tws is not None else self.mean_tws)

    def twa(self, t_rel: float, heading: float) -> Optional[float]:
        """True wind angle, positive with the wind on the starboard side."""
        twd, _tws = self.at(t_rel)
        if twd is None:
            return None
        return ((float(twd) - float(heading) + 180.0) % 360.0) - 180.0

    def range_text(self) -> str:
        known = [v for v in self.twd_list if v is not None]
        if len(known) < 2:
            return f"{self.mean_twd}\u00b0" if self.mean_twd is not None else "unknown"
        return f"{min(known):.0f}-{max(known):.0f}\u00b0 (mean {self.mean_twd:.0f}\u00b0)"
