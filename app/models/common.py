"""Shared response primitives used across multiple endpoints."""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    """Current UTC time with microseconds stripped — used as `last_updated` default."""
    return datetime.now(timezone.utc).replace(microsecond=0)


class SeriesPoint(BaseModel):
    """A single observation in a time series with optional WoW change metrics."""

    period: str = Field(..., description="ISO date or EIA-style period (YYYY-MM-DD or YYYY-MM)")
    value: float = Field(..., description="Numeric observation value")
    wow_change: float | None = Field(
        None, description="Week-over-week absolute change vs the prior period"
    )
    wow_pct_change: float | None = Field(
        None, description="Week-over-week percent change vs the prior period"
    )


class FredObservation(BaseModel):
    """A single FRED series observation. Dates use FRED's `date` field name."""

    date: str = Field(..., description="ISO date (YYYY-MM-DD)")
    value: float = Field(..., description="Numeric observation value")


class FredSeries(BaseModel):
    """Latest value + recent observation history for a FRED economic series."""

    series_id: str = Field(..., description="FRED series identifier (e.g. DGS10)")
    latest_value: float | None = Field(None, description="Most recent non-missing value")
    latest_date: str | None = Field(None, description="ISO date of the most recent value")
    observations: list[FredObservation] = Field(
        default_factory=list, description="Recent observations, newest-first"
    )
