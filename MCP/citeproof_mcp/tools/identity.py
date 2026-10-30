# SPDX-License-Identifier: MIT
"""Paper identity: canonical aliases from dedup clusters, study grouping and known-item recall.

Tables owned here: ``paper_aliases``, ``alias_conflicts``, ``retrieved_records``,
``retrieval_runs``, ``study_reports``, ``recall_seeds`` and ``recall_checks``. Other
modules read these tables only through the public functions in the "Read API" section.
"""

import json
import re
import sqlite3
from typing import Annotated, Any

from pydantic import Field

from citeproof_mcp import store
from citeproof_mcp.compat import MCPServer
from citeproof_mcp.tools import extraction, prisma
from citeproof_mcp.tools.common import (
    cap,
    fail,
    list_arg,
    paper_ref_arg,
    reviewer_arg,
    safe_tool,
    text_arg,
)

store.register_schema(
    (
        """CREATE TABLE IF NOT EXISTS paper_aliases (
            alias TEXT PRIMARY KEY,
            canonical_ref TEXT NOT NULL,
            kind TEXT NOT NULL,
            origin TEXT NOT NULL,
            created_at TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS alias_conflicts (
            alias TEXT NOT NULL,
            existing_ref TEXT NOT NULL,
            proposed_ref TEXT NOT NULL,
            origin TEXT NOT NULL,
            seen_at TEXT NOT NULL,
            resolved_at TEXT,
            PRIMARY KEY (alias, proposed_ref)
        )""",
        """CREATE TABLE IF NOT EXISTS retrieved_records (
            idx INTEGER PRIMARY KEY,
            source TEXT,
            record_id TEXT,
            doi TEXT,
            pmid TEXT,
            title TEXT,
            year TEXT,
            paper_ref TEXT,
            canonical_ref TEXT
        )""",
        """CREATE TABLE IF NOT EXISTS retrieval_runs (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            records INTEGER NOT NULL,
            recorded_at TEXT NOT NULL,
            dedup TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS study_reports (
            paper_ref TEXT PRIMARY KEY,
            study_id TEXT NOT NULL,
            note TEXT,
            linked_at TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS recall_seeds (
            paper_ref TEXT PRIMARY KEY,
            label TEXT,
            added_at TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS recall_checks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            checked_at TEXT NOT NULL,
            result TEXT NOT NULL
        )""",
    )
)

STUDY_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")
MAX_REFS = 200
ORIGIN_DEDUP = "dedup"
ORIGIN_MANUAL = "manual"
_YEAR_RE = re.compile(r"(?:1[89]|20|21)\d{2}")

ReviewId = Annotated[str, Field(description="The id returned by review_create.")]
PaperRefs = Annotated[
    list[str],
    Field(description="1 to 200 paper keys: a DOI ('10.1234/abc'), 'pmid:<digits>' or 'title:<title>|<year>'."),
]
PaperRef = Annotated[
    str,
    Field(description="Paper key: a DOI ('10.1234/abc'), 'pmid:<digits>' or 'title:<title>|<year>'."),
]


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _kind(alias: str) -> str:
    if alias.startswith("pmid:"):
        return "pmid"
    if alias.startswith("title:"):
        return "title"
    return "doi"


def _year_int(value: Any) -> int | None:
    """A four-digit year from an int or text; None when absent."""
    if value is None or isinstance(value, bool):
        return None
    match = _YEAR_RE.search(str(value))
    return int(match.group(0)) if match else None


