"""Tests for crack spread computations in app/services/downstream.py."""
from __future__ import annotations

import pytest

from app.services.downstream import (
    _BBL,
    _add_wow,
    _align_prices,
    compute_crack_spreads,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _series(periods: list[str], values: list[float]) -> list[dict]:
    """Build a minimal price series (no wow fields needed by compute functions)."""
    return [{"period": p, "value": v} for p, v in zip(periods, values)]


# Canonical single-period inputs with easy round numbers.
# WTI = $72/bbl; RBOB = $2.00/gal = $84/bbl; HO = $2.50/gal = $105/bbl
_WTI_VAL = 72.0
_RBOB_GAL = 2.00
_HO_GAL = 2.50
_RBOB_BBL = _RBOB_GAL * _BBL   # 84.0
_HO_BBL = _HO_GAL * _BBL       # 105.0

# Expected spreads
# 3-2-1 = (2×84 + 1×105 − 3×72) / 3 = (168 + 105 − 216) / 3 = 57/3 = 19.0
# RBOB crack = 84 − 72 = 12.0
# HO crack   = 105 − 72 = 33.0
_EXPECTED_321 = 19.0
_EXPECTED_RBOB = 12.0
_EXPECTED_HO = 33.0


def _single_period_input() -> tuple[list[dict], list[dict], list[dict]]:
    wti = _series(["2024-01-12"], [_WTI_VAL])
    rbob = _series(["2024-01-12"], [_RBOB_GAL])
    ho = _series(["2024-01-12"], [_HO_GAL])
    return wti, rbob, ho


# ---------------------------------------------------------------------------
# _add_wow
# ---------------------------------------------------------------------------

class TestAddWow:
    def test_adds_delta_relative_to_lookback(self) -> None:
        series = [
            {"period": "2024-01-12", "value": 25.0},
            {"period": "2024-01-11", "value": 20.0},
        ]
        result = _add_wow(series, lookback=1)
        assert result[0]["wow_change"] == 5.0
        assert result[0]["wow_pct_change"] == 25.0   # 5/20 * 100

    def test_oldest_entry_has_none_deltas(self) -> None:
        series = [
            {"period": "2024-01-12", "value": 25.0},
            {"period": "2024-01-11", "value": 20.0},
        ]
        result = _add_wow(series, lookback=1)
        assert result[1]["wow_change"] is None
        assert result[1]["wow_pct_change"] is None

    def test_lookback_of_7_requires_8_entries(self) -> None:
        series = [{"period": str(i), "value": float(i + 10)} for i in range(8)]
        result = _add_wow(series, lookback=7)
        # index 0 should compare to index 7
        assert result[0]["wow_change"] == series[0]["value"] - series[7]["value"]
        assert result[7]["wow_change"] is None

    def test_zero_previous_value_sets_pct_none(self) -> None:
        series = [
            {"period": "2024-01-12", "value": 5.0},
            {"period": "2024-01-11", "value": 0.0},
        ]
        _add_wow(series, lookback=1)
        assert series[0]["wow_pct_change"] is None

    def test_negative_change(self) -> None:
        series = [
            {"period": "2024-01-12", "value": 15.0},
            {"period": "2024-01-11", "value": 20.0},
        ]
        _add_wow(series, lookback=1)
        assert series[0]["wow_change"] == -5.0
        assert series[0]["wow_pct_change"] == -25.0


# ---------------------------------------------------------------------------
# _align_prices
# ---------------------------------------------------------------------------

class TestAlignPrices:
    def test_returns_only_common_periods(self) -> None:
        wti  = _series(["2024-01-12", "2024-01-11"], [72.0, 71.0])
        rbob = _series(["2024-01-12"],               [2.00])        # missing 01-11
        ho   = _series(["2024-01-12", "2024-01-11"], [2.50, 2.48])

        result = _align_prices(wti, rbob, ho)
        assert len(result) == 1
        assert result[0][0] == "2024-01-12"

    def test_preserves_wti_order(self) -> None:
        periods = ["2024-01-14", "2024-01-13", "2024-01-12"]
        wti  = _series(periods, [73.0, 72.5, 72.0])
        rbob = _series(periods, [2.10, 2.05, 2.00])
        ho   = _series(periods, [2.60, 2.55, 2.50])

        result = _align_prices(wti, rbob, ho)
        assert [r[0] for r in result] == periods

    def test_returns_correct_values(self) -> None:
        wti  = _series(["2024-01-12"], [72.0])
        rbob = _series(["2024-01-12"], [2.00])
        ho   = _series(["2024-01-12"], [2.50])

        period, w, r, h = _align_prices(wti, rbob, ho)[0]
        assert period == "2024-01-12"
        assert w == 72.0
        assert r == 2.00
        assert h == 2.50

    def test_empty_series_returns_empty(self) -> None:
        assert _align_prices([], [], []) == []


# ---------------------------------------------------------------------------
# compute_crack_spreads — formula correctness
# ---------------------------------------------------------------------------

class TestComputeCrackSpreadsFormula:
    def test_three_two_one_known_value(self) -> None:
        """3-2-1 = (2×RBOB_bbl + 1×HO_bbl − 3×WTI) / 3 = 19.0 with canonical inputs."""
        wti, rbob, ho = _single_period_input()
        result = compute_crack_spreads(wti, rbob, ho, wow_lookback=1)
        assert result["three_two_one"][0]["value"] == pytest.approx(_EXPECTED_321, abs=1e-4)

    def test_rbob_crack_known_value(self) -> None:
        """RBOB crack = RBOB_bbl − WTI = 84 − 72 = 12.0."""
        wti, rbob, ho = _single_period_input()
        result = compute_crack_spreads(wti, rbob, ho, wow_lookback=1)
        assert result["rbob_crack"][0]["value"] == pytest.approx(_EXPECTED_RBOB, abs=1e-4)

    def test_ho_crack_known_value(self) -> None:
        """HO crack = HO_bbl − WTI = 105 − 72 = 33.0."""
        wti, rbob, ho = _single_period_input()
        result = compute_crack_spreads(wti, rbob, ho, wow_lookback=1)
        assert result["ho_crack"][0]["value"] == pytest.approx(_EXPECTED_HO, abs=1e-4)

    def test_unit_conversion_applied(self) -> None:
        # If no * 42 were applied, RBOB crack would be 2.00 - 72 = -70, not 12.
        wti, rbob, ho = _single_period_input()
        result = compute_crack_spreads(wti, rbob, ho, wow_lookback=1)
        assert result["rbob_crack"][0]["value"] > 0  # confirms conversion happened

    def test_crack_spread_with_higher_crude(self) -> None:
        """Higher crude compresses spreads; verify direction of effect."""
        wti_hi = _series(["2024-01-12"], [90.0])   # $18 more expensive
        rbob   = _series(["2024-01-12"], [2.00])
        ho     = _series(["2024-01-12"], [2.50])
        result = compute_crack_spreads(wti_hi, rbob, ho, wow_lookback=1)
        assert result["three_two_one"][0]["value"] < _EXPECTED_321

    def test_crack_spread_with_lower_crude(self) -> None:
        """Lower crude widens spreads; verify direction of effect."""
        wti_lo = _series(["2024-01-12"], [54.0])
        rbob   = _series(["2024-01-12"], [2.00])
        ho     = _series(["2024-01-12"], [2.50])
        result = compute_crack_spreads(wti_lo, rbob, ho, wow_lookback=1)
        assert result["three_two_one"][0]["value"] > _EXPECTED_321


# ---------------------------------------------------------------------------
# compute_crack_spreads — WoW delta
# ---------------------------------------------------------------------------

class TestComputeCrackSpreadsWow:
    def _two_period_result(self, wti_new, wti_old, rbob_new, rbob_old, ho_new, ho_old):
        wti  = _series(["2024-01-12", "2024-01-11"], [wti_new, wti_old])
        rbob = _series(["2024-01-12", "2024-01-11"], [rbob_new, rbob_old])
        ho   = _series(["2024-01-12", "2024-01-11"], [ho_new, ho_old])
        return compute_crack_spreads(wti, rbob, ho, wow_lookback=1)

    def test_wow_change_computed_on_three_two_one(self) -> None:
        # period 1: 3-2-1 = 19.0 (canonical inputs)
        # period 0: widen spreads by raising rbob to $2.10/gal → RBOB_bbl = $88.20
        #   new 3-2-1 = (2×88.20 + 105 − 3×72) / 3 = (176.4 + 105 − 216)/3 = 65.4/3 = 21.8
        result = self._two_period_result(
            wti_new=72.0, wti_old=72.0,
            rbob_new=2.10, rbob_old=_RBOB_GAL,
            ho_new=_HO_GAL, ho_old=_HO_GAL,
        )
        new_val  = result["three_two_one"][0]["value"]
        prev_val = result["three_two_one"][1]["value"]
        assert result["three_two_one"][0]["wow_change"] == pytest.approx(new_val - prev_val, abs=1e-3)

    def test_wow_change_computed_on_rbob_crack(self) -> None:
        result = self._two_period_result(
            wti_new=72.0, wti_old=72.0,
            rbob_new=2.10, rbob_old=2.00,
            ho_new=_HO_GAL, ho_old=_HO_GAL,
        )
        # RBOB crack new = 2.10*42 − 72 = 88.2 − 72 = 16.2
        # RBOB crack old = 2.00*42 − 72 = 84.0 − 72 = 12.0
        # WoW = 16.2 − 12.0 = 4.2
        assert result["rbob_crack"][0]["wow_change"] == pytest.approx(4.2, abs=1e-3)

    def test_wow_change_computed_on_ho_crack(self) -> None:
        result = self._two_period_result(
            wti_new=72.0, wti_old=72.0,
            rbob_new=_RBOB_GAL, rbob_old=_RBOB_GAL,
            ho_new=2.60, ho_old=_HO_GAL,
        )
        # HO crack new = 2.60*42 − 72 = 109.2 − 72 = 37.2
        # HO crack old = 2.50*42 − 72 = 105.0 − 72 = 33.0
        # WoW = 37.2 − 33.0 = 4.2
        assert result["ho_crack"][0]["wow_change"] == pytest.approx(4.2, abs=1e-3)

    def test_wow_none_when_not_enough_history(self) -> None:
        wti, rbob, ho = _single_period_input()
        result = compute_crack_spreads(wti, rbob, ho, wow_lookback=1)
        assert result["three_two_one"][0]["wow_change"] is None
        assert result["rbob_crack"][0]["wow_change"] is None
        assert result["ho_crack"][0]["wow_change"] is None

    def test_wow_none_for_last_entry_in_longer_series(self) -> None:
        periods = [f"2024-01-{d:02d}" for d in range(12, 4, -1)]  # 8 periods, newest-first
        wti  = _series(periods, [72.0] * 8)
        rbob = _series(periods, [2.00] * 8)
        ho   = _series(periods, [2.50] * 8)
        result = compute_crack_spreads(wti, rbob, ho, weeks=2, wow_lookback=7)
        # Only index 0 can have a WoW (compared to index 7); index 1-7 get None
        assert result["three_two_one"][0]["wow_change"] is not None
        assert result["three_two_one"][1]["wow_change"] is None


# ---------------------------------------------------------------------------
# compute_crack_spreads — structural / edge cases
# ---------------------------------------------------------------------------

class TestComputeCrackSpreadsStructure:
    def test_returns_all_three_keys(self) -> None:
        wti, rbob, ho = _single_period_input()
        result = compute_crack_spreads(wti, rbob, ho, wow_lookback=1)
        assert set(result.keys()) == {"three_two_one", "rbob_crack", "ho_crack"}

    def test_empty_inputs_return_empty_series(self) -> None:
        result = compute_crack_spreads([], [], [], wow_lookback=1)
        assert result == {"three_two_one": [], "rbob_crack": [], "ho_crack": []}

    def test_result_is_newest_first(self) -> None:
        periods = ["2024-01-14", "2024-01-13", "2024-01-12"]
        wti  = _series(periods, [73.0, 72.5, 72.0])
        rbob = _series(periods, [2.10, 2.05, 2.00])
        ho   = _series(periods, [2.60, 2.55, 2.50])
        result = compute_crack_spreads(wti, rbob, ho, wow_lookback=1)
        actual_periods = [r["period"] for r in result["three_two_one"]]
        assert actual_periods == periods

    def test_weeks_parameter_limits_output(self) -> None:
        # 14 periods of data; weeks=1, lookback=7 → max 7 entries
        periods = [f"2024-01-{d:02d}" for d in range(14, 0, -1)]
        wti  = _series(periods, [72.0] * 14)
        rbob = _series(periods, [2.00] * 14)
        ho   = _series(periods, [2.50] * 14)
        result = compute_crack_spreads(wti, rbob, ho, weeks=1, wow_lookback=7)
        assert len(result["three_two_one"]) == 7

    def test_period_absent_from_one_series_is_excluded(self) -> None:
        wti  = _series(["2024-01-12", "2024-01-11"], [72.0, 71.0])
        rbob = _series(["2024-01-12"],               [2.00])          # gap on 01-11
        ho   = _series(["2024-01-12", "2024-01-11"], [2.50, 2.48])
        result = compute_crack_spreads(wti, rbob, ho, wow_lookback=1)
        assert len(result["three_two_one"]) == 1
        assert result["three_two_one"][0]["period"] == "2024-01-12"

    def test_each_entry_has_all_four_fields(self) -> None:
        wti, rbob, ho = _single_period_input()
        result = compute_crack_spreads(wti, rbob, ho, wow_lookback=1)
        entry = result["three_two_one"][0]
        assert {"period", "value", "wow_change", "wow_pct_change"} == set(entry.keys())
