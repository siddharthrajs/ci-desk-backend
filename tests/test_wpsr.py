"""Tests for WPSRService — httpx responses and Redis are fully mocked."""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.services.wpsr import (
    TABLE_NUMBERS,
    WPSRService,
    _parse_number,
    content_hash,
    parse_wpsr_csv,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

# Sample CSV mirroring the EIA WPSR layout: title row, header row, a section
# header (numeric cells empty), then data rows with comma-separated thousands.
SAMPLE_CSV = (
    '"Table 1. U.S. Petroleum Balance Sheet, Week Ending 05/23/2025"\n'
    '"","Current Week (05/23/25)","Week Ago (05/16/25)","Difference","Percent Change","Year Ago (05/24/24)"\n'
    '"Crude Oil Supply","","","","",""\n'
    '"Domestic Production","13,420","13,400","20","0.1","13,100"\n'
    '"Alaska","443","441","2","0.5","447"\n'
    '"Net Imports","2,500","2,600","-100","-3.8","2,300"\n'
    '"Withheld","W","W","W","W","W"\n'
)

_DUMMY_REQUEST = httpx.Request("GET", "https://ir.eia.gov/wpsr/table1.csv")


def _csv_response(text: str, status: int = 200) -> httpx.Response:
    return httpx.Response(status, text=text, request=_DUMMY_REQUEST)


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
# _parse_number — pure helper
# =============================================================================

class TestParseNumber:
    def test_strips_thousands_separator(self) -> None:
        assert _parse_number("12,400") == 12400.0

    def test_returns_float(self) -> None:
        assert isinstance(_parse_number("13,420"), float)

    def test_handles_negative(self) -> None:
        assert _parse_number("-100") == -100.0

    def test_handles_decimal(self) -> None:
        assert _parse_number("0.1") == 0.1

    def test_empty_returns_none(self) -> None:
        assert _parse_number("") is None

    def test_whitespace_returns_none(self) -> None:
        assert _parse_number("   ") is None

    def test_withheld_marker_returns_none(self) -> None:
        assert _parse_number("W") is None

    def test_na_returns_none(self) -> None:
        assert _parse_number("NA") is None

    def test_dash_returns_none(self) -> None:
        assert _parse_number("--") is None

    def test_unparseable_returns_none(self) -> None:
        assert _parse_number("garbage") is None


# =============================================================================
# parse_wpsr_csv — CSV → structured dict
# =============================================================================

class TestParseWpsrCsv:
    def test_extracts_title(self) -> None:
        result = parse_wpsr_csv(SAMPLE_CSV)
        assert "Week Ending 05/23/2025" in result["title"]

    def test_skips_header_row(self) -> None:
        result = parse_wpsr_csv(SAMPLE_CSV)
        # No data row should have the header text as its label.
        labels = [r["label"] for r in result["rows"]]
        assert "Current Week (05/23/25)" not in labels

    def test_row_shape_matches_spec(self) -> None:
        result = parse_wpsr_csv(SAMPLE_CSV)
        row = next(r for r in result["rows"] if r["label"] == "Domestic Production")
        assert set(row.keys()) == {
            "label", "current", "prior_week", "difference", "percent_change", "year_ago",
        }

    def test_parses_numeric_values(self) -> None:
        result = parse_wpsr_csv(SAMPLE_CSV)
        row = next(r for r in result["rows"] if r["label"] == "Domestic Production")
        assert row["current"] == 13420.0
        assert row["prior_week"] == 13400.0
        assert row["difference"] == 20.0
        assert row["percent_change"] == 0.1
        assert row["year_ago"] == 13100.0

    def test_section_header_keeps_label_with_none_numerics(self) -> None:
        result = parse_wpsr_csv(SAMPLE_CSV)
        section = next(r for r in result["rows"] if r["label"] == "Crude Oil Supply")
        assert section["current"] is None
        assert section["prior_week"] is None
        assert section["year_ago"] is None

    def test_withheld_row_has_all_none(self) -> None:
        result = parse_wpsr_csv(SAMPLE_CSV)
        withheld = next(r for r in result["rows"] if r["label"] == "Withheld")
        assert all(withheld[col] is None for col in
                   ("current", "prior_week", "difference", "percent_change", "year_ago"))

    def test_handles_negative_values(self) -> None:
        result = parse_wpsr_csv(SAMPLE_CSV)
        row = next(r for r in result["rows"] if r["label"] == "Net Imports")
        assert row["difference"] == -100.0
        assert row["percent_change"] == -3.8

    def test_empty_csv_returns_empty_rows(self) -> None:
        result = parse_wpsr_csv("")
        assert result == {"title": "", "rows": []}

    def test_blank_lines_are_skipped(self) -> None:
        csv_with_blanks = (
            '"Title"\n'
            '\n'
            '"","C","P","D","%","Y"\n'
            '\n'
            '"Foo","1","2","3","4","5"\n'
            '\n'
        )
        result = parse_wpsr_csv(csv_with_blanks)
        assert len(result["rows"]) == 1
        assert result["rows"][0]["label"] == "Foo"


# =============================================================================
# content_hash — hashing helper
# =============================================================================

class TestContentHash:
    def test_returns_hex_string(self) -> None:
        h = content_hash([{"label": "x", "current": 1}])
        assert isinstance(h, str)
        assert len(h) == 64  # SHA-256 hex digest length
        int(h, 16)  # must be valid hex

    def test_same_input_same_hash(self) -> None:
        data = [{"label": "x", "current": 1.0}]
        assert content_hash(data) == content_hash(data)

    def test_different_input_different_hash(self) -> None:
        a = [{"label": "x", "current": 1.0}]
        b = [{"label": "x", "current": 2.0}]
        assert content_hash(a) != content_hash(b)

    def test_key_order_does_not_affect_hash(self) -> None:
        a = {"a": 1, "b": 2}
        b = {"b": 2, "a": 1}
        assert content_hash(a) == content_hash(b)

    def test_hash_changes_when_row_added(self) -> None:
        base = [{"label": "x", "current": 1.0}]
        plus = base + [{"label": "y", "current": 2.0}]
        assert content_hash(base) != content_hash(plus)


# =============================================================================
# get_wpsr_table — HTTP + cache integration
# =============================================================================

class TestGetWpsrTable:
    @pytest.mark.asyncio
    async def test_returns_full_payload_shape(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        mock_get.return_value = _csv_response(SAMPLE_CSV)
        result = await service.get_wpsr_table(1)
        assert set(result.keys()) == {
            "table_number", "title", "rows", "hash", "last_fetched",
        }

    @pytest.mark.asyncio
    async def test_table_number_echoed(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        mock_get.return_value = _csv_response(SAMPLE_CSV)
        result = await service.get_wpsr_table(3)
        assert result["table_number"] == 3

    @pytest.mark.asyncio
    async def test_hits_correct_url(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        mock_get.return_value = _csv_response(SAMPLE_CSV)
        await service.get_wpsr_table(5)
        assert mock_get.call_args.args[0] == "https://ir.eia.gov/wpsr/table5.csv"

    @pytest.mark.asyncio
    async def test_follows_redirects(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        mock_get.return_value = _csv_response(SAMPLE_CSV)
        await service.get_wpsr_table(1)
        assert mock_get.call_args.kwargs.get("follow_redirects") is True

    @pytest.mark.asyncio
    async def test_sets_user_agent(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        mock_get.return_value = _csv_response(SAMPLE_CSV)
        await service.get_wpsr_table(1)
        headers = mock_get.call_args.kwargs.get("headers", {})
        assert headers.get("User-Agent") == "CI-Desk/1.0"

    @pytest.mark.asyncio
    async def test_cache_key_includes_table_number(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        mock_get.return_value = _csv_response(SAMPLE_CSV)
        await service.get_wpsr_table(7)
        assert no_cache.cache_or_fetch.call_args.args[0] == "wpsr:table:7"

    @pytest.mark.asyncio
    async def test_hash_matches_rows_content(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        mock_get.return_value = _csv_response(SAMPLE_CSV)
        result = await service.get_wpsr_table(1)
        assert result["hash"] == content_hash(result["rows"])

    @pytest.mark.asyncio
    async def test_last_fetched_is_iso_utc(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        mock_get.return_value = _csv_response(SAMPLE_CSV)
        result = await service.get_wpsr_table(1)
        assert result["last_fetched"].endswith("Z")
        # parseable as ISO-8601
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
        mock_get.return_value = _csv_response("", status=500)
        with pytest.raises(httpx.HTTPStatusError):
            await service.get_wpsr_table(1)

    @pytest.mark.asyncio
    async def test_rows_carry_parsed_values(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        mock_get.return_value = _csv_response(SAMPLE_CSV)
        result = await service.get_wpsr_table(1)
        production = next(r for r in result["rows"] if r["label"] == "Domestic Production")
        assert production["current"] == 13420.0


# =============================================================================
# get_all_wpsr_tables — parallel fan-out
# =============================================================================

class TestGetAllWpsrTables:
    @pytest.mark.asyncio
    async def test_returns_all_nine_tables(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        mock_get.return_value = _csv_response(SAMPLE_CSV)
        result = await service.get_all_wpsr_tables()
        assert set(result["tables"].keys()) == {str(n) for n in TABLE_NUMBERS}

    @pytest.mark.asyncio
    async def test_fetches_each_table_once(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        mock_get.return_value = _csv_response(SAMPLE_CSV)
        await service.get_all_wpsr_tables()
        urls = [call.args[0] for call in mock_get.call_args_list]
        for n in TABLE_NUMBERS:
            assert f"table{n}.csv" in " ".join(urls)

    @pytest.mark.asyncio
    async def test_combined_hash_changes_when_any_table_changes(
        self, no_cache: MagicMock,
    ) -> None:
        service, mock_get = _make_service()
        mock_get.return_value = _csv_response(SAMPLE_CSV)
        first = await service.get_all_wpsr_tables()

        # Same CSV for tables 1..8, modified payload for table 9 → combined hash flips.
        altered = SAMPLE_CSV.replace("13,420", "13,999")
        call_count = {"n": 0}

        def side_effect(*args: Any, **kwargs: Any) -> httpx.Response:
            call_count["n"] += 1
            return _csv_response(altered if call_count["n"] == 9 else SAMPLE_CSV)

        mock_get.side_effect = side_effect
        second = await service.get_all_wpsr_tables()

        assert first["hash"] != second["hash"]

    @pytest.mark.asyncio
    async def test_combined_payload_shape(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        mock_get.return_value = _csv_response(SAMPLE_CSV)
        result = await service.get_all_wpsr_tables()
        assert set(result.keys()) == {"tables", "hash", "last_fetched"}

    @pytest.mark.asyncio
    async def test_uses_combined_cache_key(self, no_cache: MagicMock) -> None:
        service, mock_get = _make_service()
        mock_get.return_value = _csv_response(SAMPLE_CSV)
        await service.get_all_wpsr_tables()
        keys_used = [call.args[0] for call in no_cache.cache_or_fetch.call_args_list]
        assert "wpsr:all" in keys_used
