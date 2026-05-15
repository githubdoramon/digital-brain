"""
Tests for pre-execution and post-execution validators.

Note: These tests verify the validator interfaces work correctly.
Some tests are placeholders for more complex validation scenarios.
"""

import pytest

from agent.state import AgentState, ToolCallRecord
from tools.contracts import ToolContract, ToolParameter
from tools.registry import get_registry
from tools.validators.post_execution import (
    GoalCompletionValidator,
    GoalCoverage,
    PostExecutionValidator,
)
from tools.validators.pre_execution import PreExecutionValidator


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
        is_valid, error, _suggestions = sample_contract.validate_params(
            {
                "query": "test search",
                "limit": 20,
            }
        )

        assert is_valid is True
        assert error is None

    def test_validate_missing_required(self, sample_contract):
        """Test validation catches missing required params."""
        is_valid, error, _suggestions = sample_contract.validate_params(
            {
                "limit": 10,
            }
        )

        assert is_valid is False
        assert error is not None
        assert "query" in error.lower()

    def test_validate_wrong_type(self, sample_contract):
        """Test validation catches wrong types."""
        is_valid, error, _suggestions = sample_contract.validate_params(
            {
                "query": "test",
                "limit": "not_a_number",
            }
        )

        assert is_valid is False
        assert error is not None
        assert "limit" in error.lower()

    def test_validate_out_of_range(self, sample_contract):
        """Test validation catches out of range values."""
        is_valid, error, _suggestions = sample_contract.validate_params(
            {
                "query": "test",
                "limit": 200,
            }
        )

        assert is_valid is False
        assert error is not None
        assert "maximum" in error.lower()

    def test_normalize_adds_defaults(self, sample_contract):
        """Test normalization adds default values."""
        normalized = sample_contract.normalize(
            {
                "query": "test",
            }
        )

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
        is_empty = len(empty_result.get("results", [])) == 0 or empty_result.get("count", -1) == 0
        assert is_empty is True

        # Non-empty check
        is_empty = len(non_empty.get("results", [])) == 0 and non_empty.get("count", -1) == 0
        assert is_empty is False


