# AI observability with Mirador

Digital Brain can export OpenTelemetry traces and metrics from the orchestrator to Mirador.
The integration is optional: without `MIRADOR_API_KEY`, the app keeps its local
logging and tracing behavior and sends no telemetry to Mirador.

## Enable export

Create a Mirador server key (`mir_srv_*`) in the Mirador project dashboard and
configure it in `backend/.env`:

```dotenv
MIRADOR_API_KEY=mir_srv_replace_me
# Optional overrides
MIRADOR_OTEL_ENDPOINT=https://otel.mirador.org/v1/traces
MIRADOR_OTEL_METRICS_ENDPOINT=https://otel.mirador.org/v1/metrics
MIRADOR_METRICS_EXPORT_INTERVAL_MS=30000
OTEL_SERVICE_NAME=digital-brain-orchestrator
```

For Docker Compose, the key may instead live in the optional user-private
`MIRADOR_ENV_FILE` (default `${HOME}/.config/digital-brain/mirador.env`). Compose
loads that file before `backend/.env`, so an explicit backend value takes
precedence. Local runs load settings through `backend/orchestrator/run_local.sh`
from the process environment and `backend/.env`. Restart the orchestrator
after changing configuration. The application uses OTLP/HTTP protobuf for
traces and metrics. Metrics export defaults to a 30-second interval. Both
providers flush queued telemetry during graceful shutdown.

## What is exported

- HTTP server spans for AI entry points (`/ask`, `/mobile/ask`, their streaming
  routes, and the event-summary debug route). Spans
  include the matched route template, method, and status, and stay active through
  the complete response stream. Other HTTP requests contribute metrics without
  creating one trace per frontend page-load request. Override the traced path set
  with the comma-separated `MIRADOR_HTTP_TRACE_PATHS` environment variable.
- `digital_brain.http.requests` counter and
  `digital_brain.http.request.duration` histogram, tagged by route template,
  method, and status class. They contain no raw URL, query string, user, or
  conversation identifier.
- `agent.run` spans with the conversational profile, streaming flag, and outcome.
- `llm.chat_completion` spans for every production model request routed through
  `llm_helpers`, including sync calls, buffered streaming calls, async SSE
  streaming calls, and Ollama model warm-up requests. They record model, request
  message count, streaming flag, duration, outcome, and provider token counts
  when returned. Async streaming spans remain open through stream completion or
  failure. Requests made outside an `agent.run` still appear as standalone spans;
  background workflows do not need an HTTP request or agent run to be measured.
- `digital_brain.llm.requests` counter, tagged by model and success/error outcome.
- `gen_ai.client.operation.duration` histogram in seconds, tagged by model and
  outcome.
- `gen_ai.client.token.usage` histogram in tokens, tagged by model and input or
  output token type when the provider returns usage data.
- `digital_brain.agent.runs` counter and `digital_brain.agent.run.duration`
  histogram, tagged by conversational profile and bounded outcome (`completed`,
  `unable_to_complete`, or `limit`).
- `digital_brain.agent.run.rounds`, `digital_brain.agent.run.tool_calls`, and
  `digital_brain.agent.run.repairs` histograms, tagged by profile and outcome.
  Rounds are LLM iterations; tool calls are individual tool invocations, so a
  parallel batch can contribute multiple calls in one round.
- Safe per-run counts (`agent.rounds`, `agent.tool_calls`, and `agent.repairs`)
  on the `agent.run` span for inspecting an individual trace.
- `execute_tool {tool_name}` child spans under `agent.run`, with the registered
  tool name, success/validation-error/error outcome, parallel-execution flag,
  and duration. Tool arguments and results are deliberately omitted.
- `digital_brain.tool.calls` counter and `digital_brain.tool.duration`
  histogram, tagged by registered tool name, bounded outcome, and whether the
  call ran in a parallel batch.

Prompts, completions, tool arguments/results, user identifiers, conversation
identifiers, LLM URLs, and authorization values are not added to these spans
or metrics. Tool names come from the registered tool set; tool arguments and
results are excluded. Metric dimensions are limited to the configured model,
operation, registered tool name, profile, route template, HTTP method, status
class, outcome, execution mode, and token type. Per-run IDs are not metric
dimensions. The integration does not export application logs, SQL statements,
or outbound HTTP auto-instrumentation. Existing application logs
remain governed by the local logging configuration.

The request counter and duration histogram describe one logical helper call;
its duration includes any internal retry delays. Provider retries are not
exported as separate spans. Diagnostic scripts that call model endpoints
directly are outside this runtime instrumentation.

When an upstream proxy or client sends a W3C `traceparent`, the HTTP middleware
extracts it so spans can join that upstream trace. Without shared trace context,
independent browser HTTP requests are separate operations; the orchestrator
does not infer a page view or correlate requests by user/session identity.

Telemetry setup failures are logged and do not prevent the orchestrator from
starting. Missing keys disable Mirador export. This feature does not instrument
the mobile app or web frontend.
