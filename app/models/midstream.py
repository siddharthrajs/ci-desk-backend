"""Response models for the Midstream dashboard tab."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.models.common import SeriesPoint, utc_now


class Inventories(BaseModel):
    """Weekly EIA petroleum stocks, in thousand barrels."""

    crude: list[SeriesPoint] = Field(..., description="U.S. commercial crude stocks (ex-SPR)")
    cushing: list[SeriesPoint] = Field(..., description="Crude stocks at Cushing, OK")
    gasoline: list[SeriesPoint] = Field(..., description="U.S. motor gasoline stocks")
    distillate: list[SeriesPoint] = Field(..., description="U.S. distillate fuel oil stocks")


class RefineryUtilizationHistory(BaseModel):
    """Refinery capacity utilization (%), national + PADD 1-5."""

    national: list[SeriesPoint] = Field(..., description="U.S. national average utilization")
    padd1: list[SeriesPoint] = Field(..., description="PADD 1 — East Coast")
    padd2: list[SeriesPoint] = Field(..., description="PADD 2 — Midwest")
    padd3: list[SeriesPoint] = Field(..., description="PADD 3 — Gulf Coast (dominant hub)")
    padd4: list[SeriesPoint] = Field(..., description="PADD 4 — Rocky Mountain")
    padd5: list[SeriesPoint] = Field(..., description="PADD 5 — West Coast")


class DaysOfSupply(BaseModel):
    """Days-of-supply ratios for the latest reporting week.

    Computed as `stocks / 4-week-average product supplied` using the latest
    available observation in each series. Returns None when either side is
    missing.
    """

    gasoline: float | None = Field(None, description="Gasoline stocks ÷ gasoline demand")
    distillate: float | None = Field(None, description="Distillate stocks ÷ distillate demand")


class MidstreamResponse(BaseModel):
    """Midstream tab payload: storage, SPR, refinery throughput context."""

    inventories: Inventories = Field(..., description="Commercial petroleum inventories")
    spr: list[SeriesPoint] = Field(..., description="Strategic Petroleum Reserve level")
    refinery_utilization: RefineryUtilizationHistory = Field(
        ..., description="Refinery capacity utilization, national + PADD 1-5"
    )
    days_of_supply: DaysOfSupply = Field(..., description="Days-of-supply for the latest week")
    last_updated: datetime = Field(
        default_factory=utc_now, description="UTC timestamp when this payload was assembled"
    )
