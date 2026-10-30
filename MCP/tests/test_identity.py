# SPDX-License-Identifier: MIT
"""Tests for canonical paper aliases, study grouping and known-item recall."""

from __future__ import annotations

import json

import pytest

from tests.conftest import call


async def _review() -> str:
    res = await call("review_create", title="Identity review", question="Does drug X reduce mortality?")
    return res["review_id"]


async def _search(rid: str, hits: int, source: str = "PubMed") -> None:
    await call(
        "prisma_record_search",
        review_id=rid,
        source=source,
        query="drug X",
        date_run="2026-01-01",
        hits=hits,
    )


async def _include(rid: str, ref: str) -> None:
    for stage in ("title_abstract", "full_text"):
        res = await call(
            "screening_record",
            review_id=rid,
            paper_ref=ref,
            stage=stage,
            decision="include",
            reviewer="primary",
        )
        assert res["ok"] is True, res


@pytest.mark.asyncio
async def test_dedup_aliases_resolve_every_member_identifier_and_survive_reopen() -> None:
    rid = await _review()
    papers = [
        {
            "id": "a",
            "doi": "10.1000/abc",
            "pmid": "111",
            "title": "Trial of drug X in adults",
            "year": 2020,
            "authors": ["Smith, J"],
        },
        {"id": "b", "doi": "https://doi.org/10.1000/ABC", "title": "Trial of drug X in adults.", "year": 2020},
        {
            "id": "c",
            "doi": "10.1000/single",
            "pmid": "222",
            "title": "Single trial of drug Y",
            "year": 2021,
        },
    ]
    await _search(rid, hits=len(papers))
    first = await call("dedup_papers", papers=papers, review_id=rid)
    assert first["ok"] is True
    assert first["recorded"] is True
    assert first["aliases_recorded"] > 0
    assert first["alias_conflicts"] == 0

    refs = ["pmid:111", "10.1000/abc", "title:Trial of drug X in adults|2020", "10.1000/unknown"]
    resolved = await call("paper_resolve", review_id=rid, paper_refs=refs)
    assert resolved["ok"] is True
    assert resolved["count"] == 4
    by_input = {r["input"]: r for r in resolved["refs"]}
    for ref in refs[:3]:
        assert by_input[ref]["canonical_ref"] == "10.1000/abc"
        assert by_input[ref]["status"] == "resolved"
        assert "pmid:111" in by_input[ref]["aliases"]
        assert "title:trial of drug x in adults|2020" in by_input[ref]["aliases"]
    assert by_input["10.1000/unknown"]["status"] == "unknown"
    assert by_input["10.1000/unknown"]["canonical_ref"] == "10.1000/unknown"

    # A fresh server call reads the same mappings from the review file.
    reopened = await call("paper_resolve", review_id=rid, paper_refs=refs[:3])
    assert [r["canonical_ref"] for r in reopened["refs"]] == ["10.1000/abc"] * 3
    assert [r["status"] for r in reopened["refs"]] == ["resolved"] * 3

    # Re-running the same dedup is idempotent.
    again = await call("dedup_papers", papers=papers, review_id=rid)
    assert again["recorded"] is True
    assert again["aliases_recorded"] == 0
    assert again["alias_conflicts"] == 0


@pytest.mark.asyncio
async def test_conflicting_dois_are_surfaced_and_not_merged() -> None:
    rid = await _review()
    papers = [
        {"id": "a", "doi": "10.1000/one", "pmid": "55", "title": "First report of the trial", "year": 2020},
        {"id": "b", "doi": "10.1000/two", "pmid": "55", "title": "Second report of the trial", "year": 2020},
    ]
    await _search(rid, hits=len(papers))
    res = await call("dedup_papers", papers=papers, review_id=rid)
    assert res["recorded"] is True
    assert res["alias_conflicts"] == 1

    resolved = await call("paper_resolve", review_id=rid, paper_refs=["10.1000/two", "pmid:55"])
    by_input = {r["input"]: r for r in resolved["refs"]}
    assert by_input["10.1000/two"]["canonical_ref"] == "10.1000/two"
    assert by_input["10.1000/two"]["status"] == "conflict"
    assert by_input["10.1000/two"]["conflicts"][0]["proposed_ref"] == "10.1000/one"
    assert by_input["pmid:55"]["canonical_ref"] == "10.1000/one"
    assert by_input["pmid:55"]["status"] == "resolved"


