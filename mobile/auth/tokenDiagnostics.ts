type JwtPayload = {
  exp?: number;
  iat?: number;
  aud?: string | string[];
  iss?: string;
  email?: string;
  sub?: string;
};

function decodeBase64Url(input: string): string | null {
  try {
    const normalized = input.replace(/-/g, '+').replace(/_/g, '/');
    const padded = normalized.padEnd(Math.ceil(normalized.length / 4) * 4, '=');
    return globalThis.atob(padded);
  } catch {
    return null;
  }
}

function parseJwtPayload(token: string | null | undefined): JwtPayload | null {
  if (!token) {
    return null;
  }
  const parts = token.split('.');
  if (parts.length < 2) {
    return null;
  }
  const decoded = decodeBase64Url(parts[1]);
  if (!decoded) {
    return null;
  }
  try {
    return JSON.parse(decoded) as JwtPayload;
  } catch {
    return null;
  }
}

function fingerprintToken(token: string | null | undefined): string | null {
  if (!token) {
    return null;
  }
  let hash = 2166136261;
  for (let index = 0; index < token.length; index += 1) {
    hash ^= token.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return `fnv1a-${(hash >>> 0).toString(16)}`;
}

export function getTokenDiagnostics(token: string | null | undefined): Record<string, unknown> {
  const payload = parseJwtPayload(token);
  const nowSeconds = Math.floor(Date.now() / 1000);
  const expiresAt = typeof payload?.exp === 'number' ? new Date(payload.exp * 1000).toISOString() : undefined;
  const issuedAt = typeof payload?.iat === 'number' ? new Date(payload.iat * 1000).toISOString() : undefined;
  const expiresInSeconds = typeof payload?.exp === 'number' ? payload.exp - nowSeconds : undefined;
  const subject = typeof payload?.sub === 'string' ? payload.sub : undefined;

  return {
    token_present: Boolean(token),
    token_fingerprint: fingerprintToken(token) ?? 'none',
    token_expires_at: expiresAt ?? 'none',
    token_issued_at: issuedAt ?? 'none',
    token_expires_in_seconds: typeof expiresInSeconds === 'number' ? expiresInSeconds : 'none',
    token_is_expired: typeof expiresInSeconds === 'number' ? expiresInSeconds <= 0 : 'unknown',
    token_audience: Array.isArray(payload?.aud) ? payload.aud.join(',') : payload?.aud ?? 'none',
    token_issuer: payload?.iss ?? 'none',
    token_email_hint: payload?.email ?? 'none',
    token_subject_suffix: subject ? subject.slice(-6) : 'none',
  };
}
