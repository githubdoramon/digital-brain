Digital Brain – Quick Context

## Overview

Personal memory orchestrator with a **bounded agent architecture**. Backend: FastAPI (`backend/orchestrator`) + PostgreSQL/pgvector. Frontend: Next.js App Router (`frontend/web`) with NextAuth Google OAuth. Mobile app: React Native/Expo (`mobile`). Supports OpenAI-compatible LLM APIs (local Ollama or cloud). Optional LangSearch web search, Tavily news search, and Home Assistant integration.

**Core principle**: "The model proposes. The controller validates, executes, and decides."

**Terminology**: When a user says "app", they mean the mobile app in `mobile`, not the web frontend.

**Execution**: No need to propose ticket creation or plans for engineers to execute later. You are also the executor here.

**Mobile routing convention**: For dynamic mobile routes, prefer folder-based segments with `index.tsx` (for example `mobile/app/contacts/[contactId]/index.tsx`) so nested subroutes can be added without migrating route structure later.

**Mobile screen convention**: Reuse established full-screen patterns before creating new screen chrome. Screens that own a custom/collapsing header (for example event/contact draft editors) must hide the native Expo Stack header in `mobile/app/_layout.tsx` to avoid double navigation bars.

**Mobile voice input convention**: Chat dictation uses on-device Whisper in `mobile` with a long-press send gesture, swipe-up lock, and transcript insertion into the composer (never auto-send). This requires a native Expo dev build / prebuild workflow; do not assume it works in Expo Go.

**Mobile background task convention**: Expo background task definitions must be imported from `mobile/index.js` before `expo-router/entry`, so headless/background launches register the tasks even when React navigation has not mounted.

## Architecture Documentation

Detailed architecture docs live in `backend/orchestrator/docs/architecture/`:

| Document | Purpose |
|----------|---------|
| [OVERVIEW.md](backend/orchestrator/docs/architecture/OVERVIEW.md) | System architecture and request flow |
| [ADDING_TOOLS.md](backend/orchestrator/docs/architecture/ADDING_TOOLS.md) | Complete guide to adding new tools |
| [ADDING_INTENTS.md](backend/orchestrator/docs/architecture/ADDING_INTENTS.md) | Guide to creating new intent types |
| [TOOL_GROUPS.md](backend/orchestrator/docs/architecture/TOOL_GROUPS.md) | Tool group reference and patterns |
| [STATE_MANAGEMENT.md](backend/orchestrator/docs/architecture/STATE_MANAGEMENT.md) | AgentState guide with examples |
| [VALIDATION.md](backend/orchestrator/docs/architecture/VALIDATION.md) | Pre/post validation system |
| [AGENT_LIMITS.md](backend/orchestrator/docs/architecture/AGENT_LIMITS.md) | Limits and stop rules configuration |
| [LINKED_ITEMS_DSL.md](backend/orchestrator/docs/architecture/LINKED_ITEMS_DSL.md) | Prompt-level protocol for controller-derived deep links |
| [CLIENT_API_PROXY.md](CLIENT_API_PROXY.md) | Client API proxy requirements and routing |
| [agents/DAILY_BRIEFING.md](backend/orchestrator/docs/agents/DAILY_BRIEFING.md) | Daily briefing agent behavior, generation flow, and quality rules |
| [agents/MEMORY_EXPERT.md](backend/orchestrator/docs/agents/MEMORY_EXPERT.md) | Memory expert retrieval/disambiguation behavior and contact-aware rules |


## Services & Runtime

Docker Compose services: `db` (pgvector), `orchestrator` (FastAPI), `frontend` (Next.js). See `docker-compose.yml`.

- **Backend entry**: `backend/orchestrator/app.py`
- **Auth**: Google ID token (`Authorization: Bearer …`); email allowlist in `auth.py`
- **CORS**: Allows `http://localhost:3000`
- **Service-to-service**: `x-service-api-key` header (`ORCHESTRATOR_API_KEY`)

## Client API Proxy

All client API calls must go through the frontend proxy layer. See
`CLIENT_API_PROXY.md` for required prefixes and routing behavior.

## Backend Structure

