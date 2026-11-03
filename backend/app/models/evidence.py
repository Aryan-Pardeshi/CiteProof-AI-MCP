"""Typed data models and deterministic evidence store for AI Research Assistant.

These Pydantic models form the single source of truth for the research pipeline,
guaranteeing mathematical PRISMA tracking, provenance anchoring for every claim,
and strict separation between literature review findings and proposed future research.
"""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class ConfidenceBasis(str, Enum):
    """How a claim was anchored to its source; drives the confidence score."""

    EXACT_QUOTE_FULLTEXT = "exact_quote_fulltext"
    EXACT_QUOTE_ABSTRACT = "exact_quote_abstract"
    PARAPHRASE = "paraphrase"


# Sentinel used when a section name genuinely cannot be derived from the source.
UNKNOWN_SECTION = "unknown"

# Extraction-method confidence rule. Scores are derived deterministically from
# HOW a claim was anchored, never guessed:
#   exact verbatim span located in fetched full text  -> 0.90 (high)
#   exact verbatim span located in abstract only      -> 0.65 (medium; no page context)
#   paraphrase / inference without verbatim anchor    -> 0.40 (low)
# Modifiers: +0.05 when an exact page number is known,
#            -0.20 when the traceability chain is incomplete (no URL/DOI locator).
# Result is clamped to [0.05, 0.95].
CONFIDENCE_BASE = {
    ConfidenceBasis.EXACT_QUOTE_FULLTEXT: 0.90,
    ConfidenceBasis.EXACT_QUOTE_ABSTRACT: 0.65,
    ConfidenceBasis.PARAPHRASE: 0.40,
}
CONFIDENCE_PAGE_BONUS = 0.05
CONFIDENCE_CHAIN_PENALTY = 0.20
CONFIDENCE_MIN = 0.05
CONFIDENCE_MAX = 0.95

# Shared description for the `verification_status` fields. The status is a
# resolution check (did the quote and every chain link locate?), not a support check.
_RESOLUTION_STATUS_DESCRIPTION = (
    "Resolution only: 'resolved' means the exact quote was located in the source text "
    "and every chain link (paper, span, URL/DOI) is present; it does not assert that "
    "the quote supports the claim text."
)


def make_paper_id(doi: str | None, title: str, year: str | None = None) -> str:
    """Generate a deterministic 16-character SHA-256 hash paper ID.
    
    DOI is canonical; normalized title is used as fallback.
    """
    if doi and doi.strip():
        # Canonicalize DOI
        doi_clean = doi.strip().lower()
        if doi_clean.startswith("https://doi.org/"):
            doi_clean = doi_clean[len("https://doi.org/"):]
        elif doi_clean.startswith("http://doi.org/"):
            doi_clean = doi_clean[len("http://doi.org/"):]
        elif doi_clean.startswith("doi:"):
            doi_clean = doi_clean[len("doi:"):]
        key = f"doi:{doi_clean}"
    else:
        # Normalize title: collapse whitespace first, then strip disallowed characters
        cleaned = re.sub(r"\s+", " ", (title or "").lower().strip())
        normalized = re.sub(r"[^a-z0-9 ]", "", cleaned).strip()
        key = f"title:{normalized}:{year or 'nd'}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def make_evidence_id(paper_id: str, claim_index: int) -> str:
    """Generate a scoped evidence ID: paper_id prefix + sequential claim index."""
    return f"{paper_id}_ev{claim_index:03d}"


def make_claim_id(section: str, claim_index: int) -> str:
    """Generate a section-scoped review claim ID."""
    clean_sec = re.sub(r"[^a-z0-9]", "", (section or "sec").lower())[:8]
    return f"{clean_sec}_cl{claim_index:03d}"


def make_span_id(paper_id: str, span_index: int) -> str:
    """Generate a scoped evidence-span ID: {paper_id}_sp{index:03d}."""
    return f"{paper_id}_sp{span_index:03d}"


def score_confidence(
    basis: ConfidenceBasis | str,
    page_known: bool = False,
    chain_complete: bool = True,
) -> float:
    """Apply the explicit extraction-method confidence rule.

    basis:   anchoring method (see CONFIDENCE_BASE for the documented table)
    page_known: exact page number was derived from the source, not guessed
    chain_complete: Claim -> Span -> Paper -> locator (URL or DOI) all resolve
    """
    key = basis.value if isinstance(basis, ConfidenceBasis) else str(basis)
    score = CONFIDENCE_BASE.get(key, CONFIDENCE_BASE[ConfidenceBasis.PARAPHRASE])
    if page_known:
        score += CONFIDENCE_PAGE_BONUS
    if not chain_complete:
        score -= CONFIDENCE_CHAIN_PENALTY
    return round(min(max(score, CONFIDENCE_MIN), CONFIDENCE_MAX), 2)


