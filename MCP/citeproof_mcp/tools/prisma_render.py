# SPDX-License-Identifier: MIT
"""Pure text rendering for the PRISMA tools: flow diagrams, checklist rules, number checks.

Nothing here touches the database. Every function takes the counts dict produced by
``prisma.compute_status`` (or report text) and returns text or plain data.
"""

import html
import re
from typing import Any

# ---------------------------------------------------------------------------
# Flow diagrams
# ---------------------------------------------------------------------------


def _top(counter: dict[str, int], n: int) -> list[tuple[str, int]]:
    return sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))[:n]


def _mermaid_text(value: Any) -> str:
    """Escape agent text for a quoted Mermaid label using Mermaid's own entity codes."""
    s = re.sub(r"[\r\n]+", " ", str(value if value is not None else ""))
    s = s.replace("#", "#35;").replace('"', "#quot;").replace("<", "#lt;").replace(">", "#gt;")
    return s.strip()


def _md_text(value: Any) -> str:
    """Escape agent text so Markdown never renders HTML."""
    s = re.sub(r"[\r\n]+", " ", str(value if value is not None else ""))
    return s.replace("<", "&lt;").replace(">", "&gt;").strip()


def build_mermaid(s: dict[str, Any]) -> str:
    sources = "".join(
        f"<br/>{_mermaid_text(k)}: {v}" for k, v in sorted(s["records_by_source"].items())
    )
    reasons = "".join(
        f"<br/>{_mermaid_text(k)}: {v}" for k, v in _top(s["exclusion_reasons"], 5)
    )
    lines = [
        "flowchart TD",
        "    subgraph Identification",
        f'        id1["Records identified from databases (n = {s["records_identified"]}){sources}"]',
        f'        id2["Duplicates removed (n = {s["duplicates_removed"]})"]',
        f'        id3["Records after deduplication (n = {s["records_after_dedup"]})"]',
        "        id1 --> id2",
        "        id2 --> id3",
        "    end",
        "    subgraph Screening",
        f'        sc1["Records screened (title/abstract) (n = {s["records_screened"]})"]',
        f'        sc_exc["Records excluded (title/abstract) (n = {s["excluded_title_abstract"]})"]',
        f'        sc2["Reports sought for retrieval (n = {s["full_text_requested"]})"]',
        f'        sc_unav["Reports not retrieved (n = {s["full_text_unavailable"]})"]',
        f'        sc3["Reports assessed for eligibility (full-text) (n = {s["full_text_assessed"]})"]',
        f'        sc_ft_exc["Reports excluded (full-text) (n = {s["excluded_full_text"]}){reasons}"]',
        "        id3 --> sc1",
        "        sc1 --> sc_exc",
        "        sc1 --> sc2",
        "        sc2 --> sc_unav",
        "        sc2 --> sc3",
        "        sc3 --> sc_ft_exc",
    ]
    if s["full_text_pending"]:
        lines += [
            f'        sc_pend["Reports awaiting a full-text decision (n = {s["full_text_pending"]})"]',
            "        sc2 --> sc_pend",
        ]
    lines += [
        "    end",
        "    subgraph Included",
        (
            f'        inc["Studies included in review (n = {s["study_count"]})<br/>'
            f'Reports of included studies (n = {s["reports_included"]})"]'
        ),
        "        sc3 --> inc",
        "    end",
    ]
    return "\n".join(lines)


_SVG_STYLES = {
    "main": ('fill="#f0f4f8" stroke="#3b82f6"', "#333"),
    "side": ('fill="#fef2f2" stroke="#ef4444"', "#991b1b"),
    "screen": ('fill="#ecfdf5" stroke="#10b981"', "#333"),
    "incl": ('fill="#faf5ff" stroke="#8b5cf6"', "#333"),
}


def _svg_box(x: int, y: int, w: int, h: int, style: str, lines: list[str], bold_first: bool = False) -> str:
    box, color = _SVG_STYLES[style]
    out = [f'  <rect x="{x}" y="{y}" width="{w}" height="{h}" rx="4" {box} stroke-width="1.5"/>']
    ty = y + 22
    for i, line in enumerate(lines):
        weight = ' font-weight="bold"' if bold_first and i == 0 else ""
        size = 11 if line.startswith("•") else 12
        out.append(
            f'  <text x="{x + 10}" y="{ty}" font-size="{size}"{weight} fill="{color}" '
            f'font-family="sans-serif">{html.escape(line, quote=True)}</text>'
        )
        ty += 18
    return "\n".join(out)


