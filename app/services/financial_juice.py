"""Financial Juice RSS feed service — fetches news items from the last 24 hours."""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

logger = logging.getLogger(__name__)

RSS_URL = "https://www.financialjuice.com/rss"

_24H = timedelta(hours=24)


class FinancialJuiceService:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def get_recent_news(self) -> list[dict[str, Any]]:
        """Fetch the RSS feed and return items published in the last 24 hours."""
        response = await self._client.get(RSS_URL, timeout=15.0)
        response.raise_for_status()
        return self._parse_rss(response.text)

    @staticmethod
    def _parse_rss(xml_text: str) -> list[dict[str, Any]]:
        root = ET.fromstring(xml_text)
        channel = root.find("channel")
        if channel is None:
            return []

        cutoff = datetime.now(tz=timezone.utc) - _24H
        items: list[dict[str, Any]] = []

        for item in channel.findall("item"):
            title_el = item.find("title")
            desc_el = item.find("description")
            pub_date_el = item.find("pubDate")

            if pub_date_el is None or not pub_date_el.text:
                continue

            try:
                pub_dt = parsedate_to_datetime(pub_date_el.text)
            except Exception:
                continue

            if pub_dt < cutoff:
                continue

            items.append({
                "title": (title_el.text or "").strip() if title_el is not None else "",
                "description": (desc_el.text or "").strip() if desc_el is not None else "",
                "published_at": pub_dt.isoformat(),
            })

        return items
