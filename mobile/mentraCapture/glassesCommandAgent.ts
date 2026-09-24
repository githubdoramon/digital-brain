import * as FileSystem from 'expo-file-system/legacy';
import { AppState } from 'react-native';

import {
  API_BASE_URL,
  apiFetch,
  getAuthRequestContext,
  getGlassesResponseTimingMetadata,
} from '@/api/client';
import GlassesAlertsNative from '@/modules/digital-brain-glasses-alerts/src';
import { getClientContext } from '@/location/clientContext';

import { appendMentraDebugLog, appendWakeCommandDebugLog } from './debug';
import { interceptDeviceCommand } from './commandRegistry';
import { blinkMentraOrangeLed, blinkMentraRedLed } from './sdk';
import type { GlassesCommandTranscribed } from './commandTranscription';

export const GLASSES_COMMAND_HARD_DEADLINE_MS = 70_000;
export const GLASSES_COMMAND_SHORTCUT_DEADLINE_MS = 10_000;
export const GLASSES_COMMAND_AGENT_DEADLINE_MS = 60_000;

export type GlassesCommandAgentState =
  | 'idle'
  | 'dispatching'
  | 'executing'
  | 'downloading_audio'
  | 'playing_audio'
  | 'completed'
  | 'error'
  | 'timed_out';

export type GlassesCommandOutcome =
  | 'control_completed'
  | 'shortcut_completed'
  | 'agent_response'
  | 'error';

export type GlassesCommandResponse = {
  outcome: GlassesCommandOutcome;
  command_id?: string;
  thread_id?: string | null;
  session_id?: string | null;
  pending_event_id?: string | null;
  answer?: string;
  audio_url?: string;
  audio_route?: string;
  audio?: { download_url?: string; audio_id?: string; expires_at?: string } | null;
  error?: { code?: string; message?: string } | string | null;
  [key: string]: unknown;
};

export type GlassesCommandAgentHooks = {
  pauseListening: () => Promise<void>;
  resumeListening: () => Promise<void>;
};

const NOOP_HOOKS: GlassesCommandAgentHooks = {
  pauseListening: async () => undefined,
  resumeListening: async () => undefined,
};

type ActiveCommand = {
  commandId: string;
  startedAt: number;
  state: GlassesCommandAgentState;
};

let activeCommand: ActiveCommand | null = null;

function monotonicNowMs(): number {
  return typeof performance !== 'undefined' && typeof performance.now === 'function'
    ? performance.now()
    : Date.now();
}

function debug(event: string, payload?: Record<string, unknown>): void {
  void appendMentraDebugLog(event, payload).catch(() => undefined);
  void appendWakeCommandDebugLog(event, payload).catch(() => undefined);
}

export function getGlassesCommandAgentState(): GlassesCommandAgentState {
  return activeCommand?.state ?? 'idle';
}

export function getGlassesCommandInFlight(): {
  commandId: string;
  state: GlassesCommandAgentState;
} | null {
  return activeCommand ? { commandId: activeCommand.commandId, state: activeCommand.state } : null;
}

function setState(command: ActiveCommand, state: GlassesCommandAgentState): void {
  if (
    activeCommand !== command ||
    command.state === 'timed_out' ||
    command.state === 'error' ||
    command.state === 'completed'
  )
    return;
  command.state = state;
  debug('glasses_command_agent_state', {
    command_id: command.commandId,
    state,
    elapsed_ms: Math.round(monotonicNowMs() - command.startedAt),
  });
}

function isCommandLive(command: ActiveCommand): boolean {
  return activeCommand === command && command.state !== 'timed_out' && command.state !== 'error';
}

function errorMessage(error: unknown): string {
  if (error instanceof Error) return error.message;
  if (typeof error === 'string') return error;
  if (error && typeof error === 'object' && 'message' in error) return String(error.message);
  return 'Glasses command failed.';
}

