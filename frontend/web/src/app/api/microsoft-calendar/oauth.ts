import { createOAuthState, isValidOAuthState } from "@/lib/oauth-state";

export const MICROSOFT_CALENDAR_STATE_COOKIE = "microsoft_calendar_oauth_state";
export const MICROSOFT_CALENDAR_SCOPES = [
  "openid",
  "profile",
  "email",
  "offline_access",
  "User.Read",
  "Calendars.Read",
];

export { createOAuthState, isValidOAuthState };

export function getMicrosoftOAuthConfig(): { clientId: string; clientSecret: string; tenant: string } {
  const clientId = process.env.MICROSOFT_CALENDAR_CLIENT_ID?.trim();
  const clientSecret = process.env.MICROSOFT_CALENDAR_CLIENT_SECRET?.trim();
  const tenant = process.env.MICROSOFT_CALENDAR_TENANT_ID?.trim() || "organizations";
  if (!clientId || !clientSecret) throw new Error("Microsoft Calendar OAuth is not configured");
  return { clientId, clientSecret, tenant };
}
