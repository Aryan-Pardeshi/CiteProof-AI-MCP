// SPDX-License-Identifier: MIT
'use strict';

// Keep everything uv writes inside one folder so a sandboxed client (for
// example Claude Science) only has to allow a single readable/writable path:
//   <dir>/cache   uv package cache          (UV_CACHE_DIR)
//   <dir>/tools   uv tool environments      (UV_TOOL_DIR)
//   <dir>/python  uv-managed Python builds  (UV_PYTHON_INSTALL_DIR)
//   <dir>/bin     uv/uvx binaries           (UV_INSTALL_DIR, auto-install only)

const os = require('node:os');
const path = require('node:path');

const SUBDIRS = ['cache', 'tools', 'python', 'bin'];

/** Pull `--uv-home <dir>` / `--uv-home=<dir>` out of the pass-through arguments. */
function parseUvHomeArgs(argv) {
  const input = Array.isArray(argv) ? argv : [];
  const args = [];
  let uvHome = null;
  for (let index = 0; index < input.length; index += 1) {
    const arg = input[index];
    if (arg === '--uv-home') {
      const value = input[index + 1];
      if (typeof value !== 'string' || value.trim() === '') {
        return { args, uvHome: null, error: '--uv-home needs a directory, for example --uv-home ~/.citeproof-uv' };
      }
      uvHome = value.trim();
      index += 1;
    } else if (arg.startsWith('--uv-home=')) {
      const value = arg.slice('--uv-home='.length).trim();
      if (!value) {
        return { args, uvHome: null, error: '--uv-home needs a directory, for example --uv-home ~/.citeproof-uv' };
      }
      uvHome = value;
    } else {
      args.push(arg);
    }
  }
  return { args, uvHome, error: null };
}

/** The flag wins over CITEPROOF_UV_HOME; relative paths resolve against cwd. */
function uvHomeDir({ flag, env, cwd, home }) {
  const fromFlag = typeof flag === 'string' ? flag.trim() : '';
  const fromEnv = env && typeof env.CITEPROOF_UV_HOME === 'string' ? env.CITEPROOF_UV_HOME.trim() : '';
  const raw = fromFlag || fromEnv;
  if (!raw) return null;
  // MCP clients pass args and env without a shell, so `~` is not expanded for us.
  const expanded = /^~(?=$|[\/])/.test(raw) ? path.join(home || os.homedir(), raw.slice(1)) : raw;
  return path.resolve(cwd || process.cwd(), expanded);
}

function createUvHomeDirs({ dir, fs }) {
  for (const subdir of SUBDIRS) {
    fs.mkdirSync(path.join(dir, subdir), { recursive: true });
  }
}

/** Env for uvx (and the Python server it starts). */
function withUvHomeEnv(env, dir) {
  return {
    ...env,
    UV_CACHE_DIR: path.join(dir, 'cache'),
    UV_TOOL_DIR: path.join(dir, 'tools'),
    UV_PYTHON_INSTALL_DIR: path.join(dir, 'python'),
  };
}

/** Extra env for the auto-installer, which also chooses where uvx lands. */
function withInstallerEnv(env, dir) {
  return { ...env, UV_INSTALL_DIR: path.join(dir, 'bin') };
}

module.exports = {
  SUBDIRS,
  createUvHomeDirs,
  parseUvHomeArgs,
  uvHomeDir,
  withInstallerEnv,
  withUvHomeEnv,
};
