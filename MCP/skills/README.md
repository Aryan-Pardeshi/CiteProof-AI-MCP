# CiteProof MCP Companion Skills

This directory contains companion agent skills for CiteProof MCP. These skills instruct research agents (such as Claude Code, Claude Desktop, and Claude Science) on how to orchestrate systematic literature reviews and call the 24 audit tools in CiteProof MCP.

Available skills:
- **`citeproof-review`**: Guides agents through protocol recording, PRISMA accounting, screening workflows, evidence extraction, quote verification, risk-of-bias assessment, claim checks, reference checks, and audit bundle export.

---

## Installation

### 1. Claude Code

You can install the skill globally or on a per-project basis:

- **Global installation**:
  Copy the `citeproof-review` folder into your Claude Code skills directory:
  - Linux/macOS: `~/.claude/skills/citeproof-review/`
  - Windows: `%USERPROFILE%\.claude\skills\citeproof-review\`

- **Project-level installation**:
  Copy the `citeproof-review` folder into your project's `.claude` directory:
  - `<project-root>/.claude/skills/citeproof-review/`

Verify installation by running Claude Code and checking available skills.

### 2. Claude.ai / Claude Desktop

To install the skill in Claude Desktop or the Claude.ai web client:

1. Create a zip archive containing the `citeproof-review/` folder (including `SKILL.md`).
2. Navigate to **Settings** -> **Capabilities** -> **Skills** *(not yet verified)*.
3. Upload the zip archive.

*(Note: The exact settings path and zip upload capability in Claude Desktop and Claude.ai depend on the client build and account tier; marked as **not yet verified**.)*

### 3. Claude Science

To add the skill in Claude Science:

1. In Claude Science, navigate to the skills management section.
2. Choose **Import skill** and supply the canonical GitHub folder URL *(not yet verified)*:
   ```text
   https://github.com/Aryan-Pardeshi/CiteProof-AI-MCP/tree/main/MCP/skills/citeproof-review
   ```

*(Note: The web UI flow for importing skills directly from GitHub URLs in Claude Science is marked as **not yet verified**.)*

---

## Important: Connector Setup vs. Skill

The skill in this directory provides agent guidance on review workflows and tool calling patterns. It requires a functioning MCP connector to execute those tools.

- **Source of truth for connector setup:**
  Refer to [`MCP/docs/CLAUDE_SCIENCE.md`](../docs/CLAUDE_SCIENCE.md) for full instructions on setting up the local-command connector, building the isolated sandbox bundle, and configuring the `CITEPROOF_MCP_HOME` data directory in Claude Science.
- **Other MCP clients:**
  Refer to [`MCP/README.md`](../README.md#install) for standard stdio connector configuration using `citeproof-mcp` or `python -m citeproof_mcp`.

---

## Updating Skills

When CiteProof MCP is updated:

1. **Claude Code**:
   Pull or copy the updated `citeproof-review/SKILL.md` into your local `~/.claude/skills/citeproof-review/` or `<project-root>/.claude/skills/citeproof-review/`.
2. **Claude Desktop / Claude.ai**:
   Package the updated folder into a zip archive and re-upload under Settings -> Capabilities -> Skills *(not yet verified)*.
3. **Claude Science**:
   Re-import the updated GitHub folder URL or refresh the connector bundle as described in [`MCP/docs/CLAUDE_SCIENCE.md`](../docs/CLAUDE_SCIENCE.md).

---

## Privacy and Local Storage

- All review state collected by the tools referenced in this skill is stored locally in SQLite under `CITEPROOF_MCP_HOME` (default: `~/.citeproof-mcp`).
- No review content is uploaded to remote servers.
- The only network requests made by the MCP server are outbound DOI queries in `verify_dois` to Crossref and doi.org.
