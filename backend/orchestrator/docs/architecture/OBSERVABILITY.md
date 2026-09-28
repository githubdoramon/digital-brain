# AI observability with Mirador

Digital Brain can export OpenTelemetry traces from the orchestrator to Mirador.
The integration is optional: without `MIRADOR_API_KEY`, the app keeps its local
logging and tracing behavior and sends no telemetry to Mirador.

## Enable export

Create a Mirador server key (`mir_srv_*`) in the Mirador project dashboard and
add it to `backend/.env`:

```dotenv
MIRADOR_API_KEY=mir_srv_replace_me
# Optional overrides
MIRADOR_OTEL_ENDPOINT=https://otel.mirador.org/v1/traces
OTEL_SERVICE_NAME=digital-brain-orchestrator
```

Restart the orchestrator. Docker Compose reads `backend/.env`; local runs load
it through `backend/orchestrator/run_local.sh`. The application uses OTLP/HTTP
protobuf and flushes queued spans during graceful shutdown.

## What is exported

- HTTP server request spans with method and response status, held through the
  complete response stream so child agent and LLM work remains on the same trace.
- `agent.run` spans with the conversational profile, streaming flag, and outcome.
- `llm.chat_completion` spans with model, request message count, streaming flag,
  duration, outcome, and provider token counts when returned.

Prompts, completions, tool arguments/results, user identifiers, conversation
identifiers, LLM URLs, and authorization values are not added to these spans.
The integration does not export application logs, SQL statements, or outbound
HTTP auto-instrumentation. Existing application logs remain governed by the
local logging configuration.

Telemetry setup failures are logged and do not prevent the orchestrator from
starting. Missing keys disable Mirador export. This feature does not instrument
the mobile app or web frontend.
