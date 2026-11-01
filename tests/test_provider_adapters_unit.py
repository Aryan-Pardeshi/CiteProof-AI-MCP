"""Mocked unit tests for the new academic provider adapters.

Every external HTTP interaction goes through httpx.MockTransport injected via
the adapters' optional `client=` seam, so these tests never touch the network.
Live re-verification lives in tests/test_live_providers.py (@pytest.mark.live).
"""

import asyncio
import sys
from pathlib import Path

import httpx
import pytest

root_dir = Path(__file__).resolve().parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from backend.app.models.evidence import PaperRecord
from backend.app.tools.datacite_search import search_datacite
from backend.app.tools.doaj_search import search_doaj
from backend.app.tools.europe_pmc_search import search_europe_pmc
from backend.app.tools.openalex_search import reconstruct_abstract, search_openalex
from backend.app.tools.orcid_search import (
    AuthorIdentity,
    _confident_match,
    enrich_record_authors,
    fetch_orcid_profile,
    resolve_orcid_authors,
)
from backend.app.tools.semantic_scholar_search import search_semantic_scholar


def _mock_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# ---------------------------------------------------------------------------
# OpenAlex
# ---------------------------------------------------------------------------

OPENALEX_SAMPLE = {
    "results": [
        {
            "id": "https://openalex.org/W2741809807",
            "doi": "https://doi.org/10.1038/s41586-021-03819-2",
            "title": "Highly accurate protein structure prediction",
            "display_name": "Highly accurate protein structure prediction (fallback)",
            "publication_year": 2021,
            "authorships": [
                {"author": {"display_name": "John Jumper"}},
                {"author": {"display_name": "Demis Hassabis"}},
            ],
            "abstract_inverted_index": {"AlphaFold": [0], "predicts": [1], "structures": [2]},
            "primary_location": {
                "source": {"display_name": "Nature"},
                "landing_page_url": "https://doi.org/10.1038/s41586-021-03819-2",
            },
            "open_access": {"oa_url": "https://example.org/oa.pdf"},
            "best_oa_location": {"pdf_url": None},
            "cited_by_count": 12345,
            "ids": {"openalex": "W2741809807", "pmid": "https://pubmed.ncbi.nlm.nih.gov/34265844/", "doi": "10.1038/s41586-021-03819-2"},
        },
    ]
}


@pytest.mark.asyncio
async def test_openalex_normalizes_full_mapping():
    seen_params = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen_params.update(dict(request.url.params))
        return httpx.Response(200, json=OPENALEX_SAMPLE)

    async with _mock_client(handler) as client:
        records = await search_openalex("protein folding", limit=10, client=client)

    assert len(records) == 1
    r = records[0]
    assert isinstance(r, PaperRecord)
    assert r.retrieval_source == "openalex"
    assert r.doi == "10.1038/s41586-021-03819-2"  # full URL form stripped
    assert r.title == "Highly accurate protein structure prediction"
    assert r.authors == ["John Jumper", "Demis Hassabis"]
    assert r.year == "2021"
    assert r.venue == "Nature"
    assert r.abstract == "AlphaFold predicts structures"
    assert r.pmid == "34265844"
    assert r.pdf_url == "https://example.org/oa.pdf"
    assert r.source_url == "https://doi.org/10.1038/s41586-021-03819-2"
    assert r.citation_count == 12345
    assert r.screening_status == "retrieved"
    assert r.is_retracted is False
    assert "is_retracted" in seen_params["select"]


@pytest.mark.asyncio
async def test_openalex_sends_mailto_from_env(monkeypatch):
    monkeypatch.setenv("OPENALEX_EMAIL", "researcher@university.edu")
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["mailto"] = dict(request.url.params).get("mailto")
        return httpx.Response(200, json={"results": []})

    async with _mock_client(handler) as client:
        await search_openalex("anything", client=client)
    assert seen["mailto"] == "researcher@university.edu"


