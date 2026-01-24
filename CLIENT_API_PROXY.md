# Client API Proxy Notes

The backend is not directly exposed to the internet. Client apps must call API routes
through the frontend proxy layer, which rewrites paths to the orchestrator service.

## Mobile

All mobile requests must start with `/mobile` so the proxy can route them.

Examples:

- `/mobile/ask`
- `/mobile/ask/stream`
- `/mobile/threads`
- `/mobile/threads/{id}`
- `/mobile/commands/event/confirm`

If a new backend endpoint is added for mobile usage, make sure the client calls it
with the `/mobile` prefix and the proxy middleware routes it to the backend. And make sure the backend can handle this route as well (for example, by responding to both `/api_endpoint` and `/mobile/api_endpoint`)

## Web

Web requests go through the Next.js API proxy (`/api/orchestrator`). The `api` helper
in `frontend/web/src/lib/api.ts` already handles this base path.

When adding new endpoints for web usage, ensure they are routed through the same
proxy so the browser never calls the backend directly.
