# SPDX-License-Identifier: MIT
"""Deterministic tests for reviewer type/model identity (#85) and blind screening (#86)."""

from __future__ import annotations

import json

import pytest

from tests.conftest import call


@pytest.mark.asyncio
async def test_reviewer_record_validation() -> None:
    """Test validation of reviewer_record: bad types, model_id constraints, primary rejection, lengths."""
    rev_res = await call("review_create", title="Reviewer Validation", question="Test")
    assert rev_res["ok"] is True
    rid = rev_res["review_id"]

    # 1. Bad reviewer_type
    bad_type = await call("reviewer_record", review_id=rid, reviewer="alice", reviewer_type="robot")
    assert bad_type["ok"] is False
    assert "reviewer_type must be one of" in bad_type["error"]

    # 2. model_id provided when reviewer_type is 'human'
    human_with_model = await call(
        "reviewer_record", review_id=rid, reviewer="dr_smith", reviewer_type="human", model_id="gpt-4o"
    )
    assert human_with_model["ok"] is False
    assert "model_id is only allowed when reviewer_type is 'model'" in human_with_model["error"]

    # 3. Reviewer 'primary' rejected
    primary_res = await call("reviewer_record", review_id=rid, reviewer="primary", reviewer_type="human")
    assert primary_res["ok"] is False
    assert "reserved for consensus" in primary_res["error"]

    # 4. Long model_id (> 200 chars)
    long_mid = await call(
        "reviewer_record", review_id=rid, reviewer="bot_1", reviewer_type="model", model_id="x" * 201
    )
    assert long_mid["ok"] is False
    assert "limit is 200" in long_mid["error"]

    # 5. Long run_ref (> 200 chars)
    long_run = await call(
        "reviewer_record", review_id=rid, reviewer="bot_1", reviewer_type="model", model_id="m1", run_ref="y" * 201
    )
    assert long_run["ok"] is False
    assert "limit is 200" in long_run["error"]

    # 6. Model without model_id: saves but returns warning
    bot_no_mid = await call("reviewer_record", review_id=rid, reviewer="bot_no_id", reviewer_type="model")
    assert bot_no_mid["ok"] is True
    assert bot_no_mid["reviewer_type"] == "model"
    assert bot_no_mid["model_id"] is None
    assert any("without a model_id" in w for w in bot_no_mid["warnings"])

    # 7. Valid human record
    human_ok = await call("reviewer_record", review_id=rid, reviewer="dr_smith", reviewer_type="human")
    assert human_ok["ok"] is True
    assert human_ok["reviewer_type"] == "human"
    assert human_ok["warnings"] == []

    # 8. Valid model record with model_id and run_ref
    model_ok = await call(
        "reviewer_record",
        review_id=rid,
        reviewer="claude_bot",
        reviewer_type="model",
        model_id="claude-3-5-sonnet",
        run_ref="temp=0.0",
    )
    assert model_ok["ok"] is True
    assert model_ok["reviewer_type"] == "model"
    assert model_ok["model_id"] == "claude-3-5-sonnet"
    assert model_ok["run_ref"] == "temp=0.0"
    assert model_ok["warnings"] == []


