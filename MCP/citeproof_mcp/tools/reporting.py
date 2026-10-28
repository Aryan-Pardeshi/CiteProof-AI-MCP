# SPDX-License-Identifier: MIT
"""Reporting and manuscript consistency checking tools.

Tables owned here: none. This module reads data owned by other modules through
their public read functions: prisma (screening_rows, search_log, reviewers_by_stage,
compute_status, resolve_reviewer), extraction (evidence_rows), evidence_checks
(rob_rows), and protocol (get_protocol).
"""

import re
import sqlite3
from collections import defaultdict
from typing import Annotated, Any

from pydantic import Field

from citeproof_mcp import store
from citeproof_mcp.compat import MCPServer
from citeproof_mcp.tools import (
    evidence_checks,
    extraction,
    prisma,
    prisma_render,
    protocol,
)
from citeproof_mcp.tools.common import (
    LIST_CAP,
    MAX_TEXT_CHARS,
    cap,
    choice_arg,
    fail,
    int_arg,
    md_table,
    safe_tool,
)

ReviewId = Annotated[str, Field(description="The id returned by review_create.")]
ReviewerOpt = Annotated[
    str | None,
    Field(description="Whose decisions to count. Default: 'primary' if that reviewer exists, else the only reviewer."),
]
SectionsArg = Annotated[
    Any,
    Field(description="Manuscript sections as a mapping of section_name to text (1 to 20 sections, each up to 400,000 characters)."),
]

# ---------------------------------------------------------------------------
# Helper functions: string normalization, citation formatting, number matching
# ---------------------------------------------------------------------------


def format_citation(paper_ref: str, title: str | None) -> str:
    """Format citation for an excluded study: title, else DOI URL, else 'title not recorded'."""
    clean_title = (title or "").strip()
    if clean_title:
        return clean_title
    doi = store.normalize_doi(paper_ref)
    if doi:
        return f"https://doi.org/{doi}"
    return "title not recorded"


def parse_reasons(raw_reason: str | None) -> tuple[list[str], bool]:
    """Split exclusion reasons on ';' and normalize identically to prisma._reason_counts.

    Returns (reasons_list, reason_missing_bool).
    """
    if raw_reason is None or not str(raw_reason).strip():
        return [], True
    parts = str(raw_reason).split(";")
    reasons: list[str] = []
    for p in parts:
        clean = re.sub(r"\s+", " ", p.strip().lower())
        if clean and clean not in reasons:
            reasons.append(clean)
    if not reasons:
        return [], True
    return reasons, False


