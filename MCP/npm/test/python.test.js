// SPDX-License-Identifier: MIT
'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');

const {
  NO_PYTHON_HINT,
  PROBE_TIMEOUT_MS,
  PYTHON_PROBE_CODE,
  buildPythonArgs,
  bundledPythonDir,
  describePython,
  findPython,
  parsePythonArgs,
  probePython,
  pythonCandidates,
  resolvePythonOverride,
  withPythonEnv,
} = require('../lib/python');
const { scriptedSpawn } = require('../test-support/helpers');

const PROBE_PREFIX = ['-c', PYTHON_PROBE_CODE];

test('interpreter candidates are python3 then python, plus py -3 on Windows', () => {
  assert.deepEqual(pythonCandidates('linux'), [
    { command: 'python3', args: [] },
    { command: 'python', args: [] },
  ]);
  assert.deepEqual(pythonCandidates('darwin'), [
    { command: 'python3', args: [] },
    { command: 'python', args: [] },
  ]);
  assert.deepEqual(pythonCandidates('win32'), [
    { command: 'python3', args: [] },
    { command: 'python', args: [] },
    { command: 'py', args: ['-3'] },
  ]);
});

test('the probe checks Python 3.12 and the three imports, with a 15 s timeout', () => {
  assert.equal(PROBE_TIMEOUT_MS, 15000);
  assert.match(PYTHON_PROBE_CODE, /import sys, mcp, pydantic, httpx/);
  assert.match(PYTHON_PROBE_CODE, /sys\.version_info >= \(3, 12\)/);
});

test('probePython runs <python> -c <code> with stdio ignored and passes only exit 0', async () => {
  const spawn = scriptedSpawn([(call) => call.child.emit('exit', 0, null)]);
  const ok = await probePython({ command: 'python3', spawn, env: { PATH: '/usr/bin' } });

  assert.equal(ok, true);
  assert.equal(spawn.calls.length, 1);
  const [call] = spawn.calls;
  assert.equal(call.command, 'python3');
  assert.deepEqual(call.args, PROBE_PREFIX);
  assert.equal(call.options.stdio, 'ignore');
  assert.deepEqual(call.options.env, { PATH: '/usr/bin' });
});

test('probePython keeps interpreter flags, so py -3 is probed as py -3 -c ...', async () => {
  const spawn = scriptedSpawn([(call) => call.child.emit('exit', 0, null)]);
  await probePython({ command: 'py', args: ['-3'], spawn, env: {} });

  assert.deepEqual(spawn.calls[0].args, ['-3', ...PROBE_PREFIX]);
});

test('a non-zero probe exit means the interpreter is not usable', async () => {
  const spawn = scriptedSpawn([(call) => call.child.emit('exit', 3, null)]);
  assert.equal(await probePython({ command: 'python3', spawn, env: {} }), false);
});

test('a probe that cannot start counts as a failure', async () => {
  const spawn = scriptedSpawn([(call) => call.child.emit('error', new Error('spawn python3 ENOENT'))]);
  assert.equal(await probePython({ command: 'python3', spawn, env: {} }), false);
});

test('a probe that hangs is killed after the timeout', async () => {
  const spawn = scriptedSpawn([() => {}]);
  const started = Date.now();
  const ok = await probePython({ command: 'python3', spawn, env: {}, timeoutMs: 20 });

  assert.equal(ok, false);
  assert.equal(spawn.calls[0].child.killedWith, 'SIGKILL');
  assert.ok(Date.now() - started < 2000, 'the probe must not wait for the default 15 s');
});

test('findPython stops at the first working candidate', async () => {
  const spawn = scriptedSpawn([
    (call) => call.child.emit('exit', 1, null),
    (call) => call.child.emit('exit', 0, null),
  ]);
  const found = await findPython({ platform: 'linux', spawn, env: {} });

  assert.deepEqual(found, { command: 'python', args: [], source: 'probe' });
  assert.equal(spawn.calls.length, 2);
});

test('findPython returns null when no candidate works', async () => {
  const spawn = scriptedSpawn([(call) => call.child.emit('exit', 1, null)]);
  assert.equal(await findPython({ platform: 'linux', spawn, env: {}, timeoutMs: 20 }), null);
  assert.equal(spawn.calls.length, 2);
});

