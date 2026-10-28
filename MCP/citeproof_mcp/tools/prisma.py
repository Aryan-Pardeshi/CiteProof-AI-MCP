# SPDX-License-Identifier: MIT
"""PRISMA 2020 identification and screening: search log, deduplication, screening
decisions, reviewer agreement, flow counts, flow diagrams and the reporting checklist.

Tables owned here: ``prisma_searches``, ``prisma_dedup``, ``screening``. Other modules
read them only through the public functions in the "Read API" section.
"""

import re
import sqlite3
from datetime import date, datetime, timedelta
from typing import Annotated, Any

from pydantic import Field

from citeproof_mcp import store
from citeproof_mcp._vendor.evidence import PRISMATracker
from citeproof_mcp.compat import MCPServer
from citeproof_mcp.tools import dedup, prisma_render
from citeproof_mcp.tools.common import (
    LIST_CAP,
    MAX_TEXT_CHARS,
    cap,
    choice_arg,
    fail,
    int_arg,
    paper_ref_arg,
    reviewer_arg,
    safe_tool,
    size_arg,
    text_arg,
)

store.register_schema(
    (
        """CREATE TABLE IF NOT EXISTS prisma_searches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            query TEXT NOT NULL,
            date_run TEXT NOT NULL,
            hits INTEGER NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS prisma_dedup (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            duplicates_removed INTEGER NOT NULL,
            method TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS screening (
            paper_ref TEXT NOT NULL,
            stage TEXT NOT NULL,
            reviewer TEXT NOT NULL,
            decision TEXT NOT NULL,
            reason TEXT,
            title TEXT,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (paper_ref, stage, reviewer)
        )""",
    )
)

STAGES = ("title_abstract", "full_text")
STAGE_ALIASES = {
    "title": "title_abstract",
    "abstract": "title_abstract",
    "title_and_abstract": "title_abstract",
    "title/abstract": "title_abstract",
    "ta": "title_abstract",
    "fulltext": "full_text",
    "ft": "full_text",
}
DECISIONS = ("include", "exclude", "not_retrieved")
DECISION_ALIASES = {"included": "include", "excluded": "exclude", "not_available": "not_retrieved", "unavailable": "not_retrieved"}
DECISIONS_BY_STAGE = {"title_abstract": ("include", "exclude"), "full_text": DECISIONS}
MAX_BATCH = 500
MAX_DEDUP_PAPERS = 10_000

PaperRef = Annotated[
    str,
    Field(description="Paper key: a DOI ('10.1234/abc'), 'pmid:<digits>' or 'title:<title>|<year>'. DOIs are matched case-insensitively."),
]
ReviewId = Annotated[str, Field(description="The id returned by review_create.")]
ReviewerOpt = Annotated[
    str | None,
    Field(description="Whose decisions to count. Default: 'primary' if that reviewer exists, else the only reviewer."),
]


# ---------------------------------------------------------------------------
# Read API (used by this module's tools and by review/export tools)
# ---------------------------------------------------------------------------


def search_totals(conn: sqlite3.Connection) -> tuple[int, dict[str, int], int]:
    """(records_identified, records_by_source, number_of_searches)."""
    rows = conn.execute(
        "SELECT source, SUM(hits) AS total, COUNT(*) AS n FROM prisma_searches GROUP BY source ORDER BY source"
    ).fetchall()
    by_source = {r["source"]: int(r["total"]) for r in rows}
    return sum(by_source.values()), by_source, sum(int(r["n"]) for r in rows)


def search_log(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT id, source, query, date_run, hits FROM prisma_searches ORDER BY id").fetchall()
    return [dict(r) for r in rows]


def dedup_record(conn: sqlite3.Connection) -> dict[str, Any] | None:
    row = conn.execute("SELECT duplicates_removed, method FROM prisma_dedup WHERE id = 1").fetchone()
    return dict(row) if row else None


def screening_rows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT paper_ref, stage, reviewer, decision, reason, title, updated_at FROM screening "
        "ORDER BY reviewer, stage, paper_ref"
    ).fetchall()
    return [dict(r) for r in rows]


def reviewers_by_stage(conn: sqlite3.Connection) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {s: [] for s in STAGES}
    for r in conn.execute("SELECT DISTINCT stage, reviewer FROM screening ORDER BY reviewer"):
        out.setdefault(r["stage"], []).append(r["reviewer"])
    return out


def resolve_reviewer(conn: sqlite3.Connection, reviewer: str | None) -> str | None:
    """The reviewer whose decisions drive the PRISMA counts (see prisma_status)."""
    known = [r[0] for r in conn.execute("SELECT DISTINCT reviewer FROM screening ORDER BY reviewer")]
    if reviewer is not None and str(reviewer).strip():
        name = reviewer_arg(reviewer)
        if known and name not in known:
            fail(f"reviewer '{name}' has no screening decisions; reviewers with decisions: {', '.join(known)}")
        return name
    if not known:
        return None
    if "primary" in known:
        return "primary"
    if len(known) == 1:
        return known[0]
    fail(
        f"Multiple reviewers found in screening decisions ({', '.join(known)}). Pass reviewer=<name>, "
        "or record the consensus decisions under reviewer 'primary' (screening_agreement lists the disagreements)."
    )
    return None  # unreachable


