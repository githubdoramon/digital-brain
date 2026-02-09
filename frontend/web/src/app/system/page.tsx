"use client";

import { useEffect, useRef, useState } from "react";
import { api, getSystemLogs, LogEntry, LogLevel, streamSystemLogs } from "@/lib/api";

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

export default function SystemStatusPage() {
  const [data, setData] = useState<ServiceVersionResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [isBuildExpanded, setIsBuildExpanded] = useState(false);
  const [logEntries, setLogEntries] = useState<LogRow[]>([]);
  const [logError, setLogError] = useState<string | null>(null);
  const [selectedLevels, setSelectedLevels] = useState<LogLevel[]>(LOG_LEVELS);
  const [isLogPaused, setIsLogPaused] = useState(false);
  const [logConnected, setLogConnected] = useState(false);
  const [isLogFullscreen, setIsLogFullscreen] = useState(false);
  const logContainerRef = useRef<HTMLDivElement | null>(null);
  const seenLogKeysRef = useRef<Set<string>>(new Set());

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
    getSystemLogs("all", 15, 200)
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

    const controller = new AbortController();

    setLogError(null);
    setLogConnected(true);

    streamSystemLogs(
      "all",
      (entry) => {
        const key = getLogKey(entry);
        if (seenLogKeysRef.current.has(key)) {
          return;
        }
        seenLogKeysRef.current.add(key);
        setLogEntries((current) => {
          const next = sortLogEntries([...current, toLogRow(entry)]);
          const trimmed = next.length > 200 ? next.slice(-200) : next;
          if (seenLogKeysRef.current.size > 2000) {
            seenLogKeysRef.current = new Set(trimmed.map((row) => getLogKey(row)));
          }
          return trimmed;
        });
      },
      (message) => {
        setLogError(message);
      },
      controller.signal
    )
      .then(() => {
        if (!controller.signal.aborted) {
          setLogConnected(false);
        }
      })
      .catch((err) => {
        if (!controller.signal.aborted) {
          setLogConnected(false);
          setLogError(err instanceof Error ? err.message : "Log stream disconnected. Retrying...");
        }
      });

    return () => {
      controller.abort();
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
        <div style={{ display: "flex", alignItems: "center", gap: "8px", flexWrap: "wrap" }}>
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
          overflowY: "auto",
          fontFamily: "var(--font-mono, monospace)",
          fontSize: "0.8rem",
        }}
      >
        {filteredLogs.length === 0 ? (
          <div style={{ color: "#94a3b8" }}>Waiting for log events...</div>
        ) : (
          filteredLogs.map((entry) => (
            <div key={entry.rowKey} style={{ display: "grid", gridTemplateColumns: "80px 70px 1fr", gap: "8px" }}>
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
              <span>{entry.message}</span>
            </div>
          ))
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
