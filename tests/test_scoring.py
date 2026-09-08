"""Tests for race-result and series-scoring functions."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import app as ro


# ---------------------------------------------------------------------------
# corrected_seconds_for_result
# ---------------------------------------------------------------------------

class TestCorrectedSecondsForResult:
    def test_irc_formula(self):
        # IRC: corrected = elapsed × TCC
        result = ro.corrected_seconds_for_result(3600, 1.050, "IRC")
        assert result == pytest.approx(3780.0)

    def test_ytc_formula(self):
        # YTC: corrected = elapsed × 1000 / YTC number
        result = ro.corrected_seconds_for_result(3600, 720, "YTC")
        assert result == pytest.approx(5000.0)

    def test_irc_case_insensitive(self):
        upper = ro.corrected_seconds_for_result(3600, 1.050, "IRC")
        lower = ro.corrected_seconds_for_result(3600, 1.050, "irc")
        assert upper == pytest.approx(lower)

    def test_none_elapsed_returns_none(self):
        assert ro.corrected_seconds_for_result(None, 1.050, "IRC") is None

    def test_none_rating_returns_none(self):
        assert ro.corrected_seconds_for_result(3600, None, "IRC") is None

    def test_both_none_returns_none(self):
        assert ro.corrected_seconds_for_result(None, None, "IRC") is None

    def test_irc_rating_above_1_slows_corrected_time(self):
        # TCC > 1 means slower on corrected time (larger is less favourable)
        t_fast = ro.corrected_seconds_for_result(3600, 0.9, "IRC")
        t_slow = ro.corrected_seconds_for_result(3600, 1.1, "IRC")
        assert t_fast < t_slow

    def test_ytc_higher_number_means_faster_corrected(self):
        # Higher YTC (handicap) divides elapsed by a bigger number → shorter corrected time
        t_high = ro.corrected_seconds_for_result(3600, 900, "YTC")
        t_low = ro.corrected_seconds_for_result(3600, 600, "YTC")
        assert t_high < t_low


# ---------------------------------------------------------------------------
# rounded_seconds_for_scoring
# ---------------------------------------------------------------------------

class TestRoundedSecondsForScoring:
    def test_none_returns_none(self):
        assert ro.rounded_seconds_for_scoring(None) is None

    def test_exact_integer(self):
        assert ro.rounded_seconds_for_scoring(3600.0) == 3600

    def test_rounds_down(self):
        assert ro.rounded_seconds_for_scoring(3600.4) == 3600

    def test_rounds_up(self):
        assert ro.rounded_seconds_for_scoring(3600.6) == 3601

    def test_returns_int(self):
        assert isinstance(ro.rounded_seconds_for_scoring(3600.0), int)

    def test_small_values(self):
        assert ro.rounded_seconds_for_scoring(0.0) == 0
        assert ro.rounded_seconds_for_scoring(1.9) == 2


# ---------------------------------------------------------------------------
# assign_low_point_race_ranks
# ---------------------------------------------------------------------------

class TestAssignLowPointRaceRanks:

    @staticmethod
    def _row(name: str, corrected: float | None) -> dict:
        return {"corrected_seconds": corrected, "entry": {"boat_name": name}}

    def test_ascending_ranking(self):
        rows = [
            self._row("C", 3900),
            self._row("A", 3600),
            self._row("B", 3750),
        ]
        ro.assign_low_point_race_ranks(rows)
        by = {r["entry"]["boat_name"]: r for r in rows}
        assert by["A"]["rank"] == 1
        assert by["B"]["rank"] == 2
        assert by["C"]["rank"] == 3

    def test_ranking_points_equal_rank_for_no_ties(self):
        rows = [self._row("A", 3600), self._row("B", 3700)]
        ro.assign_low_point_race_ranks(rows)
        by = {r["entry"]["boat_name"]: r for r in rows}
        assert by["A"]["points"] == pytest.approx(1.0)
        assert by["B"]["points"] == pytest.approx(2.0)

    def test_tie_shares_average_points(self):
        # A and B have equal corrected time → tied 1st/2nd → each scores (1+2)/2 = 1.5
        rows = [
            self._row("A", 3600),
            self._row("B", 3600),
            self._row("C", 3900),
        ]
        ro.assign_low_point_race_ranks(rows)
        by = {r["entry"]["boat_name"]: r for r in rows}
        assert by["A"]["points"] == pytest.approx(1.5)
        assert by["B"]["points"] == pytest.approx(1.5)
        assert by["A"]["tie"] is True
        assert by["C"]["rank"] == 3
        assert by["C"]["tie"] is False

    def test_dnf_boat_excluded_from_ranking(self):
        rows = [self._row("Finisher", 3600), self._row("DNF", None)]
        ro.assign_low_point_race_ranks(rows)
        by = {r["entry"]["boat_name"]: r for r in rows}
        assert by["Finisher"]["rank"] == 1
        assert "rank" not in by["DNF"]

    def test_single_finisher_gets_rank_1(self):
        rows = [self._row("Solo", 3600)]
        ro.assign_low_point_race_ranks(rows)
        assert rows[0]["rank"] == 1
        assert rows[0]["points"] == pytest.approx(1.0)

    def test_rank_text_is_string(self):
        rows = [self._row("Boat", 3600)]
        ro.assign_low_point_race_ranks(rows)
        assert rows[0]["rank_text"] == "1"

    def test_empty_list_does_not_raise(self):
        ro.assign_low_point_race_ranks([])

    def test_all_dnf_no_ranks_assigned(self):
        rows = [self._row("X", None), self._row("Y", None)]
        ro.assign_low_point_race_ranks(rows)
        assert "rank" not in rows[0]
        assert "rank" not in rows[1]

    def test_tie_detection_uses_rounded_seconds(self):
        # 3600.1 and 3600.4 both round to 3600 → tied despite different raw values
        rows = [self._row("A", 3600.1), self._row("B", 3600.4)]
        ro.assign_low_point_race_ranks(rows)
        assert rows[0]["tie"] is True
        assert rows[1]["tie"] is True


# ---------------------------------------------------------------------------
# parse_discard_profile
# ---------------------------------------------------------------------------

class TestParseDiscardProfile:
    def test_valid_profile(self):
        values, errors = ro.parse_discard_profile("0,0,1,1,2")
        assert values == [0, 0, 1, 1, 2]
        assert errors == []

    def test_empty_string_returns_default(self):
        values, errors = ro.parse_discard_profile("")
        assert values == [0, 0, 1, 1, 1, 1, 2, 2, 2]
        assert errors == []

    def test_none_returns_default(self):
        values, _errors = ro.parse_discard_profile(None)
        assert len(values) > 0

    def test_single_zero(self):
        values, errors = ro.parse_discard_profile("0")
        assert values == [0]
        assert errors == []

    def test_negative_value_produces_error(self):
        _values, errors = ro.parse_discard_profile("0,-1,1")
        assert any("negative" in e.lower() for e in errors)

    def test_non_integer_produces_error(self):
        _values, errors = ro.parse_discard_profile("0,x,1")
        assert any("whole number" in e.lower() for e in errors)

    def test_blank_item_produces_error(self):
        _values, errors = ro.parse_discard_profile("0,,1")
        assert any("blank" in e.lower() for e in errors)

    def test_all_invalid_falls_back_to_single_zero(self):
        values, errors = ro.parse_discard_profile("x,y,z")
        assert values == [0]
        assert len(errors) == 3


# ---------------------------------------------------------------------------
# series_discards_for_race_count
# ---------------------------------------------------------------------------

class TestSeriesDiscardsForRaceCount:
    # Default profile: 0,0,1,1,1,1,2,2,2

    def test_zero_races_gives_zero_discards(self):
        assert ro.series_discards_for_race_count(0) == 0

    def test_negative_race_count_gives_zero(self):
        assert ro.series_discards_for_race_count(-5) == 0

    def test_first_two_races_no_discard(self):
        assert ro.series_discards_for_race_count(1) == 0
        assert ro.series_discards_for_race_count(2) == 0

    def test_three_races_one_discard(self):
        assert ro.series_discards_for_race_count(3) == 1

    def test_seven_races_two_discards(self):
        assert ro.series_discards_for_race_count(7) == 2

    def test_beyond_profile_length_uses_last_value(self):
        # Profile has 9 entries; last value is 2
        assert ro.series_discards_for_race_count(20) == 2
        assert ro.series_discards_for_race_count(100) == 2


# ---------------------------------------------------------------------------
# choose_discard_indexes
# ---------------------------------------------------------------------------

class TestChooseDiscardIndexes:

    @staticmethod
    def _score(points: float, code: str = "") -> dict:
        return {"points": points, "code": code}

    def test_zero_discards_returns_empty_set(self):
        scores = [self._score(1), self._score(2), self._score(3)]
        assert ro.choose_discard_indexes(scores, 0) == set()

    def test_highest_point_score_discarded(self):
        scores = [self._score(1), self._score(3), self._score(2)]
        discards = ro.choose_discard_indexes(scores, 1)
        assert discards == {1}  # score at index 1 has highest points

    def test_two_discards(self):
        scores = [self._score(1), self._score(4), self._score(3), self._score(2)]
        discards = ro.choose_discard_indexes(scores, 2)
        assert discards == {1, 2}  # 4 and 3 points discarded

    def test_cannot_discard_all_scores(self):
        # Must retain at least one score in the total
        scores = [self._score(1), self._score(2)]
        discards = ro.choose_discard_indexes(scores, 10)
        assert len(discards) <= 1

    def test_dne_is_not_eligible_for_discard(self):
        # DNE (Disqualification Not Excludable) must never be discarded
        scores = [self._score(100, "DNE"), self._score(2), self._score(3)]
        discards = ro.choose_discard_indexes(scores, 1)
        assert 0 not in discards

    def test_dgm_is_not_eligible_for_discard(self):
        scores = [self._score(100, "DGM"), self._score(2), self._score(3)]
        discards = ro.choose_discard_indexes(scores, 1)
        assert 0 not in discards

    def test_equal_scores_earlier_race_discarded_first(self):
        # Scores at index 1 and 2 are equal; the earlier one (index 1) is discarded
        scores = [self._score(1), self._score(5), self._score(5)]
        discards = ro.choose_discard_indexes(scores, 1)
        assert discards == {1}

    def test_empty_scores_returns_empty_set(self):
        assert ro.choose_discard_indexes([], 1) == set()
