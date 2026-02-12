/**
 * Utility for making authenticated API requests through the Next.js proxy.
 */

const API_BASE = "/api/orchestrator";

export type ClientLocationContext = {
  lat: number;
  lon: number;
  accuracy_m?: number;
  captured_at: string;
  source: "browser";
};

export type ClientContext = {
  timezone?: string;
  locale?: string;
  location?: ClientLocationContext;
};

export type UiDirectiveOption = {
  id: string;
  label: string;
};

export type UiDirectiveLink = {
  label: string;
  url: string;
};

export type UiDirectiveField = {
  id: string;
  kind: string;
  label: string;
  placeholder?: string;
  required?: boolean;
  options?: UiDirectiveOption[];
};

export type UiDirectiveBlock = {
  id: string;
  type: "clarification_form" | "choice_buttons" | "info_card";
  title?: string;
  description?: string;
  submit_label?: string;
  action_id?: string;
  fields?: UiDirectiveField[];
  options?: UiDirectiveOption[];
  links?: UiDirectiveLink[];
  body?: string;
};

export type UiDirectives = {
  version: string;
  fallback_text: string;
  blocks: UiDirectiveBlock[];
};

export type UiSubmissionInput = {
  block_id?: string;
  action_id?: string;
  values?: Record<string, unknown>;
  text_fallback?: string;
};

let cachedClientContext: ClientContext | null = null;
let locationRequestInFlight = false;

function roundCoordinate(value: number): number {
  return Math.round(value * 1000) / 1000;
}

function getBaseClientContext(): ClientContext {
  if (typeof window === "undefined") {
    return {};
  }

  const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  const locale = navigator.language || Intl.DateTimeFormat().resolvedOptions().locale;

  return {
    timezone: timezone || undefined,
    locale: locale || undefined,
  };
}

function requestBrowserLocationInBackground(): void {
  if (locationRequestInFlight || typeof window === "undefined" || !navigator.geolocation) {
    return;
  }

  locationRequestInFlight = true;
  navigator.geolocation.getCurrentPosition(
    (position) => {
      const lat = roundCoordinate(position.coords.latitude);
      const lon = roundCoordinate(position.coords.longitude);
      const capturedAt = new Date(position.timestamp || Date.now()).toISOString();

      const accuracy = Number.isFinite(position.coords.accuracy)
        ? Math.round(position.coords.accuracy * 10) / 10
        : undefined;

      cachedClientContext = {
        ...(cachedClientContext ?? getBaseClientContext()),
        location: {
          lat,
          lon,
          accuracy_m: accuracy,
          captured_at: capturedAt,
          source: "browser",
        },
      };
      locationRequestInFlight = false;
    },
    () => {
      locationRequestInFlight = false;
    },
    {
      enableHighAccuracy: false,
      timeout: 10000,
      maximumAge: 5 * 60 * 1000,
    }
  );
}

export function primeClientContext(): void {
  if (!cachedClientContext) {
    cachedClientContext = getBaseClientContext();
  }
  requestBrowserLocationInBackground();
}

export function getClientContext(): ClientContext {
  if (!cachedClientContext) {
    primeClientContext();
  }
  return {
    ...(cachedClientContext ?? {}),
    location: cachedClientContext?.location
      ? { ...cachedClientContext.location }
      : undefined,
  };
}

export interface MeetingIn {
  title: string;
  content: string;
  date: string;
  link?: string;
  attendees?: string[];
  tags?: string[];
}

/**
 * Make an authenticated request via the Next.js API routes.
 */
async function apiRequest<T = unknown>(
  endpoint: string,
  options: RequestInit = {}
): Promise<T> {
  const headers = new Headers(options.headers);
  const method = options.method ?? "GET";
  const isFormData =
    typeof FormData !== "undefined" && options.body instanceof FormData;

  if (!headers.has("content-type") && !["GET", "HEAD"].includes(method) && !isFormData) {
    headers.set("content-type", "application/json");
  }

  const response = await fetch(`${API_BASE}${endpoint}`, {
    ...options,
    headers,
  });

  if (!response.ok) {
    const errorBody = (await response.json().catch(() => undefined)) as { detail?: string } | undefined;
    throw new Error(errorBody?.detail || `Request failed: ${response.statusText}`);
  }

  if (response.status === 204) {
    return undefined as T;
  }

  const contentType = response.headers.get("content-type");
  if (contentType?.includes("application/json")) {
    return (await response.json()) as T;
  }

  return (await response.text()) as unknown as T;
}

