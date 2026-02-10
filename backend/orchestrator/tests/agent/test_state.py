"""
Tests for AgentState dataclass.
"""

from agent.state import AgentState, ToolCallRecord


class TestAgentState:
    """Tests for AgentState initialization and methods."""

    def test_default_initialization(self):
        """Test AgentState with minimal arguments."""
        state = AgentState(goal="Test goal")

        assert state.goal == "Test goal"
        assert state.step_count == 0
        assert state.repair_count == 0
        assert state.constraints == []
        assert state.known_facts == []
        assert state.completed_actions == []
        assert state.tool_calls == []
        assert state.intent is None
        assert state.allowed_tool_groups == []
        assert state.information_candidates == []

    def test_full_initialization(self):
        """Test AgentState with all arguments."""
        state = AgentState(
            goal="Find all meetings",
            constraints=["read_only"],
            known_facts=["User is in EST timezone"],
            step_count=2,
            repair_count=1,
            intent="memory_search",
            allowed_tool_groups=["memory", "resolution"],
        )

        assert state.goal == "Find all meetings"
        assert state.constraints == ["read_only"]
        assert state.known_facts == ["User is in EST timezone"]
        assert state.step_count == 2
        assert state.repair_count == 1
        assert state.intent == "memory_search"
        assert state.allowed_tool_groups == ["memory", "resolution"]

    def test_add_fact(self):
        """Test adding facts to state."""
        state = AgentState(goal="Test")

        state.add_fact("Fact 1")
        state.add_fact("Fact 2")

        assert state.known_facts == ["Fact 1", "Fact 2"]

    def test_add_fact_deduplication(self):
        """Test that duplicate facts are not added."""
        state = AgentState(goal="Test")

        state.add_fact("Same fact")
        state.add_fact("Same fact")

        assert state.known_facts == ["Same fact"]

    def test_add_action(self):
        """Test adding completed actions."""
        state = AgentState(goal="Test")

        state.add_action("Searched memories")
        state.add_action("Executed SQL query")

        assert state.completed_actions == ["Searched memories", "Executed SQL query"]

    def test_record_tool_call(self):
        """Test recording tool calls."""
        state = AgentState(goal="Test")

        record = ToolCallRecord(
            tool_name="search_memories",
            arguments={"query": "meetings"},
            result={"results": []},
            duration_ms=150.5,
            success=True,
        )
        state.record_tool_call(record)

        assert len(state.tool_calls) == 1
        assert state.tool_calls[0].tool_name == "search_memories"
        assert state.tool_calls[0].arguments == {"query": "meetings"}
        assert state.tool_calls[0].success is True

    def test_tool_calls_count(self):
        """Test tool_calls_count property."""
        state = AgentState(goal="Test")

        assert state.tool_calls_count == 0

        record = ToolCallRecord(
            tool_name="test",
            arguments={},
            result={},
            duration_ms=100,
            success=True,
        )
        state.record_tool_call(record)

        assert state.tool_calls_count == 1

    def test_to_context_string(self):
        """Test state serialization to context string."""
        state = AgentState(
            goal="Find meetings",
            constraints=["read_only"],
            step_count=3,
        )
        state.add_fact("Found 5 meetings")
        state.add_action("Searched calendar")

        context = state.to_context_string()

        assert "GOAL: Find meetings" in context
        assert "STEP: 3" in context
        assert "CONSTRAINTS: read_only" in context
        assert "Found 5 meetings" in context
        assert "Searched calendar" in context

    def test_information_candidates_are_injected_into_context(self):
        """Test remembered candidate evidence is visible to the model context."""
        state = AgentState(goal="What is my vitamin b12 level?")
        state.remember_information_candidate(
            kind="document",
            candidate_id="doc:abc123",
            label="Clinical Laboratory Test Results Report",
            score=1.23,
            query="vitamin b12",
        )
        state.mark_information_candidate_inspected("document", "doc:abc123")

        context = state.to_context_string()
        assert "INFORMATION_CANDIDATES:" in context
        assert "document:Clinical Laboratory Test Results Report [doc:abc123]" in context
        assert "inspected" in context

    def test_get_best_information_candidate_prefers_inspected(self):
        """Test candidate prioritization for fallback context reuse."""
        state = AgentState(goal="Test")
        state.remember_information_candidate(
            kind="document",
            candidate_id="doc:uninspected",
            label="Uninspected",
            score=9.0,
        )
        state.remember_information_candidate(
            kind="document",
            candidate_id="doc:inspected",
            label="Inspected",
            score=1.0,
        )
        state.mark_information_candidate_inspected("document", "doc:inspected")

        best = state.get_best_information_candidate(inspected_only=True)
        assert best is not None
        assert best["candidate_id"] == "doc:inspected"

    def test_to_dict(self):
        """Test state serialization to dictionary."""
        state = AgentState(
            goal="Test goal",
            constraints=["read_only"],
            step_count=2,
        )

        data = state.to_dict()

        assert data["goal"] == "Test goal"
        assert data["constraints"] == ["read_only"]
        assert data["step_count"] == 2
        assert "known_facts" in data
        assert "tool_calls" in data

    def test_has_repeated_calls(self):
        """Test detection of repeated identical tool calls."""
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

        assert state.has_repeated_calls(3) is True

    def test_has_repeated_calls_different(self):
        """Test no repeated calls with different arguments."""
        state = AgentState(goal="Test")

        for i in range(3):
            record = ToolCallRecord(
                tool_name="search_memories",
                arguments={"query": f"query{i}"},
                result={"results": []},
                duration_ms=100,
                success=True,
            )
            state.record_tool_call(record)

        assert state.has_repeated_calls(3) is False

    def test_has_empty_result_streak(self):
        """Test detection of empty result streak."""
        state = AgentState(goal="Test")

        for i in range(3):
            record = ToolCallRecord(
                tool_name="search_memories",
                arguments={"query": f"query{i}"},
                result={"results": []},
                duration_ms=100,
                success=True,
            )
            state.record_tool_call(record)

        assert state.has_empty_result_streak(3) is True