def _svg_arrow(x1: int, y1: int, x2: int, y2: int) -> str:
    return (
        f'  <line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="#333" stroke-width="2" '
        'marker-end="url(#arrow)"/>'
    )


def build_svg(s: dict[str, Any]) -> str:
    reasons = [f"• {k[:32]}: {v}" for k, v in _top(s["exclusion_reasons"], 4)]
    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 950" width="100%" height="100%">',
        "  <defs>",
        ('    <marker id="arrow" viewBox="0 0 10 10" refX="6" refY="5" markerWidth="6" markerHeight="6" '
        'orient="auto-start-reverse"><path d="M 0 1 L 10 5 L 0 9 z" fill="#333"/></marker>'),
        "  </defs>",
        ('  <text x="400" y="35" font-size="20" font-weight="bold" text-anchor="middle" '
        'font-family="sans-serif" fill="#222">PRISMA 2020 Flow Diagram</text>'),
        _svg_box(40, 60, 360, 70, "main", ["Identification", f"Records identified from databases: {s['records_identified']}"], True),
        _svg_arrow(220, 130, 220, 160),
        _svg_box(40, 160, 360, 50, "main", [f"Records after duplicates removed: {s['records_after_dedup']}"]),
        _svg_arrow(400, 185, 480, 185),
        _svg_box(480, 160, 280, 50, "side", [f"Duplicates removed: {s['duplicates_removed']}"]),
        _svg_arrow(220, 210, 220, 250),
        _svg_box(40, 250, 360, 60, "screen", ["Screening", f"Records screened (title/abstract): {s['records_screened']}"], True),
        _svg_arrow(400, 280, 480, 280),
        _svg_box(480, 255, 280, 50, "side", [f"Records excluded: {s['excluded_title_abstract']}"]),
        _svg_arrow(220, 310, 220, 350),
        _svg_box(40, 350, 360, 60, "screen", ["Reports sought for retrieval", f"Reports sought: {s['full_text_requested']}"], True),
        _svg_arrow(400, 380, 480, 380),
        _svg_box(
            480, 355, 280, 50, "side",
            [f"Reports not retrieved: {s['full_text_unavailable']}"]
            + ([f"Awaiting full-text decision: {s['full_text_pending']}"] if s["full_text_pending"] else []),
        ),
        _svg_arrow(220, 410, 220, 450),
        _svg_box(40, 450, 360, 70, "screen", ["Full-text eligibility", f"Reports assessed for eligibility: {s['full_text_assessed']}"], True),
        _svg_arrow(400, 485, 480, 485),
        _svg_box(
            480, 450, 280, 270, "side",
            ["Reports excluded (full-text)", f"Total excluded: {s['excluded_full_text']}", "Top exclusion reasons:"] + reasons,
            True,
        ),
        _svg_arrow(220, 520, 220, 570),
        _svg_box(40, 570, 360, 70, "incl", ["Included", f"Studies included in review: {s['study_count']}", f"Reports of included studies: {s['reports_included']}"], True),
        "</svg>",
    ]
    return "\n".join(parts)


def build_markdown(s: dict[str, Any]) -> str:
    lines = [
        "# PRISMA 2020 Flow Summary",
        "",
        "1. **Identification**",
        f"   - Records identified from searches: {s['records_identified']}",
    ]
    if s["records_by_source"]:
        lines.append("     - Records by source:")
        lines += [f"     - {_md_text(k)}: {v}" for k, v in sorted(s["records_by_source"].items())]
    lines += [
        f"   - Duplicate records removed: {s['duplicates_removed']}",
        f"   - Records after deduplication: {s['records_after_dedup']}",
        "",
        "2. **Screening**",
        f"   - Records screened (title/abstract): {s['records_screened']}",
        f"   - Records excluded (title/abstract): {s['excluded_title_abstract']}",
        f"   - Reports sought for retrieval: {s['full_text_requested']}",
        f"   - Reports not retrieved: {s['full_text_unavailable']}",
    ]
    if s["full_text_pending"]:
        lines.append(f"   - Reports awaiting a full-text decision: {s['full_text_pending']}")
    lines += [
        f"   - Reports assessed for eligibility (full-text): {s['full_text_assessed']}",
        f"   - Reports excluded (full-text): {s['excluded_full_text']}",
    ]
    if s["exclusion_reasons"]:
        lines.append("     - Exclusion reasons:")
        lines += [f"     - {_md_text(k)}: {v}" for k, v in _top(s["exclusion_reasons"], 5)]
    lines += [
        "",
        "3. **Included**",
        f"   - Studies included in review: {s['study_count']}",
        f"   - Reports of included studies: {s['reports_included']}",
    ]
    return "\n".join(lines)


