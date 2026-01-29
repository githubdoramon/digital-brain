"""
Centralized LLM request helpers.

This module provides a unified interface for making LLM requests across the application,
ensuring consistent configuration, error handling, and response parsing.
"""

import json
import os
from typing import Any, Optional

import requests

# LLM Configuration
LLM_BASE_URL = os.getenv("LLM_BASE_URL")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_CHAT_MODEL = os.getenv("LLM_CHAT_MODEL")
LLM_CHAT_MODEL_SIMPLER = os.getenv("LLM_CHAT_MODEL_SIMPLER")
LLM_TIMEOUT = int(os.getenv("LLM_TIMEOUT", "120"))

# Validate configuration
if not LLM_BASE_URL:
    raise RuntimeError("LLM_BASE_URL environment variable is required")
if not LLM_CHAT_MODEL:
    raise RuntimeError("LLM_CHAT_MODEL environment variable is required")


def get_llm_headers() -> dict[str, str]:
    """
    Get standard headers for LLM API requests.

    Returns:
        Dict with Content-Type and optional Authorization headers
    """
    headers = {"Content-Type": "application/json"}
    if LLM_API_KEY:
        headers["Authorization"] = f"Bearer {LLM_API_KEY}"
    return headers


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
    use_simpler_model: Optional[bool] = None,
    timeout: Optional[int] = None,
    max_tokens: Optional[int] = None,
    temperature: Optional[float] = None,
    top_p: Optional[float] = None,
    tools: Optional[list[dict[str, Any]]] = None,
    tool_choice: Optional[str | dict[str, Any]] = None,
) -> dict[str, Any]:
    resolved_model = model
    if not resolved_model and use_simpler_model and LLM_CHAT_MODEL_SIMPLER:
        resolved_model = LLM_CHAT_MODEL_SIMPLER

    payload: dict[str, Any] = {
        "model": resolved_model or LLM_CHAT_MODEL,
        "messages": messages,
        "stream": False,
    }

    if max_tokens:
        payload["max_tokens"] = max_tokens

    if temperature is not None:
        payload["temperature"] = temperature

    if top_p is not None:
        payload["top_p"] = top_p

    if tools:
        payload["tools"] = tools
        if tool_choice is not None:
            payload["tool_choice"] = tool_choice

    response = requests.post(
        f"{LLM_BASE_URL}/chat/completions",
        headers=get_llm_headers(),
        json=payload,
        timeout=timeout or LLM_TIMEOUT,
    )
    response.raise_for_status()

    content = response.json()

    print(f"[llm_helpers] LLM input: {json.dumps(messages, ensure_ascii=False)}")
    print(f"[llm_helpers] LLM response: {json.dumps(content, ensure_ascii=False)}")

    return content


def call_llm(
    prompt: str,
    *,
    system_prompt: Optional[str] = None,
    model: Optional[str] = None,
    use_simpler_model: Optional[bool] = None,
    timeout: Optional[int] = None,
    max_tokens: Optional[int] = None,
    temperature: Optional[float] = None,
    top_p: Optional[float] = None,
) -> str:
    """
    Make a synchronous chat request to the LLM.

    Args:
        prompt: The user prompt/question
        system_prompt: Optional system prompt for instructions
        model: Override the default model
        use_simpler_model: Use the simpler model when available
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
        use_simpler_model=use_simpler_model,
        timeout=timeout,
        max_tokens=max_tokens,
        temperature=temperature,
        top_p=top_p,
    )
    content = data.get("choices", [{}])[0].get("message", {}).get("content", "")

    if not content:
        raise RuntimeError(f"LLM returned empty content: {data}")

    return content


def call_llm_json(
    prompt: str,
    *,
    system_prompt: Optional[str] = None,
    model: Optional[str] = None,
    use_simpler_model: Optional[bool] = None,
    timeout: Optional[int] = None,
    max_tokens: Optional[int] = None,
    temperature: Optional[float] = None,
    top_p: Optional[float] = None,
) -> dict[str, Any]:
    """
    Make an LLM request and parse the response as JSON.

    Automatically extracts JSON from markdown code blocks if present.

    Args:
        prompt: The user prompt/question
        system_prompt: Optional system prompt for instructions
        model: Override the default model
        use_simpler_model: Use the simpler model when available
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
        use_simpler_model=use_simpler_model,
        timeout=timeout,
        max_tokens=max_tokens,
        temperature=temperature,
        top_p=top_p,
    )

    # Extract JSON from potential markdown code blocks
    if "```json" in content:
        content = content.split("```json")[1].split("```")[0].strip()
    elif "```" in content:
        content = content.split("```")[1].split("```")[0].strip()

    return json.loads(content)


def call_llm_with_tools(
    prompt: str,
    *,
    tools: list[dict[str, Any]],
    tool_handlers: dict[str, Any],
    system_prompt: Optional[str] = None,
    model: Optional[str] = None,
    use_simpler_model: Optional[bool] = None,
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
            use_simpler_model=use_simpler_model,
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
            print(f"[llm_helpers] Tool call: {name} args={raw_args}")
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

            print(f"[llm_helpers] Tool result: {name} -> {result}")
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
    use_simpler_model: Optional[bool] = None,
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
        use_simpler_model=use_simpler_model,
        timeout=timeout,
        max_tokens=max_tokens,
        temperature=temperature,
        top_p=top_p,
        tool_choice=tool_choice,
        max_steps=max_steps,
        max_tool_calls=max_tool_calls,
    )

    content = result.get("content", "")
    if "```json" in content:
        content = content.split("```json")[1].split("```")[0].strip()
    elif "```" in content:
        content = content.split("```")[1].split("```")[0].strip()

    return json.loads(content)


def call_llm_with_context(
    prompt: str,
    *,
    user_email: Optional[str] = None,
    time_context: Optional[str] = None,
    additional_context: Optional[dict[str, str]] = None,
    system_prompt: Optional[str] = None,
    model: Optional[str] = None,
    use_simpler_model: Optional[bool] = None,
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
        use_simpler_model: Use the simpler model when available
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
        use_simpler_model=use_simpler_model,
        timeout=timeout,
    )
