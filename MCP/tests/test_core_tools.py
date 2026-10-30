# SPDX-License-Identifier: MIT
"""Tests for Task A core MCP tools: verify_dois, resolve_citations, and lint_prose."""

from __future__ import annotations

from typing import Any

import pytest
from citeproof_mcp.server import build_server

from tests.conftest import Client, call

# ---------------------------------------------------------------------------
# verify_dois tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_verify_dois_four_cases(monkeypatch: pytest.MonkeyPatch) -> None:
    """Statuses and summary counts for all four cases: ok True, ok False, ok None, and retracted."""
    async def fake_verify_dois(dois: list[str]) -> dict[str, dict[str, Any]]:
        return {
            "10.1000/resolved": {
                "ok": True,
                "title": "Resolved Study",
                "year": 2021,
                "journal": "Nature",
                "retracted": False,
            },
            "10.1000/notfound": {
                "ok": False,
                "retracted": None,
            },
            "10.1000/unverified": {
                "ok": None,
                "error": "unverified (network)",
                "retracted": None,
            },
            "10.1000/retracted": {
                "ok": True,
                "title": "Retracted Article",
                "year": 2018,
                "journal": "Lancet",
                "retracted": True,
            },
        }

    monkeypatch.setattr("citeproof_mcp.tools.core._verify_dois_lib", fake_verify_dois)

    dois = [
        "10.1000/resolved",
        "10.1000/notfound",
        "10.1000/unverified",
        "10.1000/retracted",
    ]
    res = await call("verify_dois", dois=dois)

    assert res["ok"] is True
    assert "summary" in res
    assert "results" in res

    summary = res["summary"]
    assert summary["checked"] == 4
    assert summary["resolved"] == 2
    assert summary["not_found"] == 1
    assert summary["unverified"] == 1
    assert summary["retracted"] == 1

    results_by_doi = {r["doi"]: r for r in res["results"]}

    # Case 1: ok True (resolved, not retracted)
    item_res = results_by_doi["10.1000/resolved"]
    assert item_res["status"] == "resolved"
    assert item_res["title"] == "Resolved Study"
    assert item_res["year"] == 2021
    assert item_res["journal"] == "Nature"
    assert item_res["retracted"] is False

    # Case 2: ok False (not_found)
    item_nf = results_by_doi["10.1000/notfound"]
    assert item_nf["status"] == "not_found"
    assert item_nf["retracted"] is None

    # Case 3: ok None (unverified)
    item_uv = results_by_doi["10.1000/unverified"]
    assert item_uv["status"] == "unverified"
    assert item_uv["retracted"] is None

    # Case 4: retracted True
    item_ret = results_by_doi["10.1000/retracted"]
    assert item_ret["status"] == "resolved"
    assert item_ret["retracted"] is True
    assert item_ret["title"] == "Retracted Article"


@pytest.mark.asyncio
async def test_verify_dois_duplicates_and_case(monkeypatch: pytest.MonkeyPatch) -> None:
    """Duplicate and mixed-case DOIs collapse in input order; DOIs are sent lower-cased (case-insensitive)."""
    recorded_dois: list[str] = []

    async def fake_verify_dois(dois: list[str]) -> dict[str, dict[str, Any]]:
        recorded_dois.extend(dois)
        return {d: {"ok": True, "title": "Paper", "retracted": False} for d in dois}

    monkeypatch.setattr("citeproof_mcp.tools.core._verify_dois_lib", fake_verify_dois)

    dois = [
        "10.1000/Alpha",
        "10.1000/beta",
        "10.1000/alpha",
        "10.1000/BETA",
        " 10.1000/Alpha ",
        "10.1000/gamma",
    ]
    res = await call("verify_dois", dois=dois)

    assert res["ok"] is True
    assert res["summary"]["checked"] == 3
    assert len(res["results"]) == 3
    assert [r["doi"] for r in res["results"]] == ["10.1000/alpha", "10.1000/beta", "10.1000/gamma"]
    assert [r["input"] for r in res["results"]] == ["10.1000/Alpha", "10.1000/beta", "10.1000/gamma"]
    assert recorded_dois == ["10.1000/alpha", "10.1000/beta", "10.1000/gamma"]


