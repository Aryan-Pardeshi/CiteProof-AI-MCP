# SPDX-License-Identifier: MIT
"""Tests for review event audit trail, triggers, baseline migration, and review_history tool."""

import json
import sqlite3

import pytest
from citeproof_mcp import store
from citeproof_mcp.tools import events

from tests.conftest import call


@pytest.mark.asyncio
async def test_screening_decision_insert_and_update():
    """A screening decision recorded and then changed produces insert then update events.
    The previous decision is visible in old_row."""
    rev = await call("review_create", title="Events Screening Review", question="Question?")
    review_id = rev["review_id"]
    paper = "10.1234/ev.test.paper"

    # 1. First record: insert event
    r1 = await call(
        "screening_record",
        review_id=review_id,
        paper_ref=paper,
        stage="title_abstract",
        decision="include",
        reviewer="primary",
        title="Sample Paper",
    )
    assert r1["ok"]

    h1 = await call("review_history", review_id=review_id, table="screening")
    assert h1["ok"]
    assert h1["total"] == 1
    ev1 = h1["events"][0]
    assert ev1["seq"] == 1
    assert ev1["op"] == "insert"
    assert ev1["table_name"] == "screening"
    assert ev1["row_key"] == f"{paper}|title_abstract|primary"
    assert ev1["old_row"] is None
    assert ev1["new_row"]["decision"] == "include"
    assert ev1["changed_fields"] is None

    # 2. Changed record: update event
    r2 = await call(
        "screening_record",
        review_id=review_id,
        paper_ref=paper,
        stage="title_abstract",
        decision="exclude",
        reason="wrong population",
        reviewer="primary",
        title="Sample Paper",
    )
    assert r2["ok"]

    h2 = await call("review_history", review_id=review_id, table="screening")
    assert h2["ok"]
    assert h2["total"] == 2
    ev2 = h2["events"][1]
    assert ev2["seq"] == 2
    assert ev2["op"] == "update"
    assert ev2["table_name"] == "screening"
    assert ev2["row_key"] == f"{paper}|title_abstract|primary"
    assert ev2["old_row"]["decision"] == "include"
    assert ev2["old_row"]["reason"] is None
    assert ev2["new_row"]["decision"] == "exclude"
    assert ev2["new_row"]["reason"] == "wrong population"
    assert {"decision", "reason"}.issubset(set(ev2["changed_fields"]))
    assert set(ev2["changed_fields"]) <= {"decision", "reason", "updated_at"}


@pytest.mark.asyncio
async def test_deterministic_seq_order():
    """Events appear in strictly deterministic seq order (1, 2, 3...) oldest first."""
    rev = await call("review_create", title="Deterministic Order Review", question="Question?")
    review_id = rev["review_id"]

    await call("protocol_record", review_id=review_id, population="Adults with asthma")
    await call("protocol_record", review_id=review_id, intervention="Inhaled corticosteroids")
    await call("screening_record", review_id=review_id, paper_ref="10.1001/a", stage="title_abstract", decision="include")
    await call("screening_record", review_id=review_id, paper_ref="10.1001/b", stage="title_abstract", decision="exclude", reason="wrong pop")

    hist = await call("review_history", review_id=review_id)
    assert hist["ok"]
    seqs = [e["seq"] for e in hist["events"]]
    assert len(seqs) >= 4
    assert seqs == sorted(seqs)
    assert seqs == list(range(1, len(seqs) + 1))


@pytest.mark.asyncio
async def test_no_event_for_noop_update():
    """An update that leaves all row columns unchanged produces no event."""
    rev = await call("review_create", title="Noop Review", question="Question?")
    review_id = rev["review_id"]

    await call("protocol_record", review_id=review_id, population="Healthy adults")
    h1 = await call("review_history", review_id=review_id, table="protocol")
    assert h1["total"] == 1

    # Execute a raw UPDATE that changes nothing (identical values including updated_at)
    with store.review_db(review_id) as conn:
        row = conn.execute("SELECT * FROM protocol WHERE field = 'population'").fetchone()
        val = row["value"]
        upd = row["updated_at"]

    with store.review_db(review_id, write=True) as conn:
        conn.execute(
            "UPDATE protocol SET value = ?, updated_at = ? WHERE field = 'population'",
            (val, upd),
        )

    h2 = await call("review_history", review_id=review_id, table="protocol")
    assert h2["total"] == 1
    assert len(h2["events"]) == 1


