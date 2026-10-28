# SPDX-License-Identifier: MIT
"""Audit trail: append-only review events and history query.

Tables owned here: ``review_events``, ``review_events_meta``.

Events capture every insert, update, and delete across tracked review tables via
deterministic SQLite triggers. Existing review files are migrated on first open by
capturing a baseline event for each existing row; events start at the baseline, and
changes before the upgrade are not recoverable. Triggers prevent updates and
deletions on ``review_events``, making the table append-only inside SQLite.
"""

import hashlib
import json
import sqlite3
from typing import Annotated, Any

from pydantic import Field

from citeproof_mcp import store
from citeproof_mcp.compat import MCPServer
from citeproof_mcp.tools import (
    core,  # noqa: F401
    evidence_checks,  # noqa: F401
    extraction,  # noqa: F401
    prisma,  # noqa: F401
    protocol,  # noqa: F401
    source_tracking,  # noqa: F401
)
from citeproof_mcp.tools.common import (
    LIST_CAP,
    cap,
    fail,
    int_arg,
    paper_ref_arg,
    safe_tool,
    text_arg,
)

store.register_schema(
    (
        """CREATE TABLE IF NOT EXISTS review_events (
            seq INTEGER PRIMARY KEY AUTOINCREMENT,
            at TEXT NOT NULL,
            table_name TEXT NOT NULL,
            op TEXT NOT NULL CHECK (op IN ('baseline', 'insert', 'update', 'delete')),
            row_key TEXT NOT NULL,
            old_row TEXT,
            new_row TEXT
        )""",
        """CREATE INDEX IF NOT EXISTS idx_review_events_lookup
        ON review_events (table_name, row_key, seq)""",
        """CREATE TABLE IF NOT EXISTS review_events_meta (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            baseline_at TEXT NOT NULL
        )""",
        """CREATE TRIGGER IF NOT EXISTS trg_review_events_no_update
        BEFORE UPDATE ON review_events
        BEGIN
            SELECT RAISE(ABORT, 'review_events is append-only');
        END""",
        """CREATE TRIGGER IF NOT EXISTS trg_review_events_no_delete
        BEFORE DELETE ON review_events
        BEGIN
            SELECT RAISE(ABORT, 'review_events is append-only');
        END""",
    )
)

EXCLUDED_TABLES = {"meta", "review_events", "review_events_meta"}

ReviewId = Annotated[str, Field(description="The id returned by review_create.")]


def _quote_ident(ident: str) -> str:
    escaped = ident.replace('"', '""')
    return f'"{escaped}"'


def _quote_literal(val: str) -> str:
    escaped = val.replace("'", "''")
    return f"'{escaped}'"


