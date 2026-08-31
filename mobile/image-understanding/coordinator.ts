import * as Device from 'expo-device';
import * as FileSystem from 'expo-file-system/legacy';

import { BalancedVlmImageUnderstandingEngine } from './engines/balancedVlmEngine';
import { FastVisionImageUnderstandingEngine } from './engines/fastVisionEngine';
import { parseVisualObservationDetailed } from './observationSchema';
import { redactDiagnosticText } from './privacy';
import {
  IMAGE_OBSERVATION_SCHEMA_VERSION,
  type EngineModelState,
  type EngineInferenceContext,
  type EngineProgress,
  type ImageUnderstandingEngine,
  type ImageUnderstandingEngineId,
  type ImageUnderstandingProcessLogEntry,
  type ImageUnderstandingRunRecord,
} from './types';

const MAX_PROCESS_LOG_ENTRIES = 250;

const engineList: ImageUnderstandingEngine[] = [
  new FastVisionImageUnderstandingEngine(),
  new BalancedVlmImageUnderstandingEngine(),
];
const pipelineEngineIds: ImageUnderstandingEngineId[] = ['fast-vision', 'balanced-vlm'];
const engines = new Map(engineList.map((engine) => [engine.id, engine]));

let operationTail: Promise<void> = Promise.resolve();
let legacyCleanup: Promise<void> | null = null;

function removeLegacyLiteRtArtifacts(): Promise<void> {
  if (legacyCleanup) return legacyCleanup;
  legacyCleanup = (async () => {
    if (!FileSystem.documentDirectory) return;
    const legacyModel = `${FileSystem.documentDirectory}models/gemma-4-E2B-it.litertlm`;
    await Promise.all(
      [legacyModel, `${legacyModel}.tmp`].map((uri) =>
        FileSystem.deleteAsync(uri, { idempotent: true }).catch(() => undefined),
      ),
    );
  })();
  return legacyCleanup;
}

function withSerializedInference<T>(operation: () => Promise<T>): Promise<T> {
  const run = async () => {
    await removeLegacyLiteRtArtifacts();
    return operation();
  };
  const result = operationTail.then(run, run);
  operationTail = result.then(
    () => undefined,
    () => undefined,
  );
  return result;
}

function getEngine(id: ImageUnderstandingEngineId): ImageUnderstandingEngine {
  const engine = engines.get(id);
  if (!engine) throw new Error(`Unknown image-understanding engine: ${id}`);
  return engine;
}

async function unloadEveryEngine(onProgress?: (progress: EngineProgress) => void): Promise<void> {
  for (const engine of engineList) {
    await engine.unload(onProgress);
  }
}

