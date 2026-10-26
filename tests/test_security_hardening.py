"""Security hardening: request limits, OpenRouter attribution, frontend CSP and link safety."""

from __future__ import annotations

import json
from html.parser import HTMLParser
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


class _ScriptScan(HTMLParser):
    """Collect script tags and inline event-handler attributes from a page."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.scripts: list[tuple[dict[str, str], str]] = []
        self.handlers: list[str] = []
        self._current: dict[str, str] | None = None
        self._body: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {name: value or "" for name, value in attrs}
        self.handlers.extend(name for name in values if name.startswith("on"))
        if tag == "script":
            self._current, self._body = values, []

    def handle_data(self, data: str) -> None:
        if self._current is not None:
            self._body.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._current is not None:
            self.scripts.append((self._current, "".join(self._body)))
            self._current = None


def test_pages_have_no_cdn_scripts_inline_scripts_or_handlers() -> None:
    for page in FRONTEND.rglob("*.html"):
        scan = _ScriptScan()
        scan.feed(page.read_text(encoding="utf-8"))
        for attrs, body in scan.scripts:
            src = attrs.get("src")
            if src:
                assert not src.startswith(("http://", "https://", "//")), page.name
            elif attrs.get("type") != "application/ld+json":
                assert not body.strip(), f"{page.name}: inline script"
        assert not scan.handlers, f"{page.name}: inline event handlers {scan.handlers}"


def test_app_links_and_model_values_are_made_safe() -> None:
    text = (FRONTEND / "app.js").read_text(encoding="utf-8")
    assert "function safeHttpUrl" in text
    assert "card.href = url;" not in text
    assert "${e.reported_value}" not in text
    assert ">${w.status}<" not in text
    assert "createTreeWalker" in text
