const API_BASE_URL = process.env.EXPO_PUBLIC_BACKEND_URL ?? 'http://localhost:8000';

type FetchOptions = RequestInit & { token?: string | null };

export async function apiFetch(path: string, options: FetchOptions = {}) {
  const { token, headers, ...rest } = options;
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...rest,
    headers: {
      'Content-Type': 'application/json',
      ...(headers ?? {}),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
  });

  if (!response.ok) {
    const message = await response.text();
    const error = new Error(message || `Request failed with ${response.status}`);
    (error as Error & { status?: number; authExpired?: boolean }).status = response.status;
    if (response.status === 401 && message.toLowerCase().includes('token expired')) {
      (error as Error & { authExpired?: boolean }).authExpired = true;
    }
    console.error('[apiFetch] error', {
      path,
      status: response.status,
      message: message || response.statusText,
    });
    throw error;
  }

  if (response.status === 204) {
    return null;
  }

  return response.json();
}

export { API_BASE_URL };