@pytest.mark.asyncio
async def test_verify_dois_rejected_inputs() -> None:
    """101 DOIs and an empty list are rejected with ok: false."""
    # Empty list
    res_empty = await call("verify_dois", dois=[])
    assert res_empty["ok"] is False
    assert "error" in res_empty

    # 101 DOIs
    res_101 = await call("verify_dois", dois=[f"10.1000/{i}" for i in range(101)])
    assert res_101["ok"] is False
    assert "error" in res_101


@pytest.mark.asyncio
async def test_verify_dois_exception_handling(monkeypatch: pytest.MonkeyPatch) -> None:
    """A library exception returns ok: false without a traceback."""
    async def fake_verify_dois(dois: list[str]) -> dict[str, dict[str, Any]]:
        raise ConnectionResetError("Remote registry connection dropped")

    monkeypatch.setattr("citeproof_mcp.tools.core._verify_dois_lib", fake_verify_dois)

    res = await call("verify_dois", dois=["10.1000/network-fail"])
    assert res["ok"] is False
    assert "error" in res
    assert "Traceback" not in res["error"]
    assert "Remote registry connection dropped" in res["error"]


# ---------------------------------------------------------------------------
# resolve_citations tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_citations_doi_citation() -> None:
    """DOI citation in text resolves against a supplied paper record."""
    text = "The core protocol is detailed in https://doi.org/10.1016/j.cell.2021.05.001 as expected."
    papers = [
        {
            "title": "Single-cell atlas of human tissues",
            "authors": ["Aviv Regev", "Sarah Teichmann"],
            "year": 2021,
            "doi": "10.1016/j.cell.2021.05.001",
        }
    ]
    res = await call("resolve_citations", text=text, papers=papers)

    assert res["ok"] is True
    assert res["citations_found"] == 1
    assert res["resolved"] == 1
    assert res["doi_citations_checked"] == 1
    assert res["unresolved"] == []
    assert res["orphan_papers"] == []


@pytest.mark.asyncio
async def test_resolve_citations_author_year() -> None:
    """Author-year citation resolves against a supplied paper record."""
    text = "Previous trials demonstrated substantial biomarker improvements (Donners, 2021)."
    papers = [
        {
            "title": "Dietary interventions in metabolic syndrome",
            "authors": "Donners, Michael",  # string format
            "year": 2021,
        }
    ]
    res = await call("resolve_citations", text=text, papers=papers)

    assert res["ok"] is True
    assert res["citations_found"] == 1
    assert res["resolved"] == 1
    assert res["unresolved"] == []
    assert res["orphan_papers"] == []


@pytest.mark.asyncio
async def test_resolve_citations_unknown_unverified() -> None:
    """An unknown citation in text is listed as unverified."""
    text = "Novel therapeutic efficacy was proposed in (Vogelstein, 2024)."
    papers = [
        {
            "title": "Genomic landscapes in cancer",
            "authors": ["Vogelstein, Bert"],
            "year": 2013,
        }
    ]
    res = await call("resolve_citations", text=text, papers=papers)

    assert res["ok"] is True
    assert res["citations_found"] == 1
    assert res["resolved"] == 0
    assert "(Vogelstein, 2024)" in res["unresolved"]


