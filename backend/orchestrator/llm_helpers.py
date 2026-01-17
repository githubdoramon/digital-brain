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


def call_llm(
    prompt: str,
    *,
    system_prompt: Optional[str] = None,
    model: Optional[str] = None,
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
    messages = []

    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})

    messages.append({"role": "user", "content": prompt})

    payload: dict[str, Any] = {
        "model": model or LLM_CHAT_MODEL,
        "messages": messages,
        "stream": False,
    }

    if max_tokens:
        payload["max_tokens"] = max_tokens

    if temperature is not None:
        payload["temperature"] = temperature

    if top_p is not None:
        payload["top_p"] = top_p

    response = requests.post(
        f"{LLM_BASE_URL}/chat/completions",
        headers=get_llm_headers(),
        json=payload,
        timeout=timeout or LLM_TIMEOUT,
    )
    response.raise_for_status()

    data = response.json()
    content = data.get("choices", [{}])[0].get("message", {}).get("content", "")

    if not content:
        raise RuntimeError(f"LLM returned empty content: {data}")

    import json
    print(f"[llm_helpers] LLM input: {json.dumps(messages, ensure_ascii=False)}")
    print(f"[llm_helpers] LLM response: {content}")

    return content


def call_llm_json(
    prompt: str,
    *,
    system_prompt: Optional[str] = None,
    model: Optional[str] = None,
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


def call_llm_with_context(
    prompt: str,
    *,
    user_email: Optional[str] = None,
    time_context: Optional[str] = None,
    additional_context: Optional[dict[str, str]] = None,
    system_prompt: Optional[str] = None,
    model: Optional[str] = None,
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
        timeout=timeout,
    )
