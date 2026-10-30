# SPDX-License-Identifier: MIT
"""Whole-review views: review_status (what is done, what is missing, what to do next)
and export_bundle (every record in one reproducibility document).

This module owns no tables. It reads the other modules only through their public
read functions.
"""

import hashlib
import json
import sqlite3
from itertools import combinations
from typing import Annotated, Any

from pydantic import Field

from citeproof_mcp import __version__, store
from citeproof_mcp.compat import MCPServer
from citeproof_mcp.tools import (
    core,
    events,
    evidence_checks,
    extraction,
    identity,
    prisma,
    prisma_render,
    protocol,
    source_tracking,
    stats_checks,
)
from citeproof_mcp.tools.common import choice_arg, md_cell, md_table, safe_tool

ReviewId = Annotated[str, Field(description="The id returned by review_create.")]
ReviewerOpt = Annotated[
    str | None,
    Field(description="Whose screening decisions count for PRISMA. Default: 'primary', else the only reviewer."),
]

PHASES = (
    "protocol",
    "searching",
    "deduplication",
    "title_abstract_screening",
    "full_text_screening",
    "data_extraction",
    "risk_of_bias",
    "claim_checking",
    "reporting",
)
SAMPLE = 20
MAX_EXPORT_ROWS = 5000


def build_overview(conn: sqlite3.Connection, reviewer: str | None) -> dict[str, Any]:
    """Everything review_status returns except 'ok'."""
    meta = store.read_meta(conn)
    proto = protocol.protocol_state(conn)
    try:
        status: dict[str, Any] | None = prisma.compute_status(conn, reviewer)
        status_error = None
    except store.StoreError as exc:
        status, status_error = None, str(exc)
    included = prisma.included_refs(conn, reviewer)
    included_set = set(included)
    reviewers = prisma.reviewers_by_stage(conn)
    rows = extraction.evidence_rows(conn)
    rob = evidence_checks.rob_overview(conn)
    claims = evidence_checks.claims_summary(conn)
    dois = core.doi_check_rows(conn)

    # Coverage compares canonical refs, so a PMID decision and a DOI evidence row of one paper match.
    def canon(ref: str) -> str:
        return identity.resolve_ref(conn, ref)

    included_canon = {canon(p) for p in included}
    with_rows = {canon(r["paper_ref"]) for r in rows}
    no_rows = [p for p in included if canon(p) not in with_rows]
    rob_refs = {canon(p["paper_ref"]) for p in rob["papers"]}
    no_rob = [p for p in included if canon(p) not in rob_refs]
    rob_incomplete = [p["paper_ref"] for p in rob["papers"] if p["overall"] == "incomplete"]
    doi_by_ref = {d["doi"]: d for d in dois}
    retracted = [d["doi"] for d in dois if d["retracted"]]
    not_found = [d["doi"] for d in dois if d["status"] == "not_found"]
    included_dois = [p for p in included if p.startswith("10.")]
    unchecked = [p for p in included_dois if p not in doi_by_ref or doi_by_ref[p]["status"] == "unverified"]

    source_changes = source_tracking.source_changes_rows(conn)
    distinct_texts = source_tracking.distinct_source_texts_by_paper(conn)

    open_stages = prisma.open_blind_stages(conn)
    stages_conf = prisma.stage_settings(conn)
    screening_stages_info: dict[str, dict[str, Any]] = {}
    decisions = prisma.screening_rows(conn)
    for stage_name in prisma.STAGES:
        st_row = stages_conf.get(stage_name)
        mode = st_row["mode"] if st_row else "unblinded"
        expected = st_row["reviewers"] if st_row else []
        closed_at = st_row["closed_at"] if st_row else None
        decided_counts: dict[str, int] = {}
        for r in decisions:  # sorted by reviewer, so the counts keep that order
            if r["stage"] == stage_name:
                decided_counts[r["reviewer"]] = decided_counts.get(r["reviewer"], 0) + 1
        screening_stages_info[stage_name] = {
            "mode": mode,
            "expected_reviewers": expected,
            "decided_counts": decided_counts,
            "closed_at": closed_at,
        }

    warnings: list[str] = []
    if source_changes:
        newest_changes = source_changes[-SAMPLE:]
        for c in newest_changes:
            fmt_msg = " (formatting only)" if c["formatting_only"] else ""
            warnings.append(
                f"Source text changed for {c['tool']} record '{c['record_key']}' on {c['paper_ref']}: "
                f"sha256 {c['previous_sha256'][:6]}… → {c['current_sha256'][:6]}…{fmt_msg}."
            )
        if len(source_changes) > SAMPLE:
            omitted = len(source_changes) - SAMPLE
            warnings.append(
                f"{omitted} earlier source text change(s) are omitted here; export_bundle lists all changes."
            )
    steps: dict[str, list[str]] = {phase: [] for phase in PHASES}
    if not proto["complete"]:
        steps["protocol"].append(f"Complete the protocol with protocol_record: {', '.join(proto['missing_required'])}.")
    if status_error:
        warnings.append(status_error)
        # Consensus is refused at an open blind stage; the close step below comes first.
        if not open_stages:
            steps["title_abstract_screening"].append("Record consensus screening decisions under reviewer 'primary'.")
    if status is not None:
        warnings.extend(status["warnings"])
        warnings.extend(status["invariant_errors"])
        if not status["searches_recorded"]:
            steps["searching"].append("Run the planned searches and log each one with prisma_record_search.")
        elif not status["dedup_recorded"]:
            steps["deduplication"].append("Run dedup_papers over all exported records with review_id set.")
        if status["records_awaiting_screening"]:
            steps["title_abstract_screening"].append(
                f"Record title_abstract decisions for {status['records_awaiting_screening']} more record(s)."
            )
        if status["full_text_pending"]:
            steps["full_text_screening"].append(
                f"Record full_text decisions for {status['full_text_pending']} report(s) (include, exclude with reason, or not_retrieved)."
            )
        if status["invariant_errors"]:
            steps["full_text_screening"].append("Fix the screening decisions behind the PRISMA invariant errors.")
    for stage in prisma.STAGES:
        if stage in open_stages:
            steps[f"{stage}_screening"].append(
                f"Finish blind screening for stage {stage} and call screening_stage_close."
            )
        else:
            names = reviewers.get(stage, [])
            if len(names) >= 2:
                steps[f"{stage}_screening"].append(
                    f"Check agreement at {stage} with screening_agreement (reviewers: {', '.join(names[:5])})."
                )
    if (status and status["studies_included"]) and not any(len(n) >= 2 for n in reviewers.values()):
        warnings.append("Every screening decision is by one reviewer; report single-reviewer screening (PRISMA item 8).")
    screening_warns = prisma.screening_warnings(conn, stage=None)
    for sw in screening_warns:
        if sw not in warnings:
            warnings.append(sw)
    if no_rows:
        steps["data_extraction"].append(f"Record evidence rows with evidence_record for {len(no_rows)} included study(ies).")
    stray = sorted({r["paper_ref"] for r in rows if canon(r["paper_ref"]) not in included_canon}) if included else []
    if stray:
        warnings.append(f"{len(stray)} evidence row paper(s) are not included at full_text: {', '.join(stray[:5])}.")
    if no_rob:
        steps["risk_of_bias"].append(f"Assess risk of bias with rob_record for {len(no_rob)} included study(ies).")
    if rob_incomplete:
        steps["risk_of_bias"].append(f"Finish the missing domains for {len(rob_incomplete)} paper(s) (rob_summary lists them).")
    if unchecked:
        steps["claim_checking"].append(f"Check {len(unchecked)} included DOI(s) with verify_dois(review_id=...).")
    if retracted:
        hit = [d for d in retracted if d in included_set]
        warnings.append(
            f"{len(retracted)} checked DOI(s) are retracted"
            + (f", {len(hit)} of them included: {', '.join(hit[:5])}" if hit else "")
            + "."
        )
    if not_found:
        warnings.append(f"{len(not_found)} DOI(s) do not resolve (possible fabricated or mistyped citations).")
    if included and claims["total"] == 0:
        steps["claim_checking"].append("Record the report's key claims with claim_record and check them with claim_report.")
    if claims["suspicious_count"]:
        steps["claim_checking"].append(f"Re-check {claims['suspicious_count']} suspicious claim(s) (claim_report).")
    steps["reporting"].append(
        "Check the draft with prisma_checklist, resolve_citations and lint_prose, then save export_bundle."
    )

    phase = next(p for p in PHASES if steps[p])
    next_steps = [s for p in PHASES for s in steps[p]][:10]

    prisma_part: dict[str, Any]
    if status is None:
        prisma_part = {"error": status_error}
    else:
        prisma_part = {
            k: status[k]
            for k in (
                "reviewer_used", "records_identified", "duplicates_removed", "records_after_dedup",
                "records_screened", "records_awaiting_screening", "full_text_requested", "full_text_unavailable",
                "full_text_pending", "full_text_assessed", "studies_included", "invariants_valid", "complete",
            )
        }
    return {
        "review": {k: meta.get(k) for k in ("review_id", "title", "question", "year_min", "year_max", "created_at")},
        "phase": phase,
        "protocol": {k: proto[k] for k in ("complete", "missing_required", "missing_recommended")},
        "prisma": prisma_part,
        "screening_reviewers": reviewers,
        "screening_stages": screening_stages_info,
        "reviewers": prisma.reviewer_rows(conn),
        "included_studies": len(included),
        "studies": {k: v for k, v in identity.identity_summary(conn, included).items() if k.endswith("_count")},
        "extraction": {
            "rows": len(rows),
            "studies_with_rows": len(with_rows),
            "included_without_rows_count": len(no_rows),
            "included_without_rows": no_rows[:SAMPLE],
            "rows_quote_not_verified": sum(1 for r in rows if not r["quote_verified"]),
            "rows_numbers_not_in_quote": sum(1 for r in rows if r["numbers_not_in_quote"]),
        },
        "risk_of_bias": {
            "papers_assessed": len(rob["papers"]),
            "counts_by_overall": rob["counts_by_overall"],
            "included_without_rob_count": len(no_rob),
            "included_without_rob": no_rob[:SAMPLE],
            "incomplete": rob_incomplete[:SAMPLE],
            "papers_with_unverified_quotes": rob["papers_with_unverified_quotes"][:SAMPLE],
        },
        "claims": {k: claims[k] for k in ("total", "counts_by_label", "quotes_not_verified", "suspicious_count", "unsupported_count")},
        "dois": {
            "checked": len(dois),
            "retracted": retracted[:SAMPLE],
            "not_found": not_found[:SAMPLE],
            "unverified": sum(1 for d in dois if d["status"] == "unverified"),
            "included_dois_unchecked": len(unchecked),
        },
        "source_texts": {
            "distinct_by_paper": distinct_texts,
            "changes_count": len(source_changes),
        },
        "warnings": warnings,
        "next_steps": next_steps,
    }


