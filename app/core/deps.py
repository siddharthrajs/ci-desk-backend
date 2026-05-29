"""FastAPI dependency providers for service instances.

Each provider returns a fresh service object bound to the shared httpx client
and any required API keys from settings. Services are cheap to instantiate
(they hold no per-request state of their own) and caching lives inside each
service via the shared RedisCache.
"""

from __future__ import annotations

from fastapi import Depends

from app.config import settings
from app.core.http_client import get_http_client
from app.services.ai_summary import AiSummaryService
from app.services.bakerhughes import BakerHughesService
from app.services.cftc import CFTCService
from app.services.eia import EIAService
from app.services.financial_juice import FinancialJuiceService
from app.services.finnhub import FinnhubService
from app.services.fred import FREDService
from app.services.wpsr import WPSRService


def get_eia_service() -> EIAService:
    return EIAService(get_http_client(), settings.eia_api_key)


def get_fred_service() -> FREDService:
    return FREDService(get_http_client(), settings.fred_api_key)


def get_cftc_service() -> CFTCService:
    return CFTCService(get_http_client())


def get_wpsr_service() -> WPSRService:
    return WPSRService(get_http_client())


def get_baker_hughes_service(
    fred: FREDService = Depends(get_fred_service),
) -> BakerHughesService:
    return BakerHughesService(fred)


def get_finnhub_service() -> FinnhubService:
    return FinnhubService(get_http_client(), settings.finnhub_api_key)


def get_financial_juice_service() -> FinancialJuiceService:
    return FinancialJuiceService(get_http_client())


def get_ai_summary_service() -> AiSummaryService:
    return AiSummaryService(settings.gemini_api_key)
