'use client';

import Link from "next/link";
import { useEffect, useState, type ReactNode, type HTMLAttributes } from "react";
import { useParams } from "next/navigation";
import ReactMarkdown from "react-markdown";
import type { Components } from "react-markdown";
import remarkGfm from "remark-gfm";

import { api } from "@/lib/api";

type MeetingRaw = {
  content?: string;
  link?: string;
  attendees?: string[];
  attendee_contact_ids?: string[];
  [key: string]: unknown;
};

type MeetingPlace = {
  place_id: string | null;
  name: string | null;
  city: string | null;
  country: string | null;
  lat: number | null;
  lon: number | null;
};

type MeetingResponse = {
  id: string;
  title?: string | null;
  start_date?: string | null;
  end_date?: string | null;
  summary?: string | null;
  people?: string[] | null;
  tags?: string[] | null;
  types?: string[] | null;
  raw?: MeetingRaw | Record<string, unknown> | null;
  place?: {
    place_id?: string | null;
    name?: string | null;
    city?: string | null;
    country?: string | null;
    lat?: number | null;
    lon?: number | null;
  } | null;
};

type MeetingDetail = {
  id: string;
  title: string | null;
  startDate: string | null;
  endDate: string | null;
  summary: string | null;
  people: string[];
  tags: string[];
  types: string[];
  raw: MeetingRaw | null;
  place: MeetingPlace | null;
};

type MetadataItem = {
  label: string;
  value: ReactNode;
};

type MarkdownCodeProps = HTMLAttributes<HTMLElement> & {
  inline?: boolean;
  className?: string;
  children?: ReactNode;
};

const markdownComponents: Components = {
  h1: ({ children }) => (
    <h1 style={{ margin: "0 0 1.25rem 0", fontSize: "1.6rem", fontWeight: 600, color: "#0f172a" }}>{children}</h1>
  ),
  h2: ({ children }) => (
    <h2 style={{ margin: "1.5rem 0 1rem 0", fontSize: "1.3rem", fontWeight: 600, color: "#111827" }}>{children}</h2>
  ),
  h3: ({ children }) => (
    <h3 style={{ margin: "1.25rem 0 0.85rem 0", fontSize: "1.1rem", fontWeight: 600, color: "#1f2937" }}>{children}</h3>
  ),
  p: ({ children }) => (
    <p style={{ margin: "0 0 0.9rem 0", lineHeight: 1.6, color: "#1f2937" }}>{children}</p>
  ),
  a: ({ href, children }) => (
    <a
      href={href ?? "#"}
      target="_blank"
      rel="noopener noreferrer"
      style={{ color: "#2563eb", textDecoration: "underline" }}
    >
      {children}
    </a>
  ),
  ul: ({ children }) => (
    <ul style={{ margin: "0 0 1rem 1.25rem", padding: 0, lineHeight: 1.6, color: "#1f2937" }}>{children}</ul>
  ),
  ol: ({ children }) => (
    <ol style={{ margin: "0 0 1rem 1.25rem", padding: 0, lineHeight: 1.6, color: "#1f2937" }}>{children}</ol>
  ),
  li: ({ children }) => <li style={{ margin: "0.25rem 0" }}>{children}</li>,
  blockquote: ({ children }) => (
    <blockquote
      style={{
        margin: "0 0 1rem 0",
        padding: "0.65rem 0.9rem",
        borderLeft: "4px solid #cbd5f5",
        borderRadius: "6px",
        background: "#f8fafc",
        color: "#1e293b",
      }}
    >
      {children}
    </blockquote>
  ),
  code: ({ inline, className, children }: MarkdownCodeProps) => {
    const codeProps =
      typeof className === "string" && className.length > 0 ? { className } : {};

    if (inline) {
      return (
        <code
          style={{
            background: "rgba(15, 23, 42, 0.08)",
            padding: "0.15rem 0.35rem",
            borderRadius: "4px",
            fontSize: "0.9rem",
          }}
          {...codeProps}
        >
          {children}
        </code>
      );
    }

    return (
      <pre
        style={{
          background: "#0f172a",
          color: "#e2e8f0",
          padding: "1rem",
          borderRadius: "8px",
          overflowX: "auto",
          margin: "0 0 1rem 0",
          fontSize: "0.9rem",
        }}
      >
        <code {...codeProps}>{children}</code>
      </pre>
    );
  },
};

