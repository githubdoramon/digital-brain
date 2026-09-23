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
  let nativeSamples = 0;
  let resetCount = 0;
  const spotter = {
    async acceptV8WakePcm16(encoded) {
      const bytes = Buffer.from(encoded, 'base64');
      assert.equal(bytes.length % 2, 0);
      nativeSamples += bytes.length / 2;
      return nativeSamples === 32000 ? [{ keyword: 'okay_brain', sampleIndex: nativeSamples }] : [];
    },
    async resetV8WakeSpotter() {
      nativeSamples = 0;
      resetCount += 1;
    },
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
  const events = await detector.acceptPcm16(input);
  assert.equal(events.length, 1);
  assert.equal(events[0].modelName, 'okay-brain');
  assert.equal(events[0].audioTimeMs, 2000);
  assert.equal(events[0].preRollStartAudioTimeMs, 200);
  assert.equal(events[0].preRollEndAudioTimeMs, 2000);
  assert.deepEqual(events[0].preRollPcm16, input.slice(3200, 32000));
  assert.deepEqual(events[0].postDetectionPcm16, input.slice(32000));
  assert.deepEqual(accepted.backend.observedPcm, input.slice(0, 32000));
  assert.equal(accepted.backend.resetCount, 1);
  assert.equal(evaluations[0].passed, true);
  await detector.reset();
  assert.equal(accepted.getResetCount(), 1);
  assert.equal((await detector.acceptPcm16(input)).length, 1);

  const rejected = fixture(-2);
  const rejectedDetector = new V8TwoStageWakeWordDetector(rejected.model, rejected.spotter, rejected.backend);
  assert.deepEqual(await rejectedDetector.acceptPcm16(input), []);
  assert.equal(rejected.backend.observedPcm.length, 32000);

  console.log('PASS: v8 candidate alignment, on-demand score, pre-roll/tail, reject, reset');
}

main().finally(() => {
  Module._extensions['.ts'] = originalExtension;
}).catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
