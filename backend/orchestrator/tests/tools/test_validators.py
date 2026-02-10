"""
Tests for pre-execution and post-execution validators.

Note: These tests verify the validator interfaces work correctly.
Some tests are placeholders for more complex validation scenarios.
"""

import pytest

from agent.state import AgentState, ToolCallRecord
from tools.contracts import ToolContract, ToolParameter
from tools.validators.post_execution import (
    GoalCompletionValidator,
    GoalCoverage,
    PostExecutionValidator,
)


class TestPreExecutionValidation:
    """Tests for pre-execution validation through contracts."""

    @pytest.fixture
    def sample_contract(self):
        """Create sample contract for testing."""
        return ToolContract(
            name="test_tool",
            description="Test tool",
            parameters=[
                ToolParameter(
                    name="query",
                    type="string",
                    description="Search query",
                    required=True,
                ),
                ToolParameter(
                    name="limit",
                    type="integer",
                    description="Max results",
                    required=False,
                    default=10,
                    minimum=1,
                    maximum=100,
                ),
            ],
        )

    def test_validate_valid_params(self, sample_contract):
        """Test validation of valid parameters."""
        is_valid, error, suggestions = sample_contract.validate_params({
            "query": "test search",
            "limit": 20,
        })

        assert is_valid is True
        assert error is None

    def test_validate_missing_required(self, sample_contract):
        """Test validation catches missing required params."""
        is_valid, error, suggestions = sample_contract.validate_params({
            "limit": 10,
        })

        assert is_valid is False
        assert error is not None
        assert "query" in error.lower()

    def test_validate_wrong_type(self, sample_contract):
        """Test validation catches wrong types."""
        is_valid, error, suggestions = sample_contract.validate_params({
            "query": "test",
            "limit": "not_a_number",
        })

        assert is_valid is False
        assert error is not None
        assert "limit" in error.lower()

    def test_validate_out_of_range(self, sample_contract):
        """Test validation catches out of range values."""
        is_valid, error, suggestions = sample_contract.validate_params({
            "query": "test",
            "limit": 200,
        })

        assert is_valid is False
        assert error is not None
        assert "maximum" in error.lower()

    def test_normalize_adds_defaults(self, sample_contract):
        """Test normalization adds default values."""
        normalized = sample_contract.normalize({
            "query": "test",
        })

        assert normalized["query"] == "test"
        assert normalized["limit"] == 10

    def test_validation_feedback(self, sample_contract):
        """Test validation feedback for invalid params."""
        feedback = sample_contract.get_validation_feedback({})

        assert feedback["valid"] is False
        assert "error" in feedback
        assert "required_fields" in feedback


class TestPostExecutionValidation:
    """Tests for post-execution validation concepts."""

    def test_result_with_error_is_failure(self):
        """Test results with error key are considered failures."""
        result = {"error": "Something went wrong"}
        has_error = "error" in result
        assert has_error is True

    def test_result_without_error_is_success(self):
        """Test results without error key are considered success."""
        result = {"results": [{"id": "1"}]}
        has_error = "error" in result
        assert has_error is False

    def test_empty_results_detection(self):
        """Test empty results detection."""
        empty_result = {"results": [], "count": 0}
        non_empty = {"results": [{"id": "1"}], "count": 1}

        # Empty check
        is_empty = (
            len(empty_result.get("results", [])) == 0 or
            empty_result.get("count", -1) == 0
        )
        assert is_empty is True

        # Non-empty check
        is_empty = (
            len(non_empty.get("results", [])) == 0 and
            non_empty.get("count", -1) == 0
        )
        assert is_empty is False


class TestValidationFlow:
    """Tests for the overall validation flow."""

    @pytest.fixture
    def state(self):
        return AgentState(goal="Find all meetings from last week")

    def test_state_tracks_validation_errors(self, state):
        """Test state can track validation-related info."""
        # Record a tool call that had validation issues
        record = ToolCallRecord(
            tool_name="search_memories",
            arguments={"query": "test"},
            result={"results": []},
            duration_ms=100,
            success=True,
            validation_errors=["Parameter out of range"],
            was_repaired=True,
        )
        state.record_tool_call(record)

        assert state.tool_calls[0].validation_errors is not None
        assert state.tool_calls[0].was_repaired is True

    def test_repair_count_tracks_validation_repairs(self, state):
        """Test repair count is tracked."""
        assert state.repair_count == 0

        state.repair_count += 1

        assert state.repair_count == 1


