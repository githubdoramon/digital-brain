"""
Tests for AgentController.

Note: These tests verify the controller interface and configuration.
Full integration tests would require mocking the LLM backend.
"""

import pytest

from agent.controller import AgentController
from agent.guardrails import build_contact_scope_context
from agent.limits import AgentConfig, LimitType
from agent.state import AgentState, ToolCallRecord
from agents.main.agent import build_main_conversational_agent


def _build_controller(config: AgentConfig | None = None) -> AgentController:
    cfg = config or AgentConfig()
    agent = build_main_conversational_agent(
        max_steps=cfg.max_steps,
        max_tool_calls=cfg.max_tool_calls,
        timeout_seconds=120,
    )
    return AgentController(config=cfg, conversational_agent=agent)


class TestAgentControllerInitialization:
    """Tests for AgentController initialization."""

    def test_default_initialization(self):
        """Test controller with default config."""
        controller = _build_controller()

        assert controller.config is not None
        assert controller.config.max_steps > 0

    def test_custom_config(self):
        """Test controller with custom config."""
        config = AgentConfig(max_steps=10, max_tool_calls=15)
        controller = _build_controller(config)

        assert controller.config.max_steps == 10
        assert controller.config.max_tool_calls == 15

    def test_disable_intent_routing(self):
        """Test controller with intent routing disabled."""
        config = AgentConfig(enable_intent_routing=False)
        controller = _build_controller(config)

        assert controller.config.enable_intent_routing is False

    def test_disable_validation(self):
        """Test controller with validation disabled."""
        config = AgentConfig(enable_validation=False)
        controller = _build_controller(config)

        assert controller.config.enable_validation is False


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
        for i, result in enumerate(
            [
                {"results": [{"id": "1"}]},
                {"results": [{"id": "2"}]},
                {"results": [{"id": "3"}]},
            ]
        ):
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
    async def test_high_confidence_route_restricts_tools(self, monkeypatch):
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
                confidence=0.95,
                allowed_tool_groups=["home"],
                constraints=[],
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
            captured["tool_names"] = sorted(t.get("function", {}).get("name", "") for t in tools)
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
        assert "search_memories" not in tool_names

    @pytest.mark.asyncio
    async def test_medium_confidence_adds_resolution_group(self, monkeypatch):
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
                intent=IntentType.MEMORY_SEARCH,
                confidence=0.7,
                allowed_tool_groups=["memory"],
                constraints=[],
                reasoning="memory intent",
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
            lambda *args, **kwargs: [{"role": "user", "content": "Find memory"}],
        )
        monkeypatch.setattr(
            controller,
            "_check_goal_completion",
            lambda *args, **kwargs: {"achieved": True, "reason": "ok", "pending_actions": []},
        )

        def fake_call_llm(_messages, tools):
            captured["tool_names"] = sorted(t.get("function", {}).get("name", "") for t in tools)
            return {"message": {"content": "Done with medium confidence."}}

        monkeypatch.setattr(controller, "_call_llm", fake_call_llm)

        await controller.run(
            question="Find memory",
            user_email="user@example.com",
            conversation_history=[],
        )

        tool_names = captured.get("tool_names", [])
        assert "search_memories" in tool_names
        assert "resolve_contacts" in tool_names

    @pytest.mark.asyncio
    async def test_low_confidence_fails_open_to_full_tools(self, monkeypatch):
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
                intent=IntentType.UNKNOWN,
                confidence=0.4,
                allowed_tool_groups=["web"],
                constraints=[],
                reasoning="uncertain",
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
            lambda *args, **kwargs: [{"role": "user", "content": "unclear request"}],
        )
        monkeypatch.setattr(
            controller,
            "_check_goal_completion",
            lambda *args, **kwargs: {"achieved": True, "reason": "ok", "pending_actions": []},
        )

        def fake_call_llm(_messages, tools):
            captured["tool_names"] = sorted(t.get("function", {}).get("name", "") for t in tools)
            return {"message": {"content": "Done with full tools."}}

        monkeypatch.setattr(controller, "_call_llm", fake_call_llm)

        await controller.run(
            question="unclear request",
            user_email="user@example.com",
            conversation_history=[],
        )

        tool_names = captured.get("tool_names", [])
        assert "search_memories" in tool_names
        assert "home_assistant" in tool_names

    @pytest.mark.asyncio
    async def test_run_skips_contact_presolve_for_web_intent(self, monkeypatch):
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

        async def fake_run_intent_router(*_args, **_kwargs):
            return IntentClassification(
                intent=IntentType.WEB_SEARCH,
                confidence=0.9,
                allowed_tool_groups=["web"],
                constraints=[],
                reasoning="web intent",
            )

        monkeypatch.setattr(controller, "_run_intent_router", fake_run_intent_router)
        monkeypatch.setattr(
            controller,
            "_prime_contact_scope_for_question",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                AssertionError("contact pre-resolution should be skipped for web intent")
            ),
        )
        monkeypatch.setattr(
            controller,
            "_build_messages",
            lambda *args, **kwargs: [{"role": "user", "content": "What is the weather today?"}],
        )
        monkeypatch.setattr(
            controller,
            "_check_goal_completion",
            lambda *args, **kwargs: {"achieved": True, "reason": "ok", "pending_actions": []},
        )
        monkeypatch.setattr(
            controller,
            "_call_llm",
            lambda *_args, **_kwargs: {"message": {"content": "Sunny today."}},
        )

        await controller.run(
            question="What is the weather today?",
            user_email="user@example.com",
            conversation_history=[],
        )

    @pytest.mark.asyncio
    async def test_run_keeps_contact_presolve_for_memory_intent(self, monkeypatch):
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
        observed = {"called": False}

        async def fake_run_intent_router(*_args, **_kwargs):
            return IntentClassification(
                intent=IntentType.MEMORY_SEARCH,
                confidence=0.9,
                allowed_tool_groups=["memory", "resolution"],
                constraints=[],
                reasoning="memory intent",
            )

        monkeypatch.setattr(controller, "_run_intent_router", fake_run_intent_router)

        def fake_prime(*_args, **_kwargs):
            observed["called"] = True
            return None

        monkeypatch.setattr(controller, "_prime_contact_scope_for_question", fake_prime)
        monkeypatch.setattr(
            controller,
            "_build_messages",
            lambda *args, **kwargs: [{"role": "user", "content": "When did I last meet Gio?"}],
        )
        monkeypatch.setattr(
            controller,
            "_check_goal_completion",
            lambda *args, **kwargs: {"achieved": True, "reason": "ok", "pending_actions": []},
        )
        monkeypatch.setattr(
            controller,
            "_call_llm",
            lambda *_args, **_kwargs: {"message": {"content": "You met Gio recently."}},
        )

        await controller.run(
            question="When did I last meet Gio?",
            user_email="user@example.com",
            conversation_history=[],
        )

        assert observed["called"] is True


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
        state.resolution["pending_contact_need_user_input"] = {
            "kind": "disambiguation",
            "prompt": "Which John do you mean?",
            "submission_mode": "text",
        }
        state.resolution["pending_contact_ambiguous_contacts"] = [
            {
                "original_text": "John",
                "candidates": [
                    {"contact_id": "c1", "display_name": "John Smith"},
                    {"contact_id": "c2", "display_name": "John Doe"},
                ],
            }
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
        assert preempt.get("status") == "need_user_input"
        assert "John" in preempt.get("need_user_input", {}).get("prompt", "")

    def test_blocks_redundant_resolve_contacts_after_ambiguity(self, controller):
        state = AgentState(goal="When did I meet John?")
        state.record_tool_call(
            ToolCallRecord(
                tool_name="resolve_contacts",
                arguments={"text": "When did I meet John?"},
                result={
                    "status": "need_user_input",
                    "need_user_input": {
                        "kind": "disambiguation",
                        "prompt": "Which John?",
                        "submission_mode": "text",
                    },
                    "ambiguous_contacts": [],
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
        assert blocked.get("status") == "need_user_input"

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
        state.resolution["pending_contact_need_user_input"] = {
            "kind": "disambiguation",
            "prompt": "Which John do you mean?",
            "submission_mode": "text",
        }
        state.resolution["pending_contact_ambiguous_contacts"] = [
            {
                "original_text": "John",
                "candidates": [
                    {"contact_id": "c1", "display_name": "John Smith"},
                    {"contact_id": "c2", "display_name": "John Doe"},
                ],
            }
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
        assert preempt.get("status") == "need_user_input"

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

    def test_temporal_latest_caps_time_end_at_now_for_past_queries(self, controller, monkeypatch):
        state = AgentState(goal="When did I last meet Gio?")
        monkeypatch.setattr("agent.controller.utc_now_iso", lambda: "2026-02-07T12:00:00+00:00")

        args, preempt = controller._prepare_memory_search_arguments(
            args={"query": "When did I last meet Gio?"},
            state=state,
            question="When did I last meet Gio?",
            user_email=None,
            conversation_history=[],
        )

        assert preempt is None
        assert args.get("sort_order") == "newest"
        assert args.get("time_end") == "2026-02-07T12:00:00+00:00"

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

    def test_future_temporal_query_sets_time_start_without_time_end(self, controller, monkeypatch):
        state = AgentState(goal="What meetings are scheduled with Gio next week?")
        monkeypatch.setattr("agent.controller.utc_now_iso", lambda: "2026-02-07T12:00:00+00:00")

        args, preempt = controller._prepare_memory_search_arguments(
            args={"query": "What meetings are scheduled with Gio next week?"},
            state=state,
            question="What meetings are scheduled with Gio next week?",
            user_email=None,
            conversation_history=[],
        )

        assert preempt is None
        assert args.get("time_start") == "2026-02-07T12:00:00+00:00"
        assert args.get("time_end") in {None, ""}

    def test_low_signal_name_query_uses_goal_when_contact_scope_exists(self, controller):
        state = AgentState(goal="When did I last meet Gio?")
        state.resolution["active_contact_scope_ids"] = ["contact-gio"]
        state.resolution["active_contact_scope"] = [
            {
                "mention_text": "Gio",
                "display_name": "Giovanni Panerai",
                "contact_id": "contact-gio",
            }
        ]
        args, preempt = controller._prepare_memory_search_arguments(
            args={"query": "Gio"},
            state=state,
            question="/new when did I last meet Gio?",
            user_email="user@example.com",
            conversation_history=[],
        )
        assert preempt is None
        assert args.get("contact_ids") == ["contact-gio"]
        assert args.get("query") == "events"
        assert args.get("sort_order") == "newest"
        assert args.get("limit") == 25

    def test_scoped_query_keeps_only_semantic_topic_terms(self, controller):
        state = AgentState(goal="When did I last meet Gio and we talked about birds?")
        state.resolution["active_contact_scope_ids"] = ["contact-gio"]
        state.resolution["active_contact_scope"] = [
            {
                "mention_text": "Gio",
                "display_name": "Giovanni Panerai",
                "contact_id": "contact-gio",
            }
        ]

        args, preempt = controller._prepare_memory_search_arguments(
            args={"query": "When did I last meet Gio and we talked about birds?"},
            state=state,
            question="When did I last meet Gio and we talked about birds?",
            user_email="user@example.com",
            conversation_history=[],
        )

        assert preempt is None
        assert args.get("contact_ids") == ["contact-gio"]
        assert args.get("query") == "birds"
        assert args.get("sort_order") == "newest"
        assert args.get("limit") == 25

    def test_builds_contact_scope_context_message(self, controller):
        state = AgentState(goal="When did I last meet Gio?")
        state.resolution["active_contact_scope"] = [
            {
                "mention_text": "Gio",
                "display_name": "Giovanni Panerai",
                "contact_id": "contact:gio-acme-xyz",
            }
        ]

        context = build_contact_scope_context(state.resolution.get("active_contact_scope") or [])

        assert context is not None
        assert "RESOLVED CONTACT SCOPE" in context
        assert "'Gio' -> 'Giovanni Panerai'" in context
        assert "contact:gio-acme-xyz" in context
        assert "query to 'events'" in context

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

    def test_blocks_research_when_document_already_inspected(self, controller):
        state = AgentState(goal="What is my vitamin b12 level?")
        state.remember_information_candidate(
            kind="document",
            candidate_id="doc:lab",
            label="Clinical Laboratory Test Results Report",
            score=1.3,
            query="vitamin b12",
        )
        state.mark_information_candidate_inspected("document", "doc:lab")
        state.record_tool_call(
            ToolCallRecord(
                tool_name="search_memories",
                arguments={
                    "query": "vitamin b12",
                    "tags": ["health", "lab"],
                    "sort_order": "relevance",
                },
                result={
                    "results": [
                        {
                            "id": "doc:lab",
                            "kind": "document",
                            "title": "Clinical Laboratory Test Results Report",
                        }
                    ],
                    "count": 1,
                },
                duration_ms=95,
                success=True,
            )
        )

        blocked = controller._block_redundant_memory_search(
            state,
            {
                "query": "vitamin b12 level",
                "tags": ["health", "lab"],
                "sort_order": "relevance",
            },
        )
        assert blocked is not None
        assert blocked.get("status") == "no_progress"
        assert "already inspected document" in blocked.get("message", "").lower()

    def test_resolves_contact_scope_during_person_referential_memory_search(
        self, controller, monkeypatch
    ):
        state = AgentState(goal="When did I last meet Gio?")
        monkeypatch.setattr(
            "contact_resolution_service.resolve_contacts_request",
            lambda _payload: {
                "status": "success",
                "people_mentioned": ["Gio"],
                "resolved_contacts": [
                    {
                        "contact_id": "contact-gio",
                        "display_name": "Giovanni Panerai",
                        "original_text": "Gio",
                    }
                ],
                "ambiguous_contacts": [],
            },
        )

        args, preempt = controller._prepare_memory_search_arguments(
            args={"query": "When did I last meet Gio?"},
            state=state,
            question="When did I last meet Gio?",
            user_email="user@example.com",
            conversation_history=[],
        )

        assert preempt is None
        assert args.get("contact_ids") == ["contact-gio"]
        assert args.get("query") == "events"

    def test_person_referential_no_people_resolution_is_cached(self, controller, monkeypatch):
        state = AgentState(goal="When did I meet someone?")
        call_counter = {"count": 0}

        def fake_resolver(_payload):
            call_counter["count"] += 1
            return {
                "status": "no_people",
                "people_mentioned": [],
                "resolved_contacts": [],
                "ambiguous_contacts": [],
            }

        monkeypatch.setattr(
            "contact_resolution_service.resolve_contacts_request",
            fake_resolver,
        )

        controller._prepare_memory_search_arguments(
            args={"query": "When did I meet someone?"},
            state=state,
            question="When did I meet someone?",
            user_email="user@example.com",
            conversation_history=[],
        )
        controller._prepare_memory_search_arguments(
            args={"query": "When did I meet someone?"},
            state=state,
            question="When did I meet someone?",
            user_email="user@example.com",
            conversation_history=[],
        )

        assert call_counter["count"] == 1

    def test_primes_contact_scope_from_question(self, controller, monkeypatch):
        state = AgentState(goal="When did I last meet Gio?")
        monkeypatch.setattr(
            "contact_resolution_service.resolve_contacts_request",
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

    def test_primes_contacts_even_for_generic_question(self, controller, monkeypatch):
        state = AgentState(goal="What happened last week?")
        captured_payload = {}

        def fake_resolver(payload):
            captured_payload.update(payload)
            return {
                "status": "no_people",
                "people_mentioned": [],
                "resolved_contacts": [],
                "ambiguous_contacts": [],
            }

        monkeypatch.setattr(
            "contact_resolution_service.resolve_contacts_request",
            fake_resolver,
        )

        prompt = controller._prime_contact_scope_for_question(
            state=state,
            question="What happened last week?",
            user_email="user@example.com",
            conversation_history=[],
        )

        assert prompt is None
        assert captured_payload.get("text") == "What happened last week?"
        assert captured_payload.get("user_email") == "user@example.com"

    def test_prime_contact_scope_includes_current_user_message_in_context(
        self, controller, monkeypatch
    ):
        state = AgentState(goal="Perenai")
        captured_payload = {}

        def fake_resolver(payload):
            captured_payload.update(payload)
            return {
                "status": "no_people",
                "people_mentioned": [],
                "resolved_contacts": [],
                "ambiguous_contacts": [],
            }

        monkeypatch.setattr(
            "contact_resolution_service.resolve_contacts_request",
            fake_resolver,
        )

        prompt = controller._prime_contact_scope_for_question(
            state=state,
            question="Perenai",
            user_email="user@example.com",
            conversation_history=[
                {"role": "user", "content": "when did I last meet Gio?"},
                {"role": "assistant", "content": "Which Gio did you mean?"},
            ],
        )

        assert prompt is None
        conversation_messages = captured_payload.get("conversation_messages")
        assert conversation_messages is not None
        assert conversation_messages[-1] == {"role": "user", "content": "Perenai"}


class TestClientContextNormalization:
    """Tests for client context normalization before prompt injection."""

    @pytest.fixture
    def controller(self, agent_config):
        return AgentController(config=agent_config)

    def test_normalizes_and_rounds_location_context(self, controller):
        normalized = controller._normalize_client_context(
            {
                "timezone": " America/Los_Angeles ",
                "locale": " en-US ",
                "location": {
                    "lat": "37.7749295",
                    "lon": "-122.4194155",
                    "accuracy_m": "42.44",
                    "captured_at": "2026-02-08T10:20:30Z",
                    "source": "browser",
                },
            }
        )

        assert normalized == {
            "timezone": "America/Los_Angeles",
            "locale": "en-US",
            "location": {
                "lat": 37.775,
                "lon": -122.419,
                "accuracy_m": 42.4,
                "captured_at": "2026-02-08T10:20:30Z",
                "source": "browser",
            },
        }

    def test_drops_invalid_location_values(self, controller):
        normalized = controller._normalize_client_context(
            {
                "timezone": "UTC",
                "location": {
                    "lat": "400",
                    "lon": "-122.41",
                },
            }
        )

        assert normalized == {"timezone": "UTC"}
