// SPDX-License-Identifier: MIT
'use strict';

// Launcher logic. Everything is injected (env, fs, spawn, streams, process) so
// the tests can run offline and assert that nothing is ever written to stdout
// while an MCP session is running.

const fsDefault = require('node:fs');
const os = require('node:os');
const { spawn: spawnDefault } = require('node:child_process');

const pkg = require('../package.json');
const { installUv, installerCommand, MANUAL_INSTALL_URL } = require('./install');
const {
  NO_PYTHON_HINT,
  buildPythonArgs,
  bundledPythonDir,
  describePython,
  findPython,
  parsePythonArgs,
  resolvePythonOverride,
  withPythonEnv,
} = require('./python');
const { buildUvxArgs } = require('./source');
const { createUvHomeDirs, parseUvHomeArgs, uvHomeDir, withInstallerEnv, withUvHomeEnv } = require('./uv-home');
const { resolveUvx } = require('./uvx');

const NAME = 'citeproof-mcp';

const SIGNAL_EXIT_CODES = {
  SIGHUP: 129,
  SIGINT: 130,
  SIGQUIT: 131,
  SIGTERM: 143,
};

function isEnabled(value) {
  if (value === undefined || value === null) return false;
  const text = String(value).trim().toLowerCase();
  return text !== '' && text !== '0' && text !== 'false' && text !== 'no';
}

function write(stderr, message) {
  stderr.write(`${NAME}: ${message}\n`);
}

function parseArgs(argv) {
  const args = Array.isArray(argv) ? argv : [];
  if (args.length === 1 && (args[0] === '--version' || args[0] === '-v')) return { mode: 'version' };
  if (args.length === 1 && (args[0] === '--help' || args[0] === '-h')) return { mode: 'help' };
  const python = parsePythonArgs(args);
  if (python.error) return { mode: 'error', error: python.error };
  const uvHome = parseUvHomeArgs(python.args);
  if (uvHome.error) return { mode: 'error', error: uvHome.error };
  return { mode: 'run', args: uvHome.args, uvHome: uvHome.uvHome, python: python.python };
}

function helpText(version) {
  return [
    `${NAME} ${version}`,
    '',
    'Run the CiteProof MCP server over stdio. The package ships the Python',
    'server. The launcher uses CITEPROOF_MCP_PYTHON, then uv (uvx), then a',
    'Python 3.12+ already on PATH, and only then installs uv automatically.',
    '',
    'Usage:',
    `  npx -y ${NAME} [server arguments...]`,
    '',
    'Options:',
    '  -h, --help     Show this help and exit.',
    '  -v, --version  Show the launcher version and exit.',
    '  --python <path>  Run the bundled server with this Python interpreter',
    '                   (same as CITEPROOF_MCP_PYTHON; needs mcp, pydantic, httpx).',
    '  --uv-home <dir>  Keep every uv file (cache, tools, Python, binaries) in <dir>.',
    '',
    'Environment:',
    '  CITEPROOF_MCP_PYTHON       Python 3.12+ with mcp, pydantic and httpx; skips uv',
    '  CITEPROOF_MCP_SOURCE       uvx --from value (default: this repo at the matching version tag)',
    '  CITEPROOF_UVX              full path to the uvx executable',
    '  CITEPROOF_UV_HOME          single folder for uv cache, tools, Python and binaries',
    '  CITEPROOF_NO_AUTO_INSTALL  set to 1 to never install uv automatically',
    '  CITEPROOF_MCP_HOME         data directory used by the server (default ~/.citeproof-mcp)',
    '',
    'The server records and checks what your research agent supplies. It does not',
    'search the literature or read papers.',
    '',
  ].join('\n');
}

function signalExitCode(signal) {
  return SIGNAL_EXIT_CODES[signal] || 1;
}