@pytest.mark.asyncio
async def test_existing_alias_is_not_overwritten_by_a_later_dedup() -> None:
    rid = await _review()
    await _search(rid, hits=1)
    first = await call(
        "dedup_papers",
        papers=[{"pmid": "999", "title": "Original report without a doi", "year": 2020}],
        review_id=rid,
    )
    assert first["recorded"] is True and first["alias_conflicts"] == 0

    later = await call(
        "dedup_papers",
        papers=[{"doi": "10.1000/later", "pmid": "999", "title": "Original report without a doi", "year": 2020}],
        review_id=rid,
    )
    assert later["recorded"] is True
    assert later["alias_conflicts"] >= 1

    resolved = await call("paper_resolve", review_id=rid, paper_refs=["pmid:999"])
    row = resolved["refs"][0]
    assert row["canonical_ref"] == "pmid:999"
    assert row["status"] == "conflict"


@pytest.mark.asyncio
async def test_manual_alias_requires_replace_and_keeps_the_previous_mapping() -> None:
    rid = await _review()
    first = await call(
        "paper_alias_record",
        review_id=rid,
        alias="10.1000/preprint",
        canonical_ref="10.1000/published",
    )
    assert first["ok"] is True
    assert first["recorded"] is True
    assert first["replaced"] is False
    assert first["previous_canonical"] is None

    same = await call(
        "paper_alias_record",
        review_id=rid,
        alias="10.1000/preprint",
        canonical_ref="10.1000/published",
    )
    assert same["recorded"] is False and same["replaced"] is False

    refused = await call(
        "paper_alias_record",
        review_id=rid,
        alias="10.1000/preprint",
        canonical_ref="10.1000/final",
    )
    assert refused["ok"] is False
    assert "replace=true" in refused["error"]

    replaced = await call(
        "paper_alias_record",
        review_id=rid,
        alias="10.1000/preprint",
        canonical_ref="10.1000/final",
        replace=True,
    )
    assert replaced["ok"] is True
    assert replaced["replaced"] is True
    assert replaced["previous_canonical"] == "10.1000/published"

    resolved = await call("paper_resolve", review_id=rid, paper_refs=["10.1000/preprint"])
    row = resolved["refs"][0]
    assert row["canonical_ref"] == "10.1000/final"
    # The explicit replace is the user's decision, so nothing is left open; the old mapping stays in alias_conflicts.
    assert row["status"] == "resolved"
    assert row["conflicts"] == []


@pytest.mark.asyncio
async def test_study_link_relink_and_unlink() -> None:
    rid = await _review()
    linked = await call("study_link", review_id=rid, study_id="Study-1", paper_refs=["10.1000/a", "pmid:5"])
    assert linked["ok"] is True
    assert linked["linked"] == ["10.1000/a", "pmid:5"]
    assert linked["reports_in_study"] == 2

    same = await call("study_link", review_id=rid, study_id="Study-1", paper_refs=["10.1000/a"])
    assert same["linked"] == [] and same["unchanged"] == ["10.1000/a"]

    refused = await call("study_link", review_id=rid, study_id="Study_2", paper_refs=["10.1000/a"])
    assert refused["ok"] is False
    assert "replace=true" in refused["error"]

    moved = await call("study_link", review_id=rid, study_id="Study_2", paper_refs=["10.1000/a"], replace=True)
    assert moved["ok"] is True
    assert moved["moved"] == [{"paper_ref": "10.1000/a", "from_study": "Study-1", "to_study": "Study_2"}]
    assert moved["linked"] == []

    removed = await call("study_unlink", review_id=rid, paper_refs=["10.1000/a"])
    assert removed["ok"] is True
    assert removed["removed"] == ["10.1000/a"]
    assert removed["count"] == 1

    nothing = await call("study_unlink", review_id=rid, paper_refs=["10.1000/a"])
    assert nothing["removed"] == [] and nothing["not_linked"] == ["10.1000/a"]


