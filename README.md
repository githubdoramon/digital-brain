# Digital Brain

A agentic memory and orchestration system running on private local LLM infrastructure for contextual retrieval, and long-term operational intelligence for work and personal life.

## Why

My memory is a big piece of shit. I got tired of not remembering who was in a given event, what I got as a gift for my birthday, when is my best friend's birthday, or when I had dinner with my wife 2 weeks ago. So I decided to put all of this in the same place, and put some AI on top, because, why not?

## Code quality

Although I started the project caring a bit about code quality, it quickly evolved to a heavily vibe-coded repo. Sometimes I care again and review some of the code, sometimes I am just prompting from mobile and yolo push stuff. So, expect anything.

## IMPORTANT

This system is EXTREMELY biased to what I want/need, it is not intended to be a plug and play for anyone to use. I add features that I feel it will help me. BUT, if you want to play with it, evolve it, or propose some features as well, please do so. Or just fork it away as well :)

## Structure

This is a monorepo. Each system has its own README with setup, configuration,
and any system-specific notes — start there when you want to run or hack on
a particular piece.

| Path                                               | What it is                                                                                                                                                                                               | Where to read more                                                                                                                                             |
| -------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [`backend/orchestrator/`](backend/orchestrator/)   | FastAPI orchestrator — memory, agents, tools, LLM routing. Port 8000.                                                                                                                                    | [README](backend/orchestrator/README.md) · architecture docs in [`docs/architecture/`](backend/orchestrator/docs/architecture/)                                |
| [`backend/robot-gateway/`](backend/robot-gateway/) | MQTT gateway for physical robots: telemetry ingest, command dispatch. Port 8001.                                                                                                                         | [README](backend/robot-gateway/README.md) · firmware protocol in [MQTT_PROTOCOL.md](backend/robot-gateway/MQTT_PROTOCOL.md)                                    |
| [`frontend/web/`](frontend/web/)                   | Next.js web app — UI + API proxy layer in front of the backend. Port 3000.                                                                                                                               | [README](frontend/web/README.md)                                                                                                                               |
| [`mobile/`](mobile/)                               | React Native / Expo mobile app — chat, capture, location, notifications, and on-device smart-glasses scene understanding.                                                                                | [`mobile/.env.example`](mobile/.env.example) · [`mobile/app.config.ts`](mobile/app.config.ts) · [glasses capture pipeline](mobile/GLASSES_CAPTURE_PIPELINE.md) |
| [`backend/db/init.sql`](backend/db/init.sql)       | PostgreSQL + pgvector schema applied at first container boot. Incremental schema lives in [`backend/orchestrator/db_migrations/`](backend/orchestrator/db_migrations/) and runs at orchestrator startup. | —                                                                                                                                                              |
| [`docker-compose.yml`](docker-compose.yml)         | Brings up PostgreSQL, orchestrator, frontend, Mosquitto, and the robot gateway together.                                                                                                                 | —                                                                                                                                                              |
| [`AGENTS.md`](AGENTS.md)                           | Quick context for anyone (human or AI) hacking on the codebase — conventions, architecture pointers, anonymization rule for tests.                                                                       | —                                                                                                                                                              |

Shared backend env vars live in [`backend/env.template`](backend/env.template); the per-system READMEs reference the variables they care about.

### Running it

I usually test things on "production", really rare to run locally. Github actions build and deploy for me on my server. If you wanna try it out, build the docker images, and run them. If you have trouble running it, open an issue and I'll help.
