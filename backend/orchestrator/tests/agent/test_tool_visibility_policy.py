"""Tests for shared tool visibility governance policy."""

from agent.enums import ConfidenceTier, ToolVisibilityMode
from agent.limits import LimitType, LimitViolation
from agent.router import IntentClassification, IntentType
from agent.state import AgentState, ToolCallRecord
from agent.tool_visibility_policy import (
    confidence_tier,
    resolve_tool_visibility,
    should_escalate_tool_visibility,
)
from tools.registry import get_registry


def test_confidence_tier_mapping():
    assert confidence_tier(0.9, high_threshold=0.8, medium_threshold=0.6) is ConfidenceTier.HIGH
    assert confidence_tier(0.7, high_threshold=0.8, medium_threshold=0.6) is ConfidenceTier.MEDIUM
    assert confidence_tier(0.3, high_threshold=0.8, medium_threshold=0.6) is ConfidenceTier.LOW


def test_resolve_tool_visibility_medium_adds_resolution():
    registry = get_registry()
    classification = IntentClassification(
        intent=IntentType.MEMORY_SEARCH,
        confidence=0.7,
        allowed_tool_groups=["memory"],
        constraints=[],
        skill_hints=[],
    )
    tools, mode, groups = resolve_tool_visibility(
        tool_registry=registry,
        classification=classification,
        restriction_mode="conservative",
        high_threshold=0.8,
        medium_threshold=0.6,
    )
    tool_names = {t.get("function", {}).get("name") for t in tools}
    assert mode is ToolVisibilityMode.RESTRICTED_WITH_RESOLUTION
    assert "memory" in groups
    assert "resolution" in groups
    assert "search_memories" in tool_names
    assert "resolve_contacts" in tool_names


def test_should_escalate_on_no_progress_violation():
    state = AgentState(goal="test", tool_visibility_mode="restricted")
    violation = LimitViolation(
        limit_type=LimitType.NO_PROGRESS_REPEATED,
        message="repeated no-progress",
    )
    assert should_escalate_tool_visibility(state=state, violation=violation) is True


def test_should_escalate_on_recent_empty_results():
    state = AgentState(goal="test", tool_visibility_mode="restricted")
    state.record_tool_call(
        ToolCallRecord(
            tool_name="search_memories",
            arguments={"query": "a"},
            result={"results": []},
            duration_ms=1,
            success=True,
        )
    )
    state.record_tool_call(
        ToolCallRecord(
            tool_name="search_memories",
            arguments={"query": "b"},
            result={"results": []},
            duration_ms=1,
            success=True,
        )
    )
    assert should_escalate_tool_visibility(state=state, violation=None) is True
