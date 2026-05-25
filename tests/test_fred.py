"""Tests for FREDService — httpx responses and Redis are fully mocked."""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.services.fred import (
    SERIES_DXY,
    SERIES_FED_FUNDS,
    SERIES_US10Y,
    SERIES_WTI,
    FREDService,
    _DAILY_TTL,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DUMMY_REQUEST = httpx.Request("GET", "https://api.stlouisfed.org/fred/series/observations")


def _fred_response(observations: list[dict[str, Any]], status: int = 200) -> httpx.Response:
    """Build a minimal FRED observations API response with a dummy request attached."""
    body = {"observations": observations} if status == 200 else {}
    return httpx.Response(status, json=body, request=_DUMMY_REQUEST)


def _obs(date: str, value: str) -> dict[str, Any]:
    return {"realtime_start": date, "realtime_end": date, "date": date, "value": value}


def _make_service() -> tuple[FREDService, AsyncMock]:
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    service = FREDService(client=mock_client, api_key="test_key")
    return service, mock_client.get


# ---------------------------------------------------------------------------
# no_cache fixture — bypasses Redis so fetch_fn is always called directly
# ---------------------------------------------------------------------------

@pytest.fixture
def no_cache():
    async def passthrough(key: str, fn: Any, **kw: Any) -> Any:
        return await fn()

    mock_cache = MagicMock()
    mock_cache.cache_or_fetch = AsyncMock(side_effect=passthrough)
    with patch("app.services.fred.get_cache", return_value=mock_cache):
        yield mock_cache


# =============================================================================
# _parse_observations — static helper
# =============================================================================

class TestParseObservations:
    def test_converts_string_values_to_float(self) -> None:
        rows = [_obs("2024-01-12", "4.35"), _obs("2024-01-11", "4.30")]
        result = FREDService._parse_observations(rows)
        assert result[0]["value"] == 4.35
        assert isinstance(result[0]["value"], float)

    def test_preserves_date_string(self) -> None:
        rows = [_obs("2024-01-12", "4.35")]
        result = FREDService._parse_observations(rows)
        assert result[0]["date"] == "2024-01-12"

    def test_drops_dot_missing_values(self) -> None:
        rows = [_obs("2024-01-12", "."), _obs("2024-01-11", "4.30")]
        result = FREDService._parse_observations(rows)
        assert len(result) == 1
        assert result[0]["date"] == "2024-01-11"

    def test_drops_none_values(self) -> None:
        rows = [{"date": "2024-01-12", "value": None}, _obs("2024-01-11", "4.30")]
        result = FREDService._parse_observations(rows)
        assert len(result) == 1

    def test_drops_non_numeric_strings(self) -> None:
        rows = [_obs("2024-01-12", "NA"), _obs("2024-01-11", "4.30")]
        result = FREDService._parse_observations(rows)
        assert len(result) == 1

    def test_all_missing_returns_empty(self) -> None:
        rows = [_obs("2024-01-12", "."), _obs("2024-01-11", ".")]
        assert FREDService._parse_observations(rows) == []

    def test_empty_rows_returns_empty(self) -> None:
        assert FREDService._parse_observations([]) == []

    def test_multiple_valid_rows_all_returned(self) -> None:
        rows = [_obs("2024-01-12", "4.35"), _obs("2024-01-11", "4.30"), _obs("2024-01-10", "4.25")]
        result = FREDService._parse_observations(rows)
        assert len(result) == 3


# =============================================================================
# get_fred_series — generic helper
# =============================================================================

class TestGetFredSeries:
    @pytest.mark.asyncio
    async def test_returns_latest_value(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        mock_get.return_value = _fred_response([
            _obs("2024-01-12", "4.35"),
            _obs("2024-01-11", "4.30"),
        ])
        result = await service.get_fred_series("DGS10")
        assert result["latest_value"] == 4.35

    @pytest.mark.asyncio
    async def test_returns_latest_date(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        mock_get.return_value = _fred_response([_obs("2024-01-12", "4.35")])
        result = await service.get_fred_series("DGS10")
        assert result["latest_date"] == "2024-01-12"

    @pytest.mark.asyncio
    async def test_returns_series_id(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        mock_get.return_value = _fred_response([_obs("2024-01-12", "4.35")])
        result = await service.get_fred_series("DGS10")
        assert result["series_id"] == "DGS10"

    @pytest.mark.asyncio
    async def test_observations_list_present(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        mock_get.return_value = _fred_response([
            _obs("2024-01-12", "4.35"),
            _obs("2024-01-11", "4.30"),
        ])
        result = await service.get_fred_series("DGS10")
        assert len(result["observations"]) == 2

    @pytest.mark.asyncio
    async def test_missing_values_skipped_in_observations(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        mock_get.return_value = _fred_response([
            _obs("2024-01-12", "."),
            _obs("2024-01-11", "4.30"),
        ])
        result = await service.get_fred_series("DGS10")
        assert len(result["observations"]) == 1
        assert result["latest_value"] == 4.30
        assert result["latest_date"] == "2024-01-11"

    @pytest.mark.asyncio
    async def test_all_missing_returns_none_latest(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        mock_get.return_value = _fred_response([_obs("2024-01-12", ".")])
        result = await service.get_fred_series("DGS10")
        assert result["latest_value"] is None
        assert result["latest_date"] is None
        assert result["observations"] == []

    @pytest.mark.asyncio
    async def test_sends_api_key(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        mock_get.return_value = _fred_response([_obs("2024-01-12", "4.35")])
        await service.get_fred_series("DGS10")
        params = mock_get.call_args.kwargs.get("params", {})
        assert params.get("api_key") == "test_key"

    @pytest.mark.asyncio
    async def test_sends_correct_series_id(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        mock_get.return_value = _fred_response([_obs("2024-01-12", "4.35")])
        await service.get_fred_series("DGS10")
        params = mock_get.call_args.kwargs.get("params", {})
        assert params.get("series_id") == "DGS10"

    @pytest.mark.asyncio
    async def test_sort_order_is_desc(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        mock_get.return_value = _fred_response([_obs("2024-01-12", "4.35")])
        await service.get_fred_series("DGS10")
        params = mock_get.call_args.kwargs.get("params", {})
        assert params.get("sort_order") == "desc"

    @pytest.mark.asyncio
    async def test_cache_key_uses_lowercase_series_id(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        mock_get.return_value = _fred_response([_obs("2024-01-12", "4.35")])
        await service.get_fred_series("DGS10")
        cache_key = no_cache.cache_or_fetch.call_args.args[0]
        assert cache_key == "fred:dgs10"

    @pytest.mark.asyncio
    async def test_uses_daily_ttl(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        mock_get.return_value = _fred_response([_obs("2024-01-12", "4.35")])
        await service.get_fred_series("DGS10")
        ttl = no_cache.cache_or_fetch.call_args.kwargs.get("ttl")
        assert ttl == _DAILY_TTL

    @pytest.mark.asyncio
    async def test_http_error_propagates(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        mock_get.return_value = _fred_response([], status=500)
        with pytest.raises(httpx.HTTPStatusError):
            await service.get_fred_series("DGS10")

    @pytest.mark.asyncio
    async def test_hits_observations_endpoint(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        mock_get.return_value = _fred_response([_obs("2024-01-12", "4.35")])
        await service.get_fred_series("DGS10")
        url = mock_get.call_args.args[0]
        assert "series/observations" in url


# =============================================================================
# Named wrappers — verify series_id and cache key
# =============================================================================

class TestNamedWrappers:
    @pytest.mark.asyncio
    async def test_get_dxy_uses_correct_series(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        mock_get.return_value = _fred_response([_obs("2024-01-12", "120.5")])
        await service.get_dxy()
        params = mock_get.call_args.kwargs.get("params", {})
        assert params.get("series_id") == SERIES_DXY

    @pytest.mark.asyncio
    async def test_get_us10y_uses_correct_series(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        mock_get.return_value = _fred_response([_obs("2024-01-12", "4.35")])
        await service.get_us10y()
        params = mock_get.call_args.kwargs.get("params", {})
        assert params.get("series_id") == SERIES_US10Y

    @pytest.mark.asyncio
    async def test_get_fed_funds_uses_correct_series(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        mock_get.return_value = _fred_response([_obs("2024-01-01", "5.33")])
        await service.get_fed_funds()
        params = mock_get.call_args.kwargs.get("params", {})
        assert params.get("series_id") == SERIES_FED_FUNDS

    @pytest.mark.asyncio
    async def test_get_wti_uses_correct_series(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        mock_get.return_value = _fred_response([_obs("2024-01-12", "72.50")])
        await service.get_wti()
        params = mock_get.call_args.kwargs.get("params", {})
        assert params.get("series_id") == SERIES_WTI

    @pytest.mark.asyncio
    async def test_get_dxy_cache_key(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        mock_get.return_value = _fred_response([_obs("2024-01-12", "120.5")])
        await service.get_dxy()
        cache_key = no_cache.cache_or_fetch.call_args.args[0]
        assert cache_key == f"fred:{SERIES_DXY.lower()}"

    @pytest.mark.asyncio
    async def test_get_wti_returns_observations(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        mock_get.return_value = _fred_response([
            _obs("2024-01-12", "72.50"),
            _obs("2024-01-11", "71.80"),
        ])
        result = await service.get_wti()
        assert result["latest_value"] == 72.50
        assert len(result["observations"]) == 2
