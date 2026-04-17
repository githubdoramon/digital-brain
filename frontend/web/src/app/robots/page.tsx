"use client";

import { useCallback, useEffect, useState } from "react";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type Robot = {
  robot_id: string;
  name: string;
  description: string | null;
  status: string;
  tags: string[];
  metadata: Record<string, unknown>;
  last_seen_at: string | null;
  registered_at: string;
  updated_at: string;
  modules: Module[];
};

type Module = {
  module_id: string;
  robot_id: string;
  name: string;
  module_type: string;
  status: string;
  capabilities: string[];
  metadata: Record<string, unknown>;
  last_seen_at: string | null;
  registered_at: string;
  updated_at: string;
};

type TelemetryEntry = {
  id: number;
  robot_id: string;
  module_id: string;
  measured_at: string;
  received_at: string;
  payload_type: string;
  payload: Record<string, unknown>;
};

type Command = {
  command_id: string;
  robot_id: string;
  module_id: string;
  command_type: string;
  payload: Record<string, unknown>;
  status: string;
  created_at: string;
  sent_at: string | null;
  acked_at: string | null;
  error: string | null;
  created_by: string | null;
};

type GatewayHealth = {
  mqtt_connected: boolean;
  db_reachable: boolean;
  subscribed_topics: string[];
};

// ---------------------------------------------------------------------------
// API helper (robot-gateway proxy)
// ---------------------------------------------------------------------------

const GW_BASE = "/api/robot-gateway";

