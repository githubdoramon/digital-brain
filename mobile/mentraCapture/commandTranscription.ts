import {
  invalidateEnglishWhisperContext,
  isMissingNativeWhisperContextError,
  LOCAL_WHISPER_MODEL_FILE_NAME,
  warmEnglishWhisperContext,
} from '@/chat/localTranscription';
import { normalizeTranscriptText } from '@/chat/voiceState';

import { appendMentraDebugLog, appendWakeCommandDebugLog } from './debug';
import { retainWakeCommandAudio } from './wakeCommandDebug';

const COMMAND_GRACE_MS = 0;
const COMMAND_WAKE_TAIL_IGNORE_MS = 350;
const COMMAND_INITIAL_COMMAND_WINDOW_MS = 3_000;
const COMMAND_SILENCE_MS = 1_500;
const COMMAND_MAX_LISTENING_MS = 8_000;
const COMMAND_MIN_SPEECH_MS = 120;
const COMMAND_MIN_SPEECH_RMS_THRESHOLD = 0.018;
const COMMAND_SPEECH_RMS_MARGIN = 0.008;
const COMMAND_MAX_SPEECH_RMS_THRESHOLD = 0.08;
// A frame can start speech at the noise-adaptive threshold, but it must be
// materially louder and sustained before it keeps a command session alive.
// The command WAV examples put the wearer's voice above roughly -20 dBFS,
// while the unwanted room conversation was generally below -23 dBFS.
const COMMAND_CONTINUATION_RMS_THRESHOLD = 0.075;
const COMMAND_CONTINUATION_NOISE_RATIO = 1.6;
const COMMAND_CONTINUATION_MIN_MS = 100;
const AMBIENT_RMS_HISTORY_SIZE = 120;
const MIN_AMBIENT_RMS = 0.002;
const COMMAND_FILTER_SAMPLE_RATE_HZ = 16_000;
const COMMAND_FILTER_LOW_HZ = 120;
const COMMAND_FILTER_HIGH_HZ = 7_000;

type CommandSessionState = 'idle' | 'grace' | 'listening' | 'transcribing';
type CommandFinishReason = 'silence' | 'timeout' | 'no_speech' | 'cancelled';

export type WakeCommandTiming = {
  wakePhrase: string;
  detectionAudioTimeMs: number;
  preRollStartAudioTimeMs: number;
  preRollEndAudioTimeMs: number;
};

type CommandSession = {
  id: string;
  wakeDetectedAt: number;
  listeningStartedAt: number | null;
  speechStartedAt: number | null;
  lastSpeechAt: number | null;
  speechCandidateStartedAt: number | null;
  continuationCandidateStartedAt: number | null;
  continuationCandidateLastAt: number | null;
  graceChunks: Int16Array[];
  chunks: Int16Array[];
  filteredChunks: Int16Array[];
  filter: AudioBandPassFilter;
  samples: number;
  ambientRms: number[];
  minimumRms: number | null;
  maximumRms: number | null;
  minimumRawRms: number | null;
  maximumRawRms: number | null;
  currentThreshold: number;
  strongSpeechChunkCount: number;
  weakAudioChunkCount: number;
  maximumTimer: ReturnType<typeof setTimeout> | null;
  pcmChunkCount: number;
  lastPcmAt: number | null;
  largestPcmGapMs: number;
  wakeTailIgnoreUntil: number;
  initialEndpointAllowedAt: number;
  wakeTiming: WakeCommandTiming;
};

export type GlassesCommandListeningFinished = {
  commandId: string;
  reason: Exclude<CommandFinishReason, 'cancelled'>;
  wakeDetectedAt: number;
  listeningStartedAt: number;
  speechStartedAt: number | null;
  audioDurationMs: number;
};

export type GlassesCommandClientTimings = {
  wake_to_listening_start_ms: number;
  wake_to_speech_start_ms: number | null;
  wake_to_listening_end_ms: number;
  wake_to_transcription_start_ms: number;
  model_wait_ms: number;
  transcription_ms: number;
  transcription_total_ms: number;
  wake_to_transcript_ms: number;
  audio_duration_ms: number;
  transcription_attempt_count: number;
};

export type GlassesCommandTranscribed = {
  commandId: string;
  transcript: string;
  rawTranscript: string;
  language: string;
  audioDurationMs: number;
  wakeDetectedAt: number;
  clientTimings: GlassesCommandClientTimings;
};

export type GlassesCommandTranscriptionFailed = {
  commandId: string;
  wakeDetectedAt: number;
  error: string;
};

let state: CommandSessionState = 'idle';
let activeSession: CommandSession | null = null;
let commandModelWarmup: Promise<void> | null = null;
const ambientRmsHistory: number[] = [];