@pytest.mark.asyncio
async def test_study_link_is_found_through_an_alias_recorded_later() -> None:
    rid = await _review()
    await call("study_link", review_id=rid, study_id="S1", paper_refs=["10.1000/pre"])
    await call("paper_alias_record", review_id=rid, alias="10.1000/pre", canonical_ref="10.1000/pub")

    resolved = await call("paper_resolve", review_id=rid, paper_refs=["10.1000/pub"])
    assert resolved["refs"][0]["canonical_ref"] == "10.1000/pub"
    assert resolved["refs"][0]["study_id"] == "S1"

    removed = await call("study_unlink", review_id=rid, paper_refs=["10.1000/pub"])
    assert removed["removed"] == ["10.1000/pub"]
    assert (await call("paper_resolve", review_id=rid, paper_refs=["10.1000/pre"]))["refs"][0]["study_id"] is None


@pytest.mark.asyncio
async def test_study_overview_separates_reports_from_studies() -> None:
    rid = await _review()
    refs = ["10.1000/p1", "10.1000/p2", "10.1000/p3", "10.1000/p4"]
    papers = [
        {"id": "p1", "doi": refs[0], "title": "Report one of the trial", "year": 2020},
        {"id": "p2", "doi": refs[1], "title": "Report two of the trial", "year": 2021},
        {"id": "p3", "doi": refs[2], "title": "Separate trial report", "year": 2020},
        {"id": "p4", "doi": refs[3], "title": "Unlinked included report", "year": 2022},
    ]
    await _search(rid, hits=len(papers))
    assert (await call("dedup_papers", papers=papers, review_id=rid))["recorded"] is True

    await call("study_link", review_id=rid, study_id="S1", paper_refs=refs[:2])
    await call("study_link", review_id=rid, study_id="S2", paper_refs=[refs[2]])
    for ref in refs:
        await _include(rid, ref)

    for ref, size in ((refs[0], 240), (refs[1], 260), (refs[2], 300)):
        source = f"Report states {size} participants were analysed."
        rec = await call(
            "evidence_record",
            review_id=rid,
            paper_ref=ref,
            outcome="all-cause mortality",
            study_design="RCT",
            quote=f"{size} participants were analysed",
            source_text=source,
            sample_size=size,
        )
        assert rec["ok"] is True, rec
        assert rec["quote_verified"] is True

    overview = await call("study_overview", review_id=rid)
    assert overview["report_count"] == 4
    assert overview["study_count"] == 3
    groups = {g["study_id"]: g for g in overview["groups"]}
    assert groups["S1"]["reports"] == [refs[0], refs[1]]
    unlinked = [g for g in overview["groups"] if g["unlinked"]]
    assert len(unlinked) == 1 and unlinked[0]["reports"] == [refs[3]]

    risk = overview["duplicate_participant_risk"]
    assert len(risk) == 1
    assert risk[0]["study_id"] == "S1"
    assert risk[0]["outcome_key"] == "all-cause mortality"
    assert risk[0]["reports"] == [refs[0], refs[1]]

    # PRISMA counts reports (4); the study-level view counts 3 studies.
    status = await call("prisma_status", review_id=rid)
    assert status["studies_included"] == 4


