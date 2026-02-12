import { apiFetch } from '@/api/client';
import type { UiDirectives } from './uiDirectives';

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
    ui_directives?: UiDirectives;
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
    ui_directives?: UiDirectives;
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

  // Always sync to the latest server thread on app open.
  // Server ordering is newest first (updated_at DESC, created_at DESC).
  let threadId: string | null = resolvedThreads[0]?.id ?? null;

  let messages: ChatMessage[] = [];
  if (threadId) {
    const threadDetail = (await apiFetch(`/mobile/threads/${threadId}`, { token })) as ThreadDetail;
    messages = (threadDetail.messages || []).map((msg) => ({
      id: `${msg.message_id}`,
      role: msg.role,
      content: msg.content,
      metadata: msg.metadata ?? undefined,
    }));
  }

  return {
    threadId,
    pendingEventId:
      threadId && storedSession?.threadId === threadId ? storedSession?.pendingEventId ?? null : null,
    messages,
  };
}