function debug(event: string, payload?: Record<string, unknown>): void {
  void appendMentraDebugLog(event, payload).catch(() => undefined);
  void appendWakeCommandDebugLog(event, payload).catch(() => undefined);
}

function createCommandId(): string {
  const crypto = (globalThis as { crypto?: { randomUUID?: () => string } }).crypto;
  if (crypto?.randomUUID) return crypto.randomUUID();
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/gu, (character) => {
    const random = Math.floor(Math.random() * 16);
    const value = character === 'x' ? random : (random & 0x3) | 0x8;
    return value.toString(16);
  });
}

function rms(samples: Int16Array): number {
  if (samples.length === 0) return 0;
  let sum = 0;
  for (let index = 0; index < samples.length; index += 1) {
    const normalized = samples[index] / 32_768;
    sum += normalized * normalized;
  }
  return Math.sqrt(sum / samples.length);
}

class BiquadFilter {
  private readonly b0: number;
  private readonly b1: number;
  private readonly b2: number;
  private readonly a1: number;
  private readonly a2: number;
  private x1 = 0;
  private x2 = 0;
  private y1 = 0;
  private y2 = 0;

  constructor(type: 'highpass' | 'lowpass', frequencyHz: number, sampleRateHz: number) {
    const omega = (2 * Math.PI * frequencyHz) / sampleRateHz;
    const cosine = Math.cos(omega);
    const alpha = Math.sin(omega) / (2 * Math.SQRT1_2);
    const a0 = 1 + alpha;
    const a1 = -2 * cosine;
    const a2 = 1 - alpha;
    const common = type === 'highpass' ? (1 + cosine) / 2 : (1 - cosine) / 2;
    const numerator =
      type === 'highpass'
        ? { b0: common, b1: -(1 + cosine), b2: common }
        : { b0: common, b1: 1 - cosine, b2: common };

    this.b0 = numerator.b0 / a0;
    this.b1 = numerator.b1 / a0;
    this.b2 = numerator.b2 / a0;
    this.a1 = a1 / a0;
    this.a2 = a2 / a0;
  }

  process(sample: number): number {
    const output =
      this.b0 * sample +
      this.b1 * this.x1 +
      this.b2 * this.x2 -
      this.a1 * this.y1 -
      this.a2 * this.y2;
    this.x2 = this.x1;
    this.x1 = sample;
    this.y2 = this.y1;
    this.y1 = output;
    return output;
  }
}

/**
 * Removes low-frequency handling noise and high-frequency hiss before VAD and
 * Whisper. The original PCM is retained separately for investigation clips.
 */
export class AudioBandPassFilter {
  private readonly highpass: BiquadFilter;
  private readonly lowpass: BiquadFilter;

  constructor(
    sampleRateHz = COMMAND_FILTER_SAMPLE_RATE_HZ,
    lowHz = COMMAND_FILTER_LOW_HZ,
    highHz = COMMAND_FILTER_HIGH_HZ,
  ) {
    this.highpass = new BiquadFilter('highpass', lowHz, sampleRateHz);
    this.lowpass = new BiquadFilter('lowpass', highHz, sampleRateHz);
  }

  process(samples: Int16Array): Int16Array {
    const filtered = new Int16Array(samples.length);
    for (let index = 0; index < samples.length; index += 1) {
      const normalized = this.lowpass.process(this.highpass.process(samples[index] / 32_768));
      const clipped = Math.max(-1, Math.min(0.999969482421875, normalized));
      filtered[index] = Math.round(clipped * 32_768);
    }
    return filtered;
  }
}

const ambientFilter = new AudioBandPassFilter();

function percentile(values: number[], quantile: number): number | null {
  if (values.length === 0) return null;
  const sorted = [...values].sort((left, right) => left - right);
  return sorted[Math.min(sorted.length - 1, Math.floor((sorted.length - 1) * quantile))] ?? null;
}

function speechThreshold(ambientRms: number[]): number {
  const noiseFloor = percentile(ambientRms, 0.2);
  if (noiseFloor === null) return COMMAND_MIN_SPEECH_RMS_THRESHOLD;
  return Math.max(
    COMMAND_MIN_SPEECH_RMS_THRESHOLD,
    Math.min(COMMAND_MAX_SPEECH_RMS_THRESHOLD, noiseFloor + COMMAND_SPEECH_RMS_MARGIN),
  );
}

function continuationThreshold(startThreshold: number, ambientRms: number[] = []): number {
  const noiseFloor = percentile(ambientRms, 0.2) ?? 0;
  return Math.max(
    startThreshold,
    COMMAND_CONTINUATION_RMS_THRESHOLD,
    noiseFloor * COMMAND_CONTINUATION_NOISE_RATIO,
  );
}

