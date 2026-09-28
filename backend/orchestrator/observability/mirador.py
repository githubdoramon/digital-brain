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


def configure_mirador() -> None:
    """Enable FastAPI and OTLP tracing when a Mirador server key is configured."""
    global _configured, _provider
    if _configured:
        return

    api_key = os.getenv("MIRADOR_API_KEY", "").strip()
    if not api_key:
        logger.info("Mirador telemetry disabled: MIRADOR_API_KEY is not configured")
        return

    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        endpoint = os.getenv("MIRADOR_OTEL_ENDPOINT", "https://otel.mirador.org/v1/traces")
        service_name = os.getenv("OTEL_SERVICE_NAME", "digital-brain-orchestrator")
        provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
        provider.add_span_processor(
            BatchSpanProcessor(
                OTLPSpanExporter(endpoint=endpoint, headers={"Authorization": api_key})
            )
        )
        trace.set_tracer_provider(provider)
        _provider = provider
        _configured = True
        logger.info("Mirador OTLP tracing enabled service=%s", service_name)
    except Exception:
        # Telemetry setup must never prevent the orchestrator from starting.
        logger.exception("Mirador OTLP tracing setup failed; continuing without export")


def install_request_middleware(app: Any) -> None:
    """Trace the complete ASGI exchange, including streaming response bodies."""

    class RequestTracingMiddleware:
        def __init__(self, downstream_app: Any) -> None:
            self.downstream_app = downstream_app

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
    """Flush pending spans and stop the OTLP exporter during graceful shutdown."""
    if _provider is not None:
        _provider.shutdown()


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
                span.set_attribute("agent.outcome", "success")
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
        with tracer.start_as_current_span("llm.chat_completion") as span:
            span.set_attribute("gen_ai.request.model", str(payload.get("model") or "unknown"))
            span.set_attribute(
                "gen_ai.request.message_count", len(messages) if isinstance(messages, list) else 0
            )
            span.set_attribute("gen_ai.request.stream", function.__name__.endswith("_stream"))
            try:
                result = function(payload, *args, **kwargs)
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
                span.set_attribute("llm.duration_ms", (time.perf_counter() - started) * 1000)

    return wrapped
