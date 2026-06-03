from unittest.mock import AsyncMock, MagicMock, patch

import feedparser
import pytest
from fastapi.testclient import TestClient

from app.main import app

CACHED_BRIEF = {
    "sources": [
        {
            "source": "Reuters Energy",
            "url": "https://feeds.reuters.com/reuters/businessNews",
            "articles": [
                {
                    "title": "Oil rises on OPEC+ cut extension",
                    "link": "https://example.com/1",
                    "published": "2026-06-03T08:00:00+00:00",
                    "summary": "WTI climbed...",
                }
            ],
        }
    ],
    "generated_at": "2026-06-03T08:00:00+00:00",
}

SAMPLE_RSS = """<?xml version="1.0"?>
<rss version="2.0"><channel><title>Test</title>
<item>
  <title>Oil rises on OPEC+ cut extension</title>
  <link>https://example.com/1</link>
  <pubDate>Tue, 03 Jun 2026 08:00:00 +0000</pubDate>
  <description>WTI climbed past $80...</description>
</item>
</channel></rss>"""


@pytest.fixture(scope="module")
def client_with_cache():
    # module-scoped: one TestClient for all tests in this module.
    # Patch get_cache at the router's import — the lifespan overwrites the
    # module-level `cache` variable so patching there doesn't survive.
    # Also patch scheduler start/shutdown: the AsyncIOScheduler is a module-level
    # singleton that binds to the first event loop it sees; patching prevents it
    # from trying to reuse a closed loop when other test modules ran first.
    mock_cache = MagicMock()
    mock_cache.ping = AsyncMock(return_value=True)
    mock_cache.get_json = AsyncMock(return_value=CACHED_BRIEF)
    mock_cache.close = AsyncMock()
    with patch("app.routers.macro.get_cache", return_value=mock_cache), \
         patch("app.core.cache.cache"), \
         patch("app.scheduler.setup.scheduler.start"), \
         patch("app.scheduler.setup.scheduler.shutdown"):
        with TestClient(app, raise_server_exceptions=True) as c:
            yield c, mock_cache


def test_brief_returns_cached_data(client_with_cache):
    c, mock_cache = client_with_cache
    mock_cache.get_json = AsyncMock(return_value=CACHED_BRIEF)

    response = c.get("/api/macro/brief")

    assert response.status_code == 200
    body = response.json()
    assert len(body["sources"]) == 1
    assert body["sources"][0]["source"] == "Reuters Energy"
    assert body["sources"][0]["articles"][0]["title"] == "Oil rises on OPEC+ cut extension"


def test_brief_cold_cache_returns_empty(client_with_cache):
    c, mock_cache = client_with_cache
    mock_cache.get_json = AsyncMock(return_value=None)

    response = c.get("/api/macro/brief")

    assert response.status_code == 200
    body = response.json()
    assert body["sources"] == []
    assert "generated_at" in body


def test_feedparser_parses_rss_text():
    """Verify feedparser can parse an RSS string (no network call)."""
    parsed = feedparser.parse(SAMPLE_RSS)

    assert len(parsed.entries) == 1
    assert parsed.entries[0].title == "Oil rises on OPEC+ cut extension"
    assert parsed.entries[0].link == "https://example.com/1"
