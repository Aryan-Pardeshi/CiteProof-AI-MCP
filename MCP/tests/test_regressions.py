# SPDX-License-Identifier: MIT
"""Regression tests for bugs found in the review of the first release of the MCP tools."""

from __future__ import annotations

import concurrent.futures
import sqlite3
from typing import Any

import pytest
from citeproof_mcp import store
from citeproof_mcp.server import build_server
from citeproof_mcp.tools import prisma
from citeproof_mcp.tools.quotes import check_quote, extract_numbers, quote_in_source

from tests.conftest import Client, call


async def _review() -> str:
    res = await call("review_create", title="Regression review", question="Does X reduce Y?")
    assert res["ok"] is True
    return res["review_id"]


# --- tool surface ------------------------------------------------------------


@pytest.mark.asyncio
async def test_required_arguments_are_required_in_schema() -> None:
    async with Client(build_server()) as c:
        schemas = {t.name: t.model_dump(by_alias=True)["inputSchema"] for t in (await c.list_tools()).tools}
    claim_required = set(schemas["claim_record"]["required"])
    assert {"review_id", "claim_text", "paper_ref", "label", "reason"} <= claim_required
    assert set(schemas["evidence_record"]["required"]) >= {"quote", "source_text", "outcome"}


@pytest.mark.asyncio
async def test_review_create_requires_question() -> None:
    res = await call("review_create", title="T", question="  ")
    assert res["ok"] is False
    assert "question" in res["error"]


# --- paper_ref canonical form ---------------------------------------------------


@pytest.mark.asyncio
async def test_doi_case_and_prefix_map_to_one_paper() -> None:
    rid = await _review()
    first = await call("screening_record", review_id=rid, paper_ref="10.1056/NEJMoa1", stage="title_abstract", decision="include")
    second = await call(
        "screening_record", review_id=rid, paper_ref="https://doi.org/10.1056/nejmoa1", stage="title_abstract",
        decision="exclude", reason="wrong population",
    )
    assert first["paper_ref"] == second["paper_ref"] == "10.1056/nejmoa1"
    assert second["updated"] is True
    listed = await call("screening_list", review_id=rid)
    assert listed["total"] == 1


def test_canonical_paper_ref_forms() -> None:
    assert store.canonical_paper_ref("doi:10.1000/ABC.") == "10.1000/abc"
    assert store.canonical_paper_ref("PMID: 00123") == "pmid:123"
    assert store.canonical_paper_ref("title:A  Trial, of X!|2020") == "title:a trial of x|2020"
    assert store.canonical_paper_ref("title:No year") == "title:no year|nd"
    for bad in ("", "p1", "pmid:abc", "title:|2020", "title:x|20", "x" * 400):
        with pytest.raises(store.StoreError):
            store.canonical_paper_ref(bad)


# --- quotes -------------------------------------------------------------------------


def test_ellipsis_quote_cannot_drop_a_short_part() -> None:
    """Old behaviour verified 'not ... X' when only X was in the text, inverting the meaning."""
    source = "The intervention reduced mortality significantly in the treated group."
    assert quote_in_source("not ... reduced mortality significantly", source) is False
    assert quote_in_source("intervention ... reduced mortality significantly", source) is True


def test_ellipsis_parts_must_be_near_each_other() -> None:
    source = "patients receiving drug x were enrolled. " + ("filler words here. " * 200) + "experienced significant improvement"
    assert quote_in_source("patients receiving drug x ... experienced significant improvement", source) is False


def test_quote_must_match_on_word_boundaries() -> None:
    source = "Patients showed significant improvement at follow-up."
    assert quote_in_source("ed significant improvement", source) is False
    assert quote_in_source("showed significant improvement", source) is True


def test_quote_matches_across_compound_hyphen_line_break_and_zero_width() -> None:
    source = "The long-\nterm outcome was favourable in most patients."
    assert quote_in_source("the long-term outcome was favourable", source) is True
    assert quote_in_source("the long\u200b-term outcome was favourable", source) is True
    # Numbers keep their sign and ranges keep their hyphen
    assert quote_in_source("difference was -5.2 points", "The mean difference was 5.2 points.") is False