test('parsePythonArgs pulls --python out of the pass-through arguments', () => {
  assert.deepEqual(parsePythonArgs(['--python', '/usr/bin/python3', '--verbose']), {
    args: ['--verbose'],
    python: '/usr/bin/python3',
    error: null,
  });
  assert.deepEqual(parsePythonArgs(['--verbose', '--python=/opt/py']), {
    args: ['--verbose'],
    python: '/opt/py',
    error: null,
  });
  assert.deepEqual(parsePythonArgs(['--verbose']), { args: ['--verbose'], python: null, error: null });
});

test('parsePythonArgs rejects a missing or empty value', () => {
  assert.match(parsePythonArgs(['--python']).error, /--python needs an interpreter path/);
  assert.match(parsePythonArgs(['--python', '   ']).error, /--python needs an interpreter path/);
  assert.match(parsePythonArgs(['--python=']).error, /--python needs an interpreter path/);
});

test('resolvePythonOverride prefers the flag and trims both sources', () => {
  assert.equal(resolvePythonOverride({ flag: ' /flag/py ', env: { CITEPROOF_MCP_PYTHON: '/env/py' } }), '/flag/py');
  assert.equal(resolvePythonOverride({ flag: null, env: { CITEPROOF_MCP_PYTHON: ' /env/py ' } }), '/env/py');
  assert.equal(resolvePythonOverride({ flag: '', env: { CITEPROOF_MCP_PYTHON: '   ' } }), null);
  assert.equal(resolvePythonOverride({ flag: null, env: {} }), null);
});

test('withPythonEnv prepends the bundled dir with ";" on Windows', () => {
  const env = withPythonEnv({ PATH: 'C:\\bin', PYTHONPATH: 'C:\\old\\py' }, 'C:\\pkg\\python', 'win32');
  assert.equal(env.PYTHONPATH, 'C:\\pkg\\python;C:\\old\\py');
  assert.equal(env.PYTHONUNBUFFERED, '1');
  assert.equal(env.PATH, 'C:\\bin');
});

test('withPythonEnv prepends the bundled dir with ":" elsewhere', () => {
  const env = withPythonEnv({ PYTHONPATH: '/old/py' }, '/pkg/python', 'linux');
  assert.equal(env.PYTHONPATH, '/pkg/python:/old/py');
  assert.equal(env.PYTHONUNBUFFERED, '1');
});

test('withPythonEnv writes PYTHONPATH alone when none existed', () => {
  assert.equal(withPythonEnv({}, '/pkg/python', 'darwin').PYTHONPATH, '/pkg/python');
  assert.equal(withPythonEnv({ PYTHONPATH: '  ' }, '/pkg/python', 'darwin').PYTHONPATH, '/pkg/python');
});

test('buildPythonArgs runs the module and forwards server arguments', () => {
  assert.deepEqual(buildPythonArgs({ pythonArgs: [], extraArgs: ['--http'] }), ['-m', 'citeproof_mcp', '--http']);
  assert.deepEqual(buildPythonArgs({ pythonArgs: ['-3'], extraArgs: [] }), ['-3', '-m', 'citeproof_mcp']);
  assert.deepEqual(buildPythonArgs({}), ['-m', 'citeproof_mcp']);
});

test('bundledPythonDir points at the package python/ folder', () => {
  assert.equal(bundledPythonDir(), path.resolve(__dirname, '..', 'python'));
});

test('describePython shows the interpreter and its flags', () => {
  assert.equal(describePython({ command: 'py', args: ['-3'] }), 'py -3');
  assert.equal(describePython({ command: '/usr/bin/python3', args: [] }), '/usr/bin/python3');
});

test('the failure hint names the packages and CITEPROOF_MCP_PYTHON', () => {
  assert.match(NO_PYTHON_HINT, /No Python 3\.12\+ with the mcp, pydantic and httpx packages was found either/);
  assert.match(NO_PYTHON_HINT, /CITEPROOF_MCP_PYTHON/);
});
