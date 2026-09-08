"""The wind burned into the live stream, and when it refuses to say anything.

The relay already re-encodes the camera to add the club and sponsor logos, so a
TWD/TWS/gust readout across the top is one more filter on a pipeline that exists.
ffmpeg re-reads the text file while it runs, so a small updater rewrites one file
and the picture follows without restarting the encode.

**The risk is not the drawing, it is the staleness.** A file-backed readout keeps
showing whatever it was last given. If the hut link drops, the stream would carry
on displaying a wind from twenty minutes ago, burned into live-looking footage,
with nothing on screen to say so — and people sail on it. So most of what follows
is about the readout going *blank* rather than going wrong: no sample, an old
sample, a half-filled sample, a clock that disagrees.

These are the relay's files rather than the app's, run on a different host and
deployed by hand, which is all the more reason for the pure parts to be tested
here where the suite actually runs.
"""
from __future__ import annotations

import importlib.util
import os
import time

import pytest

_RELAY = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "deploy", "live_stream", "relay_wind.py")
_spec = importlib.util.spec_from_file_location("relay_wind", _RELAY)
relay_wind = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(relay_wind)


def _sample(twd=245, tws=12.4, gust=18.1, age=0.0):
    return {"t": time.time() - age, "twd": twd, "tws": tws, "gust": gust,
            "source": "station"}


class TestWhatItPaints:
    def test_the_three_numbers_asked_for(self):
        assert relay_wind.format_wind(_sample()) == "TWD 245   TWS 12.4 kn   Gust 18.1 kn"

    def test_a_bearing_is_always_three_digits(self):
        """7 degrees reads 007. On a compass a bare 7 is ambiguous at a glance,
        and this is being read off a moving picture."""
        assert relay_wind.format_wind(_sample(twd=7)).startswith("TWD 007")

    def test_a_bearing_wraps_rather_than_reading_360(self):
        assert relay_wind.format_wind(_sample(twd=360)).startswith("TWD 000")
        assert relay_wind.format_wind(_sample(twd=371)).startswith("TWD 011")

    def test_no_gust_drops_the_gust_and_keeps_the_rest(self):
        """Plenty of stations do not report one, and "Gust --" is just noise."""
        out = relay_wind.format_wind(_sample(gust=None))
        assert out == "TWD 245   TWS 12.4 kn"

    def test_speeds_keep_one_decimal(self):
        assert "TWS 4.0 kn" in relay_wind.format_wind(_sample(tws=4))


class TestWhenItSaysNothing:
    """Blank is the honest answer. An empty file draws an empty string, so the
    picture simply stops claiming to know the wind."""

    def test_an_old_sample_is_not_shown(self):
        assert relay_wind.format_wind(_sample(age=600)) == ""

    def test_a_fresh_sample_is(self):
        assert relay_wind.format_wind(_sample(age=10)) != ""

    def test_the_boundary_is_where_it_says(self):
        assert relay_wind.format_wind(_sample(age=119), stale_after=120) != ""
        assert relay_wind.format_wind(_sample(age=121), stale_after=120) == ""

    def test_a_sample_from_the_future_is_refused(self):
        """Clocks disagreeing means the age cannot be reasoned about at all, in
        either direction — so it is not treated as very fresh."""
        assert relay_wind.format_wind(_sample(age=-3600)) == ""

    def test_no_direction_is_not_a_wind_readout(self):
        assert relay_wind.format_wind(_sample(twd=None)) == ""

    def test_no_speed_is_not_either(self):
        assert relay_wind.format_wind(_sample(tws=None)) == ""

    def test_rubbish_in_nothing_out(self):
        for value in (None, {}, [], "wind", {"twd": "north", "tws": "brisk"},
                      {"twd": 245, "tws": 12.4, "t": "not a time"}):
            assert relay_wind.format_wind(value) == ""

    def test_a_sample_with_no_timestamp_is_still_shown(self):
        """Manual wind entered in Settings has no station stamp. It is what the
        race officer typed, so it is current by definition."""
        assert relay_wind.format_wind({"twd": 245, "tws": 12.4}) != ""


