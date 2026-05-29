"""Tests for AiSummaryService — google.generativeai is fully mocked."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.ai_summary import DEFAULT_PROMPT, GEMINI_MODEL, AiSummaryService


def _make_service() -> tuple[AiSummaryService, MagicMock]:
    """Return (service, mock_model) with genai fully patched."""
    with patch("app.services.ai_summary.genai") as mock_genai:
        mock_model = MagicMock()
        mock_model.generate_content_async = AsyncMock()
        mock_genai.GenerativeModel.return_value = mock_model
        svc = AiSummaryService(api_key="test_key")
    # Swap in the mock model so patching doesn't need to stay active
    svc._model = mock_model
    return svc, mock_model


def _fake_response(text: str) -> MagicMock:
    resp = MagicMock()
    resp.text = text
    return resp


_SAMPLE_ITEMS = [
    {"title": "WTI up 2%", "description": "WTI crude rose on inventory draw.", "published_at": "2026-05-29T10:00:00+00:00"},
    {"title": "OPEC holds output", "description": "OPEC+ keeps production steady.", "published_at": "2026-05-29T09:00:00+00:00"},
]


class TestSummarize:
    @pytest.mark.asyncio
    async def test_returns_model_response_text(self) -> None:
        svc, mock_model = _make_service()
        mock_model.generate_content_async.return_value = _fake_response("Summary text")
        result = await svc.summarize(_SAMPLE_ITEMS)
        assert result == "Summary text"

    @pytest.mark.asyncio
    async def test_empty_items_returns_no_news_message(self) -> None:
        svc, mock_model = _make_service()
        result = await svc.summarize([])
        assert "No news items" in result
        mock_model.generate_content_async.assert_not_called()

    @pytest.mark.asyncio
    async def test_default_prompt_included_in_call(self) -> None:
        svc, mock_model = _make_service()
        mock_model.generate_content_async.return_value = _fake_response("Summary")
        await svc.summarize(_SAMPLE_ITEMS)
        prompt_arg = mock_model.generate_content_async.call_args.args[0]
        assert DEFAULT_PROMPT in prompt_arg

    @pytest.mark.asyncio
    async def test_custom_prompt_overrides_default(self) -> None:
        svc, mock_model = _make_service()
        mock_model.generate_content_async.return_value = _fake_response("Summary")
        await svc.summarize(_SAMPLE_ITEMS, prompt="Custom prompt")
        prompt_arg = mock_model.generate_content_async.call_args.args[0]
        assert "Custom prompt" in prompt_arg
        assert DEFAULT_PROMPT not in prompt_arg

    @pytest.mark.asyncio
    async def test_news_titles_included_in_prompt(self) -> None:
        svc, mock_model = _make_service()
        mock_model.generate_content_async.return_value = _fake_response("Summary")
        await svc.summarize(_SAMPLE_ITEMS)
        prompt_arg = mock_model.generate_content_async.call_args.args[0]
        assert "WTI up 2%" in prompt_arg
        assert "OPEC holds output" in prompt_arg

    @pytest.mark.asyncio
    async def test_model_exception_propagates(self) -> None:
        svc, mock_model = _make_service()
        mock_model.generate_content_async.side_effect = RuntimeError("API error")
        with pytest.raises(RuntimeError, match="API error"):
            await svc.summarize(_SAMPLE_ITEMS)


class TestInit:
    def test_configures_genai_with_api_key(self) -> None:
        with patch("app.services.ai_summary.genai") as mock_genai:
            mock_genai.GenerativeModel.return_value = MagicMock()
            AiSummaryService(api_key="my_key")
            mock_genai.configure.assert_called_once_with(api_key="my_key")

    def test_creates_model_with_correct_name(self) -> None:
        with patch("app.services.ai_summary.genai") as mock_genai:
            mock_genai.GenerativeModel.return_value = MagicMock()
            AiSummaryService(api_key="my_key")
            mock_genai.GenerativeModel.assert_called_once_with(GEMINI_MODEL)
