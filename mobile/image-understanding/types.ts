// This is intentionally source-independent: an image is the first producer,
// but the persisted system unit is a moment rather than an image analysis run.
export const IMAGE_OBSERVATION_SCHEMA_VERSION = 'moment_observation.v1' as const;

export type Confidence = 'low' | 'medium' | 'high';

export type VisualObservation = {
  schema_version: typeof IMAGE_OBSERVATION_SCHEMA_VERSION;
  summary: string;
  objects: {
    label: string;
    count_min: number;
    count_max: number;
    details: string[];
  }[];
  visible_text: string[];
  people_presence: 'none' | 'possible' | 'present';
  people_count_min: number;
  people_count_max: number;
  people_details: string[];
  setting: string | null;
  interpretations: {
    claim: string;
    evidence: string[];
    confidence: Confidence;
  }[];
  uncertainties: string[];
  person_identification_attempted: false;
};

export type ImageUnderstandingEngineId = 'fast-vision' | 'balanced-vlm';

export type EngineInferenceContext = {
  detectorObservation?: VisualObservation;
};

export type ImageUnderstandingStage =
  | 'checking'
  | 'downloading'
  | 'loading'
  | 'inferring'
  | 'unloading'
  | 'deleting'
  | 'idle';

export type EngineProgress = {
  stage: ImageUnderstandingStage;
  progress?: number;
  detail: string;
};

export type EngineModelState = {
  downloaded: boolean;
  modelSizeBytes: number | null;
  loaded: boolean;
  compatibilityWarning: string | null;
};

export type EngineInferenceResult = {
  rawOutput: string;
  inferenceMs: number;
  promptTokens: number | null;
  completionTokens: number | null;
  totalTokens: number | null;
  timeToFirstTokenMs: number | null;
  tokensPerSecond: number | null;
  currentMemoryBytes: number | null;
  peakMemoryBytes: number | null;
  modelSizeBytes: number | null;
  parsedObservation?: VisualObservation;
  stageMeasurements?: {
    imageDecodeMs?: number;
    textRecognitionMs?: number;
    imageLabelingMs?: number;
    objectDetectionMs?: number;
    sceneClassificationMs?: number;
  };
};

export type ImageUnderstandingProcessLogEntry = {
  timestamp: string;
  elapsedMs: number;
  stage: string;
  message: string;
  measurements?: Record<string, number | boolean | null>;
};

export interface ImageUnderstandingEngine {
  readonly id: ImageUnderstandingEngineId;
  readonly label: string;
  readonly runtimePackage: string;
  readonly runtimeVersion: string;
  readonly modelId: string;
  readonly modelVersion: string;
  readonly computeBackend: string;
  readonly modelSource: string;
  readonly promptVersion: string;
  inspect(): Promise<EngineModelState>;
  download(onProgress: (progress: EngineProgress) => void): Promise<EngineModelState>;
  load(onProgress: (progress: EngineProgress) => void): Promise<number>;
  infer(
    imageUri: string,
    onProgress: (progress: EngineProgress) => void,
    context?: EngineInferenceContext,
  ): Promise<EngineInferenceResult>;
  interrupt?(): void;
  unload(onProgress?: (progress: EngineProgress) => void): Promise<void>;
  deleteModel(onProgress: (progress: EngineProgress) => void): Promise<void>;
}

export type ImageUnderstandingRunRecord = {
  id: string;
  timestamp: string;
  schemaVersion: string;
  promptVersion: string;
  runtime: {
    engineId: string;
    packageName: string;
    packageVersion: string;
    modelId: string;
    modelVersion: string;
    computeBackend: string;
  };
  device: {
    manufacturer: string | null;
    modelName: string | null;
    osName: string;
    osVersion: string;
    totalMemoryBytes: number | null;
  };
  measurements: {
    modelSizeBytes: number | null;
    coldLoadMs: number | null;
    inferenceMs: number | null;
    promptTokens: number | null;
    completionTokens: number | null;
    totalTokens: number | null;
    timeToFirstTokenMs: number | null;
    tokensPerSecond: number | null;
    currentMemoryBytes: number | null;
    peakMemoryBytes: number | null;
    imageDecodeMs: number | null;
    textRecognitionMs: number | null;
    imageLabelingMs: number | null;
    objectDetectionMs: number | null;
    sceneClassificationMs: number | null;
  };
  outputValid: boolean;
  parseRepairs: string[];
  processLog: ImageUnderstandingProcessLogEntry[];
  rawOutput: string | null;
  observation: VisualObservation | null;
  error: string | null;
};
