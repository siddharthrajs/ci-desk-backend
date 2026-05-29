"""Upstream tab — US subtab.

One endpoint per dashboard panel. The frontend composes the hero strip from
the latest values returned by /upstream/us/crude-production (cards 1, 3, 4)
and /upstream/us/rig-count (card 2). OPEC+ subtab is a separate router added
later.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.deps import get_eia_service
from app.core.upstream import call_upstream
from app.models.upstream import (
    ApiGravityResponse,
    CrudeImportsResponse,
    CrudeProductionResponse,
    ImportCountry,
    ImportsFeed,
    ImportsHistoryPoint,
    NaturalGasResponse,
    OpecHistoryResponse,
    OpecHero,
    OpecMemberRow,
    OpecProductionResponse,
    OpecSparkPoint,
    ProductionByRegionResponse,
    RegionLatest,
    ReservesResponse,
    RigCountResponse,
)
from app.services.eia import EIAService

router = APIRouter(prefix="/upstream", tags=["upstream"])


# ---------------------------------------------------------------------------
# /upstream/us/crude-production
# ---------------------------------------------------------------------------

@router.get(
    "/us/crude-production",
    response_model=CrudeProductionResponse,
    summary="US weekly + L48 crude production, net imports, monthly history",
    responses={502: {"description": "EIA unavailable"}},
)
async def get_us_crude_production(
    eia: EIAService = Depends(get_eia_service),
) -> CrudeProductionResponse:
    data = await call_upstream("EIA", eia.get_us_crude_production)
    return CrudeProductionResponse.model_validate(data)


# ---------------------------------------------------------------------------
# /upstream/us/rig-count
# ---------------------------------------------------------------------------

@router.get(
    "/us/rig-count",
    response_model=RigCountResponse,
    summary="Monthly EIA rotary rig count — total, oil, gas, onshore, offshore",
    responses={502: {"description": "EIA unavailable"}},
)
async def get_us_rig_count(
    eia: EIAService = Depends(get_eia_service),
) -> RigCountResponse:
    data = await call_upstream("EIA", eia.get_us_rig_count)
    return RigCountResponse.model_validate(data)


# ---------------------------------------------------------------------------
# /upstream/us/production-by-region
# ---------------------------------------------------------------------------

@router.get(
    "/us/production-by-region",
    response_model=ProductionByRegionResponse,
    summary="Monthly crude production by state/PADD — TX, ND, NM, PADD 2/3, GoA",
    responses={502: {"description": "EIA unavailable"}},
)
async def get_us_production_by_region(
    eia: EIAService = Depends(get_eia_service),
) -> ProductionByRegionResponse:
    data = await call_upstream("EIA", eia.get_us_production_by_region)
    return ProductionByRegionResponse(
        regions={k: RegionLatest(**v) for k, v in data.get("regions", {}).items()},
        history=data.get("history", []),
    )


# ---------------------------------------------------------------------------
# /upstream/us/api-gravity
# ---------------------------------------------------------------------------

@router.get(
    "/us/api-gravity",
    response_model=ApiGravityResponse,
    summary="Lower-48 crude production by API gravity bucket (monthly)",
    responses={502: {"description": "EIA unavailable"}},
)
async def get_us_api_gravity(
    eia: EIAService = Depends(get_eia_service),
) -> ApiGravityResponse:
    data = await call_upstream("EIA", eia.get_us_api_gravity)
    return ApiGravityResponse.model_validate(data)


# ---------------------------------------------------------------------------
# /upstream/us/crude-imports
# ---------------------------------------------------------------------------

@router.get(
    "/us/crude-imports",
    response_model=CrudeImportsResponse,
    summary="US crude imports by country — weekly preliminary + monthly final",
    responses={502: {"description": "EIA unavailable"}},
)
async def get_us_crude_imports(
    eia: EIAService = Depends(get_eia_service),
) -> CrudeImportsResponse:
    data = await call_upstream("EIA", eia.get_us_crude_imports)
    return CrudeImportsResponse(
        weekly_preliminary=ImportsFeed(
            total_mbd   = data["weekly_preliminary"].get("total_mbd"),
            top_origins = [ImportCountry(**o) for o in data["weekly_preliminary"].get("top_origins", [])],
            history     = [ImportsHistoryPoint(**h) for h in data["weekly_preliminary"].get("history", [])],
        ),
        monthly_final=ImportsFeed(
            total_mbd   = data["monthly_final"].get("total_mbd"),
            top_origins = [ImportCountry(**o) for o in data["monthly_final"].get("top_origins", [])],
            history     = [ImportsHistoryPoint(**h) for h in data["monthly_final"].get("history", [])],
        ),
    )


# ---------------------------------------------------------------------------
# /upstream/us/natural-gas
# ---------------------------------------------------------------------------

@router.get(
    "/us/natural-gas",
    response_model=NaturalGasResponse,
    summary="US natural gas production — gross withdrawals + dry, monthly 5Y",
    responses={502: {"description": "EIA unavailable"}},
)
async def get_us_natural_gas(
    eia: EIAService = Depends(get_eia_service),
) -> NaturalGasResponse:
    data = await call_upstream("EIA", eia.get_us_natural_gas)
    return NaturalGasResponse.model_validate(data)


# ---------------------------------------------------------------------------
# /upstream/us/reserves
# ---------------------------------------------------------------------------

@router.get(
    "/us/reserves",
    response_model=ReservesResponse,
    summary="US proved reserves — crude (BBbl) and dry natural gas (Tcf), annual",
    responses={502: {"description": "EIA unavailable"}},
)
async def get_us_reserves(
    eia: EIAService = Depends(get_eia_service),
) -> ReservesResponse:
    data = await call_upstream("EIA", eia.get_us_reserves)
    return ReservesResponse.model_validate(data)


# ---------------------------------------------------------------------------
# /upstream/opec/production
# ---------------------------------------------------------------------------

@router.get(
    "/opec/production",
    response_model=OpecProductionResponse,
    summary="OPEC+ crude production — hero KPIs, country table, 36M sparklines (monthly)",
    responses={502: {"description": "EIA unavailable"}},
)
async def get_opec_production(
    eia: EIAService = Depends(get_eia_service),
) -> OpecProductionResponse:
    data = await call_upstream("EIA", eia.get_opec_production)
    return OpecProductionResponse(
        hero=OpecHero(**data.get("hero", {})),
        table=[OpecMemberRow(**row) for row in data.get("table", [])],
        sparklines={
            iso3: [OpecSparkPoint(**pt) for pt in pts]
            for iso3, pts in data.get("sparklines", {}).items()
        },
    )


# ---------------------------------------------------------------------------
# /upstream/opec/history
# ---------------------------------------------------------------------------

@router.get(
    "/opec/history",
    response_model=OpecHistoryResponse,
    summary="OPEC+ crude production history — all members, 10Y monthly for stacked area",
    responses={502: {"description": "EIA unavailable"}},
)
async def get_opec_history(
    eia: EIAService = Depends(get_eia_service),
) -> OpecHistoryResponse:
    data = await call_upstream("EIA", eia.get_opec_history)
    return OpecHistoryResponse(
        members={
            iso3: [OpecSparkPoint(**pt) for pt in pts]
            for iso3, pts in data.get("members", {}).items()
        },
        periods_available=data.get("periods_available", 0),
    )
