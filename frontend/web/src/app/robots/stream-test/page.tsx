"use client";

import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";

type Robot = {
  robot_id: string;
  name: string;
  modules: Module[];
};

type Module = {
  module_id: string;
  name: string;
  module_type: string;
  capabilities: string[];
};

type CaptureStream = {
  session_id: string;
  robot_id: string;
  module_id: string;
  created_at: string;
  expires_at: string;
  upstream_connected: boolean;
  viewer_count: number;
  video_enabled: boolean;
  audio_enabled: boolean;
  last_video_frame_at: string | null;
  last_audio_chunk_at: string | null;
  last_upstream_activity_at: string | null;
  requested_by_user_id: string | null;
  last_error: string | null;
  status: string;
  video_meta: Record<string, unknown> | null;
  audio_meta: Record<string, unknown> | null;
  viewer_paths: Record<string, string>;
};

const GW_BASE = "/api/robot-gateway";

async function gw<T>(endpoint: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers);
  if (!headers.has("content-type") && !["GET", "HEAD"].includes(options.method ?? "GET")) {
    headers.set("content-type", "application/json");
  }

  const response = await fetch(`${GW_BASE}${endpoint}`, { ...options, headers });
  if (!response.ok) {
    const body = (await response.json().catch(() => undefined)) as { detail?: string } | undefined;
    throw new Error(body?.detail || `Request failed: ${response.statusText}`);
  }

  if (response.status === 204) {
    return undefined as T;
  }

  return (await response.json()) as T;
}

function formatTime(value: string | null): string {
  if (!value) return "-";
  return new Date(value).toLocaleString();
}

