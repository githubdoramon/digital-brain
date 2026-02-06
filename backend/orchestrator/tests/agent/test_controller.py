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


class TestToolExposurePolicy:
    """Tests for tool exposure strategy in the controller."""

    @pytest.mark.asyncio
    async def test_run_exposes_full_toolset_without_intent_narrowing(self, monkeypatch):
        from agent.router import IntentClassification, IntentType

        controller = AgentController(
            config=AgentConfig(
                max_steps=2,
                max_tool_calls=5,
                max_repairs=1,
                enable_intent_routing=True,
                enable_validation=False,
            )
        )

        captured: dict[str, list[str]] = {}

        async def fake_run_intent_router(*_args, **_kwargs):
            return IntentClassification(
                intent=IntentType.HOME_CONTROL,
                confidence=0.9,
                allowed_tool_groups=["home"],
                constraints=[],
                skill_hints=[],
                reasoning="home intent",
            )

        monkeypatch.setattr(controller, "_run_intent_router", fake_run_intent_router)
        monkeypatch.setattr(
            controller,
            "_prime_contact_scope_for_question",
            lambda *args, **kwargs: None,
        )
        monkeypatch.setattr(
            controller,
            "_build_messages",
            lambda *args, **kwargs: [{"role": "user", "content": "Turn off office heater"}],
        )
        monkeypatch.setattr(
            controller,
            "_check_goal_completion",
            lambda *args, **kwargs: {"achieved": True, "reason": "ok", "pending_actions": []},
        )

        def fake_call_llm(_messages, tools):
            captured["tool_names"] = sorted(
                t.get("function", {}).get("name", "") for t in tools
            )
            return {
                "message": {
                    "content": "Action completed successfully with available context and tools.",
                }
            }

        monkeypatch.setattr(controller, "_call_llm", fake_call_llm)

        await controller.run(
            question="Turn off office heater",
            user_email="user@example.com",
            conversation_history=[],
        )

        tool_names = captured.get("tool_names", [])
        assert "home_assistant" in tool_names
        assert "search_memories" in tool_names


