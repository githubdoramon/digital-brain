import { NextRequest } from "next/server";

import { proxyMeetingRequest } from "./_proxy";

export async function POST(request: NextRequest) {
  return proxyMeetingRequest(request, "/ingest/meetings");
}

