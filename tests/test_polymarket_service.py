"""Tests for PolymarketService — httpx responses and Redis are fully mocked."""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.services.polymarket import PolymarketService, _EVENTS_TTL, _MARKETS_TTL

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DUMMY_REQUEST = httpx.Request("GET", "https://gamma-api.polymarket.com/events")


def _gamma_response(data: list[Any], status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=data, request=_DUMMY_REQUEST)


def _make_service() -> tuple[PolymarketService, AsyncMock]:
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    service = PolymarketService(client=mock_client)
    return service, mock_client.get


@pytest.fixture
def no_cache():
    async def passthrough(key: str, fn: Any, **kw: Any) -> Any:
        return await fn()

    mock_cache = MagicMock()
    mock_cache.cache_or_fetch = AsyncMock(side_effect=passthrough)
    with patch("app.services.polymarket.get_cache", return_value=mock_cache):
        yield mock_cache


# =============================================================================
# get_events — new params: tag_slug, order, ascending
# =============================================================================

class TestGetEventsNewParams:
    @pytest.mark.asyncio
    async def test_tag_slug_sent_to_api(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        mock_get.return_value = _gamma_response([])
        await service.get_events(tag_slug="geopolitics")
        params = mock_get.call_args.kwargs.get("params", {})
        assert params.get("tag_slug") == "geopolitics"

    @pytest.mark.asyncio
    async def test_order_sent_to_api(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        mock_get.return_value = _gamma_response([])
        await service.get_events(order="volume")
        params = mock_get.call_args.kwargs.get("params", {})
        assert params.get("order") == "volume"

    @pytest.mark.asyncio
    async def test_ascending_false_sent_as_string(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        mock_get.return_value = _gamma_response([])
        await service.get_events(ascending=False)
        params = mock_get.call_args.kwargs.get("params", {})
        assert params.get("ascending") == "false"

    @pytest.mark.asyncio
    async def test_ascending_true_sent_as_string(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        mock_get.return_value = _gamma_response([])
        await service.get_events(ascending=True)
        params = mock_get.call_args.kwargs.get("params", {})
        assert params.get("ascending") == "true"

    @pytest.mark.asyncio
    async def test_none_tag_slug_excluded_from_params(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        mock_get.return_value = _gamma_response([])
        await service.get_events(tag_slug=None)
        params = mock_get.call_args.kwargs.get("params", {})
        assert "tag_slug" not in params

    @pytest.mark.asyncio
    async def test_none_order_excluded_from_params(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        mock_get.return_value = _gamma_response([])
        await service.get_events(order=None)
        params = mock_get.call_args.kwargs.get("params", {})
        assert "order" not in params

    @pytest.mark.asyncio
    async def test_none_ascending_excluded_from_params(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        mock_get.return_value = _gamma_response([])
        await service.get_events(ascending=None)
        params = mock_get.call_args.kwargs.get("params", {})
        assert "ascending" not in params

    @pytest.mark.asyncio
    async def test_cache_key_includes_tag_slug(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        mock_get.return_value = _gamma_response([])
        await service.get_events(tag_slug="geopolitics", order="volume", ascending=False)
        cache_key: str = no_cache.cache_or_fetch.call_args.args[0]
        assert "geopolitics" in cache_key
        assert "volume" in cache_key
        assert "False" in cache_key

    @pytest.mark.asyncio
    async def test_uses_events_ttl(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        mock_get.return_value = _gamma_response([])
        await service.get_events(tag_slug="geopolitics")
        ttl = no_cache.cache_or_fetch.call_args.kwargs.get("ttl")
        assert ttl == _EVENTS_TTL

    @pytest.mark.asyncio
    async def test_returns_list_from_api(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        mock_get.return_value = _gamma_response([{"id": "1", "title": "Test"}])
        result = await service.get_events(tag_slug="geopolitics")
        assert result == [{"id": "1", "title": "Test"}]


# =============================================================================
# get_markets — new params: tag_slug, order, ascending
# =============================================================================

class TestGetMarketsNewParams:
    @pytest.mark.asyncio
    async def test_tag_slug_sent_to_api(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        mock_get.return_value = _gamma_response([])
        await service.get_markets(tag_slug="geopolitics")
        params = mock_get.call_args.kwargs.get("params", {})
        assert params.get("tag_slug") == "geopolitics"

    @pytest.mark.asyncio
    async def test_order_sent_to_api(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        mock_get.return_value = _gamma_response([])
        await service.get_markets(order="liquidity")
        params = mock_get.call_args.kwargs.get("params", {})
        assert params.get("order") == "liquidity"

    @pytest.mark.asyncio
    async def test_ascending_false_sent_as_string(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        mock_get.return_value = _gamma_response([])
        await service.get_markets(ascending=False)
        params = mock_get.call_args.kwargs.get("params", {})
        assert params.get("ascending") == "false"

    @pytest.mark.asyncio
    async def test_none_tag_slug_excluded_from_params(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        mock_get.return_value = _gamma_response([])
        await service.get_markets(tag_slug=None)
        params = mock_get.call_args.kwargs.get("params", {})
        assert "tag_slug" not in params

    @pytest.mark.asyncio
    async def test_none_ascending_excluded_from_params(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        mock_get.return_value = _gamma_response([])
        await service.get_markets(ascending=None)
        params = mock_get.call_args.kwargs.get("params", {})
        assert "ascending" not in params

    @pytest.mark.asyncio
    async def test_cache_key_includes_new_params(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        mock_get.return_value = _gamma_response([])
        await service.get_markets(tag_slug="geopolitics", order="volume", ascending=False)
        cache_key: str = no_cache.cache_or_fetch.call_args.args[0]
        assert "geopolitics" in cache_key
        assert "volume" in cache_key

    @pytest.mark.asyncio
    async def test_uses_markets_ttl(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        mock_get.return_value = _gamma_response([])
        await service.get_markets(tag_slug="geopolitics")
        ttl = no_cache.cache_or_fetch.call_args.kwargs.get("ttl")
        assert ttl == _MARKETS_TTL