@pytest.mark.asyncio
async def test_openalex_placeholder_email_not_sent(monkeypatch):
    monkeypatch.setenv("OPENALEX_EMAIL", "your_email@example.com")
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["mailto"] = dict(request.url.params).get("mailto")
        return httpx.Response(200, json={"results": []})

    async with _mock_client(handler) as client:
        await search_openalex("anything", client=client)
    assert seen.get("mailto") is None


@pytest.mark.asyncio
async def test_openalex_non_200_returns_empty():
    async with _mock_client(lambda req: httpx.Response(503)) as client:
        assert await search_openalex("anything", client=client) == []


@pytest.mark.asyncio
async def test_openalex_connect_error_returns_empty():
    def handler(request):
        raise httpx.ConnectError("connection refused")

    async with _mock_client(handler) as client:
        assert await search_openalex("anything", client=client) == []


def test_reconstruct_abstract_orders_words():
    idx = {"words": [1], "three": [2], "are": [3], "fun": [0]}
    assert reconstruct_abstract(idx) == "fun words three are"
    assert reconstruct_abstract(None) == ""
    assert reconstruct_abstract({}) == ""


# ---------------------------------------------------------------------------
# Semantic Scholar
# ---------------------------------------------------------------------------

S2_SAMPLE = {
    "data": [
        {
            "paperId": "abc123",
            "title": "Scaling Laws for Neural Language Models",
            "abstract": "We study empirical scaling laws.",
            "authors": [{"name": "Jared Kaplan"}, {"name": "Sam McCandlish"}],
            "year": 2020,
            "venue": "arXiv",
            "citationCount": 5000,
            "externalIds": {"DOI": "10.48550/arXiv.2001.08361", "PubMed": "32123456", "ArXiv": "2001.08361"},
            "openAccessPdf": {"url": "https://arxiv.org/pdf/2001.08361"},
            "url": "https://www.semanticscholar.org/paper/abc123",
        },
        {"paperId": "skipme", "title": "", "year": 2021},
    ]
}


@pytest.mark.asyncio
async def test_semantic_scholar_normalizes_external_ids():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=S2_SAMPLE)

    async with _mock_client(handler) as client:
        records = await search_semantic_scholar("scaling laws", limit=10, client=client)

    assert len(records) == 1  # empty-title entry dropped
    r = records[0]
    assert r.retrieval_source == "semantic_scholar"
    assert r.doi == "10.48550/arxiv.2001.08361"
    assert r.pmid == "32123456"
    assert r.arxiv_id == "2001.08361"
    assert r.authors == ["Jared Kaplan", "Sam McCandlish"]
    assert r.year == "2020"
    assert r.venue == "arXiv"
    assert r.pdf_url == "https://arxiv.org/pdf/2001.08361"
    assert r.source_url == "https://doi.org/10.48550/arxiv.2001.08361"
    assert r.citation_count == 5000


@pytest.mark.asyncio
async def test_semantic_scholar_retries_on_429_then_succeeds():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, json={"message": "Too Many Requests"})
        return httpx.Response(200, json=S2_SAMPLE)

    async with _mock_client(handler) as client:
        records = await search_semantic_scholar("scaling laws", client=client)
    assert calls["n"] == 2
    assert len(records) == 1


@pytest.mark.asyncio
async def test_semantic_scholar_gives_up_after_persistent_429():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(429, json={"message": "Too Many Requests"})

    async with _mock_client(handler) as client:
        records = await search_semantic_scholar("scaling laws", client=client)
    assert records == []
    assert calls["n"] == 3  # initial + 2 retries


@pytest.mark.asyncio
async def test_semantic_scholar_api_key_header(monkeypatch):
    monkeypatch.setenv("SEMANTIC_SCHOLAR_API_KEY", "sk-test-123")
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["key"] = request.headers.get("x-api-key")
        return httpx.Response(200, json={"data": []})

    async with _mock_client(handler) as client:
        await search_semantic_scholar("q", client=client)
    assert seen["key"] == "sk-test-123"


