"""News tab — market news, company news, oil quotes, economic calendar (Finnhub)."""

from __future__ import annotations

import asyncio
from datetime import date, timedelta

from fastapi import APIRouter, Depends, Query

from app.core.deps import get_finnhub_service
from app.core.upstream import call_upstream
from app.models.news import (
    CompanyNewsResponse,
    EconomicCalendarResponse,
    EconomicEvent,
    MarketNewsResponse,
    NewsArticle,
    OilQuotesResponse,
    QuoteData,
)
from app.services.finnhub import OIL_TICKERS, FinnhubService

router = APIRouter(prefix="/news", tags=["news"])


@router.get(
    "/market",
    response_model=MarketNewsResponse,
    summary="Market-wide news feed",
    responses={502: {"description": "Upstream Finnhub fetch failed"}},
)
async def get_market_news(
    category: str = Query(
        "general",
        description="Finnhub news category: general | forex | crypto | merger",
    ),
    finnhub: FinnhubService = Depends(get_finnhub_service),
) -> MarketNewsResponse:
    articles_raw = await call_upstream(
        "Finnhub", lambda: finnhub.get_market_news(category)
    )
    return MarketNewsResponse(
        category=category,
        articles=[NewsArticle(**a) for a in articles_raw],
    )


@router.get(
    "/company",
    response_model=CompanyNewsResponse,
    summary="Company-specific news for a ticker symbol",
    responses={502: {"description": "Upstream Finnhub fetch failed"}},
)
async def get_company_news(
    symbol: str = Query("XOM", description="Ticker symbol, defaults to XOM"),
    from_date: str | None = Query(None, description="Start date YYYY-MM-DD, defaults to 30 days ago"),
    to_date: str | None = Query(None, description="End date YYYY-MM-DD, defaults to today"),
    finnhub: FinnhubService = Depends(get_finnhub_service),
) -> CompanyNewsResponse:
    today = date.today()
    _to = to_date or today.isoformat()
    _from = from_date or (today - timedelta(days=30)).isoformat()

    articles_raw = await call_upstream(
        "Finnhub", lambda: finnhub.get_company_news(symbol.upper(), _from, _to)
    )
    return CompanyNewsResponse(
        symbol=symbol.upper(),
        from_date=_from,
        to_date=_to,
        articles=[NewsArticle(**a) for a in articles_raw],
    )


@router.get(
    "/quotes",
    response_model=OilQuotesResponse,
    summary="Real-time quotes for all tracked oil & energy tickers",
    responses={502: {"description": "Upstream Finnhub fetch failed"}},
)
async def get_oil_quotes(
    finnhub: FinnhubService = Depends(get_finnhub_service),
) -> OilQuotesResponse:
    async def fetch_all() -> list[dict]:
        return list(
            await asyncio.gather(*[finnhub.get_quote(ticker) for ticker in OIL_TICKERS])
        )

    quotes_raw = await call_upstream("Finnhub", fetch_all)
    return OilQuotesResponse(quotes=[QuoteData(**q) for q in quotes_raw])


@router.get(
    "/calendar",
    response_model=EconomicCalendarResponse,
    summary="Economic calendar: EIA inventory reports, OPEC dates, macro events",
    responses={502: {"description": "Upstream Finnhub fetch failed"}},
)
async def get_economic_calendar(
    from_date: str | None = Query(None, description="Start date YYYY-MM-DD, defaults to 7 days ago"),
    to_date: str | None = Query(None, description="End date YYYY-MM-DD, defaults to 30 days ahead"),
    finnhub: FinnhubService = Depends(get_finnhub_service),
) -> EconomicCalendarResponse:
    today = date.today()
    _from = from_date or (today - timedelta(days=7)).isoformat()
    _to = to_date or (today + timedelta(days=30)).isoformat()

    calendar_raw = await call_upstream(
        "Finnhub", lambda: finnhub.get_economic_calendar(_from, _to)
    )
    events_raw = calendar_raw.get("economicCalendar", []) if isinstance(calendar_raw, dict) else []
    return EconomicCalendarResponse(
        from_date=_from,
        to_date=_to,
        events=[EconomicEvent(**e) for e in events_raw],
    )