class TestPreExecutionSemanticValidation:
    """Tests for targeted semantic checks in pre-execution validation."""

    @pytest.fixture
    def validator(self):
        return PreExecutionValidator(get_registry())

    def test_home_assistant_call_tool_requires_tool_name(self, validator):
        result = validator.validate("home_assistant", {"action": "call_tool", "arguments": {}})
        assert result.valid is False
        assert "tool_name" in "; ".join(result.errors)

    def test_lookup_contact_search_requires_query(self, validator):
        result = validator.validate("lookup_contact", {"action": "search"})
        assert result.valid is False
        assert "query" in "; ".join(result.errors)

    def test_get_events_by_ids_requires_event_ids(self, validator):
        result = validator.validate("get_events", {"action": "by_ids"})
        assert result.valid is False
        assert "event_ids" in "; ".join(result.errors)

    def test_get_events_by_time_span_accepts_single_bound(self, validator):
        result = validator.validate(
            "get_events",
            {"action": "by_time_span", "time_start": "2026-02-01T00:00:00Z"},
        )
        assert result.valid is True

    def test_get_events_by_time_span_requires_at_least_one_bound(self, validator):
        result = validator.validate("get_events", {"action": "by_time_span"})
        assert result.valid is False
        assert "time_start" in "; ".join(result.errors)

    def test_get_events_by_ids_drops_irrelevant_limit_during_normalization(self, validator):
        result, normalized = validator.validate_and_normalize(
            "get_events",
            {"action": "by_ids", "event_ids": ["event:123"], "limit": 0},
        )

        assert result.valid is True
        assert normalized is not None
        assert normalized["action"] == "by_ids"
        assert normalized["event_ids"] == ["event:123"]
        assert "limit" not in normalized

    def test_lookup_contact_places_requires_contact_selector(self, validator):
        result = validator.validate("lookup_contact_places", {"role_hint": "home"})
        assert result.valid is False
        assert "contact_id" in "; ".join(result.errors)
        assert "group_query" in "; ".join(result.errors)

    def test_summarize_memories_requires_time_bounds(self, validator):
        result = validator.validate("summarize_memories", {"query_focus": "topics"})
        assert result.valid is False
        assert "time_start" in "; ".join(result.errors)

    def test_lookup_place_contacts_requires_place_selector(self, validator):
        result = validator.validate("lookup_place_contacts", {"role_hint": "home"})
        assert result.valid is False
        assert "place_id" in "; ".join(result.errors)

    def test_emit_ui_directive_surfaces_detailed_errors(self, validator):
        """Repair feedback must include the concrete sanitize errors, not 'failed validation'."""
        # The shape the LLM kept guessing in the orchestrator logs: top-level
        # `type`/`form`/`fallback_text` instead of nested `blocks[]` entries.
        result = validator.validate(
            "emit_ui_directive",
            {
                "directive": {
                    "fallback_text": "...",
                    "form": [{"name": "clarification", "type": "textarea"}],
                    "type": "clarification_form",
                }
            },
        )
        assert result.valid is False
        joined = "; ".join(result.errors)
        assert "blocks" in joined
        # Generic fallback message must not be the only signal anymore.
        assert "failed validation" not in joined or "blocks" in joined


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

    def test_search_memories_document_result_suggests_get_document(self, validator):
        result = validator.validate(
            tool_name="search_memories",
            params={"query": "vitamin b12"},
            result={
                "results": [
                    {
                        "id": "doc:lab",
                        "kind": "document",
                        "title": "Clinical Laboratory Test Results Report",
                        "score": 1.4,
                    }
                ],
                "count": 1,
            },
            goal="What is my vitamin b12 level?",
            known_facts=[],
        )
        assert result.coverage == GoalCoverage.NEEDS_MORE_TOOLS
        assert "get_document" in result.suggested_next_tools
        assert any("Top document candidate:" in fact for fact in result.extracted_facts)

    def test_get_document_result_is_deterministically_accepted(self, validator):
        result = validator.validate(
            tool_name="get_document",
            params={"document_id": "doc:lab"},
            result={"document": {"document_id": "doc:lab", "title": "Clinical report"}},
            goal="What is my vitamin b12 level?",
            known_facts=[],
        )
        assert result.coverage == GoalCoverage.NEEDS_MORE_TOOLS
        assert "Document retrieved" in result.reason

    def test_summarize_memories_result_is_sufficient(self, validator):
        result = validator.validate(
            tool_name="summarize_memories",
            params={
                "time_start": "2026-01-01T00:00:00Z",
                "time_end": "2026-01-31T23:59:59Z",
            },
            result={
                "summary": "Overview\n- Project Apollo moved forward.",
                "count": 3,
                "source_items": [{"kind": "event", "id": "event:1", "title": "Apollo sync"}],
            },
            goal="Summarize my work last week",
            known_facts=[],
        )
        assert result.coverage == GoalCoverage.SATISFIED
        assert any("GOAL_ACHIEVED" in fact for fact in result.extracted_facts)


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
                result={"results": [{"id": "event-1", "kind": "event"}], "count": 1},
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
        assert "must be inspected with get_events" in reason
        assert pending

    def test_temporal_document_query_does_not_require_get_events(self):
        validator = GoalCompletionValidator()
        tool_calls = [
            ToolCallRecord(
                tool_name="search_memories",
                arguments={"query": "hemoglobin reading", "sort_order": "newest"},
                result={"results": [{"id": "doc:lab", "kind": "document"}], "count": 1},
                duration_ms=40,
                success=True,
            ),
            ToolCallRecord(
                tool_name="get_document",
                arguments={"document_id": "doc:lab"},
                result={"document": {"document_id": "doc:lab", "title": "Clinical report"}},
                duration_ms=35,
                success=True,
            ),
        ]

        achieved, reason, pending = validator.check_goal_achieved(
            goal="What is my latest hemoglobin reading?",
            tool_calls=tool_calls,
            known_facts=["Retrieved document: Clinical report"],
            final_content="Your latest hemoglobin result is 14.2 g/dL from 2026-02-10.",
        )

        assert achieved is True
        assert "Query returned" in reason
        assert not pending

    def test_document_candidate_requires_document_inspection(self):
        validator = GoalCompletionValidator()
        tool_calls = [
            ToolCallRecord(
                tool_name="search_memories",
                arguments={"query": "vitamin b12"},
                result={"results": [{"id": "doc:lab", "kind": "document"}], "count": 1},
                duration_ms=40,
                success=True,
            )
        ]
        achieved, reason, pending = validator.check_goal_achieved(
            goal="What is my vitamin b12 level?",
            tool_calls=tool_calls,
            known_facts=["Found 1 relevant memories", "Top document candidate: Clinical (doc:lab)"],
            final_content="",
        )
        assert achieved is False
        assert "Top candidate is a document" in reason
        assert pending

    def test_contradiction_no_record_after_results_is_not_achieved(self):
        validator = GoalCompletionValidator()
        tool_calls = [
            ToolCallRecord(
                tool_name="search_memories",
                arguments={"query": "vitamin b12"},
                result={"results": [{"id": "doc:lab", "kind": "document"}], "count": 1},
                duration_ms=40,
                success=True,
            ),
            ToolCallRecord(
                tool_name="get_document",
                arguments={"document_id": "doc:lab"},
                result={"document": {"document_id": "doc:lab", "title": "Clinical report"}},
                duration_ms=60,
                success=True,
            ),
        ]
        achieved, reason, pending = validator.check_goal_achieved(
            goal="What is my vitamin b12 level?",
            tool_calls=tool_calls,
            known_facts=["Found 1 relevant memories", "Retrieved document: Clinical report"],
            final_content="I don't have a record of a recent vitamin b12 measurement.",
        )
        assert achieved is False
        assert "contradicts retrieved results" in reason
        assert pending

    def test_evolving_status_query_prefers_latest_event_candidate(self):
        validator = GoalCompletionValidator()
        tool_calls = [
            ToolCallRecord(
                tool_name="search_memories",
                arguments={"query": "pregnant"},
                result={
                    "results": [
                        {
                            "id": "doc:lab",
                            "kind": "document",
                            "title": "Clinical Laboratory Test Results Report",
                            "score": 1.5,
                            "document_date": "2025-10-29T00:00:00+00:00",
                        },
                        {
                            "id": "meeting:older",
                            "kind": "event",
                            "title": "Avery <> Alex - 1:1",
                            "score": 0.59,
                            "start_date": "2025-12-09T11:29:00+00:00",
                        },
                        {
                            "id": "meeting:newer",
                            "kind": "event",
                            "title": "Avery <> Alex - 1:1",
                            "score": 0.57,
                            "start_date": "2026-04-06T11:30:00+01:00",
                        },
                    ],
                    "count": 3,
                },
                duration_ms=50,
                success=True,
            )
        ]

        achieved, reason, pending = validator.check_goal_achieved(
            goal="What is Avery Hill's current status?",
            tool_calls=tool_calls,
            known_facts=["Found 3 relevant memories"],
            final_content="",
        )

        assert achieved is False
        assert "must be inspected with get_events" in reason
        assert pending == [
            "Call get_events with action='by_ids' and event_ids=['meeting:newer'] for 'Avery <> Alex - 1:1' before responding"
        ]

    def test_evolving_status_query_with_inspected_latest_event_is_achieved(self):
        validator = GoalCompletionValidator()
        tool_calls = [
            ToolCallRecord(
                tool_name="search_memories",
                arguments={"query": "pregnant"},
                result={
                    "results": [
                        {
                            "id": "doc:lab",
                            "kind": "document",
                            "title": "Clinical Laboratory Test Results Report",
                            "score": 1.5,
                            "document_date": "2025-10-29T00:00:00+00:00",
                        },
                        {
                            "id": "meeting:newer",
                            "kind": "event",
                            "title": "Avery <> Alex - 1:1",
                            "score": 0.57,
                            "start_date": "2026-04-06T11:30:00+01:00",
                        },
                    ],
                    "count": 2,
                },
                duration_ms=50,
                success=True,
            ),
            ToolCallRecord(
                tool_name="get_events",
                arguments={"action": "by_ids", "event_ids": ["meeting:newer"]},
                result={
                    "events": [
                        {
                            "id": "meeting:newer",
                            "title": "Avery <> Alex - 1:1",
                            "summary": "due date: April 15",
                        }
                    ],
                    "count": 1,
                },
                duration_ms=40,
                success=True,
            ),
        ]

        achieved, reason, pending = validator.check_goal_achieved(
            goal="What is Avery Hill's current status?",
            tool_calls=tool_calls,
            known_facts=["Retrieved 1 event details"],
            final_content="Avery's latest status is that paternity leave is imminent.",
        )

        assert achieved is True
        assert "Query returned" in reason
        assert not pending
