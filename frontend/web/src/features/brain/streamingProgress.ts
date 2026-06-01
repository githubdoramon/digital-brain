function formatToolArgValue(value: unknown): string {
  if (value === null || value === undefined) return "null";
  if (typeof value === "string") {
    const compact = value.trim().replace(/\s+/g, " ");
    return compact.length > 36 ? `${compact.slice(0, 33)}...` : compact;
  }
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  if (Array.isArray(value)) return `list(${value.length})`;
  if (typeof value === "object") return "object";
  return String(value);
}

function humanToolName(toolNameRaw: string): string {
  const toolName = toolNameRaw.trim();
  const aliases: Record<string, string> = {
    search_memories: "Searching memory",
    get_events: "Checking events",
    get_document: "Reading a document",
    resolve_contacts: "Resolving contacts",
    lookup_contact: "Looking up a contact",
    select_contacts: "Selecting contacts",
    lookup_places: "Looking up places",
    lookup_contact_places: "Checking places",
    lookup_place_contacts: "Checking place contacts",
    web_search: "Searching the web",
    fetch_web_page: "Fetching a web page",
    home_assistant: "Using Home Assistant",
    run_skill_script: "Running a skill",
    emit_ui_directive: "Building a response card",
    bash: "Running a system command",
  };
  return aliases[toolName] || toolName.replace(/_/g, " ");
}

function normalizeToolArgs(argsRaw: unknown): Record<string, unknown> {
  if (argsRaw && typeof argsRaw === "object" && !Array.isArray(argsRaw)) {
    return argsRaw as Record<string, unknown>;
  }
  if (typeof argsRaw === "string") {
    const text = argsRaw.trim();
    if (!text) return {};
    try {
      const parsed = JSON.parse(text) as unknown;
      if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
        return parsed as Record<string, unknown>;
      }
    } catch {
      return {};
    }
  }
  return {};
}

export function buildToolProgressChip(toolNameRaw: string, argsRaw: unknown): string {
  const toolName = toolNameRaw.trim();
  if (!toolName) return "";

  const humanName = humanToolName(toolName);
  const args = normalizeToolArgs(argsRaw);
  const preferredKeys = ["query", "url", "limit", "max_results", "topic", "contact_ids"];
  const preferredEntries = preferredKeys
    .filter((key) => key in args)
    .map((key) => [key, args[key]] as const);
  const entries = Object.entries(args).filter(([, value]) => value !== undefined);
  const sourceEntries = preferredEntries.length > 0 ? preferredEntries : entries;
  const selectedEntries = sourceEntries
    .slice(0, 2)
    .map(([key, value]) => `${key}: ${formatToolArgValue(value)}`);

  if (selectedEntries.length === 0) return humanName;
  return `${humanName} (${selectedEntries.join(", ")})`;
}