export function responseOutcome(value: unknown): GlassesCommandResponse {
  if (!value || typeof value !== 'object')
    throw new Error('Glasses command returned an invalid response.');
  const response = value as Record<string, unknown>;
  const outcome = response.outcome ?? response.type;
  if (
    outcome !== 'control_completed' &&
    outcome !== 'shortcut_completed' &&
    outcome !== 'agent_response' &&
    outcome !== 'error'
  ) {
    throw new Error('Glasses command returned an unknown outcome.');
  }
  return { ...(response as GlassesCommandResponse), outcome };
}

function responseError(response: GlassesCommandResponse): string {
  if (typeof response.error === 'string') return response.error;
  if (response.error && typeof response.error === 'object' && response.error.message) {
    return response.error.message;
  }
  return 'The glasses command was not completed.';
}

async function persistResponseSession(response: GlassesCommandResponse): Promise<void> {
  const responseThreadId = response.thread_id ?? response.session_id ?? null;
  if (!responseThreadId) return;
  const sessionModule = await import('@/chat/session');
  const current = await sessionModule.loadChatSession().catch(() => null);
  const hasPendingEvent = Object.prototype.hasOwnProperty.call(response, 'pending_event_id');
  const pendingEventId = hasPendingEvent
    ? (response.pending_event_id ?? null)
    : response.outcome === 'shortcut_completed' ||
        (current?.threadId && current.threadId !== responseThreadId)
      ? null
      : (current?.pendingEventId ?? null);
  await sessionModule.saveChatSession({
    threadId: responseThreadId,
    pendingEventId,
  });
  debug('glasses_command_session_updated', {
    command_id: response.command_id,
    has_thread: true,
    has_pending_event: Boolean(pendingEventId),
  });
}

export function resolveAudioRoute(response: GlassesCommandResponse): string | null {
  const route =
    response.audio_url ??
    response.audio_route ??
    (response.audio && typeof response.audio === 'object'
      ? response.audio.download_url
      : undefined);
  if (typeof route !== 'string' || !route.trim()) return null;
  return route.startsWith('http://') || route.startsWith('https://')
    ? route
    : `${API_BASE_URL}${route.startsWith('/') ? '' : '/'}${route}`;
}

function temporaryAudioUri(commandId: string): string {
  const base = FileSystem.cacheDirectory ?? FileSystem.documentDirectory;
  if (!base) throw new Error('Private temporary storage is unavailable.');
  return `${base}glasses-command-${commandId}.audio`;
}

async function downloadSpeechAudio(
  command: ActiveCommand,
  response: GlassesCommandResponse,
): Promise<string> {
  const endpoint = resolveAudioRoute(response);
  if (!endpoint) throw new Error('The agent response did not include an audio route.');
  const downloadStartedAt = monotonicNowMs();
  debug('glasses_command_audio_download_started', {
    command_id: command.commandId,
    client_download_started_at_ms: Date.now(),
  });
  let authContextMs = 0;
  let destinationPrepareMs = 0;
  let fileDownloadMs = 0;
  let fileValidationMs = 0;
  let responseStatus: number | null = null;
  let responseContentType = '';
  let responseTimingMetadata: Record<string, unknown> = {};
  let destination: string | null = null;
  try {
    let stageStartedAt = monotonicNowMs();
    const { token } = await getAuthRequestContext();
    authContextMs = monotonicNowMs() - stageStartedAt;
    if (!token) throw new Error('Authentication is unavailable for glasses audio.');
    stageStartedAt = monotonicNowMs();
    destination = temporaryAudioUri(command.commandId);
    await FileSystem.deleteAsync(destination, { idempotent: true }).catch(() => undefined);
    destinationPrepareMs = monotonicNowMs() - stageStartedAt;
    stageStartedAt = monotonicNowMs();
    const result = await FileSystem.downloadAsync(endpoint, destination, {
      headers: {
        Authorization: `Bearer ${token}`,
        'X-Glasses-Command-Id': command.commandId,
      },
    });
    responseStatus = result.status;
    responseContentType = result.headers?.['content-type'] ?? '';
    responseTimingMetadata = getGlassesResponseTimingMetadata(result.headers);
    fileDownloadMs = monotonicNowMs() - stageStartedAt;
    if (!isCommandLive(command)) {
      await FileSystem.deleteAsync(destination, { idempotent: true }).catch(() => undefined);
      throw new Error('Glasses command timed out before audio was ready.');
    }
    stageStartedAt = monotonicNowMs();
    const info = await FileSystem.getInfoAsync(result.uri);
    const size = 'size' in info && typeof info.size === 'number' ? info.size : 0;
    fileValidationMs = monotonicNowMs() - stageStartedAt;
    if (result.status < 200 || result.status >= 300 || !info.exists || size <= 0) {
      await FileSystem.deleteAsync(destination, { idempotent: true }).catch(() => undefined);
      throw new Error(`Glasses audio download failed with status ${result.status}.`);
    }
    debug('glasses_command_audio_download_ready', {
      command_id: command.commandId,
      auth_context_ms: Math.round(authContextMs),
      destination_prepare_ms: Math.round(destinationPrepareMs),
      file_download_ms: Math.round(fileDownloadMs),
      file_validation_ms: Math.round(fileValidationMs),
      elapsed_ms: Math.round(monotonicNowMs() - command.startedAt),
      size_bytes: size,
      response_status: responseStatus,
      response_content_type: responseContentType,
      ...responseTimingMetadata,
    });
    return destination;
  } catch (error) {
    debug('glasses_command_audio_download_failed', {
      command_id: command.commandId,
      auth_context_ms: Math.round(authContextMs),
      destination_prepare_ms: Math.round(destinationPrepareMs),
      file_download_ms: Math.round(fileDownloadMs),
      file_validation_ms: Math.round(fileValidationMs),
      elapsed_ms: Math.round(monotonicNowMs() - downloadStartedAt),
      response_status: responseStatus,
      response_content_type: responseContentType,
      ...responseTimingMetadata,
      error_name: error instanceof Error ? error.name : 'unknown',
    });
    if (destination) {
      await FileSystem.deleteAsync(destination, { idempotent: true }).catch(() => undefined);
    }
    throw error;
  }
}

