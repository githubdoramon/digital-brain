Digital Brain – Quick Context

## Overview

Personal memory orchestrator with a **bounded agent architecture**. Backend: FastAPI (`backend/orchestrator`) + PostgreSQL/pgvector. Frontend: Next.js App Router (`frontend/web`) with NextAuth Google OAuth. Mobile app: React Native/Expo (`mobile`). Supports OpenAI-compatible LLM APIs (local Ollama or cloud). Optional Tavily web search and Home Assistant integration.

**Core principle**: "The model proposes. The controller validates, executes, and decides."

**Terminology**: When a user says "app", they mean the mobile app in `mobile`, not the web frontend.

**Mobile routing convention**: For dynamic mobile routes, prefer folder-based segments with `index.tsx` (for example `mobile/app/contacts/[contactId]/index.tsx`) so nested subroutes can be added without migrating route structure later.

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
│   │   ├── web.py            # Web search/fetch
│   │   └── system.py         # Bash commands
│   └── validators/            # Pre/post execution validation
│       ├── pre_execution.py
│       └── post_execution.py
├── observability/              # Tracing and logging
│   ├── logger.py             # Structured runtime + trace logging
│   └── log_stream.py         # In-memory log buffer + streaming
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
| `CONVERSATIONAL` | (none) | General chat |
| `COMPLEX` | (multiple) | Multi-step tasks |

### Tool Groups

| Group | Tools |
|-------|-------|
| `memory` | search_memories, get_events, get_document |
| `resolution` | resolve_contacts, lookup_contact, select_contacts, lookup_places, lookup_contact_places, lookup_place_contacts |
| `web` | web_search, fetch_web_page |
| `home` | home_assistant |
| `skills` | run_skill_script |
| `ui` | emit_ui_directive |
| `system` | bash |

### Important Rules (Recent)

