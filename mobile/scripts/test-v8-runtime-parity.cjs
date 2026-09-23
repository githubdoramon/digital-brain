/* Optional cross-repository parity check against a frozen v8 lab replay. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const Module = require('node:module');
const path = require('node:path');
const ts = require('typescript');

const mobileRoot = path.resolve(__dirname, '..');
const labRoot = path.resolve(process.env.WAKE_WORD_LAB_ROOT || path.join(mobileRoot, '..', '..', 'mentra-ramon'));
const originalExtension = Module._extensions['.ts'];
Module._extensions['.ts'] = (module, filename) => {
  const source = fs.readFileSync(filename, 'utf8');
  module._compile(ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
    fileName: filename,
  }).outputText, filename);
};

function readWav(filename) {
  const bytes = fs.readFileSync(filename);
  assert.equal(bytes.toString('ascii', 0, 4), 'RIFF');
  assert.equal(bytes.toString('ascii', 8, 12), 'WAVE');
  let format;
  let audio;
  for (let offset = 12; offset + 8 <= bytes.length;) {
    const kind = bytes.toString('ascii', offset, offset + 4);
    const size = bytes.readUInt32LE(offset + 4);
    const data = offset + 8;
    if (kind === 'fmt ') format = [bytes.readUInt16LE(data), bytes.readUInt16LE(data + 2),
      bytes.readUInt32LE(data + 4), bytes.readUInt16LE(data + 14)];
    if (kind === 'data') audio = bytes.subarray(data, data + size);
    offset = data + size + (size % 2);
  }
  assert.deepEqual(format, [1, 1, 16000, 16]);
  assert(audio);
  return Int16Array.from({ length: audio.length / 2 }, (_, index) => audio.readInt16LE(index * 2));
}

async function main() {
  const ort = require(path.join(labRoot, 'node_modules', 'onnxruntime-node'));
  const { OpenWakeWordOnnxBackend } = require(path.join(mobileRoot, 'wakeWord', 'OpenWakeWordOnnxBackend.ts'));
  const { V8TwoStageWakeWordDetector } = require(path.join(mobileRoot, 'wakeWord', 'V8TwoStageWakeWordDetector.ts'));
  const manifest = JSON.parse(fs.readFileSync(path.join(labRoot, 'recordings', 'v8-sherpa-corrected-labels-manifest.json')));
  const report = JSON.parse(fs.readFileSync(path.join(labRoot, 'artifacts', 'candidates', 'v8-two-stage-on-demand-report.json')));
  const expected = report.calibration.replays.find((row) =>
    row.kind === 'target' && row.variant === 'context+0' && row.path.endsWith('wake-noisy-001.wav'));
  assert(expected && expected.candidates.length === 1);
  const target = manifest.files.find((row) => row.path === expected.path);
  const ambient = manifest.files.filter((row) =>
    row.split === target.split && row.kind === 'ambient' && row.group !== target.group && row.seconds > 4.1)
    .sort((left, right) => left.group.localeCompare(right.group))[0];
  assert(ambient);
  const ambientPcm = readWav(ambient.path);
  const start = parseInt(target.group.slice(0, 8), 16) % (ambientPcm.length - 4 * 16000 - 1280);
  const pcm = new Int16Array(4 * 16000 + readWav(target.path).length);
  pcm.set(ambientPcm.subarray(start, start + 4 * 16000));
  pcm.set(readWav(target.path), 4 * 16000);

  const candidateSample = Math.round(expected.candidates[0].time * 16000);
  let received = 0;
  const spotter = {
    async acceptV8WakePcm16(encoded) {
      received += Buffer.from(encoded, 'base64').length / 2;
      return received === candidateSample ? [{ keyword: 'hey_brain', sampleIndex: received }] : [];
    },
    async resetV8WakeSpotter() { received = 0; },
  };
  const backbone = await OpenWakeWordOnnxBackend.create(
    ort,
    path.join(mobileRoot, 'assets', 'wake-word', 'melspectrogram.onnx'),
    path.join(mobileRoot, 'assets', 'wake-word', 'embedding_model.onnx'),
    1280,
  );
  const model = JSON.parse(fs.readFileSync(path.join(mobileRoot, 'assets', 'wake-word', 'hey-brain-v8.json')));
  const observed = [];
  const detector = new V8TwoStageWakeWordDetector(model, spotter, backbone, (row) => observed.push(row));
  for (let offset = 0; offset < pcm.length; offset += 320) {
    const events = await detector.acceptPcm16(pcm.subarray(offset, offset + 320));
    if (events.length) break;
  }
  assert.equal(observed.length, 1);
  assert(Math.abs(observed[0].score - expected.candidates[0].score) < 1e-4,
    `App score ${observed[0].score} differs from lab ${expected.candidates[0].score}`);
  assert.equal(observed[0].passed, true);
  console.log(`PASS: app v8 verifier score ${observed[0].score.toFixed(6)} matches lab ${expected.candidates[0].score.toFixed(6)}`);
}

main().finally(() => { Module._extensions['.ts'] = originalExtension; }).catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
