import AsyncStorage from '@react-native-async-storage/async-storage';
import * as FileSystem from 'expo-file-system/legacy';

const LOG_FILE_NAME = 'digital-brain-mentra-debug.jsonl';
const WAKE_COMMAND_LOG_FILE_NAME = 'digital-brain-wake-command-debug.jsonl';
const LOG_DIRECTORY_URI = FileSystem.documentDirectory ?? FileSystem.cacheDirectory ?? '';
const DEFAULT_LOG_FILE_URI = `${LOG_DIRECTORY_URI}${LOG_FILE_NAME}`;
const DEFAULT_WAKE_COMMAND_LOG_URI = `${LOG_DIRECTORY_URI}${WAKE_COMMAND_LOG_FILE_NAME}`;
const ACTIVE_LOG_URI_STORAGE_KEY = 'digital_brain_mentra_debug_active_log_uri.v1';
const ACTIVE_WAKE_COMMAND_LOG_URI_STORAGE_KEY =
  'digital_brain_wake_command_debug_active_log_uri.v1';
const MAX_LOG_BYTES = 1_000_000;
const LOG_TRIM_HEADROOM_BYTES = 64_000;
const MAX_STRING_LENGTH = 600;

let writeChain: Promise<void> = Promise.resolve();
let wakeCommandWriteChain: Promise<void> = Promise.resolve();
let activeLogUri = DEFAULT_LOG_FILE_URI;
let activeLogUriLoaded = false;
let activeLogUriLoad: Promise<void> | null = null;
let activeLogGeneration = 0;
let activeWakeCommandLogUri = DEFAULT_WAKE_COMMAND_LOG_URI;
let activeWakeCommandLogUriLoaded = false;
let activeWakeCommandLogUriLoad: Promise<void> | null = null;
let activeWakeCommandLogGeneration = 0;
const logFileSizes = new Map<string, number>();

const REDACTED_KEY =
  /(?:uri|url|path|file|body|bytes|token|password|auth|ssid|capture|asset|requestid|address|deviceid|devicename|name|id)/i;

function isTraceIdentifierKey(key: string): boolean {
  const normalized = key.replace(/[^a-z]/gi, '').toLowerCase();
  return ['commandid', 'requestid', 'correlationid', 'traceid'].includes(normalized);
}

function utf8ByteLength(value: string): number {
  let length = 0;
  for (const character of value) {
    const codePoint = character.codePointAt(0) ?? 0;
    length += codePoint <= 0x7f ? 1 : codePoint <= 0x7ff ? 2 : codePoint <= 0xffff ? 3 : 4;
  }
  return length;
}

async function getLogFileSize(uri: string): Promise<number> {
  const cached = logFileSizes.get(uri);
  if (cached !== undefined) return cached;
  const info = await FileSystem.getInfoAsync(uri).catch(() => null);
  const size = info?.exists && 'size' in info ? (info.size ?? 0) : 0;
  logFileSizes.set(uri, size);
  return size;
}

function rememberLogFileSize(uri: string, size: number): void {
  logFileSizes.delete(uri);
  logFileSizes.set(uri, size);
  while (logFileSizes.size > 8) {
    const oldestUri = logFileSizes.keys().next().value;
    if (typeof oldestUri !== 'string') break;
    logFileSizes.delete(oldestUri);
  }
}

