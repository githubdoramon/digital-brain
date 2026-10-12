import { getServerSession } from "next-auth";
import { NextRequest, NextResponse } from "next/server";

import { authOptions } from "@/app/api/auth/[...nextauth]/route";
import { getOAuthCallbackUrl } from "@/lib/oauth-callback-url";
import {
  getMicrosoftOAuthConfig,
  isValidOAuthState,
  MICROSOFT_CALENDAR_STATE_COOKIE,
} from "../oauth";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

type TokenResponse = { access_token?: string; refresh_token?: string; error?: string };
type MicrosoftProfile = { mail?: string | null; userPrincipalName?: string | null };

function redirectWithError(request: NextRequest, code: string) {
  const response = NextResponse.redirect(
    new URL(`/system/calendars?provider=microsoft&error=${encodeURIComponent(code)}`, request.nextUrl.origin)
  );
  response.cookies.set(MICROSOFT_CALENDAR_STATE_COOKIE, "", { path: "/api/microsoft-calendar", maxAge: 0 });
  return response;
}

export async function GET(request: NextRequest) {
  const session = await getServerSession(authOptions);
  if (!session?.user?.email || !session.idToken) return redirectWithError(request, "sign_in_required");
  const code = request.nextUrl.searchParams.get("code") || "";
  const state = request.nextUrl.searchParams.get("state") || "";
  const cookieState = request.cookies.get(MICROSOFT_CALENDAR_STATE_COOKIE)?.value || "";
  if (!isValidOAuthState(state, cookieState)) return redirectWithError(request, "invalid_oauth_state");
  if (!code || request.nextUrl.searchParams.get("error")) return redirectWithError(request, "authorization_failed");

  try {
    const { clientId, clientSecret } = getMicrosoftOAuthConfig();
    const redirectUri = getOAuthCallbackUrl(request.url, "/api/microsoft-calendar/callback");
    const tokenResponse = await fetch(
      "https://login.microsoftonline.com/organizations/oauth2/v2.0/token",
      {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: new URLSearchParams({
          client_id: clientId,
          client_secret: clientSecret,
          grant_type: "authorization_code",
          code,
          redirect_uri: redirectUri,
          scope: "openid profile email offline_access User.Read Calendars.Read",
        }),
        cache: "no-store",
      }
    );
    const tokens = (await tokenResponse.json()) as TokenResponse;
    if (!tokenResponse.ok || !tokens.access_token || !tokens.refresh_token) {
      return redirectWithError(request, "token_exchange_failed");
    }
    const profileResponse = await fetch(
      "https://graph.microsoft.com/v1.0/me?$select=mail,userPrincipalName",
      { headers: { Authorization: `Bearer ${tokens.access_token}` }, cache: "no-store" }
    );
    if (!profileResponse.ok) return redirectWithError(request, "account_check_failed");
    const profile = (await profileResponse.json()) as MicrosoftProfile;
    const connectedEmail = profile.mail?.trim() || profile.userPrincipalName?.trim();
    if (!connectedEmail) return redirectWithError(request, "account_check_failed");

    const backendUrl = new URL(
      "/integrations/microsoft-calendar/connection",
      process.env.BACKEND_API_BASE ?? "http://localhost:8000"
    );
    const saveResponse = await fetch(backendUrl, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${session.idToken}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ connectedEmail, refreshToken: tokens.refresh_token }),
      cache: "no-store",
    });
    if (!saveResponse.ok) return redirectWithError(request, "connection_save_failed");
    const response = NextResponse.redirect(
      new URL("/system/calendars?provider=microsoft&connected=1", request.nextUrl.origin)
    );
    response.cookies.set(MICROSOFT_CALENDAR_STATE_COOKIE, "", {
      path: "/api/microsoft-calendar",
      maxAge: 0,
    });
    return response;
  } catch {
    return redirectWithError(request, "connection_failed");
  }
}