export default function RobotStreamTestPage() {
  const [robots, setRobots] = useState<Robot[]>([]);
  const [robotId, setRobotId] = useState("");
  const [moduleId, setModuleId] = useState("");
  const [session, setSession] = useState<CaptureStream | null>(null);
  const [videoEnabled, setVideoEnabled] = useState(true);
  const [audioEnabled, setAudioEnabled] = useState(true);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [audioStatus, setAudioStatus] = useState("idle");
  const sessionId = session?.session_id ?? null;
  const statusPath = session?.viewer_paths.status ?? null;
  const audioContextRef = useRef<AudioContext | null>(null);
  const audioSocketRef = useRef<WebSocket | null>(null);
  const nextAudioTimeRef = useRef(0);

  useEffect(() => {
    let cancelled = false;
    gw<Robot[]>("/robots")
      .then((items) => {
        if (cancelled) return;
        setRobots(items);
        if (!robotId && items[0]) {
          setRobotId(items[0].robot_id);
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : String(err));
        }
      });

    return () => {
      cancelled = true;
    };
  }, [robotId]);

  const selectedRobot = useMemo(
    () => robots.find((robot) => robot.robot_id === robotId) ?? null,
    [robotId, robots]
  );

  useEffect(() => {
    if (selectedRobot && !selectedRobot.modules.some((module) => module.module_id === moduleId)) {
      setModuleId(selectedRobot.modules[0]?.module_id ?? "");
    }
  }, [moduleId, selectedRobot]);

  useEffect(() => {
    if (!statusPath) return;
    const interval = window.setInterval(async () => {
      try {
        const next = await gw<CaptureStream>(statusPath);
        setSession(next);
      } catch {
        setSession(null);
      }
    }, 2000);

    return () => window.clearInterval(interval);
  }, [sessionId, statusPath]);

  const mjpgUrl = session
    ? `${GW_BASE}${session.viewer_paths.camera_mjpg}?t=${encodeURIComponent(session.last_video_frame_at ?? session.created_at)}`
    : null;

  useEffect(() => {
    if (!session?.audio_enabled || !session.viewer_paths.audio_pcm_ws_public) {
      setAudioStatus("idle");
      audioSocketRef.current?.close();
      audioSocketRef.current = null;
      return;
    }

    const AudioContextCtor = window.AudioContext || (window as typeof window & { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
    if (!AudioContextCtor) {
      setAudioStatus("unsupported");
      return;
    }

    const context = audioContextRef.current ?? new AudioContextCtor({ sampleRate: 16000 });
    audioContextRef.current = context;
    void context.resume();

    nextAudioTimeRef.current = Math.max(context.currentTime + 0.1, nextAudioTimeRef.current || 0);
    const socket = new WebSocket(session.viewer_paths.audio_pcm_ws_public);
    socket.binaryType = "arraybuffer";
    audioSocketRef.current = socket;
    setAudioStatus("connecting");

    const sampleRate = Number(session.audio_meta?.sample_rate_hz ?? 16000) || 16000;
    const channelCount = Number(session.audio_meta?.channels ?? 1) || 1;

    socket.onopen = () => setAudioStatus("live");
    socket.onclose = () => {
      if (audioSocketRef.current === socket) {
        audioSocketRef.current = null;
        setAudioStatus("disconnected");
      }
    };
    socket.onerror = () => setAudioStatus("error");
    socket.onmessage = (event) => {
      if (!(event.data instanceof ArrayBuffer)) return;

      const pcm = new Int16Array(event.data);
      if (!pcm.length) return;

      const frameCount = Math.floor(pcm.length / channelCount);
      const audioBuffer = context.createBuffer(channelCount, frameCount, sampleRate);
      for (let channel = 0; channel < channelCount; channel += 1) {
        const channelData = audioBuffer.getChannelData(channel);
        for (let frame = 0; frame < frameCount; frame += 1) {
          const sample = pcm[frame * channelCount + channel] ?? 0;
          channelData[frame] = sample / 32768;
        }
      }

      const source = context.createBufferSource();
      source.buffer = audioBuffer;
      source.connect(context.destination);
      const startAt = Math.max(context.currentTime + 0.02, nextAudioTimeRef.current);
      source.start(startAt);
      nextAudioTimeRef.current = startAt + audioBuffer.duration;
    };

    return () => {
      if (audioSocketRef.current === socket) {
        audioSocketRef.current = null;
      }
      socket.close();
    };
  }, [
    session?.audio_enabled,
    session?.viewer_paths.audio_pcm_ws_public,
    session?.audio_meta?.sample_rate_hz,
    session?.audio_meta?.channels,
  ]);

  useEffect(() => {
    return () => {
      audioSocketRef.current?.close();
      audioSocketRef.current = null;
      audioContextRef.current?.close().catch(() => undefined);
      audioContextRef.current = null;
    };
  }, []);

  async function startStream() {
    if (!robotId || !moduleId) {
      setError("Select a robot and module first.");
      return;
    }

    setLoading(true);
    setError(null);
    try {
      const next = await gw<CaptureStream>("/api/capture/streams", {
        method: "POST",
        body: JSON.stringify({ robot_id: robotId, module_id: moduleId, video: videoEnabled, audio: audioEnabled }),
      });
      setSession(next);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }

  async function stopStream() {
    if (!session) return;
    setLoading(true);
    try {
      await gw<void>(session.viewer_paths.status.replace(/\/status$/, ""), { method: "DELETE" });
      setSession(null);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }

  return (
    <div style={{ maxWidth: 1120, margin: "0 auto", padding: "24px 16px 40px" }}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 16, alignItems: "center", marginBottom: 20 }}>
        <div>
          <div style={{ fontSize: 12, letterSpacing: "0.08em", textTransform: "uppercase", color: "#64748b", marginBottom: 8 }}>
            Robot Capture Relay
          </div>
          <h1 style={{ fontSize: "1.8rem", margin: 0 }}>Stream Test</h1>
        </div>
        <Link href="/robots" style={{ color: "#0f766e", textDecoration: "none", fontWeight: 600 }}>
          Back to robots
        </Link>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "minmax(280px, 360px) 1fr", gap: 20 }}>
        <section style={panelStyle}>
          <h2 style={sectionTitleStyle}>Session</h2>

          <label style={labelStyle}>
            Robot
            <select value={robotId} onChange={(event) => setRobotId(event.target.value)} style={inputStyle}>
              {robots.map((robot) => (
                <option key={robot.robot_id} value={robot.robot_id}>
                  {robot.name}
                </option>
              ))}
            </select>
          </label>

          <label style={labelStyle}>
            Module
            <select value={moduleId} onChange={(event) => setModuleId(event.target.value)} style={inputStyle}>
              {(selectedRobot?.modules ?? []).map((module) => (
                <option key={module.module_id} value={module.module_id}>
                  {module.name} ({module.module_type})
                </option>
              ))}
            </select>
          </label>

          <label style={toggleStyle}>
            <input type="checkbox" checked={videoEnabled} onChange={(event) => setVideoEnabled(event.target.checked)} />
            Video
          </label>
          <label style={toggleStyle}>
            <input type="checkbox" checked={audioEnabled} onChange={(event) => setAudioEnabled(event.target.checked)} />
            Audio
          </label>

          <div style={{ display: "flex", gap: 10, marginTop: 12 }}>
            <button onClick={startStream} disabled={loading} style={primaryButtonStyle}>
              {loading ? "Working..." : "Start or reuse stream"}
            </button>
            <button onClick={stopStream} disabled={loading || !session} style={secondaryButtonStyle}>
              Stop
            </button>
          </div>

          {error && <div style={errorStyle}>{error}</div>}

          <div style={{ marginTop: 18, display: "grid", gap: 8, fontSize: 14 }}>
            <div><strong>Status:</strong> {session?.status ?? "idle"}</div>
            <div><strong>Session:</strong> {session?.session_id ?? "-"}</div>
            <div><strong>Upstream:</strong> {session?.upstream_connected ? "connected" : "waiting"}</div>
            <div><strong>Viewers:</strong> {session?.viewer_count ?? 0}</div>
            <div><strong>Last frame:</strong> {formatTime(session?.last_video_frame_at ?? null)}</div>
            <div><strong>Last audio:</strong> {formatTime(session?.last_audio_chunk_at ?? null)}</div>
            <div><strong>Audio:</strong> {audioStatus}</div>
            <div><strong>Expires:</strong> {formatTime(session?.expires_at ?? null)}</div>
          </div>

          {session?.video_meta && (
            <pre style={metaStyle}>{JSON.stringify(session.video_meta, null, 2)}</pre>
          )}
        </section>

        <section style={{ ...panelStyle, minHeight: 520, display: "flex", flexDirection: "column" }}>
          <h2 style={sectionTitleStyle}>Viewer</h2>
          <div
            style={{
              flex: 1,
              borderRadius: 16,
              overflow: "hidden",
              background: "linear-gradient(135deg, #0f172a, #1f2937)",
              border: "1px solid rgba(148, 163, 184, 0.22)",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              position: "relative",
            }}
          >
            {mjpgUrl ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img
                src={mjpgUrl}
                alt="Robot MJPEG stream"
                style={{ width: "100%", height: "100%", objectFit: "contain", background: "#020617" }}
              />
            ) : (
              <div style={{ color: "#cbd5e1", textAlign: "center", padding: 24 }}>
                Start a relay session to wait for the capture module.
              </div>
            )}
            <div
              style={{
                position: "absolute",
                left: 14,
                top: 14,
                background: "rgba(15, 23, 42, 0.76)",
                color: "#f8fafc",
                borderRadius: 999,
                padding: "6px 10px",
                fontSize: 12,
              }}
            >
              {session?.upstream_connected ? "LIVE" : "WAITING"}
            </div>
          </div>
          <p style={{ margin: "12px 0 0", color: "#475569", fontSize: 13 }}>
            This page exercises relay create/status/stop, renders MJPEG through the proxy, and opens the direct audio WebSocket with a short-lived viewer token.
          </p>
        </section>
      </div>
    </div>
  );
}