def find_line_and_excerpt(text: str, start: int, end: int) -> tuple[int, str]:
    """1-based line number and excerpt of at most 160 characters around a match."""
    line = text[:start].count("\n") + 1
    margin = max(0, (160 - (end - start)) // 2)
    lo = max(0, start - margin)
    hi = min(len(text), end + margin)
    snippet = re.sub(r"\s+", " ", text[lo:hi]).strip()
    return line, snippet[:160]


def get_decimal_tolerance(num_str: str) -> float:
    """Rounding tolerance: half a unit in the last stated decimal."""
    clean = num_str.replace("−", "-").strip()
    if "." in clean:
        decimals = len(clean.split(".")[1])
    else:
        decimals = 0
    return 0.5 * (10 ** (-decimals))


def number_matches(stated_str: str, recorded_val: float | None) -> bool:
    """Check if stated number matches recorded float/int within decimal rounding tolerance."""
    if recorded_val is None:
        return False
    clean = stated_str.replace("−", "-").strip()
    try:
        stated_val = float(clean)
    except ValueError:
        return False
    tol = get_decimal_tolerance(clean)
    return abs(stated_val - float(recorded_val)) <= (tol + 1e-9)


# ---------------------------------------------------------------------------
# Effect estimate extraction patterns
# ---------------------------------------------------------------------------

MEASURE_NORM: dict[str, str] = {
    "relative risk": "rr",
    "relative risks": "rr",
    "risk ratio": "rr",
    "risk ratios": "rr",
    "odds ratio": "or",
    "odds ratios": "or",
    "hazard ratio": "hr",
    "hazard ratios": "hr",
    "mean difference": "md",
    "mean differences": "md",
    "standardised mean difference": "smd",
    "standardised mean differences": "smd",
    "standardized mean difference": "smd",
    "standardized mean differences": "smd",
    "hedges' g": "smd",
    "hedges’ g": "smd",
    "hedges g": "smd",
    "risk difference": "rd",
    "risk differences": "rd",
    "incidence rate ratio": "irr",
    "incidence rate ratios": "irr",
    "rate ratio": "irr",
    "rate ratios": "irr",
    "rr": "rr",
    "or": "or",
    "hr": "hr",
    "md": "md",
    "smd": "smd",
    "rd": "rd",
    "irr": "irr",
}

EFFECT_REGEX = re.compile(
    # Spelled-out names ignore case; abbreviations must be upper case so prose "or"/"md" never match.
    r"\b(?P<measure>(?i:standardi[sz]ed\s+mean\s+differences?|hedges['’]?\s*g|"
    r"relative\s+risks?|risk\s+ratios?|odds\s+ratios?|hazard\s+ratios?|"
    r"mean\s+differences?|risk\s+differences?|incidence\s+rate\s+ratios?|rate\s+ratios?)|"
    r"RR|OR|HR|MD|SMD|RD|IRR)\b"
    r"\s*(?:=|:|\bof\b)?\s*"
    r"(?P<estimate>[-−]?\d+(?:\.\d+)?)"
    r"\s*(?:,|;)?\s*"
    r"(?:[\(\[]\s*)?"
    r"(?:(?:90%|95%|99%)\s+)?(?:CI|confidence\s+intervals?)\s*(?:=|:|,)?\s*"
    r"(?P<lower>[-−]?\d+(?:\.\d+)?)"
    r"\s*(?:to|–|—|-|,)\s*"
    r"(?P<upper>[-−]?\d+(?:\.\d+)?)\s*[\)\]]?"
)


def find_effect_estimates(text: str) -> list[dict[str, Any]]:
    """Extract '<measure> <estimate> (95% CI <l> to <u>)' effect mentions from text."""
    results: list[dict[str, Any]] = []
    for m in EFFECT_REGEX.finditer(text):
        raw_meas = m.group("measure")
        meas_code = MEASURE_NORM.get(re.sub(r"\s+", " ", raw_meas.lower().strip()))
        if not meas_code:
            continue
        line, excerpt = find_line_and_excerpt(text, m.start(), m.end())
        results.append({
            "measure": meas_code,
            "raw_measure": raw_meas,
            "estimate": m.group("estimate"),
            "lower": m.group("lower"),
            "upper": m.group("upper"),
            "start": m.start(),
            "end": m.end(),
            "line": line,
            "excerpt": excerpt,
        })
    return results


# ---------------------------------------------------------------------------
# Methods section search patterns
# ---------------------------------------------------------------------------

DB_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("PubMed", re.compile(r"\bpubmed\b", re.IGNORECASE)),
    ("MEDLINE", re.compile(r"\bmedline\b", re.IGNORECASE)),
    ("Embase", re.compile(r"\bembase\b", re.IGNORECASE)),
    ("CENTRAL / Cochrane Library", re.compile(r"(?i:\bcochrane\s+library\b|\bcochrane\s+central\b)|\bCENTRAL\b")),
    ("Web of Science", re.compile(r"\bweb\s+of\s+science\b|\bwos\b", re.IGNORECASE)),
    ("Scopus", re.compile(r"\bscopus\b", re.IGNORECASE)),
    ("CINAHL", re.compile(r"\bcinahl\b", re.IGNORECASE)),
    ("PsycINFO", re.compile(r"\bpsycinfo\b", re.IGNORECASE)),
    ("ClinicalTrials.gov", re.compile(r"\bclinicaltrials(?:\.gov)?\b", re.IGNORECASE)),
    ("WHO ICTRP", re.compile(r"\bwho\s+ictrp\b|\bictrp\b", re.IGNORECASE)),
    ("Google Scholar", re.compile(r"\bgoogle\s+scholar\b", re.IGNORECASE)),
    ("LILACS", re.compile(r"\blilacs\b", re.IGNORECASE)),
    ("Epistemonikos", re.compile(r"\bepistemonikos\b", re.IGNORECASE)),
]


def normalize_db_name(raw: str) -> str:
    """Normalize a database name to its canonical name."""
    low = raw.strip().lower()
    if "cochrane" in low or "central" in low:
        return "CENTRAL / Cochrane Library"
    if "clinicaltrials" in low:
        return "ClinicalTrials.gov"
    if "ictrp" in low:
        return "WHO ICTRP"
    if "pubmed" in low:
        return "PubMed"
    if "medline" in low:
        return "MEDLINE"
    if "embase" in low:
        return "Embase"
    if "web of science" in low or low == "wos":
        return "Web of Science"
    if "scopus" in low:
        return "Scopus"
    if "cinahl" in low:
        return "CINAHL"
    if "psycinfo" in low:
        return "PsycINFO"
    if "google scholar" in low:
        return "Google Scholar"
    if "lilacs" in low:
        return "LILACS"
    if "epistemonikos" in low:
        return "Epistemonikos"
    return raw.strip()


