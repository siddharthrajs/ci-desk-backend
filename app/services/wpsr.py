"""
EIA Weekly Petroleum Status Report (WPSR) — live table CSV service.

Source files: https://ir.eia.gov/wpsr/table1.csv … table9.csv
The EIA server issues 302 redirects to a versioned filename, so requests
must follow redirects. Tables are published every Wednesday at 10:30 AM ET.
The CSVs are encoded in Windows-1252 (Latin-1 superset) and served without
a charset header — we decode them explicitly.

Every WPSR table has a different shape: different label-column counts,
different numeric-column counts, and table 1 actually contains two stacked
sub-tables (Stocks + Supply/Disposition). A per-table schema config drives
the parser; each parsed table is returned as a list of sections so single-
and double-section tables share the same response shape.
"""

from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx

from app.core.cache import get_cache

logger = logging.getLogger(__name__)

# =============================================================================
# Constants
# =============================================================================

_BASE_URL = "https://ir.eia.gov/wpsr"
_USER_AGENT = "CI-Desk/1.0"
_SOURCE_ENCODING = "cp1252"  # EIA CSVs are Windows-1252

TABLE_NUMBERS: tuple[int, ...] = tuple(range(1, 10))

_WPSR_TTL_SECONDS = 3600

# Cache key prefix bumped to v2 because the payload shape changed.
_CACHE_PREFIX = "wpsr:v2"

# Strings the EIA uses to indicate "not meaningful / withheld / NA".
_NA_TOKENS = frozenset({"", "NA", "W", "-", "--", "–", "—", "– –", "— —"})

# Matches header cells like "5/15/26" or "5/15/2026" (the date columns).
_DATE_RE = re.compile(r"^\s*(\d{1,2})/(\d{1,2})/(\d{2,4})\s*$")


# =============================================================================
# Schema definitions
# =============================================================================

@dataclass(frozen=True)
class WPSRColumn:
    """One numeric column in a parsed section."""
    field: str   # JSON field name
    header: str  # human-readable display label


@dataclass(frozen=True)
class WPSRSectionSchema:
    """Shape of one logical section within a WPSR table CSV."""
    name: str                                  # internal identifier
    title: str                                 # human-readable title
    label_columns: tuple[str, ...]             # ("label",) or ("group", "label")
    numeric_columns: tuple[WPSRColumn, ...]    # ordered numeric fields


# Numeric column presets — reused across tables that share a layout.
_COL_CURRENT       = WPSRColumn("current",                 "Current Week")
_COL_PRIOR_WEEK    = WPSRColumn("prior_week",              "Prior Week")
_COL_DIFF_WOW      = WPSRColumn("diff_wow",                "Diff (WoW)")
_COL_PCT_WOW       = WPSRColumn("pct_wow",                 "% (WoW)")
_COL_YEAR_AGO      = WPSRColumn("year_ago",                "Year Ago")
_COL_DIFF_YOY      = WPSRColumn("diff_yoy",                "Diff (YoY)")
_COL_PCT_YOY       = WPSRColumn("pct_yoy",                 "% (YoY)")
_COL_TWO_YR_AGO    = WPSRColumn("two_years_ago",           "Two Years Ago")
_COL_PCT_TWO_YR    = WPSRColumn("pct_two_year",            "% (2-Yr)")
_COL_4WK           = WPSRColumn("four_week_avg",           "4-Wk Avg")
_COL_4WK_YA        = WPSRColumn("four_week_avg_year_ago", "4-Wk Avg Year Ago")
_COL_PCT_4WK       = WPSRColumn("pct_four_week",           "% (4-Wk)")
_COL_YTD           = WPSRColumn("ytd_avg",                 "YTD Avg")
_COL_YTD_YA        = WPSRColumn("ytd_avg_year_ago",        "YTD Avg Year Ago")
_COL_PCT_YTD       = WPSRColumn("pct_ytd",                 "% (YTD)")
_COL_SHARE_2025    = WPSRColumn("share_2025_pct",          "2025 Share %")


_TABLE1_SECTION_A = WPSRSectionSchema(
    name="stocks",
    title="Stocks (Million Barrels)",
    label_columns=("label",),
    numeric_columns=(
        _COL_CURRENT, _COL_PRIOR_WEEK, _COL_DIFF_WOW, _COL_PCT_WOW,
        _COL_YEAR_AGO, _COL_DIFF_YOY, _COL_PCT_YOY,
    ),
)