```
backend/orchestrator/
├── agent/                      # Bounded agent orchestration
│   ├── controller.py          # Main agent loop
│   ├── guardrails.py          # Query shaping and contact scope helpers
│   ├── tool_executor.py       # Tool execution + validation pipeline
│   ├── response_guardrails.py # Output/malformed-call guardrails
│   ├── router.py              # Intent classification
│   ├── state.py               # Canonical state management
│   └── limits.py              # Stop rules, progress detection
├── tools/                      # Tool system
│   ├── registry.py            # Tool registration & grouping
│   ├── contracts.py           # JSON Schema validation
│   ├── handlers/              # Tool implementations
│   │   ├── memory.py         # search_memories, get_events, get_document
│   │   ├── homeassistant.py  # Home Assistant MCP integration
│   │   ├── resolution.py     # Entity resolution
│   │   ├── skills.py         # Skill script execution
│   │   ├── pdf.py            # Generated PDF artifacts + ingestion
│   │   ├── web.py            # Web search/fetch
│   │   └── system.py         # Bash commands
│   └── validators/            # Pre/post execution validation
│       ├── pre_execution.py
│       └── post_execution.py
├── observability/              # Tracing and logging
│   ├── logger.py             # Structured runtime + trace logging
│   └── log_stream.py         # In-memory log buffer + streaming
├── routes/                     # FastAPI route modules (feature-scoped)
│   ├── chat.py               # Ask/stream and conversation thread routes
│   ├── contacts.py           # Contacts, contact groups, and resolution routes
│   ├── places.py             # Place lookup and place-contact routes
│   ├── todos.py              # Todo routes
│   ├── events.py             # Event/meeting routes and ingest routes
│   ├── documents.py          # Document routes and ingest routes
│   ├── generated_pdfs.py     # Generated PDF download routes
│   ├── user.py               # Mobile settings/device + user facts routes
│   ├── system.py             # Versions, logs, and gate access routes
│   ├── automation.py         # Tools, skills, agents, and telegram webhook routes
│   ├── daily_briefing.py     # Daily briefing API routes
│   └── news.py               # News topics/preview/interactions routes
├── mcp/                        # Model Context Protocol
│   ├── client.py             # Generic MCP client
│   └── servers/
│       └── home_assistant.py
├── app.py                      # FastAPI entry point
├── llm.py                      # LLM orchestration
├── conversations.py            # Thread management
├── documents.py                # Document storage
├── events.py                   # Event management
├── contacts.py                 # Contact management
├── retrieval.py                # Vector search
├── user_facts.py               # User facts/preferences (persistent)
├── fact_extraction.py          # Background fact extraction pipeline
├── skills.py                   # Skill management
├── auth.py                     # Authentication
└── db.py                       # Database helpers
```

## Agent Architecture

### Request Flow

```
User Question → Intent Router → Conversational Profile Dispatch → Tool Visibility Policy → Agent Loop → Response
```

1. **IntentRouter** classifies the question (rule-based or LLM fallback)
2. **Conversational profile dispatch** selects the bounded profile (for example `memory_expert`)
3. **Tool groups** are emitted as routing metadata for observability/hints
4. **AgentController** runs the loop with limits enforcement
5. Each tool call goes through **pre-validation → execution → post-validation**
6. **AgentState** tracks facts, actions, and tool call history
7. **Planner/verifier checks** prevent premature final answers and require completion evidence when tools ran
8. **Adaptive model routing** selects fast vs smart model profile per step (always enabled)
9. **Tool execution coordinator** can run independent read-only tool calls in parallel batches

### Intent Types

| Intent | Tool Groups | Description |
|--------|-------------|-------------|
| `MEMORY_SEARCH` | memory, resolution | Search memories with optional entity resolution |
| `DATA_QUERY` | memory, resolution | Structured retrieval/counting (no SQL tools) |
| `CONTACT_LOOKUP` | resolution, memory | Find people, relationships, and related records |
| `WEB_SEARCH` | web | External information |
| `HOME_CONTROL` | home | Smart home automation |
| `SKILL_EXECUTION` | skills | Run skill scripts |
| `SYSTEM_COMMAND` | system | Bash/shell commands |
| `CONVERSATIONAL` | memory, resolution, web, pdf, ui | General chat and generated PDF/content creation |

### Tool Groups

| Group | Tools |
|-------|-------|
| `memory` | search_memories, get_events, get_document |
| `resolution` | resolve_contacts, lookup_contact, select_contacts, lookup_places, lookup_contact_places, lookup_place_contacts |
| `web` | web_search, fetch_web_page |
| `home` | home_assistant |
| `skills` | run_skill_script |
| `pdf` | create_pdf, ingest_generated_pdf |
| `ui` | emit_ui_directive |
| `system` | bash |

### Important Rules (Recent)

