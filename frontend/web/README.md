This is a [Next.js](https://nextjs.org) project bootstrapped with [`create-next-app`](https://nextjs.org/docs/app/api-reference/cli/create-next-app).

# Digital Brain Frontend

Personal memory orchestrator frontend with Google OAuth authentication.

## Setup

See [AUTH_SETUP.md](./AUTH_SETUP.md) for Google OAuth configuration.

### Quick Start

```bash
# Install dependencies
npm install

# Configure environment
cp env.template .env.local
# Edit .env.local with your Google OAuth credentials and webhook settings

# Run dev server
npm run dev
```

## Deployment Webhook

- `POST /api/deploy` triggers the rollout script when the `X-Deploy-Key` header matches `DEPLOY_WEBHOOK_KEY`.
- The handler runs the executable defined by `DEPLOY_SCRIPT_PATH` (for example `scripts/redeploy.sh`). Ensure the file is mounted in the runtime container and marked executable.
- `DEPLOY_SCRIPT_TIMEOUT_MS` controls how long the server waits for the script (default 15 minutes, set to `0` to disable).
- The script requires Docker CLI access (e.g. bind-mount `/var/run/docker.sock` and the CLI binary or execute on the host).

## Features

- 🔐 Google OAuth authentication
- 👤 User access control with email allowlist
- 🧠 AI-powered memory chat interface
- 📇 Contact management
- 📅 Meeting transcripts
- 🔒 Long-lived sessions (1 year)

## Documentation

- [AUTH_SETUP.md](./AUTH_SETUP.md) - Google OAuth configuration
- [ACCESS_CONTROL.md](./ACCESS_CONTROL.md) - User access management

## Tech Stack

- Next.js 15 with App Router
- NextAuth.js v4 for authentication
- React 19
- TypeScript
