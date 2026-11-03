"""Phase 4 Structured Evidence Extraction Agents for Research Mode.

Every extracted claim is forced through the anchored evidence path:
Claim -> EvidenceSpan -> Paper -> exact location (section+page) -> URL/DOI.
Free-text claim generation without paper_id + evidence + section is impossible
by construction: anchor_quote_to_paper deterministically derives the span,
page, offsets, and confidence from the source text or marks them explicitly
unknown. Unmatchable quotes are kept as paraphrase-grade low-confidence
records rather than silently dropped, so downstream consumers can audit them.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from langchain_core.messages import HumanMessage

from backend.app.agents.research_mode._common import (
    _safe_invoke_llm,
    get_llm_for,
    included_papers,
)
from backend.app.models.evidence import (
    UNKNOWN_SECTION,
    ConfidenceBasis,
    EvidenceRecord,
    EvidenceSpan,
    apply_chain_downgrade,
    build_claims,
    locate_quote,
    make_evidence_id,
    make_paper_id,
    make_span_id,
    resolve_record_chain,
    score_confidence,
)

logger = logging.getLogger(__name__)

# Section names an LLM may legitimately report for a quote's location; anything
# else degrades to UNKNOWN_SECTION instead of being trusted.
KNOWN_SECTIONS = (
    "abstract",
    "introduction",
    "background",
    "related work",
    "methodology",
    "methods",
    "results",
    "discussion",
    "conclusion",
)

# Model refusals are not findings. They used to be stored as claim summaries for
# sources with no quotable text.
_REFUSAL_RE = re.compile(
    r"\bunable to extract\b|\bcannot extract\b|\bno (?:specific )?(?:claims|findings)\b",
    re.IGNORECASE,
)


def _clean_section_label(label: str | None) -> str:
    """Map a raw LLM section label onto a known section or 'unknown'."""
    low = (label or "").strip().lower()
    if not low:
        return UNKNOWN_SECTION
    for known in KNOWN_SECTIONS:
        if known in low:
            return known.title()
    return UNKNOWN_SECTION


def anchor_quote_to_paper(
    paper: dict[str, Any],
    quote: str,
    section_label: str | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Anchor one claim to its paper via deterministic quote location.

    Returns the record-extras dict (span fields, confidence, provenance).
    Location fields follow the explicit
    rule: page only when form-feed markers prove it, section from the label
    when recognized else 'unknown', offsets only for fulltext anchors, and
    confidence scored by the documented extraction-method table.
    """
    # Prefer the full anchor text (whole document, page markers intact) over
    # the prompt-sized excerpt so quotes deep in the paper stay attributable.
    text = paper.get("full_text") or paper.get("fulltext_excerpt") or ""
    abstract = paper.get("abstract") or ""

    basis = ConfidenceBasis.PARAPHRASE
    span_text = (quote or "").strip()
    section = _clean_section_label(section_label)
    page: int | None = None
    start_off: int | None = None
    end_off: int | None = None

    loc_full = locate_quote(text, quote)
    loc_abs = locate_quote(abstract, quote) if not loc_full else None

    if loc_full:
        start_off, end_off, page = loc_full
        span_text = text[start_off:end_off]
        basis = ConfidenceBasis.EXACT_QUOTE_FULLTEXT
    elif loc_abs:
        abs_start, abs_end, _page = loc_abs
        span_text = abstract[abs_start:abs_end]
        section = "Abstract"
        basis = ConfidenceBasis.EXACT_QUOTE_ABSTRACT
        # Abstract-only API results carry no meaningful page number.
        page = None

    chain_complete = bool(paper.get("source_url") or paper.get("doi"))
    confidence = score_confidence(
        basis,
        page_known=page is not None,
        chain_complete=chain_complete,
    )

    extras = {
        "exact_quote": span_text,
        "source_section": section,
        "section": section,
        "page": page,
        "char_offset_start": start_off,
        "char_offset_end": end_off,
        "confidence": confidence,
        "confidence_basis": basis.value,
        "verification_status": "resolved" if basis != ConfidenceBasis.PARAPHRASE else "unresolved",
        "source_url": paper.get("source_url") or "",
        "doi": paper.get("doi"),
    }
    return extras


def build_span(
    paper_id: str,
    span_seq: int,
    extras: dict[str, Any],
    span_id: str | None = None,
) -> dict[str, Any]:
    """Materialize the EvidenceSpan dict for anchored record extras."""
    return EvidenceSpan(
        span_id=span_id or make_span_id(paper_id, span_seq),
        paper_id=paper_id,
        text=extras["exact_quote"],
        section=extras.get("section", UNKNOWN_SECTION),
        page=extras.get("page"),
        char_offset_start=extras.get("char_offset_start"),
        char_offset_end=extras.get("char_offset_end"),
    ).model_dump()


