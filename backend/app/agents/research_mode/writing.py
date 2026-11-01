"""Phase 5 Section-Specific Writers for Research Mode.

Consumes structured EvidenceRecord slices and PaperRecord collections,
enforcing strict evidence grounding and APA 7th edition citations.
"""

from __future__ import annotations

import html
import logging
import os
import re
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage

from backend.app.agents.research_mode._common import (
    EVIDENCE_BASIS_NOTE,
    _safe_invoke_llm,
    _strip_preamble,
    get_llm_for,
    included_papers,
    normalize_section_body,
    strip_abstract_heading,
    write_required_section,
)
from backend.app.models.evidence import (
    PRISMATracker,
    ReviewClaim,
    make_claim_id,
)
from backend.app.tools.academic_search import format_apa
from backend.app.tools.figures import render_evidence_table, render_prisma_diagram

logger = logging.getLogger(__name__)

# Papers are cited in the rendered prose only through these author-year strings;
# the structured [EV:<id>] markers stay machine-only and never reach a reader.
_MARKER_RUN_RE = re.compile(r"(?:\[\s*ev:\s*[A-Za-z0-9_.\-]+?\s*\]\s*)+", re.IGNORECASE)
_NAME_PARTICLES = {"da", "de", "del", "della", "der", "di", "dos", "du", "la", "le", "van", "von"}
_FINDING_WORDS_RE = re.compile(
    r"\b(?:support(?:ed|s)?|refut(?:e|ed|es)|partial(?:ly)?|consistent|contradict(?:ed|s)?|"
    r"indicat(?:e|ed|es)|suggest(?:ed|s)?|found|show(?:ed|s)?|evidence|result(?:ed|s)?|"
    r"confirm(?:ed|s)?|reject(?:ed|s)?|accept(?:ed|s)?|align(?:ed|s)?|outperform(?:ed|s)?|"
    r"increase(?:d|s)?|decrease(?:d|s)?|improv(?:e|ed|es)|reduc(?:e|ed|es))\b",
    re.IGNORECASE,
)
_HYPOTHESIS_LINE_RE = re.compile(
    r"^\s*(?:[-*]\s*)?(?:\*\*|__)?\s*(?:H[1-9]\d?|Hypothesis\s+\d+)\s*(?:\*\*|__)?\s*[:：.\-–—]\s*(.+)$",
    re.IGNORECASE,
)
_META_ANALYSIS_RE = re.compile(
    r"\b(?:systematic\s+review\s+and\s+)?meta[\s-]?analy(?:sis|tic|ses)\b",
    re.IGNORECASE,
)

# Inline structured marker embedded by writers after a sentence grounded in a
# specific EvidenceRecord: [EV:<evidence_id>]. claims_linker_node converts
# these into the machine-readable ReviewClaim manifest and strips them from
# the rendered prose.
EV_MARKER_RE = re.compile(r"\[\s*ev:\s*([A-Za-z0-9_.\-]+?)\s*\]", re.IGNORECASE)

# Prose sections scanned for evidence markers and rendered APA citations.
LINKED_PROSE_SECTIONS = (
    "introduction",
    "literature_review",
    "results",
    "discussion",
    "implications",
)


def _is_quantitative_sentence(text: str) -> bool:
    """Mirror of the validator's numeric-claim heuristic, kept dependency-light."""
    return bool(re.search(
        r"(?:\b\d+\.?\d*|\d+)\s*(?:%|percent\b|BLEU\b|F1\b|accuracy\b|points\b|ms\b|seconds\b|x\b|fold\b)",
        text,
        re.IGNORECASE,
    ))


def _strip_ev_markers(text: str) -> str:
    """Remove provenance markers before feeding prose into downstream prompts.

    Sections that do not consume the structured evidence base (abstract,
    conclusion, future scope) would otherwise copy [EV:...] tags verbatim into
    rendered text that claims_linker_node never parses.
    """
    if not text:
        return text or ""
    cleaned = EV_MARKER_RE.sub("", text)
    return re.sub(r"[ \t]{2,}", " ", cleaned)


def _paper_surname(author: str) -> str:
    """Extract a family name from 'Surname, I.' or 'Given Family' forms."""
    value = str(author or "").strip()
    if not value:
        return ""
    if "," in value:
        return value.split(",", 1)[0].strip()
    tokens = value.split()
    if not tokens:
        return ""
    start = len(tokens) - 1
    while start > 0 and tokens[start - 1].casefold().strip(".") in _NAME_PARTICLES:
        start -= 1
    return " ".join(tokens[start:])


def _in_text_citation(paper: dict[str, Any] | None) -> str:
    """Build an APA 7 in-text citation label for one PaperRecord dictionary.

    Returns an empty string when the record has no resolvable year: an
    unyear-ed marker must not create a citation the validator cannot match.
    """
    if not isinstance(paper, dict) or not paper:
        return ""

    year_match = re.search(r"(?:19|20)\d{2}[a-z]?", str(paper.get("year") or ""))
    if not year_match:
        return ""
    year = year_match.group(0)

    surnames = [
        _paper_surname(author)
        for author in (paper.get("authors") or [])
        if _paper_surname(author)
    ]
    if not surnames:
        title_words = re.findall(r"[A-Za-z0-9]+", str(paper.get("title") or ""))
        if len(title_words) < 2:
            return ""
        label = " ".join(title_words[:4])
    elif len(surnames) == 1:
        label = surnames[0]
    elif len(surnames) == 2:
        label = f"{surnames[0]} & {surnames[1]}"
    else:
        label = f"{surnames[0]} et al."

    return f"{label}, {year}"


