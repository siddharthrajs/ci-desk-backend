import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import settings

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler(timezone=settings.scheduler_timezone)


def register_jobs() -> None:
    """Register all cron jobs. Add entries here when data-fetching services are ready."""
    # Example (uncomment when EIA service is implemented):
    # from app.scheduler.jobs import refresh_eia_prices
    # scheduler.add_job(
    #     refresh_eia_prices,
    #     CronTrigger(hour="*/1"),
    #     id="eia_price_refresh",
    #     replace_existing=True,
    # )
    logger.info("Scheduler jobs registered (none active yet)")
