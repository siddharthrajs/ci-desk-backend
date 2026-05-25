"""
Downstream crack spread computations.

Pure functions operate on price data already fetched from EIAService.get_spot_prices().

Unit convention (EIA):
  WTI          — $/barrel
  RBOB gasoline — $/gallon  →  multiply by 42 to convert to $/barrel
  Heating oil   — $/gallon  →  multiply by 42 to convert to $/barrel

Crack spread formulas (all values in $/barrel after conversion):
  3-2-1 crack  = (2×RBOB_bbl + 1×HO_bbl  − 3×WTI) / 3
  RBOB crack   =  RBOB_bbl − WTI
  HO crack     =  HO_bbl   − WTI
"""

from __future__ import annotations

from typing import Any

# Barrel-to-gallon conversion factor
_BBL = 42


def _index_by_period(series: list[dict[str, Any]]) -> dict[str, float]:
    """Build a {period: value} lookup from a price series."""
    return {row["period"]: row["value"] for row in series if row.get("value") is not None}


def _align_prices(
    wti: list[dict[str, Any]],
    rbob: list[dict[str, Any]],
    heating_oil: list[dict[str, Any]],
) -> list[tuple[str, float, float, float]]:
    """Return (period, wti, rbob, ho) tuples for periods present in all three series.

    Preserves the ordering of the wti series (newest-first from EIA).
    Periods missing from any one series are excluded — gaps in EIA data do occur.
    """
    rbob_idx = _index_by_period(rbob)
    ho_idx = _index_by_period(heating_oil)
    aligned = []
    for row in wti:
        p = row["period"]
        if p in rbob_idx and p in ho_idx:
            aligned.append((p, row["value"], rbob_idx[p], ho_idx[p]))
    return aligned


def _add_wow(
    series: list[dict[str, Any]],
    lookback: int,
) -> list[dict[str, Any]]:
    """Attach wow_change / wow_pct_change to each entry by comparing to `lookback` periods prior.

    For daily spot-price data the caller passes lookback=7 (same weekday, prior week).
    Entries without enough history get None for both delta fields.
    """
    for i, item in enumerate(series):
        prev_idx = i + lookback
        if prev_idx < len(series):
            prev = series[prev_idx]["value"]
            delta = item["value"] - prev
            item["wow_change"] = round(delta, 4)
            item["wow_pct_change"] = round(delta / prev * 100, 2) if prev else None
        else:
            item["wow_change"] = None
            item["wow_pct_change"] = None
    return series


def compute_crack_spreads(
    wti: list[dict[str, Any]],
    rbob: list[dict[str, Any]],
    heating_oil: list[dict[str, Any]],
    weeks: int = 12,
    wow_lookback: int = 7,
) -> dict[str, list[dict[str, Any]]]:
    """Compute 3-2-1, RBOB, and HO crack spreads from spot price series.

    Args:
        wti:         Newest-first daily WTI spot prices in $/barrel.
        rbob:        Newest-first daily RBOB spot prices in $/gallon.
        heating_oil: Newest-first daily heating oil spot prices in $/gallon.
        weeks:       How many weeks of history to return (default 12).
        wow_lookback: Periods to look back for WoW delta (default 7 = same weekday last week).

    Returns:
        {
          "three_two_one": [{period, value, wow_change, wow_pct_change}, ...],
          "rbob_crack":    [...],
          "ho_crack":      [...],
        }
        All lists are newest-first and contain at most weeks*7 entries.
    """
    aligned = _align_prices(wti, rbob, heating_oil)[: weeks * wow_lookback]

    three_two_one: list[dict[str, Any]] = []
    rbob_series: list[dict[str, Any]] = []
    ho_series: list[dict[str, Any]] = []

    for period, wti_val, rbob_val, ho_val in aligned:
        rbob_bbl = rbob_val * _BBL
        ho_bbl = ho_val * _BBL

        three_two_one.append({
            "period": period,
            "value": round((2 * rbob_bbl + ho_bbl - 3 * wti_val) / 3, 4),
        })
        rbob_series.append({
            "period": period,
            "value": round(rbob_bbl - wti_val, 4),
        })
        ho_series.append({
            "period": period,
            "value": round(ho_bbl - wti_val, 4),
        })

    return {
        "three_two_one": _add_wow(three_two_one, wow_lookback),
        "rbob_crack":    _add_wow(rbob_series, wow_lookback),
        "ho_crack":      _add_wow(ho_series, wow_lookback),
    }
