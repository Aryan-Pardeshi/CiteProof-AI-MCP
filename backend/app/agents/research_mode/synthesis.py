"""Phase 5 Synthesis & Theoretical Framing Agents for Research Mode."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from langchain_core.messages import HumanMessage

from backend.app.agents.research_mode._common import (
    _safe_invoke_llm,
    get_llm_for,
    write_required_section,
)

logger = logging.getLogger(__name__)


async def taxonomy_agent(state: dict[str, Any]) -> dict[str, Any]:
    """Agent 16: Synthesizes evidence records into a multi-dimensional thematic taxonomy."""
    ev_dicts = state.get("evidence_records") or []
    ps = state.get("problem_statement", "")
    llm = get_llm_for("planner", state, temperature=0.2)

    ev_summaries = "\n".join(f"- {e.get('claim_summary')}" for e in ev_dicts[:12])

    prompt = f"""You are a Principal Academic Synthesizer.
Classify the following evidence items into 3-4 structured thematic categories or taxonomy dimensions for:
Problem Statement: {ps}

Evidence Claims:
{ev_summaries}

Return a valid JSON array with schema:
[
  {{
    "theme_id": "theme_1",
    "theme_name": "Name of Theme (e.g. Architectural Innovations)",
    "description": "2-3 sentence overview of this research dimension",
    "subthemes": ["Subtopic A", "Subtopic B"]
  }}
]
"""
    try:
        raw = await _safe_invoke_llm(llm, [HumanMessage(content=prompt)])
        clean = raw
        if "```" in clean:
            clean = clean.split("```")[1]
            clean = clean.removeprefix("json")
        themes_data = json.loads(clean.strip())
        # Reject mappings, strings, and any other JSON value: the prompt schema is
        # an array of theme objects.
        if not isinstance(themes_data, list) or not all(isinstance(t, dict) for t in themes_data):
            raise ValueError(f"taxonomy payload is not a list of theme objects (got {type(themes_data).__name__})")
        if not themes_data:
            raise ValueError("taxonomy payload contained no themes")
        return {"taxonomy": {"themes": themes_data}}
    except Exception as e:  # noqa: BLE001
        logger.warning(f"taxonomy_agent JSON parse defaulted: {e}")
        default_themes = [
            {"theme_id": "theme_1", "theme_name": "Foundational Architectures", "description": "Core algorithmic and structural foundations.", "subthemes": []},
            {"theme_id": "theme_2", "theme_name": "Empirical Performance & Benchmarking", "description": "Quantitative benchmark evaluation across datasets.", "subthemes": []},
            {"theme_id": "theme_3", "theme_name": "Operational Constraints & Scalability", "description": "Resource efficiency, throughput, and deployment challenges.", "subthemes": []}
        ]
        return {"taxonomy": {"themes": default_themes}}


async def gap_analysis_agent(state: dict[str, Any]) -> dict[str, Any]:
    """Agent 17: Synthesizes empirical contradictions and identifies critical research gaps."""
    ps = state.get("problem_statement", "")
    ev_dicts = state.get("evidence_records") or []
    llm = get_llm_for("researcher", state, temperature=0.2)

    ev_text = "\n".join(f"- {e.get('claim_summary')} (Limitations: {e.get('limitations')})" for e in ev_dicts[:10])

    prompt = f"""You are a Critical Literature Analyst.
Identify the major research gaps, conflicting findings, and methodological limitations across the evidence base for:
Topic: {ps}

Evidence Base:
{ev_text}

Provide a comprehensive, highly rigorous academic synthesis of the Research Gaps (3-4 paragraphs in Markdown format).
Include specific subsections:
- 1. Methodological Gaps
- 2. Empirical Inconsistencies & Contradictions
- 3. Unaddressed Boundary Conditions

Instructions:
- Write only the body of this section; do not write a document title, an abstract, or '#'/'##' headings; use '###' for subsections if needed.
"""
    compact_prompt = f"""You are a Critical Literature Analyst.
Identify the major research gaps and methodological limitations for:
Topic: {ps}

Evidence Base:
{ev_text[:800]}

Write 2-3 paragraphs covering methodological gaps, inconsistent findings, and
unaddressed boundary conditions. Write only the body; no document title,
abstract, or '#'/'##' headings.
"""
    # Research Gap is a required paper section: an empty model response must be
    # retried and then fail the run, never silently drop the section from the
    # exported paper.
    body = await write_required_section(
        _safe_invoke_llm,
        llm,
        prompt,
        section="Research Gap",
        compact_prompt=compact_prompt,
    )
    return {"research_gap": body}


async def conceptual_framework_agent(state: dict[str, Any]) -> dict[str, Any]:
    """Agent 18: Constructs theoretical framework mapping independent, dependent, and moderating constructs."""
    ps = state.get("problem_statement", "")
    gap = state.get("research_gap", "")
    llm = get_llm_for("planner", state, temperature=0.2)

    prompt = f"""You are a Senior Theoretical Framework Architect.
Construct a formal Academic Conceptual Framework addressing the identified research gaps for:
Problem Statement: {ps}

Identified Gaps:
{gap[:800]}

Provide a detailed Conceptual Framework in Markdown format including:
- Core Theoretical Constructs (Independent, Dependent, Mediating, and Moderating Variables)
- Structural Relationships & Mechanistic Pathways
- Theoretical Grounding (linking constructs to established scientific paradigms)

Instructions:
- Write only the body of this section; do not write a document title, an abstract, or '#'/'##' headings; use '###' for subsections if needed.
- Do NOT repeat, re-list, or re-synthesize the Research Gaps section or include subsections titled 'Methodological Gaps' or 'Integration of Identified Methodological Gaps'; that analysis belongs exclusively in Section 3 (Research Gap). Focus strictly on theoretical constructs, structural relationships, and mechanisms.
"""
    compact_prompt = f"""You are a Senior Theoretical Framework Architect.
