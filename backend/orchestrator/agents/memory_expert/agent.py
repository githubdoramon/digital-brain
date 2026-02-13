"""Memory expert conversational agent assembly."""

from __future__ import annotations

from typing import Any

from agent.agent_interfaces import ConversationalAgentInterface
from agent.enums import ToolStatus
from agents.main.runtime_policy import (
    build_force_completion_prompt,
    classify_malformed_output,
    get_follow_up_prompt_from_state,
    normalize_tool_status,
)

from .message_builder import build_memory_expert_messages
from .profile import build_memory_expert_agent_profile, supports_intent


def _build_memory_expert_messages_adapter(
    question: str,
    state: Any,
    conversation_history: list[dict[str, str]] | None,
    user_email: str | None,
    search_limit: int,
    client_context: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    return build_memory_expert_messages(
        question=question,
        state=state,
        conversation_history=conversation_history,
        user_email=user_email,
        search_limit=search_limit,
        client_context=client_context,
    )


def _normalize_tool_status_adapter(result: dict[str, Any], default_source: str) -> ToolStatus:
    return normalize_tool_status(result, default_source=default_source)


def build_memory_expert_conversational_agent(
    *,
    max_steps: int,
    max_tool_calls: int,
    timeout_seconds: int,
) -> ConversationalAgentInterface:
    """Build memory expert conversational agent interface using generic contract."""
    profile = build_memory_expert_agent_profile(
        max_steps=max_steps,
        max_tool_calls=max_tool_calls,
        timeout_seconds=timeout_seconds,
    )
    return ConversationalAgentInterface(
        name="memory_expert",
        is_fallback=False,
        profile=profile,
        build_messages=_build_memory_expert_messages_adapter,
        get_follow_up_prompt_from_state=get_follow_up_prompt_from_state,
        classify_malformed_output=classify_malformed_output,
        build_force_completion_prompt=build_force_completion_prompt,
        normalize_tool_status=_normalize_tool_status_adapter,
        supports_intent=supports_intent,
    )
