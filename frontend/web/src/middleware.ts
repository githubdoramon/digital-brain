import { NextResponse } from "next/server";
import type { NextFetchEvent, NextRequest } from "next/server";
import { withAuth } from "next-auth/middleware";
import type { NextRequestWithAuth } from "next-auth/middleware";

// Add API route prefixes (e.g. "/api/public") that should bypass NextAuth middleware.
const AUTH_BYPASS_PREFIXES: string[] = [
  "/api/deploy",
  "/api/orchestrator/ingest/meetings",
];

const authMiddleware = withAuth({
  callbacks: {
    authorized({ token }) {
      return !!token;
    },
  },
  pages: {
    signIn: "/auth/signin",
  },
});

function shouldBypassAuth(pathname: string): boolean {
  return AUTH_BYPASS_PREFIXES.some((prefix) => pathname.startsWith(prefix));
}

export default function middleware(request: NextRequest, event: NextFetchEvent) {
  if (shouldBypassAuth(request.nextUrl.pathname)) {
    return NextResponse.next();
  }

  return authMiddleware(request as NextRequestWithAuth, event);
}

export const config = {
  matcher: ["/((?!api/auth|_next/static|_next/image|favicon.ico|auth/signin).*)"],
};
