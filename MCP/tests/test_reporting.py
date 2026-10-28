# SPDX-License-Identifier: MIT
"""Deterministic tests for prisma_excluded_studies and manuscript_check."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from citeproof_mcp import store
from citeproof_mcp.tools import reporting

from tests.conftest import call

# ---------------------------------------------------------------------------
# Unit tests for pure helper functions
# ---------------------------------------------------------------------------


def test_format_citation() -> None:
    # 1. Title present
    assert reporting.format_citation("10.1000/1", "A Great Paper") == "A Great Paper"
    # 2. Title empty, DOI ref
    assert reporting.format_citation("10.1000/1", "") == "https://doi.org/10.1000/1"
    assert reporting.format_citation("10.1000/1", None) == "https://doi.org/10.1000/1"
    # 3. Title empty, non-DOI ref
    assert reporting.format_citation("pmid:123456", None) == "title not recorded"
    assert reporting.format_citation("title:some study|2020", "") == "title not recorded"


def test_parse_reasons() -> None:
    # Empty
    assert reporting.parse_reasons(None) == ([], True)
    assert reporting.parse_reasons("") == ([], True)
    assert reporting.parse_reasons("   ;   ") == ([], True)

    # Single
    reasons, missing = reporting.parse_reasons("Wrong Population")
    assert missing is False
    assert reasons == ["wrong population"]

    # Multiple, normalized, deduplicated per paper
    reasons, missing = reporting.parse_reasons("Wrong population; Wrong comparator; WRONG POPULATION")
    assert missing is False
    assert reasons == ["wrong population", "wrong comparator"]


def test_number_matches_and_tolerance() -> None:
    # 2 decimal places -> tol = 0.005
    assert reporting.get_decimal_tolerance("0.82") == 0.005
    assert reporting.number_matches("0.82", 0.82) is True
    assert reporting.number_matches("0.82", 0.824) is True
    assert reporting.number_matches("0.82", 0.816) is True
    assert reporting.number_matches("0.82", 0.85) is False

    # 1 decimal place -> tol = 0.05
    assert reporting.get_decimal_tolerance("1.5") == 0.05
    assert reporting.number_matches("1.5", 1.54) is True
    assert reporting.number_matches("1.5", 1.46) is True
    assert reporting.number_matches("1.5", 1.6) is False

    # Whole number -> tol = 0.5
    assert reporting.get_decimal_tolerance("12") == 0.5
    assert reporting.number_matches("12", 12.0) is True
    assert reporting.number_matches("12", 12.4) is True
    assert reporting.number_matches("12", 13.0) is False

    # None and invalid
    assert reporting.number_matches("0.82", None) is False
    assert reporting.number_matches("abc", 1.0) is False


def test_find_effect_estimates() -> None:
    text = (
        "We found RR 0.82 (95% CI 0.70 to 0.96). Also hazard ratio: 1.45 (95% CI, 1.10 to 1.90), "
        "and MD: -2.5 (95% CI -4.0 to -1.0). In secondary outcomes, odds ratio of 2.0 [95% CI 1.2, 3.4] "
        "and standardised mean difference = 0.50 (95% CI 0.20-0.80)."
    )
    effects = reporting.find_effect_estimates(text)
    assert len(effects) == 5
    assert effects[0]["measure"] == "rr"
    assert effects[0]["estimate"] == "0.82"
    assert effects[0]["lower"] == "0.70"
    assert effects[0]["upper"] == "0.96"

    assert effects[1]["measure"] == "hr"
    assert effects[1]["estimate"] == "1.45"
    assert effects[1]["lower"] == "1.10"
    assert effects[1]["upper"] == "1.90"

    assert effects[2]["measure"] == "md"
    assert effects[2]["estimate"] == "-2.5"
    assert effects[2]["lower"] == "-4.0"
    assert effects[2]["upper"] == "-1.0"

    assert effects[3]["measure"] == "or"
    assert effects[3]["estimate"] == "2.0"
    assert effects[3]["lower"] == "1.2"
    assert effects[3]["upper"] == "3.4"

    assert effects[4]["measure"] == "smd"
    assert effects[4]["estimate"] == "0.50"
    assert effects[4]["lower"] == "0.20"
    assert effects[4]["upper"] == "0.80"


def test_find_named_databases() -> None:
    text = "We searched PubMed, MEDLINE, and Embase, as well as CENTRAL and the Cochrane Library."
    dbs = reporting.find_named_databases(text)
    names = [d["name"] for d in dbs]
    assert "PubMed" in names
    assert "MEDLINE" in names
    assert "Embase" in names
    assert "CENTRAL / Cochrane Library" in names


def test_find_search_dates() -> None:
    text = "Searches were run on 2026-01-15, and updated on 20 February 2026 and March 2026."
    dates = reporting.find_search_dates(text)
    assert len(dates) == 3
    assert dates[0]["iso_date"] == "2026-01-15"
    assert dates[1]["iso_date"] == "2026-02-20"
    assert dates[2]["month_str"] == "2026-03"


def test_find_reviewer_counts() -> None:
    text = "Two independent reviewers screened titles. Three reviewers extracted data."
    revs = reporting.find_reviewer_counts(text)
    assert len(revs) == 2
    assert revs[0]["stated_count"] == 2
    assert revs[1]["stated_count"] == 3


def test_find_rob_tools() -> None:
    text = "We used RoB 2 for randomized trials and ROBINS-I for non-randomized studies."
    tools = reporting.find_rob_tools(text)
    keys = [t["key"] for t in tools]
    assert "rob2" in keys
    assert "robins_i" in keys


def test_find_synthesis_methods() -> None:
    text = "A random-effects meta-analysis was performed. Narrative synthesis was used for other outcomes."
    synths = reporting.find_synthesis_methods(text)
    assert len(synths) == 2
    assert "random-effects" in synths[0]["stated_text"].lower()
    assert "narrative synthesis" in synths[1]["stated_text"].lower()


# ---------------------------------------------------------------------------
# Tool tests: prisma_excluded_studies
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_prisma_excluded_studies_empty() -> None:
    rev = await call("review_create", title="Test Review", question="Question?")
    rid = rev["review_id"]

    res_md = await call("prisma_excluded_studies", review_id=rid, format="markdown")
    assert res_md["ok"] is True
    assert res_md["format"] == "markdown"
    assert res_md["papers"] == 0
    assert res_md["by_reason"] == {}
    assert res_md["reconciliation"]["reconciles"] is True
    assert res_md["reconciliation"]["rows"] == 0
    assert res_md["reconciliation"]["excluded_full_text"] == 0
    assert "Paper reference" in res_md["table"]

    res_json = await call("prisma_excluded_studies", review_id=rid, format="json")
    assert res_json["ok"] is True
    assert res_json["format"] == "json"
    assert res_json["rows"] == []


@pytest.mark.asyncio
async def test_prisma_excluded_studies_partial_and_missing_reason(isolated_home: Path) -> None:
    rev = await call("review_create", title="Test Review", question="Question?")
    rid = rev["review_id"]

    # Ensure schema is applied
    store.open_review(rid).close()

    # Insert an old full-text row with missing reason and title via raw sqlite
    db_path = isolated_home / "reviews" / f"{rid}.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO screening (paper_ref, stage, reviewer, decision, reason, title, updated_at) "
        "VALUES ('10.1000/old1', 'full_text', 'primary', 'exclude', NULL, NULL, '2026-01-01')"
    )
    conn.execute(
        "INSERT INTO screening (paper_ref, stage, reviewer, decision, reason, title, updated_at) "
        "VALUES ('pmid:987654', 'full_text', 'primary', 'exclude', '   ', '', '2026-01-01')"
    )
    conn.commit()
    conn.close()

    res = await call("prisma_excluded_studies", review_id=rid, format="json")
    assert res["ok"] is True
    assert res["papers"] == 2
    rows = res["rows"]
    assert len(rows) == 2

    r1 = next(r for r in rows if r["paper_ref"] == "10.1000/old1")
    assert r1["reason_missing"] is True
    assert r1["reasons"] == []
    assert r1["citation"] == "https://doi.org/10.1000/old1"

    r2 = next(r for r in rows if r["paper_ref"] == "pmid:987654")
    assert r2["reason_missing"] is True
    assert r2["citation"] == "title not recorded"

    # In markdown table, "No reason recorded" must be displayed
    res_md = await call("prisma_excluded_studies", review_id=rid, format="markdown")
    assert "No reason recorded" in res_md["table"]


@pytest.mark.asyncio
async def test_prisma_excluded_studies_multiple_reasons() -> None:
    rev = await call("review_create", title="Test Review", question="Question?")
    rid = rev["review_id"]

    await call(
        "screening_record",
        review_id=rid,
        paper_ref="10.1000/paper1",
        stage="full_text",
        decision="exclude",
        reason="Wrong population; Wrong comparator",
        title="Study One",
        reviewer="primary",
    )
    await call(
        "screening_record",
        review_id=rid,
        paper_ref="10.1000/paper2",
        stage="full_text",
        decision="exclude",
        reason="Wrong population; Ineligible design",
        title="Study Two",
        reviewer="primary",
    )

    res = await call("prisma_excluded_studies", review_id=rid, format="json")
    assert res["ok"] is True
    assert res["papers"] == 2
    assert res["by_reason"]["wrong population"] == 2
    assert res["by_reason"]["wrong comparator"] == 1
    assert res["by_reason"]["ineligible design"] == 1

    r1 = next(r for r in res["rows"] if r["paper_ref"] == "10.1000/paper1")
    assert r1["reasons"] == ["wrong population", "wrong comparator"]
    assert r1["reason_missing"] is False
    assert r1["citation"] == "Study One"


@pytest.mark.asyncio
async def test_prisma_excluded_studies_reconciliation_true_and_cannot_reconcile() -> None:
    rev = await call("review_create", title="Test Review", question="Question?")
    rid = rev["review_id"]

    # 1. Normal state: 1 exclusion -> reconciles is True
    await call(
        "screening_record",
        review_id=rid,
        paper_ref="10.1000/paper1",
        stage="full_text",
        decision="exclude",
        reason="Wrong outcome",
        reviewer="primary",
    )
    res1 = await call("prisma_excluded_studies", review_id=rid)
    assert res1["ok"] is True
    assert res1["reconciliation"]["reconciles"] is True
    assert res1["reconciliation"]["rows"] == 1
    assert res1["reconciliation"]["excluded_full_text"] == 1
    assert res1["reconciliation"]["explanation"] is None

    # 2. 205 rows: the page shows 200, but reconciliation covers every row and paging reaches the rest.
    batch = [
        {
            "paper_ref": f"10.2000/trunc_{i:04d}",
            "stage": "full_text",
            "decision": "exclude",
            "reason": "Wrong outcome",
            "reviewer": "primary",
        }
        for i in range(204)
    ]
    await call("screening_record_many", review_id=rid, decisions=batch)

    res2 = await call("prisma_excluded_studies", review_id=rid)
    assert res2["ok"] is True
    assert res2["truncated"] is True
    assert res2["papers"] == 205
    assert res2["reconciliation"]["rows"] == 205
    assert res2["reconciliation"]["excluded_full_text"] == 205
    assert res2["reconciliation"]["reconciles"] is True
    assert res2["next_offset"] == 200
    page2 = await call("prisma_excluded_studies", review_id=rid, format="json", offset=200)
    assert len(page2["rows"]) == 5 and page2["truncated"] is False and page2["next_offset"] is None


# ---------------------------------------------------------------------------
# Tool tests: manuscript_check
# ---------------------------------------------------------------------------


async def _setup_clean_review() -> str:
    """Helper fixture: complete review that matches all stated facts."""
    rev = await call("review_create", title="Metformin for Diabetes", question="Efficacy?")
    rid = rev["review_id"]

    # Protocol
    await call(
        "protocol_record",
        review_id=rid,
        databases=["PubMed", "Embase"],
        synthesis_method="random-effects",
        population="Adults with T2D",
        intervention="Metformin",
        outcomes=["Mortality"],
        inclusion_criteria=["RCTs"],
        exclusion_criteria=["Animal studies"],
        search_strategy="diabetes AND metformin",
    )

    # Searches: 100 on PubMed, 50 on Embase on 2026-01-15 -> 150
    await call(
        "prisma_record_search",
        review_id=rid,
        source="PubMed",
        query="diabetes AND metformin",
        date_run="2026-01-15",
        hits=100,
    )
    await call(
        "prisma_record_search",
        review_id=rid,
        source="Embase",
        query="diabetes AND metformin",
        date_run="2026-01-15",
        hits=50,
    )

    # Dedup: 30 removed -> 120 after dedup
    await call("prisma_record_dedup", review_id=rid, duplicates_removed=30, method="Title/DOI normalisation")

    # Screening: 120 screened. 100 excluded, 20 included at title/abstract.
    # At full text: 8 excluded, 12 included.
    # Reviewers: alice and bob, plus primary consensus.
    ta_batch = [
        {
            "paper_ref": f"10.1000/study_{i:03d}",
            "stage": "title_abstract",
            "decision": "include" if i < 20 else "exclude",
            "reason": None if i < 20 else "not relevant",
            "reviewer": "primary",
        }
        for i in range(120)
    ]
    await call("screening_record_many", review_id=rid, decisions=ta_batch)

    # Add decisions for alice and bob so distinct non-primary count is 2
    await call(
        "screening_record",
        review_id=rid,
        paper_ref="10.1000/study_000",
        stage="title_abstract",
        decision="include",
        reviewer="alice",
    )
    await call(
        "screening_record",
        review_id=rid,
        paper_ref="10.1000/study_000",
        stage="title_abstract",
        decision="include",
        reviewer="bob",
    )

    ft_batch = [
        {
            "paper_ref": f"10.1000/study_{i:03d}",
            "stage": "full_text",
            "decision": "include" if i < 12 else "exclude",
            "reason": None if i < 12 else "ineligible design",
            "reviewer": "primary",
        }
        for i in range(20)
    ]
    await call("screening_record_many", review_id=rid, decisions=ft_batch)

    # Evidence row: RR 0.82 (95% CI 0.70 to 0.96)
    source_txt = "Results: metformin reduced mortality with RR 0.82 (95% CI 0.70 to 0.96)."
    await call(
        "evidence_record",
        review_id=rid,
        paper_ref="10.1000/study_000",
        outcome="Mortality",
        study_design="rct",
        effect_measure="rr",
        effect_estimate=0.82,
        ci_lower=0.70,
        ci_upper=0.96,
        quote="RR 0.82 (95% CI 0.70 to 0.96)",
        source_text=source_txt,
    )

    # RoB 2
    rob_txt = "Random sequence generation was performed using computer random numbers."
    await call(
        "rob_record",
        review_id=rid,
        paper_ref="10.1000/study_000",
        tool="rob2",
        domain="randomisation_process",
        judgement="low",
        quote="Random sequence generation was performed using computer random numbers",
        source_text=rob_txt,
    )

    return rid


@pytest.mark.asyncio
async def test_manuscript_check_clean_fixture() -> None:
    rid = await _setup_clean_review()

    sections = {
        "Abstract": "12 studies were included. Meta-analysis showed RR 0.82 (95% CI 0.70 to 0.96).",
        "Methods": (
            "We searched PubMed and Embase on 15 January 2026. Two independent reviewers screened "
            "records. Risk of bias was assessed using RoB 2. A random-effects meta-analysis was performed."
        ),
        "Results": "A total of 12 studies were included. The pooled effect was RR 0.82 (95% CI 0.70 to 0.96).",
    }

    res = await call("manuscript_check", review_id=rid, sections=sections)
    assert res["ok"] is True
    # Clean fixture has 0 mismatch findings
    mismatches = [f for f in res["findings"] if f["severity"] == "mismatch"]
    assert mismatches == []
    not_in_record = [f for f in res["findings"] if f["severity"] == "not_in_record"]
    assert not_in_record == []
    omitted = [f for f in res["findings"] if f["severity"] == "omitted"]
    assert omitted == []

    # Quantity map verified
    assert res["quantity_map"]["studies_included"] == 12
    assert res["quantity_map"]["prisma"]["records_identified"] == 150
    assert len(res["quantity_map"]["evidence"]) == 1


@pytest.mark.asyncio
async def test_manuscript_check_conflicting_included_count_across_sections() -> None:
    rid = await _setup_clean_review()

    sections = {
        "Abstract": "10 studies were included in the systematic review.",
        "Results": "In total, 12 studies were included in the final synthesis.",
    }

    res = await call("manuscript_check", review_id=rid, sections=sections)
    assert res["ok"] is True

    # Abstract states 10 (mismatches record 12)
    record_mismatches = [f for f in res["findings"] if f["kind"] == "prisma_count"]
    assert len(record_mismatches) == 1
    assert record_mismatches[0]["section"] == "Abstract"
    assert record_mismatches[0]["stated"] == 10
    assert record_mismatches[0]["recorded"] == 12

    # Cross-section mismatch between Abstract and Results
    cross_mismatches = [f for f in res["findings"] if f["kind"] == "cross_section_count"]
    assert len(cross_mismatches) == 1
    assert "Abstract" in cross_mismatches[0]["section"]
    assert "Results" in cross_mismatches[0]["section"]


@pytest.mark.asyncio
async def test_manuscript_check_count_vs_record_mismatch() -> None:
    rid = await _setup_clean_review()

    sections = {
        "Results": "Overall, 18 studies were included.",
    }

    res = await call("manuscript_check", review_id=rid, sections=sections)
    assert res["ok"] is True
    mismatches = [f for f in res["findings"] if f["kind"] == "prisma_count"]
    assert len(mismatches) == 1
    assert mismatches[0]["stated"] == 18
    assert mismatches[0]["recorded"] == 12
    assert mismatches[0]["field"] == "studies_included"


@pytest.mark.asyncio
async def test_manuscript_check_effect_estimate_not_in_record() -> None:
    rid = await _setup_clean_review()

    sections = {
        "Results": "For the secondary outcome, OR 2.50 (95% CI 1.50 to 3.50).",
    }

    res = await call("manuscript_check", review_id=rid, sections=sections)
    assert res["ok"] is True
    findings = [f for f in res["findings"] if f["kind"] == "effect_estimate"]
    assert len(findings) == 1
    assert findings[0]["severity"] == "not_in_record"
    assert "OR" in findings[0]["stated"]


@pytest.mark.asyncio
async def test_manuscript_check_ci_limit_mismatch_and_cross_section() -> None:
    rid = await _setup_clean_review()

    # 1. Single section CI limit mismatch against record (recorded is 0.70 to 0.96)
    sections = {
        "Results": "The pooled risk ratio was RR 0.82 (95% CI 0.60 to 0.90).",
    }
    res = await call("manuscript_check", review_id=rid, sections=sections)
    assert res["ok"] is True
    ci_mismatches = [f for f in res["findings"] if f["kind"] == "effect_estimate" and f["severity"] == "mismatch"]
    assert len(ci_mismatches) == 1
    assert ci_mismatches[0]["field"] == "ci_limits"
    assert "0.60 to 0.90" in ci_mismatches[0]["stated"]
    assert "0.7 to 0.96" in ci_mismatches[0]["recorded"]

    # 2. Cross section conflict: Abstract states 0.70 to 0.96 (matches record), Results states 0.65 to 0.99
    sections_cross = {
        "Abstract": "Mortality was reduced (RR 0.82, 95% CI 0.70 to 0.96).",
        "Results": "Mortality was reduced (RR 0.82, 95% CI 0.65 to 0.99).",
    }
    res_cross = await call("manuscript_check", review_id=rid, sections=sections_cross)
    assert res_cross["ok"] is True
    cross_eff = [f for f in res_cross["findings"] if f["kind"] == "cross_section_effect"]
    assert len(cross_eff) == 1
    assert "Abstract" in cross_eff[0]["section"]
    assert "Results" in cross_eff[0]["section"]


@pytest.mark.asyncio
async def test_manuscript_check_databases_named_and_omitted() -> None:
    rid = await _setup_clean_review()

    # Search log has PubMed and Embase. Methods mentions PubMed and Scopus.
    # Scopus is not_in_record; Embase is omitted.
    sections = {
        "Methods": "We searched PubMed and Scopus.",
    }
    res = await call("manuscript_check", review_id=rid, sections=sections)
    assert res["ok"] is True

    db_not_in_record = [f for f in res["findings"] if f["kind"] == "database" and f["severity"] == "not_in_record"]
    assert len(db_not_in_record) == 1
    assert db_not_in_record[0]["stated"] == "Scopus"

    db_omitted = [f for f in res["findings"] if f["kind"] == "database" and f["severity"] == "omitted"]
    assert len(db_omitted) == 1
    assert db_omitted[0]["recorded"] == "Embase"


@pytest.mark.asyncio
async def test_manuscript_check_reviewer_count_mismatch() -> None:
    rid = await _setup_clean_review()

    # Record has 2 reviewers (alice and bob). Manuscript claims 3.
    sections = {
        "Methods": "Three independent reviewers screened titles and abstracts.",
    }
    res = await call("manuscript_check", review_id=rid, sections=sections)
    assert res["ok"] is True

    rev_mismatches = [f for f in res["findings"] if f["kind"] == "reviewer_count"]
    assert len(rev_mismatches) == 1
    assert rev_mismatches[0]["severity"] == "mismatch"
    assert rev_mismatches[0]["stated"] == 3
    assert rev_mismatches[0]["recorded"] == 2


@pytest.mark.asyncio
async def test_manuscript_check_rob_tool_mismatch_and_omitted() -> None:
    rid = await _setup_clean_review()

    # Record has RoB 2. Manuscript claims ROBINS-I.
    sections = {
        "Methods": "Risk of bias was evaluated with ROBINS-I.",
    }
    res = await call("manuscript_check", review_id=rid, sections=sections)
    assert res["ok"] is True

    rob_mismatches = [f for f in res["findings"] if f["kind"] == "rob_tool" and f["severity"] == "mismatch"]
    assert len(rob_mismatches) == 1
    assert rob_mismatches[0]["stated"] == "ROBINS-I"

    rob_omitted = [f for f in res["findings"] if f["kind"] == "rob_tool" and f["severity"] == "omitted"]
    assert len(rob_omitted) == 1
    assert rob_omitted[0]["recorded"] == "RoB 2"


@pytest.mark.asyncio
async def test_manuscript_check_synthesis_unknown() -> None:
    # Review with no synthesis method recorded in protocol
    rev = await call("review_create", title="Test Review", question="Question?")
    rid = rev["review_id"]

    sections = {
        "Methods": "We performed a random-effects meta-analysis.",
    }
    res = await call("manuscript_check", review_id=rid, sections=sections)
    assert res["ok"] is True

    synth_findings = [f for f in res["findings"] if f["kind"] == "synthesis_method"]
    assert len(synth_findings) == 1
    assert synth_findings[0]["severity"] == "unknown"
    assert "no recorded synthesis method" in synth_findings[0]["message"]


@pytest.mark.asyncio
async def test_manuscript_check_missing_record_data() -> None:
    # Empty review with no searches, no rob rows, no evidence rows
    rev = await call("review_create", title="Empty Review", question="Question?")
    rid = rev["review_id"]

    sections = {
        "Methods": "We searched PubMed on 2026-01-15 using RoB 2.",
    }
    res = await call("manuscript_check", review_id=rid, sections=sections)
    assert res["ok"] is True

    unknown_fields = {f["field"] for f in res["findings"] if f["severity"] == "unknown"}
    assert "search_log" in unknown_fields
    assert "rob_rows" in unknown_fields
    assert "evidence_rows" in unknown_fields


@pytest.mark.asyncio
async def test_manuscript_check_line_numbers_correct() -> None:
    rid = await _setup_clean_review()

    # Put count mismatch on line 5
    text = "\n\n\n\n18 studies were included."
    sections = {"Results": text}

    res = await call("manuscript_check", review_id=rid, sections=sections)
    assert res["ok"] is True
    finding = next(f for f in res["findings"] if f["kind"] == "prisma_count")
    assert finding["line"] == 5


@pytest.mark.asyncio
async def test_manuscript_check_invalid_inputs() -> None:
    rid = await _setup_clean_review()

    # 1. sections not a dict (e.g. list)
    bad1 = await call("manuscript_check", review_id=rid, sections=["not", "a", "dict"])
    assert bad1["ok"] is False
    assert "object" in bad1["error"]

    # 2. Too many sections (> 20)
    too_many = {f"Section_{i}": "Some text" for i in range(21)}
    bad2 = await call("manuscript_check", review_id=rid, sections=too_many)
    assert bad2["ok"] is False
    assert "between 1 and 20" in bad2["error"]

    # 3. Empty text
    bad3 = await call("manuscript_check", review_id=rid, sections={"Abstract": "   "})
    assert bad3["ok"] is False
    assert "empty text" in bad3["error"]


def test_lowercase_words_are_not_read_as_abbreviations() -> None:
    from citeproof_mcp.tools import reporting

    text = "Central venous catheters or 2 (CI 1 to 3); nos. 4; two authors drafted it."
    assert reporting.find_effect_estimates(text) == []
    assert reporting.find_named_databases(text) == []
    assert reporting.find_rob_tools(text) == []
    assert reporting.find_reviewer_counts(text) == []
    assert reporting.find_reviewer_counts("Two review authors screened")[0]["stated_count"] == 2


def test_review_fixes_rob1_and_synthesis_classes() -> None:
    from citeproof_mcp.tools import reporting

    keys = {t["key"] for t in reporting.find_rob_tools("We used the Cochrane risk of bias 2 tool.")}
    assert keys == {"rob2"}
    assert {t["key"] for t in reporting.find_rob_tools("Cochrane risk of bias tool")} == {"rob1"}
    assert reporting.synthesis_classes("random-effects model") == reporting.synthesis_classes(
        "random-effects meta-analysis"
    ) == {"random"}