@pytest.mark.asyncio
async def test_old_file_without_metadata_reports_unknown() -> None:
    """Historical/unregistered reviewers must report reviewer_type as 'unknown' and never guess."""
    rev_res = await call("review_create", title="Legacy Review", question="Test")
    rid = rev_res["review_id"]

    # Record decisions directly without registering reviewers via reviewer_record
    await call(
        "screening_record",
        review_id=rid,
        paper_ref="10.1001/legacy1",
        stage="title_abstract",
        decision="include",
        reviewer="legacy_rev1",
    )
    await call(
        "screening_record",
        review_id=rid,
        paper_ref="10.1001/legacy1",
        stage="title_abstract",
        decision="include",
        reviewer="legacy_rev2",
    )

    # 1. screening_list reports unknown
    slist = await call("screening_list", review_id=rid, stage="title_abstract")
    assert slist["ok"] is True
    assert len(slist["records"]) == 2
    for r in slist["records"]:
        assert r["reviewer_type"] == "unknown"
        assert r["model_id"] is None
        assert r["run_ref"] is None

    # 2. screening_agreement reports unknown
    agr = await call(
        "screening_agreement",
        review_id=rid,
        stage="title_abstract",
        reviewer_a="legacy_rev1",
        reviewer_b="legacy_rev2",
    )
    assert agr["ok"] is True
    assert agr["reviewer_a_type"] == "unknown"
    assert agr["reviewer_b_type"] == "unknown"
    assert agr["reviewer_a_model_id"] is None
    assert agr["reviewer_b_model_id"] is None

    # 3. export_bundle json reports unknown
    bundle_res = await call("export_bundle", review_id=rid, format="json")
    assert bundle_res["ok"] is True
    bundle = json.loads(bundle_res["content"])
    for r in bundle["screening_decisions"]:
        assert r["reviewer_type"] == "unknown"


@pytest.mark.asyncio
async def test_screening_stage_set_validation() -> None:
    """Test validation of screening_stage_set: invalid stages, invalid modes, reviewer constraints."""
    rev_res = await call("review_create", title="Stage Config", question="Test")
    rid = rev_res["review_id"]

    # Bad stage
    bad_stage = await call("screening_stage_set", review_id=rid, stage="intro", mode="blind", reviewers=["a", "b"])
    assert bad_stage["ok"] is False

    # Bad mode
    bad_mode = await call(
        "screening_stage_set", review_id=rid, stage="title_abstract", mode="secret", reviewers=["a", "b"]
    )
    assert bad_mode["ok"] is False

    # Blind with < 2 reviewers
    single_rev = await call(
        "screening_stage_set", review_id=rid, stage="title_abstract", mode="blind", reviewers=["alice"]
    )
    assert single_rev["ok"] is False
    assert "at least 2 distinct reviewer names" in single_rev["error"]

    # Blind with duplicate reviewer names
    dup_rev = await call(
        "screening_stage_set", review_id=rid, stage="title_abstract", mode="blind", reviewers=["alice", "Alice "]
    )
    assert dup_rev["ok"] is False
    assert "at least 2 distinct reviewer names" in dup_rev["error"]

    # Blind with 'primary' in reviewers
    prim_rev = await call(
        "screening_stage_set", review_id=rid, stage="title_abstract", mode="blind", reviewers=["alice", "primary"]
    )
    assert prim_rev["ok"] is False
    assert "reviewer 'primary'" in prim_rev["error"]

    # Valid blind setup
    valid = await call(
        "screening_stage_set", review_id=rid, stage="title_abstract", mode="blind", reviewers=["alice", "bob"]
    )
    assert valid["ok"] is True
    assert valid["stage"] == "title_abstract"
    assert valid["mode"] == "blind"
    assert valid["reviewers"] == ["alice", "bob"]
    assert valid["closed_at"] is None


