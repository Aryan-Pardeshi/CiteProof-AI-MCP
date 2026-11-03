"""SerpBase provider — optional Google organic results for DeepSearch.

Opt-in only: used when ``SERPBASE_API_KEY`` is set *and* ``serpbase`` appears in
``WEB_SEARCH_PROVIDERS``. SerpBase is a paid third-party service.

API contract (https://serpbase.dev/docs):

* ``POST https://api.serpbase.dev/google/search``
* key goes in the ``X-API-Key`` header — query-string keys are rejected
* body ``{"q", "gl", "hl", "num"}``
* HTTP 200 is **not** success: the business ``status`` field must be ``0``
  (``1001`` bad key, ``1020`` no credits, ``1029`` rate limited,
  ``1500``-``1504`` upstream/transient)
* only the ``organic`` key holds organic web results
"""

from __future__ import annotations

import os
import threading
from typing import Any

import httpx

from backend.app.tools.web_search.base import (
    SearchResult,
    WebSearchProvider,
    normalize_time_range,
)

SERPBASE_BASE_URL = "https://api.serpbase.dev"
SERPBASE_SEARCH_PATH = "/google/search"
SERPBASE_TIMEOUT_SECONDS = 15.0

_client: httpx.Client | None = None
_client_lock = threading.Lock()


class SerpBaseError(RuntimeError):
    """Raised when SerpBase reports a transport or business-level failure."""


def http_client() -> httpx.Client:
    """Return the shared HTTP client, creating it on first use."""
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                _client = httpx.Client(timeout=SERPBASE_TIMEOUT_SECONDS)
    return _client


def _api_key() -> str:
    return (os.getenv("SERPBASE_API_KEY") or "").strip()


def _status_hint(status: object) -> str:
    if not isinstance(status, int):
        return "unexpected status"
    if status == 1001:
        return "invalid API key"
    if status == 1020:
        return "no credits remaining"
    if status == 1029:
        return "rate limited"
    if 1500 <= status <= 1504:
        return "upstream/transient error"
    return "unexpected status"


def _as_position(value: Any) -> int | None:
    try:
        position = int(value)
    except (TypeError, ValueError):
        return None
    return position if position > 0 else None


def map_serpbase_response(payload: Any) -> list[SearchResult]:
    """Map the ``organic`` array of a SerpBase envelope onto ``SearchResult``."""
    if not isinstance(payload, dict):
        return []
    results: list[SearchResult] = []
    for item in payload.get("organic") or []:
        if not isinstance(item, dict):
            continue
        url = str(item.get("link") or "").strip()
        if not url:
            continue
        published = item.get("date")
        results.append(
            SearchResult(
                title=str(item.get("title") or url).strip(),
                url=url,
                snippet=str(item.get("snippet") or "").strip(),
                provider="serpbase",
                position=_as_position(item.get("position")),
                published_date=str(published) if published else None,
            )
        )
    return results


class SerpBaseProvider(WebSearchProvider):
    """Structured Google organic results via SerpBase."""

    name = "serpbase"
    display_name = "SerpBase"

    def __init__(self, client: httpx.Client | None = None) -> None:
        # ``client`` is a test seam; production code uses the lazy shared client.
        self._client = client

    def is_configured(self) -> bool:
        return bool(_api_key())

    def search(
        self,
        query: str,
        *,
        max_results: int = 4,
        time_range: str | None = None,
    ) -> list[SearchResult]:
        """Return SerpBase organic results, or fail fast on unsupported filters."""
        # SerpBase's documented request body has no time filter. Rather than
        # silently return results outside the requested period, fail so
        # run_search falls back to a provider that supports the restriction.
        if normalize_time_range(time_range) is not None:
            raise SerpBaseError("time_range is not supported by SerpBase")
        api_key = _api_key()
        if not api_key:
            raise SerpBaseError("SERPBASE_API_KEY is not set")

        payload = {"q": query, "gl": "us", "hl": "en", "num": max_results}
        headers = {"Content-Type": "application/json", "X-API-Key": api_key}
        url = f"{SERPBASE_BASE_URL}{SERPBASE_SEARCH_PATH}"
        client = self._client or http_client()

        try:
            response = client.post(url, json=payload, headers=headers)
        except httpx.HTTPError as exc:
            raise SerpBaseError(f"request failed: {exc}") from exc

        try:
            body = response.json()
        except ValueError as exc:
            raise SerpBaseError(
                f"HTTP {response.status_code}: response was not JSON"
            ) from exc

        if not isinstance(body, dict):
            raise SerpBaseError(f"HTTP {response.status_code}: unexpected response body")

        status = body.get("status")
        if status != 0:
            # HTTP 200 is not success: the business status is authoritative.
            raise SerpBaseError(f"status {status}: {_status_hint(status)}")

        return map_serpbase_response(body)
