"""Academic search orchestration, multi-index retrieval, deduplication, and screening.

Integrates OpenAlex, Semantic Scholar, ArXiv, Europe PMC, DOAJ, DataCite,
Crossref, and PubMed with deterministic PRISMA 2020 tracking and structured
PaperRecord outputs. Each provider lives in its own adapter module under
backend/app/tools/ and fails independently: an exception or timeout in any
single provider never aborts the fan-out.
"""

from __future__ import annotations

import ast
import asyncio
import html
import json
import logging
import os
import re
from typing import Any

import httpx

try:
    import feedparser
except ImportError:
    feedparser = None

from backend.app.llm import ainvoke_llm, get_llm
from backend.app.models.evidence import PaperRecord, PRISMATracker, make_paper_id
from backend.app.report_guard import ReportGenerationError
from backend.app.tools.crossref_search import search_crossref
from backend.app.tools.datacite_search import search_datacite
from backend.app.tools.doaj_search import search_doaj
from backend.app.tools.europe_pmc_search import search_europe_pmc
from backend.app.tools.openalex_search import search_openalex
from backend.app.tools.pubmed_search import search_pubmed
from backend.app.tools.semantic_scholar_search import search_semantic_scholar

logger = logging.getLogger(__name__)

# Strict 6 second HTTP timeout to prevent hanging
HTTP_TIMEOUT = 6.0

# Corpus limits for the screening stage
MAX_SEARCH_KEYWORDS = 10      # keyword strings accepted from the planner
MAX_QUERIES_PER_SOURCE = 12   # simple (Boolean-free) queries issued per source
MAX_SIMPLE_QUERIES_PER_KEYWORD = 2  # a Boolean keyword expands to at most this many simple queries
SCREEN_THRESHOLD = 30        # corpus sizes at or below this skip screening entirely
SCREEN_CANDIDATES = 160      # papers sent to the LLM relevance scorer (800+ record corpora)
SCREEN_KEEP = 40             # papers retained for synthesis
SCREEN_MIN_RELEVANCE = 7.0   # scores at or above this are included directly
SCREEN_TOP_UP_FLOOR = 5.0    # related-but-indirect papers may top up a short pool
SCREEN_MIN_INCLUDED = 8      # top up with >=5 papers until this many are included
SCREEN_MIN_SCORED = 3        # fewer scored candidates than this fails the run cleanly
SCREEN_SOURCE_GUARANTEE = 1  # at most one paper per source outside the cutoff, and only above the floor
SCREEN_BATCH_SIZE = 15       # papers per LLM scoring call
SCREEN_CONCURRENCY = 4       # scoring calls in flight at once
SCREEN_BATCH_TIMEOUT_SECONDS = 90.0  # override with the env var of the same name
SCREEN_MAX_UNSCORED_FRACTION = 0.30  # above this share of failed scores the run fails


def _normalize_doi(doi: str | None) -> str:
    """Canonicalize DOI string."""
    if not doi:
        return ""
    doi = doi.strip().lower()
    doi = re.sub(r"^(https?://(dx\.)?doi\.org/|doi:\s*)", "", doi)
    return doi.strip()


def _normalize_title(title: str | None) -> str:
    """Normalize title for fuzzy collision-resistant deduplication."""
    if not title:
        return ""
    return re.sub(r"\W+", "", title.lower())


async def fetch_arxiv_papers(client: httpx.AsyncClient, keyword: str, max_results: int = 50) -> list[PaperRecord]:
    """Retrieve papers from arXiv."""
    papers = []
    if feedparser is None:
        logger.warning(
            f"ArXiv search skipped for '{keyword}': optional dependency 'feedparser' is not installed."
        )
        return papers
    try:
        clean_kw = re.sub(r"[^\w\s]", "", keyword).strip()
        url = f"http://export.arxiv.org/api/query?search_query=all:{clean_kw}&start=0&max_results={min(max_results, 50)}&sortBy=relevance"
        
        resp = await client.get(url)
        if resp.status_code == 200:
            feed = feedparser.parse(resp.text)
            for entry in feed.entries:
                title = entry.get("title", "").replace("\n", " ").strip()
                if not title:
                    continue
                abstract = entry.get("summary", "").replace("\n", " ").strip()
                authors = [a.get("name", "") for a in entry.get("authors", []) if a.get("name")]
                published = entry.get("published", "")
                year = published[:4] if len(published) >= 4 else "n.d."
                arxiv_id = entry.get("id", "")
                doi = _normalize_doi(entry.get("arxiv_doi"))

                paper_id = make_paper_id(doi=doi, title=title, year=year)
                papers.append(PaperRecord(
                    paper_id=paper_id,
                    doi=doi or None,
                    arxiv_id=arxiv_id.split("/")[-1] if arxiv_id else None,
                    title=title,
                    abstract=abstract,
                    authors=authors,
                    year=year,
                    source_url=arxiv_id,
                    pdf_url=arxiv_id.replace("/abs/", "/pdf/") if arxiv_id else None,
                    retrieval_source="arxiv",
                    citation_count=0,
                    study_type="theoretical",
                    screening_status="retrieved"
                ))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"ArXiv search skipped for '{keyword}': {e}")
    return papers


