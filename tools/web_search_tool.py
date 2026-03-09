"""
tools/web_search_tool.py — JARVIS Web Search (Phase 6)

Backends (in order of preference):
  1. DuckDuckGo (duckduckgo-search) — free, no key
  2. Google Custom Search JSON API — needs GOOGLE_CSE_KEY + GOOGLE_CSE_CX
  3. SerpAPI — needs SERP_API_KEY

Usage (from tool_registry.py):
    from tools.web_search_tool import WebSearchTool
    results = WebSearchTool().search("Python asyncio tutorial", max_results=5)
"""

import logging
import os
from typing import List, Optional

logger = logging.getLogger(__name__)


class WebSearchTool:
    """
    Unified web search interface.
    Auto-selects the best available backend.
    """

    def search(self, query: str, max_results: int = 5) -> List[dict]:
        """
        Search the web.
        Returns list of {"title": ..., "url": ..., "snippet": ...}
        """
        query = query.strip()[:500]  # sanitize
        if not query:
            return []

        # Try DuckDuckGo first (no key needed)
        result = self._duck(query, max_results)
        if result:
            return result

        # Try Google CSE
        result = self._google_cse(query, max_results)
        if result:
            return result

        # Try SerpAPI
        result = self._serp(query, max_results)
        if result:
            return result

        return [{"title": "Search unavailable", "url": "", "snippet":
                 "Install duckduckgo-search: pip install duckduckgo-search"}]

    def search_text(self, query: str, max_results: int = 5) -> str:
        """Format search results as markdown text."""
        results = self.search(query, max_results)
        if not results:
            return "No results found."
        lines = [f"**{i+1}. {r['title']}**\n{r['url']}\n{r['snippet']}"
                 for i, r in enumerate(results) if r.get("url")]
        return "\n\n".join(lines)

    # ---------------------------------------------------------------- #
    # Backends                                                          #
    # ---------------------------------------------------------------- #

    def _duck(self, query: str, max_results: int) -> Optional[List[dict]]:
        """DuckDuckGo via duckduckgo-search library."""
        try:
            from duckduckgo_search import DDGS  # type: ignore
            results = []
            with DDGS() as ddgs:
                for r in ddgs.text(query, max_results=max_results):
                    results.append({
                        "title": r.get("title", ""),
                        "url": r.get("href", ""),
                        "snippet": r.get("body", ""),
                        "source": "duckduckgo",
                    })
            return results if results else None
        except ImportError:
            return None
        except Exception as exc:
            logger.warning("DuckDuckGo search error: %s", exc)
            return None

    def _google_cse(self, query: str, max_results: int) -> Optional[List[dict]]:
        """Google Custom Search Engine JSON API."""
        api_key = os.environ.get("GOOGLE_CSE_KEY", "")
        cx = os.environ.get("GOOGLE_CSE_CX", "")
        if not api_key or not cx:
            return None
        try:
            import urllib.request
            import urllib.parse
            import json
            params = urllib.parse.urlencode({
                "key": api_key, "cx": cx,
                "q": query, "num": min(max_results, 10)
            })
            url = f"https://www.googleapis.com/customsearch/v1?{params}"
            # Only fetch known Google API endpoint
            with urllib.request.urlopen(url, timeout=10) as resp:  # nosec B310
                data = json.loads(resp.read())
            items = data.get("items", [])
            return [
                {"title": i.get("title", ""), "url": i.get("link", ""),
                 "snippet": i.get("snippet", ""), "source": "google"}
                for i in items
            ]
        except Exception as exc:
            logger.warning("Google CSE error: %s", exc)
            return None

    def _serp(self, query: str, max_results: int) -> Optional[List[dict]]:
        """SerpAPI backend."""
        api_key = os.environ.get("SERP_API_KEY", "")
        if not api_key:
            return None
        try:
            import urllib.request
            import urllib.parse
            import json
            params = urllib.parse.urlencode({
                "api_key": api_key, "q": query, "num": min(max_results, 10)
            })
            url = f"https://serpapi.com/search?{params}"
            with urllib.request.urlopen(url, timeout=10) as resp:  # nosec B310
                data = json.loads(resp.read())
            results = []
            for r in data.get("organic_results", []):
                results.append({
                    "title": r.get("title", ""),
                    "url": r.get("link", ""),
                    "snippet": r.get("snippet", ""),
                    "source": "serp",
                })
            return results if results else None
        except Exception as exc:
            logger.warning("SerpAPI error: %s", exc)
            return None