def test_quote_in_unspaced_script_matches() -> None:
    """Word boundaries made every Chinese/Japanese quote fail."""
    source = "短い日本語のテキストですがここに引用があります。"
    assert quote_in_source("日本語のテキストですがここに引用", source) is True


def test_non_latin_and_accented_titles_keep_their_letters() -> None:
    """Title refs used to drop every non-ASCII letter: 'Ünïcödé' became 'n c d', Chinese titles became ''."""
    assert store.canonical_paper_ref("title:Ünïcödé Trial|2020") == "title:unicode trial|2020"
    assert store.canonical_paper_ref("title:糖尿病の研究|2021") == "title:糖尿病の研究|2021"
    assert store.normalize_paper_ref(title="Müller study", year=2019) == "title:muller study|2019"


def test_quote_note_explains_empty_source() -> None:
    verified, note = check_quote("a quote long enough to check", "")
    assert verified is False and "source_text is empty" in note


def test_extract_numbers_reads_signs_ranges_and_separators() -> None:
    nums = extract_numbers("RR 0.82 (95% CI 0.70-0.96); n = 1,234; MD \u22120.5 (\u22120.9 to \u22120.1); 0\u00b745")
    for value in (0.82, 0.70, 0.96, 1234, -0.5, -0.9, -0.1, 0.45, 95):
        assert any(abs(value - n) < 1e-9 for n in nums), value
    assert -0.96 not in nums


# --- verify_dois -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_verify_dois_normalises_urls_and_rejects_non_dois_without_network(monkeypatch) -> None:
    sent: list[str] = []

    async def fake(dois: list[str]) -> dict[str, Any]:
        sent.extend(dois)
        return {d: {"ok": True, "title": "T", "retracted": False, "registry": "crossref"} for d in dois}

    monkeypatch.setattr("citeproof_mcp.tools.core._verify_dois_lib", fake)
    res = await call("verify_dois", dois=["https://doi.org/10.1000/ABC", "not a doi"])
    assert sent == ["10.1000/abc"]
    by_input = {r["input"]: r for r in res["results"]}
    assert by_input["https://doi.org/10.1000/ABC"]["status"] == "resolved"
    assert by_input["not a doi"]["status"] == "invalid"
    assert res["summary"]["invalid"] == 1


@pytest.mark.asyncio
async def test_verify_dois_offline_is_unverified_not_a_verdict(monkeypatch) -> None:
    async def offline(dois: list[str]) -> dict[str, Any]:
        return {d: {"ok": None, "error": "unverified (network)", "retracted": None} for d in dois}

    monkeypatch.setattr("citeproof_mcp.tools.core._verify_dois_lib", offline)
    res = await call("verify_dois", dois="10.1000/a 10.1000/b")
    assert res["ok"] is True
    assert res["offline"] is True
    assert res["summary"]["not_found"] == 0
    assert {r["status"] for r in res["results"]} == {"unverified"}
    assert "retry" in res["note"]


@pytest.mark.asyncio
async def test_verify_dois_saves_results_to_review(monkeypatch) -> None:
    async def fake(dois: list[str]) -> dict[str, Any]:
        return {"10.1000/ret": {"ok": True, "title": "Withdrawn", "year": 2019, "retracted": True, "registry": "crossref"}}

    monkeypatch.setattr("citeproof_mcp.tools.core._verify_dois_lib", fake)
    rid = await _review()
    res = await call("verify_dois", dois=["10.1000/RET"], review_id=rid)
    assert res["saved"] is True
    status = await call("review_status", review_id=rid)
    assert status["dois"]["retracted"] == ["10.1000/ret"]