_TABLE1_SECTION_B = WPSRSectionSchema(
    name="supply_disposition",
    title="Supply and Disposition (Thousand Barrels per Day)",
    label_columns=("group", "label"),
    numeric_columns=(
        _COL_CURRENT, _COL_PRIOR_WEEK, _COL_DIFF_WOW,
        _COL_YEAR_AGO, _COL_DIFF_YOY,
        _COL_4WK, _COL_4WK_YA, _COL_PCT_4WK,
        _COL_YTD, _COL_YTD_YA, _COL_PCT_YTD,
    ),
)

# Tables 2 and 3 share an identical column layout.
_REFINER_PRODUCTION_COLUMNS = (
    _COL_CURRENT, _COL_PRIOR_WEEK, _COL_DIFF_WOW,
    _COL_YEAR_AGO, _COL_PCT_YOY,
    _COL_TWO_YR_AGO, _COL_PCT_TWO_YR,
    _COL_4WK, _COL_4WK_YA, _COL_PCT_4WK,
)

# Tables 4, 5, 6 share a 7-column "stocks with 2-year comparison" layout.
_STOCKS_COLUMNS = (
    _COL_CURRENT, _COL_PRIOR_WEEK, _COL_DIFF_WOW,
    _COL_YEAR_AGO, _COL_PCT_YOY,
    _COL_TWO_YR_AGO, _COL_PCT_TWO_YR,
)


@dataclass(frozen=True)
class WPSRTableSchema:
    """A whole WPSR table — title plus one or two ordered sections."""
    title: str
    sections: tuple[WPSRSectionSchema, ...]


WPSR_SCHEMAS: dict[int, WPSRTableSchema] = {
    1: WPSRTableSchema(
        title="U.S. Petroleum Balance Sheet",
        sections=(_TABLE1_SECTION_A, _TABLE1_SECTION_B),
    ),
    2: WPSRTableSchema(
        title="Refiner Inputs, Utilization, and Net Production",
        sections=(
            WPSRSectionSchema(
                name="refiner_inputs_and_production",
                title="Refiner Inputs, Utilization, and Net Production",
                label_columns=("group", "label"),
                numeric_columns=_REFINER_PRODUCTION_COLUMNS,
            ),
        ),
    ),
    3: WPSRTableSchema(
        title="Refiner Net Production and Blender Net Production",
        sections=(
            WPSRSectionSchema(
                name="refiner_and_blender_net_production",
                title="Refiner and Blender Net Production",
                label_columns=("group", "label"),
                numeric_columns=_REFINER_PRODUCTION_COLUMNS,
            ),
        ),
    ),
    4: WPSRTableSchema(
        title="Crude Oil and Petroleum Product Stocks",
        sections=(
            WPSRSectionSchema(
                name="crude_and_product_stocks",
                title="Crude Oil and Petroleum Product Stocks (Million Barrels)",
                label_columns=("label",),
                numeric_columns=_STOCKS_COLUMNS,
            ),
        ),
    ),
    5: WPSRTableSchema(
        title="Stocks of Motor Gasoline and Fuel Ethanol",
        sections=(
            WPSRSectionSchema(
                name="gasoline_and_ethanol_stocks",
                title="Stocks of Motor Gasoline and Fuel Ethanol (Million Barrels)",
                label_columns=("group", "label"),
                numeric_columns=_STOCKS_COLUMNS,
            ),
        ),
    ),
    6: WPSRTableSchema(
        title="Stocks of Distillate, Jet Fuel, Residual Fuel Oil, and Propane",
        sections=(
            WPSRSectionSchema(
                name="distillate_jet_resid_propane_stocks",
                title="Stocks of Distillate, Jet Fuel, Residual Fuel Oil, and Propane (Million Barrels)",
                label_columns=("label",),
                numeric_columns=_STOCKS_COLUMNS,
            ),
        ),
    ),
    7: WPSRTableSchema(
        title="Imports and Exports of Crude Oil and Petroleum Products",
        sections=(
            WPSRSectionSchema(
                name="imports_exports",
                title="Imports and Exports of Crude Oil and Petroleum Products (Thousand Barrels per Day)",
                label_columns=("label",),
                numeric_columns=(
                    _COL_CURRENT, _COL_PRIOR_WEEK, _COL_DIFF_WOW,
                    _COL_YEAR_AGO, _COL_PCT_YOY,
                    _COL_TWO_YR_AGO, _COL_PCT_TWO_YR,
                    _COL_4WK, _COL_4WK_YA, _COL_PCT_4WK,
                ),
            ),
        ),
    ),
    8: WPSRTableSchema(
        title="Preliminary Crude Imports by Country of Origin",
        sections=(
            WPSRSectionSchema(
                name="crude_imports_by_country",
                title="Preliminary Crude Imports by Country of Origin (Thousand Barrels per Day)",
                label_columns=("group", "label"),
                numeric_columns=(
                    _COL_SHARE_2025,
                    _COL_CURRENT, _COL_PRIOR_WEEK, _COL_DIFF_WOW,
                    _COL_YEAR_AGO, _COL_PCT_YOY,
                    _COL_TWO_YR_AGO, _COL_PCT_TWO_YR,
                    _COL_4WK, _COL_4WK_YA, _COL_PCT_4WK,
                ),
            ),
        ),
    ),
    9: WPSRTableSchema(
        title="Weekly History of Production, Inputs, and Blending",
        sections=(
            WPSRSectionSchema(
                name="weekly_history",
                title="Weekly History (Thousand Barrels per Day)",
                label_columns=("group", "label"),
                numeric_columns=(
                    _COL_CURRENT, _COL_PRIOR_WEEK,
                    _COL_YEAR_AGO, _COL_TWO_YR_AGO,
                    _COL_4WK, _COL_4WK_YA,
                ),
            ),
        ),
    ),
}


