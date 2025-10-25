/**
 * Utility for making authenticated API requests to the backend.
 */

const API_BASE = process.env.BACKEND_API_BASE ?? "http://localhost:8000";

/**
 * Get the current Google ID token from NextAuth session
 */
async function getIdToken(): Promise<string | null> {
  try {
    const response = await fetch("/api/auth/session");
    const session = await response.json();
    return session?.idToken || null;
  } catch (error) {
    console.error("Failed to get ID token:", error);
    return null;
  }
}

/**
 * Make an authenticated request to the backend API
 */
export async function apiRequest<T = any>(
  endpoint: string,
  options: RequestInit = {}
): Promise<T> {
  // Get auth token
  const idToken = await getIdToken();
  console.log("idToken", idToken);

  // Prepare headers
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };

  // Merge existing headers if any
  if (options.headers) {
    const existingHeaders = options.headers as Record<string, string>;
    Object.assign(headers, existingHeaders);
  }

  // Add authorization if we have a token
  if (idToken) {
    headers["Authorization"] = `Bearer ${idToken}`;
  }

  // Make the request
  const response = await fetch(`${API_BASE}${endpoint}`, {
    ...options,
    headers,
  });

  // Handle errors
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(error.detail || `Request failed: ${response.statusText}`);
  }

  // Return JSON response
  return response.json();
}

/**
 * Convenience methods for common HTTP verbs
 */
export const api = {
  get: <T = any>(endpoint: string) =>
    apiRequest<T>(endpoint, { method: "GET" }),

  post: <T = any>(endpoint: string, data?: any) =>
    apiRequest<T>(endpoint, {
      method: "POST",
      body: data ? JSON.stringify(data) : undefined,
    }),

  delete: <T = any>(endpoint: string) =>
    apiRequest<T>(endpoint, { method: "DELETE" }),

  put: <T = any>(endpoint: string, data?: any) =>
    apiRequest<T>(endpoint, {
      method: "PUT",
      body: data ? JSON.stringify(data) : undefined,
    }),
};

