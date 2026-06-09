"""Polymarket Gamma API service — markets, events, and embedded prices.

All endpoints are public (no auth required). Prices are decimal probabilities (0–1).
TTLs are short because outcomePrices update with every trade.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.core.cache import get_cache

logger = logging.getLogger(__name__)

_MARKETS_TTL = 60        # 1 min — prices change with each trade
_MARKET_TTL = 30         # 30s — single market, live price
_EVENTS_TTL = 120        # 2 min — event list changes slowly
_EVENT_TTL = 60          # 1 min — single event with embedded markets
_SEARCH_PAGE_SIZE = 100  # Gamma API max per request
_SEARCH_MAX_PAGES = 5    # Scan up to 500 markets for keyword matches


class PolymarketService:
    GAMMA_URL = "https://gamma-api.polymarket.com"

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        response = await self._client.get(
            f"{self.GAMMA_URL}{path}",
            params={k: v for k, v in (params or {}).items() if v is not None},
        )
        response.raise_for_status()
        return response.json()

    async def get_markets(
        self,
        limit: int = 20,
        offset: int = 0,
        active: bool | None = None,
        closed: bool | None = None,
        tag_id: int | None = None,
        tag_slug: str | None = None,
        order: str | None = None,
        ascending: bool | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "limit": limit,
            "offset": offset,
            "active": str(active).lower() if active is not None else None,
            "closed": str(closed).lower() if closed is not None else None,
            "tag_id": tag_id,
            "tag_slug": tag_slug,
            "order": order,
            "ascending": str(ascending).lower() if ascending is not None else None,
        }
        cache_key = (
            f"polymarket:markets:{limit}:{offset}:{active}:{closed}:"
            f"{tag_id}:{tag_slug}:{order}:{ascending}"
        )

        async def fetch() -> list[dict[str, Any]]:
            return await self._get("/markets", params)

        return await get_cache().cache_or_fetch(cache_key, fetch, ttl=_MARKETS_TTL)

    async def search_markets(
        self,
        q: str,
        limit: int = 20,
        active: bool | None = None,
        closed: bool | None = None,
        tag_id: int | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch up to _SEARCH_MAX_PAGES pages from Polymarket and filter by keyword in question."""
        keyword = q.lower().strip()
        cache_key = f"polymarket:search:{keyword}:{limit}:{active}:{closed}:{tag_id}"

        async def fetch() -> list[dict[str, Any]]:
            matches: list[dict[str, Any]] = []
            for page in range(_SEARCH_MAX_PAGES):
                params: dict[str, Any] = {
                    "limit": _SEARCH_PAGE_SIZE,
                    "offset": page * _SEARCH_PAGE_SIZE,
                    "active": str(active).lower() if active is not None else None,
                    "closed": str(closed).lower() if closed is not None else None,
                    "tag_id": tag_id,
                }
                page_data: list[dict[str, Any]] = await self._get("/markets", params)
                if not page_data:
                    break
                matches.extend(
                    m for m in page_data if keyword in m.get("question", "").lower()
                )
                if len(matches) >= limit or len(page_data) < _SEARCH_PAGE_SIZE:
                    break
            return matches[:limit]

        return await get_cache().cache_or_fetch(cache_key, fetch, ttl=_MARKETS_TTL)

    async def get_market(self, condition_id: str) -> dict[str, Any]:
        async def fetch() -> dict[str, Any]:
            return await self._get(f"/markets/{condition_id}")

        return await get_cache().cache_or_fetch(
            f"polymarket:market:{condition_id}",
            fetch,
            ttl=_MARKET_TTL,
        )

    async def get_events(
        self,
        limit: int = 20,
        offset: int = 0,
        active: bool | None = None,
        closed: bool | None = None,
        tag_id: int | None = None,
        tag_slug: str | None = None,
        order: str | None = None,
        ascending: bool | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "limit": limit,
            "offset": offset,
            "active": str(active).lower() if active is not None else None,
            "closed": str(closed).lower() if closed is not None else None,
            "tag_id": tag_id,
            "tag_slug": tag_slug,
            "order": order,
            "ascending": str(ascending).lower() if ascending is not None else None,
        }
        cache_key = (
            f"polymarket:events:{limit}:{offset}:{active}:{closed}:"
            f"{tag_id}:{tag_slug}:{order}:{ascending}"
        )

        async def fetch() -> list[dict[str, Any]]:
            return await self._get("/events", params)

        return await get_cache().cache_or_fetch(cache_key, fetch, ttl=_EVENTS_TTL)

    async def get_event(self, event_id: str) -> dict[str, Any]:
        async def fetch() -> dict[str, Any]:
            return await self._get(f"/events/{event_id}")

        return await get_cache().cache_or_fetch(
            f"polymarket:event:{event_id}",
            fetch,
            ttl=_EVENT_TTL,
        )
