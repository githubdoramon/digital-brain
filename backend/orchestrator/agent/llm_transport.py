"""LLM transport helpers for agent chat + streaming tool-calling."""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from typing import Any

from llm_helpers import call_llm_chat, stream_llm_chat
from observability.logger import get_runtime_logger

logger = get_runtime_logger(__name__)


def call_llm_with_tools(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    *,
    model: str | None,
    timeout: int,
) -> dict[str, Any]:
    """Make a synchronous chat-completions call with tool definitions."""
    data = call_llm_chat(
        messages,
        tools=tools,
        tool_choice="auto",
        model=model,
        timeout=timeout,
    )

    if data.get("choices"):
        return {"message": data["choices"][0].get("message", {})}

    raise ValueError(
        "Unexpected LLM API response format: missing 'choices' field. "
        f"Response: {data}"
    )


async def stream_llm_with_tools(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    *,
    model: str | None,
    timeout: int,
) -> AsyncGenerator[dict[str, Any], None]:
    """Stream chat-completions chunks and normalize tool-call deltas."""
    accumulated_tool_calls: dict[int, dict[str, Any]] = {}
    accumulated_reasoning = ""
    accumulated_content = ""

    def _coerce_text(value: Any) -> str:
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            parts: list[str] = []
            for item in value:
                if isinstance(item, str):
                    parts.append(item)
                    continue
                if isinstance(item, dict):
                    text = item.get("text") or item.get("content")
                    if isinstance(text, str):
                        parts.append(text)
            return "".join(parts)
        return ""

    async for line in stream_llm_chat(
        messages,
        tools=tools,
        tool_choice="auto",
        model=model,
        timeout=timeout,
    ):
        line = line.strip()
        if not line or line == "data: [DONE]":
            continue
        if line.startswith("data: "):
            line = line[6:]

        try:
            chunk = json.loads(line)

            if "error" in chunk:
                error_msg = chunk.get("error", {})
                if isinstance(error_msg, dict):
                    error_msg = error_msg.get("message", str(error_msg))
                raise RuntimeError(f"LLM API streaming error: {error_msg}")

            delta = chunk.get("choices", [{}])[0].get("delta", {})
            finish_reason = chunk.get("choices", [{}])[0].get("finish_reason")
            reasoning_delta = _coerce_text(
                delta.get("reasoning") or delta.get("reasoning_content")
            )
            if reasoning_delta:
                accumulated_reasoning += reasoning_delta

            if "tool_calls" in delta:
                for tc in delta["tool_calls"]:
                    idx = tc.get("index", 0)
                    if idx not in accumulated_tool_calls:
                        accumulated_tool_calls[idx] = {
                            "id": tc.get("id", ""),
                            "type": "function",
                            "function": {"name": "", "arguments": ""},
                        }
                    if tc.get("id"):
                        accumulated_tool_calls[idx]["id"] = tc["id"]
                    if "function" in tc:
                        if tc["function"].get("name"):
                            accumulated_tool_calls[idx]["function"]["name"] = tc["function"][
                                "name"
                            ]
                        if tc["function"].get("arguments"):
                            accumulated_tool_calls[idx]["function"]["arguments"] += tc[
                                "function"
                            ]["arguments"]

            normalized_message: dict[str, Any] = {"content": delta.get("content", "")}
            accumulated_content += str(delta.get("content", "") or "")
            if accumulated_reasoning:
                normalized_message["reasoning"] = accumulated_reasoning

            normalized: dict[str, Any] = {}
            normalized["message"] = normalized_message

            if finish_reason in ("tool_calls", "stop") and accumulated_tool_calls:
                normalized_message["tool_calls"] = list(accumulated_tool_calls.values())
                normalized["done"] = True
            elif finish_reason == "stop":
                normalized["done"] = True

            if normalized.get("done"):
                content_preview = accumulated_content.strip().replace("\n", " ")[:400]
                reasoning_preview = accumulated_reasoning.strip().replace("\n", " ")[:400]
                logger.info(
                    "[llm_helpers] LLM response (stream final): content=%s reasoning=%s",
                    content_preview or "",
                    reasoning_preview or "",
                )

            yield normalized
        except json.JSONDecodeError:
            continue
