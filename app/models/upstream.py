"""Response models for the Upstream dashboard tab."""

from __future__ import annotations

from datetime import datetime
from typing import Any

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


# ---------------------------------------------------------------------------
# Enhanced US production sub-endpoint
# ---------------------------------------------------------------------------

class MonthlyProductionPoint(BaseModel):
    date: str = Field(..., description="YYYY-MM-DD (first of month)")
    us_total: float | None = Field(None, description="US total crude production, MBD")
    padd3:    float | None = Field(None, description="PADD 3 Gulf Coast, MBD")
    padd2:    float | None = Field(None, description="PADD 2 Midwest, MBD")
    gom:      float | None = Field(None, description="Gulf of Mexico offshore, MBD")


class WeeklyProductionPoint(BaseModel):
    date:  str   = Field(..., description="YYYY-MM-DD")
    value: float = Field(..., description="US total crude production, MBD")


class UsProductionResponse(BaseModel):
    weekly_estimate_mbd: float | None  = Field(None, description="Latest weekly estimate, MBD")
    weekly_wow_change:   float | None  = Field(None, description="WoW change in MBD")
    monthly_history: list[MonthlyProductionPoint] = Field(default_factory=list)
    weekly_history:  list[WeeklyProductionPoint]  = Field(default_factory=list)
    last_updated: datetime = Field(default_factory=utc_now)


# ---------------------------------------------------------------------------
# DUC wells sub-endpoint
# ---------------------------------------------------------------------------

class BasinDuc(BaseModel):
    current:    int | None = None
    mom_change: int | None = None


class DucHistoryPoint(BaseModel):
    date:        str      = Field(..., description="YYYY-MM-DD (first of month)")
    total:       int | None = None
    permian:     int | None = None
    eagle_ford:  int | None = None
    bakken:      int | None = None
    niobrara:    int | None = None
    appalachia:  int | None = None
    anadarko:    int | None = None
    haynesville: int | None = None


class DucWellsResponse(BaseModel):
    last_updated: datetime = Field(default_factory=utc_now)
    total_duc:   int | None   = None
    mom_change:  int | None   = None
    mom_pct:     float | None = None
    yoy_change:  int | None   = None
    yoy_pct:     float | None = None
    signal: str = Field("NEUTRAL", description="DRAW | BUILD | NEUTRAL")
    history: list[DucHistoryPoint]       = Field(default_factory=list)
    basins:  dict[str, BasinDuc]         = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Crude imports sub-endpoint
# ---------------------------------------------------------------------------

class ImportOrigin(BaseModel):
    country:    str         = Field(...)
    volume_mbd: float       = Field(..., description="MBD")
    share_pct:  float       = Field(..., description="% of total imports")
    mom_change: float | None = None


class ImportHistoryPoint(BaseModel):
    date:  str   = Field(..., description="YYYY-MM-DD (first of month)")
    value: float = Field(..., description="Total US imports, MBD")


class CrudeImportsResponse(BaseModel):
    last_updated:      datetime              = Field(default_factory=utc_now)
    total_imports_mbd: float | None          = None
    top_origins:       list[ImportOrigin]    = Field(default_factory=list)
    history_total:     list[ImportHistoryPoint] = Field(default_factory=list)
