import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

import redis.asyncio as aioredis

from app.config import settings

logger = logging.getLogger(__name__)

T = TypeVar("T")


class RedisCache:
    def __init__(self, url: str) -> None:
        self._client: aioredis.Redis = aioredis.from_url(
            url,
            encoding="utf-8",
            decode_responses=True,
        )

    async def get_json(self, key: str) -> Any | None:
        raw = await self._client.get(key)
        if raw is None:
            return None
        return json.loads(raw)

    async def set_json(self, key: str, value: Any, ttl: int | None = None) -> None:
        payload = json.dumps(value)
        await self._client.set(key, payload, ex=ttl or settings.cache_ttl_seconds)

    async def cache_or_fetch(
        self,
        key: str,
        fetch_fn: Callable[[], Awaitable[T]],
        ttl: int | None = None,
    ) -> T:
        """Return cached value if present; otherwise call fetch_fn, cache the result, and return it.

        If Redis is unavailable at any point, logs a warning and falls back to fetch_fn directly.
        """
        try:
            cached = await self.get_json(key)
            if cached is not None:
                return cached  # type: ignore[return-value]
        except Exception as exc:
            logger.warning("Redis unavailable, fetching directly for key=%s: %s", key, exc)
            return await fetch_fn()

        data = await fetch_fn()

        try:
            await self.set_json(key, data, ttl)
        except Exception as exc:
            logger.warning("Redis set failed for key=%s: %s", key, exc)

        return data

    async def delete(self, key: str) -> None:
        await self._client.delete(key)

    async def ping(self) -> bool:
        try:
            return await self._client.ping()
        except Exception:
            return False

    async def close(self) -> None:
        await self._client.aclose()


# Module-level singleton — initialised in lifespan
cache: RedisCache | None = None


def get_cache() -> RedisCache:
    if cache is None:
        raise RuntimeError("Cache not initialised — lifespan not started")
    return cache
