"""
Centralized LLM request helpers.

This module provides a unified interface for making LLM requests across the application,
ensuring consistent configuration, error handling, and response parsing.
"""

import asyncio
import json
import os
import time
from collections.abc import AsyncGenerator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Optional

import httpx
import requests

from llm_config import (
    get_fast_keep_alive,
    get_ollama_api_base_url,
    is_ollama_base_url,
    resolve_chat_model,
)
from observability.logger import get_runtime_logger

logger = get_runtime_logger(__name__)

# LLM Configuration
LLM_BASE_URL = os.getenv("LLM_BASE_URL")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_TIMEOUT = int(os.getenv("LLM_TIMEOUT", "120"))
LLM_MAX_RETRIES = int(os.getenv("LLM_MAX_RETRIES", "3"))
LLM_RETRY_BASE_DELAY = float(os.getenv("LLM_RETRY_BASE_DELAY", "1.0"))
LLM_WARM_KEEP_ALIVE = os.getenv("LLM_WARM_KEEP_ALIVE", "15m")
_LLM_KEEP_ALIVE_OVERRIDE: ContextVar[str | int | None] = ContextVar(
    "llm_keep_alive_override", default=None
)


@contextmanager
def use_llm_keep_alive(keep_alive: str | int | None):
    token = _LLM_KEEP_ALIVE_OVERRIDE.set(keep_alive)
    try:
        yield
    finally:
        _LLM_KEEP_ALIVE_OVERRIDE.reset(token)


class LLMUnavailableError(RuntimeError):
    """Raised when the LLM service cannot be reached on a critical path."""


def _get_required_setting(name: str, fallback: str | None = None) -> str:
    """Return env setting value, raising only when an actual LLM call needs it."""
    value = os.getenv(name)
    if value is None:
        value = fallback
    normalized = str(value or "").strip()
    if not normalized:
        raise RuntimeError(f"{name} environment variable is required")
    return normalized


def _get_optional_setting(name: str, fallback: str | None = None) -> str:
    """Return env setting value if available, otherwise a normalized fallback."""
    value = os.getenv(name)
    if value is None:
        value = fallback
    return str(value or "").strip()


def get_llm_headers() -> dict[str, str]:
    """
    Get standard headers for LLM API requests.

    Returns:
        Dict with Content-Type and optional Authorization headers
    """
    headers = {"Content-Type": "application/json"}
    api_key = _get_optional_setting("LLM_API_KEY", LLM_API_KEY)
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def _maybe_attach_keep_alive(payload: dict[str, Any]) -> None:
    base_url = _get_required_setting("LLM_BASE_URL", LLM_BASE_URL)
    if not is_ollama_base_url(base_url):
        return

    keep_alive_override = _LLM_KEEP_ALIVE_OVERRIDE.get()
    if keep_alive_override is not None:
        payload["keep_alive"] = keep_alive_override
        return

    model_name = str(payload.get("model") or "").strip()
    if not model_name:
        return

    try:
        fast_model = resolve_chat_model(use_fast_model=True)
    except RuntimeError:
        return

    if model_name != fast_model:
        return

    payload["keep_alive"] = get_fast_keep_alive()


def build_chat_payload(
    messages: list[dict[str, Any]],
    *,
    model: Optional[str] = None,
    use_fast_model: Optional[bool] = None,
    stream: bool = False,
    max_tokens: Optional[int] = None,
    temperature: Optional[float] = None,
    top_p: Optional[float] = None,
    reasoning_effort: Optional[str] = None,
    response_format: Optional[dict[str, Any]] = None,
    extra_body: Optional[dict[str, Any]] = None,
    tools: Optional[list[dict[str, Any]]] = None,
    tool_choice: Optional[str | dict[str, Any]] = None,
) -> dict[str, Any]:
    resolved_model = resolve_chat_model(model=model, use_fast_model=use_fast_model)
    payload: dict[str, Any] = {
        "model": resolved_model,
        "messages": messages,
        "stream": stream,
    }

    if max_tokens:
        payload["max_tokens"] = max_tokens

    if temperature is not None:
        payload["temperature"] = temperature

    if top_p is not None:
        payload["top_p"] = top_p

    normalized_reasoning_effort = str(reasoning_effort or "").strip().lower()
    if normalized_reasoning_effort:
        payload["reasoning_effort"] = normalized_reasoning_effort
        payload["reasoning"] = {"effort": normalized_reasoning_effort}

    if response_format:
        payload["response_format"] = response_format

    if tools:
        payload["tools"] = tools
        if tool_choice is not None:
            payload["tool_choice"] = tool_choice

    if extra_body:
        payload.update(extra_body)

    _maybe_attach_keep_alive(payload)

    return payload


