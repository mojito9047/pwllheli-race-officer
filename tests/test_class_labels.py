"""Rating-band class labels shown on the entries tables (core.classconfig)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import classconfig as cc  # noqa: E402

# ISORA-style bands: IRC0 r>1.07, IRC1 1.01<r<=1.069, IRC2 r<=1.009.
IRC_CFG = {"classes": [
    {"name": "IRC0", "rating_type": "IRC", "min": 1.07, "max": None, "min_inclusive": False, "max_inclusive": False},
    {"name": "IRC1", "rating_type": "IRC", "min": 1.01, "max": 1.069, "min_inclusive": False, "max_inclusive": True},
    {"name": "IRC2", "rating_type": "IRC", "min": None, "max": 1.009, "min_inclusive": False, "max_inclusive": True},
]}


def test_label_by_irc_band():
    assert cc.entry_rating_class_label(IRC_CFG, 1.084, None) == "IRC0"
    assert cc.entry_rating_class_label(IRC_CFG, 1.149, None) == "IRC0"
    assert cc.entry_rating_class_label(IRC_CFG, 1.069, None) == "IRC1"   # upper inclusive
    assert cc.entry_rating_class_label(IRC_CFG, 1.014, None) == "IRC1"
    assert cc.entry_rating_class_label(IRC_CFG, 1.009, None) == "IRC2"   # upper inclusive
    assert cc.entry_rating_class_label(IRC_CFG, 0.925, None) == "IRC2"


def test_no_rating_or_no_match():
    assert cc.entry_rating_class_label(IRC_CFG, None, None) == ""
    # YTC rating is ignored because no YTC bands are configured.
    assert cc.entry_rating_class_label(IRC_CFG, None, 850) == ""


def test_dual_bands_join():
    cfg = {"classes": IRC_CFG["classes"] + [
        {"name": "YTC1", "rating_type": "YTC", "min": 800, "max": 900, "min_inclusive": True, "max_inclusive": True},
    ]}
    assert cc.entry_rating_class_label(cfg, 1.084, 850) == "IRC0 / YTC1"


def test_labels_map():
    ents = [
        {"id": 1, "manual_irc_rating": 1.084, "manual_ytc_rating": None},
        {"id": 2, "manual_irc_rating": 1.018, "manual_ytc_rating": None},
        {"id": 3, "manual_irc_rating": 0.956, "manual_ytc_rating": None},
    ]
    assert cc.entry_class_labels_map(IRC_CFG, ents) == {1: "IRC0", 2: "IRC1", 3: "IRC2"}


def test_labels_map_empty_without_bands():
    ents = [{"id": 1, "manual_irc_rating": 1.084, "manual_ytc_rating": None}]
    assert cc.entry_class_labels_map({"classes": []}, ents) == {}
