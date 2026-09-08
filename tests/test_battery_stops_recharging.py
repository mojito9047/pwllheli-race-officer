"""The hut battery no longer getting back to full — the winter warning.

A solar hut does not fail suddenly. Nothing looks wrong on any one day: the
battery reads high, the sun comes up, the charger works. What changes is that
the bank stops quite reaching full, then reaches a little less each day, and by
the time the voltage is visibly low there are days rather than weeks in hand.

The club's array is **flat on the roof**, which at 52.9°N is close to the worst
case in December: the noon sun is 13.7° up and a flat panel is nearly edge-on to
it. Measured against the hut's 24 W standing load, a midwinter day — even a
clear one — does not replace what the night took. So the useful question is not
"is the battery low" but "has it stopped recovering", which is answerable weeks
earlier.

Fifty days of the club's real summer data reach 100% every single day, so the
warning must be silent through all of it. A card that cries wolf all summer is
one nobody reads in November.
"""
from __future__ import annotations

import time

import pytest

from core import power


def _samples(daily_peaks, per_day=48, start_day=0):
    """Rows shaped like the power table: one day per entry, peaking as given."""
    rows = []
    day_seconds = 86400
    now = time.time()
    for offset, peak in enumerate(daily_peaks):
        day_start = now - (len(daily_peaks) - 1 - offset + start_day) * day_seconds
        for i in range(per_day):
            # A crude day: falls overnight, rises to the day's peak by afternoon.
            frac = i / max(1, per_day - 1)
            soc = peak - (1 - frac) * 8
            rows.append((day_start + i * (day_seconds / per_day), soc))
    return rows


@pytest.fixture()
def store(monkeypatch, tmp_path):
    """A power database holding whatever daily peaks a test asks for."""
    import sqlite3

    def build(daily_peaks, per_day=48):
        path = tmp_path / "power_history.db"
        if path.exists():
            path.unlink()
        db = sqlite3.connect(path)
        db.execute("CREATE TABLE power_samples (id INTEGER PRIMARY KEY AUTOINCREMENT,"
                   " sample_time REAL, sample_iso TEXT, battery_soc REAL)")
        import datetime as dt
        for t, soc in _samples(daily_peaks, per_day):
            iso = dt.datetime.fromtimestamp(t).isoformat(timespec="seconds")
            db.execute("INSERT INTO power_samples (sample_time, sample_iso, battery_soc)"
                       " VALUES (?,?,?)", (t, iso, soc))
        db.commit()
        db.close()
        monkeypatch.setattr(power, "POWER_DB_PATH", path)
        monkeypatch.setattr(power, "POWER_DB_INITIALIZED", True)
        monkeypatch.setattr(power, "init_power_db", lambda: None)
        monkeypatch.setattr(power, "power_config", lambda: {"enabled": True})
    return build


class TestWhileTheSunKeepsUp:
    def test_a_summer_month_says_nothing(self, store):
        """Every day back to full, which is what fifty days of the club's real
        data actually look like."""
        store([100.0] * 30)
        assert power.recharge_warning() is None

    def test_one_dull_day_is_not_news(self, store):
        """It happens all summer. A card that fires on it is a card nobody reads
        by the time it matters."""
        store([100.0] * 20 + [96.0] + [100.0] * 3)
        assert power.recharge_warning() is None

    def test_two_dull_days_are_still_not(self, store):
        store([100.0] * 20 + [96.0, 95.0] + [100.0])
        assert power.recharge_warning() is None


class TestWhenItStopsRecovering:
    def test_three_days_short_of_full_is_the_signal(self, store):
        """The whole point: a trend, caught while the battery is still high."""
        store([100.0] * 15 + [97.0, 96.0, 95.0, 94.0])
        warning = power.recharge_warning()
        assert warning and warning["level"] == "watch"
        assert warning["days"] == 3
        assert warning["soc"] > 80          # still high — that is the value of it

    def test_a_week_of_it_is_urgent(self, store):
        store([100.0] * 10 + [96, 94, 92, 90, 88, 86, 84, 82])
        warning = power.recharge_warning()
        assert warning["level"] == "urgent" and warning["days"] >= 7

    def test_a_genuinely_low_bank_is_urgent_however_long(self, store):
        """Two days of collapse is not a gentle trend, and should not read as a
        watch item just because the streak is short."""
        store([100.0] * 20 + [58.0, 55.0])
        warning = power.recharge_warning()
        assert warning and warning["level"] == "urgent"

    def test_it_names_the_day_it_was_last_full(self, store):
        store([100.0] * 15 + [97.0, 96.0, 95.0, 94.0])
        assert power.recharge_warning()["last_full"]

    def test_recovering_clears_it(self, store):
        """A bright day after a dull spell resets the count, because the bank
        has demonstrably caught up."""
        store([100.0] * 10 + [95, 94, 93] + [100.0, 99.5])
        assert power.recharge_warning() is None


class TestItCannotBreakTheDashboard:
    def test_power_monitoring_off_says_nothing(self, store, monkeypatch):
        store([90.0] * 10)
        monkeypatch.setattr(power, "power_config", lambda: {"enabled": False})
        assert power.recharge_warning() is None

    def test_too_little_history_says_nothing(self, store):
        """Two days is not a trend. Silence beats a warning built on nothing."""
        store([95.0, 94.0], per_day=20)
        assert power.recharge_warning() is None

    def test_no_database_at_all_says_nothing(self, monkeypatch, tmp_path):
        monkeypatch.setattr(power, "POWER_DB_PATH", tmp_path / "missing.db")
        monkeypatch.setattr(power, "init_power_db", lambda: None)
        monkeypatch.setattr(power, "power_config", lambda: {"enabled": True})
        assert power.recharge_warning() is None

    def test_it_swallows_anything_underneath(self, monkeypatch):
        """This runs on the page the hut leaves open all day."""
        monkeypatch.setattr(power, "power_config", lambda: {"enabled": True})
        monkeypatch.setattr(power, "days_since_full",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
        assert power.recharge_warning() is None

    def test_today_alone_does_not_start_the_count(self, store):
        """Today may simply not have got there yet — it is mid-morning. Counting
        it would fire the warning every single morning."""
        store([100.0] * 20 + [82.0])
        assert power.recharge_warning() is None