type TranscriptWord = {
  normalized: string;
  start: number;
  end: number;
};

function transcriptWords(text: string): TranscriptWord[] {
  const words: TranscriptWord[] = [];
  const matcher = /[\p{L}\p{N}]+/gu;
  for (const match of text.matchAll(matcher)) {
    const value = match[0];
    const start = match.index;
    if (start === undefined) continue;
    words.push({
      normalized: value.normalize('NFD').replace(/\p{M}/gu, '').toLocaleLowerCase(),
      start,
      end: start + value.length,
    });
  }
  return words;
}

function phoneticSkeleton(word: string): string {
  return word.replace(/[aeiouy]/gu, '');
}

function editDistance(left: string, right: string): number {
  const previous = Array.from({ length: right.length + 1 }, (_, index) => index);
  for (let leftIndex = 1; leftIndex <= left.length; leftIndex += 1) {
    let diagonal = previous[0] ?? 0;
    previous[0] = leftIndex;
    for (let rightIndex = 1; rightIndex <= right.length; rightIndex += 1) {
      const saved = previous[rightIndex] ?? 0;
      previous[rightIndex] = Math.min(
        (previous[rightIndex - 1] ?? 0) + 1,
        saved + 1,
        diagonal + (left[leftIndex - 1] === right[rightIndex - 1] ? 0 : 1),
      );
      diagonal = saved;
    }
  }
  return previous[right.length] ?? Math.max(left.length, right.length);
}

function isWakeAnchorMatch(candidate: string, expected: string): boolean {
  if (candidate === expected) return true;
  const candidateSkeleton = phoneticSkeleton(candidate);
  const expectedSkeleton = phoneticSkeleton(expected);
  return (
    candidateSkeleton.length >= 2 &&
    expectedSkeleton.length >= 2 &&
    editDistance(candidateSkeleton, expectedSkeleton) <= 1
  );
}

function isCombinedWakePrefixMatch(candidate: string, wakeWords: TranscriptWord[]): boolean {
  if (wakeWords.length < 2) return false;

  const expected = wakeWords.map((word) => word.normalized).join('');
  const candidateSkeleton = phoneticSkeleton(candidate);
  const expectedSkeleton = phoneticSkeleton(expected);
  const minimumLength = Math.max(4, expected.length - 2);

  return (
    candidate.length >= minimumLength &&
    candidateSkeleton.length >= 3 &&
    editDistance(candidateSkeleton, expectedSkeleton) <= 1
  );
}

function stripWakeWordPrefix(
  transcript: string,
  wakePhrase: string,
): {
  transcript: string;
  wakeWordPrefixRemoved: boolean;
  removalMethod: 'combined_fuzzy' | 'model_anchor' | 'none';
} {
  // The model has already acoustically accepted the wake phrase. Use its
  // label's final word as a fuzzy anchor in the first few recognised words,
  // rather than requiring Whisper to spell the phrase exactly ("okay brain",
  // "hey Brian", etc.). We intentionally keep audio untrimmed: a detector
  // decision timestamp is not a phonetic word-boundary timestamp, so cutting
  // PCM there can remove the start of a fast command.
  const wakeWords = transcriptWords(wakePhrase);
  const anchor = wakeWords.at(-1)?.normalized;
  const words = transcriptWords(transcript);
  if (!anchor || wakeWords.length < 2 || words.length < 2) {
    return { transcript, wakeWordPrefixRemoved: false, removalMethod: 'none' };
  }

  const firstWord = words[0];
  if (isCombinedWakePrefixMatch(firstWord.normalized, wakeWords)) {
    const withoutWakeWord = transcript
      .slice(firstWord.end)
      .replace(/^[\s,.:;!?-]*/u, '')
      .trim();
    return {
      transcript: withoutWakeWord,
      wakeWordPrefixRemoved: true,
      removalMethod: 'combined_fuzzy',
    };
  }

  const maximumAnchorIndex = Math.min(words.length - 1, wakeWords.length);
  const anchorIndex = words
    .slice(1, maximumAnchorIndex + 1)
    .findIndex((word) => isWakeAnchorMatch(word.normalized, anchor));
  if (anchorIndex === -1) {
    return { transcript, wakeWordPrefixRemoved: false, removalMethod: 'none' };
  }

  const wakeAnchor = words[anchorIndex + 1];
  const withoutWakeWord = transcript.slice(wakeAnchor?.end ?? 0).replace(/^[\s,.:;!?-]*/u, '');
  return {
    transcript: withoutWakeWord,
    wakeWordPrefixRemoved: true,
    removalMethod: 'model_anchor',
  };
}