def confidence_label(confidence: float) -> Literal["high", "medium", "low"]:
    """Bucket a confidence float into a human-readable label."""
    if confidence >= 0.75:
        return "high"
    if confidence >= 0.5:
        return "medium"
    return "low"


_WHITESPACE_RE = re.compile(r"\s+")


def _normalize_with_index_map(document: str) -> tuple[str, list[int]]:
    """Lowercase and collapse whitespace, remembering each output char's origin.

    Returns (normalized_text, index_map) where index_map[i] is the offset in the
    original document that produced normalized_text[i]. Form-feed page markers
    participate in whitespace collapsing but stay countable via the original doc.
    """
    out: list[str] = []
    idx_map: list[int] = []
    prev_space = True
    for i, ch in enumerate(document):
        if ch.isspace():
            if not prev_space:
                out.append(" ")
                idx_map.append(i)
                prev_space = True
            continue
        # lower() can expand one char into several (e.g. 'İ' -> 'i̇'); every
        # produced char maps back to the same origin offset so idx_map never
        # desyncs from the normalized string.
        for lowered in ch.lower():
            out.append(lowered)
            idx_map.append(i)
        prev_space = False
    while out and out[-1] == " ":
        out.pop()
        idx_map.pop()
    return "".join(out), idx_map


def locate_quote(
    document: str | None,
    quote: str,
) -> tuple[int, int, int | None] | None:
    """Find a whitespace/case-insensitive verbatim match inside document text.

    Returns (char_offset_start, char_offset_end, page) where the offsets index
    into the ORIGINAL document string, and page is derived by counting
    form-feed page markers ("\\f") emitted by the PDF full-text fetcher.
    page is None when the document carries no page markers (abstract-only
    results), never a guess. Returns None when the quote does not appear.
    """
    if not document or not quote:
        return None
    norm_doc, idx_map = _normalize_with_index_map(document)
    norm_quote = _WHITESPACE_RE.sub(" ", quote.lower()).strip()
    if not norm_quote:
        return None
    pos = norm_doc.find(norm_quote)
    if pos < 0:
        return None
    start_orig = idx_map[pos]
    end_pos = pos + len(norm_quote) - 1
    end_orig = idx_map[end_pos] + 1 if end_pos < len(idx_map) else len(document)
    page = document[:start_orig].count("\f") + 1 if "\f" in document else None
    return start_orig, end_orig, page


class PaperSection(BaseModel):
    """A named section of a paper with its page range when known."""

    name: str
    text: str = ""
    page_start: int | None = None
    page_end: int | None = None


class ProvenanceInfo(BaseModel):
    """Where a paper came from and how it was fetched."""

    source_provider: str
    retrieval_method: str = "api_search"
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    url: str = ""


class EvidenceSpan(BaseModel):
    """Exact quoted span from a paper — the atomic anchor of every claim."""

    span_id: str = Field(..., description="Scoped ID in format {paper_id}_sp001")
    paper_id: str = Field(..., description="Foreign key to PaperRecord.paper_id")
    text: str = Field(..., description="Exact quoted span from the source document")
    section: str = UNKNOWN_SECTION
    page: int | None = None
    char_offset_start: int | None = None
    char_offset_end: int | None = None


class Claim(BaseModel):
    """A surfaced claim that must resolve the full traceability chain.

    Invariant enforced by validation: paper_id is mandatory, section always
    carries a non-null value ('unknown' sentinel), source_url is never None.
    Claims whose chain cannot be resolved are downgraded to unresolved +
    low confidence by build_evidence_chains instead of silently surfacing.
    """

    claim_id: str = Field(
        default="",
        description="Scoped claim identifier; assigned by build_claims when empty"
    )
    text: str = Field(..., description="The claim as stated")
    paper_id: str = Field(..., description="FK to PaperRecord.paper_id")
    evidence_span_id: str = Field(..., description="FK to EvidenceSpan.span_id")
    section: str = UNKNOWN_SECTION
    page: int | None = None
    confidence: float = Field(default=0.4, ge=0.0, le=1.0)
    confidence_basis: ConfidenceBasis = ConfidenceBasis.PARAPHRASE
    verification_status: Literal["resolved", "unresolved"] = Field(
        default="unresolved",
        description=_RESOLUTION_STATUS_DESCRIPTION,
    )
    source_url: str = ""
    doi: str | None = None


