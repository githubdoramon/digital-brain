/**
 * Utility for making authenticated API requests through the Next.js proxy.
 */

const API_BASE = "/api/orchestrator";

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
  }
): Promise<StreamBundle> {
  return api.post<StreamBundle>("/ask", {
    question,
    thread_id: options.threadId,
    limit: options.limit ?? 30,
    pending_event_id: options.pendingEventId ?? undefined,
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
  },
  callbacks: StreamCallbacks
): Promise<StreamBundle> {
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
