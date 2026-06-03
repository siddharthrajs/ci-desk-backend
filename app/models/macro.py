"""Response models for the Macro dashboard tab."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.models.common import FredSeries, utc_now


class MacroResponse(BaseModel):
    """Macro tab payload: USD, rates, oil benchmark — all sourced from FRED."""

    dxy: FredSeries = Field(..., description="Nominal Broad U.S. Dollar Index (DTWEXBGS)")
    us10y: FredSeries = Field(..., description="10-Year U.S. Treasury constant maturity rate (DGS10)")
    fed_funds: FredSeries = Field(..., description="Effective Federal Funds Rate (FEDFUNDS)")
    wti: FredSeries = Field(..., description="WTI crude oil spot price, Cushing OK (DCOILWTICO)")
    last_updated: datetime = Field(
        default_factory=utc_now, description="UTC timestamp when this payload was assembled"
    )


class Article(BaseModel):
    title: str
    link: str
    published: str
    summary: str | None = None


class SourceBrief(BaseModel):
    source: str
    url: str
    articles: list[Article]


class MorningBriefResponse(BaseModel):
    sources: list[SourceBrief]
    generated_at: datetime
