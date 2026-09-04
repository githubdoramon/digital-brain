import { Asset } from 'expo-asset';
import { Platform } from 'react-native';

import GlassesAlertsNative from '@/modules/digital-brain-glasses-alerts/src';
import {
  blinkMentraBlueLed,
  blinkMentraOrangeLed,
  getMentraConnectionStatus,
  setMentraMicState,
  subscribeMentraConnectionState,
  subscribeMentraMicPcm,
  subscribeMentraVideoRecordingStatus,
} from '@/mentraCapture/sdk';
import { appendMentraDebugLog, appendWakeCommandDebugLog } from '@/mentraCapture/debug';
import {
  acceptGlassesCommandPcm,
  cancelGlassesCommandTranscription,
  isGlassesCommandSessionActive,
  observeGlassesAmbientPcm,
  startGlassesCommandTranscription,
  warmGlassesCommandTranscription,
} from '@/mentraCapture/commandTranscription';
import {
  EmbeddingWakeWordDetector,
  OpenWakeWordOnnxBackend,
  type EmbeddingWakeWordModel,
  type OnnxRuntimeLike,
} from '@/wakeWord';

const model = require('@/assets/wake-word/hey-brain-embedding.json') as EmbeddingWakeWordModel;
const MAX_PENDING_PCM_CHUNKS = 24;
const EVALUATION_LOG_INTERVAL_MS = 5_000;

type PauseReason = 'audio_recording' | 'video_recording' | 'connection_lost';
let initialized = false;
let detector: EmbeddingWakeWordDetector | null = null;
let pcmUnsubscribe: (() => void) | null = null;
let connectionUnsubscribe: (() => void) | null = null;
let videoUnsubscribe: (() => void) | null = null;
let pendingPcm: Int16Array[] = [];
let processingPcm = false;
let listenerActive = false;
let listenerActivation: Promise<void> | null = null;
let pauseReasons = new Map<PauseReason, boolean>();
let detectorInitialization: Promise<EmbeddingWakeWordDetector> | null = null;
let lastEvaluationLogAt = 0;

function debug(event: string, payload?: Record<string, unknown>): void {
  void appendMentraDebugLog(event, payload).catch(() => undefined);
  void appendWakeCommandDebugLog(event, payload).catch(() => undefined);
}

function shouldListen(): boolean {
  return Platform.OS === 'android' && pauseReasons.size === 0;
}

function loadOnnxRuntime(): OnnxRuntimeLike {
  // Do not eagerly import the legacy ONNX bridge during app bootstrap. If a
  // native development client is stale or fails to register it, wake-word
  // startup is logged and the rest of the app remains usable.
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  return require('onnxruntime-react-native') as OnnxRuntimeLike;
}

async function loadOnnxAsset(moduleId: number): Promise<string> {
  const asset = Asset.fromModule(moduleId);
  await asset.downloadAsync();
  if (!asset.localUri) throw new Error(`Wake-word model asset is unavailable: ${asset.name}`);
  return asset.localUri;
}

async function getDetector(): Promise<EmbeddingWakeWordDetector> {
  if (detector) return detector;
  if (!detectorInitialization) {
    detectorInitialization = (async () => {
      const startedAt = Date.now();
      const [melPath, embeddingPath] = await Promise.all([
        // Metro exposes packaged ONNX assets as numeric module IDs.
        // eslint-disable-next-line @typescript-eslint/no-require-imports
        loadOnnxAsset(require('@/assets/wake-word/melspectrogram.onnx')),
        // eslint-disable-next-line @typescript-eslint/no-require-imports
        loadOnnxAsset(require('@/assets/wake-word/embedding_model.onnx')),
      ]);
      const backend = await OpenWakeWordOnnxBackend.create(
        loadOnnxRuntime(),
        melPath,
        embeddingPath,
        model.audioConfig.streamHopSamples,
      );
      detector = new EmbeddingWakeWordDetector(model, backend, (evaluation) => {
        const now = Date.now();
        if (!evaluation.passed && now - lastEvaluationLogAt < EVALUATION_LOG_INTERVAL_MS) return;
        lastEvaluationLogAt = now;
        debug('wake_evaluation', {
          score: evaluation.score,
          threshold: evaluation.threshold,
          passed: evaluation.passed,
          consecutive_hits: evaluation.consecutiveHits,
          audio_time_ms: evaluation.audioTimeMs,
        });
      });
      debug('wake_detector_ready', { initialization_ms: Date.now() - startedAt });
      return detector;
    })().catch((error) => {
      detectorInitialization = null;
      throw error;
    });
  }
  return detectorInitialization;
}