async def evidence_extractor_agent(state: dict[str, Any]) -> dict[str, Any]:
    """Agent 11: Extracts structured factual findings as anchored evidence records."""
    paper_dicts = state.get("paper_records") or state.get("screened_papers") or []
    if not paper_dicts:
        return {"evidence_records": [], "evidence_spans": []}

    llm = get_llm_for("researcher", state, temperature=0.1)
    evidence_records: list[dict[str, Any]] = []
    evidence_spans: list[dict[str, Any]] = []
    # Evidence and span IDs are numbered per paper so ev00N and sp00N stay in
    # 1:1 correspondence within each paper.
    ev_seq_by_paper: dict[str, int] = {}

    # Process top 10 included papers
    for p_idx, p in enumerate(included_papers(paper_dicts)[:10]):
        abstract = (p.get("abstract") or "").strip()
        excerpt = (p.get("fulltext_excerpt") or "").strip()
        # A title-only record has nothing to quote. Sending it to the model
        # produced refusal text ("Unable to extract...") that was then stored as
        # a claim at paraphrase confidence, i.e. an error string became evidence.
        if not abstract and not excerpt:
            logger.info(
                "Skipping title-only paper %r: no abstract or full text to anchor evidence.",
                (p.get("title") or "")[:60],
            )
            continue
        paper_id = p.get("paper_id")
        if not paper_id:
            # Persist a stable identifier back onto the record so downstream
            # evidence records and spans all carry the same ID.
            paper_id = make_paper_id(
                doi=p.get("doi"),
                title=p.get("title", ""),
                year=p.get("year"),
            )
            p["paper_id"] = paper_id
        title = p.get("title", "")
        abstract = p.get("abstract", "")
        excerpt = p.get("fulltext_excerpt") or ""

        text_content = f"Title: {title}\nAbstract: {abstract}\nKey Excerpt: {excerpt[:800]}"

        prompt = f"""You are an expert Evidence Extraction Specialist.
Extract 2-3 specific, verifiable empirical or theoretical claims from the paper below.
For each claim you MUST provide the exact verbatim quote copied character-for-character
from the paper text, plus the section it appears in.

Paper:
{text_content}

Return a valid JSON array of objects:
[
  {{
    "claim_summary": "Concise summary of specific empirical finding or theoretical principle",
    "exact_quote": "Verbatim quote copied exactly from the paper text supporting this finding",
    "source_section": "Abstract" | "Introduction" | "Methodology" | "Results" | "Discussion" | "Conclusion" | "unknown",
    "task_or_domain": "e.g. Natural Language Processing, Protein Folding (or null)",
    "model_or_method": "e.g. AlphaFold2, Transformer, CNN (or null)"
  }}
]
"""
        records_before = len(evidence_records)
        try:
            raw = await _safe_invoke_llm(llm, [HumanMessage(content=prompt)])
            clean = raw
            if "```" in clean:
                clean = clean.split("```")[1]
                clean = clean.removeprefix("json")
            items = json.loads(clean.strip())

            for item in items:
                if not isinstance(item, dict):
                    continue
                quote = item.get("exact_quote")
                if not quote:
                    continue
                claim_summary = str(item.get("claim_summary") or "").strip() or "Empirical finding"
                if _REFUSAL_RE.search(claim_summary):
                    logger.info("Dropping model refusal as evidence: %r", claim_summary[:80])
                    continue
                extras = anchor_quote_to_paper(p, str(quote), item.get("source_section"))
                paper_seq = ev_seq_by_paper.get(paper_id, 0) + 1
                ev_seq_by_paper[paper_id] = paper_seq
                span = build_span(paper_id, paper_seq, extras)
                extras["evidence_span_id"] = span["span_id"]
                ev = EvidenceRecord(
                    evidence_id=make_evidence_id(paper_id, paper_seq),
                    paper_id=paper_id,
                    claim_summary=str(claim_summary),
                    task_or_domain=item.get("task_or_domain"),
                    model_or_method=item.get("model_or_method"),
                    effect_direction="unclear",
                    **extras,
                )
                evidence_records.append(ev.model_dump())
                evidence_spans.append(span)

        except Exception as e:  # noqa: BLE001
            logger.warning(f"evidence_extractor failed for paper '{title[:30]}': {e}")

        # Fallback only if no records were extracted for this paper AND valid abstract quote exists
        if len(evidence_records) == records_before and abstract and len(abstract.strip()) > 30:
            fallback_quote = abstract.strip()[:200]
            extras = anchor_quote_to_paper(p, fallback_quote, "Abstract")
            fallback_seq = ev_seq_by_paper.get(paper_id, 0) + 1
            ev_seq_by_paper[paper_id] = fallback_seq
            span = build_span(paper_id, fallback_seq, extras)
            extras["evidence_span_id"] = span["span_id"]
            ev = EvidenceRecord(
                evidence_id=make_evidence_id(paper_id, fallback_seq),
                paper_id=paper_id,
                claim_summary=f"Investigates {title[:60]}",
                effect_direction="unclear",
                **extras,
            )
            evidence_records.append(ev.model_dump())
            evidence_spans.append(span)

    logger.info(
        f"evidence_extractor_agent generated {len(evidence_records)} structured evidence records "
        f"with {len(evidence_spans)} anchored spans."
    )
    return {
        "evidence_records": evidence_records,
        "evidence_spans": evidence_spans,
        "paper_records": paper_dicts,
    }


