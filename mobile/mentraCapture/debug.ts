import * as FileSystem from 'expo-file-system/legacy';

const LOG_FILE_NAME = 'digital-brain-mentra-debug.jsonl';
const LOG_FILE_URI = `${FileSystem.documentDirectory ?? FileSystem.cacheDirectory}${LOG_FILE_NAME}`;
const MAX_LOG_BYTES = 1_000_000;
const MAX_STRING_LENGTH = 600;

let writeChain: Promise<void> = Promise.resolve();

const REDACTED_KEY =
  /(?:uri|url|path|file|body|bytes|token|password|auth|ssid|capture|asset|requestid|address|deviceid|devicename|name|id)/i;

function redactString(value: string): string {
  return value
    .replace(/(?:https?|file):\/\/[^\s"']+/gi, '[redacted-url]')
    .slice(0, MAX_STRING_LENGTH);
}

function redact(value: unknown, depth = 0): unknown {
  if (value == null || typeof value === 'number' || typeof value === 'boolean') return value;
  if (typeof value === 'string') return redactString(value);
  if (depth >= 4) return '[truncated]';
  if (Array.isArray(value)) return value.slice(0, 24).map((item) => redact(item, depth + 1));
  if (typeof value === 'object') {
    const result: Record<string, unknown> = {};
    Object.entries(value as Record<string, unknown>)
      .slice(0, 40)
      .forEach(([key, item]) => {
        if (REDACTED_KEY.test(key)) {
          result[key] = '[redacted]';
        } else {
          result[key] = redact(item, depth + 1);
        }
      });
    return result;
  }
  return String(value);
}

export function getMentraDebugLogFileName(): string {
  return LOG_FILE_NAME;
}

export async function appendMentraDebugLog(
  event: string,
  payload?: Record<string, unknown> | unknown,
): Promise<void> {
  const line = `${JSON.stringify({
    timestamp: new Date().toISOString(),
    event: redactString(event),
    payload: redact(payload),
  })}\n`;
  writeChain = writeChain
    .catch(() => undefined)
    .then(async () => {
      const existing = await FileSystem.readAsStringAsync(LOG_FILE_URI).catch(() => '');
      const next = `${existing}${line}`;
      await FileSystem.writeAsStringAsync(
        LOG_FILE_URI,
        next.length > MAX_LOG_BYTES ? next.slice(-MAX_LOG_BYTES) : next,
        { encoding: FileSystem.EncodingType.UTF8 },
      );
    });
  await writeChain;
}

export async function readMentraDebugLog(): Promise<string> {
  return FileSystem.readAsStringAsync(LOG_FILE_URI).catch(() => '');
}

export async function getMentraDebugLogInfo(): Promise<{ exists: boolean; sizeBytes: number }> {
  const info = await FileSystem.getInfoAsync(LOG_FILE_URI);
  return {
    exists: info.exists,
    sizeBytes: info.exists && 'size' in info ? (info.size ?? 0) : 0,
  };
}

export async function clearMentraDebugLog(): Promise<void> {
  await FileSystem.deleteAsync(LOG_FILE_URI, { idempotent: true });
}
