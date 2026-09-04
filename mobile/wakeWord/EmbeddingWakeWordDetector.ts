import type { DetectionEvent, EmbeddingDetectionEvaluation, EmbeddingWakeWordModel } from './types';
import type { SpeechEmbeddingBackend } from './OpenWakeWordOnnxBackend';

export class EmbeddingWakeWordDetector {
  private readonly embeddings: Float32Array[] = [];
  private readonly preRoll: number[] = [];
  private processedHopSamples = 0;
  private acceptedPcmSamples = 0;
  private consecutiveHits = 0;
  private cooldownUntilSample = 0;
  private acceptanceQueue: Promise<void> = Promise.resolve();

  constructor(
    readonly model: EmbeddingWakeWordModel,
    private readonly backend: SpeechEmbeddingBackend,
    private readonly onEvaluation?: (evaluation: EmbeddingDetectionEvaluation) => void,
  ) {
    if (model.schemaVersion !== 3 || model.kind !== 'personal-openwakeword-mlp') {
      throw new Error('Unsupported embedding wake-word model');
    }
    const { audioConfig, classifier } = model;
    const inputSize = audioConfig.embeddingFrames * audioConfig.embeddingSize;
    if (backend.embeddingSize !== audioConfig.embeddingSize)
      throw new Error('Embedding backend size mismatch');
    if (classifier.inputWeights.length !== inputSize * classifier.hiddenSize) {
      throw new Error('Wake-word input weight shape is invalid');
    }
    if (
      classifier.hiddenBias.length !== classifier.hiddenSize ||
      classifier.outputWeights.length !== classifier.hiddenSize
    ) {
      throw new Error('Wake-word hidden layer shape is invalid');
    }
  }

  acceptPcm16(chunk: Int16Array): Promise<DetectionEvent[]> {
    const ownedChunk = chunk.slice();
    const result = this.acceptanceQueue.then(() => this.acceptSerial(ownedChunk));
    this.acceptanceQueue = result.then(
      () => undefined,
      () => undefined,
    );
    return result;
  }

  private async acceptSerial(chunk: Int16Array): Promise<DetectionEvent[]> {
    this.appendPreRoll(chunk);
    this.acceptedPcmSamples += chunk.length;
    const events: DetectionEvent[] = [];
    for (const embedding of await this.backend.acceptPcm16(chunk)) {
      this.processedHopSamples += this.model.audioConfig.streamHopSamples;
      this.embeddings.push(embedding);
      if (this.embeddings.length > this.model.audioConfig.embeddingFrames) this.embeddings.shift();
      const event = this.evaluate();
      if (event) events.push(event);
    }
    return events;
  }

  reset(): void {
    this.backend.reset();
    this.embeddings.length = 0;
    this.preRoll.length = 0;
    this.processedHopSamples = 0;
    this.acceptedPcmSamples = 0;
    this.consecutiveHits = 0;
    this.cooldownUntilSample = 0;
  }

  private evaluate(): DetectionEvent | null {
    const { audioConfig, classifier, detectorConfig } = this.model;
    if (this.embeddings.length < audioConfig.embeddingFrames) return null;
    if (this.processedHopSamples < this.cooldownUntilSample) {
      this.consecutiveHits = 0;
      return null;
    }

    const score = this.score();
    const passed = score >= classifier.threshold;
    this.consecutiveHits = passed ? this.consecutiveHits + 1 : 0;
    this.onEvaluation?.({
      audioTimeMs: (this.processedHopSamples * 1000) / audioConfig.sampleRate,
      score,
      threshold: classifier.threshold,
      passed,
      consecutiveHits: this.consecutiveHits,
    });
    if (this.consecutiveHits < detectorConfig.consecutiveHits) return null;

    this.consecutiveHits = 0;
    this.cooldownUntilSample =
      this.processedHopSamples +
      Math.round((detectorConfig.cooldownMs * audioConfig.sampleRate) / 1000);
    return {
      modelName: this.model.name,
      score,
      threshold: classifier.threshold,
      audioTimeMs: (this.processedHopSamples * 1000) / audioConfig.sampleRate,
      preRollStartAudioTimeMs:
        ((this.acceptedPcmSamples - this.preRoll.length) * 1000) / audioConfig.sampleRate,
      preRollEndAudioTimeMs: (this.acceptedPcmSamples * 1000) / audioConfig.sampleRate,
      preRollPcm16: Int16Array.from(this.preRoll),
    };
  }

  private score(): number {
    const { audioConfig, classifier } = this.model;
    const hidden = new Float64Array(classifier.hiddenSize);
    for (let hiddenIndex = 0; hiddenIndex < classifier.hiddenSize; hiddenIndex += 1) {
      let value = classifier.hiddenBias[hiddenIndex];
      let inputIndex = 0;
      for (const embedding of this.embeddings) {
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

  private appendPreRoll(chunk: Int16Array): void {
    for (const sample of chunk) this.preRoll.push(sample);
    const maximum = Math.round(
      (this.model.detectorConfig.preRollMs * this.model.audioConfig.sampleRate) / 1000,
    );
    if (this.preRoll.length > maximum) this.preRoll.splice(0, this.preRoll.length - maximum);
  }
}