@pytest.mark.asyncio
async def test_resolve_citations_orphan_paper() -> None:
    """A supplied paper never cited in text is listed under orphan_papers."""
    text = "Recent synthesis relies on (Harrison, 2020)."
    papers = [
        {
            "title": "Harrison 2020 Synthesis",
            "authors": ["Harrison, Paul"],
            "year": 2020,
        },
        {
            "title": "Uncited Clinical Review",
            "authors": ["Miller, Rebecca"],
            "year": 2022,
        },
    ]
    res = await call("resolve_citations", text=text, papers=papers)

    assert res["ok"] is True
    assert res["resolved"] == 1
    assert "Uncited Clinical Review" in res["orphan_papers"]
    assert "Harrison 2020 Synthesis" not in res["orphan_papers"]


@pytest.mark.asyncio
async def test_resolve_citations_oversize_rejected() -> None:
    """Oversize input text (> 400,000 chars) or papers list (> 2,000) is rejected."""
    # Oversize text
    long_text = "word " * 80_001  # > 400,000 characters
    res_text = await call("resolve_citations", text=long_text, papers=[])
    assert res_text["ok"] is False
    assert "error" in res_text

    # Oversize papers list
    many_papers = [
        {"title": f"Paper {i}", "authors": ["Author"], "year": 2020}
        for i in range(2001)
    ]
    res_papers = await call("resolve_citations", text="Short prose", papers=many_papers)
    assert res_papers["ok"] is False
    assert "error" in res_papers


# ---------------------------------------------------------------------------
# lint_prose tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lint_prose_process_narration() -> None:
    """Text with process narration returns at least one issue."""
    text = "In conducting this review, DOIs were verified against CrossRef by the automated script."
    res = await call("lint_prose", text=text)

    assert res["ok"] is True
    assert res["advisory"] is True
    assert len(res["issues"]) >= 1
    codes = [i["code"] for i in res["issues"]]
    assert "PROCNOTE" in codes


@pytest.mark.asyncio
async def test_lint_prose_clean_text() -> None:
    """Clean prose without narration or style issues returns no issues."""
    text = (
        "## Introduction\n\n"
        "Cardiovascular diseases represent a leading cause of mortality worldwide. "
        "Recent clinical trials demonstrate substantial risk reduction through targeted lifestyle interventions."
    )
    res = await call("lint_prose", text=text)

    assert res["ok"] is True
    assert res["advisory"] is True
    assert len(res["issues"]) == 0


# ---------------------------------------------------------------------------
# Tool registration and discovery tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_tools_descriptions() -> None:
    """All tools are listed by the in-memory client with a non-empty description."""
    async with Client(build_server()) as c:
        tools_resp = await c.list_tools()

    tools_by_name = {t.name: t for t in tools_resp.tools}
    expected_tools = {
        "review_create",
        "review_list",
        "verify_dois",
        "resolve_citations",
        "lint_prose",
        "protocol_record",
        "prisma_record_search",
        "dedup_papers",
        "prisma_record_dedup",
        "screening_record",
        "screening_record_many",
        "screening_list",
        "screening_agreement",
        "prisma_status",
        "prisma_flow_diagram",
        "prisma_checklist",
        "evidence_record",
        "evidence_table",
        "claim_record",
        "claim_report",
        "rob_record",
        "rob_summary",
        "review_status",
        "export_bundle",
        "reviewer_record",
        "screening_stage_set",
        "screening_stage_close",
        "paper_resolve",
        "paper_alias_record",
        "study_link",
        "study_unlink",
        "study_overview",
        "recall_seed_record",
        "recall_check",
        "stats_check_ratio_ci",
        "stats_recompute_2x2",
        "stats_check_evidence",
        "prisma_excluded_studies",
        "manuscript_check",
        "review_history",
    }
    assert set(tools_by_name) == expected_tools
    for name in expected_tools:
        desc = tools_by_name[name].description
        assert desc is not None and len(desc.strip()) > 40, f"Tool '{name}' description is too short"
        schema = tools_by_name[name].model_dump(by_alias=True)["inputSchema"]
        for prop, spec in schema.get("properties", {}).items():
            assert spec.get("description"), f"{name}.{prop} has no description"
