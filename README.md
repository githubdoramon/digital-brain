# Digital Brain

Monorepo containing backend services and frontend client for the Digital Brain project.

## Structure

- `backend/orchestrator/` — FastAPI orchestrator (memory, agents, tools, LLM) on port 8000
- `backend/robot-gateway/` — MQTT robot communication gateway on port 8001 (see [README](backend/robot-gateway/README.md))
- `frontend/web/` — Next.js application (web UI, API proxy layer) on port 3000
- `docker-compose.yml` — All services: PostgreSQL, orchestrator, frontend, Mosquitto MQTT broker, robot gateway

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

Refer to `backend/env.template` for shared backend configuration (PostgreSQL, LLM, MQTT, service keys).
Frontend variables: `frontend/web/env.template` (OAuth, API base URLs).
Robot gateway-specific variables are documented in `backend/env.template` under the "Robot Gateway" section.
