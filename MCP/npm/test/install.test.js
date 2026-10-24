// SPDX-License-Identifier: MIT
'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');

const { INSTALL_PS1_URL, INSTALL_SH_URL, installUv, installerCommand } = require('../lib/install');
const { memoryFs, scriptedSpawn } = require('../test-support/helpers');

const CURL = '/usr/bin/curl';
const WGET = '/usr/bin/wget';

test('macOS/Linux installer uses curl when curl is on PATH', () => {
  const installer = installerCommand({
    platform: 'linux',
    env: { PATH: '/usr/bin' },
    fs: memoryFs([CURL]),
  });
  assert.deepEqual(installer, {
    command: 'sh',
    args: ['-c', `curl -LsSf ${INSTALL_SH_URL} | sh`],
    url: INSTALL_SH_URL,
  });
});

test('macOS/Linux installer falls back to wget when curl is missing', () => {
  const installer = installerCommand({
    platform: 'darwin',
    env: { PATH: '/usr/bin' },
    fs: memoryFs([WGET]),
  });
  assert.deepEqual(installer, {
    command: 'sh',
    args: ['-c', `wget -qO- ${INSTALL_SH_URL} | sh`],
    url: INSTALL_SH_URL,
  });
});

test('no download tool means no automatic installer', () => {
  assert.equal(installerCommand({ platform: 'linux', env: { PATH: '/usr/bin' }, fs: memoryFs([]) }), null);
});

test('Windows installer runs the official PowerShell script non-interactively', () => {
  const installer = installerCommand({ platform: 'win32', env: { PATH: '' }, fs: memoryFs([]) });
  assert.deepEqual(installer, {
    command: 'powershell',
    args: ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command', `irm ${INSTALL_PS1_URL} | iex`],
    url: INSTALL_PS1_URL,
  });
});

test('installUv keeps installer output off stdout and sets UV_NO_MODIFY_PATH', async () => {
  const spawn = scriptedSpawn([(call) => call.child.emit('exit', 0, null)]);
  const env = { PATH: '/usr/bin', HOME: '/home/a' };
  const code = await installUv({
    spawn,
    installer: { command: 'sh', args: ['-c', 'true'], url: INSTALL_SH_URL },
    env,
  });

  assert.equal(code, 0);
  assert.equal(spawn.calls.length, 1);
  assert.equal(spawn.calls[0].command, 'sh');
  assert.deepEqual(spawn.calls[0].args, ['-c', 'true']);
  assert.deepEqual(spawn.calls[0].options.stdio, ['ignore', 2, 2]);
  assert.equal(spawn.calls[0].options.env.UV_NO_MODIFY_PATH, '1');
  assert.equal(spawn.calls[0].options.env.HOME, '/home/a');
});

test('installUv reports a non-zero installer exit code', async () => {
  const spawn = scriptedSpawn([(call) => call.child.emit('exit', 3, null)]);
  const code = await installUv({
    spawn,
    installer: { command: 'sh', args: ['-c', 'false'], url: INSTALL_SH_URL },
    env: {},
  });
  assert.equal(code, 3);
});
