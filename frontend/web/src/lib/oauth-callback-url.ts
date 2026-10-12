import { isIP } from "node:net";

export function getOAuthCallbackUrl(requestUrl: string, callbackPath: string): string {
  const configuredUrl = process.env.NEXTAUTH_URL?.trim();
  if (process.env.NODE_ENV === "production" && !configuredUrl) {
    throw new Error("NEXTAUTH_URL must be configured in production");
  }

  const appUrl = new URL(configuredUrl || requestUrl);
  if (!configuredUrl && process.env.NODE_ENV !== "production" && appUrl.hostname === "0.0.0.0") {
    appUrl.protocol = "http:";
    appUrl.hostname = "localhost";
  }

  if (process.env.NODE_ENV === "production") {
    if (appUrl.protocol !== "https:") {
      throw new Error("NEXTAUTH_URL must use HTTPS in production");
    }
    if (isIP(appUrl.hostname)) {
      throw new Error("NEXTAUTH_URL must use a domain name in production");
    }
  }

  return new URL(callbackPath, appUrl.origin).toString();
}
