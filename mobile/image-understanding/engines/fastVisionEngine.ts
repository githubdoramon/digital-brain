import * as FileSystem from 'expo-file-system/legacy';
import { Platform } from 'react-native';

import FastVisionNative from '@/modules/fast-vision/src';

import { buildFastVisionObservation } from '../fastVisionObservation';
import {
  type EngineInferenceResult,
  type EngineModelState,
  type EngineProgress,
  type ImageUnderstandingEngine,
} from '../types';

const DETECTOR_URL =
  'https://storage.googleapis.com/mediapipe-models/object_detector/efficientdet_lite0/int8/1/efficientdet_lite0.tflite';
const DETECTOR_SIZE_BYTES = 4_602_795;
const DETECTOR_MD5 = 'cebf64af6c35e5abd734494685064842';
const SCENE_MODEL_URL =
  'https://huggingface.co/litert-community/Places365-ResNet18-LiteRT/resolve/a61b97b29accb8ea5e75cc8085db5557b2ebfcdd/places_fp16.tflite';
const SCENE_MODEL_SIZE_BYTES = 22_775_088;
const SCENE_MODEL_MD5 = '5461f2f7903dc47ce1c05e0ae331b2e1';
const SCENE_LABELS_URL =
  'https://raw.githubusercontent.com/CSAILVision/places365/8a953ed56438726dc98bdef3796d042e7f1f171e/categories_places365.txt';
const SCENE_LABELS_SIZE_BYTES = 6_833;
const SCENE_LABELS_MD5 = '06c963b85866bd0649f97cb43dd16673';
const SCENE_IO_URL =
  'https://raw.githubusercontent.com/CSAILVision/places365/8a953ed56438726dc98bdef3796d042e7f1f171e/IO_places365.txt';
const SCENE_IO_SIZE_BYTES = 6_214;
const SCENE_IO_MD5 = '82b6256a773cf34aee9429b7fbaf1b26';
const MODEL_DIRECTORY = FileSystem.documentDirectory
  ? `${FileSystem.documentDirectory}image-understanding/fast-vision/`
  : null;
const DETECTOR_FILE = MODEL_DIRECTORY
  ? `${MODEL_DIRECTORY}efficientdet-lite0-int8-v1.tflite`
  : null;
const SCENE_MODEL_FILE = MODEL_DIRECTORY
  ? `${MODEL_DIRECTORY}places365-resnet18-fp16.tflite`
  : null;
const SCENE_LABELS_FILE = MODEL_DIRECTORY ? `${MODEL_DIRECTORY}places365-categories.txt` : null;
const SCENE_IO_FILE = MODEL_DIRECTORY ? `${MODEL_DIRECTORY}places365-indoor-outdoor.txt` : null;

type Artifact = {
  url: string;
  file: string | null;
  size: number;
  md5: string;
  label: string;
};

const ARTIFACTS: Artifact[] = [
  {
    url: DETECTOR_URL,
    file: DETECTOR_FILE,
    size: DETECTOR_SIZE_BYTES,
    md5: DETECTOR_MD5,
    label: 'EfficientDet-Lite0 detector',
  },
  {
    url: SCENE_MODEL_URL,
    file: SCENE_MODEL_FILE,
    size: SCENE_MODEL_SIZE_BYTES,
    md5: SCENE_MODEL_MD5,
    label: 'Places365 scene classifier',
  },
  {
    url: SCENE_LABELS_URL,
    file: SCENE_LABELS_FILE,
    size: SCENE_LABELS_SIZE_BYTES,
    md5: SCENE_LABELS_MD5,
    label: 'Places365 categories',
  },
  {
    url: SCENE_IO_URL,
    file: SCENE_IO_FILE,
    size: SCENE_IO_SIZE_BYTES,
    md5: SCENE_IO_MD5,
    label: 'Places365 indoor/outdoor metadata',
  },
];
const MODEL_SIZE_BYTES = ARTIFACTS.reduce((total, artifact) => total + artifact.size, 0);

function requireNativeModule() {
  if (!FastVisionNative) {
    throw new Error('Fast Vision is available only in an Android native build.');
  }
  return FastVisionNative;
}

