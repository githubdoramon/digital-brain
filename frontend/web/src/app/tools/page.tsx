"use client";

import { useMemo, useState } from "react";
import type { FormEvent } from "react";
import { api } from "@/lib/api";

type ToolName = "search_memories" | "lookup_contact" | "resolve_contacts";

type ToolRunResponse = {
  tool_name: string;
  args: Record<string, unknown>;
  normalized_args: Record<string, unknown>;
  result: Record<string, unknown>;
  duration_ms: number;
};

type MemoryResult = {
  id: string;
  kind: "event" | "document";
  title?: string | null;
  summary?: string | null;
  snippet?: string | null;
  score?: number;
  score_breakdown?: {
    semantic?: number;
    keyword?: number;
    structured?: number;
  };
  match_sources?: string[];
  start_date?: string | null;
  end_date?: string | null;
  place?: {
    place_id: string;
    name?: string | null;
    city?: string | null;
    country?: string | null;
  } | null;
  people?: string[];
  tags?: string[];
  document_date?: string | null;
  description?: string | null;
  file_name?: string | null;
  download_url?: string | null;
};

type LookupContactResult = {
  action?: string;
  contacts?: Array<{
    contact_id: string;
    display_name: string;
    match_score?: number;
    match_reason?: string;
  }>;
  contact?: {
    contact_id: string;
    display_name: string;
  };
  relationships?: Array<{
    contact_id?: string | null;
    type?: string | null;
    other_type?: string | null;
    related_contact?: {
      display_name?: string | null;
    };
  }>;
  primary_contact?: {
    contact_id: string;
    display_name: string;
    match_score?: number;
    match_reason?: string;
  };
  related_contacts?: Array<{
    contact_id?: string | null;
    type?: string | null;
    other_type?: string | null;
    related_contact?: {
      display_name?: string | null;
    };
  }>;
};

type ResolvedContact = {
  original_text: string;
  contact_id: string;
  display_name: string;
  matched_via: string;
  confidence: string;
  resolution_path?: string[] | null;
};

type NewContact = {
  original_text: string;
  display_name: string;
  inferred_profession?: string | null;
};

type AmbiguousContact = {
  original_text: string;
  candidates: Array<{
    contact_id: string;
    display_name: string;
    match_score: number;
  }>;
};

type NeedUserInput = {
  kind: string;
  prompt: string;
};

type ResolveContactsResult = {
  status?: "success" | "need_user_input" | "no_people" | "error";
  text?: string;
  people_mentioned?: string[];
  resolved_contacts?: ResolvedContact[];
  new_contacts?: NewContact[];
  ambiguous_contacts?: AmbiguousContact[];
  need_user_input?: NeedUserInput;
  message?: string;
};

const TOOL_OPTIONS: Array<{ value: ToolName; label: string }> = [
  { value: "search_memories", label: "search_memories" },
  { value: "lookup_contact", label: "lookup_contact" },
  { value: "resolve_contacts", label: "resolve_contacts" },
];

const baseCardStyle = {
  border: "1px solid #e2e2e2",
  borderRadius: "12px",
  padding: "16px",
  background: "#fff",
};

function formatScore(value: number | undefined) {
  if (value === undefined || Number.isNaN(value)) {
    return "n/a";
  }
  return value.toFixed(3);
}

function parseCommaList(value: string): string[] | undefined {
  const items = value
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
  return items.length > 0 ? items : undefined;
}

