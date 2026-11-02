"""Provider selection, fallback and the ``search_web`` tool.

``WEB_SEARCH_PROVIDERS`` is an ordered, comma-separated list of provider names
(default ``tavily``). A provider is tried only when it is listed *and* its key
is set; unconfigured providers are skipped silently. A provider that raises or
returns nothing hands over to the next configured provider, so one flaky
backend never stops a DeepSearch run.
"""

from __future__ import annotations

import logging
import os

from langchain_core.tools import tool

from backend.app.tools.web_search.base import (
    SearchResult,
    WebSearchProvider,
    format_results,
    normalize_search_topic,
)
from backend.app.tools.web_search.serpbase import SerpBaseProvider
from backend.app.tools.web_search.tavily import TavilyProvider

logger = logging.getLogger(__name__)

PROVIDERS_ENV_VAR = "WEB_SEARCH_PROVIDERS"
DEFAULT_PROVIDER_ORDER = ("tavily",)
DEFAULT_MAX_RESULTS = 4

#: Adapters are stateless — every SDK/HTTP client inside them is created lazily.
PROVIDERS: dict[str, WebSearchProvider] = {
    "tavily": TavilyProvider(),
    "serpbase": SerpBaseProvider(),
}


def provider_order() -> list[str]:
    """Return the configured provider names, in order, without duplicates."""
    raw = (os.getenv(PROVIDERS_ENV_VAR) or "").strip()
    names = [part.strip().lower() for part in raw.split(",")]
    ordered = [name for name in names if name] or list(DEFAULT_PROVIDER_ORDER)

    seen: set[str] = set()
    unique: list[str] = []
    for name in ordered:
        if name in seen:
            continue
        seen.add(name)
        unique.append(name)
    return unique


def listed_providers() -> list[WebSearchProvider]:
    """Return adapters for every known provider named in the config."""
    providers: list[WebSearchProvider] = []
    for name in provider_order():
        provider = PROVIDERS.get(name)
        if provider is None:
            logger.warning("Ignoring unknown web search provider %r", name)
            continue
        providers.append(provider)
    return providers


def configured_providers() -> list[WebSearchProvider]:
    """Return the listed providers whose credentials are present."""
    return [provider for provider in listed_providers() if provider.is_configured()]


def run_search(
    query: str,
    *,
    max_results: int = DEFAULT_MAX_RESULTS,
    time_range: str | None = None,
) -> str:
    """Run ``query`` through the provider chain and render one text block.

    Never raises: an error string is returned instead, so the research graph
    keeps running.
    """
    providers = configured_providers()
    if not providers:
        # Nothing is configured. Attempt whatever is listed anyway so the caller
        # gets a provider error rather than a silent no-op — the same outcome
        # the single-client implementation produced without a key.
        providers = listed_providers()
    if not providers:
        return "Error using web search: no web search providers are configured."

    errors: list[tuple[str, str]] = []
    empty: list[tuple[str, str]] = []
    for provider in providers:
        try:
            results = provider.search(
                query,
                max_results=max_results,
                time_range=time_range,
            )
        except Exception as exc:  # noqa: BLE001 - any provider fault means "try the next one"
            logger.warning("Web search provider %s failed: %s", provider.name, exc)
            errors.append((provider.label, str(exc)))
            continue
        if results:
            return format_results(query, results)
        empty.append((provider.label, "no results"))

    if errors:
        failures = errors + empty
        if len(failures) == 1:
            label, message = failures[0]
            return f"Error using {label} search: {message}"
        detail = "; ".join(f"{label}: {message}" for label, message in failures)
        return f"Error using web search: {detail}"
    return f'No web search results found for "{query}".'


@tool
def search_web(
    query: str,
    search_topic: list[str] | str | None = None,
    time_range: str | None = None,
) -> str:
    """Search the web for the given query using the configured providers.

    ``search_topic`` accepts a list or a single string. ``time_range`` accepts
    ``day``, ``month``, ``week`` or ``year``; omit it for historical queries.
    """
    topics = normalize_search_topic(search_topic)
    if topics:
        query += f" Use sources: {', '.join(topics)}"
    return run_search(query, max_results=DEFAULT_MAX_RESULTS, time_range=time_range)


__all__ = [
    "DEFAULT_PROVIDER_ORDER",
    "PROVIDERS",
    "PROVIDERS_ENV_VAR",
    "SearchResult",
    "WebSearchProvider",
    "configured_providers",
    "listed_providers",
    "provider_order",
    "run_search",
    "search_web",
]
