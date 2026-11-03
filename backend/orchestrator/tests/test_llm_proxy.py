import asyncio
import json

import httpx
import pytest
from fastapi import FastAPI

import llm_admission
from routes import llm_proxy as proxy

TOKEN = "test-proxy-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
PAYLOAD = {"model": "chat-fast", "messages": [{"role": "user", "content": "Hello"}]}


@pytest.fixture
def gateway(monkeypatch):
    monkeypatch.setenv("LLM_PROXY_TOKEN", TOKEN)
    monkeypatch.setenv("LLM_BASE_URL", "https://provider.example/v1")
    monkeypatch.setenv("LLM_API_KEY", "private-upstream-key")
    monkeypatch.setenv("LLM_CHAT_MODEL_FAST", "chat-fast")
    monkeypatch.setenv("LLM_CHAT_MODEL_SMART", "chat-smart")
    monkeypatch.setenv("LLM_CHAT_MODEL_VISION", "chat-smart")
    monkeypatch.setattr(llm_admission, "_STATES", {})
    app = FastAPI()
    app.include_router(proxy.create_llm_proxy_router())
    factory = httpx.AsyncClient
    calls = []

    def upstream(request):
        calls.append(request)
        return httpx.Response(200, json={"choices": [], "usage": {"total_tokens": 10}})

    def install(handler=upstream):
        monkeypatch.setattr(
            proxy.httpx,
            "AsyncClient",
            lambda **kwargs: factory(
                transport=httpx.MockTransport(handler),
                **kwargs,
            ),
        )

    install()
    return (
        factory(transport=httpx.ASGITransport(app), base_url="http://gateway.example"),
        calls,
        install,
    )


@pytest.mark.parametrize("path", ["models", "chat/completions"])
async def test_token_required_and_disabled(gateway, monkeypatch, path):
    client, calls, _ = gateway
    method = client.get if path == "models" else client.post
    for headers in ({}, {"Authorization": "Bearer incorrect"}, {"Authorization": "Bearer é"}):
        # httpx headers are ASCII, while raw ASGI may contain arbitrary bytes.
        if "é" in headers.get("Authorization", ""):
            headers = {"Authorization": b"Bearer \xff"}
        response = await method(f"/llm/v1/{path}", headers=headers)
        assert response.status_code == 401
    monkeypatch.delenv("LLM_PROXY_TOKEN")
    assert (await method(f"/llm/v1/{path}", headers=AUTH)).status_code == 503
    assert calls == []


async def test_lists_only_configured_models(gateway):
    client, calls, _ = gateway
    response = await client.get("/llm/v1/models", headers=AUTH)
    assert response.json() == {
        "object": "list",
        "data": [
            {"id": name, "object": "model", "owned_by": "digital-brain"}
            for name in ("chat-fast", "chat-smart")
        ],
    }
    assert response.headers["cache-control"] == "no-store"
    assert calls == []


@pytest.mark.parametrize("base", ["https://provider.example", "https://provider.example/v1/"])
async def test_json_forwarding_uses_only_server_credentials(gateway, monkeypatch, base, caplog):
    client, calls, _ = gateway
    monkeypatch.setenv("LLM_BASE_URL", base)
    payload = {
        **PAYLOAD,
        "tools": [{"type": "function", "function": {"name": "lookup"}}],
        "tool_choice": "auto",
        "response_format": {"type": "json_object"},
        "max_tokens": 128,
    }
    response = await client.post(
        "/llm/v1/chat/completions", headers={**AUTH, "Cookie": "private"}, json=payload
    )
    assert response.status_code == 200
    assert response.json()["usage"] == {"total_tokens": 10}
    assert str(calls[0].url) == "https://provider.example/v1/chat/completions"
    assert json.loads(calls[0].content) == payload
    assert calls[0].headers["authorization"] == "Bearer private-upstream-key"
    assert "cookie" not in calls[0].headers
    assert all(state["foreground"] == 0 for state in llm_admission.status())
    for private in (TOKEN, "private-upstream-key", "Hello"):
        assert private not in caplog.text


@pytest.mark.parametrize(
    "change",
    [
        {"model": "not-allowed"},
        {"messages": []},
        {"stream": "true"},
        {"max_tokens": 0},
        {"max_tokens": True},
        {"max_completion_tokens": 16385},
        {"n": 2},
    ],
)
async def test_invalid_requests_never_reach_provider(gateway, change):
    client, calls, _ = gateway
    response = await client.post(
        "/llm/v1/chat/completions", headers=AUTH, json={**PAYLOAD, **change}
    )
    assert response.status_code == 400
    assert calls == []


async def test_invalid_and_oversized_bodies(gateway):
    client, calls, _ = gateway
    for body in (b"{", b"[]", b"\xff"):
        assert (
            await client.post("/llm/v1/chat/completions", headers=AUTH, content=body)
        ).status_code == 400
    response = await client.post(
        "/llm/v1/chat/completions", headers=AUTH, content=b" " * (proxy.MAX_REQUEST_BYTES + 1)
    )
    assert response.status_code == 413
    assert calls == []