# --- resolve_citations -------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_citations_sees_grouped_narrative_and_no_comma_forms() -> None:
    text = (
        "Earlier work (Smith, 2020; Jones and Lee, 2021) agrees. Brown et al. (2019) disagreed "
        "(Green 2018). Several trials (e.g., Smith, 2020a) exist. Published in (January 2020) only."
    )
    papers = [
        {"title": "A", "authors": ["Smith, John"], "year": 2020},
        {"title": "B", "authors": ["Jones, Ann", "Lee, Bo"], "year": 2021},
        {"title": "C", "authors": ["Brown, Carl"], "year": 2019},
        {"title": "D", "authors": ["Green, Dee"], "year": 2018},
    ]
    res = await call("resolve_citations", text=text, papers=papers)
    assert res["ok"] is True
    assert res["citations_found"] == 5
    assert res["resolved"] == 5
    assert res["unresolved"] == []
    assert res["orphan_papers"] == []


@pytest.mark.asyncio
async def test_resolve_citations_ambiguous_is_not_counted_as_resolved() -> None:
    papers = [
        {"title": "First", "authors": ["Smith, A"], "year": 2020},
        {"title": "Second", "authors": ["Smith, B"], "year": 2020},
    ]
    res = await call("resolve_citations", text="As shown (Smith, 2020).", papers=papers)
    assert res["resolved"] == 0
    assert res["ambiguous"] == ["(Smith, 2020)"]


@pytest.mark.asyncio
async def test_resolve_citations_reports_numeric_citations() -> None:
    res = await call("resolve_citations", text="Shown before [1, 2] and [3-5].", papers=[])
    assert res["numeric_citations_found"] == 2
    assert "not checked" in res["note"]


# --- PRISMA ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_repeated_search_call_does_not_double_count() -> None:
    rid = await _review()
    args = {"review_id": rid, "source": "PubMed", "query": "x AND y", "date_run": "2026-01-01", "hits": 100}
    first = await call("prisma_record_search", **args)
    again = await call("prisma_record_search", **{**args, "source": "pubmed", "hits": 120})
    assert first["updated"] is False and again["updated"] is True
    assert again["records_identified"] == 120
    assert again["records_by_source"] == {"PubMed": 120}


@pytest.mark.asyncio
async def test_search_date_must_be_a_real_date() -> None:
    rid = await _review()
    res = await call("prisma_record_search", review_id=rid, source="PubMed", query="q", date_run="2026-02-30", hits=1)
    assert res["ok"] is False and "date_run" in res["error"]


@pytest.mark.asyncio
async def test_pending_full_text_is_not_counted_as_not_retrieved() -> None:
    rid = await _review()
    await call("prisma_record_search", review_id=rid, source="PubMed", query="q", date_run="2026-01-01", hits=3)
    await call("prisma_record_dedup", review_id=rid, duplicates_removed=0, method="none found")
    batch = [{"paper_ref": f"pmid:{i}", "stage": "title_abstract", "decision": "include"} for i in (1, 2, 3)]
    batch += [
        {"paper_ref": "pmid:1", "stage": "full_text", "decision": "include"},
        {"paper_ref": "pmid:2", "stage": "full_text", "decision": "not_retrieved"},
    ]
    assert (await call("screening_record_many", review_id=rid, decisions=batch))["ok"] is True
    st = await call("prisma_status", review_id=rid)
    assert st["full_text_requested"] == 3
    assert st["full_text_unavailable"] == 1
    assert st["full_text_assessed"] == 1
    assert st["full_text_pending"] == 1
    assert st["complete"] is False
    assert any("full_text decisions" in s for s in st["next_steps"])


@pytest.mark.asyncio
async def test_not_retrieved_only_at_full_text() -> None:
    rid = await _review()
    res = await call("screening_record", review_id=rid, paper_ref="pmid:1", stage="title_abstract", decision="not_retrieved")
    assert res["ok"] is False and "full_text" in res["error"]


@pytest.mark.asyncio
async def test_full_text_orphan_breaks_invariant() -> None:
    rid = await _review()
    await call("screening_record", review_id=rid, paper_ref="pmid:9", stage="full_text", decision="include")
    st = await call("prisma_status", review_id=rid)
    assert st["invariants_valid"] is False
    assert st["full_text_without_title_abstract_include"] == ["pmid:9"]


