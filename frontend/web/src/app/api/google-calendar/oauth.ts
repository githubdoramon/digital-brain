import { createOAuthState, isValidOAuthState } from "@/lib/oauth-state";

export const GOOGLE_CALENDAR_STATE_COOKIE = "google_calendar_oauth_state";
export const GOOGLE_CALENDAR_SCOPES = [
  "https://www.googleapis.com/auth/calendar.calendarlist.readonly",
  "https://www.googleapis.com/auth/calendar.events.readonly",
];

export { createOAuthState, isValidOAuthState };

export function getGoogleOAuthClient(): { clientId: string; clientSecret: string } {
  const clientId = process.env.GOOGLE_CLIENT_ID?.trim() ||
    process.env.GOOGLE_CLIENT_IDS?.split(",")[0]?.trim();
  const clientSecret = process.env.GOOGLE_CLIENT_SECRET?.trim();
  if (!clientId || !clientSecret) throw new Error("Google OAuth is not configured");
  return { clientId, clientSecret };
}
