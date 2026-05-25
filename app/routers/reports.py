"""Reports tab — EIA WPSR tables and CFTC COT managed money positions."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends

from app.core.deps import get_cftc_service, get_wpsr_service
from app.core.upstream import call_upstream
from app.models.reports import (
    COTResponse,
    ManagedMoneyPosition,
    WPSRResponse,
    WPSRTable,
)
from app.services.cftc import BRENT_CODE, WTI_CODE, CFTCService
from app.services.wpsr import WPSRService

router = APIRouter(prefix="/reports", tags=["reports"])


@router.get(
    "/wpsr",
    response_model=WPSRResponse,
    summary="EIA Weekly Petroleum Status Report — all tables",
    responses={502: {"description": "Upstream EIA WPSR fetch failed"}},
)
async def get_wpsr(
    wpsr: WPSRService = Depends(get_wpsr_service),
) -> WPSRResponse:
    payload = await call_upstream("WPSR", wpsr.get_all_wpsr_tables)
    tables = {key: WPSRTable(**table) for key, table in payload["tables"].items()}
    return WPSRResponse(
        tables=tables,
        hash=payload["hash"],
        last_fetched=payload["last_fetched"],
    )


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
