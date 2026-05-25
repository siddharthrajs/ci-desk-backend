import asyncio
import logging

import httpx

logger = logging.getLogger(__name__)

_client: httpx.AsyncClient | None = None

_RETRY_STATUSES = frozenset({500, 502, 503, 504})
_MAX_RETRIES = 3
_BACKOFF_BASE = 0.5  # seconds; actual waits: 0.5s, 1s, 2s


def get_http_client() -> httpx.AsyncClient:
    if _client is None:
        raise RuntimeError("HTTP client not initialised — lifespan not started")
    return _client


async def init_http_client() -> None:
    global _client
    _client = httpx.AsyncClient(
        timeout=httpx.Timeout(connect=5.0, read=30.0, write=10.0, pool=5.0),
        limits=httpx.Limits(max_connections=50, max_keepalive_connections=20),
        headers={"User-Agent": "CI-Desk/1.0"},
        follow_redirects=True,
    )


async def close_http_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


async def request_with_retry(method: str, url: str, **kwargs: object) -> httpx.Response:
    """Send an HTTP request, retrying up to _MAX_RETRIES times on 5xx or timeouts."""
    client = get_http_client()
    last_response: httpx.Response | None = None

    for attempt in range(_MAX_RETRIES + 1):
        if attempt > 0:
            backoff = _BACKOFF_BASE * (2 ** (attempt - 1))
            await asyncio.sleep(backoff)
        try:
            response = await client.request(method, url, **kwargs)
            if response.status_code not in _RETRY_STATUSES:
                return response
            last_response = response
            if attempt < _MAX_RETRIES:
                logger.warning(
                    "Retrying after HTTP %d (attempt %d/%d): %s",
                    response.status_code, attempt + 1, _MAX_RETRIES, url,
                )
        except (httpx.TimeoutException, httpx.ConnectError) as exc:
            if attempt == _MAX_RETRIES:
                raise
            logger.warning(
                "Retrying after %s (attempt %d/%d): %s",
                type(exc).__name__, attempt + 1, _MAX_RETRIES, url,
            )

    # All retries exhausted — return the last 5xx response so callers can inspect it.
    assert last_response is not None
    return last_response
