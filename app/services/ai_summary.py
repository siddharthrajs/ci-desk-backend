"""AI summary service — calls Gemini or OpenAI to produce a crude oil macro briefing."""

from __future__ import annotations

import logging
from typing import Any

import google.generativeai as genai
from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

GEMINI_MODEL = "gemini-2.0-flash"
OPENAI_MODEL = "gpt-4o"

DEFAULT_PROMPT = (
    "You are an expert crude oil market analyst. Below are financial news headlines "
    "and summaries from the last 24 hours. Please provide a concise macro summary "
    "(3-5 paragraphs) of the most relevant developments for the crude oil market. "
    "Focus on supply/demand dynamics, geopolitical factors, economic indicators, "
    "and significant price drivers. Be analytical and specific."
)


class AiSummaryService:
    def __init__(self, gemini_api_key: str, openai_api_key: str) -> None:
        genai.configure(api_key=gemini_api_key)
        self._gemini_model = genai.GenerativeModel(GEMINI_MODEL)
        self._openai_client = AsyncOpenAI(api_key=openai_api_key)

    async def summarize(
        self,
        news_items: list[dict[str, Any]],
        prompt: str = DEFAULT_PROMPT,
        provider: str = "gemini",
    ) -> str:
        if not news_items:
            return "No news items available for the last 24 hours."

        news_block = "\n\n".join(
            f"- {item['title']}: {item['description']}"
            for item in news_items
        )
        full_prompt = f"{prompt}\n\nNews items:\n{news_block}"

        if provider == "openai":
            response = await self._openai_client.chat.completions.create(
                model=OPENAI_MODEL,
                messages=[{"role": "user", "content": full_prompt}],
            )
            return response.choices[0].message.content or ""

        response = await self._gemini_model.generate_content_async(full_prompt)
        return response.text