class TestContactAwareMemorySearch:
    """Tests for contact-aware memory search enrichment hooks."""

    @pytest.fixture
    def controller(self, agent_config):
        return AgentController(config=agent_config)

    def test_enriches_contact_ids_from_resolution(self, controller):
        state = AgentState(goal="When did I talk to John?")
        state.resolution["active_contact_scope_ids"] = ["contact-123"]
        args, preempt = controller._prepare_memory_search_arguments(
            args={"query": "When did I talk to John?"},
            state=state,
            question="When did I talk to John?",
            user_email="user@example.com",
            conversation_history=[],
        )

        assert preempt is None
        assert args.get("contact_ids") == ["contact-123"]

    def test_preempts_memory_search_when_contact_is_ambiguous(self, controller):
        state = AgentState(goal="When did I talk to John?")
        state.resolution["pending_contact_clarification"] = "Which John do you mean?"
        state.resolution["pending_contact_ambiguous_contacts"] = [
            {"clarification_prompt": "Which John do you mean?"}
        ]
        state.resolution["pending_contact_people"] = ["John"]
        args, preempt = controller._prepare_memory_search_arguments(
            args={"query": "When did I talk to John?"},
            state=state,
            question="When did I talk to John?",
            user_email="user@example.com",
            conversation_history=[],
        )

        assert "contact_ids" not in args
        assert preempt is not None
        assert preempt.get("needs_clarification") is True
        assert "Which John do you mean?" in preempt.get("clarification_prompt", "")

    def test_blocks_redundant_resolve_contacts_after_ambiguity(self, controller):
        state = AgentState(goal="When did I meet John?")
        state.record_tool_call(
            ToolCallRecord(
                tool_name="resolve_contacts",
                arguments={"text": "When did I meet John?"},
                result={
                    "status": "needs_clarification",
                    "ambiguous_contacts": [{"clarification_prompt": "Which John?"}],
                },
                duration_ms=10,
                success=True,
            )
        )

        blocked = controller._block_redundant_contact_resolution(
            state,
            {"text": "When did I meet John?"},
        )
        assert blocked is not None
        assert blocked.get("status") == "needs_clarification"

    def test_execute_handler_receives_runtime_context(self, controller, monkeypatch):
        captured = {}

        def fake_handler(args, **kwargs):
            captured.update(kwargs)
            return {"ok": True}

        monkeypatch.setattr("tools.handlers.get_handler", lambda _: fake_handler)
        result = controller._execute_handler(
            tool_name="resolve_contacts",
            args={"text": "John"},
            state=AgentState(goal="x"),
            question="x",
            search_limit=5,
            user_email="user@example.com",
            conversation_history=[{"role": "user", "content": "John"}],
        )

        assert result == {"ok": True}
        assert captured.get("user_email") == "user@example.com"
        assert captured.get("conversation_history") == [{"role": "user", "content": "John"}]

    def test_applies_active_contact_scope_to_followup_search(self, controller):
        state = AgentState(goal="When did I last meet Gio?")
        state.resolution["active_contact_scope_ids"] = ["contact-123"]

        args, preempt = controller._prepare_memory_search_arguments(
            args={"query": "last meeting"},
            state=state,
            question="When did I last meet Gio?",
            user_email="user@example.com",
            conversation_history=[],
        )

        assert preempt is None
        assert args.get("contact_ids") == ["contact-123"]

    def test_pending_clarification_blocks_search(self, controller):
        state = AgentState(goal="When did I meet John?")
        state.resolution["pending_contact_clarification"] = "Which John do you mean?"
        state.resolution["pending_contact_ambiguous_contacts"] = [
            {"clarification_prompt": "Which John do you mean?"}
        ]
        state.resolution["pending_contact_people"] = ["John"]

        args, preempt = controller._prepare_memory_search_arguments(
            args={"query": "meeting notes"},
            state=state,
            question="When did I meet John?",
            user_email="user@example.com",
            conversation_history=[],
        )

        assert "contact_ids" not in args
        assert preempt is not None
        assert preempt.get("needs_clarification") is True

    def test_temporal_latest_sets_newest_sort_and_wider_limit(self, controller):
        state = AgentState(goal="When did I last meet Gio?")
        args, preempt = controller._prepare_memory_search_arguments(
            args={"query": "When did I last meet Gio?"},
            state=state,
            question="When did I last meet Gio?",
            user_email=None,
            conversation_history=[],
        )
        assert preempt is None
        assert args.get("sort_order") == "newest"
        assert args.get("limit") == 25

    def test_temporal_first_sets_oldest_sort_and_wider_limit(self, controller):
        state = AgentState(goal="When was the first time I met Gio?")
        args, preempt = controller._prepare_memory_search_arguments(
            args={"query": "first time I met Gio"},
            state=state,
            question="When was the first time I met Gio?",
            user_email=None,
            conversation_history=[],
        )
        assert preempt is None
        assert args.get("sort_order") == "oldest"
        assert args.get("limit") == 25

    def test_low_signal_name_query_uses_goal_when_contact_scope_exists(self, controller):
        state = AgentState(goal="When did I last meet Gio?")
        state.resolution["active_contact_scope_ids"] = ["contact-gio"]
        args, preempt = controller._prepare_memory_search_arguments(
            args={"query": "Gio"},
            state=state,
            question="/new when did I last meet Gio?",
            user_email="user@example.com",
            conversation_history=[],
        )
        assert preempt is None
        assert args.get("contact_ids") == ["contact-gio"]
        assert args.get("query") == "when did I last meet Gio?"
        assert args.get("sort_order") == "newest"
        assert args.get("limit") == 25

    def test_blocks_redundant_equivalent_memory_search(self, controller):
        state = AgentState(goal="When did I last meet Gio?")
        state.record_tool_call(
            ToolCallRecord(
                tool_name="search_memories",
                arguments={
                    "query": "when did I last meet Gio?",
                    "contact_ids": ["contact-gio"],
                    "sort_order": "newest",
                    "limit": 25,
                },
                result={"results": [{"id": "event-1"}], "count": 1},
                duration_ms=120,
                success=True,
            )
        )
        blocked = controller._block_redundant_memory_search(
            state,
            {
                "query": "When did I last meet Gio?",
                "contact_ids": ["contact-gio"],
                "sort_order": "newest",
                "limit": 5,
            },
        )
        assert blocked is not None
        assert blocked.get("status") == "no_progress"

    def test_primes_contact_scope_from_question(self, controller, monkeypatch):
        state = AgentState(goal="When did I last meet Gio?")
        monkeypatch.setattr(
            "agents.contacts.executor.handle_resolve_contacts_request",
            lambda _payload: {
                "status": "success",
                "people_mentioned": ["Gio"],
                "resolved_contacts": [
                    {"contact_id": "contact-gio", "display_name": "Giovanni Panerai"}
                ],
                "ambiguous_contacts": [],
            },
        )

        prompt = controller._prime_contact_scope_for_question(
            state=state,
            question="/new when did I last meet Gio?",
            user_email="user@example.com",
            conversation_history=[],
        )

        assert prompt is None
        assert state.resolution.get("active_contact_scope_ids") == ["contact-gio"]