@safe_tool
def review_status(review_id: ReviewId, reviewer: ReviewerOpt = None) -> dict[str, Any]:
    """One-call overview of a review: what is recorded, what is missing, warnings and the next steps in order.

    Always pass warnings (such as human screening caution and same-model warnings)
    to the user.

    Call at the start of a session (to pick up where the review stopped) and before
    writing the report. It reads every record; it never changes anything.

    Returns: review (title, question, years), phase (first unfinished step:
    protocol, searching, deduplication, title_abstract_screening,
    full_text_screening, data_extraction, risk_of_bias, claim_checking, reporting),
    protocol (complete, missing fields), prisma (key counts, complete, or error),
    screening_reviewers (per stage), screening_stages, reviewers, included_studies,
    extraction (rows and gaps), risk_of_bias (coverage of included studies),
    claims (counts), dois (checked, retracted, not_found, included DOIs not yet
    checked), warnings, next_steps (at most 10, in workflow order). Lists of
    paper_refs show the first 20.
    """
    with store.review_db(review_id) as conn:
        return {"ok": True, **build_overview(conn, reviewer)}


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


def _collect(conn: sqlite3.Connection, reviewer: str | None) -> dict[str, Any]:
    overview = build_overview(conn, reviewer)
    try:
        status: dict[str, Any] = prisma.compute_status(conn, reviewer)
        diagram = prisma_render.build_mermaid(status)
    except store.StoreError as exc:
        status, diagram = {"error": str(exc)}, None
    open_stages = prisma.open_blind_stages(conn)
    all_screening = prisma.screening_rows(conn)
    if open_stages:
        visible_screening = [r for r in all_screening if r["stage"] not in open_stages]
    else:
        visible_screening = all_screening
    claims = evidence_checks.claim_rows(conn)
    stages = prisma.reviewers_by_stage(conn)
    agreements = []
    for stage, names in stages.items():
        if stage in open_stages:
            continue
        for a, b in list(combinations(names, 2))[:5]:
            result = prisma.agreement(conn, stage, a, b)
            result["disagreements"] = result["disagreements"][:50]
            agreements.append(result)
    rob = evidence_checks.rob_overview(conn)
    source_changes = source_tracking.source_changes_rows(conn)
    distinct_texts = source_tracking.distinct_source_texts_by_paper(conn)
    screening_warns = prisma.screening_warnings(conn, stage=None)
    aliases = identity.alias_rows(conn)
    stats_rows = stats_checks.stats_check_rows(conn)
    return {
        "bundle_version": 1,
        "server_version": __version__,
        "generated_at": store.now_iso(),
        "review": store.read_meta(conn),
        "status": {k: overview[k] for k in ("phase", "warnings", "next_steps")},
        "protocol": protocol.get_protocol(conn),
        "protocol_changes": protocol.protocol_changes(conn),
        "searches": prisma.search_log(conn),
        "dedup": prisma.dedup_record(conn),
        "prisma": status,
        "prisma_flow_mermaid": diagram,
        "screening_decisions": visible_screening[:MAX_EXPORT_ROWS],
        "screening_decisions_total": len(visible_screening),
        "screening_stages": overview.get("screening_stages", {}),
        "reviewers": prisma.reviewer_rows(conn),
        "screening_warnings": screening_warns,
        "agreement": agreements,
        "evidence_table": extraction.evidence_rows(conn),
        "risk_of_bias": {"papers": rob["papers"], "domain_records": evidence_checks.rob_rows(conn), "rules": evidence_checks.OVERALL_RULES},
        "claims": claims[:MAX_EXPORT_ROWS],
        "claims_total": len(claims),
        "doi_checks": core.doi_check_rows(conn),
        "source_changes": source_changes,
        "distinct_source_texts": distinct_texts,
        "studies": identity.identity_summary(conn, prisma.included_refs(conn, reviewer)),
        "paper_aliases": aliases[:MAX_EXPORT_ROWS],
        "recall_check": identity.latest_recall_check(conn),
        "stats_checks": stats_rows[:MAX_EXPORT_ROWS],
        "events": events.event_summary(conn),
        "truncated": any(len(rows) > MAX_EXPORT_ROWS for rows in (visible_screening, claims, aliases, stats_rows)),
    }


