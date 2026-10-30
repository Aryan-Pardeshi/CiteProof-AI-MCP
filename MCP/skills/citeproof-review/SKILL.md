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
protocol_record & recall_seed_record (record protocol and known-item seed set)
  │
  ▼
prisma_record_search (log every database search)
  │
  ▼
dedup_papers (run over complete exported hits with review_id)
  │
  ▼
recall_check (compare seed set against deduplicated retrieval)
  │
  ▼
reviewer_record & screening_stage_set (register reviewer types/models; configure blind/unblinded stage)
  │
  ▼
screening_record / screening_record_many, then screening_stage_close (blind stages only) and screening_agreement, once per stage:
  ├─ Phase 1: stage="title_abstract" (include / exclude). Close the stage if blind, check agreement, then record consensus
  │          as reviewer="primary" before any primary full_text decision (a full_text decision without a title_abstract
  │          include by the same reviewer is warned about and breaks the PRISMA invariants).
  └─ Phase 2: stage="full_text" (include / exclude / not_retrieved; reason required for exclude), same steps.
  Pass human-caution and same-model warnings from screening_agreement to the user.
  │
  ▼
study_link (group included reports into distinct studies)
  │
  ▼
evidence_record (per included study and outcome; supply quote and source_text)
  │
  ▼
study_overview (after evidence_record: duplicate_participant_risk is computed from stored evidence rows)
  │
  ▼
stats_check_ratio_ci, stats_recompute_2x2 & stats_check_evidence (check ratio/CI consistency and 2x2 data)
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
prisma_flow_diagram, prisma_checklist, prisma_excluded_studies, manuscript_check, lint_prose (audit reporting draft and full-text exclusions)
  │
  ▼
review_history (inspect audit trail lifecycle events)
  │
  ▼