def _year_text(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def _pmid_text(value: Any) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip()
    if text.lower().startswith("pmid:"):
        text = text[5:].strip()
    return str(int(text)) if text.isdigit() else None


def _text(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()


def _record_paper_ref(doi: str | None, pmid: str | None, title: str | None, year: Any) -> str | None:
    try:
        return store.normalize_paper_ref(doi=doi, pmid=pmid, title=title, year=_year_int(year))
    except store.StoreError:
        return None


def _title_ref(paper: dict[str, Any]) -> str | None:
    """Title ref for a record with a known year; None when the year is missing."""
    title = _text(paper.get("title"))
    year = _year_int(paper.get("year"))
    if title is None or year is None:
        return None
    return _record_paper_ref(None, None, title, year)


def _dedup_record_ref(paper: dict[str, Any]) -> tuple[str | None, str | None, str | None, str | None]:
    """(paper_ref, doi, pmid, title) for one exported record."""
    doi = store.normalize_doi(paper.get("doi")) if paper.get("doi") else None
    pmid = _pmid_text(paper.get("pmid"))
    title = _text(paper.get("title"))
    return _record_paper_ref(doi, pmid, title, paper.get("year")), doi, pmid, title


def _cluster_canonical(
    papers: list[dict[str, Any]], members: list[int], kept: int
) -> tuple[str | None, set[str]]:
    """(canonical ref, extra DOIs) for one cluster or singleton record."""
    ordered = [kept] + [i for i in members if i != kept]
    dois: list[str] = []
    for i in ordered:
        doi = store.normalize_doi(papers[i].get("doi")) if papers[i].get("doi") else None
        if doi and doi not in dois:
            dois.append(doi)
    if dois:
        return dois[0], set(dois[1:])
    pmids: list[str] = []
    for i in ordered:
        pmid = _pmid_text(papers[i].get("pmid"))
        if pmid and pmid not in pmids:
            pmids.append(pmid)
    if pmids:
        return f"pmid:{pmids[0]}", set()
    return _record_paper_ref(None, None, papers[kept].get("title"), papers[kept].get("year")), set()


def _record_conflict(
    conn: sqlite3.Connection,
    alias: str,
    existing_ref: str,
    proposed_ref: str,
    origin: str,
    now: str,
    seen: set[tuple[str, str]],
) -> None:
    conn.execute(
        """INSERT INTO alias_conflicts (alias, existing_ref, proposed_ref, origin, seen_at)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(alias, proposed_ref) DO NOTHING""",
        (alias, existing_ref, proposed_ref, origin, now),
    )
    seen.add((alias, proposed_ref))


def _register_alias(
    conn: sqlite3.Connection,
    alias: str,
    canonical_ref: str,
    origin: str,
    now: str,
    seen: set[tuple[str, str]],
) -> int:
    """Map alias to canonical_ref. Returns 1 when a new mapping was inserted.

    An existing mapping is never overwritten; a disagreeing proposal becomes a
    conflict row instead, so two records are never silently merged.
    """
    row = conn.execute("SELECT canonical_ref FROM paper_aliases WHERE alias = ?", (alias,)).fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO paper_aliases (alias, canonical_ref, kind, origin, created_at) VALUES (?, ?, ?, ?, ?)",
            (alias, canonical_ref, _kind(alias), origin, now),
        )
        return 1
    if row[0] != canonical_ref:
        _record_conflict(conn, alias, row[0], canonical_ref, origin, now, seen)
    return 0


# ---------------------------------------------------------------------------
# Read API
# ---------------------------------------------------------------------------


def resolve_ref(conn: sqlite3.Connection, paper_ref: str) -> str:
    """Canonical ref for ``paper_ref``, or the ref itself when no alias is recorded.

    Alias chains are followed (at most ten hops) so a manual mapping that points at
    an already-aliased ref cannot loop forever.
    """
    ref = store.canonical_paper_ref(paper_ref)
    seen: set[str] = set()
    for _ in range(10):
        row = conn.execute("SELECT canonical_ref FROM paper_aliases WHERE alias = ?", (ref,)).fetchone()
        if row is None or row[0] == ref or row[0] in seen:
            return ref
        seen.add(ref)
        ref = row[0]
    return ref


def alias_rows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT alias, canonical_ref, kind, origin, created_at FROM paper_aliases ORDER BY alias"
    ).fetchall()
    return [dict(r) for r in rows]


def _link_rows(conn: sqlite3.Connection, paper_ref: str) -> list[dict[str, Any]]:
    """Study-link rows stored under this ref or any alias that resolves to its canonical ref."""
    canonical = resolve_ref(conn, paper_ref)
    candidates = [canonical]
    raw = store.canonical_paper_ref(paper_ref)
    if raw not in candidates:
        candidates.append(raw)
    # Every alias that resolves here, including chains such as pre -> mid -> canonical.
    frontier = [canonical]
    while frontier:
        for alias in _aliases_for(conn, frontier.pop()):
            if alias not in candidates:
                candidates.append(alias)
                frontier.append(alias)
    rows: list[dict[str, Any]] = []
    for candidate in candidates:
        row = conn.execute(
            "SELECT paper_ref, study_id, note, linked_at FROM study_reports WHERE paper_ref = ?", (candidate,)
        ).fetchone()
        if row is not None:
            rows.append(dict(row))
    return rows


def study_of(conn: sqlite3.Connection, paper_ref: str) -> str | None:
    """study_id linked to this report, looking through aliases; None when unlinked."""
    rows = _link_rows(conn, paper_ref)
    return rows[0]["study_id"] if rows else None


def _aliases_for(conn: sqlite3.Connection, canonical_ref: str) -> list[str]:
    return [
        r[0]
        for r in conn.execute(
            "SELECT alias FROM paper_aliases WHERE canonical_ref = ? ORDER BY alias", (canonical_ref,)
        )
    ]


