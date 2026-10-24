// SPDX-License-Identifier: MIT
'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');

const { runCli } = require('../lib/cli');
const pkg = require('../package.json');
const { defaultSource } = require('../lib/source');
const { captureStream, fakeProcess, memoryFs, scriptedSpawn } = require('../test-support/helpers');

const UVX = '/home/a/.local/bin/uvx';

function baseOptions(overrides = {}) {
  return {
    env: { HOME: '/home/a' },
    platform: 'linux',
    home: '/home/a',
    fs: memoryFs([UVX]),
    stdout: captureStream(),
    stderr: captureStream(),
    process: fakeProcess(),
    ...overrides,
  };
}

test('--version prints the launcher version to stdout and starts nothing', async () => {
  const options = baseOptions({ argv: ['--version'], spawn: scriptedSpawn([]) });
  const code = await runCli(options);

  assert.equal(code, 0);
  assert.equal(options.stdout.text, `${pkg.version}\n`);
  assert.equal(options.spawn.calls.length, 0);
});

test('--help prints usage and starts nothing', async () => {
  const options = baseOptions({ argv: ['--help'], spawn: scriptedSpawn([]) });
  const code = await runCli(options);

  assert.equal(code, 0);
  assert.match(options.stdout.text, /npx -y citeproof-mcp/);
  assert.match(options.stdout.text, /CITEPROOF_NO_AUTO_INSTALL/);
  assert.equal(options.spawn.calls.length, 0);
});

test('normal launch writes nothing to stdout and forwards the exit code', async () => {
  const spawn = scriptedSpawn([(call) => call.child.emit('exit', 7, null)]);
  const options = baseOptions({ argv: ['--http', '--port', '8080'], spawn });
  const code = await runCli(options);

  assert.equal(code, 7);
  assert.equal(options.stdout.text, '', 'stdout must stay reserved for MCP frames');
  assert.equal(spawn.calls.length, 1);

  const [call] = spawn.calls;
  assert.equal(call.command, UVX);
  assert.equal(call.options.stdio, 'inherit');
  assert.deepEqual(call.args, [
    '--from',
    defaultSource(pkg.version),
    '--python',
    '3.12',
    'citeproof-mcp',
    '--http',
    '--port',
    '8080',
  ]);
});

test('CITEPROOF_MCP_SOURCE is forwarded to uvx', async () => {
  const spawn = scriptedSpawn([(call) => call.child.emit('exit', 0, null)]);
  const options = baseOptions({
    argv: [],
    spawn,
    env: { HOME: '/home/a', CITEPROOF_MCP_SOURCE: '/work/MCP' },
  });
  await runCli(options);

  const args = spawn.calls[0].args;
  assert.equal(args[args.indexOf('--from') + 1], '/work/MCP');
});

test('CITEPROOF_NO_AUTO_INSTALL=1 fails with the manual link and no spawn', async () => {
  const spawn = scriptedSpawn([]);
  const options = baseOptions({ argv: [], spawn, fs: memoryFs([]), env: { HOME: '/home/a', CITEPROOF_NO_AUTO_INSTALL: '1' } });
  const code = await runCli(options);

  assert.equal(code, 1);
  assert.equal(spawn.calls.length, 0);
  assert.equal(options.stdout.text, '');
  assert.match(options.stderr.text, /automatic installation is disabled/);
  assert.match(options.stderr.text, /https:\/\/docs\.astral\.sh\/uv\/getting-started\/installation\//);
});

test('missing uv is installed automatically, then uvx starts the server', async () => {
  const fs = memoryFs(['/usr/bin/curl']);
  const spawn = scriptedSpawn([
    (call) => {
      fs.add(UVX);
      call.child.emit('exit', 0, null);
    },
    (call) => call.child.emit('exit', 0, null),
  ]);
  const options = baseOptions({
    argv: [],
    spawn,
    fs,
    env: { HOME: '/home/a', PATH: '/usr/bin' },
  });
  const code = await runCli(options);

  assert.equal(code, 0);
  assert.equal(options.stdout.text, '');
  assert.equal(spawn.calls.length, 2);

  const installer = spawn.calls[0];
  assert.equal(installer.command, 'sh');
  assert.deepEqual(installer.args, ['-c', 'curl -LsSf https://astral.sh/uv/install.sh | sh']);
  assert.deepEqual(installer.options.stdio, ['ignore', 2, 2]);
  assert.equal(installer.options.env.UV_NO_MODIFY_PATH, '1');

  assert.equal(spawn.calls[1].command, UVX);
  assert.match(options.stderr.text, /Installing uv from https:\/\/astral\.sh\/uv\/install\.sh/);
  assert.match(options.stderr.text, /uv installed/);
});

test('no curl or wget means the launcher cannot auto-install and exits 1', async () => {
  const spawn = scriptedSpawn([]);
  const options = baseOptions({ argv: [], spawn, fs: memoryFs([]), env: { HOME: '/home/a', PATH: '' } });
  const code = await runCli(options);

  assert.equal(code, 1);
  assert.equal(spawn.calls.length, 0);
  assert.equal(options.stdout.text, '');
  assert.match(options.stderr.text, /neither curl nor wget/);
  assert.match(options.stderr.text, /getting-started\/installation/);
});

test('a failed uv install exits 1 with the manual link', async () => {
  const fs = memoryFs(['/usr/bin/curl']);
  const spawn = scriptedSpawn([(call) => call.child.emit('exit', 2, null)]);
  const options = baseOptions({ argv: [], spawn, fs, env: { HOME: '/home/a', PATH: '/usr/bin' } });
  const code = await runCli(options);

  assert.equal(code, 1);
  assert.equal(spawn.calls.length, 1);
  assert.equal(options.stdout.text, '');
  assert.match(options.stderr.text, /exited with code 2/);
  assert.match(options.stderr.text, /getting-started\/installation/);
});

test('a spawn failure is reported on stderr with a non-zero exit', async () => {
  const spawn = scriptedSpawn([(call) => call.child.emit('error', new Error('spawn uvx ENOENT'))]);
  const options = baseOptions({ argv: [], spawn });
  const code = await runCli(options);

  assert.equal(code, 1);
  assert.equal(options.stdout.text, '');
  assert.match(options.stderr.text, /Failed to start uvx/);
  assert.match(options.stderr.text, /ENOENT/);
});

test('SIGINT/SIGTERM are forwarded to the server and signal exits map to 128+n', async () => {
  const spawn = scriptedSpawn([() => {}]);
  const processRef = fakeProcess();
  const options = baseOptions({ argv: [], spawn, process: processRef });

  const pending = runCli(options);
  await new Promise((resolve) => setImmediate(resolve));

  assert.equal(spawn.calls.length, 1);
  assert.equal(processRef.listenerCount('SIGINT'), 1);
  assert.equal(processRef.listenerCount('SIGTERM'), 1);

  processRef.emit('SIGINT');
  assert.equal(spawn.calls[0].child.killedWith, 'SIGINT');

  spawn.calls[0].child.emit('exit', null, 'SIGTERM');

  const code = await pending;
  assert.equal(code, 143);
  assert.equal(processRef.listenerCount('SIGINT'), 0);
  assert.equal(processRef.listenerCount('SIGTERM'), 0);
});
