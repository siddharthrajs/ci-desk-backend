"""Response models for the Midstream dashboard tab.

Sub-endpoint models (new):
  MidstreamStocksResponse  — /midstream/stocks
  CrudeExportsResponse     — /midstream/exports
  MidstreamImportsResponse — /midstream/imports
  PaddMovementsResponse    — /midstream/padd-movements

Legacy model (kept for the existing /midstream endpoint):
  MidstreamResponse
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.models.common import SeriesPoint, utc_now


# ─── legacy models (kept for /midstream backward compat) ────────────────────

class Inventories(BaseModel):
    crude:      list[SeriesPoint] = Field(..., description="U.S. commercial crude stocks (ex-SPR)")
    cushing:    list[SeriesPoint] = Field(..., description="Crude stocks at Cushing, OK")
    gasoline:   list[SeriesPoint] = Field(..., description="U.S. motor gasoline stocks")
    distillate: list[SeriesPoint] = Field(..., description="U.S. distillate fuel oil stocks")


class RefineryUtilizationHistory(BaseModel):
    national: list[SeriesPoint] = Field(...)
    padd1:    list[SeriesPoint] = Field(...)
    padd2:    list[SeriesPoint] = Field(...)
    padd3:    list[SeriesPoint] = Field(...)
    padd4:    list[SeriesPoint] = Field(...)
    padd5:    list[SeriesPoint] = Field(...)


class DaysOfSupply(BaseModel):
    gasoline:   float | None = None
    distillate: float | None = None


class MidstreamResponse(BaseModel):
    inventories:         Inventories
    spr:                 list[SeriesPoint]
    refinery_utilization: RefineryUtilizationHistory
    days_of_supply:      DaysOfSupply
    last_updated:        datetime = Field(default_factory=utc_now)


# ─── /midstream/stocks ───────────────────────────────────────────────────────

class StockHistPoint(BaseModel):
    period: str   = Field(..., description="ISO date (YYYY-MM-DD)")
    value:  float = Field(..., description="Thousand barrels (KBBL)")


class StockSeries(BaseModel):
    latest_kbbl: float | None = Field(None, description="Latest week stocks, KBBL")
    wow_kbbl:    float | None = Field(None, description="Week-over-week change, KBBL")
    history:     list[StockHistPoint] = Field(default_factory=list)


class MidstreamStocksResponse(BaseModel):
    last_updated:    datetime = Field(default_factory=utc_now)
    crude:           StockSeries = Field(default_factory=StockSeries)
    cushing:         StockSeries = Field(default_factory=StockSeries)
    gasoline:        StockSeries = Field(default_factory=StockSeries)
    distillate:      StockSeries = Field(default_factory=StockSeries)
    jet:             StockSeries = Field(default_factory=StockSeries)
    spr:             StockSeries = Field(default_factory=StockSeries)
    dos_gasoline:    float | None = Field(None, description="Gasoline days of supply")
    dos_distillate:  float | None = Field(None, description="Distillate days of supply")
    dos_jet:         float | None = Field(None, description="Jet fuel days of supply")


# ─── /midstream/exports ──────────────────────────────────────────────────────

class ExportsHistPoint(BaseModel):
    date:  str   = Field(..., description="YYYY-MM-DD")
    value: float = Field(..., description="MBD")


class CrudeExportsResponse(BaseModel):
    last_updated:     datetime = Field(default_factory=utc_now)
    # Weekly headline
    latest_mbd:       float | None = Field(None, description="Latest week, MBD")
    wow_mbd:          float | None = Field(None, description="WoW change, MBD")
    weekly_history:   list[ExportsHistPoint] = Field(default_factory=list)
    # Monthly PADD breakdown (latest month only)
    latest_period_m:  str | None = None
    padd1_mbbl:       float | None = Field(None, description="PADD 1 monthly exports, KBBL")
    padd2_mbbl:       float | None = None
    padd3_mbbl:       float | None = None
    padd4_mbbl:       float | None = None
    padd5_mbbl:       float | None = None
    monthly_history:  list[ExportsHistPoint] = Field(default_factory=list)


# ─── /midstream/imports ──────────────────────────────────────────────────────

class ImportOrigin(BaseModel):
    country:      str
    volume_mbd:   float
    share_pct:    float
    mom_change:   float | None = None
    is_opec_plus: bool = False


class ImportsHistPoint(BaseModel):
    date:  str
    value: float = Field(..., description="Total US crude imports, MBD")


class MidstreamImportsResponse(BaseModel):
    last_updated:      datetime = Field(default_factory=utc_now)
    total_mbd:         float | None = None
    top_origins:       list[ImportOrigin] = Field(default_factory=list)
    history:           list[ImportsHistPoint] = Field(default_factory=list)
    opec_plus_mbd:     float | None = None
    opec_plus_share:   float | None = Field(None, description="% of total imports from OPEC+")


# ─── /midstream/padd-movements ───────────────────────────────────────────────

class PaddFlowPoint(BaseModel):
    period: str   = Field(..., description="YYYY-MM-DD (first of month)")
    value:  float = Field(..., description="KBBL (thousand barrels, monthly total)")


class PaddMovementsResponse(BaseModel):
    last_updated:   datetime = Field(default_factory=utc_now)
    latest_period:  str | None = None
    # Key: "R20-R30" (dest-src), value: newest-first 36M history
    flows:          dict[str, list[PaddFlowPoint]] = Field(default_factory=dict)
    # Net receipts per PADD for latest month (positive = net recipient)
    net_receipts:   dict[str, float] = Field(default_factory=dict)
    # Human-readable labels per pair key
    flow_labels:    dict[str, str] = Field(default_factory=dict)