/**
 * Convenience methods for common HTTP verbs
 */
export const api = {
  get: <T = unknown>(endpoint: string) =>
    apiRequest<T>(endpoint, { method: "GET" }),

  post: <T = unknown>(endpoint: string, data?: unknown, init?: RequestInit) =>
    typeof FormData !== "undefined" && data instanceof FormData
      ? apiRequest<T>(endpoint, {
          method: "POST",
          body: data,
          ...init,
        })
      : apiRequest<T>(endpoint, {
          method: "POST",
          body: data !== undefined ? JSON.stringify(data) : undefined,
          ...init,
        }),

  patch: <T = unknown>(endpoint: string, data?: unknown, init?: RequestInit) =>
    typeof FormData !== "undefined" && data instanceof FormData
      ? apiRequest<T>(endpoint, {
          method: "PATCH",
          body: data,
          ...init,
        })
      : apiRequest<T>(endpoint, {
          method: "PATCH",
          body: data !== undefined ? JSON.stringify(data) : undefined,
          ...init,
        }),

  delete: <T = unknown>(endpoint: string, init?: RequestInit) =>
    apiRequest<T>(endpoint, { method: "DELETE", ...init }),

  put: <T = unknown>(endpoint: string, data?: unknown, init?: RequestInit) =>
    typeof FormData !== "undefined" && data instanceof FormData
      ? apiRequest<T>(endpoint, {
          method: "PUT",
          body: data,
          ...init,
        })
      : apiRequest<T>(endpoint, {
          method: "PUT",
          body: data !== undefined ? JSON.stringify(data) : undefined,
          ...init,
        }),
};

/**
 * Streaming event types from the /ask/stream SSE endpoint
 */
export type StreamEvent =
  | { type: "token"; content: string }
  | { type: "clear_content" }
  | { type: "tool_call"; name: string; args?: Record<string, unknown> }
  | { type: "tool_result"; name: string; result: unknown }
  | { type: "status"; message: string }
  | { type: "session_info"; thread_id: string; is_new_session: boolean }
  | { type: "done"; bundle: StreamBundle }
  | { type: "error"; message: string };

export type StreamBundle = {
  answer: string;
  thread_id?: string;
  thread_title?: string | null;
  is_new_session?: boolean;
  pending_event_id?: string | null;
  // Removed: event_proposal (old event capture system)
  activated_skills?: string[];
  ui_directives?: UiDirectives;
  command_result?: {
    type: string;
    [key: string]: unknown;
  };
};

/**
 * Non-streaming ask endpoint - simpler, more reliable.
 * Use this for testing or when streaming isn't needed.
 */
export async function ask(
  question: string,
  options: {
    threadId?: string | null;
    limit?: number;
    pendingEventId?: string | null;
    uiSubmission?: UiSubmissionInput;
  }
): Promise<StreamBundle> {
  const clientContext = getClientContext();
  return api.post<StreamBundle>("/ask", {
    question,
    thread_id: options.threadId,
    limit: options.limit ?? 30,
    pending_event_id: options.pendingEventId ?? undefined,
    ui_submission: options.uiSubmission ?? undefined,
    client_context: clientContext,
    timeout: 60000,
  });
}

export type StreamCallbacks = {
  onToken?: (content: string, fullContent: string) => void;
  onClearContent?: () => void;
  onToolCall?: (name: string, args?: Record<string, unknown>) => void;
  onToolResult?: (name: string, result: unknown) => void;
  onStatus?: (message: string) => void;
  onSessionInfo?: (threadId: string, isNewSession: boolean) => void;
  onError?: (message: string) => void;
};

export type LogLevel = "debug" | "info" | "decision" | "warning" | "error";

export type LogMessageSegment =
  | { kind: "text"; content: string }
  | { kind: "json"; content: string; value: unknown };

