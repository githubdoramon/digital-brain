import { apiFetch } from '@/api/client';
import type { LinkedItem } from '@/chat/linkedItems';
import type { UiDirectives } from './uiDirectives';

import { StoredChatSession } from './session';

export type CommandResult = {
  type: string;
  [key: string]: unknown;
};

export type ThreadMessage = {
  message_id: number;
  role: 'user' | 'assistant';
  content: string;
  metadata?: {
    command_result?: CommandResult;
    ui_directives?: UiDirectives;
    linked_items?: LinkedItem[];
    [key: string]: unknown;
  } | null;
  created_at: string;
};

export type MainSession = {
  thread_id: string;
  thread_title?: string | null;
  is_new_session: boolean;
  pending_event_id?: string | null;
  messages: ThreadMessage[];
};

export type EventResolvedStatus = 'created' | 'cancelled';

export type ChatMessage = {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  metadata?: {
    command_result?: CommandResult;
    ui_directives?: UiDirectives;
    linked_items?: LinkedItem[];
    event_resolved?: EventResolvedStatus;
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
  const mainSession = (await apiFetch('/mobile/main-session', { token })) as MainSession;

  const threadId = mainSession.thread_id ?? null;
  const resolvedPendingEventId = mainSession.pending_event_id ?? null;

  let messages: ChatMessage[] = [];
  messages = (mainSession.messages || []).map((msg) => {
    const meta = msg.metadata ?? undefined;
    return {
      id: `${msg.message_id}`,
      role: msg.role,
      content: msg.content,
      metadata: meta
        ? {
            command_result: meta.command_result,
            ui_directives: meta.ui_directives,
            linked_items: Array.isArray(meta.linked_items)
              ? (meta.linked_items as LinkedItem[])
              : undefined,
            event_resolved: meta.event_resolved as EventResolvedStatus | undefined,
          }
        : undefined,
    };
  });

  return {
    threadId,
    pendingEventId:
      resolvedPendingEventId ??
      (threadId && storedSession?.threadId === threadId ? storedSession?.pendingEventId ?? null : null),
    messages,
  };
}
