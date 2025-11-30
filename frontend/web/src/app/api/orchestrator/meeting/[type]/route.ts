import { NextRequest } from "next/server";

import { proxyMeetingRequest } from "../_proxy";

const BACKEND_PATHS: Record<string, string> = {
  notes: "/ingest/meetings/notes",
  update: "/ingest/meetings/update",
};

type RouteParams = {
  type?: string;
};

export async function POST(request: NextRequest, context: { params: RouteParams }) {
  const type = context.params?.type ?? "";
  const backendPath = BACKEND_PATHS[type];
  if (!backendPath) {
    return new Response("Not Found", { status: 404 });
  }
  return proxyMeetingRequest(request, backendPath);
}

