# Digital Brain

A agentic memory and orchestration system running on private local LLM infrastructure for contextual retrieval, and long-term operational intelligence for work and personal life.

## Why

My memory is a big piece of shit. I got tired of not remembering who was in a given event, what I got as a gift for my birthday, when is my best friend's birthday, or when I had dinner with my wife 2 weeks ago. So I decided to put all of this in the same place, and put some AI on top, because, why not?

## Code quality

Although I started the project caring a bit about code quality, it quickly evolved to a heavily vibe-coded repo. Sometimes I care again and review some of the code, sometimes I am just prompting from mobile and yolo push stuff. So, expect anything.

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
Mobile build-time variables live in `mobile/.env.example` and are consumed by `mobile/app.config.ts`.

## Security Defaults

- `ALLOWED_USERS` is required for both backend and frontend auth. Empty allowlists are rejected at startup.
- Telegram photo ingest is fail-closed. If `TELEGRAM_BOT_TOKEN` is set, you must also set a non-empty `TELEGRAM_ALLOWED_CHAT_IDS` and `TELEGRAM_WEBHOOK_SECRET`.
- Mobile Expo/EAS identifiers and OAuth URL schemes are intentionally env-driven; the public repo does not hardcode them.