function errorDetails(error: unknown): Record<string, unknown> {
  if (error instanceof Error) {
    return {
      error_name: error.name,
      error_message: error.message,
      error_stack: error.stack ?? null,
    };
  }
  if (error && typeof error === 'object') {
    const details: Record<string, unknown> = {};
    for (const key of Object.getOwnPropertyNames(error)) {
      try {
        details[key] = (error as Record<string, unknown>)[key];
      } catch {
        details[key] = '[unreadable]';
      }
    }
    let errorJson: string | null = null;
    try {
      errorJson = JSON.stringify(error, Object.getOwnPropertyNames(error));
    } catch {
      errorJson = null;
    }
    return {
      error_type: Object.prototype.toString.call(error),
      error_details: details,
      error_json: errorJson,
    };
  }
  return { error: String(error) };
}

function whisperAcceleration(context: unknown): {
  accelerator: 'gpu' | 'cpu';
  gpu_active: boolean;
  gpu_unavailable_reason: string | null;
} {
  // whisper.rn exposes these fields from its native context at runtime, while
  // the installed package declaration omits them from `WhisperContext`.
  const nativeContext = context as { gpu?: unknown; reasonNoGPU?: unknown };
  const gpuActive = nativeContext.gpu === true;
  return {
    accelerator: gpuActive ? 'gpu' : 'cpu',
    gpu_active: gpuActive,
    gpu_unavailable_reason:
      !gpuActive && typeof nativeContext.reasonNoGPU === 'string'
        ? nativeContext.reasonNoGPU
        : null,
  };
}

function whisperContextIdentity(context: unknown): {
  native_context_id: number | null;
  native_context_pointer: number | null;
} {
  const nativeContext = context as { id?: unknown; ptr?: unknown };
  return {
    native_context_id: typeof nativeContext.id === 'number' ? nativeContext.id : null,
    native_context_pointer: typeof nativeContext.ptr === 'number' ? nativeContext.ptr : null,
  };
}

/** Keeps only aggregate sound levels; command audio itself is never retained here. */
export function observeGlassesAmbientPcm(samples: Int16Array): void {
  const level = rms(ambientFilter.process(samples));
  if (level < MIN_AMBIENT_RMS) return;
  ambientRmsHistory.push(level);
  if (ambientRmsHistory.length > AMBIENT_RMS_HISTORY_SIZE) ambientRmsHistory.shift();
}

function clearMaximumTimer(session: CommandSession): void {
  if (!session.maximumTimer) return;
  clearTimeout(session.maximumTimer);
  session.maximumTimer = null;
}

function combineSamples(chunks: Int16Array[], sampleCount: number): ArrayBuffer {
  const buffer = new ArrayBuffer(sampleCount * Int16Array.BYTES_PER_ELEMENT);
  const combined = new Int16Array(buffer);
  let offset = 0;
  for (const chunk of chunks) {
    combined.set(chunk, offset);
    offset += chunk.length;
  }
  return buffer;
}

export function isGlassesCommandSessionActive(): boolean {
  return state !== 'idle';
}

export async function warmGlassesCommandTranscription(): Promise<void> {
  if (!commandModelWarmup) {
    const startedAt = Date.now();
    commandModelWarmup = warmEnglishWhisperContext()
      .then((context) => {
        debug('glasses_command_model_ready', {
          model: LOCAL_WHISPER_MODEL_FILE_NAME,
          ...whisperContextIdentity(context),
          ...whisperAcceleration(context),
          warmup_ms: Date.now() - startedAt,
        });
      })
      .catch((error) => {
        commandModelWarmup = null;
        debug('glasses_command_model_warmup_failed', {
          error: error instanceof Error ? error.message : String(error),
        });
        throw error;
      });
  }
  return commandModelWarmup;
}

