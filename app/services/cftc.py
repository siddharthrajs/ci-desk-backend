"""
CFTC Commitments of Traders service — disaggregated futures-only report,
petroleum and products.

Data source: CFTC Public Reporting SODA API (no authentication required).
Dataset:     Disaggregated Commitments of Traders – Futures Only (72hh-3qpy)
Endpoint:    https://publicreporting.cftc.gov/resource/72hh-3qpy.json

COT reports are published every Friday (covering the prior Tuesday).
Cache TTL is set to 6 hours so stale data is never served for more than one
reporting cycle.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

import httpx

from app.core.cache import get_cache

logger = logging.getLogger(__name__)

# =============================================================================
# Constants
# =============================================================================

_RESOURCE_URL = "https://publicreporting.cftc.gov/resource/72hh-3qpy.json"
_LOOKBACK_WEEKS = 156
_COT_TTL_SECONDS = 6 * 3600
_COL_DATE = "report_date_as_yyyy_mm_dd"

# Public cache key — imported by the router for cache busting
COT_CACHE_KEY = "cftc:petroleum:v2"

# All petroleum and products contracts from the CFTC long-format report
_PETROLEUM_CODES = (
    "02141B",  # USGC HSFO (PLATTS) — ICE Futures Energy
    "02141C",  # FUEL OIL-3% USGC/3.5% FOB RDAM — ICE Futures Energy
    "02141R",  # USGC HSFO-PLATTS/BRENT 1ST LN — ICE Futures Energy
    "022651",  # NY HARBOR ULSD — NYMEX
    "022A13",  # UP DOWN GC ULSD VS HO SPR — NYMEX
    "025608",  # ETHANOL T2 FOB INCL DUTY — NYMEX
    "025651",  # ETHANOL — NYMEX
    "06739C",  # CRUDE DIFF-WCS HOUSTON/WTI 1ST — ICE Futures Energy
    "067411",  # CRUDE OIL, LIGHT SWEET-WTI — ICE Futures Europe
    "06742G",  # CRUDE DIFF-TMX WCS 1A INDEX — ICE Futures Energy
    "06742T",  # CRUDE DIFF-TMX SW 1A INDEX — ICE Futures Energy
    "06743A",  # CONDENSATE DIF-TMX C5 1A INDEX — ICE Futures Energy
    "067651",  # WTI-PHYSICAL — NYMEX
    "06765A",  # WTI FINANCIAL CRUDE OIL — NYMEX
    "06765T",  # BRENT LAST DAY — NYMEX
    "0676A5",  # WTI HOUSTON ARGUS/WTI TR MO — NYMEX
    "067A71",  # WTI MIDLAND ARGUS VS WTI TRADE — NYMEX
    "111415",  # GASOLINE CRK-RBOB/BRENT 1st — ICE Futures Energy
    "111659",  # GASOLINE RBOB — NYMEX
    "111A34",  # GULF COAST CBOB GAS A2 PL RBOB — NYMEX
    "86465A",  # GULF JET NY HEAT OIL SPR — NYMEX
)

_WHERE_CLAUSE = (
    "cftc_contract_market_code in("
    + ",".join(f"'{c}'" for c in _PETROLEUM_CODES)
    + ")"
)

# Columns fetched from the SODA API — covers all 4 sections of the long-format report.
# Note: CFTC schema has inconsistent naming (double-underscore in swap short/spread,
# missing _all suffixes on several other_rept and prod_merc columns).
_SELECT_COLS = ",".join([
    _COL_DATE,
    "cftc_contract_market_code",
    "contract_market_name",
    "market_and_exchange_names",
    "contract_units",
    # Open interest
    "open_interest_all",
    "change_in_open_interest_all",
    # Producer/Merchant/Processor/User
    "prod_merc_positions_long",
    "prod_merc_positions_short",
    "change_in_prod_merc_long",
    "change_in_prod_merc_short",
    "pct_of_oi_prod_merc_long",
    "pct_of_oi_prod_merc_short",
    "traders_prod_merc_long_all",
    "traders_prod_merc_short_all",
    # Swap Dealers (short/spread cols use double-underscore — upstream schema bug)
    "swap_positions_long_all",
    "swap__positions_short_all",
    "swap__positions_spread_all",
    "change_in_swap_long_all",
    "change_in_swap_short_all",
    "change_in_swap_spread_all",
    "pct_of_oi_swap_long_all",
    "pct_of_oi_swap_short_all",
    "pct_of_oi_swap_spread_all",
    "traders_swap_long_all",
    "traders_swap_short_all",
    "traders_swap_spread_all",
    # Managed Money
    "m_money_positions_long_all",
    "m_money_positions_short_all",
    "m_money_positions_spread",
    "change_in_m_money_long_all",
    "change_in_m_money_short_all",
    "change_in_m_money_spread",
    "pct_of_oi_m_money_long_all",
    "pct_of_oi_m_money_short_all",
    "pct_of_oi_m_money_spread",
    "traders_m_money_long_all",
    "traders_m_money_short_all",
    "traders_m_money_spread_all",
    # Other Reportables (no _all suffix on short/spread — upstream schema bug)
    "other_rept_positions_long",
    "other_rept_positions_short",
    "other_rept_positions_spread",
    "change_in_other_rept_long",
    "change_in_other_rept_short",
    "change_in_other_rept_spread",
    "pct_of_oi_other_rept_long",
    "pct_of_oi_other_rept_short",
    "pct_of_oi_other_rept_spread",
    "traders_other_rept_long_all",
    "traders_other_rept_short",
    "traders_other_rept_spread",
    # Non-Reportable
    "nonrept_positions_long_all",
    "nonrept_positions_short_all",
    "change_in_nonrept_long_all",
    "change_in_nonrept_short_all",
    "pct_of_oi_nonrept_long_all",
    "pct_of_oi_nonrept_short_all",
    # Concentration — top 4 / top 8 largest traders
    "conc_gross_le_4_tdr_long",
    "conc_gross_le_4_tdr_short",
    "conc_gross_le_8_tdr_long",
    "conc_gross_le_8_tdr_short",
    "conc_net_le_4_tdr_long_all",
    "conc_net_le_4_tdr_short_all",
    "conc_net_le_8_tdr_long_all",
    "conc_net_le_8_tdr_short_all",
])


# =============================================================================
# Parsing helpers
# =============================================================================

def _i(rec: dict[str, Any], key: str) -> int:
    try:
        return int(float(rec.get(key) or 0))
    except (TypeError, ValueError):
        return 0


def _f(rec: dict[str, Any], key: str) -> float:
    try:
        return float(rec.get(key) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _opt_i(rec: dict[str, Any], key: str) -> int | None:
    v = rec.get(key)
    if v is None or v == "":
        return None
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _parse_row(rec: dict[str, Any]) -> dict[str, Any]:
    date_str = str(rec.get(_COL_DATE, ""))[:10]
    market_and_exchange = rec.get("market_and_exchange_names", "")
    parts = market_and_exchange.rsplit(" - ", 1)
    exchange = parts[1] if len(parts) == 2 else market_and_exchange

    return {
        "contract_market_code": rec.get("cftc_contract_market_code", ""),
        "contract_market_name": rec.get("contract_market_name", ""),
        "market_and_exchange_names": market_and_exchange,
        "exchange": exchange,
        "report_date": date_str,
        "contract_units": rec.get("contract_units", ""),
        "open_interest": _i(rec, "open_interest_all"),

        "producer_merchant": {
            "long":  _i(rec, "prod_merc_positions_long"),
            "short": _i(rec, "prod_merc_positions_short"),
        },
        "swap_dealers": {
            "long":      _i(rec, "swap_positions_long_all"),
            "short":     _i(rec, "swap__positions_short_all"),
            "spreading": _i(rec, "swap__positions_spread_all"),
        },
        "managed_money": {
            "long":      _i(rec, "m_money_positions_long_all"),
            "short":     _i(rec, "m_money_positions_short_all"),
            "spreading": _i(rec, "m_money_positions_spread"),
        },
        "other_reportables": {
            "long":      _i(rec, "other_rept_positions_long"),
            "short":     _i(rec, "other_rept_positions_short"),
            "spreading": _i(rec, "other_rept_positions_spread"),
        },
        "non_reportable": {
            "long":  _i(rec, "nonrept_positions_long_all"),
            "short": _i(rec, "nonrept_positions_short_all"),
        },

        "open_interest_change": _i(rec, "change_in_open_interest_all"),
        "producer_merchant_change": {
            "long":  _i(rec, "change_in_prod_merc_long"),
            "short": _i(rec, "change_in_prod_merc_short"),
        },
        "swap_dealers_change": {
            "long":      _i(rec, "change_in_swap_long_all"),
            "short":     _i(rec, "change_in_swap_short_all"),
            "spreading": _i(rec, "change_in_swap_spread_all"),
        },
        "managed_money_change": {
            "long":      _i(rec, "change_in_m_money_long_all"),
            "short":     _i(rec, "change_in_m_money_short_all"),
            "spreading": _i(rec, "change_in_m_money_spread"),
        },
        "other_reportables_change": {
            "long":      _i(rec, "change_in_other_rept_long"),
            "short":     _i(rec, "change_in_other_rept_short"),
            "spreading": _i(rec, "change_in_other_rept_spread"),
        },
        "non_reportable_change": {
            "long":  _i(rec, "change_in_nonrept_long_all"),
            "short": _i(rec, "change_in_nonrept_short_all"),
        },

        "producer_merchant_pct": {
            "long":  _f(rec, "pct_of_oi_prod_merc_long"),
            "short": _f(rec, "pct_of_oi_prod_merc_short"),
        },
        "swap_dealers_pct": {
            "long":      _f(rec, "pct_of_oi_swap_long_all"),
            "short":     _f(rec, "pct_of_oi_swap_short_all"),
            "spreading": _f(rec, "pct_of_oi_swap_spread_all"),
        },
        "managed_money_pct": {
            "long":      _f(rec, "pct_of_oi_m_money_long_all"),
            "short":     _f(rec, "pct_of_oi_m_money_short_all"),
            "spreading": _f(rec, "pct_of_oi_m_money_spread"),
        },
        "other_reportables_pct": {
            "long":      _f(rec, "pct_of_oi_other_rept_long"),
            "short":     _f(rec, "pct_of_oi_other_rept_short"),
            "spreading": _f(rec, "pct_of_oi_other_rept_spread"),
        },
        "non_reportable_pct": {
            "long":  _f(rec, "pct_of_oi_nonrept_long_all"),
            "short": _f(rec, "pct_of_oi_nonrept_short_all"),
        },

        "producer_merchant_traders": {
            "long":  _opt_i(rec, "traders_prod_merc_long_all"),
            "short": _opt_i(rec, "traders_prod_merc_short_all"),
        },
        "swap_dealers_traders": {
            "long":      _opt_i(rec, "traders_swap_long_all"),
            "short":     _opt_i(rec, "traders_swap_short_all"),
            "spreading": _opt_i(rec, "traders_swap_spread_all"),
        },
        "managed_money_traders": {
            "long":      _opt_i(rec, "traders_m_money_long_all"),
            "short":     _opt_i(rec, "traders_m_money_short_all"),
            "spreading": _opt_i(rec, "traders_m_money_spread_all"),
        },
        "other_reportables_traders": {
            "long":      _opt_i(rec, "traders_other_rept_long_all"),
            "short":     _opt_i(rec, "traders_other_rept_short"),
            "spreading": _opt_i(rec, "traders_other_rept_spread"),
        },

        "concentration": {
            "gross_le4_long":  _f(rec, "conc_gross_le_4_tdr_long"),
            "gross_le4_short": _f(rec, "conc_gross_le_4_tdr_short"),
            "gross_le8_long":  _f(rec, "conc_gross_le_8_tdr_long"),
            "gross_le8_short": _f(rec, "conc_gross_le_8_tdr_short"),
            "net_le4_long":    _f(rec, "conc_net_le_4_tdr_long_all"),
            "net_le4_short":   _f(rec, "conc_net_le_4_tdr_short_all"),
            "net_le8_long":    _f(rec, "conc_net_le_8_tdr_long_all"),
            "net_le8_short":   _f(rec, "conc_net_le_8_tdr_short_all"),
        },
    }


def parse_petroleum_cot(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Parse raw SODA records into per-contract COT summaries.

    Records must be ordered descending by date (newest first) so that
    rows[0] per contract is the most recent week.
    """
    by_code: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for rec in records:
        code = rec.get("cftc_contract_market_code", "")
        if code:
            by_code[code].append(rec)

    contracts: list[dict[str, Any]] = []
    for rows in by_code.values():
        parsed = _parse_row(rows[0])

        mm_long  = parsed["managed_money"]["long"]
        mm_short = parsed["managed_money"]["short"]
        mm_net   = mm_long - mm_short

        mm_chg_long  = parsed["managed_money_change"]["long"]
        mm_chg_short = parsed["managed_money_change"]["short"]
        mm_wow_net_change: int | None = mm_chg_long - mm_chg_short

        # Percentile rank: fraction of prior 3-year weeks with MM net below current
        history: list[int] = []
        for row in rows[1:]:
            try:
                hl = int(float(row.get("m_money_positions_long_all") or 0))
                hs = int(float(row.get("m_money_positions_short_all") or 0))
                history.append(hl - hs)
            except (TypeError, ValueError):
                continue

        if history:
            below = sum(1 for v in history if v < mm_net)
            mm_percentile_rank: float | None = round(below / len(history) * 100, 1)
        else:
            mm_percentile_rank = None

        parsed["mm_net"]             = mm_net
        parsed["mm_wow_net_change"]  = mm_wow_net_change
        parsed["mm_percentile_rank"] = mm_percentile_rank
        contracts.append(parsed)

    # Most liquid contracts first
    contracts.sort(key=lambda c: c["open_interest"], reverse=True)
    return contracts


# =============================================================================
# Service class
# =============================================================================

class CFTCService:
    """CFTC Commitments of Traders — petroleum and products (public, no key required)."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def _fetch_records(self) -> list[dict[str, Any]]:
        params = {
            "$where":  _WHERE_CLAUSE,
            "$order":  f"{_COL_DATE} DESC, cftc_contract_market_code ASC",
            "$limit":  "5000",
            "$select": _SELECT_COLS,
        }
        response = await self._client.get(_RESOURCE_URL, params=params)
        response.raise_for_status()
        return response.json()

    async def get_petroleum_cot(self) -> dict[str, Any]:
        """Return full petroleum COT data for all contracts, cached for 6 hours."""
        async def fetch() -> dict[str, Any]:
            records   = await self._fetch_records()
            contracts = parse_petroleum_cot(records)
            # Use WTI Physical report date as the canonical date; fall back to first contract
            report_date = next(
                (c["report_date"] for c in contracts if c["contract_market_code"] == "067651"),
                contracts[0]["report_date"] if contracts else "",
            )
            return {"contracts": contracts, "report_date": report_date}

        return await get_cache().cache_or_fetch(COT_CACHE_KEY, fetch, ttl=_COT_TTL_SECONDS)
