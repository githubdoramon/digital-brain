# Tool Groups Reference

Tool groups are the canonical capability taxonomy and are now used both for metadata and runtime visibility policy.

## Canonical Source

`backend/orchestrator/tools/registry.py` is the single source of truth.

```python
TOOL_GROUPS = {
    "memory": ["search_memories", "get_events", "get_document", "summarize_memories"],
    "graph": ["query_graph"],
    "resolution": [
        "resolve_contacts",
        "lookup_contact",
        "select_contacts",
        "lookup_places",
        "lookup_contact_places",
        "lookup_place_contacts",
    ],
    "web": ["web_search", "fetch_web_page"],
    "home": ["home_assistant"],
    "skills": ["run_skill_script"],
    "pdf": ["create_pdf", "ingest_generated_pdf"],
    "ui": ["emit_ui_directive"],
    "system": ["bash"],
}
```

`agent/router.py` imports these definitions to avoid drift.

## Runtime Use (Current)

Tool groups are used by `agent/tool_visibility_policy.py` to determine visible tools from router confidence.

```mermaid
flowchart LR
  I[Intent + confidence] --> T{Tier}
  T -->|high| H[Use routed groups]
  T -->|medium| M[Use routed groups + resolution]
  T -->|low| L[Use full toolset]
  H --> NP{No progress?}
  M --> NP
  NP -->|yes| E[Escalate to full toolset]
```

## Intent-to-Group Mapping

- `MEMORY_SEARCH`: `memory`, `resolution`
- `DATA_QUERY`: `graph`, `memory`, `resolution`
- `CONTACT_LOOKUP`: `resolution`, `memory`
- `WEB_SEARCH`: `web`
- `HOME_CONTROL`: `home`
- `SKILL_EXECUTION`: `skills`, `memory`
- `SYSTEM_COMMAND`: `system`
- `CONVERSATIONAL`: `memory`, `resolution`, `web`, `pdf`, `ui`
- `UNKNOWN`: all groups

## Policy Summary

- Restriction mode: conservative by default.
- High confidence narrows aggressively.
- Medium confidence keeps `resolution` available for recovery/disambiguation.
- Low confidence fails open for correctness.
- No-progress in restricted mode triggers full-tool escalation.

## Memory Tool Guidance

- Use `search_memories` for semantic discovery across events/documents/notes.
- Use `get_events` for strict event evidence:
  - `action=by_ids`: inspect specific event candidates from `search_memories`.
  - `action=by_time_span`: strict chronological windows (for example, "who did I meet most this week").
- Optional `contact_ids` on `get_events` can scope to linked contacts when identities are already resolved.

Negative examples:

- Do not use `lookup_contact` to count event interactions; it is for contact profiles/relationships.
- Use `lookup_places` / `lookup_contact_places` / `lookup_place_contacts` for place-entity resolution, not for free-form event retrieval.
- Do not rely only on event title keywords (for example only searching `meeting`) when ranking interactions across a time window.

## PDF Tool Guidance

- Use `create_pdf` when the user asks for a generated, printable, or downloadable PDF.
- PDF content is modeled as `body_markdown`; tabular content should be expressed as markdown pipe tables, not a separate structured table parameter.
- `create_pdf` stores an authenticated generated artifact and returns web and mobile download URLs.
- The controller derives `generated_files` metadata from successful `create_pdf` calls so web and mobile chat can render download chips without relying on model-authored UI markup.
- Set `ingest_as_document=true` only when the user asks to save/index the generated PDF as a document.
- Use `ingest_generated_pdf` for a previously generated artifact when the user later asks to add it through the regular document ingest flow.
- Generated PDF ingestion must go through `documents.ingest_document` so parsing, metadata, embeddings, contact inference, and document download behavior remain consistent with uploaded files.

## When Editing Groups

1. Update `TOOL_GROUPS` in `tools/registry.py`.
2. Ensure each tool contract is registered with the correct group(s).
3. Verify router mapping and tests still align.
4. Re-run integration tests for routing and visibility tiers.

## Common Pitfalls

- Updating router intent mappings without updating registry groups.
- Forgetting that medium tier always includes `resolution` by policy.
- Assuming all requests are narrowed; low confidence intentionally fails open.