async function artifactInfo(artifact: Artifact, uri = artifact.file) {
  if (!uri) return { valid: false, size: null as number | null };
  const info = await FileSystem.getInfoAsync(uri, { md5: true });
  if (!info.exists || info.isDirectory) return { valid: false, size: null as number | null };
  const valid = info.size === artifact.size && info.md5?.toLowerCase() === artifact.md5;
  return { valid, size: info.size };
}

export { buildFastVisionObservation } from '../fastVisionObservation';

export class FastVisionImageUnderstandingEngine implements ImageUnderstandingEngine {
  readonly id = 'fast-vision' as const;
  readonly label = 'Fast Vision';
  readonly runtimePackage = 'local Expo module';
  readonly runtimeVersion = '0.3.1';
  readonly modelId = 'ML Kit OCR + labels / EfficientDet-Lite0 INT8 / Places365 ResNet18 FP16';
  readonly modelVersion = 'ML Kit 19.0.1 + 16.0.8 / detector v1 / Places365 pinned';
  readonly computeBackend = 'CPU / Google Play services';
  readonly modelSource = SCENE_MODEL_URL;
  readonly promptVersion = 'fast_vision_pipeline.v4';

  async inspect(): Promise<EngineModelState> {
    if (Platform.OS !== 'android' || !FastVisionNative) {
      return {
        downloaded: false,
        modelSizeBytes: null,
        loaded: false,
        compatibilityWarning: 'Fast Vision is currently available only in Android native builds.',
      };
    }
    const [native, artifacts] = await Promise.all([
      FastVisionNative.getSupportStatus(),
      Promise.all(ARTIFACTS.map((artifact) => artifactInfo(artifact))),
    ]);
    return {
      downloaded: native.mlKitModulesAvailable && artifacts.every((artifact) => artifact.valid),
      modelSizeBytes: artifacts.reduce((total, artifact) => total + (artifact.size ?? 0), 0),
      loaded: native.modelsLoaded,
      compatibilityWarning: native.supported ? null : native.detail,
    };
  }

  async download(onProgress: (progress: EngineProgress) => void): Promise<EngineModelState> {
    const native = requireNativeModule();
    if (!MODEL_DIRECTORY || ARTIFACTS.some((artifact) => !artifact.file)) {
      throw new Error('App-private document storage is unavailable.');
    }
    onProgress({
      stage: 'downloading',
      progress: 0,
      detail: 'Installing on-device OCR and image-labeling modules…',
    });
    const subscription = native.addListener('onMlKitInstallProgress', (event) => {
      const ratio =
        event.downloadedBytes != null && event.totalBytes
          ? event.downloadedBytes / event.totalBytes
          : null;
      onProgress({
        stage: 'downloading',
        ...(ratio == null ? {} : { progress: Math.max(0, Math.min(0.2, ratio * 0.2)) }),
        detail: `ML Kit modules: ${event.state}…`,
      });
    });
    try {
      await native.installMlKitModules();
    } finally {
      subscription.remove();
    }

    await FileSystem.makeDirectoryAsync(MODEL_DIRECTORY, { intermediates: true });
    let completedBytes = 0;
    for (const artifact of ARTIFACTS) {
      const existing = await artifactInfo(artifact);
      if (existing.valid) {
        completedBytes += artifact.size;
        continue;
      }
      const target = artifact.file!;
      const temporaryFile = `${target}.download`;
      await FileSystem.deleteAsync(temporaryFile, { idempotent: true });
      const download = FileSystem.createDownloadResumable(
        artifact.url,
        temporaryFile,
        {},
        ({ totalBytesWritten, totalBytesExpectedToWrite }) => {
          const written =
            totalBytesExpectedToWrite > 0
              ? Math.min(
                  artifact.size,
                  (totalBytesWritten / totalBytesExpectedToWrite) * artifact.size,
                )
              : 0;
          onProgress({
            stage: 'downloading',
            progress: 0.2 + ((completedBytes + written) / MODEL_SIZE_BYTES) * 0.8,
            detail: `Downloading ${artifact.label}…`,
          });
        },
      );
      const result = await download.downloadAsync();
      if (!result) throw new Error(`${artifact.label} download did not complete.`);
      const downloaded = await artifactInfo(artifact, temporaryFile);
      if (!downloaded.valid) {
        await FileSystem.deleteAsync(temporaryFile, { idempotent: true });
        throw new Error(`${artifact.label} failed its size or checksum validation.`);
      }
      await FileSystem.deleteAsync(target, { idempotent: true });
      await FileSystem.moveAsync({ from: temporaryFile, to: target });
      completedBytes += artifact.size;
    }
    onProgress({ stage: 'downloading', progress: 1, detail: 'Fast Vision models are ready.' });
    return this.inspect();
  }

