"""Phase 3 Screening & Quality Appraisal Agents for Research Mode."""

from __future__ import annotations

import json
import logging
from typing import Any

from langchain_core.messages import HumanMessage

from backend.app.agents.research_mode._common import (
    _safe_invoke_llm,
    get_llm_for,
    included_papers,
)
from backend.app.models.evidence import PaperRecord, PRISMATracker
from backend.app.tools.academic_search import screen_papers_structured
from backend.app.tools.fulltext_fetcher import fetch_fulltexts

logger = logging.getLogger(__name__)


async def paper_screener_agent(state: dict[str, Any]) -> dict[str, Any]:
    """Agent 8: Screens candidate corpus based on title and abstract relevance."""
    raw_papers = state.get("paper_records") or state.get("raw_papers") or []
    ps = state.get("problem_statement", "")
    objs = state.get("research_objectives", [])
    raw_tracker = state.get("prisma_tracker") or {}
    tracker = PRISMATracker(**raw_tracker) if raw_tracker else None

    records = [PaperRecord.from_dict(p) for p in raw_papers]
    thread_id = state.get("thread_id")

    async def _on_screening_progress(screened: int, total: int, batches_done: int, total_batches: int):
        if not thread_id:
            return
        try:
            from backend.app.api.research_mode import _broadcast_event
            await _broadcast_event(thread_id, {
                "event": "screening_progress",
                "screened": screened,
                "total": total,
                "batches_done": batches_done,
                "total_batches": total_batches,
            }, is_node_event=False)
        except Exception:
            pass

    screen_kwargs: dict[str, Any] = {
        "tracker": tracker,
        "protocol": state.get("search_protocol"),
    }
    try:
        import inspect
        target_fn = screen_papers_structured
        if hasattr(target_fn, "side_effect") and callable(target_fn.side_effect):
            target_fn = target_fn.side_effect
        sig = inspect.signature(target_fn)
        if "on_progress" in sig.parameters or any(
            p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
        ):
            screen_kwargs["on_progress"] = _on_screening_progress
    except Exception:
        pass

    screened_records, updated_tracker = await screen_papers_structured(
        records,
        ps,
        objs,
        **screen_kwargs,
    )
    dict_screened = [r.model_dump() for r in screened_records]

    return {
        "paper_records": dict_screened,
        "screened_papers": dict_screened,
        "prisma_tracker": updated_tracker.model_dump(),
        "corpus_stats": {
            "retrieved": updated_tracker.records_identified,
            "after_dedup": updated_tracker.records_after_dedup,
            "screened": updated_tracker.records_screened,
            "included": updated_tracker.studies_included,
        }
    }


async def fulltext_eligibility_agent(state: dict[str, Any]) -> dict[str, Any]:
    """Agent 9: Retrieves full-text PDFs and JATS XML to assess full-text eligibility."""
    screened_papers = state.get("paper_records") or state.get("screened_papers") or []
    if not screened_papers:
        return {}

    logger.info(f"fulltext_eligibility_agent fetching full texts for top {len(screened_papers[:15])} papers...")
    try:
        selected = screened_papers[:15]
        enriched_selected = await fetch_fulltexts(selected)
        for p in enriched_selected:
            if "content_excerpt" in p and not p.get("fulltext_excerpt"):
                p["fulltext_excerpt"] = p["content_excerpt"]
        
        enriched_all = enriched_selected + screened_papers[15:]
        fetched = sum(1 for p in enriched_all if (p.get("fulltext_excerpt") or "").strip())
        return {
            "paper_records": enriched_all,
            "screened_papers": enriched_all,
            # Merge rather than replace: corpus_stats is the PRISMA counters block,
            # and overwriting it here wiped retrieved/screened/included.
            "corpus_stats": {**(state.get("corpus_stats") or {}), "fulltext_fetched": fetched},
        }
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Full-text fetching encountered error: {e}")
        return {}


async def quality_appraisal_agent(state: dict[str, Any]) -> dict[str, Any]:
    """Agent 10: Evaluates study methodological rigor, study design, and risk of bias."""
    paper_dicts = state.get("paper_records") or state.get("screened_papers") or []
    if not paper_dicts:
        return {}

    llm = get_llm_for("researcher", state, temperature=0.1)
    
    # Assess top 10 included papers in a single batch
    sample = included_papers(paper_dicts)[:10]
    papers_summary = "\n".join(
        f"[{i+1}] Title: {p.get('title')}\nAbstract: {(p.get('abstract') or '')[:250]}\nExcerpt: {(p.get('fulltext_excerpt') or '')[:250]}"
        for i, p in enumerate(sample)
    )

    prompt = f"""You are a Senior Methodological Reviewer and Quality Appraiser.
Evaluate the study type and quality rating (High, Medium, Low) for each paper based on empirical rigor, benchmarks, and dataset transparency.

Papers:
{papers_summary}

Return a valid JSON array with schema:
[
  {{
    "id": 1,
    "study_type": "empirical" | "benchmark" | "review" | "theoretical" | "survey",
    "quality_rating": "High" | "Medium" | "Low",
    "quality_notes": "Brief 1-sentence appraisal rationale"
  }}
]
"""
    try:
        raw = await _safe_invoke_llm(llm, [HumanMessage(content=prompt)])
        clean = raw
        if "```" in clean:
            clean = clean.split("```")[1]
            clean = clean.removeprefix("json")
        evaluations = json.loads(clean.strip())
        eval_map = {item["id"]: item for item in evaluations if isinstance(item, dict) and "id" in item}

        for idx, p in enumerate(sample):
            ev = eval_map.get(idx + 1)
            if ev:
                p["study_type"] = ev.get("study_type", "empirical")
                p["quality_rating"] = ev.get("quality_rating", "Medium")
                p["quality_rubric"] = {"appraisal_notes": ev.get("quality_notes", "")}

    except Exception as e:  # noqa: BLE001
        logger.warning(f"quality_appraisal JSON parse defaulted: {e}")
        # Only the assessed sample receives defaults; unassessed papers are left
        # untouched instead of getting synthetic study_type / quality values.
        for p in sample:
            p.setdefault("study_type", "empirical")
            p.setdefault("quality_rating", "Medium")

    return {
        "paper_records": paper_dicts,
        "screened_papers": paper_dicts,
    }