- **Single source of truth for tool groups**: keep router tool groups aligned with `backend/orchestrator/tools/registry.py`; do not maintain divergent copies.
- **Generated PDF flow**: use `create_pdf` for downloadable generated PDF artifacts and `ingest_generated_pdf` or `create_pdf(ingest_as_document=true)` when the user wants the created PDF added through the regular document ingest flow. Successful PDF creation should surface controller-derived `generated_files` metadata so web/mobile chat can render download chips.
- **Generated PDF content shape**: `create_pdf` content belongs in `body_markdown`; tables should be markdown pipe tables, not a separate structured parameter.
- **Prefer enums for internal control-flow values**: avoid raw string comparisons for statuses/actions/modes (for example limit actions, tool statuses, follow-up sources). Define shared enums and compare enum members to prevent typos and drift.
- **Tool visibility is runtime-enforced**: routing confidence tiers determine visible tool groups (`restricted`, `restricted_with_resolution`, or `full`) and can escalate to full tools on no-progress.
- **Adaptive model routing is always on**: per-step policy selects model/timeout using query complexity + runtime signals (route confidence tier, step count, tool count).
- **Planner/verifier loop is runtime-enforced**: controller tracks an execution plan and retries when final response lacks required evidence.
- **Parallel tool batches are supported**: independent read-only tools may execute concurrently via the tool execution coordinator.
- **Cross-tool no-progress dedupe is runtime-enforced**: equivalent read-only tool calls (same canonicalized arguments and goal context) should reuse prior results instead of re-executing, to prevent loop churn across memory/resolution/web retrieval paths.
- **Tool argument canonicalization is mandatory before execution**: normalize IDs and stable argument shapes centrally (validator/executor) so downstream handlers receive consistent inputs (for example prefixed document IDs).
- **Temporal memory ranking is two-stage**: for `search_memories` with `sort_order=newest|oldest`, rank by relevance first (shortlist + relevance gate) and then apply chronological ordering inside that candidate set to avoid recency-only noise.
- **LLM calls must use helpers**: all LLM requests and streams go through `backend/orchestrator/llm_helpers.py` (never call LLM endpoints via direct `requests`/`httpx` in app modules).
- **Structured LLM JSON must use response schemas**: runtime flows that require structured JSON should pass `response_format` via `build_json_schema_response_format()` and shared schemas from `backend/orchestrator/llm_json_schemas.py`; do not rely on embedding output JSON shapes in prompts except for behavioral examples that are not schema copies.
- **Controller context kwargs are global**: handlers are invoked with shared runtime context (`state`, `question`, `search_limit`, `user_email`, `conversation_history`). Every handler must accept these explicitly or via `**kwargs`.
- **Regression guard**: keep `backend/orchestrator/tests/tools/test_handlers/test_handler_signatures.py` passing to prevent `unexpected keyword argument` runtime failures.
- **`resolve_contacts` contract**: model-facing params should remain minimal (`text` only). Runtime identity/context (like `user_email`) is injected by the controller, not authored by the model.
- **Keep code modular**: avoid bloated files that mix unrelated concerns. When a file starts owning multiple responsibilities (for example, controller loop + guardrails + tool execution internals), extract cohesive modules early.
- **Documentation hygiene is mandatory**: when behavior, architecture, routing/profile selection, or runtime contracts change, update the corresponding docs in `backend/orchestrator/docs/architecture/` and this `AGENTS.md` in the same work.
- **Open-source anonymization is mandatory**: never copy real names, emails, places, companies, or other identifying strings from logs, screenshots, or production-like data into tests, fixtures, prompts, docs, or comments. Treat any string that came from a user message, exported log, screenshot, or local database as suspect by default; rewrite it to a clearly fake equivalent before it ever lands in code, even temporarily.
- **Commit gate for backend changes**: never commit or push backend code changes without running backend tests first in the same working session (at minimum affected tests; prefer full `pytest` when feasible). If linting is configured for the changed backend files, run lint checks too before committing.
- **Backend lint gate is mandatory**: before every backend commit or push, run the backend linter on the changed Python files (currently `ruff check` from `backend/orchestrator/.venv`, and use `--fix` when safe for import/order issues) and confirm it passes in the same session. Do not rely on tests alone.
- **Profile intent ownership**: conversational profiles should declare intent ownership via `supports_intent` on the profile/interface implementation; avoid hardcoding intent lists inside the central registry.
- **Agent-specific behavior docs**: detailed profile behavior for memory/disambiguation and briefing generation is documented in `backend/orchestrator/docs/agents/MEMORY_EXPERT.md` and `backend/orchestrator/docs/agents/DAILY_BRIEFING.md`.
- **Daily briefing news quality**: render per-article one-sentence summaries after bounded selection (LLM rewrite with deterministic fallback), enforce a per-topic selection hard cap (currently 10) to avoid single-topic dominance, guarantee a small floor of general headlines when available (currently 3), and use confidence-scored topic matching with accent-insensitive normalization to reduce wrong-cluster assignments.
- **Daily briefing summary policy**: keep the summary deterministic and compact (meeting count + pending todo count only), do not include preference-based idea suggestions, omit news digest text, and append weather outlook only when a latest user location from history is available.
- **News intelligence persistence**: keep story-level clusters/mentions and selected briefing news items in DB tables so ranking can use trend + novelty history and mobile interactions can be attributed to stable briefing item IDs.
- **Daily briefing event quality**: event prep summaries should prioritize non-obvious, context-grounded guidance, summarize the last 4 matching prior occurrences of the same meeting using the `summarize_memories` synthesis flow (not broad memory search), fall back to attendee-overlap history when title/recurrence matches are absent, and only use freeform current-context/research synthesis when no useful prior occurrences exist. Keep per-event prep single-responsibility: no web research on the main history-synthesis path, and no second event-summary rewrite after structured prep is built.
- **Validation semantics**: post-execution validation must treat clarification-required search/resolution results as `need_user_input`, not generic empty-result retries.
- **Validation feedback visibility is mandatory**: if tool results are compacted before being re-injected into the LLM context, preserve raw validation/error payloads (`valid=false`, `error`, `suggestions`, or handler error results) instead of flattening them into empty success-shaped payloads. Otherwise the model cannot repair the actual argument mistake.
- **UI DSL field kind contract**: command-generated `clarification_form` fields must use only supported UI DSL kinds (`text`, `textarea`, `number`, `date`, `time`, `datetime`, `email`, `url`, `select`). Do not emit aliases like `short_text`; validation rejects the whole directive and clients will only show fallback text.
- **Tool-result compaction must be budget-aware**: do not blindly ellipsize inspected event/document evidence before reinjecting it into the LLM. Preserve raw `get_events(action=by_ids)` and `get_document` payloads whenever the prompt budget allows, compact broad retrieval results only when needed, and degrade breadth before depth when trimming for context.
- **Action-scoped arg normalization**: when a tool parameter only applies to certain actions/modes, strip or ignore it for other actions during canonicalization/normalization so irrelevant schema defaults do not create repair loops (for example `get_events.limit` should not survive `action=by_ids`).
- **Temporal completion checks are source-aware**: query-goal verification should require detail inspection for the top candidate kind (`get_document` for documents, `get_events` for events) rather than forcing `get_events` for every "latest/last" question.
- **Goal completion should stay query-aligned**: when multiple evidence branches are explored, completion checks should prioritize candidates aligned with the user goal (for example temporal interaction questions should prefer event evidence over unrelated document branches).
- **Current-status progress queries should prefer newest aligned event evidence**: for questions like work in progress, due dates, or other evolving status checks, completion should prefer the newest relevant event candidate over a higher-scoring but unrelated document hit, and forced follow-up prompts should carry the exact candidate ID/tool args instead of asking the model to reconstruct them.
- **Simple contact-history queries should stay contact-first**: for questions like "when did I last meet Alex", prefer resolving the contact and using `get_events(action=by_time_span)` with `contact_ids`; do not add domain `tags`/`types` unless the user explicitly asked for a topical filter.
- **Open-ended event windows are allowed**: `get_events(action=by_time_span)` may use only `time_start` or only `time_end` for future-only or history-only queries; require at least one bound, not necessarily both.
- **Recap queries should stay bounded**: for explicit time-window recap/report questions (for example work summaries), prefer structured retrieval first, use domain tags as optional precision hints, and treat documents as first-class evidence for outcomes/decisions while events remain primary for chronology.
- **LLM observability parity is required**: sync and stream agent paths should emit comparable LLM request/response lifecycle logs so final-decision turns are debuggable in both modes.
- **Stream completion logs must capture final text and reasoning**: once a streamed LLM turn finishes, log the assembled final text preview and any returned reasoning preview so mis-grounded answers can be debugged after the fact.
- **Session command hygiene**: strip leading slash commands from user text before agent execution; commands are control signals, not semantic query content.
- **Slash-command preview flows**: `/contact` mirrors `/event` with controller-validated extraction, preview UI directives, clarification follow-ups, and explicit confirmation before mutating contacts, relationships, or contact-place links. `/contact` extraction should model plural graph operations (`contacts`, `relationships`, `contact_place_links`) rather than assuming a single main contact, preserve clarification conversation history plus prior extraction state across follow-ups, and prefer specific Title Case relationship labels/reciprocals when context supports them. Contact previews should use a single summary card with the event-style edit icon and full-screen draft editor, not inline edit forms.
- **Chat linked-items metadata**: agent `/ask` responses should include deterministic `linked_items` (for example event/document IDs + labels) derived from inspected tool results so clients can render click-through navigation to the referenced entity screens.
- **Linked-items protocol ownership**: linked-items behavior is prompt-level guidance in conversational profiles plus controller-side derivation (documented in `backend/orchestrator/docs/architecture/LINKED_ITEMS_DSL.md`); do not add a redundant skill/tool unless model-authored ordering/selection is explicitly required.
- **All-results limit policy**: when users explicitly ask for "all/everyone/entire" results, query handlers should honor unbounded retrieval semantics instead of silently capping to a fixed maximum.
- **Mobile session parity**: mobile chat should resolve session via backend main-session semantics (`/mobile/main-session` + `/mobile/ask` without explicit `thread_id` in normal flow) so idle timeout/reset rules match backend behavior. Main-session restore should not eagerly persist empty conversation threads; only create/store the thread once the first real user message is sent.
- **Location-aware place inference**: ask flows may enrich `client_context.location` with `inferred_location` (known-place proximity first, Geoapify reverse geocode fallback). Treat inferred place as approximate; in `/event` flows keep it as a disambiguation hint and avoid auto-prefilling `where` before matching unless the user explicitly provided a place.
- **Event place canonicalization**: when `/event` extraction yields a `where` value, resolve it against existing places (name + aliases, accent-insensitive fuzzy match, optional proximity boost) before creating a new place. On misses, Geoapify forward geocoding may enrich metadata (city/country/lat/lon) for the new place.
- **`/event` similar-event matching must stay lexically grounded**: same-day/place/participant overlap alone is not enough to auto-update an existing event. Keep a lexical anchor from the new text/title, reject strong title conflicts (for example lunch vs dinner) unless the user explicitly asked to update an existing event, and prefer create over risky auto-merge for recurring home/family gatherings.
- **`/event` exact-day matching should relax hard filters before giving up**: if strict people+place retrieval misses an obvious existing event, retry exact-day lookup with place-only, per-person, and text-only passes so updates still work when a stored event is missing one attendee link.
- **`/event` matching should be date/time-first when available**: if the user provides a concrete date or time, build a bounded candidate set from that temporal window first and rank within that smaller set before widening to semantic search across the whole corpus.
- **Collective selector extraction must not broaden family mentions**: contact-resolution selector extraction should only accept explicit collective intents (for example team/company/everyone/@domain) and must reject family/relationship-style groups like children/kids/family so `/event` participant lists do not explode into unrelated contacts.
- **Event photos live in Immich**: event image attachments should upload/link to Immich assets rather than local document storage. Preserve original picker file bytes/metadata where possible, send a stable duplicate-detection signal to Immich, and refresh detected people from Immich on later event reads because face clustering can finish asynchronously. When Immich returns tagged people, map those person IDs back to contacts and merge them into the event graph. Mobile chat media attachments are currently carried through `/event` preview, clarification, and confirmation so confirmed events can link uploaded photos after creation/update.
- **Meeting transcript ingest**: `POST /ingest/meetings/transcript` uses normal bearer-token auth with a mandatory current user, stores the payload in the generic `async_jobs` queue, immediately acknowledges receipt, and processes the latest queued payload after a 30-second debounce. New submissions for the same meeting replace the pending job and restart the debounce; failures are logged and retried after one minute. Processing skips regeneration when the incoming `transcript_hash` matches the stored event raw metadata; otherwise it matches the backing calendar meeting when possible, consolidates attendee contacts by exact email/name evidence, lightly normalizes transcript segments by time-ordering and merging adjacent same-speaker fragments before summarization, injects names/aliases/emails for involved people into the summarizer prompt, stores the raw transcript payload in event metadata, writes an LLM-generated discussion summary plus structured action items to event metadata, projects action items on event detail reads for mobile, and creates linked todos for action items assigned to the authenticated user by email or identifier match. With GPT-OSS 128k context, transcript summarization currently sends up to 300k transcript characters before any future chunking/synthesis path is needed.
- **Meeting speaker voice profiles**: `POST /meetings/speakers/match` accepts transient per-meeting speaker embeddings and returns backend-owned auto-label or suggestion decisions before note enhancement; participant contacts are a ranking prior, not a search restriction. Durable contact voice profiles may contain multiple internal voice clusters per contact, and matching ranks each contact by its best cluster so microphone/device/noise variants do not collapse into one blurred centroid. `POST /meetings/speakers/confirm` is the only path that trains durable contact voice profiles, records rejected match evidence when users correct labels, and ignores unassigned/unknown speakers. Raw embeddings and profile centroids stay server-internal.
- **Place follow-up continuity**: when a tool resolves a known place, persist that place context in assistant metadata and inject it on deictic follow-ups (for example "here"/"this place") so subsequent tool calls use `place_id` continuity instead of fragile address-text matching.
- **DB migration policy**: treat `backend/db/init.sql` as bootstrap schema for fresh databases. Incremental runtime-safe changes belong in ordered SQL files under `backend/orchestrator/db_migrations/`, which are auto-applied at orchestrator startup.
- **Import-time side effects**: avoid filesystem writes (like `mkdir`) during module import. Create directories lazily at the point of file operations.
- **Entity ID hygiene**: when generating IDs from user-provided names/titles (contacts, places, etc.), always slug/sanitize to safe URL/path characters (for example lowercase `a-z0-9-`) and avoid reserved characters like `#`, `?`, `/`, `%`.
- **Contact disambiguation policy**: ambiguity auto-resolution strictness is controlled by `CONTACT_DISAMBIGUATION_STRICTNESS` (`strict`/`balanced`/`lenient`).
- **Explicit new-contact intent wins over fuzzy matches**: when the user says a named person is a new contact or is not in the database, mark that mention as `new_contacts` before direct fuzzy matching. Do not substitute a weak single fuzzy match just because only one candidate was returned.
- **Skills vs prompts policy**: behavioral guidance that overlaps with tool contracts or profile prompts belongs in the prompt, not as a separate skill definition. Skill definitions (`skill_definitions/`) are reserved for genuinely unique guidance not covered elsewhere (e.g. `tagging-guide`). Do not create skills that restate tool contracts or profile protocol.
- **Logging policy**: never use `print` in orchestrator runtime code (only in scripts/tests). Use `logging.getLogger(__name__)` with `debug/info/warning/error` (or `logger.log(DECISION_LEVEL, ...)` for decisions). Logging must flow through `observability/log_stream.py` so frontend log streaming can filter by level, and normal orchestrator logs should also remain visible in the backend process output for local debugging. For streaming endpoints, rely on authenticated user context (no service API key) unless explicitly required.
- **Componentize aggressively**: never let a single file grow into a monolith. Extract reusable UI components (web and mobile), utility functions, hooks, and sub-modules into their own files as soon as a file starts handling multiple concerns. For React (web and mobile): split pages into small, focused components; co-locate them in a nearby `components/` folder or a feature-scoped directory. For backend: extract helpers, data transforms, and sub-handlers into dedicated modules. A file doing layout + data fetching + business logic + styling is a sign it needs to be broken up. Aim for each file to have a single clear responsibility.
- **FastAPI route modularity**: keep endpoint definitions in feature-scoped modules under `backend/orchestrator/routes/` and keep `backend/orchestrator/app.py` focused on app bootstrap, shared middleware/dependencies, and router composition.
- **Document ingestion pipeline**: document parsing should flow through `backend/orchestrator/document_processing/` (parser selection, normalization, and structured chunking) before embeddings are generated. Keep parser fallbacks resilient (never hard-fail on unsupported formats), persist parser metadata in `raw_metadata`, and use `scripts/reembed_all.py` after parser/chunking improvements to refresh stored embeddings. Image uploads should go through OCR-capable parsers when available, and non-text binaries must not be decoded into pseudo-text during fallback parsing.
- **Reuse before creating**: before building a new UI element, search `mobile/components/` and `frontend/web/src/` for existing components that serve the same purpose. Extend an existing component with a new variant or prop rather than creating a one-off inline implementation. Key mobile primitives: `AppPressable` (tap primitive), `Button` (labeled button with `primary`/`secondary`/`clear`/`danger` variants), `FloatingSaveButton` (FAB), `Card` (container). The same principle applies to backend utilities — check existing helpers before writing new ones.
- **Accent-insensitive text matching is mandatory**: any code that compares, searches, or fuzzy-matches user-provided text (names, titles, tags, comments, or any free-text field) must strip diacritics before comparison. Use `normalize_search_text()` from `search_normalization.py` for Python-side comparisons and PostgreSQL `unaccent()` for SQL queries. Never use raw `.lower()` or `LOWER()` alone for text matching — "Jordan" must match "José", "São Paulo" must match "Sao Paulo". The `unaccent` extension is installed in the database schema (`init.sql`).
- **Pre-resolution results must be surfaced to the agent loop**: when the controller runs `pre_resolve_contacts` before the tool loop, the outcome (whether contacts were found or not) must be injected into the LLM context via `build_contact_scope_context()`. This includes the "no matches found" case — otherwise the LLM will redundantly call `resolve_contacts` again for the same names.
- **Contacts + personal-document routing should stay memory-first**: queries about a named relative/contact plus artifacts like prescriptions, lab results, glasses specs, reports, invoices, IDs, or "a doc/document/file" should prefer `memory_search`, not `web_search`, and should usually pre-resolve the referenced contact before retrieval.
- **Contact-resolution hot path must stay minimal**: controller pre-resolution and model-invoked `resolve_contacts` should use the resolver's minimal mode (extract mentions, resolve contacts/selectors, surface ambiguity) and skip enrichment like profession inference or relationship suggestion generation. Reserve full enrichment for flows that actually consume it (for example event/entity creation).
- **Prefer deterministic contact short-circuits before LLM extraction**: straightforward references such as exact names, `my <relationship>` phrases, and deterministic collective selectors should be resolved via regex + DB/rule lookups first. Only fall back to the heavier LLM extraction/disambiguation path for pronouns, nested/ambiguous references, or unresolved spans.
- **User facts are persistent cross-conversation knowledge**: the `user_facts` table stores atomic personal facts (preferences, traits, habits, opinions, constraints, goals) that don't belong in contacts, events, places, todos, or documents. Facts are automatically extracted from conversations via a background pipeline (`fact_extraction.py`) and injected into LLM prompts via `get_user_facts_context()`.
- **User facts injection policy**: any LLM call site that produces user-facing content, resolves user intent, or makes decisions influenced by user preferences MUST inject user context via `get_user_facts_context(user_email, query, scope=...)`. The context is split into (1) **hard rules** (`fact_mode=hard_rule`) that are deterministic and scope-aware (for example entity alias mapping in contact resolution), and (2) **soft facts** (`fact_mode=soft`) that are relevance-ranked hints. Hard rules should be applied in deterministic controller/handler logic before LLM disambiguation when possible; soft facts remain prompt guidance. Utility LLM calls (title generation, tag suggestion, translation, validation) do NOT need facts. When adding a new LLM call site, ask: "Would knowing user preferences/traits/aliases change this output?" — if yes, inject facts.
- **Rule-scope enum policy**: do not use ad-hoc scope strings for hard rules. Use `RuleScope` values from `backend/orchestrator/user_fact_rules.py` (for example `RuleScope.CONTACT_RESOLUTION`, `RuleScope.EVENT_COMMAND`, `RuleScope.AGENT_GLOBAL`) in runtime code and prompt builders.
- **User facts extraction boundary**: the extraction pipeline must NOT extract information that belongs in other entities (relationships → contacts, specific events → events, tasks → todos, locations → places). The extraction prompt includes the user's existing contacts as context to prevent duplication.
- **User facts vs AgentState.known_facts**: these are completely different. `AgentState.known_facts` is transient per-request working memory (tool result summaries). `user_facts` is persistent long-term memory across all conversations. They occupy different positions in the prompt and serve different purposes.
- **User facts retrieval scoring**: facts are ranked by a composite score: `0.5 * semantic_similarity + 0.25 * (importance/10) + 0.25 * recency_decay`. Recency uses exponential decay based on `last_accessed_at` with a ~30-day half-life.
- **User facts extraction runs as BackgroundTask**: triggered after conversation persistence in `llm.py` via a callback from `app.py`. It must never block or crash user-facing responses. Short/trivial messages and slash commands skip extraction.