def warm_chat_model(
    model: str,
    *,
    timeout: Optional[int] = None,
    keep_alive: str | int | None = None,
) -> bool:
    base_url = _get_required_setting("LLM_BASE_URL", LLM_BASE_URL)
    if not is_ollama_base_url(base_url):
        logger.info("[llm_helpers] Skip model warmup for non-Ollama base URL")
        return False

    model_name = str(model or "").strip()
    if not model_name:
        raise RuntimeError("model is required for warm_chat_model")

    payload = {
        "model": model_name,
        "messages": [{"role": "user", "content": "ping"}],
        "stream": False,
        "keep_alive": keep_alive if keep_alive is not None else LLM_WARM_KEEP_ALIVE,
    }
    response = requests.post(
        f"{get_ollama_api_base_url(base_url)}/api/chat",
        headers=get_llm_headers(),
        json=payload,
        timeout=timeout or min(LLM_TIMEOUT, 30),
    )
    response.raise_for_status()
    data = response.json()
    _raise_for_llm_error(data)
    logger.info(
        "[llm_helpers] Warmed chat model model=%s keep_alive=%s done_reason=%s",
        model_name,
        payload["keep_alive"],
        data.get("done_reason", ""),
    )
    return True


def warm_fast_model(*, timeout: Optional[int] = None) -> bool:
    return warm_chat_model(
        resolve_chat_model(use_fast_model=True),
        timeout=timeout,
        keep_alive=get_fast_keep_alive(),
    )


def _raise_for_llm_error(data: dict[str, Any]) -> None:
    if "error" in data:
        error_msg = data.get("error", {})
        if isinstance(error_msg, dict):
            error_msg = error_msg.get("message", str(error_msg))
        raise RuntimeError(f"LLM API error: {error_msg}")


def is_llm_unavailable_error(exc: BaseException) -> bool:
    """Return True when an exception means the LLM service is unavailable."""
    return isinstance(
        exc,
        (
            LLMUnavailableError,
            requests.ConnectionError,
            requests.Timeout,
            httpx.ConnectError,
            httpx.ConnectTimeout,
            httpx.ReadTimeout,
        ),
    ) or (
        isinstance(exc, (requests.HTTPError, httpx.HTTPStatusError))
        and getattr(getattr(exc, "response", None), "status_code", 0) in {429}
    ) or (
        isinstance(exc, (requests.HTTPError, httpx.HTTPStatusError))
        and int(getattr(getattr(exc, "response", None), "status_code", 0) or 0) >= 500
    )


def _wrap_llm_unavailable(exc: BaseException, *, message: str | None = None) -> LLMUnavailableError:
    detail = message or "LLM service is unavailable"
    wrapped = LLMUnavailableError(detail)
    wrapped.__cause__ = exc
    return wrapped


def _is_retryable_status(status_code: int) -> bool:
    """Return True for HTTP status codes that warrant a retry."""
    return status_code >= 500 or status_code == 429