def _conflicts_for(conn: sqlite3.Connection, alias: str) -> list[dict[str, Any]]:
    """Open conflicts only; paper_alias_record resolves them and the rows stay for provenance."""
    rows = conn.execute(
        "SELECT alias, existing_ref, proposed_ref, origin, seen_at FROM alias_conflicts "
        "WHERE alias = ? AND resolved_at IS NULL ORDER BY proposed_ref",
        (alias,),
    ).fetchall()
    return [dict(r) for r in rows]


def latest_recall_check(conn: sqlite3.Connection) -> dict[str, Any] | None:
    row = conn.execute("SELECT id, result FROM recall_checks ORDER BY id DESC LIMIT 1").fetchone()
    return {"check_id": row["id"], **json.loads(row["result"])} if row else None


def identity_summary(conn: sqlite3.Connection, included_refs: list[str]) -> dict[str, Any]:
    """Report and study counts for the included reports (used by review_status and export).

    Reports are resolved through the alias map first. An included report with no
    study link counts as its own study and is flagged unlinked.
    """
    groups: dict[str, dict[str, Any]] = {}
    reports: list[dict[str, Any]] = []
    for ref in included_refs:
        canonical = resolve_ref(conn, ref)
        study_id = study_of(conn, canonical)
        # Tuple keys, so an unlinked report never shares a group with a study_id like 'unlinked:x'.
        key = ("study", study_id) if study_id is not None else ("unlinked", canonical)
        group = groups.setdefault(
            key,
            {"study_id": study_id if study_id is not None else canonical, "unlinked": study_id is None, "reports": []},
        )
        group["reports"].append(ref)
        reports.append({"report": ref, "canonical_ref": canonical, "study_id": study_id})
    return {
        "report_count": len(included_refs),
        "canonical_report_count": len({r["canonical_ref"] for r in reports}),
        "study_count": len(groups),
        "groups": [groups[key] for key in sorted(groups)],
        "unlinked_reports": [r["report"] for r in reports if r["study_id"] is None],
        "reports": reports,
    }


# ---------------------------------------------------------------------------
# Dedup hook
# ---------------------------------------------------------------------------


