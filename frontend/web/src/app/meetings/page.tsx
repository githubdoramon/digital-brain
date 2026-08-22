'use client';

import { FormEvent, useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";

type Status =
  | { kind: "idle" }
  | { kind: "success"; message: string }
  | { kind: "error"; message: string };

type EventPayload = {
  id: string;
  startDate: string;
  endDate: string;
  title: string;
  summary: string;
  people: string;
  tags: string;
};

type MeetingListItem = {
  id: string;
  title?: string | null;
  start_date?: string | null;
  end_date?: string | null;
  summary?: string | null;
};

type MeetingDetailResponse = MeetingListItem & {
  people?: string[] | null;
  tags?: string[] | null;
  raw?: Record<string, unknown> | null;
  action_items?: Array<{
    task?: string | null;
    assignee_name?: string | null;
    due_date?: string | null;
  }>;
};

type LibraryStatus =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "error"; message: string };

type RerunStatus =
  | { kind: "idle" }
  | { kind: "running" }
  | { kind: "queued"; message: string }
  | { kind: "error"; message: string };

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

function formatMeetingDate(value: string | null | undefined) {
  if (!value) {
    return "Date not recorded";
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(parsed);
}

function getStoredTranscript(raw: Record<string, unknown> | null | undefined) {
  const transcriptText = raw?.transcript_text;
  if (typeof transcriptText === "string" && transcriptText.trim()) {
    return transcriptText.trim();
  }
  return "";
}

function getTranscriptHash(raw: Record<string, unknown> | null | undefined) {
  const hash = raw?.transcript_hash;
  return typeof hash === "string" && hash.trim() ? hash.trim() : null;
}

export default function MeetingsPage() {
  const [meetings, setMeetings] = useState<MeetingListItem[]>([]);
  const [selectedMeetingId, setSelectedMeetingId] = useState<string | null>(null);
  const [selectedMeeting, setSelectedMeeting] = useState<MeetingDetailResponse | null>(null);
  const [meetingSearch, setMeetingSearch] = useState("");
  const [appliedMeetingSearch, setAppliedMeetingSearch] = useState("");
  const [libraryStatus, setLibraryStatus] = useState<LibraryStatus>({ kind: "loading" });
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [detailRefreshKey, setDetailRefreshKey] = useState(0);
  const [rerunStatus, setRerunStatus] = useState<RerunStatus>({ kind: "idle" });

  useEffect(() => {
    let active = true;
    setLibraryStatus({ kind: "loading" });
    const query = appliedMeetingSearch.trim();
    const endpoint = `/events/search?limit=50&include_future=true&meeting_only=true${
      query ? `&query=${encodeURIComponent(query)}` : ""
    }`;

    api
      .get<{ events?: MeetingListItem[] }>(endpoint)
      .then((response) => {
        if (!active) {
          return;
        }
        const nextMeetings = Array.isArray(response.events) ? response.events : [];
        setMeetings(nextMeetings);
        setSelectedMeetingId((current) =>
          current && nextMeetings.some((meeting) => meeting.id === current)
            ? current
            : nextMeetings[0]?.id ?? null
        );
        setLibraryStatus({ kind: "idle" });
      })
      .catch((error) => {
        if (!active) {
          return;
        }
        setLibraryStatus({
          kind: "error",
          message: error instanceof Error ? error.message : "Failed to load meetings",
        });
      });

    return () => {
      active = false;
    };
  }, [appliedMeetingSearch]);

  useEffect(() => {
    if (!selectedMeetingId) {
      setSelectedMeeting(null);
      setDetailError(null);
      return;
    }

    let active = true;
    setDetailLoading(true);
    setDetailError(null);
    setSelectedMeeting(null);
    api
      .get<MeetingDetailResponse>(`/meetings/${encodeURIComponent(selectedMeetingId)}`)
      .then((response) => {
        if (active) {
          setSelectedMeeting(response);
        }
      })
      .catch((error) => {
        if (active) {
          setDetailError(error instanceof Error ? error.message : "Failed to load meeting content");
        }
      })
      .finally(() => {
        if (active) {
          setDetailLoading(false);
        }
      });

    return () => {
      active = false;
    };
  }, [selectedMeetingId, detailRefreshKey]);

  async function handleRerunSummary() {
    if (!selectedMeetingId) {
      return;
    }
    setRerunStatus({ kind: "running" });
    try {
      await api.post(`/meetings/${encodeURIComponent(selectedMeetingId)}/summary/rerun`);
      setRerunStatus({
        kind: "queued",
        message: "Summary queued. Refresh the meeting after the worker finishes.",
      });
    } catch (error) {
      setRerunStatus({
        kind: "error",
        message: error instanceof Error ? error.message : "Failed to queue summary",
      });
    }
  }

  const [formState, setFormState] = useState<EventPayload>(() => ({
    id: `meeting-${Date.now()}`,
    startDate: toLocalDateTimeInput(new Date()),
    endDate: "",
    title: "",
    summary: "",
    people: "",
    tags: "meeting",
  }));
  const [status, setStatus] = useState<Status>({ kind: "idle" });
  const [isSubmitting, setSubmitting] = useState(false);

  const requestPreview = useMemo(() => {
    const body: Record<string, unknown> = {
      id: formState.id,
      start_date: new Date(formState.startDate).toISOString(),
      title: formState.title,
      summary: formState.summary,
      people: parseList(formState.people),
      tags: parseList(formState.tags),
      types: ["meeting"],
    };
    if (formState.endDate) {
      body.end_date = new Date(formState.endDate).toISOString();
    }
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

    const basePayload = {
      id: formState.id.trim() || `meeting-${Date.now()}`,
      start_date: new Date(formState.startDate).toISOString(),
      title: formState.title.trim(),
      summary: formState.summary,
      people: parseList(formState.people),
      tags: parseList(formState.tags),
      types: ["meeting"],
    };
    const eventId = basePayload.id;
    const payload = formState.endDate
      ? {
          ...basePayload,
          end_date: new Date(formState.endDate).toISOString(),
        }
      : basePayload;

    try {
      await api.post("/ingest/event", payload);

      setStatus({
        kind: "success",
        message: `Meeting ${eventId} imported successfully`,
      });
      setFormState((prev) => ({
        ...prev,
        id: `meeting-${Date.now()}`,
        title: "",
        summary: "",
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
      <div
        style={{
          display: "grid",
          gap: "22px",
          padding: "30px",
          borderRadius: "28px",
          color: "#f7f3ea",
          background:
            "radial-gradient(circle at 88% 12%, rgba(210, 159, 92, 0.26), transparent 28%), linear-gradient(135deg, #10283a 0%, #173d4a 58%, #1e554e 100%)",
          boxShadow: "0 22px 55px rgba(16, 40, 58, 0.18)",
        }}
      >
        <div style={{ display: "flex", justifyContent: "space-between", gap: "18px", alignItems: "flex-end", flexWrap: "wrap" }}>
          <div style={{ display: "grid", gap: "8px" }}>
            <span style={{ color: "#d6b47b", fontSize: "0.75rem", letterSpacing: "0.18em", textTransform: "uppercase", fontWeight: 700 }}>
              Memory / conversations
            </span>
            <h1 style={{ margin: 0, fontSize: "clamp(2rem, 5vw, 3.65rem)", lineHeight: 0.98, letterSpacing: "-0.06em", fontWeight: 700 }}>
              Meeting room
            </h1>
            <p style={{ margin: 0, maxWidth: "44rem", color: "rgba(247, 243, 234, 0.76)", lineHeight: 1.55 }}>
              Browse the conversations that became memory. Open a transcript, inspect the current summary, or send it through the summarizer again.
            </p>
          </div>
          <div style={{ display: "flex", gap: "10px", alignItems: "center", color: "rgba(247, 243, 234, 0.72)", fontSize: "0.82rem" }}>
            <span style={{ width: "8px", height: "8px", borderRadius: "50%", background: "#d6b47b", boxShadow: "0 0 0 5px rgba(214, 180, 123, 0.16)" }} />
            {meetings.length} {meetings.length === 1 ? "meeting" : "meetings"} in view
          </div>
        </div>

        <div className="meeting-library-grid" style={{ display: "grid", gridTemplateColumns: "minmax(0, 0.78fr) minmax(0, 1.42fr)", gap: "16px", alignItems: "stretch" }}>
          <aside
            style={{
              display: "grid",
              gridTemplateRows: "auto 1fr",
              minHeight: "510px",
              overflow: "hidden",
              border: "1px solid rgba(247, 243, 234, 0.14)",
              borderRadius: "20px",
              background: "rgba(8, 25, 36, 0.28)",
            }}
          >
            <form
              onSubmit={(event) => {
                event.preventDefault();
                setAppliedMeetingSearch(meetingSearch);
              }}
              style={{ display: "flex", gap: "8px", padding: "14px", borderBottom: "1px solid rgba(247, 243, 234, 0.12)" }}
            >
              <input
                value={meetingSearch}
                onChange={(event) => setMeetingSearch(event.target.value)}
                placeholder="Search meetings"
                aria-label="Search meetings"
                style={{ minWidth: 0, flex: 1, border: "1px solid rgba(247, 243, 234, 0.2)", borderRadius: "12px", padding: "10px 12px", color: "#f7f3ea", background: "rgba(247, 243, 234, 0.09)", outline: "none" }}
              />
              <button type="submit" style={{ border: 0, borderRadius: "12px", padding: "0 13px", color: "#17313e", background: "#d6b47b", fontWeight: 700, cursor: "pointer" }}>
                Find
              </button>
            </form>

            <div style={{ overflowY: "auto", padding: "8px" }}>
              {libraryStatus.kind === "loading" ? (
                <p style={{ margin: "18px 12px", color: "rgba(247, 243, 234, 0.62)" }}>Loading your meetings…</p>
              ) : libraryStatus.kind === "error" ? (
                <p role="alert" style={{ margin: "18px 12px", color: "#ffc7b8" }}>{libraryStatus.message}</p>
              ) : meetings.length === 0 ? (
                <p style={{ margin: "18px 12px", color: "rgba(247, 243, 234, 0.62)" }}>No meetings found.</p>
              ) : (
                meetings.map((meeting, index) => {
                  const active = meeting.id === selectedMeetingId;
                  return (
                    <button
                      type="button"
                      key={meeting.id}
                      onClick={() => {
                        setSelectedMeetingId(meeting.id);
                        setDetailError(null);
                        setRerunStatus({ kind: "idle" });
                      }}
                      style={{
                        display: "grid",
                        gap: "7px",
                        width: "100%",
                        textAlign: "left",
                        border: active ? "1px solid rgba(214, 180, 123, 0.75)" : "1px solid transparent",
                        borderRadius: "15px",
                        padding: "14px",
                        color: "#f7f3ea",
                        background: active ? "rgba(214, 180, 123, 0.14)" : "transparent",
                        cursor: "pointer",
                        animation: `meeting-rise 420ms ease both`,
                        animationDelay: `${Math.min(index * 35, 280)}ms`,
                      }}
                    >
                      <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", fontWeight: 700, letterSpacing: "-0.01em" }}>
                        {meeting.title?.trim() || "Untitled meeting"}
                      </span>
                      <span style={{ color: "rgba(247, 243, 234, 0.58)", fontSize: "0.78rem" }}>
                        {formatMeetingDate(meeting.start_date)}
                      </span>
                      <span style={{ color: active ? "#d6b47b" : "rgba(247, 243, 234, 0.42)", fontSize: "0.72rem", letterSpacing: "0.08em", textTransform: "uppercase", fontWeight: 700 }}>
                        {meeting.summary?.trim() ? "Summary available" : "Transcript memory"}
                      </span>
                    </button>
                  );
                })
              )}
            </div>
          </aside>

          <article style={{ minHeight: "510px", borderRadius: "20px", padding: "24px", color: "#182936", background: "#f7f3ea", boxShadow: "0 12px 30px rgba(5, 24, 32, 0.12)" }}>
            {detailLoading ? (
              <p style={{ color: "#62736f" }}>Opening meeting memory…</p>
            ) : detailError ? (
              <div role="alert" style={{ display: "grid", gap: "8px", placeItems: "start", minHeight: "450px", alignContent: "center", color: "#9a3d2c" }}>
                <strong>Could not open this meeting.</strong>
                <span>{detailError}</span>
              </div>
            ) : selectedMeeting ? (
              <div style={{ display: "grid", gap: "18px" }}>
                <div style={{ display: "flex", justifyContent: "space-between", gap: "16px", alignItems: "flex-start", flexWrap: "wrap" }}>
                  <div style={{ display: "grid", gap: "7px" }}>
                    <span style={{ color: "#5b8779", fontSize: "0.72rem", letterSpacing: "0.16em", textTransform: "uppercase", fontWeight: 800 }}>
                      Selected memory
                    </span>
                    <h2 style={{ margin: 0, maxWidth: "30rem", fontSize: "clamp(1.5rem, 3vw, 2.35rem)", lineHeight: 1.02, letterSpacing: "-0.05em" }}>
                      {selectedMeeting.title?.trim() || "Untitled meeting"}
                    </h2>
                    <span style={{ color: "#62736f", fontSize: "0.88rem" }}>{formatMeetingDate(selectedMeeting.start_date)}</span>
                  </div>
                  <div style={{ display: "flex", gap: "8px", flexWrap: "wrap" }}>
                    <button
                      type="button"
                      onClick={() => setDetailRefreshKey((key) => key + 1)}
                      disabled={detailLoading}
                      style={{ border: "1px solid #cad8d0", borderRadius: "999px", padding: "10px 14px", color: "#46645b", background: "#fff", fontWeight: 750, cursor: "pointer", opacity: detailLoading ? 0.48 : 1 }}
                    >
                      Refresh
                    </button>
                    <button
                      type="button"
                      onClick={handleRerunSummary}
                      disabled={rerunStatus.kind === "running" || !getTranscriptHash(selectedMeeting.raw)}
                      style={{ border: "1px solid #b98d54", borderRadius: "999px", padding: "10px 15px", color: "#6e4c22", background: "#f0d6a7", fontWeight: 800, cursor: "pointer", opacity: rerunStatus.kind === "running" || !getTranscriptHash(selectedMeeting.raw) ? 0.48 : 1 }}
                    >
                      {rerunStatus.kind === "running" ? "Queueing…" : "Re-run summary"}
                    </button>
                  </div>
                </div>

                {rerunStatus.kind !== "idle" && (
                  <div role={rerunStatus.kind === "error" ? "alert" : "status"} style={{ borderRadius: "12px", padding: "11px 13px", color: rerunStatus.kind === "error" ? "#9a3d2c" : "#376c5b", background: rerunStatus.kind === "error" ? "#fbe3dc" : "#deeee5", fontSize: "0.86rem" }}>
                    {rerunStatus.kind === "queued" || rerunStatus.kind === "error" ? rerunStatus.message : "Sending the meeting to the summary worker…"}
                  </div>
                )}

                <div style={{ display: "grid", gap: "8px", padding: "17px", borderLeft: "4px solid #5b8779", borderRadius: "3px 14px 14px 3px", background: "#e8f0e8" }}>
                  <span style={{ color: "#5b8779", fontSize: "0.7rem", letterSpacing: "0.14em", textTransform: "uppercase", fontWeight: 800 }}>Current summary</span>
                  <p style={{ margin: 0, lineHeight: 1.6, color: "#263d3a" }}>{selectedMeeting.summary?.trim() || "No summary has been generated yet."}</p>
                </div>

                <div style={{ display: "grid", gap: "8px" }}>
                  <div style={{ display: "flex", justifyContent: "space-between", gap: "12px", alignItems: "baseline" }}>
                    <h3 style={{ margin: 0, fontSize: "1rem", letterSpacing: "-0.02em" }}>Transcript</h3>
                    <span style={{ color: "#84928d", fontSize: "0.75rem" }}>{getTranscriptHash(selectedMeeting.raw) ? `hash ${getTranscriptHash(selectedMeeting.raw)?.slice(0, 8)}` : "not stored"}</span>
                  </div>
                  <pre style={{ maxHeight: "270px", overflow: "auto", whiteSpace: "pre-wrap", margin: 0, padding: "15px", border: "1px solid #dce4dc", borderRadius: "13px", color: "#394b47", background: "#fbfaf5", fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace", fontSize: "0.78rem", lineHeight: 1.55 }}>
                    {getStoredTranscript(selectedMeeting.raw) || "No stored transcript content for this meeting."}
                  </pre>
                </div>

                <div style={{ display: "grid", gap: "8px" }}>
                  <h3 style={{ margin: 0, fontSize: "1rem", letterSpacing: "-0.02em" }}>Action items</h3>
                  {selectedMeeting.action_items?.length ? (
                    <ul style={{ display: "grid", gap: "7px", margin: 0, paddingLeft: "20px", color: "#394b47", fontSize: "0.88rem" }}>
                      {selectedMeeting.action_items.map((item, index) => (
                        <li key={`${item.task}-${index}`}>
                          {item.task || "Untitled action"}{item.assignee_name ? ` · ${item.assignee_name}` : ""}{item.due_date ? ` · due ${item.due_date}` : ""}
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p style={{ margin: 0, color: "#84928d", fontSize: "0.88rem" }}>No action items recorded.</p>
                  )}
                </div>
              </div>
            ) : (
              <div style={{ display: "grid", placeItems: "center", minHeight: "450px", color: "#84928d", textAlign: "center" }}>
                Select a meeting to open its memory.
              </div>
            )}
          </article>
        </div>
        <style>{`@keyframes meeting-rise { from { opacity: 0; transform: translateY(5px); } to { opacity: 1; transform: translateY(0); } } @media (max-width: 760px) { .meeting-library-grid { grid-template-columns: 1fr !important; } }`}</style>
      </div>

      <div style={{ display: "grid", gap: "8px" }}>
        <h2 style={{ margin: 0, fontSize: "1.45rem", fontWeight: 650 }}>Manual import</h2>
        <p style={{ margin: 0, color: "#555" }}>
          Import a manually prepared meeting summary into your personal memory database.
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
            <span style={{ fontWeight: 600 }}>Title</span>
            <input
              type="text"
              required
              value={formState.title}
              onChange={handleChange("title")}
              placeholder="Weekly sync with product team"
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
              value={formState.startDate}
              onChange={handleChange("startDate")}
              style={{
                border: "1px solid #d0d0d0",
                borderRadius: "8px",
                padding: "10px 12px",
              }}
            />
          </label>

          <label style={{ display: "grid", gap: "6px" }}>
            <span style={{ fontWeight: 600 }}>End Date &amp; Time (optional)</span>
            <input
              type="datetime-local"
              value={formState.endDate}
              onChange={handleChange("endDate")}
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
              value={formState.summary}
              onChange={handleChange("summary")}
              rows={6}
              placeholder="Describe decisions, context, and key next steps."
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
                startDate: toLocalDateTimeInput(new Date()),
                endDate: "",
                title: "",
                summary: "",
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
          The JSON payload sent to the backend looks
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
