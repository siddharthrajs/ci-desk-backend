"""Macro tab — DXY, 10Y Treasury, Fed funds, WTI (all sourced from FRED) + Morning Brief."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from fastapi import APIRouter, Depends

from app.core.cache import get_cache
from app.core.deps import get_fred_service
from app.core.upstream import call_upstream
from app.models.common import FredSeries
from app.models.macro import MacroResponse, MorningBriefResponse
from app.services.fred import FREDService

router = APIRouter(prefix="/macro", tags=["macro"])


@router.get(
    "",
    response_model=MacroResponse,
    summary="Macro indicators: DXY, 10Y Treasury, Fed funds, WTI",
    responses={502: {"description": "Upstream FRED fetch failed"}},
)
async def get_macro(
    fred: FREDService = Depends(get_fred_service),
) -> MacroResponse:
    async def fetch_all() -> tuple[dict, dict, dict, dict]:
        return await asyncio.gather(
            fred.get_dxy(),
            fred.get_us10y(),
            fred.get_fed_funds(),
            fred.get_wti(),
        )

    dxy, us10y, fed_funds, wti = await call_upstream("FRED", fetch_all)

    return MacroResponse(
        dxy=FredSeries(**dxy),
        us10y=FredSeries(**us10y),
        fed_funds=FredSeries(**fed_funds),
        wti=FredSeries(**wti),
    )


@router.get(
    "/brief/debug-feed",
    summary="Debug a single RSS feed — returns status, content-type, raw snippet, and feedparser entry count",
    include_in_schema=False,
)
async def debug_feed(url: str) -> dict:
    import feedparser as fp
    from app.core.http_client import get_http_client
    from app.services.rss import _RSS_HEADERS
    from urllib.parse import unquote

    client = get_http_client()
    try:
        r = await client.get(unquote(url), timeout=15.0, follow_redirects=True, headers=_RSS_HEADERS)
        parsed = fp.parse(r.content)
        return {
            "status_code": r.status_code,
            "content_type": r.headers.get("content-type", ""),
            "content_length": len(r.content),
            "encoding": r.encoding,
            "feedparser_entries": len(parsed.entries),
            "feedparser_bozo": parsed.bozo,
            "feedparser_bozo_exception": str(parsed.bozo_exception) if parsed.bozo else None,
            "first_bytes_hex": r.content[:16].hex(),
            "raw_snippet": r.content[:500].decode("utf-8", errors="replace"),
        }
    except Exception as exc:
        return {"error": str(exc)}


@router.post(
    "/brief/refresh",
    response_model=MorningBriefResponse,
    summary="Manually trigger a morning brief fetch (same as the 08:00 IST scheduler job)",
)
async def refresh_morning_brief() -> MorningBriefResponse:
    from app.core.http_client import get_http_client
    from app.services.rss import RssService

    svc = RssService(get_http_client())
    payload = await svc.fetch_all()
    return MorningBriefResponse(**payload)


@router.get(
    "/brief",
    response_model=MorningBriefResponse,
    summary="Morning brief: energy RSS headlines grouped by source",
)
async def get_morning_brief() -> MorningBriefResponse:
    try:
        cached = await get_cache().get_json("macro:brief")
    except Exception:
        cached = None
    if cached is not None:
        return MorningBriefResponse(**cached)
    return MorningBriefResponse(sources=[], generated_at=datetime.now(timezone.utc))
