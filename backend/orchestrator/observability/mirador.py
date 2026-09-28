"""Privacy-conscious OpenTelemetry setup and AI span helpers for Mirador."""

from __future__ import annotations

import inspect
import logging
import os
import time
from collections.abc import Awaitable, Callable
from functools import wraps
from typing import Any, TypeVar

logger = logging.getLogger(__name__)

_T = TypeVar("_T")
_configured = False
_provider: Any | None = None
_meter_provider: Any | None = None
_llm_request_count: Any | None = None
_llm_duration: Any | None = None
_llm_token_usage: Any | None = None
_agent_run_count: Any | None = None
_agent_run_duration: Any | None = None
_agent_run_rounds: Any | None = None
_agent_run_tool_calls: Any | None = None
_agent_run_repairs: Any | None = None


def configure_mirador() -> None:
    """Enable privacy-conscious OTLP traces and metrics when configured."""
    global _configured, _provider, _meter_provider
    global _llm_request_count, _llm_duration, _llm_token_usage
    global _agent_run_count, _agent_run_duration, _agent_run_rounds
    global _agent_run_tool_calls, _agent_run_repairs
    if _configured:
        return

    api_key = os.getenv("MIRADOR_API_KEY", "").strip()
    if not api_key:
        logger.info("Mirador telemetry disabled: MIRADOR_API_KEY is not configured")
        return

    service_name = os.getenv("OTEL_SERVICE_NAME", "digital-brain-orchestrator")

    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        endpoint = os.getenv("MIRADOR_OTEL_ENDPOINT", "https://otel.mirador.org/v1/traces")
        resource = Resource.create({"service.name": service_name})
        provider = TracerProvider(resource=resource)
        provider.add_span_processor(
            BatchSpanProcessor(
                OTLPSpanExporter(endpoint=endpoint, headers={"Authorization": api_key})
            )
        )
        trace.set_tracer_provider(provider)
        _provider = provider
        logger.info("Mirador OTLP tracing enabled service=%s", service_name)
    except Exception:
        logger.exception("Mirador OTLP tracing setup failed; continuing without tracing export")

    try:
        from opentelemetry import metrics
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
        from opentelemetry.sdk.resources import Resource

        metrics_endpoint = os.getenv(
            "MIRADOR_OTEL_METRICS_ENDPOINT", "https://otel.mirador.org/v1/metrics"
        )
        export_interval_ms = int(os.getenv("MIRADOR_METRICS_EXPORT_INTERVAL_MS", "30000"))
        metric_reader = PeriodicExportingMetricReader(
            OTLPMetricExporter(
                endpoint=metrics_endpoint,
                headers={"Authorization": api_key},
            ),
            export_interval_millis=export_interval_ms,
        )
        meter_provider = MeterProvider(
            resource=Resource.create({"service.name": service_name}),
            metric_readers=[metric_reader],
        )
        metrics.set_meter_provider(meter_provider)
        meter = metrics.get_meter("digital-brain.llm")
        _llm_request_count = meter.create_counter(
            "digital_brain.llm.requests",
            unit="{request}",
            description="Completed LLM chat-completion requests by outcome and model",
        )
        _llm_duration = meter.create_histogram(
            "gen_ai.client.operation.duration",
            unit="s",
            description="Duration of an LLM chat-completion operation",
        )
        _llm_token_usage = meter.create_histogram(
            "gen_ai.client.token.usage",
            unit="{token}",
            description="Input and output tokens used by LLM chat-completion operations",
        )
        _agent_run_count = meter.create_counter(
            "digital_brain.agent.runs",
            unit="{run}",
            description="Completed agent runs by profile and outcome",
        )
        _agent_run_duration = meter.create_histogram(
            "digital_brain.agent.run.duration",
            unit="s",
            description="Duration of an agent run through response finalization",
        )
        _agent_run_rounds = meter.create_histogram(
            "digital_brain.agent.run.rounds",
            unit="{round}",
            description="LLM iterations used by an agent run",
        )
        _agent_run_tool_calls = meter.create_histogram(
            "digital_brain.agent.run.tool_calls",
            unit="{call}",
            description="Tool calls used by an agent run",
        )
        _agent_run_repairs = meter.create_histogram(
            "digital_brain.agent.run.repairs",
            unit="{attempt}",
            description="Validation repair attempts used by an agent run",
        )
        _meter_provider = meter_provider
        logger.info(
            "Mirador OTLP metrics enabled service=%s export_interval_ms=%d",
            service_name,
            export_interval_ms,
        )
    except Exception:
        # Metrics are optional and must never prevent the orchestrator from starting.
        logger.exception("Mirador OTLP metrics setup failed; continuing without metrics export")

    _configured = True


