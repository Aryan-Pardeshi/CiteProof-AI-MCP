// SPDX-License-Identifier: MIT
'use strict';

// The npm package must ship the Python server source. These tests build the
// bundle into a temporary folder so they never touch the generated npm/python.

const test = require('node:test');
const assert = require('node:assert/strict');
const { execFileSync } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const { bundle, copyFilter } = require('../scripts/bundle-python');
const pkg = require('../package.json');

const NPM_DIR = path.resolve(__dirname, '..');

function withTempDir(run) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'citeproof-bundle-'));
  try {
    return run(dir);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
}

test('package.json publishes python/ and builds it in prepack', () => {
  assert.ok(pkg.files.includes('python/'));
  assert.equal(pkg.scripts.prepack, 'node scripts/bundle-python.js');
  const pyproject = fs.readFileSync(path.join(__dirname, '..', '..', 'pyproject.toml'), 'utf8');
  assert.equal(pkg.version, /^version = "([^"]+)"/m.exec(pyproject)[1]);
});

test('the bundle copies the package, NOTICE and licences', () => {
  withTempDir((dir) => {
    const result = bundle(dir);

    assert.equal(result.target, path.resolve(dir));
    assert.ok(result.files > 10);
    assert.ok(fs.existsSync(path.join(dir, 'citeproof_mcp', '__init__.py')));
    assert.ok(fs.existsSync(path.join(dir, 'citeproof_mcp', '__main__.py')));
    assert.ok(fs.existsSync(path.join(dir, 'citeproof_mcp', 'compat.py')));
    assert.ok(fs.existsSync(path.join(dir, 'citeproof_mcp', 'tools', 'core.py')));
    assert.ok(fs.existsSync(path.join(dir, 'NOTICE.md')));
    assert.ok(fs.existsSync(path.join(dir, 'LICENSES', 'Apache-2.0.txt')));
  });
});

test('the bundle leaves out bytecode and caches', () => {
  withTempDir((dir) => {
    bundle(dir);

    const walk = (folder) => {
      for (const entry of fs.readdirSync(folder, { withFileTypes: true })) {
        const full = path.join(folder, entry.name);
        assert.notEqual(entry.name, '__pycache__');
        assert.ok(!/\.py[co]$/.test(entry.name), `unexpected bytecode: ${full}`);
        if (entry.isDirectory()) walk(full);
      }
    };
    walk(dir);
  });
});

test('the bundle replaces a previous copy instead of merging stale files', () => {
  withTempDir((dir) => {
    fs.mkdirSync(path.join(dir, 'citeproof_mcp'), { recursive: true });
    fs.writeFileSync(path.join(dir, 'citeproof_mcp', 'stale.py'), '# stale\n');
    bundle(dir);
    assert.ok(!fs.existsSync(path.join(dir, 'citeproof_mcp', 'stale.py')));
    assert.ok(fs.existsSync(path.join(dir, 'citeproof_mcp', 'server.py')));
  });
});

test('copyFilter drops __pycache__, .pytest_cache and .pyc files', () => {
  assert.equal(copyFilter('/x/__pycache__'), false);
  assert.equal(copyFilter('/x/.pytest_cache'), false);
  assert.equal(copyFilter('/x/module.pyc'), false);
  assert.equal(copyFilter('/x/module.pyo'), false);
  assert.equal(copyFilter('/x/module.py'), true);
  assert.equal(copyFilter('/x/NOTICE.md'), true);
});

test('the CLI accepts a destination and writes the same layout', () => {
  withTempDir((dir) => {
    const target = path.join(dir, 'out');
    const stdout = execFileSync(process.execPath, [path.join(NPM_DIR, 'scripts', 'bundle-python.js'), target], {
      cwd: NPM_DIR,
      encoding: 'utf8',
    });

    assert.match(stdout, /bundled the Python server into /);
    assert.ok(fs.existsSync(path.join(target, 'citeproof_mcp', 'store.py')));
    assert.ok(fs.existsSync(path.join(target, 'NOTICE.md')));
  });
});
