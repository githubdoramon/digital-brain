import { apiFetch } from '@/src/api/client';

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
  const threads = await apiFetch('/threads', { token });
  const resolvedThreads = Array.isArray(threads) ? (threads as ThreadSummary[]) : [];

  let threadId: string | null = storedSession?.threadId ?? null;
  if (threadId && !resolvedThreads.some((thread) => thread.id === threadId)) {
    threadId = null;
  }
  if (!threadId && resolvedThreads.length > 0) {
    threadId = resolvedThreads[0].id;
  }

  let messages: ChatMessage[] = [];
  if (threadId) {
    const threadDetail = (await apiFetch(`/threads/${threadId}`, { token })) as ThreadDetail;
    messages = (threadDetail.messages || []).map((msg) => ({
      id: `${msg.message_id}`,
      role: msg.role,
      content: msg.content,
      metadata: msg.metadata ?? undefined,
    }));
  }

  return {
    threadId,
    pendingEventId: threadId && storedSession?.threadId === threadId
      ? storedSession?.pendingEventId ?? null
      : null,
    messages,
  };
}
