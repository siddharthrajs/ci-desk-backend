"""Upstream tab — crude production, rig activity, OPEC supply placeholder."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends

from app.core.deps import get_baker_hughes_service, get_eia_service
from app.core.upstream import call_upstream
from app.models.common import SeriesPoint
from app.models.upstream import (
    BasinDuc,
    CrudeImportsResponse,
    DucHistoryPoint,
    DucWellsResponse,
    ImportHistoryPoint,
    ImportOrigin,
    MonthlyProductionPoint,
    OPECProduction,
    RigCount,
    UpstreamResponse,
    UsProductionResponse,
    WeeklyProductionPoint,
)
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


@router.get(
    "/us-production",
    response_model=UsProductionResponse,
    summary="US crude production — weekly estimate + monthly PADD/region breakdown",
    responses={502: {"description": "EIA unavailable"}},
)
async def get_us_production(
    eia: EIAService = Depends(get_eia_service),
) -> UsProductionResponse:
    async def fetch_all() -> tuple[Any, Any]:
        return await asyncio.gather(
            eia.get_crude_production(),
            eia.get_us_production_monthly(),
        )

    weekly_data, monthly_data = await call_upstream("EIA", fetch_all)

    latest = weekly_data[0] if weekly_data else None

    weekly_history = [
        WeeklyProductionPoint(date=p["period"], value=round(p["value"] / 1000, 3))
        for p in weekly_data
    ]
    monthly_history = [
        MonthlyProductionPoint(**pt)
        for pt in monthly_data.get("monthly_history", [])
    ]

    return UsProductionResponse(
        weekly_estimate_mbd=round(latest["value"] / 1000, 3)           if latest                         else None,
        weekly_wow_change=  round(latest["wow_change"] / 1000, 3)      if latest and latest.get("wow_change") is not None else None,
        monthly_history=monthly_history,
        weekly_history=weekly_history,
    )


@router.get(
    "/debug/duc-discovery",
    summary="[DEBUG] Broad EIA API probe to find DUC / DPR data",
    include_in_schema=False,
)
async def debug_duc_discovery(
    eia: EIAService = Depends(get_eia_service),
) -> dict[str, Any]:
    """Phase-2 broad probe — call after learning petroleum/crd has no duc/dprd routes."""
    results: dict[str, Any] = {}

    # Probe EIA root + petroleum + sibling sections that might host DPR
    meta_paths = [
        "",                        # root — lists all top-level sections
        "petroleum",               # petroleum root — lists all sub-sections
        "petroleum/crd/drill",     # drilling activity (Baker Hughes rig counts)
        "petroleum/crd/wellend",   # exploratory & development wells drilled
        "petroleum/sum",           # petroleum supply summary
        "petroleum/sum/snd",       # supply/summary (non-weekly variant)
    ]

    for path in meta_paths:
        url = f"{eia.BASE_URL}/{path}" if path else eia.BASE_URL
        try:
            resp = await eia._client.get(url, params={"api_key": eia._api_key})
            body = resp.json() if resp.status_code == 200 else resp.text[:600]
            results[f"meta:{path or 'root'}"] = {"status": resp.status_code, "body": body}
        except Exception as exc:
            results[f"meta:{path or 'root'}"] = {"error": str(exc)}

    # Raw data sample from drill + wellend — see what fields/facets exist
    for route in ["petroleum/crd/drill", "petroleum/crd/wellend"]:
        try:
            rows = await eia._fetch_eia_series(route, {}, frequency="monthly", length=3)
            results[f"data:{route}"] = rows
        except Exception as exc:
            results[f"data:{route}"] = {"error": str(exc)}

    return results


@router.get(
    "/duc-wells",
    response_model=DucWellsResponse,
    summary="DUC (Drilled but Uncompleted) wells by basin — EIA DPR monthly",
)
async def get_duc_wells(
    eia: EIAService = Depends(get_eia_service),
) -> DucWellsResponse:
    try:
        data = await call_upstream("EIA", eia.get_duc_wells)
    except Exception:
        # Return empty-but-valid payload so the panel renders gracefully
        return DucWellsResponse()

    history = [DucHistoryPoint(**pt) for pt in data.get("history", [])]
    basins  = {k: BasinDuc(**v) for k, v in data.get("basins", {}).items()}

    return DucWellsResponse(
        total_duc=  data.get("total_duc"),
        mom_change= data.get("mom_change"),
        mom_pct=    data.get("mom_pct"),
        yoy_change= data.get("yoy_change"),
        yoy_pct=    data.get("yoy_pct"),
        signal=     data.get("signal", "NEUTRAL"),
        history=    history,
        basins=     basins,
    )


@router.get(
    "/crude-imports",
    response_model=CrudeImportsResponse,
    summary="US crude oil imports by country of origin — EIA monthly, top 10",
    responses={502: {"description": "EIA unavailable"}},
)
async def get_crude_imports(
    eia: EIAService = Depends(get_eia_service),
) -> CrudeImportsResponse:
    data = await call_upstream("EIA", eia.get_crude_imports)

    return CrudeImportsResponse(
        total_imports_mbd=data.get("total_imports_mbd"),
        top_origins=[ImportOrigin(**o) for o in data.get("top_origins", [])],
        history_total=[ImportHistoryPoint(**pt) for pt in data.get("history_total", [])],
    )
