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