export type LogEntry = {
  id?: number;
  timestamp: string;
  level: LogLevel;
  message: string;
  context?: Record<string, unknown> | null;
  message_segments?: LogMessageSegment[] | null;
};

/**
 * Stream responses from the /ask/stream SSE endpoint.
 * Returns the final bundle when streaming completes.
 */
export async function askWithStreaming(
  question: string,
  options: {
    threadId?: string | null;
    limit?: number;
    pendingEventId?: string | null;
    uiSubmission?: UiSubmissionInput;
  },
  callbacks: StreamCallbacks
): Promise<StreamBundle> {
  const clientContext = getClientContext();
  const response = await fetch(`${API_BASE}/ask/stream`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      question,
      thread_id: options.threadId,
      limit: options.limit ?? 30,
      pending_event_id: options.pendingEventId ?? undefined,
      ui_submission: options.uiSubmission ?? undefined,
      client_context: clientContext,
    }),
  });

  if (!response.ok) {
    const errorBody = (await response.json().catch(() => undefined)) as { detail?: string } | undefined;
    throw new Error(errorBody?.detail || `Request failed: ${response.statusText}`);
  }

  const reader = response.body?.getReader();
  if (!reader) {
    throw new Error("Response body is not readable");
  }

  const decoder = new TextDecoder();
  let buffer = "";
  let fullContent = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() || "";

    for (const line of lines) {
      if (line.startsWith("data: ")) {
        const jsonStr = line.slice(6).trim();
        if (!jsonStr) continue;

        try {
          const event = JSON.parse(jsonStr) as StreamEvent;

          switch (event.type) {
            case "token":
              fullContent += event.content;
              callbacks.onToken?.(event.content, fullContent);
              break;
            case "clear_content":
              // Model is calling tools - clear intermediate "thinking" content
              fullContent = "";
              callbacks.onClearContent?.();
              break;
            case "tool_call":
              callbacks.onToolCall?.(event.name, event.args);
              break;
            case "tool_result":
              callbacks.onToolResult?.(event.name, event.result);
              break;
            case "status":
              callbacks.onStatus?.(event.message);
              break;
            case "session_info":
              callbacks.onSessionInfo?.(event.thread_id, event.is_new_session);
              break;
            case "error":
              callbacks.onError?.(event.message);
              break;
            case "done":
              return event.bundle;
          }
        } catch {
          // Skip invalid JSON lines
        }
      }
    }
  }

  // If we exit without a done event, return what we have
  return {
    answer: fullContent,
  };
}

export async function streamSystemLogs(
  level: LogLevel | "all",
  onEntry: (entry: LogEntry) => void,
  onError: (message: string) => void,
  signal?: AbortSignal
): Promise<void> {
  const params = new URLSearchParams();
  if (level !== "all") {
    params.set("level", level);
  }
  const response = await fetch(
    `${API_BASE}/system/logs/stream${params.toString() ? `?${params.toString()}` : ""}`,
    {
      method: "GET",
      signal,
    }
  );

  if (!response.ok) {
    const errorBody = (await response.json().catch(() => undefined)) as
      | { detail?: string }
      | undefined;
    throw new Error(errorBody?.detail || `Request failed: ${response.statusText}`);
  }

  const reader = response.body?.getReader();
  if (!reader) {
    throw new Error("Response body is not readable");
  }

  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() || "";

    for (const line of lines) {
      if (!line.startsWith("data: ")) {
        continue;
      }
      const jsonStr = line.slice(6).trim();
      if (!jsonStr) continue;

      try {
        const entry = JSON.parse(jsonStr) as LogEntry;
        if (!entry?.timestamp || !entry?.level || !entry?.message) {
          continue;
        }
        onEntry(entry);
      } catch {
        onError("Received malformed log event.");
      }
    }
  }
}

export async function getSystemLogs(
  level: LogLevel | "all",
  sinceMinutes = 15,
  limit = 200
): Promise<LogEntry[]> {
  const params = new URLSearchParams();
  if (level !== "all") {
    params.set("level", level);
  }
  params.set("since_minutes", `${sinceMinutes}`);
  params.set("limit", `${limit}`);
  const response = await api.get<{ entries: LogEntry[] }>(
    `/system/logs?${params.toString()}`
  );
  return response.entries ?? [];
}