### Limits & Safety

- **max_steps**: 15 (agent loop iterations)
- **max_tool_calls**: 20 (total tool executions)
- **max_repairs**: 2 (validation repair attempts)
- **No-progress detection**: Stops on repeated identical calls or empty results

## Backend Endpoints

### Conversation API
- `POST /ask` – Ask a question (returns answer + state)
- `POST /ask/stream` – Streaming responses
- Mobile `/ask/stream` sends a `chat-reply` push notification only when the stream disconnected before completion; notification data includes `threadId` and `isMainSession` so taps route to `/home` or `/chat/[threadId]`.
- `GET /threads` – List threads
- `POST /threads` – Create thread
- `GET /threads/{id}` – Get thread

### Data Ingestion
- `POST /ingest/contact` – Add contact
- `POST /ingest/place` – Add place
- `POST /ingest/todo` – Add todo
- `POST /ingest/event` – Add event
- `POST /ingest/meetings/transcript` – Queue authenticated meeting transcript
- `POST /ingest/document` – Upload document

### Data Access
- `GET /documents` – List documents
- `POST /documents/search` – Search documents
- `GET /contacts` – List contacts
- `GET /contact-groups` – List contact groups
- `POST /contact-groups` – Create contact group
- `GET /contact-groups/{id}` – Get contact group
- `DELETE /contact-groups/{id}` – Archive contact group
- `DELETE /events/{id}` – Delete an event
- `DELETE /mobile/events/{id}` – Delete an event from mobile clients
- `GET /meetings/{id}` – Get meeting

