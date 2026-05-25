"""Upstream tab — crude production, rig activity, OPEC supply placeholder."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends

from app.core.deps import get_baker_hughes_service, get_eia_service
from app.core.upstream import call_upstream
from app.models.common import SeriesPoint
from app.models.upstream import OPECProduction, RigCount, UpstreamResponse
from app.services.bakerhughes import BakerHughesService
from app.services.eia import EIAService

router = APIRouter(prefix="/upstream", tags=["upstream"])


@router.get(
    "",
    response_model=UpstreamResponse,
    summary="Upstream production and rig activity",
    responses={502: {"description": "Upstream data source unavailable"}},
)
async def get_upstream(
    eia: EIAService = Depends(get_eia_service),
    baker_hughes: BakerHughesService = Depends(get_baker_hughes_service),
) -> UpstreamResponse:
    production, rigs = await asyncio.gather(
        call_upstream("EIA", eia.get_crude_production),
        baker_hughes.get_rig_count(),
    )

    return UpstreamResponse(
        crude_production=[SeriesPoint(**row) for row in production],
        rig_count=RigCount(**rigs),
        opec=OPECProduction(),
    )