@pytest.mark.asyncio
async def test_prisma_status_unknown_reviewer_is_an_error() -> None:
    rid = await _review()
    await call("screening_record", review_id=rid, paper_ref="pmid:1", stage="title_abstract", decision="include", reviewer="Alice")
    res = await call("prisma_status", review_id=rid, reviewer="bob")
    assert res["ok"] is False
    assert "alice" in res["error"]
    # reviewer names are case-insensitive
    assert (await call("prisma_status", review_id=rid, reviewer="ALICE"))["reviewer_used"] == "alice"


@pytest.mark.asyncio
async def test_batch_with_repeated_key_is_refused() -> None:
    rid = await _review()
    batch = [
        {"paper_ref": "10.1000/a", "stage": "title_abstract", "decision": "include"},
        {"paper_ref": "10.1000/A", "stage": "title_abstract", "decision": "exclude", "reason": "x"},
    ]
    res = await call("screening_record_many", review_id=rid, decisions=batch)
    assert res["ok"] is False and res["index"] == 1
    assert (await call("screening_list", review_id=rid))["total"] == 0


@pytest.mark.asyncio
async def test_screening_list_pages_past_200() -> None:
    rid = await _review()
    batch = [{"paper_ref": f"pmid:{i}", "stage": "title_abstract", "decision": "include"} for i in range(250)]
    await call("screening_record_many", review_id=rid, decisions=batch)
    page1 = await call("screening_list", review_id=rid)
    assert page1["count"] == 200 and page1["total"] == 250 and page1["next_offset"] == 200
    page2 = await call("screening_list", review_id=rid, offset=page1["next_offset"])
    assert page2["count"] == 50 and page2["truncated"] is False and page2["next_offset"] is None
    refs = {r["paper_ref"] for r in page1["records"]} | {r["paper_ref"] for r in page2["records"]}
    assert len(refs) == 250


@pytest.mark.asyncio
async def test_kappa_ignores_not_retrieved_and_reports_table() -> None:
    rid = await _review()
    rows = []
    for name in ("alice", "bob"):
        rows += [
            {"paper_ref": "pmid:1", "stage": "full_text", "decision": "include", "reviewer": name},
            {"paper_ref": "pmid:2", "stage": "full_text", "decision": "exclude", "reason": "x", "reviewer": name},
            {"paper_ref": "pmid:3", "stage": "full_text", "decision": "not_retrieved", "reviewer": name},
        ]
    await call("screening_record_many", review_id=rid, decisions=rows)
    agr = await call("screening_agreement", review_id=rid, stage="full_text", reviewer_a="alice", reviewer_b="bob")
    assert agr["shared"] == 2 and agr["not_judged"] == 1
    assert agr["table"] == {"both_include": 1, "a_include_b_exclude": 0, "a_exclude_b_include": 0, "both_exclude": 1}
    assert agr["kappa"] == 1.0