### User Facts
- `GET /user/facts` – List all known facts about the user
- `PUT /user/facts/{id}` – Update/correct a fact
- `DELETE /user/facts/{id}` – Delete a fact

### Mobile Settings
- `GET /mobile/settings` – Legacy push notification summary
- `GET /mobile/settings/notifications` – List per-type notification settings and channels
- `PUT /mobile/settings/notifications/{notification_type}` – Update channels for one notification type
- `POST /mobile/devices/register` – Register Expo push token for mobile device
- `DELETE /mobile/devices/unregister` – Unregister Expo push token for mobile device
- `POST /mobile/location` – Append a user location history entry when movement exceeds the dedupe threshold
- `GET /mobile/location` – Read the latest user location

### Daily Briefings
- `GET /mobile/briefings/daily` – Get daily briefing or immediate pending status (auto-enqueues generation)
- `GET /mobile/briefings/latest` – Get latest generated briefing
- `POST /agents/daily-briefing/run` – Service API key endpoint to enqueue generation
- `POST /debug/daily-briefing/event-summary` – Run daily-briefing meeting prep synthesis for one event and inspect intermediate debug data

### News Topics
- `GET /news-topics` – List tracked topics
- `POST /news-topics` – Create/update topic
- `DELETE /news-topics/{id}` – Delete topic
- `POST /news/interactions` – Record article open/thumbs feedback

