Digital Brain – quick context

Overview
- Personal memory orchestrator. Backend: FastAPI (`backend/orchestrator`) + PostgreSQL/pgvector. Frontend: Next.js App Router (`frontend/web`) with NextAuth Google OAuth. Local Ollama provides chat + embeddings; optional Tavily web search/extract.

Services & runtime
- Compose services: `db` (pgvector), `orchestrator` (FastAPI), `frontend` (Next.js). See `docker-compose.yml`.
- Backend entry: `backend/orchestrator/app.py`. Auth via Google ID token (`Authorization: Bearer …`); allowlist enforced in `auth.py`.
- CORS allows `http://localhost:3000`. Service-to-service auth uses `x-service-api-key` (`ORCHESTRATOR_API_KEY`).

Key backend capabilities
- Ingest: contacts `/ingest/contact`, places `/ingest/place`, todos `/ingest/todo`, events `/ingest/event`, meeting notes `/ingest/events/notes` (API key), external meetings `/ingest/events`, gate face-check `/access/gate` via Immich.
- Documents: upload `/ingest/document`, list/get/update/search/delete/download via `/documents…`; files stored at `backend/orchestrator/storage/documents`.
- Conversations: `/ask` routes to `llm.answer_question`; threads CRUD under `/threads`.
- Webhooks: contacts `/webhooks/contacts` (sync/unlink external contact IDs), Telegram `/webhooks/telegram/messages`.
- LLM agent (`backend/orchestrator/llm.py`): uses Ollama models (`OLLAMA_HOST`, `OLLAMA_CHAT_MODEL`, `OLLAMA_EMBED_MODEL`). Tools: schema description + read-only SQL (`sql_tools`), memory search (`retrieval.py`), contact/place resolution, event fetching, optional Tavily internet search + page fetch (`web_tools.py`). Tracks conversation state in `conversations.py`.
- DB helpers in `db.py`; schemas in `schemas.py`; action logs in `action_logs.py`; version reporting in `versioning.py`.

Frontend highlights
- Next.js App Router in `frontend/web`. Auth config: `src/app/api/auth/[...nextauth]/route.ts` (Google OAuth, allowlist, long-lived JWT sessions). Sign-in page at `src/app/auth/signin/page.tsx`.
- Backend proxy: `src/app/api/orchestrator/[...path]/route.ts` forwards to `BACKEND_API_BASE`, attaching ID token.
- Main chat UI: `src/app/page.tsx` manages conversation threads, calls `/threads` and `/ask` via `src/lib/api.ts`.
- Additional pages: contacts (`src/app/contacts`), documents (`src/app/documents`), meetings (`src/app/meetings`), todos (`src/app/todos`), system info (`src/app/system`).

Configuration
- Backend env template: `backend/env.template` (Postgres, Ollama models, Google client ID, allowlist, `ORCHESTRATOR_API_KEY`, Tavily keys, document storage, Telegram/Immich).
- Frontend env template: `frontend/web/env.template` (NextAuth secret, Google OAuth, `BACKEND_API_BASE`, allowlist, build metadata).

Local dev
- Backend: `cd backend/orchestrator && pip install -r requirements.txt && uvicorn app:api --reload` (requires env vars and PostgreSQL).
- Frontend: `cd frontend/web && npm install && npm run dev` (`BACKEND_API_BASE` defaults to `http://localhost:8000`).
- Full stack: `docker compose up --build` from repo root.

Data/Storage notes
- Postgres schema bootstrap: `backend/db/init.sql`.
- Document files live in `backend/orchestrator/storage/documents` (volume-mounted in compose).

Features
- LLM tool/agent behavior: `backend/orchestrator/llm.py` (function tool list at top).
- Retrieval + embeddings: `backend/orchestrator/retrieval.py`; SQL safe execution: `sql_tools.py`.
- UI API helper: `frontend/web/src/lib/api.ts`.