function resetDetector(reason: string): void {
  detector?.reset();
  pendingPcm = [];
  debug('wake_detector_reset', { reason });
}

async function processPendingPcm(): Promise<void> {
  if (processingPcm) return;
  processingPcm = true;
  try {
    while (listenerActive && pendingPcm.length > 0) {
      const chunk = pendingPcm.shift();
      if (!chunk) continue;
      const activeDetector = await getDetector();
      const startedAt = Date.now();
      const events = await activeDetector.acceptPcm16(chunk);
      const elapsedMs = Date.now() - startedAt;
      if (elapsedMs > 80 || pendingPcm.length > 4) {
        debug('wake_inference_backlog', {
          inference_ms: elapsedMs,
          pending_chunks: pendingPcm.length,
          samples: chunk.length,
        });
      }
      for (const event of events) {
        const wakeDetectedAt = Date.now();
        debug('wake_detected', {
          score: event.score,
          threshold: event.threshold,
          audio_time_ms: event.audioTimeMs,
          pre_roll_start_audio_time_ms: event.preRollStartAudioTimeMs,
          pre_roll_end_audio_time_ms: event.preRollEndAudioTimeMs,
          pre_roll_samples: event.preRollPcm16.length,
        });
        // Dispatch the visible acknowledgement before creating the command
        // session so nothing on the JavaScript side adds avoidable LED delay.
        void blinkMentraBlueLed().catch((error) =>
          debug('wake_led_failed', {
            error: error instanceof Error ? error.message : String(error),
          }),
        );
        // The detector owns a bounded 1.8-second pre-roll which contains the
        // wake phrase and can include the beginning of a fast command. Keep it
        // verbatim, alongside any PCM that arrived while inference completed,
        // so Whisper and the retained WAV receive the complete utterance.
        const commandInitialChunks = [event.preRollPcm16, ...pendingPcm.splice(0)];
        const initialAudioDurationMs = Math.round(
          (commandInitialChunks.reduce((total, chunk) => total + chunk.length, 0) / 16_000) * 1_000,
        );
        debug('wake_command_audio_buffered', {
          pre_roll_samples: event.preRollPcm16.length,
          initial_audio_duration_ms: initialAudioDurationMs,
          wake_decision_audio_time_ms: event.audioTimeMs,
          wake_pre_roll_start_audio_time_ms: event.preRollStartAudioTimeMs,
          wake_pre_roll_end_audio_time_ms: event.preRollEndAudioTimeMs,
        });
        resetDetector('command_session_started');
        startGlassesCommandTranscription(
          wakeDetectedAt,
          (command) => {
            void blinkMentraOrangeLed().catch((error) =>
              debug('glasses_command_listening_finished_led_failed', {
                command_id: command.commandId,
                error: error instanceof Error ? error.message : String(error),
              }),
            );
          },
          commandInitialChunks,
          {
            wakePhrase: event.modelName.replace(/[-_]+/gu, ' '),
            detectionAudioTimeMs: event.audioTimeMs,
            preRollStartAudioTimeMs: event.preRollStartAudioTimeMs,
            preRollEndAudioTimeMs: event.preRollEndAudioTimeMs,
          },
        );
      }
    }
  } catch (error) {
    debug('wake_inference_failed', {
      error: error instanceof Error ? error.message : String(error),
    });
    resetDetector('inference_failed');
  } finally {
    processingPcm = false;
    if (listenerActive && pendingPcm.length > 0) void processPendingPcm();
  }
}

function copyPcmBytes(pcm: ArrayBuffer | ArrayBufferView): ArrayBufferLike {
  if (ArrayBuffer.isView(pcm)) {
    return pcm.buffer.slice(pcm.byteOffset, pcm.byteOffset + pcm.byteLength);
  }
  return pcm.slice(0);
}

function acceptPcm(pcm: ArrayBuffer | ArrayBufferView): void {
  if (!listenerActive) return;
  const samples = new Int16Array(copyPcmBytes(pcm));
  if (isGlassesCommandSessionActive()) {
    acceptGlassesCommandPcm(samples, (command) => {
      void blinkMentraOrangeLed().catch((error) =>
        debug('glasses_command_listening_finished_led_failed', {
          command_id: command.commandId,
          error: error instanceof Error ? error.message : String(error),
        }),
      );
    });
    return;
  }
  observeGlassesAmbientPcm(samples);
  if (pendingPcm.length >= MAX_PENDING_PCM_CHUNKS) {
    debug('wake_pcm_backlog_dropped', {
      pending_chunks: pendingPcm.length,
      samples: samples.length,
    });
    resetDetector('pcm_backlog');
    return;
  }
  pendingPcm.push(samples);
  void processPendingPcm();
}