### Webhooks
- `POST /webhooks/contacts` – Sync/unlink contacts
- `POST /webhooks/telegram/messages` – Telegram messages

### System
- `GET /system/versions` – Service versions
- `GET /system/logs` – Read recent in-memory runtime logs
- `GET /system/logs/stream` – Stream runtime logs over SSE
- `GET /system/notifications/devices` – List the current user's registered push devices
- `POST /system/notifications/test` – Send a direct push test to one registered device
- `POST /access/gate` – Face recognition (Immich)

## Frontend Structure

```
frontend/web/src/
├── app/
│   ├── page.tsx              # Main chat UI
│   ├── api/
│   │   ├── auth/[...nextauth]/  # NextAuth config
│   │   └── orchestrator/[...path]/  # API proxy
│   ├── auth/signin/          # Sign-in page
│   ├── contacts/             # Contact management
│   ├── documents/            # Document management
│   ├── meetings/             # Meeting transcripts
│   ├── todos/                # Todo management
│   └── system/               # System info
└── lib/
    └── api.ts                # Typed API client
```

## Configuration

### Backend Environment

```bash
# LLM Configuration
LLM_BASE_URL=http://localhost:11434    # Ollama or OpenAI-compatible
LLM_CHAT_MODEL_FAST=mistral
LLM_CHAT_MODEL_SMART=gpt-4o
OLLAMA_CHAT_KEEP_ALIVE=-1             # Applied to both configured chat models
LLM_API_KEY=                           # Optional
LLM_TIMEOUT=120
LLM_CHAT_MODEL_FAST=mistral            # Optional fast profile override
LLM_CHAT_MODEL_SMART=gpt-4o            # Optional smart profile override

# Adaptive Model Routing (always enabled)
AGENT_MODEL_ROUTING_COMPLEXITY_THRESHOLD=3  # Smart model starts low, escalates effort
AGENT_MODEL_ROUTING_STEP_THRESHOLD=4
AGENT_MODEL_ROUTING_TIMEOUT_BOOST_SECONDS=30

# Agent Configuration
AGENT_MAX_STEPS=15
AGENT_MAX_TOOL_CALLS=20
AGENT_MAX_REPAIRS=5
AGENT_ENABLE_INTENT_ROUTING=true
AGENT_ENABLE_VALIDATION=true

# Tracing
AGENT_TRACE_ENABLED=true
AGENT_TRACE_LEVEL=info                 # debug|info|decision|warning|error

# Database
DATABASE_URL=postgresql://user:pass@localhost/db

# Auth
GOOGLE_CLIENT_IDS=web-client-id.apps.googleusercontent.com,desktop-client-id.apps.googleusercontent.com
# Required: non-empty comma-separated allowlist
ALLOWED_USERS=user@example.com

# Optional: Home Assistant
HA_URL=http://homeassistant.local:8123
HA_TOKEN=long_lived_access_token

# Optional: Web Search
LANGSEARCH_API_KEY=xxx
TAVILY_API_KEY=xxx
```

