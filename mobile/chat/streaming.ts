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
  | { type: 'session_info'; thread_id?: string; is_new_session?: boolean }
  | { type: 'done'; bundle?: AskResponse }
  | { type: 'error'; message?: string }
  | { type: string; [key: string]: unknown };

type StreamCallbacks = {
  onSessionInfo?: (threadId: string) => void;
  onStatus?: (message: string) => void;
  onToken?: (delta: string) => void;
  onClearContent?: () => void;
  onProgressChip?: (chip: string) => void;
};

type StreamAskParams = {
  token?: string | null;
  question: string;
  pendingEventId?: string | null;
  uiSubmission?: UiSubmissionInput;
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
    return `Using ${humanName}`;
  }

  return `Using ${humanName} (${selectedEntries.join(', ')})`;
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

  let doneBundle: AskResponse | null = null;
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

  while (true) {
    const { done, value } = await reader.read();
    if (done) {
      break;
    }

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split('\n');
    buffer = lines.pop() || '';

    for (const line of lines) {
      const event = parseSseEventLine(line);
      if (!event) {
        continue;
      }

      if (event.type === 'session_info') {
        if (event.thread_id) {
          callbacks?.onSessionInfo?.(event.thread_id);
        }
        continue;
      }

      if (event.type === 'status') {
        const statusMessage = typeof event.message === 'string' ? event.message.trim() : '';
        if (statusMessage) {
          callbacks?.onStatus?.(statusMessage);
        }
        continue;
      }

      if (event.type === 'tool_call') {
        const toolName = typeof event.name === 'string' ? event.name.trim() : '';
        if (toolName) {
          const chip = buildToolProgressChip(toolName, event.args);
          if (chip) {
            callbacks?.onProgressChip?.(chip);
          }
        }
        continue;
      }

      if (event.type === 'tool_result') {
        continue;
      }

      if (event.type === 'token') {
        const delta = typeof event.content === 'string' ? event.content : '';
        if (delta) {
          callbacks?.onToken?.(delta);
        }
        continue;
      }

      if (event.type === 'clear_content') {
        callbacks?.onClearContent?.();
        continue;
      }

      if (event.type === 'error') {
        throw new Error(event.message || 'Stream ended with an error');
      }

      if (event.type === 'done') {
        doneBundle = event.bundle ?? null;
        break;
      }
    }

    if (doneBundle) {
      break;
    }
  }

  if (!doneBundle) {
    throw new Error('Stream ended before final response bundle');
  }

  return doneBundle;
}