def _tracked_tables(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    return [r[0] for r in rows if r[0] not in EXCLUDED_TABLES]


def _build_row_key_expr(pk_cols: list[str], target: str) -> str:
    if pk_cols:
        if len(pk_cols) == 1:
            return f"CAST({target}.{_quote_ident(pk_cols[0])} AS TEXT)"
        return " || '|' || ".join(f"CAST({target}.{_quote_ident(c)} AS TEXT)" for c in pk_cols)
    return f"CAST({target}.rowid AS TEXT)"


def _events_schema_hook(conn: sqlite3.Connection) -> None:
    tracked = _tracked_tables(conn)

    # 1. Baseline migration for unmigrated review files
    meta_row = conn.execute("SELECT id, baseline_at FROM review_events_meta WHERE id = 1").fetchone()
    if meta_row is None:
        baseline_at = conn.execute("SELECT strftime('%Y-%m-%dT%H:%M:%S+00:00', 'now')").fetchone()[0]
        for tbl in tracked:
            cols_info = conn.execute(f"PRAGMA table_info({_quote_ident(tbl)})").fetchall()
            col_names = [col["name"] for col in cols_info]
            pk_cols = [col["name"] for col in sorted([col for col in cols_info if col["pk"] > 0], key=lambda x: x["pk"])]
            order_by = ", ".join(_quote_ident(c) for c in pk_cols) if pk_cols else "rowid"
            select_cols = "rowid, *" if not pk_cols else "*"
            rows = conn.execute(f"SELECT {select_cols} FROM {_quote_ident(tbl)} ORDER BY {order_by}").fetchall()
            for r in rows:
                if pk_cols:
                    row_key = "|".join(str(r[c]) for c in pk_cols)
                else:
                    row_key = str(r["rowid"])
                row_dict = {c: r[c] for c in col_names}
                new_row_json = json.dumps(row_dict, separators=(",", ":"))
                conn.execute(
                    """INSERT INTO review_events (at, table_name, op, row_key, old_row, new_row)
                       VALUES (?, ?, 'baseline', ?, NULL, ?)""",
                    (baseline_at, tbl, row_key, new_row_json),
                )
        conn.execute("INSERT INTO review_events_meta (id, baseline_at) VALUES (1, ?)", (baseline_at,))

    # 2. Trigger creation / update for every tracked table
    for tbl in tracked:
        cols_info = conn.execute(f"PRAGMA table_info({_quote_ident(tbl)})").fetchall()
        col_names = [col["name"] for col in cols_info]
        pk_cols = [col["name"] for col in sorted([col for col in cols_info if col["pk"] > 0], key=lambda x: x["pk"])]
        col_hash = hashlib.sha256(",".join(col_names).encode("utf-8")).hexdigest()[:8]

        ins_name = f"ev_{tbl}_ins_{col_hash}"
        upd_name = f"ev_{tbl}_upd_{col_hash}"
        del_name = f"ev_{tbl}_del_{col_hash}"
        cur_triggers = {ins_name, upd_name, del_name}

        prefix = f"ev_{tbl}_"
        existing = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'trigger' AND tbl_name = ? AND name LIKE ?",
                (tbl, f"{prefix}%"),
            ).fetchall()
        ]

        for trg in existing:
            if trg not in cur_triggers:
                conn.execute(f"DROP TRIGGER IF EXISTS {_quote_ident(trg)}")

        new_json_args = ", ".join(f"{_quote_literal(c)}, NEW.{_quote_ident(c)}" for c in col_names)
        old_json_args = ", ".join(f"{_quote_literal(c)}, OLD.{_quote_ident(c)}" for c in col_names)
        new_row_key = _build_row_key_expr(pk_cols, "NEW")
        old_row_key = _build_row_key_expr(pk_cols, "OLD")

        ins_sql = f"""CREATE TRIGGER IF NOT EXISTS {_quote_ident(ins_name)}
AFTER INSERT ON {_quote_ident(tbl)}
BEGIN
    INSERT INTO review_events (at, table_name, op, row_key, old_row, new_row)
    VALUES (
        strftime('%Y-%m-%dT%H:%M:%S+00:00', 'now'),
        {_quote_literal(tbl)},
        'insert',
        {new_row_key},
        NULL,
        json_object({new_json_args})
    );
END"""

        upd_sql = f"""CREATE TRIGGER IF NOT EXISTS {_quote_ident(upd_name)}
AFTER UPDATE ON {_quote_ident(tbl)}
WHEN json_object({old_json_args}) IS NOT json_object({new_json_args})
BEGIN
    INSERT INTO review_events (at, table_name, op, row_key, old_row, new_row)
    VALUES (
        strftime('%Y-%m-%dT%H:%M:%S+00:00', 'now'),
        {_quote_literal(tbl)},
        'update',
        {new_row_key},
        json_object({old_json_args}),
        json_object({new_json_args})
    );
END"""

        del_sql = f"""CREATE TRIGGER IF NOT EXISTS {_quote_ident(del_name)}
AFTER DELETE ON {_quote_ident(tbl)}
BEGIN
    INSERT INTO review_events (at, table_name, op, row_key, old_row, new_row)
    VALUES (
        strftime('%Y-%m-%dT%H:%M:%S+00:00', 'now'),
        {_quote_literal(tbl)},
        'delete',
        {old_row_key},
        json_object({old_json_args}),
        NULL
    );
END"""

        for name, sql in [(ins_name, ins_sql), (upd_name, upd_sql), (del_name, del_sql)]:
            if name not in existing:
                conn.execute(sql)


store.register_schema_hook(_events_schema_hook)


# ---------------------------------------------------------------------------
# Read API
# ---------------------------------------------------------------------------