def find_named_databases(text: str) -> list[dict[str, Any]]:
    """Find mentions of recognized databases with line and excerpt."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for db_name, pat in DB_PATTERNS:
        m = pat.search(text)
        if m and db_name not in seen:
            seen.add(db_name)
            line, excerpt = find_line_and_excerpt(text, m.start(), m.end())
            out.append({"name": db_name, "line": line, "excerpt": excerpt})
    return out


MONTH_MAP: dict[str, str] = {
    "january": "01", "jan": "01",
    "february": "02", "feb": "02",
    "march": "03", "mar": "03",
    "april": "04", "apr": "04",
    "may": "05",
    "june": "06", "jun": "06",
    "july": "07", "jul": "07",
    "august": "08", "aug": "08",
    "september": "09", "sep": "09", "sept": "09",
    "october": "10", "oct": "10",
    "november": "11", "nov": "11",
    "december": "12", "dec": "12",
}

DATE_PATTERNS = [
    # ISO date: 2026-01-15
    re.compile(r"\b(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})\b"),
    # D Month YYYY: 15 January 2026
    re.compile(r"\b(?P<day>\d{1,2})\s+(?P<month>January|February|March|April|May|June|July|August|September|October|November|December|Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(?P<year>\d{4})\b", re.IGNORECASE),
    # Month D, YYYY: January 15, 2026
    re.compile(r"\b(?P<month>January|February|March|April|May|June|July|August|September|October|November|December|Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(?P<day>\d{1,2}),?\s+(?P<year>\d{4})\b", re.IGNORECASE),
    # Month YYYY: January 2026
    re.compile(r"\b(?P<month>January|February|March|April|May|June|July|August|September|October|November|December|Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(?P<year>\d{4})\b", re.IGNORECASE),
]


def find_search_dates(text: str) -> list[dict[str, Any]]:
    """Find stated search dates (ISO, D Month YYYY, Month YYYY) with line and excerpt."""
    out: list[dict[str, Any]] = []
    seen_spans: set[tuple[int, int]] = set()
    for pat in DATE_PATTERNS:
        for m in pat.finditer(text):
            span = (m.start(), m.end())
            if any(s <= span[0] and span[1] <= e for s, e in seen_spans):
                continue
            seen_spans.add(span)
            gd = m.groupdict()
            year = gd.get("year", "")
            raw_month = gd.get("month", "").lower()
            month = MONTH_MAP.get(raw_month, raw_month)
            day = gd.get("day")
            if day and day.isdigit():
                day_str = f"{int(day):02d}"
                iso_date = f"{year}-{month}-{day_str}"
            else:
                iso_date = None
            month_str = f"{year}-{month}"
            line, excerpt = find_line_and_excerpt(text, m.start(), m.end())
            out.append({
                "stated_text": m.group(0),
                "iso_date": iso_date,
                "month_str": month_str,
                "line": line,
                "excerpt": excerpt,
            })
    return out


REVIEWER_WORDS: dict[str, int] = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}

REVIEWER_COUNT_RE = re.compile(
    r"\b(?P<count>one|two|three|four|five|six|seven|eight|nine|ten|\d+)\s+"
    r"(?:independent\s+)?(?:review\s+authors?|reviewers?|screeners?)\b",
    re.IGNORECASE,
)


def find_reviewer_counts(text: str) -> list[dict[str, Any]]:
    """Find reviewer count mentions (e.g. 'two reviewers', '3 independent reviewers')."""
    out: list[dict[str, Any]] = []
    for m in REVIEWER_COUNT_RE.finditer(text):
        raw_cnt = m.group("count").lower()
        cnt = REVIEWER_WORDS.get(raw_cnt)
        if cnt is None and raw_cnt.isdigit():
            cnt = int(raw_cnt)
        if cnt is not None:
            line, excerpt = find_line_and_excerpt(text, m.start(), m.end())
            out.append({
                "stated_count": cnt,
                "raw_text": m.group(0),
                "line": line,
                "excerpt": excerpt,
            })
    return out


ROB_PATTERNS: list[tuple[str, str, re.Pattern[str]]] = [
    ("RoB 2", "rob2", re.compile(r"\brob\s*2\b|\bcochrane\s+risk\s+of\s+bias\s+2\b", re.IGNORECASE)),
    ("RoB 1", "rob1", re.compile(r"\brob\s*1\b|\bcochrane\s+risk\s+of\s+bias\b(?!\s*(?:tool\s*)?(?:2|ii|version\s*2)\b)(?:\s+tool)?\b", re.IGNORECASE)),
    ("ROBINS-I", "robins_i", re.compile(r"\brobins[- ]i\b", re.IGNORECASE)),
    ("ROBINS-E", "robins_e", re.compile(r"\brobins[- ]e\b", re.IGNORECASE)),
    ("Newcastle-Ottawa", "nos", re.compile(r"(?i:\bnewcastle[- ]ottawa\b)|\bNOS\b")),
    ("QUADAS-2", "quadas_2", re.compile(r"\bquadas[- ]2\b", re.IGNORECASE)),
    ("JBI", "jbi", re.compile(r"\bJBI\b|(?i:\bjoanna\s+briggs\b)")),
]


def normalize_rob_tool(tool_str: str) -> tuple[str, str]:
    """Return (display_name, tool_key) for a risk of bias tool string."""
    low = tool_str.strip().lower()
    if "robins_e" in low or "robins-e" in low:
        return "ROBINS-E", "robins_e"
    if "robins" in low:
        return "ROBINS-I", "robins_i"
    if "rob2" in low or "rob 2" in low:
        return "RoB 2", "rob2"
    if "rob1" in low or "rob 1" in low:
        return "RoB 1", "rob1"
    if "nos" in low or "newcastle" in low:
        return "Newcastle-Ottawa", "nos"
    if "quadas" in low:
        return "QUADAS-2", "quadas_2"
    if "jbi" in low:
        return "JBI", "jbi"
    return tool_str.strip(), tool_str.strip().lower()


def find_rob_tools(text: str) -> list[dict[str, Any]]:
    """Find named risk-of-bias tools with line and excerpt."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for display_name, key, pat in ROB_PATTERNS:
        m = pat.search(text)
        if m and key not in seen:
            seen.add(key)
            line, excerpt = find_line_and_excerpt(text, m.start(), m.end())
            out.append({
                "display_name": display_name,
                "key": key,
                "raw_text": m.group(0),
                "line": line,
                "excerpt": excerpt,
            })
    return out


