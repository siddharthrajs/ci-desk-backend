"""Response models for the Downstream dashboard tab."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.models.common import SeriesPoint, utc_now
from app.models.midstream import RefineryUtilizationHistory


class CrackSpreads(BaseModel):
    """Refining-margin proxies derived from EIA daily spot prices ($/barrel)."""

    three_two_one: list[SeriesPoint] = Field(
        ..., description="(2×RBOB + 1×HO − 3×WTI) / 3 in $/bbl"
    )
    rbob_crack: list[SeriesPoint] = Field(..., description="RBOB − WTI in $/bbl")
    ho_crack: list[SeriesPoint] = Field(..., description="Heating oil − WTI in $/bbl")


class ProductDemand(BaseModel):
    """EIA 4-week-average product supplied (kbpd) — demand proxy."""

    gasoline: list[SeriesPoint] = Field(..., description="Motor gasoline product supplied")
    distillate: list[SeriesPoint] = Field(..., description="Distillate fuel oil product supplied")
    jet: list[SeriesPoint] = Field(..., description="Kerosene-type jet fuel product supplied")


class DownstreamResponse(BaseModel):
    """Downstream tab payload: refining margins, product demand, utilization."""

    crack_spreads: CrackSpreads = Field(..., description="3-2-1, RBOB, and HO crack spreads")
    product_demand: ProductDemand = Field(
        ..., description="Demand proxy via EIA product supplied 4-week avg"
    )
    refinery_util_history: RefineryUtilizationHistory = Field(
        ..., description="Historical refinery utilization, national + PADD 1-5"
    )
    last_updated: datetime = Field(
        default_factory=utc_now, description="UTC timestamp when this payload was assembled"
    )


# ---------------------------------------------------------------------------
# V2 models — used by the new sub-endpoints
# ---------------------------------------------------------------------------

class CrackHistoryPoint(BaseModel):
    date: str
    crack_321: float | None = None
    crack_rbob: float | None = None
    crack_ho: float | None = None
    brent_wti: float | None = None
    wti: float | None = None


class CrackZScores(BaseModel):
    crack_321: float | None = None
    crack_rbob: float | None = None
    crack_ho: float | None = None
    brent_wti: float | None = None


class CrackSignals(BaseModel):
    crack_321: str = "NEUTRAL"
    crack_rbob: str = "NEUTRAL"
    crack_ho: str = "NEUTRAL"
    brent_wti: str = "NEUTRAL"


class CrackWowChanges(BaseModel):
    crack_321: float | None = None
    crack_rbob: float | None = None
    crack_ho: float | None = None
    brent_wti: float | None = None


class CrackSpreadsV2(BaseModel):
    last_updated: datetime = Field(default_factory=utc_now)
    wti: float | None = None
    brent: float | None = None
    rbob_gal: float | None = None
    ho_gal: float | None = None
    crack_321: float | None = None
    crack_rbob: float | None = None
    crack_ho: float | None = None
    brent_wti: float | None = None
    z_scores: CrackZScores = Field(default_factory=CrackZScores)
    signals: CrackSignals = Field(default_factory=CrackSignals)
    wow_changes: CrackWowChanges = Field(default_factory=CrackWowChanges)
    history_90d: list[CrackHistoryPoint] = Field(default_factory=list)


class RefineryHistoryPoint(BaseModel):
    date: str
    national: float | None = None
    padd3: float | None = None


class RefineryUtilizationV2(BaseModel):
    last_updated: datetime = Field(default_factory=utc_now)
    national_current: float | None = None
    padd3_current: float | None = None
    history: list[RefineryHistoryPoint] = Field(default_factory=list)


class ProductHistoryPoint(BaseModel):
    date: str
    value: float


class ProductDemandSeries(BaseModel):
    current_4wk_avg: float | None = None
    yoy_pct: float | None = None
    history: list[ProductHistoryPoint] = Field(default_factory=list)


class ProductDemandV2(BaseModel):
    last_updated: datetime = Field(default_factory=utc_now)
    gasoline: ProductDemandSeries = Field(default_factory=ProductDemandSeries)
    distillate: ProductDemandSeries = Field(default_factory=ProductDemandSeries)
    jet: ProductDemandSeries = Field(default_factory=ProductDemandSeries)
    total: ProductDemandSeries = Field(default_factory=ProductDemandSeries)
