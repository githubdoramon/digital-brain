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
  createGlassesCommandId,
  isGlassesCommandSessionActive,
  observeGlassesAmbientPcm,
  startGlassesCommandTranscription,
  warmGlassesCommandTranscription,
} from '@/mentraCapture/commandTranscription';
import { dispatchGlassesCommand } from '@/mentraCapture/glassesCommandAgent';
import {
  OpenWakeWordOnnxBackend,
  V8TwoStageWakeWordDetector,
  type V8Candidate,
  type EmbeddingWakeWordModel,
  type OnnxRuntimeLike,
} from '@/wakeWord';

const model = require('@/assets/wake-word/hey-brain-v8.json') as EmbeddingWakeWordModel;

type PauseReason =
  | 'audio_recording'
  | 'video_recording'
  | 'glasses_command'
  | 'connection_lost'
  | 'firmware_update';
let initialized = false;
let detector: V8TwoStageWakeWordDetector | null = null;
let pcmUnsubscribe: (() => void) | null = null;
let candidateUnsubscribe: (() => void) | null = null;
let nativeWakeErrorUnsubscribe: (() => void) | null = null;
let connectionUnsubscribe: (() => void) | null = null;
let videoUnsubscribe: (() => void) | null = null;
let pendingCandidates: V8Candidate[] = [];
let processingCandidate = false;
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
let wakeInferenceCountSinceSnapshot = 0;
let wakeInferenceSlowCountSinceSnapshot = 0;
let wakeInferenceBacklogCountSinceSnapshot = 0;
let wakeInferenceTotalMsSinceSnapshot = 0;
let wakeInferenceMaxMsSinceSnapshot = 0;
let wakeInferenceMaxPendingChunksSinceSnapshot = 0;

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
  const inferenceCount = wakeInferenceCountSinceSnapshot;
  const inferenceSlowCount = wakeInferenceSlowCountSinceSnapshot;
  const inferenceBacklogCount = wakeInferenceBacklogCountSinceSnapshot;
  const inferenceTotalMs = wakeInferenceTotalMsSinceSnapshot;
  const inferenceMaxMs = wakeInferenceMaxMsSinceSnapshot;
  const inferenceMaxPendingChunks = wakeInferenceMaxPendingChunksSinceSnapshot;
  pcmSamplesSinceSnapshot = 0;
  pcmSquaredAmplitudeSinceSnapshot = 0;
  pcmPeakSinceSnapshot = 0;
  wakeInferenceCountSinceSnapshot = 0;
  wakeInferenceSlowCountSinceSnapshot = 0;
  wakeInferenceBacklogCountSinceSnapshot = 0;
  wakeInferenceTotalMsSinceSnapshot = 0;
  wakeInferenceMaxMsSinceSnapshot = 0;
  wakeInferenceMaxPendingChunksSinceSnapshot = 0;

  const [connection, nativeStats] = await Promise.all([
    getMentraConnectionStatus().catch((error) => ({ error: wakeErrorMessage(error) })),
    GlassesAlertsNative?.getV8WakeSpotterStats().catch((error) => ({
      error: wakeErrorMessage(error),
    })) ?? Promise.resolve(null),
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
    pending_wake_candidates: pendingCandidates.length,
    processing_wake_candidate: processingCandidate,
    wake_inference_count: inferenceCount,
    wake_inference_slow_count_over_80ms: inferenceSlowCount,
    wake_inference_backlog_count_over_4_chunks: inferenceBacklogCount,
    wake_inference_average_ms: inferenceCount ? inferenceTotalMs / inferenceCount : 0,
    wake_inference_max_ms: inferenceMaxMs,
    wake_inference_max_pending_chunks: inferenceMaxPendingChunks,
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
      detector = new V8TwoStageWakeWordDetector(
        model,
        GlassesAlertsNative,
        backend,
        (evaluation) => {
          debug('wake_v8_candidate', {
            keyword: evaluation.keyword,
            score: evaluation.score,
            threshold: evaluation.threshold,
            passed: evaluation.passed,
            audio_time_ms: evaluation.audioTimeMs,
          });
        },
      );
      debug('wake_detector_ready', {
        version: 'v8-two-stage',
        initialization_ms: Date.now() - startedAt,
      });
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
  pendingCandidates = [];
  debug('wake_detector_reset', { reason });
  return detector?.reset() ?? Promise.resolve();
}

async function processPendingCandidates(): Promise<void> {
  if (processingCandidate) return;
  processingCandidate = true;
  try {
    while (listenerActive && pendingCandidates.length > 0) {
      const activeDetector = await getDetector();
      const candidate = pendingCandidates[0];
      if (!candidate || !listenerActive) break;
      if (candidate.sampleIndex > activeDetector.streamSamples) break;
      pendingCandidates.shift();
      const generation = detectorGeneration;
      const startedAt = Date.now();
      const event = await activeDetector.acceptCandidate(candidate);
      if (generation !== detectorGeneration) continue;
      const elapsedMs = Date.now() - startedAt;
      wakeInferenceCountSinceSnapshot += 1;
      wakeInferenceTotalMsSinceSnapshot += elapsedMs;
      wakeInferenceMaxMsSinceSnapshot = Math.max(wakeInferenceMaxMsSinceSnapshot, elapsedMs);
      if (elapsedMs > 80) wakeInferenceSlowCountSinceSnapshot += 1;
      if (pendingCandidates.length > 4) wakeInferenceBacklogCountSinceSnapshot += 1;
      wakeInferenceMaxPendingChunksSinceSnapshot = Math.max(
        wakeInferenceMaxPendingChunksSinceSnapshot,
        pendingCandidates.length,
      );
      debug('wake_verifier_timing', {
        keyword: candidate.keyword,
        duration_ms: elapsedMs,
        queued_candidates: pendingCandidates.length,
        passed: event !== null,
      });
      if (event) {
        const commandId = createGlassesCommandId();
        const wakeDetectedAt = Date.now();
        debug('wake_detected', {
          command_id: commandId,
          detected_at_ms: wakeDetectedAt,
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
            command_id: commandId,
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
        ];
        const initialAudioDurationMs = Math.round(
          (commandInitialChunks.reduce((total, chunk) => total + chunk.length, 0) / 16_000) * 1_000,
        );
        debug('wake_command_audio_buffered', {
          command_id: commandId,
          pre_roll_samples: event.preRollPcm16.length,
          initial_audio_duration_ms: initialAudioDurationMs,
          wake_decision_audio_time_ms: event.audioTimeMs,
          wake_pre_roll_start_audio_time_ms: event.preRollStartAudioTimeMs,
          wake_pre_roll_end_audio_time_ms: event.preRollEndAudioTimeMs,
        });
        // Queue the native reset but create the command session immediately;
        // glasses PCM can arrive while the reset promise is still settling.
        void (async () => {
          await GlassesAlertsNative?.stopV8WakeInput();
          await resetDetector('command_session_started');
        })().catch((error) =>
          debug('wake_detector_reset_failed', {
            command_id: commandId,
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
          commandId,
        );
      }
    }
  } catch (error) {
    lastWakeError = wakeErrorMessage(error);
    debug('wake_inference_failed', {
      error: lastWakeError,
    });
    await deactivateListener('inference_failed', true).catch(() => undefined);
  } finally {
    processingCandidate = false;
    if (listenerActive && pendingCandidates.length > 0 &&
      pendingCandidates[0].sampleIndex <= (detector?.streamSamples ?? 0)) {
      void processPendingCandidates();
    }
  }
}

function acceptNativeCandidate(candidate: V8Candidate): void {
  if (!listenerActive || isGlassesCommandSessionActive() ||
    !Number.isSafeInteger(candidate.sampleIndex) || candidate.sampleIndex < 0 ||
    (candidate.keyword !== 'hey_brain' && candidate.keyword !== 'okay_brain')) return;
  pendingCandidates.push(candidate);
  void processPendingCandidates();
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
    debug('wake_pcm_first_valid', {
      samples: samples.length,
      pcm_peak: pcmPeakSinceSnapshot / 32768,
    });
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
  detector?.acceptPcm16(samples);
  if (pendingCandidates.length > 0) void processPendingCandidates();
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
      const native = GlassesAlertsNative!;
      const candidateSubscription = native.addListener('onV8WakeCandidate', acceptNativeCandidate);
      candidateUnsubscribe = () => candidateSubscription.remove();
      const errorSubscription = native.addListener('onV8WakeError', (event) => {
        lastWakeError = event.message;
        debug('wake_native_input_failed', { error: event.message });
        void deactivateListener('native_input_failed', true);
      });
      nativeWakeErrorUnsubscribe = () => errorSubscription.remove();
      if (!shouldListen()) {
        unsubscribe();
        pcmUnsubscribe = null;
        candidateUnsubscribe();
        candidateUnsubscribe = null;
        nativeWakeErrorUnsubscribe();
        nativeWakeErrorUnsubscribe = null;
        return;
      }
      await native.startV8WakeInput();
      listenerActive = true;
      await setMentraMicState(true);
      if (!shouldListen() || pcmUnsubscribe !== unsubscribe) return;
      firstPcmForListener = true;
      lastWakeStep = 'listening';
      lastWakeError = null;
      debug('wake_listener_started', { model: model.name });
      void warmGlassesCommandTranscription().catch(() => undefined);
    } catch (error) {
      listenerActive = false;
      pcmUnsubscribe?.();
      pcmUnsubscribe = null;
      candidateUnsubscribe?.();
      candidateUnsubscribe = null;
      nativeWakeErrorUnsubscribe?.();
      nativeWakeErrorUnsubscribe = null;
      await GlassesAlertsNative?.stopV8WakeInput().catch(() => undefined);
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
  candidateUnsubscribe?.();
  candidateUnsubscribe = null;
  nativeWakeErrorUnsubscribe?.();
  nativeWakeErrorUnsubscribe = null;
  await GlassesAlertsNative?.stopV8WakeInput().catch(() => undefined);
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
        debug('wake_reconcile_failed', {
          reason: 'glasses_ready',
          step: lastWakeStep,
          error: lastWakeError,
        });
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
