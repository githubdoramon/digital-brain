import { getServerSession } from "next-auth";
import { NextRequest, NextResponse } from "next/server";

import { authOptions } from "@/app/api/auth/[...nextauth]/route";
import {
  createOAuthState,
  GOOGLE_CALENDAR_SCOPES,
  GOOGLE_CALENDAR_STATE_COOKIE,
  getGoogleOAuthClient,
} from "../oauth";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function GET(request: NextRequest) {
  const session = await getServerSession(authOptions);
  const signedInEmail = session?.user?.email?.trim().toLowerCase();
  if (!signedInEmail) {
    return NextResponse.redirect(new URL("/auth/signin", request.url));
  }

  try {
    const { clientId } = getGoogleOAuthClient();
    const state = createOAuthState();
    const callbackUrl = new URL("/api/google-calendar/callback", request.nextUrl.origin);
    const authorizationUrl = new URL("https://accounts.google.com/o/oauth2/v2/auth");
    authorizationUrl.search = new URLSearchParams({
      client_id: clientId,
      redirect_uri: callbackUrl.toString(),
      response_type: "code",
      scope: ["openid", "email", "profile", ...GOOGLE_CALENDAR_SCOPES].join(" "),
      access_type: "offline",
      prompt: "consent",
      include_granted_scopes: "true",
      login_hint: signedInEmail,
      state,
    }).toString();

    const response = NextResponse.redirect(authorizationUrl);
    response.cookies.set(GOOGLE_CALENDAR_STATE_COOKIE, state, {
      httpOnly: true,
      secure: request.nextUrl.protocol === "https:" || process.env.NODE_ENV === "production",
      sameSite: "lax",
      path: "/api/google-calendar",
      maxAge: 600,
    });
    return response;
  } catch {
    return NextResponse.redirect(new URL("/system/calendars?provider=google&error=oauth_config", request.url));
  }
}
