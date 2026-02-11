"""Main-agent runtime policy helpers for loop decisions and follow-ups."""

from __future__ import annotations

from typing import Any

from agent.enums import FollowUpSource, ToolStatus
from agent.response_guardrails import (
    CODE_DESCRIBING_TOOL_PROMPT,
    MALFORMED_TOOL_CALL_PROMPT,
    looks_like_code_describing_tool,
    looks_like_malformed_tool_call,
)
from ui_dsl.clarification import extract_need_user_input


def get_follow_up_prompt_from_state(state: Any) -> tuple[str | None, FollowUpSource | None]:
    """Return the next user-facing follow-up prompt from state, if any."""
    from agent.contact_resolution import get_user_clarification_prompt_for_contact_resolution

    clarification_prompt = get_user_clarification_prompt_for_contact_resolution(state)
    if clarification_prompt:
        return clarification_prompt, FollowUpSource.CONTACT_CLARIFICATION

    if state.ui_directives and state.pending_questions:
        return state.pending_questions[-1], FollowUpSource.UI_FOLLOW_UP

    return None, None


def classify_malformed_output(content: str) -> tuple[str | None, str | None]:
    """Classify malformed output and return repair prompt and reason."""
    if looks_like_malformed_tool_call(content):
        return MALFORMED_TOOL_CALL_PROMPT, "JSON tool call in text output"
    if looks_like_code_describing_tool(content):
        return CODE_DESCRIBING_TOOL_PROMPT, "Code describing tool instead of calling it"
    return None, None


def build_force_completion_prompt(goal_check: dict[str, Any]) -> str:
    """Build deterministic continuation prompt when goal is incomplete."""
    return (
        "INCOMPLETE: You have not completed the user's request yet. "
        f"Status: {goal_check['reason']}. "
        f"Required: {goal_check['pending_actions'][0]}. "
        "Do NOT respond to the user - FIRST invoke the appropriate tool to complete the action."
    )


def normalize_tool_status(result: dict[str, Any], *, default_source: str) -> ToolStatus:
    """Infer tool status from result and need_user_input envelope."""
    status = result.get("status")
    if not status and extract_need_user_input(result, default_source=default_source):
        status = ToolStatus.NEED_USER_INPUT.value
    return ToolStatus.from_value(status)
