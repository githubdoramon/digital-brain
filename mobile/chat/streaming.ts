import { fetch as expoFetch } from 'expo/fetch';

import { apiFetch, API_BASE_URL } from '@/api/client';
import type { CommandResult as ThreadCommandResult } from '@/chat/threads';
import type { UiDirectives, UiSubmissionInput } from '@/chat/uiDirectives';
import { getClientContext } from '@/location/clientContext';

type CommandResult = ThreadCommandResult;

export type AskResponse = {
  answer?: string;
  thread_id?: string | null;
  is_new_session?: boolean;
  pending_event_id?: string | null;
  command_result?: CommandResult;
  ui_directives?: UiDirectives;
};

type StreamEvent =
  | { type: 'token'; content?: string }
  | { type: 'clear_content' }
  | { type: 'status'; message?: string }
  | { type: 'tool_call'; name?: string; args?: unknown }
  | { type: 'tool_result'; name?: string }
  | { type: 'session_info'; thread_id?: string; is_new_session?: boolean; run_id?: string }
  | { type: 'done'; bundle?: AskResponse }
  | { type: 'error'; message?: string }
  | { type: string; [key: string]: unknown };

export type StreamCallbacks = {
  onSessionInfo?: (threadId: string) => void;
  onRunId?: (runId: string) => void;
  onStatus?: (message: string) => void;
  onToken?: (delta: string) => void;
  onClearContent?: () => void;
  onProgressChip?: (chip: string) => void;
};

type AskRunStatus = {
  run_id: string;
  thread_id?: string | null;
  status: 'running' | 'completed' | 'failed' | 'cancelled' | string;
  status_message?: string | null;
  result?: AskResponse | null;
  error?: { code?: string; message?: string } | null;
};

type StreamAskParams = {
  token?: string | null;
  question: string;
  pendingEventId?: string | null;
  uiSubmission?: UiSubmissionInput;
  callbacks?: StreamCallbacks;
};

const STREAM_DEBUG_ENABLED = __DEV__;

function logStreamDebug(message: string, details?: Record<string, unknown>) {
  if (!STREAM_DEBUG_ENABLED) {
    return;
  }
  if (details) {
    console.info(`[ask-stream] ${message}`, details);
    return;
  }
  console.info(`[ask-stream] ${message}`);
}

function formatToolArgValue(value: unknown): string {
  if (value === null || value === undefined) return 'null';
  if (typeof value === 'string') {
    const compact = value.trim().replace(/\s+/g, ' ');
    return compact.length > 36 ? `${compact.slice(0, 33)}...` : compact;
  }
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  if (Array.isArray(value)) return `list(${value.length})`;
  if (typeof value === 'object') return 'object';
  return String(value);
}

function humanToolName(toolNameRaw: string): string {
  const toolName = toolNameRaw.trim();
  const aliases: Record<string, string> = {
    search_memories: 'Searching memory',
    get_events: 'Checking an event',
    get_document: 'Looking at a document',
    resolve_contacts: 'Resolving contacts',
    lookup_contact: 'Looking up a contact',
    select_contacts: 'Selecting contacts',
    web_search: 'Searching the web',
    fetch_web_page: 'Fetching a web page',
    home_assistant: 'Using home assistant',
    run_skill_script: 'Running a skill script',
    emit_ui_directive: 'Building a response card',
    bash: 'Running a system command',
  };
  return aliases[toolName] || toolName.replace(/_/g, ' ');
}

function normalizeToolArgs(argsRaw: unknown): Record<string, unknown> {
  if (argsRaw && typeof argsRaw === 'object' && !Array.isArray(argsRaw)) {
    return argsRaw as Record<string, unknown>;
  }
  if (typeof argsRaw === 'string') {
    const text = argsRaw.trim();
    if (!text) return {};
    try {
      const parsed = JSON.parse(text) as unknown;
      if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
        return parsed as Record<string, unknown>;
      }
    } catch {
      return {};
    }
  }
  return {};
}

