"""
Tests for AgentController.

Note: These tests verify the controller interface and configuration.
Full integration tests would require mocking the LLM backend.
"""

import pytest

from agent.controller import AgentController, get_controller
from agent.limits import AgentConfig, LimitType
from agent.state import AgentState, ToolCallRecord


class TestAgentControllerInitialization:
    """Tests for AgentController initialization."""

    def test_default_initialization(self):
        """Test controller with default config."""
        controller = AgentController()

        assert controller.config is not None
        assert controller.config.max_steps > 0

    def test_custom_config(self):
        """Test controller with custom config."""
        config = AgentConfig(max_steps=10, max_tool_calls=15)
        controller = AgentController(config=config)

        assert controller.config.max_steps == 10
        assert controller.config.max_tool_calls == 15

    def test_disable_intent_routing(self):
        """Test controller with intent routing disabled."""
        config = AgentConfig(enable_intent_routing=False)
        controller = AgentController(config=config)

        assert controller.config.enable_intent_routing is False

    def test_disable_validation(self):
        """Test controller with validation disabled."""
        config = AgentConfig(enable_validation=False)
        controller = AgentController(config=config)

        assert controller.config.enable_validation is False


class TestGetController:
    """Tests for get_controller singleton."""

    def test_returns_controller(self):
        """Test get_controller returns AgentController."""
        controller = get_controller()
        assert isinstance(controller, AgentController)

    def test_singleton_behavior(self):
        """Test get_controller returns same instance."""
        controller1 = get_controller()
        controller2 = get_controller()

        assert controller1 is controller2


class TestAgentState:
    """Tests for AgentState used by controller."""

    def test_create_state_with_goal(self):
        """Test creating state with a goal."""
        state = AgentState(goal="Find all meetings")

        assert state.goal == "Find all meetings"
        assert state.step_count == 0
        assert state.tool_calls == []

    def test_state_tracks_tool_calls(self):
        """Test state tracks tool calls."""
        state = AgentState(goal="Test")

        record = ToolCallRecord(
            tool_name="search_memories",
            arguments={"query": "meetings"},
            result={"results": []},
            duration_ms=100,
            success=True,
        )
        state.record_tool_call(record)

        assert state.tool_calls_count == 1

    def test_state_tracks_facts(self):
        """Test state accumulates facts."""
        state = AgentState(goal="Test")

        state.add_fact("Found 5 meetings")
        state.add_fact("User prefers morning meetings")

        assert len(state.known_facts) == 2

    def test_state_serializes_to_context(self):
        """Test state can be serialized for prompt injection."""
        state = AgentState(goal="Find meetings", step_count=3)
        state.add_fact("Found relevant data")

        context = state.to_context_string()

        assert "GOAL:" in context
        assert "Find meetings" in context
        assert "STEP:" in context


class TestLimitChecking:
    """Tests for limit checking in controller."""

    @pytest.fixture
    def controller(self, agent_config):
        return AgentController(config=agent_config)

    def test_max_steps_limit(self, controller):
        """Test max steps limit is enforced."""
        state = AgentState(goal="Test", step_count=controller.config.max_steps)

        from agent.limits import LimitChecker
        checker = LimitChecker(controller.config)
        violation = checker.check(state)

        assert violation is not None
        assert violation.limit_type == LimitType.MAX_STEPS

    def test_max_tool_calls_limit(self, controller):
        """Test max tool calls limit is enforced."""
        state = AgentState(goal="Test")

        # Add tool calls up to limit
        for i in range(controller.config.max_tool_calls):
            record = ToolCallRecord(
                tool_name="test",
                arguments={"i": i},
                result={},
                duration_ms=100,
                success=True,
            )
            state.record_tool_call(record)

        from agent.limits import LimitChecker
        checker = LimitChecker(controller.config)
        violation = checker.check(state)

        assert violation is not None
        assert violation.limit_type == LimitType.MAX_TOOL_CALLS

    def test_max_repairs_limit(self, controller):
        """Test max repairs limit is enforced."""
        state = AgentState(goal="Test", repair_count=controller.config.max_repairs)

        from agent.limits import LimitChecker
        checker = LimitChecker(controller.config)
        violation = checker.check(state)

        assert violation is not None
        assert violation.limit_type == LimitType.MAX_REPAIRS


class TestNoProgressDetection:
    """Tests for no-progress detection."""

    @pytest.fixture
    def controller(self, agent_config):
        return AgentController(config=agent_config)

    def test_repeated_calls_detected(self, controller):
        """Test repeated identical calls are detected."""
        state = AgentState(goal="Test")

        # Add 3 identical calls
        for _ in range(3):
            record = ToolCallRecord(
                tool_name="search_memories",
                arguments={"query": "same"},
                result={"results": []},
                duration_ms=100,
                success=True,
            )
            state.record_tool_call(record)

        from agent.limits import LimitChecker
        checker = LimitChecker(controller.config)
        violation = checker.detect_no_progress(state)

        assert violation is not None
        assert violation.limit_type == LimitType.NO_PROGRESS_REPEATED

    def test_varied_calls_ok(self, controller):
        """Test varied calls don't trigger no-progress."""
        state = AgentState(goal="Test")

        # Add different calls with results
        for i, result in enumerate([
            {"results": [{"id": "1"}]},
            {"results": [{"id": "2"}]},
            {"results": [{"id": "3"}]},
        ]):
            record = ToolCallRecord(
                tool_name="search_memories",
                arguments={"query": f"query{i}"},
                result=result,
                duration_ms=100,
                success=True,
            )
            state.record_tool_call(record)

        from agent.limits import LimitChecker
        checker = LimitChecker(controller.config)
        violation = checker.detect_no_progress(state)

        assert violation is None


class TestSkillsIntegration:
    """Tests for skills integration concepts."""

    def test_skill_hints_in_classification(self):
        """Test skill hints are extracted from classification."""
        from agent.router import IntentClassification, IntentType

        classification = IntentClassification(
            intent=IntentType.MEMORY_SEARCH,
            confidence=0.9,
            allowed_tool_groups=["memory"],
            skill_hints=["document-search", "event-analysis"],
        )

        hints = classification.skill_hints

        assert "document-search" in hints
        assert "event-analysis" in hints


class TestResponseBundle:
    """Tests for response bundle structure."""

    def test_state_to_dict(self):
        """Test state can be serialized to dict."""
        state = AgentState(goal="Test question")
        state.add_fact("Found data")

        record = ToolCallRecord(
            tool_name="test",
            arguments={},
            result={},
            duration_ms=100,
            success=True,
        )
        state.record_tool_call(record)

        data = state.to_dict()

        assert "goal" in data
        assert "known_facts" in data
        assert "tool_calls" in data
        assert len(data["tool_calls"]) == 1