@pytest.mark.asyncio
async def test_delete_event():
    """Deleting a row emits a delete event with old_row populated and new_row null."""
    rev = await call("review_create", title="Delete Review", question="Question?")
    review_id = rev["review_id"]
    paper = "10.1234/del.paper"

    await call("screening_record", review_id=review_id, paper_ref=paper, stage="title_abstract", decision="include")

    # Delete via store connection
    with store.review_db(review_id, write=True) as conn:
        conn.execute("DELETE FROM screening WHERE paper_ref = ?", (paper,))

    hist = await call("review_history", review_id=review_id, table="screening")
    assert hist["ok"]
    assert hist["total"] == 2
    del_ev = hist["events"][1]
    assert del_ev["op"] == "delete"
    assert del_ev["old_row"]["decision"] == "include"
    assert del_ev["new_row"] is None
    assert del_ev["changed_fields"] is None


@pytest.mark.asyncio
async def test_evidence_record_rerecord_creates_update_event():
    """Re-recording an evidence row produces an update event."""
    rev = await call("review_create", title="Evidence Update Review", question="Question?")
    review_id = rev["review_id"]
    paper = "10.1234/ev.update.paper"
    source = "Mortality was 12% in 200 participants initially. Later analysis found mortality was 15% in 250 participants."

    r1 = await call(
        "evidence_record",
        review_id=review_id,
        paper_ref=paper,
        outcome="mortality",
        study_design="RCT",
        sample_size=200,
        quote="mortality was 12% in 200 participants",
        source_text=source,
    )
    assert r1["ok"]

    h1 = await call("review_history", review_id=review_id, table="evidence_rows")
    assert h1["total"] == 1
    assert h1["events"][0]["op"] == "insert"

    # Re-record with updated sample size and quote
    r2 = await call(
        "evidence_record",
        review_id=review_id,
        paper_ref=paper,
        outcome="mortality",
        study_design="RCT",
        sample_size=250,
        quote="mortality was 15% in 250 participants",
        source_text=source,
    )
    assert r2["ok"]

    h2 = await call("review_history", review_id=review_id, table="evidence_rows")
    assert h2["total"] == 2
    ev2 = h2["events"][1]
    assert ev2["op"] == "update"
    assert ev2["old_row"]["sample_size"] == 200
    assert ev2["new_row"]["sample_size"] == 250
    assert "sample_size" in ev2["changed_fields"]


@pytest.mark.asyncio
async def test_review_history_filters_and_invalid_table():
    """review_history filters by table, paper_ref, row_key, and paging; rejects invalid table."""
    rev = await call("review_create", title="Filter Review", question="Question?")
    review_id = rev["review_id"]

    await call("screening_record", review_id=review_id, paper_ref="10.1001/paper.one", stage="title_abstract", decision="include")
    await call("screening_record", review_id=review_id, paper_ref="10.1001/paper.two", stage="title_abstract", decision="exclude", reason="wrong pop")
    await call("protocol_record", review_id=review_id, population="Children")

    # 1. Filter by table
    h_scr = await call("review_history", review_id=review_id, table="screening")
    assert h_scr["ok"]
    assert all(e["table_name"] == "screening" for e in h_scr["events"])
    assert h_scr["total"] == 2

    h_proto = await call("review_history", review_id=review_id, table="protocol")
    assert h_proto["ok"]
    assert all(e["table_name"] == "protocol" for e in h_proto["events"])
    assert h_proto["total"] == 1

    # 2. Filter by paper_ref (both exact and prefix with canonicalisation)
    h_p1 = await call("review_history", review_id=review_id, paper_ref="10.1001/paper.one")
    assert h_p1["ok"]
    assert h_p1["total"] == 1
    assert h_p1["events"][0]["row_key"].startswith("10.1001/paper.one|")

    # Canonicalised doi: prefix
    h_p1_doi = await call("review_history", review_id=review_id, paper_ref="doi:10.1001/PAPER.ONE")
    assert h_p1_doi["ok"]
    assert h_p1_doi["total"] == 1

    # 3. Filter by exact row_key
    h_rk = await call("review_history", review_id=review_id, row_key="10.1001/paper.two|title_abstract|primary")
    assert h_rk["ok"]
    assert h_rk["total"] == 1
    assert h_rk["events"][0]["new_row"]["decision"] == "exclude"

    # 4. Pagination
    h_page1 = await call("review_history", review_id=review_id, limit=2, offset=0)
    assert h_page1["ok"]
    assert len(h_page1["events"]) == 2
    assert h_page1["next_offset"] == 2
    assert h_page1["total"] >= 3

    h_page2 = await call("review_history", review_id=review_id, limit=2, offset=2)
    assert h_page2["ok"]
    assert len(h_page2["events"]) >= 1

    # 5. Invalid table name
    h_bad = await call("review_history", review_id=review_id, table="nonexistent_table")
    assert h_bad["ok"] is False
    assert "table must be one of:" in h_bad["error"]
    assert "screening" in h_bad["error"]


