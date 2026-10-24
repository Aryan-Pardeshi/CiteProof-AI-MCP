// SPDX-License-Identifier: MIT
'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');

const { candidateNames, defaultUvxPaths, findOnPath, resolveUvx } = require('../lib/uvx');
const { memoryFs } = require('../test-support/helpers');

test('CITEPROOF_UVX wins over PATH and default locations', () => {
  const fs = memoryFs(['/opt/uv/uvx', '/usr/bin/uvx', '/home/a/.local/bin/uvx']);
  const resolved = resolveUvx({
    env: { CITEPROOF_UVX: '/opt/uv/uvx', PATH: '/usr/bin' },
    platform: 'linux',
    fs,
    home: '/home/a',
  });
  assert.deepEqual(resolved, { command: '/opt/uv/uvx', source: 'env' });
});

test('CITEPROOF_UVX that points nowhere fails loudly instead of falling back', () => {
  const fs = memoryFs(['/usr/bin/uvx']);
  assert.throws(
    () => resolveUvx({ env: { CITEPROOF_UVX: '/missing/uvx', PATH: '/usr/bin' }, platform: 'linux', fs, home: '/home/a' }),
    /CITEPROOF_UVX/
  );
});

test('CITEPROOF_UVX may be a bare command name found on PATH', () => {
  const fs = memoryFs(['/usr/bin/uvx']);
  const resolved = resolveUvx({ env: { CITEPROOF_UVX: 'uvx', PATH: '/usr/bin' }, platform: 'linux', fs, home: '/home/a' });
  assert.deepEqual(resolved, { command: '/usr/bin/uvx', source: 'env' });
});

test('PATH is searched in order and beats the default locations', () => {
  const fs = memoryFs(['/first/bin/uvx', '/second/bin/uvx', '/home/a/.local/bin/uvx']);
  const resolved = resolveUvx({
    env: { PATH: '/first/bin:/second/bin' },
    platform: 'linux',
    fs,
    home: '/home/a',
  });
  assert.deepEqual(resolved, { command: '/first/bin/uvx', source: 'path' });
});

test('default locations are used when uvx is not on PATH', () => {
  const fs = memoryFs(['/home/a/.cargo/bin/uvx']);
  const resolved = resolveUvx({ env: {}, platform: 'linux', fs, home: '/home/a' });
  assert.deepEqual(resolved, { command: '/home/a/.cargo/bin/uvx', source: 'default' });
});

test('returns null when uvx is nowhere', () => {
  const fs = memoryFs([]);
  assert.equal(resolveUvx({ env: {}, platform: 'linux', fs, home: '/home/a' }), null);
});

test('Windows: PATHEXT is applied to a bare uvx name', () => {
  const fs = memoryFs(['C:/tools/uvx.EXE']);
  const resolved = resolveUvx({
    env: { PATH: 'C:\\tools', PATHEXT: '.EXE;.CMD' },
    platform: 'win32',
    fs,
    home: 'C:\\Users\\a',
  });
  assert.deepEqual(resolved, { command: 'C:\\tools\\uvx.EXE', source: 'path' });
});

test('Windows: default PATHEXT is used when the env var is missing', () => {
  const fs = memoryFs(['C:/tools/uvx.exe']);
  assert.deepEqual(candidateNames('uvx', 'win32', {}), ['uvx.COM', 'uvx.com', 'uvx.EXE', 'uvx.exe', 'uvx.BAT', 'uvx.bat', 'uvx.CMD', 'uvx.cmd']);
  assert.equal(findOnPath({ name: 'uvx', env: { PATH: 'C:\\tools' }, platform: 'win32', fs }), 'C:\\tools\\uvx.exe');
});

test('Windows: an explicit extension is not expanded', () => {
  assert.deepEqual(candidateNames('uvx.exe', 'win32', {}), ['uvx.exe']);
});

test('Windows: %USERPROFILE%/.local/bin and %LOCALAPPDATA% are checked', () => {
  const paths = defaultUvxPaths({
    env: { LOCALAPPDATA: 'C:\\Users\\a\\AppData\\Local' },
    platform: 'win32',
    home: 'C:\\Users\\a',
  });
  assert.ok(paths.includes('C:\\Users\\a\\.local\\bin\\uvx.exe'));
  assert.ok(paths.includes('C:\\Users\\a\\.cargo\\bin\\uvx.exe'));
  assert.ok(paths.some((item) => item.startsWith('C:\\Users\\a\\AppData\\Local\\')));
});

test('Windows: falls back to %USERPROFILE% when no home is injected', () => {
  const fs = memoryFs(['C:/Users/a/.local/bin/uvx.exe']);
  const resolved = resolveUvx({
    env: { USERPROFILE: 'C:\\Users\\a', PATH: '' },
    platform: 'win32',
    fs,
  });
  assert.deepEqual(resolved, { command: 'C:\\Users\\a\\.local\\bin\\uvx.exe', source: 'default' });
});
