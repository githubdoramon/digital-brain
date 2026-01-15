Digital Brain – Quick Context

## Overview

Personal memory orchestrator with a **bounded agent architecture**. Backend: FastAPI (`backend/orchestrator`) + PostgreSQL/pgvector. Frontend: Next.js App Router (`frontend/web`) with NextAuth Google OAuth. Supports OpenAI-compatible LLM APIs (local Ollama or cloud). Optional Tavily web search and Home Assistant integration.

**Core principle**: "The model proposes. The controller validates, executes, and decides."

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

## Services & Runtime

Docker Compose services: `db` (pgvector), `orchestrator` (FastAPI), `frontend` (Next.js). See `docker-compose.yml`.

- **Backend entry**: `backend/orchestrator/app.py`
- **Auth**: Google ID token (`Authorization: Bearer …`); email allowlist in `auth.py`
- **CORS**: Allows `http://localhost:3000`
- **Service-to-service**: `x-service-api-key` header (`ORCHESTRATOR_API_KEY`)

## Backend Structure

```
backend/orchestrator/
├── agent/                      # Bounded agent orchestration
│   ├── controller.py          # Main agent loop
│   ├── router.py              # Intent classification
│   ├── state.py               # Canonical state management
│   └── limits.py              # Stop rules, progress detection
├── tools/                      # Tool system
│   ├── registry.py            # Tool registration & grouping
│   ├── contracts.py           # JSON Schema validation
│   ├── handlers/              # Tool implementations
│   │   ├── memory.py         # search_memories, get_events, get_document
│   │   ├── homeassistant.py  # Home Assistant MCP integration
│   │   ├── database.py       # SQL tools
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
User Question → Intent Router → Tool-Set Narrowing → Agent Loop → Response
```

1. **IntentRouter** classifies the question (rule-based or LLM fallback)
2. **Tool groups** are filtered based on intent
3. **AgentController** runs the loop with limits enforcement
4. Each tool call goes through **pre-validation → execution → post-validation**
5. **AgentState** tracks facts, actions, and tool call history

### Intent Types

| Intent | Tool Groups | Description |
|--------|-------------|-------------|
| `MEMORY_SEARCH` | memory | Search memories, events, documents |
| `DATA_QUERY` | database | SQL queries, aggregation |
| `CONTACT_LOOKUP` | resolution | Find people, relationships |
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
| `database` | execute_sql, describe_schema |
| `resolution` | resolve_query |
| `web` | web_search |
| `home` | home_assistant |
| `skills` | run_skill_script |
| `system` | bash |

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
USE_BOUNDED_AGENT=true
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
ALLOWLIST=user@example.com

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
pip install -r requirements.txt
uvicorn app:api --reload

# Frontend
cd frontend/web
yarn install
yarn dev

# Full stack
docker compose up --build
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
