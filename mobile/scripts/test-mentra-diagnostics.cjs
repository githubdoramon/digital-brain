const assert = require('node:assert/strict');
const fs = require('node:fs');
const Module = require('node:module');
const path = require('node:path');
const ts = require('typescript');

const files = new Map();
const preferences = new Map();
let markStarted;
let releaseMarker;
const markerStarted = new Promise((resolve) => { markStarted = resolve; });
const markerGate = new Promise((resolve) => { releaseMarker = resolve; });
const fileSystem = {
  documentDirectory: 'file:///test/',
  cacheDirectory: 'file:///cache/',
  EncodingType: { UTF8: 'utf8' },
  async readAsStringAsync(uri) {
    if (!files.has(uri)) throw new Error('not found');
    return files.get(uri);
  },
  async writeAsStringAsync(uri, contents, options = {}) {
    if (contents.includes('"mentra_diagnostics_cleared"')) {
      markStarted();
      await markerGate;
    }
    files.set(uri, options.append ? `${files.get(uri) ?? ''}${contents}` : contents);
  },
  async getInfoAsync(uri) {
    return files.has(uri) ? { exists: true, size: files.get(uri).length } : { exists: false };
  },
};
const asyncStorage = {
  async getItem(key) { return preferences.get(key) ?? null; },
  async setItem(key, value) { preferences.set(key, value); },
};

const originalLoad = Module._load;
const originalTs = Module._extensions['.ts'];
Module._load = function patchedLoad(request, parent, isMain) {
  if (request === 'expo-file-system/legacy') return fileSystem;
  if (request === '@react-native-async-storage/async-storage') return asyncStorage;
  return originalLoad.call(this, request, parent, isMain);
};
Module._extensions['.ts'] = (module, filename) => {
  const compiled = ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2022,
      esModuleInterop: true,
    },
    fileName: filename,
  }).outputText;
  module._compile(compiled, filename);
};

async function main() {
  const debug = require(path.resolve(__dirname, '../mentraCapture/debug.ts'));
  const clearing = debug.clearMentraDebugLog();
  await markerStarted;
  const appending = debug.appendMentraDebugLog('wake_debug_snapshot', { listener_active: true });
  releaseMarker();
  await Promise.all([clearing, appending]);
  const lines = (await debug.readMentraDebugLog()).trim().split('\n').map(JSON.parse);
  assert.deepEqual(lines.map((line) => line.event), [
    'mentra_diagnostics_cleared',
    'wake_debug_snapshot',
  ]);
  assert.equal(lines[1].payload.listener_active, true);
  assert.ok((await debug.getMentraDebugLogInfo()).sizeBytes > 0);
  console.log('PASS: clear marker and first wake snapshot survive concurrent writes');
}

main().finally(() => {
  Module._load = originalLoad;
  Module._extensions['.ts'] = originalTs;
}).catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
