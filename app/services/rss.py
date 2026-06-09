"""RSS feed service — fetches and parses daily energy news feeds."""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Any

import feedparser
import httpx
from html import unescape
from urllib.parse import unquote

from app.core.cache import get_cache

logger = logging.getLogger(__name__)

_CACHE_KEY = "macro:brief"
_TTL = 28 * 3600  # 28 hours — survives one missed daily run
_MAX_ARTICLES = 50
_TIMEOUT = 15.0

_RSS_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/rss+xml, application/xml, text/xml, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}

_ALL_FEEDS: list[dict[str, str]] = [
    {"source": "OilPrice",                 "url": "https://oilprice.com/rss/main"},
    {"source": "EIA Today in Energy",      "url": "https://www.eia.gov/rss/todayinenergy.xml"},
    {"source": "S&P Global Oil",           "url": "https://www.spglobal.com/content/spglobal/energy/us/en/rss/oil.xml"},
    {"source": "S&P Global Crude",         "url": "https://www.spglobal.com/content/spglobal/energy/us/en/rss/oil-crude.xml"},
    {"source": "S&P Global Refined Prods", "url": "https://www.spglobal.com/content/spglobal/energy/us/en/rss/oil-refined-products.xml"},
    {"source": "Energy Intelligence",      "url": "https://www.energyintel.com/rss-feed.rss"},
    {"source": "Carbon Brief",             "url": "https://www.carbonbrief.org/feed/"},
    {"source": "RenewEconomy",             "url": "https://reneweconomy.com.au/feed/"},
    {"source": "World Oil",                "url": "https://www.worldoil.com/rss?feed=news"},
    {"source": "OGJ Exploration",          "url": "https://www.ogj.com/__rss/website-scheduled-content.xml?input=%7B%22sectionAlias%22%3A%22exploration-development%22%7D"},
    {"source": "OGJ Drilling",             "url": "https://www.ogj.com/__rss/website-scheduled-content.xml?input=%7B%22sectionAlias%22%3A%22drilling-production%22%7D"},
    {"source": "OGJ Refining",             "url": "https://www.ogj.com/__rss/website-scheduled-content.xml?input=%7B%22sectionAlias%22%3A%22refining-processing%22%7D"},
]

_SP_SOURCES = {"S&P Global Oil", "S&P Global Crude", "S&P Global Refined Prods"}
FEEDS = [f for f in _ALL_FEEDS if f["source"] not in _SP_SOURCES] \
    if os.getenv("DISABLE_SP_GLOBAL_FEEDS", "").lower() == "true" \
    else _ALL_FEEDS


class RssService:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def _fetch_feed(self, source: str, url: str) -> dict[str, Any]:
        """Fetch one feed. On any error, log and return an empty articles list."""
        try:
            response = await self._client.get(unquote(url), timeout=_TIMEOUT, follow_redirects=True, headers=_RSS_HEADERS)
            response.raise_for_status()
            parsed = feedparser.parse(response.text)
            logger.info("Feed %s: %d entries parsed", source, len(parsed.entries))
            articles: list[dict[str, Any]] = []
            for entry in parsed.entries[:_MAX_ARTICLES]:
                published = (
                    getattr(entry, "published", None)
                    or getattr(entry, "updated", None)
                    or ""
                )
                raw_summary = getattr(entry, "summary", None)
                articles.append({
                    "title": unescape(getattr(entry, "title", "")),
                    "link": getattr(entry, "link", ""),
                    "published": published,
                    "summary": unescape(raw_summary[:300]) if raw_summary else None,
                })
            return {"source": source, "url": url, "articles": articles}
        except Exception as exc:
            logger.warning("RSS fetch failed for %s (%s): %s", source, url, exc)
            return {"source": source, "url": url, "articles": []}

    async def fetch_all(self) -> dict[str, Any]:
        """Fetch all feeds concurrently, write to Redis, return the payload dict."""
        results = await asyncio.gather(*[self._fetch_feed(f["source"], f["url"]) for f in FEEDS])
        payload: dict[str, Any] = {
            "sources": list(results),
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            await get_cache().set_json(_CACHE_KEY, payload, ttl=_TTL)
            logger.info("Morning brief cached (%d sources)", len(results))
        except Exception as exc:
            logger.warning("Morning brief cache write failed: %s", exc)
        return payload
