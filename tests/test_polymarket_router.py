"""Router-level tests for the Polymarket /geopolitics endpoint.

Uses a minimal test FastAPI app (no lifespan) to isolate the router logic.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.deps import get_polymarket_service
from app.routers.polymarket import router
from app.services.polymarket import PolymarketService

# ---------------------------------------------------------------------------
# Minimal test app — avoids lifespan side-effects (Redis, scheduler, etc.)
# ---------------------------------------------------------------------------

_test_app = FastAPI()
_test_app.include_router(router, prefix="/api")


@pytest.fixture
def mock_svc() -> PolymarketService:
    svc = AsyncMock(spec=PolymarketService)
    svc.get_events.return_value = []
    svc.get_markets.return_value = []
    return svc  # type: ignore[return-value]


@pytest.fixture
def client(mock_svc: PolymarketService) -> TestClient:
    _test_app.dependency_overrides[get_polymarket_service] = lambda: mock_svc
    with TestClient(_test_app) as c:
        yield c
    _test_app.dependency_overrides.clear()


# =============================================================================
# GET /api/prediction-markets/polymarket/geopolitics
# =============================================================================

class TestGeopoliticsEndpoint:
    def test_returns_200(self, client: TestClient, mock_svc: AsyncMock) -> None:
        response = client.get("/api/prediction-markets/polymarket/geopolitics")
        assert response.status_code == 200

    def test_response_has_events_and_count(self, client: TestClient, mock_svc: AsyncMock) -> None:
        response = client.get("/api/prediction-markets/polymarket/geopolitics")
        body = response.json()
        assert "events" in body
        assert "count" in body
        assert body["count"] == 0

    def test_calls_service_with_geopolitics_tag_slug(
        self, client: TestClient, mock_svc: AsyncMock
    ) -> None:
        client.get("/api/prediction-markets/polymarket/geopolitics")
        call_kwargs = mock_svc.get_events.call_args.kwargs
        assert call_kwargs.get("tag_slug") == "geopolitics"

    def test_calls_service_with_active_true(
        self, client: TestClient, mock_svc: AsyncMock
    ) -> None:
        client.get("/api/prediction-markets/polymarket/geopolitics")
        call_kwargs = mock_svc.get_events.call_args.kwargs
        assert call_kwargs.get("active") is True

    def test_calls_service_with_closed_false(
        self, client: TestClient, mock_svc: AsyncMock
    ) -> None:
        client.get("/api/prediction-markets/polymarket/geopolitics")
        call_kwargs = mock_svc.get_events.call_args.kwargs
        assert call_kwargs.get("closed") is False

    def test_calls_service_with_order_volume(
        self, client: TestClient, mock_svc: AsyncMock
    ) -> None:
        client.get("/api/prediction-markets/polymarket/geopolitics")
        call_kwargs = mock_svc.get_events.call_args.kwargs
        assert call_kwargs.get("order") == "volume"

    def test_calls_service_with_ascending_false(
        self, client: TestClient, mock_svc: AsyncMock
    ) -> None:
        client.get("/api/prediction-markets/polymarket/geopolitics")
        call_kwargs = mock_svc.get_events.call_args.kwargs
        assert call_kwargs.get("ascending") is False

    def test_default_limit_is_20(
        self, client: TestClient, mock_svc: AsyncMock
    ) -> None:
        client.get("/api/prediction-markets/polymarket/geopolitics")
        call_kwargs = mock_svc.get_events.call_args.kwargs
        assert call_kwargs.get("limit") == 20

    def test_limit_param_is_forwarded(
        self, client: TestClient, mock_svc: AsyncMock
    ) -> None:
        client.get("/api/prediction-markets/polymarket/geopolitics?limit=50")
        call_kwargs = mock_svc.get_events.call_args.kwargs
        assert call_kwargs.get("limit") == 50

    def test_offset_param_is_forwarded(
        self, client: TestClient, mock_svc: AsyncMock
    ) -> None:
        client.get("/api/prediction-markets/polymarket/geopolitics?offset=20")
        call_kwargs = mock_svc.get_events.call_args.kwargs
        assert call_kwargs.get("offset") == 20
