// SPDX-License-Identifier: MIT
'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');

const { runCli } = require('../lib/cli');
const {
  createUvHomeDirs,
  parseUvHomeArgs,
  uvHomeDir,
  withInstallerEnv,
  withUvHomeEnv,
} = require('../lib/uv-home');
const { captureStream, fakeProcess, memoryFs, scriptedSpawn } = require('../test-support/helpers');

const UVX_NAME = 'uvx';

test('--uv-home <dir> is removed from the arguments passed to the server', () => {
  const parsed = parseUvHomeArgs(['--uv-home', '/tmp/uv', '--verbose']);
  assert.deepEqual(parsed, { args: ['--verbose'], uvHome: '/tmp/uv', error: null });
});

test('--uv-home=<dir> is accepted too', () => {
  const parsed = parseUvHomeArgs(['--uv-home=/tmp/uv', '--verbose']);
  assert.deepEqual(parsed, { args: ['--verbose'], uvHome: '/tmp/uv', error: null });
});

test('arguments without --uv-home are passed through unchanged', () => {
  const parsed = parseUvHomeArgs(['--verbose', '--port', '8080']);
  assert.deepEqual(parsed, { args: ['--verbose', '--port', '8080'], uvHome: null, error: null });
});

test('--uv-home without a value is a clear error', () => {
  assert.match(parseUvHomeArgs(['--uv-home']).error, /needs a directory/);
  assert.match(parseUvHomeArgs(['--uv-home=']).error, /needs a directory/);
});

test('the flag wins over CITEPROOF_UV_HOME and relative paths resolve against cwd', () => {
  const cwd = path.resolve('/work');
  assert.equal(uvHomeDir({ flag: 'flag-home', env: { CITEPROOF_UV_HOME: 'env-home' }, cwd }), path.resolve(cwd, 'flag-home'));
  assert.equal(uvHomeDir({ flag: null, env: { CITEPROOF_UV_HOME: 'env-home' }, cwd }), path.resolve(cwd, 'env-home'));
  assert.equal(uvHomeDir({ flag: null, env: {}, cwd }), null);
  assert.equal(uvHomeDir({ flag: '  ', env: { CITEPROOF_UV_HOME: '   ' }, cwd }), null);
});

test('createUvHomeDirs makes cache, tools, python and bin', () => {
  const dir = path.resolve('/work/uv-home');
  const fs = memoryFs([]);
  createUvHomeDirs({ dir, fs });
  assert.deepEqual(fs.dirs, ['cache', 'tools', 'python', 'bin'].map((sub) => path.join(dir, sub).replace(/\\/g, '/')));
});

test('withUvHomeEnv sets the three uv folders', () => {
  const dir = path.resolve('/work/uv-home');
  assert.deepEqual(withUvHomeEnv({ HOME: '/home/a' }, dir), {
    HOME: '/home/a',
    UV_CACHE_DIR: path.join(dir, 'cache'),
    UV_TOOL_DIR: path.join(dir, 'tools'),
    UV_PYTHON_INSTALL_DIR: path.join(dir, 'python'),
  });
});

test('withInstallerEnv adds UV_INSTALL_DIR for the auto-installer', () => {
  const dir = path.resolve('/work/uv-home');
  assert.equal(withInstallerEnv({}, dir).UV_INSTALL_DIR, path.join(dir, 'bin'));
});

function uvHomeRunOptions(overrides = {}) {
  const cwd = path.resolve('/work');
  const uvHome = path.resolve(cwd, 'uv-home');
  const uvx = path.join(uvHome, 'bin', UVX_NAME);
  return {
    uvHome,
    uvx,
    options: {
      argv: ['--uv-home', 'uv-home'],
      cwd,
      env: { HOME: '/home/a', PATH: '' },
      platform: 'linux',
      home: '/home/a',
      fs: memoryFs([uvx]),
      stdout: captureStream(),
      stderr: captureStream(),
      process: fakeProcess(),
      ...overrides,
    },
  };
}

