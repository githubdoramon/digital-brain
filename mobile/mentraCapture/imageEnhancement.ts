import AsyncStorage from '@react-native-async-storage/async-storage';
import * as BackgroundTask from 'expo-background-task';
import { File as ExpoFile } from 'expo-file-system';
import * as FileSystem from 'expo-file-system/legacy';
import * as TaskManager from 'expo-task-manager';
import MentraPhotoReceiver, {
  type PhotoReceiverUploadEvent,
} from '@mentra/bluetooth-sdk/photo-receiver';
import { AppState, Platform, type AppStateStatus } from 'react-native';

import { imageUnderstandingCoordinator } from '@/image-understanding/coordinator';
import ImageEnhancementForegroundServiceNative, {
  type ImageEnhancementDeviceHealth,
} from '@/modules/digital-brain-glasses-alerts/src';
import type { ImageUnderstandingRunRecord } from '@/image-understanding/types';
import {
  copyToDigitalBrainStorage,
  DigitalBrainStorageFolder,
  getDigitalBrainStorageBaseUri,
} from '@/storage/digitalBrainStorage';

import { appendMentraDebugLog } from './debug';
import { drainQueuedMoments, enqueueImageMoment } from './moments';
import {
  ensureMentraConnection,
  registerAutomaticPhotoRequest,
  requestGlassesPhoto,
  unregisterAutomaticPhotoRequest,
} from './sdk';

export const GLASSES_IMAGE_ENHANCEMENT_TASK = 'digitalbrain-glasses-image-enhancement';

const LEGACY_BACKGROUND_TASK = 'digitalbrain-glasses-poc2-capture';
const CONFIG_KEY = 'digitalbrain.glasses.image-enhancement.config.v1';
const STATUS_KEY = 'digitalbrain.glasses.image-enhancement.status.v1';
const QUEUE_KEY = 'digitalbrain.glasses.image-enhancement.queue.v1';
const ENHANCEMENT_QUEUE_KEY = 'digitalbrain.glasses.image-enhancement.processing-queue.v1';
const SCHEDULE_STATE_KEY = 'digitalbrain.glasses.image-enhancement.schedule-state.v1';
const LEGACY_CONFIG_KEY = 'digitalbrain.glasses.poc2.config.v1';
const LEGACY_STATUS_KEY = 'digitalbrain.glasses.poc2.status.v1';
const LEGACY_QUEUE_KEY = 'digitalbrain.glasses.poc2.queue.v1';
const LEGACY_SCHEDULE_STATE_KEY = 'digitalbrain.glasses.poc2.schedule-state.v1';
const PRIVATE_ROOT = `${FileSystem.documentDirectory ?? FileSystem.cacheDirectory}Digital Brain/`;
const PRIVATE_IMAGE_DIRECTORY = `${PRIVATE_ROOT}Image Pipeline Temp/`;
const PRIVATE_EXPORT_DIRECTORY = `${PRIVATE_ROOT}Exports/`;
const LEGACY_PRIVATE_DIRECTORY = `${PRIVATE_ROOT}Smart Glasses POC 2/`;
const LEGACY_LOG_FILE_NAME = 'smart-glasses-poc2.jsonl';
const LOG_FILE_NAME = 'image-enhancement-pipeline.jsonl';
const LOG_FILE_URI = `${PRIVATE_EXPORT_DIRECTORY}${LOG_FILE_NAME}`;
const MAX_LOG_BYTES = 2_000_000;
const DEFAULT_INTERVAL_MINUTES = 1;
const MIN_INTERVAL_MINUTES = 1;
const MAX_INTERVAL_MINUTES = 24 * 60;
const MAX_QUEUE_ITEMS = 16;
const SCHEDULER_TICK_MS = 15_000;
const MAX_QUEUE_ATTEMPTS = 4;
const PHOTO_REQUEST_TIMEOUT_MS = 60_000;
const ENHANCEMENT_TIMEOUT_MS = 120_000;

export type ImageEnhancementSchedule = {
  id: string;
  intervalMinutes: number;
  enabled: boolean;
};

export type ImageEnhancementConfig = {
  enabled: boolean;
  intervalMinutes: number;
  schedules: ImageEnhancementSchedule[];
};

export type ImageEnhancementStatus = ImageEnhancementConfig & {
  running: boolean;
  lastCaptureAt: string | null;
  nextCaptureAt: string | null;
  lastError: string | null;
  captureCount: number;
  skippedOverlapCount: number;
  queuedCount: number;
  failedQueueCount: number;
};

type ImageEnhancementCaptureJob = {
  id: string;
  scheduleId: string;
  source: 'timer' | 'background_task' | 'foreground_service' | 'startup' | 'settings';
  requestedAt: string;
  attempts: number;
  nextAttemptAt: string | null;
};

type ImageEnhancementProcessingJob = {
  id: string;
  source: ImageEnhancementCaptureJob['source'];
  capturedAt: string;
  localUri: string;
  bytes: number;
  attempts: number;
  nextAttemptAt: string | null;
};

type ScheduleState = Record<string, { nextRunAt: string | null }>;

type ImageEnhancementLogEntry = {
  timestamp: string;
  event: string;
  source?: 'timer' | 'background_task' | 'foreground_service' | 'startup' | 'settings';
  durationMs?: number;
  bytes?: number;
  mimeType?: string;
  width?: number;
  height?: number;
  shared?: boolean;
  phase?: string;
  capturedAt?: string;
  deviceHealth?: ImageEnhancementDeviceHealth | null;
  pipeline?: {
    evidence: SafeRunSummary;
    enhancement: SafeRunSummary;
  };
  reason?: string;
  error?: string;
};

type SafeRunSummary = {
  engineId: string;
  promptVersion: string;
  modelId: string;
  modelVersion: string;
  computeBackend: string;
  outputValid: boolean;
  error: string | null;
  parseRepairs: string[];
  measurements: ImageUnderstandingRunRecord['measurements'];
  observation: {
    summary: string;
    objects: ImageUnderstandingRunRecord['observation'] extends infer T
      ? T extends { objects: infer O }
        ? O
        : never
      : never;
    peoplePresence: string;
    peopleCountMin: number;
    peopleCountMax: number;
    peopleDetails: string[];
    setting: string | null;
    interpretations: ImageUnderstandingRunRecord['observation'] extends infer T
      ? T extends { interpretations: infer I }
        ? I
        : never
      : never;
    uncertainties: string[];
  } | null;
};

const defaultStatus: ImageEnhancementStatus = {
  enabled: false,
  intervalMinutes: DEFAULT_INTERVAL_MINUTES,
  schedules: [{ id: 'default', intervalMinutes: DEFAULT_INTERVAL_MINUTES, enabled: true }],
  running: false,
  lastCaptureAt: null,
  nextCaptureAt: null,
  lastError: null,
  captureCount: 0,
  skippedOverlapCount: 0,
  queuedCount: 0,
  failedQueueCount: 0,
};

