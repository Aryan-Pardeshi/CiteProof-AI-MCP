#!/usr/bin/env node
// SPDX-License-Identifier: MIT
'use strict';

// Build step for the npm package: copy the pure-Python MCP server into
// npm/python/ so `npm pack` ships it. The launcher can then run the server with
// a Python 3.12+ that is already on the machine, without uv, a wheel download
// or network access. `npm pack` and `npm publish` run this through `prepack`.
//
// Usage: node scripts/bundle-python.js [destination]
// The destination defaults to npm/python and is generated, never committed.

const fs = require('node:fs');
const path = require('node:path');

const npmDir = path.resolve(__dirname, '..');
const mcpDir = path.resolve(npmDir, '..');

const SKIP_DIRS = new Set(['__pycache__', '.pytest_cache']);
const SKIP_FILE = /\.py[co]$/;

function copyFilter(source) {
  const base = path.basename(source);
  if (SKIP_DIRS.has(base)) return false;
  return !SKIP_FILE.test(base);
}

function countFiles(dir) {
  let total = 0;
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    if (entry.isDirectory()) total += countFiles(path.join(dir, entry.name));
    else if (entry.isFile()) total += 1;
  }
  return total;
}

function bundle(destination = path.join(npmDir, 'python')) {
  const target = path.resolve(destination);
  const packageDir = path.join(mcpDir, 'citeproof_mcp');
  const licensesDir = path.join(mcpDir, 'LICENSES');
  const notice = path.join(mcpDir, 'NOTICE.md');

  if (!fs.existsSync(packageDir)) {
    throw new Error(`cannot find the Python package at ${packageDir}`);
  }

  fs.rmSync(target, { recursive: true, force: true });
  fs.mkdirSync(target, { recursive: true });
  fs.cpSync(packageDir, path.join(target, 'citeproof_mcp'), { recursive: true, filter: copyFilter });
  if (fs.existsSync(licensesDir)) {
    fs.cpSync(licensesDir, path.join(target, 'LICENSES'), { recursive: true, filter: copyFilter });
  }
  if (fs.existsSync(notice)) {
    fs.copyFileSync(notice, path.join(target, 'NOTICE.md'));
  }

  return { target, files: countFiles(target) };
}

if (require.main === module) {
  try {
    const result = bundle(process.argv[2]);
    process.stdout.write(`citeproof-mcp: bundled the Python server into ${result.target} (${result.files} files)\n`);
  } catch (error) {
    process.stderr.write(`citeproof-mcp: could not bundle the Python server: ${error && error.message ? error.message : error}\n`);
    process.exitCode = 1;
  }
}

module.exports = { bundle, copyFilter };
