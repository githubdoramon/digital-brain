const API_BASE_URL = process.env.EXPO_PUBLIC_BACKEND_URL ?? 'http://localhost:8000';

type FetchOptions = RequestInit & {
  token?: string | null;
  onAuthExpired?: () => Promise<string | null>;
  retryOnAuthExpired?: boolean;
};

type ApiFetchError = Error & {
  status?: number;
  authExpired?: boolean;
  contentType?: string;
  bodyPreview?: string;
  requestUrl?: string;
  tokenPresent?: boolean;
  authDiagnostics?: Record<string, unknown>;
  requestMethod?: string;
  fetchFailed?: boolean;
};

let authTokenProvider: (() => string | null | Promise<string | null>) | null = null;
let authRefreshHandler: (() => Promise<string | null>) | null = null;
let authDiagnosticsProvider: (() => Record<string, unknown> | Promise<Record<string, unknown>>) | null = null;

export function setAuthTokenProvider(
  provider: (() => string | null | Promise<string | null>) | null,
) {
  authTokenProvider = provider;
}

export function setAuthRefreshHandler(provider: (() => Promise<string | null>) | null) {
  authRefreshHandler = provider;
}

export function setAuthDiagnosticsProvider(
  provider: (() => Record<string, unknown> | Promise<Record<string, unknown>>) | null,
) {
  authDiagnosticsProvider = provider;
}

export async function getAuthRequestContext(): Promise<{
  token: string | null;
  authDiagnostics: Record<string, unknown>;
}> {
  const token = authTokenProvider ? await authTokenProvider() : null;
  const authDiagnostics = authDiagnosticsProvider ? await authDiagnosticsProvider() : {};
  return { token, authDiagnostics };
}

export async function apiFetch(path: string, options: FetchOptions = {}) {
  const { token, headers, onAuthExpired, retryOnAuthExpired = true, ...rest } = options;
  const isFormDataBody = typeof FormData !== 'undefined' && rest.body instanceof FormData;
  const resolvedToken =
    token === undefined && authTokenProvider ? await authTokenProvider() : token;
  const resolvedOnAuthExpired = onAuthExpired ?? authRefreshHandler ?? undefined;
  const authDiagnostics = authDiagnosticsProvider ? await authDiagnosticsProvider() : {};
  const startTime = Date.now();
  const requestUrl = `${API_BASE_URL}${path}`;
  const requestMethod = rest.method ?? 'GET';
  let response: Response;

  try {
    response = await fetch(requestUrl, {
      ...rest,
      headers: {
        ...(isFormDataBody ? {} : { 'Content-Type': 'application/json' }),
        ...(headers ?? {}),
        ...(resolvedToken ? { Authorization: `Bearer ${resolvedToken}` } : {}),
      },
    });
  } catch (error) {
    const fetchError = error as ApiFetchError;
    fetchError.requestUrl = requestUrl;
    fetchError.requestMethod = requestMethod;
    fetchError.tokenPresent = Boolean(resolvedToken);
    fetchError.authDiagnostics = authDiagnostics;
    fetchError.fetchFailed = true;
    console.error('[apiFetch] network failure', {
      path,
      requestUrl,
      requestMethod,
      durationMs: Date.now() - startTime,
      tokenPresent: Boolean(resolvedToken),
      error: fetchError.message,
    });
    throw fetchError;
  }

  const contentType = response.headers.get('content-type') ?? '';

  if (!response.ok) {
    const message = await response.text();
    const isExpired = response.status === 401;
    if (isExpired && resolvedOnAuthExpired && retryOnAuthExpired) {
      console.warn('[apiFetch] auth expired, attempting refresh', {
        path,
        status: response.status,
        retryOnAuthExpired,
      });
      const refreshedToken = await resolvedOnAuthExpired();
      if (refreshedToken) {
        console.warn('[apiFetch] retrying with refreshed token', { path });
        return apiFetch(path, {
          ...options,
          token: refreshedToken,
          retryOnAuthExpired: false,
        });
      }
      console.warn('[apiFetch] refresh handler returned no token', { path });
    }
    const error = new Error(message || `Request failed with ${response.status}`) as ApiFetchError;
    error.status = response.status;
    error.contentType = contentType;
    error.bodyPreview = message.slice(0, 200);
    error.requestUrl = requestUrl;
    error.tokenPresent = Boolean(resolvedToken);
    error.authDiagnostics = authDiagnostics;
    if (isExpired) {
      error.authExpired = true;
    }
    console.error('[apiFetch] error', {
      path,
      status: response.status,
      message: message || response.statusText,
      durationMs: Date.now() - startTime,
      tokenPresent: Boolean(resolvedToken),
      retryOnAuthExpired,
    });
    throw error;
  }

  if (
    retryOnAuthExpired &&
    resolvedOnAuthExpired &&
    resolvedToken &&
    contentType.includes('text/html')
  ) {
    console.warn('[apiFetch] received html, attempting refresh retry', {
      path,
      status: response.status,
      contentType,
    });
    const refreshedToken = await resolvedOnAuthExpired();
    if (refreshedToken) {
      return apiFetch(path, {
        ...options,
        token: refreshedToken,
        retryOnAuthExpired: false,
      });
    }
  }

  if (response.status === 204) {
    return null;
  }

  if (!contentType.includes('application/json')) {
    const text = await response.text();
    const error = new Error(
      `Expected JSON response but got ${contentType || 'unknown content type'}: ${text.slice(0, 200)}`
    ) as ApiFetchError;
    error.contentType = contentType;
    error.bodyPreview = text.slice(0, 200);
    error.requestUrl = requestUrl;
    error.tokenPresent = Boolean(resolvedToken);
    error.status = response.status;
    error.authDiagnostics = authDiagnostics;
    throw error;
  }

  const data = await response.json();
  if (response.status !== 204) {
    console.info('[apiFetch] success', {
      path,
      status: response.status,
      durationMs: Date.now() - startTime,
    });
  }
  return data;
}

export { API_BASE_URL };