def _t(value: Any, limit: int = 2000) -> str:
    """Agent text outside a table: one line, escaped like a table cell (no raw HTML, no new headings)."""
    return md_cell(value, limit)


def _markdown(b: dict[str, Any]) -> str:
    meta, proto = b["review"], b["protocol"]
    out: list[str] = [
        f"# Review audit bundle: {_t(meta.get('title', ''), 500)}",
        "",
        f"- Review id: {_t(meta.get('review_id'))}",
        f"- Question: {_t(meta.get('question'))}",
        f"- Publication years: {proto.get('year_min') or 'any'} to {proto.get('year_max') or 'any'}",
        f"- Created: {meta.get('created_at')}; bundle generated: {b['generated_at']}",
        f"- Phase: {b['status']['phase']}",
        "",
        ("All judgements in this bundle were made by the reviewing agent. The server stored them, checked quotes "
        "against the source text supplied with them, and computed the counts."),
        "",
        *(
            [
                (
                    f"**Warning: this bundle is incomplete.** Screening decisions, claims, paper aliases and "
                    f"statistical checks are each capped at {MAX_EXPORT_ROWS} rows, and at least one list was cut. "
                    "Keep the review database file with this bundle."
                ),
                "",
            ]
            if b["truncated"]
            else []
        ),
        "## Protocol",
        "",
    ]
    for field in protocol.ALL_FIELDS:
        value = proto.get(field)
        shown = "; ".join(value) if isinstance(value, list) else value
        out.append(f"- **{field}**: {_t(shown) or '(not recorded)'}")
    if b["protocol_changes"]:
        amended = [c for c in b["protocol_changes"] if c["old"] is not None]
        out += ["", f"Protocol amendments: {len(amended)}"]
        out += [
            f"- {c['changed_at']} {_t(c['field'], 100)}: "
            f"{_t(json.dumps(c['old'], ensure_ascii=False))} -> {_t(json.dumps(c['new'], ensure_ascii=False))}"
            for c in amended[:100]
        ]
    out += ["", "## Searches", ""]
    out.append(
        md_table(["Source", "Date run", "Hits", "Query"], [[s["source"], s["date_run"], s["hits"], s["query"]] for s in b["searches"]], 2000)
        if b["searches"] else "No searches recorded."
    )
    dedup = b["dedup"]
    out += [
        "",
        "## Deduplication",
        "",
        f"{dedup['duplicates_removed']} duplicates removed ({_t(dedup['method'], 300)})." if dedup else "Not recorded.",
    ]
    out += ["", "## PRISMA 2020 flow", ""]
    status = b["prisma"]
    if "error" in status:
        out.append(f"Counts not computed: {_t(status['error'])}")
    else:
        out.append(prisma_render.build_markdown(status))
        out += ["", f"Invariants valid: {status['invariants_valid']}"]
        out += [f"- {_t(e)}" for e in status["invariant_errors"]]
        out += ["", "```mermaid", b["prisma_flow_mermaid"], "```"]
    out += ["", "## Screening decisions", ""]
    screening_warns = b.get("screening_warnings", [])
    if screening_warns:
        out += ["### Screening cautions", ""]
        out += [f"- Warning: {_t(w)}" for w in screening_warns]
        out += [""]

    stages_info = b.get("screening_stages", {})
    blind_stages = {s: info for s, info in stages_info.items() if info.get("mode") == "blind"}
    if blind_stages:
        out += ["### Screening stage settings", ""]
        for s_name, s_info in sorted(blind_stages.items()):
            closed = s_info.get("closed_at")
            state_str = f"closed at {closed}" if closed else "open (blinded)"
            exp_str = ", ".join(s_info.get("expected_reviewers", [])) or "none"
            dec_str = ", ".join(f"{k}: {v}" for k, v in sorted(s_info.get("decided_counts", {}).items())) or "none"
            out.append(
                f"- **{s_name}**: mode={s_info['mode']} ({state_str}); expected reviewers: {_t(exp_str)}; "
                f"decided counts: {_t(dec_str)}"
            )
        out += [""]

    rev_rows = b.get("reviewers", [])
    if rev_rows:
        out += ["### Reviewers", ""]
        out.append(
            md_table(
                ["Reviewer", "Type", "Model ID", "Run ref"],
                [[r["name"], r["reviewer_type"], r.get("model_id") or "", r.get("run_ref") or ""] for r in rev_rows],
            )
        )
        out += [""]

    shown = b["screening_decisions"]
    out.append(f"{b['screening_decisions_total']} decisions" + (f" (first {len(shown)} shown)" if len(shown) < b["screening_decisions_total"] else "") + ".")
    if shown:
        out += ["", md_table(["Paper", "Stage", "Reviewer", "Decision", "Reason"], [[r["paper_ref"], r["stage"], r["reviewer"], r["decision"], r["reason"]] for r in shown])]
    if b["agreement"]:
        out += ["", "### Reviewer agreement", ""]
        out.append(
            md_table(
                ["Stage", "Reviewers", "Shared", "Kappa", "Interpretation", "Disagreements"],
                [[a["stage"], f"{a['reviewer_a']} / {a['reviewer_b']}", a["shared"], a["kappa"], a["interpretation"], a["disagreement_count"]] for a in b["agreement"]],
            )
        )
    out += ["", "## Evidence table", ""]
    out.append(extraction.to_markdown(b["evidence_table"]) if b["evidence_table"] else "No evidence rows recorded.")
    out += ["", "## Risk of bias", ""]
    papers = b["risk_of_bias"]["papers"]
    if papers:
        out.append(
            md_table(
                ["Paper", "Tool", "Domains", "Overall", "Notes"],
                [[p["paper_ref"], p["tool"], "; ".join(f"{d}: {j}" for d, j in p["domains"].items()), p["overall"], " ".join(p["notes"])] for p in papers],
                600,
            )
        )
    else:
        out.append("No risk-of-bias assessments recorded.")
    out += ["", "## Claim checks", ""]
    if b["claims"]:
        out.append(
            md_table(
                ["Claim", "Paper", "Label", "Quote verified", "Reason"],
                [[c["claim_text"], c["paper_ref"], c["label"], c["quote_verified"], c["reason"]] for c in b["claims"]],
            )
        )
    else:
        out.append("No claims recorded.")
    out += ["", "## DOI checks", ""]
    if b["doi_checks"]:
        out.append(
            md_table(
                ["DOI", "Status", "Retracted", "Title", "Checked"],
                [[d["doi"], d["status"], d["retracted"], d["title"], d["checked_at"]] for d in b["doi_checks"]],
            )
        )
    else:
        out.append("No DOI checks saved.")
    out += ["", "## Source text changes", ""]
    if b.get("source_changes"):
        out.append(
            md_table(
                ["Paper", "Tool", "Record key", "Previous SHA-256", "Current SHA-256", "Prev len", "Curr len", "Formatting only", "Changed at"],
                [
                    [
                        c["paper_ref"],
                        c["tool"],
                        c["record_key"],
                        c["previous_sha256"][:10] + "…",
                        c["current_sha256"][:10] + "…",
                        c["previous_length"],
                        c["current_length"],
                        c["formatting_only"],
                        c["changed_at"],
                    ]
                    for c in b["source_changes"]
                ],
            )
        )
    else:
        out.append("No source text changes recorded.")

    distinct_texts = b.get("distinct_source_texts", {})
    if distinct_texts:
        out += ["", "### Distinct source texts per paper", ""]
        out.append(
            md_table(
                ["Paper", "Distinct source texts"],
                [[paper_ref, count] for paper_ref, count in distinct_texts.items()],
            )
        )

    studies = b["studies"]
    out += ["", "## Reports and studies", ""]
    out.append(f"{studies['report_count']} included report(s) from {studies['study_count']} study(ies).")
    if studies["groups"]:
        out.append(
            md_table(
                ["Study", "Reports", "Linked"],
                [[g["study_id"], "; ".join(g["reports"]), "no" if g["unlinked"] else "yes"] for g in studies["groups"]],
            )
        )

    recall = b["recall_check"]
    out += ["", "## Known-item recall", ""]
    if recall:
        c = recall["counts"]
        out.append(
            f"Checked {recall['checked_at']}: {c['found']} found, {c['not_found']} not found, "
            f"{c['unresolved']} unresolved (recall {recall['recall']}). Seed recall does not show the search was exhaustive."
        )
    else:
        out.append("No known-item recall check recorded.")

    out += ["", "## Statistical consistency checks", ""]
    if b["stats_checks"]:
        out.append(
            md_table(
                ["Check", "Paper", "Label", "Status", "Checked at"],
                [[s["kind"], s["paper_ref"] or "", s["label"] or "", s["status"], s["checked_at"]] for s in b["stats_checks"]],
            )
        )
    else:
        out.append("No statistical checks recorded.")

    ev = b["events"]
    out += ["", "## Change history", ""]
    out.append(
        f"{ev['count']} recorded event(s), last seq {ev['last_seq']}, baseline {ev['baseline_at']}. "
        "Use review_history for the full log."
    )

    out += ["", "## Open items", ""]
    out += [f"- Warning: {_t(w)}" for w in b["status"]["warnings"]]
    out += [f"- Next: {_t(s)}" for s in b["status"]["next_steps"]]
    return "\n".join(out) + "\n"