function normalizeMeeting(response: MeetingResponse): MeetingDetail {
  const id = typeof response.id === "string" ? response.id : String(response.id ?? "");

  const rawValue = response.raw;
  const raw =
    rawValue && typeof rawValue === "object" && !Array.isArray(rawValue)
      ? (rawValue as MeetingRaw)
      : null;

  const placeValue = response.place;
  let place: MeetingPlace | null = null;
  if (placeValue && typeof placeValue === "object" && !Array.isArray(placeValue)) {
    const typed = placeValue as Record<string, unknown>;
    const toStringOrNull = (value: unknown): string | null => {
      if (typeof value === "string") {
        return value;
      }
      if (value === null || value === undefined) {
        return null;
      }
      return String(value);
    };
    const toNumberOrNull = (value: unknown): number | null =>
      typeof value === "number" && Number.isFinite(value) ? value : null;

    place = {
      place_id: toStringOrNull(typed.place_id),
      name: toStringOrNull(typed.name),
      city: toStringOrNull(typed.city),
      country: toStringOrNull(typed.country),
      lat: toNumberOrNull(typed.lat),
      lon: toNumberOrNull(typed.lon),
    };
  }

  return {
    id,
    title: response.title ?? null,
    startDate: response.start_date ?? null,
    endDate: response.end_date ?? null,
    summary: response.summary ?? null,
    people: Array.isArray(response.people) ? response.people.map(String) : [],
    tags: Array.isArray(response.tags) ? response.tags.map(String) : [],
    types: Array.isArray(response.types) ? response.types.map(String) : [],
    raw,
    place,
  };
}

function formatDateTime(value: string | null | undefined): string | undefined {
  if (!value) {
    return undefined;
  }
  const timestamp = Date.parse(value);
  if (Number.isNaN(timestamp)) {
    return value;
  }
  try {
    return new Intl.DateTimeFormat(undefined, {
      dateStyle: "medium",
      timeStyle: "short",
    }).format(new Date(timestamp));
  } catch {
    return new Date(timestamp).toISOString();
  }
}

function formatPlace(place: MeetingPlace): string {
  const parts = [place.name, place.city, place.country].filter(
    (part): part is string => Boolean(part && part.trim())
  );
  return parts.join(", ");
}

function getAttendees(raw: MeetingRaw | null, fallback: string[]): string[] {
  if (raw?.attendees && Array.isArray(raw.attendees) && raw.attendees.length > 0) {
    return raw.attendees.map((attendee) => attendee.trim()).filter(Boolean);
  }
  return fallback;
}

