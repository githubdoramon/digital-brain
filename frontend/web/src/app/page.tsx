'use client';

import Link from "next/link";
import { FormEvent, useState, useRef, useEffect, useCallback } from "react";
import type { ReactNode, HTMLAttributes } from "react";
import { useSession } from "next-auth/react";
import ReactMarkdown from "react-markdown";
import type { Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { api, ask, StreamBundle } from "@/lib/api";
import { EventCommandCard } from "@/components/EventCommandCard";
import { EventClarificationCard } from "@/components/EventClarificationCard";

type Message = {
  id?: number;
  role: "user" | "assistant";
  content: string;
  timestamp: Date;
  metadata?: AssistantMetadata;
};

type ThreadSummary = {
  id: string;
  title: string | null;
  created_at: string;
  updated_at: string;
  last_message_preview?: string | null;
};

type ThreadMessage = {
  message_id: number;
  role: "user" | "assistant";
  content: string;
  metadata?: AssistantMetadata | null;
  created_at: string;
};

type ThreadDetail = ThreadSummary & {
  messages: ThreadMessage[];
};

type AssistantMetadata = {
  command_result?: {
    type: string;
    [key: string]: unknown;
  };
} & Record<string, unknown>;

type EventClarificationData = {
  type: "clarification_needed";
  questions: string[];
  partial_extraction: Record<string, unknown>;
  original_message: string;
  clarification_id?: string;
};

type EventConfirmationData = {
  type: "event_confirmation";
  preview_id: string;
  extracted: {
    title: string;
    summary: string;
    when: string | null;
    where: string | null;
    who: string[];
    documents: string[];
    tags: string[];
    types: string[];
  };
  resolution: {
    contacts: Array<{
      contact_id: string;
      display_name: string;
      query: string;
      confidence: string;
    }>;
    places: Array<{
      place_id: string;
      name: string;
    }>;
    documents: Array<{
      document_id: string;
      title: string;
    }>;
    new_entities: {
      contacts: Array<{
        display_name: string;
        query: string;
      }>;
      places: Array<{
        name: string;
        query: string;
      }>;
      documents: Array<{
        reference: string;
      }>;
    };
  };
  relationship_suggestions?: Array<{
    from_contact_id: string;
    from_display_name: string;
    to_contact_id: string;
    to_display_name: string;
    relationship_type: string;
    reciprocal_type: string;
    confidence: string;
    reasoning: string;
  }>;
  message: string;
};

type EventConfirmationResponse = {
  event_id: string;
  created_contacts?: Array<{ contact_id: string; display_name: string }>;
  created_places?: Array<{ place_id: string; name: string }>;
};

type ChatMode = "quick" | "threads";

type MarkdownCodeProps = HTMLAttributes<HTMLElement> & {
  inline?: boolean;
  children?: ReactNode;
};

function getMarkdownComponents(role: Message["role"]): Components {
  const sharedTextColor = { color: "inherit" };
  const blockSpacing = { margin: "0 0 0.75rem 0" };
  const listIndent = { margin: "0 0 0.75rem 1.2rem", padding: 0 };
  const codeBackground = role === "user" ? "rgba(255, 255, 255, 0.18)" : "rgba(15, 23, 42, 0.08)";
  const preBackground = role === "user" ? "rgba(255, 255, 255, 0.15)" : "rgba(15, 23, 42, 0.06)";

  const codeRenderer = ({ inline, children, ...props }: MarkdownCodeProps) => {
    if (inline) {
      return (
        <code
          style={{
            ...sharedTextColor,
            background: codeBackground,
            padding: "0.15rem 0.35rem",
            borderRadius: "4px",
            fontFamily:
              "var(--font-mono, ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace)",
          }}
          {...props}
        >
          {children}
        </code>
      );
    }

    return (
      <pre
        style={{
          ...sharedTextColor,
          ...blockSpacing,
          background: preBackground,
          padding: "0.85rem",
          borderRadius: "8px",
          overflowX: "auto",
          fontSize: "0.9rem",
        }}
      >
        <code
          style={{
            fontFamily:
              "var(--font-mono, ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace)",
            color: "inherit",
          }}
          {...props}
        >
          {children}
        </code>
      </pre>
    );
  };

  return {
    p: ({ children }) => (
      <p style={{ ...sharedTextColor, ...blockSpacing, lineHeight: 1.55 }}>{children}</p>
    ),
    a: ({ href, children }) => (
      <a
        href={href ?? "#"}
        target="_blank"
        rel="noopener noreferrer"
        style={{ ...sharedTextColor, textDecoration: "underline" }}
      >
        {children}
      </a>
    ),
    ul: ({ children }) => (
      <ul style={{ ...sharedTextColor, ...listIndent, display: "grid", gap: "0.35rem", listStylePosition: "outside" }}>
        {children}
      </ul>
    ),
    ol: ({ children }) => (
      <ol style={{ ...sharedTextColor, ...listIndent, display: "grid", gap: "0.35rem", listStylePosition: "outside" }}>
        {children}
      </ol>
    ),
    li: ({ children }) => (
      <li style={{ ...sharedTextColor, margin: 0, lineHeight: 1.45 }}>{children}</li>
    ),
    blockquote: ({ children }) => (
      <blockquote
        style={{
          ...sharedTextColor,
          ...blockSpacing,
          padding: "0.5rem 0.75rem",
          borderLeft: `4px solid ${role === "user" ? "rgba(255,255,255,0.4)" : "#cbd5f5"}`,
          background: role === "user" ? "rgba(255,255,255,0.12)" : "rgba(15, 23, 42, 0.04)",
          borderRadius: "6px",
        }}
      >
        {children}
      </blockquote>
    ),
    code: codeRenderer,
  } satisfies Components;
}

export default function Home() {
  const { data: session } = useSession();
  
  // Check if user has access to agent chat

  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [threads, setThreads] = useState<ThreadSummary[]>([]);
  const [selectedThreadId, setSelectedThreadId] = useState<string | null>(null);
  const [isLoadingThreads, setIsLoadingThreads] = useState(false);
  const [isLoadingMessages, setIsLoadingMessages] = useState(false);
  const [streamingContent, setStreamingContent] = useState<string>("");
  const [streamingStatus, setStreamingStatus] = useState<string>("");
  const [pendingEventId, setPendingEventId] = useState<string | null>(null);
  const messagesContainerRef = useRef<HTMLDivElement>(null);
  const lastMessageRef = useRef<HTMLDivElement>(null);

  // Quick Chat mode state
  const [chatMode, setChatMode] = useState<ChatMode>("quick");
  const [quickChatMessages, setQuickChatMessages] = useState<Message[]>([]);

  useEffect(() => {
    setPendingEventId(null);
  }, [chatMode, selectedThreadId]);

  const displayMessages = chatMode === "quick" ? quickChatMessages : messages;

  const scrollToLatestMessage = useCallback(() => {
    const container = messagesContainerRef.current;
    if (!container) return;

    const lastMessage = lastMessageRef.current;
    const containerHeight = container.clientHeight;
    if (!containerHeight) return;

    const padding = containerHeight * 0.1;
    let target = container.scrollHeight - containerHeight;

    if (lastMessage) {
      const messageHeight = lastMessage.offsetHeight;
      if (messageHeight > containerHeight) {
        target = Math.max(0, lastMessage.offsetTop - padding);
      }
    }

    requestAnimationFrame(() => {
      container.scrollTo({ top: target, behavior: "smooth" });
    });
  }, [displayMessages.length, streamingContent, isLoading]);

  const refreshThreads = useCallback(async () => {
    setIsLoadingThreads(true);
    try {
      const data = await api.get<ThreadSummary[]>("/threads");
      setThreads(data);
      return data;
    } catch (error) {
      console.error("Failed to fetch conversation threads", error);
      return [];
    } finally {
      setIsLoadingThreads(false);
    }
  }, []);

  const loadThread = useCallback(async (threadId: string) => {
    if (!threadId) return;
    setIsLoadingMessages(true);
    try {
      const thread = await api.get<ThreadDetail>(`/threads/${threadId}`);
      setSelectedThreadId(thread.id);
      const mappedMessages = thread.messages.map((message) => {
        const metadata = (message.metadata ?? undefined) as AssistantMetadata | undefined;
        return {
          id: message.message_id,
          role: message.role,
          content: message.content,
          timestamp: new Date(message.created_at),
          metadata,
        } satisfies Message;
      });
      setMessages(mappedMessages);
    } catch (error) {
      console.error("Failed to load conversation thread", error);
      setMessages([
        {
          role: "assistant",
          content: "Failed to load conversation history.",
          timestamp: new Date(),
        },
      ]);
    } finally {
      setIsLoadingMessages(false);
    }
  }, []);

  const deleteThread = useCallback(
    async (threadId: string) => {
      if (!threadId) return;
      if (!window.confirm("Delete this conversation? This action cannot be undone.")) {
        return;
      }
      setIsLoadingMessages(true);
      try {
        await api.delete(`/threads/${threadId}`);
        const updated = threads.filter((thread) => thread.id !== threadId);
        setThreads(updated);
        if (threadId === selectedThreadId) {
          if (updated.length > 0) {
            await loadThread(updated[0].id);
          } else {
            setSelectedThreadId(null);
            setMessages([]);
                }
        }
      } catch (error) {
        console.error("Failed to delete conversation thread", error);
      } finally {
        setIsLoadingMessages(false);
      }
    },
    [threads, selectedThreadId, loadThread]
  );

  // Removed: handleInsertEvent - old event proposal system removed

  useEffect(() => {
    scrollToLatestMessage();
  }, [scrollToLatestMessage]);

  useEffect(() => {
    let isMounted = true;
    (async () => {
      const fetched = await refreshThreads();
      if (!isMounted) {
        return;
      }
      if (fetched.length > 0) {
        await loadThread(fetched[0].id);
      } else {
        setMessages([]);
        setSelectedThreadId(null);
        }
    })();
    return () => {
      isMounted = false;
    };
  }, [refreshThreads, loadThread]);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!input.trim() || isLoading) return;

    const pendingInput = input.trim();

    setInput("");
    setIsLoading(true);
    setStreamingContent("");
    setStreamingStatus("");

    if (chatMode === "quick") {
      // Quick Chat mode - no explicit thread_id, let backend handle main session
      try {
        const userMessage: Message = {
          role: "user",
          content: pendingInput,
          timestamp: new Date(),
        };

        setQuickChatMessages((prev) => [...prev, userMessage]);

        // Use non-streaming endpoint
        const data: StreamBundle = await ask(pendingInput, {
          threadId: undefined, // Backend resolves main session
          limit: 5,
          pendingEventId,
        });

        const sessionIsNew = data.is_new_session ?? false;

        // Handle command results
        const metadata: AssistantMetadata | undefined = data.command_result
          ? { command_result: data.command_result }
          : undefined;

        const assistantMessage: Message = {
          id: undefined,
          role: "assistant",
          content: data.answer || "I couldn't generate a response.",
          timestamp: new Date(),
          metadata,
        };

        if (data.pending_event_id !== undefined) {
          setPendingEventId(data.pending_event_id ?? null);
        }

        // If new session, clear previous messages and start fresh
        if (sessionIsNew) {
          setQuickChatMessages([userMessage, assistantMessage]);
        } else {
          setQuickChatMessages((prev) => [...prev, assistantMessage]);
        }

        // Refresh threads list to show the quick chat thread
        await refreshThreads();
      } catch (error) {
        const errorMessage: Message = {
          role: "assistant",
          content: `Error: ${error instanceof Error ? error.message : "Unexpected error occurred"}`,
          timestamp: new Date(),
        };
        setQuickChatMessages((prev) => [...prev, errorMessage]);
      } finally {
        setIsLoading(false);
      }
    } else {
      // Threads mode - existing behavior
      try {
        let threadId = selectedThreadId;
        if (!threadId) {
          const created = await api.post<ThreadSummary>("/threads", {});
          threadId = created.id;
          setThreads((prev) => [created, ...prev]);
          setSelectedThreadId(threadId);
              setMessages([]);
        }

        const userMessage: Message = {
          role: "user",
          content: pendingInput,
          timestamp: new Date(),
        };

        setMessages((prev) => [...prev, userMessage]);

        // Use non-streaming endpoint
        const data: StreamBundle = await ask(pendingInput, {
          threadId,
          limit: 5,
          pendingEventId,
        });

        if (data.thread_id && data.thread_id !== threadId) {
          threadId = data.thread_id;
          setSelectedThreadId(threadId);
        }

        if (threadId && data.thread_title) {
          const updatedTitle = data.thread_title.trim();
          if (updatedTitle.length > 0) {
            const resolvedThreadId = threadId;
            setThreads((prev) =>
              prev.map((thread) =>
                thread.id === resolvedThreadId
                  ? { ...thread, title: updatedTitle, updated_at: new Date().toISOString() }
                  : thread
              )
            );
          }
        }

        const metadata: AssistantMetadata | undefined = data.command_result
          ? { command_result: data.command_result }
          : undefined;

        const assistantMessage: Message = {
          id: undefined,
          role: "assistant",
          content: data.answer || "I couldn't generate a response.",
          timestamp: new Date(),
          metadata,
        };

        if (data.pending_event_id !== undefined) {
          setPendingEventId(data.pending_event_id ?? null);
        }

        setMessages((prev) => [...prev, assistantMessage]);
        await refreshThreads();
      } catch (error) {
        const errorMessage: Message = {
          role: "assistant",
          content: `Error: ${error instanceof Error ? error.message : "Unexpected error occurred"}`,
          timestamp: new Date(),
        };
        setMessages((prev) => [...prev, errorMessage]);
      } finally {
        setIsLoading(false);
      }
    }
  }

  return (
    <section style={{ display: "grid", gridTemplateColumns: chatMode === "threads" ? "280px 1fr" : "1fr", gap: "16px", alignItems: "start" }}>
      {chatMode === "threads" && (
      <aside
        style={{
          border: "1px solid #e2e2e2",
          borderRadius: "12px",
          padding: "16px",
          background: "#fff",
          height: "100%",
          display: "flex",
          flexDirection: "column",
          gap: "12px",
          maxHeight: "720px",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <h2 style={{ fontSize: "1rem", fontWeight: 600, margin: 0 }}>Conversations</h2>
          <button
            onClick={async () => {
              setIsLoadingMessages(true);
              try {
                const created = await api.post<ThreadSummary>("/threads", {});
                setThreads((prev) => [created, ...prev]);
                setSelectedThreadId(created.id);
                          setMessages([]);
              } catch (error) {
                console.error("Failed to create thread", error);
              } finally {
                setIsLoadingMessages(false);
              }
            }}
            style={{
              border: "1px solid #0b6bcb",
              background: "#0b6bcb",
              color: "#fff",
              borderRadius: "6px",
              padding: "4px 8px",
              fontSize: "0.8rem",
              cursor: "pointer",
            }}
          >
            + New Chat
          </button>
        </div>
        <div style={{ flex: 1, overflowY: "auto", display: "flex", flexDirection: "column", gap: "6px" }}>
          {isLoadingThreads && (
            <div style={{ color: "#666", fontSize: "0.9rem" }}>Loading conversations...</div>
          )}
          {!isLoadingThreads && threads.length === 0 && (
            <div style={{ color: "#777", fontSize: "0.9rem" }}>
              No conversations yet. Start a new chat to begin.
            </div>
          )}
          {threads.map((thread) => {
            const isActive = thread.id === selectedThreadId;
            return (
              <div
                key={thread.id}
                onClick={() => {
                  if (!isActive) {
                    loadThread(thread.id);
                  }
                }}
                style={{
                  border: "1px solid",
                  borderColor: isActive ? "#0b6bcb" : "#e2e2e2",
                  background: isActive ? "#e0f2fe" : "#fafafa",
                  borderRadius: "8px",
                  padding: "10px 12px",
                  cursor: "pointer",
                  display: "grid",
                  gap: "6px",
                }}
              >
                <div style={{ display: "flex", justifyContent: "space-between", gap: "8px" }}>
                  <span
                    style={{
                      fontWeight: 600,
                      fontSize: "0.9rem",
                      color: "#0f1728",
                      flex: 1,
                      minWidth: 0,
                    }}
                  >
                    {thread.title?.trim() || "Untitled conversation"}
                  </span>
                  <button
                    onClick={(event) => {
                      event.stopPropagation();
                      deleteThread(thread.id);
                    }}
                    style={{
                      border: "none",
                      background: "transparent",
                      color: "#ef4444",
                      fontSize: "0.75rem",
                      cursor: "pointer",
                      padding: "2px 4px",
                    }}
                    title="Delete conversation"
                  >
                    Delete
                  </button>
                </div>
                <span style={{ fontSize: "0.75rem", color: "#6b7280" }}>
                  {thread.last_message_preview || "No messages yet"}
                </span>
                <span style={{ fontSize: "0.7rem", color: "#94a3b8" }}>
                  Updated{" "}
                  {new Date(thread.updated_at).toLocaleString([], {
                    month: "short",
                    day: "numeric",
                    hour: "2-digit",
                    minute: "2-digit",
                  })}
                </span>
              </div>
            );
          })}
        </div>
      </aside>
      )}
      <div style={{ display: "grid", gap: "16px" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
          <div>
            <h1 style={{ fontSize: "2rem", fontWeight: 600 }}>
              Welcome{session?.user?.name ? `, ${session.user.name.split(" ")[0]}` : ""}!
            </h1>
            <p style={{ color: "#555", marginTop: "8px" }}>
              Ask questions about your personal data and get AI-powered insights.
            </p>
          </div>
          <div style={{ display: "flex", gap: "4px", background: "#f1f5f9", borderRadius: "8px", padding: "4px" }}>
            <button
              onClick={() => setChatMode("quick")}
              style={{
                padding: "8px 16px",
                borderRadius: "6px",
                border: "none",
                background: chatMode === "quick" ? "#fff" : "transparent",
                color: chatMode === "quick" ? "#0b6bcb" : "#64748b",
                fontWeight: chatMode === "quick" ? 600 : 400,
                cursor:"pointer",
                boxShadow: chatMode === "quick" ? "0 1px 3px rgba(0,0,0,0.1)" : "none",
                opacity: 1,
              }}
            >
              Quick Chat
            </button>
            <button
              onClick={() => setChatMode("threads")}
              style={{
                padding: "8px 16px",
                borderRadius: "6px",
                border: "none",
                background: chatMode === "threads" ? "#fff" : "transparent",
                color: chatMode === "threads" ? "#0b6bcb" : "#64748b",
                fontWeight: chatMode === "threads" ? 600 : 400,
                cursor:"pointer",
                boxShadow: chatMode === "threads" ? "0 1px 3px rgba(0,0,0,0.1)" : "none",
                opacity:  1,
              }}
            >
              Threads
            </button>
          </div>
        </div>
        <div
          style={{
            border: "1px solid #e2e2e2",
            borderRadius: "12px",
            background: "#fff",
            boxShadow: "0 4px 12px rgba(15, 23, 42, 0.04)",
            display: "grid",
            gridTemplateRows: "auto 1fr auto",
            height: "600px",
          }}
        >
          <div
            style={{
              padding: "20px 24px",
              borderBottom: "1px solid #e2e2e2",
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
            }}
          >
            <div>
              <h2 style={{ fontSize: "1.25rem", fontWeight: 600 }}>
                Chat with your Digital Brain
              </h2>
              <p style={{ fontSize: "0.875rem", color: "#666", marginTop: "4px" }}>
                Ask about your contacts, meetings, documents, and more
              </p>
            </div>
          </div>
          <div
            ref={messagesContainerRef}
            style={{
              padding: "24px",
              overflowY: "auto",
              display: "flex",
              flexDirection: "column",
              gap: "16px",
            }}
          >
            {isLoadingMessages && (
              <div style={{ color: "#666", fontSize: "0.9rem" }}>Loading conversation...</div>
            )}

            {!isLoadingMessages && displayMessages.length === 0 && (
              <div
                style={{
                  display: "flex",
                  flexDirection: "column",
                  alignItems: "center",
                  justifyContent: "center",
                  height: "100%",
                  color: "#999",
                  gap: "12px",
                  textAlign: "center",
                }}
              >
                <div style={{ fontSize: "2.5rem" }}>💬</div>
                  <p style={{ fontSize: "0.95rem" }}>
                    Start a conversation by asking a question below
                  </p>
                  <div style={{ fontSize: "0.85rem", color: "#aaa", maxWidth: "400px" }}>
                    Examples: &quot;What meetings did I have last week?&quot; or &quot;Tell me about my conversations with Monica&quot;
                  </div>
              </div>
            )}

            {displayMessages.map((message, index) => {
              const metadata = message.metadata as AssistantMetadata | undefined;
              const commandResult = metadata?.command_result;
              const isLastMessage = index === displayMessages.length - 1 && !isLoading;
              return (
                <div
                  key={message.id ?? index}
                  ref={isLastMessage ? lastMessageRef : undefined}
                  style={{
                    display: "flex",
                    flexDirection: "column",
                    alignItems: message.role === "user" ? "flex-end" : "flex-start",
                    gap: "6px",
                  }}
                >
                  <div
                    style={{
                      maxWidth: "80%",
                      padding: "12px 16px",
                      borderRadius: "12px",
                      background: message.role === "user" ? "#0b6bcb" : "#f5f5f5",
                      color: message.role === "user" ? "#fff" : "#333",
                      wordWrap: "break-word",
                      overflowWrap: "anywhere",
                      display: "flex",
                      flexDirection: "column",
                      gap: "0.5rem",
                    }}
                  >
                    <ReactMarkdown
                      remarkPlugins={[remarkGfm]}
                      components={getMarkdownComponents(message.role)}
                    >
                      {message.content}
                    </ReactMarkdown>
                  </div>
                  {commandResult && commandResult.type === "clarification_needed" && (
                    <div style={{ maxWidth: "80%", alignSelf: "stretch" }}>
                      <EventClarificationCard
                        clarificationData={commandResult as EventClarificationData}
                        onSubmit={async (answers) => {
                          // Re-submit with additional information
                          const originalMessage = (commandResult as EventClarificationData).original_message || "";
                          const clarificationId = (commandResult as EventClarificationData).clarification_id;
                          const clarificationToken = clarificationId
                            ? `\n\n[clarification_id:${clarificationId}]`
                            : "";
                          const combinedMessage = `/event ${originalMessage}\n\nAdditional details: ${answers}${clarificationToken}`;
                          setInput(combinedMessage);
                          // Trigger form submit
                          const form = document.querySelector('form');
                          if (form) form.requestSubmit();
                        }}
                        onCancel={() => {
                          setPendingEventId(null);
                        }}
                      />
                    </div>
                  )}
                  {commandResult && commandResult.type === "event_confirmation" && (
                    <div style={{ maxWidth: "80%", alignSelf: "stretch" }}>
                      <EventCommandCard
                        commandData={commandResult as EventConfirmationData}
                        onConfirm={async (previewId, modifications) => {
                          try {
                            const result = await api.post<EventConfirmationResponse>("/commands/event/confirm", {
                              preview_id: previewId,
                              confirmed: true,
                              modifications: modifications || {},
                              skip_entities: {},
                            });

                            // Show success message
                            const eventId = result.event_id;
                            const createdCount =
                              (result.created_contacts?.length || 0) +
                              (result.created_places?.length || 0);

                            // Add success message to chat
                            const successMessage: Message = {
                              role: "assistant",
                              content: `✓ Event created successfully! ${createdCount > 0 ? `Created ${createdCount} new entities.` : ""}\n\nEvent ID: ${eventId}`,
                              timestamp: new Date(),
                            };

                            if (chatMode === "quick") {
                              setQuickChatMessages((prev) => [...prev, successMessage]);
                            }

                            setPendingEventId(null);

                            // Refresh threads list
                            await refreshThreads();
                          } catch (error: unknown) {
                            console.error("Failed to create event:", error);
                            const errorMessage = error instanceof Error ? error.message : "Unknown error";
                            alert(`Failed to create event: ${errorMessage}`);
                          }
                        }}
                        onCancel={async (previewId) => {
                          try {
                            await api.post("/commands/event/confirm", {
                              preview_id: previewId,
                              confirmed: false,
                            });
                          } catch (error) {
                            console.error("Failed to cancel event:", error);
                          } finally {
                            setPendingEventId(null);
                          }
                        }}
                      />
                    </div>
                  )}
                  <div
                    style={{
                      fontSize: "0.75rem",
                      color: "#999",
                      marginTop: "4px",
                      paddingLeft: message.role === "user" ? "0" : "8px",
                      paddingRight: message.role === "user" ? "8px" : "0",
                    }}
                  >
                    {message.timestamp.toLocaleTimeString([], {
                      hour: "2-digit",
                      minute: "2-digit",
                    })}
                  </div>
                </div>
              );
            })}

            {isLoading && (
              <div
                ref={lastMessageRef}
                style={{
                  display: "flex",
                  flexDirection: "column",
                  alignItems: "flex-start",
                  gap: "8px",
                }}
              >
                {streamingStatus && (
                  <div
                    style={{
                      padding: "6px 12px",
                      borderRadius: "8px",
                      background: "#e0f2fe",
                      color: "#0369a1",
                      fontSize: "0.85rem",
                      display: "flex",
                      alignItems: "center",
                      gap: "6px",
                    }}
                  >
                    <span style={{ animation: "pulse 1.5s infinite" }}>●</span>
                    {streamingStatus}
                  </div>
                )}
                <div
                  style={{
                    maxWidth: "80%",
                    padding: "12px 16px",
                    borderRadius: "12px",
                    background: "#f5f5f5",
                    color: "#333",
                    wordWrap: "break-word",
                    overflowWrap: "anywhere",
                    display: "flex",
                    flexDirection: "column",
                    gap: "0.5rem",
                  }}
                >
                  {streamingContent ? (
                    <ReactMarkdown
                      remarkPlugins={[remarkGfm]}
                      components={getMarkdownComponents("assistant")}
                    >
                      {streamingContent}
                    </ReactMarkdown>
                  ) : (
                    <div style={{ display: "flex", gap: "4px", alignItems: "center", color: "#666" }}>
                      <span>Thinking</span>
                      <span className="loading-dots">...</span>
                    </div>
                  )}
                </div>
              </div>
            )}

          </div>
          <form
            onSubmit={handleSubmit}
            style={{
              padding: "20px 24px",
              borderTop: "1px solid #e2e2e2",
              background: "#fafafa",
              borderRadius: "0 0 12px 12px",
            }}
          >
            <div style={{ display: "flex", gap: "12px" }}>
              <input
                type="text"
                value={input}
                onChange={(e) => setInput(e.target.value)}
                placeholder="Ask a question about your data..."
                disabled={isLoading}
                style={{
                  flex: 1,
                  border: "1px solid #d0d0d0",
                  borderRadius: "8px",
                  padding: "12px 16px",
                  fontSize: "0.95rem",
                  outline: "none",
                  background: "#fff",
                  cursor: "text",
                }}
              />
              <button
                type="submit"
                disabled={isLoading || !input.trim()}
                style={{
                  background: "#0b6bcb",
                  color: "#fff",
                  border: "none",
                  borderRadius: "8px",
                  padding: "12px 24px",
                  fontWeight: 600,
                  cursor: (isLoading || !input.trim()) ? "not-allowed" : "pointer",
                  opacity: (isLoading || !input.trim()) ? 0.6 : 1,
                  whiteSpace: "nowrap",
                }}
              >
                {isLoading ? "Sending..." : "Send"}
              </button>
            </div>
          </form>
        </div>

        <div
          style={{
            border: "1px solid #e2e2e2",
            borderRadius: "12px",
            padding: "24px",
            background: "#fff",
          }}
        >
          <h2 style={{ fontSize: "1.25rem", marginBottom: "8px" }}>
            Quick Links
          </h2>
          <ul style={{ listStyle: "disc", paddingInlineStart: "20px", color: "#444" }}>
            <li>
              Manage your personal contacts through the{" "}
              <Link href="/contacts" style={{ color: "#0b6bcb" }}>
                Contacts page
              </Link>
              .
            </li>
            <li>
              Import new meeting transcripts through the{" "}
              <Link href="/meetings" style={{ color: "#0b6bcb" }}>
                Meetings page
              </Link>
              .
            </li>
            <li>
              Ask questions using the chat interface above.
            </li>
          </ul>
        </div>
      </div>
    </section>
  );
}
