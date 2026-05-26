"""Reports tab — EIA WPSR tables and CFTC COT managed money positions."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, Path, Query

from app.core.cache import get_cache
from app.core.deps import get_cftc_service, get_wpsr_service
from app.core.upstream import call_upstream
from app.models.reports import (
    COTResponse,
    ManagedMoneyPosition,
    WPSRResponse,
    WPSRTable,
)
from app.services.cftc import BRENT_CODE, WTI_CODE, CFTCService
from app.services.wpsr import TABLE_NUMBERS, WPSRService

router = APIRouter(prefix="/reports", tags=["reports"])


_WPSR_ALL_KEY = "wpsr:v2:all"


def _wpsr_table_key(n: int) -> str:
    return f"wpsr:v2:table:{n}"


async def _bust_wpsr_cache() -> None:
    """Delete all WPSR cache keys so the next fetch hits EIA directly."""
    cache = get_cache()
    await cache.delete(_WPSR_ALL_KEY)
    for n in TABLE_NUMBERS:
        await cache.delete(_wpsr_table_key(n))


@router.get(
    "/wpsr",
    response_model=WPSRResponse,
    summary="EIA Weekly Petroleum Status Report — all tables (1..9)",
    responses={502: {"description": "Upstream EIA WPSR fetch failed"}},
)
async def get_wpsr(
    refresh: bool = Query(
        False,
        description="If true, bypass the 1-hour cache and re-scrape from EIA.",
    ),
    wpsr: WPSRService = Depends(get_wpsr_service),
) -> WPSRResponse:
    if refresh:
        await _bust_wpsr_cache()
    payload = await call_upstream("WPSR", wpsr.get_all_wpsr_tables)
    tables = {key: WPSRTable(**table) for key, table in payload["tables"].items()}
    return WPSRResponse(
        tables=tables,
        hash=payload["hash"],
        last_fetched=payload["last_fetched"],
    )


@router.get(
    "/wpsr/{table_number}",
    response_model=WPSRTable,
    summary="EIA Weekly Petroleum Status Report — single table by number",
    responses={
        400: {"description": "table_number must be in 1..9"},
        502: {"description": "Upstream EIA WPSR fetch failed"},
    },
)
async def get_wpsr_single_table(
    table_number: int = Path(..., ge=1, le=9, description="WPSR table number (1..9)"),
    refresh: bool = Query(
        False,
        description="If true, bypass the 1-hour cache and re-scrape from EIA.",
    ),
    wpsr: WPSRService = Depends(get_wpsr_service),
) -> WPSRTable:
    if refresh:
        cache = get_cache()
        await cache.delete(_wpsr_table_key(table_number))
        await cache.delete(_WPSR_ALL_KEY)
    payload = await call_upstream(
        "WPSR", lambda: wpsr.get_wpsr_table(table_number)
    )
    return WPSRTable(**payload)


@router.get(
    "/cot",
    response_model=COTResponse,
    summary="CFTC Commitments of Traders — WTI and Brent managed money positions",
    responses={502: {"description": "Upstream CFTC fetch failed"}},
)
async def get_cot(
    cftc: CFTCService = Depends(get_cftc_service),
) -> COTResponse:
    async def fetch_both() -> tuple[dict, dict]:
        return await asyncio.gather(
            cftc.get_managed_money_positions(WTI_CODE),
            cftc.get_managed_money_positions(BRENT_CODE),
        )

    wti, brent = await call_upstream("CFTC", fetch_both)
    return COTResponse(
        wti=ManagedMoneyPosition(**wti),
        brent=ManagedMoneyPosition(**brent),
    )