@pytest.mark.asyncio
async def test_recall_check_found_not_found_unresolved_and_per_source() -> None:
    rid = await _review()
    papers = [
        {
            "id": "r1",
            "doi": "10.1000/found",
            "pmid": "777",
            "title": "Known item trial of drug X",
            "year": 2020,
            "source": "PubMed",
        },
        {
            "id": "r2",
            "doi": "10.1000/other",
            "pmid": "888",
            "title": "Unrelated report of drug Y",
            "year": 2021,
            "source": "Embase",
        },
        {"id": "r3", "title": "Yearless trial of drug X", "source": "PubMed"},
        {"id": "r4", "doi": "10.1000/fourth", "title": "Fourth distinct report", "year": 2022, "source": "PubMed"},
    ]
    await _search(rid, hits=3, source="PubMed")
    await _search(rid, hits=1, source="Embase")

    seeds = await call(
        "recall_seed_record",
        review_id=rid,
        paper_refs=["pmid:777", "10.1000/missing", "title:Yearless trial of drug X|2020"],
        labels=["known item", "negative control", "yearless candidate"],
    )
    assert seeds["ok"] is True
    assert seeds["count"] == 3
    assert {s["label"] for s in seeds["seeds"]} == {"known item", "negative control", "yearless candidate"}

    before = await call("recall_check", review_id=rid)
    assert before["ok"] is True
    assert before["records_checked"] == 0
    assert before["retrieval_recorded_at"] is None
    assert before["counts"] == {"found": 0, "not_found": 0, "unresolved": 3}
    assert before["recall"] is None

    assert (await call("dedup_papers", papers=papers, review_id=rid))["recorded"] is True

    after = await call("recall_check", review_id=rid)
    assert after["ok"] is True
    assert after["records_checked"] == 4
    assert after["retrieval_recorded_at"] is not None
    by_ref = {s["paper_ref"]: s for s in after["seeds"]}
    assert by_ref["pmid:777"]["status"] == "found"
    assert by_ref["pmid:777"]["label"] == "known item"
    assert by_ref["pmid:777"]["canonical_ref"] == "10.1000/found"
    assert by_ref["pmid:777"]["sources"] == ["PubMed"]
    assert by_ref["10.1000/missing"]["status"] == "not_found"
    assert by_ref["title:yearless trial of drug x|2020"]["status"] == "unresolved"
    assert "no year" in by_ref["title:yearless trial of drug x|2020"]["reason"]
    assert after["counts"] == {"found": 1, "not_found": 1, "unresolved": 1}
    assert after["recall"] == 0.5

    per_source = {s["source"]: s for s in after["per_source"]}
    assert per_source["PubMed"]["records"] == 3
    assert per_source["PubMed"]["found"] == 1
    assert per_source["PubMed"]["missed"] == 1
    assert per_source["PubMed"]["unresolved"] == 1
    assert per_source["Embase"]["records"] == 1
    assert per_source["Embase"]["found"] == 0
    assert per_source["Embase"]["missed"] == 2
    assert per_source["Embase"]["unresolved"] == 1
    assert "not proof" in after["note"]
    assert isinstance(after["check_id"], int)

    # A changed search log clears the dedup record, so the old snapshot is not reused.
    await _search(rid, hits=5, source="PubMed")
    stale = await call("recall_check", review_id=rid)
    assert stale["records_checked"] == 0
    assert stale["counts"] == {"found": 0, "not_found": 0, "unresolved": 3}


