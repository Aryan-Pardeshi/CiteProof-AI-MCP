# SPDX-License-Identifier: MIT
"""Cross-module checks: the export carries the new records, saves to a file, and history respects blinding."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tests.conftest import call


async def _review() -> str:
    return (await call("review_create", title="Integration", question="Does X reduce Y?"))["review_id"]


@pytest.mark.asyncio
async def test_export_saves_file_with_hash_and_new_sections(isolated_home) -> None:
    rid = await _review()
    await call("stats_recompute_2x2", events_a=10, total_a=100, events_b=20, total_b=100, review_id=rid, label="mortality")
    res = await call("export_bundle", review_id=rid, format="json", save=True)
    assert res["ok"] is True
    path = Path(res["saved_path"])
    assert path.parent == isolated_home / "exports"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == res["sha256"]
    bundle = json.loads(res["content"])
    assert bundle["stats_checks"][0]["label"] == "mortality"
    assert {"studies", "paper_aliases", "recall_check", "events", "server_version"} <= set(bundle)
    assert bundle["events"]["count"] >= 1

    md = await call("export_bundle", review_id=rid)
    assert md["saved_path"] is None
    for heading in ("## Reports and studies", "## Known-item recall", "## Statistical consistency checks", "## Change history"):
        assert heading in md["content"]


@pytest.mark.asyncio
async def test_review_history_hides_open_blind_screening() -> None:
    rid = await _review()
    await call("screening_stage_set", review_id=rid, stage="title_abstract", mode="blind", reviewers=["alice", "bob"])
    for reviewer in ("alice", "bob"):
        await call(
            "screening_record", review_id=rid, paper_ref="10.1000/a", stage="title_abstract",
            decision="exclude", reason=f"reason by {reviewer}", reviewer=reviewer,
        )
    hidden = await call("review_history", review_id=rid, table="screening")
    assert hidden["total"] == 0
    assert "reason by" not in json.dumps(hidden)

    assert (await call("screening_stage_close", review_id=rid, stage="title_abstract"))["ok"] is True
    shown = await call("review_history", review_id=rid, table="screening")
    assert shown["total"] == 2


@pytest.mark.asyncio
async def test_dedup_snapshot_is_not_logged_as_events() -> None:
    rid = await _review()
    await call("prisma_record_search", review_id=rid, source="PubMed", query="x", date_run="2026-01-01", hits=2)
    papers = [{"doi": "10.1000/a", "title": "A study"}, {"doi": "10.1000/a", "title": "A study"}]
    assert (await call("dedup_papers", papers=papers, review_id=rid))["recorded"] is True
    history = await call("review_history", review_id=rid)
    assert {e["table_name"] for e in history["events"]}.isdisjoint({"retrieved_records", "retrieval_runs"})
    bad = await call("review_history", review_id=rid, table="retrieved_records")
    assert bad["ok"] is False