# ---------------------------------------------------------------------------
# Europe PMC
# ---------------------------------------------------------------------------

EPMC_SAMPLE = {
    "hitCount": 2,
    "resultList": {
        "result": [
            {
                "id": "34265844",
                "source": "MED",
                "pmid": "34265844",
                "doi": "10.1038/s41586-021-03819-2",
                "title": "Highly accurate protein structure prediction ",
                "authorString": "Jumper J, Hassabis D",
                "authorList": {"author": [{"fullName": "John Jumper"}, {"fullName": "Demis Hassabis"}]},
                "journalTitle": "Nature",
                "pubYear": "2021",
                "abstractText": "AlphaFold presents a structure predictor.",
                "citedByCount": 900,
                "fullTextUrlList": {"fullTextUrl": [
                    {"url": "https://europepmc.org/article/MED/34265844", "documentStyle": "html"},
                    {"url": "https://www.nature.com/articles/s41586-021-03819-2.pdf", "documentStyle": "pdf"},
                ]},
            },
            {
                "id": "PPR123",
                "source": "PPR",
                "title": "A preprint without structured authors",
                "authorString": "Jane Roe, Richard Roe",
                "firstPublicationDate": "2024-03-01",
                "citedByCount": 2,
            },
        ]
    },
}


@pytest.mark.asyncio
async def test_europe_pmc_normalizes_core_results():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(dict(request.url.params))
        return httpx.Response(200, json=EPMC_SAMPLE)

    async with _mock_client(handler) as client:
        records = await search_europe_pmc("alphafold", limit=5, client=client)

    assert seen["resultType"] == "core"
    assert seen["format"] == "json"
    assert len(records) == 2

    med = records[0]
    assert med.retrieval_source == "europe_pmc"
    assert med.title == "Highly accurate protein structure prediction"
    # Structured authorList preferred over authorString initials form.
    assert med.authors == ["John Jumper", "Demis Hassabis"]
    assert med.pmid == "34265844"
    assert med.doi == "10.1038/s41586-021-03819-2"
    assert med.year == "2021"
    assert med.venue == "Nature"
    assert med.citation_count == 900
    assert med.pdf_url == "https://www.nature.com/articles/s41586-021-03819-2.pdf"
    assert med.source_url == "https://europepmc.org/article/MED/34265844"

    preprint = records[1]
    assert preprint.pmid is None  # PPR record has no PMID
    assert preprint.authors == ["Jane Roe", "Richard Roe"]  # authorString fallback
    assert preprint.year == "2024"  # derived from firstPublicationDate
    assert preprint.source_url == "https://europepmc.org/article/PPR/PPR123"


@pytest.mark.asyncio
async def test_europe_pmc_error_returns_empty():
    async with _mock_client(lambda req: httpx.Response(500)) as client:
        assert await search_europe_pmc("q", client=client) == []


# ---------------------------------------------------------------------------
# DOAJ
# ---------------------------------------------------------------------------

DOAJ_SAMPLE = {
    "total": 42,
    "results": [
        {
            "last_updated": "2025-08-20T01:49:50Z",
            "bibjson": {
                "identifier": [
                    {"id": "10.46481/jnsps.2025.2273", "type": "doi"},
                    {"id": "2714-2817", "type": "pissn"},
                ],
                "journal": {"title": "Journal of Nigerian Society of Physical Sciences", "volume": "7"},
                "month": "February",
                "keywords": ["Algorithm"],
                "year": "2025",
                "subject": [{"term": "Physics"}],
                "author": [
                    {"affiliation": "Uni Wukari", "name": "Philemon Uten Emmoh"},
                    {"name": "Timothy Moses"},
                ],
                "link": [{"content_type": "HTML", "type": "fulltext", "url": "https://journal.example/view/2273"}],
                "abstract": " Feature selection  is vital. ",
                "title": "Machine Learning for Feature Selection",
            },
        }
    ],
}


