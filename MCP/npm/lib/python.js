// SPDX-License-Identifier: MIT
'use strict';

// "System Python" mode: the npm package ships the Python server source in
// python/, so a machine that already has a Python 3.12+ with `mcp`, `pydantic`
// and `httpx` does not need uv, uvx, a wheel download or any network access.
//
// Probes are short-lived child processes whose stdio is ignored: stdout must
// stay reserved for MCP frames. Every function takes injected spawn/fs so the
// tests never start a real interpreter.

const path = require('node:path');

const PYTHON_PROBE_CODE = 'import sys, mcp, pydantic, httpx; sys.exit(0 if sys.version_info >= (3, 12) else 3)';
const PROBE_TIMEOUT_MS = 15000;
const PACKAGE_NAME = 'citeproof-mcp';

const NO_PYTHON_HINT =
  'No Python 3.12+ with the mcp, pydantic and httpx packages was found either. ' +
  'Install uv, or set CITEPROOF_MCP_PYTHON to such a Python.';

/**
 * Interpreters to try, in order. `py -3` is the Windows launcher; it is only
 * reachable on Windows, where `python3` often does not exist.
 */
function pythonCandidates(platform) {
  const candidates = [
    { command: 'python3', args: [] },
    { command: 'python', args: [] },
  ];
  if (platform === 'win32') candidates.push({ command: 'py', args: ['-3'] });
  return candidates;
}

/** Pull `--python <path>` / `--python=<path>` out of the pass-through arguments. */
function parsePythonArgs(argv) {
  const input = Array.isArray(argv) ? argv : [];
  const args = [];
  let python = null;
  for (let index = 0; index < input.length; index += 1) {
    const arg = input[index];
    if (arg === '--python') {
      const value = input[index + 1];
      if (typeof value !== 'string' || value.trim() === '') {
        return { args, python: null, error: '--python needs an interpreter path, for example --python /usr/bin/python3' };
      }
      python = value.trim();
      index += 1;
    } else if (arg.startsWith('--python=')) {
      const value = arg.slice('--python='.length).trim();
      if (!value) {
        return { args, python: null, error: '--python needs an interpreter path, for example --python /usr/bin/python3' };
      }
      python = value;
    } else {
      args.push(arg);
    }
  }
  return { args, python, error: null };
}

/** `--python` wins over CITEPROOF_MCP_PYTHON; blank values are ignored. */
function resolvePythonOverride({ flag, env }) {
  const fromFlag = typeof flag === 'string' ? flag.trim() : '';
  if (fromFlag) return fromFlag;
  const fromEnv = env && typeof env.CITEPROOF_MCP_PYTHON === 'string' ? env.CITEPROOF_MCP_PYTHON.trim() : '';
  return fromEnv || null;
}

/**
 * Env for the Python server: the bundled source first on PYTHONPATH, ahead of
 * anything the user already had there, and unbuffered stdio.
 */
function withPythonEnv(env, pythonDir, platform) {
  const base = env || {};
  const separator = platform === 'win32' ? ';' : ':';
  const existing = typeof base.PYTHONPATH === 'string' ? base.PYTHONPATH.trim() : '';
  const pythonPath = existing ? `${pythonDir}${separator}${existing}` : pythonDir;
  return { ...base, PYTHONPATH: pythonPath, PYTHONUNBUFFERED: '1' };
}

/** Arguments for the server console entry point: `python -m citeproof_mcp`. */
function buildPythonArgs({ pythonArgs, extraArgs }) {
  const interpreterArgs = Array.isArray(pythonArgs) ? pythonArgs : [];
  const passthrough = Array.isArray(extraArgs) ? extraArgs : [];
  // -P: do not put the current directory on sys.path, so a local citeproof_mcp folder cannot shadow the bundled one.
  return [...interpreterArgs, '-P', '-m', 'citeproof_mcp', ...passthrough];
}

/**
 * Run one probe. Resolves true only when the interpreter exits 0 within
 * `timeoutMs`. Any spawn error, non-zero exit or timeout counts as a failure.
 */
function probePython({ command, args = [], spawn, env, timeoutMs = PROBE_TIMEOUT_MS }) {
  return new Promise((resolve) => {
    let child;
    try {
      child = spawn(command, [...args, '-c', PYTHON_PROBE_CODE], {
        stdio: 'ignore',
        env,
        windowsHide: true,
      });
    } catch {
      resolve(false);
      return;
    }
    if (!child || typeof child.on !== 'function') {
      resolve(false);
      return;
    }

    let timer = null;
    let settled = false;
    const finish = (ok) => {
      if (settled) return;
      settled = true;
      if (timer) clearTimeout(timer);
      resolve(ok);
    };

    timer = setTimeout(() => {
      try {
        if (typeof child.kill === 'function') child.kill('SIGKILL');
      } catch {
        // The probe is gone; report the failure either way.
      }
      finish(false);
    }, timeoutMs);
    if (typeof timer.unref === 'function') timer.unref();

    child.on('error', () => finish(false));
    child.on('exit', (code) => finish(code === 0));
    child.on('close', (code) => finish(code === 0));
  });
}

/**
 * Try the candidates in order and return `{ command, args, source: 'probe' }`
 * for the first one that passes, or null when none does.
 */
async function findPython({ platform, spawn, env, timeoutMs = PROBE_TIMEOUT_MS }) {
  for (const candidate of pythonCandidates(platform)) {
    // eslint-disable-next-line no-await-in-loop -- probes are sequential and ordered on purpose.
    const ok = await probePython({
      command: candidate.command,
      args: candidate.args,
      spawn,
      env,
      timeoutMs,
    });
    if (ok) return { command: candidate.command, args: candidate.args, source: 'probe' };
  }
  return null;
}

/** Display form for diagnostics, e.g. `py -3` or `/usr/bin/python3`. */
function describePython(python) {
  const args = python && Array.isArray(python.args) ? python.args : [];
  return [python && python.command, ...args].filter(Boolean).join(' ');
}

/** Absolute path of the bundled server source inside this package. */
function bundledPythonDir() {
  return path.join(__dirname, '..', 'python');
}

module.exports = {
  NO_PYTHON_HINT,
  PACKAGE_NAME,
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
};