async function playSpeechAudio(command: ActiveCommand, fileUri: string): Promise<void> {
  const native = GlassesAlertsNative;
  if (!native) throw new Error('Glasses speech playback is unavailable in this build.');
  setState(command, 'playing_audio');
  if (!isCommandLive(command)) return;
  const ledStartedAt = monotonicNowMs();
  await blinkMentraOrangeLed();
  debug('glasses_command_audio_playback_ready', {
    command_id: command.commandId,
    orange_led_ms: Math.round(monotonicNowMs() - ledStartedAt),
  });
  if (!isCommandLive(command)) return;
  const playbackRequestedAt = monotonicNowMs();
  await new Promise<void>((resolve, reject) => {
    const logPlaybackTelemetry = (event: {
      expectedDeviceId?: number;
      expectedDeviceName?: string;
      expectedDeviceType?: number;
      routedDeviceId?: number;
      routedDeviceName?: string;
      routedDeviceType?: number;
      routeVerified: boolean;
      audioFocusResult?: number;
      audioFocusGranted?: boolean;
      runtimeForegroundTypes: number;
      activityVisible: boolean;
      outputStreamVolume?: number;
      outputStreamVolumeMax?: number;
      outputStreamMuted?: boolean;
      playerDurationMs?: number;
      playerPositionMs?: number;
      playerIsPlaying?: boolean;
      playerAudioSessionId?: number;
      playerGain: number;
    }) => ({
      expected_device_id: event.expectedDeviceId,
      expected_device_name: event.expectedDeviceName,
      expected_device_type: event.expectedDeviceType,
      routed_device_id: event.routedDeviceId,
      routed_device_name: event.routedDeviceName,
      routed_device_type: event.routedDeviceType,
      route_verified: event.routeVerified,
      audio_focus_result: event.audioFocusResult,
      audio_focus_granted: event.audioFocusGranted,
      runtime_foreground_types: event.runtimeForegroundTypes,
      app_visible: event.activityVisible,
      output_stream_volume: event.outputStreamVolume,
      output_stream_volume_max: event.outputStreamVolumeMax,
      output_stream_muted: event.outputStreamMuted,
      player_duration_ms: event.playerDurationMs,
      player_position_ms: event.playerPositionMs,
      player_is_playing: event.playerIsPlaying,
      player_audio_session_id: event.playerAudioSessionId,
      player_gain: event.playerGain,
      playback_backend: 'Android MediaPlayer / USAGE_MEDIA / CONTENT_TYPE_SPEECH',
    });
    const startedSubscription = native.addListener('onSpeechPlaybackStarted', (event) => {
      if (event.commandId !== command.commandId) return;
      debug('glasses_command_audio_playback_started', {
        command_id: command.commandId,
        native_route_verified_ms: Math.round(monotonicNowMs() - playbackRequestedAt),
        ...logPlaybackTelemetry(event),
      });
    });
    const progressSubscription = native.addListener('onSpeechPlaybackProgress', (event) => {
      if (event.commandId !== command.commandId) return;
      debug('glasses_command_audio_playback_progress', {
        command_id: command.commandId,
        elapsed_ms: Math.round(monotonicNowMs() - playbackRequestedAt),
        ...logPlaybackTelemetry(event),
      });
    });
    const finishedSubscription = native.addListener('onSpeechPlaybackFinished', (event) => {
      if (event.commandId !== command.commandId) return;
      startedSubscription.remove();
      progressSubscription.remove();
      finishedSubscription.remove();
      debug('glasses_command_audio_playback_finished', {
        command_id: command.commandId,
        playback_ms: Math.round(monotonicNowMs() - playbackRequestedAt),
        native_duration_ms: event.durationMs,
        status: event.status,
        error: event.error,
        ...logPlaybackTelemetry(event),
      });
      if (event.status === 'completed') resolve();
      else reject(new Error(event.error || 'Glasses speech playback failed.'));
    });
    void native
      .playSpeechAudio(command.commandId, fileUri)
      .then((result) => {
        if (!result.started) {
          startedSubscription.remove();
          progressSubscription.remove();
          finishedSubscription.remove();
          debug('glasses_command_audio_playback_rejected', {
            command_id: command.commandId,
            reason: result.reason,
            expected_device_id: result.expectedDeviceId,
            expected_device_name: result.expectedDeviceName,
            expected_device_type: result.expectedDeviceType,
            available_audio_outputs: result.availableOutputs,
          });
          reject(new Error(result.reason || 'The Mentra glasses audio route is unavailable.'));
          return;
        }
        debug('glasses_command_audio_playback_request_accepted', {
          command_id: command.commandId,
          native_accept_ms: Math.round(monotonicNowMs() - playbackRequestedAt),
          expected_device_id: result.expectedDeviceId,
          expected_device_name: result.expectedDeviceName,
          expected_device_type: result.expectedDeviceType,
        });
      })
      .catch((error) => {
        startedSubscription.remove();
        progressSubscription.remove();
        finishedSubscription.remove();
        reject(error);
      });
  });
}

