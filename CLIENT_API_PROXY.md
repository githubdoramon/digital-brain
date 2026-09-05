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
- `/mobile/glasses/commands`
- `/mobile/glasses/audio/{audio_id}`

Smart-glasses commands are authenticated bearer-token requests. The command
body contains a UUID `command_id`, transcript, optional `thread_id` (or
`session_id`) and client context. Responses are discriminated by `outcome`:
`control_completed`, `shortcut_completed`, `agent_response`, or `error`.
Voice agent responses include a short-lived `audio` reference; gate and `slash
new` shortcuts are silent. Audio is mono WAV, authenticated, process-local,
and deleted after a successful download (or by TTL cleanup).

If a new backend endpoint is added for mobile usage, make sure the client calls it
with the `/mobile` prefix and the proxy middleware routes it to the backend. And make sure the backend can handle this route as well (for example, by responding to both `/api_endpoint` and `/mobile/api_endpoint`)

## Web

Web requests go through the Next.js API proxy (`/api/orchestrator`). The `api` helper
in `frontend/web/src/lib/api.ts` already handles this base path.

When adding new endpoints for web usage, ensure they are routed through the same
proxy so the browser never calls the backend directly.

Web-only session requests normally do not send a bearer token from the browser.
If a web screen calls an endpoint that also supports bearer-token clients, add the
`/api/orchestrator/...` path to the middleware hybrid auth prefixes so the
NextAuth session can pass middleware and the proxy route can attach the backend
authorization header.
