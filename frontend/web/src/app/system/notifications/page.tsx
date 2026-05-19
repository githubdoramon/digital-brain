"use client";

import Link from "next/link";
import { type FormEvent, useEffect, useMemo, useState } from "react";

import { api } from "@/lib/api";

type UserDevice = {
  deviceId: string;
  userEmail: string;
  expoPushToken: string;
  platform: string;
  deviceName?: string | null;
  appVersion?: string | null;
  osVersion?: string | null;
  createdAt?: string | null;
  updatedAt?: string | null;
  lastSeenAt?: string | null;
};

type UserDeviceListResponse = {
  devices: UserDevice[];
};

type PushTicket = {
  token?: string;
  status?: string;
  id?: string;
  message?: string;
  details?: Record<string, unknown>;
};

type PushTestResponse = {
  device: UserDevice;
  sent: number;
  success: number;
  errors: string[];
  tickets: PushTicket[];
};

function formatDate(value?: string | null): string {
  if (!value) return "-";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString();
}

function summarizeDevice(device: UserDevice): string {
  const parts = [device.deviceName, device.platform, device.appVersion].filter(Boolean);
  return parts.length ? parts.join(" - ") : device.deviceId;
}

function maskToken(token: string): string {
  if (token.length <= 20) return token;
  return `${token.slice(0, 12)}...${token.slice(-8)}`;
}

