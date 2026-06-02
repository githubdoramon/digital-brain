import type { StreamBundle, UiDirectives } from "@/lib/api";

export type EventDraft = {
  title: string;
  summary: string;
  when: string;
  endWhen: string;
  where: string;
  participants: string[];
  tags: string[];
  types: string[];
};

export type EventDraftModifications = {
  title?: string;
  summary?: string;
  when?: string | null;
  end_when?: string | null;
  where?: string;
  tags?: string[];
  types?: string[];
};

type CommandResult = NonNullable<StreamBundle["command_result"]>;

function textValue(value: unknown): string {
  if (value === null || value === undefined) return "";
  return String(value).trim();
}

function stringArrayValue(value: unknown): string[] {
  if (Array.isArray(value)) {
    return value.map((entry) => textValue(entry)).filter(Boolean);
  }
  const text = textValue(value);
  return text
    ? text
        .split(",")
        .map((entry) => entry.trim())
        .filter(Boolean)
    : [];
}

function normalizedDraftValue(value: string): string {
  return value.trim();
}

function sameStringList(left: string[], right: string[]): boolean {
  if (left.length !== right.length) return false;
  return left.every((entry, index) => entry === right[index]);
}

function formatEventPreviewWhen(value: string): string {
  const raw = value.trim();
  if (!raw) return "Not specified";
  const normalized = raw.replace("Z", "+00:00");
  const parsed = new Date(normalized);
  if (Number.isNaN(parsed.getTime())) return raw;
  return parsed.toLocaleString("en-US", {
    month: "short",
    day: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

export function extractEventPreviewId(commandResult: CommandResult | undefined): string | null {
  if (!commandResult || typeof commandResult !== "object") return null;
  return textValue(commandResult.preview_id) || null;
}

export function buildEventDraft(commandResult: CommandResult | undefined, previewId: string): EventDraft | null {
  if (!commandResult || typeof commandResult !== "object") return null;
  if (textValue(commandResult.preview_id) !== previewId) return null;
  const extracted =
    commandResult.extracted && typeof commandResult.extracted === "object"
      ? (commandResult.extracted as Record<string, unknown>)
      : null;
  if (!extracted) return null;
  const resolution =
    commandResult.resolution && typeof commandResult.resolution === "object"
      ? (commandResult.resolution as Record<string, unknown>)
      : {};
  const contacts = Array.isArray(resolution.contacts) ? resolution.contacts : [];
  const newEntities =
    resolution.new_entities && typeof resolution.new_entities === "object"
      ? (resolution.new_entities as Record<string, unknown>)
      : {};
  const newContacts = Array.isArray(newEntities.contacts) ? newEntities.contacts : [];
  const participants = [
    ...contacts.map((contact) =>
      contact && typeof contact === "object"
        ? textValue((contact as Record<string, unknown>).display_name)
        : "",
    ),
    ...newContacts.map((contact) =>
      contact && typeof contact === "object"
        ? textValue((contact as Record<string, unknown>).display_name)
        : "",
    ),
  ].filter(Boolean);
  const fallbackWho = participants.length > 0 ? participants : stringArrayValue(extracted.who);

  return {
    title: textValue(extracted.title),
    summary: textValue(extracted.summary),
    when: textValue(extracted.when),
    endWhen: textValue(extracted.end_when),
    where: textValue(extracted.where),
    participants: fallbackWho,
    tags: stringArrayValue(extracted.tags),
    types: stringArrayValue(extracted.types),
  };
}

export function applyEventDraftModifications(
  baseDraft: EventDraft,
  modifications: EventDraftModifications | undefined,
): EventDraft {
  if (!modifications) return baseDraft;
  return {
    title: modifications.title ?? baseDraft.title,
    summary: modifications.summary ?? baseDraft.summary,
    when:
      modifications.when === null
        ? ""
        : modifications.when === undefined
          ? baseDraft.when
          : modifications.when,
    endWhen:
      modifications.end_when === null
        ? ""
        : modifications.end_when === undefined
          ? baseDraft.endWhen
          : modifications.end_when,
    where: modifications.where ?? baseDraft.where,
    participants: baseDraft.participants,
    tags: modifications.tags ?? baseDraft.tags,
    types: modifications.types ?? baseDraft.types,
  };
}

export function buildEventDraftModifications(
  baseDraft: EventDraft,
  nextDraft: EventDraft,
): EventDraftModifications {
  const modifications: EventDraftModifications = {};
  if (normalizedDraftValue(baseDraft.title) !== normalizedDraftValue(nextDraft.title)) {
    modifications.title = normalizedDraftValue(nextDraft.title);
  }
  if (normalizedDraftValue(baseDraft.summary) !== normalizedDraftValue(nextDraft.summary)) {
    modifications.summary = normalizedDraftValue(nextDraft.summary);
  }
  if (normalizedDraftValue(baseDraft.when) !== normalizedDraftValue(nextDraft.when)) {
    modifications.when = normalizedDraftValue(nextDraft.when) || null;
  }
  if (normalizedDraftValue(baseDraft.endWhen) !== normalizedDraftValue(nextDraft.endWhen)) {
    modifications.end_when = normalizedDraftValue(nextDraft.endWhen) || null;
  }
  if (normalizedDraftValue(baseDraft.where) !== normalizedDraftValue(nextDraft.where)) {
    modifications.where = normalizedDraftValue(nextDraft.where);
  }
  if (!sameStringList(baseDraft.tags, nextDraft.tags)) {
    modifications.tags = nextDraft.tags;
  }
  if (!sameStringList(baseDraft.types, nextDraft.types)) {
    modifications.types = nextDraft.types;
  }
  return modifications;
}

export function updateEventPreviewDirectives(
  directives: UiDirectives,
  previewId: string,
  draft: EventDraft,
): UiDirectives {
  const blockId = `event_preview:${previewId}`;
  const body = [
    `Title: ${draft.title.trim() || "Untitled event"}`,
    `Summary: ${draft.summary.trim() || "No summary provided."}`,
    `When: ${formatEventPreviewWhen(draft.when)}`,
    `Ends: ${formatEventPreviewWhen(draft.endWhen)}`,
    `Where: ${draft.where.trim() || "Not specified"}`,
    `Who: ${draft.participants.length > 0 ? draft.participants.join(", ") : "No participants detected"}`,
    `Tags: ${draft.tags.length > 0 ? draft.tags.join(", ") : "None"}`,
    `Types: ${draft.types.length > 0 ? draft.types.join(", ") : "Generic"}`,
  ].join("\n");

  return {
    ...directives,
    blocks: directives.blocks.map((block) =>
      block.id === blockId
        ? {
            ...block,
            body,
          }
        : block,
    ),
  };
}
