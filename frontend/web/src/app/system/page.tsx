"use client";

import { useEffect, useRef, useState } from "react";
import {
  api,
  getSystemLogs,
  LogEntry,
  LogLevel,
  LogMessageSegment,
  streamSystemLogs,
} from "@/lib/api";

type ServiceVersion = {
  id: string;
  name: string;
  version: string;
  git_sha?: string | null;
  build_time?: string | null;
  image?: string | null;
  sources: string[];
  notes?: string | null;
  metadata?: Record<string, unknown>;
};

type ServiceVersionResponse = {
  generated_at: string;
  services: ServiceVersion[];
  manifest_path?: string | null;
  manifest_metadata?: Record<string, unknown>;
  env_entry_count: number;
  fallback_count: number;
};

type LogRow = LogEntry & { rowKey: string };

const LOG_LEVELS: LogLevel[] = ["debug", "info", "decision", "warning", "error"];
const LOG_CONTENT_MAX_WIDTH = 1024;
const HIDDEN_INDEX_STYLE = {
  position: "absolute",
  width: "1px",
  height: "1px",
  margin: "-1px",
  padding: 0,
  border: 0,
  overflow: "hidden",
  clip: "rect(0 0 0 0)",
  whiteSpace: "pre",
} as const;

function sortLogEntries(entries: LogRow[]): LogRow[] {
  return [...entries].sort((left, right) => {
    const leftTime = Date.parse(left.timestamp) || 0;
    const rightTime = Date.parse(right.timestamp) || 0;
    if (leftTime === rightTime) {
      const leftKey = typeof left.id === "number" ? left.id.toString() : left.rowKey;
      const rightKey = typeof right.id === "number" ? right.id.toString() : right.rowKey;
      return leftKey.localeCompare(rightKey);
    }
    return leftTime - rightTime;
  });
}

function toLogRow(entry: LogEntry): LogRow {
  const rowKey =
    typeof entry.id === "number"
      ? `id:${entry.id}`
      : `${entry.timestamp}-${Math.random().toString(36).slice(2)}`;
  return {
    ...entry,
    rowKey,
  };
}

function getLogKey(entry: LogEntry): string {
  if (typeof entry.id === "number") {
    return `id:${entry.id}`;
  }
  return `${entry.timestamp}|${entry.level}|${entry.message}`;
}

