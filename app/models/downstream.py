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