def _post_chat_completion(
    payload: dict[str, Any],
    *,
    timeout: Optional[int] = None,
) -> dict[str, Any]:
    base_url = _get_required_setting("LLM_BASE_URL", LLM_BASE_URL)
    resolved_timeout = timeout or LLM_TIMEOUT
    last_exception: Exception | None = None
    model_name = str(payload.get("model") or "").strip() or "(unset)"
    messages = payload.get("messages")
    message_count = len(messages) if isinstance(messages, list) else 0
    request_url = f"{base_url}/chat/completions"

    logger.info(
        "[llm_helpers] LLM outbound request: %s",
        json.dumps(
            {
                "url": request_url,
                "method": "POST",
                "timeout_seconds": resolved_timeout,
                "headers": {
                    "content-type": "application/json",
                    "authorization_present": bool(get_llm_headers().get("Authorization")),
                },
                "payload": payload,
            },
            ensure_ascii=False,
            default=str,
        ),
    )

    def _preview_response_text(response: requests.Response | None) -> str:
        if response is None:
            return ""
        text = (response.text or "").strip().replace("\n", " ")
        return text[:400]

    for attempt in range(1, LLM_MAX_RETRIES + 1):
        started_at = time.perf_counter()
        try:
            response = requests.post(
                request_url,
                headers=get_llm_headers(),
                json=payload,
                timeout=resolved_timeout,
            )
            elapsed_ms = (time.perf_counter() - started_at) * 1000
            if response.status_code and _is_retryable_status(response.status_code):
                last_exception = requests.HTTPError(
                    f"HTTP {response.status_code}", response=response
                )
                if attempt < LLM_MAX_RETRIES:
                    delay = LLM_RETRY_BASE_DELAY * (2 ** (attempt - 1))
                    logger.warning(
                        "[llm_helpers] LLM request failed (HTTP %s) for model=%s in %.1fms (timeout=%ss, messages=%d). Retry in %.1fs (attempt %d/%d). Response: %s",
                        response.status_code,
                        model_name,
                        elapsed_ms,
                        resolved_timeout,
                        message_count,
                        delay,
                        attempt,
                        LLM_MAX_RETRIES,
                        _preview_response_text(response),
                    )
                    time.sleep(delay)
                    continue
                response.raise_for_status()

            response.raise_for_status()
            content = response.json()
            _raise_for_llm_error(content)
            return content

        except requests.HTTPError as exc:
            response = getattr(exc, "response", None)
            status_code = response.status_code if response is not None else "unknown"
            elapsed_ms = (time.perf_counter() - started_at) * 1000
            logger.error(
                "[llm_helpers] LLM HTTP error status=%s model=%s elapsed=%.1fms timeout=%ss messages=%d attempt=%d/%d response=%s",
                status_code,
                model_name,
                elapsed_ms,
                resolved_timeout,
                message_count,
                attempt,
                LLM_MAX_RETRIES,
                _preview_response_text(response),
            )
            if is_llm_unavailable_error(exc):
                raise _wrap_llm_unavailable(exc) from exc
            raise

        except requests.Timeout as exc:
            last_exception = exc
            elapsed_ms = (time.perf_counter() - started_at) * 1000
            if attempt < LLM_MAX_RETRIES:
                delay = LLM_RETRY_BASE_DELAY * (2 ** (attempt - 1))
                logger.warning(
                    "[llm_helpers] LLM request timed out for model=%s after %.1fms (timeout=%ss, messages=%d). Retry in %.1fs (attempt %d/%d)",
                    model_name,
                    elapsed_ms,
                    resolved_timeout,
                    message_count,
                    delay,
                    attempt,
                    LLM_MAX_RETRIES,
                )
                time.sleep(delay)
            else:
                logger.error(
                    "[llm_helpers] LLM request timed out for model=%s after %.1fms (timeout=%ss, messages=%d) on final attempt",
                    model_name,
                    elapsed_ms,
                    resolved_timeout,
                    message_count,
                )
                raise _wrap_llm_unavailable(exc) from exc

        except requests.ConnectionError as exc:
            last_exception = exc
            if attempt < LLM_MAX_RETRIES:
                delay = LLM_RETRY_BASE_DELAY * (2 ** (attempt - 1))
                logger.warning(
                    "[llm_helpers] LLM request failed (%s) for model=%s, retrying in %.1fs (attempt %d/%d)",
                    type(exc).__name__,
                    model_name,
                    delay,
                    attempt,
                    LLM_MAX_RETRIES,
                )
                time.sleep(delay)
            else:
                raise _wrap_llm_unavailable(exc) from exc

    if last_exception is None:
        raise RuntimeError("LLM request failed without an exception")
    assert last_exception is not None
    raise last_exception