async def quantitative_extractor_agent(state: dict[str, Any]) -> dict[str, Any]:
    """Agent 12: Enriches evidence records with quantitative benchmarks, baseline vs reported numbers, effect directions."""
    ev_dicts = state.get("evidence_records") or []
    if not ev_dicts:
        return {}

    llm = get_llm_for("researcher", state, temperature=0.1)
    ev_summary = "\n".join(
        f"[{i+1}] ID: {e.get('evidence_id')}\nClaim: {e.get('claim_summary')}\nQuote: {e.get('exact_quote')}"
        for i, e in enumerate(ev_dicts[:15])
    )

    prompt = f"""You are a Quantitative Meta-Analysis Extractor.
Extract any numerical metrics, baselines, reported values, and effect directions (positive, negative, neutral, mixed) from the following evidence items.

Evidence:
{ev_summary}

Return a valid JSON array of objects:
[
  {{
    "id": 1,
    "metric_name": "e.g. Accuracy, BLEU, F1, Latency (or null if qualitative)",
    "baseline_value": 0.0 (or null),
    "reported_value": 0.0 (or null),
    "unit_or_scale": "%" | "ms" | "points" (or null),
    "effect_direction": "positive" | "negative" | "neutral" | "mixed" | "unclear"
  }}
]
"""
    try:
        raw = await _safe_invoke_llm(llm, [HumanMessage(content=prompt)])
        clean = raw
        if "```" in clean:
            clean = clean.split("```")[1]
            clean = clean.removeprefix("json")
        items = json.loads(clean.strip())
        item_map = {it["id"]: it for it in items if isinstance(it, dict) and "id" in it}

        for idx, e in enumerate(ev_dicts[:15]):
            q = item_map.get(idx + 1)
            if q:
                if q.get("metric_name"):
                    e["metric_name"] = str(q["metric_name"])
                if q.get("baseline_value") is not None:
                    try:
                        e["baseline_value"] = float(q["baseline_value"])
                    except (ValueError, TypeError):
                        pass
                if q.get("reported_value") is not None:
                    try:
                        e["reported_value"] = float(q["reported_value"])
                    except (ValueError, TypeError):
                        pass
                if q.get("unit_or_scale"):
                    e["unit_or_scale"] = str(q["unit_or_scale"])
                e["effect_direction"] = q.get("effect_direction", "unclear")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"quantitative_extractor parsing defaulted: {e}")

    return {"evidence_records": ev_dicts}


async def methodology_extractor_agent(state: dict[str, Any]) -> dict[str, Any]:
    """Agent 13: Extracts benchmark datasets, sample sizes, and experimental baselines."""
    ev_dicts = state.get("evidence_records") or []
    paper_dicts = state.get("paper_records") or []
    if not ev_dicts or not paper_dicts:
        return {}

    # task_or_domain is left unset when no task or domain was extracted from the
    # source. A paper's publication venue is not a valid substitute for it.
    return {"evidence_records": ev_dicts}


async def limitation_extractor_agent(state: dict[str, Any]) -> dict[str, Any]:
    """Agent 14: Extracts empirical caveats, boundary conditions, and threats to validity."""
    ev_dicts = state.get("evidence_records") or []
    # Leave limitations empty when absent from source rather than injecting synthetic text
    return {"evidence_records": ev_dicts}