@pytest.mark.parametrize("status,expected", [(302, 502), (401, 502), (429, 429), (500, 502)])
async def test_provider_errors_are_sanitized(gateway, status, expected):
    client, _, install = gateway
    install(
        lambda _: httpx.Response(
            status,
            text="private upstream details",
            headers={"Location": "https://internal.example"},
        )
    )
    response = await client.post("/llm/v1/chat/completions", headers=AUTH, json=PAYLOAD)
    assert response.status_code == expected
    assert "private" not in response.text
    assert "location" not in response.headers
    if status == 429:
        assert response.headers["retry-after"] == "5"
    assert all(state["foreground"] == 0 for state in llm_admission.status())


@pytest.mark.parametrize("error,expected", [(httpx.ConnectError, 502), (httpx.ReadTimeout, 504)])
async def test_network_errors_release_capacity(gateway, error, expected):
    client, _, install = gateway

    def fail(_):
        raise error("private provider address")

    install(fail)
    for _ in range(3):
        response = await client.post("/llm/v1/chat/completions", headers=AUTH, json=PAYLOAD)
        assert response.status_code == expected
        assert "private" not in response.text
    assert all(state["foreground"] == 0 for state in llm_admission.status())


async def test_sse_bytes_and_usage_pass_through(gateway):
    client, calls, install = gateway
    wire = b'data: {"choices":[{"delta":{"tool_calls":[]}}]}\n\ndata: {"usage":{"total_tokens":10}}\n\ndata: [DONE]\n\n'

    def handle(request):
        calls.append(request)
        return httpx.Response(200, content=wire, headers={"Content-Type": "text/event-stream"})

    install(handle)
    response = await client.post(
        "/llm/v1/chat/completions",
        headers=AUTH,
        json={**PAYLOAD, "stream": True, "stream_options": {"include_usage": True}},
    )
    assert response.content == wire
    assert response.headers["x-accel-buffering"] == "no"
    assert json.loads(calls[0].content)["stream_options"] == {"include_usage": True}
    assert all(state["foreground"] == 0 for state in llm_admission.status())


async def test_concurrency_is_bounded_and_released(gateway):
    client, _, install = gateway
    release = asyncio.Event()
    both_entered = asyncio.Event()
    count = 0

    async def handle(_):
        nonlocal count
        count += 1
        if count == 2:
            both_entered.set()
        await release.wait()
        return httpx.Response(200, json={"choices": []})

    install(handle)
    tasks = [
        asyncio.create_task(client.post("/llm/v1/chat/completions", headers=AUTH, json=PAYLOAD))
        for _ in range(2)
    ]
    await asyncio.wait_for(both_entered.wait(), 2)
    assert sum(state["foreground"] for state in llm_admission.status()) == 2
    assert (
        await client.post("/llm/v1/chat/completions", headers=AUTH, json=PAYLOAD)
    ).status_code == 429
    release.set()
    assert all(response.status_code == 200 for response in await asyncio.gather(*tasks))
    assert (
        await client.post("/llm/v1/chat/completions", headers=AUTH, json=PAYLOAD)
    ).status_code == 200


@pytest.mark.parametrize("phase", ["headers", "body", "stream"])
async def test_disconnect_closes_upstream_and_releases_admission(gateway, phase):
    client, _, install = gateway
    first_chunk = asyncio.Event()
    closed = asyncio.Event()

    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            if phase == "stream":
                yield b'data: {"choices":[]}\n\n'
            else:
                first_chunk.set()
            await asyncio.Event().wait()

        async def aclose(self):
            closed.set()

    async def handle(_):
        if phase == "headers":
            first_chunk.set()
            try:
                await asyncio.Event().wait()
            finally:
                closed.set()
        return httpx.Response(200, stream=Stream(), headers={"Content-Type": "text/event-stream"})

    install(handle)
    body = json.dumps({**PAYLOAD, "stream": phase != "body"}).encode()
    sent = False

    async def receive():
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        await first_chunk.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        if message["type"] == "http.response.body" and message.get("body"):
            first_chunk.set()

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.0"},
        "method": "POST",
        "path": "/llm/v1/chat/completions",
        "raw_path": b"/llm/v1/chat/completions",
        "query_string": b"",
        "headers": [(b"authorization", f"Bearer {TOKEN}".encode())],
        "scheme": "http",
        "server": ("gateway.example", 80),
        "client": ("test", 1),
        "root_path": "",
    }
    await asyncio.wait_for(client._transport.app(scope, receive, send), 2)
    assert first_chunk.is_set()
    assert closed.is_set()
    assert all(state["foreground"] == 0 for state in llm_admission.status())
    install()
    assert (
        await client.post("/llm/v1/chat/completions", headers=AUTH, json=PAYLOAD)
    ).status_code == 200
