"""Tests for main-agent runtime policy helpers."""

from agent.enums import ToolStatus
from agents.main.runtime_policy import (
    build_force_completion_prompt,
    classify_malformed_output,
    normalize_tool_status,
)


def test_classify_malformed_output_returns_repair_prompt_for_json_tool_call():
    prompt, reason = classify_malformed_output(
        '{"tool_call": {"name": "search_memories", "arguments": {"query": "x"}}}'
    )
    assert prompt is not None
    assert reason is not None


def test_build_force_completion_prompt_mentions_required_action():
    goal_check = {
        "reason": "missing lookup",
        "pending_actions": ["Call search_memories"],
    }
    text = build_force_completion_prompt(goal_check)
    assert "INCOMPLETE" in text
    assert "Call search_memories" in text


def test_normalize_tool_status_uses_need_user_input_fallback():
    status = normalize_tool_status(
        {
            "need_user_input": {
                "kind": "clarification",
                "prompt": "Which one?",
                "questions": ["Which one?"],
                "fields": [],
            }
        },
        default_source="resolve_contacts",
    )
    assert status is ToolStatus.NEED_USER_INPUT