def _post_chat_completion_stream(
    payload: dict[str, Any],
    *,
    timeout: Optional[int] = None,
) -> dict[str, Any]:
    base_url = _get_required_setting("LLM_BASE_URL", LLM_BASE_URL)
    resolved_timeout = timeout or LLM_TIMEOUT
    request_url = f"{base_url}/chat/completions"
    accumulated_content = ""
    accumulated_reasoning = ""
    accumulated_tool_calls: dict[int, dict[str, Any]] = {}

    logger.info(
        "[llm_helpers] LLM outbound request (stream): %s",
        json.dumps(
            {
                "url": request_url,
                "method": "POST",
                "timeout_seconds": resolved_timeout,
                "headers": {
                    "content-type": "application/json",
                    "authorization_present": bool(get_llm_headers().get("Authorization")),
                },
                "payload": payload,
            },
            ensure_ascii=False,
            default=str,
        ),
    )

    response = requests.post(
        request_url,
        headers=get_llm_headers(),
        json=payload,
        timeout=resolved_timeout,
        stream=True,
    )
    response.raise_for_status()

    for raw_line in response.iter_lines(decode_unicode=True):
        line = str(raw_line or "").strip()
        if not line or line == "data: [DONE]":
            continue
        if line.startswith("data: "):
            line = line[6:]
        try:
            chunk = json.loads(line)
        except json.JSONDecodeError:
            continue

        delta = chunk.get("choices", [{}])[0].get("delta", {})
        accumulated_content += str(delta.get("content", "") or "")

        reasoning_delta = delta.get("reasoning") or delta.get("reasoning_content")
        if isinstance(reasoning_delta, list):
            for item in reasoning_delta:
                if isinstance(item, dict):
                    accumulated_reasoning += str(item.get("text") or item.get("content") or "")
        elif reasoning_delta:
            accumulated_reasoning += str(reasoning_delta)

        for tool_call in delta.get("tool_calls") or []:
            if not isinstance(tool_call, dict):
                continue
            idx = int(tool_call.get("index", 0) or 0)
            bucket = accumulated_tool_calls.setdefault(
                idx,
                {
                    "id": tool_call.get("id", ""),
                    "type": "function",
                    "function": {"name": "", "arguments": ""},
                },
            )
            if tool_call.get("id"):
                bucket["id"] = tool_call["id"]
            function = tool_call.get("function") or {}
            if function.get("name"):
                bucket["function"]["name"] = function["name"]
            if function.get("arguments"):
                bucket["function"]["arguments"] += str(function.get("arguments") or "")

    content = {
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": accumulated_content,
                    **({"reasoning": accumulated_reasoning} if accumulated_reasoning else {}),
                    **({"tool_calls": list(accumulated_tool_calls.values())} if accumulated_tool_calls else {}),
                },
                "finish_reason": "tool_calls" if accumulated_tool_calls else "stop",
            }
        ]
    }
    logger.info("[llm_helpers] LLM response (stream final sync): %s", json.dumps(content, ensure_ascii=False))
    return content


def _build_messages(prompt: str, system_prompt: Optional[str] = None) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})
    return messages


def _call_llm_raw(
    messages: list[dict[str, str]],
    *,
    model: Optional[str] = None,
    use_fast_model: Optional[bool] = None,
    timeout: Optional[int] = None,
    stream: bool = False,
    max_tokens: Optional[int] = None,
    temperature: Optional[float] = None,
    top_p: Optional[float] = None,
    reasoning_effort: Optional[str] = None,
    response_format: Optional[dict[str, Any]] = None,
    extra_body: Optional[dict[str, Any]] = None,
    tools: Optional[list[dict[str, Any]]] = None,
    tool_choice: Optional[str | dict[str, Any]] = None,
) -> dict[str, Any]:
    payload = build_chat_payload(
        messages,
        model=model,
        use_fast_model=use_fast_model,
        stream=stream,
        max_tokens=max_tokens,
        temperature=temperature,
        top_p=top_p,
        reasoning_effort=reasoning_effort,
        response_format=response_format,
        extra_body=extra_body,
        tools=tools,
        tool_choice=tool_choice,
    )

    logger.info(
        "[llm_helpers] LLM request model=%s stream=%s timeout=%s",
        payload.get("model"),
        stream,
        timeout,
    )
    logger.info("[llm_helpers] LLM input: %s", json.dumps(messages, ensure_ascii=False))
    logger.info("[llm_helpers] LLM available tools: %s", json.dumps(tools, ensure_ascii=False))
    logger.info("[llm_helpers] LLM tool choice: %s", json.dumps(tool_choice, ensure_ascii=False))

    content = (
        _post_chat_completion_stream(payload, timeout=timeout)
        if stream
        else _post_chat_completion(payload, timeout=timeout)
    )

    logger.info("[llm_helpers] LLM response: %s", json.dumps(content, ensure_ascii=False))

    return content


