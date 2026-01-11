"""
Tests for LimitChecker and AgentConfig.
"""

import pytest
from agent.state import AgentState, ToolCallRecord
from agent.limits import LimitChecker, LimitViolation, AgentConfig, LimitType


class TestAgentConfig:
    """Tests for AgentConfig dataclass."""

    def test_default_values(self):
        """Test AgentConfig with default values."""
        config = AgentConfig()

        assert config.max_steps == 15
        assert config.max_tool_calls == 20
        assert config.max_repairs == 2
        assert config.enable_intent_routing is True
        assert config.enable_validation is True

    def test_custom_values(self):
        """Test AgentConfig with custom values."""
        config = AgentConfig(
            max_steps=10,
            max_tool_calls=15,
            max_repairs=3,
            enable_intent_routing=False,
            enable_validation=False,
        )

        assert config.max_steps == 10
        assert config.max_tool_calls == 15
        assert config.max_repairs == 3
        assert config.enable_intent_routing is False
        assert config.enable_validation is False

    def test_from_env(self, monkeypatch):
        """Test AgentConfig.from_env() reads environment variables."""
        monkeypatch.setenv("AGENT_MAX_STEPS", "25")
        monkeypatch.setenv("AGENT_MAX_TOOL_CALLS", "30")
        monkeypatch.setenv("AGENT_MAX_REPAIRS", "5")
        monkeypatch.setenv("AGENT_ENABLE_INTENT_ROUTING", "false")
        monkeypatch.setenv("AGENT_ENABLE_VALIDATION", "false")

        config = AgentConfig.from_env()

        assert config.max_steps == 25
        assert config.max_tool_calls == 30
        assert config.max_repairs == 5
        assert config.enable_intent_routing is False
        assert config.enable_validation is False

    def test_from_env_defaults(self, monkeypatch):
        """Test AgentConfig.from_env() uses defaults when env vars not set."""
        # Clear any existing env vars
        monkeypatch.delenv("AGENT_MAX_STEPS", raising=False)
        monkeypatch.delenv("AGENT_MAX_TOOL_CALLS", raising=False)
        monkeypatch.delenv("AGENT_MAX_REPAIRS", raising=False)

        config = AgentConfig.from_env()

        assert config.max_steps == 15
        assert config.max_tool_calls == 20
        assert config.max_repairs == 2


class TestLimitChecker:
    """Tests for LimitChecker."""

    def test_no_violation_initial_state(self, agent_config):
        """Test no violation for fresh state."""
        checker = LimitChecker(agent_config)
        state = AgentState(goal="Test")

        violation = checker.check(state)

        assert violation is None

    def test_max_steps_violation(self, agent_config):
        """Test max_steps limit violation."""
        checker = LimitChecker(agent_config)
        state = AgentState(goal="Test", step_count=5)  # config.max_steps = 5

        violation = checker.check(state)

        assert violation is not None
        assert violation.limit_type == LimitType.MAX_STEPS
        assert "5" in violation.message

    def test_max_tool_calls_violation(self, agent_config):
        """Test max_tool_calls limit violation."""
        checker = LimitChecker(agent_config)
        state = AgentState(goal="Test")

        # Add tool calls to reach limit
        for i in range(10):  # config.max_tool_calls = 10
            record = ToolCallRecord(
                tool_name="test",
                arguments={"i": i},
                result={},
                duration_ms=100,
                success=True,
            )
            state.record_tool_call(record)

        violation = checker.check(state)

        assert violation is not None
        assert violation.limit_type == LimitType.MAX_TOOL_CALLS

    def test_max_repairs_violation(self, agent_config):
        """Test max_repairs limit violation."""
        checker = LimitChecker(agent_config)
        state = AgentState(goal="Test", repair_count=2)  # config.max_repairs = 2

        violation = checker.check(state)

        assert violation is not None
        assert violation.limit_type == LimitType.MAX_REPAIRS

    def test_violation_has_suggestion(self, agent_config):
        """Test that violations include suggestions."""
        checker = LimitChecker(agent_config)
        state = AgentState(goal="Test", step_count=5)

        violation = checker.check(state)

        assert violation is not None
        assert violation.suggestion is not None
        assert len(violation.suggestion) > 0


