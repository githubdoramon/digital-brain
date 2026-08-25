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
    reasoning_effort: str | None = None,
) -> dict[str, Any]:
    """Make a synchronous chat-completions call with tool definitions."""
    data = call_llm_chat(
        messages,
        tools=tools,
        tool_choice="auto",
        model=model,
        timeout=timeout,
        reasoning_effort=reasoning_effort,
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
    reasoning_effort: str | None = None,
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
        reasoning_effort=reasoning_effort,
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

            choices = chunk.get("choices") or []
            if not choices or not isinstance(choices[0], dict):
                logger.warning(
                    "[llm_transport] Ignoring stream chunk without a valid choice: %s",
                    json.dumps(chunk, ensure_ascii=False, default=str)[:2000],
                )
                continue

            choice = choices[0]
            delta = choice.get("delta") or {}
            if not isinstance(delta, dict):
                logger.warning(
                    "[llm_transport] Ignoring stream chunk with invalid delta type=%s",
                    type(delta).__name__,
                )
                continue
            finish_reason = choice.get("finish_reason")
            reasoning_delta = _coerce_text(
                delta.get("reasoning") or delta.get("reasoning_content")
            )
            if reasoning_delta:
                accumulated_reasoning += reasoning_delta

            tool_call_deltas = delta.get("tool_calls") or []
            if not isinstance(tool_call_deltas, list):
                logger.warning(
                    "[llm_transport] Ignoring invalid tool_calls delta type=%s payload=%s",
                    type(tool_call_deltas).__name__,
                    json.dumps(tool_call_deltas, ensure_ascii=False, default=str)[:1000],
                )
                tool_call_deltas = []

            for tc in tool_call_deltas:
                if not isinstance(tc, dict):
                    logger.warning(
                        "[llm_transport] Ignoring invalid tool-call delta type=%s",
                        type(tc).__name__,
                    )
                    continue
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
        except Exception:
            logger.exception(
                "[llm_transport] Failed to normalize streamed LLM chunk: %s",
                line[:2000],
            )
            raise
