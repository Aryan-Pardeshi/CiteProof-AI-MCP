import json
import re
import sys
from enum import Enum
from pathlib import Path
from types import SimpleNamespace
from typing import get_args, get_origin
from unittest.mock import patch

repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from backend.app.agents.research_mode.planning import (
    KeywordOutput,
    ScopeDefinitionOutput,
    ScopeRevisionOutput,
)
from backend.app.graph.research_mode_builder import (
    get_research_mode_graph,
    set_checkpointer,
)
from backend.app.models.evidence import (
    ConfidenceBasis,
    PaperRecord,
    PRISMATracker,
    SearchProtocol,
)

CONF_BASIS_VAL = next(iter(ConfidenceBasis)).value


DEFAULT_QUOTE = "Artificial intelligence has demonstrated applications in healthcare."


def _get_item_class(annotation):
    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin is list or origin is list:
        return args[0] if args else None
    for arg in args:
        sub_origin = get_origin(arg)
        sub_args = get_args(arg)
        if sub_origin is list or sub_origin is list:
            return sub_args[0] if sub_args else None
    return None


def _get_enum_value(annotation):
    if isinstance(annotation, type) and issubclass(annotation, Enum):
        return next(iter(annotation))
    args = get_args(annotation)
    for arg in args:
        if isinstance(arg, type) and issubclass(arg, Enum):
            return next(iter(arg))
    return None


class FakeStructuredLLM:
    def __init__(self, schema):
        self.schema = schema

    def invoke(self, prompt):
        if self.schema is ScopeDefinitionOutput:
            return ScopeDefinitionOutput(
                refined_problem_statement=(
                    "Impact of artificial intelligence on healthcare"
                ),
                research_objectives=[
                    "Evaluate AI applications in healthcare",
                    "Assess clinical benefits of AI",
                    "Identify limitations of AI in healthcare",
                ],
                research_questions=[
                    "How is AI being used in healthcare?",
                    "What benefits does AI provide?",
                    "What limitations affect AI adoption?",
                ],
            )

        if self.schema is KeywordOutput:
            return KeywordOutput(
                keywords=[
                    "artificial intelligence healthcare",
                    "AI clinical applications",
                    "machine learning healthcare",
                    "AI healthcare outcomes",
                    "clinical decision support AI",
                    "healthcare machine learning",
                ]
            )

        if self.schema is ScopeRevisionOutput:
            return ScopeRevisionOutput(
                problem_statement=(
                    "Impact of artificial intelligence on healthcare"
                ),
                research_objectives=[
                    "Evaluate AI applications in healthcare",
                    "Assess clinical benefits of AI",
                    "Identify limitations of AI in healthcare",
                ],
                keywords=[
                    "artificial intelligence healthcare",
                    "AI clinical applications",
                    "machine learning healthcare",
                ],
            )

        if self.schema is SearchProtocol:
            return SearchProtocol(
                population="Healthcare applications",
                intervention="Artificial intelligence",
                outcomes=[
                    "Clinical benefits",
                    "Clinical outcomes",
                    "Limitations",
                ],
                inclusion_criteria=["Peer-reviewed empirical studies"],
                exclusion_criteria=["Opinion pieces", "Duplicates"],
                boolean_queries=["artificial intelligence AND healthcare"],
                search_keywords=[
                    "AI healthcare",
                    "machine learning healthcare",
                ],
            )

        fields = getattr(self.schema, "model_fields", {})
        values = {}

        for name, field in fields.items():
            enum_val = _get_enum_value(field.annotation)
            if enum_val is not None:
                values[name] = enum_val
                continue

            item_cls = _get_item_class(field.annotation)

            if item_cls is not None:
                if hasattr(item_cls, "model_fields"):
                    values[name] = [FakeStructuredLLM(item_cls).invoke(prompt)]
                elif item_cls is str:
                    if name in (
                        "subthemes",
                        "research_objectives",
                        "research_questions",
                        "keywords",
                        "outcomes",
                        "inclusion_criteria",
                        "exclusion_criteria",
                        "boolean_queries",
                        "search_keywords",
                    ):
                        values[name] = ["Sample item 1", "Sample item 2"]
                    else:
                        values[name] = [DEFAULT_QUOTE]
                else:
                    values[name] = []
                continue

            if name == "population":
                values[name] = "Healthcare applications"
            elif name == "intervention":
                values[name] = "Artificial intelligence"
            elif name in ("problem_statement", "refined_problem_statement"):
                values[name] = "Impact of artificial intelligence on healthcare"
            elif name == "description":
                values[name] = "Canned structured output for offline integration testing"
            elif name == "title":
                values[name] = "Artificial Intelligence in Healthcare"
            elif name in ("claim_summary", "exact_quote", "claim_text", "text", "theme_name"):
                values[name] = DEFAULT_QUOTE
            elif name in ("section", "source_section"):
                values[name] = "Results"
            elif name == "target_section":
                values[name] = "literature_review"
            elif name in ("verification_status", "validation_status"):
                values[name] = "resolved"
            elif name == "confidence":
                values[name] = 0.9
            elif name in ("source_url", "doi"):
                values[name] = "https://example.org/paper001"
            elif name == "paper_id":
                values[name] = "paper001"
            elif name == "evidence_id":
                values[name] = "paper001_ev001"
            elif name in ("evidence_span_id", "span_id"):
                values[name] = "paper001_sp001"
            elif name == "claim_id":
                values[name] = "claim001"
            elif name == "theme_id":
                values[name] = "theme_1"
            elif name in ("is_quantitative", "passed_all_gates", "prisma_invariants_valid", "passed"):
                values[name] = True
            elif name.startswith(("total_", "verified_", "resolved_", "grounded_")):
                values[name] = 1
            elif field.default is not None:
                values[name] = field.default
            elif field.default_factory is not None:
                values[name] = field.default_factory()
            else:
                annotation_str = str(field.annotation)
                if "bool" in annotation_str:
                    values[name] = False
                elif "int" in annotation_str or "float" in annotation_str:
                    values[name] = 0
                elif "Dict" in annotation_str or "dict" in annotation_str:
                    values[name] = {}
                else:
                    values[name] = "Canned structured output"

        return self.schema(**values)