async function ensureUvx({ env, platform, fs, home, stderr, spawn, uvHome, probe }) {
  let resolved;
  try {
    resolved = resolveUvx({ env, platform, fs, home, uvHome });
  } catch (error) {
    write(stderr, error.message);
    return { code: 1 };
  }
  if (resolved) {
    write(stderr, `uvx mode: using ${resolved.command}.`);
    return { uvx: resolved };
  }

  // No uv: a Python 3.12+ with mcp, pydantic and httpx can run the bundled
  // source directly, with no download and no network.
  if (probe) {
    const python = await probe();
    if (python) return { python };
  }

  if (isEnabled(env.CITEPROOF_NO_AUTO_INSTALL)) {
    write(stderr, 'uv (uvx) was not found and automatic installation is disabled by CITEPROOF_NO_AUTO_INSTALL=1.');
    write(stderr, NO_PYTHON_HINT);
    write(stderr, `Install uv manually: ${MANUAL_INSTALL_URL}`);
    write(stderr, 'Then run this command again, or set CITEPROOF_UVX to the full path of uvx.');
    return { code: 1 };
  }

  const installer = installerCommand({ platform, env, fs });
  if (!installer) {
    write(stderr, 'uv (uvx) was not found, and neither curl nor wget is available to install it.');
    write(stderr, NO_PYTHON_HINT);
    write(stderr, `Install uv manually: ${MANUAL_INSTALL_URL}`);
    return { code: 1 };
  }

  write(stderr, 'uv (uvx) was not found.');
  write(stderr, `Installing uv from ${installer.url} (set CITEPROOF_NO_AUTO_INSTALL=1 to skip this).`);

  let installCode;
  try {
    installCode = await installUv({ spawn, installer, env: uvHome ? withInstallerEnv(env, uvHome) : env });
  } catch (error) {
    write(stderr, `Could not start the uv installer: ${error && error.message ? error.message : String(error)}`);
    write(stderr, NO_PYTHON_HINT);
    write(stderr, `Install uv manually: ${MANUAL_INSTALL_URL}`);
    return { code: 1 };
  }
  if (installCode !== 0) {
    write(stderr, `The uv installer exited with code ${installCode}.`);
    write(stderr, NO_PYTHON_HINT);
    write(stderr, `Install uv manually: ${MANUAL_INSTALL_URL}`);
    return { code: 1 };
  }

  try {
    resolved = resolveUvx({ env, platform, fs, home, uvHome });
  } catch (error) {
    write(stderr, error.message);
    return { code: 1 };
  }
  if (!resolved) {
    write(stderr, 'uv was installed, but uvx was not found in the usual locations.');
    write(stderr, 'Open a new terminal, or set CITEPROOF_UVX to the full path of uvx.');
    if (uvHome) write(stderr, `Looked in ${uvHome} as well.`);
    write(stderr, NO_PYTHON_HINT);
    write(stderr, `Manual install: ${MANUAL_INSTALL_URL}`);
    return { code: 1 };
  }

  write(stderr, `uv installed. Using ${resolved.command}.`);
  return { uvx: resolved };
}

function runChild({ command, args, env, spawn, stderr, processRef, label }) {
  return new Promise((resolve) => {
    let child;
    try {
      child = spawn(command, args, { stdio: 'inherit', env });
    } catch (error) {
      write(stderr, `Failed to start ${label}: ${error && error.message ? error.message : String(error)}`);
      resolve(1);
      return;
    }
    if (!child || typeof child.on !== 'function') {
      write(stderr, `Failed to start ${label}: the spawn call returned no process.`);
      resolve(1);
      return;
    }

    let settled = false;
    const forward = (signal) => {
      try {
        child.kill(signal);
      } catch {
        // The child is already gone; the exit handler reports the code.
      }
    };
    const onSigint = () => forward('SIGINT');
    const onSigterm = () => forward('SIGTERM');
    const finish = (code, signal) => {
      if (settled) return;
      settled = true;
      processRef.removeListener('SIGINT', onSigint);
      processRef.removeListener('SIGTERM', onSigterm);
      if (typeof code === 'number') resolve(code);
      else if (signal) resolve(signalExitCode(signal));
      else resolve(1);
    };

    processRef.on('SIGINT', onSigint);
    processRef.on('SIGTERM', onSigterm);
    child.on('error', (error) => {
      write(stderr, `Failed to start ${label}: ${error && error.message ? error.message : String(error)}`);
      finish(1);
    });
    child.on('exit', finish);
  });
}

