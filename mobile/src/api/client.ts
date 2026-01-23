const API_BASE_URL = process.env.EXPO_PUBLIC_BACKEND_URL ?? 'http://localhost:8000';

type FetchOptions = RequestInit & {
  token?: string | null;
  onAuthExpired?: () => Promise<string | null>;
  retryOnAuthExpired?: boolean;
};

let authTokenProvider: (() => string | null | Promise<string | null>) | null = null;
let authRefreshHandler: (() => Promise<string | null>) | null = null;

export function setAuthTokenProvider(
  provider: (() => string | null | Promise<string | null>) | null,
) {
  authTokenProvider = provider;
}

export function setAuthRefreshHandler(provider: (() => Promise<string | null>) | null) {
  authRefreshHandler = provider;
}

export async function apiFetch(path: string, options: FetchOptions = {}) {
  const { token, headers, onAuthExpired, retryOnAuthExpired = true, ...rest } = options;
  const resolvedToken =
    token === undefined && authTokenProvider ? await authTokenProvider() : token;
  const resolvedOnAuthExpired = onAuthExpired ?? authRefreshHandler ?? undefined;
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...rest,
    headers: {
      'Content-Type': 'application/json',
      ...(headers ?? {}),
      ...(resolvedToken ? { Authorization: `Bearer ${resolvedToken}` } : {}),
    },
  });

  console.log('[apiFetch] response', path);
  console.log('[apiFetch] response', response);

  if (!response.ok) {
    const message = await response.text();
    const isExpired = response.status === 401 && message.toLowerCase().includes('token expired');
    if (isExpired && resolvedOnAuthExpired && retryOnAuthExpired) {
      const refreshedToken = await resolvedOnAuthExpired();
      if (refreshedToken) {
        return apiFetch(path, {
          ...options,
          token: refreshedToken,
          retryOnAuthExpired: false,
        });
      }
    }
    const error = new Error(message || `Request failed with ${response.status}`);
    (error as Error & { status?: number; authExpired?: boolean }).status = response.status;
    if (isExpired) {
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

  const contentType = response.headers.get('content-type') ?? '';
  if (!contentType.includes('application/json')) {
    const text = await response.text();
    throw new Error(
      `Expected JSON response but got ${contentType || 'unknown content type'}: ${text.slice(0, 200)}`
    );
  }

  return response.json();
}

export { API_BASE_URL };