@pytest.mark.asyncio
async def test_blind_screening_flow_and_leak_prevention() -> None:
    """Test open blind mode: hiding peer decisions, rejecting consensus, closing check, unblinding."""
    rev_res = await call("review_create", title="Blinding Audit", question="Test")
    rid = rev_res["review_id"]

    # 1. Configure stage title_abstract as blind
    set_res = await call(
        "screening_stage_set", review_id=rid, stage="title_abstract", mode="blind", reviewers=["alice", "bob"]
    )
    assert set_res["ok"] is True

    # 2. Alice records decision for paper 1
    rec_a1 = await call(
        "screening_record",
        review_id=rid,
        paper_ref="10.1001/p1",
        stage="title_abstract",
        decision="include",
        reviewer="alice",
        title="Paper One Title",
    )
    assert rec_a1["ok"] is True

    # 3. Bob records decisions for paper 1 and paper 2 with secret reasons
    peer_reason_1 = "PeerReasonBobP1"
    peer_reason_2 = "PeerReasonBobP2"
    rec_b1 = await call(
        "screening_record",
        review_id=rid,
        paper_ref="10.1001/p1",
        stage="title_abstract",
        decision="exclude",
        reason=peer_reason_1,
        reviewer="bob",
        title="Paper One Title",
    )
    assert rec_b1["ok"] is True

    rec_b2 = await call(
        "screening_record",
        review_id=rid,
        paper_ref="10.1001/p2",
        stage="title_abstract",
        decision="exclude",
        reason=peer_reason_2,
        reviewer="bob",
        title="Paper Two Title",
    )
    assert rec_b2["ok"] is True

    # 4. Consensus under 'primary' is rejected while stage is open blind
    prim_rec = await call(
        "screening_record",
        review_id=rid,
        paper_ref="10.1001/p1",
        stage="title_abstract",
        decision="include",
        reviewer="primary",
    )
    assert prim_rec["ok"] is False
    assert "consensus is recorded after the stage is closed" in prim_rec["error"]

    prim_batch = await call(
        "screening_record_many",
        review_id=rid,
        decisions=[
            {"paper_ref": "10.1001/p1", "stage": "title_abstract", "decision": "include", "reviewer": "primary"}
        ],
    )
    assert prim_batch["ok"] is False
    assert "consensus is recorded after the stage is closed" in prim_batch["error"]

    # 5. screening_list hiding:
    # A) with as_reviewer="alice": only Alice's row is returned, Bob's rows are hidden and excluded from total
    list_alice = await call("screening_list", review_id=rid, stage="title_abstract", as_reviewer="alice")
    assert list_alice["ok"] is True
    assert list_alice["total"] == 1
    assert len(list_alice["records"]) == 1
    assert list_alice["records"][0]["paper_ref"] == "10.1001/p1"
    assert list_alice["records"][0]["reviewer"] == "alice"
    assert list_alice["hidden_blind_rows"] == 2
    assert "hidden" in list_alice["blind_note"].lower()
    assert peer_reason_1 not in json.dumps(list_alice["records"])

    # B) without as_reviewer: all open blind rows are hidden, total == 0
    list_anon = await call("screening_list", review_id=rid, stage="title_abstract")
    assert list_anon["ok"] is True
    assert list_anon["total"] == 0
    assert len(list_anon["records"]) == 0
    assert list_anon["hidden_blind_rows"] == 3

    # C) with reviewer="bob" and as_reviewer="alice": 0 rows returned
    list_bob_as_alice = await call(
        "screening_list", review_id=rid, stage="title_abstract", reviewer="bob", as_reviewer="alice"
    )
    assert list_bob_as_alice["ok"] is True
    assert list_bob_as_alice["total"] == 0
    assert len(list_bob_as_alice["records"]) == 0
    assert list_bob_as_alice["hidden_blind_rows"] == 2

    # 6. screening_agreement while open:
    agr_open = await call(
        "screening_agreement",
        review_id=rid,
        stage="title_abstract",
        reviewer_a="alice",
        reviewer_b="bob",
    )
    assert agr_open["ok"] is True
    assert agr_open["blinded"] is True
    assert "kappa" not in agr_open
    assert "table" not in agr_open
    assert "disagreements" not in agr_open
    assert "screening_stage_close" in agr_open["next_step"]

    # 7. review_status and export_bundle do not leak peer decisions or kappa
    status_res = await call("review_status", review_id=rid)
    assert status_res["ok"] is True
    stage_info = status_res["screening_stages"]["title_abstract"]
    assert stage_info["mode"] == "blind"
    assert stage_info["closed_at"] is None
    assert stage_info["decided_counts"] == {"alice": 1, "bob": 2}
    assert peer_reason_1 not in json.dumps(status_res)
    assert peer_reason_2 not in json.dumps(status_res)

    bundle_md = await call("export_bundle", review_id=rid, format="markdown")
    assert bundle_md["ok"] is True
    assert peer_reason_1 not in bundle_md["content"]
    assert peer_reason_2 not in bundle_md["content"]
    assert "10.1001/p2" not in bundle_md["content"]
    assert "kappa" not in bundle_md["content"].lower()

    bundle_js = await call("export_bundle", review_id=rid, format="json")
    assert bundle_js["ok"] is True
    assert peer_reason_1 not in bundle_js["content"]
    assert peer_reason_2 not in bundle_js["content"]
    assert "10.1001/p2" not in bundle_js["content"]
    js_data = json.loads(bundle_js["content"])
    assert len(js_data["screening_decisions"]) == 0
    assert len(js_data["agreement"]) == 0

    # 8. Closing stage:
    # A) Fails when expected reviewer Alice is missing decision for paper 2
    close_fail = await call("screening_stage_close", review_id=rid, stage="title_abstract")
    assert close_fail["ok"] is False
    assert close_fail["missing_count"] == 1
    assert close_fail["missing_pairs"] == [{"reviewer": "alice", "paper_ref": "10.1001/p2"}]

    # B) Alice records decision for paper 2
    await call(
        "screening_record",
        review_id=rid,
        paper_ref="10.1001/p2",
        stage="title_abstract",
        decision="include",
        reviewer="alice",
    )

    # C) Now screening_stage_close succeeds
    close_succ = await call("screening_stage_close", review_id=rid, stage="title_abstract")
    assert close_succ["ok"] is True
    assert close_succ["stage"] == "title_abstract"
    assert close_succ["closed_at"] is not None
    assert close_succ["papers_count"] == 2
    assert close_succ["decisions_count"] == 4
    assert close_succ["reviewer_counts"] == {"alice": 2, "bob": 2}

    # 9. After closing:
    # A) Changing already closed blind stage back to blind fails
    set_again = await call(
        "screening_stage_set", review_id=rid, stage="title_abstract", mode="blind", reviewers=["alice", "bob"]
    )
    assert set_again["ok"] is False
    assert "already closed" in set_again["error"]

    # B) Consensus decisions under 'primary' can now be recorded
    prim_succ = await call(
        "screening_record",
        review_id=rid,
        paper_ref="10.1001/p1",
        stage="title_abstract",
        decision="include",
        reviewer="primary",
    )
    assert prim_succ["ok"] is True

    # C) screening_list returns all decisions without as_reviewer
    list_closed = await call("screening_list", review_id=rid, stage="title_abstract")
    assert list_closed["ok"] is True
    assert list_closed["total"] >= 4
    assert list_closed["hidden_blind_rows"] == 0

    # D) screening_agreement is now unblinded and shows kappa and disagreements
    agr_closed = await call(
        "screening_agreement",
        review_id=rid,
        stage="title_abstract",
        reviewer_a="alice",
        reviewer_b="bob",
    )
    assert agr_closed["ok"] is True
    assert agr_closed["blinded"] is False
    assert "kappa" in agr_closed
    assert "table" in agr_closed
    assert agr_closed["shared"] == 2
    assert agr_closed["disagreement_count"] == 2

    # E) export_bundle now includes decisions and agreement
    bundle_closed_md = await call("export_bundle", review_id=rid, format="markdown")
    assert peer_reason_1 in bundle_closed_md["content"]
    assert "### Reviewer agreement" in bundle_closed_md["content"]