class PaperRecord(BaseModel):
    """Structured representation of an academic paper retrieved during search."""
    
    paper_id: str = Field(
        ...,
        description="Deterministic 16-hex hash of DOI or normalized title"
    )
    doi: str | None = None
    pmid: str | None = None
    arxiv_id: str | None = None
    title: str
    authors: list[str] = Field(default_factory=list)
    # Author name (as it appears in `authors`) -> ORCID iD. Set only on a confident
    # ORCID match (see tools/orcid_search.py); unmatched authors have no entry.
    author_orcids: dict[str, str] = Field(default_factory=dict)
    year: str = "n.d."
    venue: str | None = None
    abstract: str = ""
    source_url: str = ""
    pdf_url: str | None = None
    fulltext_excerpt: str | None = None
    full_text: str | None = Field(
        None,
        description="Complete extracted document text with \\f page markers when available"
    )
    sections: list[PaperSection] = Field(default_factory=list)
    provenance: ProvenanceInfo | None = None
    retrieval_source: str = "unknown"  # openalex, semantic_scholar, arxiv, crossref, pubmed, tavily
    citation_count: int = 0
    relevance_score: float | None = None
    study_type: Literal["empirical", "benchmark", "review", "theoretical", "survey", "meta-analysis", "other"] | None = "empirical"
    quality_rating: Literal["High", "Medium", "Low", "Unclear"] | None = "Unclear"
    quality_rubric: dict[str, Any] | None = None
    screening_status: Literal["retrieved", "screened", "included", "excluded"] = "retrieved"
    exclusion_reason: str | None = None
    # Set by sources that report retractions (OpenAlex); screening excludes these.
    is_retracted: bool = False
    hypothesis_support: dict[str, str] = Field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PaperRecord:
        """Create a PaperRecord from raw paper dictionary with auto-generated paper_id."""
        d = dict(data)
        if "paper_id" not in d or not d["paper_id"]:
            d["paper_id"] = make_paper_id(d.get("doi"), d.get("title", ""), d.get("year"))
        return cls(**d)


class EvidenceRecord(BaseModel):
    """Structured, verifiable unit of empirical or theoretical evidence from a paper."""
    
    evidence_id: str = Field(
        ...,
        description="Scoped identifier in format {paper_id}_ev001"
    )
    paper_id: str = Field(
        ...,
        description="Foreign key to PaperRecord.paper_id"
    )
    claim_summary: str = Field(
        ...,
        description="Concise description of the specific factual claim or finding"
    )
    exact_quote: str | None = Field(
        None,
        description="Verbatim text from the source paper demonstrating provenance"
    )
    source_section: str | None = Field(
        None,
        description="Legacy alias for section; populated alongside section"
    )
    section: str = Field(
        UNKNOWN_SECTION,
        description="Canonical section name; 'unknown' when not derivable from the source"
    )
    evidence_span_id: str | None = Field(
        None,
        description="Foreign key to EvidenceSpan.span_id anchoring this record"
    )
    page: int | None = Field(
        None,
        description="Exact page number in the source document; None when unavailable"
    )
    char_offset_start: int | None = None
    char_offset_end: int | None = None
    confidence: float = Field(
        default=CONFIDENCE_BASE[ConfidenceBasis.PARAPHRASE],
        ge=0.0,
        le=1.0,
        description="Deterministic score from the extraction-method rule (see score_confidence)"
    )
    confidence_basis: ConfidenceBasis = ConfidenceBasis.PARAPHRASE
    verification_status: Literal["resolved", "unresolved"] = Field(
        default="unresolved",
        description=_RESOLUTION_STATUS_DESCRIPTION,
    )
    source_url: str = ""
    doi: str | None = None
    task_or_domain: str | None = None
    dataset: str | None = None
    model_or_method: str | None = None
    sample_size: str | None = None
    metric_name: str | None = None
    baseline_value: float | None = None
    reported_value: float | None = None
    unit_or_scale: str | None = None
    effect_direction: Literal["positive", "negative", "neutral", "mixed", "unclear"] | None = "unclear"
    confidence_interval_or_p: str | None = None
    limitations: str | None = None
    reproducibility_available: bool = False
    hypothesis_relevance: dict[str, Literal["Supports", "Refutes", "Neutral", "Partial", "Not Applicable"]] = Field(
        default_factory=dict
    )


