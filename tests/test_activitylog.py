"""Tests for the daily-rotating activity/audit log."""
from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import activitylog, appstate, logfiles  # noqa: E402


def _reset_logger():
    logging.getLogger("pwllheli.activity").handlers.clear()
    activitylog._LOGGER = None


def test_log_activity_writes_to_runtime_logs(tmp_path, monkeypatch):
    _reset_logger()
    monkeypatch.setattr(appstate, "RUNTIME_DIR", tmp_path)
    try:
        activitylog.log_activity("race created", "#1 'Club Race'", user="admin")
        for handler in logging.getLogger("pwllheli.activity").handlers:
            handler.flush()
        log_file = logfiles.dated_path(tmp_path / "logs", "activity", logfiles.today())
        assert log_file.exists()
        text = log_file.read_text(encoding="utf-8")
        assert "user=admin" in text
        assert "race created" in text
        assert "Club Race" in text
    finally:
        _reset_logger()
