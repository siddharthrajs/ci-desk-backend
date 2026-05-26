"""Tests for WPSRService.

Real CSVs captured from ir.eia.gov (May 2026 release) live under
tests/fixtures/wpsr/ — we parse those instead of synthetic samples so the
schemas stay honest against the actual EIA output.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.services.wpsr import (
    TABLE_NUMBERS,
    WPSR_SCHEMAS,
    WPSRService,
    _extract_period_dates,
    _parse_header_date,
    _parse_number,
    content_hash,
    parse_wpsr_csv,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "wpsr"


def _load_csv(table_number: int) -> str:
    """Read a captured WPSR CSV as cp1252 (matches the service decoder)."""
    return (_FIXTURE_DIR / f"table{table_number}.csv").read_bytes().decode("cp1252")


_DUMMY_REQUEST = httpx.Request("GET", "https://ir.eia.gov/wpsr/table1.csv")


def _csv_response(table_number: int, status: int = 200) -> httpx.Response:
    """Build an httpx.Response whose .content is the raw cp1252 bytes."""
    raw = (_FIXTURE_DIR / f"table{table_number}.csv").read_bytes()
    return httpx.Response(status, content=raw, request=_DUMMY_REQUEST)


def _empty_response(status: int = 200) -> httpx.Response:
    return httpx.Response(status, content=b"", request=_DUMMY_REQUEST)


def _make_service() -> tuple[WPSRService, AsyncMock]:
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    return WPSRService(client=mock_client), mock_client.get


@pytest.fixture
def no_cache():
    """Bypass Redis: cache_or_fetch invokes the fetch fn directly each call."""
    async def passthrough(key: str, fn: Any, **kw: Any) -> Any:
        return await fn()

    mock_cache = MagicMock()
    mock_cache.cache_or_fetch = AsyncMock(side_effect=passthrough)
    with patch("app.services.wpsr.get_cache", return_value=mock_cache):
        yield mock_cache


# =============================================================================
# Pure helpers
# =============================================================================

class TestParseNumber:
    @pytest.mark.parametrize("raw,expected", [
        ("12,400", 12400.0),
        ("13,420", 13420.0),
        ("-100",   -100.0),
        ("0.1",    0.1),
        ("1,601.408", 1601.408),
    ])
    def test_parses_numerics(self, raw: str, expected: float) -> None:
        assert _parse_number(raw) == expected

    @pytest.mark.parametrize("raw", ["", "   ", "W", "NA", "--", "–", "— —", "garbage"])
    def test_blanks_and_na_return_none(self, raw: str) -> None:
        assert _parse_number(raw) is None


class TestParseHeaderDate:
    @pytest.mark.parametrize("raw,iso", [
        ("5/15/26",   "2026-05-15"),
        ("5/8/26",    "2026-05-08"),
        ("12/31/25",  "2025-12-31"),
        ("5/15/2026", "2026-05-15"),
    ])
    def test_parses_dates(self, raw: str, iso: str) -> None:
        assert _parse_header_date(raw) == iso

    @pytest.mark.parametrize("raw", ["STUB_1", "Difference", "Percent Change", "", "2025  Percentage"])
    def test_non_dates_return_none(self, raw: str) -> None:
        assert _parse_header_date(raw) is None


class TestExtractPeriodDates:
    def test_dedupes_and_labels_in_order(self) -> None:
        header = ["STUB_1", "STUB_2", "5/15/26", "5/8/26", "Difference",
                  "5/16/25", "Percent Change", "5/17/24", "Percent Change",
                  "5/15/26", "5/16/25", "Percent Change"]
        dates = _extract_period_dates(header)
        assert dates == {
            "current":        "2026-05-15",
            "prior_week":     "2026-05-08",
            "year_ago":       "2025-05-16",
            "two_years_ago":  "2024-05-17",
        }

    def test_handles_three_date_header(self) -> None:
        header = ["STUB_1", "5/15/26", "5/8/26", "Difference",
                  "Percent Change", "5/16/25", "Difference", "Percent Change"]
        dates = _extract_period_dates(header)
        assert dates == {
            "current":    "2026-05-15",
            "prior_week": "2026-05-08",
            "year_ago":   "2025-05-16",
        }
        assert "two_years_ago" not in dates


# =============================================================================
# parse_wpsr_csv — table 1 (two stacked sub-tables)
# =============================================================================

class TestParseTable1:
    def test_returns_two_sections(self) -> None:
        result = parse_wpsr_csv(_load_csv(1), 1)
        assert len(result["sections"]) == 2

    def test_section_names(self) -> None:
        result = parse_wpsr_csv(_load_csv(1), 1)
        assert [s["name"] for s in result["sections"]] == ["stocks", "supply_disposition"]

    def test_first_data_row_is_kept(self) -> None:
        """Regression: parser used to drop 'Crude Oil' thinking it was a header row."""
        result = parse_wpsr_csv(_load_csv(1), 1)
        stocks = result["sections"][0]
        labels = [r["label"] for r in stocks["rows"]]
        assert "Crude Oil" in labels

        crude = next(r for r in stocks["rows"] if r["label"] == "Crude Oil")
        assert crude["current"] == 819.188
        assert crude["prior_week"] == 836.971
        assert crude["diff_wow"] == -17.784

    def test_stocks_section_period_dates(self) -> None:
        result = parse_wpsr_csv(_load_csv(1), 1)
        stocks = result["sections"][0]
        assert stocks["period_dates"]["current"] == "2026-05-15"
        assert stocks["period_dates"]["prior_week"] == "2026-05-08"
        assert stocks["period_dates"]["year_ago"] == "2025-05-16"

    def test_supply_disposition_uses_two_label_columns(self) -> None:
        result = parse_wpsr_csv(_load_csv(1), 1)
        supply = result["sections"][1]
        assert supply["label_columns"] == ["group", "label"]

        first = supply["rows"][0]
        assert first["group"].strip() == "Crude Oil Supply"
        # Item label survives — not folded into the group column.
        assert "Domestic Production" in first["label"]
        # And numerics aren't shifted: current should be ~13,702.
        assert first["current"] == 13702.0
        assert first["prior_week"] == 13710.0
        assert first["diff_wow"] == -8.0


# =============================================================================
# parse_wpsr_csv — tables 2..9
# =============================================================================

class TestParseTables2Through9:
    def test_every_table_keeps_its_first_data_row(self) -> None:
        """Regression: parser used to discard the line right after the header."""
        expected_first_label_substr = {
            2: "Crude Oil Inputs",
            3: "Finished Motor Gasoline",
            4: "Crude Oil",
            5: "Total Motor Gasoline",
            6: "Distillate Fuel Oil",
            7: "Net Imports",
            8: "Canada",
            9: "Domestic Production",
        }
        for n, expected_substr in expected_first_label_substr.items():
            result = parse_wpsr_csv(_load_csv(n), n)
            first_row = result["sections"][0]["rows"][0]
            assert expected_substr in first_row["label"], (
                f"Table {n}: expected first row label to contain "
                f"{expected_substr!r}, got {first_row['label']!r}"
            )

    def test_two_label_column_tables_split_group_and_label(self) -> None:
        for n in (2, 3, 5, 8, 9):
            section = parse_wpsr_csv(_load_csv(n), n)["sections"][0]
            assert section["label_columns"] == ["group", "label"], f"table {n}"
            row = section["rows"][0]
            assert row["group"] and row["label"], f"table {n} row missing labels"
            # Item label must never equal the group label (the old bug).
            assert row["group"].strip() != row["label"].strip(), f"table {n}"

    def test_single_label_column_tables(self) -> None:
        for n in (4, 6, 7):
            section = parse_wpsr_csv(_load_csv(n), n)["sections"][0]
            assert section["label_columns"] == ["label"], f"table {n}"
            assert "group" not in section["rows"][0]

    def test_table2_first_row_values_align_with_csv(self) -> None:
        section = parse_wpsr_csv(_load_csv(2), 2)["sections"][0]
        crude_inputs = section["rows"][0]
        # CSV: 16,319 16,399 -80 16,490 -1.0 16,482 -1.0 16,205 16,260 -0.3
        assert crude_inputs["current"]            == 16319.0
        assert crude_inputs["prior_week"]         == 16399.0
        assert crude_inputs["diff_wow"]           == -80.0
        assert crude_inputs["year_ago"]           == 16490.0
        assert crude_inputs["pct_yoy"]            == -1.0
        assert crude_inputs["two_years_ago"]      == 16482.0
        assert crude_inputs["pct_two_year"]       == -1.0
        assert crude_inputs["four_week_avg"]      == 16205.0
        assert crude_inputs["four_week_avg_year_ago"] == 16260.0
        assert crude_inputs["pct_four_week"]      == -0.3

    def test_table8_has_2025_share_column(self) -> None:
        section = parse_wpsr_csv(_load_csv(8), 8)["sections"][0]
        canada = section["rows"][0]
        # CSV: 61.7  3,792  4,067  -275 ...
        assert canada["group"].strip() == "Crude Imports By Country of Origin"
        assert canada["label"].strip() == "Canada"
        assert canada["share_2025_pct"] == 61.7
        assert canada["current"]        == 3792.0
        assert canada["prior_week"]     == 4067.0

    def test_table9_has_no_diff_or_pct_fields(self) -> None:
        section = parse_wpsr_csv(_load_csv(9), 9)["sections"][0]
        assert "diff_wow" not in section["numeric_columns"]
        assert "pct_wow" not in section["numeric_columns"]
        assert "pct_yoy" not in section["numeric_columns"]
        first = section["rows"][0]
        assert "diff_wow" not in first
        assert first["current"]   == 13702.0
        assert first["year_ago"]  == 13392.0

    def test_period_dates_extracted_for_every_table(self) -> None:
        for n in TABLE_NUMBERS:
            for section in parse_wpsr_csv(_load_csv(n), n)["sections"]:
                assert section["period_dates"].get("current") == "2026-05-15", (
                    f"table {n} section {section['name']!r} missing current date"
                )
                assert section["period_dates"].get("prior_week") == "2026-05-08"

    def test_em_dash_na_marker_becomes_none(self) -> None:
        # Table 2 line 20 has "� �" / "– –" in the % columns. Either way, those
        # cells must come through as None (not 0, not raise).
        section = parse_wpsr_csv(_load_csv(2), 2)["sections"][0]
        # "Percent Utilization" is the row at index where labels match.
        pct_util = next(
            r for r in section["rows"]
            if "Percent Utilization" in r["label"]
        )
        assert pct_util["pct_yoy"] is None
        assert pct_util["pct_two_year"] is None
        assert pct_util["pct_four_week"] is None


# =============================================================================
# Schema sanity
# =============================================================================

class TestSchemas:
    def test_one_schema_per_table(self) -> None:
        assert set(WPSR_SCHEMAS.keys()) == set(TABLE_NUMBERS)

    def test_section_field_names_are_unique(self) -> None:
        for n, schema in WPSR_SCHEMAS.items():
            for section in schema.sections:
                fields = [c.field for c in section.numeric_columns]
                assert len(fields) == len(set(fields)), (
                    f"table {n} section {section.name!r} has duplicate numeric fields"
                )


# =============================================================================
# content_hash
# =============================================================================

class TestContentHash:
    def test_returns_64_hex_chars(self) -> None:
        h = content_hash([{"label": "x", "current": 1}])
        assert len(h) == 64
        int(h, 16)

    def test_same_input_same_hash(self) -> None:
        data = [{"label": "x", "current": 1.0}]
        assert content_hash(data) == content_hash(data)

    def test_different_input_different_hash(self) -> None:
        a = [{"label": "x", "current": 1.0}]
        b = [{"label": "x", "current": 2.0}]
        assert content_hash(a) != content_hash(b)

    def test_key_order_does_not_affect_hash(self) -> None:
        assert content_hash({"a": 1, "b": 2}) == content_hash({"b": 2, "a": 1})


# =============================================================================
# get_wpsr_table — HTTP + cache integration
# =============================================================================

class TestGetWpsrTable:
    @pytest.mark.asyncio
    async def test_returns_full_payload_shape(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        mock_get.return_value = _csv_response(1)
        result = await service.get_wpsr_table(1)
        assert set(result.keys()) == {
            "table_number", "title", "sections", "hash", "last_fetched",
        }

    @pytest.mark.asyncio
    async def test_table_number_echoed(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        mock_get.return_value = _csv_response(3)
        result = await service.get_wpsr_table(3)
        assert result["table_number"] == 3

    @pytest.mark.asyncio
    async def test_hits_correct_url(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        mock_get.return_value = _csv_response(5)
        await service.get_wpsr_table(5)
        assert mock_get.call_args.args[0] == "https://ir.eia.gov/wpsr/table5.csv"

    @pytest.mark.asyncio
    async def test_follows_redirects(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        mock_get.return_value = _csv_response(1)
        await service.get_wpsr_table(1)
        assert mock_get.call_args.kwargs.get("follow_redirects") is True

    @pytest.mark.asyncio
    async def test_sets_user_agent(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        mock_get.return_value = _csv_response(1)
        await service.get_wpsr_table(1)
        assert mock_get.call_args.kwargs["headers"]["User-Agent"] == "CI-Desk/1.0"

    @pytest.mark.asyncio
    async def test_cache_key_includes_table_number_and_v2(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        mock_get.return_value = _csv_response(7)
        await service.get_wpsr_table(7)
        assert no_cache.cache_or_fetch.call_args.args[0] == "wpsr:v2:table:7"

    @pytest.mark.asyncio
    async def test_hash_matches_sections_content(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        mock_get.return_value = _csv_response(1)
        result = await service.get_wpsr_table(1)
        assert result["hash"] == content_hash(result["sections"])

    @pytest.mark.asyncio
    async def test_last_fetched_is_iso_utc(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        mock_get.return_value = _csv_response(1)
        result = await service.get_wpsr_table(1)
        assert result["last_fetched"].endswith("Z")
        from datetime import datetime
        datetime.fromisoformat(result["last_fetched"].replace("Z", "+00:00"))

    @pytest.mark.asyncio
    async def test_rejects_invalid_table_number(self, no_cache: MagicMock) -> None:
        service, _ = _make_service()
        with pytest.raises(ValueError):
            await service.get_wpsr_table(0)
        with pytest.raises(ValueError):
            await service.get_wpsr_table(10)

    @pytest.mark.asyncio
    async def test_http_error_propagates(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        mock_get.return_value = _empty_response(status=500)
        with pytest.raises(httpx.HTTPStatusError):
            await service.get_wpsr_table(1)


# =============================================================================
# get_all_wpsr_tables — parallel fan-out
# =============================================================================

class TestGetAllWpsrTables:
    @pytest.mark.asyncio
    async def test_returns_all_nine_tables(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        mock_get.side_effect = lambda url, **kw: _csv_response(
            int(url.rstrip(".csv").split("table")[-1])
        )
        result = await service.get_all_wpsr_tables()
        assert set(result["tables"].keys()) == {str(n) for n in TABLE_NUMBERS}

    @pytest.mark.asyncio
    async def test_fetches_each_table_once(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        mock_get.side_effect = lambda url, **kw: _csv_response(
            int(url.rstrip(".csv").split("table")[-1])
        )
        await service.get_all_wpsr_tables()
        urls = [call.args[0] for call in mock_get.call_args_list]
        for n in TABLE_NUMBERS:
            assert any(f"table{n}.csv" in u for u in urls), f"no fetch for table {n}"

    @pytest.mark.asyncio
    async def test_combined_payload_shape(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        mock_get.side_effect = lambda url, **kw: _csv_response(
            int(url.rstrip(".csv").split("table")[-1])
        )
        result = await service.get_all_wpsr_tables()
        assert set(result.keys()) == {"tables", "hash", "last_fetched"}

    @pytest.mark.asyncio
    async def test_uses_versioned_combined_cache_key(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        mock_get.side_effect = lambda url, **kw: _csv_response(
            int(url.rstrip(".csv").split("table")[-1])
        )
        await service.get_all_wpsr_tables()
        keys_used = [call.args[0] for call in no_cache.cache_or_fetch.call_args_list]
        assert "wpsr:v2:all" in keys_used
