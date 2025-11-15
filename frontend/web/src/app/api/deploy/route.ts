import { NextRequest } from "next/server";
import { timingSafeEqual } from "node:crypto";
import { execFile } from "node:child_process";
import { promisify } from "node:util";

export const runtime = "nodejs";

const execFileAsync = promisify(execFile);

const DEPLOY_KEY = process.env.DEPLOY_WEBHOOK_KEY;
const SCRIPT_PATH = process.env.DEPLOY_SCRIPT_PATH;
const DEFAULT_SCRIPT_TIMEOUT_MS = 15 * 60 * 1000;
const timeoutRaw = process.env.DEPLOY_SCRIPT_TIMEOUT_MS;
const parsedTimeout = timeoutRaw ? Number.parseInt(timeoutRaw, 10) : DEFAULT_SCRIPT_TIMEOUT_MS;
const SCRIPT_TIMEOUT_MS = Number.isFinite(parsedTimeout) && parsedTimeout > 0 ? parsedTimeout : undefined;

function secureCompare(a: string, b: string): boolean {
  if (a.length !== b.length) {
    return false;
  }

  const aBuffer = Buffer.from(a);
  const bBuffer = Buffer.from(b);

  return timingSafeEqual(aBuffer, bBuffer);
}

function jsonResponse(body: unknown, init?: ResponseInit) {
  return new Response(JSON.stringify(body), {
    ...init,
    headers: {
      "content-type": "application/json",
      ...(init?.headers ?? {}),
    },
  });
}

export async function POST(request: NextRequest) {
  if (!DEPLOY_KEY) {
    console.error("DEPLOY_WEBHOOK_KEY is not configured");
    return jsonResponse({ detail: "Deployment key not configured" }, { status: 500 });
  }

  if (!SCRIPT_PATH) {
    console.error("DEPLOY_SCRIPT_PATH is not configured");
    return jsonResponse({ detail: "Deployment script path not configured" }, { status: 500 });
  }

  const providedKey = request.headers.get("x-deploy-key");
  if (!providedKey || !secureCompare(providedKey, DEPLOY_KEY)) {
    return jsonResponse({ detail: "Unauthorized" }, { status: 401 });
  }

  try {
    const { stdout, stderr } = await execFileAsync(SCRIPT_PATH, {
      timeout: SCRIPT_TIMEOUT_MS,
      shell: false,
      windowsHide: true,
    });

    return jsonResponse({
      status: "ok",
      stdout: stdout?.trim() ?? "",
      stderr: stderr?.trim() ?? "",
    });
  } catch (error) {
    console.error("Deploy script execution failed", error);

    if (error && typeof error === "object" && "stdout" in error && "stderr" in error) {
      const execError = error as { stdout?: string; stderr?: string; code?: number; signal?: string };
      return jsonResponse(
        {
          status: "error",
          detail: "Deploy script failed",
          stdout: execError.stdout?.trim() ?? "",
          stderr: execError.stderr?.trim() ?? "",
          exitCode: execError.code ?? null,
          signal: execError.signal ?? null,
        },
        { status: 500 }
      );
    }

    return jsonResponse({ detail: "Deploy script execution error" }, { status: 500 });
  }
}
