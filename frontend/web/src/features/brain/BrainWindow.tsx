"use client";

import Link from "next/link";
import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { useSession } from "next-auth/react";

import {
  api,
  askWithStreaming,
  primeClientContext,
  type StreamBundle,
  type UiSubmissionInput,
} from "@/lib/api";

import { EventDraftEditor } from "./EventDraftEditor";
import {
  applyEventDraftModifications,
  buildEventDraft,
  type EventDraftModifications,
  updateEventPreviewDirectives,
} from "./eventDraft";
import { LinkedItemsRow } from "./linkedItems";
import { AssistantMarkdown } from "./markdown";
import { SlashCommandPalette } from "./SlashCommandPalette";
import { buildToolProgressChip } from "./streamingProgress";
import type { AssistantMetadata, ChatMode, Message, ThreadDetail, ThreadSummary } from "./types";
import { UiDirectiveCard } from "./UiDirectiveCard";

const EVENT_CONFIRM_ACTION_ID = "event_confirmation_action";
const CONTACT_CONFIRM_ACTION_ID = "contact_confirmation_action";
const EVENT_CONFIRM_OPTION_PREFIX = "confirm:";
const EVENT_CANCEL_OPTION_PREFIX = "cancel:";
const EVENT_EDIT_OPTION_PREFIX = "edit:";
const CONTACT_CONFIRM_OPTION_PREFIX = "confirm:";
const CONTACT_CANCEL_OPTION_PREFIX = "cancel:";
const CONTACT_EDIT_OPTION_PREFIX = "edit:";

type CommandAction = {
  type: "confirm" | "cancel" | "edit";
  previewId: string;
};

type EventConfirmResult = {
  success: boolean;
  event_id?: string | null;
  operation?: string | null;
  error?: string | null;
};

type ContactConfirmResult = {
  success: boolean;
  error?: string | null;
};

type ActiveDraftEditor = {
  kind: "event";
  messageId: string | number;
  previewId: string;
};

function buildAssistantMetadata(data: StreamBundle, progressChip?: string): AssistantMetadata | undefined {
  const metadata: AssistantMetadata = {};
  if (data.command_result) metadata.command_result = data.command_result;
  if (data.ui_directives) metadata.ui_directives = data.ui_directives;
  if (data.linked_items && data.linked_items.length > 0) metadata.linked_items = data.linked_items;
  if (progressChip) metadata.progress_chip = progressChip;
  return Object.keys(metadata).length > 0 ? metadata : undefined;
}

function assistantContentFromBundle(data: StreamBundle): string {
  return (
    data.answer ||
    data.ui_directives?.fallback_text ||
    (data.command_result ? "Command completed." : "Ready when you are.")
  );
}

function timestampLabel(value: Date): string {
  return value.toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
  });
}

function parseCommandAction(optionIdRaw: unknown, prefixes: {
  confirm: string;
  cancel: string;
  edit: string;
}): CommandAction | null {
  if (typeof optionIdRaw !== "string") return null;
  const optionId = optionIdRaw.trim();
  if (optionId.startsWith(prefixes.confirm)) {
    const previewId = optionId.slice(prefixes.confirm.length).trim();
    return previewId ? { type: "confirm", previewId } : null;
  }
  if (optionId.startsWith(prefixes.cancel)) {
    const previewId = optionId.slice(prefixes.cancel.length).trim();
    return previewId ? { type: "cancel", previewId } : null;
  }
  if (optionId.startsWith(prefixes.edit)) {
    const previewId = optionId.slice(prefixes.edit.length).trim();
    return previewId ? { type: "edit", previewId } : null;
  }
  return null;
}

