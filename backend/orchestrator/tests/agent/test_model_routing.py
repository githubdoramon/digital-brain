"""Tests for adaptive model routing policy."""

import os

os.environ.setdefault("LLM_BASE_URL", "http://localhost:11434")
os.environ.setdefault("LLM_CHAT_MODEL_FAST", "test-fast-model")
os.environ.setdefault("LLM_CHAT_MODEL_SMART", "test-smart-model")

from agent.model_routing import select_llm_call_policy
from agent.state import AgentState


def test_select_llm_call_policy_prefers_smart_for_complex_questions(monkeypatch):
    monkeypatch.setenv("LLM_CHAT_MODEL_FAST", "fast-model")
    monkeypatch.setenv("LLM_CHAT_MODEL_SMART", "smart-model")
    monkeypatch.setenv("AGENT_MODEL_ROUTING_COMPLEXITY_THRESHOLD", "3")

    state = AgentState(goal="compare architectures")
    state.route_confidence_tier = "low"
    state.step_count = 5

    policy = select_llm_call_policy(
        question="Compare and analyze tradeoffs between two agent architectures and provide strategy",
        state=state,
        tools_count=9,
        default_model="default-model",
        default_timeout=60,
    )

    assert policy.profile == "smart"
    assert policy.model == "smart-model"
    assert policy.timeout >= 60
    assert policy.reasoning_effort == "xhigh"


def test_select_llm_call_policy_starts_smart_at_low_effort(monkeypatch):
    monkeypatch.setenv("LLM_CHAT_MODEL_FAST", "fast-model")
    monkeypatch.setenv("LLM_CHAT_MODEL_SMART", "smart-model")
    monkeypatch.setenv("AGENT_MODEL_ROUTING_COMPLEXITY_THRESHOLD", "4")

    state = AgentState(goal="hello")

    policy = select_llm_call_policy(
        question="What time is it?",
        state=state,
        tools_count=1,
        default_model="default-model",
        default_timeout=60,
    )

    assert policy.profile == "smart"
    assert policy.model == "smart-model"
    assert policy.reasoning_effort == "low"
    assert policy.timeout == 60
