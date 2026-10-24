// SPDX-License-Identifier: MIT
'use strict';

// Shared fakes for the launcher tests: no real filesystem, process or spawn.

const { EventEmitter } = require('node:events');

function normalizePath(value) {
  return String(value).replace(/\\/g, '/');
}

function memoryFs(files = []) {
  const set = new Set(files.map(normalizePath));
  const dirs = [];
  return {
    dirs,
    add(file) {
      set.add(normalizePath(file));
    },
    has(file) {
      return set.has(normalizePath(file));
    },
    mkdirSync(dir) {
      dirs.push(normalizePath(dir));
      set.add(normalizePath(dir));
    },
    statSync(file) {
      if (!set.has(normalizePath(file))) {
        const error = new Error(`ENOENT: no such file or directory, stat '${file}'`);
        error.code = 'ENOENT';
        throw error;
      }
      return { isFile: () => true };
    },
    existsSync(file) {
      return set.has(normalizePath(file));
    },
  };
}

function captureStream() {
  let buffer = '';
  return {
    write(chunk) {
      buffer += String(chunk);
      return true;
    },
    get text() {
      return buffer;
    },
  };
}

function fakeProcess() {
  const listeners = new Map();
  return {
    on(event, listener) {
      if (!listeners.has(event)) listeners.set(event, new Set());
      listeners.get(event).add(listener);
      return this;
    },
    removeListener(event, listener) {
      const set = listeners.get(event);
      if (set) set.delete(listener);
      return this;
    },
    listenerCount(event) {
      const set = listeners.get(event);
      return set ? set.size : 0;
    },
    emit(event, ...args) {
      const set = listeners.get(event);
      if (!set) return false;
      for (const listener of [...set]) listener(...args);
      return true;
    },
  };
}

/**
 * Spawn fake. `handlers[i]` runs on a later tick for the i-th spawn call; the
 * last handler is reused for any extra calls. Each handler receives
 * `{ command, args, options, child }`.
 */
function scriptedSpawn(handlers) {
  const calls = [];
  const spawn = (command, args, options) => {
    const child = new EventEmitter();
    child.killedWith = null;
    child.kill = (signal) => {
      child.killedWith = signal;
      return true;
    };
    const call = { command, args, options, child };
    calls.push(call);
    const handler = handlers[Math.min(calls.length - 1, handlers.length - 1)];
    if (handler) setImmediate(() => handler(call));
    return child;
  };
  spawn.calls = calls;
  return spawn;
}

module.exports = { captureStream, fakeProcess, memoryFs, normalizePath, scriptedSpawn };