@pytest.mark.asyncio
async def test_current_tables_equal_latest_new_row():
    """Current database tables match the new_row of the latest event per row_key."""
    rev = await call("review_create", title="Latest State Equivalence Review", question="Question?")
    review_id = rev["review_id"]

    await call("protocol_record", review_id=review_id, population="Initial pop")
    await call("protocol_record", review_id=review_id, population="Updated pop")
    await call("screening_record", review_id=review_id, paper_ref="10.1111/p1", stage="title_abstract", decision="include")
    await call("screening_record", review_id=review_id, paper_ref="10.1111/p2", stage="title_abstract", decision="exclude", reason="r1")
    await call("screening_record", review_id=review_id, paper_ref="10.1111/p2", stage="title_abstract", decision="include")

    with store.review_db(review_id) as conn:
        events_rows = conn.execute(
            "SELECT table_name, row_key, op, new_row FROM review_events ORDER BY seq ASC"
        ).fetchall()

        latest_by_key: dict[tuple[str, str], dict] = {}
        for r in events_rows:
            key = (r["table_name"], r["row_key"])
            latest_by_key[key] = {
                "op": r["op"],
                "new_row": json.loads(r["new_row"]) if r["new_row"] else None,
            }

        for (table_name, row_key), info in latest_by_key.items():
            if info["op"] == "delete":
                continue
            cols_info = conn.execute(f'PRAGMA table_info("{table_name}")').fetchall()
            pk_cols = [c["name"] for c in sorted([col for col in cols_info if col["pk"] > 0], key=lambda x: x["pk"])]
            col_names = [c["name"] for c in cols_info]

            if pk_cols:
                pk_vals = row_key.split("|")
                where = " AND ".join(f'"{c}" = ?' for c in pk_cols)
                row = conn.execute(f'SELECT * FROM "{table_name}" WHERE {where}', pk_vals).fetchone()
            else:
                row = conn.execute(f'SELECT * FROM "{table_name}" WHERE rowid = ?', (row_key,)).fetchone()

            assert row is not None, f"Row {row_key} in {table_name} should exist"
            curr_dict = {c: row[c] for c in col_names}
            assert curr_dict == info["new_row"]


@pytest.mark.asyncio
async def test_append_only_triggers_block_update_and_delete(isolated_home):
    """The BEFORE UPDATE and BEFORE DELETE triggers block manual mutation of review_events."""
    rev = await call("review_create", title="Tamper Block Review", question="Question?")
    review_id = rev["review_id"]

    await call("protocol_record", review_id=review_id, population="Elderly")

    db_path = isolated_home / "reviews" / f"{review_id}.db"
    raw_conn = sqlite3.connect(str(db_path))
    try:
        raw_conn.execute("PRAGMA foreign_keys = ON")
        with pytest.raises(sqlite3.IntegrityError, match="review_events is append-only"):
            raw_conn.execute("UPDATE review_events SET at = '2099-01-01T00:00:00+00:00' WHERE seq = 1")

        with pytest.raises(sqlite3.IntegrityError, match="review_events is append-only"):
            raw_conn.execute("DELETE FROM review_events WHERE seq = 1")
    finally:
        raw_conn.close()


