import { getServerSession } from "next-auth";
import { NextRequest, NextResponse } from "next/server";

import { authOptions } from "@/app/api/auth/[...nextauth]/route";
import {
  createOAuthState,
  getMicrosoftOAuthConfig,
  MICROSOFT_CALENDAR_SCOPES,
  MICROSOFT_CALENDAR_STATE_COOKIE,
} from "../oauth";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function GET(request: NextRequest) {
  const session = await getServerSession(authOptions);
  if (!session?.user?.email) return NextResponse.redirect(new URL("/auth/signin", request.url));
  try {
    const { clientId, tenant } = getMicrosoftOAuthConfig();
    const state = createOAuthState();
    const redirectUri = new URL("/api/microsoft-calendar/callback", request.nextUrl.origin).toString();
    const authorizationUrl = new URL(
      `https://login.microsoftonline.com/${encodeURIComponent(tenant)}/oauth2/v2.0/authorize`
    );
    authorizationUrl.search = new URLSearchParams({
      client_id: clientId,
      response_type: "code",
      redirect_uri: redirectUri,
      response_mode: "query",
      scope: MICROSOFT_CALENDAR_SCOPES.join(" "),
      login_hint: session.user.email,
      state,
    }).toString();
    const response = NextResponse.redirect(authorizationUrl);
    response.cookies.set(MICROSOFT_CALENDAR_STATE_COOKIE, state, {
      httpOnly: true,
      secure: request.nextUrl.protocol === "https:" || process.env.NODE_ENV === "production",
      sameSite: "lax",
      path: "/api/microsoft-calendar",
      maxAge: 600,
    });
    return response;
  } catch {
    return NextResponse.redirect(new URL("/system/calendars?provider=microsoft&error=oauth_config", request.url));
  }
}
