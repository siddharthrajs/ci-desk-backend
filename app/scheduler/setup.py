import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.config import settings

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler(timezone=settings.scheduler_timezone)


def register_jobs() -> None:
    """Register all background refresh jobs."""
    # DISABLED: all scheduled jobs are temporarily disabled
    return

    if settings.finnhub_api_key:
        from app.scheduler.jobs import (
            refresh_finnhub_company_news,
            refresh_finnhub_market_news,
            refresh_finnhub_quotes,
        )

        scheduler.add_job(
            refresh_finnhub_quotes,
            IntervalTrigger(seconds=30),
            id="finnhub_quotes",
            replace_existing=True,
        )
        scheduler.add_job(
            refresh_finnhub_market_news,
            CronTrigger(minute="*/5"),
            id="finnhub_market_news",
            replace_existing=True,
        )
        scheduler.add_job(
            refresh_finnhub_company_news,
            CronTrigger(minute="*/5"),
            id="finnhub_company_news",
            replace_existing=True,
        )
        logger.info("Finnhub refresh jobs registered (quotes=30s, news=5min)")
    else:
        logger.warning("FINNHUB_API_KEY not set — Finnhub refresh jobs skipped")

    from app.scheduler.jobs import refresh_morning_brief

    scheduler.add_job(
        refresh_morning_brief,
        CronTrigger(hour=8, minute=0, timezone="Asia/Kolkata"),
        id="morning_brief",
        replace_existing=True,
    )
    logger.info("Morning brief job registered (daily 08:00 IST)")

    logger.info("Scheduler jobs registered")
