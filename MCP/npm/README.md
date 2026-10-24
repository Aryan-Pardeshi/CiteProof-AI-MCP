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

CiteProof runs **entirely on your computer**. There is no hosted server, no account, and no upload of review data.

## Requirements

- Node.js 18 or newer.
- Network access on the first run: the launcher downloads the Python server as a wheel from the GitHub release (and uv, and Python 3.12, if needed). Git is not required.
- macOS, Linux or Windows.

## Install

### Claude Science

1. Open **Customize → Connectors**.
2. Choose **Add connector → Local command**.
3. Set **Name** to `citeproof` and the **Command** to:

   ```bash
   npx -y citeproof-mcp
   ```

4. Save and start a new conversation.

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

### Claude Desktop extension

If you prefer a one-click install, download `citeproof.mcpb` from the [latest GitHub release](https://github.com/Aryan-Pardeshi/CiteProof-AI-MCP/releases/latest) and open it with Claude Desktop. The bundle contains the same launcher and runs with Claude Desktop's built-in Node; uv is still downloaded on first use.

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

### claude.ai web

The claude.ai web custom connector UI expects a remote MCP server reachable over HTTP. CiteProof is a local stdio server and **is not supported there**. Use Claude Desktop, Claude Science, Claude Code or another desktop MCP client.

## Privacy

- The server and the launcher run locally. Review records are written to a local SQLite database under `CITEPROOF_MCP_HOME` (default `~/.citeproof-mcp`) and never leave your computer.
- The only network calls the server makes are DOI lookups in `verify_dois`: Crossref first, then doi.org, to check that a DOI is registered and not retracted. The DOIs you ask it to check are sent to those services; nothing else is.
- The launcher downloads the server, uv and Python on first use. It has no telemetry and no analytics.

## What the launcher does

1. Looks for `uvx` in this order: `CITEPROOF_UVX`, your `PATH`, `<CITEPROOF_UV_HOME>/bin`, then the usual uv install locations (`~/.local/bin/uvx`, `~/.cargo/bin/uvx`, and the Windows equivalents under `%USERPROFILE%` and `%LOCALAPPDATA%`).
2. If `uvx` is missing, it runs Astral's official installer non-interactively (`https://astral.sh/uv/install.sh`, or `install.ps1` on Windows) and prints progress to **stderr**. It does not edit your shell profiles.
3. Starts `uvx --from <source> --python 3.12 citeproof-mcp` with inherited stdio. The default source is this repository at the tag matching the launcher version, so uv also downloads Python 3.12 if your machine does not have it.
4. Forwards SIGINT/SIGTERM to the server and exits with the server's exit code. Extra arguments after the launcher flags are passed through to the server unchanged.

stdout is reserved for the MCP protocol. The launcher never writes to it during a session; all diagnostics go to stderr. There is no telemetry.

## Environment variables

| Variable | Purpose |
| --- | --- |
| `CITEPROOF_MCP_HOME` | Data directory for the server's local SQLite records (default `~/.citeproof-mcp`). |
| `CITEPROOF_MCP_SOURCE` | Override the `uvx --from` value, for example `citeproof-mcp==0.2.0` once the server is on PyPI, or an absolute path to a local checkout of `MCP/`. |
| `CITEPROOF_UV_HOME` | Keep everything uv writes in one folder: `cache/`, `tools/`, `python/` and `bin/`. Useful inside a sandbox. The `--uv-home <dir>` flag does the same and wins over the variable. |
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

## Troubleshooting

**Claude Science says a file is not readable inside the sandbox.** uv normally writes to `~/.local/share/uv`, `~/.cache/uv` and similar folders, which may sit outside the connector's workspace. Set `CITEPROOF_UV_HOME` to a folder inside the connector's workspace, for example:

```json
{
  "mcpServers": {
    "citeproof": {
      "command": "npx",
      "args": ["-y", "citeproof-mcp", "--uv-home", "/path/to/workspace/.citeproof-uv"]
    }
  }
}
```

The launcher creates `cache/`, `tools/`, `python/` and `bin/` there and points `UV_CACHE_DIR`, `UV_TOOL_DIR`, `UV_PYTHON_INSTALL_DIR` and (for the auto-installer) `UV_INSTALL_DIR` at them.

**uv is missing and auto-install is disabled.** Install uv manually, then rerun the client:

```bash
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# Windows (PowerShell)
powershell -ExecutionPolicy Bypass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Full instructions: <https://docs.astral.sh/uv/getting-started/installation/>. You can also point `CITEPROOF_UVX` at the installed binary.

## Links

- Server, tools and documentation: <https://github.com/Aryan-Pardeshi/CiteProof-AI-MCP/tree/main/MCP>
- Claude Desktop extension (`citeproof.mcpb`): <https://github.com/Aryan-Pardeshi/CiteProof-AI-MCP/releases/latest>
- Issues: <https://github.com/Aryan-Pardeshi/CiteProof-AI-MCP/issues>

## Licence

MIT. See [LICENSE](LICENSE).