SYNTHESIS_RE = re.compile(
    r"\brandom[- ]effects?(?:\s+models?|\s+meta[- ]analys[ie]s)?\b|"
    r"\bfixed[- ]effects?(?:\s+models?|\s+meta[- ]analys[ie]s)?\b|"
    r"\bnarrative\s+synthesis\b",
    re.IGNORECASE,
)


def synthesis_classes(text: str) -> set[str]:
    """Synthesis model types named in text: 'random', 'fixed' and/or 'narrative'."""
    low = text.lower()
    return {
        name
        for name, pattern in (("random", r"\brandom[- ]effects?\b"), ("fixed", r"\bfixed[- ]effects?\b"), ("narrative", r"\bnarrative\b"))
        if re.search(pattern, low)
    }


def find_synthesis_methods(text: str) -> list[dict[str, Any]]:
    """Find synthesis methods stated in text."""
    out: list[dict[str, Any]] = []
    for m in SYNTHESIS_RE.finditer(text):
        line, excerpt = find_line_and_excerpt(text, m.start(), m.end())
        out.append({
            "stated_text": m.group(0),
            "line": line,
            "excerpt": excerpt,
        })
    return out


# ---------------------------------------------------------------------------
# Tool 1: prisma_excluded_studies
# ---------------------------------------------------------------------------


@safe_tool
def prisma_excluded_studies(
    review_id: ReviewId,
    reviewer: ReviewerOpt = None,
    format: Annotated[
        str,
        Field(description="'markdown' (table for report appendix) or 'json' (structured rows)."),
    ] = "markdown",
    offset: Annotated[int, Field(description="Rows to skip, for paging; each call returns up to 200 rows.")] = 0,
) -> dict[str, Any]:
    """Table of full-text excluded studies, their citations and recorded exclusion reasons.

    Reads full-text 'exclude' decisions for the counting reviewer, normalises exclusion
    reasons consistently with PRISMA flow counts, and checks reconciliation against
    compute_status's excluded_full_text count.

    Returns: ok, format, papers (count), by_reason (counts per reason), reconciliation
    (rows, excluded_full_text, reconciles, explanation; always over every row), truncated,
    next_offset (pass as offset for the next page of up to 200 rows), and table (when markdown)
    or rows (when json). Each row in rows has paper_ref, citation, reasons, reason_missing.

    Does NOT verify the scientific validity of exclusion decisions or reasons; reports
    recorded audit data only.
    """
    fmt = choice_arg(format, "format", ("markdown", "json"))

    with store.review_db(review_id) as conn:
        used_reviewer = prisma.resolve_reviewer(conn, reviewer)
        if used_reviewer is None:
            raw_rows: list[sqlite3.Row] = []
        else:
            raw_rows = conn.execute(
                "SELECT paper_ref, reason, title FROM screening "
                "WHERE stage = 'full_text' AND decision = 'exclude' AND reviewer = ? "
                "ORDER BY paper_ref",
                (used_reviewer,),
            ).fetchall()

        status: dict[str, Any] | None = None
        status_excluded: int | None = None
        status_err: str | None = None
        try:
            status = prisma.compute_status(conn, used_reviewer)
            status_excluded = int(status.get("excluded_full_text", 0))
        except store.StoreError as exc:
            status_err = str(exc)

    all_rows: list[dict[str, Any]] = []
    by_reason: dict[str, int] = defaultdict(int)

    for r in raw_rows:
        paper_ref = str(r["paper_ref"])
        citation = format_citation(paper_ref, r["title"])
        reasons, reason_missing = parse_reasons(r["reason"])
        for rsn in reasons:
            by_reason[rsn] += 1
        all_rows.append({
            "paper_ref": paper_ref,
            "citation": citation,
            "reasons": reasons,
            "reason_missing": reason_missing,
        })

    skip = int_arg(offset, "offset", min_value=0)
    capped_rows = all_rows[skip : skip + LIST_CAP]
    papers_count = len(all_rows)
    truncated = skip + len(capped_rows) < papers_count

    # Reconcile the full row set; paging only limits what is shown.
    explanation: str | None = None
    if status_excluded is None:
        reconciles = False
        explanation = f"PRISMA status could not be computed: {status_err}"
    else:
        reconciles = papers_count == status_excluded
        if not reconciles:
            explanation = (
                f"Excluded studies rows ({papers_count}) do not reconcile with PRISMA status "
                f"excluded_full_text ({status_excluded})."
            )

    reconciliation = {
        "rows": papers_count,
        "excluded_full_text": status_excluded,
        "reconciles": reconciles,
        "explanation": explanation,
    }

    result: dict[str, Any] = {
        "ok": True,
        "format": fmt,
        "papers": papers_count,
        "by_reason": dict(sorted(by_reason.items(), key=lambda kv: (-kv[1], kv[0]))),
        "reconciliation": reconciliation,
        "truncated": truncated,
        "next_offset": skip + len(capped_rows) if truncated else None,
    }

    if fmt == "markdown":
        headers = ["Paper reference", "Citation", "Exclusion reasons"]
        table_body = [
            [
                row["paper_ref"],
                row["citation"],
                "; ".join(row["reasons"]) if row["reasons"] else "No reason recorded",
            ]
            for row in capped_rows
        ]
        result["table"] = md_table(headers, table_body)
    else:
        result["rows"] = capped_rows

    return result