def record_dedup(conn: sqlite3.Connection, papers: list[dict[str, Any]], result: dict[str, Any]) -> dict[str, Any]:
    """Persist aliases, conflicts and the retrieved-record snapshot of one full dedup.

    Called inside the dedup write transaction. Every identifier of every record is
    mapped to its cluster's canonical ref; a conflicting DOI stays its own canonical
    and is written to ``alias_conflicts``. Re-running the same input adds nothing.
    """
    now = store.now_iso()
    seen: set[tuple[str, str]] = set()
    aliases_recorded = 0

    canonical_by_index: dict[int, tuple[str | None, set[str]]] = {}
    for cluster in result.get("clusters", []):
        members = [i for i in cluster.get("member_indices", []) if isinstance(i, int) and 0 <= i < len(papers)]
        if not members:
            continue
        kept = cluster.get("kept_index")
        if kept not in members:
            kept = members[0]
        canonical, extra_dois = _cluster_canonical(papers, members, kept)
        for i in members:
            canonical_by_index[i] = (canonical, extra_dois)
    for i in range(len(papers)):
        if i not in canonical_by_index:
            canonical_by_index[i] = _cluster_canonical(papers, [i], i)

    for i, paper in enumerate(papers):
        canonical, extra_dois = canonical_by_index[i]
        if canonical is None:
            continue
        _, doi, pmid, _title = _dedup_record_ref(paper)
        identifiers: list[str] = []
        if doi:
            identifiers.append(doi)
        if pmid:
            identifiers.append(f"pmid:{pmid}")
        title_alias = _title_ref(paper)
        if title_alias:
            identifiers.append(title_alias)
        for alias in identifiers:
            if alias in extra_dois:
                # The cluster holds more than one DOI: the extra DOI keeps itself
                # as canonical and the disagreement is recorded, not merged.
                _record_conflict(conn, alias, alias, canonical, ORIGIN_DEDUP, now, seen)
                continue
            aliases_recorded += _register_alias(conn, alias, canonical, ORIGIN_DEDUP, now, seen)

    conn.execute("DELETE FROM retrieved_records")
    for idx, paper in enumerate(papers):
        paper_ref, doi, pmid, title = _dedup_record_ref(paper)
        canonical_ref = resolve_ref(conn, paper_ref) if paper_ref else None
        conn.execute(
            """INSERT INTO retrieved_records
               (idx, source, record_id, doi, pmid, title, year, paper_ref, canonical_ref)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                idx,
                _text(paper.get("source")),
                str(paper["id"]).strip() if paper.get("id") is not None else None,
                doi,
                pmid,
                title,
                _year_text(paper.get("year")),
                paper_ref,
                canonical_ref,
            ),
        )
    conn.execute(
        """INSERT INTO retrieval_runs (id, records, recorded_at, dedup) VALUES (1, ?, ?, ?)
           ON CONFLICT(id) DO UPDATE SET records = excluded.records, recorded_at = excluded.recorded_at,
               dedup = excluded.dedup""",
        (len(papers), now, json.dumps(prisma.dedup_record(conn), sort_keys=True)),
    )
    return {"aliases_recorded": aliases_recorded, "alias_conflicts": len(seen)}


def clear_retrieval_snapshot(conn: sqlite3.Connection) -> None:
    """Drop the retrieved-record snapshot when the search log it was taken from changes."""
    conn.execute("DELETE FROM retrieved_records")
    conn.execute("DELETE FROM retrieval_runs")


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


def _refs(value: Any, name: str = "paper_refs") -> list[str]:
    refs = list_arg(value, name, max_items=MAX_REFS, max_len=store.MAX_PAPER_REF_CHARS)
    if not refs:
        fail(f"{name} must contain 1 to {MAX_REFS} paper refs")
    return refs


@safe_tool
def paper_resolve(
    review_id: ReviewId,
    paper_refs: PaperRefs,
) -> dict[str, Any]:
    """Resolve paper refs through the canonical alias map.

    Looks up every ref recorded by dedup_papers or paper_alias_record. Status is
    'resolved' when the ref has an alias, 'conflict' when alias_conflicts holds a
    disagreeing proposal for it, and 'unknown' when no alias exists (the ref is
    returned unchanged). Only an exact stored mapping is used; nothing is guessed.

    Returns: refs (per ref: input, canonical_ref, status, aliases of the canonical
    record, study_id, conflicts), count, truncated, note.

    Does NOT merge conflicting mappings and does not add aliases; use
    paper_alias_record for a manual mapping.
    """
    refs = _refs(paper_refs)
    with store.review_db(review_id) as conn:
        resolved: list[dict[str, Any]] = []
        for value in refs:
            canonical_input = paper_ref_arg(value)
            canonical = resolve_ref(conn, canonical_input)
            conflicts = _conflicts_for(conn, canonical_input)
            aliases = _aliases_for(conn, canonical)
            has_alias = (
                conn.execute("SELECT 1 FROM paper_aliases WHERE alias = ?", (canonical_input,)).fetchone()
                is not None
            )
            status = "conflict" if conflicts else ("resolved" if has_alias else "unknown")
            conflict_rows, _ = cap(conflicts, 50)
            alias_list, _ = cap(aliases)
            resolved.append(
                {
                    "input": value,
                    "canonical_ref": canonical,
                    "status": status,
                    "aliases": alias_list,
                    "study_id": study_of(conn, canonical),
                    "conflicts": conflict_rows,
                }
            )
    listed, truncated = cap(resolved)
    return {
        "ok": True,
        "refs": listed,
        "count": len(resolved),
        "truncated": truncated,
        "note": (
            "Exact alias lookup only. 'unknown' means no alias is recorded, not that the paper does not exist; "
            "'conflict' means a disagreeing mapping is on record and the existing mapping was kept."
        ),
    }


@safe_tool
def paper_alias_record(
    review_id: ReviewId,
    alias: PaperRef,
    canonical_ref: PaperRef,
    replace: Annotated[
        bool,
        Field(description="Replace an existing mapping that points elsewhere. The old mapping is kept in alias_conflicts."),
    ] = False,
) -> dict[str, Any]:
    """Record one manual alias, for example a preprint DOI that belongs to a published paper.

    The alias and canonical_ref are canonicalised like every paper_ref. An existing
    mapping to another canonical ref is refused unless replace=true; a replacement
    updates the mapping and saves the previous canonical ref in alias_conflicts for
    provenance. Sending the same mapping again changes nothing.

    Returns: alias, canonical_ref, previous_canonical, recorded (true when a new
    alias was inserted), replaced (true when an existing mapping was changed), kind,
    origin.

    Does NOT validate the papers against an external registry.
    """
    alias_clean = paper_ref_arg(alias)
    canonical_clean = paper_ref_arg(canonical_ref)
    with store.review_db(review_id, write=True) as conn:
        now = store.now_iso()
        canonical = resolve_ref(conn, canonical_clean)
        row = conn.execute("SELECT canonical_ref FROM paper_aliases WHERE alias = ?", (alias_clean,)).fetchone()
        previous = row[0] if row else None
        # dedup maps every identifier to its own canonical; a self-mapping is not a decision to override.
        self_mapped = previous == alias_clean
        if previous is not None and previous != canonical and not self_mapped and not replace:
            fail(
                f"alias '{alias_clean}' already maps to '{previous}'; send replace=true to point it at "
                f"'{canonical}' (the previous mapping is kept in alias_conflicts)"
            )
        recorded = previous is None
        replaced = previous is not None and previous != canonical
        if recorded:
            conn.execute(
                "INSERT INTO paper_aliases (alias, canonical_ref, kind, origin, created_at) VALUES (?, ?, ?, ?, ?)",
                (alias_clean, canonical, _kind(alias_clean), ORIGIN_MANUAL, now),
            )
        elif replaced:
            if not self_mapped:
                conn.execute(
                    "INSERT INTO alias_conflicts (alias, existing_ref, proposed_ref, origin, seen_at) "
                    "VALUES (?, ?, ?, ?, ?) ON CONFLICT(alias, proposed_ref) DO NOTHING",
                    (alias_clean, previous, canonical, ORIGIN_MANUAL, now),
                )
            conn.execute(
                "UPDATE paper_aliases SET canonical_ref = ?, kind = ?, origin = ?, created_at = ? WHERE alias = ?",
                (canonical, _kind(alias_clean), ORIGIN_MANUAL, now, alias_clean),
            )
        # An explicit mapping is the user's decision: close every open conflict for this alias, keep the rows.
        conn.execute(
            "UPDATE alias_conflicts SET resolved_at = ? WHERE alias = ? AND resolved_at IS NULL", (now, alias_clean)
        )
    return {
        "ok": True,
        "alias": alias_clean,
        "canonical_ref": canonical,
        "previous_canonical": previous,
        "recorded": recorded,
        "replaced": replaced,
        "kind": _kind(alias_clean),
        "origin": ORIGIN_MANUAL,
    }


def _linked_rows(conn: sqlite3.Connection, refs: list[str]) -> dict[str, list[dict[str, Any]]]:
    """Every stored link per ref: a DOI and a PMID linked before they were aliased give two rows."""
    return {ref: rows for ref in refs if (rows := _link_rows(conn, ref))}


@safe_tool
def study_link(
    review_id: ReviewId,
    study_id: Annotated[str, Field(description="Stable study identifier, 1 to 80 characters from A-Z, a-z, 0-9, '_', '.', ':' or '-'.")],
    paper_refs: PaperRefs,
    note: Annotated[str | None, Field(description="Why these reports belong to one study (max 1000 characters).")] = None,
    replace: Annotated[
        bool,
        Field(description="Move reports that are already linked to another study. Without it such a link is refused."),
    ] = False,
) -> dict[str, Any]:
    """Link one or more reports to a study so reports and studies can be counted separately.

    Refs are canonicalised and resolved through the alias map before linking. A report
    already linked to a different study is refused unless replace=true, so a corrected
    link is explicit. Re-sending the same link changes nothing; the note is updated
    only when one is supplied.

    Returns: study_id, linked (newly linked refs), unchanged (already linked to this
    study), moved (paper_ref, from_study, to_study), reports_in_study (all reports now
    linked to study_id), count (refs in this call), note.

    Does NOT merge study records, combine sample sizes or decide whether two reports
    are the same study; use study_overview to see the grouping.
    """
    study = text_arg(study_id, "study_id", max_len=80)
    if not STUDY_ID_RE.fullmatch(study):
        fail("study_id must be 1 to 80 characters from A-Z, a-z, 0-9, '_', '.', ':' or '-' (for example 'Smith2020')")
    refs = _refs(paper_refs)
    note_clean = text_arg(note, "note", required=False, max_len=1000)
    linked: list[str] = []
    unchanged: list[str] = []
    moved: list[dict[str, str]] = []
    with store.review_db(review_id, write=True) as conn:
        resolved: list[str] = []
        for value in refs:
            ref = resolve_ref(conn, paper_ref_arg(value))
            if ref not in resolved:
                resolved.append(ref)
        existing = _linked_rows(conn, resolved)
        blocked = [
            {"paper_ref": ref, "study_id": row["study_id"]}
            for ref, rows in existing.items()
            for row in rows
            if row["study_id"] != study
        ]
        if blocked and not replace:
            shown = ", ".join(f"{b['paper_ref']} ({b['study_id']})" for b in blocked[:5])
            fail(
                f"{len(blocked)} report(s) are already linked to another study: {shown}. Send replace=true to move "
                "them (the response lists every move)."
            )
        now = store.now_iso()
        for ref in resolved:
            rows = existing.get(ref)
            if not rows:
                conn.execute(
                    "INSERT INTO study_reports (paper_ref, study_id, note, linked_at) VALUES (?, ?, ?, ?)",
                    (ref, study, note_clean, now),
                )
                linked.append(ref)
                continue
            old = [row for row in rows if row["study_id"] != study]
            if not old:
                for row in rows:
                    if note_clean is not None and note_clean != row["note"]:
                        conn.execute(
                            "UPDATE study_reports SET note = ? WHERE paper_ref = ?", (note_clean, row["paper_ref"])
                        )
                unchanged.append(ref)
                continue
            for row in old:
                conn.execute(
                    "UPDATE study_reports SET study_id = ?, note = COALESCE(?, note), linked_at = ? WHERE paper_ref = ?",
                    (study, note_clean, now, row["paper_ref"]),
                )
            for from_study in sorted({row["study_id"] for row in old}):
                moved.append({"paper_ref": ref, "from_study": from_study, "to_study": study})
        reports_in_study = int(
            conn.execute("SELECT COUNT(*) FROM study_reports WHERE study_id = ?", (study,)).fetchone()[0]
        )
    return {
        "ok": True,
        "study_id": study,
        "linked": linked,
        "unchanged": unchanged,
        "moved": moved,
        "reports_in_study": reports_in_study,
        "count": len(resolved),
        "note": note_clean,
    }


@safe_tool
def study_unlink(
    review_id: ReviewId,
    paper_refs: PaperRefs,
) -> dict[str, Any]:
    """Remove the study link from one or more reports, leaving the reports themselves intact.

    Refs are resolved through the alias map, so a link stored under an earlier form of
    the key is removed too.

    Returns: removed (refs that had a link), not_linked (refs with no link), count.

    Does NOT delete screening decisions, evidence rows or any other report record.
    """
    refs = _refs(paper_refs)
    removed: list[str] = []
    not_linked: list[str] = []
    with store.review_db(review_id, write=True) as conn:
        seen: set[str] = set()
        for value in refs:
            ref = resolve_ref(conn, paper_ref_arg(value))
            if ref in seen:
                continue
            seen.add(ref)
            rows = _link_rows(conn, ref)
            if rows:
                for row in rows:
                    conn.execute("DELETE FROM study_reports WHERE paper_ref = ?", (row["paper_ref"],))
                removed.append(ref)
            else:
                not_linked.append(ref)
    return {"ok": True, "removed": removed, "not_linked": not_linked, "count": len(removed)}


@safe_tool
def study_overview(
    review_id: ReviewId,
    reviewer: Annotated[
        str | None,
        Field(description="Whose full_text includes to count. Default: 'primary' if that reviewer exists, else the only reviewer."),
    ] = None,
) -> dict[str, Any]:
    """Count included reports separately from studies and flag outcomes one study reports twice.

    Included reports come from the PRISMA full_text include decisions and are resolved
    through the alias map. Each distinct study is one group; an included report with
    no study link counts as its own study and is flagged unlinked.
    duplicate_participant_risk lists an outcome recorded from two or more reports of
    the same linked study: pooling both rows would count its participants twice.

    Returns: report_count, canonical_report_count, study_count, groups (study_id,
    unlinked, reports), unlinked_reports, duplicate_participant_risk (study_id,
    outcome_key, outcome, reports), truncated, note.

    Does NOT prove that two reports share participants, decide whether pooling is
    appropriate, or treat an unlinked report as anything other than its own study.
    """
    reviewer_clean = reviewer_arg(reviewer) if reviewer is not None and str(reviewer).strip() else None
    with store.review_db(review_id) as conn:
        included = prisma.included_refs(conn, reviewer_clean)
        summary = identity_summary(conn, included)
        included_canonical = {resolve_ref(conn, ref) for ref in included}
        by_outcome: dict[tuple[str, str], dict[str, Any]] = {}
        for row in extraction.evidence_rows(conn):
            # One report recorded under its DOI and its PMID is still one report.
            report = resolve_ref(conn, row["paper_ref"])
            if report not in included_canonical:
                continue
            study_id = study_of(conn, row["paper_ref"])
            if study_id is None:
                continue
            # Same key evidence_record stores; evidence_rows() does not return the column.
            outcome_key = re.sub(r"\s+", " ", row["outcome"].strip().lower())
            entry = by_outcome.setdefault(
                (study_id, outcome_key),
                {"study_id": study_id, "outcome_key": outcome_key, "outcome": row["outcome"], "reports": set()},
            )
            entry["reports"].add(report)
    risk = [
        {**entry, "reports": sorted(entry["reports"])}
        for key, entry in sorted(by_outcome.items())
        if len(entry["reports"]) >= 2
    ]
    groups, cut_g = cap(summary["groups"])
    unlinked, cut_u = cap(summary["unlinked_reports"])
    risk_list, cut_r = cap(risk)
    return {
        "ok": True,
        "report_count": summary["report_count"],
        "canonical_report_count": summary["canonical_report_count"],
        "study_count": summary["study_count"],
        "groups": groups,
        "unlinked_reports": unlinked,
        "duplicate_participant_risk": risk_list,
        "truncated": cut_g or cut_u or cut_r,
        "note": (
            "Counts come from exact alias and study links, not from any judgement about whether reports share "
            "participants. An unlinked report is counted as its own study."
        ),
    }


@safe_tool
def recall_seed_record(
    review_id: ReviewId,
    paper_refs: PaperRefs,
    labels: Annotated[
        list[str] | None,
        Field(description="Optional labels, one per paper_ref, for example when each seed came from a different source."),
    ] = None,
    replace: Annotated[bool, Field(description="Replace the whole seed set instead of adding to it.")] = False,
) -> dict[str, Any]:
    """Record a small seed set of known eligible papers for a known-item recall check.

    Seeds are stored as canonical refs, so a later alias mapping is applied when
    recall_check runs. With replace=true the previous seed set is removed first.
    Re-adding an existing seed keeps its label unless a new label is supplied.

    Returns: added, updated, unchanged, removed (seeds cleared by replace), count,
    seeds (paper_ref, label, added_at), truncated.

    Does NOT search for the seeds or decide eligibility; it records what you send.
    """
    _refs(paper_refs)  # type and size checks; pairing below keeps the caller's positions
    if labels is not None and (not isinstance(labels, list) or len(labels) != len(paper_refs)):
        fail("labels must have one entry per paper_ref, in the same order (or be omitted)")
    pairs: list[tuple[str, str | None]] = []
    seen: set[str] = set()
    for index, value in enumerate(paper_refs):
        ref = paper_ref_arg(value)
        label = text_arg(labels[index], f"labels[{index}]", required=False, max_len=200) if labels else None
        if ref in seen:
            continue
        seen.add(ref)
        pairs.append((ref, label))
    with store.review_db(review_id, write=True) as conn:
        now = store.now_iso()
        removed = conn.execute("DELETE FROM recall_seeds").rowcount if replace else 0
        added = updated = unchanged = 0
        for ref, label in pairs:
            row = conn.execute("SELECT label FROM recall_seeds WHERE paper_ref = ?", (ref,)).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO recall_seeds (paper_ref, label, added_at) VALUES (?, ?, ?)", (ref, label, now)
                )
                added += 1
            elif label is not None and label != row[0]:
                conn.execute("UPDATE recall_seeds SET label = ? WHERE paper_ref = ?", (label, ref))
                updated += 1
            else:
                unchanged += 1
        rows = conn.execute("SELECT paper_ref, label, added_at FROM recall_seeds ORDER BY paper_ref").fetchall()
    seeds, truncated = cap([dict(r) for r in rows])
    return {
        "ok": True,
        "added": added,
        "updated": updated,
        "unchanged": unchanged,
        "removed": removed,
        "count": len(rows),
        "seeds": seeds,
        "truncated": truncated,
    }


def _has_year(text: str | None) -> bool:
    return bool(text and _YEAR_RE.search(text))


@safe_tool
def recall_check(review_id: ReviewId) -> dict[str, Any]:
    """Compare the recorded seed set with the retrieved records of the last full dedup.

    Alias resolution runs first. A seed is 'found' when a retrieved record has the same
    canonical ref, 'not_found' when retrieved records exist but none matches, and
    'unresolved' when no full dedup has been recorded, when the seed has an alias
    conflict, or when a title seed's only candidate records have no year. Each found
    seed lists the sources that retrieved it; per-source counts separate found, missed
    and unresolved seeds.

    Returns: seeds (paper_ref, label, added_at, canonical_ref, status, sources,
    matched_records, truncated, reason), counts (found, not_found, unresolved),
    recall (found / (found + not_found), null
    when that denominator is 0), per_source (source, records, found, missed,
    unresolved), records_checked, retrieval_recorded_at, check_id, checked_at,
    truncated, note.

    Does NOT prove the search was exhaustive: it only says whether this seed set was
    in the recorded export.
    """
    with store.review_db(review_id, write=True) as conn:
        seed_rows = [
            dict(r)
            for r in conn.execute("SELECT paper_ref, label, added_at FROM recall_seeds ORDER BY paper_ref")
        ]
        if not seed_rows:
            fail("no recall seeds are recorded; call recall_seed_record first with the known eligible papers")
        run = conn.execute("SELECT records, recorded_at, dedup FROM retrieval_runs WHERE id = 1").fetchone()
        dedup = prisma.dedup_record(conn)
        # The snapshot is valid only for the dedup record it was taken with; a later manual count replaces it.
        has_snapshot = run is not None and dedup is not None and run["dedup"] == json.dumps(dedup, sort_keys=True)
        records = (
            [
                dict(r)
                for r in conn.execute(
                    "SELECT idx, source, record_id, paper_ref, canonical_ref, title, year "
                    "FROM retrieved_records ORDER BY idx"
                )
            ]
            if has_snapshot
            else []
        )
        checked_at = store.now_iso()
        source_counts: dict[str, dict[str, Any]] = {}
        for record in records:
            # Aliases recorded after the dedup (e.g. preprint -> published) apply to the records too.
            record["canonical_ref"] = resolve_ref(conn, record["paper_ref"]) if record["paper_ref"] else None
            source = record["source"] or "unknown"
            entry = source_counts.setdefault(
                source, {"source": source, "records": 0, "found": 0, "missed": 0, "unresolved": 0}
            )
            entry["records"] += 1

        outcomes: list[dict[str, Any]] = []
        counts = {"found": 0, "not_found": 0, "unresolved": 0}
        for seed in seed_rows:
            seed_ref = seed["paper_ref"]
            conflicts = _conflicts_for(conn, seed_ref)
            if not has_snapshot:
                status = "unresolved"
                canonical = resolve_ref(conn, seed_ref)
                matched: list[dict[str, Any]] = []
                reason = (
                    "no full dedup is recorded for the current search log; run dedup_papers with review_id and "
                    "every exported record"
                )
            elif conflicts:
                status = "unresolved"
                canonical = resolve_ref(conn, seed_ref)
                matched = []
                existing = conflicts[0]["existing_ref"]
                proposed = conflicts[0]["proposed_ref"]
                reason = (
                    f"alias conflict for '{seed_ref}': it maps to '{existing}' but '{proposed}' was also proposed; "
                    "resolve it with paper_alias_record before relying on this seed"
                )
            else:
                canonical = resolve_ref(conn, seed_ref)
                matched = [r for r in records if r["canonical_ref"] == canonical]
                reason = None
                if matched:
                    status = "found"
                elif seed_ref.startswith("title:"):
                    title_part = seed_ref[6:].rpartition("|")[0]
                    candidates = [
                        r
                        for r in records
                        if r["title"] and store.normalize_title(r["title"]) == title_part
                    ]
                    if candidates and not any(_has_year(r["year"]) for r in candidates):
                        status = "unresolved"
                        reason = (
                            "the only retrieved records with this title have no year, so a title match cannot be "
                            "confirmed"
                        )
                    else:
                        status = "not_found"
                else:
                    status = "not_found"
            counts[status] += 1
            sources = sorted({r["source"] or "unknown" for r in matched})
            matched_rows, cut_m = cap(matched, 50)
            outcomes.append(
                {
                    "paper_ref": seed_ref,
                    "label": seed["label"],
                    "added_at": seed["added_at"],
                    "canonical_ref": canonical,
                    "status": status,
                    "sources": sources,
                    "matched_records": [
                        {"idx": r["idx"], "source": r["source"], "record_id": r["record_id"]} for r in matched_rows
                    ],
                    "truncated": cut_m,
                    "reason": reason,
                }
            )

        for source, entry in source_counts.items():
            for outcome in outcomes:
                if source in outcome["sources"]:
                    entry["found"] += 1
                elif outcome["status"] == "unresolved":
                    entry["unresolved"] += 1
                else:
                    entry["missed"] += 1

        found = counts["found"]
        denominator = counts["found"] + counts["not_found"]
        recall = round(found / denominator, 4) if denominator else None
        per_source = sorted(source_counts.values(), key=lambda e: e["source"])
        seed_list, cut_s = cap(outcomes)
        per_source_list, cut_p = cap(per_source)
        summary = {
            "checked_at": checked_at,
            "records_checked": len(records),
            "retrieval_recorded_at": run["recorded_at"] if has_snapshot else None,
            "counts": counts,
            "recall": recall,
            "per_source": per_source_list,
            "seeds": seed_list,
        }
        check_id = conn.execute(
            "INSERT INTO recall_checks (checked_at, result) VALUES (?, ?)",
            (checked_at, json.dumps(summary, ensure_ascii=False, sort_keys=True)),
        ).lastrowid
    return {
        "ok": True,
        **summary,
        "check_id": check_id,
        "truncated": cut_s or cut_p,
        "note": (
            "Seed recall measures whether these known eligible papers were in the recorded export. It is not "
            "proof that the search was exhaustive."
        ),
    }


def register(server: MCPServer) -> None:
    for tool in (
        paper_resolve,
        paper_alias_record,
        study_link,
        study_unlink,
        study_overview,
        recall_seed_record,
        recall_check,
    ):
        server.tool()(tool)
