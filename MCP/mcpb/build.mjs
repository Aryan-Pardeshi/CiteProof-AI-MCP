#!/usr/bin/env node
// SPDX-License-Identifier: MIT
// Build the Claude Desktop extension (MCPB) from the npm launcher.
// No dependencies: copy the launcher into dist/, validate the manifest, pack.

import { copyFileSync, cpSync, mkdirSync, rmSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const npmDir = path.resolve(here, '..', 'npm');
const dist = path.join(here, 'dist');
const bundle = path.join(here, 'citeproof.mcpb');

const quote = (value) => `"${value}"`;

function run(command) {
  const result = spawnSync(command, { stdio: 'inherit', shell: true });
  if (result.error) {
    console.error(`citeproof-mcpb: could not run ${command}: ${result.error.message}`);
    process.exit(1);
  }
  if (result.status !== 0) {
    console.error(`citeproof-mcpb: command failed (exit ${result.status}): ${command}`);
    process.exit(result.status ?? 1);
  }
}

rmSync(dist, { recursive: true, force: true });
rmSync(bundle, { force: true });
mkdirSync(path.join(dist, 'server'), { recursive: true });

// The manifest points at server/bin/citeproof-mcp.js, so keep the npm layout.
cpSync(path.join(npmDir, 'bin'), path.join(dist, 'server', 'bin'), { recursive: true });
cpSync(path.join(npmDir, 'lib'), path.join(dist, 'server', 'lib'), { recursive: true });
// The bundled Python source lets the launcher run without uv (see scripts/bundle-python.js).
run(`node ${quote(path.join(npmDir, 'scripts', 'bundle-python.js'))}`);
cpSync(path.join(npmDir, 'python'), path.join(dist, 'server', 'python'), { recursive: true });
copyFileSync(path.join(npmDir, 'package.json'), path.join(dist, 'server', 'package.json'));
copyFileSync(path.join(npmDir, 'LICENSE'), path.join(dist, 'LICENSE'));
copyFileSync(path.join(here, 'manifest.json'), path.join(dist, 'manifest.json'));
copyFileSync(path.join(here, 'icon.png'), path.join(dist, 'icon.png'));

const mcpb = ['npx', '-y', '@anthropic-ai/mcpb'];

run([...mcpb, 'validate', quote(dist)].join(' '));
run([...mcpb, 'pack', quote(dist), quote(bundle)].join(' '));

console.log(`citeproof-mcpb: built ${bundle}`);