def install_request_middleware(app: Any) -> None:
    """Trace the complete ASGI exchange, including streaming response bodies."""

    class RequestTracingMiddleware:
        def __init__(self, app: Any) -> None:
            self.downstream_app = app

        async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
            if scope.get("type") != "http":
                await self.downstream_app(scope, receive, send)
                return

            try:
                from opentelemetry import trace
            except ImportError:
                await self.downstream_app(scope, receive, send)
                return

            tracer = trace.get_tracer("digital-brain.http")
            with tracer.start_as_current_span("http.server") as span:
                span.set_attribute("http.request.method", scope.get("method", ""))

                async def capture_status(message: dict[str, Any]) -> None:
                    if message.get("type") == "http.response.start":
                        span.set_attribute("http.response.status_code", message.get("status", 0))
                    await send(message)

                try:
                    await self.downstream_app(scope, receive, capture_status)
                except Exception as exc:
                    span.set_attribute("http.response.status_code", 500)
                    span.set_attribute("error.type", type(exc).__name__)
                    raise

    app.add_middleware(RequestTracingMiddleware)


def shutdown_mirador() -> None:
    """Flush pending telemetry and stop OTLP exporters during graceful shutdown."""
    if _provider is not None:
        _provider.shutdown()
    if _meter_provider is not None:
        _meter_provider.shutdown()


def _record_llm_metrics(
    *,
    model: str,
    duration_seconds: float,
    outcome: str,
    result: Any,
) -> None:
    """Record bounded-cardinality LLM metrics without affecting request behavior."""
    if _llm_request_count is None or _llm_duration is None:
        return

    attributes = {
        "gen_ai.operation.name": "chat",
        "gen_ai.request.model": model,
        "llm.outcome": outcome,
    }
    try:
        _llm_request_count.add(1, attributes)
        _llm_duration.record(max(0.0, duration_seconds), attributes)

        usage = result.get("usage") if isinstance(result, dict) else None
        if _llm_token_usage is None or not isinstance(usage, dict):
            return
        for token_type, usage_key in (
            ("input", "prompt_tokens"),
            ("output", "completion_tokens"),
        ):
            try:
                token_count = int(usage.get(usage_key))
            except (TypeError, ValueError):
                continue
            if token_count >= 0:
                _llm_token_usage.record(
                    token_count,
                    {
                        "gen_ai.operation.name": "chat",
                        "gen_ai.request.model": model,
                        "gen_ai.token.type": token_type,
                    },
                )
    except Exception:
        # Never let telemetry bugs change LLM request outcomes.
        logger.debug("Could not record LLM metrics", exc_info=True)


def record_agent_run_metrics(
    *,
    profile: str,
    outcome: str,
    duration_seconds: float,
    rounds: int,
    tool_calls: int,
    repairs: int,
) -> None:
    """Record bounded-cardinality agent-run metrics and safe span summaries."""
    # Profile names and outcomes are bounded runtime values. Never attach run,
    # user, conversation, prompt, or tool argument identifiers to metrics.
    attributes = {"agent.profile": profile, "agent.outcome": outcome}
    try:
        try:
            from opentelemetry import trace

            span = trace.get_current_span()
            if span.is_recording():
                span.set_attribute("agent.profile", profile)
                span.set_attribute("agent.outcome", outcome)
                span.set_attribute("agent.rounds", max(0, rounds))
                span.set_attribute("agent.tool_calls", max(0, tool_calls))
                span.set_attribute("agent.repairs", max(0, repairs))
        except ImportError:
            pass

        if _agent_run_count is None or _agent_run_duration is None:
            return
        _agent_run_count.add(1, attributes)
        _agent_run_duration.record(max(0.0, duration_seconds), attributes)
        if _agent_run_rounds is not None:
            _agent_run_rounds.record(max(0, rounds), attributes)
        if _agent_run_tool_calls is not None:
            _agent_run_tool_calls.record(max(0, tool_calls), attributes)
        if _agent_run_repairs is not None:
            _agent_run_repairs.record(max(0, repairs), attributes)
    except Exception:
        # Telemetry must never affect response generation.
        logger.debug("Could not record agent-run metrics", exc_info=True)


