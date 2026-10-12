import { createHmac, randomBytes, timingSafeEqual } from "node:crypto";

export function createOAuthState(): string {
  const nonce = randomBytes(32).toString("base64url");
  return `${nonce}.${signState(nonce)}`;
}

export function isValidOAuthState(state: string, cookieState: string): boolean {
  if (!state || !cookieState || state !== cookieState) return false;
  const [nonce, signature, ...rest] = state.split(".");
  if (!nonce || !signature || rest.length) return false;
  const expected = signState(nonce);
  const actualBytes = Buffer.from(signature);
  const expectedBytes = Buffer.from(expected);
  return actualBytes.length === expectedBytes.length && timingSafeEqual(actualBytes, expectedBytes);
}

function signState(nonce: string): string {
  const secret = process.env.NEXTAUTH_SECRET?.trim();
  if (!secret) throw new Error("NEXTAUTH_SECRET is not configured");
  return createHmac("sha256", secret).update(nonce).digest("base64url");
}
