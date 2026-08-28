import { NativeModule, requireOptionalNativeModule } from 'expo';

export type FastVisionInstallProgress = {
  state: string;
  downloadedBytes: number | null;
  totalBytes: number | null;
};

export type FastVisionAnalysisProgress = {
  stage:
    | 'decode'
    | 'text_recognition'
    | 'image_labeling'
    | 'object_detection'
    | 'scene_classification';
  status: 'starting' | 'completed' | 'retrying' | 'failed';
  elapsedMs: number | null;
  errorCode: number | null;
};

export type FastVisionComponentError = {
  stage: FastVisionAnalysisProgress['stage'];
  message: string;
  errorCode: number | null;
};

export type FastVisionLabel = {
  text: string;
  confidence: number;
  index: number;
};

export type FastVisionDetection = {
  label: string;
  confidence: number;
  index: number;
  box: { left: number; top: number; right: number; bottom: number };
};

export type FastVisionTextBlock = {
  text: string;
  lines: string[];
  box: { left: number; top: number; right: number; bottom: number } | null;
};

export type FastVisionScene = {
  label: string;
  confidence: number;
  settingType: 'indoor' | 'outdoor';
};

export type FastVisionAnalysis = {
  imageWidth: number;
  imageHeight: number;
  labels: FastVisionLabel[];
  visibleText: string[];
  textBlocks: FastVisionTextBlock[];
  detections: FastVisionDetection[];
  scenes: FastVisionScene[];
  indoorProbability: number;
  outdoorProbability: number;
  componentErrors: FastVisionComponentError[];
  timings: {
    imageDecodeMs: number;
    textRecognitionMs: number;
    imageLabelingMs: number;
    objectDetectionMs: number;
    sceneClassificationMs: number;
    totalMs: number;
  };
};

export type FastVisionSupport = {
  supported: boolean;
  detail: string | null;
  mlKitModulesAvailable: boolean;
  modelsLoaded: boolean;
};

type FastVisionEvents = {
  onMlKitInstallProgress(progress: FastVisionInstallProgress): void;
  onFastVisionProgress(progress: FastVisionAnalysisProgress): void;
};

declare class FastVisionNativeModule extends NativeModule<FastVisionEvents> {
  getSupportStatus(): Promise<FastVisionSupport>;
  installMlKitModules(): Promise<void>;
  releaseMlKitModules(): Promise<void>;
  loadModels(
    detectorModelPath: string,
    sceneModelPath: string,
    sceneLabelsPath: string,
    sceneIndoorOutdoorPath: string,
  ): Promise<void>;
  analyze(imageUri: string): Promise<FastVisionAnalysis>;
  unload(): Promise<void>;
}

export default requireOptionalNativeModule<FastVisionNativeModule>('FastVision');
