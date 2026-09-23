const API_BASE_URL = process.env.EXPO_PUBLIC_BACKEND_URL ?? 'http://localhost:8000';

type FetchOptions = RequestInit & {
  token?: string | null;
  onAuthExpired?: () => Promise<string | null>;
  onTiming?: (phase: string, elapsedMs: number, metadata?: Record<string, unknown>) => void;
  retryOnAuthExpired?: boolean;
};

const GLASSES_TIMING_HEADERS: Record<string, string> = {
  'x-digital-brain-proxy-request-received-at-ms': 'proxy_request_received_at_ms',
  'x-digital-brain-proxy-session-resolution-ms': 'proxy_session_resolution_ms',
  'x-digital-brain-proxy-upstream-headers-ms': 'proxy_upstream_headers_ms',
  'x-digital-brain-proxy-handler-to-headers-ms': 'proxy_handler_to_headers_ms',
  'x-digital-brain-proxy-headers-ready-at-ms': 'proxy_headers_ready_at_ms',
  'x-glasses-backend-route-ms': 'backend_route_ms',
  'x-glasses-backend-completed-at-ms': 'backend_completed_at_ms',
  'server-timing': 'server_timing',
  date: 'response_date',
};

export function getGlassesResponseTimingMetadata(
  headers: Headers | Record<string, string> | undefined,
): Record<string, unknown> {
  if (!headers) return {};
  const metadata: Record<string, unknown> = {};
  for (const [headerName, fieldName] of Object.entries(GLASSES_TIMING_HEADERS)) {
    const value =
      typeof (headers as Headers).get === 'function'
        ? (headers as Headers).get(headerName)
        : Object.entries(headers as Record<string, string>).find(
            ([key]) => key.toLowerCase() === headerName,
          )?.[1];
    if (value !== null && value !== undefined && value !== '') metadata[fieldName] = value;
  }
  return metadata;
}

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
let authDiagnosticsProvider:
  | (() => Record<string, unknown> | Promise<Record<string, unknown>>)
  | null = null;

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
  const apiFetchStartedAt = Date.now();
  const { token, headers, onAuthExpired, onTiming, retryOnAuthExpired = true, ...rest } = options;
  const isFormDataBody = typeof FormData !== 'undefined' && rest.body instanceof FormData;
  const resolvedToken =
    token === undefined && authTokenProvider ? await authTokenProvider() : token;
  const resolvedOnAuthExpired = onAuthExpired ?? authRefreshHandler ?? undefined;
  const authDiagnostics = authDiagnosticsProvider ? await authDiagnosticsProvider() : {};
  const startTime = Date.now();
  const requestUrl = `${API_BASE_URL}${path}`;
  const requestMethod = rest.method ?? 'GET';
  let response: Response;
  const fetchStartedAt = Date.now();
  onTiming?.('auth_resolution', fetchStartedAt - apiFetchStartedAt, {
    client_request_started_at_ms: apiFetchStartedAt,
    client_fetch_started_at_ms: fetchStartedAt,
  });

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
    onTiming?.('fetch_failed', Date.now() - fetchStartedAt);
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
  onTiming?.('response_headers', Date.now() - fetchStartedAt, {
    client_response_headers_at_ms: Date.now(),
    response_status: response.status,
    response_content_type: response.headers.get('content-type') ?? '',
    ...getGlassesResponseTimingMetadata(response.headers),
  });

  const contentType = response.headers.get('content-type') ?? '';

  if (!response.ok) {
    const errorBodyStartedAt = Date.now();
    const message = await response.text();
    onTiming?.('error_body_read', Date.now() - errorBodyStartedAt);
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
    const textBodyStartedAt = Date.now();
    const text = await response.text();
    onTiming?.('unexpected_body_read', Date.now() - textBodyStartedAt);
    const error = new Error(
      `Expected JSON response but got ${contentType || 'unknown content type'}: ${text.slice(0, 200)}`,
    ) as ApiFetchError;
    error.contentType = contentType;
    error.bodyPreview = text.slice(0, 200);
    error.requestUrl = requestUrl;
    error.tokenPresent = Boolean(resolvedToken);
    error.status = response.status;
    error.authDiagnostics = authDiagnostics;
    throw error;
  }

  const jsonBodyStartedAt = Date.now();
  const data = await response.json();
  onTiming?.('json_body_read_and_parse', Date.now() - jsonBodyStartedAt);
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
