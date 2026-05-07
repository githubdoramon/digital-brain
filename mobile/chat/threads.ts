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

export type CommandResolvedStatus = 'created' | 'cancelled' | 'updated';

export type CommandResolvedMeta = {
  status: CommandResolvedStatus;
  label?: string;
};

export type MessageMediaAttachment = {
  attachment_id?: string;
  file_name?: string | null;
  mime_type?: string | null;
  source?: string | null;
  captured_at?: string | null;
  width?: number | null;
  height?: number | null;
  uri?: string | null;
};

export type ChatMessage = {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  metadata?: {
    command_result?: CommandResult;
    ui_directives?: UiDirectives;
    linked_items?: LinkedItem[];
    command_resolved?: CommandResolvedMeta;
    media_attachments?: MessageMediaAttachment[];
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
            command_resolved: meta.command_resolved as CommandResolvedMeta | undefined,
            media_attachments: Array.isArray(meta.media_attachments)
              ? (meta.media_attachments as MessageMediaAttachment[])
              : undefined,
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
