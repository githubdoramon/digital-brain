import * as FileSystem from 'expo-file-system/legacy';

import { API_BASE_URL, apiFetch, getAuthRequestContext } from '@/api/client';
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
    elapsed_ms: Date.now() - command.startedAt,
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
  const { token } = await getAuthRequestContext();
  if (!token) throw new Error('Authentication is unavailable for glasses audio.');
  const destination = temporaryAudioUri(command.commandId);
  await FileSystem.deleteAsync(destination, { idempotent: true }).catch(() => undefined);
  debug('glasses_command_audio_download_started', { command_id: command.commandId });
  const downloadStartedAt = Date.now();
  const result = await FileSystem.downloadAsync(endpoint, destination, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!isCommandLive(command)) {
    await FileSystem.deleteAsync(destination, { idempotent: true }).catch(() => undefined);
    throw new Error('Glasses command timed out before audio was ready.');
  }
  const info = await FileSystem.getInfoAsync(result.uri);
  const size = 'size' in info && typeof info.size === 'number' ? info.size : 0;
  if (result.status < 200 || result.status >= 300 || !info.exists || size <= 0) {
    await FileSystem.deleteAsync(destination, { idempotent: true }).catch(() => undefined);
    throw new Error(`Glasses audio download failed with status ${result.status}.`);
  }
  debug('glasses_command_audio_download_ready', {
    command_id: command.commandId,
    download_ms: Date.now() - downloadStartedAt,
    elapsed_ms: Date.now() - command.startedAt,
    size_bytes: size,
  });
  return destination;
}

async function playSpeechAudio(command: ActiveCommand, fileUri: string): Promise<void> {
  const native = GlassesAlertsNative;
  if (!native) throw new Error('Glasses speech playback is unavailable in this build.');
  setState(command, 'playing_audio');
  if (!isCommandLive(command)) return;
  await blinkMentraOrangeLed();
  if (!isCommandLive(command)) return;
  const playbackStartedAt = Date.now();
  await new Promise<void>((resolve, reject) => {
    const subscription = native.addListener('onSpeechPlaybackFinished', (event) => {
      if (event.commandId !== command.commandId) return;
      subscription.remove();
      debug('glasses_command_audio_playback_finished', {
        command_id: command.commandId,
        playback_ms: Date.now() - playbackStartedAt,
        native_duration_ms: event.durationMs,
        status: event.status,
      });
      if (event.status === 'completed') resolve();
      else reject(new Error(event.error || 'Glasses speech playback failed.'));
    });
    void native
      .playSpeechAudio(command.commandId, fileUri)
      .then((result) => {
        if (!result.started) {
          subscription.remove();
          reject(new Error('The Mentra glasses audio route is unavailable.'));
        }
      })
      .catch((error) => {
        subscription.remove();
        reject(error);
      });
  });
}

async function executeCommand(
  command: ActiveCommand,
  transcript: GlassesCommandTranscribed,
): Promise<GlassesCommandResponse> {
  setState(command, 'executing');
  const clientContext = getClientContext();
  const sessionModule = await import('@/chat/session');
  const session = await sessionModule.loadChatSession().catch(() => null);
  const context = {
    commandId: transcript.commandId,
    transcript: transcript.transcript,
    timezone: clientContext.timezone,
    location: clientContext.location,
  };
  const localResult = await interceptDeviceCommand(context);
  if (localResult) return responseOutcome(localResult);

  const body = {
    command_id: transcript.commandId,
    transcript: transcript.transcript,
    thread_id: session?.threadId ?? undefined,
    client_context: clientContext,
  };
  debug('glasses_command_transport_started', {
    command_id: transcript.commandId,
    has_thread: Boolean(session?.threadId),
    has_location: Boolean(clientContext.location),
  });
  const transportStartedAt = Date.now();
  const response = await apiFetch('/mobile/glasses/commands', {
    method: 'POST',
    body: JSON.stringify({ ...body, client_timings: transcript.clientTimings }),
  });
  debug('glasses_command_transport_completed', {
    command_id: transcript.commandId,
    request_ms: Date.now() - transportStartedAt,
    outcome:
      response && typeof response === 'object'
        ? (response as Record<string, unknown>).outcome
        : null,
  });
  return responseOutcome(response);
}

async function runCommandLifecycle(
  command: ActiveCommand,
  transcript: GlassesCommandTranscribed,
  hooks: GlassesCommandAgentHooks,
  onAudioFile: (uri: string) => void,
): Promise<GlassesCommandResponse> {
  await hooks.pauseListening();
  if (!isCommandLive(command)) throw new Error('Glasses command deadline reached.');
  const response = await executeCommand(command, transcript);
  if (!isCommandLive(command)) throw new Error('Glasses command completed after its deadline.');
  if (response.outcome === 'error') throw new Error(responseError(response));
  await persistResponseSession(response);
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
    startedAt: Date.now(),
    state: 'dispatching',
  };
  activeCommand = command;
  let temporaryAudio: string | null = null;
  let deadlineTimer: ReturnType<typeof setTimeout> | null = null;
  let timedOut = false;
  try {
    const deadline = new Promise<never>((_, reject) => {
      deadlineTimer = setTimeout(() => {
        timedOut = true;
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
      elapsed_ms: Date.now() - command.startedAt,
    });
  } catch (error) {
    if (timedOut) {
      setState(command, 'timed_out');
      activeCommand = null;
      debug('glasses_command_timed_out', {
        command_id: command.commandId,
        elapsed_ms: Date.now() - command.startedAt,
      });
      await GlassesAlertsNative?.stopSpeechAudio(command.commandId).catch(() => undefined);
    } else {
      setState(command, 'error');
      debug('glasses_command_failed', {
        command_id: command.commandId,
        elapsed_ms: Date.now() - command.startedAt,
        error: errorMessage(error),
      });
    }
    await blinkMentraRedLed().catch(() => undefined);
  } finally {
    if (deadlineTimer) clearTimeout(deadlineTimer);
    if (temporaryAudio) {
      await FileSystem.deleteAsync(temporaryAudio, { idempotent: true }).catch(() => undefined);
      debug('glasses_command_audio_cleaned', { command_id: command.commandId });
    }
    if (activeCommand === command) activeCommand = null;
    await hooks.resumeListening().catch((error) => {
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
