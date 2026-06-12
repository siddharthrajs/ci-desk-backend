"""Positions tab — historical COT time series per contract."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Path

from app.core.deps import get_cftc_service
from app.core.upstream import call_upstream
from app.models.reports import COTHistoryResponse
from app.services.cftc import CFTCService

router = APIRouter(prefix="/positions", tags=["positions"])


@router.get(
    "/cot/{contract_code}",
    response_model=COTHistoryResponse,
    summary="CFTC COT — 3-year weekly history for a single petroleum contract",
    responses={502: {"description": "Upstream CFTC fetch failed"}},
)
async def get_cot_history(
    contract_code: str = Path(..., description="CFTC contract market code (e.g. 067651 for WTI-Physical)"),
    cftc: CFTCService = Depends(get_cftc_service),
) -> COTHistoryResponse:
    payload = await call_upstream(
        "CFTC", lambda: cftc.get_contract_history(contract_code)
    )
    return COTHistoryResponse(**payload)
