// SPDX-License-Identifier: MIT
'use strict';

// Build the uvx command line. The launcher never reads papers or talks to the
// network itself; it only starts the Python MCP server from this source.

const DEFAULT_REPOSITORY = 'git+https://github.com/Aryan-Pardeshi/CiteProof-AI-MCP';
const PACKAGE_NAME = 'citeproof-mcp';
const PYTHON_VERSION = '3.12';

/** Default uvx `--from` value: the repo tag matching the launcher version. */
function defaultSource(version) {
  return `${DEFAULT_REPOSITORY}@v${version}#subdirectory=MCP`;
}

/** CITEPROOF_MCP_SOURCE wins when it is a non-empty string. */
function resolveSource(env, version) {
  const override = env && env.CITEPROOF_MCP_SOURCE;
  if (typeof override === 'string' && override.trim()) return override.trim();
  return defaultSource(version);
}

/**
 * Arguments for uvx: uv flags first, the Python console script, then any extra
 * arguments the user passed through (for example `--help`).
 */
function buildUvxArgs({ version, env, extraArgs }) {
  const passthrough = Array.isArray(extraArgs) ? extraArgs : [];
  return ['--from', resolveSource(env || {}, version), '--python', PYTHON_VERSION, PACKAGE_NAME, ...passthrough];
}

module.exports = {
  DEFAULT_REPOSITORY,
  PACKAGE_NAME,
  PYTHON_VERSION,
  buildUvxArgs,
  defaultSource,
  resolveSource,
};
