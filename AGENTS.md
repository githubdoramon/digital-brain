Digital Brain – Quick Context

## Overview

Personal memory orchestrator with a **bounded agent architecture**. Backend: FastAPI (`backend/orchestrator`) + PostgreSQL/pgvector. Frontend: Next.js App Router (`frontend/web`) with NextAuth Google OAuth. Mobile app: React Native/Expo (`mobile`). Supports OpenAI-compatible LLM APIs (local Ollama or cloud). Optional Tavily web search and Home Assistant integration.

**Core principle**: "The model proposes. The controller validates, executes, and decides."

**Terminology**: When a user says "app", they mean the mobile app in `mobile`, not the web frontend.

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
│   ├── trace.py              # Structured trace logging
│   └── logger.py             # Detailed request logging
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
├── skills.py                   # Skill management
├── auth.py                     # Authentication
└── db.py                       # Database helpers
```

## Agent Architecture

### Request Flow

```
User Question → Intent Router (metadata) → Agent Loop (full tool set) → Response
```

1. **IntentRouter** classifies the question (rule-based or LLM fallback)
2. **Tool groups** are emitted as routing metadata for observability/hints
3. **AgentController** runs the loop with limits enforcement
4. Each tool call goes through **pre-validation → execution → post-validation**
5. **AgentState** tracks facts, actions, and tool call history

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
| `resolution` | resolve_query, resolve_contacts, lookup_contact |
| `web` | web_search, fetch_web_page |
| `home` | home_assistant |
| `skills` | run_skill_script |
| `system` | bash |

### Important Rules (Recent)

- **Single source of truth for tool groups**: keep router tool groups aligned with `backend/orchestrator/tools/registry.py`; do not maintain divergent copies.
- **Prefer enums for internal control-flow values**: avoid raw string comparisons for statuses/actions/modes (for example limit actions, tool statuses, follow-up sources). Define shared enums and compare enum members to prevent typos and drift.
- **Tool groups are metadata today**: router still classifies intents and groups, but the controller currently exposes the full tool set to the LLM.
- **LLM calls must use helpers**: all LLM requests and streams go through `backend/orchestrator/llm_helpers.py` (never call LLM endpoints via direct `requests`/`httpx` in app modules).
- **Controller context kwargs are global**: handlers are invoked with shared runtime context (`state`, `question`, `search_limit`, `user_email`, `conversation_history`). Every handler must accept these explicitly or via `**kwargs`.
- **Regression guard**: keep `backend/orchestrator/tests/tools/test_handlers/test_handler_signatures.py` passing to prevent `unexpected keyword argument` runtime failures.
- **`resolve_contacts` contract**: model-facing params should remain minimal (`text` only). Runtime identity/context (like `user_email`) is injected by the controller, not authored by the model.
- **Keep code modular**: avoid bloated files that mix unrelated concerns. When a file starts owning multiple responsibilities (for example, controller loop + guardrails + tool execution internals), extract cohesive modules early.
- **Contact-aware memory search flow**: if `search_memories` has no `contact_ids` and query is person-referential, controller attempts contact resolution first.
- **Contact-aware memory search flow**: on unambiguous resolution, inject `contact_ids` into memory search.
- **Contact-aware memory search flow**: on ambiguity, return a clarification-needed result instead of running unfiltered search.
- **Contact-aware memory search flow**: avoid repeating identical `resolve_contacts` calls after `needs_clarification`/`no_people` (no-progress guard).
- **Validation semantics**: post-execution validation must treat clarification-required search/resolution results as `need_user_input`, not generic empty-result retries.
- **Session command hygiene**: strip leading slash commands from user text before agent execution; commands are control signals, not semantic query content.
- **Import-time side effects**: avoid filesystem writes (like `mkdir`) during module import. Create directories lazily at the point of file operations.
- **Contact disambiguation policy**: ambiguity auto-resolution strictness is controlled by `CONTACT_DISAMBIGUATION_STRICTNESS` (`strict`/`balanced`/`lenient`).
- **Logging policy**: never use `print` in orchestrator runtime code (only in scripts/tests). Use `logging.getLogger(__name__)` with `debug/info/warning/error` (or `logger.log(DECISION_LEVEL, ...)` for decisions). Logging must flow through `observability/log_stream.py` so frontend log streaming can filter by level. For streaming endpoints, rely on authenticated user context (no service API key) unless explicitly required.

### Limits & Safety

- **max_steps**: 15 (agent loop iterations)
- **max_tool_calls**: 20 (total tool executions)
- **max_repairs**: 2 (validation repair attempts)
- **No-progress detection**: Stops on repeated identical calls or empty results

## Backend Endpoints

### Conversation API
- `POST /ask` – Ask a question (returns answer + state)
- `GET/POST /ask/stream` – Streaming responses
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
- `GET /meetings/{id}` – Get meeting

### Webhooks
- `POST /webhooks/contacts` – Sync/unlink contacts
- `POST /webhooks/telegram/messages` – Telegram messages

### System
- `GET /services/version` – Service versions
- `GET /access/gate` – Face recognition (Immich)

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

# Agent Configuration
AGENT_MAX_STEPS=15
AGENT_MAX_TOOL_CALLS=20
AGENT_MAX_REPAIRS=2
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
- **Document files**: `backend/orchestrator/storage/documents` (volume-mounted)
- **Vector embeddings**: pgvector in PostgreSQL

## Key Implementation Files

| Purpose | File |
|---------|------|
| Agent loop | `backend/orchestrator/agent/controller.py` |
| Intent routing | `backend/orchestrator/agent/router.py` |
| Tool registry | `backend/orchestrator/tools/registry.py` |
| Tool contracts | `backend/orchestrator/tools/contracts.py` |
| Validation | `backend/orchestrator/tools/validators/` |
| Tracing | `backend/orchestrator/observability/trace.py` |
| LLM orchestration | `backend/orchestrator/llm.py` |
| Vector search | `backend/orchestrator/retrieval.py` |
| Frontend API client | `frontend/web/src/lib/api.ts` |
