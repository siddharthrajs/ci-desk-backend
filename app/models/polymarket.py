"""Response models for Polymarket prediction markets tab."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.models.common import utc_now


class PolymarketTag(BaseModel):
    id: str | None = None
    label: str | None = None
    slug: str | None = None


class PolymarketMarket(BaseModel):
    """Single binary-outcome market with embedded prices."""

    condition_id: str = Field(..., alias="conditionId", description="Unique condition ID (0x...)")
    question: str = Field(..., description="The market question")
    outcomes: list[str] = Field(default_factory=list, description='Outcome labels e.g. ["Yes","No"]')
    outcome_prices: list[float] = Field(
        default_factory=list,
        alias="outcomePrices",
        description="Implied probabilities per outcome (0–1)",
    )
    volume: float | None = Field(None, description="Total lifetime volume (USD)")
    volume_24h: float | None = Field(None, alias="volume24hr", description="24h trading volume (USD)")
    liquidity: float | None = Field(None, description="Current market liquidity (USD)")
    active: bool = Field(False, description="Whether the market is accepting orders")
    closed: bool = Field(False, description="Whether the market has closed/resolved")
    end_date: datetime | None = Field(None, alias="endDate", description="Market expiry datetime")
    start_date: datetime | None = Field(None, alias="startDate")
    slug: str | None = None
    image: str | None = None
    tags: list[PolymarketTag] = Field(default_factory=list)

    model_config = {"populate_by_name": True}

    @field_validator("outcomes", mode="before")
    @classmethod
    def _parse_outcomes(cls, v: Any) -> list[str]:
        if isinstance(v, str):
            try:
                return json.loads(v)
            except (json.JSONDecodeError, ValueError):
                return []
        return v or []

    @field_validator("outcome_prices", mode="before")
    @classmethod
    def _parse_prices(cls, v: Any) -> list[float]:
        if isinstance(v, str):
            try:
                return [float(p) for p in json.loads(v)]
            except (json.JSONDecodeError, ValueError, TypeError):
                return []
        if isinstance(v, list):
            return [float(p) for p in v]
        return []

    @field_validator("volume", "liquidity", mode="before")
    @classmethod
    def _parse_numeric_string(cls, v: Any) -> float | None:
        if v is None:
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None


class PolymarketEvent(BaseModel):
    """An event grouping one or more related markets."""

    id: str = Field(..., description="Event ID")
    title: str = Field(..., description="Event title")
    slug: str | None = None
    description: str | None = None
    active: bool = False
    closed: bool = False
    volume: float | None = None
    start_date: datetime | None = Field(None, alias="startDate")
    end_date: datetime | None = Field(None, alias="endDate")
    image: str | None = None
    tags: list[PolymarketTag] = Field(default_factory=list)
    markets: list[PolymarketMarket] = Field(default_factory=list)

    model_config = {"populate_by_name": True}

    @field_validator("volume", mode="before")
    @classmethod
    def _parse_volume(cls, v: Any) -> float | None:
        if v is None:
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None


class MarketsResponse(BaseModel):
    markets: list[PolymarketMarket]
    count: int = Field(..., description="Number of markets returned")
    last_updated: datetime = Field(default_factory=utc_now)


class MarketResponse(BaseModel):
    market: PolymarketMarket
    last_updated: datetime = Field(default_factory=utc_now)


class EventsResponse(BaseModel):
    events: list[PolymarketEvent]
    count: int = Field(..., description="Number of events returned")
    last_updated: datetime = Field(default_factory=utc_now)


class EventResponse(BaseModel):
    event: PolymarketEvent
    last_updated: datetime = Field(default_factory=utc_now)
