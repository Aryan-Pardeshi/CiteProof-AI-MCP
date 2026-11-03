# Provider-neutral primitives for the DeepSearch web-search layer
"""Shared models and helpers for every web-search adapter.

Each adapter maps its own payload into :class:`SearchResult`, so the researcher
agent sees one stable text block no matter who served the results. Nothing in
this module touches the network or reads a key at import time.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from pydantic import BaseModel

# Models sometimes send these as strings when a value is not set.
_UNSET_VALUES = frozenset({"", "none", "null"})
_TIME_RANGES = frozenset({"day", "month", "week", "year"})


class SearchResult(BaseModel):
    """One normalised organic web result."""

    title: str
    url: str
    snippet: str = ""
    provider: str
    position: int | None = None
    published_date: str | None = None


class WebSearchProvider(ABC):
    """Minimal contract a web-search backend has to implement."""

    name: str = ""
    display_name: str = ""

    @abstractmethod
    def is_configured(self) -> bool:
        """Return True when every credential this provider needs is present."""

    @abstractmethod
    def search(
        self,
        query: str,
        *,
        max_results: int = 4,
        time_range: str | None = None,
    ) -> list[SearchResult]:
        """Return normalised results, or raise if the provider itself failed."""

    @property
    def label(self) -> str:
        """Human-readable name used in log lines and error strings."""
        return self.display_name or self.name.title()


def normalize_search_topic(value: list[str] | str | None) -> list[str]:
    """Return a clean list; ``None``, ``""``, ``"none"`` and ``"null"`` mean unset."""
    if value is None:
        return []
    if isinstance(value, str):
        cleaned = value.strip()
        return [] if cleaned.lower() in _UNSET_VALUES else [cleaned]
    if isinstance(value, (list, tuple, set)):
        topics = []
        for item in value:
            cleaned = str(item).strip()
            if cleaned and cleaned.lower() not in _UNSET_VALUES:
                topics.append(cleaned)
        return topics
    return []


def normalize_time_range(value: str | None) -> str | None:
    """Return one of day/month/week/year, or ``None`` for an unset/invalid value."""
    if not isinstance(value, str):
        return None
    cleaned = value.strip().lower()
    if cleaned in _UNSET_VALUES or cleaned not in _TIME_RANGES:
        return None
    return cleaned


def _one_line(value: str | None) -> str:
    """Collapse whitespace so a snippet never breaks the result block layout."""
    if not value:
        return ""
    return " ".join(str(value).split())


def format_results(query: str, results: list[SearchResult]) -> str:
    """Render results as the single stable text block ``search_web`` returns."""
    provider = results[0].provider if results else "unknown"
    lines = [f'Web search results for "{_one_line(query)}" (provider: {provider}):']
    for index, result in enumerate(results, start=1):
        lines.append(f"{index}. {_one_line(result.title) or '(untitled)'}")
        lines.append(f"   URL: {result.url.strip()}")
        snippet = _one_line(result.snippet)
        if snippet:
            lines.append(f"   {snippet}")
    return "\n".join(lines)
