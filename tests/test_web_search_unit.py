"""Offline unit tests for the provider-neutral web-search layer.

Every adapter is driven through an injected fake client or ``httpx.MockTransport``,
so these tests never touch the network and never need a real key. Live checks
live in ``tests/test_live_providers.py`` behind the ``live`` marker, which
``pytest.ini`` deselects by default.
"""

import inspect
import json
import os
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

root_dir = Path(__file__).resolve().parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from backend.app.tools.web_search import registry, search_web
from backend.app.tools.web_search.base import (
    SearchResult,
    WebSearchProvider,
    format_results,
)
from backend.app.tools.web_search.serpbase import (
    SERPBASE_BASE_URL,
    SerpBaseError,
    SerpBaseProvider,
)
from backend.app.tools.web_search.tavily import TavilyProvider

TAVILY_PAYLOAD = {
    "query": "alphafold",
    "results": [
        {
            "title": "AlphaFold 3 predicts structures",
            "url": "https://example.org/alphafold-3",
            "content": "A new model for protein structure prediction.",
            "score": 0.93,
        },
        {
            "title": "Deep learning for biology",
            "url": "https://example.org/deep-learning",
            "content": "How structure prediction changed.",
            "score": 0.81,
            "published_date": "2026-05-01",
        },
    ],
}