def included_refs(conn: sqlite3.Connection, reviewer: str | None = None) -> list[str]:
    """paper_refs with a full_text include by the counting reviewer. Empty when that is ambiguous."""
    try:
        name = resolve_reviewer(conn, reviewer)
    except store.StoreError:
        return []
    if name is None:
        return []
    rows = conn.execute(
        "SELECT paper_ref FROM screening WHERE reviewer = ? AND stage = 'full_text' AND decision = 'include' "
        "ORDER BY paper_ref",
        (name,),
    ).fetchall()
    return [r[0] for r in rows]


def _reason_counts(conn: sqlite3.Connection, reviewer: str, stage: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for r in conn.execute(
        "SELECT reason FROM screening WHERE reviewer = ? AND stage = ? AND decision = 'exclude'", (reviewer, stage)
    ):
        key = re.sub(r"\s+", " ", (r[0] or "").strip().lower())
        if key:
            counts[key] = counts.get(key, 0) + 1
    return counts


def compute_status(conn: sqlite3.Connection, reviewer: str | None = None) -> dict[str, Any]:
    """Full PRISMA 2020 counts, invariant checks, warnings and next steps (no 'ok' key)."""
    used = resolve_reviewer(conn, reviewer)
    identified, by_source, n_searches = search_totals(conn)
    dedup = dedup_record(conn)
    duplicates = int(dedup["duplicates_removed"]) if dedup else 0
    after_dedup = max(0, identified - duplicates)

    counts: dict[tuple[str, str], int] = {}
    pending = 0
    orphans: list[str] = []
    reasons_ft: dict[str, int] = {}
    reasons_ta: dict[str, int] = {}
    if used is not None:
        for r in conn.execute(
            "SELECT stage, decision, COUNT(*) AS c FROM screening WHERE reviewer = ? GROUP BY stage, decision",
            (used,),
        ):
            counts[(r["stage"], r["decision"])] = int(r["c"])
        pending = int(
            conn.execute(
                """SELECT COUNT(*) FROM screening t
                   WHERE t.reviewer = ? AND t.stage = 'title_abstract' AND t.decision = 'include'
                     AND NOT EXISTS (SELECT 1 FROM screening f WHERE f.paper_ref = t.paper_ref
                                     AND f.reviewer = t.reviewer AND f.stage = 'full_text')""",
                (used,),
            ).fetchone()[0]
        )
        orphans = [
            r[0]
            for r in conn.execute(
                """SELECT paper_ref FROM screening f
                   WHERE f.reviewer = ? AND f.stage = 'full_text'
                     AND NOT EXISTS (SELECT 1 FROM screening t WHERE t.paper_ref = f.paper_ref
                                     AND t.reviewer = f.reviewer AND t.stage = 'title_abstract'
                                     AND t.decision = 'include')
                   ORDER BY paper_ref""",
                (used,),
            )
        ]
        reasons_ft = _reason_counts(conn, used, "full_text")
        reasons_ta = _reason_counts(conn, used, "title_abstract")

    def c(stage: str, decision: str | None = None) -> int:
        return sum(v for (s, d), v in counts.items() if s == stage and (decision is None or d == decision))

    screened = c("title_abstract")
    excluded_ta = c("title_abstract", "exclude")
    requested = c("title_abstract", "include")
    unavailable = c("full_text", "not_retrieved")
    excluded_ft = c("full_text", "exclude")
    included = c("full_text", "include")
    assessed = included + excluded_ft

    tracker = PRISMATracker(
        records_identified=identified,
        records_by_source=by_source,
        duplicates_removed=duplicates,
        records_after_dedup=after_dedup,
        records_screened=screened,
        excluded_title_abstract=excluded_ta,
        full_text_requested=requested,
        full_text_unavailable=unavailable,
        full_text_assessed=assessed,
        excluded_full_text=excluded_ft,
        studies_included=included,
        exclusion_reasons=reasons_ft,
    )
    errors = tracker.validate_invariants()
    if orphans:
        errors.append(
            f"PRISMA Invariant Broken: {len(orphans)} paper(s) have a full_text decision but no title_abstract "
            f"include, so reports sought ({requested}) != assessed ({assessed}) + not retrieved ({unavailable}) "
            f"+ awaiting ({pending})"
        )

    warnings: list[str] = []
    if n_searches == 0 and screened:
        warnings.append("No searches are recorded, so records_identified is 0.")
    if n_searches and dedup is None:
        warnings.append("No deduplication is recorded; duplicates_removed is taken as 0.")
    if screened > after_dedup:
        warnings.append(
            f"More records screened ({screened}) than remain after deduplication ({after_dedup}): "
            "a search or the duplicate count is missing or wrong."
        )
    awaiting = max(0, after_dedup - screened)

    next_steps: list[str] = []
    if n_searches == 0:
        next_steps.append("Record every database search with prisma_record_search.")
    elif dedup is None:
        next_steps.append("Run dedup_papers over the exported records (or record the count with prisma_record_dedup).")
    if awaiting:
        next_steps.append(f"Record title_abstract decisions for {awaiting} more record(s).")
    if pending:
        next_steps.append(
            f"Record full_text decisions (include, exclude with reason, or not_retrieved) for {pending} report(s)."
        )
    if errors:
        next_steps.append("Fix the decisions named in invariant_errors before reporting the counts.")
    complete = bool(n_searches and dedup and not awaiting and not pending and not errors and screened == after_dedup)
    if complete:
        next_steps.append("PRISMA counts are complete; render them with prisma_flow_diagram.")

    orphan_list, _ = cap(orphans, 20)
    return {
        "reviewer_used": used,
        "invariants_valid": not errors,
        "invariant_errors": errors,
        "warnings": warnings,
        **tracker.model_dump(),
        "exclusion_reasons_title_abstract": reasons_ta,
        "full_text_pending": pending,
        "records_awaiting_screening": awaiting,
        "full_text_without_title_abstract_include": orphan_list,
        "searches_recorded": n_searches,
        "dedup_recorded": dedup is not None,
        "dedup_method": dedup["method"] if dedup else None,
        "complete": complete,
        "next_steps": next_steps,
    }


def agreement(conn: sqlite3.Connection, stage: str, rev_a: str, rev_b: str) -> dict[str, Any]:
    """Cohen's kappa for include/exclude decisions both reviewers made at one stage."""

    def decisions(name: str) -> dict[str, sqlite3.Row]:
        rows = conn.execute(
            "SELECT paper_ref, decision, reason, title FROM screening WHERE stage = ? AND reviewer = ?", (stage, name)
        ).fetchall()
        return {r["paper_ref"]: r for r in rows}

    map_a, map_b = decisions(rev_a), decisions(rev_b)
    only_a = len(map_a.keys() - map_b.keys())
    only_b = len(map_b.keys() - map_a.keys())
    table = {"both_include": 0, "a_include_b_exclude": 0, "a_exclude_b_include": 0, "both_exclude": 0}
    not_judged = 0
    disagreements: list[dict[str, Any]] = []
    for ref in sorted(map_a.keys() & map_b.keys()):
        ra, rb = map_a[ref], map_b[ref]
        da, db = ra["decision"], rb["decision"]
        if da not in ("include", "exclude") or db not in ("include", "exclude"):
            not_judged += 1
            continue
        key = {
            ("include", "include"): "both_include",
            ("include", "exclude"): "a_include_b_exclude",
            ("exclude", "include"): "a_exclude_b_include",
            ("exclude", "exclude"): "both_exclude",
        }[(da, db)]
        table[key] += 1
        if da != db:
            disagreements.append(
                {
                    "paper_ref": ref,
                    "title": ra["title"] or rb["title"] or "",
                    "a": da,
                    "b": db,
                    "reason_a": ra["reason"],
                    "reason_b": rb["reason"],
                }
            )

    shared = sum(table.values())
    agree = table["both_include"] + table["both_exclude"]
    po = pe = kappa = None
    interpretation = "undefined"
    if shared:
        po = agree / shared
        a_inc = (table["both_include"] + table["a_include_b_exclude"]) / shared
        b_inc = (table["both_include"] + table["a_exclude_b_include"]) / shared
        pe = a_inc * b_inc + (1 - a_inc) * (1 - b_inc)
        if abs(1.0 - pe) > 1e-9:
            kappa = round((po - pe) / (1.0 - pe), 4)
            interpretation = _landis_koch(kappa)
    listed, truncated = cap(disagreements)
    return {
        "stage": stage,
        "reviewer_a": rev_a,
        "reviewer_b": rev_b,
        "shared": shared,
        "agree": agree,
        "table": table,
        "po": round(po, 4) if po is not None else None,
        "pe": round(pe, 4) if pe is not None else None,
        "kappa": kappa,
        "interpretation": interpretation,
        "disagreement_count": len(disagreements),
        "disagreements": listed,
        "truncated": truncated,
        "only_a": only_a,
        "only_b": only_b,
        "not_judged": not_judged,
    }


def _landis_koch(kappa: float) -> str:
    """Landis and Koch (1977) bands."""
    if kappa < 0.0:
        return "poor"
    for upper, label in ((0.20, "slight"), (0.40, "fair"), (0.60, "moderate"), (0.80, "substantial")):
        if kappa <= upper:
            return label
    return "almost perfect"


# ---------------------------------------------------------------------------
# Write helpers
# ---------------------------------------------------------------------------


def _save_dedup(conn: sqlite3.Connection, duplicates_removed: int, method: str) -> int:
    identified, _, _ = search_totals(conn)
    if duplicates_removed > identified:
        fail(
            f"duplicates_removed ({duplicates_removed}) cannot exceed the total hits recorded so far "
            f"({identified}); record every search with prisma_record_search first"
        )
    conn.execute(
        """INSERT INTO prisma_dedup (id, duplicates_removed, method) VALUES (1, ?, ?)
           ON CONFLICT(id) DO UPDATE SET duplicates_removed = excluded.duplicates_removed, method = excluded.method""",
        (duplicates_removed, method),
    )
    return identified


def _clean_decision(item: Any) -> dict[str, Any]:
    if not isinstance(item, dict):
        fail("each decision must be an object with paper_ref, stage and decision")
    stage = choice_arg(item.get("stage"), "stage", STAGES, STAGE_ALIASES)
    decision = choice_arg(item.get("decision"), "decision", DECISIONS, DECISION_ALIASES)
    if decision not in DECISIONS_BY_STAGE[stage]:
        fail("decision 'not_retrieved' is only valid at stage 'full_text' (the full text could not be obtained)")
    reason = text_arg(item.get("reason"), "reason", required=False, max_len=300)
    if decision == "exclude" and not reason:
        fail("reason is required when decision is 'exclude'; use a short reusable label such as 'wrong population'")
    return {
        "paper_ref": paper_ref_arg(item.get("paper_ref")),
        "stage": stage,
        "decision": decision,
        "reason": reason,
        "reviewer": reviewer_arg(item.get("reviewer")),
        "title": text_arg(item.get("title"), "title", required=False, max_len=500),
    }


def _upsert_decision(conn: sqlite3.Connection, d: dict[str, Any], now: str) -> tuple[bool, list[str]]:
    key = (d["paper_ref"], d["stage"], d["reviewer"])
    updated = (
        conn.execute("SELECT 1 FROM screening WHERE paper_ref = ? AND stage = ? AND reviewer = ?", key).fetchone()
        is not None
    )
    warnings: list[str] = []
    if d["stage"] == "full_text":
        ta = conn.execute(
            "SELECT decision FROM screening WHERE paper_ref = ? AND stage = 'title_abstract' AND reviewer = ?",
            (d["paper_ref"], d["reviewer"]),
        ).fetchone()
        if ta is None or ta[0] != "include":
            warnings.append(
                f"Paper '{d['paper_ref']}' has no title_abstract include decision by reviewer '{d['reviewer']}'."
            )
    elif d["decision"] != "include":
        ft = conn.execute(
            "SELECT 1 FROM screening WHERE paper_ref = ? AND stage = 'full_text' AND reviewer = ?",
            (d["paper_ref"], d["reviewer"]),
        ).fetchone()
        if ft is not None:
            warnings.append(
                f"Paper '{d['paper_ref']}' already has a full_text decision by '{d['reviewer']}'; "
                "excluding it at title_abstract breaks the PRISMA counts until that decision is changed."
            )
    conn.execute(
        """INSERT INTO screening (paper_ref, stage, reviewer, decision, reason, title, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(paper_ref, stage, reviewer) DO UPDATE SET
               decision = excluded.decision,
               reason = excluded.reason,
               title = COALESCE(excluded.title, screening.title),
               updated_at = excluded.updated_at""",
        (d["paper_ref"], d["stage"], d["reviewer"], d["decision"], d["reason"], d["title"], now),
    )
    return updated, warnings


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@safe_tool
def prisma_record_search(
    review_id: ReviewId,
    source: Annotated[str, Field(description="Database or register searched, e.g. 'PubMed', 'Embase', 'ClinicalTrials.gov'.")],
    query: Annotated[str, Field(description="The exact search string as run, including field tags and limits.")],
    date_run: Annotated[str, Field(description="Date the search was run, YYYY-MM-DD.")],
    hits: Annotated[int, Field(description="Number of records the search returned (0 or more).")],
) -> dict[str, Any]:
    """Log one database or register search for the PRISMA identification box.

    Call once per database search, right after running it. Re-sending the same
    source + query + date_run replaces the hit count instead of adding a second row,
    so a retried call cannot double-count records. Source names are matched
    case-insensitively ('pubmed' joins 'PubMed').

    Adding a search, or changing a search's hit count, clears the review's
    deduplication record, because that count belonged to the old set of records.
    Run dedup_papers (or prisma_record_dedup) again afterwards. Re-sending a
    search with the same hit count changes nothing.

    Returns: search_id, updated (true when an existing search was replaced),
    records_identified (sum of hits over all searches), records_by_source,
    searches_recorded, dedup_cleared (true when an earlier dedup record was
    cleared), warnings.

    Does NOT run the search or check the hit count; it records what you report.
    """
    source_clean = text_arg(source, "source", max_len=200)
    query_clean = text_arg(query, "query", max_len=20_000)
    hits_clean = int_arg(hits, "hits", min_value=0, max_value=100_000_000)
    date_clean = text_arg(date_run, "date_run", max_len=40)
    try:
        run_day = datetime.strptime(date_clean, "%Y-%m-%d").date() if re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_clean) else None
    except ValueError:
        run_day = None
    if run_day is None:
        fail("date_run must be a real date in YYYY-MM-DD format")
    warnings: list[str] = []
    if run_day > date.today() + timedelta(days=1):
        warnings.append(f"date_run {date_clean} is in the future; check the year.")

    with store.review_db(review_id, write=True) as conn:
        same = conn.execute(
            "SELECT source FROM prisma_searches WHERE lower(source) = lower(?) ORDER BY id LIMIT 1", (source_clean,)
        ).fetchone()
        if same:
            source_clean = same[0]
        existing = conn.execute(
            "SELECT id, hits FROM prisma_searches WHERE source = ? AND query = ? AND date_run = ?",
            (source_clean, query_clean, date_clean),
        ).fetchone()
        search_log_changed = existing is None or existing[1] != hits_clean
        if existing:
            conn.execute("UPDATE prisma_searches SET hits = ? WHERE id = ?", (hits_clean, existing[0]))
            search_id = existing[0]
        else:
            search_id = conn.execute(
                "INSERT INTO prisma_searches (source, query, date_run, hits) VALUES (?, ?, ?, ?)",
                (source_clean, query_clean, date_clean, hits_clean),
            ).lastrowid
        # A dedup count belongs to one exact set of exported records. When the search log
        # changes, the old count is stale, so it is cleared instead of silently reused.
        dedup_cleared = False
        if search_log_changed:
            dedup_cleared = conn.execute("DELETE FROM prisma_dedup").rowcount > 0
        if dedup_cleared:
            warnings.append(
                "The search log changed, so the earlier deduplication record was cleared. "
                "Run dedup_papers again on every exported record (or prisma_record_dedup with the new count)."
            )
        identified, by_source, n = search_totals(conn)
    return {
        "ok": True,
        "search_id": search_id,
        "updated": existing is not None,
        "records_identified": identified,
        "records_by_source": by_source,
        "searches_recorded": n,
        "dedup_cleared": dedup_cleared,
        "warnings": warnings,
    }