function endListening(
  session: CommandSession,
  reason: Exclude<CommandFinishReason, 'cancelled'>,
  onListeningFinished: (event: GlassesCommandListeningFinished) => void,
  onTranscribed?: (event: GlassesCommandTranscribed) => void,
  onTranscriptionFailed?: (event: GlassesCommandTranscriptionFailed) => void,
): void {
  if (activeSession !== session || state !== 'listening') return;
  clearMaximumTimer(session);
  const listeningStartedAt = session.listeningStartedAt ?? Date.now();
  const listeningEndedAt = Date.now();
  const audioDurationMs = Math.round((session.samples / 16_000) * 1_000);
  const timing: GlassesCommandListeningFinished = {
    commandId: session.id,
    reason,
    wakeDetectedAt: session.wakeDetectedAt,
    listeningStartedAt,
    speechStartedAt: session.speechStartedAt,
    audioDurationMs,
  };
  const pcm = session.samples > 0 ? combineSamples(session.chunks, session.samples) : null;
  const filteredPcm =
    session.samples > 0 ? combineSamples(session.filteredChunks, session.samples) : null;
  state = 'transcribing';
  debug('glasses_command_listening_finished', {
    command_id: session.id,
    reason,
    wake_to_listening_end_ms: listeningEndedAt - session.wakeDetectedAt,
    listening_ms: listeningEndedAt - listeningStartedAt,
    speech_to_endpoint_ms: session.speechStartedAt ? Date.now() - session.speechStartedAt : null,
    audio_duration_ms: audioDurationMs,
    ambient_rms_p20: percentile(session.ambientRms, 0.2),
    rms_min: session.minimumRms,
    rms_max: session.maximumRms,
    filtered_rms_min: session.minimumRms,
    filtered_rms_max: session.maximumRms,
    raw_rms_min: session.minimumRawRms,
    raw_rms_max: session.maximumRawRms,
    filter_low_hz: COMMAND_FILTER_LOW_HZ,
    filter_high_hz: COMMAND_FILTER_HIGH_HZ,
    final_speech_threshold: session.currentThreshold,
    continuation_rms_threshold: continuationThreshold(session.currentThreshold, session.ambientRms),
    continuation_noise_ratio: COMMAND_CONTINUATION_NOISE_RATIO,
    strong_speech_chunk_count: session.strongSpeechChunkCount,
    weak_audio_chunk_count: session.weakAudioChunkCount,
    last_qualifying_speech_after_wake_ms: session.lastSpeechAt
      ? session.lastSpeechAt - session.wakeDetectedAt
      : null,
    pcm_chunk_count: session.pcmChunkCount,
    largest_pcm_gap_ms: session.largestPcmGapMs,
  });
  onListeningFinished(timing);

  if (pcm) {
    void retainWakeCommandAudio(session.id, session.wakeDetectedAt, pcm)
      .then((audio) =>
        debug('glasses_command_audio_retained', {
          command_id: session.id,
          audio_file: audio.fileName,
          audio_size_bytes: audio.sizeBytes,
          audio_duration_ms: audioDurationMs,
          sample_rate_hz: 16_000,
          channels: 1,
          encoding: 'pcm_s16le_wav',
        }),
      )
      .catch((error) =>
        debug('glasses_command_audio_retain_failed', {
          command_id: session.id,
          error: error instanceof Error ? error.message : String(error),
        }),
      );
  } else {
    debug('glasses_command_audio_not_retained', {
      command_id: session.id,
      reason: 'no_pcm_received',
    });
  }

  if (!session.speechStartedAt || !pcm || !filteredPcm) {
    debug('glasses_command_transcription_skipped', {
      command_id: session.id,
      reason: 'no_speech',
      wake_to_complete_ms: Date.now() - session.wakeDetectedAt,
    });
    activeSession = null;
    state = 'idle';
    return;
  }

  const transcriptionStartedAt = Date.now();
  void (async () => {
    try {
      let context = await warmEnglishWhisperContext();
      const initialModelReadyAt = Date.now();
      let transcriptionAttempt = 1;
      let retryRecoveryMs: number | null = null;
      let attemptStartedAt = Date.now();
      const transcribe = () => {
        debug('glasses_command_transcription_attempt', {
          command_id: session.id,
          attempt: transcriptionAttempt,
          model: LOCAL_WHISPER_MODEL_FILE_NAME,
          ...whisperContextIdentity(context),
          ...whisperAcceleration(context),
        });
        return context.transcribeData(filteredPcm, {
          language: 'en',
          maxThreads: 4,
        }).promise;
      };

      let result;
      try {
        result = await transcribe();
      } catch (error) {
        if (!isMissingNativeWhisperContextError(error)) throw error;

        debug('glasses_command_whisper_context_invalidated', {
          command_id: session.id,
          attempt: transcriptionAttempt,
          model: LOCAL_WHISPER_MODEL_FILE_NAME,
          ...whisperContextIdentity(context),
          reason: 'native_context_not_found',
          ...errorDetails(error),
        });
        const recoveryStartedAt = Date.now();
        invalidateEnglishWhisperContext();
        commandModelWarmup = null;
        debug('glasses_command_whisper_context_recreating', {
          command_id: session.id,
          model: LOCAL_WHISPER_MODEL_FILE_NAME,
        });
        context = await warmEnglishWhisperContext();
        retryRecoveryMs = Date.now() - recoveryStartedAt;
        transcriptionAttempt += 1;
        attemptStartedAt = Date.now();
        debug('glasses_command_whisper_context_recreated', {
          command_id: session.id,
          attempt: transcriptionAttempt,
          model: LOCAL_WHISPER_MODEL_FILE_NAME,
          recovery_ms: retryRecoveryMs,
          ...whisperContextIdentity(context),
          ...whisperAcceleration(context),
        });
        result = await transcribe();
      }

      const modelReadyAt = initialModelReadyAt;
      const transcriptionFinishedAt = Date.now();
      const transcriptionMs = transcriptionFinishedAt - attemptStartedAt;
      if (activeSession !== session) {
        debug('glasses_command_transcription_discarded', {
          command_id: session.id,
          reason: 'session_cancelled',
        });
        return;
      }
      const normalizedTranscript = normalizeTranscriptText(result.result);
      const { transcript, wakeWordPrefixRemoved, removalMethod } = stripWakeWordPrefix(
        normalizedTranscript,
        session.wakeTiming.wakePhrase,
      );
      debug('glasses_command_transcribed', {
        command_id: session.id,
        transcript,
        raw_transcript: result.result,
        normalized_transcript: normalizedTranscript,
        wake_word_prefix_removed: wakeWordPrefixRemoved,
        wake_word_removal_method: removalMethod,
        wake_phrase_model_label: session.wakeTiming.wakePhrase,
        wake_decision_audio_time_ms: session.wakeTiming.detectionAudioTimeMs,
        wake_pre_roll_start_audio_time_ms: session.wakeTiming.preRollStartAudioTimeMs,
        wake_pre_roll_end_audio_time_ms: session.wakeTiming.preRollEndAudioTimeMs,
        language: result.language,
        model: LOCAL_WHISPER_MODEL_FILE_NAME,
        ...whisperAcceleration(context),
        segment_count: result.segments.length,
        aborted: result.isAborted,
        audio_duration_ms: audioDurationMs,
        model_wait_ms: modelReadyAt - transcriptionStartedAt,
        transcription_ms: transcriptionMs,
        transcription_attempt_count: transcriptionAttempt,
        whisper_context_recovery_ms: retryRecoveryMs,
        transcription_total_ms: transcriptionFinishedAt - initialModelReadyAt,
        wake_to_transcript_ms: transcriptionFinishedAt - session.wakeDetectedAt,
      });
      onTranscribed?.({
        commandId: session.id,
        transcript,
        rawTranscript: result.result,
        language: result.language,
        audioDurationMs,
        wakeDetectedAt: session.wakeDetectedAt,
        clientTimings: {
          wake_to_listening_start_ms: listeningStartedAt - session.wakeDetectedAt,
          wake_to_speech_start_ms: session.speechStartedAt
            ? session.speechStartedAt - session.wakeDetectedAt
            : null,
          wake_to_listening_end_ms: listeningEndedAt - session.wakeDetectedAt,
          wake_to_transcription_start_ms: transcriptionStartedAt - session.wakeDetectedAt,
          model_wait_ms: modelReadyAt - transcriptionStartedAt,
          transcription_ms: transcriptionMs,
          transcription_total_ms: transcriptionFinishedAt - initialModelReadyAt,
          wake_to_transcript_ms: transcriptionFinishedAt - session.wakeDetectedAt,
          audio_duration_ms: audioDurationMs,
          transcription_attempt_count: transcriptionAttempt,
        },
      });
    } catch (error) {
      debug('glasses_command_transcription_failed', {
        command_id: session.id,
        ...errorDetails(error),
        audio_duration_ms: audioDurationMs,
        wake_to_failure_ms: Date.now() - session.wakeDetectedAt,
      });
      // Cancellation and a normal no-speech endpoint intentionally do not
      // reach this callback. Only report a real model/transcription failure
      // while this session still owns the transcription boundary.
      if (activeSession === session) {
        onTranscriptionFailed?.({
          commandId: session.id,
          wakeDetectedAt: session.wakeDetectedAt,
          error: error instanceof Error ? error.message : String(error),
        });
      }
    } finally {
      if (activeSession === session) {
        activeSession = null;
        state = 'idle';
      }
    }
  })();
}