def call_llm_chat(
    messages: list[dict[str, Any]],
    *,
    tools: Optional[list[dict[str, Any]]] = None,
    tool_choice: Optional[str | dict[str, Any]] = None,
    model: Optional[str] = None,
    use_fast_model: Optional[bool] = None,
    timeout: Optional[int] = None,
    stream: bool = False,
    max_tokens: Optional[int] = None,
    temperature: Optional[float] = None,
    top_p: Optional[float] = None,
    reasoning_effort: Optional[str] = None,
    response_format: Optional[dict[str, Any]] = None,
    extra_body: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    data = _call_llm_raw(
        messages,
        model=model,
        use_fast_model=use_fast_model,
        timeout=timeout,
        stream=stream,
        max_tokens=max_tokens,
        temperature=temperature,
        top_p=top_p,
        reasoning_effort=reasoning_effort,
        response_format=response_format,
        extra_body=extra_body,
        tools=tools,
        tool_choice=tool_choice,
    )
    _raise_for_llm_error(data)
    return data


async def stream_llm_chat(
    messages: list[dict[str, Any]],
    *,
    tools: Optional[list[dict[str, Any]]] = None,
    tool_choice: Optional[str | dict[str, Any]] = None,
    model: Optional[str] = None,
    use_fast_model: Optional[bool] = None,
    timeout: Optional[int] = None,
    max_tokens: Optional[int] = None,
    temperature: Optional[float] = None,
    top_p: Optional[float] = None,
    reasoning_effort: Optional[str] = None,
    response_format: Optional[dict[str, Any]] = None,
    extra_body: Optional[dict[str, Any]] = None,
) -> AsyncGenerator[str, None]:
    base_url = _get_required_setting("LLM_BASE_URL", LLM_BASE_URL)
    payload = build_chat_payload(
        messages,
        model=model,
        use_fast_model=use_fast_model,
        stream=True,
        max_tokens=max_tokens,
        temperature=temperature,
        top_p=top_p,
        reasoning_effort=reasoning_effort,
        response_format=response_format,
        extra_body=extra_body,
        tools=tools,
        tool_choice=tool_choice,
    )
    logger.info("[llm_helpers] LLM input (stream): %s", json.dumps(messages, ensure_ascii=False))
    logger.info("[llm_helpers] LLM available tools (stream): %s", json.dumps(tools, ensure_ascii=False))
    logger.info("[llm_helpers] LLM tool choice (stream): %s", json.dumps(tool_choice, ensure_ascii=False))
    resolved_timeout = timeout or LLM_TIMEOUT
    timeout_config = httpx.Timeout(resolved_timeout, connect=10.0)
    last_exception: Exception | None = None

    for attempt in range(1, LLM_MAX_RETRIES + 1):
        try:
            async with httpx.AsyncClient(timeout=timeout_config) as client:
                async with client.stream(
                    "POST",
                    f"{base_url}/chat/completions",
                    headers=get_llm_headers(),
                    json=payload,
                ) as response:
                    if _is_retryable_status(response.status_code):
                        last_exception = httpx.HTTPStatusError(
                            f"HTTP {response.status_code}",
                            request=response.request,
                            response=response,
                        )
                        if attempt < LLM_MAX_RETRIES:
                            delay = LLM_RETRY_BASE_DELAY * (2 ** (attempt - 1))
                            logger.warning(
                                "[llm_helpers] Stream request failed (HTTP %s), retrying in %.1fs (attempt %d/%d)",
                                response.status_code,
                                delay,
                                attempt,
                                LLM_MAX_RETRIES,
                            )
                            await asyncio.sleep(delay)
                            continue
                        response.raise_for_status()

                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        yield line
                    logger.info("[llm_helpers] LLM response (stream): completed")
                    return  # success, stop retrying

        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout) as exc:
            last_exception = exc
            if attempt < LLM_MAX_RETRIES:
                delay = LLM_RETRY_BASE_DELAY * (2 ** (attempt - 1))
                logger.warning(
                    "[llm_helpers] Stream request failed (%s), retrying in %.1fs (attempt %d/%d)",
                    type(exc).__name__,
                    delay,
                    attempt,
                    LLM_MAX_RETRIES,
                )
                await asyncio.sleep(delay)
            else:
                raise _wrap_llm_unavailable(exc) from exc
        except httpx.HTTPStatusError as exc:
            if is_llm_unavailable_error(exc):
                raise _wrap_llm_unavailable(exc) from exc
            raise

    if last_exception is None:
        raise RuntimeError("LLM stream request failed without an exception")
    assert last_exception is not None
    raise last_exception


