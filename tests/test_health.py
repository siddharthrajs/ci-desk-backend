from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client():
    with patch("app.core.cache.cache") as mock_cache:
        mock_cache.ping = AsyncMock(return_value=True)
        with TestClient(app, raise_server_exceptions=True) as c:
            yield c


def test_health_ok(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] in ("ok", "degraded")
    assert "dependencies" in body
