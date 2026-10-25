---
name: citeproof-review
description: Guide research agents through running or auditing a systematic literature review with CiteProof MCP tools, maintaining a verifiable local audit trail of search, screening, extraction, and bias assessments.
---

# CiteProof Review Skill

This skill guides research agents in executing or auditing systematic literature reviews using the CiteProof Model Context Protocol (MCP) server.

## Role Split

CiteProof enforces a strict division of responsibility between the agent and the audit system:

- **The Research Agent**:
  - Searches academic databases and retrieves candidate papers.
  - Reads paper titles, abstracts, and full texts.
  - Makes all scientific and methodological judgements (e.g. inclusion/exclusion, risk-of-bias ratings, extracted findings, and claim veracity).
  - Supplies source text excerpts, decisions, and citation data to the MCP tools.

- **CiteProof MCP**:
  - Contains **no language model** and requires no model API keys.
  - Does **not** search literature, crawl the web, or read PDFs independently.
  - Deterministically stores review protocols, search logs, screening decisions, extracted data, bias assessments, and claims.
  - Verifies verbatim quotations against supplied source texts (`quote_verified`) and checks that extracted numbers appear within recorded quotes.
  - Validates PRISMA accounting invariants and calculates dual-reviewer agreement (Cohen's kappa).
  - Applies published deterministic rule sets for overall risk-of-bias summaries.
  - Verifies DOI registration with official registries (Crossref and doi.org).
  - Exports a unified, reproducible markdown audit bundle.

## Local Storage and Privacy

All review data is stored locally on your machine:
- Review records live in SQLite databases in `<CITEPROOF_MCP_HOME>/reviews/<review_id>.db` (default: `~/.citeproof-mcp/reviews/<review_id>.db`).
- Full source texts supplied for evidence and claim checks are **never** stored on disk; CiteProof stores only the text length and a SHA-256 digest alongside the verification flags. The **quotes** you record with evidence rows and claims are stored in the review file.
- No telemetry, analytics, or review content is uploaded to any remote server.
- The **only** network calls made by CiteProof MCP are outbound HTTPS queries to Crossref (`api.crossref.org`) and doi.org in `verify_dois`.

## Paper Identity (`paper_ref`)

Every paper must be referenced consistently across all tools using a valid `paper_ref` string. Supported formats:

1. **DOI**: Normalized lowercase DOI string, stripped of `doi:` or URL prefixes (e.g. `10.1038/s41586-021-03819-2`). Input strings like `https://doi.org/10.1038/s41586-021-03819-2` or `doi:10.1038/s41586-021-03819-2` are automatically canonicalized.
2. **PMID**: `pmid:<digits>` (e.g. `pmid:34265844`).
3. **Title and Year**: `title:<title>|<year>` (e.g. `title:drug x in hypertension|2021` or `title:...|nd` if no publication year is known).

*Note: Always use the exact same `paper_ref` identifier for a given study across screening, evidence extraction, risk of bias, and claim checks.*

## Error Handling (`ok: false`)

Every CiteProof MCP tool returns a JSON object with a boolean `ok` field:
- When `ok: true`, the operation succeeded. Inspect the returned fields for results, counts, and advisory `warnings`.
- When `ok: false`, the operation was refused or failed validation. Inspect the `error` string. The error message explicitly states the problem (for example, missing mandatory fields, invalid dates, unrecorded deduplication, or mismatched counts).
- Tools fail closed and never return unhandled Python exceptions or stack traces. When `ok: false` is returned, read the `error` message, correct the input data or execute prerequisite steps, and retry.

## Review Workflow Order

Execute tools in the following sequence as defined in `MCP/citeproof_mcp/server.py` and `MCP/docs/SPEC.md`:

```text
review_create
  │
  ▼
protocol_record
  │
  ▼
prisma_record_search (log every database search)
  │
  ▼
dedup_papers (run over complete exported hits with review_id)
  │
  ▼
screening_record / screening_record_many
  ├─ Phase 1: stage="title_abstract" (include / exclude)
  ├─ Phase 2: stage="full_text" (include / exclude / not_retrieved; reason required for exclude)
  └─ Optional dual-screening: record reviewer A & B, run screening_agreement, record consensus as reviewer="primary"
  │
  ▼
evidence_record (per included study and outcome; supply quote and source_text)
  │
  ▼
rob_record (per study domain) ──► rob_summary
  │
  ▼
claim_record (link report claims to evidence) ──► claim_report
  │
  ▼
verify_dois & resolve_citations (validate reference list and in-text citations)
  │
  ▼
prisma_flow_diagram, prisma_checklist, lint_prose (audit reporting draft)
  │
  ▼
export_bundle (generate comprehensive audit trail document)
```

You can call `review_status` at any time to inspect the current review phase, identify incomplete steps, and review suggested next actions.

---

## Tool Reference

### 1. Setup and Protocol
- `review_create(title, question, year_min=None, year_max=None)`:
  Initializes a review database. Returns `review_id` and initial guidance.
- `review_list(limit=50)`:
  Lists existing reviews ordered newest first.
- `protocol_record(review_id, population=None, intervention=None, comparator=None, outcomes=None, study_designs=None, inclusion_criteria=None, exclusion_criteria=None, databases=None, search_strategy=None, languages=None, registration=None, rob_tool=None, synthesis_method=None, year_min=None, year_max=None)`:
  Records or updates review protocol parameters. Tracks amendments in `protocol_changes`.
  Permitted `rob_tool` values: `rob2`, `robins_i`, `nos`, `nos_case_control`, `mixed`, `none`.

### 2. Search and Deduplication
- `prisma_record_search(review_id, source, query, date_run, hits)`:
  Logs search yields (e.g. source="PubMed", date_run="YYYY-MM-DD", hits=150). Adding or changing hit counts resets existing deduplication records.
- `dedup_papers(papers, review_id=None)`:
  Deterministic deduplication over candidate paper objects (`id`, `doi`, `pmid`, `title`, `year`, `authors`/`first_author`, `abstract`). When `review_id` is passed, `records_checked` must match total search hits.
- `prisma_record_dedup(review_id, duplicates_removed, method)`:
  Manually records duplicate removal counts when deduplication was performed outside `dedup_papers`.

### 3. Screening and Agreement
- `screening_record(review_id, paper_ref, stage, decision, reason=None, reviewer="primary", title=None)`:
  Records a screening decision. `stage`: `title_abstract` or `full_text`. `decision`: `include`, `exclude`, or `not_retrieved` (full_text only). `reason` is required for `exclude`.
- `screening_record_many(review_id, decisions)`:
  Batch records up to 500 screening decisions.
- `screening_list(review_id, stage=None, reviewer=None, decision=None, paper_ref=None, limit=200, offset=0)`:
  Queries recorded screening decisions.
- `screening_agreement(review_id, stage, reviewer_a, reviewer_b)`:
  Computes Cohen's kappa, raw agreement percentage, contingency table, and Landis–Koch strength interpretation between two reviewers.

### 4. PRISMA Tracking and Verification
- `prisma_status(review_id, reviewer=None)`:
  Computes complete PRISMA 2020 flow counts and checks accounting invariants (identified = deduplicated + duplicates; screened = include + exclude; reports sought = assessed + not retrieved + pending).
- `prisma_flow_diagram(review_id, format="mermaid", reviewer=None)`:
  Generates flow diagram in `mermaid`, `svg`, or `markdown`.
- `prisma_checklist(review_id, report_text, reviewer=None)`:
  Performs keyword matching across 27 PRISMA 2020 items and checks reported numbers against recorded counts.

### 5. Evidence Extraction and Risk of Bias
- `evidence_record(review_id, paper_ref, outcome, study_design, quote, source_text, sample_size=None, population=None, intervention=None, comparator=None, timepoint=None, effect_measure=None, effect_estimate=None, ci_lower=None, ci_upper=None, ci_level=0.95, notes=None)`:
  Stores structured evidence rows. Verifies quote presence in `source_text` (`quote_verified`) and checks that numerical figures in estimates appear in the quote.
  Permitted `effect_measure` values: `rr`, `or`, `hr`, `irr`, `rom`, `rd`, `md`, `smd`, `other`.
- `evidence_table(review_id, format="markdown", paper_ref=None)`:
  Renders extracted evidence rows as markdown, CSV, or JSON.
- `rob_record(review_id, paper_ref, tool, domain, judgement, quote="", source_text="", rationale=None)`:
  Records domain-level risk of bias. Tools: `rob2` (domains D1–D5, overall; judgements low, some_concerns, high), `robins_i` (domains D1–D7, overall; judgements low, moderate, serious, critical, no_information), `nos` or `nos_case_control` (domains selection, comparability, outcome/exposure; star counts).
- `rob_summary(review_id)`:
  Computes overall study bias judgements and traffic-light summaries using deterministic published rules.

### 6. Claims, Citations, and Prose
- `claim_record(review_id, claim_text, paper_ref, label, reason, source_text="", quote="")`:
  Registers key synthesis claims with status `supported`, `partial`, or `unsupported`. Verifies supporting quotations against `source_text`.
- `claim_report(review_id, report_text=None)`:
  Summarizes claim support status and flags weak or unverified claims appearing in `report_text`.
- `verify_dois(dois, review_id=None)`:
  Performs network lookups against Crossref and doi.org to verify DOI registration and retraction status. Saves results when `review_id` is supplied.
- `resolve_citations(text, papers)`:
  Parses author-year and numeric citations in `text` and matches them against candidate paper metadata dictionaries.
- `lint_prose(text)`:
  Scans prose for advisory issues such as vague attribution, unsupported certainty, or malformed citation brackets.

### 7. Review Status and Bundle Export
- `review_status(review_id, reviewer=None)`:
  Audits overall review progress across all stages, surfaces invariant warnings, and lists next action items.
- `export_bundle(review_id, format="markdown", reviewer=None)`:
  Exports the entire audit trail (protocol, PRISMA counts, flow diagram, screening log, agreement, evidence table, risk-of-bias summary, claims, and DOI verifications) into a single reproducible markdown document.

## Limitations

- **No Scientific Inference**: CiteProof checks inputs against structural rules and string matches. It cannot determine if an author's interpretation is correct or if a paper is methodologically sound.
- **Quote Verification Boundary**: Quote matching handles Unicode normalization, punctuation variations, and whitespace collapses. However, quotes that are paraphrased or translated will not verify against source text.
- **Network Scope**: Only `verify_dois` connects to the internet. If you are offline, DOIs will remain in an `unverified` state.