async function gw<T = unknown>(
  endpoint: string,
  options: RequestInit = {}
): Promise<T> {
  const headers = new Headers(options.headers);
  if (
    !headers.has("content-type") &&
    !["GET", "HEAD"].includes(options.method ?? "GET")
  ) {
    headers.set("content-type", "application/json");
  }
  const res = await fetch(`${GW_BASE}${endpoint}`, { ...options, headers });
  if (!res.ok) {
    const body = (await res.json().catch(() => undefined)) as
      | { detail?: string }
      | undefined;
    throw new Error(body?.detail || `Request failed: ${res.statusText}`);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const STATUS_COLORS: Record<string, string> = {
  online: "#166534",
  offline: "#666",
  error: "#b91c1c",
  maintenance: "#92400e",
  pending: "#92400e",
  sent: "#2563eb",
  acknowledged: "#166534",
  failed: "#b91c1c",
  expired: "#666",
};

function StatusBadge({ status }: { status: string }) {
  return (
    <span
      style={{
        display: "inline-block",
        padding: "2px 8px",
        borderRadius: "4px",
        fontSize: "0.75rem",
        fontWeight: 600,
        color: "#fff",
        backgroundColor: STATUS_COLORS[status] ?? "#666",
      }}
    >
      {status}
    </span>
  );
}

function formatTime(iso: string | null): string {
  if (!iso) return "-";
  return new Date(iso).toLocaleString();
}

// ---------------------------------------------------------------------------
// Tabs
// ---------------------------------------------------------------------------

type Tab = "robots" | "telemetry" | "commands";

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function RobotsPage() {
  const [tab, setTab] = useState<Tab>("robots");
  const [health, setHealth] = useState<GatewayHealth | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Robots
  const [robots, setRobots] = useState<Robot[]>([]);
  const [selectedRobotId, setSelectedRobotId] = useState<string | null>(null);

  // Telemetry
  const [telemetry, setTelemetry] = useState<TelemetryEntry[]>([]);
  const [telemetryFilter, setTelemetryFilter] = useState("");

  // Commands
  const [commands, setCommands] = useState<Command[]>([]);

  // ----------------------------------------------------------------
  // Fetchers
  // ----------------------------------------------------------------

  const fetchHealth = useCallback(async () => {
    try {
      const h = await gw<GatewayHealth>("/health");
      setHealth(h);
      setError(null);
    } catch (err) {
      setError(
        `Gateway unreachable: ${err instanceof Error ? err.message : String(err)}`
      );
    }
  }, []);

  const fetchRobots = useCallback(async () => {
    try {
      const list = await gw<Robot[]>("/robots");
      setRobots(list);
    } catch {
      // health already shows the error
    }
  }, []);

  const fetchTelemetry = useCallback(
    async (robotId: string) => {
      try {
        const qs = telemetryFilter
          ? `?payload_type=${encodeURIComponent(telemetryFilter)}&limit=50`
          : "?limit=50";
        const entries = await gw<TelemetryEntry[]>(
          `/robots/${robotId}/telemetry${qs}`
        );
        setTelemetry(entries);
      } catch {
        setTelemetry([]);
      }
    },
    [telemetryFilter]
  );

  const fetchCommands = useCallback(async (robotId: string) => {
    try {
      const list = await gw<Command[]>(`/robots/${robotId}/commands?limit=50`);
      setCommands(list);
    } catch {
      setCommands([]);
    }
  }, []);

  // ----------------------------------------------------------------
  // Effects
  // ----------------------------------------------------------------

  useEffect(() => {
    fetchHealth();
    fetchRobots();
  }, [fetchHealth, fetchRobots]);

  useEffect(() => {
    if (selectedRobotId && tab === "telemetry") {
      fetchTelemetry(selectedRobotId);
    }
    if (selectedRobotId && tab === "commands") {
      fetchCommands(selectedRobotId);
    }
  }, [selectedRobotId, tab, fetchTelemetry, fetchCommands]);

  // Auto-select first robot
  useEffect(() => {
    if (!selectedRobotId && robots.length > 0) {
      setSelectedRobotId(robots[0].robot_id);
    }
  }, [robots, selectedRobotId]);

  // ----------------------------------------------------------------
  // Render
  // ----------------------------------------------------------------

  const selectedRobot = robots.find((r) => r.robot_id === selectedRobotId);

  return (
    <div style={{ maxWidth: 960, margin: "0 auto", padding: "24px 16px" }}>
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          marginBottom: 24,
        }}
      >
        <h1 style={{ fontSize: "1.5rem", fontWeight: 700, margin: 0 }}>
          Robot Gateway
        </h1>
        <button
          onClick={() => {
            fetchHealth();
            fetchRobots();
            if (selectedRobotId && tab === "telemetry")
              fetchTelemetry(selectedRobotId);
            if (selectedRobotId && tab === "commands")
              fetchCommands(selectedRobotId);
          }}
          style={{
            padding: "6px 14px",
            border: "1px solid #d0d0d0",
            borderRadius: 6,
            background: "#fff",
            cursor: "pointer",
            fontSize: "0.85rem",
          }}
        >
          Refresh
        </button>
      </div>

      {/* Health bar */}
      {health && (
        <div
          style={{
            display: "flex",
            gap: 16,
            padding: "10px 16px",
            marginBottom: 20,
            background: "#f8f9fa",
            borderRadius: 8,
            fontSize: "0.85rem",
          }}
        >
          <span>
            MQTT:{" "}
            <strong
              style={{ color: health.mqtt_connected ? "#166534" : "#b91c1c" }}
            >
              {health.mqtt_connected ? "connected" : "disconnected"}
            </strong>
          </span>
          <span>
            DB:{" "}
            <strong
              style={{ color: health.db_reachable ? "#166534" : "#b91c1c" }}
            >
              {health.db_reachable ? "reachable" : "unreachable"}
            </strong>
          </span>
          <span style={{ color: "#999" }}>
            Topics: {health.subscribed_topics.length}
          </span>
        </div>
      )}

      {error && (
        <div
          style={{
            padding: "10px 16px",
            marginBottom: 20,
            background: "#fef2f2",
            border: "1px solid #fecaca",
            borderRadius: 8,
            color: "#b91c1c",
            fontSize: "0.85rem",
          }}
        >
          {error}
        </div>
      )}

      {/* Robot selector */}
      {robots.length > 0 && (
        <div
          style={{
            display: "flex",
            gap: 8,
            marginBottom: 16,
            flexWrap: "wrap",
          }}
        >
          {robots.map((r) => (
            <button
              key={r.robot_id}
              onClick={() => setSelectedRobotId(r.robot_id)}
              style={{
                padding: "6px 14px",
                border:
                  r.robot_id === selectedRobotId
                    ? "2px solid #2563eb"
                    : "1px solid #d0d0d0",
                borderRadius: 6,
                background:
                  r.robot_id === selectedRobotId ? "#eff6ff" : "#fff",
                cursor: "pointer",
                fontSize: "0.85rem",
                display: "flex",
                alignItems: "center",
                gap: 6,
              }}
            >
              {r.name} <StatusBadge status={r.status} />
            </button>
          ))}
        </div>
      )}

      {robots.length === 0 && !error && (
        <div
          style={{
            padding: 40,
            textAlign: "center",
            color: "#999",
            fontSize: "0.9rem",
          }}
        >
          No robots registered yet. Register a robot via the gateway API.
        </div>
      )}

      {/* Tabs */}
      {selectedRobot && (
        <>
          <div
            style={{
              display: "flex",
              gap: 0,
              borderBottom: "1px solid #e2e2e2",
              marginBottom: 20,
            }}
          >
            {(["robots", "telemetry", "commands"] as Tab[]).map((t) => (
              <button
                key={t}
                onClick={() => setTab(t)}
                style={{
                  padding: "8px 20px",
                  border: "none",
                  borderBottom:
                    tab === t ? "2px solid #2563eb" : "2px solid transparent",
                  background: "none",
                  cursor: "pointer",
                  fontWeight: tab === t ? 600 : 400,
                  color: tab === t ? "#2563eb" : "#666",
                  fontSize: "0.9rem",
                  textTransform: "capitalize",
                }}
              >
                {t === "robots" ? "Detail" : t}
              </button>
            ))}
          </div>

          {/* ---- Detail tab ---- */}
          {tab === "robots" && (
            <div>
              <div
                style={{
                  padding: 20,
                  border: "1px solid #e2e2e2",
                  borderRadius: 8,
                  marginBottom: 16,
                }}
              >
                <h2
                  style={{ fontSize: "1.1rem", fontWeight: 600, marginTop: 0 }}
                >
                  {selectedRobot.name}
                </h2>
                {selectedRobot.description && (
                  <p style={{ color: "#666", margin: "4px 0 12px" }}>
                    {selectedRobot.description}
                  </p>
                )}
                <div
                  style={{
                    display: "grid",
                    gridTemplateColumns: "120px 1fr",
                    gap: "6px 12px",
                    fontSize: "0.85rem",
                  }}
                >
                  <span style={{ color: "#999" }}>ID</span>
                  <span>{selectedRobot.robot_id}</span>
                  <span style={{ color: "#999" }}>Status</span>
                  <span>
                    <StatusBadge status={selectedRobot.status} />
                  </span>
                  <span style={{ color: "#999" }}>Last seen</span>
                  <span>{formatTime(selectedRobot.last_seen_at)}</span>
                  <span style={{ color: "#999" }}>Registered</span>
                  <span>{formatTime(selectedRobot.registered_at)}</span>
                  {selectedRobot.tags.length > 0 && (
                    <>
                      <span style={{ color: "#999" }}>Tags</span>
                      <span>{selectedRobot.tags.join(", ")}</span>
                    </>
                  )}
                </div>
              </div>

              <h3
                style={{
                  fontSize: "0.95rem",
                  fontWeight: 600,
                  marginBottom: 10,
                }}
              >
                Modules ({selectedRobot.modules.length})
              </h3>

              {selectedRobot.modules.length === 0 ? (
                <p style={{ color: "#999", fontSize: "0.85rem" }}>
                  No modules registered.
                </p>
              ) : (
                <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                  {selectedRobot.modules.map((m) => (
                    <div
                      key={m.module_id}
                      style={{
                        padding: "12px 16px",
                        border: "1px solid #e2e2e2",
                        borderRadius: 8,
                        fontSize: "0.85rem",
                      }}
                    >
                      <div
                        style={{
                          display: "flex",
                          justifyContent: "space-between",
                          alignItems: "center",
                          marginBottom: 6,
                        }}
                      >
                        <strong>{m.name}</strong>
                        <StatusBadge status={m.status} />
                      </div>
                      <div style={{ color: "#666" }}>
                        <span>ID: {m.module_id}</span>
                        <span style={{ marginLeft: 16 }}>
                          Type: {m.module_type}
                        </span>
                        {m.capabilities.length > 0 && (
                          <span style={{ marginLeft: 16 }}>
                            Caps: {m.capabilities.join(", ")}
                          </span>
                        )}
                        <span style={{ marginLeft: 16 }}>
                          Last seen: {formatTime(m.last_seen_at)}
                        </span>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}

          {/* ---- Telemetry tab ---- */}
          {tab === "telemetry" && (
            <div>
              <div
                style={{
                  display: "flex",
                  gap: 8,
                  marginBottom: 12,
                  alignItems: "center",
                }}
              >
                <input
                  type="text"
                  placeholder="Filter by payload_type..."
                  value={telemetryFilter}
                  onChange={(e) => setTelemetryFilter(e.target.value)}
                  style={{
                    padding: "6px 12px",
                    border: "1px solid #d0d0d0",
                    borderRadius: 6,
                    fontSize: "0.85rem",
                    flex: 1,
                    maxWidth: 300,
                  }}
                />
                <button
                  onClick={() =>
                    selectedRobotId && fetchTelemetry(selectedRobotId)
                  }
                  style={{
                    padding: "6px 14px",
                    border: "1px solid #d0d0d0",
                    borderRadius: 6,
                    background: "#fff",
                    cursor: "pointer",
                    fontSize: "0.85rem",
                  }}
                >
                  Search
                </button>
              </div>

              {telemetry.length === 0 ? (
                <p style={{ color: "#999", fontSize: "0.85rem" }}>
                  No telemetry data.
                </p>
              ) : (
                <div
                  style={{
                    overflowX: "auto",
                    border: "1px solid #e2e2e2",
                    borderRadius: 8,
                  }}
                >
                  <table
                    style={{
                      width: "100%",
                      borderCollapse: "collapse",
                      fontSize: "0.8rem",
                    }}
                  >
                    <thead>
                      <tr style={{ background: "#f8f9fa" }}>
                        <th style={thStyle}>Time</th>
                        <th style={thStyle}>Module</th>
                        <th style={thStyle}>Type</th>
                        <th style={thStyle}>Payload</th>
                      </tr>
                    </thead>
                    <tbody>
                      {telemetry.map((t) => (
                        <tr key={t.id} style={{ borderTop: "1px solid #eee" }}>
                          <td style={tdStyle}>{formatTime(t.measured_at)}</td>
                          <td style={tdStyle}>{t.module_id}</td>
                          <td style={tdStyle}>
                            <code>{t.payload_type}</code>
                          </td>
                          <td style={tdStyle}>
                            <code
                              style={{
                                fontSize: "0.75rem",
                                color: "#444",
                                whiteSpace: "pre-wrap",
                                wordBreak: "break-all",
                              }}
                            >
                              {JSON.stringify(t.payload, null, 1)}
                            </code>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )}

          {/* ---- Commands tab ---- */}
          {tab === "commands" && (
            <div>
              {commands.length === 0 ? (
                <p style={{ color: "#999", fontSize: "0.85rem" }}>
                  No commands sent yet.
                </p>
              ) : (
                <div
                  style={{
                    overflowX: "auto",
                    border: "1px solid #e2e2e2",
                    borderRadius: 8,
                  }}
                >
                  <table
                    style={{
                      width: "100%",
                      borderCollapse: "collapse",
                      fontSize: "0.8rem",
                    }}
                  >
                    <thead>
                      <tr style={{ background: "#f8f9fa" }}>
                        <th style={thStyle}>ID</th>
                        <th style={thStyle}>Module</th>
                        <th style={thStyle}>Type</th>
                        <th style={thStyle}>Status</th>
                        <th style={thStyle}>Created</th>
                        <th style={thStyle}>Acked</th>
                      </tr>
                    </thead>
                    <tbody>
                      {commands.map((c) => (
                        <tr
                          key={c.command_id}
                          style={{ borderTop: "1px solid #eee" }}
                        >
                          <td style={tdStyle}>
                            <code>{c.command_id}</code>
                          </td>
                          <td style={tdStyle}>{c.module_id}</td>
                          <td style={tdStyle}>{c.command_type}</td>
                          <td style={tdStyle}>
                            <StatusBadge status={c.status} />
                          </td>
                          <td style={tdStyle}>{formatTime(c.created_at)}</td>
                          <td style={tdStyle}>{formatTime(c.acked_at)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Table styles
// ---------------------------------------------------------------------------

const thStyle: React.CSSProperties = {
  textAlign: "left",
  padding: "8px 12px",
  fontWeight: 600,
  whiteSpace: "nowrap",
};

const tdStyle: React.CSSProperties = {
  padding: "8px 12px",
  verticalAlign: "top",
};
