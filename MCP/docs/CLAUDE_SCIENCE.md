# Use CiteProof MCP in Claude Science

This page shows how to add the CiteProof MCP server to Claude Science as a **Local command** connector. The npx method is the recommended install; the bundle method is the fallback. Both were tested on Windows 11 with the native sandbox (Claude Science 0.1.67 for the bundle, 0.1.70 for the npx failure that led to the bundled-server launcher). Other platforms may differ; if you try them, please open an issue.

## Contents

- [Recommended: npx](#recommended-npx)
- [Fallback: bundle](#fallback-bundle)
- [Update after a new release](#update-after-a-new-release)
- [Where your review data lives](#where-your-review-data-lives)
- [Troubleshooting](#troubleshooting)
- [Other MCP clients](#other-mcp-clients)

## Recommended: npx

Claude Science stages the npm package into the connector's own workspace. The package ships the Python server source, and the launcher runs it with the sandbox's own `python3`, which already has `mcp`, `pydantic` and `httpx`. So this method needs no uv, no download and no network access, including no access to astral.sh, GitHub or PyPI.

The launcher checks, in order: `CITEPROOF_MCP_PYTHON` (or `--python`), `uvx` if it is present, then `python3`, `python` and (on Windows) `py -3` on `PATH`, requiring Python 3.12+ with the three packages. Inside the sandbox the third step succeeds with `python3`.

1. Open **Customize → Connectors**.
2. Choose **Add connector → Local command**.
3. Set **Name** to `citeproof` (lowercase letters, digits and hyphens only) and the **Command** to:

   ```text
   npx -y citeproof-mcp
   ```

4. In **Advanced settings → Environment variables**, keep the review data inside the connector's workspace, because the sandbox only lets the connector write there:

   ```text
   CITEPROOF_MCP_HOME=C:/Users/<you>/.claude-science/orgs/<org-id>/workspaces/_mcp-citeproof/data
   ```

   On Windows the workspace folder is `C:/Users/<you>/.claude-science/orgs/<org-id>/workspaces/_mcp-citeproof`. It normally appears after the first start attempt of the connector. `claude-science logs` also prints it as `cwd` in the sandbox policy lines for `_mcp-citeproof`. If you leave `CITEPROOF_MCP_HOME` out, review files go to `~/.citeproof-mcp`, which may be outside the sandbox's writable area.

5. Save and start a new conversation.
6. **Check the tool list.** The connector page should list 24 tools, from `review_create` to `export_bundle`. By default Claude Science asks for approval each time a tool runs. You can set each tool to always allow once you trust it.
7. **Start a review.** Ask Claude Science to create a review with `review_create`, then follow the order in [SPEC.md](SPEC.md#workflow). `review_status` tells the agent what to do next at any point.

If the connector reports `uv (uvx) was not found ... No Python 3.12+ with the mcp, pydantic and httpx packages was found either`, the launcher did not find a Python with the required packages. The bundle method below can help when npx cannot stage or read the server source, but it still uses Claude Science's `python3` and needs those packages. If that interpreter lacks them, set `CITEPROOF_MCP_PYTHON` to an interpreter readable inside the sandbox that has them.

## Fallback: bundle

Claude Science's sandbox can read the connector's own workspace folder and Claude Science's bundled runtime, but not a project folder or a Python virtual environment somewhere else on your disk. A command that points at your own `.venv` fails with "interpreter not readable inside the sandbox".

The bundle method copies the server into the connector's workspace folder and starts it with that same sandbox `python3`. Use it when npx cannot stage or read the server source. It does not need npx or uv, but it still requires Python 3.12+ with `mcp`, `pydantic` and `httpx` installed in the sandbox.

We do not ask you to run `claude-science sandbox setup`. That command changes Windows folder permissions, and you do not need it for this install.

1. **Add the connector once so Claude Science creates its workspace folder.** Open Customize, then Connectors, then Add connector, then Local command. Use the name `citeproof` and the command line `python3 placeholder.py`. It will show an error. That is expected. On our machine the workspace folder appeared after the first start attempt of a connector. If it does not appear on yours, create the folder from step 2 by hand.
2. **Find the workspace folder.** On Windows it is:

   ```text
   C:/Users/<you>/.claude-science/orgs/<org-id>/workspaces/_mcp-citeproof
   ```

   `claude-science logs` also prints it as `cwd` in the sandbox policy lines for `_mcp-citeproof`.
3. **Build the bundle into that folder.** From the root of this repository:

   ```bash
   python MCP/scripts/build_claude_science_bundle.py "C:/Users/<you>/.claude-science/orgs/<org-id>/workspaces/_mcp-citeproof/bundle"
   ```

   The script needs only the Python standard library. It writes into an empty folder or replaces a previous bundle. It refuses any folder that holds other files, so passing the connector's workspace folder by mistake cannot delete your `data` folder.
4. **Point the connector at the bundle.** Remove the placeholder connector and add it again with:

   - Name: `citeproof`
   - Command line (use forward slashes; backslashes are stripped by the form):

     ```text
     python3 C:/Users/<you>/.claude-science/orgs/<org-id>/workspaces/_mcp-citeproof/bundle/run.py
     ```

   - Advanced settings, environment variables:

     ```text
     CITEPROOF_MCP_HOME=C:/Users/<you>/.claude-science/orgs/<org-id>/workspaces/_mcp-citeproof/data
     ```

5. **Check the tool list.** The connector page should list 24 tools, from `review_create` to `export_bundle`.
6. **Start a review** with `review_create`, then follow the order in [SPEC.md](SPEC.md#workflow).

## Update after a new release

**npx:** the package name resolves to the newest published version, but Claude Science keeps the copy it staged. Remove the connector and add it again with the same command and environment variables to pick up the new launcher and server. Your review data is in the `data` folder and survives this.

**Bundle:** run the build script again with the same destination, then reconnect:

```bash
python MCP/scripts/build_claude_science_bundle.py "<the same bundle folder>"
```

The running server keeps the old code in memory. The Connectors page has no restart button when the connector is healthy, so remove the connector and add it again with the same settings. Your review data is in the `data` folder and survives this.

## Where your review data lives

One SQLite file per review under `CITEPROOF_MCP_HOME/reviews/`. Nothing is sent to us. The only network calls are DOI checks to Crossref and doi.org when the agent calls `verify_dois`.

The sandbox only lets the connector write inside its own workspace folder, which is why the data folder sits there. If you remove the connector, keep the `data` folder, or copy it first.

## Troubleshooting

| Message in the connector page | Cause and fix |
|---|---|
| `uv (uvx) was not found ... No Python 3.12+ with the mcp, pydantic and httpx packages was found either` | The launcher did not find an interpreter with the packages. Set `CITEPROOF_MCP_PYTHON` to one that is readable inside the sandbox and has them. The bundle method only helps if the sandbox Python already has the packages but npx cannot stage or read the server source. |
| `npx` cannot reach the registry | Claude Science normally stages the package for you. If it does not, use the bundle method, which needs no network. |
| `this kernel's Python interpreter ... is not readable inside the sandbox` | The command points outside the workspace folder. Use `python3` with the bundle path from the fallback steps, or the npx command. |
| `Set "command" to node, npx or python3 ...` | You gave an absolute path to a Python that Claude Science does not provide. Use `python3` or `npx`. |
| `can't open file ... Permission denied` (bundle method) | The bundle is not inside the connector's workspace folder. Build it into `.../workspaces/_mcp-citeproof/bundle`. |
| Name rejected | Names allow lowercase letters, digits and hyphens only. |
| 18 tools instead of 24 | The old server process is still running. Remove and re-add the connector. |

## Other MCP clients

Any client that can start a stdio MCP server works. The npm launcher needs only Node.js 18 or newer:

```bash
npx -y citeproof-mcp
```

Or install the Python package and point the client at the `citeproof-mcp` command:

```bash
pip install ./MCP
```

For clients that use a JSON config (Claude Desktop, Cursor and similar), the entry looks like this:

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

If you leave `CITEPROOF_MCP_HOME` out, review files go to `~/.citeproof-mcp`.

These clients were not tested for this release. The server follows the standard MCP stdio protocol, and it was tested against both mcp 1.27 and mcp 2.x.
