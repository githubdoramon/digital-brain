import { NextResponse } from "next/server";

export async function GET() {
  return NextResponse.json({
    name: "Frontend Web",
    id: "frontend",
    version: process.env.NEXT_PUBLIC_APP_VERSION ?? "unknown",
    git_sha: process.env.NEXT_PUBLIC_APP_GIT_SHA ?? null,
    build_time: process.env.NEXT_PUBLIC_APP_BUILD_TIME ?? null,
    deployment: process.env.NEXT_PUBLIC_APP_DEPLOYMENT ?? null,
    sources: ["frontend_env"],
  });
}