async function executeCommand(
  command: ActiveCommand,
  transcript: GlassesCommandTranscribed,
): Promise<GlassesCommandResponse> {
  setState(command, 'executing');
  const preparationStartedAt = monotonicNowMs();
  let stageStartedAt = monotonicNowMs();
  const clientContext = getClientContext();
  const clientContextMs = monotonicNowMs() - stageStartedAt;
  stageStartedAt = monotonicNowMs();
  const sessionModule = await import('@/chat/session');
  const session = await sessionModule.loadChatSession().catch(() => null);
  const sessionLoadMs = monotonicNowMs() - stageStartedAt;
  const context = {
    commandId: transcript.commandId,
    transcript: transcript.transcript,
    timezone: clientContext.timezone,
    location: clientContext.location,
  };
  stageStartedAt = monotonicNowMs();
  const localResult = await interceptDeviceCommand(context);
  const localCommandCheckMs = monotonicNowMs() - stageStartedAt;
  if (localResult) {
    const response = responseOutcome(localResult);
    debug('glasses_command_local_command_matched', {
      command_id: transcript.commandId,
      outcome: response.outcome,
      check_ms: Math.round(localCommandCheckMs),
    });
    return response;
  }
  debug('glasses_command_local_command_passthrough', {
    command_id: transcript.commandId,
    check_ms: Math.round(localCommandCheckMs),
  });

  const body = {
    command_id: transcript.commandId,
    transcript: transcript.transcript,
    thread_id: session?.threadId ?? undefined,
    client_context: clientContext,
  };
  debug('glasses_command_transport_started', {
    command_id: transcript.commandId,
    client_transport_started_at_ms: Date.now(),
    has_thread: Boolean(session?.threadId),
    has_location: Boolean(clientContext.location),
    client_context_ms: Math.round(clientContextMs),
    session_load_ms: Math.round(sessionLoadMs),
    local_command_check_ms: Math.round(localCommandCheckMs),
    preparation_ms: Math.round(monotonicNowMs() - preparationStartedAt),
    app_state: AppState.currentState,
  });
  const transportStartedAt = monotonicNowMs();
  try {
    const response = await apiFetch('/mobile/glasses/commands', {
      method: 'POST',
      headers: { 'X-Glasses-Command-Id': transcript.commandId },
      body: JSON.stringify({ ...body, client_timings: transcript.clientTimings }),
      onTiming: (phase, elapsedMs, metadata) => {
        debug('glasses_command_transport_phase', {
          command_id: transcript.commandId,
          phase,
          duration_ms: elapsedMs,
          ...metadata,
        });
      },
    });
    debug('glasses_command_transport_completed', {
      command_id: transcript.commandId,
      request_ms: Math.round(monotonicNowMs() - transportStartedAt),
      elapsed_ms: Math.round(monotonicNowMs() - command.startedAt),
      outcome:
        response && typeof response === 'object'
          ? (response as Record<string, unknown>).outcome
          : null,
    });
    return responseOutcome(response);
  } catch (error) {
    debug('glasses_command_transport_failed', {
      command_id: transcript.commandId,
      request_ms: Math.round(monotonicNowMs() - transportStartedAt),
      elapsed_ms: Math.round(monotonicNowMs() - command.startedAt),
      error_name: error instanceof Error ? error.name : 'unknown',
      status: error && typeof error === 'object' && 'status' in error ? error.status : undefined,
    });
    throw error;
  }
}