export_bundle (save=true to write audit bundle with SHA-256 to disk; pass human-caution and same-model warnings to the user)
```

You can call `review_status` at any time to inspect the current review phase, identify incomplete steps, and review suggested next actions.

---

## Tool Reference

### 1. Setup, Protocol, and Validation Seeds
- `review_create(title, question, year_min=None, year_max=None)`:
  Initializes a review database. Returns `review_id` and initial guidance.
- `review_list(limit=50)`:
  Lists existing reviews ordered newest first.
- `protocol_record(review_id, population=None, intervention=None, comparator=None, outcomes=None, study_designs=None, inclusion_criteria=None, exclusion_criteria=None, databases=None, search_strategy=None, languages=None, registration=None, rob_tool=None, synthesis_method=None, year_min=None, year_max=None)`:
  Records or updates review protocol parameters. Tracks amendments in `protocol_changes`.
  Permitted `rob_tool` values: `rob2`, `robins_i`, `nos`, `nos_case_control`, `mixed`, `none`.
- `recall_seed_record(review_id, paper_refs, labels=None, replace=False)`:
  Records 1 to 200 known eligible papers as benchmark seeds for a known-item recall check. Stored as canonical refs. `replace=True` clears previous seeds. Does not search for seeds or decide eligibility.

### 2. Search, Deduplication, and Recall Validation
- `prisma_record_search(review_id, source, query, date_run, hits)`:
  Logs search yields (e.g. source="PubMed", date_run="YYYY-MM-DD", hits=150). Adding or changing hit counts resets existing deduplication records.
- `dedup_papers(papers, review_id=None)`:
  Deterministic deduplication over candidate paper objects (`id`, `doi`, `pmid`, `title`, `year`, `authors`/`first_author`, `abstract`). When `review_id` is passed, `records_checked` must match total search hits. Records aliases and conflicts (`aliases_recorded`, `alias_conflicts`).
- `prisma_record_dedup(review_id, duplicates_removed, method)`:
  Manually records duplicate removal counts when deduplication was performed outside `dedup_papers`.
- `recall_check(review_id)`:
  Compares recorded seed set with retrieved records of the last full dedup after alias resolution. Seeds are classified as `found`, `not_found`, or `unresolved`. Lists per-source counts. Note: seed recall does not prove the search was exhaustive; it only checks whether seeds were in the recorded export.

### 3. Screening Independence and Agreement
- `reviewer_record(review_id, reviewer, reviewer_type, model_id=None, run_ref=None)`:
  Records reviewer type ('human', 'model', or 'unknown') and model identity. Blinding and reviewer types are by convention, with an audit trail, not a guarantee. One agent can call tools under another reviewer's name, and two runs of the same model are not independent reviewers. Reviewer type is self-declared; no output may call a review verified or independent because of it. Reviewer 'primary' is reserved for consensus decisions and cannot be given a type. Does not verify caller identity or authenticate models.
- `screening_stage_set(review_id, stage, mode, reviewers)`:
  Configures stage mode ('blind' or 'unblinded') and expected reviewers ('title_abstract' or 'full_text'). Reviewer names are caller-supplied; blinding is by convention, with an audit trail, not a guarantee. At an open blind stage, peer decisions and agreement are hidden until closed with `screening_stage_close`. Setting 'blind' requires at least 2 distinct reviewers, none 'primary', and must happen before anyone records a decision at that stage.
- `screening_stage_close(review_id, stage)`:
  Closes an open blind screening stage and unblinds peer decisions and agreement. Requires that every expected reviewer has recorded a decision for every paper any expected reviewer recorded at this stage. Fails and lists missing pairs if incomplete. On success, consensus decisions may be recorded under 'primary'.
- `screening_record(review_id, paper_ref, stage, decision, reason=None, reviewer="primary", title=None)`:
  Records a screening decision. `stage`: `title_abstract` or `full_text`. `decision`: `include`, `exclude`, or `not_retrieved` (full_text only). `reason` is required for `exclude`.
- `screening_record_many(review_id, decisions)`:
  Batch records up to 500 screening decisions.
- `screening_list(review_id, stage=None, reviewer=None, decision=None, paper_ref=None, as_reviewer=None, limit=200, offset=0)`:
  Queries recorded screening decisions. At an open blind stage, rows are returned only when `reviewer == as_reviewer`; peer rows are hidden and excluded from total.
- `screening_agreement(review_id, stage, reviewer_a, reviewer_b)`:
  Computes Cohen's kappa between two reviewers. At an open blind stage, agreement is hidden until `screening_stage_close`. Always pass warnings (such as human screening caution and same-model warnings) to the user. Does not resolve disagreements.

### 4. Paper and Study Identity
- `paper_resolve(review_id, paper_refs)`:
  Resolves 1 to 200 paper keys through the canonical alias map recorded by deduplication or manual aliases. Status: `resolved`, `conflict`, or `unknown`. Uses exact stored mappings only; nothing is guessed.
- `paper_alias_record(review_id, alias, canonical_ref, replace=False)`:
  Records one manual alias (e.g. preprint DOI to published paper). Mapping to another canonical ref is refused unless `replace=True` (old mapping saved in `alias_conflicts` for provenance). Does not validate against external registries.
- `study_link(review_id, study_id, paper_refs, note=None, replace=False)`:
  Links reports to a study identifier so reports and studies can be counted separately. Refused if already linked to another study unless `replace=True`. Does not merge study records or combine sample sizes.
- `study_unlink(review_id, paper_refs)`:
  Removes study link from reports without deleting screening decisions or evidence rows.
- `study_overview(review_id, reviewer=None)`:
  Counts included reports separately from studies and flags `duplicate_participant_risk` (outcomes reported multiple times across reports of the same study). Does not prove reports share participants or decide pooling appropriateness.

### 5. Evidence Extraction and Statistical Checks
- `evidence_record(review_id, paper_ref, outcome, study_design, quote, source_text, sample_size=None, population=None, intervention=None, comparator=None, timepoint=None, effect_measure=None, effect_estimate=None, ci_lower=None, ci_upper=None, ci_level=0.95, notes=None)`:
  Stores structured evidence rows. Verifies quote presence in `source_text` (`quote_verified`) and checks that numerical figures in estimates appear in the quote.
  Permitted `effect_measure` values: `rr`, `or`, `hr`, `irr`, `rom`, `rd`, `md`, `smd`, `other`.
- `evidence_table(review_id, format="markdown", paper_ref=None)`:
  Renders extracted evidence rows as markdown, CSV, or JSON.
- `stats_check_ratio_ci(measure, estimate, ci_lower, ci_upper, ci_level=95, p_value=None, decimals=2, rel_tol=0.01, review_id=None, paper_ref=None, label=None)`:
  Checks whether a ratio estimate (RR, OR, HR, IRR, ROM) is consistent with its confidence interval and optional p-value under rounding tolerance. Reports detected inconsistencies; does not prove a paper is wrong. When `review_id` is passed, check is saved to the review.
- `stats_recompute_2x2(events_a, total_a, events_b, total_b, continuity_correction="none", reported=None, ci_level=95, decimals=2, rel_tol=0.01, review_id=None, paper_ref=None, label=None)`:
  Recomputes RR, OR (Woolf), and RD from arm counts and optionally compares reported results under rounding tolerance. Comparison match is arithmetic agreement, not proof the paper is correct.
- `stats_check_evidence(review_id, decimals=2, rel_tol=0.01)`:
  Runs ratio-CI consistency check over all stored evidence rows with a ratio measure. Reports detected inconsistencies. Does not edit rows or judge analysis.

### 6. Claims and Risk of Bias
- `claim_record(review_id, claim_text, paper_ref, label, reason, source_text="", quote="")`:
  Registers key synthesis claims with status `supported`, `partial`, or `unsupported`. Verifies supporting quotations against `source_text`.
- `claim_report(review_id, report_text=None)`:
  Summarizes claim support status and flags weak or unverified claims appearing in `report_text`.
- `rob_record(review_id, paper_ref, tool, domain, judgement, quote="", source_text="", rationale=None)`:
  Records domain-level risk of bias. Tools: `rob2` (domains D1–D5, overall; judgements low, some_concerns, high), `robins_i` (domains D1–D7, overall; judgements low, moderate, serious, critical, no_information), `nos` or `nos_case_control` (domains selection, comparability, outcome/exposure; star counts).
- `rob_summary(review_id)`:
  Computes overall study bias judgements and traffic-light summaries using deterministic published rules.

### 7. References, Reporting, and Manuscript Checks
- `verify_dois(dois, review_id=None)`:
  Performs network lookups against Crossref and doi.org to verify DOI registration and retraction status. Saves results when `review_id` is supplied.
- `resolve_citations(text, papers)`:
  Parses author-year and numeric citations in `text` and matches them against candidate paper metadata dictionaries.
- `lint_prose(text)`:
  Scans prose for advisory issues such as vague attribution, unsupported certainty, or malformed citation brackets.
- `prisma_status(review_id, reviewer=None)`:
  Computes complete PRISMA 2020 flow counts and checks accounting invariants (identified = deduplicated + duplicates; screened = include + exclude; reports sought = assessed + not retrieved + pending).
- `prisma_flow_diagram(review_id, format="mermaid", reviewer=None)`:
  Generates flow diagram in `mermaid`, `svg`, or `markdown`.
- `prisma_checklist(review_id, report_text, reviewer=None)`:
  Performs keyword matching across 27 PRISMA 2020 items and checks reported numbers against recorded counts.
- `prisma_excluded_studies(review_id, reviewer=None, format="markdown")`:
  Generates structured table of full-text excluded studies, citations, and recorded exclusion reasons, reconciling against PRISMA counts.
- `manuscript_check(review_id, sections, reviewer=None)`:
  Compares manuscript sections (up to 20 sections) against recorded review audit data (PRISMA counts, effect estimates, CIs, search databases, search dates, reviewer counts, risk of bias tools, synthesis methods). Reports detected inconsistencies; does not judge scientific truth or evaluate methodological quality.

### 8. History, Status, and Bundle Export
- `review_history(review_id, table=None, paper_ref=None, row_key=None, limit=100, offset=0)`:
  Queries append-only event history captured by SQLite triggers across tracked tables, oldest first. Screening events of an open blind stage are hidden until `screening_stage_close`.
- `review_status(review_id, reviewer=None)`:
  Audits overall review progress across all stages, surfaces invariant warnings, and lists next action items.
- `export_bundle(review_id, format="markdown", reviewer=None, save=False)`:
  Exports the review record into a single markdown or JSON document. It includes a summary of the change history, not every event; keep the review database file (or page through `review_history`) for the full history. Always pass warnings (such as human screening caution and same-model warnings) to the user. When `save=True`, writes the document to `<CITEPROOF_MCP_HOME>/exports/<review_id>-audit-bundle.md|json` and returns `saved_path` and `sha256`.

## Limitations

- **No Scientific Inference**: CiteProof checks inputs against structural rules and string matches. It cannot determine if an author's interpretation is correct or if a paper is methodologically sound.
- **Quote Verification Boundary**: Quote matching handles Unicode normalization, punctuation variations, and whitespace collapses. However, quotes that are paraphrased or translated will not verify against source text.
- **Network Scope**: Only `verify_dois` connects to the internet. If you are offline, DOIs will remain in an `unverified` state.
- **Blinding Convention**: Blinding and reviewer types are by convention, with an audit trail, not enforced. The server cannot verify caller identity or authenticate models; AI screening is never called independent or verified. Two runs of the same model are not independent reviewers.
- **Statistical and Manuscript Discrepancies**: Statistical and manuscript checks report detected inconsistencies, never that a paper is wrong. Seed recall is not proof that a search was exhaustive. The server contains no language model.
