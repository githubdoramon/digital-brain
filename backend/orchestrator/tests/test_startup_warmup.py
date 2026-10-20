from __future__ import annotations

import asyncio
import importlib
import json
from contextlib import nullcontext
from unittest.mock import MagicMock

import httpx
import pytest

import llm_helpers


@pytest.fixture
def app_module(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "https://models.example/v1")
    monkeypatch.setenv("LLM_CHAT_MODEL_FAST", "fast-test")
    monkeypatch.setenv("LLM_CHAT_MODEL_SMART", "smart-test")
    module = importlib.import_module("app")
    for name in (
        "configure_logging",
        "install_stdout_logger",
        "configure_mirador",
        "shutdown_mirador",
        "run_pending_migrations",
    ):
        monkeypatch.setattr(module, name, MagicMock())
    monkeypatch.setattr(module, "get_conn", nullcontext)
    for worker in vars(module).values():
        if hasattr(worker, "start_worker") and hasattr(worker, "stop_worker"):
            monkeypatch.setattr(worker, "start_worker", MagicMock())
            monkeypatch.setattr(worker, "stop_worker", MagicMock())
    return module


async def test_startup_serves_requests_while_warmup_is_stuck_and_shutdown_cancels(
    app_module, monkeypatch
):
    entered = asyncio.Event()
    cancelled = asyncio.Event()

    async def warm():
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(app_module, "warm_configured_chat_models", warm)
    test_app = app_module.FastAPI()

    @test_app.get("/ready")
    async def ready():
        return {"ready": True}

    lifespan = app_module.lifespan(test_app)
    try:
        await asyncio.wait_for(lifespan.__aenter__(), timeout=1)
        await asyncio.wait_for(entered.wait(), timeout=1)
        assert app_module.meeting_transcript_jobs.start_worker.call_count == 1
        assert app_module.knowledge_jobs.start_worker.call_count == 1
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=test_app), base_url="http://test.example"
        ) as client:
            response = await asyncio.wait_for(client.get("/ready"), timeout=1)
        assert response.status_code == 200
        assert response.json() == {"ready": True}
    finally:
        await asyncio.wait_for(lifespan.__aexit__(None, None, None), timeout=1)
    assert cancelled.is_set()
    assert app_module.knowledge_jobs.stop_worker.call_count == 1
    assert app_module.shutdown_mirador.call_count == 1


async def test_warmup_failure_does_not_fail_startup_or_shutdown(app_module, monkeypatch, caplog):
    completed = asyncio.Event()

    async def warm():
        completed.set()
        raise RuntimeError("PRIVATE_PROVIDER_BODY")

    monkeypatch.setattr(app_module, "warm_configured_chat_models", warm)
    async with app_module.lifespan(app_module.api):
        await asyncio.wait_for(completed.wait(), timeout=1)
    assert app_module.meeting_transcript_jobs.stop_worker.call_count == 1
    assert app_module.shutdown_mirador.call_count == 1
    assert "reason=RuntimeError" in caplog.text
    assert "PRIVATE_PROVIDER_BODY" not in caplog.text


@pytest.fixture
def async_transport(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "https://models.example/v1")
    real_client = httpx.AsyncClient
    clients = []

    def install(handler):
        def client(**kwargs):
            instance = real_client(transport=httpx.MockTransport(handler), **kwargs)
            clients.append(instance)
            return instance

        monkeypatch.setattr(llm_helpers.httpx, "AsyncClient", client)
        return clients

    return install


@pytest.mark.parametrize("native_status", [404, 405])
async def test_async_warmup_uses_bounded_compatible_fallback(async_transport, native_status):
    calls = []

    async def handle(request):
        calls.append((str(request.url), json.loads(request.content)))
        if request.url.path == "/api/chat":
            return httpx.Response(native_status)
        return httpx.Response(200, json={"choices": [{"message": {"content": ""}}]})

    clients = async_transport(handle)
    payload = llm_helpers._chat_warmup_payload("smart-test", "20m")
    await llm_helpers._post_chat_model_warmup_async(payload, timeout=1)
    assert calls[0][1]["messages"] == []
    assert calls[1] == (
        "https://models.example/v1/chat/completions",
        {
            "model": "smart-test",
            "messages": [{"role": "user", "content": "ping"}],
            "stream": False,
            "max_tokens": 1,
            "reasoning_effort": "none",
            "reasoning": {"effort": "none"},
        },
    )
    assert all(client.is_closed for client in clients)


