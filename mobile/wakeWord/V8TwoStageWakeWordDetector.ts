import type { SpeechEmbeddingBackend } from './OpenWakeWordOnnxBackend';
import type { EmbeddingWakeWordModel } from './types';

const SAMPLE_RATE = 16_000;
const VERIFIER_THRESHOLD = 0.7614435404638955;

export type V8Keyword = 'hey_brain' | 'okay_brain';
export type V8Candidate = { keyword: V8Keyword; sampleIndex: number };

export type V8NativeCommandCapture = {
  pcm: Uint8Array;
  startSampleIndex: number;
  endSampleIndex: number;
  ambientRms: number[];
};

export interface V8NativeSpotter {
  resetV8WakeSpotter(): Promise<void>;
  getV8WakeAudio(startSampleIndex: number, endSampleIndex: number): Promise<Uint8Array>;
  startV8WakeCommandCapture(startSampleIndex: number): Promise<V8NativeCommandCapture>;
  stopV8WakeCommandCapture(resumeWakeDetection: boolean): Promise<void>;
}

export type V8CandidateEvaluation = {
  keyword: V8Keyword;
  audioTimeMs: number;
  score: number | null;
  threshold: number;
  passed: boolean;
};

export type V8DetectionEvent = {
  modelName: string;
  score: number;
  threshold: number;
  audioTimeMs: number;
  preRollStartAudioTimeMs: number;
  preRollEndAudioTimeMs: number;
  preRollStartSampleIndex: number;
};

function pcm16FromLittleEndianBytes(bytes: Uint8Array): Int16Array {
  if (bytes.byteLength % Int16Array.BYTES_PER_ELEMENT !== 0) {
    throw new Error('Native wake audio is not aligned to PCM16 samples');
  }
  if (bytes.byteOffset % Int16Array.BYTES_PER_ELEMENT === 0) {
    return new Int16Array(bytes.buffer, bytes.byteOffset, bytes.byteLength / 2);
  }
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const samples = new Int16Array(bytes.byteLength / 2);
  for (let index = 0; index < samples.length; index += 1) {
    samples[index] = view.getInt16(index * 2, true);
  }
  return samples;
}

/** Native Sherpa owns the continuous PCM ring; JS only reads audio for a candidate. */
export class V8TwoStageWakeWordDetector {
  private verificationQueue: Promise<void> = Promise.resolve();

  constructor(
    readonly model: EmbeddingWakeWordModel,
    private readonly spotter: V8NativeSpotter,
    private readonly verifierBackend: SpeechEmbeddingBackend,
    private readonly onCandidate?: (evaluation: V8CandidateEvaluation) => void,
  ) {
    if (model.schemaVersion !== 3 || model.kind !== 'personal-openwakeword-mlp') {
      throw new Error('Unsupported v8 verifier model');
    }
    if (model.audioConfig.sampleRate !== SAMPLE_RATE || model.audioConfig.streamHopSamples !== 1_280) {
      throw new Error('V8 audio configuration mismatch');
    }
    if (verifierBackend.embeddingSize !== model.audioConfig.embeddingSize) {
      throw new Error('V8 embedding size mismatch');
    }
    const { classifier, audioConfig } = model;
    if (
      classifier.inputWeights.length !==
        audioConfig.embeddingFrames * audioConfig.embeddingSize * classifier.hiddenSize ||
      classifier.hiddenBias.length !== classifier.hiddenSize ||
      classifier.outputWeights.length !== classifier.hiddenSize
    ) {
      throw new Error('V8 verifier weight shape mismatch');
    }
  }

  acceptCandidate(candidate: V8Candidate): Promise<V8DetectionEvent | null> {
    const result = this.verificationQueue.then(() => this.verifyCandidate(candidate));
    this.verificationQueue = result.then(() => undefined, () => undefined);
    return result;
  }

  startCommandCapture(candidate: V8Candidate): Promise<V8NativeCommandCapture> {
    const preRollSamples = Math.round((this.model.detectorConfig.preRollMs * SAMPLE_RATE) / 1_000);
    return this.spotter.startV8WakeCommandCapture(
      Math.max(0, candidate.sampleIndex - preRollSamples),
    );
  }

  reset(): Promise<void> {
    const result = this.verificationQueue.then(async () => {
      await this.spotter.resetV8WakeSpotter();
      this.verifierBackend.reset();
    });
    this.verificationQueue = result.then(
      () => undefined,
      () => undefined,
    );
    return result;
  }

  private async verifyCandidate(candidate: V8Candidate): Promise<V8DetectionEvent | null> {
    if (!Number.isSafeInteger(candidate.sampleIndex) || candidate.sampleIndex < 0) {
      throw new Error('V8 candidate has an invalid sample index');
    }
    const score = await this.scoreCandidate(candidate.sampleIndex);
    const passed = score !== null && score >= VERIFIER_THRESHOLD;
    this.onCandidate?.({
      keyword: candidate.keyword,
      audioTimeMs: (candidate.sampleIndex * 1_000) / SAMPLE_RATE,
      score,
      threshold: VERIFIER_THRESHOLD,
      passed,
    });
    if (!passed || score === null) return null;
    const preRollSamples = Math.round((this.model.detectorConfig.preRollMs * SAMPLE_RATE) / 1_000);
    return {
      modelName: candidate.keyword.replace('_', '-'),
      score,
      threshold: VERIFIER_THRESHOLD,
      audioTimeMs: (candidate.sampleIndex * 1_000) / SAMPLE_RATE,
      preRollStartAudioTimeMs:
        (Math.max(0, candidate.sampleIndex - preRollSamples) * 1_000) / SAMPLE_RATE,
      preRollEndAudioTimeMs: (candidate.sampleIndex * 1_000) / SAMPLE_RATE,
      preRollStartSampleIndex: Math.max(0, candidate.sampleIndex - preRollSamples),
    };
  }

  private async scoreCandidate(eventSamples: number): Promise<number | null> {
    const hop = this.model.audioConfig.streamHopSamples;
    // Same phase alignment as training.evaluate_two_stage.candidate_score.
    const first = Math.max(0, Math.floor((eventSamples - 4 * SAMPLE_RATE) / hop) * hop);
    this.verifierBackend.reset();
    const bytes = await this.spotter.getV8WakeAudio(first, eventSamples);
    const embeddings = await this.verifierBackend.acceptPcm16(pcm16FromLittleEndianBytes(bytes));
    const { audioConfig, classifier } = this.model;
    if (embeddings.length < audioConfig.embeddingFrames) return null;
    const window = embeddings.slice(-audioConfig.embeddingFrames);
    const hidden = new Float64Array(classifier.hiddenSize);
    for (let hiddenIndex = 0; hiddenIndex < classifier.hiddenSize; hiddenIndex += 1) {
      let value = classifier.hiddenBias[hiddenIndex];
      let inputIndex = 0;
      for (const embedding of window) {
        for (let dimension = 0; dimension < audioConfig.embeddingSize; dimension += 1) {
          value +=
            embedding[dimension] *
            classifier.inputWeights[inputIndex * classifier.hiddenSize + hiddenIndex];
          inputIndex += 1;
        }
      }
      hidden[hiddenIndex] = Math.max(0, value);
    }
    let logit = classifier.outputBias;
    for (let index = 0; index < classifier.hiddenSize; index += 1) {
      logit += hidden[index] * classifier.outputWeights[index];
    }
    return 1 / (1 + Math.exp(-Math.max(-30, Math.min(30, logit))));
  }
}