async function runCommandLifecycle(
  command: ActiveCommand,
  transcript: GlassesCommandTranscribed,
  hooks: GlassesCommandAgentHooks,
  onAudioFile: (uri: string) => void,
): Promise<GlassesCommandResponse> {
  const pauseStartedAt = monotonicNowMs();
  await hooks.pauseListening();
  debug('glasses_command_listener_paused', {
    command_id: command.commandId,
    pause_ms: Math.round(monotonicNowMs() - pauseStartedAt),
  });
  if (!isCommandLive(command)) throw new Error('Glasses command deadline reached.');
  const response = await executeCommand(command, transcript);
  if (!isCommandLive(command)) throw new Error('Glasses command completed after its deadline.');
  if (response.outcome === 'error') throw new Error(responseError(response));
  const sessionPersistStartedAt = monotonicNowMs();
  await persistResponseSession(response);
  debug('glasses_command_response_session_persisted', {
    command_id: command.commandId,
    persist_ms: Math.round(monotonicNowMs() - sessionPersistStartedAt),
    has_thread: Boolean(response.thread_id ?? response.session_id),
  });
  if (response.outcome === 'agent_response') {
    setState(command, 'downloading_audio');
    const audioFile = await downloadSpeechAudio(command, response);
    onAudioFile(audioFile);
    await playSpeechAudio(command, audioFile);
  } else if (
    response.outcome === 'shortcut_completed' ||
    response.outcome === 'control_completed'
  ) {
    await blinkMentraOrangeLed();
  }
  return response;
}

