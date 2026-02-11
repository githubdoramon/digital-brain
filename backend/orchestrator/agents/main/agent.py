"""Main conversational agent assembly."""

from __future__ import annotations

from typing import Any

from agent.agent_interfaces import ConversationalAgentInterface
from agent.enums import ToolStatus
from agents.main.message_builder import build_main_messages, inject_main_skills
from agents.main.profile import build_main_agent_profile
from agents.main.runtime_policy import (
    build_force_completion_prompt,
    classify_malformed_output,
    get_follow_up_prompt_from_state,
    normalize_tool_status,
)


def _build_main_messages_adapter(
    question: str,
    state: Any,
    conversation_history: list[dict[str, str]] | None,
    user_email: str | None,
    search_limit: int,
    client_context: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    return build_main_messages(
        question=question,
        state=state,
        conversation_history=conversation_history,
        user_email=user_email,
        search_limit=search_limit,
        client_context=client_context,
        skill_injector=lambda messages, q, history, st: inject_main_skills(
            messages=messages,
            question=q,
            conversation_history=history,
            state=st,
        ),
    )


def _normalize_tool_status_adapter(result: dict[str, Any], default_source: str) -> ToolStatus:
    return normalize_tool_status(result, default_source=default_source)


def build_main_conversational_agent(
    *,
    max_steps: int,
    max_tool_calls: int,
    timeout_seconds: int,
) -> ConversationalAgentInterface:
    """Build main conversational agent interface using generic contract."""
    profile = build_main_agent_profile(
        max_steps=max_steps,
        max_tool_calls=max_tool_calls,
        timeout_seconds=timeout_seconds,
    )
    return ConversationalAgentInterface(
        profile=profile,
        build_messages=_build_main_messages_adapter,
        get_follow_up_prompt_from_state=get_follow_up_prompt_from_state,
        classify_malformed_output=classify_malformed_output,
        build_force_completion_prompt=build_force_completion_prompt,
        normalize_tool_status=_normalize_tool_status_adapter,
    )