@pytest.mark.asyncio
async def test_human_caution_warning() -> None:
    """Test human-caution warning: present for AI-only and unknown types, absent once a human reviewer has decisions."""
    # 1. AI-only review with blind stage
    rev_ai = await call("review_create", title="AI Screening", question="Test")
    rid_ai = rev_ai["review_id"]

    await call("reviewer_record", review_id=rid_ai, reviewer="agent1", reviewer_type="model", model_id="gpt-4o")
    await call("reviewer_record", review_id=rid_ai, reviewer="agent2", reviewer_type="model", model_id="claude-3-5-sonnet")
    await call(
        "screening_stage_set", review_id=rid_ai, stage="title_abstract", mode="blind", reviewers=["agent1", "agent2"]
    )

    await call(
        "screening_record",
        review_id=rid_ai,
        paper_ref="10.1001/ai1",
        stage="title_abstract",
        decision="include",
        reviewer="agent1",
    )
    await call(
        "screening_record",
        review_id=rid_ai,
        paper_ref="10.1001/ai1",
        stage="title_abstract",
        decision="exclude",
        reason="Wrong domain",
        reviewer="agent2",
    )

    # In blind screening agreement:
    blind_agr = await call(
        "screening_agreement", review_id=rid_ai, stage="title_abstract", reviewer_a="agent1", reviewer_b="agent2"
    )
    assert blind_agr["ok"] is True
    expected_blind_warning = (
        "Blind screening was done by AI reviewers only. A human reviewer has not been recorded for these papers. "
        "Human screening and sign-off are still required."
    )
    assert expected_blind_warning in blind_agr["warnings"]

    # In review_status:
    expected_status_warning = (
        "Screening was done by AI reviewers only, or reviewer types are not recorded. A human reviewer has not "
        "been recorded for these papers. Human screening and sign-off are still required."
    )
    st = await call("review_status", review_id=rid_ai)
    assert expected_status_warning in st["warnings"]

    # In export_bundle markdown:
    bundle_md = await call("export_bundle", review_id=rid_ai, format="markdown")
    assert expected_status_warning in bundle_md["content"]

    # 2. Add a human reviewer and record a decision
    await call("reviewer_record", review_id=rid_ai, reviewer="dr_human", reviewer_type="human")
    await call(
        "screening_record",
        review_id=rid_ai,
        paper_ref="10.1001/ai1",
        stage="title_abstract",
        decision="include",
        reviewer="dr_human",
    )

    # Now the human-caution warning must be absent!
    st_cleared = await call("review_status", review_id=rid_ai)
    assert not any("Human screening and sign-off are still required" in w for w in st_cleared["warnings"])

    agr_cleared = await call(
        "screening_agreement", review_id=rid_ai, stage="title_abstract", reviewer_a="agent1", reviewer_b="dr_human"
    )
    assert not any("Human screening and sign-off are still required" in w for w in agr_cleared["warnings"])

    # 3. Unknown reviewer types also trigger the warning
    rev_unk = await call("review_create", title="Unknown Types", question="Test")
    rid_unk = rev_unk["review_id"]
    await call(
        "screening_record",
        review_id=rid_unk,
        paper_ref="10.1001/unk1",
        stage="title_abstract",
        decision="include",
        reviewer="rev_unknown",
    )
    st_unk = await call("review_status", review_id=rid_unk)
    assert expected_status_warning in st_unk["warnings"]