@safe_tool
def prisma_record_dedup(
    review_id: ReviewId,
    duplicates_removed: Annotated[int, Field(description="Number of duplicate records removed before screening.")],
    method: Annotated[str, Field(description="How duplicates were found, e.g. 'EndNote + manual check by DOI and title'.")],
) -> dict[str, Any]:
    """Record a duplicate count you computed elsewhere (replaces any earlier count).

    Prefer dedup_papers, which computes and records the count from the records
    themselves. Use this tool only when deduplication was done outside this server.
    duplicates_removed cannot exceed the total hits recorded with prisma_record_search.

    Returns: duplicates_removed, records_identified, records_after_dedup, method.
    """
    n = int_arg(duplicates_removed, "duplicates_removed", min_value=0)
    method_clean = text_arg(method, "method", max_len=500)
    with store.review_db(review_id, write=True) as conn:
        identified = _save_dedup(conn, n, method_clean)
    return {
        "ok": True,
        "duplicates_removed": n,
        "records_identified": identified,
        "records_after_dedup": identified - n,
        "method": method_clean,
    }


@safe_tool
def dedup_papers(
    papers: Annotated[
        list[dict[str, Any]],
        Field(
            description=(
                "Every record exported from all searches (before deduplication), up to 10000. Each object may "
                "have: id (your own key, echoed back), doi, pmid, title, year, authors (list or 'A; B') or "
                "first_author, abstract, source."
            )
        ),
    ],
    review_id: Annotated[
        str | None,
        Field(
            description=(
                "If given, the duplicate count is recorded as this review's PRISMA dedup step, but only when you "
                "send every exported record in this one call (records_checked must equal the hits recorded with "
                "prisma_record_search). A partial or batched call is analysed but not recorded."
            )
        ),
    ] = None,
) -> dict[str, Any]:
    """Find duplicate records deterministically so the PRISMA duplicate count is computed, not guessed.

    Two records are the same paper when they share a DOI (case-insensitive, URL or
    'doi:' prefix ignored), a PMID, or the same normalised title (20+ characters)
    with the same year and first-author surname. Matching is transitive. Records
    with the same title but a different year or first author are NOT merged; they
    are listed in possible_duplicates for you to decide. In each cluster the record
    with the most complete metadata is kept (ties: earliest).

    Returns: records_checked, unique_records, duplicates_removed, removed_indices
    (0-based positions to drop), clusters (kept_index, kept_id, member_indices,
    matched_on, conflicting_dois; capped at 200), possible_duplicates (capped),
    truncated, recorded (true when saved to the review), warnings, note.

    A review has one dedup count, so it is recorded only when the call covers
    every exported record (records_checked equals the recorded hits); otherwise
    recorded is false and the previous record is kept.

    Does NOT fetch or merge metadata, and does not use fuzzy title matching.
    Before you discard the records at removed_indices, copy any field you still
    need (an abstract, a PMID or DOI, a full-text link) onto the kept record.
    """
    if not isinstance(papers, list) or not papers:
        fail("papers must be a non-empty list of record objects")
    if len(papers) > MAX_DEDUP_PAPERS:
        fail(
            f"papers has {len(papers)} records; the limit is {MAX_DEDUP_PAPERS} per call. Duplicates across "
            "separate calls would be missed, so deduplicate a larger set elsewhere and record the whole-review "
            "count with prisma_record_dedup."
        )
    for i, p in enumerate(papers):
        if not isinstance(p, dict):
            fail(f"papers[{i}] must be an object with doi, pmid, title, year and authors fields")
    result = dedup.find_duplicates(papers)
    removed = len(result["removed_indices"])
    clusters, cut_a = cap(result["clusters"])
    possible, cut_b = cap(result["possible_duplicates"], 50)
    warnings: list[str] = []
    no_key = sum(1 for p in papers if not (p.get("doi") or p.get("pmid") or dedup.title_key(p.get("title"))))
    if no_key:
        warnings.append(f"{no_key} record(s) have no DOI, PMID or title and cannot be matched.")

    recorded = False
    identity_counts = {"aliases_recorded": 0, "alias_conflicts": 0}
    if review_id is not None and str(review_id).strip():
        with store.review_db(review_id, write=True) as conn:
            identified, _, _ = search_totals(conn)
            if identified == 0:
                fail("no searches are recorded yet; record every search with prisma_record_search first")
            if len(papers) == identified:
                _save_dedup(conn, removed, f"{dedup.METHOD} ({len(papers)} records checked)")
                from citeproof_mcp.tools import identity

                identity_counts = identity.record_dedup(conn, papers, result)
                recorded = True
            else:
                # One review has one dedup count. A partial or batched call must never replace it.
                warnings.append(
                    f"{len(papers)} records were checked but the recorded searches total {identified} hits, so the "
                    "duplicate count was NOT recorded (the review keeps its previous dedup record). Send every "
                    f"exported record in one call. If the review has more than {MAX_DEDUP_PAPERS} records, or you "
                    "deduplicated elsewhere, record the whole-review count with prisma_record_dedup."
                )
    return {
        "ok": True,
        "records_checked": len(papers),
        "unique_records": len(papers) - removed,
        "duplicates_removed": removed,
        "removed_indices": result["removed_indices"],
        "clusters": clusters,
        "possible_duplicates": possible,
        "truncated": cut_a or cut_b,
        "recorded": recorded,
        "method": dedup.METHOD,
        "aliases_recorded": identity_counts["aliases_recorded"],
        "alias_conflicts": identity_counts["alias_conflicts"],
        "warnings": warnings,
        "note": (
            "Only the duplicate count is computed. Records are not merged: copy any field you need from the "
            "removed records onto the kept one before you discard them."
        ),
    }


