"""Static checks for the documentation portal and the CiteProof MCP reference.

The tests read the shipped files with the standard library only. They do not
start a server or make network calls. The MCP tool list is compared with the
server's own tool modules.
"""

from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
BASE = "https://citeproof.top"
MCP_DIR = ROOT / "MCP"

DOCS_PAGES = {
    "docs.html": "/docs",
    "docs/mcp.html": "/docs/mcp",
    "docs/research-workflow.html": "/docs/research-workflow",
    "docs/self-hosting.html": "/docs/self-hosting",
    "docs/troubleshooting.html": "/docs/troubleshooting",
}

SHELL_PAGES = list(DOCS_PAGES)
HUB = "mcp.html"
NEW_PAGES = [*DOCS_PAGES, HUB]

TOOL_GROUPS = {
    "tools-core": [
        "review_create",
        "review_list",
        "verify_dois",
        "resolve_citations",
        "lint_prose",
    ],
    "tools-protocol": ["protocol_record"],
    "tools-prisma": [
        "prisma_record_search",
        "dedup_papers",
        "prisma_record_dedup",
        "screening_record",
        "screening_record_many",
        "screening_list",
        "screening_agreement",
        "prisma_status",
        "prisma_flow_diagram",
        "prisma_checklist",
    ],
    "tools-evidence": ["evidence_record", "evidence_table"],
    "tools-claims": ["claim_record", "claim_report", "rob_record", "rob_summary"],
    "tools-review": ["review_status", "export_bundle"],
}
ALL_TOOLS = [tool for tools in TOOL_GROUPS.values() for tool in tools]