# =============================================================================
# Pure parsing helpers
# =============================================================================

def _parse_number(raw: str) -> float | None:
    """Convert an EIA CSV cell to float, returning None for blanks / NA markers."""
    cleaned = raw.strip().replace(",", "")
    if cleaned in _NA_TOKENS:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _parse_header_date(cell: str) -> str | None:
    """Parse '5/15/26' or '5/15/2026' as ISO date 'YYYY-MM-DD'."""
    match = _DATE_RE.match(cell)
    if not match:
        return None
    month, day, year = (int(g) for g in match.groups())
    if year < 100:
        year += 2000
    try:
        return f"{year:04d}-{month:02d}-{day:02d}"
    except ValueError:
        return None


def _extract_period_dates(header_row: list[str]) -> dict[str, str]:
    """Pull unique dates from a header row, labelled by chronological position.

    EIA headers always list dates newest → oldest, so position 0 = current
    week, 1 = prior week, 2 = year ago, 3 = two years ago.
    """
    seen: list[str] = []
    for cell in header_row:
        iso = _parse_header_date(cell)
        if iso and iso not in seen:
            seen.append(iso)
    keys = ("current", "prior_week", "year_ago", "two_years_ago")
    return {key: date for key, date in zip(keys, seen)}


def _parse_section_rows(
    data_rows: list[list[str]],
    schema: WPSRSectionSchema,
) -> list[dict[str, Any]]:
    """Apply a section schema to its data rows."""
    n_labels = len(schema.label_columns)
    n_numerics = len(schema.numeric_columns)
    parsed: list[dict[str, Any]] = []

    for raw in data_rows:
        cells = list(raw)
        # If a stray header sneaks into data rows, skip it.
        if cells and cells[0].strip().startswith("STUB_"):
            continue

        # Pad short rows so zip(...) lands all columns.
        padded = cells + [""] * max(0, n_labels + n_numerics - len(cells))
        label_cells = [c.strip() for c in padded[:n_labels]]
        if not any(label_cells):
            continue

        row: dict[str, Any] = {}
        for col_name, value in zip(schema.label_columns, label_cells):
            row[col_name] = value
        for col_spec, raw_val in zip(
            schema.numeric_columns, padded[n_labels : n_labels + n_numerics]
        ):
            row[col_spec.field] = _parse_number(raw_val)
        parsed.append(row)

    return parsed


def _split_table1(rows: list[list[str]]) -> tuple[list[list[str]], list[list[str]]]:
    """Cut table 1 into its two sub-tables at the second STUB_1 header row."""
    second_idx: int | None = None
    for i, row in enumerate(rows[1:], start=1):
        if row and row[0].strip() == "STUB_1":
            second_idx = i
            break
    if second_idx is None:
        # Defensive: no second sub-table found, treat the whole file as section A.
        return rows, []
    return rows[:second_idx], rows[second_idx:]