@pytest.mark.asyncio
async def test_same_model_warning() -> None:
    """Test same-model warning: warned when two reviewers share a model identity (case-insensitive, trimmed)."""
    rev_res = await call("review_create", title="Same Model Test", question="Test")
    rid = rev_res["review_id"]

    # Register two reviewers with the same model ID (case variation and leading/trailing whitespace)
    await call(
        "reviewer_record",
        review_id=rid,
        reviewer="bot_alpha",
        reviewer_type="model",
        model_id="Claude-3-5-Sonnet-20241022",
    )
    await call(
        "reviewer_record",
        review_id=rid,
        reviewer="bot_beta",
        reviewer_type="model",
        model_id="  claude-3-5-sonnet-20241022  ",
    )

    await call(
        "screening_record",
        review_id=rid,
        paper_ref="10.1001/m1",
        stage="title_abstract",
        decision="include",
        reviewer="bot_alpha",
    )
    await call(
        "screening_record",
        review_id=rid,
        paper_ref="10.1001/m1",
        stage="title_abstract",
        decision="include",
        reviewer="bot_beta",
    )

    # 1. In screening_agreement
    agr = await call(
        "screening_agreement",
        review_id=rid,
        stage="title_abstract",
        reviewer_a="bot_alpha",
        reviewer_b="bot_beta",
    )
    assert agr["ok"] is True
    expected_same_model = (
        "Reviewers bot_alpha and bot_beta are runs of the same model (Claude-3-5-Sonnet-20241022); "
        "their agreement is not independent validation."
    )
    assert any("are runs of the same model" in w and "not independent validation" in w for w in agr["warnings"])
    assert expected_same_model in agr["warnings"]

    # 2. In review_status
    st = await call("review_status", review_id=rid)
    assert expected_same_model in st["warnings"]

    # 3. In export_bundle markdown and json
    bundle_md = await call("export_bundle", review_id=rid, format="markdown")
    assert expected_same_model in bundle_md["content"]

    bundle_js = await call("export_bundle", review_id=rid, format="json")
    bundle_data = json.loads(bundle_js["content"])
    assert expected_same_model in bundle_data["status"]["warnings"]


