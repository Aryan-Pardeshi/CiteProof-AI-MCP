// SPDX-License-Identifier: MIT
'use strict';

// Locate the `uvx` executable without shelling out to `where`/`which`.
// Resolution order (see README): CITEPROOF_UVX, PATH, then the default uv
// install locations. Every function takes an injected `fs` so tests never
// touch the real filesystem.

const path = require('node:path');
const os = require('node:os');

const DEFAULT_PATHEXT = '.COM;.EXE;.BAT;.CMD';

function isFile(fs, candidate) {
  try {
    const stat = fs.statSync(candidate);
    if (stat && typeof stat.isFile === 'function') return stat.isFile();
    return true;
  } catch {
    try {
      return typeof fs.existsSync === 'function' && Boolean(fs.existsSync(candidate));
    } catch {
      return false;
    }
  }
}

function pathSeparator(platform) {
  return platform === 'win32' ? ';' : ':';
}

function extensions(platform, env) {
  if (platform !== 'win32') return [''];
  const raw = env.PATHEXT || DEFAULT_PATHEXT;
  const seen = new Set();
  const result = [];
  for (const part of raw.split(';')) {
    const ext = part.trim();
    if (!ext) continue;
    for (const variant of [ext, ext.toLowerCase()]) {
      if (seen.has(variant)) continue;
      seen.add(variant);
      result.push(variant);
    }
  }
  if (result.length === 0) result.push('.EXE', '.exe');
  return result;
}

function candidateNames(name, platform, env) {
  if (platform !== 'win32') return [name];
  // An explicit extension means the caller named the exact file.
  if (path.win32.extname(name)) return [name];
  return extensions(platform, env).map((ext) => name + ext);
}

function findOnPath({ name, env, platform, fs }) {
  const raw = env.PATH || env.Path || '';
  const dirs = String(raw).split(pathSeparator(platform));
  const names = candidateNames(name, platform, env);
  for (const dir of dirs) {
    if (!dir) continue;
    for (const candidate of names) {
      const full = platform === 'win32' ? path.win32.join(dir, candidate) : path.posix.join(dir, candidate);
      if (isFile(fs, full)) return full;
    }
  }
  return null;
}

function homeDirectory({ env, home }) {
  return home || env.HOME || env.USERPROFILE || os.homedir();
}

function defaultUvxPaths({ env, platform, home }) {
  const base = homeDirectory({ env, home });
  if (platform === 'win32') {
    const join = path.win32.join;
    const paths = [
      join(base, '.local', 'bin', 'uvx.exe'),
      join(base, '.local', 'bin', 'uvx'),
      join(base, '.cargo', 'bin', 'uvx.exe'),
      join(base, '.cargo', 'bin', 'uvx'),
    ];
    const localAppData = env.LOCALAPPDATA;
    if (localAppData) {
      paths.push(join(localAppData, 'uv', 'bin', 'uvx.exe'));
      paths.push(join(localAppData, 'Programs', 'uv', 'uvx.exe'));
      paths.push(join(localAppData, 'Microsoft', 'WindowsApps', 'uvx.exe'));
    }
    return paths;
  }
  return [path.posix.join(base, '.local', 'bin', 'uvx'), path.posix.join(base, '.cargo', 'bin', 'uvx')];
}

/**
 * Return `{ command, source }` for uvx, or null when it cannot be found.
 * `source` is 'env' | 'path' | 'default'.
 * Throws when CITEPROOF_UVX is set but does not point at an executable.
 */
function resolveUvx({ env, platform, fs, home }) {
  const override = env.CITEPROOF_UVX;
  if (override && override.trim()) {
    const value = override.trim();
    const found = path.isAbsolute(value) ? (isFile(fs, value) ? value : null) : findOnPath({ name: value, env, platform, fs });
    if (!found) {
      throw new Error(
        `CITEPROOF_UVX is set to "${value}" but no uvx executable was found there. ` +
          'Fix the path or unset CITEPROOF_UVX to use the normal lookup.'
      );
    }
    return { command: found, source: 'env' };
  }

  const onPath = findOnPath({ name: 'uvx', env, platform, fs });
  if (onPath) return { command: onPath, source: 'path' };

  for (const candidate of defaultUvxPaths({ env, platform, home })) {
    if (isFile(fs, candidate)) return { command: candidate, source: 'default' };
  }

  return null;
}

module.exports = {
  DEFAULT_PATHEXT,
  candidateNames,
  defaultUvxPaths,
  findOnPath,
  isFile,
  resolveUvx,
};