async def test_warmup_timeout_closes_fallback_and_does_not_use_high_reasoning_timeout(
    async_transport, monkeypatch
):
    monkeypatch.setenv("LLM_TIMEOUT", "900")
    monkeypatch.setenv("LLM_HIGH_REASONING_TIMEOUT", "900")
    fallback_cancelled = asyncio.Event()

    async def handle(request):
        if request.url.path == "/api/chat":
            return httpx.Response(404)
        try:
            await asyncio.Event().wait()
        finally:
            fallback_cancelled.set()

    clients = async_transport(handle)
    with pytest.raises(TimeoutError):
        await llm_helpers._post_chat_model_warmup_async(
            llm_helpers._chat_warmup_payload("smart-test", "20m"), timeout=0.02
        )
    assert fallback_cancelled.is_set()
    assert all(client.is_closed for client in clients)


async def test_shutdown_cancel_closes_inflight_http_client(async_transport):
    entered = asyncio.Event()
    cancelled = asyncio.Event()

    async def handle(request):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    clients = async_transport(handle)
    task = asyncio.create_task(
        llm_helpers._post_chat_model_warmup_async(
            llm_helpers._chat_warmup_payload("smart-test", "20m"), timeout=180
        )
    )
    await asyncio.wait_for(entered.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cancelled.is_set()
    assert all(client.is_closed for client in clients)


@pytest.mark.parametrize("outcome", ["success", "failure", "cancelled"])
async def test_async_warmup_trace_covers_the_await_and_records_only_safe_metadata(
    monkeypatch, outcome
):
    import sys
    import types
    from contextlib import contextmanager

    from observability import mirador

    attributes = {}
    closed = []
    options = []
    span = MagicMock()
    span.set_attribute.side_effect = lambda key, value: attributes.__setitem__(key, value)

    @contextmanager
    def start_span(name, **kwargs):
        options.append(kwargs)
        try:
            yield span
        finally:
            closed.append(True)

    trace = types.ModuleType("opentelemetry.trace")
    trace.get_tracer = lambda _: types.SimpleNamespace(start_as_current_span=start_span)
    trace.StatusCode = types.SimpleNamespace(ERROR="error")
    trace.Status = lambda code: code
    package = types.ModuleType("opentelemetry")
    package.trace = trace
    monkeypatch.setitem(sys.modules, "opentelemetry", package)
    monkeypatch.setitem(sys.modules, "opentelemetry.trace", trace)
    metrics = MagicMock()
    monkeypatch.setattr(mirador, "_record_llm_metrics", metrics)
    entered = asyncio.Event()
    release = asyncio.Event()

    @mirador.traced_llm_request
    async def request(payload):
        entered.set()
        await release.wait()
        if outcome == "failure":
            raise RuntimeError("PRIVATE_PROVIDER_BODY")
        return {"usage": {"prompt_tokens": 3, "completion_tokens": 1}}

    task = asyncio.create_task(request({"model": "smart-test", "messages": []}))
    try:
        await asyncio.wait_for(entered.wait(), timeout=1)
        assert not closed
        metrics.assert_not_called()
        if outcome == "cancelled":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            release.set()
            if outcome == "failure":
                with pytest.raises(RuntimeError):
                    await task
            else:
                await task
    finally:
        if not task.done():
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
    assert closed == [True]
    assert options == [{"record_exception": False, "set_status_on_exception": False}]
    assert attributes["llm.outcome"] == ("success" if outcome == "success" else "error")
    assert attributes["gen_ai.request.stream"] is False
    assert "PRIVATE_PROVIDER_BODY" not in str(attributes)
    if outcome == "success":
        assert attributes["gen_ai.usage.input_tokens"] == 3
        assert attributes["gen_ai.usage.output_tokens"] == 1
    else:
        assert attributes["error.type"] == (
            "RuntimeError" if outcome == "failure" else "CancelledError"
        )
    metrics.assert_called_once()