function formatDate(input?: string | null): string {
  if (!input) {
    return "—";
  }

  const parsed = new Date(input);
  if (Number.isNaN(parsed.getTime())) {
    return input;
  }

  return parsed.toLocaleString([], {
    year: "numeric",
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function abbreviateSha(sha?: string | null): string {
  if (!sha) {
    return "—";
  }
  return sha.length > 10 ? sha.slice(0, 10) : sha;
}

function formatLogTimestamp(input: string): string {
  const parsed = new Date(input);
  if (Number.isNaN(parsed.getTime())) {
    return input;
  }
  return parsed.toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function tryParseJsonAt(text: string, start: number): { end: number; value: unknown; raw: string } | null {
  const first = text[start];
  if (first !== "{" && first !== "[") {
    return null;
  }

  const stack: string[] = [first];
  let inString = false;
  let isEscaped = false;

  for (let index = start + 1; index < text.length; index += 1) {
    const char = text[index];

    if (inString) {
      if (isEscaped) {
        isEscaped = false;
        continue;
      }
      if (char === "\\") {
        isEscaped = true;
        continue;
      }
      if (char === '"') {
        inString = false;
      }
      continue;
    }

    if (char === '"') {
      inString = true;
      continue;
    }

    if (char === "{" || char === "[") {
      stack.push(char);
      continue;
    }

    if (char === "}" || char === "]") {
      const top = stack[stack.length - 1];
      const isMatch = (top === "{" && char === "}") || (top === "[" && char === "]");
      if (!isMatch) {
        return null;
      }

      stack.pop();
      if (stack.length === 0) {
        const raw = text.slice(start, index + 1);
        try {
          const value = JSON.parse(raw);
          return { end: index, value, raw };
        } catch {
          return null;
        }
      }
    }
  }

  return null;
}

function splitLogMessageSegments(message: string): LogMessageSegment[] {
  const segments: LogMessageSegment[] = [];
  let textStart = 0;
  let cursor = 0;

  while (cursor < message.length) {
    const char = message[cursor];
    if (char !== "{" && char !== "[") {
      cursor += 1;
      continue;
    }

    const parsed = tryParseJsonAt(message, cursor);
    if (!parsed) {
      cursor += 1;
      continue;
    }

    if (textStart < cursor) {
      segments.push({ kind: "text", content: message.slice(textStart, cursor) });
    }
    segments.push({ kind: "json", content: parsed.raw, value: parsed.value });
    cursor = parsed.end + 1;
    textStart = cursor;
  }

  if (textStart < message.length) {
    segments.push({ kind: "text", content: message.slice(textStart) });
  }

  return segments.length > 0 ? segments : [{ kind: "text", content: message }];
}

function getLogSegments(entry: LogEntry): LogMessageSegment[] {
  const backendSegments = entry.message_segments?.filter(
    (segment): segment is LogMessageSegment =>
      !!segment && (segment.kind === "text" || segment.kind === "json") && typeof segment.content === "string"
  );
  return backendSegments && backendSegments.length > 0 ? backendSegments : splitLogMessageSegments(entry.message);
}

function buildJsonSearchIndex(value: unknown, path = "root", depth = 0): string {
  if (depth > 7) {
    return `${path}: [max-depth]`;
  }
  if (value === null) {
    return `${path}: null`;
  }
  if (Array.isArray(value)) {
    const base = `${path}: [array length=${value.length}]`;
    const nested = value.slice(0, 100).map((item, index) => buildJsonSearchIndex(item, `${path}[${index}]`, depth + 1));
    return [base, ...nested].join("\n");
  }
  if (value && typeof value === "object") {
    const entries = Object.entries(value as Record<string, unknown>);
    const base = `${path}: {object keys=${entries.length}}`;
    const nested = entries.slice(0, 200).map(([key, nestedValue]) => buildJsonSearchIndex(nestedValue, `${path}.${key}`, depth + 1));
    return [base, ...nested].join("\n");
  }
  return `${path}: ${String(value)}`;
}

function matchesSearch(haystack: string, query: string): boolean {
  if (!query) {
    return false;
  }
  return haystack.toLowerCase().includes(query.toLowerCase());
}

function formatJsonSummary(value: unknown): string {
  if (Array.isArray(value)) {
    return `Array(${value.length})`;
  }
  if (value && typeof value === "object") {
    const keys = Object.keys(value as Record<string, unknown>);
    if (keys.length === 0) {
      return "Object";
    }
    const preview = keys.slice(0, 3).join(", ");
    return keys.length > 3 ? `Object { ${preview}, ... }` : `Object { ${preview} }`;
  }
  return typeof value === "string" ? value : JSON.stringify(value);
}

function renderJsonValue(value: unknown) {
  if (value === null) {
    return <span style={{ color: "#fda4af" }}>null</span>;
  }
  if (typeof value === "string") {
    return <span style={{ color: "#86efac" }}>&quot;{value}&quot;</span>;
  }
  if (typeof value === "number") {
    return <span style={{ color: "#fcd34d" }}>{value}</span>;
  }
  if (typeof value === "boolean") {
    return <span style={{ color: "#93c5fd" }}>{String(value)}</span>;
  }
  return <span style={{ color: "#cbd5e1" }}>{String(value)}</span>;
}

function JsonTreeNode({ name, value }: { name?: string; value: unknown }) {
  if (Array.isArray(value)) {
    return (
      <details style={{ marginTop: "4px" }}>
        <summary style={{ cursor: "pointer", color: "#93c5fd", overflowWrap: "anywhere" }}>
          {name ? `${name}: ` : ""}
          {formatJsonSummary(value)}
        </summary>
        <div style={{ marginLeft: "14px", paddingLeft: "10px", borderLeft: "1px solid #334155" }}>
          {value.map((item, index) => (
            <JsonTreeNode key={`${name ?? "root"}-${index}`} name={`[${index}]`} value={item} />
          ))}
        </div>
      </details>
    );
  }

  if (value && typeof value === "object") {
    const entries = Object.entries(value as Record<string, unknown>);
    return (
      <details style={{ marginTop: "4px" }}>
        <summary style={{ cursor: "pointer", color: "#93c5fd", overflowWrap: "anywhere" }}>
          {name ? `${name}: ` : ""}
          {formatJsonSummary(value)}
        </summary>
        <div style={{ marginLeft: "14px", paddingLeft: "10px", borderLeft: "1px solid #334155" }}>
          {entries.length === 0 ? (
            <div style={{ color: "#94a3b8", marginTop: "4px" }}>&#123;&#125;</div>
          ) : (
            entries.map(([key, nested]) => <JsonTreeNode key={key} name={key} value={nested} />)
          )}
        </div>
      </details>
    );
  }

  return (
    <div style={{ marginTop: "4px", overflowWrap: "anywhere", wordBreak: "break-word" }}>
      {name ? <span style={{ color: "#f8fafc" }}>{name}: </span> : null}
      {renderJsonValue(value)}
    </div>
  );
}

function LogMessageContent({ entry, searchQuery }: { entry: LogRow; searchQuery: string }) {
  const segments = getLogSegments(entry);

  const copyJson = async (value: unknown) => {
    try {
      await navigator.clipboard.writeText(JSON.stringify(value, null, 2));
    } catch {
      // noop
    }
  };

  return (
    <div style={{ minWidth: 0, display: "grid", gap: "4px" }}>
      {segments.map((segment, index) => {
        if (segment.kind === "text") {
          return (
            <span key={`text-${index}`} style={{ whiteSpace: "pre", overflowWrap: "normal", wordBreak: "normal" }}>
              {segment.content}
            </span>
          );
        }

        const searchIndex = buildJsonSearchIndex(segment.value);
        const hasJsonMatch = matchesSearch(searchIndex, searchQuery) || matchesSearch(segment.content, searchQuery);

        return (
          <div key={`json-${index}`} style={{ border: "1px solid #334155", borderRadius: "8px", padding: "8px", position: "relative" }}>
            <button
              type="button"
              aria-label="Copy JSON"
              title="Copy JSON"
              onClick={() => copyJson(segment.value)}
              style={{
                position: "absolute",
                top: "6px",
                right: "6px",
                border: "1px solid #475569",
                borderRadius: "6px",
                background: "#0b1220",
                color: "#cbd5e1",
                width: "22px",
                height: "22px",
                padding: 0,
                display: "inline-flex",
                alignItems: "center",
                justifyContent: "center",
                cursor: "pointer",
              }}
            >
              <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5">
                <rect x="6" y="2.5" width="7.5" height="10.5" rx="1.5" />
                <path d="M3.5 10.5H2.8A1.8 1.8 0 0 1 1 8.7V3.8A1.8 1.8 0 0 1 2.8 2h4.9A1.8 1.8 0 0 1 9.5 3.8v.7" />
              </svg>
            </button>
            <span style={HIDDEN_INDEX_STYLE}>{searchIndex}</span>
            {searchQuery ? (
              <span
                style={{
                  position: "absolute",
                  top: "8px",
                  right: "34px",
                  fontSize: "0.65rem",
                  lineHeight: 1,
                  padding: "3px 6px",
                  borderRadius: "999px",
                  border: "1px solid",
                  borderColor: hasJsonMatch ? "#16a34a" : "#475569",
                  color: hasJsonMatch ? "#bbf7d0" : "#94a3b8",
                  background: hasJsonMatch ? "rgba(22, 163, 74, 0.12)" : "#0b1220",
                }}
              >
                {hasJsonMatch ? "Match" : "No match"}
              </span>
            ) : null}
            <JsonTreeNode value={segment.value} />
          </div>
        );
      })}
    </div>
  );
}

export default function SystemStatusPage() {
  const [data, setData] = useState<ServiceVersionResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [isBuildExpanded, setIsBuildExpanded] = useState(false);
  const [logEntries, setLogEntries] = useState<LogRow[]>([]);
  const [logError, setLogError] = useState<string | null>(null);
  const [selectedLevels, setSelectedLevels] = useState<LogLevel[]>(LOG_LEVELS);
  const [logSearchQuery, setLogSearchQuery] = useState("");
  const [activeLogMatchIndex, setActiveLogMatchIndex] = useState(0);
  const [isLogPaused, setIsLogPaused] = useState(false);
  const [logConnected, setLogConnected] = useState(false);
  const [isLogFullscreen, setIsLogFullscreen] = useState(false);
  const logContainerRef = useRef<HTMLDivElement | null>(null);
  const seenLogKeysRef = useRef<Set<string>>(new Set());
  const logRowRefs = useRef<Map<string, HTMLDivElement>>(new Map());

  const frontendMetadata = {
    version: process.env.NEXT_PUBLIC_APP_VERSION ?? "unknown",
    gitSha: process.env.NEXT_PUBLIC_APP_GIT_SHA ?? null,
    buildTime: process.env.NEXT_PUBLIC_APP_BUILD_TIME ?? null,
    deployment: process.env.NEXT_PUBLIC_APP_DEPLOYMENT ?? null,
  };

  useEffect(() => {
    let cancelled = false;

    (async () => {
      try {
        const response = await api.get<ServiceVersionResponse>("/system/versions");
        if (!cancelled) {
          setData(response);
        }
      } catch (err) {
        if (!cancelled) {
          console.error("Failed to load service version data", err);
          setError(err instanceof Error ? err.message : "Failed to load system version data.");
        }
      } finally {
        if (!cancelled) {
          setIsLoading(false);
        }
      }
    })();

    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let mounted = true;
    getSystemLogs("all", 60, 1000)
      .then((entries) => {
        if (!mounted) return;
        const rows = entries.map(toLogRow);
        const sorted = sortLogEntries(rows);
        seenLogKeysRef.current = new Set(sorted.map((row) => getLogKey(row)));
        setLogEntries(sorted);
      })
      .catch((err) => {
        if (!mounted) return;
        setLogError(err instanceof Error ? err.message : "Failed to fetch logs.");
      });
    return () => {
      mounted = false;
    };
  }, []);

  useEffect(() => {
    if (isLogPaused) {
      setLogConnected(false);
      return;
    }

    let cancelled = false;
    const controller = new AbortController();
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;

    const wait = (ms: number) =>
      new Promise<void>((resolve) => {
        reconnectTimer = setTimeout(resolve, ms);
      });

    const runStreamLoop = async () => {
      let reconnectAttempt = 0;

      while (!cancelled && !controller.signal.aborted) {
        setLogError(null);
        setLogConnected(true);

        try {
          await streamSystemLogs(
            "all",
            (entry) => {
              const key = getLogKey(entry);
              if (seenLogKeysRef.current.has(key)) {
                return;
              }
              seenLogKeysRef.current.add(key);
              setLogEntries((current) => {
                const next = sortLogEntries([...current, toLogRow(entry)]);
                const trimmed = next.length > 1000 ? next.slice(-1000) : next;
                if (seenLogKeysRef.current.size > 5000) {
                  seenLogKeysRef.current = new Set(trimmed.map((row) => getLogKey(row)));
                }
                return trimmed;
              });
            },
            (message) => {
              setLogError(message);
            },
            controller.signal
          );

          if (cancelled || controller.signal.aborted) {
            break;
          }

          setLogConnected(false);
          setLogError("Log stream disconnected. Reconnecting...");
        } catch (err) {
          if (cancelled || controller.signal.aborted) {
            break;
          }

          setLogConnected(false);
          setLogError(err instanceof Error ? err.message : "Log stream disconnected. Retrying...");
        }

        reconnectAttempt += 1;
        const backoffMs = Math.min(5000, 500 * 2 ** Math.min(reconnectAttempt, 3));
        await wait(backoffMs);
      }
    };

    void runStreamLoop();

    return () => {
      cancelled = true;
      controller.abort();
      if (reconnectTimer) {
        clearTimeout(reconnectTimer);
      }
    };
  }, [isLogPaused]);

  const toggleLevel = (level: LogLevel) => {
    setSelectedLevels((current) => {
      if (current.includes(level)) {
        return current.filter((item) => item !== level);
      }
      return [...current, level];
    });
  };

  const resetLevels = () => {
    setSelectedLevels(LOG_LEVELS);
  };

  const filteredLogs = selectedLevels.length
    ? logEntries.filter((entry) => selectedLevels.includes(entry.level))
    : [];
  const matchedLogRowKeys = logSearchQuery
    ? filteredLogs
        .filter((entry) => {
          if (matchesSearch(entry.message, logSearchQuery)) {
            return true;
          }
          return getLogSegments(entry).some((segment) => {
            if (segment.kind !== "json") {
              return false;
            }
            return matchesSearch(buildJsonSearchIndex(segment.value), logSearchQuery) || matchesSearch(segment.content, logSearchQuery);
          });
        })
        .map((entry) => entry.rowKey)
    : [];
  const hasSearchMatches = matchedLogRowKeys.length > 0;
  const activeMatchRowKey = hasSearchMatches
    ? matchedLogRowKeys[((activeLogMatchIndex % matchedLogRowKeys.length) + matchedLogRowKeys.length) % matchedLogRowKeys.length]
    : null;

  useEffect(() => {
    setActiveLogMatchIndex(0);
  }, [logSearchQuery]);

  useEffect(() => {
    if (!activeMatchRowKey) {
      return;
    }
    const node = logRowRefs.current.get(activeMatchRowKey);
    if (node) {
      node.scrollIntoView({ behavior: "smooth", block: "nearest", inline: "nearest" });
    }
  }, [activeMatchRowKey]);

  useEffect(() => {
    const container = logContainerRef.current;
    if (!container) return;
    container.scrollTop = container.scrollHeight;
  }, [filteredLogs.length]);

  const logPanel = (
    <div
      style={{
        border: "1px solid #e2e2e2",
        borderRadius: "12px",
        padding: "16px",
        background: "#fff",
        display: "flex",
        flexDirection: "column",
        gap: "12px",
        height: isLogFullscreen ? "100%" : "auto",
        width: "100%",
        maxWidth: "100%",
        minWidth: 0,
        overflow: "hidden",
        boxSizing: "border-box",
        flex: isLogFullscreen ? "1 1 auto" : undefined,
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: "12px" }}>
        <div>
          <h2 style={{ fontSize: "1.1rem", fontWeight: 600, margin: 0 }}>Orchestrator Logs</h2>
          <p style={{ margin: "6px 0 0", color: "#6b7280", fontSize: "0.9rem" }}>
            Live log stream via SSE. Showing {filteredLogs.length} of {logEntries.length} entries.
          </p>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: "8px", flexWrap: "wrap", justifyContent: "flex-end" }}>
          <div style={{ display: "flex", alignItems: "center", gap: "6px", flexWrap: "wrap" }}>
            <input
              value={logSearchQuery}
              onChange={(event) => setLogSearchQuery(event.target.value)}
              placeholder="Search logs + JSON"
              style={{
                border: "1px solid #d1d5db",
                borderRadius: "8px",
                padding: "6px 8px",
                fontSize: "0.8rem",
                minWidth: "180px",
                maxWidth: "220px",
              }}
            />
            {logSearchQuery ? (
              <>
                <button
                  type="button"
                  onClick={() => setActiveLogMatchIndex((prev) => prev - 1)}
                  disabled={!hasSearchMatches}
                  style={{
                    border: "1px solid #d1d5db",
                    borderRadius: "8px",
                    padding: "6px 8px",
                    fontSize: "0.8rem",
                    background: "#fff",
                    cursor: hasSearchMatches ? "pointer" : "not-allowed",
                    opacity: hasSearchMatches ? 1 : 0.55,
                  }}
                >
                  Prev
                </button>
                <button
                  type="button"
                  onClick={() => setActiveLogMatchIndex((prev) => prev + 1)}
                  disabled={!hasSearchMatches}
                  style={{
                    border: "1px solid #d1d5db",
                    borderRadius: "8px",
                    padding: "6px 8px",
                    fontSize: "0.8rem",
                    background: "#fff",
                    cursor: hasSearchMatches ? "pointer" : "not-allowed",
                    opacity: hasSearchMatches ? 1 : 0.55,
                  }}
                >
                  Next
                </button>
                <span style={{ fontSize: "0.75rem", color: hasSearchMatches ? "#475569" : "#b91c1c" }}>
                  {hasSearchMatches
                    ? `${((activeLogMatchIndex % matchedLogRowKeys.length) + matchedLogRowKeys.length) % matchedLogRowKeys.length + 1}/${matchedLogRowKeys.length}`
                    : "0 matches"}
                </span>
              </>
            ) : null}
          </div>
          <div style={{ display: "flex", gap: "6px", flexWrap: "wrap" }}>
            {LOG_LEVELS.map((level) => {
              const isActive = selectedLevels.includes(level);
              return (
                <button
                  key={level}
                  type="button"
                  onClick={() => toggleLevel(level)}
                  style={{
                    border: "1px solid #d1d5db",
                    borderRadius: "999px",
                    padding: "4px 10px",
                    fontSize: "0.8rem",
                    textTransform: "uppercase",
                    background: isActive ? "#e2e8f0" : "#fff",
                    cursor: "pointer",
                  }}
                >
                  {level}
                </button>
              );
            })}
          </div>
          <button
            type="button"
            onClick={resetLevels}
            style={{
              border: "1px solid #d1d5db",
              borderRadius: "8px",
              padding: "6px 10px",
              fontSize: "0.8rem",
              background: "#fff",
              cursor: "pointer",
            }}
          >
            All levels
          </button>
          <button
            type="button"
            onClick={() => setIsLogFullscreen((prev) => !prev)}
            aria-label={isLogFullscreen ? "Exit full screen" : "Enter full screen"}
            title={isLogFullscreen ? "Exit full screen" : "Enter full screen"}
            style={{
              border: "1px solid #d1d5db",
              borderRadius: "8px",
              padding: "6px",
              width: "32px",
              height: "32px",
              display: "inline-flex",
              alignItems: "center",
              justifyContent: "center",
              background: isLogFullscreen ? "#e2e8f0" : "#fff",
              cursor: "pointer",
            }}
          >
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6">
              <path d="M2.5 6V2.5H6" />
              <path d="M10 2.5H13.5V6" />
              <path d="M13.5 10V13.5H10" />
              <path d="M6 13.5H2.5V10" />
            </svg>
          </button>
          <button
            type="button"
            onClick={() => setIsLogPaused((prev) => !prev)}
            style={{
              border: "1px solid #d1d5db",
              borderRadius: "8px",
              padding: "6px 10px",
              fontSize: "0.9rem",
              background: isLogPaused ? "#e2e8f0" : "#fff",
              cursor: "pointer",
            }}
          >
            {isLogPaused ? "Resume" : "Pause"}
          </button>
          <button
            type="button"
            onClick={() => {
              setLogEntries([]);
              seenLogKeysRef.current.clear();
            }}
            style={{
              border: "1px solid #d1d5db",
              borderRadius: "8px",
              padding: "6px 10px",
              fontSize: "0.9rem",
              background: "#fff",
              cursor: "pointer",
            }}
          >
            Clear
          </button>
        </div>
      </div>

      <div style={{ display: "flex", gap: "12px", fontSize: "0.85rem", color: "#64748b" }}>
        <span>
          <strong>Status:</strong> {isLogPaused ? "Paused" : logConnected ? "Connected" : "Connecting"}
        </span>
        {logError && <span style={{ color: "#b91c1c" }}>{logError}</span>}
      </div>

      <div
        ref={logContainerRef}
        style={{
          border: "1px solid #e5e7eb",
          borderRadius: "10px",
          background: "#0f172a",
          color: "#e2e8f0",
          padding: "12px",
          flex: isLogFullscreen ? "1 1 auto" : undefined,
          minHeight: isLogFullscreen ? 0 : undefined,
          maxHeight: isLogFullscreen ? "none" : "320px",
          overflowX: "auto",
          overflowY: "auto",
          fontFamily: "var(--font-mono, monospace)",
          fontSize: "0.8rem",
        }}
      >
        {filteredLogs.length === 0 ? (
          <div style={{ color: "#94a3b8" }}>Waiting for log events...</div>
        ) : (
          <div style={{ minWidth: "100%", width: "max-content", maxWidth: `${LOG_CONTENT_MAX_WIDTH}px` }}>
            {filteredLogs.map((entry) => (
              <div
                key={entry.rowKey}
                ref={(node) => {
                  if (node) {
                    logRowRefs.current.set(entry.rowKey, node);
                  } else {
                    logRowRefs.current.delete(entry.rowKey);
                  }
                }}
                style={{ display: "grid", gridTemplateColumns: "80px 70px minmax(0, 1fr)", gap: "8px", minWidth: "100%" }}
              >
                <span style={{ color: "#94a3b8" }}>{formatLogTimestamp(entry.timestamp)}</span>
                <span
                  style={{
                    textTransform: "uppercase",
                    color:
                      entry.level === "error"
                        ? "#fca5a5"
                        : entry.level === "warning"
                        ? "#fde68a"
                        : entry.level === "info"
                        ? "#93c5fd"
                        : entry.level === "decision"
                        ? "#fbcfe8"
                        : "#cbd5f5",
                  }}
                >
                  {entry.level}
                </span>
                <div
                  style={
                    activeMatchRowKey === entry.rowKey
                      ? { outline: "1px solid #16a34a", borderRadius: "6px", padding: "2px 4px", background: "rgba(22, 163, 74, 0.08)" }
                      : undefined
                  }
                >
                  <LogMessageContent entry={entry} searchQuery={logSearchQuery} />
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );

  return (
    <section style={{ display: "grid", gap: "24px" }}>
      <div>
        <h1 style={{ fontSize: "1.75rem", fontWeight: 600 }}>System Status</h1>
        <p style={{ color: "#555", marginTop: "8px" }}>
          Overview of running containers, their versions, and build timestamps exposed by the orchestrator.
        </p>
      </div>

      <div
        style={{
          border: "1px solid #e2e2e2",
          borderRadius: "12px",
          padding: "16px",
          background: "#fff",
          display: "grid",
          gap: "12px",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: "12px" }}>
          <div>
            <h2 style={{ fontSize: "1.1rem", fontWeight: 600, margin: 0 }}>Build Versions</h2>
            <p style={{ margin: "6px 0 0", color: "#6b7280", fontSize: "0.9rem" }}>
              Frontend + backend build metadata.
            </p>
          </div>
          <button
            type="button"
            onClick={() => setIsBuildExpanded((prev) => !prev)}
            style={{
              border: "1px solid #d1d5db",
              borderRadius: "999px",
              padding: "6px 12px",
              fontSize: "0.85rem",
              background: "#fff",
              cursor: "pointer",
            }}
          >
            {isBuildExpanded ? "Hide" : "Show"}
          </button>
        </div>

        {isBuildExpanded && (
          <div style={{ display: "grid", gap: "16px" }}>
            <div
              style={{
                border: "1px solid #e2e2e2",
                borderRadius: "12px",
                padding: "16px",
                background: "#fff",
                display: "grid",
                gap: "8px",
              }}
            >
              <h3 style={{ fontSize: "1rem", fontWeight: 600, margin: 0 }}>Frontend Build</h3>
              <div style={{ fontSize: "0.95rem", color: "#111" }}>
                <strong>Version:</strong>{" "}
                <span style={{ fontFamily: "var(--font-mono, monospace)" }}>{frontendMetadata.version}</span>
              </div>
              <div style={{ fontSize: "0.95rem", color: "#374151" }}>
                <strong>Git SHA:</strong>{" "}
                <span style={{ fontFamily: "var(--font-mono, monospace)" }}>
                  {abbreviateSha(frontendMetadata.gitSha)}
                </span>
              </div>
              <div style={{ fontSize: "0.95rem", color: "#374151" }}>
                <strong>Built At:</strong> {formatDate(frontendMetadata.buildTime)}
              </div>
              {frontendMetadata.deployment && (
                <div style={{ fontSize: "0.95rem", color: "#4b5563" }}>
                  <strong>Deployment:</strong>{" "}
                  <span style={{ fontFamily: "var(--font-mono, monospace)" }}>
                    {frontendMetadata.deployment}
                  </span>
                </div>
              )}
              <div style={{ fontSize: "0.9rem" }}>
                <a href="/api/version" style={{ color: "#0b6bcb", textDecoration: "underline" }}>
                  View frontend version JSON
                </a>
              </div>
            </div>

            {error ? (
              <div
                style={{
                  border: "1px solid #fca5a5",
                  background: "#fee2e2",
                  color: "#b91c1c",
                  padding: "16px",
                  borderRadius: "8px",
                }}
              >
                <strong style={{ display: "block", marginBottom: "4px" }}>Unable to fetch versions</strong>
                <span>{error}</span>
              </div>
            ) : isLoading ? (
              <div style={{ color: "#666" }}>Loading service information...</div>
            ) : data ? (
              <>
                <div
                  style={{
                    border: "1px solid #e2e2e2",
                    borderRadius: "12px",
                    padding: "16px",
                    background: "#fff",
                    overflowX: "auto",
                  }}
                >
                  <table style={{ width: "100%", borderCollapse: "collapse", minWidth: "720px" }}>
                    <thead>
                      <tr style={{ textAlign: "left", borderBottom: "1px solid #e2e2e2" }}>
                        <th style={{ padding: "12px 8px", fontSize: "0.85rem", color: "#6b7280" }}>Service</th>
                        <th style={{ padding: "12px 8px", fontSize: "0.85rem", color: "#6b7280" }}>Version</th>
                        <th style={{ padding: "12px 8px", fontSize: "0.85rem", color: "#6b7280" }}>Git SHA</th>
                        <th style={{ padding: "12px 8px", fontSize: "0.85rem", color: "#6b7280" }}>Built At</th>
                        <th style={{ padding: "12px 8px", fontSize: "0.85rem", color: "#6b7280" }}>Image</th>
                        <th style={{ padding: "12px 8px", fontSize: "0.85rem", color: "#6b7280" }}>Sources</th>
                        <th style={{ padding: "12px 8px", fontSize: "0.85rem", color: "#6b7280" }}>Notes</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.services.map((service) => (
                        <tr key={service.id} style={{ borderBottom: "1px solid #f1f5f9" }}>
                          <td style={{ padding: "12px 8px", fontWeight: 500, color: "#0f172a" }}>
                            {service.name}
                          </td>
                          <td style={{ padding: "12px 8px", fontFamily: "var(--font-mono, monospace)" }}>
                            {service.version}
                          </td>
                          <td style={{ padding: "12px 8px", fontFamily: "var(--font-mono, monospace)" }}>
                            {abbreviateSha(service.git_sha)}
                          </td>
                          <td style={{ padding: "12px 8px" }}>{formatDate(service.build_time)}</td>
                          <td
                            style={{
                              padding: "12px 8px",
                              fontFamily: "var(--font-mono, monospace)",
                              fontSize: "0.85rem",
                            }}
                          >
                            {service.image || "—"}
                          </td>
                          <td style={{ padding: "12px 8px" }}>
                            {service.sources.length > 0 ? service.sources.join(", ") : "—"}
                          </td>
                          <td style={{ padding: "12px 8px", fontSize: "0.85rem", color: "#475569" }}>
                            {service.notes || "—"}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>

                <div
                  style={{
                    border: "1px solid #e2e2e2",
                    borderRadius: "12px",
                    padding: "16px",
                    background: "#f9fafb",
                    color: "#475569",
                  }}
                >
                  <div style={{ display: "grid", gap: "6px" }}>
                    <div>
                      <strong>Manifest Path:</strong>{" "}
                      <span style={{ fontFamily: "var(--font-mono, monospace)" }}>
                        {data.manifest_path ?? "Not provided"}
                      </span>
                    </div>
                    <div>
                      <strong>Generated At:</strong> <span>{formatDate(data.generated_at)}</span>
                    </div>
                    <div>
                      <strong>Environment Overrides:</strong> {data.env_entry_count}
                    </div>
                    <div>
                      <strong>Fallback Entries:</strong> {data.fallback_count}
                    </div>
                  </div>
                </div>
              </>
            ) : (
              <div style={{ color: "#666" }}>No version data available.</div>
            )}
          </div>
        )}
      </div>

      {isLogFullscreen ? (
        <div
          style={{
            position: "fixed",
            inset: 0,
            background: "rgba(15, 23, 42, 0.65)",
            padding: "24px",
            zIndex: 60,
            display: "flex",
            overflow: "hidden",
            boxSizing: "border-box",
          }}
        >
          {logPanel}
        </div>
      ) : (
        logPanel
      )}
    </section>
  );
}
