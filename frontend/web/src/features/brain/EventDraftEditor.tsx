"use client";

import { FormEvent, useState } from "react";
import type { EventDraft, EventDraftModifications } from "./eventDraft";
import { buildEventDraftModifications } from "./eventDraft";

function parseList(value: string): string[] {
  return value
    .split(",")
    .map((entry) => entry.trim())
    .filter(Boolean);
}

function listText(value: string[]): string {
  return value.join(", ");
}

function fieldStyle() {
  return {
    border: "1px solid #cbd5e1",
    borderRadius: "8px",
    color: "#0f172a",
    font: "inherit",
    padding: "9px 10px",
    width: "100%",
  };
}

export function EventDraftEditor({
  baseDraft,
  initialDraft,
  onCancel,
  onSave,
}: {
  baseDraft: EventDraft;
  initialDraft: EventDraft;
  onCancel: () => void;
  onSave: (modifications: EventDraftModifications, nextDraft: EventDraft) => void;
}) {
  const [title, setTitle] = useState(initialDraft.title);
  const [summary, setSummary] = useState(initialDraft.summary);
  const [when, setWhen] = useState(initialDraft.when);
  const [endWhen, setEndWhen] = useState(initialDraft.endWhen);
  const [where, setWhere] = useState(initialDraft.where);
  const [tags, setTags] = useState(listText(initialDraft.tags));
  const [types, setTypes] = useState(listText(initialDraft.types));

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const nextDraft: EventDraft = {
      title,
      summary,
      when,
      endWhen,
      where,
      participants: initialDraft.participants,
      tags: parseList(tags),
      types: parseList(types),
    };
    onSave(buildEventDraftModifications(baseDraft, nextDraft), nextDraft);
  };

  return (
    <form
      onSubmit={submit}
      style={{
        background: "#ffffff",
        border: "1px solid #bfdbfe",
        borderRadius: "8px",
        boxShadow: "0 8px 24px rgba(15, 23, 42, 0.08)",
        display: "grid",
        gap: "12px",
        maxWidth: "80%",
        padding: "14px",
        width: "min(680px, 100%)",
      }}
    >
      <div style={{ display: "grid", gap: "4px" }}>
        <h3 style={{ color: "#0f172a", fontSize: "1rem", margin: 0 }}>Edit event fields</h3>
        <p style={{ color: "#64748b", fontSize: "0.86rem", lineHeight: 1.4, margin: 0 }}>
          Save changes here, then use the preview card button to create or update the event.
        </p>
      </div>

      <label style={{ color: "#334155", display: "grid", gap: "5px", fontSize: "0.86rem" }}>
        Title
        <input value={title} onChange={(event) => setTitle(event.target.value)} style={fieldStyle()} />
      </label>

      <label style={{ color: "#334155", display: "grid", gap: "5px", fontSize: "0.86rem" }}>
        Summary
        <textarea
          value={summary}
          onChange={(event) => setSummary(event.target.value)}
          rows={3}
          style={{ ...fieldStyle(), resize: "vertical" }}
        />
      </label>

      <div style={{ display: "grid", gap: "10px", gridTemplateColumns: "repeat(2, minmax(0, 1fr))" }}>
        <label style={{ color: "#334155", display: "grid", gap: "5px", fontSize: "0.86rem" }}>
          Starts
          <input value={when} onChange={(event) => setWhen(event.target.value)} style={fieldStyle()} />
        </label>
        <label style={{ color: "#334155", display: "grid", gap: "5px", fontSize: "0.86rem" }}>
          Ends
          <input value={endWhen} onChange={(event) => setEndWhen(event.target.value)} style={fieldStyle()} />
        </label>
      </div>

      <label style={{ color: "#334155", display: "grid", gap: "5px", fontSize: "0.86rem" }}>
        Where
        <input value={where} onChange={(event) => setWhere(event.target.value)} style={fieldStyle()} />
      </label>

      <div style={{ display: "grid", gap: "10px", gridTemplateColumns: "repeat(2, minmax(0, 1fr))" }}>
        <label style={{ color: "#334155", display: "grid", gap: "5px", fontSize: "0.86rem" }}>
          Tags
          <input value={tags} onChange={(event) => setTags(event.target.value)} style={fieldStyle()} />
        </label>
        <label style={{ color: "#334155", display: "grid", gap: "5px", fontSize: "0.86rem" }}>
          Types
          <input value={types} onChange={(event) => setTypes(event.target.value)} style={fieldStyle()} />
        </label>
      </div>

      <div style={{ display: "flex", gap: "8px", justifyContent: "flex-end" }}>
        <button
          type="button"
          onClick={onCancel}
          style={{
            background: "#ffffff",
            border: "1px solid #cbd5e1",
            borderRadius: "8px",
            color: "#334155",
            cursor: "pointer",
            fontWeight: 650,
            padding: "8px 12px",
          }}
        >
          Close
        </button>
        <button
          type="submit"
          style={{
            background: "#0b6bcb",
            border: "1px solid #0b6bcb",
            borderRadius: "8px",
            color: "#ffffff",
            cursor: "pointer",
            fontWeight: 650,
            padding: "8px 12px",
          }}
        >
          Save changes
        </button>
      </div>
    </form>
  );
}
