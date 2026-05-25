"""Helper for translating upstream HTTP failures into a 502 response.

External-data-source endpoints (EIA, FRED, CFTC, WPSR) wrap their service calls
with `call_upstream` so an httpx error from the origin propagates to the client
as `502 Bad Gateway` with a descriptive message rather than a generic 500.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import TypeVar

import httpx
from fastapi import HTTPException, status

logger = logging.getLogger(__name__)

T = TypeVar("T")


async def call_upstream(source: str, fn: Callable[[], Awaitable[T]]) -> T:
    """Execute `fn` and convert httpx network/HTTP errors to a 502 response.

    Args:
        source: Short identifier for logging and the client-facing detail
                (e.g. "EIA", "FRED", "CFTC", "WPSR").
        fn:     Zero-argument async callable that performs the upstream fetch.
    """
    try:
        return await fn()
    except httpx.HTTPStatusError as exc:
        logger.warning(
            "Upstream %s returned HTTP %d: %s",
            source, exc.response.status_code, exc.request.url,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Upstream {source} returned HTTP {exc.response.status_code}",
        ) from exc
    except httpx.HTTPError as exc:
        logger.warning("Upstream %s request failed: %s", source, exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Upstream {source} unavailable",
        ) from exc