class PageParser(HTMLParser):
    """Collect headings, links, metadata and visible breadcrumb text."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.headings: list[tuple[str, str, str]] = []
        self.links: list[dict[str, str]] = []
        self.metas: list[dict[str, str]] = []
        self.scripts: list[dict[str, str]] = []
        self.json_ld: list[str] = []
        self.breadcrumb_parts: list[str] = []
        self._heading: tuple[str, str, str] | None = None
        self._in_breadcrumb = False
        self._ld_chunks: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key: value or "" for key, value in attrs}
        classes = values.get("class", "").split()
        if tag in {"h1", "h2", "h3"}:
            self._heading = (tag, values.get("id", ""), "")
        elif tag == "a" or tag == "link":
            self.links.append(values)
        elif tag == "meta":
            self.metas.append(values)
        elif tag == "script":
            if values.get("type") == "application/ld+json":
                self._ld_chunks = []
            elif values.get("src"):
                self.scripts.append(values)
        if tag == "nav" and "docs-breadcrumbs" in classes:
            self._in_breadcrumb = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._ld_chunks is not None:
            self.json_ld.append("".join(self._ld_chunks))
            self._ld_chunks = None
        elif self._heading is not None and tag == self._heading[0]:
            self.headings.append(self._heading)
            self._heading = None
        if tag == "nav":
            self._in_breadcrumb = False

    def handle_data(self, data: str) -> None:
        if self._ld_chunks is not None:
            self._ld_chunks.append(data)
        elif self._heading is not None:
            self._heading = (
                self._heading[0],
                self._heading[1],
                self._heading[2] + data,
            )
        elif self._in_breadcrumb:
            text = " ".join(data.split())
            if text:
                self.breadcrumb_parts.append(text)

    def heading(self, tag: str) -> list[str]:
        return [
            " ".join(text.split()) for kind, _, text in self.headings if kind == tag
        ]

    def meta(self, key: str) -> str:
        for values in self.metas:
            if values.get("name") == key or values.get("property") == key:
                return values.get("content", "")
        return ""

    def link(self, rel: str) -> str:
        for values in self.links:
            if rel in values.get("rel", "").split():
                return values.get("href", "")
        return ""


def parse(name: str) -> PageParser:
    parser = PageParser()
    parser.feed((FRONTEND / name).read_text(encoding="utf-8"))
    return parser


def text(name: str) -> str:
    return (FRONTEND / name).read_text(encoding="utf-8")


@pytest.mark.parametrize("name", NEW_PAGES)
def test_one_h1_canonical_and_description(name: str) -> None:
    parser = parse(name)
    assert len(parser.heading("h1")) == 1, f"{name} needs exactly one h1"
    assert parser.heading("h1")[0]
    expected = BASE + (DOCS_PAGES.get(name) or "/mcp")
    assert parser.link("canonical") == expected
    assert parser.meta("description")


@pytest.mark.parametrize("name", SHELL_PAGES)
def test_docs_shell_landmarks_and_controls(name: str) -> None:
    parser = parse(name)
    markup = text(name)
    assert 'class="docs-sidebar"' in markup
    assert 'id="docs-search-input"' in markup
    assert 'id="docs-toc-list"' in markup
    assert 'aria-label="Breadcrumb"' in markup
    assert 'aria-label="On this page"' in markup
    assert any("docs.css" in link.get("href", "") for link in parser.links)
    assert '<script src="/docs.js' in markup
    assert "docs-menu-toggle" in markup
    assert 'id="docs-sidebar"' in markup


@pytest.mark.parametrize("name", SHELL_PAGES)
def test_sidebar_has_one_active_documentation_page(name: str) -> None:
    markup = text(name)
    sidebar = re.search(r'<aside class="docs-sidebar".*?</aside>', markup, re.DOTALL)
    assert sidebar, name
    active = re.findall(
        r'<a href="([^"]+)" aria-current="page">([^<]+)</a>',
        sidebar.group(0),
    )
    assert len(active) == 1, f"{name}: {active}"
    assert active[0][1].strip()


@pytest.mark.parametrize("name", SHELL_PAGES)
def test_shell_sidebar_links_resolve(name: str) -> None:
    markup = text(name)
    sidebar = re.search(r'<aside class="docs-sidebar".*?</aside>', markup, re.DOTALL)
    assert sidebar, name
    hrefs = re.findall(r'href="([^"]+)"', sidebar.group(0))
    assert hrefs
    for href in hrefs:
        if href.startswith(("http://", "https://", "#")):
            continue
        target = href.split("#")[0]
        if not target:
            continue
        path = FRONTEND / target.lstrip("/")
        assert (
            path.is_file()
            or path.with_suffix(".html").is_file()
            or (path / "index.html").is_file()
        ), f"{name} sidebar links to missing {href}"


@pytest.mark.parametrize("name", NEW_PAGES)
def test_internal_links_and_anchors_resolve(name: str) -> None:
    markup = text(name)
    ids = set(re.findall(r'\sid="([^"]+)"', markup))
    for href in re.findall(r'href="([^"]+)"', markup):
        if href.startswith(("http://", "https://", "mailto:")):
            continue
        if href.startswith("#"):
            assert href[1:] in ids, f"{name} has a dead anchor {href}"
            continue
        target, _, fragment = href.partition("#")
        path = FRONTEND / target.split("?")[0].lstrip("/")
        assert (
            path.is_file()
            or path.with_suffix(".html").is_file()
            or (path / "index.html").is_file()
        ), f"{name} links to missing {href}"
        if fragment:
            if path.is_dir():
                destination = path / "index.html"
            else:
                destination = path if path.is_file() else path.with_suffix(".html")
            destination_ids = set(
                re.findall(r'\sid="([^"]+)"', destination.read_text(encoding="utf-8"))
            )
            assert fragment in destination_ids, f"{name} links to missing {href}"


@pytest.mark.parametrize("name", NEW_PAGES)
def test_no_placeholder_links(name: str) -> None:
    assert 'href="#"' not in text(name)


@pytest.mark.parametrize("name", NEW_PAGES)
def test_visible_breadcrumbs_match_json_ld(name: str) -> None:
    parser = parse(name)
    assert parser.breadcrumb_parts, f"{name} has no visible breadcrumbs"
    crumbs: list[str] = []
    for block in parser.json_ld:
        data = json.loads(block)
        nodes = data.get("@graph", [data])
        for node in nodes:
            if node.get("@type") == "BreadcrumbList":
                crumbs = [item["name"] for item in node["itemListElement"]]
    assert crumbs, f"{name} has no BreadcrumbList JSON-LD"
    assert parser.breadcrumb_parts == crumbs, (name, parser.breadcrumb_parts, crumbs)


@pytest.mark.parametrize("name", NEW_PAGES)
def test_dark_only_and_no_remote_frameworks(name: str) -> None:
    markup = text(name)
    parser = parse(name)
    assert parser.meta("color-scheme") == "dark"
    assert "theme-toggle" not in markup
    assert "light-mode-toggle" not in markup
    for script in parser.scripts:
        source = script.get("src", "")
        assert not source.startswith(("http://", "https://")), (
            f"{name} loads remote script {source}"
        )


@pytest.mark.parametrize("name", NEW_PAGES)
def test_no_personal_email(name: str) -> None:
    markup = text(name)
    assert "mailto:" not in markup
    assert not re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", markup), (
        f"{name} contains a personal email address"
    )


def test_mcp_reference_groups_and_tool_order() -> None:
    markup = text("docs/mcp.html")
    positions = []
    for group, tools in TOOL_GROUPS.items():
        start = markup.find(f'<h2 id="{group}">')
        assert start != -1, group
        positions.append(start)
        section = markup[start:]
        for following in TOOL_GROUPS:
            if following != group:
                cut = section.find(f'<h2 id="{following}">')
                if cut != -1:
                    section = section[:cut]
        for tool in tools:
            assert f'id="tool-{tool.replace("_", "-")}"' in section, (group, tool)
    assert positions == sorted(positions), "tool groups are out of order"


def test_all_tools_have_arguments_and_examples() -> None:
    markup = text("docs/mcp.html")
    blocks = re.findall(
        r'<div class="docs-tool">(.*?)(?=<div class="docs-tool">|<h2 id=)',
        markup,
        re.DOTALL,
    )
    assert len(blocks) == len(ALL_TOOLS) == 24
    for block in blocks:
        name = re.search(r'<h3 id="tool-([a-z0-9-]+)">', block)
        assert name, block[:80]
        assert "<table" in block, name.group(1)
        for header in ("Argument", "Type", "Required", "Notes"):
            assert header in block, (name.group(1), header)
        assert "Example request." in block, name.group(1)
        assert "Example result." in block, name.group(1)
        assert "<code>" in block


def test_tool_names_match_the_server_modules() -> None:
    tools_dir = MCP_DIR / "citeproof_mcp" / "tools"
    assert tools_dir.is_dir()
    registered: set[str] = set()
    for module in tools_dir.glob("*.py"):
        body = module.read_text(encoding="utf-8")
        defined = set(
            re.findall(r"^(?:async )?def ([a-z][a-z0-9_]+)\(", body, re.MULTILINE)
        )
        match = re.search(r"^def register\(.*\Z", body, re.MULTILINE | re.DOTALL)
        if not match:
            continue
        for name in defined:
            if name != "register" and re.search(rf"\b{name}\b", match.group(0)):
                registered.add(name)
    assert registered, "could not parse tool names from the server modules"
    assert set(ALL_TOOLS) == registered
    markup = text("docs/mcp.html")
    for tool in registered:
        assert tool in markup


def test_mcp_hub_headline_setup_paths_and_compatibility() -> None:
    parser = parse(HUB)
    markup = text(HUB)
    assert parser.heading("h1") == [
        "Give Claude Science a verifiable research workflow"
    ]
    for phrase in (
        "Claude Desktop extension",
        "npx",
        "Claude Science local command",
        "npx -y citeproof-mcp",
        "CITEPROOF_NO_AUTO_INSTALL=1",
        "CITEPROOF_MCP_HOME",
        "Node 18",
    ):
        assert phrase in markup, phrase
    for status in ("Tested", "New", "Not yet tested"):
        assert status in markup, status
    for client in (
        "Claude Science",
        "Claude Desktop",
        "Claude Code",
        "Cursor, VS Code, Windsurf",
    ):
        assert client in markup, client
    assert (
        "no language model" in markup.lower()
        or "contains no language model" in markup.lower()
    )


def test_mcp_hub_describes_the_role_split() -> None:
    markup = text(HUB)
    assert "research agent" in markup.lower()
    assert "audit layer" in markup.lower()
    for link in ("/docs/mcp", "/docs", "/docs/troubleshooting"):
        assert f'href="{link}"' in markup


def test_mcp_hub_hero_has_install_block_and_copy_controls() -> None:
    parser = parse(HUB)
    markup = text(HUB)
    assert "Get the CiteProof MCP" in parser.heading("h2")
    assert (
        "One command. Runs on your computer. Works with Claude Science, "
        "Claude Desktop and Claude Code."
    ) in markup
    assert 'id="mcp-install-command">npx -y citeproof-mcp</code>' in markup
    assert 'data-copy-target="mcp-install-command"' in markup
    assert markup.count('aria-live="polite"') >= 2
    assert "Claude Desktop extension" in markup
    assert ".mcpb" in markup
    assert "Claude Science local command" in markup
    assert (
        '<span class="mcp-lockup-label" aria-label="Claude Science">'
        "Claude Science</span>"
    ) in markup
    assert 'role="img" aria-label="Claude Science"' not in markup
    assert 'role="img" aria-label="Model Context Protocol"' in markup
    assert 'alt="CiteProof"' in markup


def test_mcp_hub_install_tabs_switch_one_accessible_command_bar() -> None:
    markup = text(HUB)
    tab_pattern = (
        r"<button\b"
        r'(?=[^>]*role="tab")'
        r'(?=[^>]*data-mcp-command="([^"]+)")'
        r"[^>]*>(.*?)</button>"
    )
    tabs = re.findall(tab_pattern, markup, re.DOTALL)
    assert len(tabs) == 3
    assert [label.strip() for _, label in tabs] == [
        "npx",
        "Claude Desktop (.mcpb)",
        "Claude Science",
    ]
    assert tabs[0][0] == "npx -y citeproof-mcp"
    assert tabs[1][0] == (
        "https://github.com/Aryan-Pardeshi/CiteProof-AI-MCP/releases/latest"
    )
    assert tabs[2][0] == "npx -y citeproof-mcp"
    assert 'role="tablist"' in markup
    assert 'id="mcp-install-panel"' in markup
    assert 'class="mcp-command-prompt" aria-hidden="true">$</span>' in markup
    assert 'data-copy-target="mcp-install-command"' in markup


def test_mcp_hub_feature_grid_icons_and_tool_chips() -> None:
    markup = text(HUB)
    cards = re.findall(
        r'<div class="mcp-cap-card(?: [^"]*)?">(.*?)</div>',
        markup,
        re.DOTALL,
    )
    assert len(cards) == 15
    for card in cards:
        assert 'class="mcp-cap-icon' in card
        assert 'href="/docs/mcp#tool-' in card
        assert re.search(r"<h3>[^<]+</h3>", card)
        assert re.search(r"<p>[^<].{80,}</p>", card, re.DOTALL)


def test_mcp_hub_privacy_facts_and_final_cta() -> None:
    markup = text(HUB)
    facts = re.search(r'<ul class="mcp-privacy-facts">(.*?)</ul>', markup, re.DOTALL)
    assert facts
    assert len(re.findall(r"<li>", facts.group(1))) == 3
    assert "SQLite" in facts.group(1)
    assert "never uploaded" in facts.group(1).lower()
    assert re.search(r"doi\.org", facts.group(1))
    final_cta = re.search(
        r'<section class="mcp-final-cta(?: [^"]*)?".*?</section>',
        markup,
        re.DOTALL,
    )
    assert final_cta
    assert "npx -y citeproof-mcp" in final_cta.group(0)
    assert 'href="/docs/mcp"' in final_cta.group(0)
    assert 'href="/docs"' in final_cta.group(0)


def test_mcp_hub_setup_copy_covers_fixed_install_paths() -> None:
    markup = text(HUB)
    for phrase in (
        "double-click",
        "Settings",
        "Extensions",
        "Install",
        "fetches Python 3.12",
        "CITEPROOF_NO_AUTO_INSTALL=1",
        "CITEPROOF_MCP_HOME",
    ):
        assert phrase in markup, phrase
    assert "hosted MCP endpoint" not in markup
    assert "remote URL" not in markup


def test_mcp_reference_privacy_and_claude_ai_compatibility_are_local_only() -> None:
    markup = text("docs/mcp.html")
    lowered = markup.lower()
    assert "each review is one sqlite file" in lowered
    assert "no review data is uploaded" in lowered
    assert "only network calls are doi checks" in lowered
    assert (
        "claude.ai web custom connectors cannot start a local stdio process" in lowered
    )
    assert "hosted connector" not in lowered
    assert "remote mcp url" not in lowered
    assert "remote-only" not in lowered


def test_mcp_hub_links_every_tool_to_a_real_reference_anchor() -> None:
    hub = text(HUB)
    reference = text("docs/mcp.html")
    linked = {
        value.replace("-", "_")
        for value in re.findall(r'href="/docs/mcp#tool-([a-z0-9-]+)"', hub)
    }
    anchors = set(re.findall(r'id="tool-([a-z0-9-]+)"', reference))
    assert linked == set(ALL_TOOLS)
    assert {value.replace("_", "-") for value in linked} <= anchors


def test_mcp_hub_has_ten_step_flow_and_local_privacy_callout() -> None:
    markup = text(HUB)
    flow = re.search(r'<ol class="mcp-flow-strip">(.*?)</ol>', markup, re.DOTALL)
    assert flow, "missing flow strip"
    assert len(re.findall(r"<li>", flow.group(1))) == 10
    assert "Local by default" in markup
    assert "SQLite" in markup
    assert "never uploaded" in markup
    assert re.search(r"api\.crossref\.org", markup)
    assert re.search(r"doi\.org", markup)


def test_mcp_hub_hero_styles_and_copy_handler_exist() -> None:
    css = text("mcp.css")
    for selector in (
        ".mcp-install",
        ".mcp-command",
        ".mcp-copy-btn",
        ".mcp-cap-grid",
        ".mcp-flow-strip",
        ".mcp-privacy",
        ".mcp-flow-strip::before",
        "prefers-reduced-motion",
    ):
        assert selector in css, selector
    assert 'href="/mcp.css?v=2.0.0"' in text(HUB)
    script = text("docs.js")
    assert "[data-copy-target]" in script
    assert "Copied to clipboard" in script
    assert "data-mcp-command" in script


def test_docs_search_index_matches_headings() -> None:
    script = (FRONTEND / "docs.js").read_text(encoding="utf-8")
    pages = re.findall(
        r"url: '([^']+)',\s*title: '([^']+)',\s*headings: \[(.*?)\]\s*\n    \}",
        script,
        re.DOTALL,
    )
    assert pages
    for url, title, body in pages:
        assert title
        if url == "/":
            page = FRONTEND / "index.html"
        elif url == "/docs":
            page = FRONTEND / "docs.html"
        elif url == "/mcp":
            page = FRONTEND / "mcp.html"
        else:
            page = FRONTEND / (url.lstrip("/") + ".html")
        assert page.is_file(), url
        markup = page.read_text(encoding="utf-8")
        heading_ids = set(re.findall(r'<h[23] id="([^"]+)"', markup))
        indexed = re.findall(r"\['([^']+)', '([^']+)'\]", body)
        assert indexed, url
        for heading_id, label in indexed:
            assert label
            assert heading_id in heading_ids, (url, heading_id)
        assert heading_ids <= {heading_id for heading_id, _ in indexed}, url


def test_sitemap_lists_all_new_pages() -> None:
    sitemap = (FRONTEND / "sitemap.xml").read_text(encoding="utf-8")
    for path in [*DOCS_PAGES.values(), "/mcp"]:
        assert f"<loc>{BASE}{path}</loc>" in sitemap, path


def test_docs_css_contains_drawer_and_outline_styles() -> None:
    css = (FRONTEND / "docs.css").read_text(encoding="utf-8")
    for selector in (
        ".docs-sidebar",
        ".docs-overlay",
        ".docs-toc",
        ".docs-search",
        ".docs-copy",
        ".docs-page a:focus-visible",
    ):
        assert selector in css, selector
    assert "@media (max-width: 960px)" in css
    assert "prefers-reduced-motion" in css