@safe_tool
def screening_record(
    review_id: ReviewId,
    paper_ref: PaperRef,
    stage: Annotated[str, Field(description="'title_abstract' or 'full_text'.")],
    decision: Annotated[
        str,
        Field(description="'include' or 'exclude'; at full_text also 'not_retrieved' (full text could not be obtained)."),
    ],
    reason: Annotated[
        str | None,
        Field(description="Required for 'exclude': a short reusable label such as 'wrong population' (max 300 chars)."),
    ] = None,
    reviewer: Annotated[
        str,
        Field(description="Who decided (1-40 chars, stored lower-case). Use 'primary' for the decisions that count."),
    ] = "primary",
    title: Annotated[str | None, Field(description="Paper title, kept for the audit trail.")] = None,
) -> dict[str, Any]:
    """Record one screening decision for one paper. Re-sending the same paper_ref + stage + reviewer replaces it.

    Use for single decisions; use screening_record_many for batches. PRISMA counts
    use the reviewer named 'primary' (or the only reviewer). With two independent
    reviewers, record each under their own name, compare with screening_agreement,
    then record the consensus under 'primary'.

    Returns: updated, paper_ref (canonical form), stage, reviewer, decision,
    stage_counts (this reviewer's decisions so far at this stage), warnings
    (e.g. a full_text decision with no title_abstract include).

    Does NOT read the paper; the decision is yours.
    """
    d = _clean_decision(
        {"paper_ref": paper_ref, "stage": stage, "decision": decision, "reason": reason, "reviewer": reviewer, "title": title}
    )
    with store.review_db(review_id, write=True) as conn:
        updated, warnings = _upsert_decision(conn, d, store.now_iso())
        stage_counts = {
            r["decision"]: int(r["c"])
            for r in conn.execute(
                "SELECT decision, COUNT(*) AS c FROM screening WHERE reviewer = ? AND stage = ? GROUP BY decision",
                (d["reviewer"], d["stage"]),
            )
        }
    return {
        "ok": True,
        "updated": updated,
        "paper_ref": d["paper_ref"],
        "stage": d["stage"],
        "reviewer": d["reviewer"],
        "decision": d["decision"],
        "stage_counts": stage_counts,
        "warnings": warnings,
    }


