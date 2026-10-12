import { getServerSession } from "next-auth";
import { NextRequest, NextResponse } from "next/server";

import { authOptions } from "@/app/api/auth/[...nextauth]/route";
import { getOAuthCallbackUrl, getPublicAppUrl } from "@/lib/oauth-callback-url";
import {
  getGoogleOAuthClient,
  GOOGLE_CALENDAR_STATE_COOKIE,
  isValidOAuthState,
} from "../oauth";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

type GoogleTokenResponse = {
  access_token?: string;
  refresh_token?: string;
  error?: string;
};

type GoogleUserInfo = {
  email?: string;
  verified_email?: boolean;
};

function redirectWithError(request: NextRequest, code: string) {
  const response = NextResponse.redirect(
    new URL(
      `/system/calendars?provider=google&error=${encodeURIComponent(code)}`,
      getPublicAppUrl(request.url).origin
    )
  );
  response.cookies.set(GOOGLE_CALENDAR_STATE_COOKIE, "", {
    path: "/api/google-calendar",
    maxAge: 0,
  });
  return response;
}

export async function GET(request: NextRequest) {
  const session = await getServerSession(authOptions);
  const sessionEmail = session?.user?.email?.trim().toLowerCase();
  if (!session || !sessionEmail || !session.idToken) {
    return redirectWithError(request, "sign_in_required");
  }

  const code = request.nextUrl.searchParams.get("code") || "";
  const state = request.nextUrl.searchParams.get("state") || "";
  const cookieState = request.cookies.get(GOOGLE_CALENDAR_STATE_COOKIE)?.value || "";
  if (!isValidOAuthState(state, cookieState)) {
    return redirectWithError(request, "invalid_oauth_state");
  }
  if (!code || request.nextUrl.searchParams.get("error")) {
    return redirectWithError(request, "authorization_failed");
  }

  try {
    const { clientId, clientSecret } = getGoogleOAuthClient();
    const callbackUrl = getOAuthCallbackUrl(request.url, "/api/google-calendar/callback");
    const tokenResponse = await fetch("https://oauth2.googleapis.com/token", {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({
        code,
        client_id: clientId,
        client_secret: clientSecret,
        redirect_uri: callbackUrl,
        grant_type: "authorization_code",
      }),
      cache: "no-store",
    });
    const tokens = (await tokenResponse.json()) as GoogleTokenResponse;
    if (!tokenResponse.ok || !tokens.access_token || !tokens.refresh_token) {
      return redirectWithError(request, "token_exchange_failed");
    }

    const profileResponse = await fetch("https://www.googleapis.com/oauth2/v2/userinfo", {
      headers: { Authorization: `Bearer ${tokens.access_token}` },
      cache: "no-store",
    });
    if (!profileResponse.ok) return redirectWithError(request, "account_check_failed");
    const profile = (await profileResponse.json()) as GoogleUserInfo;
    if (
      !profile.verified_email ||
      profile.email?.trim().toLowerCase() !== sessionEmail
    ) {
      return redirectWithError(request, "account_mismatch");
    }

    const backendBase = process.env.BACKEND_API_BASE ?? "http://localhost:8000";
    const backendUrl = new URL("/integrations/google-calendar/connection", backendBase);
    const saveResponse = await fetch(backendUrl, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${session.idToken}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ refreshToken: tokens.refresh_token }),
      cache: "no-store",
    });
    if (!saveResponse.ok) {
      console.error(`[google-calendar.oauth] Backend rejected connection save with HTTP ${saveResponse.status}`);
      return redirectWithError(request, "connection_save_failed");
    }

    const response = NextResponse.redirect(
      new URL("/system/calendars?provider=google&connected=1", getPublicAppUrl(request.url).origin)
    );
    response.cookies.set(GOOGLE_CALENDAR_STATE_COOKIE, "", {
      path: "/api/google-calendar",
      maxAge: 0,
    });
    return response;
  } catch {
    return redirectWithError(request, "connection_failed");
  }
}