async def fetch_tavily_web_papers(keyword: str, max_results: int = 5) -> list[PaperRecord]:
    """LAST RESORT FALLBACK: Web articles via Tavily when academic databases return insufficient literature."""
    papers = []
    try:
        api_key = os.getenv("TAVILY_API_KEY")
        if not api_key:
            return papers
        from tavily import TavilyClient
        logger.info(f"Triggering LAST RESORT Tavily web search fallback for keyword: '{keyword}'")
        tavily = TavilyClient(api_key=api_key)
        res = tavily.search(query=f"academic research paper {keyword}", max_results=max_results, search_depth="basic")
        results = res.get("results", []) if isinstance(res, dict) else []
        for r in results:
            title = r.get("title", "").strip()
            content = r.get("content", "").strip()
            url = r.get("url", "").strip()
            if not title or not content:
                continue
            paper_id = make_paper_id(doi=None, title=title, year="n.d.")
            papers.append(PaperRecord(
                paper_id=paper_id,
                title=title,
                abstract=content,
                authors=["Web Source"],
                source_url=url,
                pdf_url=url if url.lower().endswith(".pdf") else None,
                retrieval_source="tavily_web_fallback",
                citation_count=0,
                screening_status="retrieved"
            ))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Tavily web fallback search skipped for '{keyword}': {e}")
    return papers


_BOOLEAN_SYNTAX_RE = re.compile(r'[()"]|\b(?:AND|OR|NOT)\b', re.IGNORECASE)


def _split_top_level(text: str, operator: str) -> list[str]:
    """Split on a Boolean operator that sits outside parentheses and quotes."""
    parts: list[str] = []
    buffer: list[str] = []
    depth = 0
    in_quotes = False
    operator_re = re.compile(rf"\b{operator}\b", re.IGNORECASE)
    index = 0
    while index < len(text):
        char = text[index]
        if char == '"':
            in_quotes = not in_quotes
            buffer.append(char)
            index += 1
            continue
        if not in_quotes:
            if char == "(":
                depth += 1
            elif char == ")":
                depth = max(0, depth - 1)
            elif depth == 0:
                match = operator_re.match(text, index)
                if match:
                    parts.append("".join(buffer))
                    buffer = []
                    index = match.end()
                    continue
        buffer.append(char)
        index += 1
    parts.append("".join(buffer))
    return [part for part in parts if part.strip()]


def _strip_outer_parentheses(text: str) -> str:
    """Remove balanced parentheses that wrap the whole operand."""
    cleaned = text.strip()
    while cleaned.startswith("(") and cleaned.endswith(")"):
        depth = 0
        in_quotes = False
        wraps_whole = True
        for index, char in enumerate(cleaned):
            if char == '"':
                in_quotes = not in_quotes
                continue
            if in_quotes:
                continue
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0 and index != len(cleaned) - 1:
                    wraps_whole = False
                    break
        if not wraps_whole or depth != 0:
            break
        cleaned = cleaned[1:-1].strip()
    return cleaned