function runServer({ uvx, args, env, spawn, stderr, processRef }) {
  return runChild({
    command: uvx.command,
    args,
    env,
    spawn,
    stderr,
    processRef,
    label: `uvx (${uvx.command})`,
  });
}

/**
 * Run the server from the bundled `python/` source with a system interpreter.
 * The working directory is left untouched; only PYTHONPATH changes.
 */
function runPythonServer({ python, args, env, platform, spawn, stderr, processRef }) {
  const pythonDir = bundledPythonDir();
  write(stderr, `python mode: using ${describePython(python)} with the bundled server (no uv needed).`);
  return runChild({
    command: python.command,
    args: buildPythonArgs({ pythonArgs: python.args, extraArgs: args }),
    env: withPythonEnv(env, pythonDir, platform),
    spawn,
    stderr,
    processRef,
    label: `python (${describePython(python)})`,
  });
}

/**
 * Run the launcher. Resolves with the process exit code.
 * Options are optional; the bin file passes only `argv`.
 */
async function runCli(options = {}) {
  const argv = options.argv || [];
  let env = options.env || process.env;
  const platform = options.platform || process.platform;
  const stdout = options.stdout || process.stdout;
  const stderr = options.stderr || process.stderr;
  const spawn = options.spawn || spawnDefault;
  const fs = options.fs || fsDefault;
  const home = options.home || os.homedir();
  const processRef = options.process || process;
  const version = options.version || pkg.version;
  const probeTimeoutMs = options.probeTimeoutMs;

  const parsed = parseArgs(argv);
  if (parsed.mode === 'version') {
    stdout.write(`${version}\n`);
    return 0;
  }
  if (parsed.mode === 'help') {
    stdout.write(helpText(version));
    return 0;
  }
  if (parsed.mode === 'error') {
    write(stderr, parsed.error);
    return 1;
  }

  // 1. An explicit interpreter wins over everything else.
  const pythonOverride = resolvePythonOverride({ flag: parsed.python, env });
  if (pythonOverride) {
    return runPythonServer({
      python: { command: pythonOverride, args: [], source: 'override' },
      args: parsed.args,
      env,
      platform,
      spawn,
      stderr,
      processRef,
    });
  }

  const uvHome = uvHomeDir({ flag: parsed.uvHome, env, cwd: options.cwd });
  if (uvHome) {
    try {
      createUvHomeDirs({ dir: uvHome, fs });
    } catch (error) {
      write(
        stderr,
        `Could not create the uv home folder ${uvHome}: ${error && error.message ? error.message : String(error)}`
      );
      return 1;
    }
    env = withUvHomeEnv(env, uvHome);
  }

  // 2. uvx, 3. a Python 3.12+ on PATH, 4. install uv.
  const ensured = await ensureUvx({
    env,
    platform,
    fs,
    home,
    stderr,
    spawn,
    uvHome,
    probe: () => findPython({ platform, spawn, env, timeoutMs: probeTimeoutMs }),
  });
  if (ensured.code !== undefined) return ensured.code;

  if (ensured.python) {
    return runPythonServer({
      python: ensured.python,
      args: parsed.args,
      env,
      platform,
      spawn,
      stderr,
      processRef,
    });
  }

  const args = buildUvxArgs({ version, env, extraArgs: parsed.args });
  return runServer({ uvx: ensured.uvx, args, env, spawn, stderr, processRef });
}

module.exports = {
  ensureUvx,
  helpText,
  isEnabled,
  parseArgs,
  runCli,
  runServer,
  signalExitCode,
};
