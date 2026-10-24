#!/usr/bin/env node
// SPDX-License-Identifier: MIT
'use strict';

// stdout is the MCP channel: this file must never write to it (except for
// --help / --version, when no MCP session is running). All diagnostics go to
// stderr.

const { runCli } = require('../lib/cli');

runCli({ argv: process.argv.slice(2) }).then(
  (code) => {
    process.exitCode = typeof code === 'number' ? code : 1;
  },
  (error) => {
    process.stderr.write(`citeproof-mcp: ${error && error.message ? error.message : String(error)}\n`);
    process.exitCode = 1;
  }
);