const panelStyle: React.CSSProperties = {
  background: "#ffffff",
  border: "1px solid #dbe4ea",
  borderRadius: 18,
  padding: 18,
  boxShadow: "0 14px 40px rgba(15, 23, 42, 0.06)",
};

const sectionTitleStyle: React.CSSProperties = {
  margin: "0 0 16px",
  fontSize: "1rem",
};

const labelStyle: React.CSSProperties = {
  display: "grid",
  gap: 6,
  color: "#334155",
  fontSize: 14,
  marginBottom: 12,
};

const inputStyle: React.CSSProperties = {
  width: "100%",
  padding: "10px 12px",
  borderRadius: 10,
  border: "1px solid #cbd5e1",
  background: "#f8fafc",
};

const toggleStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 8,
  color: "#334155",
  fontSize: 14,
  marginBottom: 8,
};

const primaryButtonStyle: React.CSSProperties = {
  border: "none",
  borderRadius: 999,
  padding: "10px 16px",
  background: "linear-gradient(135deg, #0f766e, #0f172a)",
  color: "#fff",
  cursor: "pointer",
  fontWeight: 700,
};

const secondaryButtonStyle: React.CSSProperties = {
  border: "1px solid #cbd5e1",
  borderRadius: 999,
  padding: "10px 16px",
  background: "#fff",
  cursor: "pointer",
  fontWeight: 600,
};

const errorStyle: React.CSSProperties = {
  marginTop: 14,
  padding: "10px 12px",
  borderRadius: 12,
  background: "#fff1f2",
  color: "#be123c",
  fontSize: 14,
};

const metaStyle: React.CSSProperties = {
  marginTop: 16,
  padding: 12,
  borderRadius: 12,
  background: "#f8fafc",
  color: "#334155",
  overflowX: "auto",
  fontSize: 12,
};
