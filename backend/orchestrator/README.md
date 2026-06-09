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
# Required: comma-separated client IDs accepted as token audiences.
GOOGLE_CLIENT_IDS=web-client-id.apps.googleusercontent.com,desktop-client-id.apps.googleusercontent.com

# Required: Comma-separated list of allowed emails
# Empty or missing is rejected at startup unless DEV_BYPASS_AUTH is enabled locally
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

### Meetings
- `POST /ingest/meetings/transcript` - Queue a meeting transcript payload using bearer-token auth; acknowledges receipt immediately, debounces/replaces newer payloads for the same meeting for 30 seconds, skips regeneration when the transcript hash is unchanged, then asynchronously updates the matching meeting, stores an LLM-generated discussion summary plus structured action items, exposes those action items on event detail reads, and creates todos for action items assigned to the current user
- `POST /ingest/meetings` - Upsert external meetings (e.g., Google Calendar) using the expanded `EventIn` payload plus `externalType`/`externalId`
- `POST /ingest/meetings/update` - Apply updates for an external meeting using the same payload as above

### LLM Chat
- `POST /ask` - Ask questions about memories

The LLM agent uses a collection of function tools (memory search, SQL access, etc.).
If you provide a LangSearch API key (`LANGSEARCH_API_KEY`), the agent can also run live
internet searches to enrich its answers. It can also fetch the full contents of
specific web pages directly for deeper context. Configure optional overrides using the environment variables
documented in `backend/env.template`.

## Development

```bash
# Install dependencies
pip install -r requirements.txt

# Set environment variables (copy from env.template)
export GOOGLE_CLIENT_IDS=...
export ALLOWED_USERS=...

# Run
uvicorn app:api --reload
```

## Deployment

Make sure to set `GOOGLE_CLIENT_IDS` and a non-empty `ALLOWED_USERS` in your deployment environment.

If you enable Telegram photo ingest, also set:

```bash
TELEGRAM_BOT_TOKEN=...
TELEGRAM_ALLOWED_CHAT_IDS=123456789
TELEGRAM_WEBHOOK_SECRET=...
IMMICH_SERVER_URL=...
IMMICH_API_KEY=...
```

The Telegram integration is fail-closed: when `TELEGRAM_BOT_TOKEN` is configured, both
`TELEGRAM_ALLOWED_CHAT_IDS` and `TELEGRAM_WEBHOOK_SECRET` are required.