- **Single source of truth for tool groups**: keep router tool groups aligned with `backend/orchestrator/tools/registry.py`; do not maintain divergent copies.
- **Prefer enums for internal control-flow values**: avoid raw string comparisons for statuses/actions/modes (for example limit actions, tool statuses, follow-up sources). Define shared enums and compare enum members to prevent typos and drift.
- **Tool visibility is runtime-enforced**: routing confidence tiers determine visible tool groups (`restricted`, `restricted_with_resolution`, or `full`) and can escalate to full tools on no-progress.
- **Adaptive model routing is always on**: per-step policy selects model/timeout using query complexity + runtime signals (route confidence tier, step count, tool count).
- **Planner/verifier loop is runtime-enforced**: controller tracks an execution plan and retries when final response lacks required evidence.
- **Parallel tool batches are supported**: independent read-only tools may execute concurrently via the tool execution coordinator.
- **Temporal memory ranking is two-stage**: for `search_memories` with `sort_order=newest|oldest`, rank by relevance first (shortlist + relevance gate) and then apply chronological ordering inside that candidate set to avoid recency-only noise.
- **LLM calls must use helpers**: all LLM requests and streams go through `backend/orchestrator/llm_helpers.py` (never call LLM endpoints via direct `requests`/`httpx` in app modules).
- **Controller context kwargs are global**: handlers are invoked with shared runtime context (`state`, `question`, `search_limit`, `user_email`, `conversation_history`). Every handler must accept these explicitly or via `**kwargs`.
- **Regression guard**: keep `backend/orchestrator/tests/tools/test_handlers/test_handler_signatures.py` passing to prevent `unexpected keyword argument` runtime failures.
- **`resolve_contacts` contract**: model-facing params should remain minimal (`text` only). Runtime identity/context (like `user_email`) is injected by the controller, not authored by the model.
- **Keep code modular**: avoid bloated files that mix unrelated concerns. When a file starts owning multiple responsibilities (for example, controller loop + guardrails + tool execution internals), extract cohesive modules early.
- **Documentation hygiene is mandatory**: when behavior, architecture, routing/profile selection, or runtime contracts change, update the corresponding docs in `backend/orchestrator/docs/architecture/` and this `AGENTS.md` in the same work.
- **Profile intent ownership**: conversational profiles should declare intent ownership via `supports_intent` on the profile/interface implementation; avoid hardcoding intent lists inside the central registry.
- **Agent-specific behavior docs**: detailed profile behavior for memory/disambiguation and briefing generation is documented in `backend/orchestrator/docs/agents/MEMORY_EXPERT.md` and `backend/orchestrator/docs/agents/DAILY_BRIEFING.md`.
- **Daily briefing news quality**: render per-article one-sentence summaries after bounded selection (LLM rewrite with deterministic fallback), append a short news digest paragraph to the overall briefing summary when selected news exists, and use confidence-scored topic matching with accent-insensitive normalization to reduce wrong-cluster assignments.
- **Daily briefing event quality**: event prep summaries should prioritize non-obvious, context-grounded guidance, filter low-value generic advice, and clearly separate current upcoming-event context from historical similar-event references.
- **Validation semantics**: post-execution validation must treat clarification-required search/resolution results as `need_user_input`, not generic empty-result retries.
- **Temporal completion checks are source-aware**: query-goal verification should require detail inspection for the top candidate kind (`get_document` for documents, `get_events` for events) rather than forcing `get_events` for every "latest/last" question.
- **Session command hygiene**: strip leading slash commands from user text before agent execution; commands are control signals, not semantic query content.
- **All-results limit policy**: when users explicitly ask for "all/everyone/entire" results, query handlers should honor unbounded retrieval semantics instead of silently capping to a fixed maximum.
- **Mobile session parity**: mobile chat should resolve session via backend main-session semantics (`/mobile/main-session` + `/mobile/ask` without explicit `thread_id` in normal flow) so idle timeout/reset rules match backend behavior.
- **Location-aware place inference**: ask flows may enrich `client_context.location` with `inferred_location` (known-place proximity first, Geoapify reverse geocode fallback). Treat inferred place as approximate, and in `/event` flows only prefill `where` when the user did not explicitly provide a location.
- **Event place canonicalization**: when `/event` extraction yields a `where` value, resolve it against existing places (name + aliases, accent-insensitive fuzzy match, optional proximity boost) before creating a new place. On misses, Geoapify forward geocoding may enrich metadata (city/country/lat/lon) for the new place.
- **Place follow-up continuity**: when a tool resolves a known place, persist that place context in assistant metadata and inject it on deictic follow-ups (for example "here"/"this place") so subsequent tool calls use `place_id` continuity instead of fragile address-text matching.
- **DB migration policy**: treat `backend/db/init.sql` as bootstrap schema for fresh databases. Incremental runtime-safe changes belong in ordered SQL files under `backend/orchestrator/db_migrations/`, which are auto-applied at orchestrator startup.
- **Import-time side effects**: avoid filesystem writes (like `mkdir`) during module import. Create directories lazily at the point of file operations.
- **Entity ID hygiene**: when generating IDs from user-provided names/titles (contacts, places, etc.), always slug/sanitize to safe URL/path characters (for example lowercase `a-z0-9-`) and avoid reserved characters like `#`, `?`, `/`, `%`.
- **Contact disambiguation policy**: ambiguity auto-resolution strictness is controlled by `CONTACT_DISAMBIGUATION_STRICTNESS` (`strict`/`balanced`/`lenient`).
- **Skills vs prompts policy**: behavioral guidance that overlaps with tool contracts or profile prompts belongs in the prompt, not as a separate skill definition. Skill definitions (`skill_definitions/`) are reserved for genuinely unique guidance not covered elsewhere (e.g. `tagging-guide`). Do not create skills that restate tool contracts or profile protocol.
- **Logging policy**: never use `print` in orchestrator runtime code (only in scripts/tests). Use `logging.getLogger(__name__)` with `debug/info/warning/error` (or `logger.log(DECISION_LEVEL, ...)` for decisions). Logging must flow through `observability/log_stream.py` so frontend log streaming can filter by level. For streaming endpoints, rely on authenticated user context (no service API key) unless explicitly required.
- **Componentize aggressively**: never let a single file grow into a monolith. Extract reusable UI components (web and mobile), utility functions, hooks, and sub-modules into their own files as soon as a file starts handling multiple concerns. For React (web and mobile): split pages into small, focused components; co-locate them in a nearby `components/` folder or a feature-scoped directory. For backend: extract helpers, data transforms, and sub-handlers into dedicated modules. A file doing layout + data fetching + business logic + styling is a sign it needs to be broken up. Aim for each file to have a single clear responsibility.
- **Document ingestion pipeline**: document parsing should flow through `backend/orchestrator/document_processing/` (parser selection, normalization, and structured chunking) before embeddings are generated. Keep parser fallbacks resilient (never hard-fail on unsupported formats), persist parser metadata in `raw_metadata`, and use `scripts/reembed_all.py` after parser/chunking improvements to refresh stored embeddings.
- **Reuse before creating**: before building a new UI element, search `mobile/components/` and `frontend/web/src/` for existing components that serve the same purpose. Extend an existing component with a new variant or prop rather than creating a one-off inline implementation. Key mobile primitives: `AppPressable` (tap primitive), `Button` (labeled button with `primary`/`secondary`/`clear`/`danger` variants), `FloatingSaveButton` (FAB), `Card` (container). The same principle applies to backend utilities — check existing helpers before writing new ones.
- **Accent-insensitive text matching is mandatory**: any code that compares, searches, or fuzzy-matches user-provided text (names, titles, tags, comments, or any free-text field) must strip diacritics before comparison. Use `normalize_search_text()` from `search_normalization.py` for Python-side comparisons and PostgreSQL `unaccent()` for SQL queries. Never use raw `.lower()` or `LOWER()` alone for text matching — "Jordan" must match "José", "São Paulo" must match "Sao Paulo". The `unaccent` extension is installed in the database schema (`init.sql`).
- **Pre-resolution results must be surfaced to the agent loop**: when the controller runs `pre_resolve_contacts` before the tool loop, the outcome (whether contacts were found or not) must be injected into the LLM context via `build_contact_scope_context()`. This includes the "no matches found" case — otherwise the LLM will redundantly call `resolve_contacts` again for the same names.
- **User facts are persistent cross-conversation knowledge**: the `user_facts` table stores atomic personal facts (preferences, traits, habits, opinions, constraints, goals) that don't belong in contacts, events, places, todos, or documents. Facts are automatically extracted from conversations via a background pipeline (`fact_extraction.py`) and injected into LLM prompts via `get_user_facts_context()`.
- **User facts injection policy**: any LLM call site that produces user-facing content, resolves user intent, or makes decisions influenced by user preferences MUST inject user facts via `get_user_facts_context(user_email, query)`. Currently injected in: agent message builders (`main`, `memory_expert`), `/event` command entity extraction, daily briefing pipeline (per-event synthesis + markdown generation), and contact resolution (people extraction + LLM disambiguation). User facts like name aliases ("Dana means Dana Lewis"), relationship shortcuts, and contextual hints directly affect how text is interpreted. Utility LLM calls (title generation, tag suggestion, translation, validation) do NOT need facts. When adding a new LLM call site, ask: "Would knowing user preferences/traits/aliases change this output?" — if yes, inject facts.
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
- `GET /threads` – List threads
- `POST /threads` – Create thread
- `GET /threads/{id}` – Get thread