Construct a concise but complete Conceptual Framework for:
Problem Statement: {ps}
Gaps: {gap[:400]}

Cover independent, dependent, mediating, and moderating constructs, their structural relationships, and theoretical grounding.
Write only the body; no document title, abstract, or '#'/'##' headings.
"""
    body = await write_required_section(
        _safe_invoke_llm,
        llm,
        prompt,
        section="Conceptual Framework",
        compact_prompt=compact_prompt,
    )
    return {"conceptual_framework": body}


async def hypotheses_agent(state: dict[str, Any]) -> dict[str, Any]:
    """Agent 19: Formulates directional, testable hypotheses H1..H5 grounded in extracted evidence."""
    ps = state.get("problem_statement", "")
    framework = state.get("conceptual_framework", "")
    evidence_records = state.get("evidence_records") or []
    claims = state.get("claims") or []
    corpus_stats = state.get("corpus_stats") or {}
    included_count = corpus_stats.get("included") or len(
        [p for p in state.get("paper_records", []) if p.get("screening_status") == "included"]
    )
    fulltext_count = corpus_stats.get("fulltext_fetched", 0)
    is_abstract_only = fulltext_count == 0

    # Statistical markers only: bare numbers (years, sample sizes) are not findings.
    num_pattern = re.compile(
        r"\b[pP]\s*[<>=≤]|\b\d+(?:\.\d+)?\s*%|\bCI\b|\bSMD\b|\bRR\b|\bOR\b|\bHR\b"
        r"|\b(?:Cohen|Hedges)'?s?\s*[dg]\b|\b[dg]\s*=\s*-?\d"
    )
    numeric_claims_count = sum(
        1 for r in evidence_records
        if num_pattern.search(str(r.get("claim_summary", "")) + " " + str(r.get("exact_quote", "")))
    )

    evidence_constraints = f"""
Available Evidence Base:
- Included studies: {included_count}
- Full-text manuscripts retrieved: {fulltext_count} ({'Abstract-only review: open-access full-texts unavailable' if is_abstract_only else 'Full-text available'})
- Total extracted empirical claims: {max(len(evidence_records), len(claims))}
- Quantitative statistical findings: {numeric_claims_count}

CRITICAL CONSTRAINTS ON HYPOTHESIS FORMULATION:
1. Hypotheses MUST be strictly constrained by what the extracted evidence actually supports.
2. {'Because this review is based on abstracts only (' + str(included_count) + ' included studies), do NOT formulate hypotheses that assume pooled standardized mean differences (SMD), meta-analytic subgroup moderators, dose-response kinetics, or statistical meta-regression models that cannot be tested with abstract-level data.' if is_abstract_only or numeric_claims_count < 3 else 'Ground all hypotheses in observed empirical effect sizes.'}
3. Formulate directional, testable comparative hypotheses focused on primary intervention outcomes and relative patterns observable from the included evidence.
"""

    llm = get_llm_for("researcher", state, temperature=0.2)

    prompt = f"""You are an Expert Hypothesis Formulator.
Formulate 4-5 rigorous, testable, directional academic hypotheses (H1 to H5) for:
Problem Statement: {ps}

Conceptual Framework Context:
{framework[:1000]}
{evidence_constraints}

Return a valid JSON array of strings:
[
  "H1: Detailed directional hypothesis statement...",
  "H2: Detailed directional hypothesis statement...",
  "H3: Detailed directional hypothesis statement...",
  "H4: Detailed directional hypothesis statement...",
  "H5: Detailed directional hypothesis statement..."
]
"""
    raw = await _safe_invoke_llm(llm, [HumanMessage(content=prompt)])
    hyp_list: list[str] = []
    try:
        clean = raw
        if "```" in clean:
            clean = clean.split("```")[1]
            clean = clean.removeprefix("json")
        parsed = json.loads(clean.strip())
        if not isinstance(parsed, list):
            raise TypeError(f"hypotheses payload is not a list (got {type(parsed).__name__})")
        # Normalize object entries to their text value; anything else is rejected
        # so downstream writers always receive a list of strings.
        normalized: list[str] = []
        for item in parsed:
            if isinstance(item, str):
                text = item.strip()
            elif isinstance(item, dict):
                text = str(
                    item.get("hypothesis")
                    or item.get("text")
                    or item.get("statement")
                    or item.get("description")
                    or ""
                ).strip()
            else:
                text = ""
            if text:
                normalized.append(text)
        if not normalized:
            raise ValueError("hypotheses payload contained no usable statements")
        hyp_list = normalized
    except Exception:  # noqa: BLE001
        # Fallback regex extraction of H1..H5
        lines = [line.strip() for line in raw.split("\n") if line.strip().startswith(("H1", "H2", "H3", "H4", "H5", "1.", "2.", "3.", "4.", "5."))]
        hyp_list = lines if lines else [
            f"H1: Active interventions yield significant improvements in primary outcomes for {ps[:50]}.",
            f"H2: Comparative intervention efficacy differs across delivery formats in {ps[:50]}.",
            "H3: Treatment response is maintained at follow-up assessments across evaluated studies."
        ]

    # Copy papers cleanly without injecting synthetic modulo ratings
    paper_dicts = state.get("paper_records") or []
    copied_papers = [dict(p) for p in paper_dicts]

    return {
        "hypotheses": hyp_list,
        "paper_records": copied_papers,
        "hitl_checkpoint": "checkpoint_3",
        "status": "awaiting_approval"
    }