@safe_tool
def screening_record_many(
    review_id: ReviewId,
    decisions: Annotated[
        list[dict[str, Any]],
        Field(
            description=(
                "Up to 500 objects, each with the screening_record fields: paper_ref, stage, decision, "
                "reason (required for exclude), reviewer (default 'primary'), title."
            )
        ),
    ],
) -> dict[str, Any]:
    """Record up to 500 screening decisions in one all-or-nothing transaction.

    Same rules as screening_record. If any item is invalid, or the same
    paper_ref + stage + reviewer appears twice, nothing is written and the result
    names the item: {ok: false, error, index}.

    Returns: count, inserted_count, updated_count, warning_count, warnings (first 50).
    """
    if not isinstance(decisions, list) or not decisions:
        fail("decisions must be a non-empty list of decision objects")
    if len(decisions) > MAX_BATCH:
        fail(f"decisions has {len(decisions)} items; the limit is {MAX_BATCH} per call")
    cleaned: list[dict[str, Any]] = []
    seen: dict[tuple[str, str, str], int] = {}
    for idx, item in enumerate(decisions):
        try:
            d = _clean_decision(item)
        except store.StoreError as exc:
            return {"ok": False, "error": f"Item at index {idx}: {exc}", "index": idx}
        key = (d["paper_ref"], d["stage"], d["reviewer"])
        if key in seen:
            return {
                "ok": False,
                "error": f"Item at index {idx} repeats item {seen[key]} (same paper_ref, stage and reviewer)",
                "index": idx,
            }
        seen[key] = idx
        cleaned.append(d)

    updated_count = 0
    warnings: list[str] = []
    now = store.now_iso()
    with store.review_db(review_id, write=True) as conn:
        for d in cleaned:
            updated, item_warnings = _upsert_decision(conn, d, now)
            updated_count += int(updated)
            warnings.extend(item_warnings)
    listed, _ = cap(warnings, 50)
    return {
        "ok": True,
        "count": len(cleaned),
        "inserted_count": len(cleaned) - updated_count,
        "updated_count": updated_count,
        "warning_count": len(warnings),
        "warnings": listed,
    }