@pytest.mark.asyncio
async def test_recall_marks_a_seed_with_an_alias_conflict_unresolved() -> None:
    rid = await _review()
    # Two DOIs in one cluster (shared PMID): the second DOI is a recorded, open conflict.
    papers = [
        {"doi": "10.1000/a", "pmid": "111", "title": "Seed report for the conflict", "year": 2020},
        {"doi": "10.1000/b", "pmid": "111", "title": "Seed report for the conflict", "year": 2020},
    ]
    await _search(rid, hits=2)
    assert (await call("dedup_papers", papers=papers, review_id=rid))["recorded"] is True
    await call("recall_seed_record", review_id=rid, paper_refs=["10.1000/b"])

    result = await call("recall_check", review_id=rid)
    assert result["counts"] == {"found": 0, "not_found": 0, "unresolved": 1}
    assert result["recall"] is None
    assert "alias conflict" in result["seeds"][0]["reason"]

    # Deciding the mapping clears the conflict, and the seed counts again.
    await call("paper_alias_record", review_id=rid, alias="10.1000/b", canonical_ref="10.1000/a")
    result = await call("recall_check", review_id=rid)
    assert result["counts"] == {"found": 1, "not_found": 0, "unresolved": 0}


@pytest.mark.asyncio
async def test_identity_tools_reject_invalid_input() -> None:
    rid = await _review()
    many = [f"10.1000/x{i}" for i in range(201)]

    assert (await call("paper_resolve", review_id=rid, paper_refs=[]))["ok"] is False
    assert (await call("paper_resolve", review_id=rid, paper_refs=many))["ok"] is False
    assert (await call("paper_alias_record", review_id=rid, alias="not a ref", canonical_ref="10.1000/x"))["ok"] is False

    assert (await call("study_link", review_id=rid, study_id="bad id!", paper_refs=["10.1000/a"]))["ok"] is False
    assert (await call("study_link", review_id=rid, study_id="S1", paper_refs=[]))["ok"] is False
    assert (await call("study_unlink", review_id=rid, paper_refs=[]))["ok"] is False

    assert (await call("recall_seed_record", review_id=rid, paper_refs=[]))["ok"] is False
    assert (await call("recall_seed_record", review_id=rid, paper_refs=many))["ok"] is False
    mismatch = await call(
        "recall_seed_record",
        review_id=rid,
        paper_refs=["10.1000/a", "10.1000/b"],
        labels=["only one"],
    )
    assert mismatch["ok"] is False
    assert "labels" in mismatch["error"]

    assert (await call("recall_check", review_id=rid))["ok"] is False


@pytest.mark.asyncio
async def test_review_fixes_conflict_resolution_labels_late_alias_and_stale_snapshot() -> None:
    rid = await _review()
    papers = [
        {"id": "a", "doi": "10.1000/pre", "title": "Preprint of the trial report", "year": 2020, "source": "PubMed"},
        {"id": "b", "doi": "10.1000/other", "title": "Another unrelated trial report", "year": 2021, "source": "Embase"},
    ]
    await _search(rid, hits=len(papers))
    assert (await call("dedup_papers", papers=papers, review_id=rid))["recorded"] is True

    # Repeated labels and repeated refs keep their positions.
    seeds = await call(
        "recall_seed_record", review_id=rid, paper_refs=["10.1000/pub", "10.1000/pub", "10.1000/other"],
        labels=["PubMed", "PubMed", "Embase"],
    )
    assert seeds["ok"] is True and seeds["added"] == 2

    # A manual alias made after the dedup applies to the stored records.
    await call("paper_alias_record", review_id=rid, alias="10.1000/pre", canonical_ref="10.1000/pub")
    check = await call("recall_check", review_id=rid)
    assert {s["paper_ref"]: s["status"] for s in check["seeds"]} == {"10.1000/pub": "found", "10.1000/other": "found"}

    # An explicit mapping resolves an open conflict; the provenance row stays.
    await call("paper_alias_record", review_id=rid, alias="10.1000/pre", canonical_ref="10.1000/other", replace=True)
    resolved = await call("paper_resolve", review_id=rid, paper_refs=["10.1000/pre"])
    assert resolved["refs"][0]["status"] == "resolved"

    # A dedup count recorded elsewhere invalidates the snapshot.
    await call("prisma_record_dedup", review_id=rid, duplicates_removed=0, method="manual count in EndNote")
    stale = await call("recall_check", review_id=rid)
    assert stale["counts"]["unresolved"] == 2


