import asyncio
import logging

logger = logging.getLogger(__name__)


async def refresh_finnhub_quotes() -> None:
    """Pre-warm quote cache for all oil tickers (runs every 30s)."""
    from app.config import settings
    from app.core.http_client import get_http_client
    from app.services.finnhub import FinnhubService, OIL_TICKERS

    if not settings.finnhub_api_key:
        return

    svc = FinnhubService(get_http_client(), settings.finnhub_api_key)
    results = await asyncio.gather(*[svc.get_quote(t) for t in OIL_TICKERS], return_exceptions=True)
    errors = sum(1 for r in results if isinstance(r, Exception))
    if errors:
        logger.warning("Finnhub quote refresh: %d/%d tickers failed", errors, len(OIL_TICKERS))
    else:
        logger.debug("Finnhub quotes refreshed for %d tickers", len(OIL_TICKERS))


async def refresh_finnhub_market_news() -> None:
    """Pre-warm general market news cache (runs every 5 min)."""
    from app.config import settings
    from app.core.http_client import get_http_client
    from app.services.finnhub import FinnhubService

    if not settings.finnhub_api_key:
        return

    svc = FinnhubService(get_http_client(), settings.finnhub_api_key)
    await svc.get_market_news("general")
    logger.debug("Finnhub market news refreshed")


async def refresh_morning_brief() -> None:
    """Fetch all RSS feeds and update morning brief cache (runs daily at 08:00 IST)."""
    from app.core.http_client import get_http_client
    from app.services.rss import RssService

    svc = RssService(get_http_client())
    await svc.fetch_all()
    logger.info("Morning brief refresh complete")


async def warmup_morning_brief() -> None:
    """On startup: fetch the brief only if the cache is cold."""
    try:
        from app.core.cache import get_cache
        from app.core.http_client import get_http_client
        from app.services.rss import RssService, _CACHE_KEY

        if await get_cache().get_json(_CACHE_KEY) is None:
            logger.info("Morning brief cache cold — running startup fetch")
            svc = RssService(get_http_client())
            await svc.fetch_all()
    except Exception as exc:
        logger.warning("Morning brief warmup failed (will retry at next scheduled run): %s", exc)


async def refresh_finnhub_company_news() -> None:
    """Pre-warm company news cache for all oil tickers (runs every 5 min)."""
    from app.config import settings
    from app.core.http_client import get_http_client
    from app.services.finnhub import FinnhubService, OIL_TICKERS

    if not settings.finnhub_api_key:
        return

    svc = FinnhubService(get_http_client(), settings.finnhub_api_key)
    results = await asyncio.gather(
        *[svc.get_company_news(t) for t in OIL_TICKERS], return_exceptions=True
    )
    errors = sum(1 for r in results if isinstance(r, Exception))
    if errors:
        logger.warning("Finnhub company news refresh: %d/%d tickers failed", errors, len(OIL_TICKERS))
    else:
        logger.debug("Finnhub company news refreshed for %d tickers", len(OIL_TICKERS))