class FakePaperDict(dict):
    def model_dump(self):
        return dict(self)


class FakeLLM:
    model_name = "fake-test-model"
    openai_api_base = "http://fake-test"

    def with_structured_output(self, schema, method=None):
        return FakeStructuredLLM(schema)

    def invoke(self, messages):
        prompt = str(messages).lower()

        if "taxonomy" in prompt or "theme" in prompt:
            return SimpleNamespace(
                content=(
                    '['
                    '  {'
                    '    "theme_id": "theme_1",'
                    '    "theme_name": "AI Applications in Healthcare",'
                    '    "description": "Research on the application of artificial intelligence across healthcare workflows.",'
                    '    "subthemes": ["Clinical decision support", "Diagnosis", "Patient management"]'
                    '  }'
                    ']'
                )
            )

        if "quality" in prompt and "json" in prompt:
            return SimpleNamespace(
                content=(
                    '{"quality_rating":"high",'
                    '"quality_rationale":"The study provides clear empirical evidence.",'
                    '"passed":true}'
                )
            )

        if "hypoth" in prompt:
            return SimpleNamespace(
                content=(
                    '{"hypotheses":["AI-assisted healthcare applications can improve clinical decision support outcomes."]}'
                )
            )

        # Writer prompts: when evidence ids are present in the prompt, emit
        # prose carrying inline [EV:<id>] markers so claims_linker_node can
        # build review_claims from them.
        ev_ids = sorted(set(re.findall(r"paper\d{3}_ev\d{3}", prompt)))
        if not ev_ids and "[ev:" in prompt:
            ev_ids = ["paper001_ev001"]

        if ev_ids:
            body = " ".join(
                "Artificial intelligence has demonstrated applications in "
                f"healthcare and clinical decision support. [EV:{eid}]"
                for eid in ev_ids
            )
            return SimpleNamespace(content=body)

        if any(k in prompt for k in ("evidence", "claim", "extract", "review", "synthesis", "manifest")):
            return SimpleNamespace(
                content=json.dumps([{
                    "claim_id": "claim001",
                    "claim_text": DEFAULT_QUOTE,
                    "claim_summary": DEFAULT_QUOTE,
                    "exact_quote": DEFAULT_QUOTE,
                    "section": "Results",
                    "source_section": "Results",
                    "page": 1,
                    "paper_id": "paper001",
                    "evidence_id": "paper001_ev001",
                    "evidence_ids": ["paper001_ev001"],
                    "evidence_span_id": "paper001_sp001",
                    "confidence": 0.9,
                    "confidence_basis": CONF_BASIS_VAL,
                    "verification_status": "resolved",
                }])
            )

        return SimpleNamespace(
            content=(
                "Artificial intelligence is increasingly used in healthcare. "
                "The literature reports applications in clinical decision support, "
                "diagnosis, and patient management. Future studies would evaluate "
                "these applications using reproducible empirical designs."
            )
        )


