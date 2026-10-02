# Copyright © 2026 CapeNet Ltd. All Rights Reserved.
"""A weather forecast from the internet, for the Virtual Race Officer to read.

The club's own instrument says what the wind is doing now; nothing in the app
said what it would do next, so "will it build during the race?" had no answer
but a guess. This fetches one, from the URL set in Settings -> Virtual Race
Officer, or -- with none set -- Open-Meteo's forecast at the start line.

Two kinds of source, told apart by what comes back rather than by a setting:

* **Hourly data** (Open-Meteo's JSON): wind direction, speed and gusts for
  every hour, which is what a race officer's question needs. Read as numbers,
  in knots whatever unit the URL asked for.
* **Anything else** is a web page, and is read for its words only. That is as
  much as a page gives to a fetch: windy.app's spot page, for one, carries a
  day-by-day summary written for kitesurfers, because its hourly forecast is
  drawn in the browser. The words are passed on as the page's own, never as the
  app's -- they are whatever the page says, instructions included.

Cached for half an hour per URL: a forecast changes every few hours, and the
hut's link is 4G shared with everything else.
"""
from __future__ import annotations

import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from html import unescape
from typing import Any, Dict, List, Optional

CACHE_S = 30 * 60
TIMEOUT_S = 8.0
MAX_BYTES = 800_000
TEXT_LIMIT = 4000
# The Met Office's own model, at the finest resolution Open-Meteo has for the UK.
DEFAULT_MODEL = "ukmo_seamless"
USER_AGENT = "PwllheliRaceOfficer/1.0 (+https://pro.pwllhelisailingclub.org)"

# Open-Meteo gives speeds in whatever unit was asked for; the app speaks knots.
_TO_KNOTS = {"kn": 1.0, "kt": 1.0, "km/h": 1 / 1.852, "m/s": 3600 / 1852, "mp/h": 0.868976,
             "mph": 0.868976}


class ForecastError(Exception):
    """A forecast that could not be had, with the reason in words."""


@dataclass
class Forecast:
    source: str                          # what to call it: "Open-Meteo (UK Met Office model)"
    url: str
    fetched_at: float
    hours: List[Dict[str, Any]] = field(default_factory=list)   # {"time", "twd", "tws", "gust"}
    text: str = ""                        # a web page's words, when it is not hourly data

    @property
    def hourly(self) -> bool:
        return bool(self.hours)


_CACHE: Dict[str, Forecast] = {}
_LOCK = threading.Lock()


def open_meteo_url(lat: float, lon: float, days: int = 3, model: str = DEFAULT_MODEL) -> str:
    """Open-Meteo's hourly wind at one position, in knots and local time."""
    query = urllib.parse.urlencode({
        "latitude": f"{lat:.4f}", "longitude": f"{lon:.4f}",
        "hourly": "wind_speed_10m,wind_direction_10m,wind_gusts_10m",
        "wind_speed_unit": "kn", "timezone": "Europe/London",
        "forecast_days": int(days), "models": model,
    })
    return f"https://api.open-meteo.com/v1/forecast?{query}"


def _describe(url: str) -> str:
    host = urllib.parse.urlparse(url).netloc.lower()
    if host.endswith("open-meteo.com"):
        model = urllib.parse.parse_qs(urllib.parse.urlparse(url).query).get("models", [""])[0]
        return "Open-Meteo" + (" (UK Met Office model)" if model.startswith("ukmo") else "")
    return host or url


