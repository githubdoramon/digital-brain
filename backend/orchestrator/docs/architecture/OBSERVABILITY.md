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

- HTTP server request spans with method and response status, held through the
  complete response stream so child agent and LLM work remains on the same trace.
- `agent.run` spans with the conversational profile, streaming flag, and outcome.
- `llm.chat_completion` spans with model, request message count, streaming flag,
  duration, outcome, and provider token counts when returned.
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

Prompts, completions, tool arguments/results, user identifiers, conversation
identifiers, LLM URLs, and authorization values are not added to these spans
or metrics. Metric dimensions are limited to the configured model, operation,
profile, outcome, and token type. Per-run IDs are not metric dimensions. The
integration does not export application logs, SQL
statements, or outbound HTTP auto-instrumentation. Existing application logs
remain governed by the local logging configuration.

Telemetry setup failures are logged and do not prevent the orchestrator from
starting. Missing keys disable Mirador export. This feature does not instrument
the mobile app or web frontend.