DIAGRAM_BUILDERS = {"mermaid": build_mermaid, "svg": build_svg, "markdown": build_markdown}

# ---------------------------------------------------------------------------
# PRISMA 2020 checklist keyword rules (Page et al., BMJ 2021;372:n71, 27 items)
# ---------------------------------------------------------------------------

_DATABASES = (
    r"pubmed|medline|embase|cinahl|web of science|scopus|psycinfo|cochrane library|cochrane central|"
    r"clinicaltrials\.gov|ictrp|lilacs|google scholar"
)

# Item 1 is checked on the first non-empty line (the title) instead of with patterns.
TITLE_RE = re.compile(r"systematic review|meta[- ]analys[ie]s|scoping review|rapid review|umbrella review", re.IGNORECASE)

PRISMA_2020_ITEMS: tuple[tuple[int, str, str, tuple[str, ...]], ...] = (
    (1, "Title", "Title identifies the report as a systematic review", ()),
    (2, "Abstract", "Abstract", (r"abstract",)),
    (3, "Introduction", "Rationale", (r"rationale", r"background")),
    (4, "Introduction", "Objectives", (r"objectives?", r"research questions?", r"review questions?", r"we aimed", r"aims? of (?:this|the) review")),
    (5, "Methods", "Eligibility criteria", (r"eligibility criteria", r"inclusion criteria", r"exclusion criteria", r"were eligible", r"picos?")),
    (6, "Methods", "Information sources", (r"information sources", r"databases? searched", r"searched (?:the )?(?:" + _DATABASES + r")", r"(?:" + _DATABASES + r")")),
    (7, "Methods", "Search strategy", (r"search strateg(?:y|ies)", r"search strings?", r"search terms", r"boolean")),
    (8, "Methods", "Selection process", (r"selection process", r"screening process", r"screened independently", r"independently screened", r"two (?:independent )?reviewers")),
    (9, "Methods", "Data collection process", (r"data collection", r"data extraction", r"extracted data", r"data were extracted", r"extraction form")),
    (10, "Methods", "Data items", (r"data items", r"outcomes? (?:sought|of interest)", r"variables (?:were )?extracted", r"outcome measures?")),
    (11, "Methods", "Study risk of bias assessment", (r"risk of bias (?:assessment|tool)", r"assessed (?:the )?risk of bias", r"rob ?2", r"robins-?i", r"newcastle-?ottawa", r"quality assessment", r"critical appraisal")),
    (12, "Methods", "Effect measures", (r"effect measures?", r"odds ratios?", r"risk ratios?", r"relative risks?", r"(?:standardi[sz]ed )?mean differences?", r"hazard ratios?")),
    (13, "Methods", "Synthesis methods", (r"synthesis methods?", r"random[- ]effects?", r"fixed[- ]effects?", r"narrative synthesis", r"meta-analys[ie]s (?:was|were) (?:performed|conducted)", r"pooled using")),
    (14, "Methods", "Reporting bias assessment", (r"reporting bias assessment", r"publication bias", r"funnel plots?", r"egger")),
    (15, "Methods", "Certainty assessment", (r"certainty (?:assessment|of (?:the )?evidence|in (?:the )?evidence)", r"grade")),
    (16, "Results", "Study selection", (r"study selection", r"flow diagram", r"prisma flow", r"records (?:were )?identified")),
    (17, "Results", "Study characteristics", (r"study characteristics", r"characteristics of (?:the )?included studies")),
    (18, "Results", "Risk of bias in studies", (r"risk of bias (?:in|of|across) (?:the )?(?:included )?studies", r"(?:at|had) (?:a )?(?:low|high|moderate|serious) risk of bias", r"some concerns")),
    (19, "Results", "Results of individual studies", (r"results of individual studies", r"individual study results", r"forest plots?", r"for each (?:included )?study")),
    (20, "Results", "Results of syntheses", (r"results of (?:the )?syntheses?", r"pooled (?:estimate|effect|analysis|risk|odds|mean)", r"summary estimate", r"overall effect")),
    (21, "Results", "Reporting biases", (r"reporting biases", r"funnel plot asymmetry", r"egger'?s test", r"publication bias (?:was|were)")),
    (22, "Results", "Certainty of evidence", (r"summary of findings", r"certainty of (?:the )?evidence (?:was|is)", r"(?:high|moderate|low|very low)[- ]certainty evidence")),
    (23, "Discussion", "Discussion (interpretation, limitations, implications)", (r"discussion", r"limitations?", r"implications for (?:practice|policy|research)")),
    (24, "Other information", "Registration and protocol", (r"prospero", r"registration", r"registered", r"review protocol", r"protocol (?:was|is) (?:registered|available|published)")),
    (25, "Other information", "Support", (r"funding", r"funded by", r"financial support", r"grant", r"sponsor")),
    (26, "Other information", "Competing interests", (r"competing interests?", r"conflicts? of interests?", r"declarations? of interests?")),
    (27, "Other information", "Availability of data, code and other materials", (r"data availability", r"availability of data", r"code (?:is )?available", r"available (?:on|upon) (?:reasonable )?request", r"supplementary (?:material|data|files?)")),
)

