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
cp env.example .env.local
# Edit .env.local with your Google OAuth credentials

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

- [AUTH_SETUP.md](./AUTH_SETUP.md) - Google OAuth configuration
- [ACCESS_CONTROL.md](./ACCESS_CONTROL.md) - User access management

## Tech Stack

- Next.js 15 with App Router
- NextAuth.js v4 for authentication
- React 19
- TypeScript
