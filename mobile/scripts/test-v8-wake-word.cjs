const assert = require('node:assert/strict');
const Module = require('node:module');
const path = require('node:path');
const ts = require('typescript');

const mobileRoot = path.resolve(__dirname, '..');
const originalExtension = Module._extensions['.ts'];
Module._extensions['.ts'] = (module, filename) => {
  const source = require('node:fs').readFileSync(filename, 'utf8');
  const compiled = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
    fileName: filename,
  }).outputText;
  module._compile(compiled, filename);
};

const { V8TwoStageWakeWordDetector } = require(path.join(mobileRoot, 'wakeWord', 'V8TwoStageWakeWordDetector.ts'));

function pcmBytes(pcm) {
  const bytes = new Uint8Array(pcm.length * 2);
  const view = new DataView(bytes.buffer);
  for (let index = 0; index < pcm.length; index += 1) view.setInt16(index * 2, pcm[index], true);
  return bytes;
}

function fixture(outputBias) {
  const model = {
    schemaVersion: 3,
    kind: 'personal-openwakeword-mlp',
    name: 'test-wake',
    audioConfig: { sampleRate: 16000, streamHopSamples: 1280, embeddingFrames: 16, embeddingSize: 96 },
    detectorConfig: { consecutiveHits: 2, cooldownMs: 2500, preRollMs: 1800 },
    classifier: {
      hiddenSize: 1,
      inputWeights: Array(16 * 96).fill(0),
      hiddenBias: [1],
      outputWeights: [1],
      outputBias,
    },
  };
  let resetCount = 0;
  let nativePcm = new Int16Array(0);
  const spotter = {
    async resetV8WakeSpotter() {
      resetCount += 1;
    },
    async getV8WakeAudio(startSampleIndex, endSampleIndex) {
      return pcmBytes(nativePcm.subarray(startSampleIndex, endSampleIndex));
    },
    async startV8WakeCommandCapture(startSampleIndex) {
      return {
        pcm: pcmBytes(nativePcm.subarray(startSampleIndex)),
        startSampleIndex,
        endSampleIndex: nativePcm.length,
        ambientRms: [0.01],
      };
    },
    async stopV8WakeCommandCapture() {},
    setPcm(pcm) { nativePcm = pcm; },
  };
  const backend = {
    embeddingSize: 96,
    resetCount: 0,
    observedPcm: null,
    reset() { this.resetCount += 1; },
    async acceptPcm16(pcm) {
      this.observedPcm = pcm;
      return Array.from({ length: Math.floor(pcm.length / 1280) }, () => new Float32Array(96));
    },
  };
  return { model, spotter, backend, getResetCount: () => resetCount };
}

async function main() {
  const accepted = fixture(1);
  const evaluations = [];
  const detector = new V8TwoStageWakeWordDetector(
    accepted.model, accepted.spotter, accepted.backend, (event) => evaluations.push(event),
  );
  const input = Int16Array.from({ length: 40000 }, (_, index) => index % 1000);
  accepted.spotter.setPcm(input);
  const event = await detector.acceptCandidate({ keyword: 'okay_brain', sampleIndex: 32000 });
  assert.equal(event.modelName, 'okay-brain');
  assert.equal(event.audioTimeMs, 2000);
  assert.equal(event.preRollStartAudioTimeMs, 200);
  assert.equal(event.preRollEndAudioTimeMs, 2000);
  assert.equal(event.preRollStartSampleIndex, 3200);
  assert.deepEqual(accepted.backend.observedPcm, input.slice(0, 32000));
  assert.equal(accepted.backend.resetCount, 1);
  assert.equal(evaluations[0].passed, true);
  const commandCapture = await detector.startCommandCapture({ keyword: 'okay_brain', sampleIndex: 32000 });
  assert.equal(commandCapture.startSampleIndex, 3200);
  assert.deepEqual(new Int16Array(commandCapture.pcm.buffer), input.slice(3200));
  await detector.reset();
  assert.equal(accepted.getResetCount(), 1);
  accepted.spotter.setPcm(input);
  assert((await detector.acceptCandidate({ keyword: 'okay_brain', sampleIndex: 32000 })) !== null);

  const rejected = fixture(-2);
  const rejectedDetector = new V8TwoStageWakeWordDetector(rejected.model, rejected.spotter, rejected.backend);
  rejected.spotter.setPcm(input);
  assert.equal(await rejectedDetector.acceptCandidate({ keyword: 'okay_brain', sampleIndex: 32000 }), null);
  assert.equal(rejected.backend.observedPcm.length, 32000);

  console.log('PASS: v8 candidate alignment, on-demand score, pre-roll/tail, reject, reset');
}

main().finally(() => {
  Module._extensions['.ts'] = originalExtension;
}).catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