class TestFetchingIt:
    def test_it_reads_the_current_sample_out_of_the_reply(self, monkeypatch):
        payload = b'{"ok": true, "current": {"twd": 245, "tws": 12.4, "gust": 18.1}}'

        class FakeResponse:
            def read(self): return payload
            def __enter__(self): return self
            def __exit__(self, *a): return False

        monkeypatch.setattr(relay_wind.urllib.request, "urlopen",
                            lambda *a, **k: FakeResponse())
        assert relay_wind.fetch_wind("http://hut/x")["twd"] == 245

    def test_it_identifies_itself_like_the_manifest_fetch_beside_it(self, monkeypatch):
        """Cloudflare's bot rules 403 the default python-urllib User-Agent, so
        curl succeeds from the relay while urllib gets nothing — and the failure
        is silent, looking exactly like a station with no wind. The manifest
        fetch in relay_branded_source.py carries a User-Agent for this reason;
        this one was written without and drew a blank readout on a live stream."""
        seen = {}

        class FakeResponse:
            def read(self): return b'{"current": {"twd": 164, "tws": 13.6}}'
            def __enter__(self): return self
            def __exit__(self, *a): return False

        def fake(req, timeout=None):
            seen["ua"] = req.get_header("User-agent")
            return FakeResponse()

        monkeypatch.setattr(relay_wind.urllib.request, "urlopen", fake)
        relay_wind.fetch_wind("http://hut/x")
        assert seen["ua"] and "urllib" not in seen["ua"].lower()

    def test_the_hut_being_unreachable_is_not_an_exception(self, monkeypatch):
        def boom(*a, **k):
            raise OSError("no route to host")
        monkeypatch.setattr(relay_wind.urllib.request, "urlopen", boom)
        assert relay_wind.fetch_wind("http://hut/x") is None

    def test_a_reply_that_is_not_json_is_survived(self, monkeypatch):
        class FakeResponse:
            def read(self): return b"<html>nope</html>"
            def __enter__(self): return self
            def __exit__(self, *a): return False
        monkeypatch.setattr(relay_wind.urllib.request, "urlopen",
                            lambda *a, **k: FakeResponse())
        assert relay_wind.fetch_wind("http://hut/x") is None

    def test_the_hut_going_away_blanks_the_file(self, tmp_path, monkeypatch):
        """The whole point. A dropped link must clear the readout, not freeze it
        at the last good value."""
        path = str(tmp_path / "wind.txt")
        monkeypatch.setattr(relay_wind, "fetch_wind", lambda url=None, timeout=4.0: _sample())
        assert relay_wind.write_once(path) != ""
        assert open(path, encoding="utf-8").read() != ""
        monkeypatch.setattr(relay_wind, "fetch_wind", lambda url=None, timeout=4.0: None)
        assert relay_wind.write_once(path) == ""
        assert open(path, encoding="utf-8").read() == ""

    def test_the_file_is_replaced_atomically(self, tmp_path, monkeypatch):
        """ffmpeg re-reads this file constantly and must never catch it
        half-written."""
        path = tmp_path / "wind.txt"
        monkeypatch.setattr(relay_wind, "fetch_wind", lambda url=None, timeout=4.0: _sample())
        relay_wind.write_once(str(path))
        assert path.exists() and not (tmp_path / "wind.txt.tmp").exists()

    def test_an_unwritable_path_does_not_raise(self, monkeypatch):
        monkeypatch.setattr(relay_wind, "fetch_wind", lambda url=None, timeout=4.0: _sample())
        relay_wind.write_once(os.path.join("no", "such", "directory", "wind.txt"))


class TestTheFilter:
    def test_it_is_centred_at_the_top(self):
        chain = relay_wind.drawtext_chain("base", "vwind", "/tmp/w.txt", "/f.ttf", 720, 16)
        assert "x=(w-text_w)/2" in chain and ":y=16" in chain

    def test_it_reloads_while_ffmpeg_runs(self):
        """Without this the readout would be frozen at whatever the file held
        when the stream started."""
        assert "reload=1" in relay_wind.drawtext_chain(
            "base", "vwind", "/tmp/w.txt", "/f.ttf", 720, 16)

    def test_it_reads_the_text_from_the_file_not_the_command(self):
        """Text baked into the filtergraph could not change, and would need
        escaping against a filtergraph parser that treats commas and colons as
        syntax."""
        chain = relay_wind.drawtext_chain("base", "vwind", "/tmp/w.txt", "/f.ttf", 720, 16)
        assert "textfile=" in chain
        # A literal ":text=" option would be the baked-in kind. ("drawtext=" and
        # "textfile=" both contain "text=", so match the option separator.)
        assert ":text=" not in chain

    def test_the_path_colon_is_escaped(self):
        """A Windows-style path would otherwise end the option early. The relay
        is Linux, but the escaping is what stops a surprise being silent."""
        chain = relay_wind.drawtext_chain("base", "vwind", r"C:\tmp\w.txt", r"D:\f.ttf", 720, 16)
        assert "C\\:/tmp/w.txt" in chain
        # The font path goes through the same parser and needs the same care.
        assert "D\\:/f.ttf" in chain

    def test_it_joins_the_chain_it_was_given(self):
        chain = relay_wind.drawtext_chain("v3", "vwind", "/tmp/w.txt", "/f.ttf", 720, 16)
        assert chain.startswith("[v3]") and chain.endswith("[vwind]")

    def test_the_text_is_outlined_rather_than_boxed(self):
        """White alone vanishes against the pale overcast sky this camera mostly
        looks at, so it needs backing — but a box puts a caption bar across the
        picture. An outline reads as well without the slab."""
        chain = relay_wind.drawtext_chain("b", "o", "/w.txt", "/f.ttf", 1080, 24)
        # ":borderw=", not "borderw=" — "boxborderw=" contains the latter.
        assert ":borderw=" in chain and "bordercolor=" in chain
        assert "box=1" not in chain

    def test_the_box_is_still_available(self, monkeypatch):
        monkeypatch.setattr(relay_wind, "BOX_BEHIND_TEXT", True)
        chain = relay_wind.drawtext_chain("b", "o", "/w.txt", "/f.ttf", 1080, 24)
        assert "box=1" in chain and ":borderw=" not in chain

    def test_the_outline_scales_with_the_text(self, monkeypatch):
        """A one-pixel outline round 48px text would not be visible at all."""
        thin = relay_wind.drawtext_chain("b", "o", "/w.txt", "/f.ttf", 360, 8)
        thick = relay_wind.drawtext_chain("b", "o", "/w.txt", "/f.ttf", 2160, 48)
        width = lambda c: int(c.split(":borderw=")[1].split(":")[0])
        assert width(thin) < width(thick)

    def test_the_default_size_is_the_one_that_looked_right(self):
        """Halved after seeing it on the real stream: 48px at 1080p read as a
        banner across the picture rather than a readout in the corner of it."""
        chain = relay_wind.drawtext_chain("b", "o", "/w.txt", "/f.ttf", 1080, 24)
        assert int(chain.split("fontsize=")[1].split(":")[0]) == 24

    def test_the_size_can_be_tuned_without_a_code_change(self, monkeypatch):
        """It is a matter of taste on a screen nobody developing this is looking
        at, and every adjustment otherwise costs a copy and a restart."""
        monkeypatch.setattr(relay_wind, "FONT_SCALE", 0.05)
        chain = relay_wind.drawtext_chain("b", "o", "/w.txt", "/f.ttf", 1080, 24)
        assert int(chain.split("fontsize=")[1].split(":")[0]) == 54

    def test_the_text_scales_with_the_frame(self):
        small = relay_wind.drawtext_chain("b", "o", "/w.txt", "/f.ttf", 360, 8)
        large = relay_wind.drawtext_chain("b", "o", "/w.txt", "/f.ttf", 1080, 24)
        size = lambda c: int(c.split("fontsize=")[1].split(":")[0])
        assert size(small) < size(large)