export async function dispatchGlassesCommand(
  transcript: GlassesCommandTranscribed,
  hooks: GlassesCommandAgentHooks = NOOP_HOOKS,
): Promise<void> {
  if (activeCommand) {
    debug('glasses_command_dispatch_ignored', {
      command_id: transcript.commandId,
      reason: 'command_in_flight',
    });
    return;
  }
  const command: ActiveCommand = {
    commandId: transcript.commandId,
    startedAt: monotonicNowMs(),
    state: 'dispatching',
  };
  activeCommand = command;
  debug('glasses_command_lifecycle_started', {
    command_id: command.commandId,
    app_state: AppState.currentState,
    client_timings: transcript.clientTimings,
  });
  let previousAppState = AppState.currentState;
  const appStateSubscription = AppState.addEventListener('change', (nextAppState) => {
    if (activeCommand !== command) return;
    debug('glasses_command_app_state_changed', {
      command_id: command.commandId,
      previous_state: previousAppState,
      app_state: nextAppState,
      elapsed_ms: Math.round(monotonicNowMs() - command.startedAt),
    });
    previousAppState = nextAppState;
  });
  let temporaryAudio: string | null = null;
  let deadlineTimer: ReturnType<typeof setTimeout> | null = null;
  let timedOut = false;
  const expectedDeadlineAt = Date.now() + GLASSES_COMMAND_HARD_DEADLINE_MS;
  let deadlineLateByMs: number | null = null;
  debug('glasses_command_deadline_scheduled', {
    command_id: command.commandId,
    deadline_ms: GLASSES_COMMAND_HARD_DEADLINE_MS,
    expected_deadline_at: new Date(expectedDeadlineAt).toISOString(),
    app_state: AppState.currentState,
  });
  try {
    const deadline = new Promise<never>((_, reject) => {
      deadlineTimer = setTimeout(() => {
        timedOut = true;
        const firedAt = Date.now();
        deadlineLateByMs = Math.max(0, firedAt - expectedDeadlineAt);
        debug('glasses_command_deadline_fired', {
          command_id: command.commandId,
          expected_deadline_at: new Date(expectedDeadlineAt).toISOString(),
          fired_at: new Date(firedAt).toISOString(),
          timer_late_by_ms: deadlineLateByMs,
          elapsed_ms: Math.round(monotonicNowMs() - command.startedAt),
          app_state: AppState.currentState,
        });
        reject(new Error('Glasses command exceeded the 70-second deadline.'));
      }, GLASSES_COMMAND_HARD_DEADLINE_MS);
    });
    const response = await Promise.race([
      runCommandLifecycle(command, transcript, hooks, (uri) => {
        temporaryAudio = uri;
      }),
      deadline,
    ]);
    if (!isCommandLive(command)) return;
    if (isCommandLive(command)) setState(command, 'completed');
    debug('glasses_command_completed', {
      command_id: command.commandId,
      outcome: response.outcome,
      elapsed_ms: Math.round(monotonicNowMs() - command.startedAt),
    });
  } catch (error) {
    if (timedOut) {
      setState(command, 'timed_out');
      activeCommand = null;
      debug('glasses_command_timed_out', {
        command_id: command.commandId,
        elapsed_ms: Math.round(monotonicNowMs() - command.startedAt),
        timer_late_by_ms: deadlineLateByMs,
        app_state: AppState.currentState,
      });
      await GlassesAlertsNative?.stopSpeechAudio(command.commandId).catch(() => undefined);
    } else {
      setState(command, 'error');
      debug('glasses_command_failed', {
        command_id: command.commandId,
        elapsed_ms: Math.round(monotonicNowMs() - command.startedAt),
        error: errorMessage(error),
      });
    }
    await blinkMentraRedLed().catch(() => undefined);
  } finally {
    if (deadlineTimer) clearTimeout(deadlineTimer);
    appStateSubscription.remove();
    if (temporaryAudio) {
      await FileSystem.deleteAsync(temporaryAudio, { idempotent: true }).catch(() => undefined);
      debug('glasses_command_audio_cleaned', { command_id: command.commandId });
    }
    if (activeCommand === command) activeCommand = null;
    const resumeStartedAt = monotonicNowMs();
    await hooks
      .resumeListening()
      .then(() => {
        debug('glasses_command_listener_resumed', {
          command_id: command.commandId,
          resume_ms: Math.round(monotonicNowMs() - resumeStartedAt),
        });
      })
      .catch((error) => {
        debug('glasses_command_listener_resume_failed', {
          command_id: command.commandId,
          error: errorMessage(error),
        });
        void blinkMentraRedLed().catch(() => undefined);
      });
  }
}

export function resetGlassesCommandAgentForTests(): void {
  activeCommand = null;
}
