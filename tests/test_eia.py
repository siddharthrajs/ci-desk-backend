"""Tests for EIAService — httpx responses and Redis are fully mocked."""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.services.eia import (
    CRUDE_STOCKS,
    CUSHING_STOCKS,
    DISTILLATE_STOCKS,
    GASOLINE_STOCKS,
    PRODUCT_SUPPLIED,
    PRODUCT_SUPPLIED_PRODUCTS,
    REFINERY_AREAS,
    REFINERY_UTILIZATION,
    SPR_LEVEL,
    SPOT_PRICES,
    SPOT_PRODUCTS,
    EIAService,
    _ROUTE_REFINERY,
    _ROUTE_SPOT_PRICES,
    _ROUTE_STOCKS,
    _ROUTE_PRODUCT_SUPPLIED,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DUMMY_REQUEST = httpx.Request("GET", "https://api.eia.gov/v2/test/data/")


def _eia_response(data: list[dict[str, Any]], status: int = 200) -> httpx.Response:
    """Build a minimal EIA v2 API response envelope with a dummy request attached.

    httpx.Response.raise_for_status() requires _request to be set; attaching a
    dummy request avoids a RuntimeError when the service calls it on our mock.
    """
    body = {"response": {"total": len(data), "data": data}} if status == 200 else {}
    return httpx.Response(status, json=body, request=_DUMMY_REQUEST)


def _make_service() -> tuple[EIAService, AsyncMock]:
    """Return (service, mock_get).

    The service used to call `self._client.get(url, params=...)` directly, so
    the second tuple element was the client's `.get` mock. As of the retry
    refactor it calls `request_with_retry("GET", url, params=...)` from
    `app.core.http_client`. To keep these tests unchanged, we monkey-patch the
    module-level `request_with_retry` reference with a shim that drops the
    leading method arg and forwards everything else to `mock_get` — so
    `mock_get.call_args.args[0]` is still the URL and `mock_get.return_value`
    still controls the HTTP response.
    """
    import app.services.eia as eia_mod
    mock_get = AsyncMock()

    async def _shim(_method: str, url: str, **kwargs: Any) -> Any:
        return await mock_get(url, **kwargs)

    eia_mod.request_with_retry = _shim  # type: ignore[assignment]

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    service = EIAService(client=mock_client, api_key="test_key")
    return service, mock_get


def _stock_row(period: str, value: str, product: str = "EPC0", area: str = "NUS") -> dict:
    return {"period": period, "product": product, "area": area, "process": "SAX", "value": value}


def _price_row(period: str, value: str, product: str) -> dict:
    return {"period": period, "product": product, "value": value}


# ---------------------------------------------------------------------------
# no_cache fixture — bypasses Redis so fetch_fn is always called directly
# ---------------------------------------------------------------------------

@pytest.fixture
def no_cache():
    async def passthrough(key: str, fn: Any, **kw: Any) -> Any:
        return await fn()

    mock_cache = MagicMock()
    mock_cache.cache_or_fetch = AsyncMock(side_effect=passthrough)
    with patch("app.services.eia.get_cache", return_value=mock_cache):
        yield mock_cache


# =============================================================================
# _parse_series — static helper
# =============================================================================

class TestParseSeries:
    def test_converts_string_values_to_float(self) -> None:
        rows = [
            {"period": "2024-01-12", "value": "450000"},
            {"period": "2024-01-05", "value": "448000"},
        ]
        result = EIAService._parse_series(rows)
        assert result[0]["value"] == 450000.0
        assert isinstance(result[0]["value"], float)

    def test_computes_wow_change(self) -> None:
        rows = [
            {"period": "2024-01-12", "value": "450000"},
            {"period": "2024-01-05", "value": "448000"},
        ]
        result = EIAService._parse_series(rows)
        assert result[0]["wow_change"] == 2000.0
        assert result[0]["wow_pct_change"] == round(2000 / 448000 * 100, 2)

    def test_oldest_row_has_none_deltas(self) -> None:
        rows = [
            {"period": "2024-01-12", "value": "450000"},
            {"period": "2024-01-05", "value": "448000"},
        ]
        result = EIAService._parse_series(rows)
        assert result[-1]["wow_change"] is None
        assert result[-1]["wow_pct_change"] is None

    def test_negative_wow_change(self) -> None:
        rows = [
            {"period": "2024-01-12", "value": "440000"},
            {"period": "2024-01-05", "value": "448000"},
        ]
        result = EIAService._parse_series(rows)
        assert result[0]["wow_change"] == -8000.0
        assert result[0]["wow_pct_change"] < 0

    def test_skips_non_numeric_values(self) -> None:
        rows = [
            {"period": "2024-01-12", "value": "NA"},
            {"period": "2024-01-05", "value": "448000"},
        ]
        result = EIAService._parse_series(rows)
        assert len(result) == 1
        assert result[0]["period"] == "2024-01-05"

    def test_skips_none_values(self) -> None:
        rows = [
            {"period": "2024-01-12", "value": None},
            {"period": "2024-01-05", "value": "448000"},
        ]
        result = EIAService._parse_series(rows)
        assert len(result) == 1

    def test_empty_rows_returns_empty_list(self) -> None:
        assert EIAService._parse_series([]) == []

    def test_single_row_has_none_deltas(self) -> None:
        rows = [{"period": "2024-01-12", "value": "450000"}]
        result = EIAService._parse_series(rows)
        assert len(result) == 1
        assert result[0]["wow_change"] is None
        assert result[0]["wow_pct_change"] is None

    def test_zero_previous_value_pct_is_none(self) -> None:
        rows = [
            {"period": "2024-01-12", "value": "1000"},
            {"period": "2024-01-05", "value": "0"},
        ]
        result = EIAService._parse_series(rows)
        assert result[0]["wow_change"] == 1000.0
        assert result[0]["wow_pct_change"] is None

    def test_result_preserves_period_strings(self) -> None:
        rows = [{"period": "2024-W02", "value": "100"}]
        result = EIAService._parse_series(rows)
        assert result[0]["period"] == "2024-W02"


# =============================================================================
# _parse_grouped — static helper
# =============================================================================

class TestParseGrouped:
    def test_groups_rows_by_key(self) -> None:
        rows = [
            {"period": "2024-01-12", "area": "NUS",   "value": "90"},
            {"period": "2024-01-12", "area": "R10",   "value": "85"},
            {"period": "2024-01-05", "area": "NUS",   "value": "89"},
            {"period": "2024-01-05", "area": "R10",   "value": "84"},
        ]
        label_map = {"NUS": "national", "R10": "padd1"}
        result = EIAService._parse_grouped(rows, "area", label_map)

        assert set(result.keys()) == {"national", "padd1"}
        assert len(result["national"]) == 2
        assert len(result["padd1"]) == 2

    def test_unknown_codes_are_dropped(self) -> None:
        rows = [
            {"period": "2024-01-12", "area": "UNKNOWN", "value": "90"},
            {"period": "2024-01-12", "area": "NUS",     "value": "85"},
        ]
        label_map = {"NUS": "national"}
        result = EIAService._parse_grouped(rows, "area", label_map)
        assert len(result["national"]) == 1

    def test_empty_rows_returns_empty_groups(self) -> None:
        label_map = {"NUS": "national", "R10": "padd1"}
        result = EIAService._parse_grouped([], "area", label_map)
        assert result == {"national": [], "padd1": []}

    def test_wow_change_applied_within_each_group(self) -> None:
        rows = [
            {"period": "2024-01-12", "product": "EPM0", "value": "220000"},
            {"period": "2024-01-05", "product": "EPM0", "value": "210000"},
        ]
        label_map = {"EPM0": "gasoline"}
        result = EIAService._parse_grouped(rows, "product", label_map)
        assert result["gasoline"][0]["wow_change"] == 10000.0


# =============================================================================
# Public methods — happy-path HTTP + cache integration
# =============================================================================

class TestGetCrudeStocks:
    @pytest.mark.asyncio
    async def test_returns_parsed_data(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        mock_get.return_value = _eia_response([
            _stock_row("2024-01-12", "450000"),
            _stock_row("2024-01-05", "448000"),
        ])
        result = await service.get_crude_stocks()
        assert len(result) == 2
        assert result[0]["period"] == "2024-01-12"
        assert result[0]["value"] == 450000.0

    @pytest.mark.asyncio
    async def test_hits_correct_route(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        mock_get.return_value = _eia_response([_stock_row("2024-01-12", "450000")])
        await service.get_crude_stocks()
        url = mock_get.call_args.args[0]
        assert _ROUTE_STOCKS in url

    @pytest.mark.asyncio
    async def test_uses_correct_cache_key(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        mock_get.return_value = _eia_response([_stock_row("2024-01-12", "450000")])
        await service.get_crude_stocks()
        cache_key = no_cache.cache_or_fetch.call_args.args[0]
        assert cache_key == "eia:crude_stocks"

    @pytest.mark.asyncio
    async def test_sends_api_key(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        mock_get.return_value = _eia_response([_stock_row("2024-01-12", "450000")])
        await service.get_crude_stocks()
        params = mock_get.call_args.kwargs.get("params", {})
        assert params.get("api_key") == "test_key"


class TestGetCushingStocks:
    @pytest.mark.asyncio
    async def test_returns_parsed_data(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        mock_get.return_value = _eia_response([
            _stock_row("2024-01-12", "25000", area="Y35NY"),
        ])
        result = await service.get_cushing_stocks()
        assert result[0]["value"] == 25000.0

    @pytest.mark.asyncio
    async def test_cache_key(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        mock_get.return_value = _eia_response([_stock_row("2024-01-12", "25000")])
        await service.get_cushing_stocks()
        assert no_cache.cache_or_fetch.call_args.args[0] == "eia:cushing_stocks"


class TestGetGasolineStocks:
    @pytest.mark.asyncio
    async def test_returns_parsed_data(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        mock_get.return_value = _eia_response([
            _stock_row("2024-01-12", "220000", product="EPM0"),
            _stock_row("2024-01-05", "218000", product="EPM0"),
        ])
        result = await service.get_gasoline_stocks()
        assert result[0]["wow_change"] == 2000.0

    @pytest.mark.asyncio
    async def test_cache_key(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        mock_get.return_value = _eia_response([_stock_row("2024-01-12", "220000")])
        await service.get_gasoline_stocks()
        assert no_cache.cache_or_fetch.call_args.args[0] == "eia:gasoline_stocks"


class TestGetDistillateStocks:
    @pytest.mark.asyncio
    async def test_returns_parsed_data(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        mock_get.return_value = _eia_response([
            _stock_row("2024-01-12", "115000", product="EPD0"),
        ])
        result = await service.get_distillate_stocks()
        assert result[0]["value"] == 115000.0

    @pytest.mark.asyncio
    async def test_cache_key(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        mock_get.return_value = _eia_response([_stock_row("2024-01-12", "115000")])
        await service.get_distillate_stocks()
        assert no_cache.cache_or_fetch.call_args.args[0] == "eia:distillate_stocks"


class TestGetSprLevel:
    @pytest.mark.asyncio
    async def test_returns_parsed_data(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        mock_get.return_value = _eia_response([
            _stock_row("2024-01-12", "350000", product="EPCO"),
            _stock_row("2024-01-05", "351000", product="EPCO"),
        ])
        result = await service.get_spr_level()
        assert result[0]["wow_change"] == -1000.0

    @pytest.mark.asyncio
    async def test_cache_key(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        mock_get.return_value = _eia_response([_stock_row("2024-01-12", "350000")])
        await service.get_spr_level()
        assert no_cache.cache_or_fetch.call_args.args[0] == "eia:spr_level"


class TestGetRefineryUtilization:
    def _make_rows(self) -> list[dict]:
        areas = list(REFINERY_AREAS.keys())
        rows = []
        for area in areas:
            rows.append({"period": "2024-01-12", "area": area, "process": "YOP", "value": "90"})
            rows.append({"period": "2024-01-05", "area": area, "process": "YOP", "value": "88"})
        return rows

    @pytest.mark.asyncio
    async def test_returns_all_areas(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        mock_get.return_value = _eia_response(self._make_rows())
        result = await service.get_refinery_utilization()
        assert set(result.keys()) == set(REFINERY_AREAS.values())

    @pytest.mark.asyncio
    async def test_each_area_has_parsed_data(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        mock_get.return_value = _eia_response(self._make_rows())
        result = await service.get_refinery_utilization()
        for label in REFINERY_AREAS.values():
            assert len(result[label]) == 2
            assert result[label][0]["wow_change"] == 2.0

    @pytest.mark.asyncio
    async def test_hits_refinery_route(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        mock_get.return_value = _eia_response(self._make_rows())
        await service.get_refinery_utilization()
        url = mock_get.call_args.args[0]
        assert _ROUTE_REFINERY in url

    @pytest.mark.asyncio
    async def test_cache_key(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        mock_get.return_value = _eia_response(self._make_rows())
        await service.get_refinery_utilization()
        assert no_cache.cache_or_fetch.call_args.args[0] == "eia:refinery_utilization"


class TestGetProductSupplied:
    def _make_rows(self) -> list[dict]:
        rows = []
        for product_code in PRODUCT_SUPPLIED_PRODUCTS:
            rows.append({
                "period": "2024-01-12", "product": product_code,
                "area": "NUS", "process": "VPP", "value": "9000",
            })
            rows.append({
                "period": "2024-01-05", "product": product_code,
                "area": "NUS", "process": "VPP", "value": "8900",
            })
        return rows

    @pytest.mark.asyncio
    async def test_returns_all_products(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        mock_get.return_value = _eia_response(self._make_rows())
        result = await service.get_product_supplied()
        assert set(result.keys()) == set(PRODUCT_SUPPLIED_PRODUCTS.values())

    @pytest.mark.asyncio
    async def test_each_product_has_wow_change(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        mock_get.return_value = _eia_response(self._make_rows())
        result = await service.get_product_supplied()
        for label in PRODUCT_SUPPLIED_PRODUCTS.values():
            assert result[label][0]["wow_change"] == 100.0

    @pytest.mark.asyncio
    async def test_hits_product_supplied_route(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        mock_get.return_value = _eia_response(self._make_rows())
        await service.get_product_supplied()
        url = mock_get.call_args.args[0]
        assert _ROUTE_PRODUCT_SUPPLIED in url

    @pytest.mark.asyncio
    async def test_cache_key(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        mock_get.return_value = _eia_response(self._make_rows())
        await service.get_product_supplied()
        assert no_cache.cache_or_fetch.call_args.args[0] == "eia:product_supplied"


class TestGetSpotPrices:
    def _make_rows(self) -> list[dict]:
        rows = []
        for product_code in SPOT_PRODUCTS:
            rows.append(_price_row("2024-01-12", "72.50", product_code))
            rows.append(_price_row("2024-01-11", "71.80", product_code))
        return rows

    @pytest.mark.asyncio
    async def test_returns_all_products(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        mock_get.return_value = _eia_response(self._make_rows())
        result = await service.get_spot_prices()
        assert set(result.keys()) == set(SPOT_PRODUCTS.values())

    @pytest.mark.asyncio
    async def test_each_price_has_wow_change(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        mock_get.return_value = _eia_response(self._make_rows())
        result = await service.get_spot_prices()
        assert result["wti"][0]["wow_change"] == pytest.approx(0.7, abs=0.01)

    @pytest.mark.asyncio
    async def test_uses_daily_frequency(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        mock_get.return_value = _eia_response(self._make_rows())
        await service.get_spot_prices()
        params = mock_get.call_args.kwargs.get("params", {})
        assert params.get("frequency") == "daily"

    @pytest.mark.asyncio
    async def test_hits_spot_price_route(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        mock_get.return_value = _eia_response(self._make_rows())
        await service.get_spot_prices()
        url = mock_get.call_args.args[0]
        assert _ROUTE_SPOT_PRICES in url

    @pytest.mark.asyncio
    async def test_cache_key(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        mock_get.return_value = _eia_response(self._make_rows())
        await service.get_spot_prices()
        assert no_cache.cache_or_fetch.call_args.args[0] == "eia:spot_prices"


# =============================================================================
# Edge cases: HTTP errors and empty responses
# =============================================================================

class TestEdgeCases:
    @pytest.mark.asyncio
    async def test_http_error_propagates(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        mock_get.return_value = _eia_response([], status=500)
        with pytest.raises(httpx.HTTPStatusError):
            await service.get_crude_stocks()

    @pytest.mark.asyncio
    async def test_empty_data_array_returns_empty_list(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        mock_get.return_value = _eia_response([])
        result = await service.get_crude_stocks()
        assert result == []

    @pytest.mark.asyncio
    async def test_all_invalid_values_returns_empty_list(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        mock_get.return_value = _eia_response([
            _stock_row("2024-01-12", "NA"),
            _stock_row("2024-01-05", "--"),
        ])
        result = await service.get_crude_stocks()
        assert result == []

    @pytest.mark.asyncio
    async def test_mixed_valid_invalid_values(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        mock_get.return_value = _eia_response([
            _stock_row("2024-01-12", "450000"),
            _stock_row("2024-01-05", "NA"),
        ])
        result = await service.get_crude_stocks()
        assert len(result) == 1
        assert result[0]["wow_change"] is None

    @pytest.mark.asyncio
    async def test_weekly_frequency_is_default(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        mock_get.return_value = _eia_response([_stock_row("2024-01-12", "450000")])
        await service.get_crude_stocks()
        params = mock_get.call_args.kwargs.get("params", {})
        assert params.get("frequency") == "weekly"

    @pytest.mark.asyncio
    async def test_sort_is_newest_first(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        mock_get.return_value = _eia_response([_stock_row("2024-01-12", "450000")])
        await service.get_crude_stocks()
        params = mock_get.call_args.kwargs.get("params", {})
        assert params.get("sort[0][column]") == "period"
        assert params.get("sort[0][direction]") == "desc"
