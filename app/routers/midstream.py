"""Midstream tab — five sub-endpoints + legacy monolithic endpoint.

Sub-endpoints (new):
  GET /midstream/stocks          — commercial stocks + SPR + days of supply
  GET /midstream/refinery        — refinery utilization by PADD (2Y)
  GET /midstream/exports         — crude exports (weekly MBD + monthly PADD)
  GET /midstream/imports         — crude imports by country (monthly final)
  GET /midstream/padd-movements  — inter-PADD crude pipeline flows (monthly)

Legacy endpoint (kept for backward compat):
  GET /midstream                 — original monolithic response
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends

from app.core.deps import get_eia_service
from app.core.upstream import call_upstream
from app.models.common import SeriesPoint
from app.models.midstream import (
    CrudeExportsResponse,
    DaysOfSupply,
    ExportsHistPoint,
    ImportOrigin,
    ImportsHistPoint,
    Inventories,
    MidstreamImportsResponse,
    MidstreamResponse,
    MidstreamStocksResponse,
    PaddFlowPoint,
    PaddMovementsResponse,
    RefineryUtilizationHistory,
    StockHistPoint,
    StockSeries,
)
from app.services.eia import EIAService

router = APIRouter(prefix="/midstream", tags=["midstream"])

_502 = {502: {"description": "Upstream data source unavailable"}}


# ─── helper ──────────────────────────────────────────────────────────────────

def _points(rows: list[dict[str, Any]]) -> list[SeriesPoint]:
    return [SeriesPoint(**row) for row in rows]


# ─── /midstream/stocks ───────────────────────────────────────────────────────

@router.get(
    "/stocks",
    response_model=MidstreamStocksResponse,
    summary="Weekly US commercial petroleum stocks — crude, Cushing, gasoline, distillate, jet, SPR",
    responses=_502,
)
async def get_midstream_stocks(
    eia: EIAService = Depends(get_eia_service),
) -> MidstreamStocksResponse:
    data = await call_upstream("EIA", eia.get_midstream_stocks)

    def _series(d: dict[str, Any]) -> StockSeries:
        return StockSeries(
            latest_kbbl=d.get("latest_kbbl"),
            wow_kbbl=d.get("wow_kbbl"),
            history=[StockHistPoint(**pt) for pt in d.get("history", [])],
        )

    return MidstreamStocksResponse(
        crude=_series(data.get("crude", {})),
        cushing=_series(data.get("cushing", {})),
        gasoline=_series(data.get("gasoline", {})),
        distillate=_series(data.get("distillate", {})),
        jet=_series(data.get("jet", {})),
        spr=_series(data.get("spr", {})),
        dos_gasoline=data.get("dos_gasoline"),
        dos_distillate=data.get("dos_distillate"),
        dos_jet=data.get("dos_jet"),
    )


# ─── /midstream/refinery ─────────────────────────────────────────────────────

@router.get(
    "/refinery",
    response_model=RefineryUtilizationHistory,
    summary="Weekly refinery utilization (%) by PADD — 2Y history",
    responses=_502,
)
async def get_midstream_refinery(
    eia: EIAService = Depends(get_eia_service),
) -> RefineryUtilizationHistory:
    data = await call_upstream("EIA", eia.get_refinery_utilization_2yr)
    return RefineryUtilizationHistory(
        national=_points(data.get("national", [])),
        padd1=_points(data.get("padd1", [])),
        padd2=_points(data.get("padd2", [])),
        padd3=_points(data.get("padd3", [])),
        padd4=_points(data.get("padd4", [])),
        padd5=_points(data.get("padd5", [])),
    )


# ─── /midstream/exports ──────────────────────────────────────────────────────

@router.get(
    "/exports",
    response_model=CrudeExportsResponse,
    summary="US crude oil exports — weekly MBD trend + monthly PADD breakdown",
    responses=_502,
)
async def get_crude_exports(
    eia: EIAService = Depends(get_eia_service),
) -> CrudeExportsResponse:
    data = await call_upstream("EIA", eia.get_crude_exports)
    return CrudeExportsResponse(
        latest_mbd=data.get("latest_mbd"),
        wow_mbd=data.get("wow_mbd"),
        weekly_history=[ExportsHistPoint(**pt) for pt in data.get("weekly_history", [])],
        latest_period_m=data.get("latest_period_m"),
        padd1_mbbl=data.get("padd1_mbbl"),
        padd2_mbbl=data.get("padd2_mbbl"),
        padd3_mbbl=data.get("padd3_mbbl"),
        padd4_mbbl=data.get("padd4_mbbl"),
        padd5_mbbl=data.get("padd5_mbbl"),
        monthly_history=[ExportsHistPoint(**pt) for pt in data.get("monthly_history", [])],
    )


# ─── /midstream/imports ──────────────────────────────────────────────────────

@router.get(
    "/imports",
    response_model=MidstreamImportsResponse,
    summary="US crude imports by country — monthly final, top origins + OPEC+ share",
    responses=_502,
)
async def get_midstream_imports(
    eia: EIAService = Depends(get_eia_service),
) -> MidstreamImportsResponse:
    data = await call_upstream("EIA", eia.get_midstream_imports)
    return MidstreamImportsResponse(
        total_mbd=data.get("total_mbd"),
        top_origins=[ImportOrigin(**o) for o in data.get("top_origins", [])],
        history=[ImportsHistPoint(**pt) for pt in data.get("history", [])],
        opec_plus_mbd=data.get("opec_plus_mbd"),
        opec_plus_share=data.get("opec_plus_share"),
    )


# ─── /midstream/padd-movements ───────────────────────────────────────────────

@router.get(
    "/padd-movements",
    response_model=PaddMovementsResponse,
    summary="Inter-PADD crude pipeline movements — 13 directional pairs, monthly 3Y",
    responses=_502,
)
async def get_padd_movements(
    eia: EIAService = Depends(get_eia_service),
) -> PaddMovementsResponse:
    data = await call_upstream("EIA", eia.get_padd_movements)
    return PaddMovementsResponse(
        latest_period=data.get("latest_period"),
        flows={
            pair: [PaddFlowPoint(**pt) for pt in pts]
            for pair, pts in data.get("flows", {}).items()
        },
        net_receipts=data.get("net_receipts", {}),
        flow_labels=data.get("flow_labels", {}),
    )


# ─── legacy /midstream (backward compat) ─────────────────────────────────────

@router.get(
    "",
    response_model=MidstreamResponse,
    summary="[Legacy] Inventories, SPR, refinery utilization, days of supply",
    responses=_502,
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

    (crude, cushing, gasoline, distillate, spr, refinery, product_supplied) = \
        await call_upstream("EIA", fetch_all)

    def _latest(rows: list[dict[str, Any]]) -> float | None:
        return rows[0]["value"] if rows else None

    def _dos(stocks: list[dict[str, Any]], demand: list[dict[str, Any]]) -> float | None:
        s, d = _latest(stocks), _latest(demand)
        return round(s / d, 1) if (s is not None and d) else None

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
            gasoline=_dos(gasoline, product_supplied.get("gasoline", [])),
            distillate=_dos(distillate, product_supplied.get("distillate", [])),
        ),
    )
