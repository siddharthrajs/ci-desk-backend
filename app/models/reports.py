"""Response models for the Reports dashboard tab (WPSR and CFTC COT)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.models.common import utc_now


# =============================================================================
# WPSR — EIA Weekly Petroleum Status Report
# =============================================================================


class WPSRRow(BaseModel):
    """One labelled row in a WPSR table. Numeric cells are None when blank."""

    label: str = Field(..., description="Row label as printed in the WPSR")
    current: float | None = Field(None, description="Current period value")
    prior_week: float | None = Field(None, description="Prior week value")
    difference: float | None = Field(None, description="Current minus prior week")
    percent_change: float | None = Field(None, description="Percent change vs prior week")
    year_ago: float | None = Field(None, description="Same period one year ago")


class WPSRTable(BaseModel):
    """A single WPSR table (1..9) with its parsed rows and content hash."""

    table_number: int = Field(..., ge=1, le=9, description="WPSR table number (1..9)")
    title: str = Field(..., description="Original title row from the source CSV")
    rows: list[WPSRRow] = Field(..., description="Parsed rows; section headers have null numerics")
    hash: str = Field(..., description="SHA-256 digest over the parsed rows")
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
# COT — CFTC Commitments of Traders (managed money)
# =============================================================================


class ManagedMoneyPosition(BaseModel):
    """Managed money futures positions for a single commodity from the disaggregated COT."""

    commodity: str = Field(..., description="Display name (e.g. 'WTI', 'Brent')")
    report_date: str = Field(..., description="ISO date of the most recent COT release")
    long: int = Field(..., description="Managed money long contracts")
    short: int = Field(..., description="Managed money short contracts")
    net_position: int = Field(..., description="Long minus short")
    wow_change: int | None = Field(None, description="Net position change vs the prior week")
    percentile_rank: float | None = Field(
        None, description="Where the current net sits in the 3-year history (0..100)"
    )


class COTResponse(BaseModel):
    """Managed money positions for WTI and Brent."""

    wti: ManagedMoneyPosition = Field(..., description="WTI light sweet crude positions")
    brent: ManagedMoneyPosition = Field(..., description="Brent crude positions")
    last_updated: datetime = Field(
        default_factory=utc_now, description="UTC timestamp when this response was assembled"
    )