async def provenance_agent(state: dict[str, Any]) -> dict[str, Any]:
    """Agent 15: Resolves the full traceability chain for every evidence record.

    Chain: Claim -> EvidenceSpan -> Paper -> exact location -> URL/DOI.
    - Drops records whose paper_id matches nothing in the corpus (ungroundable).
    - Backfills missing full text via PDF fetch for paraphrase-grade records,
      re-anchoring them when the verbatim quote is then located.
    - Downgrades records with incomplete chains to unresolved + low confidence
      instead of surfacing black-box claims.
    - Emits machine-readable Claim objects for downstream output layers.
    """
    ev_dicts = state.get("evidence_records") or []
    # Fall back to screened_papers so evidence stays validated when only that
    # corpus key is populated upstream.
    paper_dicts = state.get("paper_records") or state.get("screened_papers") or []
    valid_paper_ids = {p.get("paper_id") for p in paper_dicts if p.get("paper_id")}
    papers_by_id = {p.get("paper_id"): p for p in paper_dicts if p.get("paper_id")}

    validated: list[dict[str, Any]] = []
    if not valid_paper_ids:
        # No corpus available to anchor against: keep the evidence rather than
        # discarding every record because upstream state was empty.
        if ev_dicts:
            logger.warning(
                "provenance_agent found no paper corpus to anchor against; "
                f"passing {len(ev_dicts)} evidence records through unfiltered."
            )
        validated = [dict(e) for e in ev_dicts]
    else:
        for e in ev_dicts:
            pid = e.get("paper_id")
            if pid in valid_paper_ids:
                validated.append(dict(e))
            else:
                logger.warning(f"Discarded ungrounded evidence record {e.get('evidence_id')} without paper match.")

    # --- Full-text backfill for paraphrase-grade records missing excerpts ---
    needs_backfill = {
        r["paper_id"]
        for r in validated
        if r.get("confidence_basis") == ConfidenceBasis.PARAPHRASE.value
        and papers_by_id.get(r["paper_id"])
        and not (
            papers_by_id[r["paper_id"]].get("full_text")
            or papers_by_id[r["paper_id"]].get("fulltext_excerpt")
        )
    }
    if needs_backfill:
        try:
            from backend.app.tools.fulltext_fetcher import fetch_fulltexts

            to_fetch = [papers_by_id[pid] for pid in needs_backfill]
            enriched = await fetch_fulltexts([dict(p) for p in to_fetch])
            for p in enriched:
                target = papers_by_id.get(p.get("paper_id"))
                if target is not None and (p.get("full_text") or p.get("fulltext_excerpt")):
                    if p.get("full_text"):
                        target["full_text"] = p["full_text"]
                    if p.get("fulltext_excerpt"):
                        target["fulltext_excerpt"] = p["fulltext_excerpt"]
        except Exception as e:  # noqa: BLE001
            logger.warning(f"provenance_agent full-text backfill skipped: {e}")

    # --- Re-anchor paraphrase records once backfilled text is available ---
    span_seq_by_paper: dict[str, int] = {}
    for r in validated:
        if r.get("confidence_basis") != ConfidenceBasis.PARAPHRASE.value:
            continue
        quote = r.get("exact_quote")
        paper = papers_by_id.get(r["paper_id"])
        if not quote or not paper or not (paper.get("full_text") or paper.get("fulltext_excerpt")):
            continue
        extras = anchor_quote_to_paper(paper, quote, r.get("section"))
        if extras["confidence_basis"] == ConfidenceBasis.PARAPHRASE.value:
            continue  # still unlocatable; keep the honest downgrade
        seq = span_seq_by_paper.get(r["paper_id"], 0) + 1
        span_seq_by_paper[r["paper_id"]] = seq
        sid = r.get("evidence_span_id")
        r.update(extras)
        r["evidence_span_id"] = sid or make_span_id(r["paper_id"], seq)

    # --- Resolve the traceability chain; downgrade broken links ---
    for r in validated:
        chain = resolve_record_chain(r, paper_dicts)
        apply_chain_downgrade(r, chain)

    # --- Canonical spans for kept records ---
    # Always rebuild from the record: re-anchoring/backfill may have upgraded
    # page/section/quote after the original span was emitted, so reusing a
    # stale span would make evidence_records and evidence_spans disagree.
    final_spans: list[dict[str, Any]] = []
    for r in validated:
        sid = r.get("evidence_span_id")
        if not sid:
            continue
        final_spans.append(EvidenceSpan(
            span_id=sid,
            paper_id=r["paper_id"],
            text=r.get("exact_quote") or "",
            section=r.get("section", UNKNOWN_SECTION),
            page=r.get("page"),
            char_offset_start=r.get("char_offset_start"),
            char_offset_end=r.get("char_offset_end"),
        ).model_dump())

    claims = [c.model_dump() for c in build_claims(validated, paper_dicts)]

    return {
        "evidence_records": validated,
        "evidence_spans": final_spans,
        "claims": claims,
        "hitl_checkpoint": "checkpoint_2",
        "status": "awaiting_approval"
    }