def test_kappa_hand_computed_negative_value() -> None:
    """2 papers, complete disagreement: po 0, pe 0.5, kappa -1."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE screening (paper_ref TEXT, stage TEXT, reviewer TEXT, decision TEXT, reason TEXT, title TEXT, updated_at TEXT)"
    )
    conn.executemany(
        "INSERT INTO screening VALUES (?, 'title_abstract', ?, ?, 'r', NULL, 't')",
        [("p1", "a", "include"), ("p1", "b", "exclude"), ("p2", "a", "exclude"), ("p2", "b", "include")],
    )
    res = prisma.agreement(conn, "title_abstract", "a", "b")
    assert res["po"] == 0.0 and res["pe"] == 0.5 and res["kappa"] == -1.0
    assert res["interpretation"] == "poor"


# --- evidence checks --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_supported_claim_needs_source_text() -> None:
    rid = await _review()
    res = await call(
        "claim_record", review_id=rid, claim_text="X reduces Y", paper_ref="pmid:1", label="supported",
        reason="r", quote="x reduced y by half in adults",
    )
    assert res["ok"] is False and "source_text" in res["error"]


@pytest.mark.asyncio
async def test_claim_report_flags_weak_claims_left_in_report() -> None:
    rid = await _review()
    src = "In adults, X reduced Y by half over twelve weeks."
    await call("claim_record", review_id=rid, claim_text="X reduces Y in children", paper_ref="pmid:1",
               label="unsupported", reason="adults only", source_text=src)
    await call("claim_record", review_id=rid, claim_text="X halves Y in adults", paper_ref="pmid:1",
               label="supported", reason="stated", source_text=src, quote="X reduced Y by half over twelve weeks")
    report = "Results. X reduces Y in children. X halves Y in adults."
    res = await call("claim_report", review_id=rid, report_text=report)
    assert [c["claim_text"] for c in res["weak_claims_in_report"]] == ["X reduces Y in children"]
    assert "weak_claims_in_report" in res["next_step"]
    assert "counts" not in res  # the duplicated counts field is gone


@pytest.mark.asyncio
async def test_rob2_multiple_some_concerns_allows_recorded_high() -> None:
    rid = await _review()
    src = "Allocation was concealed. Outcome assessors were not blinded to group."
    q = "outcome assessors were not blinded"
    doms = {
        "D1": "low", "D2": "some concerns", "D3": "low", "D4": "Some_Concerns", "D5": "low",
    }
    for d, j in doms.items():
        res = await call("rob_record", review_id=rid, paper_ref="pmid:7", tool="rob2", domain=d, judgement=j, quote=q, source_text=src)
        assert res["ok"] is True, res
    summ = await call("rob_summary", review_id=rid)
    paper = summ["papers"][0]
    assert paper["overall"] == "some_concerns" and paper["notes"]
    res = await call("rob_record", review_id=rid, paper_ref="pmid:7", tool="rob2", domain="overall", judgement="high",
                     rationale="Two domains with concerns substantially lower confidence.")
    assert res["ok"] is True
    paper = (await call("rob_summary", review_id=rid))["papers"][0]
    assert paper["overall"] == "high" and paper["overall_by_rule"] == "some_concerns"


@pytest.mark.asyncio
async def test_rob2_recorded_overall_cannot_be_less_severe_than_rule() -> None:
    rid = await _review()
    src = "Randomisation used sealed envelopes that were not opaque."
    q = "sealed envelopes that were not opaque"
    for d in ("D1", "D2", "D3", "D4", "D5"):
        await call("rob_record", review_id=rid, paper_ref="pmid:8", tool="rob2", domain=d,
                   judgement="high" if d == "D1" else "low", quote=q, source_text=src)
    await call("rob_record", review_id=rid, paper_ref="pmid:8", tool="rob2", domain="overall", judgement="low", rationale="r")
    paper = (await call("rob_summary", review_id=rid))["papers"][0]
    assert paper["overall"] == "high"
    assert any("not allowed" in n for n in paper["notes"])


@pytest.mark.asyncio
async def test_robins_i_reviewer_can_resolve_no_information() -> None:
    rid = await _review()
    src = "Models adjusted for age, sex and baseline severity."
    q = "models adjusted for age, sex and baseline severity"
    for i in range(1, 8):
        j = "no_information" if i == 5 else ("moderate" if i == 1 else "low")
        await call("rob_record", review_id=rid, paper_ref="pmid:9", tool="robins_i", domain=f"D{i}", judgement=j,
                   quote="" if j == "no_information" else q, source_text=src)
    paper = (await call("rob_summary", review_id=rid))["papers"][0]
    assert paper["overall"] == "no_information"
    bad = await call("rob_record", review_id=rid, paper_ref="pmid:9", tool="robins_i", domain="overall", judgement="low", rationale="r")
    assert bad["ok"] is True
    paper = (await call("rob_summary", review_id=rid))["papers"][0]
    assert paper["overall"] == "no_information"  # 'low' is below the worst rated domain (moderate)
    await call("rob_record", review_id=rid, paper_ref="pmid:9", tool="robins_i", domain="overall", judgement="moderate",
               rationale="Missing data domain is not key here")
    paper = (await call("rob_summary", review_id=rid))["papers"][0]
    assert paper["overall"] == "moderate"


@pytest.mark.asyncio
async def test_robins_i_serious_beats_no_information() -> None:
    rid = await _review()
    src = "There was no adjustment for confounding."
    for i in range(1, 8):
        j = {1: "serious", 2: "no_information"}.get(i, "low")
        await call("rob_record", review_id=rid, paper_ref="pmid:10", tool="robins_i", domain=f"D{i}", judgement=j,
                   quote="" if j == "no_information" else "there was no adjustment for confounding", source_text=src)
    paper = (await call("rob_summary", review_id=rid))["papers"][0]
    assert paper["overall"] == "serious"


@pytest.mark.asyncio
async def test_nos_accepts_integer_stars_and_case_control_domains() -> None:
    rid = await _review()
    src = "Cases and controls were drawn from the same hospital population."
    q = "drawn from the same hospital population"
    for d, s in (("selection", 4), ("comparability", 2), ("exposure", 3)):
        res = await call("rob_record", review_id=rid, paper_ref="pmid:11", tool="nos_case_control", domain=d, judgement=s, quote=q, source_text=src)
        assert res["ok"] is True, res
    paper = (await call("rob_summary", review_id=rid))["papers"][0]
    assert paper["overall"] == "good" and paper["total_stars"] == 9


# --- infrastructure -------------------------------------------------------------------------


def test_concurrent_writes_to_one_review_do_not_fail() -> None:
    rid = store.create_review("Concurrency", "Q?")

    def write(i: int) -> dict[str, Any]:
        return prisma.screening_record(
            review_id=rid, paper_ref=f"pmid:{i}", stage="title_abstract", decision="include"
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(write, range(120)))
    assert all(r["ok"] for r in results), [r for r in results if not r["ok"]][:3]
    with store.review_db(rid) as conn:
        assert conn.execute("SELECT COUNT(*) FROM screening").fetchone()[0] == 120
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"


@pytest.mark.asyncio
async def test_review_file_from_first_release_is_upgraded() -> None:
    """A rob table created by the first release has no rationale column; opening the file adds it."""
    rid = store.create_review("Old file", "Q?")
    path = store.data_dir() / "reviews" / f"{rid}.db"
    raw = sqlite3.connect(path)
    raw.execute(
        "CREATE TABLE rob (paper_ref TEXT, tool TEXT, domain TEXT, judgement TEXT, quote TEXT, quote_verified INTEGER, "
        "source_chars INTEGER, source_sha256 TEXT, updated_at TEXT, PRIMARY KEY (paper_ref, tool, domain))"
    )
    raw.commit()
    raw.close()
    res = await call("rob_record", review_id=rid, paper_ref="pmid:1", tool="rob2", domain="overall", judgement="low", rationale="r")
    assert res["ok"] is True, res


@pytest.mark.asyncio
async def test_internal_errors_hide_paths_and_tracebacks(monkeypatch) -> None:
    rid = await _review()

    def boom(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError(r"failed at C:\Users\someone\secret\file.db")

    monkeypatch.setattr(prisma, "compute_status", boom)
    res = await call("prisma_status", review_id=rid)
    assert res["ok"] is False
    assert "secret" not in res["error"] and "Traceback" not in res["error"]
    assert "RuntimeError" in res["error"]


@pytest.mark.asyncio
async def test_wrong_types_return_errors_not_exceptions() -> None:
    rid = await _review()
    cases = [
        ("screening_record_many", {"review_id": rid, "decisions": [{"paper_ref": 12345, "stage": "x"}]}),
        ("screening_record", {"review_id": rid, "paper_ref": "pmid:1", "stage": "title_abstract", "decision": "include", "reviewer": "x" * 41}),
        ("prisma_record_search", {"review_id": rid, "source": "PubMed", "query": "q", "date_run": "2026-01-01", "hits": -1}),
        ("dedup_papers", {"papers": []}),
        ("screening_list", {"review_id": rid, "limit": 0}),
        ("evidence_table", {"review_id": rid, "format": "xlsx"}),
        ("export_bundle", {"review_id": "../etc"}),
        ("protocol_record", {"review_id": rid, "rob_tool": "cochrane"}),
    ]
    for name, args in cases:
        res = await call(name, **args)
        assert res["ok"] is False, (name, res)
        assert res["error"]
