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
    CrackSpreadsV2,
    DownstreamResponse,
    ProductDemand,
    ProductDemandV2,
    RefineryUtilizationV2,
)
from app.models.midstream import RefineryUtilizationHistory
from app.services.downstream import (
    compute_crack_spreads,
    compute_crack_spreads_v2,
    compute_product_demand_v2,
    compute_refinery_utilization_v2,
)
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


@router.get(
    "/debug/spot-prices-raw",
    summary="[DEBUG] Raw EIA spot-price rows — shows series/product/duoarea keys",
    include_in_schema=False,
)
async def debug_spot_prices(
    eia: EIAService = Depends(get_eia_service),
) -> dict[str, Any]:
    """Fetch rows from EIA spot prices and return them raw.

    unfiltered_12: 12 rows with no facet filter — shows every field EIA sends
                   (look for 'series', 'product', 'duoarea', 'process' keys).
    rwtc_5:        5 rows filtered by series=RWTC — confirms whether that filter works.
    """
    async def fetch() -> Any:
        unfiltered, rwtc = await asyncio.gather(
            eia._fetch_eia_series("petroleum/pri/spt", {}, frequency="daily", length=12),
            eia._fetch_eia_series("petroleum/pri/spt", {"series": ["RWTC"]}, frequency="daily", length=5),
        )
        return {"unfiltered_12": unfiltered, "rwtc_5": rwtc}

    return await call_upstream("EIA", fetch)


@router.get(
    "/crack-spreads",
    response_model=CrackSpreadsV2,
    summary="Crack spreads with z-scores, signals, and 90-day history",
    responses={502: {"description": "Upstream data source unavailable"}},
)
async def get_crack_spreads(
    eia: EIAService = Depends(get_eia_service),
) -> CrackSpreadsV2:
    async def fetch() -> Any:
        return await eia.get_spot_prices_full()

    spots = await call_upstream("EIA", fetch)
    data = compute_crack_spreads_v2(
        wti=spots.get("wti", []),
        brent=spots.get("brent", []),
        rbob=spots.get("rbob", []),
        heating_oil=spots.get("heating_oil", []),
    )
    return CrackSpreadsV2.model_validate(data)


@router.get(
    "/refinery-utilization",
    response_model=RefineryUtilizationV2,
    summary="Refinery utilization history — national estimate + PADD 3, 2Y weekly",
    responses={502: {"description": "Upstream data source unavailable"}},
)
async def get_refinery_utilization(
    eia: EIAService = Depends(get_eia_service),
) -> RefineryUtilizationV2:
    async def fetch() -> Any:
        return await eia.get_refinery_utilization_2yr()

    padd_data = await call_upstream("EIA", fetch)
    data = compute_refinery_utilization_v2(padd_data)
    return RefineryUtilizationV2.model_validate(data)


@router.get(
    "/product-demand",
    response_model=ProductDemandV2,
    summary="Product demand — 4-week avg, YoY%, and 2Y weekly history",
    responses={502: {"description": "Upstream data source unavailable"}},
)
async def get_product_demand(
    eia: EIAService = Depends(get_eia_service),
) -> ProductDemandV2:
    async def fetch() -> Any:
        return await eia.get_product_supplied_full()

    demand_data = await call_upstream("EIA", fetch)
    data = compute_product_demand_v2(demand_data)
    return ProductDemandV2.model_validate(data)
