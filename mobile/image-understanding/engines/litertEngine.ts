import { Platform } from 'react-native';
import * as Device from 'expo-device';
import {
  checkMultimodalSupport,
  createLLM,
  GEMMA_4_E2B_IT,
  ModelRegistry,
  type LiteRTLMInstance,
} from 'react-native-litert-lm';

import { IMAGE_OBSERVATION_PROMPT } from '../observationSchema';
import type {
  EngineInferenceResult,
  EngineModelState,
  EngineProgress,
  ImageUnderstandingEngine,
} from '../types';
import { IMAGE_OBSERVATION_PROMPT_VERSION } from '../types';

const FORCE_LOAD_MINIMUM_DEVICE_MEMORY_BYTES = 8 * 1024 * 1024 * 1024;

function localPath(path: string): string {
  return path.startsWith('file://') ? path.slice('file://'.length) : path;
}

export class LiteRTImageUnderstandingEngine implements ImageUnderstandingEngine {
  readonly id = 'litert-lm' as const;
  readonly label = 'LiteRT-LM';
  readonly runtimePackage = 'react-native-litert-lm';
  readonly runtimeVersion = '0.5.1';
  readonly modelId = 'gemma-4-E2B-it';
  readonly modelVersion = 'main / LiteRT-LM bundle';
  readonly computeBackend = 'GPU (strict; no CPU fallback)';
  readonly modelSource = GEMMA_4_E2B_IT;
  readonly promptVersion = IMAGE_OBSERVATION_PROMPT_VERSION;

  private model: LiteRTLMInstance | null = null;
  private loadedPath: string | null = null;
  private modelSizeBytes: number | null = null;

  async inspect(): Promise<EngineModelState> {
    if (Platform.OS !== 'android' && Platform.OS !== 'ios') {
      return {
        downloaded: false,
        modelSizeBytes: null,
        loaded: false,
        compatibilityWarning: 'LiteRT-LM is available only in native Android and iOS builds.',
      };
    }
    const downloaded = ModelRegistry.isCached(this.modelSource);
    const path = downloaded ? ModelRegistry.getFilePath(this.modelSource) : null;
    const size = path ? ModelRegistry.getFileSizeBytes(path) : -1;
    this.modelSizeBytes = size >= 0 ? size : null;
    return {
      downloaded,
      modelSizeBytes: this.modelSizeBytes,
      loaded: Boolean(this.model?.isReady()),
      compatibilityWarning: checkMultimodalSupport() ?? null,
    };
  }

  async download(onProgress: (progress: EngineProgress) => void): Promise<EngineModelState> {
    onProgress({ stage: 'downloading', progress: 0, detail: 'Checking LiteRT model cache…' });
    const path = await ModelRegistry.resolveModel(this.modelSource, {
      onProgress: (value) =>
        onProgress({
          stage: 'downloading',
          progress: Math.max(0, Math.min(1, value)),
          detail: 'Downloading Gemma 4 E2B LiteRT bundle…',
        }),
    });
    this.loadedPath = path;
    const size = ModelRegistry.getFileSizeBytes(path);
    this.modelSizeBytes = size >= 0 ? size : null;
    return this.inspect();
  }

  async load(onProgress: (progress: EngineProgress) => void): Promise<number> {
    await this.unload();
    const path = this.loadedPath ?? ModelRegistry.getFilePath(this.modelSource);
    if (!ModelRegistry.isCached(this.modelSource) || !path) {
      throw new Error('Download the LiteRT model before loading it.');
    }
    const forceLoad =
      typeof Device.totalMemory === 'number' &&
      Device.totalMemory >= FORCE_LOAD_MINIMUM_DEVICE_MEMORY_BYTES;
    onProgress({
      stage: 'loading',
      detail: forceLoad
        ? 'Loading Gemma 4 E2B with LiteRT-LM; overriding the conservative preflight on this 8+ GB device…'
        : 'Loading Gemma 4 E2B with LiteRT-LM…',
    });
    const startedAt = Date.now();
    const model = createLLM({ enableMemoryTracking: true, maxMemorySnapshots: 64 });
    try {
      await model.loadModel(localPath(path), {
        backend: 'gpu',
        maxContextTokens: 2048,
        maxOutputTokens: 768,
        temperature: 0.1,
        topK: 20,
        topP: 0.9,
        multimodal: true,
        forceLoad,
      });
      this.model = model;
      this.loadedPath = path;
      return Date.now() - startedAt;
    } catch (error) {
      model.close();
      throw error;
    }
  }

  async infer(
    imageUri: string,
    onProgress: (progress: EngineProgress) => void,
  ): Promise<EngineInferenceResult> {
    const model = this.model;
    if (!model?.isReady()) throw new Error('LiteRT-LM model is not loaded.');
    onProgress({ stage: 'inferring', detail: 'Analyzing the photo locally with LiteRT-LM…' });
    model.resetConversation();
    const startedAt = Date.now();
    const rawOutput = await model.sendMessageWithImage(
      IMAGE_OBSERVATION_PROMPT,
      localPath(imageUri),
    );
    const inferenceMs = Date.now() - startedAt;
    const stats = model.getStats();
    const usage = model.getMemoryUsage();
    const summary = model.memoryTracker?.getSummary();
    return {
      rawOutput,
      inferenceMs,
      promptTokens: stats.promptTokens >= 0 ? stats.promptTokens : null,
      completionTokens: stats.completionTokens >= 0 ? stats.completionTokens : null,
      totalTokens: stats.totalTokens >= 0 ? stats.totalTokens : null,
      timeToFirstTokenMs: stats.timeToFirstToken >= 0 ? stats.timeToFirstToken : null,
      tokensPerSecond: stats.tokensPerSecond >= 0 ? stats.tokensPerSecond : null,
      currentMemoryBytes: usage.residentBytes || summary?.currentResidentBytes || null,
      peakMemoryBytes: summary?.peakResidentBytes || null,
      modelSizeBytes: this.modelSizeBytes,
    };
  }

  async unload(onProgress?: (progress: EngineProgress) => void): Promise<void> {
    const model = this.model;
    if (!model) return;
    onProgress?.({ stage: 'unloading', detail: 'Releasing LiteRT-LM native resources…' });
    this.model = null;
    try {
      await model.unload();
    } finally {
      model.close();
    }
  }

  async deleteModel(onProgress: (progress: EngineProgress) => void): Promise<void> {
    onProgress({ stage: 'deleting', detail: 'Deleting the LiteRT model…' });
    await this.unload(onProgress);
    ModelRegistry.deleteFile(this.modelSource);
    this.loadedPath = null;
    this.modelSizeBytes = null;
  }
}