async def fake_evidence_llm(
    llm,
    messages,
    max_retries=4,
    base_backoff=2.0,
    node="llm",
    **kwargs,
):
    prompt = str(messages).lower()

    if "taxonomy" in prompt or "theme" in prompt:
        return (
            '['
            '  {'
            '    "theme_id": "theme_1",'
            '    "theme_name": "AI Applications in Healthcare",'
            '    "description": "Research on the application of artificial intelligence across healthcare workflows.",'
            '    "subthemes": ["Clinical decision support", "Diagnosis", "Patient management"]'
            '  }'
            ']'
        )

    if any(k in prompt for k in ("evidence", "claim", "extract", "review", "synthesis", "manifest")):
        # Pick the paper actually being processed so the quote really appears
        # in that paper's full text (otherwise the span resolves as unresolved).
        pid, quote = "paper001", None
        for p in fake_papers():
            first = p.full_text.split(". ")[0].rstrip(".") + "."
            if first.lower() in prompt:
                pid, quote = p.paper_id, first
                break
        quote = quote or DEFAULT_QUOTE

        return json.dumps([{
            "claim_id": "claim001",
            "claim_text": quote,
            "claim_summary": quote,
            "exact_quote": quote,
            "section": "Results",
            "source_section": "Results",
            "page": 1,
            "paper_id": pid,
            "evidence_id": f"{pid}_ev001",
            "evidence_ids": [f"{pid}_ev001"],
            "evidence_span_id": f"{pid}_sp001",
            "confidence": 0.9,
            "confidence_basis": CONF_BASIS_VAL,
            "verification_status": "resolved",
        }])

    return (
        "Artificial intelligence is increasingly used in healthcare. "
        "The literature reports applications in clinical decision support, "
        "diagnosis, and patient management."
    )


