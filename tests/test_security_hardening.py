"""Security hardening: request limits, OpenRouter attribution, frontend CSP and link safety."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.app import llm as llm_module
from backend.app.api.agent import ResearchApproveRequest, ResearchStartRequest
from backend.app.api.research_mode import ResearchModeStartRequest

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"


def test_research_requests_reject_oversized_fields() -> None:
    with pytest.raises(ValidationError):
        ResearchStartRequest(query="x" * 4001)
    with pytest.raises(ValidationError):
        ResearchApproveRequest(thread_id="t", message="x" * 4001)
    with pytest.raises(ValidationError):
        ResearchModeStartRequest(problem_statement="p", research_questions=["q"] * 11)
    with pytest.raises(ValidationError):
        ResearchModeStartRequest(problem_statement="p", research_objectives=["o" * 1001])

    assert ResearchModeStartRequest(problem_statement="p").research_objectives == []


def test_openrouter_calls_carry_app_attribution(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_BASE_URL", "https://openrouter.ai/api/v1")
    client = llm_module.get_llm(model="deepseek/deepseek-chat")
    assert client.default_headers["X-Title"] == "CiteProof"

    monkeypatch.setenv("LLM_BASE_URL", "https://api.deepseek.com")
    assert not llm_module.get_llm(model="deepseek-chat").default_headers


def test_csp_allows_scripts_from_this_site_only() -> None:
    config = json.loads((FRONTEND / "vercel.json").read_text(encoding="utf-8"))
    headers = {h["key"]: h["value"] for entry in config["headers"] for h in entry["headers"]}
    script_src = next(
        part for part in headers["Content-Security-Policy"].split(";")
        if part.strip().startswith("script-src")
    )
    assert script_src.strip() == "script-src 'self'"


def test_pages_have_no_cdn_scripts_inline_scripts_or_handlers() -> None:
    for page in FRONTEND.rglob("*.html"):
        text = page.read_text(encoding="utf-8")
        for attrs, body in re.findall(r"<script([^>]*)>(.*?)</script>", text, re.DOTALL):
            src = re.search(r'src="([^"]+)"', attrs)
            if src:
                assert not src.group(1).startswith(("http://", "https://", "//")), page.name
            elif "application/ld+json" not in attrs:
                assert not body.strip(), f"{page.name}: inline script"
        assert not re.search(r"\son[a-z]+=\"", text), f"{page.name}: inline event handler"


def test_app_links_and_model_values_are_made_safe() -> None:
    text = (FRONTEND / "app.js").read_text(encoding="utf-8")
    assert "function safeHttpUrl" in text
    assert "card.href = url;" not in text
    assert "${e.reported_value}" not in text
    assert ">${w.status}<" not in text
    assert "createTreeWalker" in text
