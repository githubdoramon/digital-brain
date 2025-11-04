# Orchestrator API

Personal memory orchestrator backend with Google OAuth JWT authentication.

## Authentication

All API endpoints require authentication via Google OAuth JWT tokens.

### How It Works

1. Frontend authenticates user with Google OAuth (via NextAuth.js)
2. Frontend receives Google ID token
3. Frontend sends ID token in `Authorization` header: `Bearer <token>`
4. Backend validates token with Google and checks user allowlist

### Configuration

Set these environment variables:

```bash
# Required: Your Google OAuth Client ID (same as frontend)
GOOGLE_CLIENT_ID=your-client-id.apps.googleusercontent.com

# Optional: Comma-separated list of allowed emails
# Empty = allow all users with valid Google accounts
ALLOWED_USERS=user1@example.com,user2@example.com
```

### Error Responses

- `401 Unauthorized`: Missing or invalid token
- `403 Forbidden`: Valid token but user not in allowlist

## API Endpoints

All endpoints require `Authorization: Bearer <google-id-token>` header.

### Contacts
- `POST /ingest/contact` - Create/update contact
- `GET /contacts` - List all contacts
- `GET /contacts/{id}` - Get specific contact
- `DELETE /contacts/{id}` - Delete contact

### Events & Places
- `POST /ingest/event` - Ingest event/memory
- `POST /ingest/place` - Ingest place

### Search & Retrieval
- `POST /resolve` - Resolve entities in text
- `POST /search` - Search memories
- `POST /get` - Get events by IDs

### LLM Chat
- `POST /ask` - Ask questions about memories

The LLM agent uses a collection of function tools (memory search, SQL access, etc.).
If you provide a Tavily API key (`TAVILY_API_KEY`), the agent can also run live
internet searches to enrich its answers. With the same credentials it can now
fetch the full contents of specific web pages for deeper context via Tavily's
extract API. Configure optional overrides using the environment variables
documented in `backend/env.template`.

## Development

```bash
# Install dependencies
pip install -r requirements.txt

# Set environment variables (copy from env.template)
export GOOGLE_CLIENT_ID=...
export ALLOWED_USERS=...

# Run
uvicorn app:api --reload
```

## Deployment

Make sure to set `GOOGLE_CLIENT_ID` and `ALLOWED_USERS` in your deployment environment.