function createRunId(engineId: ImageUnderstandingEngineId): string {
  return `${engineId}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

function baseRun(engine: ImageUnderstandingEngine): ImageUnderstandingRunRecord {
  return {
    id: createRunId(engine.id),
    timestamp: new Date().toISOString(),
    schemaVersion: IMAGE_OBSERVATION_SCHEMA_VERSION,
    promptVersion: engine.promptVersion,
    runtime: {
      engineId: engine.id,
      packageName: engine.runtimePackage,
      packageVersion: engine.runtimeVersion,
      modelId: engine.modelId,
      modelVersion: engine.modelVersion,
      computeBackend: engine.computeBackend,
    },
    device: {
      manufacturer: Device.manufacturer,
      modelName: Device.modelName,
      osName: Device.osName ?? 'unknown',
      osVersion: Device.osVersion ?? 'unknown',
      totalMemoryBytes: typeof Device.totalMemory === 'number' ? Device.totalMemory : null,
    },
    measurements: {
      modelSizeBytes: null,
      coldLoadMs: null,
      inferenceMs: null,
      promptTokens: null,
      completionTokens: null,
      totalTokens: null,
      timeToFirstTokenMs: null,
      tokensPerSecond: null,
      currentMemoryBytes: null,
      peakMemoryBytes: null,
      imageDecodeMs: null,
      textRecognitionMs: null,
      imageLabelingMs: null,
      objectDetectionMs: null,
      sceneClassificationMs: null,
    },
    outputValid: false,
    parseRepairs: [],
    processLog: [],
    rawOutput: null,
    observation: null,
    error: null,
  };
}

function createProcessLogger(run: ImageUnderstandingRunRecord, startedAt: number) {
  let lastDownloadBucket = -1;

  return (
    stage: string,
    message: string,
    measurements?: ImageUnderstandingProcessLogEntry['measurements'],
  ) => {
    const progress = measurements?.progressPercent;
    if (stage === 'downloading' && typeof progress === 'number') {
      const bucket = Math.floor(progress / 5);
      if (bucket === lastDownloadBucket && progress < 100) return;
      lastDownloadBucket = bucket;
    }

    run.processLog.push({
      timestamp: new Date().toISOString(),
      elapsedMs: Date.now() - startedAt,
      stage,
      message: redactDiagnosticText(message),
      ...(measurements ? { measurements } : {}),
    });
    if (run.processLog.length > MAX_PROCESS_LOG_ENTRIES) {
      run.processLog.splice(1, run.processLog.length - MAX_PROCESS_LOG_ENTRIES);
    }
  };
}

function imageUriScheme(imageUri: string): string {
  if (imageUri.startsWith('/')) return 'absolute-path';
  return imageUri.match(/^([a-z][a-z0-9+.-]*):/i)?.[1]?.toLowerCase() ?? 'unknown';
}

async function runEngineLocked(
  id: ImageUnderstandingEngineId,
  imageUri: string,
  onProgress: (engineId: ImageUnderstandingEngineId, progress: EngineProgress) => void,
  context?: EngineInferenceContext,
): Promise<ImageUnderstandingRunRecord> {
  const engine = getEngine(id);
  const run = baseRun(engine);
  const startedAt = Date.now();
  const log = createProcessLogger(run, startedAt);
  const report = (progress: EngineProgress) => {
    log(progress.stage, progress.detail, {
      progressPercent:
        progress.progress == null
          ? null
          : Math.round(Math.max(0, Math.min(1, progress.progress)) * 100),
    });
    onProgress(id, progress);
  };
  log('starting', `Starting serialized ${engine.label} run.`, {
    usesGenerativePrompt: engine.id !== 'fast-vision',
    usesDetectorEvidence: Boolean(context?.detectorObservation),
    imageUsesLocalFileUri: imageUriScheme(imageUri) === 'file',
    imageUsesAbsolutePath: imageUriScheme(imageUri) === 'absolute-path',
  });
  try {
    log('unloading', 'Releasing every image-understanding engine before loading this model.');
    await unloadEveryEngine();
    log('unloading', 'All image-understanding engines report unloaded.');
    log('download_starting', 'Checking the cache and downloading any missing model artifacts.');
    const downloaded = await engine.download(report);
    run.measurements.modelSizeBytes = downloaded.modelSizeBytes;
    log('downloaded', 'Required model artifacts are available locally.', {
      modelSizeBytes: downloaded.modelSizeBytes,
      alreadyLoaded: downloaded.loaded,
    });
    log('load_starting', 'About to enter the native model load call.');
    const coldLoadMs = await engine.load(report);
    run.measurements.coldLoadMs = coldLoadMs;
    log('loaded', 'Native model load completed.', { coldLoadMs });
    log('inference_starting', 'About to enter the native multimodal inference call.');
    const inference = await engine.infer(imageUri, report, context);
    run.rawOutput = inference.rawOutput;
    run.measurements.inferenceMs = inference.inferenceMs;
    run.measurements.promptTokens = inference.promptTokens;
    run.measurements.completionTokens = inference.completionTokens;
    run.measurements.totalTokens = inference.totalTokens;
    run.measurements.timeToFirstTokenMs = inference.timeToFirstTokenMs;
    run.measurements.tokensPerSecond = inference.tokensPerSecond;
    run.measurements.modelSizeBytes = inference.modelSizeBytes ?? downloaded.modelSizeBytes;
    run.measurements.currentMemoryBytes = inference.currentMemoryBytes;
    run.measurements.peakMemoryBytes = inference.peakMemoryBytes;
    run.measurements.imageDecodeMs = inference.stageMeasurements?.imageDecodeMs ?? null;
    run.measurements.textRecognitionMs = inference.stageMeasurements?.textRecognitionMs ?? null;
    run.measurements.imageLabelingMs = inference.stageMeasurements?.imageLabelingMs ?? null;
    run.measurements.objectDetectionMs = inference.stageMeasurements?.objectDetectionMs ?? null;
    run.measurements.sceneClassificationMs =
      inference.stageMeasurements?.sceneClassificationMs ?? null;
    log('inferred', 'Native multimodal inference completed; raw output captured locally.', {
      inferenceMs: inference.inferenceMs,
      rawOutputCharacters: inference.rawOutput.length,
      promptTokens: inference.promptTokens,
      completionTokens: inference.completionTokens,
      totalTokens: inference.totalTokens,
      timeToFirstTokenMs: inference.timeToFirstTokenMs,
      tokensPerSecond: inference.tokensPerSecond,
      currentMemoryBytes: inference.currentMemoryBytes,
      peakMemoryBytes: inference.peakMemoryBytes,
      imageDecodeMs: run.measurements.imageDecodeMs,
      textRecognitionMs: run.measurements.textRecognitionMs,
      imageLabelingMs: run.measurements.imageLabelingMs,
      objectDetectionMs: run.measurements.objectDetectionMs,
      sceneClassificationMs: run.measurements.sceneClassificationMs,
    });
    try {
      log('parsing', `Validating raw output against ${IMAGE_OBSERVATION_SCHEMA_VERSION}.`);
      const parsed = parseVisualObservationDetailed(
        inference.parsedObservation
          ? JSON.stringify(inference.parsedObservation)
          : inference.rawOutput,
      );
      run.observation = parsed.observation;
      run.parseRepairs = parsed.repairs;
      run.outputValid = true;
      if (parsed.repairs.length) {
        log(
          'parse_repaired',
          `Structured observation is valid after conservative repair: ${parsed.repairs.join(' ')}`,
          {
            repairCount: parsed.repairs.length,
          },
        );
      } else {
        log('parsed', 'Structured observation is valid.');
      }
    } catch (error) {
      run.error = `Structured output invalid: ${redactDiagnosticText(error)}`;
      log('parse_failed', run.error, { rawOutputCharacters: inference.rawOutput.length });
    }
  } catch (error) {
    run.error = redactDiagnosticText(error);
    log('failed', run.error);
  } finally {
    log('unloading', `Releasing ${engine.label} native resources after the run.`);
    try {
      await engine.unload(report);
      log('unloaded', `${engine.label} native resources released.`);
    } catch (error) {
      const unloadError = `Resource release failed: ${redactDiagnosticText(error)}`;
      run.error = run.error ? `${run.error}; ${unloadError}` : unloadError;
      log('unload_failed', unloadError);
    }
    log('finished', run.error ? 'Run finished with an error.' : 'Run completed successfully.', {
      outputValid: run.outputValid,
      totalElapsedMs: Date.now() - startedAt,
    });
    report({ stage: 'idle', detail: run.error ? 'Run finished with an error.' : 'Run complete.' });
  }
  return run;
}

export const imageUnderstandingCoordinator = {
  /** Interrupt native generation without waiting behind the serialization lock. */
  interruptActive(): void {
    for (const engine of engineList) engine.interrupt?.();
  },

  inspectPipeline(): Promise<Record<'fast-vision' | 'balanced-vlm', EngineModelState>> {
    return withSerializedInference(async () => {
      const entries = await Promise.all(
        engineList.map(async (engine) => [engine.id, await engine.inspect()] as const),
      );
      return Object.fromEntries(entries) as Record<
        'fast-vision' | 'balanced-vlm',
        EngineModelState
      >;
    });
  },

  runPipeline(
    imageUri: string,
    onProgress: (engineId: ImageUnderstandingEngineId, progress: EngineProgress) => void,
  ): Promise<{ evidenceRun: ImageUnderstandingRunRecord; finalRun: ImageUnderstandingRunRecord }> {
    return withSerializedInference(async () => {
      const evidenceRun = await runEngineLocked('fast-vision', imageUri, onProgress);
      const finalRun = await runEngineLocked(
        'balanced-vlm',
        imageUri,
        onProgress,
        evidenceRun.outputValid && evidenceRun.observation
          ? { detectorObservation: evidenceRun.observation }
          : undefined,
      );
      return { evidenceRun, finalRun };
    });
  },

  downloadPipeline(
    onProgress: (engineId: ImageUnderstandingEngineId, progress: EngineProgress) => void,
  ): Promise<Record<'fast-vision' | 'balanced-vlm', EngineModelState>> {
    return withSerializedInference(async () => {
      await unloadEveryEngine();
      const fastVision = await getEngine('fast-vision').download((progress) =>
        onProgress('fast-vision', progress),
      );
      const balancedVlm = await getEngine('balanced-vlm').download((progress) =>
        onProgress('balanced-vlm', progress),
      );
      return { 'fast-vision': fastVision, 'balanced-vlm': balancedVlm };
    });
  },

  deletePipeline(
    onProgress: (engineId: ImageUnderstandingEngineId, progress: EngineProgress) => void,
  ): Promise<void> {
    return withSerializedInference(async () => {
      await unloadEveryEngine();
      for (const id of pipelineEngineIds) {
        await getEngine(id).deleteModel((progress) => onProgress(id, progress));
      }
    });
  },
};
