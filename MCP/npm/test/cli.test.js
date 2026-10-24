// SPDX-License-Identifier: MIT
'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');

const { runCli } = require('../lib/cli');
const pkg = require('../package.json');
const { defaultSource } = require('../lib/source');
const { INSTALL_SH_URL, downloadThenRun } = require('../lib/install');
const { PYTHON_PROBE_CODE } = require('../lib/python');
const { captureStream, fakeProcess, memoryFs, scriptedSpawn } = require('../test-support/helpers');

const UVX = '/home/a/.local/bin/uvx';
const BUNDLED = path.resolve(__dirname, '..', 'python');

// A probe that fails: the candidate is missing, too old, or lacks a package.
const failedProbe = (call) => call.child.emit('exit', 3, null);
const passedProbe = (call) => call.child.emit('exit', 0, null);

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

test('CITEPROOF_NO_AUTO_INSTALL=1 fails with the manual link after probing and does not install', async () => {
  const spawn = scriptedSpawn([failedProbe]);
  const options = baseOptions({ argv: [], spawn, fs: memoryFs([]), env: { HOME: '/home/a', CITEPROOF_NO_AUTO_INSTALL: '1' } });
  const code = await runCli(options);

  assert.equal(code, 1);
  assert.equal(options.stdout.text, '');
  assert.equal(spawn.calls.length, 2, 'python3 and python are probed once each');
  assert.deepEqual(
    spawn.calls.map((call) => call.command),
    ['python3', 'python'],
    'no installer or server is spawned'
  );
  assert.match(options.stderr.text, /automatic installation is disabled/);
  assert.match(options.stderr.text, /https:\/\/docs\.astral\.sh\/uv\/getting-started\/installation\//);
  assert.match(options.stderr.text, /No Python 3\.12\+ with the mcp, pydantic and httpx packages was found either/);
});

test('missing uv is installed automatically, then uvx starts the server', async () => {
  const fs = memoryFs(['/usr/bin/curl']);
  const spawn = scriptedSpawn([
    failedProbe,
    failedProbe,
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
  assert.equal(spawn.calls.length, 4);

  const installer = spawn.calls[2];
  assert.equal(installer.command, 'sh');
  assert.deepEqual(installer.args, ['-c', downloadThenRun(`curl -LsSf ${INSTALL_SH_URL} -o "$t"`)]);
  assert.deepEqual(installer.options.stdio, ['ignore', 2, 2]);
  assert.equal(installer.options.env.UV_NO_MODIFY_PATH, '1');

  assert.equal(spawn.calls[3].command, UVX);
  assert.match(options.stderr.text, /Installing uv from https:\/\/astral\.sh\/uv\/install\.sh/);
  assert.match(options.stderr.text, /uv installed/);
});

test('no curl or wget means the launcher cannot auto-install and exits 1', async () => {
  const spawn = scriptedSpawn([failedProbe]);
  const options = baseOptions({ argv: [], spawn, fs: memoryFs([]), env: { HOME: '/home/a', PATH: '' } });
  const code = await runCli(options);

  assert.equal(code, 1);
  assert.equal(spawn.calls.length, 2, 'only the two Python probes ran');
  assert.equal(options.stdout.text, '');
  assert.match(options.stderr.text, /neither curl nor wget/);
  assert.match(options.stderr.text, /getting-started\/installation/);
});

test('a failed uv install exits 1 with the manual link and the Python hint', async () => {
  const fs = memoryFs(['/usr/bin/curl']);
  const spawn = scriptedSpawn([failedProbe, failedProbe, (call) => call.child.emit('exit', 2, null)]);
  const options = baseOptions({ argv: [], spawn, fs, env: { HOME: '/home/a', PATH: '/usr/bin' } });
  const code = await runCli(options);

  assert.equal(code, 1);
  assert.equal(spawn.calls.length, 3);
  assert.equal(options.stdout.text, '');
  assert.match(options.stderr.text, /exited with code 2/);
  assert.match(options.stderr.text, /No Python 3\.12\+ with the mcp, pydantic and httpx packages was found either/);
  assert.match(options.stderr.text, /Install uv, or set CITEPROOF_MCP_PYTHON to such a Python\./);
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

test('CITEPROOF_MCP_PYTHON runs the bundled server without uvx or probing', async () => {
  const spawn = scriptedSpawn([(call) => call.child.emit('exit', 0, null)]);
  const options = baseOptions({
    argv: ['--verbose'],
    spawn,
    fs: memoryFs([]),
    env: { HOME: '/home/a', PYTHONPATH: '/old/py', CITEPROOF_MCP_PYTHON: '/opt/python3.12' },
  });
  const code = await runCli(options);

  assert.equal(code, 0);
  assert.equal(options.stdout.text, '', 'stdout must stay reserved for MCP frames');
  assert.equal(spawn.calls.length, 1, 'a configured interpreter is used directly, with no probe');

  const [call] = spawn.calls;
  assert.equal(call.command, '/opt/python3.12');
  assert.deepEqual(call.args, ['-P', '-m', 'citeproof_mcp', '--verbose']);
  assert.equal(call.options.stdio, 'inherit');
  assert.equal(call.options.env.PYTHONUNBUFFERED, '1');
  assert.equal(call.options.env.PYTHONPATH, `${BUNDLED}:/old/py`);
  assert.match(options.stderr.text, /python mode: using \/opt\/python3\.12/);
});

test('--python wins over CITEPROOF_MCP_PYTHON and is removed from the server arguments', async () => {
  const spawn = scriptedSpawn([(call) => call.child.emit('exit', 0, null)]);
  const options = baseOptions({
    argv: ['--python', '/from/flag', '--http'],
    spawn,
    fs: memoryFs([]),
    env: { HOME: '/home/a', CITEPROOF_MCP_PYTHON: '/from/env' },
  });
  await runCli(options);

  assert.equal(spawn.calls.length, 1);
  assert.equal(spawn.calls[0].command, '/from/flag');
  assert.deepEqual(spawn.calls[0].args, ['-P', '-m', 'citeproof_mcp', '--http']);
});

test('--python=<path> is accepted too', async () => {
  const spawn = scriptedSpawn([(call) => call.child.emit('exit', 0, null)]);
  const options = baseOptions({ argv: ['--python=/from/equals'], spawn, fs: memoryFs([]) });
  await runCli(options);

  assert.equal(spawn.calls[0].command, '/from/equals');
  assert.deepEqual(spawn.calls[0].args, ['-P', '-m', 'citeproof_mcp']);
});

test('a missing --python value is a usage error and starts nothing', async () => {
  const spawn = scriptedSpawn([failedProbe]);
  const options = baseOptions({ argv: ['--python'], spawn, fs: memoryFs([]) });
  const code = await runCli(options);

  assert.equal(code, 1);
  assert.equal(spawn.calls.length, 0);
  assert.equal(options.stdout.text, '');
  assert.match(options.stderr.text, /--python needs an interpreter path/);
});

test('uvx wins over a Python on PATH: no probe runs', async () => {
  const spawn = scriptedSpawn([(call) => call.child.emit('exit', 0, null)]);
  const options = baseOptions({ argv: [], spawn });
  await runCli(options);

  assert.equal(spawn.calls.length, 1);
  assert.equal(spawn.calls[0].command, UVX);
  assert.match(options.stderr.text, /uvx mode: using /);
});

test('without uvx, the first working Python on PATH runs the bundled server', async () => {
  const spawn = scriptedSpawn([failedProbe, passedProbe, (call) => call.child.emit('exit', 0, null)]);
  const options = baseOptions({
    argv: ['--port', '8080'],
    spawn,
    fs: memoryFs([]),
    env: { HOME: '/home/a', PATH: '/usr/bin' },
  });
  const code = await runCli(options);

  assert.equal(code, 0);
  assert.equal(options.stdout.text, '');
  assert.equal(spawn.calls.length, 3);

  const [python3Probe, pythonProbe, server] = spawn.calls;
  assert.equal(python3Probe.command, 'python3');
  assert.deepEqual(python3Probe.args, ['-c', PYTHON_PROBE_CODE]);
  assert.equal(python3Probe.options.stdio, 'ignore');

  assert.equal(pythonProbe.command, 'python');
  assert.deepEqual(pythonProbe.args, ['-c', PYTHON_PROBE_CODE]);

  assert.equal(server.command, 'python');
  assert.deepEqual(server.args, ['-P', '-m', 'citeproof_mcp', '--port', '8080']);
  assert.equal(server.options.stdio, 'inherit');
  assert.equal(server.options.env.PYTHONPATH, BUNDLED);
  assert.equal(server.options.env.PYTHONUNBUFFERED, '1');
  assert.match(options.stderr.text, /python mode: using python/);
});

test('Windows probes python3, python, then py -3, and uses ";" in PYTHONPATH', async () => {
  const spawn = scriptedSpawn([failedProbe, failedProbe, passedProbe, (call) => call.child.emit('exit', 0, null)]);
  const options = baseOptions({
    argv: [],
    spawn,
    platform: 'win32',
    fs: memoryFs([]),
    home: 'C:\\Users\\a',
    env: { USERPROFILE: 'C:\\Users\\a', PATH: 'C:\\Python312', PYTHONPATH: 'C:\\old\\py' },
  });
  const code = await runCli(options);

  assert.equal(code, 0);
  assert.equal(spawn.calls.length, 4);
  assert.equal(spawn.calls[0].command, 'python3');
  assert.equal(spawn.calls[1].command, 'python');
  assert.equal(spawn.calls[2].command, 'py');
  assert.deepEqual(spawn.calls[2].args, ['-3', '-c', PYTHON_PROBE_CODE]);

  const server = spawn.calls[3];
  assert.equal(server.command, 'py');
  assert.deepEqual(server.args, ['-3', '-P', '-m', 'citeproof_mcp']);
  assert.equal(server.options.env.PYTHONPATH, `${BUNDLED};C:\\old\\py`);
  assert.match(options.stderr.text, /python mode: using py -3/);
});

test('a Python interpreter that fails to start is skipped', async () => {
  const spawn = scriptedSpawn([
    (call) => call.child.emit('error', new Error('spawn python3 ENOENT')),
    passedProbe,
    (call) => call.child.emit('exit', 0, null),
  ]);
  const options = baseOptions({ argv: [], spawn, fs: memoryFs([]) });
  const code = await runCli(options);

  assert.equal(code, 0);
  assert.equal(spawn.calls[2].command, 'python');
  assert.equal(spawn.calls[2].options.stdio, 'inherit');
});

test('a Python server start failure in python mode is reported on stderr', async () => {
  const spawn = scriptedSpawn([
    passedProbe,
    (call) => call.child.emit('error', new Error('spawn python ENOENT')),
  ]);
  const options = baseOptions({ argv: [], spawn, fs: memoryFs([]) });
  const code = await runCli(options);

  assert.equal(code, 1);
  assert.equal(options.stdout.text, '');
  assert.match(options.stderr.text, /Failed to start python/);
});

test('signals are forwarded in python mode too', async () => {
  const spawn = scriptedSpawn([passedProbe, () => {}]);
  const processRef = fakeProcess();
  const options = baseOptions({ argv: [], spawn, fs: memoryFs([]), process: processRef });

  const pending = runCli(options);
  await new Promise((resolve) => setImmediate(resolve));
  await new Promise((resolve) => setImmediate(resolve));

  assert.equal(spawn.calls[1].command, 'python3');
  processRef.emit('SIGTERM');
  assert.equal(spawn.calls[1].child.killedWith, 'SIGTERM');

  spawn.calls[1].child.emit('exit', null, 'SIGINT');
  assert.equal(await pending, 130);
});