let config: ImageEnhancementConfig = {
  enabled: false,
  intervalMinutes: DEFAULT_INTERVAL_MINUTES,
  schedules: [{ id: 'default', intervalMinutes: DEFAULT_INTERVAL_MINUTES, enabled: true }],
};
let configLoaded: Promise<ImageEnhancementConfig> | null = null;
let status: ImageEnhancementStatus = { ...defaultStatus };
let statusLoaded = false;
let schedulerTimer: ReturnType<typeof setInterval> | null = null;
let captureQueue: ImageEnhancementCaptureJob[] = [];
let queueLoaded: Promise<void> | null = null;
let enhancementQueue: ImageEnhancementProcessingJob[] = [];
let enhancementQueueLoaded: Promise<void> | null = null;
let scheduleState: ScheduleState = {};
let scheduleStateLoaded: Promise<void> | null = null;
let queueDrain: Promise<void> | null = null;
let enhancementDrain: Promise<void> | null = null;
let schedulerDispatch: Promise<void> | null = null;
let activeCaptureJob: ImageEnhancementCaptureJob | null = null;
let nativeOperationInFlight = false;
let stopReceiverWhenIdle = false;
let logWriteChain: Promise<void> = Promise.resolve();
let sharedLogWriteChain: Promise<void> = Promise.resolve();
let storageSyncChain: Promise<void> = Promise.resolve();
let sharedStorageSyncTimer: ReturnType<typeof setTimeout> | null = null;
let schedulerInitialized = false;
let appStateSubscription: { remove: () => void } | null = null;
let foregroundTickSubscription: { remove: () => void } | null = null;
let lastPhotoSyncAt = 0;
let privateStorageMigration: Promise<void> | null = null;
const listeners = new Set<(value: ImageEnhancementStatus) => void>();

async function readMigratedStorageValue(key: string, legacyKey: string): Promise<string | null> {
  const current = await AsyncStorage.getItem(key);
  if (current != null) {
    await AsyncStorage.removeItem(legacyKey).catch(() => undefined);
    return current;
  }
  const legacy = await AsyncStorage.getItem(legacyKey);
  if (legacy != null) {
    await AsyncStorage.setItem(key, legacy);
    await AsyncStorage.removeItem(legacyKey).catch(() => undefined);
  }
  return legacy;
}

const wait = (milliseconds: number) =>
  new Promise<void>((resolve) => setTimeout(resolve, milliseconds));

