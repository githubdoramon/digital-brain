import { apiFetch } from '@/api/client';

import { StoredChatSession } from './session';

export type CommandResult = {
  type: string;
  [key: string]: unknown;
};

export type ThreadSummary = {
  id: string;
  title: string | null;
  created_at: string;
  updated_at: string;
  last_message_preview?: string | null;
};

export type ThreadMessage = {
  message_id: number;
  role: 'user' | 'assistant';
  content: string;
  metadata?: {
    command_result?: CommandResult;
    [key: string]: unknown;
  } | null;
  created_at: string;
};

export type ThreadDetail = ThreadSummary & {
  messages: ThreadMessage[];
};

export type ChatMessage = {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  metadata?: {
    command_result?: CommandResult;
  };
};

type RestoreResult = {
  threadId: string | null;
  pendingEventId: string | null;
  messages: ChatMessage[];
};

export async function restoreChatHistory(
  token: string,
  storedSession: StoredChatSession | null,
): Promise<RestoreResult> {
  const threads = await apiFetch('/mobile/threads', { token });
  const resolvedThreads = Array.isArray(threads) ? (threads as ThreadSummary[]) : [];

  let threadId: string | null = storedSession?.threadId ?? null;
  if (threadId && resolvedThreads.length > 0 && !resolvedThreads.some((thread) => thread.id === threadId)) {
    threadId = null;
  }
  if (!threadId && resolvedThreads.length > 0) {
    const preferred = resolvedThreads.find((thread) => thread.last_message_preview);
    threadId = (preferred ?? resolvedThreads[0]).id;
  }

  let messages: ChatMessage[] = [];
  if (threadId) {
    const threadDetail = (await apiFetch(`/mobile/threads/${threadId}`, { token })) as ThreadDetail;
    messages = (threadDetail.messages || []).map((msg) => ({
      id: `${msg.message_id}`,
      role: msg.role,
      content: msg.content,
      metadata: msg.metadata ?? undefined,
    }));

    if (messages.length === 0 && resolvedThreads.length > 1) {
      const fallback = resolvedThreads.find(
        (thread) => thread.id !== threadId && thread.last_message_preview,
      );
      if (fallback) {
        threadId = fallback.id;
        const fallbackDetail = (await apiFetch(`/mobile/threads/${threadId}`, { token })) as ThreadDetail;
        messages = (fallbackDetail.messages || []).map((msg) => ({
          id: `${msg.message_id}`,
          role: msg.role,
          content: msg.content,
          metadata: msg.metadata ?? undefined,
        }));
      }
    }
  }

  return {
    threadId,
    pendingEventId: threadId && storedSession?.threadId === threadId
      ? storedSession?.pendingEventId ?? null
      : null,
    messages,
  };
}
