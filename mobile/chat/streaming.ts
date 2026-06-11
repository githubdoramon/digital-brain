import { fetch as expoFetch } from 'expo/fetch';
import { AppState } from 'react-native';

import { apiFetch, API_BASE_URL } from '@/api/client';
import type { GeneratedFile } from '@/chat/generatedFiles';
import type { LinkedItem } from '@/chat/linkedItems';
import type { ChatMediaAttachmentPayload } from '@/chat/mediaAttachments';
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
  linked_items?: LinkedItem[];
  generated_files?: GeneratedFile[];
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

type StreamRequestError = Error & {
  status?: number;
  authExpired?: boolean;
  isReconnectable?: boolean;
  errorCode?: string;
};

type StreamAskParams = {
  token?: string | null;
  question: string;
  threadId?: string | null;
  pendingCommandId?: string | null;
  uiSubmission?: UiSubmissionInput;
  mediaAttachments?: ChatMediaAttachmentPayload[];
  callbacks?: StreamCallbacks;
};

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
    create_pdf: 'Creating a PDF',
    ingest_generated_pdf: 'Saving a PDF',
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
    return null;
  }
}

function delay(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function shouldTryRunPolling(error: unknown): boolean {
  if (error instanceof Error) {
    const typedError = error as StreamRequestError;
    if (typedError.isReconnectable === false) {
      return false;
    }
  }
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

function buildStreamRequestError(
  message: string,
  options?: {
    status?: number;
    authExpired?: boolean;
    isReconnectable?: boolean;
    errorCode?: string;
  },
): StreamRequestError {
  const error = new Error(message) as StreamRequestError;
  if (options?.status !== undefined) {
    error.status = options.status;
  }
  if (options?.authExpired) {
    error.authExpired = true;
  }
  if (options?.isReconnectable !== undefined) {
    error.isReconnectable = options.isReconnectable;
  }
  if (options?.errorCode) {
    error.errorCode = options.errorCode;
  }
  return error;
}

async function requestCompletionNotification(runId: string, token: string | null | undefined) {
  if (!token) return;
  await expoFetch(
    `${API_BASE_URL}/mobile/ask/runs/${encodeURIComponent(runId)}/notify-on-completion`,
    {
      method: 'POST',
      headers: {
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
    },
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
    throw buildStreamRequestError('Missing token for run polling', { isReconnectable: false });
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
      throw buildStreamRequestError(message, {
        isReconnectable: false,
        errorCode: run.error?.code,
      });
    }
  }

  throw buildStreamRequestError('Timed out waiting for run to complete', {
    isReconnectable: true,
  });
}

export async function askWithStreaming({
  token,
  question,
  threadId,
  pendingCommandId,
  uiSubmission,
  mediaAttachments,
  callbacks,
}: StreamAskParams): Promise<AskResponse> {
  const payload = {
    question,
    thread_id: threadId ?? undefined,
    pending_event_id: pendingCommandId ?? undefined,
    client_context: getClientContext(),
    ui_submission: uiSubmission ?? undefined,
    media_attachments: mediaAttachments && mediaAttachments.length > 0 ? mediaAttachments : undefined,
  };

  const abortController = new AbortController();
  let runId: string | null = null;
  let backgroundAborted = false;
  let backgroundAbortStarted = false;
  let abortListenerRemoved = false;
  const backgroundAbortSubscription = AppState.addEventListener('change', (nextState) => {
    if (nextState === 'active') return;
    if (backgroundAbortStarted) return;
    backgroundAbortStarted = true;
    backgroundAborted = true;
    const abortStream = () => {
      abortController.abort();
    };
    if (!runId) {
      abortStream();
      return;
    }
    void requestCompletionNotification(runId, token).finally(abortStream);
  });

  const cleanupBackgroundAbortListener = () => {
    if (abortListenerRemoved) return;
    abortListenerRemoved = true;
    backgroundAbortSubscription.remove();
  };

  const buildBackgroundAbortError = () =>
    buildStreamRequestError('Stream paused while the app is in the background', {
      isReconnectable: false,
      errorCode: 'stream_backgrounded',
    });

  let streamResponse: Awaited<ReturnType<typeof expoFetch>>;
  try {
    streamResponse = await expoFetch(`${API_BASE_URL}/mobile/ask/stream`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: JSON.stringify(payload),
      signal: abortController.signal,
    });
  } catch (error) {
    cleanupBackgroundAbortListener();
    if (backgroundAborted) {
      throw buildBackgroundAbortError();
    }
    throw error;
  }

  if (!streamResponse.ok) {
    cleanupBackgroundAbortListener();
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

    throw buildStreamRequestError(detail, {
      status: streamResponse.status,
      authExpired: streamResponse.status === 401,
      isReconnectable: false,
    });
  }

  const headerRunId = streamResponse.headers.get('x-ask-run-id')?.trim() || null;
  const headerThreadId = streamResponse.headers.get('x-ask-thread-id')?.trim() || null;

  let doneBundle: AskResponse | null = null;
  runId = headerRunId;
  if (runId) {
    callbacks?.onRunId?.(runId);
  }
  if (headerThreadId) {
    callbacks?.onSessionInfo?.(headerThreadId);
  }
  const reader = streamResponse.body?.getReader();
  if (!reader) {
    cleanupBackgroundAbortListener();
    return (await apiFetch('/mobile/ask', {
      method: 'POST',
      body: JSON.stringify(payload),
      token,
    })) as AskResponse;
  }

  const decoder = new TextDecoder();
  let buffer = '';

  const handleParsedEvent = (event: StreamEvent): AskResponse | null => {
    if (event.type === 'session_info') {
      const threadId = typeof event.thread_id === 'string' ? event.thread_id.trim() : '';
      if (threadId) {
        callbacks?.onSessionInfo?.(threadId);
      }
      const parsedRunId = typeof event.run_id === 'string' ? event.run_id.trim() : '';
      if (parsedRunId) {
        runId = parsedRunId;
        callbacks?.onRunId?.(parsedRunId);
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
        callbacks?.onToken?.(delta);
      }
      return null;
    }

    if (event.type === 'clear_content') {
      callbacks?.onClearContent?.();
      return null;
    }

    if (event.type === 'error') {
      const message =
        typeof event.message === 'string' && event.message.trim()
          ? event.message.trim()
          : 'Stream ended with an error';
      throw buildStreamRequestError(message, { isReconnectable: false });
    }

    if (event.type === 'done') {
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
    cleanupBackgroundAbortListener();
    if (backgroundAborted) {
      throw buildBackgroundAbortError();
    }
    if (runId && shouldTryRunPolling(error)) {
      callbacks?.onStatus?.('Reconnecting...');
      return waitForRunCompletion(runId, token, callbacks);
    }
    throw error;
  }

  if (!doneBundle) {
    cleanupBackgroundAbortListener();
    if (runId) {
      callbacks?.onStatus?.('Reconnecting...');
      return waitForRunCompletion(runId, token, callbacks);
    }
    throw buildStreamRequestError('Stream ended before final response bundle', {
      isReconnectable: true,
    });
  }

  cleanupBackgroundAbortListener();
  return doneBundle;
}
