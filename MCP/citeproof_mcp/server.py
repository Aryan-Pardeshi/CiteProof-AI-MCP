# SPDX-License-Identifier: MIT
"""Server assembly. Every tool module exposes ``register(server)``."""

import inspect
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version

from citeproof_mcp import __version__
from citeproof_mcp.compat import MCPServer
from citeproof_mcp.tools import (
    core,
    events,
    evidence_checks,
    extraction,
    identity,
    prisma,
    protocol,
    reporting,
    review,
    stats_checks,
)

INSTRUCTIONS = (
    "Audit tools for a systematic review. You make every judgement (include or exclude, claim supported or "
    "not, risk of bias, extracted values); these tools store it, check the evidence it points to against the "
    "source text you supply (quote_verified), compute PRISMA counts and refuse malformed input. Nothing here "
    "searches the literature or reads papers for you.\n"
    "Usual order: review_create -> protocol_record (and recall_seed_record for known eligible papers) -> "
    "prisma_record_search (once per database search) -> dedup_papers (all exported records, with review_id) -> "
    "recall_check -> reviewer_record for each reviewer (human or model) and, for blind screening, "
    "screening_stage_set -> screening_record / screening_record_many at title_abstract, then full_text (two "
    "reviewers: record each, screening_stage_close when blind, run screening_agreement, record the consensus as "
    "reviewer 'primary') -> study_link for reports of one study, study_overview -> evidence_record per included "
    "study and outcome, stats_check_evidence / stats_recompute_2x2 -> rob_record per domain, rob_summary -> "
    "claim_record for the report's key claims, claim_report -> verify_dois and resolve_citations on the "
    "reference list -> prisma_flow_diagram, prisma_checklist, prisma_excluded_studies, manuscript_check and "
    "lint_prose on the draft -> export_bundle. Call review_status at any time for what is missing and the next "
    "steps, and review_history for what changed. Pass every screening warning (AI-only reviewers, same-model "
    "reviewers) to the user; blind screening is by convention, the server cannot verify who calls it.\n"
    "paper_ref identifies a paper everywhere: a DOI ('10.1234/abc'), 'pmid:<digits>' or "
    "'title:<title>|<year>'; use the same form for the same paper in every tool. Every result has ok; when ok "
    "is false, error says what to change."
)

TOOL_MODULES = (
    core,
    protocol,
    prisma,
    extraction,
    identity,
    evidence_checks,
    stats_checks,
    reporting,
    review,
    events,
)


def _server_version() -> str:
    """Version reported in ``serverInfo``; the package version once installed."""
    try:
        return package_version("citeproof-mcp")
    except PackageNotFoundError:
        return __version__


def _version_kwargs() -> dict[str, str]:
    """mcp 2.x takes ``version``; mcp 1.x (Claude Science) has no such parameter."""
    try:
        parameters = inspect.signature(MCPServer.__init__).parameters
    except (TypeError, ValueError):
        return {}
    return {"version": _server_version()} if "version" in parameters else {}


def build_server() -> MCPServer:
    server = MCPServer("citeproof", instructions=INSTRUCTIONS, **_version_kwargs())
    for module in TOOL_MODULES:
        module.register(server)
    return server