# ---------------------------------------------------------------------------
# Tool 2: manuscript_check
# ---------------------------------------------------------------------------


@safe_tool
def manuscript_check(
    review_id: ReviewId,
    sections: SectionsArg,
    reviewer: ReviewerOpt = None,
) -> dict[str, Any]:
    """Check manuscript sections against recorded review audit data.

    Compares stated PRISMA counts, cross-section count consistency, effect estimates
    and confidence intervals, search databases, search dates, reviewer counts, risk of
    bias tools, and synthesis methods against the audit trail.

    Returns: ok, findings (each: kind, severity 'mismatch'|'not_in_record'|'unknown'|'omitted',
    section, line, excerpt, stated, recorded, field, message; capped at 200), truncated,
    quantity_map, counts_by_kind,
    counts_by_severity, checked, not_checked.

    Does NOT judge the truth of scientific claims, check semantic entailment, or evaluate
    methodological quality; compares record-backed quantities and factual statements only.
    """
    if not isinstance(sections, dict) or isinstance(sections, (list, tuple, str)):
        fail("sections must be an object {section_name: text}")
    if len(sections) < 1 or len(sections) > 20:
        fail(f"sections must have between 1 and 20 sections; got {len(sections)}")

    cleaned_sections: dict[str, str] = {}
    for name, text in sections.items():
        if not isinstance(name, str) or not name.strip():
            fail("section names must be non-empty strings")
        clean_name = name.strip()
        if len(clean_name) > 80:
            fail(f"section name '{clean_name[:40]}...' is {len(clean_name)} characters; maximum is 80")
        if not isinstance(text, str):
            fail(f"text for section '{clean_name}' must be a string")
        if not text.strip():
            fail(f"section '{clean_name}' has empty text")
        if len(text) > MAX_TEXT_CHARS:
            fail(f"section '{clean_name}' text is {len(text)} characters; maximum is {MAX_TEXT_CHARS}")
        cleaned_sections[clean_name] = text

    with store.review_db(review_id) as conn:
        status: dict[str, Any] | None = None
        status_err: str | None = None
        try:
            status = prisma.compute_status(conn, reviewer)
        except store.StoreError as exc:
            status_err = str(exc)

        ev_rows = extraction.evidence_rows(conn)
        search_entries = prisma.search_log(conn)
        rob_entries = evidence_checks.rob_rows(conn)
        by_stage = prisma.reviewers_by_stage(conn)
        proto = protocol.get_protocol(conn)

    # Build quantity map
    prisma_map: dict[str, Any] = {}
    if status is not None:
        for k in (
            "records_identified", "duplicates_removed", "records_after_dedup",
            "records_screened", "excluded_title_abstract", "full_text_requested",
            "full_text_unavailable", "full_text_assessed", "excluded_full_text",
            "studies_included",
        ):
            prisma_map[k] = status.get(k)

    quantity_map: dict[str, Any] = {
        "prisma": prisma_map,
        "studies_included": status.get("studies_included", 0) if status else 0,
        "evidence": [
            {
                "paper_ref": r.get("paper_ref"),
                "outcome": r.get("outcome"),
                "measure": r.get("effect_measure"),
                "estimate": r.get("effect_estimate"),
                "ci_lower": r.get("ci_lower"),
                "ci_upper": r.get("ci_upper"),
            }
            for r in ev_rows
        ],
    }

    findings: list[dict[str, Any]] = []

    # Rule (f): add 'unknown' findings when record lacks required tables
    if not search_entries:
        findings.append({
            "kind": "database",
            "severity": "unknown",
            "section": None,
            "line": None,
            "excerpt": "",
            "stated": None,
            "recorded": None,
            "field": "search_log",
            "message": "No searches are recorded in the audit trail to check search sources and dates against.",
        })

    if not rob_entries:
        findings.append({
            "kind": "rob_tool",
            "severity": "unknown",
            "section": None,
            "line": None,
            "excerpt": "",
            "stated": None,
            "recorded": None,
            "field": "rob_rows",
            "message": "No risk-of-bias assessments are recorded in the audit trail to check RoB tools against.",
        })

    if not ev_rows:
        findings.append({
            "kind": "effect_estimate",
            "severity": "unknown",
            "section": None,
            "line": None,
            "excerpt": "",
            "stated": None,
            "recorded": None,
            "field": "evidence_rows",
            "message": "No extracted evidence rows are recorded in the audit trail to check effect estimates against.",
        })

    if status is None:
        findings.append({
            "kind": "prisma_count",
            "severity": "unknown",
            "section": None,
            "line": None,
            "excerpt": "",
            "stated": None,
            "recorded": None,
            "field": "prisma_status",
            "message": f"PRISMA status could not be computed to check study counts: {status_err}",
        })

    # (b) PRISMA counts per section & (c) Cross-section PRISMA counts
    section_stated_counts: dict[str, list[dict[str, Any]]] = defaultdict(list)
    if status is not None:
        for sec_name, sec_text in cleaned_sections.items():
            checks = prisma_render.number_checks(sec_text, status)
            for ch in checks:
                fld = ch["field"]
                reported = ch["reported"]
                recorded = ch["recorded"]
                snippet = ch["snippet"]
                idx = sec_text.find(snippet)
                line = sec_text[:idx].count("\n") + 1 if idx >= 0 else 1
                excerpt = snippet[:160]

                section_stated_counts[fld].append({
                    "section": sec_name,
                    "reported": reported,
                    "line": line,
                    "excerpt": excerpt,
                })

                if not ch["matches"]:
                    findings.append({
                        "kind": "prisma_count",
                        "severity": "mismatch",
                        "section": sec_name,
                        "line": line,
                        "excerpt": excerpt,
                        "stated": reported,
                        "recorded": recorded,
                        "field": fld,
                        "message": (
                            f"Stated {fld} count ({reported}) in '{sec_name}' "
                            f"does not match recorded count ({recorded})."
                        ),
                    })

        # Check cross-section conflicts for PRISMA counts
        seen_count_pairs: set[tuple[str, str, str]] = set()
        for fld, items in section_stated_counts.items():
            for i, it1 in enumerate(items):
                for it2 in items[i + 1:]:
                    sec1, sec2 = it1["section"], it2["section"]
                    if sec1 != sec2 and it1["reported"] != it2["reported"]:
                        pair_key = (fld, min(sec1, sec2), max(sec1, sec2))
                        if pair_key not in seen_count_pairs:
                            seen_count_pairs.add(pair_key)
                            findings.append({
                                "kind": "cross_section_count",
                                "severity": "mismatch",
                                "section": f"{sec1} vs {sec2}",
                                "line": it2["line"],
                                "excerpt": f"{sec1}: '{it1['excerpt']}' vs {sec2}: '{it2['excerpt']}'"[:160],
                                "stated": f"{sec1}: {it1['reported']}, {sec2}: {it2['reported']}",
                                "recorded": status.get(fld),
                                "field": fld,
                                "message": (
                                    f"Conflicting {fld} counts stated across sections: "
                                    f"'{sec1}' states {it1['reported']} but '{sec2}' states {it2['reported']}."
                                ),
                            })

    # (d) Effect estimates
    section_stated_effects: list[dict[str, Any]] = []
    if ev_rows:
        for sec_name, sec_text in cleaned_sections.items():
            effects = find_effect_estimates(sec_text)
            for eff in effects:
                meas = eff["measure"]
                est_str = eff["estimate"]
                l_str = eff["lower"]
                u_str = eff["upper"]
                line = eff["line"]
                excerpt = eff["excerpt"]

                section_stated_effects.append({
                    "section": sec_name,
                    "measure": meas,
                    "estimate_str": est_str,
                    "lower_str": l_str,
                    "upper_str": u_str,
                    "line": line,
                    "excerpt": excerpt,
                })

                matching_measure_rows = [r for r in ev_rows if r.get("effect_measure") == meas]
                matching_est_rows = [
                    r for r in matching_measure_rows
                    if number_matches(est_str, r.get("effect_estimate"))
                ]

                if not matching_est_rows:
                    findings.append({
                        "kind": "effect_estimate",
                        "severity": "not_in_record",
                        "section": sec_name,
                        "line": line,
                        "excerpt": excerpt,
                        "stated": f"{meas.upper()} {est_str} (95% CI {l_str} to {u_str})",
                        "recorded": None,
                        "field": "effect_estimate",
                        "message": (
                            f"Effect estimate {meas.upper()} {est_str} stated in '{sec_name}' "
                            "was not found in the audit record."
                        ),
                    })
                else:
                    # Check CI limits against matching estimate rows
                    exact_ci_match = any(
                        number_matches(l_str, r.get("ci_lower")) and number_matches(u_str, r.get("ci_upper"))
                        for r in matching_est_rows
                    )
                    if not exact_ci_match:
                        best = matching_est_rows[0]
                        rec_ci = f"CI {best.get('ci_lower')} to {best.get('ci_upper')}"
                        findings.append({
                            "kind": "effect_estimate",
                            "severity": "mismatch",
                            "section": sec_name,
                            "line": line,
                            "excerpt": excerpt,
                            "stated": f"CI {l_str} to {u_str}",
                            "recorded": rec_ci,
                            "field": "ci_limits",
                            "message": (
                                f"Stated confidence limits (CI {l_str} to {u_str}) for {meas.upper()} {est_str} "
                                f"in '{sec_name}' do not match recorded limits ({rec_ci})."
                            ),
                        })

        # Cross-section effect estimate limit consistency
        seen_eff_pairs: set[tuple[str, str, str, str]] = set()
        for i, e1 in enumerate(section_stated_effects):
            for e2 in section_stated_effects[i + 1:]:
                sec1, sec2 = e1["section"], e2["section"]
                if sec1 != sec2 and e1["measure"] == e2["measure"] and number_matches(e1["estimate_str"], float(e2["estimate_str"].replace("−", "-"))):
                    l_match = number_matches(e1["lower_str"], float(e2["lower_str"].replace("−", "-")))
                    u_match = number_matches(e1["upper_str"], float(e2["upper_str"].replace("−", "-")))
                    if not (l_match and u_match):
                        pair_key = (e1["measure"], e1["estimate_str"], min(sec1, sec2), max(sec1, sec2))
                        if pair_key not in seen_eff_pairs:
                            seen_eff_pairs.add(pair_key)
                            findings.append({
                                "kind": "cross_section_effect",
                                "severity": "mismatch",
                                "section": f"{sec1} vs {sec2}",
                                "line": e2["line"],
                                "excerpt": f"{sec1}: '{e1['excerpt']}' vs {sec2}: '{e2['excerpt']}'"[:160],
                                "stated": f"{sec1}: CI {e1['lower_str']} to {e1['upper_str']}, {sec2}: CI {e2['lower_str']} to {e2['upper_str']}",
                                "recorded": None,
                                "field": "ci_limits",
                                "message": (
                                    f"Conflicting confidence limits stated for {e1['measure'].upper()} {e1['estimate_str']} across sections: "
                                    f"'{sec1}' states ({e1['lower_str']} to {e1['upper_str']}) but '{sec2}' states ({e2['lower_str']} to {e2['upper_str']})."
                                ),
                            })

    # (e) Methods checks
    method_sections = [(n, t) for n, t in cleaned_sections.items() if "method" in n.lower()]
    if method_sections:
        first_method_name = method_sections[0][0]

        # 1. Databases
        if search_entries:
            recorded_dbs = {normalize_db_name(s["source"]) for s in search_entries if s.get("source")}
            stated_dbs_seen: set[str] = set()

            for sec_name, sec_text in method_sections:
                named_dbs = find_named_databases(sec_text)
                for db_info in named_dbs:
                    db_name = db_info["name"]
                    stated_dbs_seen.add(db_name)
                    if db_name not in recorded_dbs:
                        findings.append({
                            "kind": "database",
                            "severity": "not_in_record",
                            "section": sec_name,
                            "line": db_info["line"],
                            "excerpt": db_info["excerpt"],
                            "stated": db_name,
                            "recorded": None,
                            "field": "databases",
                            "message": f"Database '{db_name}' stated in the manuscript but not found in the audit record.",
                        })

            for rec_db in sorted(recorded_dbs):
                if rec_db not in stated_dbs_seen:
                    findings.append({
                        "kind": "database",
                        "severity": "omitted",
                        "section": first_method_name,
                        "line": None,
                        "excerpt": "",
                        "stated": None,
                        "recorded": rec_db,
                        "field": "databases",
                        "message": f"Database '{rec_db}' recorded in search log but omitted from the methods section.",
                    })

        # 2. Search dates
        if search_entries:
            rec_dates = [s["date_run"] for s in search_entries if s.get("date_run")]
            rec_months = [d[:7] for d in rec_dates if len(d) >= 7]

            for sec_name, sec_text in method_sections:
                dates_found = find_search_dates(sec_text)
                for df in dates_found:
                    stated_text = df["stated_text"]
                    iso_d = df["iso_date"]
                    m_str = df["month_str"]

                    # A stated day must match a recorded day; month precision only when no day is stated.
                    matched_date = any(d.startswith(iso_d) for d in rec_dates) if iso_d else m_str in rec_months

                    if not matched_date:
                        findings.append({
                            "kind": "search_date",
                            "severity": "not_in_record",
                            "section": sec_name,
                            "line": df["line"],
                            "excerpt": df["excerpt"],
                            "stated": stated_text,
                            "recorded": rec_dates,
                            "field": "date_run",
                            "message": f"Search date '{stated_text}' stated in the manuscript but not found in the audit record.",
                        })

        # 3. Reviewer counts
        all_non_primary = {r for revs in by_stage.values() for r in revs if r != "primary"}
        recorded_reviewers = len(all_non_primary)

        for sec_name, sec_text in method_sections:
            rev_counts = find_reviewer_counts(sec_text)
            for rc in rev_counts:
                stated_cnt = rc["stated_count"]
                if not recorded_reviewers:
                    # Only 'primary' (or nothing) recorded: the record has no reviewer-level data to contradict.
                    findings.append({
                        "kind": "reviewer_count",
                        "severity": "unknown",
                        "section": sec_name,
                        "line": rc["line"],
                        "excerpt": rc["excerpt"],
                        "stated": stated_cnt,
                        "recorded": None,
                        "field": "reviewer_count",
                        "message": (
                            "The audit trail has no decisions under individual reviewer names (only 'primary' or "
                            "none), so the stated reviewer count cannot be checked."
                        ),
                    })
                elif stated_cnt != recorded_reviewers:
                    findings.append({
                        "kind": "reviewer_count",
                        "severity": "mismatch",
                        "section": sec_name,
                        "line": rc["line"],
                        "excerpt": rc["excerpt"],
                        "stated": stated_cnt,
                        "recorded": recorded_reviewers,
                        "field": "reviewer_count",
                        "message": (
                            f"Stated reviewer count ({stated_cnt}) in '{sec_name}' "
                            f"does not match recorded distinct reviewers ({recorded_reviewers})."
                        ),
                    })

        # 4. Risk of bias tools
        if rob_entries:
            rec_rob_map = {normalize_rob_tool(r["tool"])[1]: normalize_rob_tool(r["tool"])[0] for r in rob_entries if r.get("tool")}
            stated_rob_seen: set[str] = set()

            for sec_name, sec_text in method_sections:
                rob_tools = find_rob_tools(sec_text)
                for rt in rob_tools:
                    k = rt["key"]
                    disp = rt["display_name"]
                    stated_rob_seen.add(k)
                    if k not in rec_rob_map:
                        rec_display_list = list(rec_rob_map.values())
                        findings.append({
                            "kind": "rob_tool",
                            "severity": "mismatch",
                            "section": sec_name,
                            "line": rt["line"],
                            "excerpt": rt["excerpt"],
                            "stated": disp,
                            "recorded": rec_display_list,
                            "field": "rob_tool",
                            "message": (
                                f"Stated risk-of-bias tool '{disp}' in '{sec_name}' "
                                f"does not match recorded tool(s) ({', '.join(rec_display_list)})."
                            ),
                        })

            for rec_k, rec_disp in sorted(rec_rob_map.items()):
                if rec_k not in stated_rob_seen:
                    findings.append({
                        "kind": "rob_tool",
                        "severity": "omitted",
                        "section": first_method_name,
                        "line": None,
                        "excerpt": "",
                        "stated": None,
                        "recorded": rec_disp,
                        "field": "rob_tool",
                        "message": f"Risk-of-bias tool '{rec_disp}' recorded in audit trail is omitted from the methods section.",
                    })

        # 5. Synthesis method
        rec_synth = (proto.get("synthesis_method") or "").strip()
        for sec_name, sec_text in method_sections:
            synth_methods = find_synthesis_methods(sec_text)
            for sm in synth_methods:
                stated_synth = sm["stated_text"]
                if not rec_synthesis_method_is_recorded(rec_synth):
                    findings.append({
                        "kind": "synthesis_method",
                        "severity": "unknown",
                        "section": sec_name,
                        "line": sm["line"],
                        "excerpt": sm["excerpt"],
                        "stated": stated_synth,
                        "recorded": None,
                        "field": "synthesis_method",
                        "message": "The audit record has no recorded synthesis method to compare with.",
                    })
                else:
                    # Compare the model type, not the wording ("random-effects model" vs "... meta-analysis").
                    rec_classes = synthesis_classes(rec_synth)
                    if rec_classes and not synthesis_classes(stated_synth) & rec_classes:
                        findings.append({
                            "kind": "synthesis_method",
                            "severity": "mismatch",
                            "section": sec_name,
                            "line": sm["line"],
                            "excerpt": sm["excerpt"],
                            "stated": stated_synth,
                            "recorded": rec_synth,
                            "field": "synthesis_method",
                            "message": (
                                f"Stated synthesis method '{stated_synth}' in '{sec_name}' "
                                f"does not match recorded method '{rec_synth}'."
                            ),
                        })

    counts_by_kind: dict[str, int] = defaultdict(int)
    counts_by_severity: dict[str, int] = defaultdict(int)
    for f in findings:
        counts_by_kind[f["kind"]] += 1
        counts_by_severity[f["severity"]] += 1

    checked = [
        "PRISMA study counts per section against compute_status",
        "Cross-section consistency of PRISMA study counts",
        "Effect estimates and 95% confidence intervals against evidence_rows",
        "Cross-section consistency of effect estimates and confidence limits",
        "Search databases in methods against search_log",
        "Search dates in methods against search_log date_run",
        "Reviewer count in methods against screening reviewers",
        "Risk of bias tools in methods against rob_rows",
        "Synthesis method in methods against protocol",
    ]

    not_checked = [
        "Truth or scientific validity of medical and factual claims",
        "Entailment or semantic reasoning about study conclusions",
        "Methodological quality of search strategies or risk-of-bias judgements",
        "Mathematical correctness of meta-analytic pooling",
    ]

    listed, truncated = cap(findings)
    return {
        "ok": True,
        "findings": listed,
        "truncated": truncated,
        "quantity_map": quantity_map,
        "counts_by_kind": dict(counts_by_kind),
        "counts_by_severity": dict(counts_by_severity),
        "checked": checked,
        "not_checked": not_checked,
    }


def rec_synthesis_method_is_recorded(val: Any) -> bool:
    """True if protocol synthesis_method is recorded and non-empty."""
    return bool(val and str(val).strip())


def register(server: MCPServer) -> None:
    for tool in (prisma_excluded_studies, manuscript_check):
        server.tool()(tool)