class TestTheJournalSaysWhichItIs:
    """Deployed, restarted, no readout on screen and nothing in the journal — and
    no way to tell "switched off" from "started and died" from "working".

    It logged only on failure, so silence carried three meanings. One positive
    line makes silence mean off, which is what a log is for.
    """

    def test_starting_the_updater_announces_itself(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(relay_wind, "fetch_wind", lambda url=None, timeout=4.0: _sample())
        # Do not actually fork in a test run.
        monkeypatch.setattr(relay_wind.os, "fork", lambda: 1234, raising=False)
        relay_wind.start_writer(str(tmp_path / "wind.txt"), url="http://hut/x")
        err = capsys.readouterr().err
        assert "wind: readout on" in err
        assert "http://hut/x" in err
        assert "TWD 245" in err          # the value it will paint, to compare on screen

    def test_it_says_so_even_when_there_is_no_wind_yet(self, tmp_path, monkeypatch, capsys):
        """The awkward case: enabled and working, but nothing to draw. Without a
        line here that looks exactly like not enabled."""
        monkeypatch.setattr(relay_wind, "fetch_wind", lambda url=None, timeout=4.0: None)
        monkeypatch.setattr(relay_wind.os, "fork", lambda: 1234, raising=False)
        relay_wind.start_writer(str(tmp_path / "wind.txt"))
        err = capsys.readouterr().err
        assert "wind: readout on" in err and "no wind yet" in err

    def test_a_host_that_cannot_fork_says_so_and_carries_on(self, tmp_path, monkeypatch, capsys):
        """The updater is a forked child, which Windows has no equivalent of.
        The relay is Linux, but a host that cannot fork must get one readout and
        a warning rather than a traceback that takes the stream down."""
        monkeypatch.setattr(relay_wind, "fetch_wind", lambda url=None, timeout=4.0: _sample())
        def no_fork():
            raise AttributeError("module 'os' has no attribute 'fork'")
        monkeypatch.setattr(relay_wind.os, "fork", no_fork, raising=False)
        path = tmp_path / "wind.txt"
        assert relay_wind.start_writer(str(path)) is None
        assert "TWD 245" in path.read_text(encoding="utf-8")   # the one value still landed
        assert "will not refresh" in capsys.readouterr().err


class TestItIsOffUnlessAskedFor:
    def test_no_font_no_readout(self):
        """A missing font disables it rather than letting ffmpeg fail on a filter
        it cannot build — the stream matters more than the numbers."""
        assert relay_wind.find_font(candidates=("/no/such/font.ttf",)) is None

    def test_the_first_font_present_wins(self, tmp_path):
        real = tmp_path / "DejaVuSans.ttf"
        real.write_bytes(b"not really a font")
        assert relay_wind.find_font(candidates=("/no/such.ttf", str(real))) == str(real)
