export interface FeatureConfig {
  sampleRate: number;
  frameLength: number;
  hopLength: number;
  fftSize: number;
  melBins: number;
  minHz: number;
  maxHz: number;
  includeDeltas?: boolean;
  deltaScale?: number;
}

export interface DetectorConfig {
  threshold: number;
  consecutiveHits: number;
  cooldownMs: number;
  preRollMs: number;
  evaluationHopFrames: number;
  minimumRms: number;
  windowLengthFactors: number[];
  dtwBandRatio?: number;
  confuserMargin?: number;
}

export interface WakeWordTemplate {
  id: string;
  frames: number[][];
  category?: string;
}

export interface WakeWordModel {
  schemaVersion: 1 | 2;
  kind: 'personal-logmel-dtw';
  name: string;
  createdAt: string;
  featureConfig: FeatureConfig;
  detectorConfig: DetectorConfig;
  templates: WakeWordTemplate[];
  confuserTemplates?: WakeWordTemplate[];
  calibration?: Record<string, number>;
}

export interface DetectionEvent {
  modelName: string;
  score: number;
  threshold: number;
  /** Stream position at which the detector committed the positive decision. */
  audioTimeMs: number;
  /** Source-audio bounds of `preRollPcm16`; may extend past `audioTimeMs` while a hop is pending. */
  preRollStartAudioTimeMs: number;
  preRollEndAudioTimeMs: number;
  preRollPcm16: Int16Array;
  confuserScore?: number;
  confuserMargin?: number;
}

export interface DetectionEvaluation {
  audioTimeMs: number;
  rms: number;
  targetDistance: number;
  threshold: number;
  targetPassed: boolean;
  confuserDistance?: number;
  confuserMargin?: number;
  requiredConfuserMargin: number;
  confuserPassed: boolean;
}

export interface EmbeddingWakeWordModel {
  schemaVersion: 3;
  kind: 'personal-openwakeword-mlp';
  name: string;
  createdAt: string;
  audioConfig: {
    sampleRate: number;
    streamHopSamples: number;
    embeddingFrames: number;
    embeddingSize: number;
  };
  backbone: {
    melspectrogramModel: string;
    embeddingModel: string;
    license: string;
    source: string;
  };
  classifier: {
    type: 'mlp-relu';
    hiddenSize: number;
    inputWeights: number[];
    hiddenBias: number[];
    outputWeights: number[];
    outputBias: number;
    threshold: number;
  };
  detectorConfig: {
    consecutiveHits: number;
    cooldownMs: number;
    preRollMs: number;
  };
  training?: Record<string, unknown>;
  heldoutEvaluation?: Record<string, unknown>;
}

export interface EmbeddingDetectionEvaluation {
  audioTimeMs: number;
  score: number;
  threshold: number;
  passed: boolean;
  consecutiveHits: number;
}
