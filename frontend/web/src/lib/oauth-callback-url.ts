import { isIP } from "node:net";

export function getPublicAppUrl(requestUrl: string): URL {
  const configuredUrl = process.env.NEXTAUTH_URL?.trim();
  if (process.env.NODE_ENV === "production" && !configuredUrl) {
    throw new Error("NEXTAUTH_URL must be configured in production");
  }

  const appUrl = new URL(configuredUrl || requestUrl);
  if (appUrl.hostname === "0.0.0.0") {
    throw new Error("Use localhost locally or set NEXTAUTH_URL to the public app URL");
  }

  if (process.env.NODE_ENV === "production") {
    if (appUrl.protocol !== "https:") {
      throw new Error("NEXTAUTH_URL must use HTTPS in production");
    }
    if (isIP(appUrl.hostname)) {
      throw new Error("NEXTAUTH_URL must use a domain name in production");
    }
  }

  return appUrl;
}

export function getOAuthCallbackUrl(requestUrl: string, callbackPath: string): string {
  return new URL(callbackPath, getPublicAppUrl(requestUrl).origin).toString();
}
