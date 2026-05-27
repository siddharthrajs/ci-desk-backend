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