@safe_tool
def screening_list(
    review_id: ReviewId,
    stage: Annotated[str | None, Field(description="Filter: 'title_abstract' or 'full_text'.")] = None,
    reviewer: Annotated[str | None, Field(description="Filter: reviewer name.")] = None,
    decision: Annotated[str | None, Field(description="Filter: 'include', 'exclude' or 'not_retrieved'.")] = None,
    paper_ref: Annotated[str | None, Field(description="Filter: one paper.")] = None,
    limit: Annotated[int, Field(description="Page size, 1 to 200.")] = 200,
    offset: Annotated[int, Field(description="Rows to skip, for paging.")] = 0,
) -> dict[str, Any]:
    """List recorded screening decisions, newest first, one page at a time.

    Returns: records (paper_ref, stage, reviewer, decision, reason, title,
    updated_at), count (rows in this page), total (rows matching the filters),
    truncated (more rows after this page), next_offset (pass it as offset to get
    the next page, or null).
    """
    clauses: list[str] = []
    params: list[Any] = []
    if stage is not None and str(stage).strip():
        clauses.append("stage = ?")
        params.append(choice_arg(stage, "stage", STAGES, STAGE_ALIASES))
    if reviewer is not None and str(reviewer).strip():
        clauses.append("reviewer = ?")
        params.append(reviewer_arg(reviewer))
    if decision is not None and str(decision).strip():
        clauses.append("decision = ?")
        params.append(choice_arg(decision, "decision", DECISIONS, DECISION_ALIASES))
    if paper_ref is not None and str(paper_ref).strip():
        clauses.append("paper_ref = ?")
        params.append(paper_ref_arg(paper_ref))
    page = int_arg(limit, "limit", min_value=1, max_value=LIST_CAP)
    skip = int_arg(offset, "offset", min_value=0)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    with store.review_db(review_id) as conn:
        total = int(conn.execute(f"SELECT COUNT(*) FROM screening {where}", params).fetchone()[0])
        rows = conn.execute(
            f"SELECT paper_ref, stage, reviewer, decision, reason, title, updated_at FROM screening {where} "
            "ORDER BY updated_at DESC, rowid DESC LIMIT ? OFFSET ?",
            params + [page, skip],
        ).fetchall()
    records = [dict(r) for r in rows]
    more = skip + len(records) < total
    return {
        "ok": True,
        "records": records,
        "count": len(records),
        "total": total,
        "truncated": more,
        "next_offset": skip + len(records) if more else None,
    }


