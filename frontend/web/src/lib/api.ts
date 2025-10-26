/**
 * Utility for making authenticated API requests through the Next.js proxy.
 */

const API_BASE = "/api/orchestrator";

/**
 * Make an authenticated request via the Next.js API routes.
 */
export async function apiRequest<T = any>(
  endpoint: string,
  options: RequestInit = {}
): Promise<T> {
  const headers = new Headers(options.headers);
  if (!headers.has("content-type") && !["GET", "HEAD"].includes(options.method ?? "")) {
    headers.set("content-type", "application/json");
  }

  const response = await fetch(`${API_BASE}${endpoint}`, {
    ...options,
    headers,
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(error.detail || `Request failed: ${response.statusText}`);
  }

  if (response.status === 204) {
    return undefined as T;
  }

  const contentType = response.headers.get("content-type");
  if (contentType?.includes("application/json")) {
    return response.json();
  }

  return (await response.text()) as T;
}

/**
 * Convenience methods for common HTTP verbs
 */
export const api = {
  get: <T = any>(endpoint: string) =>
    apiRequest<T>(endpoint, { method: "GET" }),

  post: <T = any>(endpoint: string, data?: any, init?: RequestInit) =>
    apiRequest<T>(endpoint, {
      method: "POST",
      body: data ? JSON.stringify(data) : undefined,
      ...init,
    }),

  delete: <T = any>(endpoint: string, init?: RequestInit) =>
    apiRequest<T>(endpoint, { method: "DELETE", ...init }),

  put: <T = any>(endpoint: string, data?: any, init?: RequestInit) =>
    apiRequest<T>(endpoint, {
      method: "PUT",
      body: data ? JSON.stringify(data) : undefined,
      ...init,
    }),
};

