"""Phase 2 Retrieval & Citation Graph Agents for Research Mode."""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from backend.app.models.evidence import PaperRecord, PRISMATracker
from backend.app.tools.academic_search import (
    _normalize_doi,
    search_academic_papers_structured,
)
from backend.app.tools.crossref_search import search_crossref
from backend.app.tools.opencitations_search import expand_citation_graph

logger = logging.getLogger(__name__)


async def paper_fetcher_agent(state: dict[str, Any]) -> dict[str, Any]:
    """Agent 5: Orchestrates concurrent multi-source retrieval across OpenAlex, S2, ArXiv, Crossref, PubMed."""
    keywords = state.get("keywords", [])
    logger.info(f"paper_fetcher_agent executing with {len(keywords)} search keywords...")
    
    records, tracker = await search_academic_papers_structured(keywords)
    dict_records = [r.model_dump() for r in records]
    
    return {
        "paper_records": dict_records,
        "raw_papers": dict_records,  # Backward compatibility
        "prisma_tracker": tracker.model_dump(),
        "corpus_stats": {
            "retrieved": tracker.records_identified,
            "after_dedup": tracker.records_after_dedup,
            "screened": 0,
            "included": 0,
        },
        "status": "fetching_papers"
    }


async def _resolve_dois_via_crossref(dois: list[str]) -> list[PaperRecord]:
    """Resolve DOIs to real Crossref metadata, dropping any that cannot be resolved.

    Keeps only records whose returned DOI matches the requested DOI so the
    citation-graph corpus never carries synthetic titles or unresolved stubs.
    """
    if not dois:
        return []

    results = await asyncio.gather(
        *[search_crossref(d, limit=3) for d in dois],
        return_exceptions=True
    )

    resolved: list[PaperRecord] = []
    for requested_doi, res in zip(dois, results):
        if not isinstance(res, list):
            continue
        wanted = _normalize_doi(requested_doi)
        for record in res:
            if not isinstance(record, PaperRecord):
                continue
            if _normalize_doi(record.doi) != wanted:
                continue
            resolved.append(record.model_copy(update={"retrieval_source": "opencitations"}))
            break

    logger.info(f"citation_expander_agent resolved {len(resolved)}/{len(dois)} discovered DOIs via Crossref.")
    return resolved


async def citation_expander_agent(state: dict[str, Any]) -> dict[str, Any]:
    """Agent 6: Traverses 1-hop forward/backward citation graph for seed papers."""
    paper_dicts = state.get("paper_records") or []
    if not paper_dicts:
        return {}

    seed_dois = [p.get("doi") for p in paper_dicts if p.get("doi")]
    if not seed_dois:
        return {}

    try:
        discovered_dois = await expand_citation_graph(seed_dois, max_expansion=15)
        logger.info(f"citation_expander_agent discovered {len(discovered_dois)} connected citation graph DOIs")
        existing_dois = {str(p.get("doi")).lower().strip() for p in paper_dicts if p.get("doi")}
        new_dois = [d for d in discovered_dois if d.lower().strip() not in existing_dois]
        if new_dois:
            # Resolve real bibliographic metadata before admitting a DOI into the
            # citable corpus. Unresolved DOIs are dropped rather than inserted as
            # placeholder records with a synthetic title.
            resolved = await _resolve_dois_via_crossref(new_dois[:10])
            if not resolved:
                logger.info("citation_expander_agent resolved no DOI metadata; corpus unchanged.")
                return {}
            new_records = [r.model_dump() for r in resolved]
            all_records = paper_dicts + new_records

            # Keep PRISMA conservation true across corpus growth: a paper added
            # by citation expansion is one more identified and one more
            # deduplicated record, never a silent mismatch at screening.
            raw_tracker = state.get("prisma_tracker") or {}
            updates: dict[str, Any] = {
                "paper_records": all_records,
                "raw_papers": all_records,
            }
            if raw_tracker:
                tracker = PRISMATracker(**raw_tracker)
                added = len(new_records)
                tracker.records_identified += added
                tracker.records_after_dedup += added
                tracker.records_by_source["opencitations"] = (
                    tracker.records_by_source.get("opencitations", 0) + added
                )
                updates["prisma_tracker"] = tracker.model_dump()
                updates["corpus_stats"] = {
                    "retrieved": tracker.records_identified,
                    "after_dedup": tracker.records_after_dedup,
                    "screened": 0,
                    "included": 0,
                }
            return updates
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Citation graph expansion encountered non-critical error: {e}")

    return {}


async def metadata_validator_agent(state: dict[str, Any]) -> dict[str, Any]:
    """Agent 7: Normalizes paper metadata; optionally canonicalizes author names via ORCID.

    Collapses whitespace in titles (dropping a trailing period) and strips blank
    author entries. When ORCID_AUTHOR_ENRICHMENT=1 (off by default: one ORCID
    lookup per enriched author would otherwise add latency to every run), the first
    ORCID_MAX_AUTHORS authors (default 2) are replaced by their canonical ORCID
    display name on a confident match, and the matched iD is stored in
    author_orcids.

    It does not validate venues, clean or resolve DOIs, or cross-check metadata
    against a registry: DOIs and venues pass through unchanged.
    """
    paper_dicts = state.get("paper_records") or []
    cleaned_records: list[dict[str, Any]] = []

    orcid_enrichment = os.getenv("ORCID_AUTHOR_ENRICHMENT", "").strip().lower() in {"1", "true", "yes"}
    if orcid_enrichment:
        from backend.app.tools.orcid_search import (
            enrich_records_authors,
            orcid_max_authors_from_env,
        )

    for p in paper_dicts:
        item = dict(p)
        # Clean title whitespace
        if item.get("title"):
            item["title"] = " ".join(item["title"].split()).rstrip(".")
        # Clean authors
        authors = item.get("authors") or []
        if isinstance(authors, list):
            item["authors"] = [a.strip() for a in authors if a and str(a).strip()]
        cleaned_records.append(item)

    if orcid_enrichment:
        try:
            records = [PaperRecord.from_dict(item) for item in cleaned_records]
            enriched = await enrich_records_authors(records, max_authors=orcid_max_authors_from_env())
            cleaned_records = [r.model_dump() for r in enriched]
            logger.info("ORCID author enrichment applied to corpus.")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"ORCID author enrichment skipped (non-fatal): {e}")

    return {
        "paper_records": cleaned_records,
        "raw_papers": cleaned_records
    }