class ReviewClaim(BaseModel):
    """A claim generated in the paper prose with links to supporting evidence."""
    
    claim_id: str = Field(
        ...,
        description="Identifier in format {section}_cl001"
    )
    claim_text: str = Field(
        ...,
        description="The written sentence or thesis in the paper"
    )
    target_section: str = Field(
        ...,
        description="Target section: 'literature_review', 'results', 'discussion', etc."
    )
    supporting_evidence_ids: list[str] = Field(
        default_factory=list,
        description="List of EvidenceRecord.evidence_id grounding this claim"
    )
    is_quantitative: bool = False
    quantitative_metric: str | None = None
    quantitative_value: float | None = None
    validation_status: Literal["resolved", "unsupported", "flagged", "pending"] = Field(
        default="pending",
        description=(
            "'resolved': the claim -> evidence -> paper -> source-locator chain resolves. "
            "'flagged': that chain is broken. 'unsupported': a quantitative claim whose "
            "numbers do not match the cited evidence values. 'pending': not yet checked. "
            "No value asserts that the cited passage supports the sentence; no validator "
            "performs that check."
        ),
    )
    integrity_notes: str | None = None


class PRISMATracker(BaseModel):
    """Deterministic PRISMA 2020 flow tracker with mathematical invariants."""
    
    records_identified: int = 0
    records_by_source: dict[str, int] = Field(default_factory=dict)
    duplicates_removed: int = 0
    records_after_dedup: int = 0
    records_screened: int = 0
    excluded_title_abstract: int = 0
    full_text_requested: int = 0
    full_text_unavailable: int = 0
    full_text_assessed: int = 0
    excluded_full_text: int = 0
    studies_included: int = 0
    exclusion_reasons: dict[str, int] = Field(default_factory=dict)

    def validate_invariants(self) -> list[str]:
        """Validate mathematical conservation laws of the PRISMA flowchart."""
        errors = []
        if self.records_identified - self.duplicates_removed != self.records_after_dedup:
            errors.append(
                f"PRISMA Invariant Broken: identified ({self.records_identified}) - "
                f"duplicates ({self.duplicates_removed}) != after_dedup ({self.records_after_dedup})"
            )
        expected_screened_excluded = self.records_screened - self.excluded_title_abstract
        if self.full_text_requested > 0 and self.full_text_requested != expected_screened_excluded:
            errors.append(
                f"PRISMA Invariant Broken: screened ({self.records_screened}) - "
                f"excluded_title_abstract ({self.excluded_title_abstract}) != full_text_requested ({self.full_text_requested})"
            )
        if self.full_text_assessed > 0:
            expected_included = self.full_text_assessed - self.excluded_full_text
            if self.studies_included != expected_included:
                errors.append(
                    f"PRISMA Invariant Broken: full_text_assessed ({self.full_text_assessed}) - "
                    f"excluded_full_text ({self.excluded_full_text}) != studies_included ({self.studies_included})"
                )
        return errors


class SearchProtocol(BaseModel):
    """PICOC research scope and Boolean search protocol."""
    
    population: str = ""
    intervention: str = ""
    comparison: str = ""
    outcomes: list[str] = Field(default_factory=list)
    context: str = ""
    inclusion_criteria: list[str] = Field(default_factory=list)
    exclusion_criteria: list[str] = Field(default_factory=list)
    boolean_queries: list[str] = Field(default_factory=list)
    search_keywords: list[str] = Field(default_factory=list)


class TaxonomyTheme(BaseModel):
    """A theme or category in the synthesized literature taxonomy."""
    
    theme_id: str
    theme_name: str
    description: str
    paper_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    subthemes: list[str] = Field(default_factory=list)


class ValidationReport(BaseModel):
    """Audit report generated by the deterministic validation pipeline."""
    
    # Citation counts are resolution-only: a citation is "verified" when its
    # (first-author surname, year) matches a PaperRecord, not when that paper
    # supports the sentence the citation is attached to.
    total_inline_citations: int = 0
    verified_citations: int = 0
    unverified_citations: list[str] = Field(default_factory=list)
    orphan_references: list[str] = Field(default_factory=list)
    total_quantitative_claims: int = 0
    grounded_quantitative_claims: int = 0
    unsupported_quantitative_claims: list[str] = Field(default_factory=list)
    # ReviewClaim manifest audit: rendered prose claims resolving the full
    # claim_id -> evidence_id -> paper_id -> source_url/doi chain.
    total_review_claims: int = 0
    resolved_review_claims: int = 0
    unresolved_review_claims: list[str] = Field(default_factory=list)
    integrity_flags: list[str] = Field(default_factory=list)
    prisma_invariants_valid: bool = True
    passed_all_gates: bool = True


# ---------------------------------------------------------------------------
# Traceability chain resolution
# ---------------------------------------------------------------------------
#
# Claim -> EvidenceSpan -> Paper -> exact location (section+page) -> URL/DOI.
# resolve_record_chain walks the full chain for one evidence record. A missing
# link never blocks surfacing outright; instead the claim is downgraded to
# unresolved with a deterministically reduced confidence score.


