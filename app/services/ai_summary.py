"""AI summary service — calls Gemini to produce a crude oil macro briefing."""

from __future__ import annotations

import logging
from typing import Any

import google.generativeai as genai

logger = logging.getLogger(__name__)

GEMINI_MODEL = "gemini-2.0-flash"

DEFAULT_PROMPT = (
    "You are an expert crude oil market analyst. Below are financial news headlines "
    "and summaries from the last 24 hours. Please provide a concise macro summary "
    "(3-5 paragraphs) of the most relevant developments for the crude oil market. "
    "Focus on supply/demand dynamics, geopolitical factors, economic indicators, "
    "and significant price drivers. Be analytical and specific."
)


class AiSummaryService:
    def __init__(self, api_key: str) -> None:
        genai.configure(api_key=api_key)
        self._model = genai.GenerativeModel(GEMINI_MODEL)

    async def summarize(
        self,
        news_items: list[dict[str, Any]],
        prompt: str = DEFAULT_PROMPT,
    ) -> str:
        if not news_items:
            return "No news items available for the last 24 hours."

        news_block = "\n\n".join(
            f"- {item['title']}: {item['description']}"
            for item in news_items
        )
        full_prompt = f"{prompt}\n\nNews items:\n{news_block}"

        response = await self._model.generate_content_async(full_prompt)
        return response.text