class TestToolCallRecord:
    """Tests for ToolCallRecord dataclass."""

    def test_initialization(self):
        """Test ToolCallRecord creation."""
        record = ToolCallRecord(
            tool_name="search_memories",
            arguments={"query": "test"},
            result={"results": []},
            duration_ms=150.5,
            success=True,
            error=None,
        )

        assert record.tool_name == "search_memories"
        assert record.arguments == {"query": "test"}
        assert record.success is True
        assert record.error is None
        assert record.duration_ms == 150.5

    def test_with_error(self):
        """Test ToolCallRecord with error."""
        record = ToolCallRecord(
            tool_name="get_events",
            arguments={"query": "SELECT *"},
            result={},
            duration_ms=50,
            success=False,
            error="Upstream event provider unavailable",
        )

        assert record.success is False
        assert record.error == "Upstream event provider unavailable"

    def test_to_dict(self):
        """Test ToolCallRecord serialization."""
        record = ToolCallRecord(
            tool_name="test_tool",
            arguments={"arg": "value"},
            result={"data": "result"},
            duration_ms=100,
            success=True,
        )

        data = record.to_dict()

        assert data["tool_name"] == "test_tool"
        assert data["arguments"] == {"arg": "value"}
        assert data["result"] == {"data": "result"}
        assert data["success"] is True
        assert data["duration_ms"] == 100


class TestStateRuntimeFields:
    """Tests for runtime state helper fields."""

    def test_resolution_field(self):
        """Test resolution field for runtime contact scope state."""
        state = AgentState(goal="Test")

        assert state.resolution == {}
        state.resolution["entity_type"] = "person"
        assert state.resolution["entity_type"] == "person"

    def test_activated_skills_field(self):
        """Test activated_skills field for runtime skill gating."""
        state = AgentState(goal="Test")

        assert state.activated_skills == []
        state.activated_skills.append({"skill": "document-search"})
        assert len(state.activated_skills) == 1
