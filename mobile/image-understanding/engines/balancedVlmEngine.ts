import * as FileSystem from 'expo-file-system/legacy';
import { Platform } from 'react-native';
import { LFM2_5_VL_450M_QUANTIZED, LLMModule, type Message } from 'react-native-executorch';
import { ExpoResourceFetcher } from 'react-native-executorch-expo-resource-fetcher';

import {
  BALANCED_OBSERVATION_PROMPT_VERSION,
  buildBalancedObservation,
  buildBalancedObservationPrompt,
} from '../balancedObservation';
import { ensureExecutorchInitialized } from '../executorchRuntime';
import type {
  EngineInferenceContext,
  EngineInferenceResult,
  EngineModelState,
  EngineProgress,
  ImageUnderstandingEngine,
} from '../types';

const MODEL = LFM2_5_VL_450M_QUANTIZED;
const ARTIFACTS = [MODEL.modelSource, MODEL.tokenizerSource, MODEL.tokenizerConfigSource] as const;
const MINIMUM_ANDROID_API = 33;

function artifactFilename(source: string): string {
  return source.split('?')[0].split('/').at(-1) ?? source;
}

function localPath(path: string): string {
  return path.startsWith('file://') ? path.slice('file://'.length) : path;
}

async function downloadedArtifactInfo(): Promise<{ downloaded: boolean; sizeBytes: number }> {
  const files = await ExpoResourceFetcher.listDownloadedFiles().catch(() => [] as string[]);
  const matches = ARTIFACTS.map((source) => {
    const filename = artifactFilename(source);
    return files.find((file) => file.endsWith(`/${filename}`) || file.endsWith(filename));
  });
  if (matches.some((path) => !path)) return { downloaded: false, sizeBytes: 0 };
  const infos = await Promise.all(matches.map((path) => FileSystem.getInfoAsync(path!)));
  return {
    downloaded: infos.every((info) => info.exists && !info.isDirectory),
    sizeBytes: infos.reduce((total, info) => total + (info.exists ? info.size : 0), 0),
  };
}

function compatibilityWarning(): string | null {
  if (Platform.OS !== 'android') {
    return 'The balanced LFM2.5-VL benchmark is currently enabled only on Android.';
  }
  const api = typeof Platform.Version === 'number' ? Platform.Version : Number(Platform.Version);
  if (Number.isFinite(api) && api < MINIMUM_ANDROID_API) {
    return 'React Native ExecuTorch 0.9.x requires Android 13 or newer for this benchmark.';
  }
  return null;
}

export class BalancedVlmImageUnderstandingEngine implements ImageUnderstandingEngine {
  readonly id = 'balanced-vlm' as const;
  readonly label = 'Balanced VLM';
  readonly runtimePackage = 'react-native-executorch';
  readonly runtimeVersion = '0.9.3';
  readonly modelId = 'LFM2.5-VL-450M-quantized';
  readonly modelVersion = 'v0.9.0 / XNNPACK 8da4w';
  readonly computeBackend = 'CPU / XNNPACK';
  readonly modelSource = MODEL.modelSource;
  readonly promptVersion = BALANCED_OBSERVATION_PROMPT_VERSION;

  private model: LLMModule | null = null;
  private modelSizeBytes: number | null = null;
  private inferenceStartedAt: number | null = null;
  private firstTokenAt: number | null = null;

  constructor() {
    ensureExecutorchInitialized();
  }

  async inspect(): Promise<EngineModelState> {
    const warning = compatibilityWarning();
    const artifact = warning ? { downloaded: false, sizeBytes: 0 } : await downloadedArtifactInfo();
    this.modelSizeBytes = artifact.sizeBytes || null;
    return {
      downloaded: artifact.downloaded,
      modelSizeBytes: this.modelSizeBytes,
      loaded: Boolean(this.model),
      compatibilityWarning: warning,
    };
  }

