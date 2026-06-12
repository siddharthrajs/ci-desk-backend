"""Response models for the Reports dashboard tab (WPSR and CFTC COT)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.models.common import utc_now


# =============================================================================
# WPSR — EIA Weekly Petroleum Status Report
# =============================================================================


class WPSRSection(BaseModel):
    """One logical section inside a WPSR table.

    Most tables have a single section; table 1 has two ("stocks" and
    "supply_disposition"). Each row carries the label columns named in
    ``label_columns`` plus every numeric field named in ``numeric_columns``.
    """

    name: str = Field(..., description="Internal section identifier (e.g. 'stocks')")
    title: str = Field(..., description="Human-readable section title")
    label_columns: list[str] = Field(
        ...,
        description="Per-row label fields, in render order. One of ('label',) or ('group', 'label').",
    )
    numeric_columns: list[str] = Field(
        ..., description="Per-row numeric field names, in render order"
    )
    column_headers: list[str] = Field(
        ..., description="Display labels paired one-to-one with numeric_columns"
    )
    period_dates: dict[str, str] = Field(
        ...,
        description=(
            "ISO dates pulled from the CSV header, keyed by role: "
            "'current', 'prior_week', 'year_ago', 'two_years_ago' (where present)."
        ),
    )
    rows: list[dict[str, float | str | None]] = Field(
        ...,
        description=(
            "Parsed rows. Each row contains every key listed in label_columns "
            "(string values) and every key listed in numeric_columns (float | None)."
        ),
    )


class WPSRTable(BaseModel):
    """A single WPSR table (1..9) with its sections and content hash."""

    table_number: int = Field(..., ge=1, le=9, description="WPSR table number (1..9)")
    title: str = Field(..., description="Human-readable table title")
    sections: list[WPSRSection] = Field(
        ...,
        description="One section per logical sub-table. Table 1 has two; the rest have one.",
    )
    hash: str = Field(..., description="SHA-256 digest over the sections payload")
    last_fetched: str = Field(..., description="ISO-8601 UTC timestamp of the fetch")


class WPSRResponse(BaseModel):
    """All nine WPSR tables plus a combined content hash and timestamps."""

    tables: dict[str, WPSRTable] = Field(
        ..., description="Tables keyed by stringified table number ('1'..'9')"
    )
    hash: str = Field(..., description="SHA-256 digest over the combined per-table hashes")
    last_fetched: str = Field(..., description="ISO-8601 UTC timestamp of the fetch")
    last_updated: datetime = Field(
        default_factory=utc_now, description="UTC timestamp when this response was assembled"
    )


# =============================================================================
# COT — CFTC Commitments of Traders (disaggregated, all petroleum contracts)
# =============================================================================


class COTPositionGroup(BaseModel):
    long: int
    short: int
    spreading: int | None = None


class COTChangeGroup(BaseModel):
    long: int
    short: int
    spreading: int | None = None


class COTPctGroup(BaseModel):
    long: float
    short: float
    spreading: float | None = None


class COTTraderGroup(BaseModel):
    long: int | None = None
    short: int | None = None
    spreading: int | None = None


class COTConcentration(BaseModel):
    gross_le4_long: float
    gross_le4_short: float
    gross_le8_long: float
    gross_le8_short: float
    net_le4_long: float
    net_le4_short: float
    net_le8_long: float
    net_le8_short: float


class COTContract(BaseModel):
    """Full disaggregated COT data for one petroleum futures contract."""

    contract_market_code: str
    contract_market_name: str
    market_and_exchange_names: str
    exchange: str
    report_date: str
    contract_units: str
    open_interest: int

    # Positions by trader category
    producer_merchant: COTPositionGroup
    swap_dealers: COTPositionGroup
    managed_money: COTPositionGroup
    other_reportables: COTPositionGroup
    non_reportable: COTPositionGroup

    # Week-over-week changes (pre-computed by CFTC, available directly in source data)
    open_interest_change: int
    producer_merchant_change: COTChangeGroup
    swap_dealers_change: COTChangeGroup
    managed_money_change: COTChangeGroup
    other_reportables_change: COTChangeGroup
    non_reportable_change: COTChangeGroup

    # Percent of open interest
    producer_merchant_pct: COTPctGroup
    swap_dealers_pct: COTPctGroup
    managed_money_pct: COTPctGroup
    other_reportables_pct: COTPctGroup
    non_reportable_pct: COTPctGroup

    # Number of traders
    producer_merchant_traders: COTTraderGroup
    swap_dealers_traders: COTTraderGroup
    managed_money_traders: COTTraderGroup
    other_reportables_traders: COTTraderGroup

    # Concentration — percent of OI held by top 4 / top 8 traders
    concentration: COTConcentration

    # Derived
    mm_net: int
    mm_wow_net_change: int | None = None
    mm_percentile_rank: float | None = None


class COTResponse(BaseModel):
    """All petroleum COT contracts from the CFTC disaggregated futures-only report."""

    contracts: list[COTContract]
    report_date: str = Field(..., description="ISO date of the most recent report (as-of Tuesday)")
    last_updated: datetime = Field(
        default_factory=utc_now, description="UTC timestamp when this response was assembled"
    )