_COMPILED_ITEMS = [
    (num, section, topic, [re.compile(r"\b" + p + r"\b", re.IGNORECASE) for p in patterns])
    for num, section, topic, patterns in PRISMA_2020_ITEMS
]


def checklist_items(report_text: str) -> list[dict[str, Any]]:
    """One entry per PRISMA 2020 item: found and the matched phrase."""
    first_line = next((ln.strip() for ln in report_text.splitlines() if ln.strip()), "")
    out: list[dict[str, Any]] = []
    for num, section, topic, patterns in _COMPILED_ITEMS:
        matched: str | None = None
        if num == 1:
            m = TITLE_RE.search(first_line)
            matched = m.group(0) if m else None
        else:
            for pat in patterns:
                m = pat.search(report_text)
                if m:
                    matched = m.group(0)
                    break
        out.append({"item": num, "section": section, "topic": topic, "found": matched is not None, "matched": matched})
    return out


# ---------------------------------------------------------------------------
# Numbers in the report vs recorded counts
# ---------------------------------------------------------------------------

_N = r"(?P<n>\d{1,3}(?:,\d{3})+|\d+)"
NUMBER_PATTERNS: dict[str, tuple[str, ...]] = {
    "records_identified": (
        _N + r"\s+(?:records|citations|references|articles|hits|search results)\s+(?:were\s+|was\s+)?(?:identified|retrieved|found)",
        r"(?:identified|retrieved|yielded|returned)\s+(?:a\s+total\s+of\s+)?" + _N + r"\s+(?:records|citations|references|articles|hits|results)",
    ),
    "duplicates_removed": (
        _N + r"\s+duplicates?\b",
        r"(?:removing|removed|removal\s+of)\s+" + _N + r"\s+duplicate",
    ),
    "records_screened": (
        _N + r"\s+(?:records|titles(?:\s+and\s+abstracts)?|abstracts|citations)\s+(?:were\s+)?screened",
        r"screened\s+(?:the\s+titles\s+and\s+abstracts\s+of\s+)?" + _N + r"\s+(?:records|citations|titles|abstracts|articles)",
    ),
    "full_text_assessed": (
        _N + r"\s+full[- ]?texts?(?:\s+(?:articles|reports|papers|records))?\s+(?:were\s+)?(?:assessed|reviewed|examined)",
        r"(?:assessed|reviewed|examined)\s+" + _N + r"\s+full[- ]?text",
    ),
    "studies_included": (
        _N + r"\s+(?:studies|trials|articles|reports|papers|rcts)\s+(?:were\s+|was\s+)?included",
        r"(?:included|including)\s+" + _N + r"\s+(?:studies|trials|articles|papers|rcts)\b",
        _N + r"\s+(?:included|eligible)\s+(?:studies|trials)",
    ),
}
_COMPILED_NUMBERS = {
    field: [re.compile(p, re.IGNORECASE) for p in patterns] for field, patterns in NUMBER_PATTERNS.items()
}


def number_checks(report_text: str, status: dict[str, Any], limit: int = 50) -> list[dict[str, Any]]:
    """Every count the report states for a PRISMA box, next to the recorded count."""
    found: list[tuple[int, dict[str, Any]]] = []
    seen = set()
    for field, patterns in _COMPILED_NUMBERS.items():
        for pat in patterns:
            for m in pat.finditer(report_text):
                key = (field, m.start("n"))
                if key in seen:
                    continue
                seen.add(key)
                reported = int(m.group("n").replace(",", ""))
                lo = max(0, m.start() - 30)
                snippet = re.sub(r"\s+", " ", report_text[lo : m.end() + 30]).strip()
                found.append(
                    (
                        m.start(),
                        {
                            "field": field,
                            "reported": reported,
                            "recorded": status[field],
                            # "N studies were included" may count linked studies rather than reports.
                            "matches": reported == status[field]
                            or (field == "studies_included" and reported == status.get("study_count")),
                            "snippet": snippet[:160],
                        },
                    )
                )
    found.sort(key=lambda t: t[0])
    return [entry for _, entry in found[:limit]]