def _clean_simple_query(term: str) -> str:
    """Turn one Boolean operand into a plain multi-word query."""
    cleaned = re.sub(r'[()"]', " ", term)
    cleaned = re.sub(r"\b(?:AND|OR|NOT)\b", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,;.-")
    return cleaned


def _simple_queries_for_keyword(keyword: str, limit: int) -> list[str]:
    """Expand one (possibly Boolean) keyword into a few simple source queries.

    Semantic Scholar and the other full-text indexes take plain terms; they do
    not understand ``OR`` or parentheses. Each top-level ``AND`` group supplies
    its ``OR`` operands, and the first operand of every group forms one combined
    query so the intervention and population stay together. PubMed keeps the
    original Boolean string (see ``search_academic_papers_structured``).
    """
    text = (keyword or "").strip()
    if not text:
        return []
    if not _BOOLEAN_SYNTAX_RE.search(text):
        return [text]

    queries: list[str] = []
    groups: list[list[str]] = []
    for and_group in _split_top_level(text, "AND"):
        # A negated operand must never become a positive query term.
        positive = _strip_outer_parentheses(_split_top_level(and_group, "NOT")[0])
        terms = [
            _clean_simple_query(term)
            for term in _split_top_level(positive, "OR")
        ]
        terms = [term for term in terms if term]
        if terms:
            groups.append(terms)

    if not groups:
        return []

    if len(groups) > 1:
        combined = " ".join(group[0] for group in groups)
        if len(combined.split()) <= 10:
            queries.append(combined)

    index = 0
    while len(queries) < limit and any(index < len(group) for group in groups):
        for group in groups:
            if index < len(group):
                queries.append(group[index])
                if len(queries) >= limit:
                    break
        index += 1

    deduped: list[str] = []
    seen: set[str] = set()
    for query in queries:
        key = query.casefold()
        if key and key not in seen:
            seen.add(key)
            deduped.append(query)
    return deduped[:limit]


def _expand_keywords_for_simple_sources(
    keywords: list[str],
    max_queries: int = MAX_QUERIES_PER_SOURCE,
) -> list[str]:
    """Round-robin the simple queries of every keyword so each one contributes."""
    expanded = [
        _simple_queries_for_keyword(keyword, MAX_SIMPLE_QUERIES_PER_KEYWORD)
        for keyword in keywords
    ]
    result: list[str] = []
    seen: set[str] = set()
    index = 0
    while len(result) < max_queries and any(index < len(queries) for queries in expanded):
        for queries in expanded:
            if index >= len(queries):
                continue
            query = queries[index]
            key = query.casefold()
            if key not in seen:
                seen.add(key)
                result.append(query)
            if len(result) >= max_queries:
                break
        index += 1
    if result:
        return result
    fallback = [keyword.strip() for keyword in keywords if keyword and keyword.strip()]
    return fallback[:1] or ["research"]


async def search_academic_papers_structured(
    keywords: list[str]
) -> tuple[list[PaperRecord], PRISMATracker]:
    """Concurrent multi-source academic search returning typed PaperRecords and PRISMATracker."""
    selected_keywords = keywords[:MAX_SEARCH_KEYWORDS] if keywords else ["research"]
    # Semantic Scholar and the other full-text indexes reject or ignore Boolean
    # syntax (live run: every "A OR B" query returned 429/400). They receive
    # simple queries instead; PubMed supports Boolean and keeps the original.
    simple_keywords = _expand_keywords_for_simple_sources(selected_keywords)
    records_by_source: dict[str, int] = {
        "openalex": 0,
        "semantic_scholar": 0,
        "arxiv": 0,
        "europe_pmc": 0,
        "doaj": 0,
        "datacite": 0,
        "crossref": 0,
        "pubmed": 0,
        "tavily_web_fallback": 0,
    }

    # Each adapter manages its own HTTP client and timeout (same contract as
    # search_crossref / search_pubmed) so a slow or hanging provider cannot
    # stall the others through a shared pool. ArXiv keeps its inline
    # feedparser-based fetcher and shares one client across keywords.
    tasks = []
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT, follow_redirects=True) as arxiv_client:
        for kw in simple_keywords:
            tasks.append(search_openalex(kw, limit=15))
            tasks.append(search_semantic_scholar(kw, limit=15))
            tasks.append(fetch_arxiv_papers(arxiv_client, kw))
            tasks.append(search_europe_pmc(kw, limit=15))
            tasks.append(search_doaj(kw, limit=15))
            tasks.append(search_datacite(kw, limit=15))
            tasks.append(search_crossref(kw, limit=15))
        for kw in selected_keywords:
            tasks.append(search_pubmed(kw, limit=15))

        results_list = await asyncio.gather(*tasks, return_exceptions=True)
        combined: list[PaperRecord] = []
        for res in results_list:
            if isinstance(res, list):
                for p in res:
                    if isinstance(p, PaperRecord):
                        combined.append(p)
                        src = p.retrieval_source
                        records_by_source[src] = records_by_source.get(src, 0) + 1

    # Deduplicate by DOI and normalized title
    seen_dois = set()
    seen_titles = set()
    seen_paper_ids = set()
    deduped: list[PaperRecord] = []

    for p in combined:
        doi = p.doi
        norm_title = _normalize_title(p.title)
        pid = p.paper_id

        if pid in seen_paper_ids:
            continue
        if doi and doi in seen_dois:
            continue
        if norm_title and norm_title in seen_titles:
            continue

        seen_paper_ids.add(pid)
        if doi:
            seen_dois.add(doi)
        if norm_title:
            seen_titles.add(norm_title)

        deduped.append(p)

    # LAST RESORT FALLBACK: If academic indexes returned fewer than 5 papers, trigger Tavily
    if len(deduped) < 5 and selected_keywords:
        logger.warning(f"Academic search yield low ({len(deduped)} papers). Invoking Tavily web fallback...")
        tavily_tasks = [fetch_tavily_web_papers(kw, max_results=5) for kw in selected_keywords[:3]]
        tavily_results = await asyncio.gather(*tavily_tasks, return_exceptions=True)
        for res in tavily_results:
            if isinstance(res, list):
                for p in res:
                    norm_title = _normalize_title(p.title)
                    if norm_title and norm_title not in seen_titles:
                        seen_titles.add(norm_title)
                        seen_paper_ids.add(p.paper_id)
                        combined.append(p)
                        deduped.append(p)
                        records_by_source["tavily_web_fallback"] = records_by_source.get("tavily_web_fallback", 0) + 1

    duplicates_count = max(0, len(combined) - len(deduped))
    tracker = PRISMATracker(
        records_identified=len(combined),
        records_by_source=records_by_source,
        duplicates_removed=duplicates_count,
        records_after_dedup=len(deduped),
        records_screened=0,
        excluded_title_abstract=0,
        full_text_requested=0,
        full_text_assessed=0,
        excluded_full_text=0,
        studies_included=0,
    )

    logger.info(
        f"Search complete: {len(combined)} identified, {duplicates_count} duplicates removed, "
        f"{len(deduped)} retained after deduplication."
    )
    return deduped, tracker


