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


# ---------------------------------------------------------------------------
# V2 compute helpers — used by the new sub-endpoints
# ---------------------------------------------------------------------------

def _z_and_signal(values: list[float]) -> tuple[float | None, str]:
    """Return (z_score, signal) for a time series (newest-first).

    Signal thresholds: |z| > 1.5 → ELEVATED / DEPRESSED, else NEUTRAL.
    """
    n = len(values)
    if n < 3:
        return None, "NEUTRAL"
    mean = sum(values) / n
    variance = sum((v - mean) ** 2 for v in values) / n
    std = variance ** 0.5
    if std == 0:
        return 0.0, "NEUTRAL"
    z = round((values[0] - mean) / std, 2)
    if z > 1.5:
        return z, "ELEVATED"
    if z < -1.5:
        return z, "DEPRESSED"
    return z, "NEUTRAL"


def _wow(series: list[tuple[str, float]], lookback: int = 7) -> float | None:
    if len(series) > lookback:
        return round(series[0][1] - series[lookback][1], 2)
    return None


def compute_crack_spreads_v2(
    wti: list[dict[str, Any]],
    brent: list[dict[str, Any]],
    rbob: list[dict[str, Any]],
    heating_oil: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compute crack spreads, z-scores, signals, and 90-day history.

    Inputs are newest-first daily price series from EIAService.get_spot_prices_full().
    WTI and Brent are in $/bbl; RBOB and heating oil are in $/gal.
    """
    wti_idx = {r["period"]: r["value"] for r in wti if r.get("value") is not None}
    brent_idx = {r["period"]: r["value"] for r in brent if r.get("value") is not None}
    rbob_idx = {r["period"]: r["value"] for r in rbob if r.get("value") is not None}
    ho_idx = {r["period"]: r["value"] for r in heating_oil if r.get("value") is not None}

    # Common periods for crack spreads (WTI + RBOB + HO), newest-first
    crack_periods = sorted(set(wti_idx) & set(rbob_idx) & set(ho_idx), reverse=True)
    brent_periods = sorted(set(wti_idx) & set(brent_idx), reverse=True)

    crack_321_s: list[tuple[str, float]] = []
    crack_rbob_s: list[tuple[str, float]] = []
    crack_ho_s: list[tuple[str, float]] = []
    brent_wti_s: list[tuple[str, float]] = []

    for p in crack_periods:
        w = wti_idx[p]
        r = rbob_idx[p] * _BBL
        h = ho_idx[p] * _BBL
        crack_321_s.append((p, (2 * r + h - 3 * w) / 3))
        crack_rbob_s.append((p, r - w))
        crack_ho_s.append((p, h - w))

    for p in brent_periods:
        brent_wti_s.append((p, brent_idx[p] - wti_idx[p]))

    z321, sig321   = _z_and_signal([v for _, v in crack_321_s])
    zrbob, sigrbob = _z_and_signal([v for _, v in crack_rbob_s])
    zho, sigho     = _z_and_signal([v for _, v in crack_ho_s])
    zbwti, sigbwti = _z_and_signal([v for _, v in brent_wti_s])

    latest = crack_periods[0] if crack_periods else None

    # 90-day history oldest-first for charting
    c321_dict   = dict(crack_321_s)
    crbob_dict  = dict(crack_rbob_s)
    cho_dict    = dict(crack_ho_s)
    bwti_dict   = dict(brent_wti_s)

    all_periods = sorted(set(c321_dict) | set(bwti_dict))  # ISO dates sort oldest-first
    history: list[dict[str, Any]] = []
    for p in all_periods:
        row: dict[str, Any] = {"date": p, "crack_321": None, "crack_rbob": None,
                                "crack_ho": None, "brent_wti": None, "wti": None}
        if p in c321_dict:
            row["crack_321"]  = round(c321_dict[p], 2)
            row["crack_rbob"] = round(crbob_dict[p], 2)
            row["crack_ho"]   = round(cho_dict[p], 2)
            row["wti"]        = round(wti_idx[p], 2) if p in wti_idx else None
        if p in bwti_dict:
            row["brent_wti"]  = round(bwti_dict[p], 2)
        history.append(row)

    return {
        "wti":        round(wti_idx[latest], 2)   if latest and latest in wti_idx   else None,
        "brent":      round(brent_idx[latest], 2) if latest and latest in brent_idx else None,
        "rbob_gal":   round(rbob_idx[latest], 4)  if latest and latest in rbob_idx  else None,
        "ho_gal":     round(ho_idx[latest], 4)    if latest and latest in ho_idx    else None,
        "crack_321":  round(crack_321_s[0][1], 2)  if crack_321_s  else None,
        "crack_rbob": round(crack_rbob_s[0][1], 2) if crack_rbob_s else None,
        "crack_ho":   round(crack_ho_s[0][1], 2)   if crack_ho_s   else None,
        "brent_wti":  round(brent_wti_s[0][1], 2)  if brent_wti_s  else None,
        "z_scores":   {"crack_321": z321, "crack_rbob": zrbob, "crack_ho": zho, "brent_wti": zbwti},
        "signals":    {"crack_321": sig321, "crack_rbob": sigrbob, "crack_ho": sigho, "brent_wti": sigbwti},
        "wow_changes": {
            "crack_321":  _wow(crack_321_s),
            "crack_rbob": _wow(crack_rbob_s),
            "crack_ho":   _wow(crack_ho_s),
            "brent_wti":  _wow(brent_wti_s),
        },
        "history_90d": history,
    }


def compute_refinery_utilization_v2(
    padd_data: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    """Derive national estimate (avg of PADD 1-5) and PADD 3 utilization history."""
    padd_keys = ["padd1", "padd2", "padd3", "padd4", "padd5"]

    idx: dict[str, dict[str, float]] = {}
    for key in padd_keys:
        for row in padd_data.get(key, []):
            p = row["period"]
            v = row.get("value")
            if v is not None:
                if p not in idx:
                    idx[p] = {}
                idx[p][key] = v

    all_periods = sorted(idx.keys())  # oldest-first
    history: list[dict[str, Any]] = []
    for p in all_periods:
        vals = [idx[p][k] for k in padd_keys if k in idx[p]]
        national = round(sum(vals) / len(vals), 1) if vals else None
        padd3 = idx[p].get("padd3")
        history.append({
            "date": p,
            "national": national,
            "padd3": round(padd3, 1) if padd3 is not None else None,
        })

    latest = history[-1] if history else {}
    return {
        "national_current": latest.get("national"),
        "padd3_current":    latest.get("padd3"),
        "history":          history,
    }


def compute_product_demand_v2(
    demand_data: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    """Compute 4-week average, YoY%, and history for each product.

    EIA reports in KBPD (Thousand Barrels/Day); we convert to MBD for the API response.
    """
    result: dict[str, Any] = {}
    for key in ["gasoline", "distillate", "jet", "total"]:
        series = demand_data.get(key, [])  # newest-first

        latest_4 = [r["value"] for r in series[:4] if r.get("value") is not None]
        avg_4wk = round(sum(latest_4) / len(latest_4) / 1000, 2) if latest_4 else None

        current = series[0]["value"] if series and series[0].get("value") is not None else None
        year_ago = series[51]["value"] if len(series) > 51 else None
        yoy_pct: float | None = None
        if current is not None and year_ago is not None and year_ago != 0:
            yoy_pct = round((current - year_ago) / year_ago * 100, 1)

        history = [
            {"date": r["period"], "value": round(r["value"] / 1000, 3)}
            for r in reversed(series)
            if r.get("value") is not None
        ]

        result[key] = {"current_4wk_avg": avg_4wk, "yoy_pct": yoy_pct, "history": history}

    return result


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
