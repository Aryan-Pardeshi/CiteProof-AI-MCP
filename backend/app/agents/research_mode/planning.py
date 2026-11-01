"""Phase 1 Planning & Protocol Agents for Research Mode."""

from __future__ import annotations

import logging
import re
from typing import Any

from pydantic import BaseModel, Field

from backend.app.agents.research_mode._common import get_llm_for
from backend.app.llm import ainvoke_structured_with_retry
from backend.app.models.evidence import SearchProtocol

logger = logging.getLogger(__name__)


class ScopeDefinitionOutput(BaseModel):
    refined_problem_statement: str = Field(description="Formal academic problem statement")
    research_objectives: list[str] = Field(default_factory=list, description="List of 3-5 research objectives")
    research_questions: list[str] = Field(default_factory=list, description="List of 3-5 research questions")


class KeywordOutput(BaseModel):
    keywords: list[str] = Field(default_factory=list, description="List of 6-8 diverse academic search queries")


class ScopeRevisionOutput(BaseModel):
    problem_statement: str = Field(description="Updated problem statement incorporating user feedback")
    research_objectives: list[str] = Field(default_factory=list, description="Updated objectives")
    keywords: list[str] = Field(default_factory=list, description="Updated search queries")


def _item_text(item: Any) -> str:
    """Coerce one scope item to non-empty text; models sometimes return objects."""
    if isinstance(item, dict):
        for key in ("text", "objective", "question", "content", "statement"):
            if isinstance(item.get(key), str) and item[key].strip():
                return item[key].strip()
        item = next((v for v in item.values() if isinstance(v, str)), "")
    return str(item or "").strip()


def clean_scope_items(items: Any, fallback: list[str]) -> list[str]:
    """Drop blank/object-shaped items so a checkpoint can never show only 'O1'."""
    cleaned = [text for text in (_item_text(i) for i in (items or [])) if text]
    return cleaned[:5] or fallback


async def scope_definition_agent(state: dict[str, Any]) -> dict[str, Any]:
    """Agent 1: Refines user problem statement and extracts research objectives and questions."""
    ps = state.get("problem_statement", "")
    llm = get_llm_for("planner", state, temperature=0.2)

    prompt = f"""You are an elite Academic Research Director and Methodologist.
Refine the following research topic into a rigorous academic problem statement, research objectives, and research questions.

Topic:
{ps}
"""
    try:
        data: ScopeDefinitionOutput = await ainvoke_structured_with_retry(
            llm, schema=ScopeDefinitionOutput, prompt=prompt, strict=False, max_retries=2
        )
        return {
            "problem_statement": data.refined_problem_statement or ps,
            "research_objectives": clean_scope_items(
                data.research_objectives, [f"Investigate core dimensions of {ps}"]
            ),
            "research_questions": clean_scope_items(
                data.research_questions, [f"What are the empirical impacts of {ps}?"]
            ),
            "status": "in_progress"
        }
    except Exception as e:  # noqa: BLE001
        logger.warning(f"scope_definition structured invocation failed: {e}. Using fallback.")
        return {
            "problem_statement": ps,
            "research_objectives": [f"Investigate core dimensions of {ps}"],
            "research_questions": [f"What are the empirical impacts of {ps}?"],
            "status": "in_progress"
        }


async def protocol_agent(state: dict[str, Any]) -> dict[str, Any]:
    """Agent 2: Formulates PICOC framework, inclusion/exclusion criteria, and search strategy."""
    ps = state.get("problem_statement", "")
    objs = clean_scope_items(state.get("research_objectives"), [])
    llm = get_llm_for("planner", state, temperature=0.1)

    prompt = f"""You are an expert Systematic Review Methodologist adhering to PRISMA 2020 protocols.
Formulate the Search Protocol and PICOC scope for:

Problem Statement:
{ps}

Objectives:
{chr(10).join(f"- {o}" for o in objs)}
"""
    try:
        protocol: SearchProtocol = await ainvoke_structured_with_retry(
            llm, schema=SearchProtocol, prompt=prompt, strict=False, max_retries=2
        )
        return {"search_protocol": protocol.model_dump()}
    except Exception as e:  # noqa: BLE001
        logger.warning(f"protocol_agent structured invocation failed: {e}. Using default protocol.")
        protocol = SearchProtocol(
            population=ps,
            intervention="Contemporary AI architectures",
            outcomes=["Performance", "Accuracy", "Scalability"],
            inclusion_criteria=["Peer-reviewed academic publications", "Empirical benchmark evaluations"],
            exclusion_criteria=["Opinion pieces", "Duplicate publications"]
        )
        return {"search_protocol": protocol.model_dump()}


