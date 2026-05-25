"""Tests for BakerHughesService — FREDService is mocked at the method level."""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest

from app.services.bakerhughes import BakerHughesService, _unavailable
from app.services.fred import FREDService

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DUMMY_REQUEST = httpx.Request("GET", "https://api.stlouisfed.org/fred/series/observations")


def _fred_data(
    latest_value: float | None = 620.0,
    latest_date: str = "2024-01-12",
    observations: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a minimal get_fred_series return value."""
    if observations is None:
        observations = [
            {"date": "2024-01-12", "value": latest_value or 620.0},
            {"date": "2024-01-05", "value": 615.0},
        ]
    return {
        "series_id":    "RIGTNXUS",
        "latest_value": latest_value,
        "latest_date":  latest_date,
        "observations": observations,
    }


def _make_service(fred_return: dict[str, Any] | None = None) -> tuple[BakerHughesService, AsyncMock]:
    fred_mock = AsyncMock(spec=FREDService)
    fred_mock.get_fred_series = AsyncMock(return_value=fred_return or _fred_data())
    service = BakerHughesService(fred_service=fred_mock)
    return service, fred_mock


# =============================================================================
# _unavailable helper
# =============================================================================

class TestUnavailableHelper:
    def test_available_is_false(self) -> None:
        result = _unavailable("test reason")
        assert result["available"] is False

    def test_reason_is_preserved(self) -> None:
        result = _unavailable("FRED returned HTTP 404")
        assert result["reason"] == "FRED returned HTTP 404"

    def test_all_count_fields_are_none(self) -> None:
        result = _unavailable("test")
        assert result["total"] is None
        assert result["oil"] is None
        assert result["gas"] is None
        assert result["wow_change"] is None
        assert result["report_date"] is None

    def test_source_is_unavailable(self) -> None:
        result = _unavailable("test")
        assert result["source"] == "unavailable"


# =============================================================================
# get_rig_count — happy path
# =============================================================================

class TestGetRigCountSuccess:
    @pytest.mark.asyncio
    async def test_available_is_true(self) -> None:
        service, _ = _make_service()
        result = await service.get_rig_count()
        assert result["available"] is True

    @pytest.mark.asyncio
    async def test_source_is_fred(self) -> None:
        service, _ = _make_service()
        result = await service.get_rig_count()
        assert result["source"] == "FRED/BakerHughes"

    @pytest.mark.asyncio
    async def test_total_is_int(self) -> None:
        service, _ = _make_service(_fred_data(latest_value=620.0))
        result = await service.get_rig_count()
        assert result["total"] == 620
        assert isinstance(result["total"], int)

    @pytest.mark.asyncio
    async def test_report_date_returned(self) -> None:
        service, _ = _make_service(_fred_data(latest_date="2024-01-12"))
        result = await service.get_rig_count()
        assert result["report_date"] == "2024-01-12"

    @pytest.mark.asyncio
    async def test_wow_change_computed(self) -> None:
        data = _fred_data(
            latest_value=620.0,
            observations=[
                {"date": "2024-01-12", "value": 620.0},
                {"date": "2024-01-05", "value": 615.0},
            ],
        )
        service, _ = _make_service(data)
        result = await service.get_rig_count()
        assert result["wow_change"] == 5.0

    @pytest.mark.asyncio
    async def test_negative_wow_change(self) -> None:
        data = _fred_data(
            latest_value=610.0,
            observations=[
                {"date": "2024-01-12", "value": 610.0},
                {"date": "2024-01-05", "value": 620.0},
            ],
        )
        service, _ = _make_service(data)
        result = await service.get_rig_count()
        assert result["wow_change"] == -10.0

    @pytest.mark.asyncio
    async def test_single_observation_wow_change_is_none(self) -> None:
        data = _fred_data(
            latest_value=620.0,
            observations=[{"date": "2024-01-12", "value": 620.0}],
        )
        service, _ = _make_service(data)
        result = await service.get_rig_count()
        assert result["wow_change"] is None

    @pytest.mark.asyncio
    async def test_oil_and_gas_always_none(self) -> None:
        service, _ = _make_service()
        result = await service.get_rig_count()
        assert result["oil"] is None
        assert result["gas"] is None

    @pytest.mark.asyncio
    async def test_calls_correct_fred_series(self) -> None:
        service, fred_mock = _make_service()
        await service.get_rig_count()
        fred_mock.get_fred_series.assert_called_once_with("RIGTNXUS")


# =============================================================================
# get_rig_count — error / unavailable paths
# =============================================================================

class TestGetRigCountUnavailable:
    @pytest.mark.asyncio
    async def test_fred_404_returns_unavailable(self) -> None:
        service, fred_mock = _make_service()
        err_response = httpx.Response(404, request=_DUMMY_REQUEST)
        fred_mock.get_fred_series.side_effect = httpx.HTTPStatusError(
            "404", request=_DUMMY_REQUEST, response=err_response
        )
        result = await service.get_rig_count()
        assert result["available"] is False
        assert "404" in result["reason"]

    @pytest.mark.asyncio
    async def test_fred_500_returns_unavailable(self) -> None:
        service, fred_mock = _make_service()
        err_response = httpx.Response(500, request=_DUMMY_REQUEST)
        fred_mock.get_fred_series.side_effect = httpx.HTTPStatusError(
            "500", request=_DUMMY_REQUEST, response=err_response
        )
        result = await service.get_rig_count()
        assert result["available"] is False

    @pytest.mark.asyncio
    async def test_unexpected_exception_returns_unavailable(self) -> None:
        service, fred_mock = _make_service()
        fred_mock.get_fred_series.side_effect = RuntimeError("connection refused")
        result = await service.get_rig_count()
        assert result["available"] is False

    @pytest.mark.asyncio
    async def test_no_latest_value_returns_unavailable(self) -> None:
        data = _fred_data(latest_value=None, observations=[])
        service, _ = _make_service(data)
        result = await service.get_rig_count()
        assert result["available"] is False

    @pytest.mark.asyncio
    async def test_unavailable_has_none_counts(self) -> None:
        service, fred_mock = _make_service()
        err_response = httpx.Response(503, request=_DUMMY_REQUEST)
        fred_mock.get_fred_series.side_effect = httpx.HTTPStatusError(
            "503", request=_DUMMY_REQUEST, response=err_response
        )
        result = await service.get_rig_count()
        assert result["total"] is None
        assert result["oil"] is None
        assert result["gas"] is None
        assert result["wow_change"] is None
