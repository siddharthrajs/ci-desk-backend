"""Response models for the Upstream → US subtab.

Each model maps 1:1 to one endpoint in routers/upstream.py. Volumes are in
MBD (million barrels per day) for liquids and Bcf/d (billion cubic feet per
day) for natural gas, unless a field name says otherwise.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.models.common import utc_now


# ---------------------------------------------------------------------------
# /upstream/us/crude-production
# Weekly US + L48 + Net Imports + Monthly US (history for hero strip + chart)
# ---------------------------------------------------------------------------

class WeeklyPoint(BaseModel):
    date:  str   = Field(..., description="ISO date (YYYY-MM-DD)")
    value: float = Field(..., description="MBD")


class MonthlyRegionPoint(BaseModel):
    date:     str          = Field(..., description="YYYY-MM-DD (first of month)")
    us_total: float | None = None
    l48:      float | None = None


class CrudeProductionResponse(BaseModel):
    last_updated: datetime = Field(default_factory=utc_now)

    # Hero card 1: weekly US crude production
    weekly_us_mbd:      float | None = None
    weekly_us_wow:      float | None = None
    weekly_us_yoy:      float | None = None

    # Hero card 3: weekly L48 crude production
    weekly_l48_mbd:     float | None = None
    weekly_l48_wow:     float | None = None

    # Hero card 4: weekly net imports (crude)
    weekly_net_imports_mbd: float | None = None
    weekly_net_imports_wow: float | None = None

    # Primary chart: weekly history (5Y), monthly history (3Y)
    weekly_history:  list[WeeklyPoint]        = Field(default_factory=list)
    monthly_history: list[MonthlyRegionPoint] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# /upstream/us/rig-count  (EIA-republished Baker Hughes, monthly)
# ---------------------------------------------------------------------------

class RigSeriesPoint(BaseModel):
    date:     str = Field(..., description="YYYY-MM-DD (first of month)")
    total:    int | None = None
    oil:      int | None = None
    gas:      int | None = None
    onshore:  int | None = None
    offshore: int | None = None


class RigCountResponse(BaseModel):
    last_updated: datetime = Field(default_factory=utc_now)
    # Hero card 2 — latest values + change vs prior reporting period
    latest_total:    int | None = None
    latest_oil:      int | None = None
    latest_gas:      int | None = None
    mom_change:      int | None = None
    yoy_change:      int | None = None
    history: list[RigSeriesPoint] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# /upstream/us/production-by-region  (monthly, small-multiples)
# ---------------------------------------------------------------------------

class RegionLatest(BaseModel):
    current:    float | None = Field(None, description="Latest monthly value, MBD")
    mom_change: float | None = None
    yoy_change: float | None = None


class RegionHistoryPoint(BaseModel):
    date:            str = Field(..., description="YYYY-MM-DD")
    texas:           float | None = None
    north_dakota:    float | None = None
    new_mexico:      float | None = None
    padd2:           float | None = None
    padd3:           float | None = None
    gulf_of_america: float | None = None


class ProductionByRegionResponse(BaseModel):
    last_updated: datetime = Field(default_factory=utc_now)
    regions: dict[str, RegionLatest] = Field(default_factory=dict)
    history: list[RegionHistoryPoint] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# /upstream/us/api-gravity  (Lower-48 monthly, gravity-bucket share)
# ---------------------------------------------------------------------------

class GravityPoint(BaseModel):
    date:       str          = Field(..., description="YYYY-MM-DD")
    heavy:      float | None = Field(None, description="≤30 API, MBD")
    medium:     float | None = Field(None, description="30.1–40 API, MBD")
    light:      float | None = Field(None, description="40.1–50 API, MBD")
    condensate: float | None = Field(None, description="50.1+ API, MBD")


class ApiGravityResponse(BaseModel):
    last_updated: datetime = Field(default_factory=utc_now)
    # Latest snapshot — % share by bucket
    latest_heavy_pct:      float | None = None
    latest_medium_pct:     float | None = None
    latest_light_pct:      float | None = None
    latest_condensate_pct: float | None = None
    history: list[GravityPoint] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# /upstream/us/crude-imports  (weekly preliminary + monthly final)
# ---------------------------------------------------------------------------

class ImportCountry(BaseModel):
    country:    str          = Field(..., description="ISO-3 or descriptive country name")
    volume_mbd: float
    share_pct:  float
    mom_change: float | None = None
    is_opec_plus: bool = False


class ImportsHistoryPoint(BaseModel):
    date:  str   = Field(..., description="YYYY-MM-DD")
    value: float = Field(..., description="Total US crude imports, MBD")


class ImportsFeed(BaseModel):
    """A single feed (weekly preliminary or monthly final). Both feeds share this shape."""
    total_mbd:    float | None             = None
    top_origins:  list[ImportCountry]      = Field(default_factory=list)
    history:      list[ImportsHistoryPoint] = Field(default_factory=list)


class CrudeImportsResponse(BaseModel):
    last_updated: datetime = Field(default_factory=utc_now)
    weekly_preliminary: ImportsFeed = Field(default_factory=ImportsFeed)
    monthly_final:      ImportsFeed = Field(default_factory=ImportsFeed)


# ---------------------------------------------------------------------------
# /upstream/us/natural-gas  (monthly, gross withdrawals + dry + shale)
# ---------------------------------------------------------------------------

class NaturalGasPoint(BaseModel):
    date:               str          = Field(..., description="YYYY-MM-DD")
    gross_withdrawals:  float | None = Field(None, description="Bcf/d")
    dry_production:     float | None = Field(None, description="Bcf/d")


class NaturalGasResponse(BaseModel):
    last_updated: datetime = Field(default_factory=utc_now)
    latest_gross_withdrawals: float | None = None
    latest_dry_production:    float | None = None
    yoy_change_pct:           float | None = None
    history: list[NaturalGasPoint] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# /upstream/us/reserves  (annual proved reserves — footer tiles)
# ---------------------------------------------------------------------------

class ReservesPoint(BaseModel):
    year:  str
    value: float | None = None


class ReservesResponse(BaseModel):
    last_updated: datetime = Field(default_factory=utc_now)
    crude_latest_year:    str | None   = None
    crude_proved_bbbl:    float | None = Field(None, description="Billion barrels")
    ng_latest_year:       str | None   = None
    ng_proved_tcf:        float | None = Field(None, description="Trillion cubic feet")
    crude_history: list[ReservesPoint] = Field(default_factory=list)
    ng_history:    list[ReservesPoint] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# /upstream/opec/production  (monthly — hero KPIs, country table, sparklines)
# ---------------------------------------------------------------------------

class OpecHero(BaseModel):
    total_mbd:     float | None = Field(None, description="OPEC+ aggregate crude, MBD")
    total_mom:     float | None = None
    latest_period: str | None   = None
    saudi_mbd:     float | None = None
    saudi_mom:     float | None = None
    russia_mbd:    float | None = None
    russia_mom:    float | None = None
    iraq_mbd:      float | None = None
    iraq_mom:      float | None = None


class OpecMemberRow(BaseModel):
    iso3:        str
    country:     str
    latest_mbd:  float
    mom:         float | None = Field(None, description="MoM change, MBD")
    mom_pct:     float | None = None
    yoy:         float | None = Field(None, description="YoY change, MBD")
    yoy_pct:     float | None = None
    share_pct:   float | None = Field(None, description="% of OPEC+ total")


class OpecSparkPoint(BaseModel):
    period: str   = Field(..., description="YYYY-MM-DD (first of month)")
    value:  float = Field(..., description="MBD")


class OpecProductionResponse(BaseModel):
    last_updated: datetime = Field(default_factory=utc_now)
    hero:       OpecHero
    table:      list[OpecMemberRow]              = Field(default_factory=list)
    sparklines: dict[str, list[OpecSparkPoint]]  = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# /upstream/opec/history  (10Y stacked area — all members, monthly)
# ---------------------------------------------------------------------------

class OpecHistoryResponse(BaseModel):
    last_updated:      datetime = Field(default_factory=utc_now)
    members:           dict[str, list[OpecSparkPoint]] = Field(default_factory=dict)
    periods_available: int = 0


# ---------------------------------------------------------------------------
# /upstream/opec/overview  (EIA STEO, anchored to international — capacity,
# spare, structural split, world balance). All values mb/d. Crude basis.
# Histories include STEO's forward forecast (is_forecast); the actual/forecast
# boundary is international's latest OPEC month.
# ---------------------------------------------------------------------------

class OpecOverviewHero(BaseModel):
    last_actual_period:       str | None   = Field(None, description="Actual/forecast cutoff, YYYY-MM (from international)")
    spare_capacity_mbd:       float | None = Field(None, description="OPEC spare crude capacity")
    production_capacity_mbd:  float | None = Field(None, description="OPEC crude production capacity")
    capacity_utilization_pct: float | None = Field(None, description="OPEC production ÷ capacity")
    market_balance_mbd:       float | None = Field(None, description="Implied supply−demand (+surplus/−deficit)")
    market_balance_label:     str | None   = None


class OpecCapacityPoint(BaseModel):
    period:      str  = Field(..., description="YYYY-MM-01")
    is_forecast: bool = False
    production:  float | None = Field(None, description="OPEC crude production (STEO)")
    capacity:    float | None = Field(None, description="OPEC crude production capacity")
    spare:       float | None = Field(None, description="OPEC spare crude capacity")


class OpecSplitPoint(BaseModel):
    period:          str  = Field(..., description="YYYY-MM-01")
    is_forecast:     bool = False
    opec:            float | None = None
    opec_plus_other: float | None = None
    non_opec_plus:   float | None = None


class OpecBalancePoint(BaseModel):
    period:          str  = Field(..., description="YYYY-MM-01")
    is_forecast:     bool = False
    net_withdrawals: float | None = Field(None, description="World net inventory withdrawals (mb/d; <0 = build)")
    implied_balance: float | None = Field(None, description="Implied supply−demand = −net_withdrawals")


class OpecOverviewResponse(BaseModel):
    last_updated:       datetime = Field(default_factory=utc_now)
    last_actual_period: str | None = None
    hero:               OpecOverviewHero        = Field(default_factory=OpecOverviewHero)
    capacity_history:   list[OpecCapacityPoint] = Field(default_factory=list)
    split_history:      list[OpecSplitPoint]    = Field(default_factory=list)
    balance_history:    list[OpecBalancePoint]  = Field(default_factory=list)


# ---------------------------------------------------------------------------
# /upstream/opec/disruptions  (EIA STEO PADI_* — barrels offline, mb/d)
# ---------------------------------------------------------------------------

class OpecDisruptionCountry(BaseModel):
    code:       str
    name:       str
    latest_mbd: float
    mom:        float | None = None


class OpecDisruptionsResponse(BaseModel):
    last_updated:  datetime = Field(default_factory=utc_now)
    latest_period: str | None = None
    total_mbd:     float | None = None
    countries:     list[OpecDisruptionCountry]      = Field(default_factory=list)
    series:        dict[str, list[OpecSparkPoint]]  = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# /upstream/opec/compliance  (quota JSON × international actuals, mb/d crude)
# ---------------------------------------------------------------------------

class OpecComplianceRow(BaseModel):
    iso3:         str
    country:      str
    required_mbd: float
    actual_mbd:   float | None = None
    delta_mbd:    float | None = Field(None, description="actual − required; + = over-producing")
    status:       str | None   = Field(None, description="over | under | on")


class OpecComplianceResponse(BaseModel):
    last_updated:       datetime = Field(default_factory=utc_now)
    as_of:              str | None = Field(None, description="Quota effective month (YYYY-MM)")
    source:             str | None = None
    actual_period:      str | None = Field(None, description="Latest actual production month")
    total_required_mbd: float | None = None
    total_actual_mbd:   float | None = None
    total_delta_mbd:    float | None = None
    rows:               list[OpecComplianceRow] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# /upstream/opec/cross-check  (EIA international vs JODI — 5 reporting members)
# ---------------------------------------------------------------------------

class OpecCrossCheckPoint(BaseModel):
    period: str  = Field(..., description="YYYY-MM-01")
    eia:    float | None = None
    jodi:   float | None = None


class OpecCrossCheckResponse(BaseModel):
    last_updated:  datetime = Field(default_factory=utc_now)
    members:       list[str] = Field(default_factory=list, description="Members compared (both sources)")
    latest_period: str | None = None
    eia_latest:    float | None = None
    jodi_latest:   float | None = None
    diff_latest:   float | None = Field(None, description="EIA − JODI at latest common month")
    history:       list[OpecCrossCheckPoint] = Field(default_factory=list)