@pytest.mark.asyncio
async def test_doaj_normalizes_bibjson():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=DOAJ_SAMPLE)

    async with _mock_client(handler) as client:
        records = await search_doaj("feature selection", limit=5, client=client)

    assert len(records) == 1
    r = records[0]
    assert r.retrieval_source == "doaj"
    assert r.doi == "10.46481/jnsps.2025.2273"  # pulled from typed identifier list
    assert r.title == "Machine Learning for Feature Selection"
    assert r.authors == ["Philemon Uten Emmoh", "Timothy Moses"]
    assert r.year == "2025"
    assert r.venue == "Journal of Nigerian Society of Physical Sciences"
    assert r.abstract == "Feature selection is vital."
    assert r.source_url == "https://journal.example/view/2273"
    assert r.citation_count == 0  # DOAJ exposes no citation counts


@pytest.mark.asyncio
async def test_doaj_pdf_link_becomes_pdf_url():
    sample = {
        "results": [{
            "bibjson": {
                "title": "PDF-only record",
                "link": [{"content_type": "PDF", "type": "fulltext", "url": "https://x.org/a.pdf"}],
            }
        }]
    }

    async with _mock_client(lambda req: httpx.Response(200, json=sample)) as client:
        records = await search_doaj("pdf", client=client)
    assert records[0].source_url == "https://x.org/a.pdf"
    assert records[0].pdf_url == "https://x.org/a.pdf"


# ---------------------------------------------------------------------------
# DataCite
# ---------------------------------------------------------------------------

DATACITE_SAMPLE = {
    "data": [
        {
            "id": "10.5281/zenodo.19417200",
            "attributes": {
                "doi": "10.5281/zenodo.19417200",
                "titles": [
                    {"title": "Alternate Title", "titleType": "AlternativeTitle"},
                    {"title": "Machine Learning In Digital Forensics"},
                ],
                "creators": [
                    {"name": "Dilshan Perera", "familyName": "Dilshan Perera", "nameType": "Personal"},
                    {"givenName": "Ada", "familyName": "Lovelace"},  # no 'name' key
                ],
                "publisher": "Zenodo",
                "container": {"title": None},
                "publicationYear": 2025,
                "dates": [{"date": "2025-01-01", "dateType": "Issued"}],
                "descriptions": [
                    {"description": "Methods appendix", "descriptionType": "Methods"},
                    {"description": "A study of DFIR workflows.", "descriptionType": "Abstract"},
                ],
                "types": {"resourceTypeGeneral": "JournalArticle"},
                "citationCount": 7,
                "viewCount": 999,   # usage metrics deliberately NOT citation_count
                "url": "https://zenodo.org/doi/10.5281/zenodo.19417200",
            },
        },
        {"attributes": {"titles": [], "doi": "10.9999/no-title"}},
    ]
}


@pytest.mark.asyncio
async def test_datacite_normalizes_jsonapi_attributes():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=DATACITE_SAMPLE)

    async with _mock_client(handler) as client:
        records = await search_datacite("digital forensics", limit=5, client=client)

    assert len(records) == 1  # title-less record skipped
    r = records[0]
    assert r.retrieval_source == "datacite"
    assert r.doi == "10.5281/zenodo.19417200"
    # Plain title preferred over typed AlternativeTitle
    assert r.title == "Machine Learning In Digital Forensics"
    assert r.authors == ["Dilshan Perera", "Ada Lovelace"]  # name composed from given/family
    assert r.year == "2025"
    assert r.venue == "Zenodo"  # null container.title falls back to publisher
    assert r.abstract == "A study of DFIR workflows."  # Methods description excluded
    assert r.source_url == "https://zenodo.org/doi/10.5281/zenodo.19417200"
    assert r.citation_count == 7


@pytest.mark.asyncio
async def test_datacite_year_falls_back_to_issued_date():
    sample = {
        "data": [{
            "attributes": {
                "titles": [{"title": "No pubYear record"}],
                "dates": [{"date": "2019-06-12", "dateType": "Issued"}],
                "creators": [],
            }
        }]
    }

    async with _mock_client(lambda req: httpx.Response(200, json=sample)) as client:
        records = await search_datacite("q", client=client)
    assert records[0].year == "2019"


