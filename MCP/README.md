# CiteProof MCP

**A local audit trail for AI-assisted systematic reviews.** CiteProof MCP gives research agents structured records for review protocols, PRISMA counts, screening decisions, extracted evidence, risk-of-bias assessments, citation checks, and a reproducible export.

[Model Context Protocol (MCP)](https://modelcontextprotocol.io) server · Python 3.12+ · stdio transport · MIT

> **The agent does the research; CiteProof keeps it auditable.** The server contains no language model and does not search for or read papers. It records the agent's decisions, validates inputs, checks supplied evidence, and computes review counts. No model API key is required.

## Why CiteProof MCP?

Systematic reviews involve many decisions that are easy to lose track of across searches, screening, extraction, and reporting. CiteProof MCP provides one local record of that work and checks that the pieces are internally consistent.

- **Traceable screening:** record title/abstract and full-text decisions, reasons for exclusion, and reviewer agreement using Cohen's kappa.
- **PRISMA accounting:** track database searches, deduplication, screening counts, pending decisions, and consistency invariants.
- **Evidence checks:** compare supplied quotations with source text and flag numbers that do not appear in a recorded quote.
- **Risk-of-bias records:** maintain domain-level judgements for RoB 2, ROBINS-I, and Newcastle–Ottawa scales, with rule-based summaries.
- **Citation checks:** look up DOI registration, resolve in-text citations against a supplied reference list, and lint prose for advisory issues.
- **One export:** assemble the protocol, decisions, evidence, checks, warnings, and next steps into a review bundle.

## How it works

The MCP server runs alongside a compatible research agent (for example, Claude Science or another stdio-capable MCP client).

```text
Research agent
  ├─ searches for and reads papers
  ├─ makes screening, extraction, and interpretation decisions
  └─ sends decisions and supplied text to CiteProof MCP
                         │
                         ▼
                 CiteProof MCP
  ├─ validates records and checks supplied quotations
  ├─ computes PRISMA counts and reviewer agreement
  ├─ tracks risk-of-bias and citation checks
  └─ keeps a local audit trail and exports it
```

CiteProof does **not** independently decide whether a study should be included, whether a claim is true, or whether a risk-of-bias judgement is scientifically correct. Those are the researcher's or agent's judgements; the server checks structure, rules, and the evidence supplied to it.

## Tool overview

The server exposes 24 tools. Use [`docs/SPEC.md`](docs/SPEC.md) for the full argument schemas, output fields, validation rules, and examples.

| Workflow stage | Tools |
|---|---|
| Review setup and status | `review_create`, `review_list`, `protocol_record`, `review_status` |
| Searches and deduplication | `prisma_record_search`, `dedup_papers`, `prisma_record_dedup` |
| Screening and agreement | `screening_record`, `screening_record_many`, `screening_list`, `screening_agreement` |
| PRISMA reporting | `prisma_status`, `prisma_flow_diagram`, `prisma_checklist` |
| Evidence and claims | `evidence_record`, `evidence_table`, `claim_record`, `claim_report` |
| Risk of bias | `rob_record`, `rob_summary` |
| References and prose | `verify_dois`, `resolve_citations`, `lint_prose` |
| Export | `export_bundle` |

## Requirements

- Python **3.12 or newer**
- An MCP client that supports the **stdio** transport
- Internet access only when using DOI registry checks; the other records and checks can run locally

The package supports the `mcp` 1.27 API used by the tested Claude Science setup as well as `mcp` 2.x.

## Install

### Any MCP client (npx)

```bash
npx -y citeproof-mcp
```

The [`citeproof-mcp`](https://www.npmjs.com/package/citeproof-mcp) npm package ships the Python server source. The launcher uses, in order: `CITEPROOF_MCP_PYTHON` (or `--python`), `uvx` if it is present, a Python 3.12+ already on `PATH` that has `mcp`, `pydantic` and `httpx`, and only then installs [uv](https://docs.astral.sh/uv/) and downloads the wheel from the matching [GitHub release](https://github.com/Aryan-Pardeshi/CiteProof-AI-MCP/releases/latest). Requirements: Node.js 18 or newer, plus either such a Python or network access on the first run. The first `npx` run also needs the npm registry to fetch the package unless it is already cached; Claude Science stages the package for you. Git is not required. Launcher options and environment variables are in [`npm/README.md`](npm/README.md).

```json
{
  "mcpServers": {
    "citeproof": {
      "command": "npx",
      "args": ["-y", "citeproof-mcp"],
      "env": { "CITEPROOF_MCP_HOME": "/path/to/your/review-data" }
    }
  }
}
```

- **Claude Science:** Customize → Connectors → Add connector → Local command, name `citeproof`, command `npx -y citeproof-mcp`. When `uvx` is not available, the launcher runs the bundled server with the sandbox's own `python3`, so it needs no uv and no download once Claude Science has staged the npm package, provided that interpreter has the required packages. To select that Python even when `uvx` exists, set `CITEPROOF_MCP_PYTHON=python3`. If the npx setup is unavailable in your Claude Science setup, see the bundle method in [`docs/CLAUDE_SCIENCE.md`](docs/CLAUDE_SCIENCE.md). `CITEPROOF_UV_HOME` only helps when uv is available or can be installed with network access.
- **Claude Desktop:** open [`citeproof.mcpb`](https://github.com/Aryan-Pardeshi/CiteProof-AI-MCP/releases/latest/download/citeproof.mcpb) from the latest release (Settings → Extensions).
- **Claude Code:** `claude mcp add citeproof -- npx -y citeproof-mcp`.

### From source (pip)

```bash
python -m pip install ./MCP
```

The installation provides the `citeproof-mcp` command; configure your client with `"command": "citeproof-mcp"`. You can also run `python -m citeproof_mcp`.

### Claude Science on Windows (bundle fallback)

Claude Science's local-command connector runs inside its own sandbox. The npx launcher works there when the sandbox's `python3` has `mcp`, `pydantic` and `httpx` (it does in the tested setup) because the npm package now ships the server source. If npx cannot stage or read that source, the bundle workflow in [`docs/CLAUDE_SCIENCE.md`](docs/CLAUDE_SCIENCE.md) copies it into the connector's workspace. The bundle still needs the sandbox's Python and the same packages.

### Local data directory

Review state is saved as SQLite files under:

- The directory specified by `CITEPROOF_MCP_HOME`, when set
- `~/.citeproof-mcp`, when the variable is omitted

Each review has its own database under `reviews/<review_id>.db`. Back up this directory if you need to preserve review records. Exported bundles are returned by the tool; they are not automatically written to a separate report file.

## A typical review workflow

1. **Create the review** with `review_create`. Save its returned `review_id`.
2. **Record the protocol** with `protocol_record`: population, intervention, outcomes, eligibility criteria, databases, and search strategy.
3. **Log every search** with `prisma_record_search`, including source, query, date, and hit count.
4. **Deduplicate the complete result set** with `dedup_papers`. For PRISMA accounting, the tool must receive the complete exported set, not separately deduplicated batches.
5. **Screen studies** at title/abstract and full-text stages. Use `screening_record` or `screening_record_many`; capture full-text exclusion reasons.
6. **Measure reviewer agreement** by recording both reviewers' decisions and calling `screening_agreement`. Record the agreed result under the `primary` reviewer when applicable.
7. **Extract evidence and assess bias** using `evidence_record` and `rob_record`; inspect `evidence_table` and `rob_summary`.
8. **Check claims and references** with `claim_record`, `claim_report`, `verify_dois`, and `resolve_citations`.
9. **Prepare and audit the report** with `prisma_flow_diagram`, `prisma_checklist`, and `lint_prose`.
10. **Export the audit trail** with `export_bundle`. Call `review_status` at any time to see missing steps, warnings, and suggested next actions.

For exact argument names, permitted values, and edge cases, follow the [tool specification](docs/SPEC.md).

## What is checked — and what is not

| Check | What CiteProof does |
|---|---|
| Quotes | Checks a supplied quote against supplied source text after normalisation. A failed match is flagged rather than hidden. |
| Evidence numbers | Flags numerical values entered in an evidence row that do not appear in its quote; validates effect estimates and confidence intervals for supported measures. |
| PRISMA | Computes counts from recorded searches, deduplication, and screening decisions, then checks consistency invariants. |
| Reviewer agreement | Calculates Cohen's kappa for decisions shared by two reviewers. |
| DOI status | Queries Crossref and doi.org. If a registry cannot be reached, the status is **unverified**, not **not found**. |
| Citation resolution | Matches citations against a supplied reference list. A resolved citation is a match, not proof that the cited source supports the surrounding claim. |
| Risk of bias | Computes the overall summary from recorded domains and documented rules; the scientific judgements still come from the reviewer or agent. |
| Prose and checklist | Provides advisory or keyword-based checks. These do not establish the quality, completeness, or scientific validity of a report. |

## Privacy and network behaviour

- Review records are kept locally in SQLite under the configured data directory.
- CiteProof has no embedded language model and requires no model-provider API key.
- DOI verification is the only feature that makes outbound network requests, to Crossref and doi.org.
- CiteProof does not search the web, fetch papers, or read PDFs. The calling agent supplies the source text and research decisions.
- Source text used in evidence checks is not kept in full by the audit store; the implementation records its length and SHA-256 alongside the check results.

See [`docs/SPEC.md`](docs/SPEC.md#security) for implementation-level security details.

## Run tests

From the MCP package directory:

```bash
cd MCP
python -m pip install -e ".[test]"
python -m pytest -q -p no:cacheprovider
```

The suite uses an in-memory MCP client and an isolated data directory. These tests are intended to run offline.

## Documentation

- [Full tool specification](docs/SPEC.md)
- [Claude Science install and troubleshooting](docs/CLAUDE_SCIENCE.md)
- [Third-party notices](NOTICE.md)

## License

CiteProof MCP is released under the [MIT License](../LICENSE). Adapted third-party components are documented in [`NOTICE.md`](NOTICE.md) and [`LICENSES/Apache-2.0.txt`](LICENSES/Apache-2.0.txt).