class TestNoProgressDetection:
    """Tests for no-progress detection."""

    def test_no_progress_repeated_tool_calls(self, agent_config):
        """Test detection of repeated identical tool calls."""
        checker = LimitChecker(agent_config)
        state = AgentState(goal="Test")

        # Add 3 identical tool calls
        for _ in range(3):
            record = ToolCallRecord(
                tool_name="search_memories",
                arguments={"query": "same query"},
                result={"results": []},
                duration_ms=100,
                success=True,
            )
            state.record_tool_call(record)

        violation = checker.detect_no_progress(state)

        assert violation is not None
        assert violation.limit_type == LimitType.NO_PROGRESS_REPEATED

    def test_progress_with_different_calls(self, agent_config):
        """Test that different tool calls with results don't trigger no-progress."""
        checker = LimitChecker(agent_config)
        state = AgentState(goal="Test")

        # Add different tool calls with non-empty results
        record1 = ToolCallRecord(
            tool_name="search_memories",
            arguments={"query": "query1"},
            result={"results": [{"id": "1"}]},
            duration_ms=100,
            success=True,
        )
        record2 = ToolCallRecord(
            tool_name="search_memories",
            arguments={"query": "query2"},
            result={"results": [{"id": "2"}]},
            duration_ms=100,
            success=True,
        )
        record3 = ToolCallRecord(
            tool_name="execute_sql",
            arguments={"query": "SELECT *"},
            result={"rows": [{"name": "test"}]},
            duration_ms=100,
            success=True,
        )
        state.record_tool_call(record1)
        state.record_tool_call(record2)
        state.record_tool_call(record3)

        violation = checker.detect_no_progress(state)

        assert violation is None

    def test_no_progress_consecutive_empty_results(self, agent_config):
        """Test detection of consecutive empty results."""
        checker = LimitChecker(agent_config)
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

    def test_progress_with_non_empty_results(self, agent_config):
        """Test that non-empty results don't trigger no-progress."""
        checker = LimitChecker(agent_config)
        state = AgentState(goal="Test")

        # Add calls with results
        record1 = ToolCallRecord(
            tool_name="search_memories",
            arguments={"query": "query1"},
            result={"results": [{"id": "1"}]},
            duration_ms=100,
            success=True,
        )
        record2 = ToolCallRecord(
            tool_name="search_memories",
            arguments={"query": "query2"},
            result={"results": []},
            duration_ms=100,
            success=True,
        )
        record3 = ToolCallRecord(
            tool_name="search_memories",
            arguments={"query": "query3"},
            result={"results": [{"id": "2"}]},
            duration_ms=100,
            success=True,
        )
        state.record_tool_call(record1)
        state.record_tool_call(record2)
        state.record_tool_call(record3)

        violation = checker.detect_no_progress(state)

        assert violation is None

    def test_no_progress_with_few_calls(self, agent_config):
        """Test that few tool calls don't trigger no-progress."""
        checker = LimitChecker(agent_config)
        state = AgentState(goal="Test")

        # Add only 2 calls (less than threshold)
        for _ in range(2):
            record = ToolCallRecord(
                tool_name="search_memories",
                arguments={"query": "same"},
                result={"results": []},
                duration_ms=100,
                success=True,
            )
            state.record_tool_call(record)

        violation = checker.detect_no_progress(state)

        assert violation is None


class TestLimitViolation:
    """Tests for LimitViolation dataclass."""

    def test_initialization(self):
        """Test LimitViolation creation."""
        violation = LimitViolation(
            limit_type=LimitType.MAX_STEPS,
            message="Reached maximum steps (15)",
            suggestion="Try a more specific query",
        )

        assert violation.limit_type == LimitType.MAX_STEPS
        assert violation.message == "Reached maximum steps (15)"
        assert violation.suggestion == "Try a more specific query"

    def test_to_dict(self):
        """Test LimitViolation serialization."""
        violation = LimitViolation(
            limit_type=LimitType.MAX_TOOL_CALLS,
            message="Too many tool calls",
            suggestion="Simplify the task",
        )

        data = violation.to_dict()

        assert data["limit_type"] == "max_tool_calls"
        assert data["message"] == "Too many tool calls"
        assert data["suggestion"] == "Simplify the task"


class TestShouldStop:
    """Tests for should_stop comprehensive check."""

    def test_should_stop_false_for_fresh_state(self, agent_config):
        """Test should_stop is False for fresh state."""
        checker = LimitChecker(agent_config)
        state = AgentState(goal="Test")

        should_stop, violation = checker.should_stop(state)

        assert should_stop is False
        assert violation is None

    def test_should_stop_at_max_steps(self, agent_config):
        """Test should_stop at max steps."""
        checker = LimitChecker(agent_config)
        state = AgentState(goal="Test", step_count=5)

        should_stop, violation = checker.should_stop(state)

        assert should_stop is True
        assert violation is not None
        assert violation.limit_type == LimitType.MAX_STEPS