def _basis_of(record: dict[str, Any]) -> ConfidenceBasis:
    raw = record.get("confidence_basis") or ConfidenceBasis.PARAPHRASE
    try:
        return ConfidenceBasis(raw)
    except ValueError:
        return ConfidenceBasis.PARAPHRASE


def resolve_record_chain(
    record: dict[str, Any],
    paper_records: list[dict[str, Any]],
) -> dict[str, Any]:
    """Resolve Claim -> EvidenceSpan -> Paper -> location -> locator for one record.

    Returns the machine-readable evidence-chain dict attached alongside
    human-readable citations in API output. Missing links are listed explicitly
    and downgrade verification_status + confidence; nothing is guessed.
    """
    papers_by_id = {p.get("paper_id"): p for p in paper_records if p.get("paper_id")}
    paper = papers_by_id.get(record.get("paper_id"))

    missing_links: list[str] = []
    if paper is None:
        missing_links.append("paper_not_in_corpus")

    span_id = record.get("evidence_span_id")
    exact_quote = (record.get("exact_quote") or "").strip()
    if not span_id:
        missing_links.append("evidence_span_missing")

    source_url = str(record.get("source_url") or (paper or {}).get("source_url") or "")
    doi = record.get("doi") or (paper or {}).get("doi")
    if not source_url and not doi:
        missing_links.append("source_locator_missing")

    basis = _basis_of(record)
    page = record.get("page")
    section = record.get("section") or UNKNOWN_SECTION

    # The documented -0.20 chain penalty applies specifically to a missing
    # URL/DOI locator; other missing links downgrade verification_status but
    # do not silently alter the extraction-method score.
    locator_missing = "source_locator_missing" in missing_links
    confidence = score_confidence(
        basis,
        page_known=page is not None,
        chain_complete=not locator_missing,
    )
    if "paper_not_in_corpus" in missing_links:
        # No resolvable source document at all: never trust above paraphrase grade.
        confidence = min(confidence, CONFIDENCE_BASE[ConfidenceBasis.PARAPHRASE])

    resolved = not missing_links and basis != ConfidenceBasis.PARAPHRASE

    return {
        "claim_id": record.get("evidence_id", ""),
        "claim": record.get("claim_summary", ""),
        "paper_id": record.get("paper_id"),
        "paper_title": (paper or {}).get("title", ""),
        "evidence_span_id": span_id,
        "evidence": {
            "span_id": span_id,
            "exact_quote": exact_quote or None,
            "section": section,
            "page": page,
            "char_offset_start": record.get("char_offset_start"),
            "char_offset_end": record.get("char_offset_end"),
        },
        "section": section,
        "page": page,
        "confidence": confidence,
        "confidence_label": confidence_label(confidence),
        "confidence_basis": basis.value,
        "verification_status": "resolved" if resolved else "unresolved",
        "missing_links": missing_links,
        "source_url": source_url,
        "doi": doi,
    }


def apply_chain_downgrade(
    record: dict[str, Any],
    chain: dict[str, Any],
) -> dict[str, Any]:
    """Write resolved chain verdicts back onto an evidence record dict."""
    record["confidence"] = chain["confidence"]
    record["confidence_label"] = chain["confidence_label"]
    record["verification_status"] = chain["verification_status"]
    record["source_url"] = chain["source_url"]
    record["doi"] = chain["doi"]
    return record


def build_evidence_chains(
    evidence_records: list[dict[str, Any]],
    paper_records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Resolve the traceability chain for every evidence record."""
    return [resolve_record_chain(r, paper_records) for r in evidence_records]


def build_claims(
    evidence_records: list[dict[str, Any]],
    paper_records: list[dict[str, Any]],
) -> list[Claim]:
    """Materialize validated Claim objects from anchored evidence records.

    Records without an evidence span cannot form a traceable claim and are
    skipped rather than emitted as free-text assertions.
    """
    claims: list[Claim] = []
    for r in evidence_records:
        if not r.get("evidence_span_id"):
            continue
        chain = resolve_record_chain(r, paper_records)
        claims.append(Claim(
            claim_id=chain["claim_id"] or make_claim_id(chain["section"], len(claims) + 1),
            text=r.get("claim_summary", ""),
            paper_id=r["paper_id"],
            evidence_span_id=r["evidence_span_id"],
            section=chain["section"],
            page=chain["page"],
            confidence=chain["confidence"],
            confidence_basis=_basis_of(r),
            verification_status=chain["verification_status"],
            source_url=chain["source_url"],
            doi=chain["doi"],
        ))
    return claims
