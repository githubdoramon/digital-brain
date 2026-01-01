"use client";

import { useState } from "react";

export type EventProposal = {
  title?: string | null;
  start_date: string;
  end_date?: string | null;
  summary?: string | null;
  people?: string[];
  tags?: string[];
  types?: string[];
  place?: string | null;
  place_id?: string | null;
  confidence?: number | null;
  missing?: string[];
  raw?: Record<string, unknown>;
};

type Props = {
  proposal: EventProposal;
  onInsert?: (proposal: EventProposal) => Promise<void>;
};

export function EventProposalCard({ proposal, onInsert }: Props) {
  const [status, setStatus] = useState<"idle" | "saving" | "saved" | "error">("idle");
  const [error, setError] = useState<string | null>(null);

  const startDate = proposal.start_date ? new Date(proposal.start_date) : null;
  const endDate = proposal.end_date ? new Date(proposal.end_date) : null;

  async function handleInsert() {
    if (!onInsert) return;
    setStatus("saving");
    setError(null);
    try {
      await onInsert(proposal);
      setStatus("saved");
    } catch (err) {
      setStatus("error");
      setError(err instanceof Error ? err.message : "Failed to insert event");
    }
  }

  return (
    <div
      style={{
        border: "1px solid #d9e2ec",
        background: "#f8fafc",
        borderRadius: "10px",
        padding: "12px 14px",
        display: "grid",
        gap: "8px",
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <div style={{ fontWeight: 700, color: "#0f172a" }}>Event proposal</div>
        <span
          style={{
            fontSize: "0.8rem",
            color: proposal.confidence != null ? "#0b6bcb" : "#475569",
          }}
        >
          {proposal.confidence != null ? `Confidence: ${(proposal.confidence * 100).toFixed(0)}%` : "Draft"}
        </span>
      </div>
      <div style={{ display: "grid", gap: "6px", color: "#1f2937", fontSize: "0.9rem" }}>
        {proposal.title && <div style={{ fontWeight: 600 }}>{proposal.title}</div>}
        {startDate && (
          <div>
            <strong>Start:</strong>{" "}
            {startDate.toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}
          </div>
        )}
        {endDate && (
          <div>
            <strong>End:</strong>{" "}
            {endDate.toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}
          </div>
        )}
        {proposal.place && (
          <div>
            <strong>Place:</strong> {proposal.place}
          </div>
        )}
        {proposal.people && proposal.people.length > 0 && (
          <div>
            <strong>People:</strong> {proposal.people.join(", ")}
          </div>
        )}
        {proposal.tags && proposal.tags.length > 0 && (
          <div>
            <strong>Tags:</strong> {proposal.tags.join(", ")}
          </div>
        )}
        {proposal.summary && <div>{proposal.summary}</div>}
        {proposal.missing && proposal.missing.length > 0 && (
          <div style={{ color: "#b45309" }}>
            <strong>Missing:</strong> {proposal.missing.join(", ")}
          </div>
        )}
      </div>
      <div style={{ display: "flex", gap: "8px", alignItems: "center" }}>
        <button
          onClick={handleInsert}
          disabled={status === "saving"}
          style={{
            background: "#0b6bcb",
            color: "#fff",
            border: "none",
            borderRadius: "8px",
            padding: "8px 12px",
            fontWeight: 600,
            cursor: status === "saving" ? "not-allowed" : "pointer",
            opacity: status === "saving" ? 0.7 : 1,
          }}
        >
          {status === "saving" ? "Inserting..." : status === "saved" ? "Inserted" : "Insert event"}
        </button>
        {error && <span style={{ color: "#dc2626", fontSize: "0.85rem" }}>{error}</span>}
      </div>
    </div>
  );
}

export default EventProposalCard;