async function activateListener(): Promise<void> {
  if (listenerActive || !shouldListen()) return;
  if (listenerActivation) return listenerActivation;
  listenerActivation = (async () => {
    const status = await getMentraConnectionStatus();
    if (!status.connected) {
      pauseReasons.set('connection_lost', false);
      debug('wake_waiting_for_glasses', { state: status.state, fully_booted: status.fullyBooted });
      return;
    }
    pauseReasons.delete('connection_lost');
    if (!shouldListen()) return;
    await GlassesAlertsNative?.startGlassesWakeRuntime();
    try {
      await getDetector();
      if (!shouldListen()) return;
      const unsubscribe = subscribeMentraMicPcm((event) => acceptPcm(event.pcm));
      pcmUnsubscribe = unsubscribe;
      if (!shouldListen()) {
        unsubscribe();
        pcmUnsubscribe = null;
        return;
      }
      await setMentraMicState(true);
      if (!shouldListen() || pcmUnsubscribe !== unsubscribe) return;
      listenerActive = true;
      debug('wake_listener_started', { model: model.name });
      void warmGlassesCommandTranscription().catch(() => undefined);
    } catch (error) {
      pcmUnsubscribe?.();
      pcmUnsubscribe = null;
      await GlassesAlertsNative?.stopGlassesWakeRuntime().catch(() => undefined);
      throw error;
    }
  })();
  try {
    await listenerActivation;
  } finally {
    listenerActivation = null;
  }
}

async function deactivateListener(reason: string, disableMic: boolean): Promise<void> {
  cancelGlassesCommandTranscription(reason);
  if (!listenerActive && !pcmUnsubscribe) return;
  listenerActive = false;
  pcmUnsubscribe?.();
  pcmUnsubscribe = null;
  resetDetector(reason);
  if (disableMic) await setMentraMicState(false).catch(() => undefined);
  debug('wake_listener_stopped', { reason, mic_disabled: disableMic });
}

async function reconcile(reason: string): Promise<void> {
  if (!shouldListen()) {
    await deactivateListener(reason, pauseReasons.get('video_recording') !== true);
    if (pauseReasons.size > 0)
      await GlassesAlertsNative?.stopGlassesWakeRuntime().catch(() => undefined);
    return;
  }
  await activateListener();
}

export async function pauseWakeWordListening(
  reason: 'audio_recording' | 'video_recording',
): Promise<void> {
  pauseReasons.set(reason, reason === 'audio_recording');
  await deactivateListener(reason, reason === 'audio_recording');
  await GlassesAlertsNative?.stopGlassesWakeRuntime().catch(() => undefined);
}

export async function resumeWakeWordListening(
  owner: 'audio_recording' | 'video_recording',
  reason: string,
): Promise<void> {
  pauseReasons.delete(owner);
  await reconcile(reason);
}

export async function initializeWakeWordRuntime(): Promise<void> {
  if (initialized || Platform.OS !== 'android') return;
  initialized = true;
  debug('wake_runtime_initialized', { model: model.name, automatic: true });
  connectionUnsubscribe = subscribeMentraConnectionState((status) => {
    if (status.connected) {
      pauseReasons.delete('connection_lost');
      void reconcile('glasses_ready');
      return;
    }
    pauseReasons.set('connection_lost', false);
    void deactivateListener('glasses_not_ready', true);
  });
  videoUnsubscribe = subscribeMentraVideoRecordingStatus((event) => {
    if (event.status === 'recording_started' || event.data?.recording === true) {
      void pauseWakeWordListening('video_recording');
    }
    if (event.status === 'recording_stopped' || event.status === 'not_recording') {
      void resumeWakeWordListening('video_recording', 'video_recording_stopped');
    }
  });
  await reconcile('startup').catch((error) =>
    debug('wake_runtime_start_failed', {
      error: error instanceof Error ? error.message : String(error),
    }),
  );
}

export async function disposeWakeWordRuntime(): Promise<void> {
  initialized = false;
  connectionUnsubscribe?.();
  connectionUnsubscribe = null;
  videoUnsubscribe?.();
  videoUnsubscribe = null;
  await deactivateListener('runtime_disposed', true);
  await GlassesAlertsNative?.stopGlassesWakeRuntime().catch(() => undefined);
}