def _paper_for_evidence(
    evidence: dict[str, Any],
    papers_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    paper_id = str(evidence.get("paper_id") or "")
    if not paper_id:
        evidence_id = str(evidence.get("evidence_id") or "")
        paper_id = evidence_id.rsplit("_ev", 1)[0] if "_ev" in evidence_id else ""
    return papers_by_id.get(paper_id) if paper_id else None


def _render_markers_as_citations(
    text: str,
    evidence_by_id: dict[str, dict[str, Any]],
    papers_by_id: dict[str, dict[str, Any]],
) -> str:
    """Replace each contiguous [EV:...] run with visible APA author-year text.

    Unresolvable markers are dropped: a fabricated reference must never reach
    the rendered paper. Existing author-year citations directly before a marker
    suppress a duplicate insertion.
    """
    if not text:
        return ""

    def _replace(match: re.Match[str]) -> str:
        raw_run = match.group(0)
        # The run regex swallows trailing whitespace; give it back so line and
        # paragraph breaks after a marker survive.
        trailing = raw_run[len(raw_run.rstrip()):]
        ids = []
        for evidence_id in EV_MARKER_RE.findall(raw_run):
            if evidence_id not in ids:
                ids.append(evidence_id)

        preceding = text[max(0, match.start() - 180):match.start()]
        citations: list[str] = []
        for evidence_id in ids:
            evidence = evidence_by_id.get(evidence_id)
            if not evidence:
                continue
            citation = _in_text_citation(_paper_for_evidence(evidence, papers_by_id))
            if not citation:
                continue
            surname, _, year = citation.partition(", ")
            if surname and surname.split()[0] in preceding and year in preceding:
                continue
            if citation not in citations:
                citations.append(citation)

        if not citations:
            return trailing
        return f" ({'; '.join(citations)})" + trailing

    return _MARKER_RUN_RE.sub(_replace, text)


def _papers_cited_by_label(text: str, papers_by_id: dict[str, dict[str, Any]]) -> set[str]:
    """Paper ids whose author-year citation appears in prose without a marker.

    Writers may cite an included paper that has no evidence record, so it can
    carry no [EV:] marker; it still needs its reference entry.
    """
    cited: set[str] = set()
    if not text:
        return cited
    for paper_id, paper in papers_by_id.items():
        label = _in_text_citation(paper)
        if not label:
            continue
        surname, _, year = label.rpartition(", ")
        first = surname.split()[0] if surname else ""
        if not first or not year:
            continue
        # Matches "(Smith et al., 2020)" and narrative "Smith et al. (2020)".
        pattern = rf"\b{re.escape(first)}\b(?:[^.\n]|\bal\.){{0,40}}?\b{re.escape(year)}\b"
        if re.search(pattern, text):
            cited.add(paper_id)
    return cited


def _citation_labels(papers: list[dict[str, Any]]) -> str:
    """Canonical "(Author, Year)" labels for included papers, one per line.

    Writers that may cite by author-year get only these labels, so a manual
    citation always matches a paper the reference list can keep.
    """
    labels: list[str] = []
    for paper in included_papers(papers):
        label = _in_text_citation(paper)
        if label and f"- ({label})" not in labels:
            labels.append(f"- ({label})")
    return "\n".join(labels) or "- (none: cite only with [EV:<Evidence ID>] markers)"


def _clean_citation_punctuation(text: str) -> str:
    """Remove spaces and duplicated punctuation introduced by marker removal."""
    if not text:
        return ""
    cleaned = re.sub(r"[ \t]+([,.;:?!])", r"\1", text)
    cleaned = re.sub(r"([,;:])\s*([.;])", r"\2", cleaned)
    cleaned = re.sub(r"(?<!\.)\.\.(?!\.)", ".", cleaned)
    cleaned = re.sub(r"\?\s*\.", "?", cleaned)
    cleaned = re.sub(r"!\s*\.", "!", cleaned)
    cleaned = re.sub(r"\(\s+", "(", cleaned)
    cleaned = re.sub(r"\s+\)", ")", cleaned)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    return cleaned.strip()


def _strip_hypothesis_restatements(text: str) -> str:
    """Drop standalone lines that only restate an H1..H5 hypothesis.

    A line that names a hypothesis and then reports a finding is kept verbatim;
    only a bare restatement (or a bolded hypothesis label) is removed. This
    keeps the Results section an evidence synthesis rather than a copy of the
    hypotheses list.
    """
    if not text:
        return ""

    kept: list[str] = []
    for line in text.splitlines():
        match = _HYPOTHESIS_LINE_RE.match(line)
        if match and not _FINDING_WORDS_RE.search(match.group(1)):
            logger.info("Dropped hypothesis restatement from Results: %s", line.strip()[:80])
            continue
        kept.append(line)
    return "\n".join(kept).strip()


def _sanitize_title(title: str, fallback: str) -> str:
    """Remove method claims the pipeline did not perform (notably meta-analysis)."""
    cleaned = html.unescape(str(title or "")).strip().strip('"').strip("'").rstrip(".")
    if not cleaned:
        return fallback

    cleaned = re.sub(
        r"\bsystematic\s+review\s+and\s+meta[\s-]?analy(?:sis|tic|ses)\b",
        "Systematic Review",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"\bMeta[\s-]?analytic\s+Review\b", "Systematic Review", cleaned, flags=re.IGNORECASE)
    cleaned = _META_ANALYSIS_RE.sub("Systematic Review", cleaned)
    cleaned = re.sub(r"\bSystematic Review(?:\s+Review)+\b", "Systematic Review", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bSystematic Review\s+and\s+Systematic Review\b", "Systematic Review", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" :;-–—")
    return cleaned or fallback


async def claims_linker_node(state: dict[str, Any]) -> dict[str, Any]:
    """Convert inline [EV:<id>] markers into citations and the claims manifest.

    Deterministic post-pass over writer output:
    - Each sentence carrying resolvable markers becomes one ReviewClaim whose
      supporting_evidence_ids link back to EvidenceRecords.
    - Every marker run is rendered as an APA author-year citation, so prose
      carries visible citations instead of zero in-text references.
    - Markers referencing unknown evidence ids never reach the reader and are
      reported under unresolved_claims.
    - The reference list is reconciled with the papers actually cited: a cited
      paper is added and an uncited paper is pruned.
    """
    evidence_by_id = {
        e.get("evidence_id"): e
        for e in (state.get("evidence_records") or [])
        if e.get("evidence_id")
    }
    papers = state.get("paper_records") or state.get("screened_papers") or []
    papers_by_id = {
        str(p.get("paper_id")): p
        for p in papers
        if isinstance(p, dict) and p.get("paper_id")
    }

    updates: dict[str, Any] = {}
    review_claims: list[dict[str, Any]] = []
    unresolved_claims: list[dict[str, Any]] = []
    cited_paper_ids: list[str] = []

    for section in LINKED_PROSE_SECTIONS:
        prose = str(state.get(section) or "")
        if not prose:
            continue

        # Pull markers that trail a sentence onto the sentence itself so a
        # marker-only fragment ("...state of the art. [EV:id]") never becomes
        # an empty claim while the real sentence loses its link.
        prose = re.sub(r"([.?!])[ \t]+(?=\[\s*ev:)", r"\1", prose, flags=re.IGNORECASE)

        sentences = re.split(r"(?<=[.?!])\s+", prose)
        pending_ids: list[str] = []
        last_claim_index: int | None = None
        for sentence in sentences:
            marker_ids = EV_MARKER_RE.findall(sentence)
            if not marker_ids:
                continue
            resolved_ids: list[str] = []
            for mid in marker_ids:
                if mid in evidence_by_id:
                    if mid not in resolved_ids:
                        resolved_ids.append(mid)
                else:
                    unresolved_claims.append({
                        "section": section,
                        "evidence_id": mid,
                        "reason": "unknown evidence_id",
                    })

            has_text = bool(_clean_citation_punctuation(EV_MARKER_RE.sub("", sentence)))
            cleaned = _clean_citation_punctuation(
                _render_markers_as_citations(sentence, evidence_by_id, papers_by_id)
            )
            if not has_text or not cleaned:
                # Marker-only fragment: defer its ids to the adjacent claim
                # sentence instead of emitting an empty claim_text.
                pending_ids.extend(i for i in resolved_ids if i not in pending_ids)
                continue

            # Preserve order while removing duplicates (pending first).
            supporting: list[str] = []
            for evidence_id in pending_ids + resolved_ids:
                if evidence_id not in supporting:
                    supporting.append(evidence_id)
            pending_ids = []
            if supporting:
                review_claims.append(ReviewClaim(
                    claim_id=make_claim_id(section, len(review_claims) + 1),
                    claim_text=cleaned,
                    target_section=section,
                    supporting_evidence_ids=supporting,
                    is_quantitative=_is_quantitative_sentence(cleaned),
                    validation_status="pending",
                ).model_dump())
                last_claim_index = len(review_claims) - 1
                for evidence_id in supporting:
                    paper = _paper_for_evidence(evidence_by_id[evidence_id], papers_by_id)
                    paper_id = str((paper or {}).get("paper_id") or "")
                    if paper_id and paper_id not in cited_paper_ids:
                        cited_paper_ids.append(paper_id)

        if pending_ids:
            if last_claim_index is not None:
                target_ids = review_claims[last_claim_index]["supporting_evidence_ids"]
                target_ids.extend(i for i in pending_ids if i not in target_ids)
                for evidence_id in pending_ids:
                    paper = _paper_for_evidence(evidence_by_id[evidence_id], papers_by_id)
                    paper_id = str((paper or {}).get("paper_id") or "")
                    if paper_id and paper_id not in cited_paper_ids:
                        cited_paper_ids.append(paper_id)
            else:
                for mid in pending_ids:
                    unresolved_claims.append({
                        "section": section,
                        "evidence_id": mid,
                        "reason": "marker not adjacent to any claim sentence",
                    })

        updates[section] = _clean_citation_punctuation(
            _render_markers_as_citations(prose, evidence_by_id, papers_by_id)
        )

    # Reconcile the bibliography with the visible citations. Keep the existing
    # list untouched when no citation was produced (nothing to prune against).
    if cited_paper_ids and state.get("references"):
        cited_set = set(cited_paper_ids)
        rendered = "\n".join(
            str(updates.get(section) or state.get(section) or "")
            for section in LINKED_PROSE_SECTIONS
        )
        cited_set.update(_papers_cited_by_label(rendered, papers_by_id))
        references = [
            format_apa(paper)
            for paper in papers
            if str(paper.get("paper_id")) in cited_set
            and (paper.get("screening_status") == "included" or not paper.get("screening_status"))
        ]
        references = sorted({reference for reference in references if reference}, key=str.lower)
        if references:
            updates["references"] = references

    logger.info(
        f"claims_linker: built {len(review_claims)} review claims, "
        f"{len(unresolved_claims)} unresolved markers, "
        f"rendered citations for {len(cited_paper_ids)} paper(s)."
    )
    updates["review_claims"] = review_claims
    updates["unresolved_claims"] = unresolved_claims
    return updates


def _format_evidence_context(
    state: dict[str, Any],
    max_items: int | None = None,
) -> str:
    """Format every structured evidence record into compact context for writers.

    ``max_items`` exists only for the reduced-input retry path; normal writers
    receive the full evidence base. An arbitrary cap silently hid most findings
    from the Literature Review and Results writers.
    """
    ev_dicts = state.get("evidence_records") or []
    if not ev_dicts:
        # Fallback to papers summary if evidence records not yet populated
        paper_dicts = state.get("paper_records") or state.get("screened_papers") or []
        lines = []
        for p in included_papers(paper_dicts)[:10]:
            raw_authors = p.get("authors") or ["Author"]
            authors_str = ", ".join(raw_authors[:3]) + (" et al." if len(raw_authors) > 3 else "")
            year = p.get("year", "n.d.")
            lines.append(f"[{authors_str} ({year})] {p.get('title')}: {(p.get('abstract') or '')[:300]}")
        return "\n\n".join(lines)

    selected = ev_dicts if max_items is None else ev_dicts[:max_items]
    lines = []
    for e in selected:
        parts = [
            f"Evidence ID: {e.get('evidence_id')}",
            f"Claim: {e.get('claim_summary') or e.get('exact_quote') or ''}",
        ]
        quote = str(e.get("exact_quote") or "").strip()
        if quote:
            source = e.get("source_section") or e.get("section") or "unknown"
            parts.append(f'Exact Quote: "{quote}" (Source: {source})')
        metric_name = e.get("metric_name")
        reported = e.get("reported_value")
        baseline = e.get("baseline_value")
        if metric_name or reported is not None or baseline is not None:
            metric_line = f"Metric: {metric_name or 'unspecified'} = {reported if reported is not None else 'n/a'}"
            if baseline is not None:
                metric_line += f" (Baseline: {baseline})"
            parts.append(metric_line)
        if e.get("effect_direction"):
            parts.append(f"Effect: {e.get('effect_direction')}")
        lines.append("\n".join(parts))
    return "\n---\n".join(lines)


async def literature_review_agent(state: dict[str, Any]) -> dict[str, Any]:
    """Writer 1: Writes exhaustive, thematic Literature Review grounded in evidence records."""
    ps = state.get("problem_statement", "")
    evidence_ctx = _format_evidence_context(state)
    papers = state.get("paper_records") or state.get("screened_papers") or []

    # Every included paper is offered for citation: capping the hints at 15 is
    # how the writers ended up citing almost none of the corpus. Screening
    # exclusions (protocols, retractions) are never offered.
    citation_hints = "\n".join(f"- {format_apa(p)}" for p in included_papers(papers))

    llm = get_llm_for("aggregator", state, temperature=0.3)
    prompt = f"""You are a Leading Academic Scholar.
Write an exhaustive, highly structured, critical Literature Review for:
Topic: {ps}

Structured Evidence Base:
{evidence_ctx}

Available Primary Literature for Citation:
{citation_hints}

Instructions:
1. Organize the literature review into 3-4 major thematic sections corresponding to the evidence base.
2. In-text citations MUST strictly use APA format e.g. (Author et al., Year) or Author (Year). Cite the papers above; do not leave claims uncited.
3. Critically analyze conflicting methodologies and compare quantitative benchmark results.
4. TRACEABILITY: immediately after any sentence grounded in a specific evidence record, append its machine tag on the same line, e.g. "...establishes a new state of the art [EV:<Evidence ID>]". Use the exact Evidence ID from the context above.
5. Write only the body of this section; do not write a document title, an abstract, or '#'/'##' headings; use '###' for subsections if needed.
{EVIDENCE_BASIS_NOTE}
"""
    compact_prompt = f"""You are a Leading Academic Scholar.
Write a concise but complete Literature Review for:
Topic: {ps}

Structured Evidence Base (reduced):
{_format_evidence_context(state, max_items=6)}

Citation hints (reduced):
{chr(10).join(f"- {format_apa(p)}" for p in papers[:8])}

Use APA author-year citations and [EV:<Evidence ID>] markers after grounded sentences.
Write only the body; no document title, abstract, or '#'/'##' headings.
{EVIDENCE_BASIS_NOTE}
"""
    body = await write_required_section(
        _safe_invoke_llm,
        llm,
        prompt,
        section="Literature Review",
        compact_prompt=compact_prompt,
    )
    return {"literature_review": body}


async def research_design_agent(state: dict[str, Any]) -> dict[str, Any]:
    """Writer 2: Formulates proposed research design (explicitly framed as proposed future study)."""
    ps = state.get("problem_statement", "")
    hypotheses = state.get("hypotheses", [])
    protocol = state.get("search_protocol") or {}
    criteria = protocol.get("inclusion_criteria") if isinstance(protocol, dict) else None
    protocol_text = (
        f"{protocol.get('population', '')} {protocol.get('intervention', '')}"
        if isinstance(protocol, dict) else ""
    )
    # Match clinical vocabulary in the topic and PICOC text, not mere field presence.
    ps_lower = f"{ps} {protocol_text}".lower()
    is_clinical = bool(
        re.search(r"\b(clinical|trial|patient|insomnia|therapy|treatment|drug|pharmac|medical|disease|health|cbt|rct|placebo)\b", ps_lower)
        or re.search(r"\b(systematic review|meta-analysis)\b", ps_lower)
        or re.search(r"\brandomi[sz]ed\b", " ".join(map(str, criteria or [])).lower())
    )

    if is_clinical:
        structure_guide = """Structure the section in Markdown:
- 1. Study Design & Methodological Framework (e.g., PRISMA-compliant Systematic Review / Multi-arm Clinical Trial Protocol)
- 2. Target Population, Intervention, Comparator & Setting (PICO Framework)
- 3. Variable Operationalization, Outcome Measures & Quality Control Protocols"""
    else:
        structure_guide = """Structure the section in Markdown:
- 1. Methodological Philosophy & Research Paradigm
- 2. Experimental Architecture & Target Testbeds
- 3. Variable Operationalization & Control Protocols"""

    llm = get_llm_for("planner", state, temperature=0.2)

    prompt = f"""You are an Expert Research Methodologist.
Propose a comprehensive Research Design & Experimental Methodology to empirically test the formulated hypotheses for:
Topic: {ps}

Hypotheses:
{chr(10).join(f"- {h}" for h in hypotheses)}

{structure_guide}

Instructions:
- Write only the body of this section; do not write a document title, an abstract, or '#'/'##' headings; use '###' for subsections if needed.
{EVIDENCE_BASIS_NOTE}
"""
    raw = await _safe_invoke_llm(llm, [HumanMessage(content=prompt)])
    cleaned = _strip_preamble(raw)
    return {"research_design": normalize_section_body(cleaned, section_title="Research Design")}


async def data_collection_agent(state: dict[str, Any]) -> dict[str, Any]:
    """Writer 3: Formulates proposed data collection protocol & instruments."""
    ps = state.get("problem_statement", "")
    design = state.get("research_design", "")
    llm = get_llm_for("planner", state, temperature=0.2)

    prompt = f"""You are a Data Engineering and Sampling Methodologist.
Detail the proposed Data Collection Protocol for:
Topic: {ps}

Research Design Context:
{design[:800]}

Detail:
- Sampling Strategy & Dataset Selection
- Data Ingestion, Extraction, and Preprocessing Pipelines
- Ethical Considerations & Data Integrity Safeguards

Instructions:
- Write only the body of this section; do not write a document title, an abstract, or '#'/'##' headings; use '###' for subsections if needed.
{EVIDENCE_BASIS_NOTE}
"""
    raw = await _safe_invoke_llm(llm, [HumanMessage(content=prompt)])
    cleaned = _strip_preamble(raw)
    return {"data_collection_plan": normalize_section_body(cleaned, section_title="Data Collection")}


async def data_analysis_agent(state: dict[str, Any]) -> dict[str, Any]:
    """Writer 4: Formulates proposed statistical analysis pipeline."""
    hypotheses = state.get("hypotheses", [])
    llm = get_llm_for("planner", state, temperature=0.2)

    prompt = f"""You are a Senior Quantitative Biostatistician and Data Scientist.
Specify the proposed Statistical and Empirical Analysis Plan for testing:
Hypotheses:
{chr(10).join(f"- {h}" for h in hypotheses)}

Detail:
- Primary Statistical Tests (e.g. ANOVA, Regression, Ablation Benchmarks)
- Significance Thresholds, Confidence Intervals, and Power Analysis
- Model Evaluation Metrics and Error Analysis Protocols

Instructions:
- Write only the body of this section; do not write a document title, an abstract, or '#'/'##' headings; use '###' for subsections if needed.
{EVIDENCE_BASIS_NOTE}
"""
    raw = await _safe_invoke_llm(llm, [HumanMessage(content=prompt)])
    cleaned = _strip_preamble(raw)
    return {"data_analysis_plan": normalize_section_body(cleaned, section_title="Data Analysis")}


async def results_agent(state: dict[str, Any]) -> dict[str, Any]:
    """Writer 5: Synthesizes empirical evidence and maps findings against formulated hypotheses."""
    ps = state.get("problem_statement", "")
    hypotheses = state.get("hypotheses", [])
    evidence_ctx = _format_evidence_context(state)
    llm = get_llm_for("aggregator", state, temperature=0.2)

    prompt = f"""You are a Principal Empirical Research Analyst.
Synthesize the empirical findings from the literature base and map them against the formulated hypotheses for:
Topic: {ps}

Formulated Hypotheses:
{chr(10).join(f"- {h}" for h in hypotheses)}

Extracted Evidence Records & Benchmark Metrics:
{evidence_ctx}

Provide a detailed Results Section in Markdown:
- 1. Overview of Included Evidence Base
- 2. Hypothesis-by-Hypothesis Empirical Synthesis (evaluate whether current literature supports, refutes, or partially confirms H1..H5)
- 3. Quantitative Benchmark Comparisons (tabulate or summarize reported baseline vs state-of-the-art metrics)
4. Do NOT restate a hypothesis as a standalone heading or bolded line. Report what the evidence shows in prose.
5. TRACEABILITY: immediately after any sentence grounded in a specific evidence record, append its machine tag on the same line, e.g. "...reaches 94.2% accuracy [EV:<Evidence ID>]". Use the exact Evidence ID from the context above.
6. Write only the body of this section; do not write a document title, an abstract, or '#'/'##' headings; use '###' for subsections if needed.
{EVIDENCE_BASIS_NOTE}
"""
    compact_prompt = f"""You are a Principal Empirical Research Analyst.
Write a concise Results section evaluating the hypotheses with the evidence for:
Topic: {ps}
Hypotheses:
{chr(10).join(f"- {h}" for h in hypotheses[:5])}

Evidence (reduced):
{_format_evidence_context(state, max_items=6)}

Do not restate hypotheses as standalone lines. Use APA citations and [EV:<Evidence ID>] markers after grounded sentences.
Write only the body; no document title, abstract, or '#'/'##' headings.
{EVIDENCE_BASIS_NOTE}
"""
    body = await write_required_section(
        _safe_invoke_llm,
        llm,
        prompt,
        section="Results",
        compact_prompt=compact_prompt,
    )
    return {"results": _strip_hypothesis_restatements(body)}


async def discussion_agent(state: dict[str, Any]) -> dict[str, Any]:
    """Writer 6: Discusses findings, convergence, and divergence."""
    ps = state.get("problem_statement", "")
    results = _strip_ev_markers(state.get("results", ""))
    llm = get_llm_for("aggregator", state, temperature=0.3)

    prompt = f"""You are a Senior Academic Scholar and Discussion Lead.
Write an in-depth Discussion section for:
Topic: {ps}

Synthesized Results:
{results[:1000]}

Provide:
- Discussion of Findings & Theoretical Convergence
- Explanation of Divergence and Boundary Conditions
- Comparison with Prior Syntheses

Instructions:
- Write only the body of this section; do not write a document title, an abstract, or '#'/'##' headings; use '###' for subsections if needed.
{EVIDENCE_BASIS_NOTE}
"""
    body = await write_required_section(
        _safe_invoke_llm,
        llm,
        prompt,
        section="Discussion",
    )
    return {"discussion": body}


async def implications_agent(state: dict[str, Any]) -> dict[str, Any]:
    """Writer 6b: Writes the dedicated Practical & Theoretical Implications section."""
    ps = state.get("problem_statement", "")
    results = _strip_ev_markers(state.get("results", ""))
    discussion = _strip_ev_markers(state.get("discussion", ""))
    evidence_ctx = _format_evidence_context(state)
    citation_labels = _citation_labels(state.get("paper_records") or state.get("screened_papers") or [])
    llm = get_llm_for("aggregator", state, temperature=0.3)

    prompt = f"""You are a Senior Academic Scholar writing the Implications section.
Topic: {ps}

Synthesized Results:
{results[:900]}

Discussion Context:
{discussion[:700]}

Evidence Base:
{evidence_ctx}

Citable studies (use these exact author-year labels, no others):
{citation_labels}

Write a substantive Implications section (not a pointer to another section) covering:
- Theoretical Implications: what the synthesis changes, qualifies, or confirms in theory.
- Practical & Industrial Implications: concrete guidance for practitioners, engineers, and organizations.
- Policy & Societal Implications: decision-maker considerations, where the evidence is strong enough.
- Conditions and Limits: under which contexts these implications should not be generalized.

Instructions:
- Ground every implication in the supplied evidence; distinguish what is established from what is suggested.
- Cite studies only with the author-year labels listed above or with [EV:<Evidence ID>] markers; never invent a citation.
- TRACEABILITY: immediately after any sentence grounded in a specific evidence record, append its machine tag on the same line, e.g. "...improves adherence [EV:<Evidence ID>]". Use the exact Evidence ID from the evidence base above.
- Write only the body of this section; do not write a document title, an abstract, or '#'/'##' headings; use '###' for subsections if needed.
{EVIDENCE_BASIS_NOTE}
"""
    compact_prompt = f"""You are a Senior Academic Scholar writing the Implications section.
Topic: {ps}
Results: {results[:500]}
Discussion: {discussion[:400]}
Evidence (reduced): {_format_evidence_context(state, max_items=5)}
Citable studies (exact labels only):
{citation_labels}

Cover theoretical implications, practical implications, and their boundary conditions in substantive prose.
Cite only with the labels above or [EV:<Evidence ID>] markers after grounded sentences.
Write only the body; no document title, abstract, or '#'/'##' headings.
{EVIDENCE_BASIS_NOTE}
"""
    body = await write_required_section(
        _safe_invoke_llm,
        llm,
        prompt,
        section="Implications",
        compact_prompt=compact_prompt,
    )
    return {"implications": body}


async def limitations_agent(state: dict[str, Any]) -> dict[str, Any]:
    """Writer 7: Evaluates limitations across the evidence base and review methodology."""
    ps = state.get("problem_statement", "")
    ev_dicts = state.get("evidence_records") or []
    llm = get_llm_for("researcher", state, temperature=0.2)

    ev_lims = [f"- {e.get('claim_summary')}: {e.get('limitations')}" for e in ev_dicts if e.get("limitations")]
    ev_lims_text = "\n".join(ev_lims[:8]) if ev_lims else "General empirical boundary constraints in literature."

    prompt = f"""You are a Critical Research Reviewer.
Write a rigorous, transparent Limitations section for:
Topic: {ps}

Extracted Evidence Caveats:
{ev_lims_text}

Address:
- Methodological Limitations in Current Literature
- Data Availability, Generalizability, and Compute Constraints
- Systematic Review Scope & Search Boundary Limitations

Instructions:
- Write only the body of this section; do not write a document title, an abstract, or '#'/'##' headings; use '###' for subsections if needed.
{EVIDENCE_BASIS_NOTE}
"""
    body = await write_required_section(
        _safe_invoke_llm,
        llm,
        prompt,
        section="Limitations",
        compact_prompt=f"""You are a Critical Research Reviewer.
Write a concise Limitations section for: {ps}
Evidence caveats: {ev_lims_text[:600]}

Cover methodological limitations, generalizability, and systematic-review scope.
Write only the body; no document title, abstract, or '#'/'##' headings.
{EVIDENCE_BASIS_NOTE}
""",
    )
    return {"limitations": body}


async def conclusion_agent(state: dict[str, Any]) -> dict[str, Any]:
    """Writer 8: Formulates authoritative conclusion summarizing research contributions."""
    ps = state.get("problem_statement", "")
    results = _strip_ev_markers(state.get("results", ""))
    llm = get_llm_for("aggregator", state, temperature=0.2)

    prompt = f"""You are a Lead Research Author.
Write an authoritative Academic Conclusion (3 paragraphs) summarizing the core discoveries, synthesis, and takeaways for:
Topic: {ps}

Synthesized Results Findings:
{results[:800]}

Instructions:
- Write only the body of this section; do not write a document title, an abstract, or '#'/'##' headings; use '###' for subsections if needed.
"""
    body = await write_required_section(
        _safe_invoke_llm,
        llm,
        prompt,
        section="Conclusion",
        compact_prompt=f"""You are a Lead Research Author.
Write a concise Academic Conclusion (2-3 paragraphs) for: {ps}
Results: {results[:400]}

Summarize the core discoveries and takeaways without claiming a meta-analysis.
Write only the body; no document title, abstract, or '#'/'##' headings.
""",
    )
    return {"conclusion": body}


async def future_scope_agent(state: dict[str, Any]) -> dict[str, Any]:
    """Writer 9: Outlines future research directions."""
    ps = state.get("problem_statement", "")
    gaps = state.get("research_gap", "")
    llm = get_llm_for("planner", state, temperature=0.3)

    prompt = f"""You are an Academic Visionary.
Formulate 4-5 high-impact Future Research Directions for:
Topic: {ps}

Identified Gaps:
{gaps}

Instructions:
- Write only the body of this section; do not write a document title, an abstract, or '#'/'##' headings; use '###' for subsections if needed.
- Return ONLY a bulleted list of 4-5 items.
"""
    raw = await _safe_invoke_llm(llm, [HumanMessage(content=prompt)])
    cleaned = _strip_preamble(raw)
    normalized = normalize_section_body(cleaned, section_title="Future Scope")
    # Strip only list markers (bullets and "1." / "1)" enumerators) so legitimate
    # leading text such as a year ("2026 benchmarks ...") is preserved.
    lines = []
    for line in normalized.split("\n"):
        cleaned_line = re.sub(r"^\s*(?:[-*•]+|\d{1,2}[.)])\s+", "", line).strip()
        if len(cleaned_line) > 10:
            lines.append(cleaned_line)
    # Return an empty list when nothing usable was extracted so the renderer can
    # omit the section instead of showing generic filler.
    return {"future_scope": lines[:5]}


async def references_agent(state: dict[str, Any]) -> dict[str, Any]:
    """Writer 10: Formats APA 7th edition bibliography from included paper records."""
    paper_dicts = state.get("paper_records") or state.get("screened_papers") or []
    references = [format_apa(p) for p in paper_dicts if p.get("screening_status") == "included" or not p.get("screening_status")]
    if not references:
        references = [format_apa(p) for p in paper_dicts[:15]]
    references = sorted(set(references), key=lambda r: r.lower())
    return {"references": references}


async def appendices_agent(state: dict[str, Any]) -> dict[str, Any]:
    """Writer 11: Compiles search protocol, evidence tables, and claim traceability matrix."""
    protocol = state.get("search_protocol") or {}
    tracker = state.get("prisma_tracker") or {}
    review_claims = state.get("review_claims") or []
    evidence_by_id = {
        e.get("evidence_id"): e
        for e in (state.get("evidence_records") or [])
        if e.get("evidence_id")
    }

    inc = protocol.get('inclusion_criteria') or []
    exc = protocol.get('exclusion_criteria') or []
    inc_str = ", ".join(inc) if inc else "Peer-reviewed empirical studies"
    exc_str = ", ".join(exc) if exc else "Non-empirical articles and duplicates"

    app_text = f"""## Appendix A: Systematic Search Protocol
- **Population / Domain**: {protocol.get('population', 'N/A')}
- **Intervention**: {protocol.get('intervention', 'N/A')}
- **Inclusion Criteria**: {inc_str}
- **Exclusion Criteria**: {exc_str}

## Appendix B: PRISMA Selection Audit
- Total Records Identified: {tracker.get('records_identified', 0)}
- Duplicates Removed: {tracker.get('duplicates_removed', 0)}
- Records Screened: {tracker.get('records_screened', 0)}
- Studies Included: {tracker.get('studies_included', 0)}
"""

    if review_claims:
        rows = []
        for c in review_claims:
            locations = []
            for eid in c.get("supporting_evidence_ids") or []:
                ev = evidence_by_id.get(eid) or {}
                loc = str(ev.get("section") or "unknown")
                if ev.get("page") is not None:
                    loc += f" p. {ev['page']}"
                locator = ev.get("doi") or ev.get("source_url") or ""
                target = f"https://doi.org/{locator}" if locator and not str(locator).startswith("http") else locator
                link = f" [source]({target})" if target else ""
                locations.append(f"{eid} ({loc}){link}")
            rows.append(
                f"- **{c.get('claim_id')}** ({c.get('target_section', '')}): "
                f"{str(c.get('claim_text') or '')[:140]} -> {'; '.join(locations) if locations else 'unlinked'}"
            )
        app_text += "\n## Appendix C: Claim Traceability Matrix\n"
        app_text += (
            "Machine-readable claim -> evidence -> source chains for each sentence the writer tagged with "
            "evidence (introduction, literature review, results and discussion). Untagged sentences are not linked.\n"
        )
        app_text += "\n".join(rows) + "\n"

    return {"appendices": app_text}


async def introduction_agent(state: dict[str, Any]) -> dict[str, Any]:
    """Writer 12: Synthesizes comprehensive Introduction with problem context and contributions."""
    ps = state.get("problem_statement", "")
    objs = state.get("research_objectives", [])
    llm = get_llm_for("aggregator", state, temperature=0.3)

    prompt = f"""You are a Lead Academic Author.
Write a publication-grade Introduction section for:
Topic: {ps}

Objectives:
{chr(10).join(f"- {o}" for o in objs)}

Structure:
- Context & Scientific Motivation
- Problem Articulation & Significance
- Outline of Research Objectives & Paper Structure

Instructions:
- Write only the body of this section; do not write a document title, an abstract, or '#'/'##' headings; use '###' for subsections if needed.
"""
    body = await write_required_section(
        _safe_invoke_llm,
        llm,
        prompt,
        section="Introduction",
        compact_prompt=f"""You are a Lead Academic Author.
Write a concise Introduction for: {ps}
Objectives: {chr(10).join(f'- {o}' for o in objs[:4])}

Cover context, problem significance, and paper structure. Write only the body; no document title, abstract, or '#'/'##' headings.
""",
    )
    return {"introduction": body}


async def abstract_agent(state: dict[str, Any]) -> dict[str, Any]:
    """Writer 13: Synthesizes structured abstract."""
    ps = state.get("problem_statement", "")
    results = _strip_ev_markers(state.get("results", ""))
    conclusion = _strip_ev_markers(state.get("conclusion", ""))
    llm = get_llm_for("aggregator", state, temperature=0.2)

    prompt = f"""You are a Lead Academic Author.
Write a structured Academic Abstract (250-300 words) summarizing:
- Background & Problem Motivation: {ps}
- Methodology & Synthesis: Structured multi-source evidence extraction and PRISMA 2020 protocol
- Principal Findings: {results[:500]}
- Significance: {conclusion[:300]}

Instructions:
- Write only the body of the abstract; do not write a document title or an 'Abstract' heading.
"""
    body = await write_required_section(
        _safe_invoke_llm,
        llm,
        prompt,
        section="Abstract",
        normalizer=strip_abstract_heading,
        compact_prompt=f"""You are a Lead Academic Author.
Write a structured Academic Abstract (200-250 words) for: {ps}
Findings: {results[:300]}
Significance: {conclusion[:200]}

Write only the abstract body; no document title or 'Abstract' heading.
""",
    )
    return {"abstract": body}


async def title_agent(state: dict[str, Any]) -> dict[str, Any]:
    """Writer 14: Finalizes concise academic title."""
    ps = state.get("problem_statement", "")
    llm = get_llm_for("planner", state, temperature=0.2)

    prompt = f"""You are a Scholarly Editor.
Generate a concise, authoritative academic research paper title for:
Topic: {ps}

The paper is an evidence synthesis, NOT a meta-analysis: no pooled effect size,
no statistical pooling, and no meta-regression were performed. Do not use the
words "meta-analysis", "meta-analytic", or "pooled effect" in the title. Name
the design accurately (for example "A Systematic Review").

Return ONLY the single title string without quotes.
"""
    raw = await _safe_invoke_llm(llm, [HumanMessage(content=prompt)])
    title = _sanitize_title(_strip_preamble(raw).strip().strip('"'), fallback=ps)
    return {
        "title": title or ps,
        "status": "completed"
    }


async def figures_node(state: dict[str, Any]) -> dict[str, Any]:
    """Renders PRISMA flow chart and evidence mapping figures."""
    figures_dir = os.getenv("FIGURES_DIR", "./data/figures")
    thread_id = state.get("thread_id", "default")
    safe_thread_id = re.sub(r"[^a-zA-Z0-9_\-]", "_", str(thread_id))
    resolved_base = Path(figures_dir).resolve()
    out_dir = (resolved_base / safe_thread_id).resolve()
    if not str(out_dir).startswith(str(resolved_base)):
        out_dir = resolved_base / "default"
    out_dir.mkdir(parents=True, exist_ok=True)

    prisma_path = str(out_dir / "prisma.png")
    evidence_path = str(out_dir / "evidence.png")

    fig_map: dict[str, str] = {}
    try:
        # Prefer the deterministic PRISMA tracker; fall back to raw corpus_stats.
        # Pass the dict through as-is so render_prisma_diagram can filter the
        # fields it supports instead of us discarding it with a key guard.
        raw_tr = state.get("prisma_tracker") or state.get("corpus_stats") or {}
        tracker_arg = raw_tr if isinstance(raw_tr, dict) else PRISMATracker()
        p_res = render_prisma_diagram(tracker_arg, prisma_path)
        if p_res and os.path.exists(p_res):
            fig_map["prisma"] = p_res
    except Exception as e:  # noqa: BLE001
        logger.warning(f"PRISMA diagram rendering failed: {e}")

    try:
        papers = state.get("paper_records") or state.get("screened_papers") or []
        hyps = state.get("hypotheses") or []
        e_res = render_evidence_table(papers, hyps, evidence_path)
        if e_res and os.path.exists(e_res):
            fig_map["evidence"] = e_res
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Evidence table rendering failed: {e}")

    return {"figures": fig_map}
