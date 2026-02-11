"""Tests for episodic memory and planning lifecycle fields in AgentState."""

import os

os.environ.setdefault("LLM_BASE_URL", "http://localhost:11434")
os.environ.setdefault("LLM_CHAT_MODEL", "test-model")

from agent.state import AgentState


def test_state_episodic_memory_generates_hints():
    state = AgentState(goal="test")
    state.remember_episode(
        summary="Retrieved document about vitamin b12 blood work",
        source_tool="get_document",
        salience=0.9,
    )
    hints = state.get_episodic_hints()
    assert "vitamin" in hints or "blood" in hints


def test_state_execution_plan_progress_in_context():
    state = AgentState(goal="test")
    state.set_execution_plan(["Clarify scope", "Collect evidence"])
    state.complete_plan_step("Clarify scope")
    context = state.to_context_string()
    assert "EXECUTION_PLAN_PROGRESS: 1/2" in context