test('--uv-home creates the folders and runs uvx from <dir>/bin', async () => {
  const { uvHome, uvx, options } = uvHomeRunOptions({
    spawn: scriptedSpawn([(call) => call.child.emit('exit', 0, null)]),
  });
  const code = await runCli(options);

  assert.equal(code, 0);
  assert.equal(options.stdout.text, '');
  assert.equal(options.spawn.calls.length, 1);
  assert.equal(options.spawn.calls[0].command, uvx);

  const env = options.spawn.calls[0].options.env;
  assert.equal(env.UV_CACHE_DIR, path.join(uvHome, 'cache'));
  assert.equal(env.UV_TOOL_DIR, path.join(uvHome, 'tools'));
  assert.equal(env.UV_PYTHON_INSTALL_DIR, path.join(uvHome, 'python'));

  for (const sub of ['cache', 'tools', 'python', 'bin']) {
    assert.ok(options.fs.has(path.join(uvHome, sub)), `${sub} was not created`);
  }
});

test('CITEPROOF_UV_HOME is used when the flag is absent', async () => {
  const cwd = path.resolve('/work');
  const uvHome = path.resolve(cwd, 'from-env');
  const uvx = path.join(uvHome, 'bin', UVX_NAME);
  const spawn = scriptedSpawn([(call) => call.child.emit('exit', 0, null)]);
  const options = {
    argv: [],
    cwd,
    env: { HOME: '/home/a', PATH: '', CITEPROOF_UV_HOME: 'from-env' },
    platform: 'linux',
    home: '/home/a',
    fs: memoryFs([uvx]),
    spawn,
    stdout: captureStream(),
    stderr: captureStream(),
    process: fakeProcess(),
  };

  const code = await runCli(options);

  assert.equal(code, 0);
  assert.equal(spawn.calls[0].command, uvx);
  assert.equal(spawn.calls[0].options.env.UV_CACHE_DIR, path.join(uvHome, 'cache'));
});

test('the flag wins over CITEPROOF_UV_HOME', async () => {
  const cwd = path.resolve('/work');
  const fromFlag = path.resolve(cwd, 'flag-home');
  const spawn = scriptedSpawn([(call) => call.child.emit('exit', 0, null)]);
  const options = {
    argv: ['--uv-home', 'flag-home'],
    cwd,
    env: { HOME: '/home/a', PATH: '', CITEPROOF_UV_HOME: 'env-home' },
    platform: 'linux',
    home: '/home/a',
    fs: memoryFs([path.join(fromFlag, 'bin', UVX_NAME)]),
    spawn,
    stdout: captureStream(),
    stderr: captureStream(),
    process: fakeProcess(),
  };

  await runCli(options);
  assert.equal(spawn.calls[0].command, path.join(fromFlag, 'bin', UVX_NAME));
});

test('a --uv-home without a value exits 1 before doing anything', async () => {
  const spawn = scriptedSpawn([]);
  const options = {
    argv: ['--uv-home'],
    env: { HOME: '/home/a' },
    platform: 'linux',
    home: '/home/a',
    fs: memoryFs([]),
    spawn,
    stdout: captureStream(),
    stderr: captureStream(),
    process: fakeProcess(),
  };

  const code = await runCli(options);

  assert.equal(code, 1);
  assert.equal(spawn.calls.length, 0);
  assert.equal(options.stdout.text, '');
  assert.match(options.stderr.text, /needs a directory/);
});

test('auto-install with a uv home sets UV_INSTALL_DIR and then finds uvx there', async () => {
  const cwd = path.resolve('/work');
  const uvHome = path.resolve(cwd, 'uv-home');
  const uvx = path.join(uvHome, 'bin', UVX_NAME);
  const fs = memoryFs(['/usr/bin/curl']);
  const spawn = scriptedSpawn([
    (call) => {
      fs.add(uvx);
      call.child.emit('exit', 0, null);
    },
    (call) => call.child.emit('exit', 0, null),
  ]);
  const options = {
    argv: ['--uv-home', 'uv-home'],
    cwd,
    env: { HOME: '/home/a', PATH: '/usr/bin' },
    platform: 'linux',
    home: '/home/a',
    fs,
    spawn,
    stdout: captureStream(),
    stderr: captureStream(),
    process: fakeProcess(),
  };

  const code = await runCli(options);

  assert.equal(code, 0);
  assert.equal(spawn.calls.length, 2);
  assert.equal(spawn.calls[0].options.env.UV_INSTALL_DIR, path.join(uvHome, 'bin'));
  assert.equal(spawn.calls[0].options.env.UV_CACHE_DIR, path.join(uvHome, 'cache'));
  assert.equal(spawn.calls[1].command, uvx);
});