### Frontend Environment

```bash
NEXTAUTH_SECRET=random-string
NEXTAUTH_URL=http://localhost:3000
# NextAuth uses the web OAuth client. GOOGLE_CLIENT_ID is preferred; if omitted,
# the frontend falls back to the first GOOGLE_CLIENT_IDS entry for compatibility.
GOOGLE_CLIENT_ID=xxx.apps.googleusercontent.com
# GOOGLE_CLIENT_IDS=web-client-id.apps.googleusercontent.com,mobile-client-id.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=xxx
BACKEND_API_BASE=http://localhost:8000
ALLOWED_USERS=user@example.com
```

See `backend/env.template` and `frontend/web/env.template` for full templates.

## Local Development

```bash
# Backend
cd backend/orchestrator
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app:api --reload

# Frontend
cd frontend/web
npm install
npm run dev

# Mobile
cd mobile
npm install
npm run dev

# Full stack
docker compose up --build
```

### Python Virtualenv (backend)

- Always run backend Python commands inside `backend/orchestrator/.venv`.
- If `.venv` does not exist yet, create and bootstrap it:

```bash
cd backend/orchestrator
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/pip install -r requirements-dev.txt
```

- You can either activate the venv, or run tools directly from it:

```bash
# Option 1: activate shell
cd backend/orchestrator
source .venv/bin/activate
pytest tests/commands/test_event_confirm_groups.py

# Option 2: explicit venv binaries (no activation)
cd backend/orchestrator
.venv/bin/python -m pytest tests/commands/test_event_confirm_groups.py
.venv/bin/uvicorn app:api --reload
```

