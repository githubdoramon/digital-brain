"""Generic agent interfaces and loaders for profile-based runtime wiring."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from ui_dsl.clarification import extract_need_user_input

from .enums import FollowUpSource, ToolStatus
from .response_guardrails import (
    CODE_DESCRIBING_TOOL_PROMPT,
    MALFORMED_TOOL_CALL_PROMPT,
    looks_like_code_describing_tool,
    looks_like_malformed_tool_call,
)
from .runtime_profiles import BoundedAgentProfile, build_agent_profile


@dataclass(frozen=True)
class ConversationalAgentInterface:
    """Interface for conversational bounded agents executed by AgentController."""

    name: str
    is_fallback: bool
    profile: BoundedAgentProfile
    build_messages: Callable[
        [str, Any, list[dict[str, str]] | None, str | None, int, dict[str, Any] | None],
        list[dict[str, Any]],
    ]
    get_follow_up_prompt_from_state: Callable[[Any], tuple[str | None, FollowUpSource | None]]
    classify_malformed_output: Callable[[str], tuple[str | None, str | None]]
    build_force_completion_prompt: Callable[[dict[str, Any]], str]
    normalize_tool_status: Callable[[dict[str, Any], str], ToolStatus]
    supports_intent: Callable[[Any], bool]


def build_default_conversational_interface(
    *,
    max_steps: int,
    max_tool_calls: int,
    timeout_seconds: int,
) -> ConversationalAgentInterface:
    """Generic fallback interface used when no agent-specific implementation is injected."""

    def _build_messages(
        question: str,
        _state: Any,
        conversation_history: list[dict[str, str]] | None,
        _user_email: str | None,
        _search_limit: int,
        _client_context: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        if conversation_history:
            messages.extend(conversation_history)
        messages.append({"role": "user", "content": question})
        return messages

    def _get_follow_up(state: Any) -> tuple[str | None, FollowUpSource | None]:
        if state.pending_questions:
            return str(state.pending_questions[-1]), FollowUpSource.UI_FOLLOW_UP
        return None, None

    def _classify_malformed(content: str) -> tuple[str | None, str | None]:
        if looks_like_malformed_tool_call(content):
            return MALFORMED_TOOL_CALL_PROMPT, "JSON tool call in text output"
        if looks_like_code_describing_tool(content):
            return CODE_DESCRIBING_TOOL_PROMPT, "Code describing tool instead of calling it"
        return None, None

    def _force_completion(goal_check: dict[str, Any]) -> str:
        return (
            "INCOMPLETE: You have not completed the user's request yet. "
            f"Status: {goal_check['reason']}. "
            f"Required: {goal_check['pending_actions'][0]}. "
            "Do NOT respond to the user - FIRST invoke the appropriate tool to complete the action."
        )

    def _normalize_tool_status(result: dict[str, Any], default_source: str) -> ToolStatus:
        status = result.get("status")
        if not status and extract_need_user_input(result, default_source=default_source):
            status = ToolStatus.NEED_USER_INPUT.value
        return ToolStatus.from_value(status)

    def _supports_intent(_intent: Any) -> bool:
        return False

    profile = build_agent_profile(
        name="generic",
        max_steps=max_steps,
        max_tool_calls=max_tool_calls,
        timeout_seconds=timeout_seconds,
    )
    return ConversationalAgentInterface(
        name="generic",
        is_fallback=True,
        profile=profile,
        build_messages=_build_messages,
        get_follow_up_prompt_from_state=_get_follow_up,
        classify_malformed_output=_classify_malformed,
        build_force_completion_prompt=_force_completion,
        normalize_tool_status=_normalize_tool_status,
        supports_intent=_supports_intent,
    )