# ---------------------------------------------------------------------------
# ORCID (public API helper)
# ---------------------------------------------------------------------------

ORCID_EXPANDED = {
    "num-found": 2,
    "expanded-result": [
        {
            "orcid-id": "0000-0002-1825-0097",
            "given-names": "Josiah",
            "family-names": "Carberry",
            "credit-name": None,
            "institution-name": ["Reed College"],
        },
        {"orcid-id": "", "given-names": "Missing", "family-names": "Id"},
    ],
}


@pytest.mark.asyncio
async def test_resolve_orcid_authors_parses_expanded_result():
    async with _mock_client(lambda req: httpx.Response(200, json=ORCID_EXPANDED)) as client:
        identities = await resolve_orcid_authors("Carberry", client=client)

    assert len(identities) == 1  # entry without orcid-id dropped
    i = identities[0]
    assert i.orcid_id == "0000-0002-1825-0097"
    assert i.given_names == "Josiah"
    assert i.family_names == "Carberry"
    assert i.institutions == ["Reed College"]
    assert i.display_name == "Josiah Carberry"


def test_confident_match_rules():
    identity = AuthorIdentity(orcid_id="0000-0002-1825-0097", given_names="Josiah", family_names="Carberry")
    assert _confident_match("Josiah Carberry", identity)
    assert _confident_match("Prof. Josiah Q. Carberry", identity)  # extra tokens allowed
    assert not _confident_match("J. Carberry", identity)  # surname-only query too weak
    assert not _confident_match("Someone Else", identity)


@pytest.mark.asyncio
async def test_enrich_record_authors_canonicalizes_confident_match():
    # Author string carries a middle initial; ORCID returns the canonical
    # two-token form. Token-subset rule makes this a confident match.
    record = PaperRecord(paper_id="x", title="T", authors=["Josiah Q. Carberry", "Mystery Writer"])

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=ORCID_EXPANDED)

    async with _mock_client(handler) as client:
        enriched = await enrich_record_authors(record, max_authors=2, client=client)

    assert enriched.authors[0] == "Josiah Carberry"
    assert enriched.authors[1] == "Mystery Writer"  # beyond-confidence or unmatched names untouched
    assert record.authors == ["Josiah Q. Carberry", "Mystery Writer"]  # input never mutated


@pytest.mark.asyncio
async def test_enrich_record_authors_no_network_change_on_empty():
    record = PaperRecord(paper_id="x", title="T", authors=["Josiah Carberry"])

    async with _mock_client(lambda req: httpx.Response(200, json={"expanded-result": []})) as client:
        enriched = await enrich_record_authors(record, client=client)

    # No confident match in the empty ORCID response -> authors unchanged.
    assert enriched.authors == ["Josiah Carberry"]


@pytest.mark.asyncio
async def test_fetch_orcid_profile_validates_format():
    assert await fetch_orcid_profile("not-an-orcid") is None


@pytest.mark.asyncio
async def test_fetch_orcid_profile_parses_person():
    sample = {
        "orcid-identifier": {"path": "0000-0002-1825-0097"},
        "person": {
            "name": {
                "given-names": {"value": "Josiah"},
                "family-names": {"value": "Carberry"},
                "credit-name": {"value": "J. Carberry"},
            }
        },
    }

    async with _mock_client(lambda req: httpx.Response(200, json=sample)) as client:
        profile = await fetch_orcid_profile("0000-0002-1825-0097", client=client)

    assert profile.display_name == "J. Carberry"  # credit-name preferred


# ---------------------------------------------------------------------------
# Fan-out failure isolation (academic_search.search_academic_papers_structured)
# ---------------------------------------------------------------------------

def _paper(source: str, title: str) -> PaperRecord:
    return PaperRecord(paper_id=f"id-{source}-{title[:8]}", title=title, retrieval_source=source)