function buildToolProgressChip(toolNameRaw: string, argsRaw: unknown): string {
  const toolName = toolNameRaw.trim();
  if (!toolName) return '';

  const humanName = humanToolName(toolName);
  const args = normalizeToolArgs(argsRaw);
  const preferredKeys = ['query', 'url', 'limit', 'max_results', 'topic', 'contact_ids'];
  const preferredEntries = preferredKeys
    .filter((key) => key in args)
    .map((key) => [key, args[key]] as const);
  const sourceEntries = preferredEntries.length > 0 ? preferredEntries : Object.entries(args);
  const entries = Object.entries(args).filter(([, value]) => value !== undefined);
  const selectedEntries = (sourceEntries.length > 0 ? sourceEntries : entries)
    .slice(0, 2)
    .map(([key, value]) => `${key}: ${formatToolArgValue(value)}`);

  if (selectedEntries.length === 0) {
    return `${humanName}`;
  }

  return `${humanName} (${selectedEntries.join(', ')})`;
}

function parseSseEventLine(line: string): StreamEvent | null {
  if (!line.startsWith('data: ')) {
    return null;
  }
  const raw = line.slice(6).trim();
  if (!raw) {
    return null;
  }
  try {
    return JSON.parse(raw) as StreamEvent;
  } catch {
    logStreamDebug('failed to parse event line', {
      rawPreview: raw.slice(0, 220),
    });
    return null;
  }
}

function delay(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function shouldTryRunPolling(error: unknown): boolean {
  if (!(error instanceof Error)) {
    return false;
  }
  const text = error.message.toLowerCase();
  return (
    text.includes('connection abort') ||
    text.includes('network request failed') ||
    text.includes('stream ended before final response bundle') ||
    text.includes('terminated')
  );
}

export async function waitForRunCompletion(
  runId: string,
  token: string | null | undefined,
  callbacks?: StreamCallbacks,
  options?: {
    intervalMs?: number;
    maxAttempts?: number;
  },
): Promise<AskResponse> {
  if (!token) {
    throw new Error('Missing token for run polling');
  }

  const intervalMs = options?.intervalMs ?? 2000;
  const maxAttempts = options?.maxAttempts ?? 45;

  for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
    if (attempt > 0) {
      await delay(intervalMs);
    }

    const run = (await apiFetch(`/mobile/ask/runs/${encodeURIComponent(runId)}`, {
      token,
    })) as AskRunStatus;

    logStreamDebug('poll run status', {
      runId,
      attempt: attempt + 1,
      status: run.status,
      hasResult: Boolean(run.result),
      hasError: Boolean(run.error),
    });

    const statusMessage = (run.status_message || '').trim();
    if (statusMessage) {
      callbacks?.onStatus?.(statusMessage);
    }

    if (run.thread_id) {
      callbacks?.onSessionInfo?.(run.thread_id);
    }

    if (run.status === 'completed' && run.result) {
      return run.result;
    }

    if (run.status === 'failed' || run.status === 'cancelled') {
      const message = run.error?.message || 'Request failed while reconnecting';
      throw new Error(message);
    }
  }

  throw new Error('Timed out waiting for run to complete');
}

