# Digital Brain

Monorepo containing backend services and frontend client for the Digital Brain project.

## Structure

- `backend/` — FastAPI orchestrator, PostgreSQL artifacts, Docker Compose, env files, and setup tooling
- `frontend/` — Next.js application for meeting ingestion and future UI work

## Backend

### Requirements

- Docker and Docker Compose
- Python 3.11+ (for running helper scripts locally)

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
