import { NextResponse } from "next/server";
import type { NextFetchEvent, NextRequest } from "next/server";
import { withAuth } from "next-auth/middleware";
import type { NextRequestWithAuth } from "next-auth/middleware";

const SERVICE_API_KEY_PREFIXES: string[] = [
  "/api/orchestrator/meeting",
  "/api/removed-service",
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
  return SERVICE_API_KEY_PREFIXES.some((prefix) => pathname.startsWith(prefix));
}

function validateServiceApiKey(request: NextRequest) {
  const serviceApiKey = request.headers.get("x-service-api-key");
  if (!serviceApiKey) {
    return NextResponse.json(
      { detail: "Missing x-service-api-key header" },
      {
        status: 401,
        headers: { "content-type": "application/json" },
      }
    );
  }

  return NextResponse.next();
}

export default function middleware(request: NextRequest, event: NextFetchEvent) {
  const pathname = request.nextUrl.pathname;

  if (shouldBypassAuth(pathname)) {
    const validationResponse = validateServiceApiKey(request);
    if (validationResponse) {
      return validationResponse;
    }

    return NextResponse.next();
  }

  return authMiddleware(request as NextRequestWithAuth, event);
}

export const config = {
  matcher: ["/((?!api/auth|_next/static|_next/image|favicon.ico|auth/signin).*)"],
};
