"""
Integration tests for the full agent flow.

Note: These tests verify integration between components.
Full end-to-end tests with LLM calls require additional infrastructure.
"""

import pytest

from agent.controller import AgentController
from agent.limits import AgentConfig, LimitChecker, LimitType
from agent.router import TOOL_GROUPS, IntentRouter, IntentType
from agent.state import AgentState, ToolCallRecord
from tools.registry import get_registry


class TestAgentControllerIntegration:
    """Integration tests for AgentController with other components."""

    @pytest.fixture
    def controller(self, agent_config):
        return AgentController(config=agent_config)

    def test_controller_with_limit_checker(self, controller):
        """Test controller uses limit checker correctly."""
        state = AgentState(goal="Test", step_count=controller.config.max_steps)

        checker = LimitChecker(controller.config)
        violation = checker.check(state)

        assert violation is not None
        assert violation.limit_type == LimitType.MAX_STEPS

    def test_controller_config_integration(self, controller):
        """Test controller config is applied correctly."""
        assert controller.config.max_steps == 5
        assert controller.config.max_tool_calls == 10
        assert controller.config.max_repairs == 2


class TestIntentRouterIntegration:
    """Integration tests for intent router with tool registry."""

    @pytest.fixture
    def router(self):
        return IntentRouter(enable_llm_routing=False)

    @pytest.fixture
    def registry(self):
        return get_registry()

    def test_router_tool_groups_match_registry(self, router, registry):
        """Test router tool groups align with registry."""
        for group_name in TOOL_GROUPS.keys():
            # Each tool group should be represented in registry
            tools = registry.get_tools_for_groups([group_name])
            tool_names = [t.name for t in tools]
            expected = TOOL_GROUPS[group_name]

            for tool_name in expected:
                assert tool_name in tool_names, f"{tool_name} missing from registry"

    def test_home_control_classification(self, router, registry):
        """Test home control intent provides correct tools."""
        result = router._rule_based_classify("Turn on the living room lights")

        assert result.intent == IntentType.HOME_CONTROL
        assert "home" in result.allowed_tool_groups

        # Get actual tools for this intent
        tools = registry.get_tools_for_groups(result.allowed_tool_groups)
        tool_names = [t.name for t in tools]

        assert "home_assistant" in tool_names

    def test_memory_search_classification(self, router, registry):
        """Test memory search intent provides correct tools."""
        result = router._rule_based_classify("Find my meetings from last week")

        assert result.intent == IntentType.MEMORY_SEARCH
        assert "memory" in result.allowed_tool_groups

        tools = registry.get_tools_for_groups(result.allowed_tool_groups)
        tool_names = [t.name for t in tools]

        assert "search_memories" in tool_names


class TestLimitEnforcementIntegration:
    """Integration tests for limit enforcement across components."""

    @pytest.fixture
    def controller(self):
        """Create controller with low limits for testing."""
        config = AgentConfig(
            max_steps=3,
            max_tool_calls=5,
            max_repairs=1,
        )
        return AgentController(config=config)

    def test_stops_at_max_steps(self, controller):
        """Test limit checker stops at max steps."""
        state = AgentState(goal="Test", step_count=3)

        checker = LimitChecker(controller.config)
        should_stop, violation = checker.should_stop(state)

        assert should_stop is True
        assert violation is not None
        assert violation.limit_type == LimitType.MAX_STEPS

    def test_stops_at_max_tool_calls(self, controller):
        """Test limit checker stops at max tool calls."""
        state = AgentState(goal="Test")

        # Add 5 tool calls
        for i in range(5):
            record = ToolCallRecord(
                tool_name="test",
                arguments={"i": i},
                result={},
                duration_ms=100,
                success=True,
            )
            state.record_tool_call(record)

        checker = LimitChecker(controller.config)
        should_stop, violation = checker.should_stop(state)

        assert should_stop is True
        assert violation is not None
        assert violation.limit_type == LimitType.MAX_TOOL_CALLS

    def test_stops_at_max_repairs(self, controller):
        """Test limit checker stops at max repairs."""
        state = AgentState(goal="Test", repair_count=1)

        checker = LimitChecker(controller.config)
        violation = checker.check(state)

        assert violation is not None
        assert violation.limit_type == LimitType.MAX_REPAIRS