function safeError(error: unknown): string {
  const message = error instanceof Error ? error.message : String(error);
  return message
    .replace(/(?:file|content|https?):\/\/[^\s"']+/gi, '[redacted-location]')
    .slice(0, 500);
}

function withTimeout<T>(
  operation: Promise<T>,
  timeoutMs: number,
  message: string,
  onTimeout?: () => void,
): Promise<T> {
  return new Promise((resolve, reject) => {
    const timeout = setTimeout(() => {
      reject(new Error(message));
      // Some JSI interruption calls are synchronous. Report the timeout first
      // so a slow native interrupt cannot hold the queue/UI in the running state.
      if (onTimeout) {
        setTimeout(() => {
          try {
            onTimeout();
          } catch {
            // The original timeout remains the actionable error.
          }
        }, 0);
      }
    }, timeoutMs);
    operation.then(
      (value) => {
        clearTimeout(timeout);
        resolve(value);
      },
      (error) => {
        clearTimeout(timeout);
        reject(error);
      },
    );
  });
}

function trackNativeOperation<T>(
  operation: Promise<T>,
  timeoutMs: number,
  message: string,
  onTimeout?: () => void,
  blocksCapture = true,
): Promise<T> {
  if (blocksCapture) nativeOperationInFlight = true;
  const markSettled = () => {
    if (blocksCapture) nativeOperationInFlight = false;
  };
  operation.then(markSettled, markSettled);
  return withTimeout(operation, timeoutMs, message, onTimeout);
}

function clampInterval(value: number): number {
  if (!Number.isFinite(value)) return DEFAULT_INTERVAL_MINUTES;
  return Math.max(MIN_INTERVAL_MINUTES, Math.min(MAX_INTERVAL_MINUTES, Math.round(value)));
}

function defaultSchedule(intervalMinutes = DEFAULT_INTERVAL_MINUTES): ImageEnhancementSchedule {
  return { id: 'default', intervalMinutes, enabled: true };
}

function normalizeSchedules(value: unknown, legacyInterval: number): ImageEnhancementSchedule[] {
  // The pipeline intentionally has one capture interval. Older builds persisted an
  // experimental list of schedules; collapse it to the default interval so
  // those extra schedules cannot continue running invisibly.
  const storedDefault = Array.isArray(value)
    ? value.find(
        (item) =>
          item &&
          typeof item === 'object' &&
          (item as Partial<ImageEnhancementSchedule>).id === 'default',
      )
    : null;
  const interval = storedDefault
    ? clampInterval(Number((storedDefault as Partial<ImageEnhancementSchedule>).intervalMinutes))
    : legacyInterval;
  const enabled = storedDefault
    ? (storedDefault as Partial<ImageEnhancementSchedule>).enabled !== false
    : true;
  return [{ id: 'default', intervalMinutes: interval, enabled }];
}

async function loadConfig(): Promise<ImageEnhancementConfig> {
  if (configLoaded) return configLoaded;
  configLoaded = readMigratedStorageValue(CONFIG_KEY, LEGACY_CONFIG_KEY)
    .then((raw) => {
      if (!raw) return config;
      try {
        const parsed = JSON.parse(raw) as Partial<ImageEnhancementConfig>;
        const legacyInterval = clampInterval(Number(parsed.intervalMinutes));
        const schedules = normalizeSchedules(parsed.schedules, legacyInterval);
        config = {
          enabled: parsed.enabled === true,
          intervalMinutes: schedules[0]?.intervalMinutes ?? DEFAULT_INTERVAL_MINUTES,
          schedules,
        };
      } catch {
        config = {
          enabled: false,
          intervalMinutes: DEFAULT_INTERVAL_MINUTES,
          schedules: [defaultSchedule()],
        };
      }
      return config;
    })
    .catch(() => config);
  return configLoaded;
}

async function persistConfig(): Promise<void> {
  await AsyncStorage.setItem(CONFIG_KEY, JSON.stringify(config));
}

async function loadStatus(): Promise<void> {
  if (statusLoaded) return;
  statusLoaded = true;
  const raw = await readMigratedStorageValue(STATUS_KEY, LEGACY_STATUS_KEY).catch(() => null);
  if (!raw) return;
  try {
    const parsed = JSON.parse(raw) as Partial<ImageEnhancementStatus>;
    status = {
      ...defaultStatus,
      ...parsed,
      enabled: config.enabled,
      intervalMinutes: config.intervalMinutes,
      schedules: config.schedules,
      running: false,
      lastError: typeof parsed.lastError === 'string' ? parsed.lastError : null,
      queuedCount: 0,
      failedQueueCount: 0,
    };
  } catch {
    status = { ...defaultStatus, ...config };
  }
}

async function loadCaptureQueue(): Promise<void> {
  if (queueLoaded) return queueLoaded;
  queueLoaded = readMigratedStorageValue(QUEUE_KEY, LEGACY_QUEUE_KEY)
    .then((raw) => {
      if (!raw) return;
      try {
        const parsed = JSON.parse(raw);
        if (!Array.isArray(parsed)) return;
        captureQueue = parsed
          .filter((item): item is Partial<ImageEnhancementCaptureJob> =>
            Boolean(item && typeof item === 'object'),
          )
          .map(
            (item): ImageEnhancementCaptureJob => ({
              id: typeof item.id === 'string' ? item.id : '',
              scheduleId: typeof item.scheduleId === 'string' ? item.scheduleId : 'default',
              source:
                item.source === 'background_task' ||
                item.source === 'foreground_service' ||
                item.source === 'startup' ||
                item.source === 'settings'
                  ? item.source
                  : 'timer',
              requestedAt:
                typeof item.requestedAt === 'string' ? item.requestedAt : new Date().toISOString(),
              attempts: Number.isFinite(Number(item.attempts))
                ? Math.max(0, Number(item.attempts))
                : 0,
              nextAttemptAt: typeof item.nextAttemptAt === 'string' ? item.nextAttemptAt : null,
            }),
          )
          .filter((item) => item.id && item.scheduleId)
          .slice(0, MAX_QUEUE_ITEMS);
      } catch {
        captureQueue = [];
      }
    })
    .catch(() => undefined);
  await queueLoaded;
}

async function persistCaptureQueue(): Promise<void> {
  await AsyncStorage.setItem(QUEUE_KEY, JSON.stringify(captureQueue));
}

async function loadEnhancementQueue(): Promise<void> {
  if (enhancementQueueLoaded) return enhancementQueueLoaded;
  enhancementQueueLoaded = AsyncStorage.getItem(ENHANCEMENT_QUEUE_KEY)
    .then((raw) => {
      if (!raw) return;
      try {
        const parsed = JSON.parse(raw);
        if (!Array.isArray(parsed)) return;
        enhancementQueue = parsed
          .filter((item): item is Partial<ImageEnhancementProcessingJob> =>
            Boolean(item && typeof item === 'object'),
          )
          .map(
            (item): ImageEnhancementProcessingJob => ({
              id: typeof item.id === 'string' ? item.id : '',
              source:
                item.source === 'background_task' ||
                item.source === 'foreground_service' ||
                item.source === 'startup' ||
                item.source === 'settings'
                  ? item.source
                  : 'timer',
              capturedAt: typeof item.capturedAt === 'string' ? item.capturedAt : '',
              localUri: typeof item.localUri === 'string' ? item.localUri : '',
              bytes: Math.max(0, Number(item.bytes) || 0),
              attempts: Math.max(0, Number(item.attempts) || 0),
              nextAttemptAt: typeof item.nextAttemptAt === 'string' ? item.nextAttemptAt : null,
            }),
          )
          .filter((item) => item.id && item.capturedAt && item.localUri && item.bytes > 0);
      } catch {
        enhancementQueue = [];
      }
    })
    .catch(() => undefined);
  await enhancementQueueLoaded;
}

async function persistEnhancementQueue(): Promise<void> {
  await AsyncStorage.setItem(ENHANCEMENT_QUEUE_KEY, JSON.stringify(enhancementQueue));
}

async function enqueueEnhancementJob(
  job: Omit<ImageEnhancementProcessingJob, 'id' | 'attempts' | 'nextAttemptAt'>,
): Promise<void> {
  await loadEnhancementQueue();
  enhancementQueue.push({
    ...job,
    id: `image-enhancement-process-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    attempts: 0,
    nextAttemptAt: null,
  });
  await persistEnhancementQueue();
  updateQueueStatus();
}

async function loadScheduleState(): Promise<void> {
  if (scheduleStateLoaded) return scheduleStateLoaded;
  scheduleStateLoaded = readMigratedStorageValue(SCHEDULE_STATE_KEY, LEGACY_SCHEDULE_STATE_KEY)
    .then((raw) => {
      if (!raw) return;
      try {
        const parsed = JSON.parse(raw);
        if (!parsed || typeof parsed !== 'object') return;
        scheduleState = Object.fromEntries(
          Object.entries(parsed)
            .filter(([, value]) => value && typeof value === 'object')
            .map(([id, value]) => {
              const nextRunAt = (value as { nextRunAt?: unknown }).nextRunAt;
              return [id, { nextRunAt: typeof nextRunAt === 'string' ? nextRunAt : null }];
            }),
        );
      } catch {
        scheduleState = {};
      }
    })
    .catch(() => undefined);
  await scheduleStateLoaded;
}

async function persistScheduleState(): Promise<void> {
  await AsyncStorage.setItem(SCHEDULE_STATE_KEY, JSON.stringify(scheduleState));
}

function scheduleNextRun(schedule: ImageEnhancementSchedule, from = Date.now()): string {
  return new Date(from + schedule.intervalMinutes * 60_000).toISOString();
}

function updateQueueStatus(): void {
  const nextRunTimes = config.schedules
    .filter((schedule) => schedule.enabled)
    .map((schedule) => scheduleState[schedule.id]?.nextRunAt)
    .filter((value): value is string => Boolean(value));
  const nextQueuedRetry = captureQueue
    .map((job) => job.nextAttemptAt)
    .filter((value): value is string => Boolean(value));
  const nextCaptureAt = [...nextRunTimes, ...nextQueuedRetry].sort()[0] ?? null;
  publish({
    queuedCount: captureQueue.length + enhancementQueue.length,
    nextCaptureAt: config.enabled ? nextCaptureAt : null,
  });
}

function publish(next: Partial<ImageEnhancementStatus>): void {
  status = { ...status, ...next, enabled: config.enabled, intervalMinutes: config.intervalMinutes };
  void AsyncStorage.setItem(STATUS_KEY, JSON.stringify({ ...status, running: false })).catch(
    () => undefined,
  );
  listeners.forEach((listener) => listener({ ...status }));
}

async function log(entry: ImageEnhancementLogEntry): Promise<void> {
  const line = `${JSON.stringify({ ...entry, intervalMinutes: config.intervalMinutes })}\n`;
  await ensurePrivateStorage();
  logWriteChain = logWriteChain
    .catch(() => undefined)
    .then(async () => {
      const file = new ExpoFile(LOG_FILE_URI);
      file.write(line, { append: true });
      if (file.size > MAX_LOG_BYTES) {
        const existing = await file.text();
        file.write(existing.slice(-MAX_LOG_BYTES));
      }
    });
  await logWriteChain;
  // Captures and inference must never wait for Android's document provider.
  // Coalesce shared-folder mirroring instead of rewriting the growing log and
  // rescanning every retained photo for every JSONL line.
  scheduleSharedStorageSync();
}

function scheduleSharedStorageSync(delayMs = 5_000): void {
  if (sharedStorageSyncTimer) return;
  sharedStorageSyncTimer = setTimeout(() => {
    sharedStorageSyncTimer = null;
    void mirrorLogToSharedStorage().catch(async (error) => {
      await appendMentraDebugLog('glasses_image_enhancement_shared_storage_sync_failed', {
        error: safeError(error),
      }).catch(() => undefined);
    });
  }, delayMs);
}

/** Keep a user-visible copy current; the private file remains the fallback. */
async function mirrorLogToSharedStorage(): Promise<void> {
  if (!(await getDigitalBrainStorageBaseUri().catch(() => null))) return;
  const logInfo = await getImageEnhancementLogInfo();
  if (logInfo.exists && logInfo.sizeBytes > 0) {
    sharedLogWriteChain = sharedLogWriteChain
      .catch(() => undefined)
      .then(async () => {
        await copyToDigitalBrainStorage(
          LOG_FILE_URI,
          DigitalBrainStorageFolder.Exports,
          LOG_FILE_NAME,
          'application/x-ndjson',
        );
      });
    try {
      await sharedLogWriteChain;
    } catch (error) {
      await appendMentraDebugLog('glasses_image_enhancement_shared_log_copy_failed', {
        error: safeError(error),
      }).catch(() => undefined);
    }
  }
}

async function syncPrivatePhotosToSharedStorage(): Promise<void> {
  if (!(await getDigitalBrainStorageBaseUri().catch(() => null))) return;
  if (Date.now() - lastPhotoSyncAt < 10_000) return;
  lastPhotoSyncAt = Date.now();
  const files = await FileSystem.readDirectoryAsync(PRIVATE_IMAGE_DIRECTORY).catch(() => []);
  for (const childName of files) {
    const fileName = decodeURIComponent(childName.split('/').pop() ?? '');
    if (!fileName.toLowerCase().endsWith('.jpg')) continue;
    // Expo returns child names (not absolute URIs) for file:// directories.
    // The previous migration passed these bare names to copyAsync, which made
    // SAF create an empty destination before the source lookup failed.
    const sourceUri = childName.startsWith('file://')
      ? childName
      : `${PRIVATE_IMAGE_DIRECTORY}${childName}`;
    try {
      await copyToDigitalBrainStorage(
        sourceUri,
        DigitalBrainStorageFolder.ImagePipelineTemp,
        fileName,
        'image/jpeg',
        { skipIfSameSize: true },
      );
    } catch (error) {
      await appendMentraDebugLog('glasses_image_enhancement_shared_photo_copy_failed', {
        error: safeError(error),
      }).catch(() => undefined);
    }
  }
}

function observationForLog(run: ImageUnderstandingRunRecord): SafeRunSummary['observation'] {
  const observation = run.observation;
  if (!observation) return null;
  return {
    summary: observation.summary,
    // OCR is intentionally excluded from this export. It belongs in the
    // regular observation only and must not become a diagnostic identifier.
    objects: observation.objects,
    peoplePresence: observation.people_presence,
    peopleCountMin: observation.people_count_min,
    peopleCountMax: observation.people_count_max,
    peopleDetails: observation.people_details,
    setting: observation.setting,
    interpretations: observation.interpretations,
    uncertainties: observation.uncertainties,
  } as SafeRunSummary['observation'];
}

function runForLog(run: ImageUnderstandingRunRecord): SafeRunSummary {
  return {
    engineId: run.runtime.engineId,
    promptVersion: run.promptVersion,
    modelId: run.runtime.modelId,
    modelVersion: run.runtime.modelVersion,
    computeBackend: run.runtime.computeBackend,
    outputValid: run.outputValid,
    error: run.error,
    parseRepairs: run.parseRepairs,
    measurements: run.measurements,
    observation: observationForLog(run),
  };
}

function imageDimensionsFromRun(run: ImageUnderstandingRunRecord): {
  width?: number;
  height?: number;
} {
  if (!run.rawOutput) return {};
  try {
    const parsed = JSON.parse(run.rawOutput) as {
      native_evidence?: { imageWidth?: unknown; imageHeight?: unknown };
    };
    const width = parsed.native_evidence?.imageWidth;
    const height = parsed.native_evidence?.imageHeight;
    return {
      ...(typeof width === 'number' && width > 0 ? { width } : {}),
      ...(typeof height === 'number' && height > 0 ? { height } : {}),
    };
  } catch {
    return {};
  }
}

async function migrateLegacyPrivateStorage(): Promise<void> {
  if (privateStorageMigration) return privateStorageMigration;
  privateStorageMigration = (async () => {
    await Promise.all([
      FileSystem.makeDirectoryAsync(PRIVATE_IMAGE_DIRECTORY, { intermediates: true }),
      FileSystem.makeDirectoryAsync(PRIVATE_EXPORT_DIRECTORY, { intermediates: true }),
    ]);
    const legacyFiles = await FileSystem.readDirectoryAsync(LEGACY_PRIVATE_DIRECTORY).catch(
      () => [],
    );
    for (const childName of legacyFiles) {
      const fileName = decodeURIComponent(childName.split('/').pop() ?? '');
      const sourceUri = childName.startsWith('file://')
        ? childName
        : `${LEGACY_PRIVATE_DIRECTORY}${childName}`;
      if (fileName === LEGACY_LOG_FILE_NAME) {
        const legacyText = await FileSystem.readAsStringAsync(sourceUri).catch(() => '');
        if (legacyText) {
          const currentText = await FileSystem.readAsStringAsync(LOG_FILE_URI).catch(() => '');
          await FileSystem.writeAsStringAsync(
            LOG_FILE_URI,
            `${legacyText}${currentText}`.slice(-MAX_LOG_BYTES),
          );
        }
        await FileSystem.deleteAsync(sourceUri, { idempotent: true });
      } else if (fileName.toLowerCase().endsWith('.jpg')) {
        const targetUri = `${PRIVATE_IMAGE_DIRECTORY}${fileName}`;
        const target = await FileSystem.getInfoAsync(targetUri);
        if (!target.exists) {
          await FileSystem.moveAsync({ from: sourceUri, to: targetUri });
        } else {
          const source = await FileSystem.getInfoAsync(sourceUri);
          const sameSize =
            'size' in source &&
            'size' in target &&
            typeof source.size === 'number' &&
            source.size > 0 &&
            source.size === target.size;
          if (sameSize) {
            await FileSystem.deleteAsync(sourceUri, { idempotent: true });
          } else {
            const migratedTarget = `${PRIVATE_IMAGE_DIRECTORY}legacy-${Date.now()}-${fileName}`;
            await FileSystem.moveAsync({ from: sourceUri, to: migratedTarget });
          }
        }
      }
    }
    const remaining = await FileSystem.readDirectoryAsync(LEGACY_PRIVATE_DIRECTORY).catch(() => []);
    if (remaining.length === 0) {
      await FileSystem.deleteAsync(LEGACY_PRIVATE_DIRECTORY, { idempotent: true });
    }
  })();
  return privateStorageMigration;
}

async function ensurePrivateStorage(): Promise<void> {
  await migrateLegacyPrivateStorage();
}

function privatePhotoUri(timestamp: string): string {
  const safe = timestamp.replace(/[^0-9]/g, '').slice(0, 17);
  return `${PRIVATE_IMAGE_DIRECTORY}capture-${safe || Date.now()}.jpg`;
}

async function copyReceivedPhoto(
  event: PhotoReceiverUploadEvent,
  capturedAt: string,
): Promise<{
  uri: string;
  bytes: number;
}> {
  await ensurePrivateStorage();
  const source = event.fileUri;
  const target = privatePhotoUri(capturedAt);
  await FileSystem.copyAsync({ from: source, to: target });
  const info = await FileSystem.getInfoAsync(target);
  if (!info.exists || !('size' in info) || !info.size)
    throw new Error('Received photo copy is empty');
  if (event.byteCount > 0 && info.size !== event.byteCount) {
    throw new Error(`Received photo size mismatch (${info.size}/${event.byteCount})`);
  }

  return { uri: target, bytes: info.size };
}

function queuePhotoSharedCopy(localUri: string, capturedAt: string, bytes: number): void {
  const fileName = `capture-${capturedAt.replace(/[:.]/g, '-')}.jpg`;
  const copy = storageSyncChain
    .catch(() => undefined)
    .then(async () => {
      if (!(await getDigitalBrainStorageBaseUri().catch(() => null))) return;
      const startedAt = Date.now();
      try {
        await copyToDigitalBrainStorage(
          localUri,
          DigitalBrainStorageFolder.ImagePipelineTemp,
          fileName,
          'image/jpeg',
          { skipIfSameSize: true },
        );
        lastPhotoSyncAt = Date.now();
        await log({
          timestamp: new Date().toISOString(),
          event: 'storage_shared_copy_completed',
          capturedAt,
          bytes,
          durationMs: Date.now() - startedAt,
          shared: true,
        });
      } catch (error) {
        await log({
          timestamp: new Date().toISOString(),
          event: 'storage_shared_copy_failed',
          capturedAt,
          durationMs: Date.now() - startedAt,
          error: safeError(error),
        });
      }
    });
  storageSyncChain = copy;
  void copy;
}

async function readDeviceHealth(): Promise<ImageEnhancementDeviceHealth | null> {
  if (Platform.OS !== 'android' || !ImageEnhancementForegroundServiceNative) return null;
  return ImageEnhancementForegroundServiceNative.getImageEnhancementDeviceHealth().catch(
    () => null,
  );
}

async function logPhase(
  phase: string,
  startedAt: number,
  source: ImageEnhancementCaptureJob['source'],
  capturedAt: string,
  extra: Partial<ImageEnhancementLogEntry> = {},
): Promise<void> {
  await log({
    timestamp: new Date().toISOString(),
    event: 'capture_phase_completed',
    source,
    capturedAt,
    phase,
    durationMs: Date.now() - startedAt,
    ...extra,
  });
}

function waitForPhotoUpload(requestId: string): Promise<PhotoReceiverUploadEvent> {
  return new Promise((resolve, reject) => {
    let settled = false;
    const subscription = MentraPhotoReceiver.addListener('photoUpload', (event) => {
      if (event.requestId !== requestId || settled) return;
      settled = true;
      clearTimeout(timeout);
      subscription.remove();
      resolve(event);
    });
    const timeout = setTimeout(() => {
      if (settled) return;
      settled = true;
      subscription.remove();
      reject(new Error('Timed out waiting for the glasses photo receiver'));
    }, 45_000);
  });
}

async function ensurePhotoReceiver(): Promise<{ uploadUrl: string }> {
  if (!(await MentraPhotoReceiver.isSupported())) {
    throw new Error('The local glasses photo receiver is unavailable in this build.');
  }
  const result = await MentraPhotoReceiver.startPhotoReceiver();
  return { uploadUrl: result.uploadUrl };
}

async function captureOnce(job: ImageEnhancementCaptureJob): Promise<boolean> {
  const source = job.source;
  const startedAt = Date.now();
  const capturedAt = new Date().toISOString();
  const requestId = `image-enhancement-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
  let receivedFileUri: string | null = null;
  registerAutomaticPhotoRequest(requestId);
  publish({ running: true, lastError: null, nextCaptureAt: null });
  await log({
    timestamp: capturedAt,
    event: 'capture_started',
    source,
    deviceHealth: await readDeviceHealth(),
  });
  try {
    let phaseStartedAt = Date.now();
    const connected = await ensureMentraConnection({ applyCaptureDefaults: false });
    if (!connected) throw new Error('No paired Mentra Live is available');
    await logPhase('connection', phaseStartedAt, source, capturedAt);

    phaseStartedAt = Date.now();
    const receiver = await ensurePhotoReceiver();
    await logPhase('photo_receiver_start', phaseStartedAt, source, capturedAt);

    const deliveryStartedAt = Date.now();
    const upload = waitForPhotoUpload(requestId);
    // `auto` attempts direct glasses Wi-Fi delivery and lets the SDK use its
    // phone-relayed BLE path when the direct upload cannot reach this receiver.
    // The BLE relay rewrites this receiver to loopback inside the SDK, so it
    // remains valid even when the phone is using cellular data.
    void upload.catch(() => undefined);
    phaseStartedAt = Date.now();
    await trackNativeOperation(
      requestGlassesPhoto({
        requestId,
        size: 'high',
        mode: 'photo',
        transferMethod: 'auto',
        webhookUrl: receiver.uploadUrl,
        authToken: null,
        compress: 'medium',
        save: false,
        sound: false,
      }),
      PHOTO_REQUEST_TIMEOUT_MS,
      'Timed out waiting for the glasses camera request to complete',
    );
    const requestCompletedAt = Date.now();
    await logPhase('photo_request', phaseStartedAt, source, capturedAt);

    const event = await upload;
    const deliveryCompletedAt = Date.now();
    await logPhase('photo_delivery_total', deliveryCompletedAt, source, capturedAt, {
      bytes: event.byteCount,
      durationMs: deliveryCompletedAt - deliveryStartedAt,
    });
    await logPhase('photo_transfer_after_request', deliveryCompletedAt, source, capturedAt, {
      bytes: event.byteCount,
      durationMs: Math.max(0, deliveryCompletedAt - requestCompletedAt),
    });
    receivedFileUri = event.fileUri;

    phaseStartedAt = Date.now();
    const local = await copyReceivedPhoto(event, capturedAt);
    await logPhase('private_file_copy', phaseStartedAt, source, capturedAt, {
      bytes: local.bytes,
    });
    await log({
      timestamp: new Date().toISOString(),
      event: 'photo_received',
      source,
      capturedAt,
      bytes: local.bytes,
      mimeType: 'image/jpeg',
      shared: false,
    });
    queuePhotoSharedCopy(local.uri, capturedAt, local.bytes);
    // Capture cadence must not wait for VLM inference. The photo is now durable,
    // so process it sequentially from a separate queue while the next minute's
    // glasses capture can proceed.
    await enqueueEnhancementJob({ source, capturedAt, localUri: local.uri, bytes: local.bytes });
    void drainEnhancementQueue();
    publish({
      running: false,
      lastCaptureAt: capturedAt,
      nextCaptureAt: null,
      captureCount: status.captureCount + 1,
      lastError: null,
    });
    // Keep the app-private copy even when the shared copy succeeds. Pipeline
    // inputs remain available for inspection until the user explicitly clears
    // the temporary image folder.
    await FileSystem.deleteAsync(receivedFileUri, { idempotent: true });
    return true;
  } catch (error) {
    const message = safeError(error);
    await log({
      timestamp: new Date().toISOString(),
      event: 'capture_failed',
      source,
      durationMs: Date.now() - startedAt,
      error: message,
      deviceHealth: await readDeviceHealth(),
    });
    publish({ running: false, lastError: message, nextCaptureAt: null });
    if (receivedFileUri) await FileSystem.deleteAsync(receivedFileUri, { idempotent: true });
    return false;
  } finally {
    unregisterAutomaticPhotoRequest(requestId);
    if (!config.enabled && stopReceiverWhenIdle) {
      stopReceiverWhenIdle = false;
      await MentraPhotoReceiver.stopPhotoReceiver().catch(() => undefined);
    }
  }
}

async function processEnhancementJob(job: ImageEnhancementProcessingJob): Promise<boolean> {
  const startedAt = Date.now();
  publish({ running: true, lastError: null });
  try {
    const sourceInfo = await FileSystem.getInfoAsync(job.localUri);
    if (!sourceInfo.exists || !('size' in sourceInfo) || !sourceInfo.size) {
      throw new Error('The retained automatic photo is missing or empty');
    }
    const pipelineStartedAt = Date.now();
    const pipeline = await trackNativeOperation(
      imageUnderstandingCoordinator.runPipeline(job.localUri, () => undefined),
      ENHANCEMENT_TIMEOUT_MS,
      'Timed out while enhancing the automatic glasses photo',
      () => imageUnderstandingCoordinator.interruptActive(),
      false,
    );
    await logPhase('image_understanding_pipeline', pipelineStartedAt, job.source, job.capturedAt);
    const dimensions = imageDimensionsFromRun(pipeline.evidenceRun);
    await log({
      timestamp: new Date().toISOString(),
      event: 'enhancement_completed',
      source: job.source,
      capturedAt: job.capturedAt,
      bytes: job.bytes,
      ...dimensions,
      durationMs: Date.now() - startedAt,
      pipeline: {
        evidence: runForLog(pipeline.evidenceRun),
        enhancement: runForLog(pipeline.finalRun),
      },
      deviceHealth: await readDeviceHealth(),
    });
    if (!pipeline.finalRun.observation) {
      throw new Error('The image pipeline produced no canonical observation');
    }
    await enqueueImageMoment(job.capturedAt, pipeline.finalRun.observation);
    await log({
      timestamp: new Date().toISOString(),
      event: 'moment_queued',
      source: job.source,
      capturedAt: job.capturedAt,
    });
    void drainQueuedMoments('automatic_capture').then((delivery) =>
      log({
        timestamp: new Date().toISOString(),
        event: 'moment_delivery_completed',
        source: job.source,
        capturedAt: job.capturedAt,
        reason: delivery.deferredReason ?? `accepted:${delivery.acceptedCount};rejected:${delivery.rejectedCount};pending:${delivery.pendingCount}`,
      }),
    );
    publish({ running: false, lastError: null });
    return true;
  } catch (error) {
    const message = safeError(error);
    await log({
      timestamp: new Date().toISOString(),
      event: 'enhancement_failed',
      source: job.source,
      capturedAt: job.capturedAt,
      bytes: job.bytes,
      durationMs: Date.now() - startedAt,
      error: message,
      deviceHealth: await readDeviceHealth(),
    });
    publish({ running: false, lastError: message });
    return false;
  }
}

async function drainEnhancementQueue(): Promise<void> {
  if (enhancementDrain) return enhancementDrain;
  enhancementDrain = (async () => {
    await loadEnhancementQueue();
    while (config.enabled && enhancementQueue.length > 0) {
      const now = Date.now();
      const nextIndex = enhancementQueue.findIndex(
        (job) => !job.nextAttemptAt || Date.parse(job.nextAttemptAt) <= now,
      );
      if (nextIndex < 0) break;
      const job = { ...enhancementQueue[nextIndex], attempts: enhancementQueue[nextIndex].attempts + 1 };
      enhancementQueue[nextIndex] = job;
      await persistEnhancementQueue();
      updateQueueStatus();
      const success = await processEnhancementJob(job);
      const currentIndex = enhancementQueue.findIndex((item) => item.id === job.id);
      if (success) {
        if (currentIndex >= 0) enhancementQueue.splice(currentIndex, 1);
      } else if (job.attempts < MAX_QUEUE_ATTEMPTS && currentIndex >= 0) {
        enhancementQueue[currentIndex] = {
          ...job,
          nextAttemptAt: new Date(Date.now() + Math.min(15 * 60_000, 30_000 * 2 ** job.attempts)).toISOString(),
        };
      } else if (currentIndex >= 0) {
        // Keep the retained image, but stop retrying a persistently bad input.
        enhancementQueue.splice(currentIndex, 1);
        status = { ...status, failedQueueCount: status.failedQueueCount + 1 };
      }
      await persistEnhancementQueue();
      updateQueueStatus();
    }
  })().finally(() => {
    enhancementDrain = null;
  });
  await enhancementDrain;
}

function hasPendingSchedule(scheduleId: string): boolean {
  return captureQueue.some(
    (job) => job.scheduleId === scheduleId && job.id !== activeCaptureJob?.id,
  );
}

async function enqueueDueJobs(source: ImageEnhancementCaptureJob['source']): Promise<void> {
  await loadConfig();
  await loadStatus();
  await loadEnhancementQueue();
  await loadCaptureQueue();
  await loadScheduleState();
  if (!config.enabled) return;

  const now = Date.now();
  let queueChanged = false;
  let stateChanged = false;
  for (const schedule of config.schedules) {
    if (!schedule.enabled) continue;
    const state = (scheduleState[schedule.id] ??= { nextRunAt: null });
    let nextRunAt = state.nextRunAt ? Date.parse(state.nextRunAt) : NaN;
    if (!Number.isFinite(nextRunAt)) nextRunAt = now + schedule.intervalMinutes * 60_000;
    if (nextRunAt > now) {
      const normalizedNextRunAt = new Date(nextRunAt).toISOString();
      if (state.nextRunAt !== normalizedNextRunAt) {
        state.nextRunAt = normalizedNextRunAt;
        stateChanged = true;
      }
      continue;
    }

    if (!hasPendingSchedule(schedule.id)) {
      captureQueue.push({
        id: `image-enhancement-job-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
        scheduleId: schedule.id,
        source,
        requestedAt: new Date(now).toISOString(),
        attempts: 0,
        nextAttemptAt: null,
      });
      queueChanged = true;
      await log({
        timestamp: new Date().toISOString(),
        event: 'capture_queued',
        source,
        reason: `schedule:${schedule.id}`,
      });
    } else {
      status = { ...status, skippedOverlapCount: status.skippedOverlapCount + 1 };
    }
    // Coalesce missed intervals to one pending job per schedule. The next tick
    // is always measured from the current wall clock, not from the old due time.
    state.nextRunAt = scheduleNextRun(schedule, now);
    stateChanged = true;
  }
  if (queueChanged) await persistCaptureQueue();
  if (stateChanged) await persistScheduleState();
  if (queueChanged || stateChanged) updateQueueStatus();
}

async function drainCaptureQueue(): Promise<void> {
  if (queueDrain) return queueDrain;
  queueDrain = (async () => {
    await loadCaptureQueue();
    while (config.enabled && captureQueue.length > 0) {
      const now = Date.now();
      const readyIndex = captureQueue.findIndex(
        (job) => !job.nextAttemptAt || Date.parse(job.nextAttemptAt) <= now,
      );
      if (readyIndex < 0) break;
      const job = {
        ...captureQueue[readyIndex],
        attempts: captureQueue[readyIndex].attempts + 1,
      };
      captureQueue[readyIndex] = job;
      activeCaptureJob = job;
      await persistCaptureQueue();
      updateQueueStatus();
      const success = await captureOnce(job);
      activeCaptureJob = null;
      const currentIndex = captureQueue.findIndex((item) => item.id === job.id);
      if (success) {
        if (currentIndex >= 0) captureQueue.splice(currentIndex, 1);
      } else if (config.enabled && job.attempts < MAX_QUEUE_ATTEMPTS) {
        const attempts = job.attempts;
        const retry = {
          ...job,
          attempts,
          nextAttemptAt: new Date(
            Date.now() + Math.min(15 * 60_000, 15_000 * 2 ** attempts),
          ).toISOString(),
        };
        if (currentIndex >= 0) {
          captureQueue.splice(currentIndex, 1);
          captureQueue.push(retry);
        }
        await log({
          timestamp: new Date().toISOString(),
          event: 'capture_requeued',
          source: job.source,
          reason: `attempt:${attempts}`,
        });
      } else if (!success) {
        if (currentIndex >= 0) captureQueue.splice(currentIndex, 1);
        status = { ...status, failedQueueCount: status.failedQueueCount + 1 };
        await log({
          timestamp: new Date().toISOString(),
          event: 'capture_queue_exhausted',
          source: job.source,
          reason: `attempts:${job.attempts}`,
        });
      }
      await persistCaptureQueue();
      updateQueueStatus();
      // A timeout only detaches the JS wait; the native camera/model operation
      // may still be unwinding. Never start the next queued capture alongside it.
      const nativeIdleDeadline = Date.now() + PHOTO_REQUEST_TIMEOUT_MS;
      while (nativeOperationInFlight && Date.now() < nativeIdleDeadline) await wait(250);
      if (nativeOperationInFlight) break;
    }
  })().finally(() => {
    queueDrain = null;
  });
  await queueDrain;
}

async function scheduleAndDrain(source: ImageEnhancementCaptureJob['source']): Promise<void> {
  if (schedulerDispatch) return schedulerDispatch;
  schedulerDispatch = (async () => {
    await enqueueDueJobs(source);
    await drainCaptureQueue();
    void drainEnhancementQueue();
    // A queued moment may have failed while the app was offline. Revisit it on
    // every scheduler/foreground opportunity, even when no new photo is due.
    await drainQueuedMoments(`scheduler_${source}`);
  })().finally(() => {
    schedulerDispatch = null;
  });
  await schedulerDispatch;
}

function startTimer(): void {
  if (schedulerTimer) clearInterval(schedulerTimer);
  if (!config.enabled || !config.schedules.some((schedule) => schedule.enabled)) return;
  schedulerTimer = setInterval(() => void scheduleAndDrain('timer'), SCHEDULER_TICK_MS);
}

function subscribeToForegroundCatchUp(): void {
  if (appStateSubscription) return;
  let previousState: AppStateStatus = AppState.currentState;
  appStateSubscription = AppState.addEventListener('change', (nextState) => {
    const becameActive = nextState === 'active' && previousState !== 'active';
    previousState = nextState;
    if (becameActive && config.enabled) void scheduleAndDrain('startup');
  });
}

function subscribeToNativeForegroundTicks(): void {
  if (
    foregroundTickSubscription ||
    Platform.OS !== 'android' ||
    !ImageEnhancementForegroundServiceNative
  ) {
    return;
  }
  foregroundTickSubscription = ImageEnhancementForegroundServiceNative.addListener(
    'onImageEnhancementForegroundTick',
    () => {
      if (!config.enabled) return;
      void log({
        timestamp: new Date().toISOString(),
        event: 'foreground_service_tick',
        source: 'foreground_service',
      });
      void scheduleAndDrain('foreground_service');
    },
  );
}

async function registerBackgroundTask(): Promise<void> {
  if (!(await TaskManager.isTaskRegisteredAsync(GLASSES_IMAGE_ENHANCEMENT_TASK))) {
    // Android WorkManager enforces a 15-minute minimum. The in-process timer
    // handles the configured cadence while the app process is alive; this task
    // is only a deferred catch-up/restart opportunity.
    await BackgroundTask.registerTaskAsync(GLASSES_IMAGE_ENHANCEMENT_TASK, { minimumInterval: 15 });
  }
}

async function unregisterBackgroundTask(): Promise<void> {
  if (await TaskManager.isTaskRegisteredAsync(GLASSES_IMAGE_ENHANCEMENT_TASK)) {
    await BackgroundTask.unregisterTaskAsync(GLASSES_IMAGE_ENHANCEMENT_TASK).catch(() => undefined);
  }
}

async function unregisterLegacyBackgroundTask(): Promise<void> {
  if (await TaskManager.isTaskRegisteredAsync(LEGACY_BACKGROUND_TASK).catch(() => false)) {
    await BackgroundTask.unregisterTaskAsync(LEGACY_BACKGROUND_TASK).catch(() => undefined);
  }
}

async function syncImageEnhancementForegroundService(
  enabled: boolean,
  source: 'startup' | 'settings' = 'settings',
): Promise<void> {
  if (Platform.OS !== 'android') return;
  try {
    if (!ImageEnhancementForegroundServiceNative) {
      throw new Error('Image enhancement foreground service is unavailable in this native build');
    }
    const activeSchedules = config.schedules.filter((schedule) => schedule.enabled);
    const serviceActive = enabled && activeSchedules.length > 0;
    if (serviceActive) {
      const minimumInterval = Math.min(
        ...activeSchedules.map((schedule) => schedule.intervalMinutes),
        config.intervalMinutes,
      );
      await ImageEnhancementForegroundServiceNative.startImageEnhancementForegroundService(
        minimumInterval,
        activeSchedules.length,
      );
    } else {
      await ImageEnhancementForegroundServiceNative.stopImageEnhancementForegroundService();
    }
    await log({
      timestamp: new Date().toISOString(),
      event: serviceActive ? 'foreground_service_started' : 'foreground_service_stopped',
      source,
    });
  } catch (error) {
    const message = safeError(error);
    await appendMentraDebugLog('glasses_image_enhancement_foreground_service_failed', {
      enabled,
      error: message,
    }).catch(() => undefined);
    await log({
      timestamp: new Date().toISOString(),
      event: 'foreground_service_failed',
      source,
      error: message,
    }).catch(() => undefined);
  }
}

if (!TaskManager.isTaskDefined(GLASSES_IMAGE_ENHANCEMENT_TASK)) {
  TaskManager.defineTask(GLASSES_IMAGE_ENHANCEMENT_TASK, async () => {
    try {
      await scheduleAndDrain('background_task');
      return status.lastError
        ? BackgroundTask.BackgroundTaskResult.Failed
        : BackgroundTask.BackgroundTaskResult.Success;
    } catch {
      return BackgroundTask.BackgroundTaskResult.Failed;
    }
  });
}

export async function initializeImageEnhancement(): Promise<void> {
  if (schedulerInitialized) {
    await syncImageEnhancementStorage();
    return;
  }
  schedulerInitialized = true;
  await unregisterLegacyBackgroundTask();
  await ensurePrivateStorage();
  await loadConfig();
  await loadStatus();
  // Reconcile captures and the diagnostic log from an earlier run even when
  // the toggle is now off; this recovers files after a folder is selected or
  // after an older build used the app-private fallback.
  await syncImageEnhancementStorage();
  void drainQueuedMoments('startup');
  if (!config.enabled) return;
  subscribeToNativeForegroundTicks();
  await syncImageEnhancementForegroundService(true, 'startup');
  await registerBackgroundTask().catch(async (error) => {
    await log({
      timestamp: new Date().toISOString(),
      event: 'background_registration_failed',
      source: 'startup',
      error: safeError(error),
    });
  });
  await scheduleAndDrain('startup');
  void drainEnhancementQueue();
  updateQueueStatus();
  startTimer();
  subscribeToForegroundCatchUp();
  await log({
    timestamp: new Date().toISOString(),
    event: 'capture_scheduler_started',
    source: 'startup',
  });
}

export async function getImageEnhancementConfig(): Promise<ImageEnhancementConfig> {
  await loadConfig();
  return { ...config };
}

export function getImageEnhancementStatus(): ImageEnhancementStatus {
  return { ...status, ...config };
}

export function subscribeImageEnhancementStatus(
  listener: (value: ImageEnhancementStatus) => void,
): () => void {
  listeners.add(listener);
  listener(getImageEnhancementStatus());
  return () => listeners.delete(listener);
}

export async function setImageEnhancementEnabled(enabled: boolean): Promise<void> {
  await loadConfig();
  await loadStatus();
  await loadCaptureQueue();
  await loadEnhancementQueue();
  config = { ...config, enabled };
  await loadScheduleState();
  if (enabled) {
    void drainQueuedMoments('enabled');
    for (const schedule of config.schedules) {
      if (schedule.enabled) scheduleState[schedule.id] = { nextRunAt: scheduleNextRun(schedule) };
    }
    await persistScheduleState();
  } else {
    captureQueue = [];
    await persistCaptureQueue();
  }
  await persistConfig();
  publish({
    enabled,
    running: enabled ? status.running : false,
    nextCaptureAt: null,
    queuedCount: captureQueue.length,
    lastError: null,
  });
  await log({
    timestamp: new Date().toISOString(),
    event: enabled ? 'capture_enabled' : 'capture_disabled',
    source: 'settings',
  });
  if (enabled) {
    subscribeToNativeForegroundTicks();
    await syncImageEnhancementForegroundService(true);
    await registerBackgroundTask();
    await scheduleAndDrain('settings');
    void drainEnhancementQueue();
    startTimer();
    subscribeToForegroundCatchUp();
  } else {
    await syncImageEnhancementForegroundService(false);
    if (activeCaptureJob) stopReceiverWhenIdle = true;
    await unregisterBackgroundTask();
    if (!activeCaptureJob && (await MentraPhotoReceiver.isSupported().catch(() => false))) {
      await MentraPhotoReceiver.stopPhotoReceiver().catch(() => undefined);
    }
    if (schedulerTimer) clearInterval(schedulerTimer);
    schedulerTimer = null;
  }
}

export async function setImageEnhancementIntervalMinutes(value: number): Promise<number> {
  await loadConfig();
  await loadStatus();
  const intervalMinutes = clampInterval(value);
  const schedules = config.schedules.map((schedule) =>
    schedule.id === 'default' ? { ...schedule, intervalMinutes } : schedule,
  );
  config = { ...config, intervalMinutes, schedules };
  await loadScheduleState();
  scheduleState.default = { nextRunAt: scheduleNextRun(defaultSchedule(intervalMinutes)) };
  await persistScheduleState();
  await persistConfig();
  publish({
    intervalMinutes,
    schedules,
    nextCaptureAt: null,
  });
  await log({
    timestamp: new Date().toISOString(),
    event: 'capture_interval_changed',
    source: 'settings',
  });
  if (config.enabled) {
    await syncImageEnhancementForegroundService(true);
    await registerBackgroundTask();
    startTimer();
    await scheduleAndDrain('settings');
  }
  return intervalMinutes;
}

export function getImageEnhancementLogFileName(): string {
  return LOG_FILE_NAME;
}

export async function readImageEnhancementLog(): Promise<string> {
  await ensurePrivateStorage();
  return FileSystem.readAsStringAsync(LOG_FILE_URI).catch(() => '');
}

export async function getImageEnhancementLogInfo(): Promise<{
  exists: boolean;
  sizeBytes: number;
}> {
  await ensurePrivateStorage();
  const info = await FileSystem.getInfoAsync(LOG_FILE_URI);
  return { exists: info.exists, sizeBytes: info.exists && 'size' in info ? (info.size ?? 0) : 0 };
}

export async function clearImageEnhancementLog(): Promise<void> {
  await ensurePrivateStorage();
  await FileSystem.deleteAsync(LOG_FILE_URI, { idempotent: true });
}

/** Retry durable storage for retained pipeline inputs and diagnostics. */
export async function syncImageEnhancementStorage(): Promise<void> {
  await ensurePrivateStorage();
  if (sharedStorageSyncTimer) {
    clearTimeout(sharedStorageSyncTimer);
    sharedStorageSyncTimer = null;
  }
  const sync = storageSyncChain
    .catch(() => undefined)
    .then(async () => {
      lastPhotoSyncAt = 0;
      await mirrorLogToSharedStorage();
      await syncPrivatePhotosToSharedStorage();
    });
  storageSyncChain = sync;
  await sync;
}

void appendMentraDebugLog('glasses_image_enhancement_module_loaded').catch(() => undefined);