function acceptListeningPcm(
  session: CommandSession,
  samples: Int16Array,
  onListeningFinished: (event: GlassesCommandListeningFinished) => void,
  onTranscribed?: (event: GlassesCommandTranscribed) => void,
  onTranscriptionFailed?: (event: GlassesCommandTranscriptionFailed) => void,
  sampleAt = Date.now(),
): void {
  if (session.lastPcmAt !== null) {
    session.largestPcmGapMs = Math.max(session.largestPcmGapMs, sampleAt - session.lastPcmAt);
  }
  session.lastPcmAt = sampleAt;
  session.pcmChunkCount += 1;
  const filteredSamples = session.filter.process(samples);
  session.chunks.push(samples);
  session.filteredChunks.push(filteredSamples);
  session.samples += samples.length;
  const rawLevel = rms(samples);
  const level = rms(filteredSamples);
  session.minimumRms = session.minimumRms === null ? level : Math.min(session.minimumRms, level);
  session.maximumRms = session.maximumRms === null ? level : Math.max(session.maximumRms, level);
  session.minimumRawRms =
    session.minimumRawRms === null ? rawLevel : Math.min(session.minimumRawRms, rawLevel);
  session.maximumRawRms =
    session.maximumRawRms === null ? rawLevel : Math.max(session.maximumRawRms, rawLevel);
  if (level >= MIN_AMBIENT_RMS) {
    session.ambientRms.push(level);
    if (session.ambientRms.length > AMBIENT_RMS_HISTORY_SIZE) session.ambientRms.shift();
    session.currentThreshold = speechThreshold(session.ambientRms);
  }
  // The detector fires while the wearer is still finishing “hey brain”. Keep
  // those samples in the audio passed to Whisper, but do not let the wake
  // phrase arm silence endpointing before the actual command starts.
  if (sampleAt < session.wakeTailIgnoreUntil) return;
  const startsSpeech = level >= session.currentThreshold;
  const keepsListening =
    level >= continuationThreshold(session.currentThreshold, session.ambientRms);
  if (startsSpeech) {
    session.speechCandidateStartedAt ??= sampleAt;
    if (
      !session.speechStartedAt &&
      sampleAt - session.speechCandidateStartedAt >= COMMAND_MIN_SPEECH_MS
    ) {
      session.speechStartedAt = session.speechCandidateStartedAt;
      debug('glasses_command_speech_started', {
        command_id: session.id,
        wake_to_speech_ms: session.speechStartedAt - session.wakeDetectedAt,
        rms: level,
        start_threshold: session.currentThreshold,
        continuation_threshold: continuationThreshold(session.currentThreshold, session.ambientRms),
        ambient_rms_p20: percentile(session.ambientRms, 0.2),
      });
    }
  } else {
    session.speechCandidateStartedAt = null;
  }

  if (keepsListening) {
    const candidateInterrupted =
      session.continuationCandidateLastAt !== null &&
      sampleAt - session.continuationCandidateLastAt > COMMAND_CONTINUATION_MIN_MS;
    if (session.continuationCandidateStartedAt === null || candidateInterrupted) {
      session.continuationCandidateStartedAt = sampleAt;
    }
    session.continuationCandidateLastAt = sampleAt;
    if (sampleAt - session.continuationCandidateStartedAt >= COMMAND_CONTINUATION_MIN_MS) {
      session.lastSpeechAt = sampleAt;
      session.strongSpeechChunkCount += 1;
    }
  } else {
    session.continuationCandidateStartedAt = null;
    session.continuationCandidateLastAt = null;
    if (startsSpeech) session.weakAudioChunkCount += 1;
  }

  const endpointReferenceAt = session.lastSpeechAt ?? session.speechStartedAt;
  if (
    session.speechStartedAt &&
    endpointReferenceAt &&
    sampleAt >= session.initialEndpointAllowedAt &&
    sampleAt - endpointReferenceAt >= COMMAND_SILENCE_MS
  ) {
    endListening(session, 'silence', onListeningFinished, onTranscribed, onTranscriptionFailed);
  }
}

