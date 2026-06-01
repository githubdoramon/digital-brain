"use client";

import { FormEvent, useMemo, useState } from "react";
import type { UiDirectiveBlock, UiDirectiveField, UiDirectives, UiSubmissionInput } from "@/lib/api";
import type { AssistantMetadata } from "./types";

function actionIdForBlock(block: UiDirectiveBlock): string {
  return block.action_id?.trim() || block.id;
}

function fieldStateKey(block: UiDirectiveBlock, field: UiDirectiveField): string {
  return `${block.id}:${field.id}`;
}

function formatFieldLabel(fieldId: string): string {
  return fieldId
    .replace(/_/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .replace(/^\w/, (char) => char.toUpperCase());
}

function optionLabelForField(field: UiDirectiveField | undefined, value: string): string {
  if (!field || !value) return value;
  const match = (field.options || []).find((option) => option.id === value);
  return match?.label || value;
}

function fallbackTextForForm(
  block: UiDirectiveBlock,
  values: Record<string, unknown>,
  defaultText: string,
): string {
  const entries = Object.entries(values)
    .map(([fieldId, rawValue]) => {
      const value = String(rawValue ?? "").trim();
      if (!value) return null;
      const field = (block.fields || []).find((candidate) => candidate.id === fieldId);
      const normalizedValue = optionLabelForField(field, value);
      const label = field?.label || formatFieldLabel(fieldId);
      return { label, value: normalizedValue };
    })
    .filter((entry): entry is { label: string; value: string } => Boolean(entry));

  if (entries.length === 0) return defaultText;
  if (entries.length === 1) return entries[0].value;
  return entries.map((entry) => `${entry.label}: ${entry.value}`).join("; ");
}

function inputTypeForKind(kind: string): string {
  if (kind === "email") return "email";
  if (kind === "url") return "url";
  if (kind === "number") return "number";
  if (kind === "date") return "date";
  if (kind === "time") return "time";
  if (kind === "datetime") return "datetime-local";
  return "text";
}

function renderBody(body: string | undefined) {
  if (!body?.trim()) return null;
  return (
    <div style={{ color: "#334155", display: "grid", gap: "4px", fontSize: "0.9rem", lineHeight: 1.45 }}>
      {body.split("\n").map((line, index) => (
        <div key={`${line}:${index}`}>{line}</div>
      ))}
    </div>
  );
}

export function UiDirectiveCard({
  directives,
  disabled = false,
  resolved,
  onSubmit,
}: {
  directives: UiDirectives;
  disabled?: boolean;
  resolved?: AssistantMetadata["command_resolved"];
  onSubmit: (submission: UiSubmissionInput) => void;
}) {
  const blocks = useMemo(() => directives.blocks || [], [directives.blocks]);
  const [formValues, setFormValues] = useState<Record<string, string>>({});
  const isResolved = Boolean(resolved);

  const getFieldValue = (block: UiDirectiveBlock, field: UiDirectiveField) =>
    formValues[fieldStateKey(block, field)] ?? field.value ?? "";

  const setFieldValue = (block: UiDirectiveBlock, field: UiDirectiveField, value: string) => {
    setFormValues((prev) => ({
      ...prev,
      [fieldStateKey(block, field)]: value,
    }));
  };

  const submitForm = (event: FormEvent<HTMLFormElement>, block: UiDirectiveBlock) => {
    event.preventDefault();
    const values: Record<string, unknown> = {};
    for (const field of block.fields || []) {
      const value = getFieldValue(block, field).trim();
      if (field.required && !value) return;
      if (value) values[field.id] = value;
    }

    onSubmit({
      block_id: block.id,
      action_id: actionIdForBlock(block),
      values,
      text_fallback: fallbackTextForForm(block, values, directives.fallback_text),
    });
  };

  if (blocks.length === 0) return null;

  return (
    <div style={{ display: "grid", gap: "10px", maxWidth: "80%", width: "min(680px, 100%)" }}>
      {resolved ? (
        <div
          style={{
            background: resolved.status === "cancelled" ? "#f1f5f9" : "#ecfdf5",
            border: `1px solid ${resolved.status === "cancelled" ? "#cbd5e1" : "#a7f3d0"}`,
            borderRadius: "8px",
            color: resolved.status === "cancelled" ? "#475569" : "#047857",
            fontSize: "0.88rem",
            fontWeight: 650,
            padding: "10px 12px",
          }}
        >
          {resolved.label || (resolved.status === "cancelled" ? "Cancelled" : "Changes applied")}
        </div>
      ) : null}
      {blocks.map((block) => (
        <div
          key={block.id}
          style={{
            border: "1px solid #d9e2ec",
            borderRadius: "8px",
            background: "#ffffff",
            boxShadow: "0 1px 3px rgba(15, 23, 42, 0.06)",
            display: "grid",
            gap: "10px",
            padding: "14px",
          }}
        >
          <div style={{ display: "grid", gap: "4px" }}>
            {block.title ? (
              <h3 style={{ color: "#0f172a", fontSize: "0.98rem", fontWeight: 700, margin: 0 }}>
                {block.title}
              </h3>
            ) : null}
            {block.description ? (
              <p style={{ color: "#64748b", fontSize: "0.86rem", lineHeight: 1.45, margin: 0 }}>
                {block.description}
              </p>
            ) : null}
          </div>

          {block.type === "info_card" ? (
            <>
              {renderBody(block.body)}
              {(block.links || []).length > 0 ? (
                <div style={{ display: "flex", flexWrap: "wrap", gap: "8px" }}>
                  {(block.links || []).map((link, index) => (
                    <a
                      key={`${link.url}:${index}`}
                      href={link.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      style={{ color: "#0b6bcb", fontSize: "0.86rem", textDecoration: "underline" }}
                    >
                      {link.label}
                    </a>
                  ))}
                </div>
              ) : null}
            </>
          ) : null}

          {block.type === "choice_buttons" ? (
            <div style={{ display: "flex", flexWrap: "wrap", gap: "8px" }}>
              {(block.options || []).map((option) => (
                <button
                  key={option.id}
                  type="button"
                  disabled={disabled || isResolved}
                  onClick={() =>
                    onSubmit({
                      block_id: block.id,
                      action_id: actionIdForBlock(block),
                      values: { option_id: option.id, option_label: option.label },
                      text_fallback: option.label,
                    })
                  }
                  style={{
                    border: "1px solid #0b6bcb",
                    borderRadius: "8px",
                    background: disabled || isResolved ? "#e2e8f0" : "#0b6bcb",
                    color: disabled || isResolved ? "#64748b" : "#ffffff",
                    cursor: disabled || isResolved ? "not-allowed" : "pointer",
                    fontWeight: 650,
                    padding: "8px 12px",
                  }}
                >
                  {option.label}
                </button>
              ))}
            </div>
          ) : null}

          {block.type === "clarification_form" ? (
            <form onSubmit={(event) => submitForm(event, block)} style={{ display: "grid", gap: "10px" }}>
              {(block.fields || []).map((field) => {
                const commonStyle = {
                  border: "1px solid #cbd5e1",
                  borderRadius: "8px",
                  color: "#0f172a",
                  font: "inherit",
                  padding: "9px 10px",
                  width: "100%",
                };
                return (
                  <label key={field.id} style={{ color: "#334155", display: "grid", gap: "5px", fontSize: "0.86rem" }}>
                    <span>
                      {field.label || formatFieldLabel(field.id)}
                      {field.required ? " *" : ""}
                    </span>
                    {field.kind === "textarea" ? (
                      <textarea
                        value={getFieldValue(block, field)}
                        onChange={(event) => setFieldValue(block, field, event.target.value)}
                        placeholder={field.placeholder}
                        disabled={disabled || isResolved}
                        rows={3}
                        style={{ ...commonStyle, resize: "vertical" }}
                      />
                    ) : field.kind === "select" ? (
                      <select
                        value={getFieldValue(block, field)}
                        onChange={(event) => setFieldValue(block, field, event.target.value)}
                        disabled={disabled || isResolved}
                        style={commonStyle}
                      >
                        <option value="">Select...</option>
                        {(field.options || []).map((option) => (
                          <option key={option.id} value={option.id}>
                            {option.label}
                          </option>
                        ))}
                      </select>
                    ) : (
                      <input
                        type={inputTypeForKind(field.kind)}
                        value={getFieldValue(block, field)}
                        onChange={(event) => setFieldValue(block, field, event.target.value)}
                        placeholder={field.placeholder}
                        disabled={disabled || isResolved}
                        style={commonStyle}
                      />
                    )}
                  </label>
                );
              })}
              <div>
                <button
                  type="submit"
                  disabled={disabled || isResolved}
                  style={{
                    border: "1px solid #0b6bcb",
                    borderRadius: "8px",
                    background: disabled || isResolved ? "#e2e8f0" : "#0b6bcb",
                    color: disabled || isResolved ? "#64748b" : "#ffffff",
                    cursor: disabled || isResolved ? "not-allowed" : "pointer",
                    fontWeight: 650,
                    padding: "8px 12px",
                  }}
                >
                  {block.submit_label || "Submit"}
                </button>
              </div>
            </form>
          ) : null}
        </div>
      ))}
    </div>
  );
}