- Avoid using global/system `python`, `pip`, `pytest`, or `uvicorn` for backend work in this repo.

### Test Commands

- Always run backend tests inside `backend/orchestrator/.venv`.
- Example:

```bash
cd backend/orchestrator
source .venv/bin/activate
pytest tests/agent/test_controller.py tests/integration/test_full_flow.py tests/tools/test_validators.py
```

## Test data & examples — always anonymize

This repo is open-source. **Never** use real people, real addresses, real
domains, or real businesses in code, tests, fixtures, comments, prompts, eval
cases, UI placeholders, or docs.

- Names: use generic invented full names (e.g. `Alex Carter`, `Dana Lewis`,
  `Robin Lake`, `Morgan Brooks`). Keep families/groups internally consistent
  across files so tests still make sense.
- Emails: `@example.com`, `@example.org`, `@example.invalid` (RFC 6761) only.
  Never `@gmail.com` / `@yahoo.com` / a real domain you operate.
- Phone numbers: use the RFC 6761 reserved block — `+1 555 555 01XX`.
- Addresses: invent street names (`12 Maple Street, Harborview`). Never copy
  a real address — Portuguese `Rua …` patterns and real towns (`Harborview`,
  `Northgate`, `Alder Point` are good fake examples; avoid real towns entirely).
- Companies / venues: use clearly-fake names (`Acme`, `Beacon`, `The Tide`).
  Do not name real restaurants, neighborhoods, or employers.
- Domains: `acme.example` / `example.com`. The TLD `.example` is reserved
  for documentation per RFC 2606.
- LLM prompts and few-shot examples count as code — same rules apply. If a
  prompt currently references a real entity, replace it before commit.
- Log-derived examples are especially easy to leak. Never paste names, emails,
  domains, venues, or place names from a bug report or system log into a test
  or prompt and plan to anonymize later; anonymize first, then write the test.
- Before finishing any change that adds or edits tests/prompts/examples, do a
  quick self-check: "Did any literal string here originate from user data or
  logs?" If yes, replace it with a fake equivalent before running final checks.

When in doubt, ask: would I be comfortable if this string ended up on
HackerNews? If no, anonymize.

## Data & Storage

- **Database schema**: `backend/db/init.sql`
- **Incremental migrations**: `backend/orchestrator/db_migrations/` (applied automatically at startup when `DB_AUTO_MIGRATE=true`)
- **Document files**: `backend/orchestrator/storage/documents` (volume-mounted)
- **Vector embeddings**: pgvector in PostgreSQL
- **Junction tables**: entity associations use dedicated junction tables with composite PKs and FK cascades: `event_contacts`, `todo_contacts`, `todo_events`, `todo_places`, `contact_places`, `document_contacts`. The `events` table does **not** have a `people` column — use `event_contacts` and `db.fetch_event_people()` instead. Person-scoped document retrieval/counting should use `document_contacts` rather than only text matching.

## Key Implementation Files

| Purpose | File |
|---------|------|
| Agent loop | `backend/orchestrator/agent/controller.py` |
| Intent routing | `backend/orchestrator/agent/router.py` |
| Conversational profile dispatch | `backend/orchestrator/agents/registry.py` |
| Memory expert profile | `backend/orchestrator/agents/memory_expert/` |
| Tool registry | `backend/orchestrator/tools/registry.py` |
| Tool contracts | `backend/orchestrator/tools/contracts.py` |
| Validation | `backend/orchestrator/tools/validators/` |
| Tracing/Logging | `backend/orchestrator/observability/logger.py` |
| LLM orchestration | `backend/orchestrator/llm.py` |
| Model routing policy | `backend/orchestrator/agent/model_routing.py` |
| Planner/verifier policy | `backend/orchestrator/agent/planning_policy.py` |
| Vector search | `backend/orchestrator/retrieval.py` |
| User facts service | `backend/orchestrator/user_facts.py` |
| Fact extraction pipeline | `backend/orchestrator/fact_extraction.py` |
| News feed aggregation | `backend/orchestrator/news_feeds.py` |
| Daily briefing agent | `backend/orchestrator/agents/daily_briefing/executor.py` |
| Frontend API client | `frontend/web/src/lib/api.ts` |