async function appendBoundedLine(uri: string, line: string): Promise<void> {
  const currentSize = await getLogFileSize(uri);
  await FileSystem.writeAsStringAsync(uri, line, {
    encoding: FileSystem.EncodingType.UTF8,
    append: true,
  });
  let nextSize = currentSize + utf8ByteLength(line);
  if (nextSize > MAX_LOG_BYTES + LOG_TRIM_HEADROOM_BYTES) {
    const existing = await FileSystem.readAsStringAsync(uri).catch(() => '');
    let retained = existing;
    while (retained && utf8ByteLength(retained) > MAX_LOG_BYTES) {
      const nextLine = retained.indexOf('\n');
      if (nextLine < 0) {
        retained = '';
        break;
      }
      retained = retained.slice(nextLine + 1);
    }
    await FileSystem.writeAsStringAsync(uri, retained, {
      encoding: FileSystem.EncodingType.UTF8,
    });
    nextSize = utf8ByteLength(retained);
  }
  rememberLogFileSize(uri, nextSize);
}

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
        if (!isTraceIdentifierKey(key) && REDACTED_KEY.test(key)) {
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

export function getMentraDebugLogExportFileName(): string {
  const timestamp = new Date().toISOString().replace(/[:.]/g, '-');
  return `digital-brain-mentra-debug-${timestamp}.jsonl`;
}

export function getWakeCommandDebugLogExportFileName(): string {
  const timestamp = new Date().toISOString().replace(/[:.]/g, '-');
  return `digital-brain-wake-command-debug-${timestamp}.jsonl`;
}

function buildLogLine(event: string, payload?: Record<string, unknown> | unknown): string {
  return `${JSON.stringify({
    timestamp: new Date().toISOString(),
    event: redactString(event),
    payload: redact(payload),
  })}\n`;
}

async function loadActiveLogUri(): Promise<void> {
  if (activeLogUriLoaded) return;
  if (!activeLogUriLoad) {
    const generationAtLoadStart = activeLogGeneration;
    activeLogUriLoad = AsyncStorage.getItem(ACTIVE_LOG_URI_STORAGE_KEY)
      .then((savedUri) => {
        if (
          generationAtLoadStart === activeLogGeneration &&
          typeof savedUri === 'string' &&
          savedUri.startsWith(LOG_DIRECTORY_URI)
        ) {
          activeLogUri = savedUri;
        }
      })
      .catch(() => undefined)
      .finally(() => {
        activeLogUriLoaded = true;
      });
  }
  await activeLogUriLoad;
}

async function getActiveLogUri(): Promise<string> {
  await loadActiveLogUri();
  return activeLogUri;
}

async function loadActiveWakeCommandLogUri(): Promise<void> {
  if (activeWakeCommandLogUriLoaded) return;
  if (!activeWakeCommandLogUriLoad) {
    const generationAtLoadStart = activeWakeCommandLogGeneration;
    activeWakeCommandLogUriLoad = AsyncStorage.getItem(ACTIVE_WAKE_COMMAND_LOG_URI_STORAGE_KEY)
      .then((savedUri) => {
        if (
          generationAtLoadStart === activeWakeCommandLogGeneration &&
          typeof savedUri === 'string' &&
          savedUri.startsWith(LOG_DIRECTORY_URI)
        ) {
          activeWakeCommandLogUri = savedUri;
        }
      })
      .catch(() => undefined)
      .finally(() => {
        activeWakeCommandLogUriLoaded = true;
      });
  }
  await activeWakeCommandLogUriLoad;
}

async function getActiveWakeCommandLogUri(): Promise<string> {
  await loadActiveWakeCommandLogUri();
  return activeWakeCommandLogUri;
}

export async function appendMentraDebugLog(
  event: string,
  payload?: Record<string, unknown> | unknown,
): Promise<void> {
  const line = buildLogLine(event, payload);
  const generationAtAppendStart = activeLogGeneration;
  const targetUri = await getActiveLogUri();
  if (generationAtAppendStart !== activeLogGeneration) return;
  writeChain = writeChain
    .catch(() => undefined)
    .then(() => appendBoundedLine(targetUri, line));
  await writeChain;
}

/**
 * A deliberately narrow, unredacted trace for the local wake-command POC.
 * It contains only command pipeline events and is exported alongside the
 * matching locally retained WAV clips, never sent to the backend.
 */
export async function appendWakeCommandDebugLog(
  event: string,
  payload?: Record<string, unknown> | unknown,
): Promise<void> {
  const line = `${JSON.stringify({
    timestamp: new Date().toISOString(),
    event,
    payload,
  })}\n`;
  const generationAtAppendStart = activeWakeCommandLogGeneration;
  const targetUri = await getActiveWakeCommandLogUri();
  if (generationAtAppendStart !== activeWakeCommandLogGeneration) return;
  wakeCommandWriteChain = wakeCommandWriteChain
    .catch(() => undefined)
    .then(() => appendBoundedLine(targetUri, line));
  await wakeCommandWriteChain;
}

export async function readMentraDebugLog(): Promise<string> {
  const targetUri = await getActiveLogUri();
  return FileSystem.readAsStringAsync(targetUri).catch(() => '');
}

export async function getMentraDebugLogInfo(): Promise<{ exists: boolean; sizeBytes: number }> {
  const targetUri = await getActiveLogUri();
  const info = await FileSystem.getInfoAsync(targetUri);
  return {
    exists: info.exists,
    sizeBytes: info.exists && 'size' in info ? (info.size ?? 0) : 0,
  };
}

export async function readWakeCommandDebugLog(): Promise<string> {
  const targetUri = await getActiveWakeCommandLogUri();
  return FileSystem.readAsStringAsync(targetUri).catch(() => '');
}

export async function getWakeCommandDebugLogInfo(): Promise<{
  exists: boolean;
  sizeBytes: number;
}> {
  const targetUri = await getActiveWakeCommandLogUri();
  const info = await FileSystem.getInfoAsync(targetUri);
  return {
    exists: info.exists,
    sizeBytes: info.exists && 'size' in info ? (info.size ?? 0) : 0,
  };
}

export async function clearWakeCommandDebugLog(): Promise<void> {
  const clearedAt = new Date().toISOString();
  const generation = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
  const nextUri = `${LOG_DIRECTORY_URI}digital-brain-wake-command-debug-${generation}.jsonl`;
  activeWakeCommandLogGeneration += 1;
  activeWakeCommandLogUri = nextUri;
  activeWakeCommandLogUriLoaded = true;
  void AsyncStorage.setItem(ACTIVE_WAKE_COMMAND_LOG_URI_STORAGE_KEY, nextUri).catch(
    () => undefined,
  );
  // Serialize the marker with appends for the new generation so a concurrent
  // first event cannot be overwritten by the clear write.
  const marker = `${JSON.stringify({
    timestamp: clearedAt,
    event: 'wake_command_diagnostics_cleared',
    payload: { cleared_at: clearedAt },
  })}\n`;
  wakeCommandWriteChain = wakeCommandWriteChain.catch(() => undefined).then(async () => {
    await FileSystem.writeAsStringAsync(nextUri, marker, {
      encoding: FileSystem.EncodingType.UTF8,
    });
    rememberLogFileSize(nextUri, utf8ByteLength(marker));
  });
  await wakeCommandWriteChain;
}

export async function clearMentraDebugLog(): Promise<void> {
  const clearedAt = new Date().toISOString();
  const generation = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
  const nextUri = `${LOG_DIRECTORY_URI}digital-brain-mentra-debug-${generation}.jsonl`;

  // A clear owns a new file generation immediately. Pending writers keep their
  // captured old target, so neither a stale read nor an in-flight write can
  // restore cleared diagnostics to the current export.
  activeLogGeneration += 1;
  activeLogUri = nextUri;
  activeLogUriLoaded = true;
  void AsyncStorage.setItem(ACTIVE_LOG_URI_STORAGE_KEY, nextUri).catch(() => undefined);
  void clearWakeCommandDebugLog().catch(() => undefined);
  const marker = buildLogLine('mentra_diagnostics_cleared', { cleared_at: clearedAt });
  writeChain = writeChain.catch(() => undefined).then(async () => {
    await FileSystem.writeAsStringAsync(nextUri, marker, {
      encoding: FileSystem.EncodingType.UTF8,
    });
    rememberLogFileSize(nextUri, utf8ByteLength(marker));
  });
  await writeChain;
}