@safe_tool
def screening_agreement(
    review_id: ReviewId,
    stage: Annotated[str, Field(description="'title_abstract' or 'full_text'.")],
    reviewer_a: Annotated[str, Field(description="First reviewer name.")],
    reviewer_b: Annotated[str, Field(description="Second reviewer name.")],
) -> dict[str, Any]:
    """Cohen's kappa between two reviewers' include/exclude decisions at one stage.

    Uses only papers both reviewers decided with include or exclude
    ('not_retrieved' pairs are counted in not_judged). kappa = (po - pe) / (1 - pe);
    it is null with interpretation 'undefined' when nothing is shared or both
    reviewers put every paper in the same category (pe = 1).

    Returns: shared, agree, table (2x2 counts), po, pe, kappa, interpretation
    (Landis and Koch bands: poor < 0 <= slight <= 0.20 < fair <= 0.40 < moderate
    <= 0.60 < substantial <= 0.80 < almost perfect), disagreement_count,
    disagreements (paper_ref, title, a, b, reason_a, reason_b; capped at 200),
    truncated, only_a and only_b (papers seen by one reviewer), not_judged, next_step.

    Does NOT resolve disagreements: decide each one and record it under 'primary'.
    """
    stage_clean = choice_arg(stage, "stage", STAGES, STAGE_ALIASES)
    rev_a = reviewer_arg(reviewer_a, "reviewer_a", default=None)
    rev_b = reviewer_arg(reviewer_b, "reviewer_b", default=None)
    if rev_a == rev_b:
        fail("reviewer_a and reviewer_b must be distinct reviewers")
    with store.review_db(review_id) as conn:
        result = agreement(conn, stage_clean, rev_a, rev_b)
    if result["disagreement_count"]:
        step = (
            f"Resolve the {result['disagreement_count']} disagreement(s) and record each consensus decision "
            "with reviewer='primary'."
        )
    elif result["only_a"] or result["only_b"]:
        step = "Have each reviewer decide the papers only the other one saw, then re-run this check."
    else:
        step = "No disagreements to resolve."
    return {"ok": True, **result, "next_step": step}


