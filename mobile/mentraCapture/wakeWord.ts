import { Asset } from 'expo-asset';
import { Platform } from 'react-native';

import GlassesAlertsNative from '@/modules/digital-brain-glasses-alerts/src';
import {
  blinkMentraBlueLed,
  blinkMentraRedLed,
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
import { dispatchGlassesCommand } from '@/mentraCapture/glassesCommandAgent';
import {
  OpenWakeWordOnnxBackend,
  V8TwoStageWakeWordDetector,
  type EmbeddingWakeWordModel,
  type OnnxRuntimeLike,
} from '@/wakeWord';

const model = require('@/assets/wake-word/hey-brain-v8.json') as EmbeddingWakeWordModel;
const MAX_PENDING_PCM_SAMPLES = 8 * 16_000;

type PauseReason =
  | 'audio_recording'
  | 'video_recording'
  | 'glasses_command'
  | 'connection_lost'
  | 'firmware_update';
let initialized = false;
let detector: V8TwoStageWakeWordDetector | null = null;
let pcmUnsubscribe: (() => void) | null = null;
let connectionUnsubscribe: (() => void) | null = null;
let videoUnsubscribe: (() => void) | null = null;
let pendingPcm: Int16Array[] = [];
let pendingPcmSamples = 0;
let processingPcm = false;
let detectorGeneration = 0;
let listenerActive = false;
let listenerActivation: Promise<void> | null = null;
let pauseReasons = new Map<PauseReason, boolean>();
let detectorInitialization: Promise<V8TwoStageWakeWordDetector> | null = null;
let wakeDebugTimer: ReturnType<typeof setInterval> | null = null;
let lastWakeError: string | null = null;
let lastWakeStep = 'not_initialized';
let pcmCallbacksTotal = 0;
let pcmSamplesTotal = 0;
let pcmSamplesSinceSnapshot = 0;
let pcmSquaredAmplitudeSinceSnapshot = 0;
let pcmPeakSinceSnapshot = 0;
let lastPcmAt: number | null = null;
let firstPcmForListener = false;

function debug(event: string, payload?: Record<string, unknown>): void {
  void appendMentraDebugLog(event, payload).catch(() => undefined);
  void appendWakeCommandDebugLog(event, payload).catch(() => undefined);
}

function wakeErrorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

/** A manual snapshot also proves the diagnostic writer is still working after clear. */
export async function recordWakeDebugSnapshot(source = 'manual'): Promise<Record<string, unknown>> {
  const intervalSamples = pcmSamplesSinceSnapshot;
  const intervalSquaredAmplitude = pcmSquaredAmplitudeSinceSnapshot;
  const intervalPeak = pcmPeakSinceSnapshot;
  pcmSamplesSinceSnapshot = 0;
  pcmSquaredAmplitudeSinceSnapshot = 0;
  pcmPeakSinceSnapshot = 0;

  const [connection, nativeStats] = await Promise.all([
    getMentraConnectionStatus().catch((error) => ({ error: wakeErrorMessage(error) })),
    GlassesAlertsNative?.getV8WakeSpotterStats().catch((error) => ({ error: wakeErrorMessage(error) })) ??
      Promise.resolve(null),
  ]);
  const snapshot: Record<string, unknown> = {
    source,
    initialized,
    listener_active: listenerActive,
    activation_pending: listenerActivation !== null,
    last_step: lastWakeStep,
    last_error: lastWakeError,
    pause_reasons: [...pauseReasons.keys()],
    connection,
    pcm_callbacks_total: pcmCallbacksTotal,
    pcm_samples_total: pcmSamplesTotal,
    pcm_samples_since_snapshot: intervalSamples,
    pcm_peak_since_snapshot: intervalPeak / 32768,
    pcm_rms_since_snapshot: intervalSamples
      ? Math.sqrt(intervalSquaredAmplitude / intervalSamples) / 32768
      : 0,
    last_pcm_at: lastPcmAt,
    pending_pcm_samples: pendingPcmSamples,
    processing_pcm: processingPcm,
    native_spotter: nativeStats,
  };
  await appendMentraDebugLog('wake_debug_snapshot', snapshot);
  return snapshot;
}

function handleGlassesCommandTranscriptionFailure(event: {
  commandId: string;
  wakeDetectedAt: number;
  error: string;
}): void {
  debug('glasses_command_transcription_failure_led', {
    command_id: event.commandId,
    wake_to_failure_ms: Date.now() - event.wakeDetectedAt,
    error: event.error,
  });
  void blinkMentraRedLed().catch((error) =>
    debug('glasses_command_red_led_failed', {
      command_id: event.commandId,
      error: error instanceof Error ? error.message : String(error),
    }),
  );
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

async function getDetector(): Promise<V8TwoStageWakeWordDetector> {
  if (detector) return detector;
  if (!detectorInitialization) {
    detectorInitialization = (async () => {
      const startedAt = Date.now();
      lastWakeStep = 'loading_onnx_assets';
      debug('wake_detector_initializing', { step: lastWakeStep });
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
      lastWakeStep = 'initializing_native_spotter';
      debug('wake_detector_initializing', { step: lastWakeStep });
      if (!GlassesAlertsNative) throw new Error('V8 native wake spotter is unavailable');
      await GlassesAlertsNative.initializeV8WakeSpotter();
      detector = new V8TwoStageWakeWordDetector(model, GlassesAlertsNative, backend, (evaluation) => {
        debug('wake_v8_candidate', {
          keyword: evaluation.keyword,
          score: evaluation.score,
          threshold: evaluation.threshold,
          passed: evaluation.passed,
          audio_time_ms: evaluation.audioTimeMs,
        });
      });
      debug('wake_detector_ready', { version: 'v8-two-stage', initialization_ms: Date.now() - startedAt });
      lastWakeStep = 'detector_ready';
      lastWakeError = null;
      return detector;
    })().catch((error) => {
      detectorInitialization = null;
      lastWakeError = wakeErrorMessage(error);
      debug('wake_detector_init_failed', { step: lastWakeStep, error: lastWakeError });
      throw error;
    });
  }
  return detectorInitialization;
}

function resetDetector(reason: string): Promise<void> {
  detectorGeneration += 1;
  pendingPcm = [];
  pendingPcmSamples = 0;
  debug('wake_detector_reset', { reason });
  return detector?.reset() ?? Promise.resolve();
}

async function processPendingPcm(): Promise<void> {
  if (processingPcm) return;
  processingPcm = true;
  try {
    while (listenerActive && pendingPcm.length > 0) {
      const chunk = pendingPcm.shift();
      if (!chunk) continue;
      pendingPcmSamples -= chunk.length;
      const activeDetector = await getDetector();
      const generation = detectorGeneration;
      const startedAt = Date.now();
      const events = await activeDetector.acceptPcm16(chunk);
      if (generation !== detectorGeneration) continue;
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
        const commandInitialChunks = [
          event.preRollPcm16,
          ...(event.postDetectionPcm16?.length ? [event.postDetectionPcm16] : []),
          ...pendingPcm.splice(0),
        ];
        pendingPcmSamples = 0;
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
        // Queue the native reset but create the command session immediately;
        // glasses PCM can arrive while the reset promise is still settling.
        void resetDetector('command_session_started').catch((error) =>
          debug('wake_detector_reset_failed', {
            error: error instanceof Error ? error.message : String(error),
          }),
        );
        startGlassesCommandTranscription(
          wakeDetectedAt,
          () => undefined,
          commandInitialChunks,
          {
            wakePhrase: event.modelName.replace(/[-_]+/gu, ' '),
            detectionAudioTimeMs: event.audioTimeMs,
            preRollStartAudioTimeMs: event.preRollStartAudioTimeMs,
            preRollEndAudioTimeMs: event.preRollEndAudioTimeMs,
          },
          (transcript) => {
            void dispatchGlassesCommand(transcript, {
              pauseListening: () => pauseWakeWordListening('glasses_command'),
              resumeListening: () => resumeWakeWordListening('glasses_command', 'command_finished'),
            });
          },
          handleGlassesCommandTranscriptionFailure,
        );
      }
    }
  } catch (error) {
    lastWakeError = wakeErrorMessage(error);
    debug('wake_inference_failed', {
      error: lastWakeError,
    });
    await resetDetector('inference_failed').catch(() => undefined);
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
  pcmCallbacksTotal += 1;
  pcmSamplesTotal += samples.length;
  pcmSamplesSinceSnapshot += samples.length;
  lastPcmAt = Date.now();
  for (const sample of samples) {
    pcmSquaredAmplitudeSinceSnapshot += sample * sample;
    pcmPeakSinceSnapshot = Math.max(pcmPeakSinceSnapshot, Math.abs(sample));
  }
  if (firstPcmForListener) {
    firstPcmForListener = false;
    debug('wake_pcm_first_valid', { samples: samples.length, pcm_peak: pcmPeakSinceSnapshot / 32768 });
  }
  if (isGlassesCommandSessionActive()) {
    acceptGlassesCommandPcm(
      samples,
      (command) => {
        debug('glasses_command_listening_finished', { command_id: command.commandId });
      },
      (transcript) => {
        void dispatchGlassesCommand(transcript, {
          pauseListening: () => pauseWakeWordListening('glasses_command'),
          resumeListening: () => resumeWakeWordListening('glasses_command', 'command_finished'),
        });
      },
      handleGlassesCommandTranscriptionFailure,
    );
    return;
  }
  observeGlassesAmbientPcm(samples);
  if (pendingPcmSamples + samples.length > MAX_PENDING_PCM_SAMPLES) {
    debug('wake_pcm_backlog_dropped', {
      pending_chunks: pendingPcm.length,
      pending_samples: pendingPcmSamples,
      samples: samples.length,
    });
    void resetDetector('pcm_backlog').catch(() => undefined);
    return;
  }
  pendingPcm.push(samples);
  pendingPcmSamples += samples.length;
  void processPendingPcm();
}

async function activateListener(): Promise<void> {
  if (listenerActive || !shouldListen()) return;
  if (listenerActivation) return listenerActivation;
  listenerActivation = (async () => {
    const status = await getMentraConnectionStatus();
    if (!status.connected) {
      pauseReasons.set('connection_lost', false);
      lastWakeStep = 'waiting_for_glasses';
      debug('wake_waiting_for_glasses', { state: status.state, fully_booted: status.fullyBooted });
      return;
    }
    pauseReasons.delete('connection_lost');
    if (!shouldListen()) return;
    lastWakeStep = 'starting_wake_foreground_runtime';
    debug('wake_listener_activating', { step: lastWakeStep });
    await GlassesAlertsNative?.startGlassesWakeRuntime();
    try {
      await getDetector();
      if (!shouldListen()) return;
      lastWakeStep = 'enabling_glasses_mic';
      debug('wake_listener_activating', { step: lastWakeStep });
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
      firstPcmForListener = true;
      lastWakeStep = 'listening';
      lastWakeError = null;
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
  lastWakeStep = `stopped:${reason}`;
  listenerActive = false;
  pcmUnsubscribe?.();
  pcmUnsubscribe = null;
  await resetDetector(reason).catch((error) =>
    debug('wake_detector_reset_failed', {
      error: error instanceof Error ? error.message : String(error),
    }),
  );
  if (disableMic) await setMentraMicState(false).catch(() => undefined);
  debug('wake_listener_stopped', { reason, mic_disabled: disableMic });
}

async function reconcile(reason: string): Promise<void> {
  if (!shouldListen()) {
    await deactivateListener(reason, ![...pauseReasons.values()].some(Boolean));
    if (pauseReasons.size > 0)
      await GlassesAlertsNative?.stopGlassesWakeRuntime().catch(() => undefined);
    return;
  }
  await activateListener();
}

export async function pauseWakeWordListening(
  reason: 'audio_recording' | 'video_recording' | 'glasses_command' | 'firmware_update',
  options: { keepMicrophoneEnabled?: boolean } = {},
): Promise<void> {
  const ownsMicrophone =
    reason === 'video_recording' ||
    (reason === 'audio_recording' && options.keepMicrophoneEnabled === true);
  pauseReasons.set(reason, ownsMicrophone);
  await deactivateListener(reason, !ownsMicrophone);
  await GlassesAlertsNative?.stopGlassesWakeRuntime().catch(() => undefined);
}

export async function resumeWakeWordListening(
  owner: 'audio_recording' | 'video_recording' | 'glasses_command' | 'firmware_update',
  reason: string,
): Promise<void> {
  pauseReasons.delete(owner);
  await reconcile(reason);
}

export async function initializeWakeWordRuntime(): Promise<void> {
  if (initialized || Platform.OS !== 'android') return;
  initialized = true;
  lastWakeStep = 'runtime_initialized';
  debug('wake_runtime_initialized', { model: model.name, automatic: true });
  wakeDebugTimer = setInterval(() => {
    void recordWakeDebugSnapshot('heartbeat').catch((error) => {
      lastWakeError = wakeErrorMessage(error);
    });
  }, 10_000);
  connectionUnsubscribe = subscribeMentraConnectionState((status) => {
    if (status.connected) {
      pauseReasons.delete('connection_lost');
      void reconcile('glasses_ready').catch((error) => {
        lastWakeError = wakeErrorMessage(error);
        debug('wake_reconcile_failed', { reason: 'glasses_ready', step: lastWakeStep, error: lastWakeError });
      });
      return;
    }
    pauseReasons.set('connection_lost', false);
    void deactivateListener('glasses_not_ready', true).catch((error) => {
      lastWakeError = wakeErrorMessage(error);
      debug('wake_reconcile_failed', { reason: 'glasses_not_ready', error: lastWakeError });
    });
  });
  videoUnsubscribe = subscribeMentraVideoRecordingStatus((event) => {
    if (event.status === 'recording_started' || event.data?.recording === true) {
      void pauseWakeWordListening('video_recording').catch((error) => {
        lastWakeError = wakeErrorMessage(error);
        debug('wake_reconcile_failed', { reason: 'video_recording_started', error: lastWakeError });
      });
    }
    if (event.status === 'recording_stopped' || event.status === 'not_recording') {
      void resumeWakeWordListening('video_recording', 'video_recording_stopped').catch((error) => {
        lastWakeError = wakeErrorMessage(error);
        debug('wake_reconcile_failed', { reason: 'video_recording_stopped', error: lastWakeError });
      });
    }
  });
  await reconcile('startup').catch((error) => {
    lastWakeError = wakeErrorMessage(error);
    debug('wake_runtime_start_failed', { step: lastWakeStep, error: lastWakeError });
  });
}

export async function disposeWakeWordRuntime(): Promise<void> {
  initialized = false;
  if (wakeDebugTimer) clearInterval(wakeDebugTimer);
  wakeDebugTimer = null;
  connectionUnsubscribe?.();
  connectionUnsubscribe = null;
  videoUnsubscribe?.();
  videoUnsubscribe = null;
  await deactivateListener('runtime_disposed', true);
  await GlassesAlertsNative?.releaseV8WakeSpotter().catch(() => undefined);
  detector = null;
  detectorInitialization = null;
  await GlassesAlertsNative?.stopGlassesWakeRuntime().catch(() => undefined);
}
