import { Buffer } from 'buffer';

import type { SpeechEmbeddingBackend } from './OpenWakeWordOnnxBackend';
import type { DetectionEvent, EmbeddingWakeWordModel } from './types';

const SAMPLE_RATE = 16_000;
const NATIVE_FRAME = 320;
const HISTORY_SAMPLES = 4 * SAMPLE_RATE + 1_280;
const VERIFIER_THRESHOLD = 0.7614435404638955;

export type V8Keyword = 'hey_brain' | 'okay_brain';
export type V8Candidate = { keyword: V8Keyword; sampleIndex: number };

export interface V8NativeSpotter {
  acceptV8WakePcm16(pcmBase64: string): Promise<V8Candidate[]>;
  resetV8WakeSpotter(): Promise<void>;
}

export type V8CandidateEvaluation = {
  keyword: V8Keyword;
  audioTimeMs: number;
  score: number | null;
  threshold: number;
  passed: boolean;
};

/** Sherpa runs continuously; openWakeWord embeddings run only for a candidate. */
export class V8TwoStageWakeWordDetector {
  private readonly pcmRing = new Int16Array(HISTORY_SAMPLES);
  private processedSamples = 0;
  private acceptanceQueue: Promise<void> = Promise.resolve();

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

  acceptPcm16(chunk: Int16Array): Promise<DetectionEvent[]> {
    const owned = chunk.slice();
    const result = this.acceptanceQueue.then(() => this.acceptSerial(owned));
    this.acceptanceQueue = result.then(
      () => undefined,
      () => undefined,
    );
    return result;
  }

  reset(): Promise<void> {
    const result = this.acceptanceQueue.then(async () => {
      await this.spotter.resetV8WakeSpotter();
      this.verifierBackend.reset();
      this.processedSamples = 0;
      this.pcmRing.fill(0);
    });
    this.acceptanceQueue = result.then(
      () => undefined,
      () => undefined,
    );
    return result;
  }

  private async acceptSerial(chunk: Int16Array): Promise<DetectionEvent[]> {
    for (let offset = 0; offset < chunk.length; offset += NATIVE_FRAME) {
      const frame = chunk.subarray(offset, Math.min(chunk.length, offset + NATIVE_FRAME));
      this.appendPcm(frame);
      const encoded = Buffer.from(frame.buffer, frame.byteOffset, frame.byteLength).toString('base64');
      const candidates = await this.spotter.acceptV8WakePcm16(encoded);
      for (const candidate of candidates) {
        if (candidate.sampleIndex !== this.processedSamples) {
          throw new Error('V8 native spotter/audio history position mismatch');
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
        if (!passed || score === null) continue;
        const preRollSamples = Math.round((this.model.detectorConfig.preRollMs * SAMPLE_RATE) / 1_000);
        const preRollStart = Math.max(0, this.processedSamples - preRollSamples);
        const event: DetectionEvent = {
          modelName: candidate.keyword.replace('_', '-'),
          score,
          threshold: VERIFIER_THRESHOLD,
          audioTimeMs: (candidate.sampleIndex * 1_000) / SAMPLE_RATE,
          preRollStartAudioTimeMs: (preRollStart * 1_000) / SAMPLE_RATE,
          preRollEndAudioTimeMs: (this.processedSamples * 1_000) / SAMPLE_RATE,
          preRollPcm16: this.history(preRollStart, this.processedSamples),
          postDetectionPcm16: chunk.slice(offset + frame.length),
        };
        return [event];
      }
    }
    return [];
  }

  private appendPcm(chunk: Int16Array): void {
    for (const sample of chunk) {
      this.pcmRing[this.processedSamples % HISTORY_SAMPLES] = sample;
      this.processedSamples += 1;
    }
  }

  private history(start: number, end: number): Int16Array {
    if (start < Math.max(0, this.processedSamples - HISTORY_SAMPLES) || end > this.processedSamples) {
      throw new Error('V8 candidate history fell out of the rolling PCM buffer');
    }
    const output = new Int16Array(end - start);
    for (let index = start; index < end; index += 1) {
      output[index - start] = this.pcmRing[index % HISTORY_SAMPLES];
    }
    return output;
  }

  private async scoreCandidate(eventSamples: number): Promise<number | null> {
    const hop = this.model.audioConfig.streamHopSamples;
    // Same phase alignment as training.evaluate_two_stage.candidate_score.
    const first = Math.max(0, Math.floor((eventSamples - 4 * SAMPLE_RATE) / hop) * hop);
    this.verifierBackend.reset();
    const embeddings = await this.verifierBackend.acceptPcm16(this.history(first, eventSamples));
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
