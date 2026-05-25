"""Tests for CFTCService and parse_managed_money.

parse_managed_money is a pure function tested without any mocks.
CFTCService.get_managed_money_positions is tested with mocked httpx + Redis.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.services.cftc import (
    BRENT_CODE,
    COMMODITY_LABELS,
    WTI_CODE,
    _COT_TTL_SECONDS,
    _LOOKBACK_WEEKS,
    _RESOURCE_URL,
    CFTCService,
    parse_managed_money,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DUMMY_REQUEST = httpx.Request("GET", _RESOURCE_URL)


def _cftc_response(rows: list[dict[str, Any]], status: int = 200) -> httpx.Response:
    """Build a mock CFTC SODA response with a dummy request attached."""
    body = rows if status == 200 else {}
    return httpx.Response(status, json=body, request=_DUMMY_REQUEST)


def _row(
    date: str,
    long_pos: int,
    short_pos: int,
    code: str = WTI_CODE,
) -> dict[str, str]:
    """Build a minimal CFTC SODA record row (all values are strings, as SODA returns)."""
    return {
        "report_date_as_yyyy_mm_dd": date,
        "m_money_positions_long_all":  str(long_pos),
        "m_money_positions_short_all": str(short_pos),
        "market_and_exchange_names":   "CRUDE OIL, LIGHT SWEET - NEW YORK MERCANTILE EXCHANGE",
        "cftc_commodity_code":         code,
    }


def _make_service() -> tuple[CFTCService, AsyncMock]:
    """Return (service, mock_get) with an AsyncMock httpx client."""
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    service = CFTCService(client=mock_client)
    return service, mock_client.get


# ---------------------------------------------------------------------------
# no_cache fixture — bypasses Redis so fetch_fn is always called directly
# ---------------------------------------------------------------------------

@pytest.fixture
def no_cache():
    async def passthrough(key: str, fn: Any, ttl: int | None = None) -> Any:
        return await fn()

    mock_cache = MagicMock()
    mock_cache.cache_or_fetch = AsyncMock(side_effect=passthrough)
    with patch("app.services.cftc.get_cache", return_value=mock_cache):
        yield mock_cache


# =============================================================================
# parse_managed_money — pure function, no mocks needed
# =============================================================================

class TestParseManagedMoney:

    # -------------------------------------------------------------------------
    # Schema and basic field values
    # -------------------------------------------------------------------------

    def test_returns_all_required_fields(self) -> None:
        records = [_row("2024-01-12", 200_000, 80_000)]
        result = parse_managed_money(records, WTI_CODE)
        assert set(result.keys()) == {
            "commodity", "report_date", "long", "short",
            "net_position", "wow_change", "percentile_rank",
        }

    def test_long_and_short_parsed_correctly(self) -> None:
        records = [_row("2024-01-12", 200_000, 80_000)]
        result = parse_managed_money(records, WTI_CODE)
        assert result["long"]  == 200_000
        assert result["short"] == 80_000

    def test_net_position_is_long_minus_short(self) -> None:
        records = [_row("2024-01-12", 200_000, 80_000)]
        result = parse_managed_money(records, WTI_CODE)
        assert result["net_position"] == 120_000

    def test_net_position_can_be_negative(self) -> None:
        records = [_row("2024-01-12", 50_000, 120_000)]
        result = parse_managed_money(records, WTI_CODE)
        assert result["net_position"] == -70_000

    # -------------------------------------------------------------------------
    # Commodity label
    # -------------------------------------------------------------------------

    def test_wti_label(self) -> None:
        result = parse_managed_money([_row("2024-01-12", 100_000, 40_000)], WTI_CODE)
        assert result["commodity"] == "WTI"

    def test_brent_label(self) -> None:
        result = parse_managed_money([_row("2024-01-12", 100_000, 40_000, code=BRENT_CODE)], BRENT_CODE)
        assert result["commodity"] == "Brent"

    def test_unknown_code_falls_back_to_code_string(self) -> None:
        result = parse_managed_money([_row("2024-01-12", 100_000, 40_000, code="999999")], "999999")
        assert result["commodity"] == "999999"

    # -------------------------------------------------------------------------
    # Date parsing
    # -------------------------------------------------------------------------

    def test_date_truncated_to_iso_date(self) -> None:
        # Socrata returns full ISO timestamp
        rec = _row("2024-01-12T00:00:00.000", 100_000, 40_000)
        result = parse_managed_money([rec], WTI_CODE)
        assert result["report_date"] == "2024-01-12"

    def test_date_already_plain_iso(self) -> None:
        result = parse_managed_money([_row("2024-01-12", 100_000, 40_000)], WTI_CODE)
        assert result["report_date"] == "2024-01-12"

    # -------------------------------------------------------------------------
    # WoW change
    # -------------------------------------------------------------------------

    def test_wow_change_current_minus_prior(self) -> None:
        # net[0]=120_000  net[1]=100_000 → WoW = +20_000
        records = [
            _row("2024-01-12", 200_000, 80_000),   # net = 120_000
            _row("2024-01-05", 180_000, 80_000),   # net = 100_000
        ]
        result = parse_managed_money(records, WTI_CODE)
        assert result["wow_change"] == 20_000

    def test_wow_change_negative(self) -> None:
        records = [
            _row("2024-01-12", 150_000, 80_000),   # net = 70_000
            _row("2024-01-05", 200_000, 80_000),   # net = 120_000
        ]
        result = parse_managed_money(records, WTI_CODE)
        assert result["wow_change"] == -50_000

    def test_wow_change_none_when_only_one_record(self) -> None:
        result = parse_managed_money([_row("2024-01-12", 100_000, 40_000)], WTI_CODE)
        assert result["wow_change"] is None

    # -------------------------------------------------------------------------
    # Percentile rank
    # -------------------------------------------------------------------------

    def test_percentile_rank_100_when_current_is_max(self) -> None:
        # net positions: current=200, history=[100, 150, 50] → all below → 100%
        records = [
            _row("2024-01-12", 280_000, 80_000),  # net = 200_000
            _row("2024-01-05", 180_000, 80_000),  # net = 100_000
            _row("2023-12-29", 230_000, 80_000),  # net = 150_000
            _row("2023-12-22", 130_000, 80_000),  # net =  50_000
        ]
        result = parse_managed_money(records, WTI_CODE)
        assert result["percentile_rank"] == 100.0

    def test_percentile_rank_0_when_current_is_min(self) -> None:
        # current=50, history=[100, 150, 200] → none below → 0%
        records = [
            _row("2024-01-12", 130_000, 80_000),  # net =  50_000
            _row("2024-01-05", 180_000, 80_000),  # net = 100_000
            _row("2023-12-29", 230_000, 80_000),  # net = 150_000
            _row("2023-12-22", 280_000, 80_000),  # net = 200_000
        ]
        result = parse_managed_money(records, WTI_CODE)
        assert result["percentile_rank"] == 0.0

    def test_percentile_rank_midrange(self) -> None:
        # current=100, history=[200, 50, 150] → 1 below (50) out of 3 → 33.3%
        records = [
            _row("2024-01-12", 180_000, 80_000),  # net = 100_000  ← current
            _row("2024-01-05", 280_000, 80_000),  # net = 200_000
            _row("2023-12-29", 130_000, 80_000),  # net =  50_000
            _row("2023-12-22", 230_000, 80_000),  # net = 150_000
        ]
        result = parse_managed_money(records, WTI_CODE)
        assert result["percentile_rank"] == pytest.approx(33.3, abs=0.1)

    def test_percentile_rank_excludes_current_from_history(self) -> None:
        # 2 records: current is ranked against 1-element history
        records = [
            _row("2024-01-12", 180_000, 80_000),  # net = 100_000
            _row("2024-01-05", 130_000, 80_000),  # net =  50_000
        ]
        result = parse_managed_money(records, WTI_CODE)
        # 1 history value (50_000) is below current (100_000) → 1/1 = 100%
        assert result["percentile_rank"] == 100.0

    def test_percentile_rank_none_with_single_record(self) -> None:
        result = parse_managed_money([_row("2024-01-12", 100_000, 40_000)], WTI_CODE)
        assert result["percentile_rank"] is None

    def test_percentile_rank_rounded_to_one_decimal(self) -> None:
        # 3 history values, current beats 1 → 1/3 * 100 = 33.333... → rounds to 33.3
        records = [
            _row("2024-01-12", 180_000, 80_000),  # net = 100_000
            _row("2024-01-05", 280_000, 80_000),  # net = 200_000
            _row("2023-12-29", 230_000, 80_000),  # net = 150_000
            _row("2023-12-22", 130_000, 80_000),  # net =  50_000
        ]
        result = parse_managed_money(records, WTI_CODE)
        assert isinstance(result["percentile_rank"], float)
        # 1 of 3 history values below 100_000 → 33.3
        assert result["percentile_rank"] == 33.3

    # -------------------------------------------------------------------------
    # Robustness / edge cases
    # -------------------------------------------------------------------------

    def test_skips_rows_with_non_numeric_values(self) -> None:
        records = [
            {
                "report_date_as_yyyy_mm_dd": "2024-01-12",
                "m_money_positions_long_all":  "NA",
                "m_money_positions_short_all": "80000",
            },
            _row("2024-01-05", 180_000, 80_000),
        ]
        result = parse_managed_money(records, WTI_CODE)
        assert result["report_date"] == "2024-01-05"  # first parseable row

    def test_string_values_converted_to_int(self) -> None:
        # SODA returns all values as strings
        records = [_row("2024-01-12", 200_000, 80_000)]
        result = parse_managed_money(records, WTI_CODE)
        assert isinstance(result["long"], int)
        assert isinstance(result["short"], int)
        assert isinstance(result["net_position"], int)

    def test_float_string_values_accepted(self) -> None:
        # Some SODA endpoints return floats even for integer columns
        rec = {
            "report_date_as_yyyy_mm_dd": "2024-01-12",
            "m_money_positions_long_all":  "200000.0",
            "m_money_positions_short_all": "80000.0",
        }
        result = parse_managed_money([rec], WTI_CODE)
        assert result["long"]  == 200_000
        assert result["short"] == 80_000

    def test_empty_records_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="No COT records"):
            parse_managed_money([], WTI_CODE)

    def test_all_unparseable_rows_raises_value_error(self) -> None:
        bad = [
            {"report_date_as_yyyy_mm_dd": "2024-01-12",
             "m_money_positions_long_all": None,
             "m_money_positions_short_all": None},
        ]
        with pytest.raises(ValueError, match="No parseable"):
            parse_managed_money(bad, WTI_CODE)


# =============================================================================
# CFTCService — HTTP + cache integration
# =============================================================================

class TestCFTCService:

    @pytest.mark.asyncio
    async def test_returns_parsed_result(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        mock_get.return_value = _cftc_response([
            _row("2024-01-12", 200_000, 80_000),
            _row("2024-01-05", 180_000, 80_000),
        ])
        result = await service.get_managed_money_positions(WTI_CODE)
        assert result["commodity"] == "WTI"
        assert result["net_position"] == 120_000
        assert result["wow_change"] == 20_000

    @pytest.mark.asyncio
    async def test_hits_correct_resource_url(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        mock_get.return_value = _cftc_response([_row("2024-01-12", 200_000, 80_000)])
        await service.get_managed_money_positions(WTI_CODE)
        url = mock_get.call_args.args[0]
        assert url == _RESOURCE_URL

    @pytest.mark.asyncio
    async def test_filters_by_commodity_code(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        mock_get.return_value = _cftc_response([_row("2024-01-12", 200_000, 80_000)])
        await service.get_managed_money_positions(WTI_CODE)
        params = mock_get.call_args.kwargs.get("params", {})
        assert WTI_CODE in params["$where"]

    @pytest.mark.asyncio
    async def test_orders_newest_first(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        mock_get.return_value = _cftc_response([_row("2024-01-12", 200_000, 80_000)])
        await service.get_managed_money_positions(WTI_CODE)
        params = mock_get.call_args.kwargs.get("params", {})
        assert "DESC" in params["$order"]

    @pytest.mark.asyncio
    async def test_requests_3_years_of_data(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        mock_get.return_value = _cftc_response([_row("2024-01-12", 200_000, 80_000)])
        await service.get_managed_money_positions(WTI_CODE)
        params = mock_get.call_args.kwargs.get("params", {})
        assert int(params["$limit"]) == _LOOKBACK_WEEKS

    @pytest.mark.asyncio
    async def test_uses_6h_cache_ttl(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        mock_get.return_value = _cftc_response([_row("2024-01-12", 200_000, 80_000)])
        await service.get_managed_money_positions(WTI_CODE)
        ttl = no_cache.cache_or_fetch.call_args.kwargs.get("ttl")
        assert ttl == _COT_TTL_SECONDS

    @pytest.mark.asyncio
    async def test_cache_key_includes_commodity_code(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        mock_get.return_value = _cftc_response([_row("2024-01-12", 200_000, 80_000)])
        await service.get_managed_money_positions(WTI_CODE)
        key = no_cache.cache_or_fetch.call_args.args[0]
        assert WTI_CODE in key

    @pytest.mark.asyncio
    async def test_brent_uses_different_cache_key(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        mock_get.return_value = _cftc_response([_row("2024-01-12", 200_000, 80_000, code=BRENT_CODE)])
        await service.get_managed_money_positions(BRENT_CODE)
        key = no_cache.cache_or_fetch.call_args.args[0]
        assert BRENT_CODE in key
        assert WTI_CODE not in key

    @pytest.mark.asyncio
    async def test_http_error_propagates(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        mock_get.return_value = _cftc_response([], status=500)
        with pytest.raises(httpx.HTTPStatusError):
            await service.get_managed_money_positions(WTI_CODE)