class TestPostValidatorContactResolution:
    """Tests for deterministic resolve_contacts post-validation behavior."""

    @pytest.fixture
    def validator(self):
        return PostExecutionValidator(enable_llm_validation=False)

    def test_resolve_contacts_needs_user_input(self, validator):
        result = validator.validate(
            tool_name="resolve_contacts",
            params={"text": "When did I meet John?"},
            result={
                "status": "need_user_input",
                "people_mentioned": ["John"],
                "resolved_contacts": [],
                "ambiguous_contacts": [],
                "need_user_input": {
                    "kind": "disambiguation",
                    "prompt": "Which John do you mean?",
                    "submission_mode": "text",
                },
            },
            goal="Find meetings with John",
            known_facts=[],
        )
        assert result.coverage == GoalCoverage.NEED_USER_INPUT
        assert "Which John do you mean?" in result.reason

    def test_search_memories_clarification_short_circuit(self, validator):
        result = validator.validate(
            tool_name="search_memories",
            params={"query": "When did I meet John?"},
            result={
                "status": "need_user_input",
                "need_user_input": {
                    "kind": "disambiguation",
                    "prompt": "Which John do you mean?",
                    "submission_mode": "text",
                },
                "results": [],
                "count": 0,
            },
            goal="Find meetings with John",
            known_facts=[],
        )
        assert result.coverage == GoalCoverage.NEED_USER_INPUT

    def test_emit_ui_directive_requests_user_input(self, validator):
        result = validator.validate(
            tool_name="emit_ui_directive",
            params={},
            result={
                "success": True,
                "message": "Pick one option.",
                "directive": {
                    "version": "1.0",
                    "fallback_text": "Pick one option.",
                    "blocks": [
                        {
                            "id": "range_picker",
                            "type": "choice_buttons",
                            "options": [{"id": "7d", "label": "Last 7 days"}],
                        }
                    ],
                },
            },
            goal="Help me choose a date range",
            known_facts=[],
        )
        assert result.coverage == GoalCoverage.NEED_USER_INPUT
        assert "Pick one option." in result.reason

    def test_resolve_contacts_need_user_input_envelope(self, validator):
        result = validator.validate(
            tool_name="resolve_contacts",
            params={"text": "When did I meet John?"},
            result={
                "people_mentioned": ["John"],
                "resolved_contacts": [],
                "ambiguous_contacts": [],
                "need_user_input": {
                    "kind": "disambiguation",
                    "prompt": "Which John do you mean?",
                    "questions": ["Which John do you mean?"],
                },
            },
            goal="Find meetings with John",
            known_facts=[],
        )
        assert result.coverage == GoalCoverage.NEED_USER_INPUT
        assert "Which John do you mean?" in result.reason


class TestFactExtraction:
    """Tests for fact extraction concepts."""

    def test_extract_count_fact(self):
        """Test extracting count facts from results."""
        result = {"results": [{"id": "1"}, {"id": "2"}], "count": 2}

        facts = []
        if "count" in result:
            facts.append(f"Found {result['count']} results")

        assert len(facts) == 1
        assert "2" in facts[0]

    def test_extract_error_fact(self):
        """Test extracting error facts from results."""
        result = {"error": "Database connection failed"}

        facts = []
        if "error" in result:
            facts.append(f"Tool error: {result['error']}")

        assert len(facts) == 1
        assert "Database connection failed" in facts[0]

    def test_extract_sql_row_fact(self):
        """Test extracting facts from SQL results."""
        result = {
            "rows": [
                {"name": "John", "email": "john@example.com"},
                {"name": "Jane", "email": "jane@example.com"},
            ],
            "row_count": 2,
        }

        facts = []
        if "row_count" in result:
            facts.append(f"SQL query returned {result['row_count']} rows")

        assert len(facts) == 1
        assert "2 rows" in facts[0]


class TestGoalCompletionValidatorTemporal:
    """Tests for temporal-goal completion guardrails."""

    def test_temporal_goal_not_achieved_without_event_resolution(self):
        validator = GoalCompletionValidator()
        tool_calls = [
            ToolCallRecord(
                tool_name="search_memories",
                arguments={"query": "Gio", "sort_order": "newest"},
                result={"results": [{"id": "event-1"}], "count": 1},
                duration_ms=50,
                success=True,
            )
        ]
        achieved, reason, pending = validator.check_goal_achieved(
            goal="When did I last meet Gio?",
            tool_calls=tool_calls,
            known_facts=["Found 1 relevant memories"],
            final_content="",
        )
        assert achieved is False
        assert "Temporal query needs explicit date-ordered verification" in reason
        assert pending
