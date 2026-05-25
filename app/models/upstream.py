"""Response models for the Upstream dashboard tab."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.models.common import SeriesPoint, utc_now


class RigCount(BaseModel):
    """Baker Hughes weekly rig count (sourced from FRED proxy RIGTNXUS).

    The oil/gas breakdown is always None — see README.md.
    """

    available: bool = Field(..., description="False if all upstream rig-count sources failed")
    source: str = Field(..., description="Identifier of the underlying data source")
    report_date: str | None = Field(None, description="ISO date of the most recent rig count")
    total: int | None = Field(None, description="Total U.S. rotary rig count")
    oil: int | None = Field(None, description="Oil rig count (None — see README)")
    gas: int | None = Field(None, description="Gas rig count (None — see README)")
    wow_change: float | None = Field(None, description="Week-over-week change in total rigs")
    reason: str | None = Field(None, description="Failure reason when available=False")


class OPECProduction(BaseModel):
    """OPEC monthly production placeholder.

    No free, redistribution-friendly OPEC MOMR feed exists — see README.md. The
    payload is shaped so the frontend can render an "unavailable" state today
    and switch to live data the moment a paid provider is wired in.
    """

    available: bool = Field(False, description="Always False until a paid provider is wired in")
    source: str = Field("placeholder", description="Identifier of the underlying data source")
    reason: str = Field(
        "OPEC MOMR production data requires a paid data provider (see README)",
        description="Human-readable explanation",
    )
    report_date: str | None = Field(None, description="ISO date of the most recent value")
    total_kbpd: float | None = Field(None, description="OPEC total production, thousand bbl/day")


class UpstreamResponse(BaseModel):
    """Upstream tab payload: production, rig activity, OPEC supply."""

    crude_production: list[SeriesPoint] = Field(
        ..., description="Weekly U.S. crude oil field production (kbpd), newest-first"
    )
    rig_count: RigCount = Field(..., description="Latest Baker Hughes rig count")
    opec: OPECProduction = Field(..., description="OPEC production placeholder")
    last_updated: datetime = Field(
        default_factory=utc_now, description="UTC timestamp when this payload was assembled"
    )
