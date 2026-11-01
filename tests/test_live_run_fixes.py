"""Regression tests for the 2026-10-31 CiteProof live-run issues.

Covers the deterministic fixes: blank scope items, RCT-only design screening,
title-only evidence, and the full-text counter.
"""

import asyncio
import sys
from pathlib import Path

root_dir = Path(__file__).resolve().parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from backend.app.agents.research_mode import extraction, screening
from backend.app.agents.research_mode.planning import clean_scope_items
from backend.app.models.evidence import PaperRecord, SearchProtocol
from backend.app.tools.academic_search import screen_papers_structured


def _paper(title: str, abstract: str = "", **extra) -> PaperRecord:
    return PaperRecord.from_dict({
        "title": title,
        "abstract": abstract,
        "retrieval_source": extra.pop("retrieval_source", "openalex"),
        **extra,
    })


def test_clean_scope_items_drops_blank_and_object_items():
    items = ["", "   ", {"text": "Measure remission at 12 weeks"}, {"objective": ""}, "Second objective"]
    assert clean_scope_items(items, ["fallback"]) == [
        "Measure remission at 12 weeks",
        "Second objective",
    ]
    assert clean_scope_items(["", {}], ["fallback"]) == ["fallback"]
    assert clean_scope_items(None, ["fallback"]) == ["fallback"]


def test_rct_only_protocol_excludes_reviews_and_keeps_trials():
    protocol = SearchProtocol(
        population="adults with insomnia",
        intervention="CBT-I",
        inclusion_criteria=["Randomized controlled trials (RCTs) only", "Adults"],
    )
    papers = [
        _paper("CBT-I versus pharmacotherapy: a systematic review and network meta-analysis"),
        _paper("Cognitive behavioural therapy for insomnia: a randomized controlled trial"),
        _paper("Sleep restriction versus hypnotics: a meta-analysis of trials"),
    ]

    screened, tracker = asyncio.run(
        screen_papers_structured(papers, "CBT-I vs drugs", ["efficacy"], protocol=protocol)
    )

    by_title = {p.title: p for p in screened}
    assert by_title[papers[0].title].screening_status == "excluded"
    assert by_title[papers[2].title].screening_status == "excluded"
    assert by_title[papers[1].title].screening_status == "included"
    assert tracker.studies_included == 1
    assert tracker.exclusion_reasons["Study design does not match protocol (RCTs only)"] == 2


def test_non_rct_protocol_does_not_apply_design_gate():
    papers = [_paper("A systematic review of CBT-I")]
    screened, _ = asyncio.run(screen_papers_structured(papers, "CBT-I", ["efficacy"]))
    assert screened[0].screening_status == "included"


def test_title_only_paper_never_reaches_the_extraction_model(monkeypatch):
    calls = []

    def _fail(*args, **kwargs):
        calls.append(args)
        raise AssertionError("LLM must not be called for a title-only paper")

    monkeypatch.setattr(extraction, "get_llm_for", lambda *a, **k: None)
    monkeypatch.setattr(extraction, "_safe_invoke_llm", _fail)

    title_only = _paper("Cognitive Behavioral Therapy, Singly and Combined With Medication", "")
    result = asyncio.run(extraction.evidence_extractor_agent({"paper_records": [title_only.model_dump()]}))

    assert calls == []
    assert result["evidence_records"] == []
    assert result["evidence_spans"] == []


def test_model_refusal_is_not_stored_as_evidence(monkeypatch):
    async def _refusal(*args, **kwargs):
        return (
            '[{"claim_summary": "Unable to extract specific claims because the '
            'paper has only a title", "exact_quote": "Title only"}]'
        )

    monkeypatch.setattr(extraction, "get_llm_for", lambda *a, **k: None)
    monkeypatch.setattr(extraction, "_safe_invoke_llm", _refusal)

    paper = _paper("A trial of CBT-I", "Adults with insomnia were randomized to CBT-I or zolpidem.")
    result = asyncio.run(extraction.evidence_extractor_agent({"paper_records": [paper.model_dump()]}))

    assert all(
        "unable to extract" not in r["claim_summary"].lower()
        for r in result["evidence_records"]
    )


def test_fulltext_counter_is_recorded_without_losing_corpus_stats(monkeypatch):
    async def _fetch(papers):
        enriched = [dict(p) for p in papers]
        enriched[0]["fulltext_excerpt"] = "Full text body"
        return enriched

    monkeypatch.setattr(screening, "fetch_fulltexts", _fetch)
    papers = [
        {"title": "A", "screening_status": "included"},
        {"title": "B", "screening_status": "included"},
    ]

    result = asyncio.run(screening.fulltext_eligibility_agent({
        "paper_records": papers,
        "corpus_stats": {"retrieved": 5, "screened": 2, "included": 2},
    }))

    assert result["corpus_stats"]["fulltext_fetched"] == 1
    assert result["corpus_stats"]["retrieved"] == 5
    assert result["corpus_stats"]["included"] == 2


def test_datacite_versus_primary_publisher_deduplication():
    from backend.app.tools.academic_search import SOURCE_PRIORITY, _normalize_title

    def _dedupe_priority(p: PaperRecord) -> tuple[int, int]:
        has_abs = 0 if (p.abstract and p.abstract.strip()) else 1
        src_rank = SOURCE_PRIORITY.get(str(p.retrieval_source or "").lower(), 10)
        return (has_abs, src_rank)

    p_datacite = _paper(
        title="Cognitive Behavioral Therapy for Chronic Insomnia: A Randomized Trial",
        abstract="",
        doi="10.48620/7381",
        retrieval_source="datacite"
    )
    p_pubmed = _paper(
        title="Cognitive Behavioral Therapy for Chronic Insomnia: A Randomized Trial",
        abstract="Patients with chronic insomnia were randomized to CBT-I or control.",
        doi="10.1001/archinternmed.2010.315",
        retrieval_source="pubmed"
    )

    # When both are present in the candidate stream, deduplication must keep pubmed
    combined = [p_datacite, p_pubmed]
    sorted_papers = sorted(combined, key=_dedupe_priority)

    seen_titles = set()
    deduped = []
    for p in sorted_papers:
        nt = _normalize_title(p.title)
        if nt in seen_titles:
            continue
        seen_titles.add(nt)
        deduped.append(p)

    assert len(deduped) == 1
    assert deduped[0].retrieval_source == "pubmed"
    assert deduped[0].abstract != ""


def test_research_design_template_selection_clinical(monkeypatch):
    from backend.app.agents.research_mode import writing

    captured_prompt = []

    async def _mock_invoke(llm, messages):
        prompt_text = messages[0].content
        captured_prompt.append(prompt_text)
        return "### 1. Study Design & Protocol\nClinical trial methodology."

    monkeypatch.setattr(writing, "get_llm_for", lambda *a, **k: None)
    monkeypatch.setattr(writing, "_safe_invoke_llm", _mock_invoke)

    state = {
        "problem_statement": "CBT-I vs pharmacotherapy for adult insomnia (RCT systematic review)",
        "hypotheses": ["H1: CBT-I achieves higher remission than zolpidem"],
        "search_protocol": {"intervention": "CBT-I", "population": "Adult insomnia"},
    }

    res = asyncio.run(writing.research_design_agent(state))
    assert len(captured_prompt) == 1
    assert "Study Design & Methodological Framework" in captured_prompt[0]
    assert "PICO Framework" in captured_prompt[0]
    assert "Experimental Architecture & Target Testbeds" not in captured_prompt[0]
