"""
CFTC Commitments of Traders service — disaggregated futures-only report.

Data source: CFTC Public Reporting SODA API (no authentication required).
Endpoint:    https://publicreporting.cftc.gov/resource/jun7-fc8e.json
Documentation: https://publicreporting.cftc.gov/stories/s/r4w9-dmmj

COT reports are published every Friday (covering the prior Tuesday).
Cache TTL is set to 6 hours so stale data is never served for more than one
reporting cycle.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.core.cache import get_cache

logger = logging.getLogger(__name__)

# =============================================================================
# Commodity codes and labels
# =============================================================================

# WTI Light Sweet Crude Oil — NYMEX
WTI_CODE = "067651"
# Brent Crude Oil — ICE Futures Europe
BRENT_CODE = "067411"

# Human-readable labels keyed by CFTC commodity code
COMMODITY_LABELS: dict[str, str] = {
    WTI_CODE:   "WTI",
    BRENT_CODE: "Brent",
}

# =============================================================================
# Internal constants
# =============================================================================

# Direct Socrata resource URL for the disaggregated futures-only COT report
_RESOURCE_URL = "https://publicreporting.cftc.gov/resource/jun7-fc8e.json"

# 3 years of weekly data (52 × 3 = 156 rows) gives a meaningful percentile window
_LOOKBACK_WEEKS = 156

# COT publishes weekly; 6-hour TTL ensures we never serve stale data twice
_COT_TTL_SECONDS = 6 * 3600

# SODA column names for managed money positions
_COL_LONG  = "m_money_positions_long_all"
_COL_SHORT = "m_money_positions_short_all"
_COL_DATE  = "report_date_as_yyyy_mm_dd"


# =============================================================================
# Pure parsing helper — no I/O, straightforward to unit-test
# =============================================================================

def parse_managed_money(
    records: list[dict[str, Any]],
    commodity_code: str,
) -> dict[str, Any]:
    """Parse raw CFTC SODA records into a managed money position summary.

    Args:
        records:        JSON rows from the SODA API, newest-first.
        commodity_code: CFTC commodity code used to look up the display label.

    Returns::
        {
            commodity:       display name (e.g. "WTI"),
            report_date:     ISO date string of the most recent report,
            long:            managed money long contracts (latest week),
            short:           managed money short contracts (latest week),
            net_position:    long − short (latest week),
            wow_change:      net position change vs prior week (None if < 2 records),
            percentile_rank: % of prior 3-year weeks with net below current
                             (None if only 1 parseable record),
        }

    Raises:
        ValueError: if records is empty or no row can be parsed.
    """
    if not records:
        raise ValueError(f"No COT records for commodity_code={commodity_code!r}")

    parsed: list[dict[str, Any]] = []
    for rec in records:
        try:
            long_pos  = int(float(rec[_COL_LONG]))
            short_pos = int(float(rec[_COL_SHORT]))
        except (KeyError, TypeError, ValueError):
            logger.debug("Skipping unparseable COT row: %s", rec)
            continue

        # Socrata timestamps arrive as "2024-01-12T00:00:00.000" — keep date only
        date_str = str(rec.get(_COL_DATE, ""))[:10]

        parsed.append({
            "date":  date_str,
            "long":  long_pos,
            "short": short_pos,
            "net":   long_pos - short_pos,
        })

    if not parsed:
        raise ValueError(f"No parseable COT rows for commodity_code={commodity_code!r}")

    current = parsed[0]
    prior   = parsed[1] if len(parsed) > 1 else None

    wow_change = current["net"] - prior["net"] if prior is not None else None

    # Percentile rank: fraction of historical weeks with a strictly lower net position.
    # The current week is excluded from the historical distribution so the result
    # reflects where today stands *relative to* prior observations.
    history = [r["net"] for r in parsed[1:]]
    if history:
        below = sum(1 for v in history if v < current["net"])
        percentile_rank: float | None = round(below / len(history) * 100, 1)
    else:
        percentile_rank = None

    return {
        "commodity":       COMMODITY_LABELS.get(commodity_code, commodity_code),
        "report_date":     current["date"],
        "long":            current["long"],
        "short":           current["short"],
        "net_position":    current["net"],
        "wow_change":      wow_change,
        "percentile_rank": percentile_rank,
    }


# =============================================================================
# Service class
# =============================================================================

class CFTCService:
    """CFTC Commitments of Traders report client (public, no key required)."""

    BASE_URL = "https://publicreporting.cftc.gov/api/explore/v2.1/catalog/datasets"

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def _fetch_cot_records(self, commodity_code: str) -> list[dict[str, Any]]:
        """Fetch raw disaggregated COT rows from the CFTC SODA API, newest-first."""
        params = {
            "$where":  f"cftc_commodity_code='{commodity_code}'",
            "$order":  f"{_COL_DATE} DESC",
            "$limit":  str(_LOOKBACK_WEEKS),
            "$select": f"{_COL_DATE},{_COL_LONG},{_COL_SHORT},market_and_exchange_names",
        }
        response = await self._client.get(_RESOURCE_URL, params=params)
        response.raise_for_status()
        return response.json()

    async def get_managed_money_positions(self, commodity_code: str) -> dict[str, Any]:
        """Return a managed money position summary for the given CFTC commodity code.

        Results are cached for _COT_TTL_SECONDS (6 h) — one full COT reporting cycle.
        Use WTI_CODE or BRENT_CODE from this module as the commodity_code argument.
        """
        async def fetch() -> dict[str, Any]:
            records = await self._fetch_cot_records(commodity_code)
            return parse_managed_money(records, commodity_code)

        key = f"cftc:managed_money:{commodity_code}"
        return await get_cache().cache_or_fetch(key, fetch, ttl=_COT_TTL_SECONDS)
