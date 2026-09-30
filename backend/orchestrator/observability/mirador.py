"""Privacy-conscious OpenTelemetry setup and AI span helpers for Mirador."""

from __future__ import annotations

import inspect
import json
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
_tool_call_count: Any | None = None
_tool_call_duration: Any | None = None
_http_request_count: Any | None = None
_http_request_duration: Any | None = None


def configure_mirador() -> None:
    """Enable privacy-conscious OTLP traces and metrics when configured."""
    global _configured, _provider, _meter_provider
    global _llm_request_count, _llm_duration, _llm_token_usage
    global _agent_run_count, _agent_run_duration, _agent_run_rounds
    global _agent_run_tool_calls, _agent_run_repairs
    global _tool_call_count, _tool_call_duration
    global _http_request_count, _http_request_duration
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
            description="LLM chat-completion requests by outcome and model",
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
        _tool_call_count = meter.create_counter(
            "digital_brain.tool.calls",
            unit="{call}",
            description="Individual agent tool calls by tool and outcome",
        )
        _tool_call_duration = meter.create_histogram(
            "digital_brain.tool.duration",
            unit="s",
            description="Duration of an individual agent tool call",
        )
        _http_request_count = meter.create_counter(
            "digital_brain.http.requests",
            unit="{request}",
            description="Orchestrator HTTP requests by route, method, and status class",
        )
        _http_request_duration = meter.create_histogram(
            "digital_brain.http.request.duration",
            unit="s",
            description="Orchestrator HTTP request duration by route, method, and status class",
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
    """Trace AI entry points and measure all HTTP requests through streamed bodies."""

    default_trace_paths = {
        "/ask",
        "/mobile/ask",
        "/ask/stream",
        "/mobile/ask/stream",
        "/debug/daily-briefing/event-summary",
    }
    configured_trace_paths = os.getenv("MIRADOR_HTTP_TRACE_PATHS")
    trace_paths = (
        {path.strip() for path in configured_trace_paths.split(",") if path.strip()}
        if configured_trace_paths is not None
        else default_trace_paths
    )

    def route_template(scope: dict[str, Any]) -> str:
        route = scope.get("route")
        path = getattr(route, "path", None)
        if isinstance(path, str) and path.startswith("/") and len(path) <= 200:
            return path
        return "unmatched"

    def record_http_metrics(
        *, method: str, route: str, status_code: int, duration_seconds: float
    ) -> None:
        if _http_request_count is None or _http_request_duration is None:
            return
        attributes = {
            "http.request.method": method,
            "http.route": route,
            "http.response.status_class": f"{status_code // 100}xx",
        }
        try:
            _http_request_count.add(1, attributes)
            _http_request_duration.record(max(0.0, duration_seconds), attributes)
        except Exception:
            logger.debug("Could not record HTTP metrics", exc_info=True)

    class RequestTracingMiddleware:
        def __init__(self, app: Any) -> None:
            self.downstream_app = app

        async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
            if scope.get("type") != "http":
                await self.downstream_app(scope, receive, send)
                return

            method = str(scope.get("method", ""))
            path = str(scope.get("path", ""))
            started = time.perf_counter()
            status_code = 500

            async def capture_status(message: dict[str, Any]) -> None:
                nonlocal status_code
                if message.get("type") == "http.response.start":
                    status_code = int(message.get("status", 0))
                await send(message)

            try:
                from opentelemetry import context, propagation, trace
            except ImportError:
                try:
                    await self.downstream_app(scope, receive, capture_status)
                finally:
                    record_http_metrics(
                        method=method,
                        route=route_template(scope),
                        status_code=status_code,
                        duration_seconds=time.perf_counter() - started,
                    )
                return

            # Honor standard W3C context propagation when an upstream frontend or
            # proxy supplies traceparent. Header values are used only as context,
            # never copied into telemetry attributes.
            carrier = {
                key.decode("ascii").lower(): value.decode("ascii", errors="ignore")
                for key, value in scope.get("headers", [])
                if key.lower() in {b"traceparent", b"tracestate"}
            }
            extracted_context = propagation.extract(carrier=carrier)
            context_token = context.attach(extracted_context)
            try:
                if path in trace_paths:
                    tracer = trace.get_tracer("digital-brain.http")
                    with tracer.start_as_current_span("http.server") as span:
                        span.set_attribute("http.request.method", method)
                        try:
                            await self.downstream_app(scope, receive, capture_status)
                        except Exception as exc:
                            status_code = 500
                            span.set_attribute("error.type", type(exc).__name__)
                            raise
                        finally:
                            route = route_template(scope)
                            span.set_attribute("http.route", route)
                            span.set_attribute("http.response.status_code", status_code)
                            span.update_name(f"{method} {route}")
                else:
                    await self.downstream_app(scope, receive, capture_status)
            except Exception:
                status_code = 500
                raise
            finally:
                record_http_metrics(
                    method=method,
                    route=route_template(scope),
                    status_code=status_code,
                    duration_seconds=time.perf_counter() - started,
                )
                context.detach(context_token)

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


def _record_tool_call_metrics(
    *,
    tool_name: str,
    outcome: str,
    duration_seconds: float,
    parallel: bool,
) -> None:
    """Record an individual tool call using only bounded, non-content dimensions."""
    if _tool_call_count is None or _tool_call_duration is None:
        return
    attributes = {
        "gen_ai.tool.name": tool_name,
        "tool.outcome": outcome,
        "tool.execution.parallel": parallel,
    }
    try:
        _tool_call_count.add(1, attributes)
        _tool_call_duration.record(max(0.0, duration_seconds), attributes)
    except Exception:
        logger.debug("Could not record tool-call metrics", exc_info=True)


def traced_agent_run(function: Callable[..., Awaitable[_T]]) -> Callable[..., Awaitable[_T]]:
    """Trace an agent run without exporting user, prompt, or response content."""

    def _tracer() -> Any | None:
        try:
            from opentelemetry import trace
        except ImportError:
            return None
        return trace.get_tracer("digital-brain.agent")

    if inspect.isasyncgenfunction(function):

        @wraps(function)
        async def wrapped_stream(self: Any, *args: Any, **kwargs: Any):
            tracer = _tracer()
            if tracer is None:
                async for item in function(self, *args, **kwargs):
                    yield item
                return

            with tracer.start_as_current_span("agent.run") as span:
                span.set_attribute("agent.stream", True)
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
        tracer = _tracer()
        if tracer is None:
            return await function(self, *args, **kwargs)

        with tracer.start_as_current_span("agent.run") as span:
            span.set_attribute("agent.stream", False)
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


def traced_tool_call(function: Callable[..., Awaitable[_T]]) -> Callable[..., Awaitable[_T]]:
    """Create a safe child span around one registered agent tool invocation."""

    @wraps(function)
    async def wrapped(self: Any, call: dict[str, Any], *args: Any, **kwargs: Any) -> _T:
        function_data = call.get("function", {})
        requested_name = (
            function_data.get("name", "") if isinstance(function_data, dict) else ""
        )
        tool_name = "unknown"
        if isinstance(requested_name, str):
            try:
                from tools.registry import get_registry

                if get_registry().has_tool(requested_name):
                    tool_name = requested_name
            except Exception:
                # Observability must not change tool registry or execution behavior.
                pass

        try:
            from opentelemetry import trace
            from opentelemetry.trace import Status, StatusCode
        except ImportError:
            return await function(self, call, *args, **kwargs)

        parallel = bool(kwargs.get("allow_parallel_execution", False))
        tracer = trace.get_tracer("digital-brain.agent")
        started = time.perf_counter()
        with tracer.start_as_current_span(f"execute_tool {tool_name}") as span:
            span.set_attribute("gen_ai.operation.name", "execute_tool")
            span.set_attribute("gen_ai.tool.name", tool_name)
            span.set_attribute("gen_ai.tool.type", "function")
            span.set_attribute("tool.execution.parallel", parallel)
            outcome = "error"
            try:
                result = await function(self, call, *args, **kwargs)
                if isinstance(result, dict) and result.get("valid") is False:
                    outcome = "validation_error"
                elif isinstance(result, dict) and (
                    "error" in result or result.get("success") is False
                ):
                    outcome = "error"
                else:
                    outcome = "success"
                span.set_attribute("tool.outcome", outcome)
                if outcome in {"error", "validation_error"}:
                    span.set_status(Status(StatusCode.ERROR))
                return result
            except BaseException as exc:
                span.set_attribute("tool.outcome", "error")
                span.set_attribute("error.type", type(exc).__name__)
                span.set_status(Status(StatusCode.ERROR))
                raise
            finally:
                duration_seconds = time.perf_counter() - started
                span.set_attribute("tool.duration_ms", duration_seconds * 1000)
                _record_tool_call_metrics(
                    tool_name=tool_name,
                    outcome=outcome,
                    duration_seconds=duration_seconds,
                    parallel=parallel,
                )

    return wrapped


def traced_llm_request(function: Callable[..., _T]) -> Callable[..., _T]:
    """Trace model-call timing and shape only; never export prompt or completion text."""

    if inspect.isasyncgenfunction(function):

        @wraps(function)
        async def wrapped_stream(payload: Any, *args: Any, **kwargs: Any):
            try:
                from opentelemetry import trace
            except ImportError:
                async for item in function(payload, *args, **kwargs):
                    yield item
                return

            # Transport helpers receive a ready-made payload, while
            # stream_llm_chat receives messages and model as regular arguments.
            metadata = (
                payload
                if isinstance(payload, dict)
                else {"messages": payload, "model": kwargs.get("model")}
            )
            messages = metadata.get("messages")
            model = str(metadata.get("model") or "unknown")[:128]
            tracer = trace.get_tracer("digital-brain.llm")
            started = time.perf_counter()
            outcome = "error"
            usage: dict[str, Any] | None = None
            with tracer.start_as_current_span("llm.chat_completion") as span:
                span.set_attribute("gen_ai.operation.name", "chat")
                span.set_attribute("gen_ai.request.model", model)
                span.set_attribute(
                    "gen_ai.request.message_count",
                    len(messages) if isinstance(messages, list) else 0,
                )
                span.set_attribute("gen_ai.request.stream", True)
                try:
                    async for item in function(payload, *args, **kwargs):
                        # OpenAI-compatible streaming providers may include usage
                        # on the final SSE chunk. Parse only those bounded counts.
                        if isinstance(item, str):
                            line = item.strip()
                            if line.startswith("data: "):
                                try:
                                    chunk = json.loads(line[6:])
                                except (ValueError, TypeError):
                                    chunk = None
                                if isinstance(chunk, dict) and isinstance(
                                    chunk.get("usage"), dict
                                ):
                                    usage = chunk["usage"]
                        yield item
                    outcome = "success"
                    span.set_attribute("llm.outcome", outcome)
                    _set_llm_token_attributes(span, usage)
                except BaseException as exc:
                    outcome = "cancelled" if isinstance(exc, GeneratorExit) else "error"
                    span.set_attribute("llm.outcome", outcome)
                    if outcome == "error":
                        span.set_attribute("error.type", type(exc).__name__)
                        try:
                            from opentelemetry.trace import Status, StatusCode

                            span.set_status(Status(StatusCode.ERROR))
                        except ImportError:
                            pass
                    raise
                finally:
                    duration_seconds = time.perf_counter() - started
                    span.set_attribute("llm.duration_ms", duration_seconds * 1000)
                    _record_llm_metrics(
                        model=model,
                        duration_seconds=duration_seconds,
                        outcome=outcome,
                        result={"usage": usage} if usage is not None else None,
                    )

        return wrapped_stream  # type: ignore[return-value]

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
                    _set_llm_token_attributes(span, usage)
                return result
            except Exception as exc:
                span.set_attribute("llm.outcome", "error")
                span.set_attribute("error.type", type(exc).__name__)
                try:
                    from opentelemetry.trace import Status, StatusCode

                    span.set_status(Status(StatusCode.ERROR))
                except ImportError:
                    pass
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


def _set_llm_token_attributes(span: Any, usage: Any) -> None:
    if not isinstance(usage, dict):
        return
    for usage_key, attribute in (
        ("prompt_tokens", "gen_ai.usage.input_tokens"),
        ("completion_tokens", "gen_ai.usage.output_tokens"),
    ):
        try:
            value = int(usage[usage_key])
        except (KeyError, TypeError, ValueError):
            continue
        if value >= 0:
            span.set_attribute(attribute, value)