class TestStateToolRecordIntegration:
    """Integration tests for state and tool record interaction."""

    def test_state_accumulates_tool_calls(self):
        """Test state properly accumulates multiple tool calls."""
        state = AgentState(goal="Find meetings")

        records = [
            ToolCallRecord(
                tool_name="search_memories",
                arguments={"query": "meetings"},
                result={"results": [{"id": "1"}]},
                duration_ms=100,
                success=True,
            ),
            ToolCallRecord(
                tool_name="get_events",
                arguments={"ids": ["1"]},
                result={"events": [{"title": "Meeting"}]},
                duration_ms=50,
                success=True,
            ),
        ]

        for record in records:
            state.record_tool_call(record)

        assert state.tool_calls_count == 2
        assert state.successful_tool_calls == 2

    def test_state_tracks_failed_calls(self):
        """Test state tracks failed tool calls correctly."""
        state = AgentState(goal="Test")

        success = ToolCallRecord(
            tool_name="search_memories",
            arguments={},
            result={"results": []},
            duration_ms=100,
            success=True,
        )
        failure = ToolCallRecord(
            tool_name="execute_sql",
            arguments={"query": "SELECT *"},
            result=None,
            duration_ms=50,
            success=False,
            error="Connection failed",
        )

        state.record_tool_call(success)
        state.record_tool_call(failure)

        assert state.tool_calls_count == 2
        assert state.successful_tool_calls == 1


class TestProgressDetectionIntegration:
    """Integration tests for no-progress detection."""

    @pytest.fixture
    def checker(self, agent_config):
        return LimitChecker(agent_config)

    def test_detects_repeated_empty_results(self, checker):
        """Test detection of repeated empty results pattern."""
        state = AgentState(goal="Test")

        # Add 3 calls with empty results
        for i in range(3):
            record = ToolCallRecord(
                tool_name="search_memories",
                arguments={"query": f"query{i}"},
                result={"results": []},
                duration_ms=100,
                success=True,
            )
            state.record_tool_call(record)

        violation = checker.detect_no_progress(state)

        assert violation is not None
        assert violation.limit_type == LimitType.NO_PROGRESS_EMPTY

    def test_allows_progress_with_results(self, checker):
        """Test no violation when getting results."""
        state = AgentState(goal="Test")

        # Add calls with actual results
        records = [
            ToolCallRecord(
                tool_name="search_memories",
                arguments={"query": "meetings"},
                result={"results": [{"id": "1"}]},
                duration_ms=100,
                success=True,
            ),
            ToolCallRecord(
                tool_name="get_events",
                arguments={"ids": ["1"]},
                result={"events": [{"title": "Team Meeting"}]},
                duration_ms=50,
                success=True,
            ),
        ]

        for record in records:
            state.record_tool_call(record)

        violation = checker.detect_no_progress(state)

        assert violation is None


class TestBackwardCompatibility:
    """Tests for backward compatibility with existing code."""

    def test_legacy_state_fields(self):
        """Test legacy state fields are preserved."""
        state = AgentState(goal="Test")

        # Legacy fields should exist and work
        state.resolution["entity_type"] = "person"
        state.search_results.append({"id": "1"})
        state.detailed_events.append({"event": "test"})
        state.activated_skills.append({"skill": "test"})

        # All should be accessible
        assert state.resolution["entity_type"] == "person"
        assert len(state.search_results) == 1
        assert len(state.detailed_events) == 1
        assert len(state.activated_skills) == 1

    def test_state_serialization(self):
        """Test state serializes correctly for API responses."""
        state = AgentState(goal="Find meetings", step_count=2)
        state.add_fact("Found 2 meetings")

        record = ToolCallRecord(
            tool_name="search_memories",
            arguments={"query": "meetings"},
            result={"results": [{"id": "1"}, {"id": "2"}]},
            duration_ms=150,
            success=True,
        )
        state.record_tool_call(record)

        data = state.to_dict()

        assert data["goal"] == "Find meetings"
        assert data["step_count"] == 2
        assert len(data["known_facts"]) == 1
        assert len(data["tool_calls"]) == 1

    def test_context_string_generation(self):
        """Test state generates valid context for LLM."""
        state = AgentState(goal="Find all meetings", step_count=1)
        state.add_fact("User wants recent meetings")

        context = state.to_context_string()

        assert "GOAL:" in context
        assert "Find all meetings" in context
        assert "STEP:" in context
        assert "User wants recent meetings" in context
