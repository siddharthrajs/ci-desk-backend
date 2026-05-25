import logging

from fastapi import APIRouter, Depends

from app.core.cache import RedisCache, get_cache
from app.models.health import DependencyStatus, HealthResponse
from app.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health(cache: RedisCache = Depends(get_cache)) -> HealthResponse:
    redis_ok = await cache.ping()
    overall = "ok" if redis_ok else "degraded"
    if not redis_ok:
        logger.warning("Health check: Redis unavailable")
    return HealthResponse(
        status=overall,
        env=settings.app_env,
        dependencies=DependencyStatus(redis=redis_ok),
    )