def fake_papers():
    return [
        PaperRecord(
            paper_id="paper001",
            doi="10.1000/paper001",
            title="Artificial Intelligence in Clinical Decision Support",
            authors=["Smith, A."],
            year="2024",
            venue="Journal of Medical AI",
            abstract="AI supports clinical decision making.",
            source_url="https://example.org/paper001",
            full_text=(
                "Artificial intelligence has demonstrated applications in healthcare. "
                "Clinical decision support systems can assist clinicians."
            ),
            fulltext_excerpt=(
                "Artificial intelligence has demonstrated applications in healthcare. "
                "Clinical decision support systems can assist clinicians."
            ),
            retrieval_source="openalex",
            citation_count=42,
            screening_status="retrieved",
        ),
        PaperRecord(
            paper_id="paper002",
            doi="10.1000/paper002",
            title="Machine Learning Applications in Healthcare",
            authors=["Jones, B."],
            year="2023",
            venue="Healthcare Computing Review",
            abstract="Machine learning is used across healthcare workflows.",
            source_url="https://example.org/paper002",
            full_text=(
                "Machine learning is used across healthcare workflows. "
                "Clinical models can support diagnosis and patient management."
            ),
            fulltext_excerpt=(
                "Machine learning is used across healthcare workflows. "
                "Clinical models can support diagnosis and patient management."
            ),
            retrieval_source="arxiv",
            citation_count=27,
            screening_status="retrieved",
        ),
        PaperRecord(
            paper_id="paper003",
            doi="10.1000/paper003",
            title="Limitations of AI Adoption in Healthcare",
            authors=["Brown, C."],
            year="2022",
            venue="Digital Health Studies",
            abstract="AI adoption has methodological and operational limitations.",
            source_url="https://example.org/paper003",
            full_text=(
                "AI adoption has methodological and operational limitations. "
                "Evaluation and deployment require careful validation."
            ),
            fulltext_excerpt=(
                "AI adoption has methodological and operational limitations. "
                "Evaluation and deployment require careful validation."
            ),
            retrieval_source="crossref",
            citation_count=18,
            screening_status="retrieved",
        ),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "message, checkpoint, status, next_node",
    [
        ("approve", "checkpoint_2_approved", "synthesizing", "taxonomy_agent"),
        ("Extract more evidence", "checkpoint_2_revising", "revising_evidence", "evidence_extractor"),
    ],
)
async def test_checkpoint_2_routes_approval_to_synthesis_and_revision_to_extraction(
    monkeypatch, message, checkpoint, status, next_node
):
    from backend.app.graph import research_mode_builder

    monkeypatch.setattr(research_mode_builder, "interrupt", lambda payload: {"message": message})

    result = await research_mode_builder.checkpoint_2_node({})

    assert result["hitl_checkpoint"] == checkpoint
    assert result["status"] == status
    assert research_mode_builder.route_after_checkpoint_2(result) == next_node
    assert result["user_feedback"] == (None if message == "approve" else message)


def test_research_graph_connects_checkpoint_2_directly_to_synthesis():
    graph = get_research_mode_graph().get_graph()
    destinations = {edge.target for edge in graph.edges if edge.source == "checkpoint_2"}

    assert destinations == {"evidence_extractor", "taxonomy_agent"}
    assert "evidence_auditor" not in graph.nodes