def traced_agent_run(function: Callable[..., Awaitable[_T]]) -> Callable[..., Awaitable[_T]]:
    """Trace an agent run without exporting user, prompt, or response content."""

    def _span() -> Any:
        try:
            from opentelemetry import trace
        except ImportError:
            return None

        tracer = trace.get_tracer("digital-brain.agent")
        span = tracer.start_span("agent.run")
        span.set_attribute("agent.stream", function.__name__ == "run_stream")
        return span

    if inspect.isasyncgenfunction(function):

        @wraps(function)
        async def wrapped_stream(self: Any, *args: Any, **kwargs: Any):
            span = _span()
            if span is None:
                async for item in function(self, *args, **kwargs):
                    yield item
                return
            from opentelemetry import trace

            with trace.use_span(span, end_on_exit=True):
                try:
                    async for item in function(self, *args, **kwargs):
                        yield item
                    span.set_attribute("agent.outcome", "success")
                except Exception as exc:
                    span.set_attribute("agent.outcome", "error")
                    span.set_attribute("error.type", type(exc).__name__)
                    raise

        return wrapped_stream  # type: ignore[return-value]

    @wraps(function)
    async def wrapped(self: Any, *args: Any, **kwargs: Any) -> _T:
        span = _span()
        if span is None:
            return await function(self, *args, **kwargs)
        from opentelemetry import trace

        with trace.use_span(span, end_on_exit=True):
            try:
                result = await function(self, *args, **kwargs)
                outcome = (
                    "limit"
                    if isinstance(result, dict) and result.get("limit_hit")
                    else "success"
                )
                span.set_attribute("agent.outcome", outcome)
                return result
            except Exception as exc:
                span.set_attribute("agent.outcome", "error")
                span.set_attribute("error.type", type(exc).__name__)
                raise

    return wrapped


def traced_llm_request(function: Callable[..., _T]) -> Callable[..., _T]:
    """Trace model-call timing and shape only; never export prompt or completion text."""

    @wraps(function)
    def wrapped(payload: dict[str, Any], *args: Any, **kwargs: Any) -> _T:
        try:
            from opentelemetry import trace
        except ImportError:
            return function(payload, *args, **kwargs)

        messages = payload.get("messages")
        tracer = trace.get_tracer("digital-brain.llm")
        started = time.perf_counter()
        model = str(payload.get("model") or "unknown")[:128]
        result: Any = None
        outcome = "error"
        with tracer.start_as_current_span("llm.chat_completion") as span:
            span.set_attribute("gen_ai.request.model", model)
            span.set_attribute(
                "gen_ai.request.message_count", len(messages) if isinstance(messages, list) else 0
            )
            span.set_attribute("gen_ai.request.stream", function.__name__.endswith("_stream"))
            try:
                result = function(payload, *args, **kwargs)
                outcome = "success"
                span.set_attribute("llm.outcome", "success")
                if isinstance(result, dict):
                    usage = result.get("usage")
                    if isinstance(usage, dict):
                        if usage.get("prompt_tokens") is not None:
                            span.set_attribute(
                                "gen_ai.usage.input_tokens", int(usage["prompt_tokens"])
                            )
                        if usage.get("completion_tokens") is not None:
                            span.set_attribute(
                                "gen_ai.usage.output_tokens", int(usage["completion_tokens"])
                            )
                return result
            except Exception as exc:
                span.set_attribute("llm.outcome", "error")
                span.set_attribute("error.type", type(exc).__name__)
                raise
            finally:
                duration_seconds = time.perf_counter() - started
                span.set_attribute("llm.duration_ms", duration_seconds * 1000)
                _record_llm_metrics(
                    model=model,
                    duration_seconds=duration_seconds,
                    outcome=outcome,
                    result=result,
                )

    return wrapped