export function BrainWindow() {
  const { data: session } = useSession();
  const [messages, setMessages] = useState<Message[]>([]);
  const [quickChatMessages, setQuickChatMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [threads, setThreads] = useState<ThreadSummary[]>([]);
  const [selectedThreadId, setSelectedThreadId] = useState<string | null>(null);
  const [isLoadingThreads, setIsLoadingThreads] = useState(false);
  const [isLoadingMessages, setIsLoadingMessages] = useState(false);
  const [pendingEventId, setPendingEventId] = useState<string | null>(null);
  const [chatMode, setChatMode] = useState<ChatMode>("quick");
  const [activeDirectiveMessageId, setActiveDirectiveMessageId] = useState<string | number | null>(null);
  const [activeDraftEditor, setActiveDraftEditor] = useState<ActiveDraftEditor | null>(null);
  const [eventDraftModificationsByPreview, setEventDraftModificationsByPreview] = useState<
    Record<string, EventDraftModifications>
  >({});
  const messagesContainerRef = useRef<HTMLDivElement>(null);
  const lastMessageRef = useRef<HTMLDivElement>(null);
  const [isAtBottom, setIsAtBottom] = useState(true);
  const [forceScrollNext, setForceScrollNext] = useState(false);

  const displayMessages = chatMode === "quick" ? quickChatMessages : messages;
  const latestMessageContent = displayMessages.at(-1)?.content;
  const slashMatch = input.match(/^\/([a-z]*)$/i);
  const showSlashPalette = Boolean(slashMatch) && !isLoading;

  useEffect(() => {
    primeClientContext();
  }, []);

  useEffect(() => {
    setPendingEventId(null);
    setActiveDraftEditor(null);
  }, [chatMode, selectedThreadId]);

  const scrollToLatestMessage = useCallback(() => {
    const container = messagesContainerRef.current;
    if (!container || (!isAtBottom && !forceScrollNext)) return;

    requestAnimationFrame(() => {
      container.scrollTo({ top: container.scrollHeight, behavior: "smooth" });
    });
    if (forceScrollNext) setForceScrollNext(false);
  }, [forceScrollNext, isAtBottom]);

  useEffect(() => {
    scrollToLatestMessage();
  }, [displayMessages.length, isLoading, latestMessageContent, scrollToLatestMessage]);

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
      setMessages(
        thread.messages.map((message) => ({
          id: message.message_id,
          role: message.role,
          content: message.content,
          timestamp: new Date(message.created_at),
          metadata: (message.metadata ?? undefined) as AssistantMetadata | undefined,
        })),
      );
    } catch (error) {
      console.error("Failed to load conversation thread", error);
      setMessages([
        {
          id: `load-error-${Date.now()}`,
          role: "assistant",
          content: "Failed to load conversation history.",
          timestamp: new Date(),
        },
      ]);
    } finally {
      setIsLoadingMessages(false);
    }
  }, []);

  useEffect(() => {
    let isMounted = true;
    (async () => {
      const fetched = await refreshThreads();
      if (!isMounted) return;
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
  }, [loadThread, refreshThreads]);

  const deleteThread = useCallback(
    async (threadId: string) => {
      if (!threadId || !window.confirm("Delete this conversation? This action cannot be undone.")) return;
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
    [loadThread, selectedThreadId, threads],
  );

  const setTargetMessages = useCallback(
    (targetMode: ChatMode, updater: (prev: Message[]) => Message[]) => {
      if (targetMode === "quick") {
        setQuickChatMessages(updater);
      } else {
        setMessages(updater);
      }
    },
    [],
  );

  const updateThreadTitle = useCallback((threadId: string | null, title: string | null | undefined) => {
    const updatedTitle = title?.trim();
    if (!threadId || !updatedTitle) return;
    setThreads((prev) =>
      prev.map((thread) =>
        thread.id === threadId
          ? { ...thread, title: updatedTitle, updated_at: new Date().toISOString() }
          : thread,
      ),
    );
  }, []);

  const updateMessageMetadata = useCallback(
    (messageId: string | number, metadata: Partial<AssistantMetadata>) => {
      setTargetMessages(chatMode, (prev) =>
        prev.map((message) =>
          message.id === messageId
            ? {
                ...message,
                metadata: {
                  ...message.metadata,
                  ...metadata,
                },
              }
            : message,
        ),
      );
    },
    [chatMode, setTargetMessages],
  );

  const submitMessage = useCallback(
    async (options?: { text?: string; uiSubmission?: UiSubmissionInput }) => {
      if (isLoading) return;
      const targetMode = chatMode;
      const outboundText =
        (options?.text ?? input).trim() ||
        options?.uiSubmission?.text_fallback?.trim() ||
        "Submitted structured response.";
      if (!outboundText) return;

      setForceScrollNext(true);
      setInput("");
      setIsLoading(true);

      let threadId = targetMode === "threads" ? selectedThreadId : null;
      if (targetMode === "threads" && !threadId) {
        try {
          const created = await api.post<ThreadSummary>("/threads", {});
          threadId = created.id;
          setThreads((prev) => [created, ...prev]);
          setSelectedThreadId(threadId);
          setMessages([]);
        } catch (error) {
          console.error("Failed to create thread", error);
          setIsLoading(false);
          return;
        }
      }

      const now = Date.now();
      const pendingId = `assistant-pending-${now}`;
      const userMessage: Message = {
        id: `user-${now}`,
        role: "user",
        content: outboundText,
        timestamp: new Date(),
      };
      const pendingMessage: Message = {
        id: pendingId,
        role: "assistant",
        content: "Thinking...",
        timestamp: new Date(),
        pending: true,
      };
      setTargetMessages(targetMode, (prev) => [...prev, userMessage, pendingMessage]);

      const updatePendingMessage = (changes: Partial<Message>) => {
        setTargetMessages(targetMode, (prev) =>
          prev.map((message) =>
            message.id === pendingId
              ? {
                  ...message,
                  ...changes,
                  metadata: {
                    ...message.metadata,
                    ...changes.metadata,
                  },
                }
              : message,
          ),
        );
      };

      let lastProgressChip = "";
      let latestThreadId = threadId;

      try {
        const data = await askWithStreaming(
          outboundText,
          {
            threadId: targetMode === "quick" ? undefined : threadId,
            limit: 30,
            pendingEventId,
            uiSubmission: options?.uiSubmission,
          },
          {
            onSessionInfo: (streamThreadId) => {
              latestThreadId = streamThreadId || latestThreadId;
              if (targetMode === "threads" && streamThreadId) {
                setSelectedThreadId(streamThreadId);
              }
            },
            onStatus: (message) => {
              const status = message.trim();
              if (!status) return;
              lastProgressChip = status;
              updatePendingMessage({
                metadata: { progress_chip: status },
              });
            },
            onToolCall: (name, args) => {
              const chip = buildToolProgressChip(name, args);
              if (!chip) return;
              lastProgressChip = chip;
              updatePendingMessage({
                metadata: { progress_chip: chip },
              });
            },
            onToken: (_delta, fullContent) => {
              updatePendingMessage({ content: fullContent || "Thinking..." });
            },
            onClearContent: () => {
              updatePendingMessage({ content: "Thinking..." });
            },
            onError: (message) => {
              updatePendingMessage({
                content: message || "The stream returned an error.",
                metadata: { request_error: message || "The stream returned an error." },
              });
            },
          },
        );

        if (data.thread_id) {
          latestThreadId = data.thread_id;
          if (targetMode === "threads") setSelectedThreadId(data.thread_id);
        }
        updateThreadTitle(latestThreadId, data.thread_title);

        if (data.pending_event_id !== undefined) {
          setPendingEventId(data.pending_event_id ?? null);
        }

        const metadata = buildAssistantMetadata(data, lastProgressChip);
        const finalMessage: Partial<Message> = {
          content: assistantContentFromBundle(data),
          pending: false,
          metadata,
        };

        if (targetMode === "quick" && data.is_new_session) {
          setQuickChatMessages([
            userMessage,
            {
              ...pendingMessage,
              ...finalMessage,
              metadata,
            },
          ]);
        } else {
          updatePendingMessage(finalMessage);
        }

        await refreshThreads();
      } catch (error) {
        const message = error instanceof Error ? error.message : "Unexpected error occurred";
        updatePendingMessage({
          content: `Error: ${message}`,
          pending: false,
          metadata: { request_error: message },
        });
      } finally {
        setIsLoading(false);
      }
    },
    [
      chatMode,
      input,
      isLoading,
      pendingEventId,
      refreshThreads,
      selectedThreadId,
      setTargetMessages,
      updateThreadTitle,
    ],
  );

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    void submitMessage();
  };

  const handleDirectiveSubmit = async (message: Message, submission: UiSubmissionInput) => {
    if (submission.action_id === EVENT_CONFIRM_ACTION_ID) {
      const action = parseCommandAction(submission.values?.option_id, {
        confirm: EVENT_CONFIRM_OPTION_PREFIX,
        cancel: EVENT_CANCEL_OPTION_PREFIX,
        edit: EVENT_EDIT_OPTION_PREFIX,
      });
      if (!action || activeDirectiveMessageId) return;

      if (action.type === "edit") {
        const baseDraft = buildEventDraft(message.metadata?.command_result, action.previewId);
        if (!baseDraft) {
          updateMessageMetadata(message.id, {
            request_error: "I could not load that event draft for editing. Please retry from the latest preview.",
          });
          return;
        }
        setActiveDraftEditor({ kind: "event", messageId: message.id, previewId: action.previewId });
        return;
      }

      if (pendingEventId && action.previewId !== pendingEventId) {
        window.alert("That draft is no longer active. Use the newest event preview card.");
        return;
      }

      setActiveDirectiveMessageId(message.id);
      try {
        const result = await api.post<EventConfirmResult>("/commands/event/confirm", {
          preview_id: action.previewId,
          confirmed: action.type === "confirm",
          modifications: eventDraftModificationsByPreview[action.previewId] || {},
          skip_entities: {},
        });
        if (!result.success) {
          throw new Error(result.error || "Event action failed");
        }
        const status =
          action.type !== "confirm" ? "cancelled" : result.operation === "update" ? "updated" : "created";
        updateMessageMetadata(message.id, {
          command_resolved: {
            status,
            label:
              status === "created"
                ? "Event created"
                : status === "updated"
                  ? "Event updated"
                  : "Event cancelled",
          },
        });
        setEventDraftModificationsByPreview((prev) => {
          if (!prev[action.previewId]) return prev;
          const next = { ...prev };
          delete next[action.previewId];
          return next;
        });
        if (activeDraftEditor?.previewId === action.previewId) {
          setActiveDraftEditor(null);
        }
        setPendingEventId(null);
        await refreshThreads();
      } catch (error) {
        const detail = error instanceof Error ? error.message : "I could not complete that event action.";
        updateMessageMetadata(message.id, { request_error: detail });
      } finally {
        setActiveDirectiveMessageId(null);
      }
      return;
    }

    if (submission.action_id === CONTACT_CONFIRM_ACTION_ID) {
      const action = parseCommandAction(submission.values?.option_id, {
        confirm: CONTACT_CONFIRM_OPTION_PREFIX,
        cancel: CONTACT_CANCEL_OPTION_PREFIX,
        edit: CONTACT_EDIT_OPTION_PREFIX,
      });
      if (!action || activeDirectiveMessageId) return;

      if (action.type === "edit") {
        window.alert("Desktop contact editing is not wired up yet. Use the mobile draft editor for this action.");
        return;
      }

      setActiveDirectiveMessageId(message.id);
      try {
        const result = await api.post<ContactConfirmResult>("/commands/contact/confirm", {
          preview_id: action.previewId,
          confirmed: action.type === "confirm",
          modifications: {},
        });
        if (!result.success) {
          throw new Error(result.error || "Contact action failed");
        }
        updateMessageMetadata(message.id, {
          command_resolved: {
            status: action.type === "confirm" ? "created" : "cancelled",
            label: action.type === "confirm" ? "Contact changes applied" : "Contact update cancelled",
          },
        });
        setPendingEventId(null);
        await refreshThreads();
      } catch (error) {
        const detail = error instanceof Error ? error.message : "I could not complete that contact action.";
        updateMessageMetadata(message.id, { request_error: detail });
      } finally {
        setActiveDirectiveMessageId(null);
      }
      return;
    }

    void submitMessage({
      text: submission.text_fallback || "Submitted structured response.",
      uiSubmission: submission,
    });
  };

  return (
    <section
      style={{
        alignItems: "start",
        display: "grid",
        gap: "16px",
        gridTemplateColumns: chatMode === "threads" ? "280px 1fr" : "1fr",
      }}
    >
      {chatMode === "threads" ? (
        <aside
          style={{
            background: "#fff",
            border: "1px solid #e2e8f0",
            borderRadius: "8px",
            display: "flex",
            flexDirection: "column",
            gap: "12px",
            height: "100%",
            maxHeight: "720px",
            padding: "16px",
          }}
        >
          <div style={{ alignItems: "center", display: "flex", justifyContent: "space-between" }}>
            <h2 style={{ fontSize: "1rem", fontWeight: 650, margin: 0 }}>Conversations</h2>
            <button
              type="button"
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
                background: "#0b6bcb",
                border: "1px solid #0b6bcb",
                borderRadius: "6px",
                color: "#fff",
                cursor: "pointer",
                fontSize: "0.8rem",
                padding: "4px 8px",
              }}
            >
              New
            </button>
          </div>
          <div style={{ display: "flex", flex: 1, flexDirection: "column", gap: "6px", overflowY: "auto" }}>
            {isLoadingThreads ? (
              <div style={{ color: "#64748b", fontSize: "0.9rem" }}>Loading conversations...</div>
            ) : null}
            {!isLoadingThreads && threads.length === 0 ? (
              <div style={{ color: "#64748b", fontSize: "0.9rem" }}>No conversations yet.</div>
            ) : null}
            {threads.map((thread) => {
              const isActive = thread.id === selectedThreadId;
              return (
                <button
                  key={thread.id}
                  type="button"
                  onClick={() => {
                    if (!isActive) void loadThread(thread.id);
                  }}
                  style={{
                    background: isActive ? "#e0f2fe" : "#fafafa",
                    border: "1px solid",
                    borderColor: isActive ? "#0b6bcb" : "#e2e8f0",
                    borderRadius: "8px",
                    cursor: "pointer",
                    display: "grid",
                    gap: "6px",
                    padding: "10px 12px",
                    textAlign: "left",
                  }}
                >
                  <span style={{ color: "#0f172a", fontSize: "0.9rem", fontWeight: 650 }}>
                    {thread.title?.trim() || "Untitled conversation"}
                  </span>
                  <span style={{ color: "#64748b", fontSize: "0.75rem" }}>
                    {thread.last_message_preview || "No messages yet"}
                  </span>
                  <span style={{ alignItems: "center", color: "#94a3b8", display: "flex", fontSize: "0.7rem", justifyContent: "space-between" }}>
                    Updated{" "}
                    {new Date(thread.updated_at).toLocaleString([], {
                      month: "short",
                      day: "numeric",
                      hour: "2-digit",
                      minute: "2-digit",
                    })}
                    <span
                      role="button"
                      tabIndex={0}
                      onClick={(event) => {
                        event.stopPropagation();
                        void deleteThread(thread.id);
                      }}
                      onKeyDown={(event) => {
                        if (event.key === "Enter" || event.key === " ") {
                          event.stopPropagation();
                          void deleteThread(thread.id);
                        }
                      }}
                      style={{ color: "#dc2626", paddingLeft: "8px" }}
                    >
                      Delete
                    </span>
                  </span>
                </button>
              );
            })}
          </div>
        </aside>
      ) : null}

      <div style={{ display: "grid", gap: "16px" }}>
        <div style={{ alignItems: "flex-start", display: "flex", justifyContent: "space-between" }}>
          <div>
            <h1 style={{ fontSize: "2rem", fontWeight: 650, margin: 0 }}>
              Welcome{session?.user?.name ? `, ${session.user.name.split(" ")[0]}` : ""}
            </h1>
            <p style={{ color: "#475569", margin: "8px 0 0" }}>
              Ask questions about your contacts, meetings, documents, and plans.
            </p>
          </div>
          <div style={{ background: "#f1f5f9", borderRadius: "8px", display: "flex", gap: "4px", padding: "4px" }}>
            {(["quick", "threads"] as ChatMode[]).map((mode) => (
              <button
                key={mode}
                type="button"
                onClick={() => setChatMode(mode)}
                style={{
                  background: chatMode === mode ? "#fff" : "transparent",
                  border: "none",
                  borderRadius: "6px",
                  boxShadow: chatMode === mode ? "0 1px 3px rgba(0,0,0,0.1)" : "none",
                  color: chatMode === mode ? "#0b6bcb" : "#64748b",
                  cursor: "pointer",
                  fontWeight: chatMode === mode ? 650 : 400,
                  padding: "8px 16px",
                }}
              >
                {mode === "quick" ? "Quick Chat" : "Threads"}
              </button>
            ))}
          </div>
        </div>

        <div
          style={{
            background: "#fff",
            border: "1px solid #e2e8f0",
            borderRadius: "8px",
            boxShadow: "0 4px 12px rgba(15, 23, 42, 0.04)",
            display: "grid",
            gridTemplateRows: "auto 1fr auto",
            height: "min(720px, calc(100vh - 190px))",
            minHeight: "560px",
          }}
        >
          <div
            style={{
              alignItems: "center",
              borderBottom: "1px solid #e2e8f0",
              display: "flex",
              justifyContent: "space-between",
              padding: "18px 24px",
            }}
          >
            <div>
              <h2 style={{ fontSize: "1.18rem", fontWeight: 650, margin: 0 }}>Brain</h2>
              <p style={{ color: "#64748b", fontSize: "0.875rem", margin: "4px 0 0" }}>
                Talk to your memory, create records, and follow referenced items.
              </p>
            </div>
          </div>

          <div
            ref={messagesContainerRef}
            onScroll={(event) => {
              const target = event.currentTarget;
              setIsAtBottom(target.scrollHeight - target.scrollTop - target.clientHeight < 48);
            }}
            style={{
              display: "flex",
              flexDirection: "column",
              gap: "16px",
              overflowY: "auto",
              padding: "24px",
            }}
          >
            {isLoadingMessages ? (
              <div style={{ color: "#64748b", fontSize: "0.9rem" }}>Loading conversation...</div>
            ) : null}

            {!isLoadingMessages && displayMessages.length === 0 ? (
              <div
                style={{
                  alignItems: "center",
                  color: "#64748b",
                  display: "flex",
                  flexDirection: "column",
                  gap: "10px",
                  height: "100%",
                  justifyContent: "center",
                  textAlign: "center",
                }}
              >
                <p style={{ color: "#334155", fontSize: "1rem", margin: 0 }}>Start a conversation below.</p>
                <p style={{ fontSize: "0.88rem", margin: 0 }}>
                  Try `/event dinner tomorrow` or ask what happened in a recent meeting.
                </p>
              </div>
            ) : null}

            {displayMessages.map((message, index) => {
              const isLastMessage = index === displayMessages.length - 1;
              const uiDirectives = message.metadata?.ui_directives;
              const linkedItems = message.metadata?.linked_items || [];
              const requestError = message.metadata?.request_error;
              const activeEventEditor =
                activeDraftEditor?.kind === "event" && activeDraftEditor.messageId === message.id
                  ? activeDraftEditor
                  : null;
              const activeEventBaseDraft = activeEventEditor
                ? buildEventDraft(message.metadata?.command_result, activeEventEditor.previewId)
                : null;
              const activeEventInitialDraft =
                activeEventBaseDraft && activeEventEditor
                  ? applyEventDraftModifications(
                      activeEventBaseDraft,
                      eventDraftModificationsByPreview[activeEventEditor.previewId],
                    )
                  : null;
              const eventPreviewId =
                message.metadata?.command_result &&
                typeof message.metadata.command_result.preview_id === "string"
                  ? message.metadata.command_result.preview_id
                  : null;
              const eventBaseDraft =
                eventPreviewId && uiDirectives
                  ? buildEventDraft(message.metadata?.command_result, eventPreviewId)
                  : null;
              const directivesForCard =
                uiDirectives && eventPreviewId && eventBaseDraft
                  ? updateEventPreviewDirectives(
                      uiDirectives,
                      eventPreviewId,
                      applyEventDraftModifications(
                        eventBaseDraft,
                        eventDraftModificationsByPreview[eventPreviewId],
                      ),
                    )
                  : uiDirectives;
              return (
                <div
                  key={message.id}
                  ref={isLastMessage ? lastMessageRef : undefined}
                  style={{
                    alignItems: message.role === "user" ? "flex-end" : "flex-start",
                    display: "flex",
                    flexDirection: "column",
                    gap: "7px",
                  }}
                >
                  {message.metadata?.progress_chip && message.pending ? (
                    <div
                      style={{
                        alignItems: "center",
                        background: "#e0f2fe",
                        borderRadius: "999px",
                        color: "#0369a1",
                        display: "flex",
                        fontSize: "0.82rem",
                        gap: "6px",
                        padding: "6px 10px",
                      }}
                    >
                      <span style={{ animation: "pulse 1.5s infinite" }}>●</span>
                      {message.metadata.progress_chip}
                    </div>
                  ) : null}
                  <div
                    style={{
                      background: message.role === "user" ? "#0b6bcb" : "#f8fafc",
                      border: message.role === "user" ? "1px solid #0b6bcb" : "1px solid #e2e8f0",
                      borderRadius: "8px",
                      color: message.role === "user" ? "#fff" : "#1f2937",
                      display: "flex",
                      flexDirection: "column",
                      gap: "0.5rem",
                      maxWidth: "80%",
                      overflowWrap: "anywhere",
                      padding: "12px 16px",
                    }}
                  >
                    <AssistantMarkdown content={message.content} role={message.role} />
                    {requestError ? (
                      <div style={{ color: "#991b1b", fontSize: "0.78rem" }}>{requestError}</div>
                    ) : null}
                  </div>
                  {directivesForCard ? (
                    <UiDirectiveCard
                      directives={directivesForCard}
                      disabled={isLoading || activeDirectiveMessageId === message.id}
                      resolved={message.metadata?.command_resolved}
                      onSubmit={(submission) => {
                        void handleDirectiveSubmit(message, submission);
                      }}
                    />
                  ) : null}
                  {activeEventEditor && activeEventBaseDraft && activeEventInitialDraft ? (
                    <EventDraftEditor
                      baseDraft={activeEventBaseDraft}
                      initialDraft={activeEventInitialDraft}
                      onCancel={() => setActiveDraftEditor(null)}
                      onSave={(modifications) => {
                        setEventDraftModificationsByPreview((prev) => {
                          const next = { ...prev };
                          if (Object.keys(modifications).length > 0) {
                            next[activeEventEditor.previewId] = modifications;
                          } else {
                            delete next[activeEventEditor.previewId];
                          }
                          return next;
                        });
                        setActiveDraftEditor(null);
                      }}
                    />
                  ) : null}
                  <LinkedItemsRow items={linkedItems} />
                  <div
                    style={{
                      color: "#94a3b8",
                      fontSize: "0.75rem",
                      paddingLeft: message.role === "user" ? 0 : "8px",
                      paddingRight: message.role === "user" ? "8px" : 0,
                    }}
                  >
                    {timestampLabel(message.timestamp)}
                  </div>
                </div>
              );
            })}
          </div>

          <form
            onSubmit={handleSubmit}
            style={{
              background: "#f8fafc",
              borderTop: "1px solid #e2e8f0",
              borderRadius: "0 0 8px 8px",
              padding: "16px 24px",
              position: "relative",
            }}
          >
            {showSlashPalette ? (
              <div style={{ bottom: "72px", left: "24px", position: "absolute", zIndex: 10 }}>
                <SlashCommandPalette
                  query={slashMatch?.[1] ?? ""}
                  onSelect={(command) => setInput(`/${command} `)}
                />
              </div>
            ) : null}
            <div style={{ display: "flex", gap: "12px" }}>
              <input
                type="text"
                value={input}
                onChange={(event) => setInput(event.target.value)}
                placeholder="Ask a question or type / for commands..."
                disabled={isLoading}
                style={{
                  background: "#fff",
                  border: "1px solid #cbd5e1",
                  borderRadius: "8px",
                  cursor: "text",
                  flex: 1,
                  fontSize: "0.95rem",
                  outline: "none",
                  padding: "12px 14px",
                }}
              />
              <button
                type="submit"
                disabled={isLoading || !input.trim()}
                style={{
                  background: isLoading || !input.trim() ? "#94a3b8" : "#0b6bcb",
                  border: "none",
                  borderRadius: "8px",
                  color: "#fff",
                  cursor: isLoading || !input.trim() ? "not-allowed" : "pointer",
                  fontWeight: 650,
                  padding: "12px 20px",
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
            background: "#fff",
            border: "1px solid #e2e8f0",
            borderRadius: "8px",
            display: "flex",
            flexWrap: "wrap",
            gap: "12px",
            padding: "14px 16px",
          }}
        >
          <Link href="/contacts" style={{ color: "#0b6bcb" }}>Contacts</Link>
          <Link href="/meetings" style={{ color: "#0b6bcb" }}>Meetings</Link>
          <Link href="/documents" style={{ color: "#0b6bcb" }}>Documents</Link>
        </div>
      </div>
    </section>
  );
}
