import { NextResponse } from "next/server";
import type { NextFetchEvent, NextRequest } from "next/server";
import { withAuth } from "next-auth/middleware";
import type { NextRequestWithAuth } from "next-auth/middleware";

type ServiceKeyRule = {
  prefix: string;
  header: string;
};

const SERVICE_KEY_RULES: ServiceKeyRule[] = [
  {
    prefix: "/api/orchestrator/ingest/document/external",
    header: "x-service-api-key",
  },
  {
    prefix: "/api/orchestrator/ingest/event/external",
    header: "x-service-api-key",
  },
  {
    prefix: "/api/orchestrator/ingest/events/notes",
    header: "x-service-api-key",
  },
  {
    prefix: "/api/gate",
    header: "x-service-api-key",
  },
  {
    prefix: "/api/webhooks/telegram",
    header: "x-telegram-bot-api-secret-token",
  },
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

function findServiceKeyRule(pathname: string): ServiceKeyRule | undefined {
  return SERVICE_KEY_RULES.find((rule) => pathname.startsWith(rule.prefix));
}

function validateServiceHeader(request: NextRequest, rule: ServiceKeyRule) {
  if (request.headers.has(rule.header)) {
    return null;
  }

  return NextResponse.json(
    { detail: `Missing ${rule.header} header` },
    {
      status: 401,
      headers: { "content-type": "application/json" },
    }
  );
}

export default function middleware(request: NextRequest, event: NextFetchEvent) {
  const pathname = request.nextUrl.pathname;

  const serviceKeyRule = findServiceKeyRule(pathname);

  if (serviceKeyRule) {
    const validationResponse = validateServiceHeader(request, serviceKeyRule);
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