export default function ToolsPage() {
  const [tool, setTool] = useState<ToolName>("search_memories");
  const [searchForm, setSearchForm] = useState({
    query: "",
    limit: "5",
    timeStart: "",
    timeEnd: "",
    contactIds: "",
    sortOrder: "relevance",
    tags: "",
  });
  const [lookupForm, setLookupForm] = useState({
    action: "search",
    query: "",
    contactId: "",
    searchBy: "any",
    relationshipTypes: "",
    fuzzyThreshold: "75",
    limit: "10",
  });
  const [resolveForm, setResolveForm] = useState({
    text: "",
  });
  const [response, setResponse] = useState<ToolRunResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  const lookupAction = lookupForm.action as "search" | "get_relationships" | "find_related";

  const canSubmit = useMemo(() => {
    if (tool === "search_memories") {
      return Boolean(searchForm.query.trim());
    }
    if (tool === "resolve_contacts") {
      return Boolean(resolveForm.text.trim());
    }
    if (lookupAction === "search" || lookupAction === "find_related") {
      return Boolean(lookupForm.query.trim());
    }
    return Boolean(lookupForm.contactId.trim() || lookupForm.query.trim());
  }, [tool, searchForm.query, lookupAction, lookupForm.query, lookupForm.contactId, resolveForm.text]);

  const memoryResults = (response?.result?.results as MemoryResult[] | undefined) ?? [];
  const lookupResult = response?.result as LookupContactResult | undefined;
  const resolveResult = response?.result as ResolveContactsResult | undefined;

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!canSubmit) return;
    setIsLoading(true);
    setError(null);
    setResponse(null);

    const args: Record<string, unknown> = {};

    if (tool === "search_memories") {
      args.query = searchForm.query.trim();
      if (searchForm.limit.trim()) {
        args.limit = Number(searchForm.limit);
      }
      if (searchForm.timeStart.trim()) {
        args.time_start = searchForm.timeStart.trim();
      }
      if (searchForm.timeEnd.trim()) {
        args.time_end = searchForm.timeEnd.trim();
      }
      const contactIds = parseCommaList(searchForm.contactIds);
      if (contactIds) {
        args.contact_ids = contactIds;
      }
      if (searchForm.sortOrder) {
        args.sort_order = searchForm.sortOrder;
      }
      const tags = parseCommaList(searchForm.tags);
      if (tags) {
        args.tags = tags;
      }
    } else if (tool === "lookup_contact") {
      args.action = lookupAction;
      if (lookupForm.query.trim()) {
        args.query = lookupForm.query.trim();
      }
      if (lookupForm.contactId.trim()) {
        args.contact_id = lookupForm.contactId.trim();
      }
      if (lookupAction === "search") {
        if (lookupForm.searchBy) {
          args.search_by = lookupForm.searchBy;
        }
        if (lookupForm.fuzzyThreshold.trim()) {
          args.fuzzy_threshold = Number(lookupForm.fuzzyThreshold);
        }
        if (lookupForm.limit.trim()) {
          args.limit = Number(lookupForm.limit);
        }
      }
      if (lookupAction === "find_related") {
        if (lookupForm.fuzzyThreshold.trim()) {
          args.fuzzy_threshold = Number(lookupForm.fuzzyThreshold);
        }
      }
      if (lookupAction !== "search") {
        const relationshipTypes = parseCommaList(lookupForm.relationshipTypes);
        if (relationshipTypes) {
          args.relationship_types = relationshipTypes;
        }
      }
    } else {
      args.text = resolveForm.text.trim();
    }

    try {
      const result = await api.post<ToolRunResponse>("/tools/run", {
        tool_name: tool,
        args,
      });
      setResponse(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to run tool");
    } finally {
      setIsLoading(false);
    }
  }

  return (
    <section style={{ display: "grid", gap: "24px" }}>
      <div style={{ display: "grid", gap: "8px" }}>
        <h1 style={{ fontSize: "1.75rem", fontWeight: 600 }}>Tools</h1>
        <p style={{ color: "#555" }}>
          Run tool calls directly to inspect payloads and results without the agent loop.
        </p>
      </div>

      <form onSubmit={handleSubmit} style={{ ...baseCardStyle, display: "grid", gap: "16px" }}>
        <div style={{ display: "grid", gap: "6px" }}>
          <label htmlFor="tool" style={{ fontWeight: 600, fontSize: "0.9rem" }}>
            Tool
          </label>
          <select
            id="tool"
            value={tool}
            onChange={(event) => setTool(event.target.value as ToolName)}
            style={{ padding: "10px 12px", borderRadius: "8px", border: "1px solid #d7d7d7" }}
          >
            {TOOL_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </div>

        {tool === "search_memories" ? (
          <div style={{ display: "grid", gap: "14px" }}>
            <div style={{ display: "grid", gap: "6px" }}>
              <label htmlFor="query" style={{ fontWeight: 600, fontSize: "0.9rem" }}>
                Query
              </label>
              <textarea
                id="query"
                value={searchForm.query}
                onChange={(event) => setSearchForm({ ...searchForm, query: event.target.value })}
                placeholder="Find memories about..."
                rows={3}
                style={{ padding: "10px 12px", borderRadius: "8px", border: "1px solid #d7d7d7" }}
              />
            </div>

            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))", gap: "12px" }}>
              <div style={{ display: "grid", gap: "6px" }}>
                <label htmlFor="limit" style={{ fontWeight: 600, fontSize: "0.85rem" }}>
                  Limit
                </label>
                <input
                  id="limit"
                  type="number"
                  value={searchForm.limit}
                  min={1}
                  max={50}
                  onChange={(event) => setSearchForm({ ...searchForm, limit: event.target.value })}
                  style={{ padding: "8px 10px", borderRadius: "8px", border: "1px solid #d7d7d7" }}
                />
              </div>
              <div style={{ display: "grid", gap: "6px" }}>
                <label htmlFor="sortOrder" style={{ fontWeight: 600, fontSize: "0.85rem" }}>
                  Sort order
                </label>
                <select
                  id="sortOrder"
                  value={searchForm.sortOrder}
                  onChange={(event) => setSearchForm({ ...searchForm, sortOrder: event.target.value })}
                  style={{ padding: "8px 10px", borderRadius: "8px", border: "1px solid #d7d7d7" }}
                >
                  <option value="relevance">relevance</option>
                  <option value="newest">newest</option>
                  <option value="oldest">oldest</option>
                </select>
              </div>
              <div style={{ display: "grid", gap: "6px" }}>
                <label htmlFor="timeStart" style={{ fontWeight: 600, fontSize: "0.85rem" }}>
                  Time start (ISO)
                </label>
                <input
                  id="timeStart"
                  type="text"
                  value={searchForm.timeStart}
                  onChange={(event) => setSearchForm({ ...searchForm, timeStart: event.target.value })}
                  placeholder="2025-02-01T00:00:00Z"
                  style={{ padding: "8px 10px", borderRadius: "8px", border: "1px solid #d7d7d7" }}
                />
              </div>
              <div style={{ display: "grid", gap: "6px" }}>
                <label htmlFor="timeEnd" style={{ fontWeight: 600, fontSize: "0.85rem" }}>
                  Time end (ISO)
                </label>
                <input
                  id="timeEnd"
                  type="text"
                  value={searchForm.timeEnd}
                  onChange={(event) => setSearchForm({ ...searchForm, timeEnd: event.target.value })}
                  placeholder="2025-02-07T23:59:59Z"
                  style={{ padding: "8px 10px", borderRadius: "8px", border: "1px solid #d7d7d7" }}
                />
              </div>
            </div>

            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: "12px" }}>
              <div style={{ display: "grid", gap: "6px" }}>
                <label htmlFor="contactIds" style={{ fontWeight: 600, fontSize: "0.85rem" }}>
                  Contact IDs (comma-separated)
                </label>
                <input
                  id="contactIds"
                  type="text"
                  value={searchForm.contactIds}
                  onChange={(event) => setSearchForm({ ...searchForm, contactIds: event.target.value })}
                  placeholder="contact:alex#123, contact:sam#456"
                  style={{ padding: "8px 10px", borderRadius: "8px", border: "1px solid #d7d7d7" }}
                />
              </div>
              <div style={{ display: "grid", gap: "6px" }}>
                <label htmlFor="tags" style={{ fontWeight: 600, fontSize: "0.85rem" }}>
                  Tags (comma-separated)
                </label>
                <input
                  id="tags"
                  type="text"
                  value={searchForm.tags}
                  onChange={(event) => setSearchForm({ ...searchForm, tags: event.target.value })}
                  placeholder="health, travel"
                  style={{ padding: "8px 10px", borderRadius: "8px", border: "1px solid #d7d7d7" }}
                />
              </div>
            </div>
          </div>
        ) : tool === "lookup_contact" ? (
          <div style={{ display: "grid", gap: "14px" }}>
            <div style={{ display: "grid", gap: "6px" }}>
              <label htmlFor="action" style={{ fontWeight: 600, fontSize: "0.9rem" }}>
                Action
              </label>
              <select
                id="action"
                value={lookupForm.action}
                onChange={(event) => setLookupForm({ ...lookupForm, action: event.target.value })}
                style={{ padding: "10px 12px", borderRadius: "8px", border: "1px solid #d7d7d7" }}
              >
                <option value="search">search</option>
                <option value="get_relationships">get_relationships</option>
                <option value="find_related">find_related</option>
              </select>
            </div>

            <div style={{ display: "grid", gap: "6px" }}>
              <label htmlFor="lookupQuery" style={{ fontWeight: 600, fontSize: "0.9rem" }}>
                Query
              </label>
              <input
                id="lookupQuery"
                type="text"
                value={lookupForm.query}
                onChange={(event) => setLookupForm({ ...lookupForm, query: event.target.value })}
                placeholder="Jane Doe"
                style={{ padding: "10px 12px", borderRadius: "8px", border: "1px solid #d7d7d7" }}
              />
              {lookupAction === "get_relationships" && (
                <span style={{ fontSize: "0.8rem", color: "#666" }}>
                  Provide a query or a contact ID to load relationships.
                </span>
              )}
            </div>

            {lookupAction === "get_relationships" && (
              <div style={{ display: "grid", gap: "6px" }}>
                <label htmlFor="contactId" style={{ fontWeight: 600, fontSize: "0.9rem" }}>
                  Contact ID
                </label>
                <input
                  id="contactId"
                  type="text"
                  value={lookupForm.contactId}
                  onChange={(event) => setLookupForm({ ...lookupForm, contactId: event.target.value })}
                  placeholder="contact:alex#123"
                  style={{ padding: "10px 12px", borderRadius: "8px", border: "1px solid #d7d7d7" }}
                />
              </div>
            )}

            {lookupAction === "search" && (
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))", gap: "12px" }}>
                <div style={{ display: "grid", gap: "6px" }}>
                  <label htmlFor="searchBy" style={{ fontWeight: 600, fontSize: "0.85rem" }}>
                    Search by
                  </label>
                  <select
                    id="searchBy"
                    value={lookupForm.searchBy}
                    onChange={(event) => setLookupForm({ ...lookupForm, searchBy: event.target.value })}
                    style={{ padding: "8px 10px", borderRadius: "8px", border: "1px solid #d7d7d7" }}
                  >
                    <option value="any">any</option>
                    <option value="name">name</option>
                    <option value="email">email</option>
                    <option value="phone">phone</option>
                  </select>
                </div>
                <div style={{ display: "grid", gap: "6px" }}>
                  <label htmlFor="fuzzyThreshold" style={{ fontWeight: 600, fontSize: "0.85rem" }}>
                    Fuzzy threshold
                  </label>
                  <input
                    id="fuzzyThreshold"
                    type="number"
                    min={0}
                    max={100}
                    value={lookupForm.fuzzyThreshold}
                    onChange={(event) => setLookupForm({ ...lookupForm, fuzzyThreshold: event.target.value })}
                    style={{ padding: "8px 10px", borderRadius: "8px", border: "1px solid #d7d7d7" }}
                  />
                </div>
                <div style={{ display: "grid", gap: "6px" }}>
                  <label htmlFor="lookupLimit" style={{ fontWeight: 600, fontSize: "0.85rem" }}>
                    Limit
                  </label>
                  <input
                    id="lookupLimit"
                    type="number"
                    min={1}
                    max={50}
                    value={lookupForm.limit}
                    onChange={(event) => setLookupForm({ ...lookupForm, limit: event.target.value })}
                    style={{ padding: "8px 10px", borderRadius: "8px", border: "1px solid #d7d7d7" }}
                  />
                </div>
              </div>
            )}

            {lookupAction !== "search" && (
              <div style={{ display: "grid", gap: "6px" }}>
                <label htmlFor="relationshipTypes" style={{ fontWeight: 600, fontSize: "0.85rem" }}>
                  Relationship types (comma-separated)
                </label>
                <input
                  id="relationshipTypes"
                  type="text"
                  value={lookupForm.relationshipTypes}
                  onChange={(event) => setLookupForm({ ...lookupForm, relationshipTypes: event.target.value })}
                  placeholder="family, colleague"
                  style={{ padding: "8px 10px", borderRadius: "8px", border: "1px solid #d7d7d7" }}
                />
              </div>
            )}

            {lookupAction === "find_related" && (
              <div style={{ display: "grid", gap: "6px" }}>
                <label htmlFor="findRelatedThreshold" style={{ fontWeight: 600, fontSize: "0.85rem" }}>
                  Fuzzy threshold
                </label>
                <input
                  id="findRelatedThreshold"
                  type="number"
                  min={0}
                  max={100}
                  value={lookupForm.fuzzyThreshold}
                  onChange={(event) => setLookupForm({ ...lookupForm, fuzzyThreshold: event.target.value })}
                  style={{ padding: "8px 10px", borderRadius: "8px", border: "1px solid #d7d7d7" }}
                />
              </div>
            )}
          </div>
        ) : (
          <div style={{ display: "grid", gap: "14px" }}>
            <div style={{ display: "grid", gap: "6px" }}>
              <label htmlFor="resolveText" style={{ fontWeight: 600, fontSize: "0.9rem" }}>
                Text
              </label>
              <textarea
                id="resolveText"
                value={resolveForm.text}
                onChange={(event) => setResolveForm({ text: event.target.value })}
                placeholder="Had lunch with John and my daughter's doctor yesterday"
                rows={4}
                style={{ padding: "10px 12px", borderRadius: "8px", border: "1px solid #d7d7d7" }}
              />
              <span style={{ fontSize: "0.8rem", color: "#666" }}>
                Detects people, resolves known contacts, and flags ambiguous mentions.
              </span>
            </div>
          </div>
        )}

        <button
          type="submit"
          disabled={!canSubmit || isLoading}
          style={{
            padding: "10px 16px",
            borderRadius: "8px",
            border: "none",
            background: canSubmit ? "#0b6bcb" : "#9ca3af",
            color: "#fff",
            fontWeight: 600,
            cursor: canSubmit ? "pointer" : "not-allowed",
          }}
        >
          {isLoading ? "Running..." : "Run Tool"}
        </button>
      </form>

      {error && (
        <div
          style={{
            border: "1px solid #fca5a5",
            background: "#fee2e2",
            color: "#b91c1c",
            padding: "16px",
            borderRadius: "8px",
          }}
        >
          <strong style={{ display: "block", marginBottom: "4px" }}>Tool error</strong>
          <span>{error}</span>
        </div>
      )}

      {response && (
        <div style={{ display: "grid", gap: "16px" }}>
          <div style={{ ...baseCardStyle, background: "#f8fafc" }}>
            <strong style={{ display: "block", marginBottom: "6px" }}>Execution summary</strong>
            <div style={{ display: "grid", gap: "4px", fontSize: "0.9rem", color: "#334155" }}>
              <span>Tool: {response.tool_name}</span>
              <span>Duration: {response.duration_ms.toFixed(1)} ms</span>
            </div>
          </div>

          {tool === "search_memories" && (
            <div style={{ ...baseCardStyle, display: "grid", gap: "12px" }}>
              <h2 style={{ fontSize: "1.1rem", fontWeight: 600, margin: 0 }}>
                Memory Results ({memoryResults.length})
              </h2>
              {memoryResults.length === 0 ? (
                <div style={{ color: "#666" }}>No memories found.</div>
              ) : (
                memoryResults.map((item) => (
                  <div
                    key={item.id}
                    style={{
                      border: "1px solid #e5e7eb",
                      borderRadius: "10px",
                      padding: "12px",
                      background: "#fff",
                      display: "grid",
                      gap: "6px",
                    }}
                  >
                    <div style={{ display: "flex", justifyContent: "space-between", gap: "12px" }}>
                      <strong>{item.title || item.file_name || "Untitled"}</strong>
                      <span style={{ fontSize: "0.8rem", color: "#6b7280" }}>{item.kind}</span>
                    </div>
                    {item.snippet && <span style={{ color: "#4b5563" }}>{item.snippet}</span>}
                    {item.score_breakdown && (
                      <span style={{ fontSize: "0.8rem", color: "#6b7280" }}>
                        Score {formatScore(item.score)} | semantic {formatScore(item.score_breakdown.semantic)} | keyword{" "}
                        {formatScore(item.score_breakdown.keyword)} | structured {formatScore(item.score_breakdown.structured)}
                      </span>
                    )}
                    {item.start_date && (
                      <span style={{ fontSize: "0.85rem", color: "#6b7280" }}>
                        {item.start_date}
                      </span>
                    )}
                    <div style={{ display: "flex", flexWrap: "wrap", gap: "12px", fontSize: "0.85rem" }}>
                      {item.kind === "event" && (
                        <a href={`/api/orchestrator/events/${item.id}`} style={{ color: "#0b6bcb" }}>
                          View event
                        </a>
                      )}
                      {item.kind === "document" && (
                        <a href={`/api/orchestrator/documents/${item.id}`} style={{ color: "#0b6bcb" }}>
                          View document
                        </a>
                      )}
                      {item.place?.place_id && (
                        <a
                          href={`/api/orchestrator/places/${item.place.place_id}`}
                          style={{ color: "#0b6bcb" }}
                        >
                          View place
                        </a>
                      )}
                    </div>
                    {item.people && item.people.length > 0 && (
                      <div style={{ display: "flex", flexWrap: "wrap", gap: "8px", fontSize: "0.8rem" }}>
                        {item.people.map((person) => (
                          <a
                            key={person}
                            href={`/api/orchestrator/contacts/${person}`}
                            style={{ color: "#0b6bcb" }}
                          >
                            {person}
                          </a>
                        ))}
                      </div>
                    )}
                  </div>
                ))
              )}
            </div>
          )}

          {tool === "resolve_contacts" && resolveResult && (
            <div style={{ ...baseCardStyle, display: "grid", gap: "12px" }}>
              <h2 style={{ fontSize: "1.1rem", fontWeight: 600, margin: 0 }}>
                Contact Resolution Results
              </h2>

              <div
                style={{
                  border: "1px solid #e5e7eb",
                  borderRadius: "10px",
                  padding: "12px",
                  background: "#f8fafc",
                  display: "grid",
                  gap: "8px",
                }}
              >
                <div style={{ display: "flex", alignItems: "center", gap: "8px", flexWrap: "wrap" }}>
                  <strong>Status</strong>
                  <span
                    style={{
                      fontSize: "0.75rem",
                      fontWeight: 600,
                      borderRadius: "999px",
                      padding: "3px 8px",
                      background:
                        resolveResult.status === "success"
                          ? "#dcfce7"
                          : resolveResult.status === "need_user_input"
                            ? "#fef3c7"
                            : resolveResult.status === "no_people"
                              ? "#e5e7eb"
                              : "#fee2e2",
                      color:
                        resolveResult.status === "success"
                          ? "#166534"
                          : resolveResult.status === "need_user_input"
                            ? "#92400e"
                            : resolveResult.status === "no_people"
                              ? "#374151"
                              : "#991b1b",
                    }}
                  >
                    {resolveResult.status || "unknown"}
                  </span>
                </div>
                {resolveResult.message && (
                  <div style={{ fontSize: "0.9rem", color: "#4b5563" }}>{resolveResult.message}</div>
                )}
              </div>

              {resolveResult.people_mentioned && resolveResult.people_mentioned.length > 0 && (
                <div style={{ display: "grid", gap: "6px" }}>
                  <strong style={{ fontSize: "0.95rem" }}>People Mentioned</strong>
                  <div style={{ display: "flex", flexWrap: "wrap", gap: "8px" }}>
                    {resolveResult.people_mentioned.map((person, index) => (
                      <span
                        key={`${person}-${index}`}
                        style={{
                          border: "1px solid #bfdbfe",
                          color: "#1e3a8a",
                          background: "#eff6ff",
                          borderRadius: "999px",
                          padding: "4px 10px",
                          fontSize: "0.8rem",
                        }}
                      >
                        {person}
                      </span>
                    ))}
                  </div>
                </div>
              )}

              {resolveResult.resolved_contacts && resolveResult.resolved_contacts.length > 0 && (
                <div style={{ display: "grid", gap: "8px" }}>
                  <strong style={{ fontSize: "0.95rem" }}>
                    Resolved Contacts ({resolveResult.resolved_contacts.length})
                  </strong>
                  {resolveResult.resolved_contacts.map((contact, index) => (
                    <div
                      key={`${contact.contact_id}-${index}`}
                      style={{
                        border: "1px solid #86efac",
                        borderRadius: "10px",
                        padding: "12px",
                        background: "#f0fdf4",
                        display: "grid",
                        gap: "6px",
                      }}
                    >
                      <div style={{ fontSize: "0.9rem", color: "#14532d" }}>
                        <strong>{contact.display_name}</strong> from &quot;{contact.original_text}&quot;
                      </div>
                      <div style={{ fontSize: "0.85rem", color: "#166534" }}>
                        Matched via {contact.matched_via} | confidence {contact.confidence}
                      </div>
                      <a
                        href={`/api/orchestrator/contacts/${contact.contact_id}`}
                        style={{ color: "#0b6bcb", fontSize: "0.85rem" }}
                      >
                        View contact
                      </a>
                      {contact.resolution_path && contact.resolution_path.length > 0 && (
                        <div style={{ fontSize: "0.8rem", color: "#166534" }}>
                          Path: {contact.resolution_path.join(" -> ")}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              )}

              {resolveResult.new_contacts && resolveResult.new_contacts.length > 0 && (
                <div style={{ display: "grid", gap: "8px" }}>
                  <strong style={{ fontSize: "0.95rem" }}>
                    New Contacts ({resolveResult.new_contacts.length})
                  </strong>
                  {resolveResult.new_contacts.map((contact, index) => (
                    <div
                      key={`${contact.display_name}-${index}`}
                      style={{
                        border: "1px solid #fde68a",
                        borderRadius: "10px",
                        padding: "12px",
                        background: "#fffbeb",
                        display: "grid",
                        gap: "6px",
                      }}
                    >
                      <div style={{ fontSize: "0.9rem", color: "#92400e" }}>
                        <strong>{contact.display_name}</strong> from &quot;{contact.original_text}&quot;
                      </div>
                      {contact.inferred_profession && (
                        <div style={{ fontSize: "0.85rem", color: "#a16207" }}>
                          Inferred profession: {contact.inferred_profession}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              )}

              {resolveResult.ambiguous_contacts && resolveResult.ambiguous_contacts.length > 0 && (
                <div style={{ display: "grid", gap: "8px" }}>
                  <strong style={{ fontSize: "0.95rem" }}>
                    Ambiguous Mentions ({resolveResult.ambiguous_contacts.length})
                  </strong>
                  {resolveResult.ambiguous_contacts.map((contact, index) => (
                    <div
                      key={`${contact.original_text}-${index}`}
                      style={{
                        border: "1px solid #fdba74",
                        borderRadius: "10px",
                        padding: "12px",
                        background: "#fff7ed",
                        display: "grid",
                        gap: "8px",
                      }}
                    >
                      <div style={{ fontSize: "0.9rem", color: "#9a3412" }}>
                        <strong>{contact.original_text}</strong>
                      </div>
                      {resolveResult.need_user_input?.prompt && (
                        <div style={{ fontSize: "0.85rem", color: "#c2410c" }}>
                          {resolveResult.need_user_input.prompt}
                        </div>
                      )}
                      {contact.candidates.length > 0 && (
                        <div style={{ display: "grid", gap: "6px" }}>
                          {contact.candidates.map((candidate) => (
                            <div
                              key={candidate.contact_id}
                              style={{
                                border: "1px solid #fed7aa",
                                borderRadius: "8px",
                                background: "#fff",
                                padding: "8px 10px",
                                display: "flex",
                                justifyContent: "space-between",
                                gap: "8px",
                                flexWrap: "wrap",
                              }}
                            >
                              <span style={{ fontSize: "0.85rem", color: "#9a3412" }}>{candidate.display_name}</span>
                              <span style={{ fontSize: "0.8rem", color: "#c2410c" }}>
                                score {candidate.match_score}
                              </span>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}

          {tool === "lookup_contact" && lookupResult && (
            <div style={{ ...baseCardStyle, display: "grid", gap: "12px" }}>
              <h2 style={{ fontSize: "1.1rem", fontWeight: 600, margin: 0 }}>
                Contact Results
              </h2>
              {lookupResult.contacts && lookupResult.contacts.length > 0 && (
                <div style={{ display: "grid", gap: "8px" }}>
                  {lookupResult.contacts.map((contact) => (
                    <div
                      key={contact.contact_id}
                      style={{ border: "1px solid #e5e7eb", borderRadius: "10px", padding: "12px" }}
                    >
                      <strong>{contact.display_name}</strong>
                      <div style={{ fontSize: "0.85rem", color: "#6b7280" }}>
                        Match score: {contact.match_score ?? "n/a"}
                      </div>
                      <a
                        href={`/api/orchestrator/contacts/${contact.contact_id}`}
                        style={{ color: "#0b6bcb", fontSize: "0.85rem" }}
                      >
                        View contact
                      </a>
                    </div>
                  ))}
                </div>
              )}

              {lookupResult.contact && (
                <div style={{ display: "grid", gap: "6px" }}>
                  <strong>{lookupResult.contact.display_name}</strong>
                  <a
                    href={`/api/orchestrator/contacts/${lookupResult.contact.contact_id}`}
                    style={{ color: "#0b6bcb", fontSize: "0.85rem" }}
                  >
                    View contact
                  </a>
                </div>
              )}

              {lookupResult.primary_contact && (
                <div style={{ display: "grid", gap: "6px" }}>
                  <strong>{lookupResult.primary_contact.display_name}</strong>
                  <a
                    href={`/api/orchestrator/contacts/${lookupResult.primary_contact.contact_id}`}
                    style={{ color: "#0b6bcb", fontSize: "0.85rem" }}
                  >
                    View contact
                  </a>
                </div>
              )}

              {(lookupResult.relationships || lookupResult.related_contacts) && (
                <div style={{ display: "grid", gap: "8px" }}>
                  {(lookupResult.relationships || lookupResult.related_contacts || []).map((rel, index) => (
                    <div
                      key={`${rel.contact_id || "rel"}-${index}`}
                      style={{ border: "1px solid #e5e7eb", borderRadius: "10px", padding: "12px" }}
                    >
                      <div style={{ fontSize: "0.85rem", color: "#374151" }}>
                        {rel.related_contact?.display_name || rel.contact_id}
                      </div>
                      <div style={{ fontSize: "0.8rem", color: "#6b7280" }}>
                        {rel.type || rel.other_type || "relationship"}
                      </div>
                      {rel.contact_id && (
                        <a
                          href={`/api/orchestrator/contacts/${rel.contact_id}`}
                          style={{ color: "#0b6bcb", fontSize: "0.85rem" }}
                        >
                          View contact
                        </a>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}

          <details style={{ ...baseCardStyle, background: "#f9fafb" }}>
            <summary style={{ cursor: "pointer", fontWeight: 600 }}>Raw response</summary>
            <pre style={{ marginTop: "12px", fontSize: "0.75rem", overflow: "auto" }}>
              {JSON.stringify(response, null, 2)}
            </pre>
          </details>
        </div>
      )}
    </section>
  );
}
