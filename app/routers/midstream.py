"""Midstream tab — inventories, SPR, refinery utilization, days of supply."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends

from app.core.deps import get_eia_service
from app.core.upstream import call_upstream
from app.models.common import SeriesPoint
from app.models.midstream import (
    DaysOfSupply,
    Inventories,
    MidstreamResponse,
    RefineryUtilizationHistory,
)
from app.services.eia import EIAService

router = APIRouter(prefix="/midstream", tags=["midstream"])


def _points(rows: list[dict[str, Any]]) -> list[SeriesPoint]:
    return [SeriesPoint(**row) for row in rows]


def _latest(rows: list[dict[str, Any]]) -> float | None:
    return rows[0]["value"] if rows else None


def _days_of_supply(
    stocks: list[dict[str, Any]],
    demand: list[dict[str, Any]],
) -> float | None:
    """Return stocks / demand for the latest week, or None if either is missing.

    `demand` is EIA's 4-week-average product supplied in kbpd; `stocks` is the
    weekly inventory level in thousand barrels — the units cancel to days.
    """
    s = _latest(stocks)
    d = _latest(demand)
    if s is None or not d:
        return None
    return round(s / d, 1)


@router.get(
    "",
    response_model=MidstreamResponse,
    summary="Inventories, SPR, refinery utilization, days of supply",
    responses={502: {"description": "Upstream data source unavailable"}},
)
async def get_midstream(
    eia: EIAService = Depends(get_eia_service),
) -> MidstreamResponse:
    async def fetch_all() -> tuple[Any, ...]:
        return await asyncio.gather(
            eia.get_crude_stocks(),
            eia.get_cushing_stocks(),
            eia.get_gasoline_stocks(),
            eia.get_distillate_stocks(),
            eia.get_spr_level(),
            eia.get_refinery_utilization(),
            eia.get_product_supplied(),
        )

    (
        crude,
        cushing,
        gasoline,
        distillate,
        spr,
        refinery,
        product_supplied,
    ) = await call_upstream("EIA", fetch_all)

    return MidstreamResponse(
        inventories=Inventories(
            crude=_points(crude),
            cushing=_points(cushing),
            gasoline=_points(gasoline),
            distillate=_points(distillate),
        ),
        spr=_points(spr),
        refinery_utilization=RefineryUtilizationHistory(
            national=_points(refinery.get("national", [])),
            padd1=_points(refinery.get("padd1", [])),
            padd2=_points(refinery.get("padd2", [])),
            padd3=_points(refinery.get("padd3", [])),
            padd4=_points(refinery.get("padd4", [])),
            padd5=_points(refinery.get("padd5", [])),
        ),
        days_of_supply=DaysOfSupply(
            gasoline=_days_of_supply(gasoline, product_supplied.get("gasoline", [])),
            distillate=_days_of_supply(distillate, product_supplied.get("distillate", [])),
        ),
    )