def _hours_from_open_meteo(body: Dict[str, Any]) -> List[Dict[str, Any]]:
    hourly = body.get("hourly") or {}
    units = body.get("hourly_units") or {}
    times = hourly.get("time") or []
    speed_key = next((k for k in hourly if k.startswith("wind_speed")), "")
    dir_key = next((k for k in hourly if k.startswith("wind_direction")), "")
    gust_key = next((k for k in hourly if k.startswith("wind_gusts")), "")
    if not (times and speed_key and dir_key):
        return []
    factor = _TO_KNOTS.get(str(units.get(speed_key) or "kn").lower(), 1.0)
    gust_factor = _TO_KNOTS.get(str(units.get(gust_key) or "kn").lower(), 1.0)
    hours = []
    for i, stamp in enumerate(times):
        tws = (hourly.get(speed_key) or [None] * len(times))[i]
        twd = (hourly.get(dir_key) or [None] * len(times))[i]
        gust = (hourly.get(gust_key) or [None] * len(times))[i] if gust_key else None
        if tws is None or twd is None:
            continue
        hours.append({"time": str(stamp), "twd": round(float(twd)) % 360,
                      "tws": round(float(tws) * factor, 1),
                      "gust": round(float(gust) * gust_factor, 1) if gust is not None else None})
    return hours


def page_text(html: str, limit: int = TEXT_LIMIT) -> str:
    """The words a person would read on a page: no scripts, styles or markup."""
    text = re.sub(r"(?is)<(script|style|noscript|svg)\b.*?</\1>", " ", html)
    text = re.sub(r"(?s)<!--.*?-->", " ", text)
    text = re.sub(r"(?i)<br\s*/?>|</(p|div|li|h[1-6]|tr)>", "\n", text)
    text = unescape(re.sub(r"(?s)<[^>]+>", " ", text))
    lines = [" ".join(line.split()) for line in text.splitlines()]
    text = "\n".join(line for line in lines if line)
    return text[:limit].rstrip() + (" ..." if len(text) > limit else "")


def fetch(url: str, now: Optional[float] = None, opener=None) -> Forecast:
    """The forecast at `url`, from the cache when it is under half an hour old."""
    now = time.time() if now is None else now
    url = (url or "").strip()
    if not url.lower().startswith(("http://", "https://")):
        raise ForecastError("The forecast URL in Settings is not a web address.")
    with _LOCK:
        cached = _CACHE.get(url)
        if cached and now - cached.fetched_at < CACHE_S:
            return cached
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                                   "Accept": "application/json, text/html;q=0.9"})
    try:
        with (opener or urllib.request.urlopen)(request, timeout=TIMEOUT_S) as response:
            raw = response.read(MAX_BYTES + 1)
            kind = str(response.headers.get("Content-Type") or "")
    except urllib.error.HTTPError as exc:
        raise ForecastError(f"The forecast site answered {exc.code}.") from exc
    except (urllib.error.URLError, OSError, ValueError) as exc:
        reason = getattr(exc, "reason", exc)
        raise ForecastError(f"The forecast site could not be reached ({reason}).") from exc
    body_text = raw[:MAX_BYTES].decode("utf-8", errors="replace")
    forecast = Forecast(source=_describe(url), url=url, fetched_at=now)
    if "json" in kind.lower() or body_text.lstrip().startswith("{"):
        try:
            body = json.loads(body_text)
        except ValueError as exc:
            raise ForecastError("The forecast site sent something that was not a forecast.") from exc
        if isinstance(body, dict) and body.get("error"):
            raise ForecastError(f"The forecast site refused: {body.get('reason') or body['error']}.")
        forecast.hours = _hours_from_open_meteo(body if isinstance(body, dict) else {})
        if not forecast.hours:
            raise ForecastError("The forecast has no hourly wind in it.")
    else:
        forecast.text = page_text(body_text)
        if not forecast.text:
            raise ForecastError("The forecast page has no words in it that could be read.")
    with _LOCK:
        _CACHE[url] = forecast
    return forecast


def between(forecast: Forecast, start: datetime, end: datetime) -> List[Dict[str, Any]]:
    """The forecast hours from the one before `start` to the one after `end`."""
    stamps = [(datetime.fromisoformat(h["time"]), h) for h in forecast.hours]
    before = [s for s, _ in stamps if s <= start]
    first = max(before) if before else start
    after = [s for s, _ in stamps if s >= end]
    last = min(after) if after else end
    return [h for s, h in stamps if first <= s <= last]


def turn(a: float, b: float) -> float:
    """How far the wind has gone round from `a` to `b`: + clockwise (veering)."""
    return (float(b) - float(a) + 540.0) % 360.0 - 180.0
