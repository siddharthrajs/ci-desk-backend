"""Response models for the News dashboard tab (Finnhub data)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.models.common import utc_now


class NewsArticle(BaseModel):
    """Single article from Finnhub /news or /company-news."""

    id: int | None = Field(None, description="Finnhub article ID")
    category: str | None = Field(None, description="News category")
    datetime: int | None = Field(None, description="Unix timestamp of publication")
    headline: str = Field(..., description="Article headline")
    image: str | None = Field(None, description="Thumbnail image URL")
    related: str | None = Field(None, description="Related ticker symbol(s)")
    source: str | None = Field(None, description="Publication name")
    summary: str | None = Field(None, description="Article summary or teaser")
    url: str | None = Field(None, description="Link to full article")


class MarketNewsResponse(BaseModel):
    category: str = Field(..., description="Finnhub news category used for the query")
    articles: list[NewsArticle] = Field(default_factory=list)
    last_updated: datetime = Field(default_factory=utc_now)


class CompanyNewsResponse(BaseModel):
    symbol: str = Field(..., description="Ticker symbol")
    from_date: str = Field(..., description="Start of the queried date range (YYYY-MM-DD)")
    to_date: str = Field(..., description="End of the queried date range (YYYY-MM-DD)")
    articles: list[NewsArticle] = Field(default_factory=list)
    last_updated: datetime = Field(default_factory=utc_now)


class QuoteData(BaseModel):
    """Real-time quote for a single ticker from Finnhub /quote."""

    symbol: str = Field(..., description="Ticker symbol")
    c: float | None = Field(None, description="Current price")
    d: float | None = Field(None, description="Absolute change from previous close")
    dp: float | None = Field(None, description="Percent change from previous close")
    h: float | None = Field(None, description="Intraday high")
    l: float | None = Field(None, description="Intraday low")
    o: float | None = Field(None, description="Open price")
    pc: float | None = Field(None, description="Previous close price")
    t: int | None = Field(None, description="Unix timestamp of last price update")


class OilQuotesResponse(BaseModel):
    quotes: list[QuoteData] = Field(default_factory=list)
    last_updated: datetime = Field(default_factory=utc_now)


class EconomicEvent(BaseModel):
    """Single event from Finnhub /calendar/economic."""

    actual: float | None = Field(None, description="Reported actual value")
    country: str | None = Field(None, description="Country code (e.g. US)")
    estimate: float | None = Field(None, description="Consensus estimate")
    event: str | None = Field(None, description="Event name or description")
    impact: str | None = Field(None, description="Impact level: low | medium | high")
    prev: float | None = Field(None, description="Previous period value")
    time: str | None = Field(None, description="Event date/time string")
    unit: str | None = Field(None, description="Unit of measurement")


class EconomicCalendarResponse(BaseModel):
    from_date: str = Field(..., description="Start of the queried date range (YYYY-MM-DD)")
    to_date: str = Field(..., description="End of the queried date range (YYYY-MM-DD)")
    events: list[EconomicEvent] = Field(default_factory=list)
    last_updated: datetime = Field(default_factory=utc_now)


class AiSummaryRequest(BaseModel):
    prompt: str | None = Field(None, description="Custom prompt; uses default crude oil analyst prompt if omitted")
    provider: str = Field("gemini", description="AI provider: gemini | openai")


class AiSummaryResponse(BaseModel):
    summary: str = Field(..., description="AI-generated macro summary text")
    item_count: int = Field(..., description="Number of Financial Juice news items analyzed")
    generated_at: datetime = Field(default_factory=utc_now)
