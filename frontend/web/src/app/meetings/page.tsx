'use client';

import { FormEvent, useMemo, useState } from "react";

type Status =
  | { kind: "idle" }
  | { kind: "success"; message: string }
  | { kind: "error"; message: string };

type EventPayload = {
  id: string;
  ts: string;
  what_text: string;
  people: string;
  tags: string;
};

const API_BASE = process.env.BACKEND_API_BASE ?? "http://localhost:8000";

function toLocalDateTimeInput(date: Date) {
  const pad = (value: number) => value.toString().padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(
    date.getDate()
  )}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function parseList(value: string) {
  return value
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

export default function MeetingsPage() {
  const [formState, setFormState] = useState<EventPayload>(() => ({
    id: `meeting-${Date.now()}`,
    ts: toLocalDateTimeInput(new Date()),
    what_text: "",
    people: "",
    tags: "meeting",
  }));
  const [status, setStatus] = useState<Status>({ kind: "idle" });
  const [isSubmitting, setSubmitting] = useState(false);

  const requestPreview = useMemo(() => {
    const body = {
      id: formState.id,
      ts: new Date(formState.ts).toISOString(),
      what_text: formState.what_text,
      people: parseList(formState.people),
      tags: parseList(formState.tags),
      types: ["meeting"],
    };
    return JSON.stringify(body, null, 2);
  }, [formState]);

  const handleChange = (field: keyof EventPayload) =>
    (event: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => {
      setFormState((prev) => ({ ...prev, [field]: event.target.value }));
    };

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    setStatus({ kind: "idle" });

    const payload = {
      id: formState.id.trim() || `meeting-${Date.now()}`,
      ts: new Date(formState.ts).toISOString(),
      what_text: formState.what_text,
      people: parseList(formState.people),
      tags: parseList(formState.tags),
      types: ["meeting"],
    };

    try {
      const response = await fetch(`${API_BASE}/ingest/event`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify(payload),
      });

      if (!response.ok) {
        const error = await response.json().catch(() => ({}));
        throw new Error(error.detail || "Failed to ingest meeting");
      }

      setStatus({
        kind: "success",
        message: `Meeting ${payload.id} imported successfully`,
      });
      setFormState((prev) => ({
        ...prev,
        id: `meeting-${Date.now()}`,
        what_text: "",
      }));
    } catch (error) {
      const message =
        error instanceof Error ? error.message : "Unexpected error occurred";
      setStatus({ kind: "error", message });
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <section style={{ display: "grid", gap: "24px" }}>
      <div style={{ display: "grid", gap: "8px" }}>
        <h1 style={{ fontSize: "2rem", fontWeight: 600 }}>Meetings</h1>
        <p style={{ color: "#555" }}>
          Import meeting summaries into your personal memory database. The
          entries are sent directly to the backend ingest endpoint so they
          become searchable immediately.
        </p>
      </div>

      <form
        onSubmit={handleSubmit}
        style={{
          display: "grid",
          gap: "20px",
          border: "1px solid #e2e2e2",
          borderRadius: "12px",
          padding: "24px",
          background: "#fff",
          boxShadow: "0 4px 12px rgba(15, 23, 42, 0.04)",
        }}
      >
        <div style={{ display: "grid", gap: "12px" }}>
          <label style={{ display: "grid", gap: "6px" }}>
            <span style={{ fontWeight: 600 }}>Meeting ID</span>
            <input
              type="text"
              required
              value={formState.id}
              onChange={handleChange("id")}
              style={{
                border: "1px solid #d0d0d0",
                borderRadius: "8px",
                padding: "10px 12px",
              }}
            />
          </label>

          <label style={{ display: "grid", gap: "6px" }}>
            <span style={{ fontWeight: 600 }}>Date &amp; Time</span>
            <input
              type="datetime-local"
              required
              value={formState.ts}
              onChange={handleChange("ts")}
              style={{
                border: "1px solid #d0d0d0",
                borderRadius: "8px",
                padding: "10px 12px",
              }}
            />
          </label>

          <label style={{ display: "grid", gap: "6px" }}>
            <span style={{ fontWeight: 600 }}>Summary</span>
            <textarea
              required
              value={formState.what_text}
              onChange={handleChange("what_text")}
              rows={6}
              placeholder="Describe the meeting outcomes, decisions, and key notes."
              style={{
                border: "1px solid #d0d0d0",
                borderRadius: "8px",
                padding: "10px 12px",
                resize: "vertical",
              }}
            />
          </label>

          <label style={{ display: "grid", gap: "6px" }}>
            <span style={{ fontWeight: 600 }}>Participants</span>
            <input
              type="text"
              value={formState.people}
              onChange={handleChange("people")}
              placeholder="Comma-separated IDs (e.g. contact:alice#001, contact:bob#002)"
              style={{
                border: "1px solid #d0d0d0",
                borderRadius: "8px",
                padding: "10px 12px",
              }}
            />
          </label>

          <label style={{ display: "grid", gap: "6px" }}>
            <span style={{ fontWeight: 600 }}>Tags</span>
            <input
              type="text"
              value={formState.tags}
              onChange={handleChange("tags")}
              placeholder="Comma-separated tags"
              style={{
                border: "1px solid #d0d0d0",
                borderRadius: "8px",
                padding: "10px 12px",
              }}
            />
          </label>
        </div>

        {status.kind === "error" && (
          <div
            role="alert"
            style={{
              background: "#fee2e2",
              border: "1px solid #fca5a5",
              color: "#991b1b",
              borderRadius: "8px",
              padding: "12px 16px",
            }}
          >
            {status.message}
          </div>
        )}
        {status.kind === "success" && (
          <div
            role="status"
            style={{
              background: "#dcfce7",
              border: "1px solid #86efac",
              color: "#166534",
              borderRadius: "8px",
              padding: "12px 16px",
            }}
          >
            {status.message}
          </div>
        )}

        <div style={{ display: "flex", gap: "12px" }}>
          <button
            type="submit"
            disabled={isSubmitting}
            style={{
              background: "#0b6bcb",
              color: "#fff",
              border: "none",
              borderRadius: "8px",
              padding: "10px 18px",
              fontWeight: 600,
              cursor: "pointer",
              opacity: isSubmitting ? 0.7 : 1,
            }}
          >
            {isSubmitting ? "Importing…" : "Import meeting"}
          </button>
          <button
            type="button"
            onClick={() =>
              setFormState({
                id: `meeting-${Date.now()}`,
                ts: toLocalDateTimeInput(new Date()),
                what_text: "",
                people: "",
                tags: "meeting",
              })
            }
            disabled={isSubmitting}
            style={{
              background: "transparent",
              color: "#444",
              border: "1px solid #cbd5f5",
              borderRadius: "8px",
              padding: "10px 18px",
              cursor: "pointer",
            }}
          >
            Reset
          </button>
        </div>
      </form>

      <div
        style={{
          border: "1px solid #e2e2e2",
          borderRadius: "12px",
          padding: "24px",
          background: "#fff",
          display: "grid",
          gap: "12px",
        }}
      >
        <h2 style={{ fontSize: "1.1rem", fontWeight: 600 }}>Request preview</h2>
        <p style={{ color: "#555" }}>
          The JSON payload sent to <code>{`${API_BASE}/ingest/event`}</code> looks
          like this:
        </p>
        <pre
          style={{
            background: "#0f172a",
            color: "#e2e8f0",
            padding: "16px",
            borderRadius: "10px",
            overflow: "auto",
            fontSize: "0.85rem",
          }}
        >
          {requestPreview}
        </pre>
      </div>
    </section>
  );
}