@pytest.mark.asyncio
async def test_unblinded_stages_behave_exactly_as_before() -> None:
    """An unblinded stage returns all peer decisions, computes kappa immediately, and allows consensus."""
    rev_res = await call("review_create", title="Unblinded Flow", question="Test")
    rid = rev_res["review_id"]

    await call(
        "screening_stage_set", review_id=rid, stage="title_abstract", mode="unblinded", reviewers=["rev1", "rev2"]
    )

    await call(
        "screening_record",
        review_id=rid,
        paper_ref="10.1001/u1",
        stage="title_abstract",
        decision="include",
        reviewer="rev1",
    )
    await call(
        "screening_record",
        review_id=rid,
        paper_ref="10.1001/u1",
        stage="title_abstract",
        decision="exclude",
        reason="Wrong population",
        reviewer="rev2",
    )

    # List without as_reviewer immediately returns both decisions
    slist = await call("screening_list", review_id=rid, stage="title_abstract")
    assert slist["ok"] is True
    assert slist["total"] == 2
    assert slist["hidden_blind_rows"] == 0

    # Agreement computed immediately without needing to close
    agr = await call(
        "screening_agreement", review_id=rid, stage="title_abstract", reviewer_a="rev1", reviewer_b="rev2"
    )
    assert agr["ok"] is True
    assert agr["blinded"] is False
    assert agr["shared"] == 1
    assert agr["agree"] == 0
    assert agr["disagreement_count"] == 1

    # Consensus can be recorded immediately under primary
    prim = await call(
        "screening_record",
        review_id=rid,
        paper_ref="10.1001/u1",
        stage="title_abstract",
        decision="include",
        reviewer="primary",
    )
    assert prim["ok"] is True


@pytest.mark.asyncio
async def test_open_blind_stage_cannot_leak_through_counts_or_mode_switch() -> None:
    rid = (await call("review_create", title="Blind leaks", question="Test"))["review_id"]
    await call("screening_stage_set", review_id=rid, stage="title_abstract", mode="blind", reviewers=["alice", "bob"])
    await call(
        "screening_record", review_id=rid, paper_ref="10.1000/a", stage="title_abstract", decision="exclude",
        reason="wrong population", reviewer="alice",
    )
    for tool in ("prisma_status", "export_bundle"):
        res = await call(tool, review_id=rid, reviewer="alice")
        text = json.dumps(res)
        assert "wrong population" not in text
        assert res["ok"] is False or "hidden while stage" in text
    switched = await call("screening_stage_set", review_id=rid, stage="title_abstract", mode="unblinded", reviewers=["alice", "bob"])
    assert switched["ok"] is False and "screening_stage_close" in switched["error"]