def call_llm(
    prompt: str,
    *,
    system_prompt: Optional[str] = None,
    model: Optional[str] = None,
    use_fast_model: Optional[bool] = None,
    timeout: Optional[int] = None,
    stream: bool = False,
    max_tokens: Optional[int] = None,
    temperature: Optional[float] = None,
    top_p: Optional[float] = None,
    reasoning_effort: Optional[str] = None,
    response_format: Optional[dict[str, Any]] = None,
    extra_body: Optional[dict[str, Any]] = None,
) -> str:
    """
    Make a synchronous chat request to the LLM.

    Args:
        prompt: The user prompt/question
        system_prompt: Optional system prompt for instructions
        model: Override the default model
        use_fast_model: Use the fast model when available
        timeout: Override the default timeout (seconds)
        max_tokens: Maximum tokens in response
        temperature: Sampling temperature (0-1, lower = more deterministic)
        top_p: Nucleus sampling threshold (0-1, lower = more focused)

    Returns:
        The LLM response content as a string

    Raises:
        requests.HTTPError: If the API request fails
        RuntimeError: If the response format is unexpected
    """
    messages = _build_messages(prompt, system_prompt=system_prompt)
    data = _call_llm_raw(
        messages,
        model=model,
        use_fast_model=use_fast_model,
        timeout=timeout,
        stream=stream,
        max_tokens=max_tokens,
        temperature=temperature,
        top_p=top_p,
        reasoning_effort=reasoning_effort,
        response_format=response_format,
        extra_body=extra_body,
    )
    content = data.get("choices", [{}])[0].get("message", {}).get("content", "")

    if not content:
        raise RuntimeError(f"LLM returned empty content: {data}")

    return content


def _extract_json_content(raw_content: str) -> str:
    """Extract JSON content from a raw LLM string, handling fenced blocks."""
    content = raw_content.strip()

    if "```json" in content:
        return content.split("```json", 1)[1].split("```", 1)[0].strip()
    if "```" in content:
        return content.split("```", 1)[1].split("```", 1)[0].strip()
    return content


def call_llm_json(
    prompt: str,
    *,
    system_prompt: Optional[str] = None,
    model: Optional[str] = None,
    use_fast_model: Optional[bool] = None,
    timeout: Optional[int] = None,
    stream: bool = False,
    max_tokens: Optional[int] = None,
    temperature: Optional[float] = None,
    top_p: Optional[float] = None,
    reasoning_effort: Optional[str] = None,
    response_format: Optional[dict[str, Any]] = None,
    extra_body: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """
    Make an LLM request and parse the response as JSON.

    Automatically extracts JSON from markdown code blocks if present.

    Args:
        prompt: The user prompt/question
        system_prompt: Optional system prompt for instructions
        model: Override the default model
        use_fast_model: Use the fast model when available
        timeout: Override the default timeout (seconds)
        max_tokens: Maximum tokens in response
        temperature: Sampling temperature (0-1, lower = more deterministic)
        top_p: Nucleus sampling threshold (0-1, lower = more focused)

    Returns:
        Parsed JSON response as a dict

    Raises:
        requests.HTTPError: If the API request fails
        json.JSONDecodeError: If the response is not valid JSON
    """
    content = call_llm(
        prompt,
        system_prompt=system_prompt,
        model=model,
        use_fast_model=use_fast_model,
        timeout=timeout,
        stream=stream,
        max_tokens=max_tokens,
        temperature=temperature,
        top_p=top_p,
        reasoning_effort=reasoning_effort,
        response_format=response_format,
        extra_body=extra_body,
    )

    return json.loads(_extract_json_content(content))


def call_llm_with_tools(
    prompt: str,
    *,
    tools: list[dict[str, Any]],
    tool_handlers: dict[str, Any],
    system_prompt: Optional[str] = None,
    model: Optional[str] = None,
    use_fast_model: Optional[bool] = None,
    timeout: Optional[int] = None,
    max_tokens: Optional[int] = None,
    temperature: Optional[float] = None,
    top_p: Optional[float] = None,
    tool_choice: Optional[str | dict[str, Any]] = None,
    max_steps: int = 6,
    max_tool_calls: int = 12,
) -> dict[str, Any]:
    """
    Make an LLM request with tool calling support.

    Returns a dict containing the final content and full message trace.
    """
    messages = _build_messages(prompt, system_prompt=system_prompt)
    tool_calls_made = 0

    for _ in range(max_steps):
        data = _call_llm_raw(
            messages,
            model=model,
            use_fast_model=use_fast_model,
            timeout=timeout,
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
            tools=tools,
            tool_choice=tool_choice,
        )
        message = data.get("choices", [{}])[0].get("message", {})
        messages.append(message)

        tool_calls = message.get("tool_calls", [])
        if not tool_calls:
            content = message.get("content", "")
            if not content:
                raise RuntimeError(f"LLM returned empty content: {data}")
            return {"content": content, "messages": messages, "tool_calls": tool_calls_made}

        for tool_call in tool_calls:
            if tool_calls_made >= max_tool_calls:
                raise RuntimeError("Exceeded max_tool_calls")
            tool_calls_made += 1

            function = tool_call.get("function", {})
            name = function.get("name")
            raw_args = function.get("arguments", "{}")
            logger.debug("[llm_helpers] Tool call: %s args=%s", name, raw_args)
            try:
                args = json.loads(raw_args) if raw_args else {}
            except json.JSONDecodeError:
                args = {"_raw": raw_args}

            handler = tool_handlers.get(name)
            if not handler:
                result = {"error": f"Tool handler not found: {name}"}
            else:
                try:
                    result = handler(args)
                except Exception as exc:
                    result = {"error": f"Tool handler error: {exc}"}

            logger.debug("[llm_helpers] Tool result: %s -> %s", name, result)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.get("id", ""),
                    "content": json.dumps(result),
                }
            )

    raise RuntimeError("LLM did not return a final response after tool calls")