@pytest.mark.asyncio
async def test_duplicate_risk_counts_only_included_reports_once_each() -> None:
    rid = await _review()
    refs = ["10.1000/r1", "10.1000/r2"]
    await call("study_link", review_id=rid, study_id="S1", paper_refs=refs)
    await _include(rid, refs[0])
    await call(
        "screening_record", review_id=rid, paper_ref=refs[1], stage="full_text", decision="exclude",
        reason="wrong outcome", reviewer="primary",
    )
    await call("paper_alias_record", review_id=rid, alias="pmid:123", canonical_ref=refs[0])
    for ref in (refs[0], "pmid:123", refs[1]):
        rec = await call(
            "evidence_record", review_id=rid, paper_ref=ref, outcome="mortality", study_design="RCT",
            quote="240 participants were analysed", source_text="Report states 240 participants were analysed.",
        )
        assert rec["ok"] is True, rec
    overview = await call("study_overview", review_id=rid)
    assert overview["duplicate_participant_risk"] == []


@pytest.mark.asyncio
async def test_search_log_change_drops_the_retrieval_snapshot() -> None:
    rid = await _review()
    papers = [{"id": "a", "doi": "10.1000/a", "title": "Only trial report", "year": 2020, "source": "PubMed"}]
    await _search(rid, hits=1)
    assert (await call("dedup_papers", papers=papers, review_id=rid))["recorded"] is True
    await call("recall_seed_record", review_id=rid, paper_refs=["10.1000/a"])
    assert (await call("recall_check", review_id=rid))["counts"]["found"] == 1

    old = json.loads((await call("export_bundle", review_id=rid, format="json"))["content"])["dedup"]
    await _search(rid, hits=5, source="Embase")
    # Even a manual count identical to the old record must not revive the old snapshot.
    await call("prisma_record_dedup", review_id=rid, duplicates_removed=old["duplicates_removed"], method=old["method"])
    stale = await call("recall_check", review_id=rid)
    assert stale["counts"] == {"found": 0, "not_found": 0, "unresolved": 1}


@pytest.mark.asyncio
async def test_prisma_flow_counts_linked_studies_and_reports() -> None:
    rid = await _review()
    refs = ["10.1000/s1a", "10.1000/s1b", "10.1000/s2"]
    for ref in refs:
        await _include(rid, ref)
    await call("study_link", review_id=rid, study_id="S1", paper_refs=refs[:2])
    status = await call("prisma_status", review_id=rid)
    assert (status["studies_included"], status["reports_included"], status["study_count"]) == (3, 3, 2)
    flow = await call("prisma_flow_diagram", review_id=rid, format="markdown")
    assert "Studies included in review: 2" in flow["diagram"] and "Reports of included studies: 3" in flow["diagram"]
    check = await call("prisma_checklist", review_id=rid, report_text="Two studies were included. 2 studies were included.")
    assert all(n["matches"] for n in check["numbers"] if n["field"] == "studies_included")

    # PRISMA 2020 wording "N studies (M reports)" is not flagged, and both counts are in the quantity map.
    ms = await call("manuscript_check", review_id=rid, sections={"Results": "We included 2 studies (3 reports)."})
    assert ms["ok"] is True
    assert not [f for f in ms["findings"] if f["kind"] == "prisma_count"], ms["findings"]
    assert (ms["quantity_map"]["study_count"], ms["quantity_map"]["reports_included"]) == (2, 3)
    assert ms["method"] == "pattern"


