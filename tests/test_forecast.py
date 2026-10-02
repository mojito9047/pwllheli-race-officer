"""A weather forecast from the internet, for the Virtual Race Officer.

Nothing here touches the network: a stand-in opener returns what the forecast
site would have sent.
"""
from __future__ import annotations

import io
import json
from datetime import datetime

import pytest

from core import forecast
from core.forecast import Forecast, ForecastError, between, fetch, page_text, turn


class _Response(io.BytesIO):
    def __init__(self, body: bytes, kind: str):
        super().__init__(body)
        self.headers = {"Content-Type": kind}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _opener(body, kind="application/json", calls=None):
    def open_(request, timeout=None):
        if calls is not None:
            calls.append(request.full_url)
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        return _Response(data, kind)
    return open_


OPEN_METEO = {
    "hourly_units": {"time": "iso8601", "wind_speed_10m": "km/h", "wind_direction_10m": "°",
                     "wind_gusts_10m": "km/h"},
    "hourly": {"time": ["2026-09-26T10:00", "2026-09-26T11:00", "2026-09-26T12:00"],
               "wind_speed_10m": [18.52, 22.224, 25.928],
               "wind_direction_10m": [200, 215, 230],
               "wind_gusts_10m": [27.78, 33.336, 37.04]},
}


@pytest.fixture(autouse=True)
def an_empty_cache():
    forecast._CACHE.clear()
    yield
    forecast._CACHE.clear()


class TestHourlyData:
    def test_is_read_in_knots_whatever_it_was_asked_for_in(self):
        got = fetch("https://api.open-meteo.com/v1/forecast?x=1", opener=_opener(OPEN_METEO))
        assert got.hourly
        assert got.hours[0] == {"time": "2026-09-26T10:00", "twd": 200, "tws": 10.0, "gust": 15.0}
        assert got.hours[2]["tws"] == 14.0

    def test_the_hours_asked_about_include_the_hour_either_side(self):
        got = fetch("https://api.open-meteo.com/v1/forecast?x=1", opener=_opener(OPEN_METEO))
        hours = between(got, datetime(2026, 9, 26, 10, 30), datetime(2026, 9, 26, 11, 30))
        assert [h["time"][11:16] for h in hours] == ["10:00", "11:00", "12:00"]

    def test_a_half_hour_old_answer_is_used_again(self):
        calls = []
        url = "https://api.open-meteo.com/v1/forecast?x=1"
        fetch(url, now=1000.0, opener=_opener(OPEN_METEO, calls=calls))
        fetch(url, now=1000.0 + 29 * 60, opener=_opener(OPEN_METEO, calls=calls))
        assert len(calls) == 1
        fetch(url, now=1000.0 + 31 * 60, opener=_opener(OPEN_METEO, calls=calls))
        assert len(calls) == 2

    def test_json_with_no_wind_in_it_is_said_to_have_none(self):
        with pytest.raises(ForecastError, match="no hourly wind"):
            fetch("https://api.open-meteo.com/v1/forecast?x=2", opener=_opener({"hourly": {}}))


class TestAWebPage:
    def test_is_read_for_its_words_only(self):
        """windy.app's spot page: a daily summary in the markup, the hourly
        forecast drawn in the browser by script."""
        html = (b"<html><head><style>.x{}</style><script>var hourly=[1,2,3];</script></head>"
                b"<body><h1>Pwllheli Sailing Club</h1><p>Light wind &ndash; 5.9 m/s</p></body></html>")
        got = fetch("https://windy.app/forecast2/spot/313701/Pwllheli+Sailing+Club#alerts=sail",
                    opener=_opener(html, kind="text/html; charset=UTF-8"))
        assert not got.hourly
        assert got.text == "Pwllheli Sailing Club\nLight wind – 5.9 m/s"

    def test_a_long_page_is_cut_short(self):
        text = page_text("<p>" + "word " * 5000 + "</p>", limit=100)
        assert len(text) <= 104 and text.endswith(" ...")


class TestTheWindGoingRound:
    @pytest.mark.parametrize("a,b,swing", [(200, 230, 30), (230, 200, -30), (350, 10, 20),
                                           (10, 350, -20)])
    def test_veering_is_clockwise_and_backing_the_other_way(self, a, b, swing):
        assert turn(a, b) == swing


def test_something_that_is_not_a_web_address_is_refused():
    with pytest.raises(ForecastError, match="not a web address"):
        fetch("file:///etc/passwd")


def test_a_site_that_cannot_be_reached_says_so():
    import urllib.error

    def unreachable(request, timeout=None):
        raise urllib.error.URLError("no route to host")
    with pytest.raises(ForecastError, match="could not be reached"):
        fetch("https://api.open-meteo.com/v1/forecast?x=3", opener=unreachable)


def test_a_forecast_is_its_hours_or_its_words():
    assert Forecast(source="s", url="u", fetched_at=0.0, hours=[{"time": "t"}]).hourly
    assert not Forecast(source="s", url="u", fetched_at=0.0, text="words").hourly
