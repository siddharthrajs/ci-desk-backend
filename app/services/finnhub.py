"""Finnhub API service — market news, company news, quotes, economic calendar.

Free tier limit: 60 API calls/minute.
TTLs are chosen to stay well within that budget across all background jobs.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

import httpx

from app.core.cache import get_cache

logger = logging.getLogger(__name__)

# Oil & energy tickers tracked by this dashboard
OIL_TICKERS = [
    "XOM",   # ExxonMobil
    "CVX",   # Chevron
    "SHEL",  # Shell
    "BP",    # BP
    "TTE",   # TotalEnergies
    "COP",   # ConocoPhillips
    "MRO",   # Marathon Oil
    "DVN",   # Devon Energy
    "SLB",   # SLB (Schlumberger)
    "HAL",   # Halliburton
    "USO",   # US Oil Fund ETF
    "XLE",   # Energy Select Sector SPDR
]

_NEWS_TTL = 150          # 5 min — news articles don't change second-to-second
_QUOTE_TTL = 30          # 30s — prices need to be reasonably fresh
_CALENDAR_TTL = 86_400   # 24h — economic calendar events don't shift intraday


class FinnhubService:
    BASE_URL = "https://finnhub.io/api/v1"

    def __init__(self, client: httpx.AsyncClient, api_key: str) -> None:
        self._client = client
        self._api_key = api_key

    async def _get(self, path: str, params: dict[str, Any]) -> Any:
        response = await self._client.get(
            f"{self.BASE_URL}{path}",
            params={**params, "token": self._api_key},
        )
        response.raise_for_status()
        return response.json()

    async def get_market_news(self, category: str = "general") -> list[dict[str, Any]]:
        """GET /news — market-wide news feed. Cached for _NEWS_TTL seconds."""
        async def fetch() -> list[dict[str, Any]]:
            return await self._get("/news", {"category": category})

        return await get_cache().cache_or_fetch(
            f"finnhub:news:{category}",
            fetch,
            ttl=_NEWS_TTL,
        )

    async def get_company_news(
        self,
        symbol: str,
        from_date: str | None = None,
        to_date: str | None = None,
    ) -> list[dict[str, Any]]:
        """GET /company-news — news for a single ticker. Defaults to last 30 days. Cached for _NEWS_TTL seconds."""
        today = date.today()
        _to = to_date or today.isoformat()
        _from = from_date or (today - timedelta(days=30)).isoformat()

        async def fetch() -> list[dict[str, Any]]:
            return await self._get("/company-news", {"symbol": symbol, "from": _from, "to": _to})

        return await get_cache().cache_or_fetch(
            f"finnhub:company-news:{symbol.upper()}:{_from}:{_to}",
            fetch,
            ttl=_NEWS_TTL,
        )

    async def get_quote(self, symbol: str) -> dict[str, Any]:
        """GET /quote — real-time price for a single ticker. Cached for _QUOTE_TTL seconds."""
        async def fetch() -> dict[str, Any]:
            data = await self._get("/quote", {"symbol": symbol})
            return {"symbol": symbol.upper(), **data}

        return await get_cache().cache_or_fetch(
            f"finnhub:quote:{symbol.upper()}",
            fetch,
            ttl=_QUOTE_TTL,
        )

    async def get_economic_calendar(
        self,
        from_date: str | None = None,
        to_date: str | None = None,
    ) -> dict[str, Any]:
        """GET /calendar/economic — EIA reports, OPEC dates, macro events. Cached for _CALENDAR_TTL seconds."""
        today = date.today()
        _from = from_date or (today - timedelta(days=7)).isoformat()
        _to = to_date or (today + timedelta(days=30)).isoformat()

        async def fetch() -> dict[str, Any]:
            return await self._get("/calendar/economic", {"from": _from, "to": _to})

        return await get_cache().cache_or_fetch(
            f"finnhub:calendar:{_from}:{_to}",
            fetch,
            ttl=_CALENDAR_TTL,
        )