export async function askWithStreaming({
  token,
  question,
  pendingEventId,
  uiSubmission,
  callbacks,
}: StreamAskParams): Promise<AskResponse> {
  const payload = {
    question,
    pending_event_id: pendingEventId ?? undefined,
    client_context: getClientContext(),
    ui_submission: uiSubmission ?? undefined,
  };

  const streamResponse = await expoFetch(`${API_BASE_URL}/mobile/ask/stream`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify(payload),
  });

  if (!streamResponse.ok) {
    const rawErrorBody = await streamResponse.text();
    let detail = rawErrorBody || `Request failed with ${streamResponse.status}`;
    try {
      const parsed = JSON.parse(rawErrorBody) as { detail?: string };
      if (typeof parsed.detail === 'string' && parsed.detail.trim()) {
        detail = parsed.detail.trim();
      }
    } catch {
      // Ignore non-JSON error body and use raw text fallback.
    }

    const error = new Error(detail) as Error & { status?: number; authExpired?: boolean };
    error.status = streamResponse.status;
    if (streamResponse.status === 401) {
      error.authExpired = true;
    }
    throw error;
  }

  const headerRunId = streamResponse.headers.get('x-ask-run-id')?.trim() || null;
  const headerThreadId = streamResponse.headers.get('x-ask-thread-id')?.trim() || null;
  logStreamDebug('stream opened', {
    status: streamResponse.status,
    runId: headerRunId,
    threadId: headerThreadId,
  });

  let doneBundle: AskResponse | null = null;
  let runId: string | null = headerRunId;
  if (runId) {
    callbacks?.onRunId?.(runId);
  }
  if (headerThreadId) {
    callbacks?.onSessionInfo?.(headerThreadId);
  }
  const reader = streamResponse.body?.getReader();
  if (!reader) {
    return (await apiFetch('/mobile/ask', {
      method: 'POST',
      body: JSON.stringify(payload),
      token,
    })) as AskResponse;
  }

  const decoder = new TextDecoder();
  let buffer = '';
  let tokenEventCount = 0;

  const handleParsedEvent = (event: StreamEvent): AskResponse | null => {
    const eventType = String(event.type || 'unknown');
    if (eventType !== 'token') {
      logStreamDebug('event received', {
        type: eventType,
        runId,
      });
    }

    if (event.type === 'session_info') {
      if (event.thread_id) {
        callbacks?.onSessionInfo?.(event.thread_id);
      }
      if (event.run_id) {
        runId = event.run_id;
        callbacks?.onRunId?.(event.run_id);
      }
      return null;
    }

    if (event.type === 'status') {
      const statusMessage = typeof event.message === 'string' ? event.message.trim() : '';
      if (statusMessage) {
        callbacks?.onStatus?.(statusMessage);
      }
      return null;
    }

    if (event.type === 'tool_call') {
      const toolName = typeof event.name === 'string' ? event.name.trim() : '';
      if (toolName) {
        logStreamDebug('tool call', {
          toolName,
          runId,
        });
        const chip = buildToolProgressChip(toolName, event.args);
        if (chip) {
          callbacks?.onProgressChip?.(chip);
        }
      }
      return null;
    }

    if (event.type === 'tool_result') {
      return null;
    }

    if (event.type === 'token') {
      const delta = typeof event.content === 'string' ? event.content : '';
      if (delta) {
        tokenEventCount += 1;
        if (tokenEventCount <= 3 || tokenEventCount % 25 === 0) {
          logStreamDebug('token event', {
            runId,
            tokenEventCount,
            chars: delta.length,
          });
        }
        callbacks?.onToken?.(delta);
      }
      return null;
    }

    if (event.type === 'clear_content') {
      callbacks?.onClearContent?.();
      return null;
    }

    if (event.type === 'error') {
      throw new Error(event.message || 'Stream ended with an error');
    }

    if (event.type === 'done') {
      logStreamDebug('done bundle received', {
        runId,
        hasBundle: Boolean(event.bundle),
      });
      return event.bundle ?? null;
    }

    return null;
  };

  const consumeBuffer = (flushRemainder: boolean): AskResponse | null => {
    const lines = buffer.split('\n');
    buffer = lines.pop() || '';

    for (const line of lines) {
      const event = parseSseEventLine(line);
      if (!event) continue;
      const maybeDoneBundle = handleParsedEvent(event);
      if (maybeDoneBundle) {
        return maybeDoneBundle;
      }
    }

    if (flushRemainder) {
      const tail = buffer.trim();
      if (tail) {
        const event = parseSseEventLine(tail);
        if (event) {
          const maybeDoneBundle = handleParsedEvent(event);
          if (maybeDoneBundle) {
            return maybeDoneBundle;
          }
        }
      }
      buffer = '';
    }

    return null;
  };

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (value) {
        buffer += decoder.decode(value, { stream: !done });
        const maybeDoneBundle = consumeBuffer(done);
        if (maybeDoneBundle) {
          doneBundle = maybeDoneBundle;
        }
      } else if (done) {
        const maybeDoneBundle = consumeBuffer(true);
        if (maybeDoneBundle) {
          doneBundle = maybeDoneBundle;
        }
      }

      if (done || doneBundle) {
        break;
      }
    }
  } catch (error) {
    if (runId && shouldTryRunPolling(error)) {
      logStreamDebug('stream interrupted, switching to polling', {
        runId,
        reason: error instanceof Error ? error.message : String(error),
      });
      callbacks?.onStatus?.('Reconnecting...');
      return waitForRunCompletion(runId, token, callbacks);
    }
    throw error;
  }

  if (!doneBundle) {
    if (runId) {
      logStreamDebug('stream ended without done bundle, polling', {
        runId,
      });
      callbacks?.onStatus?.('Reconnecting...');
      return waitForRunCompletion(runId, token, callbacks);
    }
    throw new Error('Stream ended before final response bundle');
  }

  return doneBundle;
}
