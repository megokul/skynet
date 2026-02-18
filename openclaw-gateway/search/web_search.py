"""
SKYNET — Web Search Integration

Provides web search for the SKYNET AI coding engine.  Uses Brave Search API
(free tier: 2 000 queries/month) with DuckDuckGo HTML scraping as
a zero-cost fallback.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger("skynet.search")


class WebSearcher:
    """Web search with Brave API primary and DuckDuckGo fallback."""

    def __init__(self, brave_api_key: str = ""):
        self.brave_api_key = brave_api_key

    async def search(self, query: str, num_results: int = 5) -> str:
        """
        Search the web and return formatted results as a string
        suitable for use as a tool_result.
        """
        num_results = min(max(num_results, 1), 10)

        if self.brave_api_key:
            try:
                return await self._brave_search(query, num_results)
            except Exception as exc:
                logger.warning("Brave search failed: %s — falling back to DDG", exc)

        return await self._ddg_search(query, num_results)

    async def _brave_search(self, query: str, num_results: int) -> str:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                "https://api.search.brave.com/res/v1/web/search",
                params={"q": query, "count": num_results},
                headers={
                    "Accept": "application/json",
                    "Accept-Encoding": "gzip",
                    "X-Subscription-Token": self.brave_api_key,
                },
                timeout=15.0,
            )
            resp.raise_for_status()
            data = resp.json()

        results = data.get("web", {}).get("results", [])
        if not results:
            return "No results found."

        formatted = []
        for i, r in enumerate(results, 1):
            formatted.append(
                f"{i}. {r.get('title', 'No title')}\n"
                f"   URL: {r.get('url', '')}\n"
                f"   {r.get('description', 'No description')}\n"
            )
        return "\n".join(formatted)

    async def _ddg_search(self, query: str, num_results: int) -> str:
        """Fallback: DuckDuckGo Lite HTML scraping (no API key needed)."""
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    "https://lite.duckduckgo.com/lite/",
                    params={"q": query},
                    headers={"User-Agent": "SKYNET/3.0"},
                    timeout=15.0,
                    follow_redirects=True,
                )
                resp.raise_for_status()
                html = resp.text

            # Simple extraction from DDG Lite results.
            results = []
            # DDG Lite wraps results in <a> tags with class "result-link".
            import re
            links = re.findall(
                r'class="result-link"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
                html,
                re.DOTALL,
            )
            for url, title in links[:num_results]:
                title_clean = re.sub(r"<[^>]+>", "", title).strip()
                results.append(f"- {title_clean}\n  URL: {url}")

            if results:
                return "\n".join(results)

            # If regex didn't match, try a simpler pattern.
            links = re.findall(r'<a[^>]+href="(https?://[^"]+)"[^>]*>([^<]+)</a>', html)
            for url, title in links[:num_results]:
                if "duckduckgo" not in url:
                    results.append(f"- {title.strip()}\n  URL: {url}")

            return "\n".join(results) if results else "No results found (DDG fallback)."

        except Exception as exc:
            logger.warning("DuckDuckGo fallback failed: %s", exc)
            return f"Web search unavailable: {exc}"