def call_llm_json_with_tools(
    prompt: str,
    *,
    tools: list[dict[str, Any]],
    tool_handlers: dict[str, Any],
    system_prompt: Optional[str] = None,
    model: Optional[str] = None,
    use_fast_model: Optional[bool] = None,
    timeout: Optional[int] = None,
    max_tokens: Optional[int] = None,
    temperature: Optional[float] = None,
    top_p: Optional[float] = None,
    tool_choice: Optional[str | dict[str, Any]] = None,
    max_steps: int = 6,
    max_tool_calls: int = 12,
) -> dict[str, Any]:
    """
    Make an LLM request with tools and parse the final response as JSON.
    """
    result = call_llm_with_tools(
        prompt,
        tools=tools,
        tool_handlers=tool_handlers,
        system_prompt=system_prompt,
        model=model,
        use_fast_model=use_fast_model,
        timeout=timeout,
        max_tokens=max_tokens,
        temperature=temperature,
        top_p=top_p,
        tool_choice=tool_choice,
        max_steps=max_steps,
        max_tool_calls=max_tool_calls,
    )

    content = result.get("content", "")
    return json.loads(_extract_json_content(content))


def call_llm_with_context(
    prompt: str,
    *,
    user_email: Optional[str] = None,
    time_context: Optional[str] = None,
    additional_context: Optional[dict[str, str]] = None,
    system_prompt: Optional[str] = None,
    model: Optional[str] = None,
    use_fast_model: Optional[bool] = None,
    timeout: Optional[int] = None,
) -> str:
    """
    Make an LLM request with rich context information.

    Automatically adds user and time context to the prompt.

    Args:
        prompt: The user prompt/question
        user_email: User's email for context
        time_context: Current time context string
        additional_context: Additional context key-value pairs
        system_prompt: Optional system prompt for instructions
        model: Override the default model
        use_fast_model: Use the fast model when available
        timeout: Override the default timeout (seconds)

    Returns:
        The LLM response content as a string
    """
    context_parts = []

    if time_context:
        context_parts.append(f"- Date/time: {time_context}")

    if user_email:
        context_parts.append(f"- User: {user_email}")

    if additional_context:
        for key, value in additional_context.items():
            context_parts.append(f"- {key}: {value}")

    if context_parts:
        context_block = "Current context:\n" + "\n".join(context_parts)
        full_prompt = f"{context_block}\n\n{prompt}"
    else:
        full_prompt = prompt

    return call_llm(
        full_prompt,
        system_prompt=system_prompt,
        model=model,
        use_fast_model=use_fast_model,
        timeout=timeout,
    )