def _section_payload(
    schema: WPSRSectionSchema,
    header_row: list[str],
    data_rows: list[list[str]],
) -> dict[str, Any]:
    return {
        "name":            schema.name,
        "title":           schema.title,
        "label_columns":   list(schema.label_columns),
        "numeric_columns": [c.field for c in schema.numeric_columns],
        "column_headers":  [c.header for c in schema.numeric_columns],
        "period_dates":    _extract_period_dates(header_row),
        "rows":            _parse_section_rows(data_rows, schema),
    }


def parse_wpsr_csv(text: str, table_number: int) -> dict[str, Any]:
    """Parse a WPSR CSV into ``{title, sections}``.

    ``sections`` is always a list. Table 1 has two entries; all other tables
    have one. The shape is identical across tables so the frontend can render
    every section with the same component.
    """
    if table_number not in WPSR_SCHEMAS:
        raise ValueError(f"No schema defined for WPSR table {table_number!r}")
    schema = WPSR_SCHEMAS[table_number]

    reader = csv.reader(io.StringIO(text))
    rows = [r for r in reader if any(cell.strip() for cell in r)]
    if not rows:
        return {
            "title": schema.title,
            "sections": [],
        }

    sections_payload: list[dict[str, Any]] = []
    if table_number == 1:
        section_a_rows, section_b_rows = _split_table1(rows)
        if section_a_rows:
            sections_payload.append(_section_payload(
                schema.sections[0], section_a_rows[0], section_a_rows[1:],
            ))
        if section_b_rows:
            sections_payload.append(_section_payload(
                schema.sections[1], section_b_rows[0], section_b_rows[1:],
            ))
    else:
        sections_payload.append(_section_payload(
            schema.sections[0], rows[0], rows[1:],
        ))

    return {
        "title": schema.title,
        "sections": sections_payload,
    }


def content_hash(data: Any) -> str:
    """SHA-256 hex digest over a canonical JSON serialisation of ``data``."""
    encoded = json.dumps(data, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


# =============================================================================
# Service class
# =============================================================================

class WPSRService:
    """EIA WPSR table client (public, no API key required)."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def _fetch_table_csv(self, table_number: int) -> str:
        """Download one WPSR table CSV and decode it from Windows-1252."""
        url = f"{_BASE_URL}/table{table_number}.csv"
        response = await self._client.get(
            url,
            follow_redirects=True,
            headers={"User-Agent": _USER_AGENT},
        )
        response.raise_for_status()
        return response.content.decode(_SOURCE_ENCODING, errors="replace")

    async def get_wpsr_table(self, table_number: int) -> dict[str, Any]:
        """Fetch and parse one WPSR table, returning its sections payload."""
        if table_number not in TABLE_NUMBERS:
            raise ValueError(f"WPSR table_number must be in 1..9, got {table_number!r}")

        async def fetch() -> dict[str, Any]:
            text = await self._fetch_table_csv(table_number)
            parsed = parse_wpsr_csv(text, table_number)
            return {
                "table_number": table_number,
                "title":        parsed["title"],
                "sections":     parsed["sections"],
                "hash":         content_hash(parsed["sections"]),
                "last_fetched": _utc_now_iso(),
            }

        key = f"{_CACHE_PREFIX}:table:{table_number}"
        return await get_cache().cache_or_fetch(key, fetch, ttl=_WPSR_TTL_SECONDS)

    async def get_all_wpsr_tables(self) -> dict[str, Any]:
        """Fetch all 9 WPSR tables in parallel, keyed by stringified number."""
        async def fetch() -> dict[str, Any]:
            results = await asyncio.gather(
                *(self.get_wpsr_table(n) for n in TABLE_NUMBERS)
            )
            tables = {str(t["table_number"]): t for t in results}
            combined_hash = content_hash([tables[str(n)]["hash"] for n in TABLE_NUMBERS])
            return {
                "tables":       tables,
                "hash":         combined_hash,
                "last_fetched": _utc_now_iso(),
            }

        return await get_cache().cache_or_fetch(
            f"{_CACHE_PREFIX}:all", fetch, ttl=_WPSR_TTL_SECONDS,
        )
