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
