"""Stateless, narrowly authenticated access to the private inference provider."""

from __future__ import annotations

import asyncio
import hmac
import json
import os
from contextlib import AsyncExitStack

import anyio
import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

import llm_admission
from llm_policy import request_timeout

MAX_REQUEST_BYTES = 2 * 1024 * 1024
MAX_OUTPUT_TOKENS = 16384
MAX_CONCURRENT_REQUESTS = 2


def require_proxy_token(authorization: str = Header(default="")) -> None:
    expected = os.getenv("LLM_PROXY_TOKEN", "").strip()
    if not expected:
        raise HTTPException(503, "LLM proxy is disabled")
    parts = authorization.split()
    if (
        len(parts) != 2
        or parts[0].lower() != "bearer"
        or not hmac.compare_digest(parts[1].encode(), expected.encode())
    ):
        raise HTTPException(401, "Invalid proxy token", headers={"WWW-Authenticate": "Bearer"})


def configured_models() -> list[str]:
    return list(
        dict.fromkeys(
            value
            for name in ("LLM_CHAT_MODEL_FAST", "LLM_CHAT_MODEL_SMART", "LLM_CHAT_MODEL_VISION")
            if (value := os.getenv(name, "").strip())
        )
    )


async def read_payload(request: Request) -> dict:
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_REQUEST_BYTES:
            raise HTTPException(413, "Request body is too large")
        body.extend(chunk)
    try:
        payload = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(400, "Invalid JSON body") from None
    if not isinstance(payload, dict):
        raise HTTPException(400, "Expected a JSON object")
    if not isinstance(payload.get("model"), str) or payload["model"] not in configured_models():
        raise HTTPException(400, "Model is not available through this proxy")
    messages = payload.get("messages")
    if (
        not isinstance(messages, list)
        or not messages
        or any(not isinstance(message, dict) for message in messages)
    ):
        raise HTTPException(400, "messages must be a non-empty array of objects")
    if "stream" in payload and not isinstance(payload["stream"], bool):
        raise HTTPException(400, "stream must be a boolean")
    if type(payload.get("n", 1)) is not int or payload.get("n", 1) != 1:
        raise HTTPException(400, "Only one completion per request is supported")
    for key in ("max_tokens", "max_completion_tokens"):
        if key in payload and (
            type(payload[key]) is not int or not 1 <= payload[key] <= MAX_OUTPUT_TOKENS
        ):
            raise HTTPException(400, f"{key} must be between 1 and {MAX_OUTPUT_TOKENS}")
    if "max_tokens" not in payload and "max_completion_tokens" not in payload:
        payload["max_tokens"] = MAX_OUTPUT_TOKENS
    return payload


async def while_connected(request: Request, operation):
    async def disconnected():
        while (await request.receive())["type"] != "http.disconnect":
            pass
        raise HTTPException(499, "Client disconnected")

    pending = asyncio.create_task(operation)
    watcher = asyncio.create_task(disconnected())
    try:
        done, _ = await asyncio.wait((pending, watcher), return_when=asyncio.FIRST_COMPLETED)
        if watcher in done:
            return await watcher
        return await pending
    finally:
        for task in (pending, watcher):
            if not task.done():
                task.cancel()
        await asyncio.gather(pending, watcher, return_exceptions=True)


class _ManagedStream(StreamingResponse):
    def __init__(self, upstream: httpx.Response, stack: AsyncExitStack, timeout: float):
        super().__init__(
            upstream.aiter_bytes(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )
        self.stack = stack
        self.timeout = timeout

    async def __call__(self, scope, receive, send):
        try:
            async with asyncio.timeout(self.timeout):
                await super().__call__(scope, receive, send)
        finally:
            # Cleanup must survive ASGI cancellation, including before iteration starts.
            with anyio.CancelScope(shield=True):
                await self.stack.aclose()


def create_llm_proxy_router() -> APIRouter:
    router = APIRouter(prefix="/llm/v1", dependencies=[Depends(require_proxy_token)])
    slots = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)

    @router.get("/models")
    async def models():
        return JSONResponse(
            {
                "object": "list",
                "data": [
                    {"id": model, "object": "model", "owned_by": "digital-brain"}
                    for model in configured_models()
                ],
            },
            headers={"Cache-Control": "no-store"},
        )

    @router.post("/chat/completions")
    async def completions(request: Request):
        payload = await read_payload(request)
        base = os.getenv("LLM_BASE_URL", "").strip().rstrip("/")
        if not base:
            raise HTTPException(503, "LLM provider is not configured")
        if slots.locked():
            raise HTTPException(429, "LLM proxy is busy", headers={"Retry-After": "5"})

        stack = AsyncExitStack()
        streaming = False
        try:
            await stack.enter_async_context(slots)
            stack.enter_context(llm_admission.request_slot(base))
            timeout = min(900, request_timeout(payload))
            client = await stack.enter_async_context(
                httpx.AsyncClient(
                    timeout=httpx.Timeout(timeout, connect=10),
                    follow_redirects=False,
                )
            )
            headers = {"Content-Type": "application/json"}
            key = os.getenv("LLM_API_KEY", "").strip()
            if key:
                headers["Authorization"] = f"Bearer {key}"
            # Root URLs are supported for Ollama; custom provider path prefixes are preserved.
            endpoint = f"{base}/v1" if httpx.URL(base).path in ("", "/") else base

            async def open_upstream():
                response = await client.send(
                    client.build_request(
                        "POST", f"{endpoint}/chat/completions", headers=headers, json=payload
                    ),
                    stream=True,
                )
                stack.push_async_callback(response.aclose)
                return response

            upstream = await while_connected(request, open_upstream())
            if upstream.status_code != 200:
                # Provider bodies/headers can contain internal URLs or credentials.
                status = 429 if upstream.status_code == 429 else 502
                return JSONResponse(
                    {
                        "error": {
                            "message": "LLM provider rejected the request",
                            "type": "upstream_error",
                        }
                    },
                    status_code=status,
                    headers={
                        "Cache-Control": "no-store",
                        **({"Retry-After": "5"} if status == 429 else {}),
                    },
                )
            if payload.get("stream"):
                if "text/event-stream" not in upstream.headers.get("content-type", ""):
                    raise HTTPException(502, "LLM provider returned an invalid stream")
                response = _ManagedStream(upstream, stack, timeout)
                streaming = True
                return response
            async with asyncio.timeout(timeout):
                body = await while_connected(request, upstream.aread())
            return Response(
                body, media_type="application/json", headers={"Cache-Control": "no-store"}
            )
        except (httpx.TimeoutException, TimeoutError):
            raise HTTPException(504, "LLM provider timed out") from None
        except httpx.HTTPError:
            raise HTTPException(502, "LLM provider is unavailable") from None
        finally:
            if not streaming:
                with anyio.CancelScope(shield=True):
                    await stack.aclose()

    return router
