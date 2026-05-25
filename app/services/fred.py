"""FRED (Federal Reserve Economic Data) API service.

All public methods return data newest-first, cached with a daily TTL.
FRED series IDs are defined as module-level constants.

FRED API docs: https://fred.stlouisfed.org/docs/api/fred/series/observations.html
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.core.cache import get_cache

logger = logging.getLogger(__name__)

# =============================================================================
# Series IDs
# =============================================================================

# Nominal Broad U.S. Dollar Index — FRED proxy for the widely quoted DXY.
# DTWEXBGS weights 26 currencies by U.S. trade share; released daily.
SERIES_DXY = "DTWEXBGS"

# 10-Year U.S. Treasury Constant Maturity Rate — daily (business days only).
# Missing weekends/holidays represented as "." in the FRED feed.
SERIES_US10Y = "DGS10"

# Effective Federal Funds Rate — monthly average.
SERIES_FED_FUNDS = "FEDFUNDS"

# WTI Crude Oil Spot Price, Cushing OK — daily ($/barrel).
SERIES_WTI = "DCOILWTICO"

# =============================================================================
# Internal constants
# =============================================================================

# Number of recent observations returned by get_fred_series by default
_DEFAULT_LIMIT = 90

# Daily TTL: most FRED series update at most once per business day
_DAILY_TTL = 86_400


class FREDService:
    """FRED API client. Inject via FastAPI Depends."""

    BASE_URL = "https://api.stlouisfed.org/fred"

    def __init__(self, client: httpx.AsyncClient, api_key: str) -> None:
        self._client = client
        self._api_key = api_key

    # -------------------------------------------------------------------------
    # Private helpers
    # -------------------------------------------------------------------------

    async def _fetch_observations(
        self,
        series_id: str,
        limit: int = _DEFAULT_LIMIT,
    ) -> list[dict[str, Any]]:
        """GET /series/observations for series_id; return the raw observation list."""
        params = {
            "series_id":  series_id,
            "api_key":    self._api_key,
            "file_type":  "json",
            "sort_order": "desc",
            "limit":      limit,
        }
        url = f"{self.BASE_URL}/series/observations"
        response = await self._client.get(url, params=params)
        response.raise_for_status()
        return response.json()["observations"]

    @staticmethod
    def _parse_observations(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Convert raw FRED observation rows to clean dicts, dropping missing values.

        FRED represents missing observations as the string "." — these are silently
        dropped so callers always receive numeric values.
        """
        parsed: list[dict[str, Any]] = []
        for row in rows:
            raw = row.get("value", "")
            if raw == "." or raw is None:
                continue
            try:
                value = float(raw)
            except (TypeError, ValueError):
                continue
            parsed.append({"date": row["date"], "value": value})
        return parsed

    # -------------------------------------------------------------------------
    # Generic helper
    # -------------------------------------------------------------------------

    async def get_fred_series(
        self,
        series_id: str,
        limit: int = _DEFAULT_LIMIT,
    ) -> dict[str, Any]:
        """Fetch a FRED series and return the latest value plus recent observations.

        Results are cached for _DAILY_TTL seconds (24 h).

        Returns:
            {
                series_id:    the FRED series identifier,
                latest_value: most recent non-missing float value (None if all missing),
                latest_date:  ISO date string of the latest observation (None if empty),
                observations: list of {date: str, value: float}, newest-first,
            }
        """
        async def fetch() -> dict[str, Any]:
            rows = await self._fetch_observations(series_id, limit=limit)
            observations = self._parse_observations(rows)
            latest = observations[0] if observations else None
            return {
                "series_id":    series_id,
                "latest_value": latest["value"] if latest else None,
                "latest_date":  latest["date"] if latest else None,
                "observations": observations,
            }

        key = f"fred:{series_id.lower()}"
        return await get_cache().cache_or_fetch(key, fetch, ttl=_DAILY_TTL)

    # -------------------------------------------------------------------------
    # Named series — thin wrappers around get_fred_series
    # -------------------------------------------------------------------------

    async def get_dxy(self) -> dict[str, Any]:
        """Nominal Broad U.S. Dollar Index (DTWEXBGS) — FRED proxy for DXY."""
        return await self.get_fred_series(SERIES_DXY)

    async def get_us10y(self) -> dict[str, Any]:
        """10-Year U.S. Treasury constant maturity rate (DGS10), daily."""
        return await self.get_fred_series(SERIES_US10Y)

    async def get_fed_funds(self) -> dict[str, Any]:
        """Effective Federal Funds Rate (FEDFUNDS), monthly average."""
        return await self.get_fred_series(SERIES_FED_FUNDS)

    async def get_wti(self) -> dict[str, Any]:
        """WTI crude oil spot price at Cushing, OK (DCOILWTICO) — $/barrel, daily."""
        return await self.get_fred_series(SERIES_WTI)
