"""Tests for execution planning and verification policy."""

import os

os.environ.setdefault("LLM_BASE_URL", "http://localhost:11434")
os.environ.setdefault("LLM_CHAT_MODEL", "test-model")

from agent.planning_policy import (
    build_execution_plan,
    build_verification_retry_prompt,
    verify_final_response,
)


def test_build_execution_plan_for_memory_intent():
    plan = build_execution_plan("Find project notes", "MEMORY_SEARCH")
    assert len(plan) >= 3
    assert any("collect evidence" in step.lower() for step in plan)


def test_verify_final_response_requires_evidence_when_tools_used():
    ok, reason, missing = verify_final_response(
        final_content="Here is your answer with no evidence.",
        goal_check={"achieved": True, "reason": "done", "pending_actions": []},
        completion_evidence=[],
        tool_calls_count=2,
    )
    assert ok is False
    assert "evidence" in reason.lower()
    assert missing


def test_build_verification_retry_prompt_contains_reason_and_action():
    prompt = build_verification_retry_prompt("Need evidence", ["Run memory search"])
    assert "Need evidence" in prompt
    assert "Run memory search" in prompt
