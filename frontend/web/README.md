This is a [Next.js](https://nextjs.org) project bootstrapped with [`create-next-app`](https://nextjs.org/docs/app/api-reference/cli/create-next-app).

# Digital Brain Frontend

Personal memory orchestrator frontend with Google OAuth authentication.

## Setup

### Quick Start

```bash
# Install dependencies
npm install

# Configure environment
cp env.template .env.local
# Edit .env.local with your Google OAuth credentials and a non-empty ALLOWED_USERS.
# Set GOOGLE_CLIENT_ID to the web OAuth client, or put it first in GOOGLE_CLIENT_IDS.

# Run dev server
npm run dev
```

## Features

- 🔐 Google OAuth authentication
- 👤 User access control with email allowlist
- 🧠 AI-powered memory chat interface
- 📇 Contact management
- 📅 Meeting transcripts
- 🔒 Long-lived sessions (1 year)

## Documentation

- `env.template` - required frontend environment variables
- `src/app/api/auth/[...nextauth]/route.ts` - NextAuth Google OAuth configuration

## Tech Stack

- Next.js 15 with App Router
- NextAuth.js v4 for authentication
- React 19
- TypeScript