export default function NotificationTestPage() {
  const [devices, setDevices] = useState<UserDevice[]>([]);
  const [deviceId, setDeviceId] = useState("");
  const [title, setTitle] = useState("Digital Brain test notification");
  const [message, setMessage] = useState("If this arrives, Expo push delivery is working for this device.");
  const [isLoading, setIsLoading] = useState(true);
  const [isSending, setIsSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<PushTestResponse | null>(null);

  useEffect(() => {
    let cancelled = false;

    (async () => {
      try {
        const response = await api.get<UserDeviceListResponse>("/system/notifications/devices");
        if (cancelled) return;
        const nextDevices = Array.isArray(response.devices) ? response.devices : [];
        setDevices(nextDevices);
        setDeviceId((current) => current || nextDevices[0]?.deviceId || "");
      } catch (err) {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : "Failed to load registered devices.");
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

  const selectedDevice = useMemo(
    () => devices.find((device) => device.deviceId === deviceId) ?? null,
    [deviceId, devices]
  );

  const handleSend = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setError(null);
    setResult(null);

    if (!deviceId) {
      setError("Choose a device first.");
      return;
    }

    setIsSending(true);
    try {
      const response = await api.post<PushTestResponse>("/system/notifications/test", {
        deviceId,
        title,
        message,
      });
      setResult(response);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to send test notification.");
    } finally {
      setIsSending(false);
    }
  };

  return (
    <section style={{ display: "grid", gap: "24px", maxWidth: "980px" }}>
      <div style={{ display: "grid", gap: "8px" }}>
        <Link href="/system" style={{ color: "#0b6bcb", textDecoration: "underline", width: "fit-content" }}>
          Back to system status
        </Link>
        <h1 style={{ fontSize: "1.75rem", fontWeight: 600, margin: 0 }}>Push Notification Test</h1>
        <p style={{ color: "#555", margin: 0 }}>
          Pick one registered device for your account and send a direct Expo push test without waiting for a real workflow.
        </p>
      </div>

      <form
        onSubmit={(event) => {
          void handleSend(event);
        }}
        style={{
          border: "1px solid #e2e8f0",
          borderRadius: "16px",
          padding: "20px",
          background: "#fff",
          display: "grid",
          gap: "16px",
        }}
      >
        <label style={{ display: "grid", gap: "8px" }}>
          <span style={{ fontWeight: 600 }}>Target device</span>
          <select
            value={deviceId}
            onChange={(event) => setDeviceId(event.target.value)}
            disabled={isLoading || isSending || devices.length === 0}
            style={{ border: "1px solid #cbd5e1", borderRadius: "10px", padding: "10px 12px" }}
          >
            {devices.length === 0 ? <option value="">No registered devices</option> : null}
            {devices.map((device) => (
              <option key={device.deviceId} value={device.deviceId}>
                {summarizeDevice(device)}
              </option>
            ))}
          </select>
        </label>

        <label style={{ display: "grid", gap: "8px" }}>
          <span style={{ fontWeight: 600 }}>Title</span>
          <input
            value={title}
            onChange={(event) => setTitle(event.target.value)}
            disabled={isSending}
            style={{ border: "1px solid #cbd5e1", borderRadius: "10px", padding: "10px 12px" }}
          />
        </label>

        <label style={{ display: "grid", gap: "8px" }}>
          <span style={{ fontWeight: 600 }}>Message</span>
          <textarea
            value={message}
            onChange={(event) => setMessage(event.target.value)}
            disabled={isSending}
            rows={4}
            style={{ border: "1px solid #cbd5e1", borderRadius: "10px", padding: "10px 12px", resize: "vertical" }}
          />
        </label>

        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: "12px", flexWrap: "wrap" }}>
          <div style={{ color: "#64748b", fontSize: "0.9rem" }}>
            {selectedDevice ? `Expo token: ${maskToken(selectedDevice.expoPushToken)}` : "Select a device to inspect its token."}
          </div>
          <button
            type="submit"
            disabled={isLoading || isSending || !deviceId || !title.trim() || !message.trim()}
            style={{
              border: "none",
              borderRadius: "999px",
              padding: "11px 16px",
              background: isLoading || isSending || !deviceId ? "#94a3b8" : "#0f766e",
              color: "#fff",
              cursor: isLoading || isSending || !deviceId ? "not-allowed" : "pointer",
              fontWeight: 600,
            }}
          >
            {isSending ? "Sending..." : "Send test push"}
          </button>
        </div>
      </form>

      {error ? (
        <div style={{ border: "1px solid #fca5a5", background: "#fef2f2", color: "#b91c1c", borderRadius: "12px", padding: "14px 16px" }}>
          {error}
        </div>
      ) : null}

      <div style={{ display: "grid", gap: "16px", gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))" }}>
        <div style={{ border: "1px solid #e2e8f0", borderRadius: "16px", padding: "18px", background: "#fff", display: "grid", gap: "10px" }}>
          <h2 style={{ margin: 0, fontSize: "1.05rem", fontWeight: 600 }}>Registered devices</h2>
          {isLoading ? <p style={{ margin: 0, color: "#64748b" }}>Loading devices...</p> : null}
          {!isLoading && devices.length === 0 ? (
            <p style={{ margin: 0, color: "#64748b" }}>
              No device is registered yet. Enable push on the mobile notification settings screen first.
            </p>
          ) : null}
          {devices.map((device) => (
            <div key={device.deviceId} style={{ borderTop: "1px solid #f1f5f9", paddingTop: "10px", display: "grid", gap: "4px" }}>
              <strong>{summarizeDevice(device)}</strong>
              <span style={{ color: "#475569", fontSize: "0.92rem" }}>Last seen: {formatDate(device.lastSeenAt || device.updatedAt)}</span>
              <span style={{ color: "#475569", fontSize: "0.92rem" }}>Token: {maskToken(device.expoPushToken)}</span>
            </div>
          ))}
        </div>

        <div style={{ border: "1px solid #e2e8f0", borderRadius: "16px", padding: "18px", background: "#fff", display: "grid", gap: "10px" }}>
          <h2 style={{ margin: 0, fontSize: "1.05rem", fontWeight: 600 }}>Latest result</h2>
          {!result ? <p style={{ margin: 0, color: "#64748b" }}>No test has been sent in this session.</p> : null}
          {result ? (
            <>
              <div style={{ color: result.success > 0 ? "#0f766e" : "#b45309", fontWeight: 600 }}>
                Sent {result.sent} push message{result.sent === 1 ? "" : "s"}; Expo accepted {result.success}.
              </div>
              <div style={{ color: "#475569", fontSize: "0.92rem" }}>
                Device: {summarizeDevice(result.device)}
              </div>
              {result.errors.length > 0 ? (
                <div style={{ color: "#b91c1c", fontSize: "0.92rem" }}>
                  {result.errors.join(" | ")}
                </div>
              ) : null}
              {result.tickets.map((ticket, index) => (
                <pre
                  key={`${ticket.id || ticket.token || index}`}
                  style={{
                    margin: 0,
                    background: "#0f172a",
                    color: "#e2e8f0",
                    padding: "12px",
                    borderRadius: "10px",
                    overflowX: "auto",
                    fontSize: "0.8rem",
                  }}
                >
                  {JSON.stringify(ticket, null, 2)}
                </pre>
              ))}
            </>
          ) : null}
        </div>
      </div>
    </section>
  );
}