@pytest.mark.asyncio
async def test_migration_baseline_events_appear_once_across_opens(isolated_home):
    """An existing file with rows before events were tracked gets baseline events on first open,
    and baseline events appear exactly once across multiple opens."""
    # Create a fresh review file
    review_id = store.create_review(title="Legacy Review", question="Migrate?")
    db_path = isolated_home / "reviews" / f"{review_id}.db"

    # Simulate a legacy pre-events database: create tables and insert data using raw sqlite
    raw_conn = sqlite3.connect(str(db_path))
    try:
        raw_conn.execute("""CREATE TABLE IF NOT EXISTS screening (
            paper_ref TEXT NOT NULL,
            stage TEXT NOT NULL,
            reviewer TEXT NOT NULL,
            decision TEXT NOT NULL,
            reason TEXT,
            title TEXT,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (paper_ref, stage, reviewer)
        )""")
        raw_conn.execute("""CREATE TABLE IF NOT EXISTS protocol (
            field TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )""")
        raw_conn.execute(
            "INSERT INTO screening VALUES ('10.1234/legacy.1', 'title_abstract', 'primary', 'include', NULL, 'Title 1', '2025-01-01T00:00:00+00:00')"
        )
        raw_conn.execute(
            "INSERT INTO screening VALUES ('10.1234/legacy.2', 'title_abstract', 'primary', 'exclude', 'wrong outcome', 'Title 2', '2025-01-01T00:00:00+00:00')"
        )
        raw_conn.execute(
            "INSERT INTO protocol VALUES ('population', 'Legacy population', '2025-01-01T00:00:00+00:00')"
        )
        raw_conn.commit()
    finally:
        raw_conn.close()

    # Clear _APPLIED to simulate fresh open
    store._APPLIED.clear()

    # First open: triggers baseline migration
    hist1 = await call("review_history", review_id=review_id)
    assert hist1["ok"]
    assert hist1["total"] == 3
    for ev in hist1["events"]:
        assert ev["op"] == "baseline"
        assert ev["old_row"] is None
        assert ev["new_row"] is not None

    # Clear _APPLIED and open a second time: baseline events must NOT duplicate
    store._APPLIED.clear()
    hist2 = await call("review_history", review_id=review_id)
    assert hist2["ok"]
    assert hist2["total"] == 3

    # Direct check on review_events_meta
    with store.review_db(review_id) as conn:
        meta_rows = conn.execute("SELECT * FROM review_events_meta").fetchall()
        assert len(meta_rows) == 1
        summary = events.event_summary(conn)
        assert summary["count"] == 3
        assert summary["last_seq"] == 3
        assert summary["baseline_at"] is not None
        assert summary["by_table"] == {"protocol": 1, "screening": 2}


@pytest.mark.asyncio
async def test_hook_idempotency_does_not_duplicate_triggers(isolated_home):
    """Opening a review twice does not duplicate triggers in sqlite_master."""
    rev = await call("review_create", title="Idempotent Hook Review", question="Question?")
    review_id = rev["review_id"]

    # Open review once so schema and triggers are created
    with store.review_db(review_id) as conn:
        triggers_1 = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'trigger' ORDER BY name"
            ).fetchall()
        ]

    assert len(triggers_1) > 0

    # Clear _APPLIED and open again
    store._APPLIED.clear()
    with store.review_db(review_id) as conn:
        triggers_2 = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'trigger' ORDER BY name"
            ).fetchall()
        ]

    assert triggers_1 == triggers_2


@pytest.mark.asyncio
async def test_read_api_functions():
    """event_rows and event_summary return expected structures."""
    rev = await call("review_create", title="Read API Review", question="Question?")
    review_id = rev["review_id"]

    await call("protocol_record", review_id=review_id, population="Adults")
    await call("protocol_record", review_id=review_id, intervention="Drug A")

    with store.review_db(review_id) as conn:
        summary = events.event_summary(conn)
        # Note: each protocol_record writes to both 'protocol' and 'protocol_changes'
        assert summary["count"] == 4
        assert summary["last_seq"] == 4
        assert summary["baseline_at"] is not None
        assert summary["by_table"]["protocol"] == 2
        assert summary["by_table"]["protocol_changes"] == 2

        all_rows = events.event_rows(conn)
        assert len(all_rows) == 4
        assert all_rows[0]["table_name"] == "protocol"
        assert all_rows[0]["seq"] == 1

        filtered = events.event_rows(conn, table_name="protocol", row_key="intervention")
        assert len(filtered) == 1
        assert filtered[0]["row_key"] == "intervention"
