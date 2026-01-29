'use client';

import Link from "next/link";
import { FormEvent, useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";

type ContactSummary = {
  contact_id: string;
  display_name: string;
  aliases: string[];
  emails: string[];
  phones: string[];
  links: string[];
  tags: string[];
  external_id?: string | null;
};

type MergeSuggestion = {
  contact_a_id: string;
  contact_a_display_name?: string | null;
  contact_b_id: string;
  contact_b_display_name?: string | null;
  score: number;
  matched_on?: string | null;
};

type MergeCandidatesResponse = {
  external_contacts: ContactSummary[];
  unlinked_contacts: ContactSummary[];
  suggestions: MergeSuggestion[];
};

type Status =
  | { kind: "idle" }
  | { kind: "success"; message: string }
  | { kind: "error"; message: string };

export default function MergeContactsPage() {
  const [data, setData] = useState<MergeCandidatesResponse | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [status, setStatus] = useState<Status>({ kind: "idle" });
  const [primarySelection, setPrimarySelection] = useState<string>("");
  const [externalSelection, setExternalSelection] = useState<string>("");
  const [activeMergeKey, setActiveMergeKey] = useState<string | null>(null);

  useEffect(() => {
    loadData();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function loadData() {
    setIsLoading(true);
    try {
      const response = await api.get<MergeCandidatesResponse>("/contacts/merge-candidates");
      setData(response);
      const orderedContacts = [...response.unlinked_contacts, ...response.external_contacts];
      const hasPrimary = orderedContacts.some((contact) => contact.contact_id === primarySelection);
      const hasExternal = orderedContacts.some((contact) => contact.contact_id === externalSelection);
      if (!hasPrimary) {
        setPrimarySelection(orderedContacts[0]?.contact_id ?? "");
      }
      if (!hasExternal) {
        const fallback = orderedContacts.find((contact) => contact.contact_id !== primarySelection);
        setExternalSelection(fallback?.contact_id ?? "");
      }
    } catch (error) {
      setStatus({
        kind: "error",
        message: error instanceof Error ? error.message : "Failed to load merge candidates",
      });
    } finally {
      setIsLoading(false);
    }
  }

  async function handleMerge(primaryId: string, duplicateId: string) {
    if (!primaryId || !duplicateId || primaryId === duplicateId) {
      setStatus({
        kind: "error",
        message: "Select two different contacts to merge",
      });
      return;
    }

    const mergeKey = `${primaryId}|${duplicateId}`;
    setActiveMergeKey(mergeKey);
    setStatus({ kind: "idle" });
    try {
      await api.post("/contacts/merge", {
        primary_contact_id: primaryId,
        duplicate_contact_id: duplicateId,
      });
      setStatus({ kind: "success", message: "Contacts merged successfully" });
      await loadData();
    } catch (error) {
      setStatus({
        kind: "error",
        message: error instanceof Error ? error.message : "Failed to merge contacts",
      });
    } finally {
      setActiveMergeKey(null);
    }
  }

  function onManualMergeSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    handleMerge(primarySelection, externalSelection);
  }

  const suggestions = useMemo(() => data?.suggestions ?? [], [data]);
  const externalContacts = data?.external_contacts ?? [];
  const unlinkedContacts = data?.unlinked_contacts ?? [];
  const allContacts = useMemo(
    () => [...unlinkedContacts, ...externalContacts],
    [externalContacts, unlinkedContacts]
  );

  return (
    <section style={{ display: "grid", gap: "24px" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <div>
          <h1 style={{ fontSize: "2rem", fontWeight: 600 }}>Merge Contacts</h1>
          <p style={{ color: "#555", maxWidth: "720px" }}>
            Combine duplicate entries across your contact list, including imported records.
            Suggested matches are based on name similarity and aliases.
          </p>
        </div>
        <Link
          href="/contacts"
          style={{
            background: "#0b6bcb",
            color: "#fff",
            borderRadius: "8px",
            padding: "10px 20px",
            fontWeight: 600,
          }}
        >
          ← Back to Contacts
        </Link>
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

      {isLoading ? (
        <div style={{ padding: "48px", textAlign: "center", color: "#666" }}>Loading merge candidates…</div>
      ) : (
        <>
          <section style={{ display: "grid", gap: "16px" }}>
            <h2 style={{ fontSize: "1.5rem", fontWeight: 600, marginBottom: "4px" }}>Suggested Matches</h2>
            {suggestions.length === 0 ? (
              <div
                style={{
                  padding: "24px",
                  borderRadius: "8px",
                  border: "1px dashed #d1d5db",
                  color: "#6b7280",
                  background: "#f9fafb",
                }}
              >
                No high-confidence matches found. Try a manual merge below.
              </div>
            ) : (
              <div style={{ display: "grid", gap: "16px" }}>
                {suggestions.map((suggestion) => {
                  const primary = allContacts.find(
                    (contact) => contact.contact_id === suggestion.contact_a_id
                  );
                  const duplicate = allContacts.find(
                    (contact) => contact.contact_id === suggestion.contact_b_id
                  );
                  const key = `${suggestion.contact_a_id}|${suggestion.contact_b_id}`;
                  return (
                    <div
                      key={key}
                      style={{
                        border: "1px solid #e5e7eb",
                        borderRadius: "10px",
                        padding: "16px",
                        background: "#fff",
                        boxShadow: "0 10px 24px rgba(15, 23, 42, 0.08)",
                        display: "grid",
                        gap: "12px",
                      }}
                    >
                      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                        <div style={{ display: "grid", gap: "4px" }}>
                          <span style={{ fontWeight: 600, fontSize: "1.1rem" }}>
                            {primary?.display_name ?? suggestion.contact_a_display_name ?? "Contact"}
                          </span>
                          <span style={{ color: "#6b7280", fontSize: "0.9rem" }}>
                            Duplicate: {duplicate?.display_name ?? suggestion.contact_b_display_name}
                          </span>
                        </div>
                        <div
                          style={{
                            background: "#eef2ff",
                            color: "#4338ca",
                            borderRadius: "999px",
                            padding: "6px 16px",
                            fontWeight: 600,
                            fontSize: "0.85rem",
                          }}
                        >
                          Score: {Math.round(suggestion.score)}%
                        </div>
                      </div>
                      {suggestion.matched_on && (
                        <div style={{ fontSize: "0.85rem", color: "#6b7280" }}>
                          Matched on “{suggestion.matched_on}”
                        </div>
                      )}
                      <div style={{ display: "flex", gap: "12px", justifyContent: "flex-end" }}>
                        <button
                          onClick={() => handleMerge(suggestion.contact_a_id, suggestion.contact_b_id)}
                          disabled={activeMergeKey === key}
                          style={{
                            background: "#0b6bcb",
                            color: "#fff",
                            border: "none",
                            borderRadius: "8px",
                            padding: "10px 20px",
                            fontWeight: 600,
                            cursor: activeMergeKey === key ? "not-allowed" : "pointer",
                            opacity: activeMergeKey === key ? 0.7 : 1,
                          }}
                        >
                          {activeMergeKey === key ? "Merging…" : "Merge Contacts"}
                        </button>
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </section>

          <section style={{ display: "grid", gap: "16px" }}>
            <h2 style={{ fontSize: "1.5rem", fontWeight: 600, marginBottom: "4px" }}>Manual Merge</h2>
            <p style={{ color: "#6b7280", fontSize: "0.95rem" }}>
              Choose any two contacts and merge the duplicate into the primary contact.
            </p>
            <form
              onSubmit={onManualMergeSubmit}
              style={{
                display: "grid",
                gap: "12px",
                padding: "20px",
                borderRadius: "10px",
                border: "1px solid #e5e7eb",
                background: "#fff",
              }}
            >
              <label style={{ display: "grid", gap: "6px" }}>
                <span style={{ fontWeight: 600, fontSize: "0.9rem" }}>Primary contact</span>
                <select
                  value={primarySelection}
                  onChange={(event) => setPrimarySelection(event.target.value)}
                  style={{
                    border: "1px solid #d1d5db",
                    borderRadius: "8px",
                    padding: "10px 12px",
                    fontSize: "0.95rem",
                  }}
                >
                  <option value="">-- Select contact --</option>
                  {allContacts.map((contact) => (
                    <option key={contact.contact_id} value={contact.contact_id}>
                      {contact.display_name || contact.contact_id}
                      {contact.external_id ? ` (external ${contact.external_id})` : ""}
                    </option>
                  ))}
                </select>
              </label>

              <label style={{ display: "grid", gap: "6px" }}>
                <span style={{ fontWeight: 600, fontSize: "0.9rem" }}>Duplicate contact</span>
                <select
                  value={externalSelection}
                  onChange={(event) => setExternalSelection(event.target.value)}
                  style={{
                    border: "1px solid #d1d5db",
                    borderRadius: "8px",
                    padding: "10px 12px",
                    fontSize: "0.95rem",
                  }}
                >
                  <option value="">-- Select contact --</option>
                  {allContacts.map((contact) => (
                    <option key={contact.contact_id} value={contact.contact_id}>
                      {contact.display_name || contact.contact_id}
                      {contact.external_id ? ` (external ${contact.external_id})` : ""}
                    </option>
                  ))}
                </select>
              </label>

              <div style={{ display: "flex", justifyContent: "flex-end" }}>
                <button
                  type="submit"
                  disabled={
                    !primarySelection ||
                    !externalSelection ||
                    primarySelection === externalSelection ||
                    activeMergeKey !== null
                  }
                  style={{
                    background: "#0b6bcb",
                    color: "#fff",
                    border: "none",
                    borderRadius: "8px",
                    padding: "10px 20px",
                    fontWeight: 600,
                    cursor:
                      !primarySelection ||
                      !externalSelection ||
                      primarySelection === externalSelection ||
                      activeMergeKey !== null
                        ? "not-allowed"
                        : "pointer",
                    opacity:
                      !primarySelection ||
                      !externalSelection ||
                      primarySelection === externalSelection ||
                      activeMergeKey !== null
                        ? 0.7
                        : 1,
                  }}
                >
                  {activeMergeKey ? "Merging…" : "Merge Selected"}
                </button>
              </div>
            </form>
          </section>

          <section style={{ display: "grid", gap: "16px" }}>
            <h2 style={{ fontSize: "1.4rem", fontWeight: 600 }}>Unlinked Contacts</h2>
            {unlinkedContacts.length === 0 ? (
              <div style={{ color: "#6b7280" }}>All contacts are linked.</div>
            ) : (
              <ul style={{ listStyle: "disc", paddingLeft: "24px", color: "#374151" }}>
                {unlinkedContacts.map((contact) => (
                  <li key={contact.contact_id}>
                    <strong>{contact.display_name || contact.contact_id}</strong>
                    {contact.emails.length > 0 && (
                      <span style={{ color: "#6b7280" }}> – {contact.emails.join(", ")}</span>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section style={{ display: "grid", gap: "16px" }}>
            <h2 style={{ fontSize: "1.4rem", fontWeight: 600 }}>External Contacts</h2>
            {externalContacts.length === 0 ? (
              <div style={{ color: "#6b7280" }}>No external contacts available.</div>
            ) : (
              <ul style={{ listStyle: "disc", paddingLeft: "24px", color: "#374151" }}>
                {externalContacts.map((contact) => (
                  <li key={contact.contact_id}>
                    <strong>{contact.display_name}</strong> – External ID:{" "}
                    <span style={{ color: "#2563eb" }}>{contact.external_id ?? "unknown"}</span>
                    {contact.emails.length > 0 && (
                      <span style={{ color: "#6b7280" }}> ({contact.emails.join(", ")})</span>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </section>
        </>
      )}
    </section>
  );
}