@pytest.mark.asyncio
async def test_recall_counts_a_record_without_source_as_unknown() -> None:
    rid = await _review()
    await _search(rid, hits=1)
    papers = [{"id": "r1", "doi": "10.1000/nosource", "title": "Record with no source", "year": 2020}]
    assert (await call("dedup_papers", papers=papers, review_id=rid))["recorded"] is True
    await call("recall_seed_record", review_id=rid, paper_refs=["10.1000/nosource"])

    res = await call("recall_check", review_id=rid)
    assert res["seeds"][0]["status"] == "found"
    assert res["seeds"][0]["sources"] == ["unknown"]
    unknown = {s["source"]: s for s in res["per_source"]}["unknown"]
    assert (unknown["found"], unknown["missed"]) == (1, 0)


@pytest.mark.asyncio
async def test_study_link_is_found_through_a_chain_of_aliases() -> None:
    rid = await _review()
    await call("study_link", review_id=rid, study_id="S1", paper_refs=["10.1000/pre"])
    await call("paper_alias_record", review_id=rid, alias="10.1000/pre", canonical_ref="10.1000/mid")
    await call("paper_alias_record", review_id=rid, alias="10.1000/mid", canonical_ref="10.1000/pub")

    resolved = await call("paper_resolve", review_id=rid, paper_refs=["10.1000/pub"])
    assert resolved["refs"][0]["study_id"] == "S1"
    again = await call("study_link", review_id=rid, study_id="S1", paper_refs=["10.1000/pub"])
    assert again["unchanged"] == ["10.1000/pub"]


@pytest.mark.asyncio
async def test_unlinked_report_never_shares_a_group_with_a_study_id() -> None:
    rid = await _review()
    await call("study_link", review_id=rid, study_id="unlinked:pmid:123", paper_refs=["10.1000/linked"])
    for ref in ("pmid:123", "10.1000/linked"):
        await _include(rid, ref)

    overview = await call("study_overview", review_id=rid)
    assert overview["study_count"] == 2
    assert [g["reports"] for g in overview["groups"] if g["unlinked"]] == [["pmid:123"]]


@pytest.mark.asyncio
async def test_status_coverage_resolves_aliases() -> None:
    rid = await _review()
    await _include(rid, "pmid:123")
    await call("paper_alias_record", review_id=rid, alias="pmid:123", canonical_ref="10.1000/x")
    rec = await call(
        "evidence_record", review_id=rid, paper_ref="10.1000/x", outcome="mortality", study_design="RCT",
        quote="240 participants were analysed", source_text="In total 240 participants were analysed.",
    )
    assert rec["ok"] is True, rec
    rob = await call(
        "rob_record", review_id=rid, paper_ref="10.1000/x", tool="rob2", domain="randomisation_process",
        judgement="low", quote="randomised by a central computer", source_text="Patients were randomised by a central computer.",
    )
    assert rob["ok"] is True, rob

    status = await call("review_status", review_id=rid)
    assert not any("evidence_record" in s for s in status["next_steps"]), status["next_steps"]
    assert not any("not included at full_text" in w for w in status["warnings"]), status["warnings"]
    assert not any("Assess risk of bias" in s for s in status["next_steps"]), status["next_steps"]


@pytest.mark.asyncio
async def test_study_link_moves_every_stored_link_of_an_aliased_report() -> None:
    rid = await _review()
    await call("study_link", review_id=rid, study_id="S1", paper_refs=["10.1000/d"])
    await call("study_link", review_id=rid, study_id="S2", paper_refs=["pmid:9"])
    await call("paper_alias_record", review_id=rid, alias="pmid:9", canonical_ref="10.1000/d")

    refused = await call("study_link", review_id=rid, study_id="S3", paper_refs=["10.1000/d"])
    assert refused["ok"] is False and "S1" in refused["error"] and "S2" in refused["error"]

    moved = await call("study_link", review_id=rid, study_id="S3", paper_refs=["10.1000/d"], replace=True)
    assert moved["ok"] is True, moved
    assert {m["from_study"] for m in moved["moved"]} == {"S1", "S2"}
    again = await call("study_link", review_id=rid, study_id="S3", paper_refs=["pmid:9"])
    assert again["ok"] is True and again["unchanged"] == ["10.1000/d"], again