@safe_tool
def export_bundle(
    review_id: ReviewId,
    format: Annotated[str, Field(description="'markdown' (readable report appendix) or 'json' (complete machine-readable record).")] = "markdown",
    reviewer: ReviewerOpt = None,
    save: Annotated[
        bool,
        Field(description="Also write the document to <CITEPROOF_MCP_HOME>/exports/ and return its path and SHA-256."),
    ] = False,
) -> dict[str, Any]:
    """Export the whole audit trail as one text document for the review's supplementary material.

    Always pass warnings (such as human screening caution and same-model warnings)
    to the user.

    Contains: review metadata, protocol and amendments, search log, deduplication,
    PRISMA counts and invariants with a Mermaid flow diagram, every screening
    decision (up to 5000), reviewer agreement for each reviewer pair, evidence
    table, risk-of-bias judgements with the overall rules, claim checks, saved DOI
    checks, reports-to-studies grouping and aliases, the latest known-item recall
    check, saved statistical checks, the change-history summary, and the open
    warnings and next steps. Source texts are not included (only their lengths and
    hashes, in json).

    Returns: format, content (the document), chars, sha256 (of the UTF-8 content),
    suggested_filename, saved_path (with save=true, else null; an existing file of
    the same name is replaced), truncated (more than 5000 screening decisions,
    claims, paper aliases or stats checks), phase.
    """
    fmt = choice_arg(format, "format", ("markdown", "json"))
    with store.review_db(review_id) as conn:
        bundle = _collect(conn, reviewer)
    content = json.dumps(bundle, indent=1, ensure_ascii=False, default=str) if fmt == "json" else _markdown(bundle)
    data = content.encode("utf-8")
    filename = f"{review_id}-audit-bundle.{'json' if fmt == 'json' else 'md'}"
    saved_path = None
    if save:
        # review_id already passed REVIEW_ID_RE inside review_db, so the name cannot leave the folder.
        folder = store.data_dir() / "exports"
        folder.mkdir(exist_ok=True)
        (folder / filename).write_bytes(data)
        saved_path = str(folder / filename)
    return {
        "ok": True,
        "format": fmt,
        "content": content,
        "chars": len(content),
        "sha256": hashlib.sha256(data).hexdigest(),
        "suggested_filename": filename,
        "saved_path": saved_path,
        "truncated": bundle["truncated"],
        "phase": bundle["status"]["phase"],
    }


def register(server: MCPServer) -> None:
    server.tool()(review_status)
    server.tool()(export_bundle)
