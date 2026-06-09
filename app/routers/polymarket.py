"""Polymarket prediction markets — market list, single market, events."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query

from app.core.deps import get_polymarket_service
from app.core.upstream import call_upstream
from app.models.polymarket import (
    EventResponse,
    EventsResponse,
    MarketResponse,
    MarketsResponse,
    PolymarketEvent,
    PolymarketMarket,
)
from app.services.polymarket import PolymarketService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/prediction-markets/polymarket", tags=["polymarket"])


@router.get(
    "/markets",
    response_model=MarketsResponse,
    summary="List Polymarket markets with current prices",
    responses={502: {"description": "Upstream Polymarket fetch failed"}},
)
async def list_markets(
    limit: int = Query(20, ge=1, le=100, description="Number of markets to return"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
    active: bool | None = Query(None, description="Filter by active status"),
    closed: bool | None = Query(None, description="Filter by closed status"),
    tag_id: int | None = Query(None, description="Filter by tag ID e.g. 1396=international-affairs, 933=federal-government"),
    q: str | None = Query(None, description="Keyword filter on market question e.g. iran, russia, trump"),
    svc: PolymarketService = Depends(get_polymarket_service),
) -> MarketsResponse:
    if q:
        raw = await call_upstream(
            "Polymarket",
            lambda: svc.search_markets(q=q, limit=limit, active=active, closed=closed, tag_id=tag_id),
        )
    else:
        raw = await call_upstream(
            "Polymarket",
            lambda: svc.get_markets(limit=limit, offset=offset, active=active, closed=closed, tag_id=tag_id),
        )
    markets = [PolymarketMarket.model_validate(m) for m in raw]
    return MarketsResponse(markets=markets, count=len(markets))


@router.get(
    "/markets/{condition_id}",
    response_model=MarketResponse,
    summary="Get a single Polymarket market by condition ID",
    responses={502: {"description": "Upstream Polymarket fetch failed"}},
)
async def get_market(
    condition_id: str,
    svc: PolymarketService = Depends(get_polymarket_service),
) -> MarketResponse:
    raw = await call_upstream("Polymarket", lambda: svc.get_market(condition_id))
    return MarketResponse(market=PolymarketMarket.model_validate(raw))


@router.get(
    "/events",
    response_model=EventsResponse,
    summary="List Polymarket events (each event groups related markets)",
    responses={502: {"description": "Upstream Polymarket fetch failed"}},
)
async def list_events(
    limit: int = Query(20, ge=1, le=100, description="Number of events to return"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
    active: bool | None = Query(None, description="Filter by active status"),
    closed: bool | None = Query(None, description="Filter by closed status"),
    tag_id: int | None = Query(None, description="Filter by tag ID e.g. 1396=international-affairs, 933=federal-government"),
    svc: PolymarketService = Depends(get_polymarket_service),
) -> EventsResponse:
    raw = await call_upstream(
        "Polymarket",
        lambda: svc.get_events(limit=limit, offset=offset, active=active, closed=closed, tag_id=tag_id),
    )
    events = [PolymarketEvent.model_validate(e) for e in raw]
    return EventsResponse(events=events, count=len(events))


@router.get(
    "/events/{event_id}",
    response_model=EventResponse,
    summary="Get a single Polymarket event by ID, including its markets",
    responses={502: {"description": "Upstream Polymarket fetch failed"}},
)
async def get_event(
    event_id: str,
    svc: PolymarketService = Depends(get_polymarket_service),
) -> EventResponse:
    raw = await call_upstream("Polymarket", lambda: svc.get_event(event_id))
    return EventResponse(event=PolymarketEvent.model_validate(raw))
