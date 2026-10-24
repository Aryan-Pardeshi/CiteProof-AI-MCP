# citeproof-mcp (npm launcher)

**Audit layer for AI literature reviews.** This package is a thin Node launcher for the [CiteProof MCP](https://citeproof.top/mcp) server. It finds `uvx` (installing [uv](https://docs.astral.sh/uv/) automatically if it is missing) and starts the Python MCP server over stdio, so any MCP client can add CiteProof the same way as any other MCP server.

```json
{
  "mcpServers": {
    "citeproof": {
      "command": "npx",
      "args": ["-y", "citeproof-mcp"]
    }
  }
}
```

CiteProof MCP records and checks what your research agent supplies: review protocols, PRISMA counts, screening decisions, extracted evidence, risk-of-bias records, DOI and quote checks, and a reproducible export bundle. **It does not search the literature or read papers**, and it contains no language model. Your agent does the research; CiteProof keeps it auditable. No API key is required.

## Requirements

- Node.js 18 or newer.
- Network access on the first run: the launcher downloads the Python server (and uv, and Python 3.12, if needed).
- macOS, Linux or Windows.

## Install

### Claude Desktop

Add this to `claude_desktop_config.json` (Settings → Developer → Edit Config):

```json
{
  "mcpServers": {
    "citeproof": {
      "command": "npx",
      "args": ["-y", "citeproof-mcp"]
    }
  }
}
```

Restart Claude Desktop after saving.

### Claude Code

```bash
claude mcp add citeproof -- npx -y citeproof-mcp
```

### Cursor, VS Code, Windsurf

Use the same generic `mcpServers` block shown at the top of this page in the client's MCP configuration file.

- **Cursor:** `~/.cursor/mcp.json` (or `.cursor/mcp.json` in a project).
- **VS Code:** the `mcp.json` used by your MCP extension; the built-in support uses `"servers"` instead of `"mcpServers"` — check your client's documentation.
- **Windsurf:** `~/.codeium/windsurf/mcp_config.json`.

> These three clients are **not yet tested** with this launcher. The command itself is plain stdio and should work anywhere `npx` works, but please open an issue if a client needs a different shape.

### Hosted connector (no install)

If your client supports remote MCP servers, add a custom connector with the URL:

```text
https://api.citeproof.top/mcp
```

Claude: Settings → Connectors → Add custom connector → paste the URL. No login is required.

## What the launcher does

1. Looks for `uvx` in this order: `CITEPROOF_UVX`, your `PATH`, then the usual uv install locations (`~/.local/bin/uvx`, `~/.cargo/bin/uvx`, and the Windows equivalents under `%USERPROFILE%` and `%LOCALAPPDATA%`).
2. If `uvx` is missing, it runs Astral's official installer non-interactively (`https://astral.sh/uv/install.sh`, or `install.ps1` on Windows) and prints progress to **stderr**. It does not edit your shell profiles.
3. Starts `uvx --from <source> --python 3.12 citeproof-mcp` with inherited stdio. The default source is this repository at the tag matching the launcher version, so uv also downloads Python 3.12 if your machine does not have it.
4. Forwards SIGINT/SIGTERM to the server and exits with the server's exit code. Extra arguments are passed through, for example `npx -y citeproof-mcp --http --port 8080`.

stdout is reserved for the MCP protocol. The launcher never writes to it during a session; all diagnostics go to stderr. There is no telemetry.

## Environment variables

| Variable | Purpose |
| --- | --- |
| `CITEPROOF_MCP_HOME` | Data directory for the server's local records (default `~/.citeproof-mcp`). |
| `CITEPROOF_MCP_SOURCE` | Override the `uvx --from` value, for example `citeproof-mcp==0.2.0` once the server is on PyPI, or an absolute path to a local checkout of `MCP/`. |
| `CITEPROOF_NO_AUTO_INSTALL` | Set to `1` to never install uv automatically. The launcher then exits with a manual install link. |
| `CITEPROOF_UVX` | Full path to the `uvx` executable, if it is not on `PATH`. |

Example with a pinned source and a custom data directory:

```json
{
  "mcpServers": {
    "citeproof": {
      "command": "npx",
      "args": ["-y", "citeproof-mcp"],
      "env": {
        "CITEPROOF_MCP_HOME": "/Users/you/Documents/citeproof",
        "CITEPROOF_MCP_SOURCE": "citeproof-mcp==0.2.0"
      }
    }
  }
}
```

## Manual install instead of auto-install

```bash
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# Windows (PowerShell)
powershell -ExecutionPolicy Bypass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Then rerun the client, or point `CITEPROOF_UVX` at the installed binary. Full instructions: <https://docs.astral.sh/uv/getting-started/installation/>.

## Links

- Server, tools and documentation: <https://github.com/Aryan-Pardeshi/CiteProof-AI-MCP/tree/main/MCP>
- Hosted MCP endpoint: `https://api.citeproof.top/mcp`
- Issues: <https://github.com/Aryan-Pardeshi/CiteProof-AI-MCP/issues>

## Licence

MIT. See [LICENSE](LICENSE).
