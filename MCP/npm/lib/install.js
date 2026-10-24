// SPDX-License-Identifier: MIT
'use strict';

// Automatic uv installation (opt out with CITEPROOF_NO_AUTO_INSTALL=1).
// We run Astral's official installer non-interactively and keep installer
// output on stderr so stdout stays the MCP channel.

const { findOnPath } = require('./uvx');

const MANUAL_INSTALL_URL = 'https://docs.astral.sh/uv/getting-started/installation/';
const INSTALL_SH_URL = 'https://astral.sh/uv/install.sh';
const INSTALL_PS1_URL = 'https://astral.sh/uv/install.ps1';

// `curl ... | sh` exits 0 when the download fails, because sh reads empty input.
// Download to a file first so a network error fails the install.
function downloadThenRun(download) {
  return `t=$(mktemp) || exit 1; ${download} && sh "$t"; s=$?; rm -f "$t"; exit $s`;
}

function installerCommand({ platform, env, fs }) {
  if (platform === 'win32') {
    return {
      command: 'powershell',
      args: ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command', `irm ${INSTALL_PS1_URL} | iex`],
      url: INSTALL_PS1_URL,
    };
  }

  if (findOnPath({ name: 'curl', env, platform, fs })) {
    return { command: 'sh', args: ['-c', downloadThenRun(`curl -LsSf ${INSTALL_SH_URL} -o "$t"`)], url: INSTALL_SH_URL };
  }
  if (findOnPath({ name: 'wget', env, platform, fs })) {
    return { command: 'sh', args: ['-c', downloadThenRun(`wget -qO "$t" ${INSTALL_SH_URL}`)], url: INSTALL_SH_URL };
  }
  return null;
}

/** Run the installer; resolves with its exit code. Output goes to stderr only. */
function installUv({ spawn, installer, env }) {
  return new Promise((resolve, reject) => {
    let child;
    try {
      child = spawn(installer.command, installer.args, {
        stdio: ['ignore', 2, 2],
        env: { ...env, UV_NO_MODIFY_PATH: '1' },
      });
    } catch (error) {
      reject(error);
      return;
    }
    if (!child || typeof child.on !== 'function') {
      resolve(0);
      return;
    }
    let settled = false;
    const done = (code) => {
      if (settled) return;
      settled = true;
      resolve(typeof code === 'number' ? code : 1);
    };
    child.on('error', (error) => {
      if (settled) return;
      settled = true;
      reject(error);
    });
    child.on('exit', done);
    child.on('close', done);
  });
}

module.exports = {
  INSTALL_PS1_URL,
  INSTALL_SH_URL,
  MANUAL_INSTALL_URL,
  downloadThenRun,
  installUv,
  installerCommand,
};