@pytest.mark.asyncio
async def test_fanout_survives_raising_providers(monkeypatch):
    """Dead providers must fail independently; healthy ones still return results."""
    import backend.app.tools.academic_search as mod

    async def boom_openalex(q, limit=15):
        raise RuntimeError("openalex exploded")

    async def dead_s2(q, limit=15):
        return []  # provider up but yields nothing

    async def timeout_epmc(q, limit=15):
        raise TimeoutError("europe pmc timed out")

    async def malformed_doaj(q, limit=15):
        raise ValueError("malformed response body")

    async def ok_datacite(q, limit=15):
        return [_paper("datacite", "DataCite Dataset Result")]

    async def ok_crossref(q, limit=15):
        return [_paper("crossref", "Crossref Journal Result")]

    async def dead_pubmed(q, limit=15):
        raise ConnectionError("ncbi unreachable")

    monkeypatch.setattr(mod, "search_openalex", boom_openalex)
    monkeypatch.setattr(mod, "search_semantic_scholar", dead_s2)
    monkeypatch.setattr(mod, "fetch_arxiv_papers", lambda c, kw, max_results=50: asyncio.sleep(0, result=[]))
    monkeypatch.setattr(mod, "search_europe_pmc", timeout_epmc)
    monkeypatch.setattr(mod, "search_doaj", malformed_doaj)
    monkeypatch.setattr(mod, "search_datacite", ok_datacite)
    monkeypatch.setattr(mod, "search_crossref", ok_crossref)
    monkeypatch.setattr(mod, "search_pubmed", dead_pubmed)
    # Keep the low-yield Tavily web fallback from firing live web searches.
    monkeypatch.setattr(mod, "fetch_tavily_web_papers", lambda kw, max_results=5: asyncio.sleep(0, result=[]))

    records, tracker = await mod.search_academic_papers_structured(["single keyword"])

    titles = {r.title for r in records}
    assert "DataCite Dataset Result" in titles
    assert "Crossref Journal Result" in titles
    assert len(records) == 2
    assert tracker.records_by_source["datacite"] == 1
    assert tracker.records_by_source["crossref"] == 1


@pytest.mark.asyncio
async def test_fanout_dedupes_across_sources(monkeypatch):
    """Same DOI from two providers collapses into one retained record."""
    import backend.app.tools.academic_search as mod

    shared = [
        PaperRecord(
            paper_id="dup1",
            doi="10.1000/shared",
            title="Shared Discovery",
            retrieval_source="openalex",
        ),
        PaperRecord(
            paper_id="dup1b",
            doi="10.1000/shared",
            title="Shared Discovery",
            retrieval_source="europe_pmc",
        ),
    ]

    async def ok_openalex(q, limit=15):
        return [shared[0]]

    async def ok_epmc(q, limit=15):
        return [shared[1]]

    async def empty(q, limit=15):
        return []

    monkeypatch.setattr(mod, "search_openalex", ok_openalex)
    monkeypatch.setattr(mod, "search_semantic_scholar", empty)
    monkeypatch.setattr(mod, "fetch_arxiv_papers", lambda c, kw, max_results=50: asyncio.sleep(0, result=[]))
    monkeypatch.setattr(mod, "search_europe_pmc", ok_epmc)
    monkeypatch.setattr(mod, "search_doaj", empty)
    monkeypatch.setattr(mod, "search_datacite", empty)
    monkeypatch.setattr(mod, "search_crossref", empty)
    monkeypatch.setattr(mod, "search_pubmed", empty)
    monkeypatch.setattr(mod, "fetch_tavily_web_papers", lambda kw, max_results=5: asyncio.sleep(0, result=[]))

    records, tracker = await mod.search_academic_papers_structured(["kw"])
    assert len(records) == 1
    assert tracker.duplicates_removed == 1
    # First-seen source wins retention.
    assert records[0].retrieval_source == "europe_pmc"  # SOURCE_PRIORITY ranks europe_pmc above openalex