  async load(onProgress: (progress: EngineProgress) => void): Promise<number> {
    const native = requireNativeModule();
    const state = await this.inspect();
    if (
      !state.downloaded ||
      !DETECTOR_FILE ||
      !SCENE_MODEL_FILE ||
      !SCENE_LABELS_FILE ||
      !SCENE_IO_FILE
    ) {
      throw new Error('Download the Fast Vision models before loading them.');
    }
    await this.unload();
    onProgress({ stage: 'loading', detail: 'Loading the Fast Vision detector and scene model…' });
    const startedAt = Date.now();
    await native.loadModels(DETECTOR_FILE, SCENE_MODEL_FILE, SCENE_LABELS_FILE, SCENE_IO_FILE);
    return Date.now() - startedAt;
  }

  async infer(
    imageUri: string,
    onProgress: (progress: EngineProgress) => void,
  ): Promise<EngineInferenceResult> {
    const native = requireNativeModule();
    onProgress({
      stage: 'inferring',
      detail: 'Running OCR, image labeling, object detection, and scene classification locally…',
    });
    const startedAt = Date.now();
    const subscription = native.addListener('onFastVisionProgress', (event) => {
      const label = event.stage.replaceAll('_', ' ');
      const error = event.errorCode == null ? '' : ` (ML Kit code ${event.errorCode})`;
      onProgress({
        stage: 'inferring',
        detail: `Fast Vision ${label}: ${event.status}${error}.`,
      });
    });
    try {
      const analysis = await native.analyze(imageUri);
      const observation = buildFastVisionObservation(analysis);
      return {
        rawOutput: JSON.stringify({ observation, native_evidence: analysis }),
        parsedObservation: observation,
        inferenceMs: Date.now() - startedAt,
        promptTokens: null,
        completionTokens: null,
        totalTokens: null,
        timeToFirstTokenMs: null,
        tokensPerSecond: null,
        currentMemoryBytes: null,
        peakMemoryBytes: null,
        modelSizeBytes: MODEL_SIZE_BYTES,
        stageMeasurements: {
          imageDecodeMs: analysis.timings.imageDecodeMs,
          textRecognitionMs: analysis.timings.textRecognitionMs,
          imageLabelingMs: analysis.timings.imageLabelingMs,
          objectDetectionMs: analysis.timings.objectDetectionMs,
          sceneClassificationMs: analysis.timings.sceneClassificationMs,
        },
      };
    } finally {
      subscription.remove();
    }
  }

  async unload(onProgress?: (progress: EngineProgress) => void): Promise<void> {
    if (!FastVisionNative) return;
    onProgress?.({ stage: 'unloading', detail: 'Releasing Fast Vision native resources…' });
    await FastVisionNative.unload();
  }

  async deleteModel(onProgress: (progress: EngineProgress) => void): Promise<void> {
    onProgress({ stage: 'deleting', detail: 'Deleting Fast Vision models…' });
    await this.unload(onProgress);
    for (const artifact of ARTIFACTS) {
      if (!artifact.file) continue;
      await FileSystem.deleteAsync(artifact.file, { idempotent: true });
      await FileSystem.deleteAsync(`${artifact.file}.download`, { idempotent: true });
    }
    if (FastVisionNative) await FastVisionNative.releaseMlKitModules();
  }
}