  async download(onProgress: (progress: EngineProgress) => void): Promise<EngineModelState> {
    const warning = compatibilityWarning();
    if (warning) throw new Error(warning);
    onProgress({
      stage: 'downloading',
      progress: 0,
      detail: 'Checking the balanced LFM2.5-VL model cache…',
    });
    await ExpoResourceFetcher.fetch(
      (value) =>
        onProgress({
          stage: 'downloading',
          progress: Math.max(0, Math.min(1, value)),
          detail: 'Downloading quantized LFM2.5-VL-450M artifacts…',
        }),
      ...ARTIFACTS,
    );
    onProgress({ stage: 'downloading', progress: 1, detail: 'Balanced VLM artifacts are ready.' });
    return this.inspect();
  }

  async load(onProgress: (progress: EngineProgress) => void): Promise<number> {
    await this.unload();
    const state = await this.inspect();
    if (!state.downloaded) throw new Error('Download the balanced VLM before loading it.');
    onProgress({
      stage: 'loading',
      detail: 'Loading quantized LFM2.5-VL-450M with ExecuTorch/XNNPACK…',
    });
    const startedAt = Date.now();
    const model = await LLMModule.fromModelName(
      MODEL,
      (value) =>
        onProgress({
          stage: 'loading',
          progress: Math.max(0, Math.min(1, value)),
          detail: 'Loading cached LFM2.5-VL artifacts…',
        }),
      () => {
        if (this.inferenceStartedAt != null && this.firstTokenAt == null) {
          this.firstTokenAt = Date.now();
        }
      },
    );
    model.configure({
      chatConfig: {
        systemPrompt:
          'You create useful visual memories of what is happening. Follow the requested headings, distinguish visible facts from interpretations, and do not invent unsupported details.',
      },
      generationConfig: {
        temperature: 0.1,
        minP: 0.15,
        repetitionPenalty: 1.05,
        outputTokenBatchSize: 1,
      },
    });
    this.model = model;
    return Date.now() - startedAt;
  }

  async infer(
    imageUri: string,
    onProgress: (progress: EngineProgress) => void,
    context?: EngineInferenceContext,
  ): Promise<EngineInferenceResult> {
    const model = this.model;
    if (!model) throw new Error('Balanced VLM is not loaded.');
    onProgress({
      stage: 'inferring',
      detail: context?.detectorObservation
        ? 'Describing the moment with detector and OCR evidence…'
        : 'Describing the moment without detector evidence…',
    });
    const prompt = buildBalancedObservationPrompt(context?.detectorObservation);
    const startedAt = Date.now();
    this.inferenceStartedAt = startedAt;
    this.firstTokenAt = null;
    try {
      const messages: Message[] = [
        { role: 'user', content: prompt, mediaPath: localPath(imageUri) },
      ];
      const rawOutput = await model.generate(messages);
      const inferenceMs = Date.now() - startedAt;
      const promptTokens = model.getPromptTokensCount();
      const completionTokens = model.getGeneratedTokenCount();
      return {
        rawOutput,
        parsedObservation: buildBalancedObservation(rawOutput, context?.detectorObservation),
        inferenceMs,
        promptTokens,
        completionTokens,
        totalTokens: model.getTotalTokensCount(),
        timeToFirstTokenMs:
          this.firstTokenAt == null ? null : Math.max(0, this.firstTokenAt - startedAt),
        tokensPerSecond:
          completionTokens > 0 && inferenceMs > 0 ? completionTokens / (inferenceMs / 1000) : null,
        currentMemoryBytes: null,
        peakMemoryBytes: null,
        modelSizeBytes: this.modelSizeBytes,
      };
    } finally {
      this.inferenceStartedAt = null;
      this.firstTokenAt = null;
    }
  }

  async unload(onProgress?: (progress: EngineProgress) => void): Promise<void> {
    const model = this.model;
    if (!model) return;
    onProgress?.({ stage: 'unloading', detail: 'Releasing Balanced VLM native resources…' });
    this.model = null;
    model.delete();
  }

  async deleteModel(onProgress: (progress: EngineProgress) => void): Promise<void> {
    onProgress({ stage: 'deleting', detail: 'Deleting the Balanced VLM artifacts…' });
    await this.unload(onProgress);
    await ExpoResourceFetcher.deleteResources(...ARTIFACTS);
    this.modelSizeBytes = null;
  }
}