export default function MeetingDetailPage() {
  const params = useParams<{ meetingId: string }>();
  const meetingIdParam = params?.meetingId;
  const encodedMeetingId = Array.isArray(meetingIdParam) ? meetingIdParam[0] : meetingIdParam ?? "";
  const meetingId = encodedMeetingId ? decodeURIComponent(encodedMeetingId) : "";

  const [meeting, setMeeting] = useState<MeetingDetail | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!meetingId) {
      setMeeting(null);
      setError("Missing meeting identifier");
      setIsLoading(false);
      return;
    }

    let isMounted = true;
    setIsLoading(true);
    setError(null);
    setMeeting(null);

    api
      .get<MeetingResponse>(`/meetings/${encodeURIComponent(meetingId)}`)
      .then((response) => {
        if (!isMounted) {
          return;
        }
        setMeeting(normalizeMeeting(response));
      })
      .catch((err) => {
        if (!isMounted) {
          return;
        }
        const message = err instanceof Error ? err.message : "Failed to load meeting";
        setError(message);
        setMeeting(null);
      })
      .finally(() => {
        if (isMounted) {
          setIsLoading(false);
        }
      });

    return () => {
      isMounted = false;
    };
  }, [meetingId]);

  const attendees = meeting ? getAttendees(meeting.raw, meeting.people) : [];
  const attendeeLabel = attendees.length > 0 ? attendees.join(", ") : undefined;
  const scheduledLabel = meeting ? formatDateTime(meeting.startDate) : undefined;
  const endLabel = meeting ? formatDateTime(meeting.endDate) : undefined;
  const meetingLink =
    meeting?.raw?.link && typeof meeting.raw.link === "string" && meeting.raw.link.trim().length > 0
      ? meeting.raw.link
      : undefined;

  const metadataItems: MetadataItem[] = [];
  if (meeting) {
    metadataItems.push({ label: "Meeting ID", value: meeting.id });
    if (scheduledLabel) {
      metadataItems.push({ label: "Scheduled", value: scheduledLabel });
    }
    if (attendeeLabel) {
      metadataItems.push({ label: "Attendees", value: attendeeLabel });
    }
    if (endLabel) {
      metadataItems.push({ label: "Ends", value: endLabel });
    }
    if (meeting.tags.length > 0) {
      metadataItems.push({ label: "Tags", value: meeting.tags.join(", ") });
    }
    if (meeting.types.length > 0) {
      metadataItems.push({ label: "Types", value: meeting.types.join(", ") });
    }
    if (meeting.place) {
      const placeLabel = formatPlace(meeting.place);
      if (placeLabel) {
        metadataItems.push({ label: "Location", value: placeLabel });
      }
    }
    if (meetingLink) {
      metadataItems.push({
        label: "Link",
        value: (
          <a
            href={meetingLink}
            target="_blank"
            rel="noopener noreferrer"
            style={{ color: "#2563eb", textDecoration: "underline" }}
          >
            Open meeting link
          </a>
        ),
      });
    }
  }

  const meetingContent = (() => {
    if (meeting?.summary) {
      return meeting.summary.trim();
    }
    if (meeting && meeting.raw && typeof meeting.raw.content === "string") {
      return meeting.raw.content.trim();
    }
    return "";
  })();

  const title = meeting?.title?.trim() || "Meeting details";

  return (
    <section style={{ display: "grid", gap: "24px" }}>
      <Link
        href="/todos"
        style={{
          color: "#2563eb",
          textDecoration: "underline",
          fontSize: "0.9rem",
          width: "fit-content",
        }}
      >
        ← Back to todos
      </Link>

      {isLoading ? (
        <div
          style={{
            border: "1px solid #e2e8f0",
            borderRadius: "12px",
            padding: "24px",
            background: "#fff",
            color: "#0f172a",
          }}
        >
          Loading meeting…
        </div>
      ) : error ? (
        <div
          role="alert"
          style={{
            border: "1px solid #fca5a5",
            borderRadius: "12px",
            padding: "20px",
            background: "#fee2e2",
            color: "#991b1b",
          }}
        >
          {error}
        </div>
      ) : meeting ? (
        <>
          <header style={{ display: "grid", gap: "8px" }}>
            <h1 style={{ fontSize: "2rem", fontWeight: 600, color: "#0f172a" }}>{title}</h1>
            <p style={{ color: "#475569", maxWidth: "60ch" }}>
              Review the full context, attendees, and notes for this meeting. Raw content is rendered with
              Markdown so you can read transcripts and summaries comfortably.
            </p>
          </header>

          <div
            style={{
              border: "1px solid #e2e2e2",
              borderRadius: "12px",
              padding: "24px",
              background: "#fff",
              boxShadow: "0 4px 12px rgba(15, 23, 42, 0.04)",
              display: "grid",
              gap: "20px",
            }}
          >
            <h2 style={{ fontSize: "1.25rem", fontWeight: 600, margin: 0, color: "#0f172a" }}>
              Overview
            </h2>
            <div
              style={{
                display: "grid",
                gap: "16px",
                gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))",
              }}
            >
              {metadataItems.length === 0 ? (
                <p style={{ color: "#6b7280", margin: 0 }}>No metadata available for this meeting.</p>
              ) : (
                metadataItems.map((item) => (
                  <div key={item.label} style={{ display: "grid", gap: "6px" }}>
                    <span
                      style={{
                        fontSize: "0.75rem",
                        letterSpacing: "0.05em",
                        textTransform: "uppercase",
                        color: "#94a3b8",
                        fontWeight: 600,
                      }}
                    >
                      {item.label}
                    </span>
                    <span style={{ fontSize: "0.95rem", color: "#1f2937" }}>{item.value}</span>
                  </div>
                ))
              )}
            </div>
          </div>

          <div
            style={{
              border: "1px solid #e2e2e2",
              borderRadius: "12px",
              padding: "24px",
              background: "#fff",
              boxShadow: "0 4px 12px rgba(15, 23, 42, 0.04)",
              display: "grid",
              gap: "16px",
            }}
          >
            <h2 style={{ fontSize: "1.25rem", fontWeight: 600, margin: 0, color: "#0f172a" }}>
              Meeting Notes
            </h2>
            {meetingContent ? (
              <ReactMarkdown components={markdownComponents} remarkPlugins={[remarkGfm]}>
                {meetingContent}
              </ReactMarkdown>
            ) : (
              <p style={{ color: "#6b7280", margin: 0 }}>No meeting content available.</p>
            )}
          </div>
        </>
      ) : (
        <div
          role="alert"
          style={{
            border: "1px solid #fca5a5",
            borderRadius: "12px",
            padding: "20px",
            background: "#fee2e2",
            color: "#991b1b",
          }}
        >
          Meeting not found.
        </div>
      )}
    </section>
  );
}