async def search_academic_papers(
    keywords: list[str]
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Legacy adapter returning dictionary papers and stats for backward compatibility."""
    records, tracker = await search_academic_papers_structured(keywords)
    dict_papers = [r.model_dump() for r in records]
    stats = {
        "retrieved": tracker.records_identified,
        "after_dedup": tracker.records_after_dedup,
        "screened": tracker.records_screened,
        "included": tracker.studies_included,
    }
    return dict_papers, stats


_SCREEN_STOPWORDS = frozenset({
    "about", "after", "all", "also", "among", "analysis", "and", "any", "are",
    "based", "been", "between", "both", "but", "can", "could", "does", "during",
    "each", "for", "from", "had", "has", "have", "her", "him", "his", "how",
    "into", "its", "may", "more", "most", "not", "one", "other", "our", "out",
    "over", "paper", "per", "research", "review", "see", "she", "should",
    "studies", "study", "such", "than", "that", "the", "their", "there",
    "these", "they", "this", "those", "through", "two", "under", "use", "using",
    "via", "was", "we", "were", "what", "when", "where", "which", "while",
    "who", "why", "with", "would", "you",
})
_SCREEN_TOKEN_RE = re.compile(r"[a-z0-9]{3,}")


def _protocol_value(protocol: Any | None, key: str) -> Any:
    if protocol is None:
        return None
    if isinstance(protocol, dict):
        return protocol.get(key)
    return getattr(protocol, key, None)


def _protocol_list(protocol: Any | None, key: str) -> list[str]:
    value = _protocol_value(protocol, key) or []
    if isinstance(value, str):
        value = [value]
    return [str(item).strip() for item in value if str(item).strip()]


def _candidate_terms(
    problem_statement: str,
    objectives: list[str] | None,
    protocol: Any | None,
) -> set[str]:
    """Cheap keyword/PICOC terms used to rank the candidate pool before scoring."""
    parts: list[str] = [problem_statement or ""]
    parts.extend(str(objective or "") for objective in (objectives or []))
    for key in ("population", "intervention", "comparison", "context"):
        value = _protocol_value(protocol, key)
        if value:
            parts.append(str(value))
    parts.extend(_protocol_list(protocol, "outcomes"))
    text = " ".join(parts).lower()
    return {
        token for token in _SCREEN_TOKEN_RE.findall(text)
        if token not in _SCREEN_STOPWORDS
    }


def _candidate_pre_score(paper: PaperRecord, terms: set[str]) -> int:
    """Title overlap counts double; abstract overlap breaks ties."""
    if not terms:
        return 0
    title_tokens = set(_SCREEN_TOKEN_RE.findall(str(paper.title or "").lower()))
    abstract_tokens = set(_SCREEN_TOKEN_RE.findall(str(paper.abstract or "").lower()))
    return 2 * len(terms & title_tokens) + len(terms & abstract_tokens)


def _select_candidates(
    papers: list[PaperRecord],
    limit: int,
    problem_statement: str = "",
    objectives: list[str] | None = None,
    protocol: Any | None = None,
) -> list[PaperRecord]:
    """Select candidate pool for screening using round-robin distribution across sources.

    Within each source, records are ordered by a cheap title/abstract overlap with
    the problem statement and PICOC terms (then citations) so the most promising
    records are scored first when the pool is capped.
    """
    terms = _candidate_terms(problem_statement, objectives, protocol)
    by_source: dict[str, list[PaperRecord]] = {}
    for p in papers:
        by_source.setdefault(p.retrieval_source, []).append(p)

    for bucket in by_source.values():
        bucket.sort(
            key=lambda p: (_candidate_pre_score(p, terms), p.citation_count or 0),
            reverse=True,
        )

    candidates: list[PaperRecord] = []
    buckets = list(by_source.values())
    idx = 0
    while len(candidates) < limit and any(idx < len(b) for b in buckets):
        for bucket in buckets:
            if idx < len(bucket):
                candidates.append(bucket[idx])
                if len(candidates) >= limit:
                    break
        idx += 1
    return candidates


def _format_picoc(protocol: Any | None) -> str:
    """Render the PICOC scope and eligibility criteria for the screening prompt."""
    if protocol is None:
        return ""

    lines: list[str] = []
    for label, key in (
        ("Population", "population"),
        ("Intervention", "intervention"),
        ("Comparison", "comparison"),
        ("Context", "context"),
    ):
        value = _protocol_value(protocol, key)
        if value:
            lines.append(f"- {label}: {value}")

    outcomes = _protocol_list(protocol, "outcomes")
    if outcomes:
        lines.append("- Outcomes: " + "; ".join(outcomes))

    inclusion = _protocol_list(protocol, "inclusion_criteria")
    if inclusion:
        lines.append("- Inclusion criteria:")
        lines.extend(f"  * {item}" for item in inclusion)

    exclusion = _protocol_list(protocol, "exclusion_criteria")
    if exclusion:
        lines.append("- Exclusion criteria:")
        lines.extend(f"  * {item}" for item in exclusion)

    return "\n".join(lines)


_PROTOCOL_TITLE_RE = re.compile(r"\bprotocol\b", re.IGNORECASE)
_PROTOCOL_RESULTS_RE = re.compile(
    r"\b(?:results?|findings?)\s*[:.]|"
    r"\b(?:we|authors?)\s+(?:found|observed|report(?:ed)?)\b|"
    r"\b(?:were|was)\s+(?:observed|found|reported)\b|"
    r"\bp\s*[<=>]\s*0?\.\d+|"
    r"\b(?:confidence interval|effect size|mean difference|odds ratio|hazard ratio)\b",
    re.IGNORECASE,
)


def _is_protocol_without_results(paper: PaperRecord) -> bool:
    """Detect a study protocol that reports no empirical results.

    Protocols are a protocol-level exclusion, not a relevance judgement: a
    protocol's abstract describes planned methods and can score highly on
    topic terms while contributing no findings. Title matching is deliberately
    narrow ("protocol") and any explicit results language keeps the paper in
    the corpus.
    """
    title = str(paper.title or "")
    abstract = str(paper.abstract or "")
    if not _PROTOCOL_TITLE_RE.search(title) and not re.search(
        r"\bprotocol\b", abstract[:500], re.IGNORECASE
    ):
        return False
    return not _PROTOCOL_RESULTS_RE.search(abstract)


_RCT_ONLY_RE = re.compile(r"\brandomi[sz]ed\b|\brandomi[sz]ation\b|\brcts?\b", re.IGNORECASE)
_REVIEW_DESIGN_RE = re.compile(
    r"\b(?:systematic review|meta-?analysis|network meta-?analysis|scoping review|"
    r"umbrella review|literature review|narrative review)\b",
    re.IGNORECASE,
)


def _protocol_requires_rcts(protocol: Any | None) -> bool:
    """True when the approved protocol restricts inclusion to randomised trials."""
    return bool(_RCT_ONLY_RE.search(" ".join(_protocol_list(protocol, "inclusion_criteria"))))


def _excluded_by_study_design(paper: PaperRecord, requires_rcts: bool) -> bool:
    """Deterministic design gate: a review is not an RCT, however relevant.

    Relevance screening alone admitted a network meta-analysis and a systematic
    review into an RCT-only corpus. Title/abstract design labels are reliable
    for reviews, and ``study_type`` is set by retrieval when known.
    """
    if not requires_rcts:
        return False
    study_type = str(getattr(paper, "study_type", "") or "").lower()
    if "review" in study_type or "meta-analysis" in study_type or "meta analysis" in study_type:
        return True
    return bool(
        _REVIEW_DESIGN_RE.search(str(paper.title or ""))
        or _REVIEW_DESIGN_RE.search(str(paper.abstract or "")[:400])
    )


def _screen_batch_timeout_seconds() -> float:
    """Per-batch LLM timeout, overridable by SCREEN_BATCH_TIMEOUT_SECONDS."""
    raw = os.getenv("SCREEN_BATCH_TIMEOUT_SECONDS", "").strip()
    if not raw:
        return SCREEN_BATCH_TIMEOUT_SECONDS
    try:
        value = float(raw)
    except ValueError:
        logger.warning(
            "Ignoring invalid SCREEN_BATCH_TIMEOUT_SECONDS=%r; using %.0fs.",
            raw,
            SCREEN_BATCH_TIMEOUT_SECONDS,
        )
        return SCREEN_BATCH_TIMEOUT_SECONDS
    if value <= 0:
        logger.warning(
            "Ignoring non-positive SCREEN_BATCH_TIMEOUT_SECONDS=%r; using %.0fs.",
            raw,
            SCREEN_BATCH_TIMEOUT_SECONDS,
        )
        return SCREEN_BATCH_TIMEOUT_SECONDS
    return value


def _screening_prompt(
    batch: list[PaperRecord],
    problem_statement: str,
    objectives_str: str,
    criteria_str: str,
) -> str:
    prompt = f"""You are screening records for a systematic review.
Problem Statement:
{problem_statement}

Research Objectives:
{objectives_str}

PICOC scope and eligibility criteria:
{criteria_str or "(no protocol criteria supplied; use the problem statement and objectives)"}

Score each paper's eligibility for this review on a 1-10 relevance scale:
- 9-10: directly answers the research question; population, intervention, and
  study design all match; reports empirical results for the review outcome.
- 7-8: clearly relevant with only a minor mismatch (for example mixed age
  groups, a related population, anxiety plus depression, a closely related
  digital CBT format, or a different but defensible comparator).
- 5-6: related but indirect (same broad topic but a different intervention,
  population, outcome, or setting, or useful background only).
- 1-4: off topic, wrong population or intervention, no relevant outcome, or a
  study protocol, editorial, opinion piece, or bibliometric analysis without
  reported results.

Scoring rules:
- Judge relevance against the problem statement and PICOC, not general interest.
- A paper that merely mentions the topic must not score 7 or above.
- Study protocols without reported results always score 1-4, even when the
  topic matches perfectly.
- Use the full 1-10 range; do not default to a middle score.

Papers:
"""
    for idx, p in enumerate(batch):
        prompt += f"\n[{idx+1}] Title: {p.title}\nAbstract: {(p.abstract or '')[:600]}\n"

    prompt += """\nReturn ONLY a JSON array of objects with fields 'id' (1-based index) and 'score' (1-10). Example:
[{"id": 1, "score": 9}, {"id": 2, "score": 4}]"""
    return prompt


def _parse_score_map(raw: str, batch_size: int) -> dict[int, float]:
    """Parse and validate a batch score payload; invalid entries stay unscored."""
    if "```" in raw:
        raw = raw.split("```")[1]
        raw = raw.removeprefix("json")
    cleaned = raw.strip()
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError:
        # Models occasionally emit a Python-style list with single quotes.
        # literal_eval parses the value without executing anything.
        payload = ast.literal_eval(cleaned)
    if not isinstance(payload, list):
        raise TypeError(f"score payload is not a list (got {type(payload).__name__})")

    score_map: dict[int, float] = {}
    for item in payload:
        if not isinstance(item, dict):
            continue
        try:
            paper_id = int(item.get("id"))
            score = float(item.get("score"))
        except (TypeError, ValueError):
            continue
        if 1 <= paper_id <= batch_size and 1.0 <= score <= 10.0:
            score_map[paper_id] = score
    return score_map


async def _request_batch_scores(
    llm,
    batch: list[PaperRecord],
    problem_statement: str,
    objectives_str: str,
    semaphore: asyncio.Semaphore,
    criteria_str: str,
    timeout: float,
) -> dict[int, float] | None:
    """One scoring call. Returns None when the call or its payload failed."""
    prompt = _screening_prompt(batch, problem_statement, objectives_str, criteria_str)
    async with semaphore:
        try:
            res = await asyncio.wait_for(ainvoke_llm(llm, prompt), timeout=timeout)
            return _parse_score_map(str(res.content).strip(), len(batch))
        except ReportGenerationError:
            raise
        except Exception as e:  # noqa: BLE001 - any provider or parse failure leaves the batch unscored
            # Only the exception type: provider messages can echo prompts and keys.
            logger.warning(
                "Screening batch scoring failed (%s) for %d papers; retrying split.",
                type(e).__name__,
                len(batch),
            )
            return None


async def _score_batch(
    llm,
    batch: list[PaperRecord],
    problem_statement: str,
    objectives_str: str,
    semaphore: asyncio.Semaphore,
    criteria_str: str = "",
    *,
    timeout: float | None = None,
    allow_split: bool = True,
) -> list[PaperRecord]:
    """Score one batch of papers, retrying a failure once as two half-batches.

    A failed call never becomes a neutral score: every paper it did not cover is
    returned with ``relevance_score`` left as None so the caller can decide
    whether the run still has enough signal to continue.
    """
    batch_timeout = timeout if timeout is not None else _screen_batch_timeout_seconds()
    score_map = await _request_batch_scores(
        llm, batch, problem_statement, objectives_str, semaphore, criteria_str, batch_timeout
    )

    if score_map is None and allow_split and len(batch) > 1:
        midpoint = len(batch) // 2
        halves = await asyncio.gather(
            _score_batch(
                llm, batch[:midpoint], problem_statement, objectives_str, semaphore,
                criteria_str, timeout=batch_timeout, allow_split=False,
            ),
            _score_batch(
                llm, batch[midpoint:], problem_statement, objectives_str, semaphore,
                criteria_str, timeout=batch_timeout, allow_split=False,
            ),
        )
        return [paper for half in halves for paper in half]

    if score_map is None:
        logger.warning(
            "Screening batch left %d papers unscored after retry (timeout %ss).",
            len(batch),
            batch_timeout,
        )

    scored: list[PaperRecord] = []
    for idx, paper in enumerate(batch):
        copy = paper.model_copy(deep=True)
        copy.relevance_score = (score_map or {}).get(idx + 1)
        scored.append(copy)
    return scored


async def screen_papers_structured(
    papers: list[PaperRecord],
    problem_statement: str,
    objectives: list[str],
    tracker: PRISMATracker | None = None,
    model: str | None = None,
    protocol: Any | None = None,
) -> tuple[list[PaperRecord], PRISMATracker]:
    """Screen candidate corpus for relevance, updating PRISMATracker deterministically."""
    tr = tracker.model_copy(deep=True) if tracker else PRISMATracker(
        records_identified=len(papers),
        records_after_dedup=len(papers)
    )

    # The corpus handed to screening is authoritative. Citation expansion could
    # otherwise leave records_after_dedup one behind records_screened. Keep
    # identified - duplicates == after_dedup true.
    if tr.records_after_dedup != len(papers):
        logger.warning(
            "PRISMA tracker after_dedup=%s does not match screening corpus=%s; reconciling.",
            tr.records_after_dedup,
            len(papers),
        )
        tr.records_after_dedup = len(papers)
        tr.records_identified = len(papers) + tr.duplicates_removed

    retracted_papers = [p for p in papers if getattr(p, "is_retracted", False) is True]
    retracted_count = len(retracted_papers)
    for p in retracted_papers:
        p.screening_status = "excluded"
        p.exclusion_reason = "Retracted publication"
    if retracted_count:
        logger.info(f"Excluding {retracted_count} retracted papers before title/abstract screening.")

    protocol_excluded_papers: list[PaperRecord] = []
    design_excluded_papers: list[PaperRecord] = []
    screenable: list[PaperRecord] = []
    requires_rcts = _protocol_requires_rcts(protocol)
    for p in papers:
        if getattr(p, "is_retracted", False) is True:
            continue
        if _is_protocol_without_results(p):
            p.screening_status = "excluded"
            p.exclusion_reason = "Protocol without reported results"
            protocol_excluded_papers.append(p)
            continue
        if _excluded_by_study_design(p, requires_rcts):
            p.screening_status = "excluded"
            p.exclusion_reason = "Study design does not match protocol (RCTs only)"
            design_excluded_papers.append(p)
            continue
        screenable.append(p)

    protocol_excluded_count = len(protocol_excluded_papers)
    design_excluded_count = len(design_excluded_papers)
    if protocol_excluded_count:
        logger.info(
            f"Excluding {protocol_excluded_count} study protocols without reported results "
            "before relevance screening."
        )
    if design_excluded_count:
        logger.info(
            f"Excluding {design_excluded_count} reviews/meta-analyses: the protocol is RCT-only."
        )

    if len(papers) <= SCREEN_THRESHOLD:
        tr.records_screened = len(papers)
        tr.excluded_title_abstract = (
            retracted_count + protocol_excluded_count + design_excluded_count
        )
        tr.full_text_requested = len(screenable)
        tr.full_text_assessed = len(screenable)
        tr.studies_included = len(screenable)
        copied_papers = []
        for p in screenable:
            p_copy = p.model_copy(deep=True)
            p_copy.screening_status = "included"
            copied_papers.append(p_copy)
        if protocol_excluded_count:
            tr.exclusion_reasons["Protocol without reported results"] = protocol_excluded_count
        if design_excluded_count:
            tr.exclusion_reasons["Study design does not match protocol (RCTs only)"] = design_excluded_count
        if retracted_count:
            tr.exclusion_reasons["Retracted publication"] = retracted_count
        return (
            copied_papers + protocol_excluded_papers + design_excluded_papers + retracted_papers,
            tr,
        )

    candidates = _select_candidates(
        screenable,
        SCREEN_CANDIDATES,
        problem_statement=problem_statement,
        objectives=objectives,
        protocol=protocol,
    )
    tr.records_screened = len(papers)
    logger.info(f"Screening {len(candidates)} candidates for relevance against problem statement...")

    llm = get_llm(model=model, role="researcher")
    objectives_str = "\n".join(f"- {obj}" for obj in objectives)
    criteria_str = _format_picoc(protocol)
    semaphore = asyncio.Semaphore(SCREEN_CONCURRENCY)
    batch_timeout = _screen_batch_timeout_seconds()

    batches = [
        candidates[i:i + SCREEN_BATCH_SIZE]
        for i in range(0, len(candidates), SCREEN_BATCH_SIZE)
    ]

    tasks = [
        _score_batch(
            llm, b, problem_statement, objectives_str, semaphore, criteria_str,
            timeout=batch_timeout,
        )
        for b in batches
    ]

    results = await asyncio.gather(
        *tasks,
        return_exceptions=True
    )

    scored_papers: list[PaperRecord] = []
    for batch, res in zip(batches, results):
        if isinstance(res, ReportGenerationError):
            raise res
        if isinstance(res, list) and len(res) == len(batch):
            scored_papers.extend(res)
            continue
        logger.warning(f"Screening batch raised: {type(res).__name__}")
        scored_papers.extend(paper.model_copy(deep=True) for paper in batch)

    scored_papers.sort(
        key=lambda p: (p.relevance_score or 0, p.citation_count or 0),
        reverse=True
    )
    total_candidates = len(candidates)
    unscored_papers = [p for p in scored_papers if p.relevance_score is None]
    scored_only = [p for p in scored_papers if p.relevance_score is not None]

    # An unscored paper must never be treated as a neutral 5: that silently
    # admits irrelevant records or (with a relevance floor) discards relevant
    # ones. If too much of the pool failed we refuse to write a paper at all.
    if total_candidates and len(unscored_papers) / total_candidates > SCREEN_MAX_UNSCORED_FRACTION:
        logger.error(
            "Screening failed: %d of %d candidates (%.0f%%) are unscored after retries; "
            "refusing to generate a report.",
            len(unscored_papers),
            total_candidates,
            100.0 * len(unscored_papers) / total_candidates,
        )
        raise ReportGenerationError(
            f"screening_unscored: {len(unscored_papers)} of {total_candidates} candidates"
        )

    strong = [p for p in scored_only if (p.relevance_score or 0) >= SCREEN_MIN_RELEVANCE]
    above_top_up_floor = [p for p in scored_only if (p.relevance_score or 0) >= SCREEN_TOP_UP_FLOOR]

    if total_candidates and len(above_top_up_floor) < SCREEN_MIN_SCORED:
        logger.error(
            "Screening failed: only %d scored candidates reached %.0f/10 (minimum %d).",
            len(above_top_up_floor),
            SCREEN_TOP_UP_FLOOR,
            SCREEN_MIN_SCORED,
        )
        raise ReportGenerationError(
            f"insufficient_relevant_studies: {len(above_top_up_floor)} relevant"
        )

    # Include every strong paper up to SCREEN_KEEP; top up a short pool with the
    # best related-but-indirect papers so a single strict batch cannot reduce the
    # paper to one study.
    included_set: list[PaperRecord] = list(strong[:SCREEN_KEEP])
    included_ids = {id(p) for p in included_set}
    top_up: list[PaperRecord] = []
    if len(included_set) < SCREEN_MIN_INCLUDED:
        for p in above_top_up_floor:
            if id(p) in included_ids:
                continue
            if len(included_set) >= SCREEN_MIN_INCLUDED or len(included_set) >= SCREEN_KEEP:
                break
            included_set.append(p)
            included_ids.add(id(p))
            top_up.append(p)

    # Source diversity is a tie-breaker, not an override: at most one paper per
    # source outside the cutoff, only above the relevance floor, and never past
    # SCREEN_KEEP.
    by_source: dict[str, list[PaperRecord]] = {}
    for p in strong:
        by_source.setdefault(p.retrieval_source, []).append(p)

    guaranteed: list[PaperRecord] = []
    for src_papers in by_source.values():
        if len(included_set) >= SCREEN_KEEP:
            break
        present = [p for p in src_papers if id(p) in included_ids]
        if len(present) >= SCREEN_SOURCE_GUARANTEE:
            continue
        for p in src_papers:
            if id(p) in included_ids:
                continue
            included_set.append(p)
            included_ids.add(id(p))
            guaranteed.append(p)
            break

    included = included_set
    excluded_scored = [p for p in scored_only if id(p) not in included_ids]
    top_up_ids = {id(p) for p in top_up}
    excluded_count = max(0, len(candidates) - len(included))
    # Deduplicated papers that never entered the candidate pool are still
    # screened records in PRISMA terms: account for them as title/abstract
    # exclusions so records_screened - excluded == full_text_requested holds.
    unscreened_surplus = max(0, len(screenable) - len(candidates))

    for p in included:
        p.screening_status = "included"
        if id(p) in top_up_ids:
            p.exclusion_reason = (
                f"Included via relevance top-up ({p.relevance_score:.1f}/10): below the "
                f"{SCREEN_MIN_RELEVANCE:.0f} inclusion threshold but at or above "
                f"{SCREEN_TOP_UP_FLOOR:.0f}, and the review minimum is {SCREEN_MIN_INCLUDED} studies"
            )
    for p in excluded_scored:
        p.screening_status = "excluded"
        score = p.relevance_score if p.relevance_score is not None else 0.0
        if score < SCREEN_MIN_RELEVANCE:
            p.exclusion_reason = (
                f"Below relevance floor ({score:.1f}/{SCREEN_MIN_RELEVANCE:.0f}) "
                "during title/abstract screening"
            )
        else:
            p.exclusion_reason = (
                f"Outranked by higher-relevance studies ({score:.1f}/10) "
                "during title/abstract screening"
            )
    for p in unscored_papers:
        p.screening_status = "excluded"
        p.exclusion_reason = "Not scored: screening batch failure"

    tr.excluded_title_abstract = (
        excluded_count
        + unscreened_surplus
        + retracted_count
        + protocol_excluded_count
        + design_excluded_count
    )
    tr.full_text_requested = len(included)
    tr.full_text_assessed = len(included)
    tr.studies_included = len(included)
    tr.exclusion_reasons["Title/abstract irrelevance"] = len(excluded_scored)
    if unscored_papers:
        tr.exclusion_reasons["Not scored: screening batch failure"] = len(unscored_papers)
    if unscreened_surplus:
        tr.exclusion_reasons["Not screened: candidate pool limit"] = unscreened_surplus
    if protocol_excluded_count:
        tr.exclusion_reasons["Protocol without reported results"] = protocol_excluded_count
    if design_excluded_count:
        tr.exclusion_reasons["Study design does not match protocol (RCTs only)"] = design_excluded_count
    if retracted_count:
        tr.exclusion_reasons["Retracted publication"] = retracted_count

    logger.info(
        f"Screening complete: {len(included)} included ({len(top_up)} via top-up, "
        f"{len(guaranteed)} via source-guarantee), {len(excluded_scored)} scored exclusions, "
        f"{len(unscored_papers)} unscored, {unscreened_surplus} not screened (candidate pool limit), "
        f"{protocol_excluded_count} protocols, {design_excluded_count} non-RCT designs, "
        f"{retracted_count} retracted."
    )
    return (
        included
        + protocol_excluded_papers
        + design_excluded_papers
        + retracted_papers
    ), tr


async def screen_papers(
    papers: list[dict[str, Any]],
    problem_statement: str,
    objectives: list[str],
    model: str | None = None
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Legacy screening adapter returning dicts and stats."""
    records = [PaperRecord.from_dict(p) for p in papers]
    screened_records, tracker = await screen_papers_structured(records, problem_statement, objectives, model=model)
    dict_screened = [r.model_dump() for r in screened_records]
    stats = {
        "screened": tracker.records_screened,
        "included": tracker.studies_included,
        "retrieved": tracker.records_identified,
        "after_dedup": tracker.records_after_dedup,
    }
    return dict_screened, stats


_HTML_TAG_RE = re.compile(r"<[^>]+>")
_APA_NAME_PARTICLES = {
    "da", "de", "del", "della", "der", "di", "dos", "du", "la", "le",
    "ten", "ter", "van", "von",
}


def _clean_metadata_text(value: Any) -> str:
    """Strip HTML markup/entities and collapse whitespace in metadata fields."""
    text = html.unescape(str(value or ""))
    text = _HTML_TAG_RE.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


def _titlecase_apa_token(token: str) -> str:
    if not token:
        return ""
    if re.fullmatch(r"[A-Za-z]\.", token):
        return token.upper()
    lowered = token.casefold()
    if lowered in _APA_NAME_PARTICLES:
        return lowered
    if "-" in token:
        return "-".join(
            part[:1].upper() + part[1:].lower() if part else part
            for part in token.split("-")
        )
    return token[:1].upper() + token[1:].lower()


def _normalize_apa_author(author: Any) -> str:
    """Normalize one author name while preserving already-correct casing.

    Lowercase source records ("john a. powell") and all-caps records
    ("CHEN SJ") are title-cased. Mixed-case names pass through unchanged so
    names such as "McDonald" and hyphenated surnames keep their spelling.
    """
    name = _clean_metadata_text(author)
    if not name:
        return ""

    letters = [character for character in name if character.isalpha()]
    if not letters:
        return name
    if not (name == name.lower() or name == name.upper()):
        return name

    if "," in name:
        family, given = name.split(",", 1)
        family_clean = " ".join(_titlecase_apa_token(part) for part in family.split())
        given_clean = " ".join(_titlecase_apa_token(part) for part in given.split())
        return f"{family_clean}, {given_clean}".strip().rstrip(",").strip()

    return " ".join(_titlecase_apa_token(part) for part in name.split())


def _clean_reference_doi(doi: Any) -> str:
    """Strip DOI URL/prefix wrappers but keep the registry's original casing."""
    value = _clean_metadata_text(doi)
    value = re.sub(r"^(https?://(?:dx\.)?doi\.org/|doi:\s*)", "", value, flags=re.IGNORECASE)
    return value.strip().rstrip(".,;")


def _sentence_ending(text: str) -> str:
    return text if text.endswith(("?", "!", ".")) else f"{text}."


def _fix_reference_punctuation(text: str) -> str:
    cleaned = re.sub(r"\s+([,.;:!?])", r"\1", text)
    cleaned = re.sub(r"([,;:])\s*([.;])", r"\2", cleaned)
    cleaned = re.sub(r"(?<!\.)\.\.(?!\.)", ".", cleaned)
    cleaned = re.sub(r"\?\s*\.", "?", cleaned)
    cleaned = re.sub(r"!\s*\.", "!", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    return cleaned.strip()


def format_apa(paper: dict[str, Any]) -> str:
    """Format paper metadata into a clean APA 7th edition reference string."""
    raw_authors = [
        _normalize_apa_author(author)
        for author in (paper.get("authors") or [])
        if _clean_metadata_text(author)
    ]
    if not raw_authors:
        author_str = "Anonymous"
    elif len(raw_authors) == 1:
        author_str = raw_authors[0]
    elif len(raw_authors) == 2:
        author_str = f"{raw_authors[0]} & {raw_authors[1]}"
    elif len(raw_authors) <= 7:
        author_str = ", ".join(raw_authors[:-1]) + f", & {raw_authors[-1]}"
    else:
        author_str = ", ".join(raw_authors[:6]) + ", et al."

    year = _clean_metadata_text(paper.get("year")) or "n.d."
    title = _clean_metadata_text(paper.get("title")).rstrip(" .") or "Untitled"
    doi = _clean_reference_doi(paper.get("doi"))
    url = _clean_metadata_text(paper.get("url") or paper.get("source_url"))

    source = _clean_metadata_text(paper.get("venue") or paper.get("source", "")).rstrip(" .")
    link = f"https://doi.org/{doi}" if doi else url

    parts = [f"{author_str} ({year}).", _sentence_ending(title)]
    if source:
        parts.append(_sentence_ending(source))
    if link:
        parts.append(link)

    apa_str = _fix_reference_punctuation(" ".join(parts))
    # A retracted reference must be recognisable at a glance in the reference list.
    return f"[RETRACTED] {apa_str}" if paper.get("is_retracted") is True else apa_str