export function startGlassesCommandTranscription(
  wakeDetectedAt: number,
  onListeningFinished: (event: GlassesCommandListeningFinished) => void,
  postWakeBufferedChunks: Int16Array[] = [],
  wakeTiming: WakeCommandTiming,
  onTranscribed?: (event: GlassesCommandTranscribed) => void,
  onTranscriptionFailed?: (event: GlassesCommandTranscriptionFailed) => void,
): void {
  if (state !== 'idle') {
    debug('glasses_command_start_ignored', { state });
    return;
  }
  const session: CommandSession = {
    id: createCommandId(),
    wakeDetectedAt,
    listeningStartedAt: null,
    speechStartedAt: null,
    lastSpeechAt: null,
    speechCandidateStartedAt: null,
    continuationCandidateStartedAt: null,
    continuationCandidateLastAt: null,
    graceChunks: postWakeBufferedChunks,
    chunks: [],
    filteredChunks: [],
    filter: new AudioBandPassFilter(),
    samples: 0,
    ambientRms: [...ambientRmsHistory],
    minimumRms: null,
    maximumRms: null,
    minimumRawRms: null,
    maximumRawRms: null,
    currentThreshold: speechThreshold(ambientRmsHistory),
    strongSpeechChunkCount: 0,
    weakAudioChunkCount: 0,
    maximumTimer: null,
    pcmChunkCount: 0,
    lastPcmAt: null,
    largestPcmGapMs: 0,
    wakeTailIgnoreUntil: wakeDetectedAt + COMMAND_WAKE_TAIL_IGNORE_MS,
    initialEndpointAllowedAt: wakeDetectedAt + COMMAND_INITIAL_COMMAND_WINDOW_MS,
    wakeTiming,
  };
  activeSession = session;
  state = 'grace';
  debug('glasses_command_grace_started', {
    command_id: session.id,
    grace_ms: COMMAND_GRACE_MS,
    prebuffered_audio_duration_ms: Math.round(
      (postWakeBufferedChunks.reduce((total, chunk) => total + chunk.length, 0) / 16_000) * 1_000,
    ),
    wake_phrase_model_label: wakeTiming.wakePhrase,
    wake_decision_audio_time_ms: wakeTiming.detectionAudioTimeMs,
    wake_pre_roll_start_audio_time_ms: wakeTiming.preRollStartAudioTimeMs,
    wake_pre_roll_end_audio_time_ms: wakeTiming.preRollEndAudioTimeMs,
  });
  void warmGlassesCommandTranscription().catch(() => undefined);
  const beginListening = () => {
    if (activeSession !== session || state !== 'grace') return;
    state = 'listening';
    const listeningStartedAt = Date.now();
    session.listeningStartedAt = listeningStartedAt;
    session.maximumTimer = setTimeout(
      () =>
        endListening(session, 'timeout', onListeningFinished, onTranscribed, onTranscriptionFailed),
      COMMAND_MAX_LISTENING_MS,
    );
    const graceAudioDurationMs = Math.round(
      (session.graceChunks.reduce((total, chunk) => total + chunk.length, 0) / 16_000) * 1_000,
    );
    debug('glasses_command_listening_started', {
      command_id: session.id,
      wake_to_listening_ms: listeningStartedAt - session.wakeDetectedAt,
      silence_endpoint_ms: COMMAND_SILENCE_MS,
      initial_command_window_ms: COMMAND_INITIAL_COMMAND_WINDOW_MS,
      maximum_listening_ms: COMMAND_MAX_LISTENING_MS,
      ambient_rms_p20: percentile(session.ambientRms, 0.2),
      speech_rms_threshold: session.currentThreshold,
      continuation_rms_threshold: continuationThreshold(
        session.currentThreshold,
        session.ambientRms,
      ),
      continuation_noise_ratio: COMMAND_CONTINUATION_NOISE_RATIO,
      filter_low_hz: COMMAND_FILTER_LOW_HZ,
      filter_high_hz: COMMAND_FILTER_HIGH_HZ,
      continuation_min_ms: COMMAND_CONTINUATION_MIN_MS,
      grace_audio_duration_ms: graceAudioDurationMs,
      grace_timer_delay_ms: 0,
      wake_tail_ignore_ms: COMMAND_WAKE_TAIL_IGNORE_MS,
    });
    const graceChunks = session.graceChunks;
    session.graceChunks = [];
    let graceChunkEndedAt = listeningStartedAt - graceAudioDurationMs;
    for (const graceChunk of graceChunks) {
      if (activeSession !== session || state !== 'listening') return;
      graceChunkEndedAt += (graceChunk.length / 16_000) * 1_000;
      acceptListeningPcm(
        session,
        graceChunk,
        onListeningFinished,
        onTranscribed,
        onTranscriptionFailed,
        graceChunkEndedAt,
      );
    }
  };
  beginListening();
}

export function acceptGlassesCommandPcm(
  samples: Int16Array,
  onListeningFinished: (event: GlassesCommandListeningFinished) => void,
  onTranscribed?: (event: GlassesCommandTranscribed) => void,
  onTranscriptionFailed?: (event: GlassesCommandTranscriptionFailed) => void,
): void {
  const session = activeSession;
  if (!session) return;
  if (state === 'grace') {
    session.graceChunks.push(samples);
    return;
  }
  if (state !== 'listening') return;
  acceptListeningPcm(session, samples, onListeningFinished, onTranscribed, onTranscriptionFailed);
}

export function cancelGlassesCommandTranscription(reason: string): void {
  const session = activeSession;
  if (!session) return;
  const previousState = state;
  clearMaximumTimer(session);
  activeSession = null;
  state = 'idle';
  debug('glasses_command_cancelled', {
    command_id: session.id,
    reason,
    state: previousState,
    wake_to_cancel_ms: Date.now() - session.wakeDetectedAt,
  });
}
