"""Unit tests for core/cache.py — Redis client is fully mocked."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.cache import RedisCache


@pytest.fixture
def mock_redis() -> AsyncMock:
    """Return a mock that behaves like redis.asyncio.Redis."""
    return AsyncMock()


@pytest.fixture
def cache(mock_redis: AsyncMock) -> RedisCache:
    """RedisCache instance wired to a mock Redis connection."""
    instance = RedisCache.__new__(RedisCache)
    instance._client = mock_redis
    return instance


# ---------------------------------------------------------------------------
# get_json
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_json_cache_hit(cache: RedisCache, mock_redis: AsyncMock) -> None:
    mock_redis.get.return_value = '{"price": 75.5}'
    result = await cache.get_json("oil:wti")
    assert result == {"price": 75.5}
    mock_redis.get.assert_awaited_once_with("oil:wti")


@pytest.mark.asyncio
async def test_get_json_cache_miss(cache: RedisCache, mock_redis: AsyncMock) -> None:
    mock_redis.get.return_value = None
    result = await cache.get_json("oil:wti")
    assert result is None


@pytest.mark.asyncio
async def test_get_json_propagates_redis_error(cache: RedisCache, mock_redis: AsyncMock) -> None:
    mock_redis.get.side_effect = ConnectionError("Redis down")
    with pytest.raises(ConnectionError):
        await cache.get_json("oil:wti")


# ---------------------------------------------------------------------------
# set_json
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_json_uses_provided_ttl(cache: RedisCache, mock_redis: AsyncMock) -> None:
    await cache.set_json("oil:wti", {"price": 75.5}, ttl=120)
    mock_redis.set.assert_awaited_once_with("oil:wti", '{"price": 75.5}', ex=120)


@pytest.mark.asyncio
async def test_set_json_falls_back_to_settings_ttl(cache: RedisCache, mock_redis: AsyncMock) -> None:
    with patch("app.core.cache.settings") as mock_settings:
        mock_settings.cache_ttl_seconds = 3600
        await cache.set_json("oil:brent", [1, 2, 3])
    mock_redis.set.assert_awaited_once_with("oil:brent", "[1, 2, 3]", ex=3600)


# ---------------------------------------------------------------------------
# cache_or_fetch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cache_or_fetch_returns_cached_value(cache: RedisCache, mock_redis: AsyncMock) -> None:
    mock_redis.get.return_value = '"cached"'
    fetch_fn = AsyncMock(return_value="fresh")

    result = await cache.cache_or_fetch("key", fetch_fn)

    assert result == "cached"
    fetch_fn.assert_not_awaited()
    mock_redis.set.assert_not_awaited()


@pytest.mark.asyncio
async def test_cache_or_fetch_calls_fetch_on_miss(cache: RedisCache, mock_redis: AsyncMock) -> None:
    mock_redis.get.return_value = None
    mock_redis.set.return_value = True
    fetch_fn = AsyncMock(return_value={"data": 42})

    result = await cache.cache_or_fetch("key", fetch_fn, ttl=60)

    assert result == {"data": 42}
    fetch_fn.assert_awaited_once()
    mock_redis.set.assert_awaited_once_with("key", '{"data": 42}', ex=60)


@pytest.mark.asyncio
async def test_cache_or_fetch_bypasses_cache_when_redis_down_on_get(
    cache: RedisCache, mock_redis: AsyncMock
) -> None:
    mock_redis.get.side_effect = ConnectionError("Redis down")
    fetch_fn = AsyncMock(return_value="live_data")

    result = await cache.cache_or_fetch("key", fetch_fn)

    assert result == "live_data"
    fetch_fn.assert_awaited_once()
    mock_redis.set.assert_not_awaited()


@pytest.mark.asyncio
async def test_cache_or_fetch_still_returns_data_when_redis_down_on_set(
    cache: RedisCache, mock_redis: AsyncMock
) -> None:
    mock_redis.get.return_value = None
    mock_redis.set.side_effect = ConnectionError("Redis down")
    fetch_fn = AsyncMock(return_value="live_data")

    result = await cache.cache_or_fetch("key", fetch_fn)

    assert result == "live_data"


@pytest.mark.asyncio
async def test_cache_or_fetch_logs_warning_when_redis_down(
    cache: RedisCache, mock_redis: AsyncMock
) -> None:
    mock_redis.get.side_effect = ConnectionError("Redis down")
    fetch_fn = AsyncMock(return_value="data")

    with patch("app.core.cache.logger") as mock_logger:
        await cache.cache_or_fetch("key", fetch_fn)
        mock_logger.warning.assert_called_once()
        assert "key" in mock_logger.warning.call_args.args[1]