async def keyword_extractor_agent(state: dict[str, Any]) -> dict[str, Any]:
    """Agent 3: Generates targeted academic search keywords and Boolean search queries."""
    ps = state.get("problem_statement", "")
    protocol = state.get("search_protocol") or {}
    llm = get_llm_for("planner", state, temperature=0.2)

    prompt = f"""You are an expert Academic Information Retrieval Specialist.
Generate 6-8 diverse, highly effective academic search keywords and Boolean query phrases for:

Problem Statement: {ps}
Target Intervention: {protocol.get('intervention', '')}

Rules:
1. Never emit bare or unexpanded abbreviations/acronyms (e.g. do NOT write bare 'SRT' or 'SCT'). Always expand acronyms into full terms, e.g. '("sleep restriction therapy" OR "sleep restriction")' instead of bare 'SRT'.
2. Provide a balanced set of search strings combining primary intervention, condition/population, and study outcomes.
"""
    try:
        out: KeywordOutput = await ainvoke_structured_with_retry(
            llm, schema=KeywordOutput, prompt=prompt, strict=False, max_retries=2
        )
        if out.keywords:
            clean_kws = [str(k).strip() for k in out.keywords if str(k).strip()][:8]
            if clean_kws:
                return {"keywords": clean_kws, "hitl_checkpoint": "checkpoint_1", "status": "awaiting_approval"}
    except Exception as e:  # noqa: BLE001
        logger.warning(f"keyword_extractor structured invocation failed: {e}")

    words = re.findall(r"\w+", ps)
    fallback_kws = [k for k in ([ps[:60].strip()] + [f"{w} research" for w in words[:4] if len(w) > 4]) if k]
    if not fallback_kws:
        fallback_kws = ["academic literature research", "empirical evaluation benchmarks"]
    return {"keywords": fallback_kws[:6], "hitl_checkpoint": "checkpoint_1", "status": "awaiting_approval"}


async def scope_reviser_agent(state: dict[str, Any]) -> dict[str, Any]:
    """Agent 4: Incorporates user feedback at Checkpoint 1 to revise scope, protocol, and keywords."""
    feedback = state.get("user_feedback", "")
    ps = state.get("problem_statement", "")
    objs = state.get("research_objectives", [])
    kws = state.get("keywords", [])
    llm = get_llm_for("planner", state, temperature=0.2)

    prompt = f"""The researcher provided the following feedback on the initial scope & keywords:
User Feedback: {feedback}

Current Problem Statement: {ps}
Current Objectives: {objs}
Current Keywords: {kws}

Revise the research plan incorporating the user's specific feedback.
"""
    try:
        out: ScopeRevisionOutput = await ainvoke_structured_with_retry(
            llm, schema=ScopeRevisionOutput, prompt=prompt, strict=False, max_retries=2
        )
        return {
            "problem_statement": out.problem_statement or ps,
            "research_objectives": clean_scope_items(out.research_objectives, objs),
            "keywords": out.keywords or kws,
            "hitl_checkpoint": "checkpoint_1",
            "status": "awaiting_approval"
        }
    except Exception as e:  # noqa: BLE001
        logger.warning(f"scope_reviser structured invocation failed: {e}")
        return {
            "problem_statement": ps,
            "research_objectives": objs,
            "keywords": kws,
            "hitl_checkpoint": "checkpoint_1",
            "status": "awaiting_approval"
        }
