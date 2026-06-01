import type { LinkedItem, StreamBundle, UiDirectives } from "@/lib/api";

export type ChatMode = "quick" | "threads";

export type AssistantMetadata = {
  command_result?: StreamBundle["command_result"];
  command_resolved?: {
    status: "created" | "updated" | "cancelled";
    label?: string;
  };
  ui_directives?: UiDirectives;
  linked_items?: LinkedItem[];
  progress_chip?: string;
  request_error?: string;
} & Record<string, unknown>;

export type Message = {
  id: string | number;
  role: "user" | "assistant";
  content: string;
  timestamp: Date;
  pending?: boolean;
  metadata?: AssistantMetadata;
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
  role: "user" | "assistant";
  content: string;
  metadata?: AssistantMetadata | null;
  created_at: string;
};

export type ThreadDetail = ThreadSummary & {
  messages: ThreadMessage[];
};
