"""Macro tab — DXY, 10Y Treasury, Fed funds, WTI (all sourced from FRED)."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends

from app.core.deps import get_fred_service
from app.core.upstream import call_upstream
from app.models.common import FredSeries
from app.models.macro import MacroResponse
from app.services.fred import FREDService

router = APIRouter(prefix="/macro", tags=["macro"])


@router.get(
    "",
    response_model=MacroResponse,
    summary="Macro indicators: DXY, 10Y Treasury, Fed funds, WTI",
    responses={502: {"description": "Upstream FRED fetch failed"}},
)
async def get_macro(
    fred: FREDService = Depends(get_fred_service),
) -> MacroResponse:
    async def fetch_all() -> tuple[dict, dict, dict, dict]:
        return await asyncio.gather(
            fred.get_dxy(),
            fred.get_us10y(),
            fred.get_fed_funds(),
            fred.get_wti(),
        )

    dxy, us10y, fed_funds, wti = await call_upstream("FRED", fetch_all)

    return MacroResponse(
        dxy=FredSeries(**dxy),
        us10y=FredSeries(**us10y),
        fed_funds=FredSeries(**fed_funds),
        wti=FredSeries(**wti),
    )
