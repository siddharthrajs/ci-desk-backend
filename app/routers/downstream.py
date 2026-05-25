"""Downstream tab — crack spreads, product demand, refinery utilization history."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends

from app.core.deps import get_eia_service
from app.core.upstream import call_upstream
from app.models.common import SeriesPoint
from app.models.downstream import (
    CrackSpreads,
    DownstreamResponse,
    ProductDemand,
)
from app.models.midstream import RefineryUtilizationHistory
from app.services.downstream import compute_crack_spreads
from app.services.eia import EIAService

router = APIRouter(prefix="/downstream", tags=["downstream"])


def _points(rows: list[dict[str, Any]]) -> list[SeriesPoint]:
    return [SeriesPoint(**row) for row in rows]


@router.get(
    "",
    response_model=DownstreamResponse,
    summary="Crack spreads, product demand, refinery utilization",
    responses={502: {"description": "Upstream data source unavailable"}},
)
async def get_downstream(
    eia: EIAService = Depends(get_eia_service),
) -> DownstreamResponse:
    async def fetch_all() -> tuple[Any, ...]:
        return await asyncio.gather(
            eia.get_spot_prices(),
            eia.get_product_supplied(),
            eia.get_refinery_utilization(),
        )

    spots, product_supplied, refinery = await call_upstream("EIA", fetch_all)

    spreads = compute_crack_spreads(
        wti=spots.get("wti", []),
        rbob=spots.get("rbob", []),
        heating_oil=spots.get("heating_oil", []),
    )

    return DownstreamResponse(
        crack_spreads=CrackSpreads(
            three_two_one=_points(spreads["three_two_one"]),
            rbob_crack=_points(spreads["rbob_crack"]),
            ho_crack=_points(spreads["ho_crack"]),
        ),
        product_demand=ProductDemand(
            gasoline=_points(product_supplied.get("gasoline", [])),
            distillate=_points(product_supplied.get("distillate", [])),
            jet=_points(product_supplied.get("jet", [])),
        ),
        refinery_util_history=RefineryUtilizationHistory(
            national=_points(refinery.get("national", [])),
            padd1=_points(refinery.get("padd1", [])),
            padd2=_points(refinery.get("padd2", [])),
            padd3=_points(refinery.get("padd3", [])),
            padd4=_points(refinery.get("padd4", [])),
            padd5=_points(refinery.get("padd5", [])),
        ),
    )
