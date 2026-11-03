# Tavily adapter for the provider-neutral web-search layer
"""Tavily provider — the default DeepSearch backend.

Behaviour is unchanged from the previous single-client implementation
(``max_results=4``, ``search_depth="advanced"``, ``time_range`` passed through),
but the ``TavilyClient`` is now built on first use instead of at import time, so
importing the module never requires a key.
"""

from __future__ import annotations

import os
import threading
from typing import Any

from backend.app.tools.web_search.base import (
    SearchResult,
    WebSearchProvider,
    normalize_time_range,
)


class _LazyTavilyClient:
    """Builds ``TavilyClient`` on first use; safe to construct at import time."""

    def __init__(self) -> None:
        self._client: Any = None
        self._lock = threading.Lock()

    def _resolve(self) -> Any:
        if self._client is None:
            with self._lock:
                if self._client is None:
                    from tavily import TavilyClient

                    api_key = (os.getenv("TAVILY_API_KEY") or "").strip()
                    if not api_key:
                        raise RuntimeError("TAVILY_API_KEY is not set")
                    self._client = TavilyClient(api_key=api_key)
        return self._client

    def search(self, **kwargs: Any) -> Any:
        return self._resolve().search(**kwargs)

    def __getattr__(self, item: str) -> Any:
        return getattr(self._resolve(), item)


#: Process-wide lazy client. Never holds a key until the first search call.
tavily_client = _LazyTavilyClient()


def map_tavily_response(payload: Any) -> list[SearchResult]:
    """Map a Tavily search payload onto :class:`SearchResult` objects."""
    if not isinstance(payload, dict):
        return []
    results: list[SearchResult] = []
    for item in payload.get("results") or []:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        if not url:
            continue
        results.append(
            SearchResult(
                title=str(item.get("title") or url).strip(),
                url=url,
                snippet=str(item.get("content") or "").strip(),
                provider="tavily",
                published_date=item.get("published_date") or None,
            )
        )
    return results


class TavilyProvider(WebSearchProvider):
    """Tavily's AI-oriented index. Default provider while Firecrawl lands."""

    name = "tavily"
    display_name = "Tavily"

    def __init__(self, client: Any | None = None) -> None:
        # ``client`` is a test seam; production code uses the lazy singleton.
        self._client = client

    def is_configured(self) -> bool:
        return bool((os.getenv("TAVILY_API_KEY") or "").strip())

    def search(
        self,
        query: str,
        *,
        max_results: int = 4,
        time_range: str | None = None,
    ) -> list[SearchResult]:
        client = self._client or tavily_client
        payload = client.search(
            time_range=normalize_time_range(time_range),
            query=query,
            max_results=max_results,
            include_images=False,
            include_raw_content=False,
            search_depth="advanced",
        )
        return map_tavily_response(payload)