### Data Ingestion
- `POST /ingest/contact` – Add contact
- `POST /ingest/place` – Add place
- `POST /ingest/todo` – Add todo
- `POST /ingest/event` – Add event
- `POST /ingest/events/notes` – Add meeting notes (API key)
- `POST /ingest/document` – Upload document

### Data Access
- `GET /documents` – List documents
- `POST /documents/search` – Search documents
- `GET /contacts` – List contacts
- `GET /contact-groups` – List contact groups
- `POST /contact-groups` – Create contact group
- `GET /contact-groups/{id}` – Get contact group
- `DELETE /contact-groups/{id}` – Archive contact group
- `GET /meetings/{id}` – Get meeting

### User Facts
- `GET /user/facts` – List all known facts about the user
- `PUT /user/facts/{id}` – Update/correct a fact
- `DELETE /user/facts/{id}` – Delete a fact

### News Topics
- `GET /news-topics` – List tracked topics
- `POST /news-topics` – Create/update topic
- `DELETE /news-topics/{id}` – Delete topic

### Webhooks
- `POST /webhooks/contacts` – Sync/unlink contacts
- `POST /webhooks/telegram/messages` – Telegram messages

### System
- `GET /system/versions` – Service versions
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
LLM_CHAT_MODEL=mistral
LLM_API_KEY=                           # Optional
LLM_TIMEOUT=120
LLM_CHAT_MODEL_FAST=mistral            # Optional fast profile override
LLM_CHAT_MODEL_SMART=gpt-4o            # Optional smart profile override

# Adaptive Model Routing (always enabled)
AGENT_MODEL_ROUTING_COMPLEXITY_THRESHOLD=3
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
GOOGLE_CLIENT_ID=xxx.apps.googleusercontent.com
ALLOWED_USERS=user@example.com

# Optional: Home Assistant
HA_URL=http://homeassistant.local:8123
HA_TOKEN=long_lived_access_token

# Optional: Web Search
TAVILY_API_KEY=xxx
```

### Frontend Environment

```bash
NEXTAUTH_SECRET=random-string
NEXTAUTH_URL=http://localhost:3000
GOOGLE_CLIENT_ID=xxx.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=xxx
BACKEND_API_BASE=http://localhost:8000
ALLOWLIST=user@example.com
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

## Data & Storage

- **Database schema**: `backend/db/init.sql`
- **Incremental migrations**: `backend/orchestrator/db_migrations/` (applied automatically at startup when `DB_AUTO_MIGRATE=true`)
- **Document files**: `backend/orchestrator/storage/documents` (volume-mounted)
- **Vector embeddings**: pgvector in PostgreSQL
- **Junction tables**: entity associations use dedicated junction tables with composite PKs and FK cascades: `event_contacts`, `todo_contacts`, `todo_events`, `todo_places`, `contact_places`. The `events` table does **not** have a `people` column — use `event_contacts` and `db.fetch_event_people()` instead.

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