def event_rows(
    conn: sqlite3.Connection,
    table_name: str | None = None,
    row_key: str | None = None,
    limit: int = 200,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Return raw event rows from review_events in seq order, optionally filtered."""
    clauses: list[str] = []
    params: list[Any] = []
    if table_name is not None:
        clauses.append("table_name = ?")
        params.append(table_name)
    if row_key is not None:
        clauses.append("row_key = ?")
        params.append(row_key)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    query = (
        f"SELECT seq, at, table_name, op, row_key, old_row, new_row "
        f"FROM review_events {where} ORDER BY seq ASC LIMIT ? OFFSET ?"
    )
    rows = conn.execute(query, params + [limit, offset]).fetchall()
    return [dict(r) for r in rows]


def event_summary(conn: sqlite3.Connection) -> dict[str, Any]:
    """Summary of events for export bundles and status views."""
    count_row = conn.execute("SELECT COUNT(*), MAX(seq) FROM review_events").fetchone()
    count = int(count_row[0]) if count_row else 0
    last_seq = count_row[1] if count_row and count_row[1] is not None else None

    meta_row = conn.execute("SELECT baseline_at FROM review_events_meta WHERE id = 1").fetchone()
    baseline_at = meta_row[0] if meta_row else None

    table_rows = conn.execute(
        "SELECT table_name, COUNT(*) AS n FROM review_events GROUP BY table_name ORDER BY table_name"
    ).fetchall()
    by_table = {r["table_name"]: int(r["n"]) for r in table_rows}

    return {
        "count": count,
        "last_seq": last_seq,
        "baseline_at": baseline_at,
        "by_table": by_table,
    }


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@safe_tool
def review_history(
    review_id: ReviewId,
    table: Annotated[
        str | None,
        Field(description="Filter events to one tracked table name."),
    ] = None,
    paper_ref: Annotated[
        str | None,
        Field(description="Filter events to one paper reference (matches row_key == ref or starting with ref|)."),
    ] = None,
    row_key: Annotated[
        str | None,
        Field(description="Filter events to an exact row_key."),
    ] = None,
    limit: Annotated[
        int,
        Field(description="Maximum events to return (1 to 200). Default: 100."),
    ] = 100,
    offset: Annotated[
        int,
        Field(description="Number of events to skip for pagination. Default: 0."),
    ] = 0,
) -> dict[str, Any]:
    """Query the append-only event history for a review, oldest first.

    Reads recorded lifecycle events (baseline, insert, update, delete) captured
    by SQLite triggers across tracked tables. Filters by table name, paper_ref,
    or row_key.

    Returns: events (list of events with seq, at, table_name, op, row_key, old_row,
    new_row, changed_fields), total (matching events count), next_offset (offset for
    the next page, or null).

    Does NOT provide cryptographic tamper-proofing or hash chaining: records are
    append-only by convention and SQLite triggers within the review file; someone with
    direct file access can modify the database file directly.
    """
    page = int_arg(limit, "limit", min_value=1, max_value=LIST_CAP)
    skip = int_arg(offset, "offset", min_value=0)

    clean_table: str | None = None
    if table is not None:
        if not isinstance(table, str) or not table.strip():
            clean_table = ""
        else:
            clean_table = table.strip()

    clean_pref: str | None = None
    if paper_ref is not None:
        if isinstance(paper_ref, str) and not paper_ref.strip():
            clean_pref = None
        else:
            clean_pref = paper_ref_arg(paper_ref)

    clean_row_key: str | None = None
    if row_key is not None:
        clean_row_key = text_arg(row_key, "row_key", max_len=500)

    with store.review_db(review_id) as conn:
        tracked = _tracked_tables(conn)
        if clean_table is not None and clean_table not in tracked:
            fail(f"table must be one of: {', '.join(tracked)}; got '{clean_table}'")

        clauses: list[str] = []
        params: list[Any] = []
        if clean_table is not None:
            clauses.append("table_name = ?")
            params.append(clean_table)
        if clean_pref is not None:
            clauses.append("(row_key = ? OR substr(row_key, 1, ?) = ?)")
            params.extend([clean_pref, len(clean_pref) + 1, f"{clean_pref}|"])
        if clean_row_key is not None:
            clauses.append("row_key = ?")
            params.append(clean_row_key)

        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        total = int(conn.execute(f"SELECT COUNT(*) FROM review_events {where}", params).fetchone()[0])
        rows = conn.execute(
            f"SELECT seq, at, table_name, op, row_key, old_row, new_row FROM review_events {where} "
            "ORDER BY seq ASC LIMIT ? OFFSET ?",
            params + [page, skip],
        ).fetchall()

    events: list[dict[str, Any]] = []
    for r in rows:
        op = r["op"]
        raw_old = r["old_row"]
        raw_new = r["new_row"]
        old_row = json.loads(raw_old) if raw_old is not None else None
        new_row = json.loads(raw_new) if raw_new is not None else None
        changed_fields: list[str] | None = None
        if op == "update":
            if old_row is not None and new_row is not None:
                changed_fields = [k for k in new_row if old_row.get(k) != new_row.get(k)]
            else:
                changed_fields = []
        events.append(
            {
                "seq": r["seq"],
                "at": r["at"],
                "table_name": r["table_name"],
                "op": op,
                "row_key": r["row_key"],
                "old_row": old_row,
                "new_row": new_row,
                "changed_fields": changed_fields,
            }
        )

    events, _ = cap(events, limit=page)
    more = skip + len(events) < total
    return {
        "ok": True,
        "events": events,
        "total": total,
        "next_offset": skip + len(events) if more else None,
    }


def register(server: MCPServer) -> None:
    server.tool()(review_history)
