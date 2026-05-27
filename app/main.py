import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.core import cache as cache_module
from app.core.cache import RedisCache
from app.core.http_client import close_http_client, init_http_client
from app.core.logging import configure_logging
from app.routers import downstream, health, macro, markets, midstream, news, reports, upstream
from app.scheduler.setup import register_jobs, scheduler
import app.services.lightstreamer_broadcaster as ls_module
from app.services.lightstreamer_broadcaster import LightstreamerBroadcaster

configure_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    logger.info("Starting ci-desk-backend", extra={"env": settings.app_env})

    await init_http_client()
    cache_module.cache = RedisCache(settings.redis_url)

    register_jobs()
    scheduler.start()
    logger.info("Scheduler started")

    ls_module.broadcaster = LightstreamerBroadcaster()
    ls_module.broadcaster.start()

    yield

    scheduler.shutdown(wait=False)
    logger.info("Scheduler stopped")

    if ls_module.broadcaster is not None:
        ls_module.broadcaster.stop()

    await cache_module.cache.close()
    await close_http_client()
    logger.info("Shutdown complete")


app = FastAPI(
    title="CI Desk — Crude Oil Trading Dashboard",
    version="0.1.0",
    docs_url="/docs" if not settings.is_production else None,
    redoc_url="/redoc" if not settings.is_production else None,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(upstream.router, prefix="/api")
app.include_router(midstream.router, prefix="/api")
app.include_router(downstream.router, prefix="/api")
app.include_router(reports.router, prefix="/api")
app.include_router(macro.router, prefix="/api")
app.include_router(markets.router, prefix="/api")
app.include_router(news.router, prefix="/api")
