"""Tests for FinancialJuiceService — httpx response is fully mocked."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from unittest.mock import AsyncMock

import httpx
import pytest

from app.services.financial_juice import RSS_URL, FinancialJuiceService

_DUMMY_REQUEST = httpx.Request("GET", RSS_URL)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _rss_xml(items_xml: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<rss version=\"2.0\"><channel>"
        f"{items_xml}"
        "</channel></rss>"
    )


def _item_xml(title: str, description: str, pub_dt: datetime) -> str:
    return (
        f"<item>"
        f"<title>{title}</title>"
        f"<description>{description}</description>"
        f"<pubDate>{format_datetime(pub_dt)}</pubDate>"
        f"</item>"
    )


def _rss_response(items_xml: str, status: int = 200) -> httpx.Response:
    content = _rss_xml(items_xml).encode()
    return httpx.Response(
        status,
        content=content,
        headers={"content-type": "application/rss+xml"},
        request=_DUMMY_REQUEST,
    )


def _recent() -> datetime:
    return datetime.now(tz=timezone.utc) - timedelta(hours=1)


def _old() -> datetime:
    return datetime.now(tz=timezone.utc) - timedelta(hours=25)


def _make_service() -> tuple[FinancialJuiceService, AsyncMock]:
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    return FinancialJuiceService(client=mock_client), mock_client.get


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestGetRecentNews:
    @pytest.mark.asyncio
    async def test_returns_items_within_24h(self) -> None:
        svc, mock_get = _make_service()
        mock_get.return_value = _rss_response(
            _item_xml("Oil up", "WTI rises.", _recent())
        )
        items = await svc.get_recent_news()
        assert len(items) == 1
        assert items[0]["title"] == "Oil up"

    @pytest.mark.asyncio
    async def test_excludes_items_older_than_24h(self) -> None:
        svc, mock_get = _make_service()
        mock_get.return_value = _rss_response(
            _item_xml("Old news", "Old.", _old())
        )
        items = await svc.get_recent_news()
        assert items == []

    @pytest.mark.asyncio
    async def test_mixed_items_returns_only_recent(self) -> None:
        svc, mock_get = _make_service()
        xml = _item_xml("Recent", "New.", _recent()) + _item_xml("Old", "Old.", _old())
        mock_get.return_value = _rss_response(xml)
        items = await svc.get_recent_news()
        assert len(items) == 1
        assert items[0]["title"] == "Recent"

    @pytest.mark.asyncio
    async def test_raises_on_http_error(self) -> None:
        svc, mock_get = _make_service()
        mock_get.return_value = _rss_response("", status=503)
        with pytest.raises(httpx.HTTPStatusError):
            await svc.get_recent_news()

    @pytest.mark.asyncio
    async def test_item_has_required_keys(self) -> None:
        svc, mock_get = _make_service()
        mock_get.return_value = _rss_response(
            _item_xml("Title", "Desc.", _recent())
        )
        items = await svc.get_recent_news()
        assert set(items[0].keys()) == {"title", "description", "published_at"}

    @pytest.mark.asyncio
    async def test_empty_channel_returns_empty_list(self) -> None:
        svc, mock_get = _make_service()
        mock_get.return_value = _rss_response("")
        items = await svc.get_recent_news()
        assert items == []

    @pytest.mark.asyncio
    async def test_item_missing_pub_date_is_skipped(self) -> None:
        svc, mock_get = _make_service()
        xml = "<item><title>No date</title><description>X</description></item>"
        mock_get.return_value = _rss_response(xml)
        items = await svc.get_recent_news()
        assert items == []

    @pytest.mark.asyncio
    async def test_fetches_correct_url(self) -> None:
        svc, mock_get = _make_service()
        mock_get.return_value = _rss_response(
            _item_xml("T", "D", _recent())
        )
        await svc.get_recent_news()
        assert mock_get.call_args.args[0] == RSS_URL
