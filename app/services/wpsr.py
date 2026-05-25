"""
EIA Weekly Petroleum Status Report (WPSR) — live table CSV service.

Source files: https://ir.eia.gov/wpsr/table1.csv … table9.csv
The EIA server issues 302 redirects to a versioned filename, so requests
must follow redirects. Tables are published every Wednesday at 10:30 AM ET.

Each parsed row is shaped as:
    {label, current, prior_week, difference, percent_change, year_ago}

Section-header rows (e.g. "Crude Oil Supply") appear in the CSV with empty
numeric columns; they are preserved as rows with all numeric fields == None
so the frontend can render the original document structure.

A SHA-256 content hash is computed over the parsed rows of each table.
Cached payloads embed this hash and a ``last_fetched`` ISO-8601 UTC timestamp
so the frontend can detect new releases without diffing the full payload.
"""

from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import json
import logging
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

# EIA publishes tables 1..9; the frontend addresses tables by this integer.
TABLE_NUMBERS: tuple[int, ...] = tuple(range(1, 10))

# WPSR is released weekly; refresh within the same release cycle is cheap, so
# cache for 1 hour to balance freshness against repeated origin calls.
_WPSR_TTL_SECONDS = 3600

# Column count for a data row after the leading label cell.
_NUMERIC_COLUMNS = ("current", "prior_week", "difference", "percent_change", "year_ago")


# =============================================================================
# Pure parsing helpers — no I/O, easy to unit-test
# =============================================================================

def _parse_number(raw: str) -> float | None:
    """Convert an EIA CSV cell to float, returning None for blanks or non-numerics.

    EIA formats large values with thousands separators ("12,400") and uses an
    empty cell — sometimes "NA" or "W" (withheld) — for missing observations.
    """
    cleaned = raw.strip().replace(",", "")
    if not cleaned or cleaned in {"NA", "W", "-", "--"}:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def parse_wpsr_csv(text: str) -> dict[str, Any]:
    """Parse a WPSR table CSV into ``{title, rows}``.

    The first non-empty row is treated as the table title (often includes a
    "Week Ending MM/DD/YYYY" date); the second non-empty row is the column
    header and is discarded. Every subsequent non-empty row becomes one entry
    in ``rows`` with the canonical row shape:

        {label, current, prior_week, difference, percent_change, year_ago}

    Section-header rows whose numeric cells are blank are preserved with all
    numeric values set to None.
    """
    reader = csv.reader(io.StringIO(text))
    non_empty = [r for r in reader if any(cell.strip() for cell in r)]

    if not non_empty:
        return {"title": "", "rows": []}

    title = non_empty[0][0].strip() if non_empty[0] else ""
    data_rows = non_empty[2:]  # skip title row and the column-header row

    rows: list[dict[str, Any]] = []
    for raw_row in data_rows:
        label = raw_row[0].strip() if raw_row else ""
        if not label:
            continue
        numerics = list(raw_row[1:6]) + [""] * max(0, 5 - (len(raw_row) - 1))
        row: dict[str, Any] = {"label": label}
        for col_name, raw_cell in zip(_NUMERIC_COLUMNS, numerics):
            row[col_name] = _parse_number(raw_cell)
        rows.append(row)

    return {"title": title, "rows": rows}


def content_hash(data: Any) -> str:
    """SHA-256 hex digest over a canonical JSON serialisation of ``data``.

    Canonicalisation (``sort_keys=True``, no whitespace) means two payloads
    with identical content but different key order hash identically — so the
    frontend's "new data?" check is stable across server restarts.
    """
    encoded = json.dumps(data, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _utc_now_iso() -> str:
    """Current UTC time as a second-precision ISO-8601 string (e.g. 2026-05-25T18:30:00Z)."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


# =============================================================================
# Service class
# =============================================================================

class WPSRService:
    """EIA WPSR table client (public, no API key required)."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def _fetch_table_csv(self, table_number: int) -> str:
        """Download a single tableN.csv file. EIA redirects 302 → versioned URL."""
        url = f"{_BASE_URL}/table{table_number}.csv"
        response = await self._client.get(
            url,
            follow_redirects=True,
            headers={"User-Agent": _USER_AGENT},
        )
        response.raise_for_status()
        return response.text

    async def get_wpsr_table(self, table_number: int) -> dict[str, Any]:
        """Return one parsed WPSR table wrapped with hash and fetch timestamp.

        Args:
            table_number: 1..9 (EIA publishes nine tables in each WPSR release).

        Returns:
            {
                table_number:  int,
                title:         str — raw title row from the CSV,
                rows:          [ {label, current, prior_week, difference,
                                  percent_change, year_ago}, ... ],
                hash:          SHA-256 hex digest of the rows,
                last_fetched:  ISO-8601 UTC timestamp,
            }

        Raises:
            ValueError: if ``table_number`` is outside 1..9.
        """
        if table_number not in TABLE_NUMBERS:
            raise ValueError(f"WPSR table_number must be in 1..9, got {table_number!r}")

        async def fetch() -> dict[str, Any]:
            text = await self._fetch_table_csv(table_number)
            parsed = parse_wpsr_csv(text)
            return {
                "table_number": table_number,
                "title":        parsed["title"],
                "rows":         parsed["rows"],
                "hash":         content_hash(parsed["rows"]),
                "last_fetched": _utc_now_iso(),
            }

        key = f"wpsr:table:{table_number}"
        return await get_cache().cache_or_fetch(key, fetch, ttl=_WPSR_TTL_SECONDS)

    async def get_all_wpsr_tables(self) -> dict[str, Any]:
        """Fetch all 9 WPSR tables in parallel; return them keyed by number.

        Returns:
            {
                tables:        { "1": <table-payload>, ..., "9": <table-payload> },
                hash:          SHA-256 digest over the combined per-table hashes,
                last_fetched:  ISO-8601 UTC timestamp,
            }

        The combined ``hash`` lets the frontend detect any change across the
        whole release with a single comparison.
        """
        async def fetch() -> dict[str, Any]:
            results = await asyncio.gather(
                *(self.get_wpsr_table(n) for n in TABLE_NUMBERS)
            )
            tables = {str(table["table_number"]): table for table in results}
            combined_hash = content_hash([tables[str(n)]["hash"] for n in TABLE_NUMBERS])
            return {
                "tables":       tables,
                "hash":         combined_hash,
                "last_fetched": _utc_now_iso(),
            }

        return await get_cache().cache_or_fetch("wpsr:all", fetch, ttl=_WPSR_TTL_SECONDS)
