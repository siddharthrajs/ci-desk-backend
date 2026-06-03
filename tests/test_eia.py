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


def _csv_response(text: str, status: int = 200) -> httpx.Response:
    """Plain-text (CSV) httpx response — for JODI bulk-file fetches."""
    return httpx.Response(status, text=text, request=_DUMMY_REQUEST)


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


# =============================================================================
# OPEC+ subtab — international route, crude/liquids basis toggle
# =============================================================================

def _intl_row(period: str, value: str, country: str = "SAU", product: str = "57") -> dict:
    """One EIA international data row (production, TBPD)."""
    return {
        "period": period,
        "countryRegionId": country,
        "productId": product,
        "activityId": "1",
        "value": value,
        "unit": "TBPD",
    }


class TestGetOpecProduction:
    def _rows(self, product: str = "57") -> list[dict]:
        rows = []
        for country in ("SAU", "RUS", "IRQ"):
            rows.append(_intl_row("2026-01", "10000", country, product))
            rows.append(_intl_row("2025-12", "9900", country, product))
        return rows

    @pytest.mark.asyncio
    async def test_default_basis_requests_crude_product_57(self, no_cache: MagicMock) -> None:
        """Default basis is crude oil + lease condensate (productId 57), NOT 55."""
        service, mock_get = _make_service()
        mock_get.return_value = _eia_response(self._rows())
        await service.get_opec_production()
        params = mock_get.call_args.kwargs.get("params", {})
        assert params.get("facets[productId][]") == ["57"]

    @pytest.mark.asyncio
    async def test_liquids_basis_requests_product_55(self, no_cache: MagicMock) -> None:
        """basis='liquids' switches to total liquids incl. NGPL (productId 55)."""
        service, mock_get = _make_service()
        mock_get.return_value = _eia_response(self._rows("55"))
        await service.get_opec_production(basis="liquids")
        params = mock_get.call_args.kwargs.get("params", {})
        assert params.get("facets[productId][]") == ["55"]

    @pytest.mark.asyncio
    async def test_distinct_cache_keys_per_basis(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        mock_get.return_value = _eia_response(self._rows())
        await service.get_opec_production()
        crude_key = no_cache.cache_or_fetch.call_args.args[0]
        await service.get_opec_production(basis="liquids")
        liquids_key = no_cache.cache_or_fetch.call_args.args[0]
        assert crude_key != liquids_key

    @pytest.mark.asyncio
    async def test_hits_international_route(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        mock_get.return_value = _eia_response(self._rows())
        await service.get_opec_production()
        url = mock_get.call_args.args[0]
        assert "international" in url

    @pytest.mark.asyncio
    async def test_returns_table_values_in_mbd(self, no_cache: MagicMock) -> None:
        """TBPD input is divided by 1000 to MBD in the country table."""
        service, mock_get = _make_service()
        mock_get.return_value = _eia_response(self._rows())
        result = await service.get_opec_production()
        sau = next(r for r in result["table"] if r["iso3"] == "SAU")
        assert sau["latest_mbd"] == 10.0


class TestGetOpecHistory:
    def _rows(self, product: str = "57") -> list[dict]:
        rows = []
        for country in ("SAU", "RUS"):
            rows.append(_intl_row("2026-01", "10000", country, product))
            rows.append(_intl_row("2025-12", "9900", country, product))
        return rows

    @pytest.mark.asyncio
    async def test_default_basis_requests_crude_product_57(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        mock_get.return_value = _eia_response(self._rows())
        await service.get_opec_history()
        params = mock_get.call_args.kwargs.get("params", {})
        assert params.get("facets[productId][]") == ["57"]

    @pytest.mark.asyncio
    async def test_liquids_basis_requests_product_55(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        mock_get.return_value = _eia_response(self._rows("55"))
        await service.get_opec_history(basis="liquids")
        params = mock_get.call_args.kwargs.get("params", {})
        assert params.get("facets[productId][]") == ["55"]

    @pytest.mark.asyncio
    async def test_distinct_cache_keys_per_basis(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        mock_get.return_value = _eia_response(self._rows())
        await service.get_opec_history()
        crude_key = no_cache.cache_or_fetch.call_args.args[0]
        await service.get_opec_history(basis="liquids")
        liquids_key = no_cache.cache_or_fetch.call_args.args[0]
        assert crude_key != liquids_key


# =============================================================================
# OPEC+ overview — STEO route (spare/production capacity, splits, balance)
# =============================================================================

def _steo_row(period: str, value: str, series: str) -> dict:
    """One EIA STEO data row. STEO values are already mb/d (NOT divided)."""
    return {
        "period": period,
        "seriesId": series,
        "seriesDescription": series,
        "value": value,
        "unit": "million barrels per day",
    }


class TestGetOpecOverview:
    """Split-sources overview: actual/forecast boundary comes from EIA
    international (the clean production source); STEO supplies spare/production
    capacity, structural splits and the world balance. STEO's broken trailing
    months (capacity that drops impossibly) fall after the cutoff → forecast,
    so the hero never shows them. Overview is basis-independent (crude STEO)."""

    # international OPEC aggregate — latest actual month is 2026-02 (the cutoff).
    def _intl_rows(self) -> list[dict]:
        return [
            _intl_row("2026-02", "30680", "OPEC", "57"),
            _intl_row("2026-01", "30790", "OPEC", "57"),
        ]

    # STEO: clean through 2026-02; 2026-03/04 are broken (capacity halves);
    # 2027-12 is model forecast.
    def _steo_rows(self) -> list[dict]:
        return [
            # COPC_OPEC — capacity
            _steo_row("2027-12", "26.955", "COPC_OPEC"),
            _steo_row("2026-04", "16.94",  "COPC_OPEC"),   # broken
            _steo_row("2026-02", "28.13",  "COPC_OPEC"),   # clean (== cutoff)
            _steo_row("2026-01", "28.06",  "COPC_OPEC"),
            # COPS_OPEC — spare
            _steo_row("2027-12", "2.58",  "COPS_OPEC"),
            _steo_row("2026-04", "0.02",  "COPS_OPEC"),    # broken
            _steo_row("2026-02", "2.22",  "COPS_OPEC"),    # clean
            # COPR_OPEC — production (for util + split)
            _steo_row("2026-02", "25.91", "COPR_OPEC"),
            # splits
            _steo_row("2026-02", "14.30", "COPR_OPECPLUS_OTHER"),
            _steo_row("2026-02", "20.10", "COPR_NONOPECPLUS_XUS"),
            # T3 — net inventory withdrawals (negative = build = surplus)
            _steo_row("2027-12", "-3.77", "T3_STCHANGE_WORLD"),
            _steo_row("2026-02", "-1.00", "T3_STCHANGE_WORLD"),
        ]

    def _wire(self, mock_get: AsyncMock) -> None:
        """international call first, STEO second."""
        mock_get.side_effect = [
            _eia_response(self._intl_rows()),
            _eia_response(self._steo_rows()),
        ]

    @pytest.mark.asyncio
    async def test_hits_steo_route(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        self._wire(mock_get)
        await service.get_opec_overview()
        urls = [c.args[0] for c in mock_get.call_args_list]
        assert any("steo" in u for u in urls)

    @pytest.mark.asyncio
    async def test_requests_capacity_spare_and_balance_series(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        self._wire(mock_get)
        await service.get_opec_overview()
        steo_call = next(c for c in mock_get.call_args_list if "steo" in c.args[0])
        series = steo_call.kwargs.get("params", {}).get("facets[seriesId][]", [])
        assert {"COPC_OPEC", "COPS_OPEC", "T3_STCHANGE_WORLD"} <= set(series)

    @pytest.mark.asyncio
    async def test_steo_values_not_divided(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        self._wire(mock_get)
        result = await service.get_opec_overview()
        assert result["hero"]["production_capacity_mbd"] == 28.13

    @pytest.mark.asyncio
    async def test_hero_anchored_to_intl_cutoff_skips_broken_tail(self, no_cache: MagicMock) -> None:
        """Hero uses 2026-02 (international cutoff), NOT STEO's broken 2026-04."""
        service, mock_get = _make_service()
        self._wire(mock_get)
        result = await service.get_opec_overview()
        assert result["hero"]["last_actual_period"] == "2026-02"
        assert result["hero"]["spare_capacity_mbd"] == 2.22   # clean, not 0.02

    @pytest.mark.asyncio
    async def test_history_forecast_boundary_is_intl_cutoff(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        self._wire(mock_get)
        result = await service.get_opec_overview()
        by_period = {p["period"]: p for p in result["capacity_history"]}
        assert by_period["2026-02-01"]["is_forecast"] is False
        assert by_period["2026-04-01"]["is_forecast"] is True   # broken month → forecast side
        assert by_period["2027-12-01"]["is_forecast"] is True

    @pytest.mark.asyncio
    async def test_market_balance_from_t3_surplus(self, no_cache: MagicMock) -> None:
        """T3 net withdrawals −1.0 (a build) → +1.0 surplus."""
        service, mock_get = _make_service()
        self._wire(mock_get)
        result = await service.get_opec_overview()
        assert result["hero"]["market_balance_mbd"] == pytest.approx(1.0, abs=0.001)
        assert result["hero"]["market_balance_label"] == "surplus"

    @pytest.mark.asyncio
    async def test_split_history_present(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        self._wire(mock_get)
        result = await service.get_opec_overview()
        feb = next(p for p in result["split_history"] if p["period"] == "2026-02-01")
        assert feb["opec_plus_other"] == 14.30
        assert feb["non_opec_plus"] == 20.10

    @pytest.mark.asyncio
    async def test_cache_key(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        self._wire(mock_get)
        await service.get_opec_overview()
        assert no_cache.cache_or_fetch.call_args.args[0] == "eia:opec_overview_v2"


# =============================================================================
# OPEC+ disruptions — STEO PADI_* (barrels offline by country)
# =============================================================================

class TestGetOpecDisruptions:
    """Anchored to international's latest OPEC month (2026-01 here) so STEO's
    broken trailing months — which inflate OPEC-member 'disruptions' (Saudi
    jumps 0.07→3.57) — are excluded from the snapshot."""

    def _intl_rows(self) -> list[dict]:
        return [
            _intl_row("2026-01", "30790", "OPEC", "57"),
            _intl_row("2025-12", "30680", "OPEC", "57"),
        ]

    def _steo_rows(self) -> list[dict]:
        return [
            # Saudi: broken 2026-04 spike vs clean 2026-01/2025-12
            _steo_row("2026-04", "3.57", "PADI_SA"),
            _steo_row("2026-01", "0.07", "PADI_SA"),
            _steo_row("2025-12", "0.06", "PADI_SA"),
            # Russia (non-OPEC, genuine outages)
            _steo_row("2026-04", "1.10", "PADI_RS"),
            _steo_row("2026-01", "0.85", "PADI_RS"),
            _steo_row("2025-12", "0.80", "PADI_RS"),
            # Nigeria
            _steo_row("2026-01", "0.20", "PADI_NI"),
            _steo_row("2025-12", "0.20", "PADI_NI"),
        ]

    def _wire(self, mock_get: AsyncMock) -> None:
        mock_get.side_effect = [
            _eia_response(self._intl_rows()),   # cutoff lookup
            _eia_response(self._steo_rows()),   # PADI
        ]

    @pytest.mark.asyncio
    async def test_hits_steo_route(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        self._wire(mock_get)
        await service.get_opec_disruptions()
        assert any("steo" in c.args[0] for c in mock_get.call_args_list)

    @pytest.mark.asyncio
    async def test_requests_padi_series(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        self._wire(mock_get)
        await service.get_opec_disruptions()
        steo_call = next(c for c in mock_get.call_args_list if "steo" in c.args[0])
        series = steo_call.kwargs.get("params", {}).get("facets[seriesId][]", [])
        assert "PADI_RS" in series

    @pytest.mark.asyncio
    async def test_snapshot_anchored_skips_broken_month(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        self._wire(mock_get)
        result = await service.get_opec_disruptions()
        assert result["latest_period"] == "2026-01"
        saudi = next(c for c in result["countries"] if c["name"] == "Saudi Arabia")
        assert saudi["latest_mbd"] == 0.07   # clean, not the 3.57 broken spike

    @pytest.mark.asyncio
    async def test_total_is_sum_at_cutoff(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        self._wire(mock_get)
        result = await service.get_opec_disruptions()
        # 2026-01: Russia 0.85 + Nigeria 0.20 + Saudi 0.07 = 1.12
        assert result["total_mbd"] == pytest.approx(1.12, abs=0.001)

    @pytest.mark.asyncio
    async def test_countries_sorted_desc(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        self._wire(mock_get)
        result = await service.get_opec_disruptions()
        assert result["countries"][0]["name"] == "Russia"
        assert result["countries"][0]["mom"] == pytest.approx(0.05, abs=0.001)  # 0.85 − 0.80

    @pytest.mark.asyncio
    async def test_history_per_country_newest_first(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        self._wire(mock_get)
        result = await service.get_opec_disruptions()
        rus = result["series"]["Russia"]
        assert rus[0]["period"] == "2026-01-01"   # broken 2026-04 excluded
        assert rus[0]["value"] == 0.85

    @pytest.mark.asyncio
    async def test_cache_key(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        self._wire(mock_get)
        await service.get_opec_disruptions()
        assert no_cache.cache_or_fetch.call_args.args[0] == "eia:opec_disruptions_v1"


# =============================================================================
# OPEC+ compliance — quota JSON joined with international actuals
# =============================================================================

class TestGetOpecCompliance:
    QUOTAS = {
        "as_of": "2026-06",
        "source": "test",
        "required_mbd": {"SAU": 10.0, "RUS": 9.5, "IRQ": 4.0},
    }

    def _intl_rows(self) -> list[dict]:
        # actual crude (TBPD → /1000): SAU 10.5, RUS 9.3, IRQ 4.2
        return [
            _intl_row("2026-01", "10500", "SAU", "57"),
            _intl_row("2025-12", "10400", "SAU", "57"),
            _intl_row("2026-01", "9300", "RUS", "57"),
            _intl_row("2026-01", "4200", "IRQ", "57"),
        ]

    @pytest.mark.asyncio
    async def test_joins_actual_with_quota(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        mock_get.return_value = _eia_response(self._intl_rows())
        with patch("app.services.eia._load_opec_quotas", return_value=self.QUOTAS):
            result = await service.get_opec_compliance()
        sau = next(r for r in result["rows"] if r["iso3"] == "SAU")
        assert sau["required_mbd"] == 10.0
        assert sau["actual_mbd"] == 10.5
        assert sau["delta_mbd"] == pytest.approx(0.5, abs=0.001)

    @pytest.mark.asyncio
    async def test_over_under_status(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        mock_get.return_value = _eia_response(self._intl_rows())
        with patch("app.services.eia._load_opec_quotas", return_value=self.QUOTAS):
            result = await service.get_opec_compliance()
        by_iso = {r["iso3"]: r for r in result["rows"]}
        assert by_iso["SAU"]["status"] == "over"   # 10.5 > 10.0
        assert by_iso["RUS"]["status"] == "under"  # 9.3 < 9.5

    @pytest.mark.asyncio
    async def test_group_totals(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        mock_get.return_value = _eia_response(self._intl_rows())
        with patch("app.services.eia._load_opec_quotas", return_value=self.QUOTAS):
            result = await service.get_opec_compliance()
        assert result["total_required_mbd"] == pytest.approx(23.5, abs=0.001)
        assert result["total_actual_mbd"] == pytest.approx(24.0, abs=0.001)
        assert result["total_delta_mbd"] == pytest.approx(0.5, abs=0.001)

    @pytest.mark.asyncio
    async def test_carries_quota_metadata(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        mock_get.return_value = _eia_response(self._intl_rows())
        with patch("app.services.eia._load_opec_quotas", return_value=self.QUOTAS):
            result = await service.get_opec_compliance()
        assert result["as_of"] == "2026-06"
        assert result["actual_period"] == "2026-01"

    @pytest.mark.asyncio
    async def test_cache_key(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        mock_get.return_value = _eia_response(self._intl_rows())
        with patch("app.services.eia._load_opec_quotas", return_value=self.QUOTAS):
            await service.get_opec_compliance()
        # compliance is the outer cache; it nests the production fetch inside.
        keys = [c.args[0] for c in no_cache.cache_or_fetch.call_args_list]
        assert keys[0] == "eia:opec_compliance_v1"


# =============================================================================
# OPEC+ cross-check — EIA international vs JODI, same 5 reporting members
# =============================================================================

class TestGetOpecCrossCheck:
    # EIA international (productId 57, ISO-3, TBPD) for the 5 JODI-reporting members.
    def _intl_rows(self) -> list[dict]:
        vals = {"SAU": "10000", "KWT": "2600", "NGA": "1400", "DZA": "980", "VEN": "1100"}
        return [_intl_row("2025-12", v, iso, "57") for iso, v in vals.items()]

    # JODI annual CSV (ISO-2, KBD). Includes rows that must be ignored.
    JODI_CSV = (
        "REF_AREA,TIME_PERIOD,ENERGY_PRODUCT,FLOW_BREAKDOWN,UNIT_MEASURE,OBS_VALUE,ASSESSMENT_CODE\n"
        "SA,2025-12,CRUDEOIL,INDPROD,KBD,10100.0000,1\n"
        "KW,2025-12,CRUDEOIL,INDPROD,KBD,2580.0000,1\n"
        "NG,2025-12,CRUDEOIL,INDPROD,KBD,1420.0000,1\n"
        "DZ,2025-12,CRUDEOIL,INDPROD,KBD,970.0000,1\n"
        "VE,2025-12,CRUDEOIL,INDPROD,KBD,1120.0000,1\n"
        "IQ,2025-12,CRUDEOIL,INDPROD,KBD,-,3\n"            # non-reporter / missing → skip
        "SA,2025-12,CRUDEOIL,CLOSTLV,KBD,5000.0000,3\n"    # wrong flow → skip
        "SA,2025-12,CRUDEOIL,INDPROD,CONVBBL,300000,1\n"   # wrong unit → skip
    )

    def _wire(self, mock_get: AsyncMock) -> None:
        # 1) international (via get_opec_production)  2) JODI CSV (one patched URL)
        mock_get.side_effect = [_eia_response(self._intl_rows()), _csv_response(self.JODI_CSV)]

    @pytest.mark.asyncio
    async def test_fetches_jodi_and_international(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        self._wire(mock_get)
        with patch("app.services.eia._jodi_csv_urls", return_value=["http://test/jodi.csv"]):
            await service.get_opec_cross_check()
        urls = [c.args[0] for c in mock_get.call_args_list]
        assert any("jodi" in u for u in urls)
        assert any("international" in u for u in urls)

    @pytest.mark.asyncio
    async def test_history_aligns_both_sources(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        self._wire(mock_get)
        with patch("app.services.eia._jodi_csv_urls", return_value=["http://test/jodi.csv"]):
            result = await service.get_opec_cross_check()
        dec = next(h for h in result["history"] if h["period"] == "2025-12-01")
        # EIA: 10.0+2.6+1.4+0.98+1.1 = 16.08
        assert dec["eia"] == pytest.approx(16.08, abs=0.001)
        # JODI: 10.1+2.58+1.42+0.97+1.12 = 16.19 (other rows ignored)
        assert dec["jodi"] == pytest.approx(16.19, abs=0.001)

    @pytest.mark.asyncio
    async def test_latest_diff(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        self._wire(mock_get)
        with patch("app.services.eia._jodi_csv_urls", return_value=["http://test/jodi.csv"]):
            result = await service.get_opec_cross_check()
        assert result["latest_period"] == "2025-12-01"
        assert result["diff_latest"] == pytest.approx(-0.11, abs=0.001)  # eia − jodi

    @pytest.mark.asyncio
    async def test_cache_key(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        self._wire(mock_get)
        with patch("app.services.eia._jodi_csv_urls", return_value=["http://test/jodi.csv"]):
            await service.get_opec_cross_check()
        keys = [c.args[0] for c in no_cache.cache_or_fetch.call_args_list]
        assert keys[0] == "eia:opec_cross_check_v1"