SERPBASE_PAYLOAD = {
    "status": 0,
    "request_id": "req_01HX",
    "elapsed_ms": 412,
    "credits_charged": 1,
    "organic": [
        {
            "title": "Vendor pricing page",
            "link": "https://vendor.example.com/pricing",
            "snippet": "What the vendor says about its own plans.",
            "position": 1,
        },
        {
            "title": "Independent comparison",
            "link": "https://blog.example.net/comparison",
            "snippet": "Third-party breakdown of the plans.",
            "position": 2,
        },
    ],
    # Only the organic array holds web results; this must never leak into them.
    "knowledge_graph": {"title": "Not an organic result", "link": "https://kg.example"},
    "related_searches": ["other query"],
}


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeTavilyClient:
    def __init__(self, payload=None, error=None):
        self.payload = TAVILY_PAYLOAD if payload is None else payload
        self.error = error
        self.calls: list[dict] = []

    def search(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.payload


class FakeProvider(WebSearchProvider):
    """Configurable stand-in used to test ordering and fallback."""

    _DISPLAY_NAMES = {"tavily": "Tavily", "serpbase": "SerpBase"}

    def __init__(self, name, *, results=None, error=None, configured=True):
        self.name = name
        self.display_name = self._DISPLAY_NAMES.get(name, name.title())
        self._results = list(results or [])
        self._error = error
        self._configured = configured
        self.calls: list[dict] = []

    def is_configured(self) -> bool:
        return self._configured

    def search(self, query, *, max_results=4, time_range=None):
        self.calls.append(
            {"query": query, "max_results": max_results, "time_range": time_range}
        )
        if self._error is not None:
            raise self._error
        return list(self._results)


def _result(provider: str, index: int = 1) -> SearchResult:
    return SearchResult(
        title=f"{provider} title {index}",
        url=f"https://{provider}.example/{index}",
        snippet=f"{provider} snippet {index}",
        provider=provider,
        position=index,
    )


@pytest.fixture()
def clean_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """No keys and no provider list, like a fresh checkout."""
    for var in ("TAVILY_API_KEY", "SERPBASE_API_KEY", "WEB_SEARCH_PROVIDERS"):
        monkeypatch.delenv(var, raising=False)
    return monkeypatch


@pytest.fixture()
def serpbase_key(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv("SERPBASE_API_KEY", "test-serpbase-key")
    return "test-serpbase-key"


def _serpbase_provider(payload, *, status_code=200, captured=None) -> SerpBaseProvider:
    def handler(request: httpx.Request) -> httpx.Response:
        if captured is not None:
            captured.append(request)
        return httpx.Response(status_code, json=payload)

    return SerpBaseProvider(client=httpx.Client(transport=httpx.MockTransport(handler)))


# ---------------------------------------------------------------------------
# Tavily mapping
# ---------------------------------------------------------------------------


def test_tavily_payload_maps_to_search_results():
    results = TavilyProvider(client=FakeTavilyClient()).search("alphafold")

    assert [r.url for r in results] == [
        "https://example.org/alphafold-3",
        "https://example.org/deep-learning",
    ]
    assert results[0].title == "AlphaFold 3 predicts structures"
    assert results[0].snippet == "A new model for protein structure prediction."
    assert results[0].provider == "tavily"
    assert results[1].published_date == "2026-05-01"


def test_tavily_keeps_todays_request_shape():
    client = FakeTavilyClient()
    TavilyProvider(client=client).search("quantum", time_range="month")

    call = client.calls[0]
    assert call["query"] == "quantum"
    assert call["max_results"] == 4
    assert call["search_depth"] == "advanced"
    assert call["time_range"] == "month"
    assert call["include_images"] is False
    assert call["include_raw_content"] is False


@pytest.mark.parametrize("raw", ["last week", "", "none", None])
def test_tavily_drops_an_unset_or_invalid_time_range(raw):
    client = FakeTavilyClient()
    TavilyProvider(client=client).search("quantum", time_range=raw)
    assert client.calls[0]["time_range"] is None


def test_empty_or_malformed_tavily_payloads_map_to_nothing():
    assert TavilyProvider(client=FakeTavilyClient(payload={})).search("q") == []
    assert TavilyProvider(client=FakeTavilyClient(payload=[])).search("q") == []
    assert (
        TavilyProvider(
            client=FakeTavilyClient(payload={"results": [{"title": "no url"}]})
        ).search("q")
        == []
    )


# ---------------------------------------------------------------------------
# SerpBase mapping
# ---------------------------------------------------------------------------


def test_serpbase_organic_results_map_to_search_results(serpbase_key):
    results = _serpbase_provider(SERPBASE_PAYLOAD).search("vendor pricing pages")

    assert [r.url for r in results] == [
        "https://vendor.example.com/pricing",
        "https://blog.example.net/comparison",
    ]
    assert results[0].title == "Vendor pricing page"
    assert results[0].snippet == "What the vendor says about its own plans."
    assert results[0].provider == "serpbase"
    assert results[0].position == 1
    assert results[1].position == 2


def test_serpbase_ignores_non_organic_blocks(serpbase_key):
    provider = _serpbase_provider(SERPBASE_PAYLOAD)
    results = provider.search("vendor pricing pages")

    urls = {r.url for r in results}
    assert "https://kg.example" not in urls
    assert len(results) == 2


def test_serpbase_sends_the_key_in_a_header_and_no_query_string(serpbase_key):
    captured: list[httpx.Request] = []
    provider = _serpbase_provider(SERPBASE_PAYLOAD, captured=captured)

    provider.search("vendor pricing pages", max_results=4)

    request = captured[0]
    assert request.method == "POST"
    assert str(request.url) == f"{SERPBASE_BASE_URL}/google/search"
    assert request.url.query == b""
    assert request.headers["X-API-Key"] == serpbase_key
    assert "api_key" not in str(request.url).lower()
    body = json.loads(request.content)
    assert body == {"q": "vendor pricing pages", "gl": "us", "hl": "en", "num": 4}
    # No tracking or referral parameters of any kind.
    assert set(body) == {"q", "gl", "hl", "num"}


@pytest.mark.parametrize(
    ("status", "needle"),
    [
        (1001, "invalid API key"),
        (1020, "no credits"),
        (1029, "rate limited"),
        (1500, "upstream"),
        (1504, "upstream"),
    ],
)
def test_serpbase_http_200_with_a_non_zero_status_is_a_failure(serpbase_key, status, needle):
    provider = _serpbase_provider({"status": status, "organic": []}, status_code=200)

    with pytest.raises(SerpBaseError) as excinfo:
        provider.search("q")

    assert str(status) in str(excinfo.value)
    assert needle in str(excinfo.value)


def test_serpbase_raises_when_the_body_is_not_json(serpbase_key):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, text="<html>bad gateway</html>")

    provider = SerpBaseProvider(client=httpx.Client(transport=httpx.MockTransport(handler)))

    with pytest.raises(SerpBaseError, match="not JSON"):
        provider.search("q")


def test_serpbase_wraps_transport_errors(serpbase_key):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out")

    provider = SerpBaseProvider(client=httpx.Client(transport=httpx.MockTransport(handler)))

    with pytest.raises(SerpBaseError, match="request failed"):
        provider.search("q")


def test_serpbase_without_a_key_refuses_to_call(clean_env):
    provider = SerpBaseProvider(client=_serpbase_provider(SERPBASE_PAYLOAD))

    assert provider.is_configured() is False
    with pytest.raises(SerpBaseError, match="SERPBASE_API_KEY"):
        provider.search("q")


# ---------------------------------------------------------------------------
# Provider order and fallback
# ---------------------------------------------------------------------------


def test_providers_default_to_tavily(clean_env):
    assert registry.provider_order() == ["tavily"]
    assert [p.name for p in registry.listed_providers()] == ["tavily"]


def test_the_configured_order_is_preserved_and_deduplicated(clean_env):
    clean_env.setenv("WEB_SEARCH_PROVIDERS", " SerpBase , tavily ,serpbase,,")
    assert registry.provider_order() == ["serpbase", "tavily"]
    assert [p.name for p in registry.listed_providers()] == ["serpbase", "tavily"]


def test_unknown_provider_names_are_ignored(clean_env):
    clean_env.setenv("WEB_SEARCH_PROVIDERS", "tavily,firecrawl")
    assert [p.name for p in registry.listed_providers()] == ["tavily"]


def test_the_first_configured_provider_wins(clean_env, monkeypatch):
    clean_env.setenv("WEB_SEARCH_PROVIDERS", "tavily,serpbase")
    clean_env.setenv("TAVILY_API_KEY", "test-tavily-key")
    clean_env.setenv("SERPBASE_API_KEY", "test-serpbase-key")
    first = FakeProvider("tavily", results=[_result("tavily")])
    second = FakeProvider("serpbase", results=[_result("serpbase")])
    monkeypatch.setitem(registry.PROVIDERS, "tavily", first)
    monkeypatch.setitem(registry.PROVIDERS, "serpbase", second)

    text = search_web.invoke({"query": "ai"})

    assert "provider: tavily" in text
    assert first.calls and second.calls == []


@pytest.mark.parametrize("failure", ["raises", "empty"])
def test_falls_back_to_the_next_configured_provider(clean_env, monkeypatch, failure):
    clean_env.setenv("WEB_SEARCH_PROVIDERS", "tavily,serpbase")
    clean_env.setenv("TAVILY_API_KEY", "test-tavily-key")
    clean_env.setenv("SERPBASE_API_KEY", "test-serpbase-key")
    if failure == "raises":
        first = FakeProvider("tavily", error=RuntimeError("rate limited"))
    else:
        first = FakeProvider("tavily", results=[])
    second = FakeProvider("serpbase", results=[_result("serpbase")])
    monkeypatch.setitem(registry.PROVIDERS, "tavily", first)
    monkeypatch.setitem(registry.PROVIDERS, "serpbase", second)

    text = search_web.invoke({"query": "ai"})

    assert first.calls, "the first provider should have been attempted"
    assert second.calls, "the second provider should have been used as a fallback"
    assert "provider: serpbase" in text
    assert "serpbase title 1" in text


def test_unconfigured_providers_are_skipped_silently(clean_env, monkeypatch):
    clean_env.setenv("WEB_SEARCH_PROVIDERS", "tavily,serpbase")
    clean_env.setenv("SERPBASE_API_KEY", "test-serpbase-key")  # no TAVILY_API_KEY
    skipped = FakeProvider("tavily", results=[_result("tavily")], configured=False)
    used = FakeProvider("serpbase", results=[_result("serpbase")])
    monkeypatch.setitem(registry.PROVIDERS, "tavily", skipped)
    monkeypatch.setitem(registry.PROVIDERS, "serpbase", used)

    text = search_web.invoke({"query": "ai"})

    assert skipped.calls == []
    assert used.calls
    assert "provider: serpbase" in text


def test_a_provider_missing_from_the_list_is_never_used(clean_env, monkeypatch):
    clean_env.setenv("WEB_SEARCH_PROVIDERS", "serpbase")  # tavily not listed
    clean_env.setenv("TAVILY_API_KEY", "test-tavily-key")
    clean_env.setenv("SERPBASE_API_KEY", "test-serpbase-key")
    unlisted = FakeProvider("tavily", results=[_result("tavily")])
    listed = FakeProvider("serpbase", results=[_result("serpbase")])
    monkeypatch.setitem(registry.PROVIDERS, "tavily", unlisted)
    monkeypatch.setitem(registry.PROVIDERS, "serpbase", listed)

    text = search_web.invoke({"query": "ai"})

    assert unlisted.calls == []
    assert "provider: serpbase" in text


# ---------------------------------------------------------------------------
# Failure surfaces
# ---------------------------------------------------------------------------


def test_a_single_failing_provider_keeps_todays_error_string(clean_env, monkeypatch):
    clean_env.setenv("TAVILY_API_KEY", "test-tavily-key")
    monkeypatch.setitem(
        registry.PROVIDERS,
        "tavily",
        FakeProvider("tavily", error=RuntimeError("upstream exploded")),
    )

    text = search_web.invoke({"query": "ai"})

    assert text == "Error using Tavily search: upstream exploded"


def test_all_providers_failing_returns_one_error_string(clean_env, monkeypatch):
    clean_env.setenv("WEB_SEARCH_PROVIDERS", "tavily,serpbase")
    clean_env.setenv("TAVILY_API_KEY", "test-tavily-key")
    clean_env.setenv("SERPBASE_API_KEY", "test-serpbase-key")
    monkeypatch.setitem(
        registry.PROVIDERS, "tavily", FakeProvider("tavily", error=RuntimeError("rate limited"))
    )
    monkeypatch.setitem(
        registry.PROVIDERS,
        "serpbase",
        FakeProvider("serpbase", error=RuntimeError("status 1020: no credits remaining")),
    )

    text = search_web.invoke({"query": "ai"})

    assert text.startswith("Error using ")
    assert "Tavily: rate limited" in text
    assert "SerpBase: status 1020" in text


def test_all_providers_empty_returns_a_no_results_block(clean_env, monkeypatch):
    clean_env.setenv("WEB_SEARCH_PROVIDERS", "tavily,serpbase")
    clean_env.setenv("TAVILY_API_KEY", "test-tavily-key")
    clean_env.setenv("SERPBASE_API_KEY", "test-serpbase-key")
    monkeypatch.setitem(registry.PROVIDERS, "tavily", FakeProvider("tavily", results=[]))
    monkeypatch.setitem(registry.PROVIDERS, "serpbase", FakeProvider("serpbase", results=[]))

    text = search_web.invoke({"query": "ai"})

    assert text == 'No web search results found for "ai".'
    assert not text.startswith("Error")


def test_no_configured_provider_surfaces_a_provider_error(clean_env, monkeypatch):
    """With no keys at all, DeepSearch still gets a readable error, not a crash."""
    monkeypatch.setitem(
        registry.PROVIDERS,
        "tavily",
        FakeProvider("tavily", error=RuntimeError("TAVILY_API_KEY is not set")),
    )

    text = search_web.invoke({"query": "ai"})

    assert text.startswith("Error using Tavily search:")
    assert "TAVILY_API_KEY" in text


# ---------------------------------------------------------------------------
# The tool contract and the rendered block
# ---------------------------------------------------------------------------


def test_search_web_keeps_its_name_and_signature():
    assert search_web.name == "search_web"
    assert list(search_web.args) == ["query", "search_topic", "time_range"]

    signature = inspect.signature(getattr(search_web, "func"))
    assert list(signature.parameters) == ["query", "search_topic", "time_range"]
    assert signature.return_annotation == "str"


def test_topic_handling_still_matches_the_old_tool(clean_env, monkeypatch):
    clean_env.setenv("TAVILY_API_KEY", "test-tavily-key")
    provider = FakeProvider("tavily", results=[_result("tavily")])
    monkeypatch.setitem(registry.PROVIDERS, "tavily", provider)

    search_web.invoke(
        {"query": "quantum", "search_topic": ["news", "none", "academic"], "time_range": "week"}
    )

    assert provider.calls[0]["query"] == "quantum Use sources: news, academic"
    assert provider.calls[0]["time_range"] == "week"


@pytest.mark.parametrize("provider_name", ["tavily", "serpbase"])
def test_every_provider_renders_the_same_block(clean_env, monkeypatch, provider_name):
    clean_env.setenv("WEB_SEARCH_PROVIDERS", provider_name)
    clean_env.setenv("TAVILY_API_KEY", "test-tavily-key")
    clean_env.setenv("SERPBASE_API_KEY", "test-serpbase-key")
    monkeypatch.setitem(
        registry.PROVIDERS,
        provider_name,
        FakeProvider(provider_name, results=[_result(provider_name, 1), _result(provider_name, 2)]),
    )

    lines = search_web.invoke({"query": "ai"}).splitlines()

    assert lines[0] == f'Web search results for "ai" (provider: {provider_name}):'
    assert lines[1] == f"1. {provider_name} title 1"
    assert lines[2] == f"   URL: https://{provider_name}.example/1"
    assert lines[3] == f"   {provider_name} snippet 1"
    assert lines[4] == f"2. {provider_name} title 2"


def test_format_results_collapses_whitespace_and_keeps_order():
    results = [
        SearchResult(
            title="Line one\nline two",
            url="https://example.org/a",
            snippet="  lots \n of\tspace  ",
            provider="tavily",
            position=1,
        )
    ]

    text = format_results("my query", results)

    assert text.splitlines() == [
        'Web search results for "my query" (provider: tavily):',
        "1. Line one line two",
        "   URL: https://example.org/a",
        "   lots of space",
    ]


# ---------------------------------------------------------------------------
# Import-time guarantees
# ---------------------------------------------------------------------------

_IMPORT_PROBE = """
import backend.app.tools.tavily_search as legacy
import backend.app.tools.web_search as ws
import backend.app.tools.web_search.tavily as tavily_module

assert ws.search_web.name == "search_web"
assert legacy.search_web.name == "search_web"
assert legacy.tavily_client is tavily_module.tavily_client
assert tavily_module.tavily_client._client is None, "Tavily client built at import time"
assert ws.configured_providers() == []
print("ok")
"""


def test_importing_with_no_keys_does_not_crash(clean_env):
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"TAVILY_API_KEY", "SERPBASE_API_KEY", "WEB_SEARCH_PROVIDERS"}
    }

    probe = subprocess.run(
        [sys.executable, "-c", _IMPORT_PROBE],
        cwd=str(root_dir),
        env=env,
        capture_output=True,
        text=True,
    )

    assert probe.returncode == 0, probe.stderr
    assert probe.stdout.strip() == "ok"
