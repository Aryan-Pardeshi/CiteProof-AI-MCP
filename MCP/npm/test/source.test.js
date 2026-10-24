// SPDX-License-Identifier: MIT
'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');

const { buildUvxArgs, defaultSource, resolveSource } = require('../lib/source');

const VERSION = '0.2.0';

test('default source pins the git tag that matches the launcher version', () => {
  const source = defaultSource(VERSION);
  assert.equal(
    source,
    'https://github.com/Aryan-Pardeshi/CiteProof-AI-MCP/releases/download/v0.2.0/citeproof_mcp-0.2.0-py3-none-any.whl'
  );
  assert.deepEqual(buildUvxArgs({ version: VERSION, env: {} }), [
    '--from',
    source,
    '--python',
    '3.12',
    'citeproof-mcp',
  ]);
});

test('CITEPROOF_MCP_SOURCE overrides the default source', () => {
  assert.equal(resolveSource({ CITEPROOF_MCP_SOURCE: 'citeproof-mcp==0.2.0' }, VERSION), 'citeproof-mcp==0.2.0');
  assert.equal(
    resolveSource({ CITEPROOF_MCP_SOURCE: '  /work/MCP  ' }, VERSION),
    '/work/MCP',
    'surrounding whitespace is trimmed'
  );
});

test('blank CITEPROOF_MCP_SOURCE falls back to the default', () => {
  assert.equal(resolveSource({ CITEPROOF_MCP_SOURCE: '   ' }, VERSION), defaultSource(VERSION));
  assert.equal(resolveSource({}, VERSION), defaultSource(VERSION));
});

test('extra CLI args are passed through after the server name', () => {
  const args = buildUvxArgs({ version: VERSION, env: {}, extraArgs: ['--http', '--port', '8080'] });
  assert.deepEqual(args.slice(-3), ['--http', '--port', '8080']);
  assert.equal(args[args.indexOf('citeproof-mcp') + 1], '--http');
  assert.equal(args[args.length - 1], '8080');
});

test('missing extraArgs is treated as an empty list', () => {
  const args = buildUvxArgs({ version: VERSION, env: {} });
  assert.equal(args[args.length - 1], 'citeproof-mcp');
});
