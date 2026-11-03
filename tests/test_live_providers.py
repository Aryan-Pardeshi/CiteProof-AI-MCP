"""Live API smoke tests for every academic provider adapter.

These hit the REAL external APIs and are excluded from normal test runs via
pytest.ini addopts (`-m "not live"`). Re-verify manually with:

    python -m pytest tests/test_live_providers.py -m live -v

Each test asserts structural validity (PaperRecord schema, retrieval_source
tag, dedup-usable identity fields), not exact result counts — public indexes
change constantly. Semantic Scholar may legitimately return [] on a 429-throttled
shared pool; that is asserted as "no exception + list", never as ">0 papers".
"""

import os
import sys
from pathlib import Path

import pytest

root_dir = Path(__file__).resolve().parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from backend.app.models.evidence import PaperRecord
from backend.app.tools.crossref_search import search_crossref
from backend.app.tools.datacite_search import search_datacite
from backend.app.tools.doaj_search import search_doaj
from backend.app.tools.europe_pmc_search import search_europe_pmc
from backend.app.tools.openalex_search import search_openalex
from backend.app.tools.orcid_search import resolve_orcid_authors
from backend.app.tools.pubmed_search import search_pubmed
from backend.app.tools.semantic_scholar_search import search_semantic_scholar
from backend.app.tools.web_search.base import SearchResult
from backend.app.tools.web_search.serpbase import SerpBaseProvider
from backend.app.tools.web_search.tavily import TavilyProvider

QUERY = "machine learning"


def _assert_valid_records(records, expected_source):
    assert isinstance(records, list)
    for r in records:
        assert isinstance(r, PaperRecord)
        assert r.retrieval_source == expected_source
        assert r.paper_id and len(r.paper_id) == 16
        assert r.title.strip()


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_openalex():
    records = await search_openalex(QUERY, limit=3)
    _assert_valid_records(records, "openalex")
    assert len(records) >= 1


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_semantic_scholar():
    records = await search_semantic_scholar(QUERY, limit=3)
    # May be [] under shared-pool throttling; must never raise.
    _assert_valid_records(records, "semantic_scholar")


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_europe_pmc():
    records = await search_europe_pmc(QUERY, limit=3)
    _assert_valid_records(records, "europe_pmc")
    assert len(records) >= 1


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_doaj():
    records = await search_doaj(QUERY, limit=3)
    _assert_valid_records(records, "doaj")
    assert len(records) >= 1


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_datacite():
    records = await search_datacite(QUERY, limit=3)
    _assert_valid_records(records, "datacite")
    assert len(records) >= 1


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_orcid_author_resolution():
    identities = await resolve_orcid_authors("family-names:Carberry", limit=3)
    assert isinstance(identities, list)
    assert len(identities) >= 1
    assert all(i.orcid_id for i in identities)


# Reference adapters kept as-is: included here so one command re-verifies the
# whole retrieval layer against live APIs.
@pytest.mark.live
@pytest.mark.asyncio
async def test_live_crossref_unchanged():
    records = await search_crossref(QUERY, limit=3)
    _assert_valid_records(records, "crossref")


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_pubmed_unchanged():
    # Returns [] without NCBI_EMAIL/NCBI_TOOL_NAME configured by design.
    records = await search_pubmed(QUERY, limit=3)
    _assert_valid_records(records, "pubmed")


# --- DeepSearch web search (opt-in; skipped without a key) -------------------


@pytest.mark.live
def test_live_web_search_tavily():
    if not (os.getenv("TAVILY_API_KEY") or "").strip():
        pytest.skip("TAVILY_API_KEY not set")
    results = TavilyProvider().search(QUERY, max_results=3)
    assert isinstance(results, list) and results
    for result in results:
        assert isinstance(result, SearchResult)
        assert result.provider == "tavily"
        assert result.url.startswith("http")


@pytest.mark.live
def test_live_web_search_serpbase():
    if not (os.getenv("SERPBASE_API_KEY") or "").strip():
        pytest.skip("SERPBASE_API_KEY not set")
    results = SerpBaseProvider().search(QUERY, max_results=3)
    assert isinstance(results, list) and results
    for result in results:
        assert isinstance(result, SearchResult)
        assert result.provider == "serpbase"
        assert result.url.startswith("http")
        assert result.position is None or result.position >= 1

