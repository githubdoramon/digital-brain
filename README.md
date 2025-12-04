# Digital Brain

Monorepo containing backend services and frontend client for the Digital Brain project.

## Structure

- `backend/` — FastAPI orchestrator, PostgreSQL artifacts, Docker Compose, env files, and setup tooling
- `frontend/` — Next.js application for meeting ingestion and future UI work

## Backend

### Requirements

- Docker and Docker Compose
- Python 3.11+ (for running helper scripts locally)

### Memory Layer (Mem0)

The backend now includes **Mem0**, an intelligent memory layer that enables the system to remember conversations and user preferences across sessions.

**Key Features:**
- Persistent conversation memory via Qdrant vector database
- User preference learning
- Context-aware responses
- 26% higher accuracy than traditional approaches
- 90% token cost savings
- Web UI for memory management at `http://localhost:6333/dashboard`

**Frontend Integration:**
- Session-based conversations
- Visual memory indicators (🧠 badge)
- Toggle to view memory details
- User-specific memory isolation

**Documentation:**
- `backend/MEM0_INTEGRATION.md` - Complete usage guide
- `backend/MEM0_UPDATES.md` - Frontend & storage details

**Testing:** Run the integration test:
```bash
cd backend
python test_mem0_integration.py
```

## Frontend

The Next.js app resides in `frontend/web`.

### Requirements

- Node.js 20+
- npm (bundled with Node.js)

### Installation

```bash
cd frontend/web
npm install
```

### Development

```bash
npm run dev
```

The app reads `process.env.BACKEND_API_BASE`; configure it in `.env.local` if you run the backend elsewhere.

The Meetings page allows importing events to the backend ingest endpoint.

## Environment Variables

Refer to `env.template` for shared backend configuration. Frontend variables can be managed through `frontend/web/.env.local`.
Backend environment files now live in `backend/.env` and `backend/env.template`.
