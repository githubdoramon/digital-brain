"""
Integration tests for the full agent flow.

Note: These tests verify integration between components.
Full end-to-end tests with LLM calls require additional infrastructure.
"""

import json

import pytest

from agent.controller import AgentController
from agent.limits import AgentConfig, LimitChecker, LimitType
from agent.router import TOOL_GROUPS, IntentClassification, IntentRouter, IntentType
from agent.state import AgentState, ToolCallRecord
from agents.main.agent import build_main_conversational_agent
from tools.registry import get_registry


def _build_controller(config: AgentConfig) -> AgentController:
    main_agent = build_main_conversational_agent(
        max_steps=config.max_steps,
        max_tool_calls=config.max_tool_calls,
        timeout_seconds=120,
    )
    return AgentController(config=config, conversational_agent=main_agent)


class TestAgentControllerIntegration:
    """Integration tests for AgentController with other components."""

    @pytest.fixture
    def controller(self, agent_config):
        return _build_controller(agent_config)

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

    @pytest.mark.asyncio
    async def test_search_memories_preempts_with_contact_clarification(
        self, controller, monkeypatch
    ):
        """
        Test search_memories is preempted when clarification is pending.

        This verifies the integration path inside _execute_tool_call:
        search_memories -> pending clarification short-circuit.
        """
        state = AgentState(goal="When did I talk to John?")
        state.step_count = 1
        state.resolution["pending_contact_need_user_input"] = {
            "kind": "disambiguation",
            "prompt": "Which John do you mean?",
            "submission_mode": "text",
        }
        state.resolution["pending_contact_ambiguous_contacts"] = [
            {
                "original_text": "John",
                "candidates": [
                    {"contact_id": "contact-1", "display_name": "John Smith"},
                    {"contact_id": "contact-2", "display_name": "John Doe"},
                ],
            }
        ]
        state.resolution["pending_contact_people"] = ["John"]

        class StubLogger:
            def log_tool_call(self, *args, **kwargs):
                return None

            def log_validation_result(self, *args, **kwargs):
                return None

        controller._logger = StubLogger()

        def should_not_run_handler(*args, **kwargs):
            raise AssertionError("search handler should not run when clarification is required")

        monkeypatch.setattr(controller, "_execute_handler", should_not_run_handler)

        call = {
            "id": "call_ambiguous_search",
            "type": "function",
            "function": {
                "name": "search_memories",
                "arguments": json.dumps({"query": "When did I talk to John?"}),
            },
        }

        result = await controller._execute_tool_call(
            call=call,
            state=state,
            question="When did I talk to John?",
            search_limit=5,
            run_id="test_run",
            user_email="user@example.com",
            conversation_history=[],
        )

        assert result["status"] == "need_user_input"
        assert "John" in result["need_user_input"]["prompt"]
        assert state.tool_calls_count == 1
        assert state.last_tool_call is not None
        assert state.last_tool_call.tool_name == "search_memories"

    @pytest.mark.asyncio
    async def test_search_memories_inherits_scope_after_resolve_contacts(
        self, controller, monkeypatch
    ):
        """After resolve_contacts succeeds, follow-up search should keep contact scope."""
        state = AgentState(goal="When did I last meet Gio?")
        state.step_count = 1
        controller.config.enable_validation = False

        class StubLogger:
            def log_tool_call(self, *args, **kwargs):
                return None

            def log_validation_result(self, *args, **kwargs):
                return None

        controller._logger = StubLogger()
        captured_search_args = {}

        def fake_execute_handler(tool_name, args, **kwargs):
            if tool_name == "resolve_contacts":
                return {
                    "status": "success",
                    "people_mentioned": ["Gio"],
                    "resolved_contacts": [
                        {"contact_id": "contact-gio", "display_name": "Giovanni Panerai"}
                    ],
                    "ambiguous_contacts": [],
                }
            if tool_name == "search_memories":
                captured_search_args.update(args)
                return {"results": [{"id": "event-1", "kind": "event"}], "count": 1}
            return {"results": [], "count": 0}

        monkeypatch.setattr(controller, "_execute_handler", fake_execute_handler)

        resolve_call = {
            "id": "call_resolve",
            "type": "function",
            "function": {
                "name": "resolve_contacts",
                "arguments": json.dumps({"text": "Gio"}),
            },
        }
        search_call = {
            "id": "call_search",
            "type": "function",
            "function": {
                "name": "search_memories",
                "arguments": json.dumps({"query": "last meeting"}),
            },
        }

        resolve_result = await controller._execute_tool_call(
            call=resolve_call,
            state=state,
            question="When did I last meet Gio?",
            search_limit=5,
            run_id="test_run",
            user_email="user@example.com",
            conversation_history=[],
        )
        assert resolve_result["status"] == "success"
        assert state.resolution.get("active_contact_scope_ids") == ["contact-gio"]

        await controller._execute_tool_call(
            call=search_call,
            state=state,
            question="When did I last meet Gio?",
            search_limit=5,
            run_id="test_run",
            user_email="user@example.com",
            conversation_history=[],
        )

        assert captured_search_args.get("contact_ids") == ["contact-gio"]

    @pytest.mark.asyncio
    async def test_search_memories_scoped_temporal_first_uses_oldest_and_events(
        self, controller, monkeypatch
    ):
        state = AgentState(goal="When was the first time I met Gio?")
        state.step_count = 1
        state.resolution["active_contact_scope_ids"] = ["contact-gio"]
        state.resolution["active_contact_scope"] = [
            {
                "mention_text": "Gio",
                "display_name": "Giovanni Panerai",
                "contact_id": "contact-gio",
            }
        ]
        controller.config.enable_validation = False

        class StubLogger:
            def log_tool_call(self, *args, **kwargs):
                return None

            def log_validation_result(self, *args, **kwargs):
                return None

        controller._logger = StubLogger()
        captured_search_args = {}

        def fake_execute_handler(tool_name, args, **_kwargs):
            if tool_name == "search_memories":
                captured_search_args.update(args)
                return {"results": [], "count": 0}
            return {"error": f"Unexpected tool: {tool_name}"}

        monkeypatch.setattr(controller, "_execute_handler", fake_execute_handler)

        search_call = {
            "id": "call_search_first",
            "type": "function",
            "function": {
                "name": "search_memories",
                "arguments": json.dumps({"query": "first time I met Gio", "limit": 3}),
            },
        }

        await controller._execute_tool_call(
            call=search_call,
            state=state,
            question="When was the first time I met Gio?",
            search_limit=5,
            run_id="test_run",
            user_email="user@example.com",
            conversation_history=[],
        )

        assert captured_search_args.get("contact_ids") == ["contact-gio"]
        assert captured_search_args.get("query") == "events"
        assert captured_search_args.get("sort_order") == "oldest"
        assert captured_search_args.get("limit") == 25

    @pytest.mark.asyncio
    async def test_search_memories_scoped_temporal_topic_keeps_semantic_query(
        self, controller, monkeypatch
    ):
        state = AgentState(goal="When did I last meet Gio and we talked about birds?")
        state.step_count = 1
        state.resolution["active_contact_scope_ids"] = ["contact-gio"]
        state.resolution["active_contact_scope"] = [
            {
                "mention_text": "Gio",
                "display_name": "Giovanni Panerai",
                "contact_id": "contact-gio",
            }
        ]
        controller.config.enable_validation = False

        class StubLogger:
            def log_tool_call(self, *args, **kwargs):
                return None

            def log_validation_result(self, *args, **kwargs):
                return None

        controller._logger = StubLogger()
        captured_search_args = {}

        def fake_execute_handler(tool_name, args, **_kwargs):
            if tool_name == "search_memories":
                captured_search_args.update(args)
                return {"results": [], "count": 0}
            return {"error": f"Unexpected tool: {tool_name}"}

        monkeypatch.setattr(controller, "_execute_handler", fake_execute_handler)

        search_call = {
            "id": "call_search_topic",
            "type": "function",
            "function": {
                "name": "search_memories",
                "arguments": json.dumps(
                    {"query": "When did I last meet Gio and we talked about birds?"}
                ),
            },
        }

        await controller._execute_tool_call(
            call=search_call,
            state=state,
            question="When did I last meet Gio and we talked about birds?",
            search_limit=5,
            run_id="test_run",
            user_email="user@example.com",
            conversation_history=[],
        )

        assert captured_search_args.get("contact_ids") == ["contact-gio"]
        assert captured_search_args.get("query") == "birds"
        assert captured_search_args.get("sort_order") == "newest"
        assert captured_search_args.get("limit") == 25

    @pytest.mark.asyncio
    async def test_run_returns_clarification_on_ambiguous_alias_from_pre_resolution(
        self, monkeypatch
    ):
        controller = AgentController(
            config=AgentConfig(
                max_steps=5,
                max_tool_calls=10,
                max_repairs=2,
                enable_intent_routing=True,
                enable_validation=True,
            )
        )

        class StubLogger:
            def start_run(self, *args, **kwargs):
                return "run_test"

            def start_step(self, *args, **kwargs):
                return None

            def log_llm_call(self, *args, **kwargs):
                return None

            def log_tool_call(self, *args, **kwargs):
                return None

            def log_validation_result(self, *args, **kwargs):
                return None

            def log_decision(self, *args, **kwargs):
                return None

            def log_state_update(self, *args, **kwargs):
                return None

            def complete_run(self, *args, **kwargs):
                return None

        controller._logger = StubLogger()

        async def fake_run_intent_router(*_args, **_kwargs):
            # The LLM router sets pre_resolve_contacts=True for person-referential
            # queries regardless of the structural intent; mirror that here so the
            # controller takes the pre-resolution short-circuit instead of looping.
            return IntentClassification(
                intent=IntentType.MEMORY_SEARCH,
                confidence=0.9,
                allowed_tool_groups=["memory", "resolution"],
                constraints=[],
                pre_resolve_contacts=True,
                reasoning="memory intent",
            )

        monkeypatch.setattr(controller, "_run_intent_router", fake_run_intent_router)

        llm_calls = {"count": 0}

        def fake_call_llm(_messages, _tools):
            llm_calls["count"] += 1
            return {"message": {"content": "unexpected"}}

        monkeypatch.setattr(controller, "_call_llm", fake_call_llm)
        monkeypatch.setattr(
            "contact_resolution_service.resolve_contacts_request",
            lambda _payload: {
                "status": "need_user_input",
                "people_mentioned": ["Gio"],
                "resolved_contacts": [],
                "ambiguous_contacts": [
                    {
                        "original_text": "Gio",
                        "candidates": [
                            {"contact_id": "contact-1", "display_name": "Giovanni Panerai"},
                            {"contact_id": "contact-2", "display_name": "Giovanni Ghelfi"},
                        ],
                    }
                ],
                "need_user_input": {
                    "kind": "disambiguation",
                    "prompt": "Which Gio did you mean?",
                    "submission_mode": "text",
                },
            },
        )

        bundle = await controller.run(
            question="When did I last meet Gio?",
            user_email="user@example.com",
        )

        assert llm_calls["count"] == 0
        assert "Which Gio did you mean?" in bundle["answer"]
        assert "Giovanni Panerai" in bundle["answer"]
        assert bundle["ui_directives"] is not None
        assert bundle["ui_directives"]["blocks"][0]["type"] == "clarification_form"
        assert bundle["ui_directives"]["fallback_text"] == "Which Gio did you mean?"

    @pytest.mark.asyncio
    async def test_run_returns_clarification_immediately_on_ambiguity(self, monkeypatch):
        """Main loop should return clarification instead of looping on ambiguous contact resolution."""
        controller = AgentController(
            config=AgentConfig(
                max_steps=5,
                max_tool_calls=10,
                max_repairs=2,
                enable_intent_routing=False,
                enable_validation=True,
            )
        )

        class StubLogger:
            def start_run(self, *args, **kwargs):
                return "run_test"

            def start_step(self, *args, **kwargs):
                return None

            def log_llm_call(self, *args, **kwargs):
                return None

            def log_tool_call(self, *args, **kwargs):
                return None

            def log_validation_result(self, *args, **kwargs):
                return None

            def log_decision(self, *args, **kwargs):
                return None

            def log_state_update(self, *args, **kwargs):
                return None

            def complete_run(self, *args, **kwargs):
                return None

        controller._logger = StubLogger()

        llm_calls = {"count": 0}

        def fake_call_llm(_messages, _tools):
            llm_calls["count"] += 1
            return {
                "message": {
                    "tool_calls": [
                        {
                            "id": "call_resolve_1",
                            "type": "function",
                            "function": {
                                "name": "resolve_contacts",
                                "arguments": json.dumps({"text": "When did I meet John?"}),
                            },
                        }
                    ]
                }
            }

        def fake_execute_handler(tool_name, args, **_kwargs):
            if tool_name == "resolve_contacts":
                return {
                    "status": "need_user_input",
                    "people_mentioned": ["John"],
                    "resolved_contacts": [],
                    "ambiguous_contacts": [
                        {
                            "original_text": "John",
                            "candidates": [
                                {"contact_id": "contact-1", "display_name": "John Smith"},
                                {"contact_id": "contact-2", "display_name": "John Doe"},
                            ],
                        }
                    ],
                    "need_user_input": {
                        "kind": "disambiguation",
                        "prompt": "Which John did you mean?",
                        "submission_mode": "text",
                    },
                }
            return {"error": f"Unexpected tool: {tool_name}"}

        monkeypatch.setattr(controller, "_call_llm", fake_call_llm)
        monkeypatch.setattr(controller, "_execute_handler", fake_execute_handler)
        monkeypatch.setattr(
            controller, "_prime_contact_scope_for_question", lambda *args, **kwargs: None
        )

        bundle = await controller.run(
            question="When did I meet John?",
            user_email="user@example.com",
        )

        assert llm_calls["count"] == 1
        assert "Which John did you mean?" in bundle["answer"]
        assert (
            bundle["resolution"]
            .get("pending_contact_ambiguous_contacts", [{}])[0]
            .get("candidates", [{}])[0]
            .get("display_name")
            == "John Smith"
        )
        assert (
            bundle["resolution"].get("pending_contact_need_user_input", {}).get("prompt")
            == "Which John did you mean?"
        )


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
        for group_name in TOOL_GROUPS:
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
            tool_name="get_events",
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


class TestStateRuntimeCompatibility:
    """Tests for runtime compatibility with current state shape."""

    def test_runtime_state_fields(self):
        """Test runtime state fields used by controller remain available."""
        state = AgentState(goal="Test")

        state.resolution["entity_type"] = "person"
        state.activated_skills.append({"skill": "test"})

        assert state.resolution["entity_type"] == "person"
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