@safe_tool
def prisma_status(review_id: ReviewId, reviewer: ReviewerOpt = None) -> dict[str, Any]:
    """Compute the PRISMA 2020 flow counts from the recorded searches, dedup and screening decisions.

    Counting reviewer: the one you name, else 'primary', else the only reviewer;
    with several reviewers and no 'primary' the call fails and says so.
    'Reports not retrieved' counts full_text decisions 'not_retrieved'. Papers
    included at title_abstract with no full_text decision yet are full_text_pending,
    not 'not retrieved'.

    Returns: reviewer_used, the PRISMA counts (records_identified, records_by_source,
    duplicates_removed, records_after_dedup, records_screened, excluded_title_abstract,
    full_text_requested, full_text_unavailable, full_text_assessed, excluded_full_text,
    studies_included), exclusion_reasons (full-text reasons, as PRISMA reports them),
    exclusion_reasons_title_abstract, full_text_pending, records_awaiting_screening,
    searches_recorded, dedup_recorded, dedup_method, invariants_valid,
    invariant_errors (arithmetic that does not add up), warnings, complete (true when
    every record has a final decision and the arithmetic holds), next_steps.

    Does NOT change anything or guess missing numbers.
    """
    with store.review_db(review_id) as conn:
        return {"ok": True, **compute_status(conn, reviewer)}


@safe_tool
def prisma_flow_diagram(
    review_id: ReviewId,
    format: Annotated[str, Field(description="'mermaid' (flowchart text), 'svg' (standalone image) or 'markdown' (numbered list).")] = "mermaid",
    reviewer: ReviewerOpt = None,
) -> dict[str, Any]:
    """Render the PRISMA 2020 flow diagram from the counts prisma_status computes.

    Text output only; save or embed the `diagram` string yourself. Agent-supplied
    text (sources, exclusion reasons) is escaped for the chosen format. Shows the
    top full-text exclusion reasons.

    Returns: format, diagram, complete (false means the counts are still in progress
    or do not add up), warnings.
    """
    fmt = choice_arg(format, "format", tuple(prisma_render.DIAGRAM_BUILDERS))
    with store.review_db(review_id) as conn:
        status = compute_status(conn, reviewer)
    warnings = list(status["warnings"]) + list(status["invariant_errors"])
    if not status["complete"]:
        warnings.append("Counts are incomplete or inconsistent; see prisma_status next_steps before publishing.")
    return {
        "ok": True,
        "format": fmt,
        "diagram": prisma_render.DIAGRAM_BUILDERS[fmt](status),
        "complete": status["complete"],
        "warnings": warnings,
    }


@safe_tool
def prisma_checklist(
    review_id: ReviewId,
    report_text: Annotated[str, Field(description="The full report text (max 400000 characters).")],
    reviewer: ReviewerOpt = None,
) -> dict[str, Any]:
    """Keyword check of a report against the 27 PRISMA 2020 items, plus a check of the PRISMA numbers it states.

    Items: each is 'found' when one of its fixed phrases appears (item 1: the first
    line names a systematic review or meta-analysis). Numbers: sentences such as
    '1,234 records were identified' or '12 studies were included' are compared with
    the recorded counts; a mismatch may be a typo, or a sentence about a subset.

    Returns: found_count, total (27), items (item, section, topic, found, matched),
    missing (topics not found), numbers (field, reported, recorded, matches, snippet),
    number_mismatches, numbers_note, note.

    This is a keyword check, not a quality judgement: it shows which items are
    mentioned, not whether they are reported well. Nothing is saved.
    """
    text = size_arg(report_text, "report_text", max_len=MAX_TEXT_CHARS)
    items = prisma_render.checklist_items(text)
    numbers: list[dict[str, Any]] = []
    numbers_note: str | None = None
    with store.review_db(review_id) as conn:
        try:
            status = compute_status(conn, reviewer)
        except store.StoreError as exc:
            status = None
            numbers_note = f"numbers not checked: {exc}"
    if status is not None:
        numbers = prisma_render.number_checks(text, status)
        if not numbers:
            numbers_note = "no sentences stating PRISMA counts were recognised"
    return {
        "ok": True,
        "found_count": sum(1 for it in items if it["found"]),
        "total": len(items),
        "items": items,
        "missing": [it["topic"] for it in items if not it["found"]],
        "numbers": numbers,
        "number_mismatches": sum(1 for n in numbers if not n["matches"]),
        "numbers_note": numbers_note,
        "note": (
            "Keyword check of the report text. It shows which items are mentioned, "
            "not whether they are reported well."
        ),
    }


def register(server: MCPServer) -> None:
    for tool in (
        prisma_record_search,
        dedup_papers,
        prisma_record_dedup,
        screening_record,
        screening_record_many,
        screening_list,
        screening_agreement,
        prisma_status,
        prisma_flow_diagram,
        prisma_checklist,
    ):
        server.tool()(tool)