@pytest.mark.asyncio
async def test_research_mode_runs_offline_from_problem_to_final_paper():
    papers = fake_papers()

    tracker = PRISMATracker(
        records_identified=3,
        records_by_source={
            "openalex": 1,
            "arxiv": 1,
            "crossref": 1,
        },
        duplicates_removed=0,
        records_after_dedup=3,
        records_screened=3,
        excluded_title_abstract=0,
        full_text_requested=3,
        full_text_unavailable=0,
        full_text_assessed=3,
        excluded_full_text=0,
        studies_included=3,
    )

    async def fake_search_academic_papers_structured(keywords):
        return papers, tracker

    async def fake_screen_papers_structured(
        records,
        problem_statement,
        objectives,
        tracker=None,
        protocol=None,
        on_progress=None,
    ):
        screened = []

        for paper in records:
            data = (
                paper.model_dump()
                if hasattr(paper, "model_dump")
                else dict(paper)
            )
            data["screening_status"] = "included"
            screened.append(PaperRecord(**data))

        updated_tracker = tracker or PRISMATracker()
        updated_tracker.records_screened = len(screened)
        updated_tracker.records_after_dedup = len(screened)
        updated_tracker.full_text_requested = len(screened)
        updated_tracker.full_text_assessed = len(screened)
        updated_tracker.studies_included = len(screened)

        return screened, updated_tracker

    async def fake_fetch_fulltexts(records):
        enriched = []

        for record in records:
            data = (
                record.model_dump()
                if hasattr(record, "model_dump")
                else dict(record)
            )

            full_text = (
                data.get("full_text")
                or data.get("fulltext_excerpt")
                or data.get("content_excerpt")
                or ""
            )

            data["content_excerpt"] = full_text
            data["fulltext_excerpt"] = full_text
            data["full_text"] = full_text

            enriched.append(FakePaperDict(data))

        return enriched

    async def fake_expand_citation_graph(*args, **kwargs):
        return []

    async def fake_crossref_search(*args, **kwargs):
        return []

    checkpointer = MemorySaver()
    set_checkpointer(checkpointer)
    graph = get_research_mode_graph()

    config = {
        "configurable": {
            "thread_id": "research-mode-offline-integration"
        }
    }

    initial_state = {
        "thread_id": "research-mode-offline-integration",
        "problem_statement": "Impact of AI on healthcare",
        "research_objectives": [],
        "research_questions": [],
        "keywords": [],
        "raw_papers": [],
        "paper_records": [],
        "screened_papers": [],
        "status": "initializing",
    }

    with patch(
        "backend.app.agents.research_mode._common.get_llm",
        return_value=FakeLLM(),
    ), patch(
        "backend.app.llm.get_llm",
        return_value=FakeLLM(),
    ), patch(
        "backend.app.agents.research_mode.retrieval.search_academic_papers_structured",
        side_effect=fake_search_academic_papers_structured,
    ), patch(
        "backend.app.agents.research_mode.retrieval.expand_citation_graph",
        side_effect=fake_expand_citation_graph,
    ), patch(
        "backend.app.agents.research_mode.retrieval.search_crossref",
        side_effect=fake_crossref_search,
    ), patch(
        "backend.app.agents.research_mode.screening.screen_papers_structured",
        side_effect=fake_screen_papers_structured,
    ), patch(
        "backend.app.agents.research_mode.screening.fetch_fulltexts",
        side_effect=fake_fetch_fulltexts,
    ), patch(
        "backend.app.agents.research_mode._common._safe_invoke_llm",
        side_effect=fake_evidence_llm,
        create=True,
    ), patch(
        "backend.app.agents.research_mode.extraction._safe_invoke_llm",
        side_effect=fake_evidence_llm,
        create=True,
    ), patch(
        "backend.app.agents.research_mode.synthesis._safe_invoke_llm",
        side_effect=fake_evidence_llm,
        create=True,
    ):
        result = await graph.ainvoke(
            initial_state,
            config=config,
        )

        # Gate 1 must pause the graph.
        assert "__interrupt__" in result

        # Resume through the three HITL gates (checkpoint_1/2/3), with a
        # safety cap in case the graph pauses more or fewer times.
        for _ in range(10):
            if "__interrupt__" not in result:
                break
            result = await graph.ainvoke(
                Command(resume={"message": "approve"}),
                config=config,
            )

        assert "__interrupt__" not in result, "graph never ran to completion"
        final_state = result


    # ---- PRISMA invariants ----
    prisma = final_state.get("prisma_tracker") or {}
    if isinstance(prisma, dict):
        prisma = PRISMATracker(**prisma)

    assert isinstance(prisma, PRISMATracker)
    assert prisma.validate_invariants() == []

    assert final_state.get("paper_records")
    assert final_state.get("evidence_records")

    # ---- Paper: assembled from section keys ----
    for section in (
        "title",
        "abstract",
        "introduction",
        "literature_review",
        "results",
        "discussion",
        "implications",
        "conclusion",
        "references",
    ):
        assert final_state.get(section), f"missing paper section: {section}"

    # ---- Claims manifest (review_claims) ----
    claims = final_state["review_claims"]
    assert claims, "no claims linked (writer output had no [EV:<id>] markers?)"

    evidence_ids = {e["evidence_id"] for e in final_state["evidence_records"]}
    for c in claims:
        assert c["supporting_evidence_ids"]
        assert set(c["supporting_evidence_ids"]) <= evidence_ids

    # Markers must be stripped from the rendered prose.
    for section in ("introduction", "literature_review", "results", "discussion", "implications"):
        assert "[EV:" not in str(final_state.get(section)).upper()

    # ---- Validation report ----
    report = final_state["validation_report"]
    assert report["prisma_invariants_valid"] is True
    assert report["total_review_claims"] == len(claims)
    assert report["resolved_review_claims"] == len(claims)
    assert report["unresolved_review_claims"] == []
    assert report["passed_all_gates"] is True
